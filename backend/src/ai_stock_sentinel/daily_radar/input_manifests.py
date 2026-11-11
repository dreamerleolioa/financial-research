"""Freeze actual scoring inputs and atomically link a published revision."""
from copy import deepcopy

from sqlalchemy import select, text, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ai_stock_sentinel.daily_radar.observation_validation import canonical_radar_run_ids
from ai_stock_sentinel.daily_radar.repository import get_symbol_candidate_history
from ai_stock_sentinel.daily_radar.scoring import SCORING_VERSION, RULE_VERSION, SCORING_CONFIG_VERSION
from ai_stock_sentinel.daily_radar.medium_term import SELECTION_VERSION
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint, canonical_json
from ai_stock_sentinel.db.models import RadarInputManifest, RadarRunInput, DailyRadarRun, DailyRadarCandidate
from ai_stock_sentinel.phase1_avwap.projection import read_phase1_avwap_contexts_for_daily_radar

MANIFEST_VERSION = "radar-scoring-input-v1"


def serialize_publication(db, *, market, run_date):
    """All DB-only scoring entrypoints share a short publication transaction."""
    if db.get_bind().dialect.name == "postgresql":
        # Match historical repair's relation -> row lock order. Freeze cannot
        # race a quarantine/origin repair that changes the history we consume.
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        db.execute(text("LOCK TABLE daily_radar_runs, daily_radar_candidates IN ROW EXCLUSIVE MODE"))
        key = int(fingerprint({"market": market, "run_date": str(run_date)})[:16], 16) & ((1 << 63) - 1)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    elif db.get_bind().dialect.name == "sqlite":
        db.execute(text("UPDATE daily_radar_runs SET id = id WHERE 0"))


def manifest_envelope(*, run_date, market, payload):
    return {"version": MANIFEST_VERSION, "run_date": str(run_date), "market": market,
            "inputs": deepcopy(payload)}


def save_manifest(db, *, run_date, market, payload):
    envelope = manifest_envelope(run_date=run_date, market=market, payload=payload)
    digest = fingerprint(envelope)
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(insert(RadarInputManifest).values(fingerprint=digest, run_date=run_date,
        market=market, payload=envelope).on_conflict_do_nothing(index_elements=["fingerprint"]))
    row = db.scalar(select(RadarInputManifest).where(RadarInputManifest.fingerprint == digest))
    if row is None or canonical_json(row.payload) != canonical_json(envelope):
        raise ValueError("radar_input_manifest_integrity_invalid")
    return row


def link_run_input(db, *, run_id, manifest):
    run = db.get(DailyRadarRun, run_id)
    if run is None or (run.market, run.run_date) != (manifest.market, manifest.run_date):
        raise ValueError("radar_run_input_identity_mismatch")
    if fingerprint(manifest.payload) != manifest.fingerprint:
        raise ValueError("radar_input_manifest_integrity_invalid")
    existing = db.get(RadarRunInput, run_id)
    if existing is not None:
        if existing.manifest_id != manifest.id:
            raise ValueError("radar_published_input_is_immutable")
        return
    count = db.scalar(select(func.count()).select_from(DailyRadarCandidate).where(DailyRadarCandidate.run_id == run_id))
    db.add(RadarRunInput(run_id=run_id, manifest_id=manifest.id, output_count=count))
    db.flush()


def reusable_public_run(db, manifest):
    # A matching older revision cannot replace a different current publication.
    row = db.execute(select(DailyRadarRun, RadarRunInput.output_count).join(RadarRunInput, RadarRunInput.run_id == DailyRadarRun.id).where(
        DailyRadarRun.id.in_(canonical_radar_run_ids(market=manifest.market,
            start_date=manifest.run_date, end_date=manifest.run_date)),
        DailyRadarRun.status == "completed", RadarRunInput.manifest_id == manifest.id)).first()
    if row is None:
        return None
    run, expected_total = row
    total, selected = db.execute(select(func.count(), func.count().filter(
        DailyRadarCandidate.selection_status == "selected")).where(DailyRadarCandidate.run_id == run.id)).one()
    return run if total == expected_total and selected == run.candidate_count else None


def collect_execution_inputs(db, *, run_date, market, records, market_context,
                             background_contexts, history_candidates=None, input_context=None, candidate_limit=100):
    records = list(records)
    symbols = [str(row["symbol"]) for row in records]
    history = list(history_candidates) if history_candidates is not None else get_symbol_candidate_history(
        db, symbols=symbols, before_date=run_date, lookback_days=None, market=market, summary_only=True)
    avwap = read_phase1_avwap_contexts_for_daily_radar(db, symbols=symbols, data_date=run_date)
    payload = deepcopy({"records": list(records), "market_context": dict(market_context or {}),
        "background_contexts": dict(background_contexts or {}), "history_candidates": history,
        "phase1_contexts": avwap, "context": dict(input_context or {}),
        "execution_options": {"candidate_limit": candidate_limit},
        "versions": {"scoring": SCORING_VERSION, "rules": RULE_VERSION,
                     "config": SCORING_CONFIG_VERSION, "selection": SELECTION_VERSION}})
    return payload


def freeze_execution_inputs(db, **kwargs):
    payload = collect_execution_inputs(db, **kwargs)
    return save_manifest(db, run_date=kwargs["run_date"], market=kwargs["market"], payload=payload), payload


def prepared_input_context(prepared, cache_rows):
    return {"prepared_run_id": prepared.id, "selected_symbols": list(prepared.selected_symbols),
        "universe": list(prepared.universe), "market_session": prepared.step_statuses.get("market-session"),
        "technical_source_provenance": {row.symbol: (row.technical or {}).get("source_provenance") for row in cache_rows},
        "market_exploration": (prepared.step_statuses.get("prepare-universe") or {}).get("market_exploration")}


def published_input_summary(db, run):
    link = db.get(RadarRunInput, run.id)
    if link is None:
        return {"state": "legacy_unrecorded"}
    manifest = db.get(RadarInputManifest, link.manifest_id)
    if (manifest is None or (manifest.market, manifest.run_date) != (run.market, run.run_date)
            or fingerprint(manifest.payload) != manifest.fingerprint):
        return {"state": "invalid", "reason": "radar_input_manifest_integrity_invalid"}
    return {"state": "recorded", "manifest_id": manifest.id, "fingerprint": manifest.fingerprint,
            "version": manifest.payload["version"], "expected_output_count": link.output_count,
            "output_complete": db.scalar(select(func.count()).select_from(DailyRadarCandidate).where(
                DailyRadarCandidate.run_id == run.id)) == link.output_count}
