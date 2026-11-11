"""An evaluated window can be collected without accepting a database mutation."""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select, text


@dataclass
class ValidationPreparation:
    candidates: list[dict]
    outcomes: list[dict]
    calendar: object
    as_of_date: date
    return_basis: str
    benchmark_prices: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationAcceptance:
    market: str
    candidate_revisions: dict[int, str]
    result_revisions: dict[tuple, str | None]


class ValidationAcceptanceConflict(ValueError):
    """Source work is obsolete; preserve accepted data and collect again."""


def capture_validation_acceptance(db, candidates, *, market, windows, validation_version):
    """Freeze preimages, including missing rows, before any external I/O."""
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_result_revision
    from ai_stock_sentinel.daily_radar.validation_inputs import validation_candidate_revision
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result
    revisions = {c['candidate_id']: validation_candidate_revision(c) for c in candidates}
    results = {(cid, w, validation_version): None for cid in revisions for w in windows}
    if results:
        for row in db.scalars(select(Result).where(Result.candidate_id.in_(revisions),
                Result.window_days.in_(windows), Result.validation_version == validation_version)
                .execution_options(populate_existing=True, yield_per=128)):
            results[(row.candidate_id, row.window_days, row.validation_version)] = forward_validation_result_revision(row)
    return ValidationAcceptance(market, revisions, results)


def _check_acceptance(db, outcomes, acceptance):
    from ai_stock_sentinel.calibration.forward_validation import parse_date
    from ai_stock_sentinel.daily_radar.forward_validation import (
        forward_validation_candidates_from_runs, forward_validation_result_revision, is_original_rights_conflict_recovery,
    )
    from ai_stock_sentinel.daily_radar.validation_inputs import validation_candidate_revision
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result
    ids = set(acceptance.candidate_revisions)
    current = forward_validation_candidates_from_runs(db, market=acceptance.market, candidate_ids=sorted(ids))
    if ({c['candidate_id']: validation_candidate_revision(c) for c in current}
            != acceptance.candidate_revisions):
        raise ValidationAcceptanceConflict('validation_inputs_changed')
    if not outcomes:
        return {}
    if any(int(row['candidate_id']) not in ids for row in outcomes):
        raise ValidationAcceptanceConflict('validation_inputs_changed')
    incoming = {(int(row['candidate_id']), int(row['window_days']), str(row['validation_version'])): row for row in outcomes}
    keys = set(incoming)
    # Relation locks freeze candidate/origin changes. Result locks also exclude
    # concurrent result owners until the calendar checkpoint has been accepted.
    result_ids = {key[0] for key in keys}
    existing = {(r.candidate_id, r.window_days, r.validation_version):
        (forward_validation_result_revision(r), r.evaluation_as_of_date,
         is_original_rights_conflict_recovery(r, incoming.get((r.candidate_id, r.window_days, r.validation_version))))
        for r in db.scalars(
        select(Result).where(Result.candidate_id.in_(result_ids), Result.window_days.in_({key[1] for key in keys}),
            Result.validation_version.in_({key[2] for key in keys}))
        .order_by(Result.candidate_id, Result.window_days, Result.validation_version, Result.id)
        .with_for_update().execution_options(populate_existing=True, yield_per=128))}
    for row in outcomes:
        key = (int(row['candidate_id']), int(row['window_days']), str(row['validation_version']))
        revision, saved_cutoff, original_recovery = existing.get(key, (None, None, False))
        cutoff = parse_date(row.get('evaluation_as_of_date'))
        if not original_recovery and saved_cutoff is not None and (cutoff is None or saved_cutoff > cutoff):
            raise ValidationAcceptanceConflict('newer_validation_result_preserved')
        if key not in acceptance.result_revisions or revision != acceptance.result_revisions[key]:
            raise ValidationAcceptanceConflict('validation_results_changed')
    return {key: acceptance.result_revisions[key] for key in keys}


def reserve_validation_commit(db, *, freeze_candidates=False):
    """Same relation -> result/candidate -> prepared order as reviewed repair."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        mode = 'SHARE ROW EXCLUSIVE' if freeze_candidates else 'ROW EXCLUSIVE'
        db.execute(text(f"LOCK TABLE daily_radar_runs, daily_radar_candidates IN {mode} MODE"))
    elif db.get_bind().dialect.name == "sqlite":
        db.execute(text("UPDATE daily_radar_runs SET id = id WHERE 0"))


def accept_validation_results(db, outcomes, *, calendar, acceptance=None, **upsert_options):
    """Accept a legacy endpoint batch without committing the caller's transaction.

    Durable work accepts its own calendar ledger and domain effects atomically;
    its prepare-only collectors do not use this prepared-run checkpoint owner.
    """
    from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
    from ai_stock_sentinel.daily_radar.trading_calendar import save_calendar
    outcomes = list(outcomes)
    reserve_validation_commit(db, freeze_candidates=acceptance is not None)
    # Preserve relation -> result/candidate -> prepared order. Even if the
    # caller catches an error and commits, this batch cannot leak flushed rows.
    with db.begin_nested():
        if acceptance is not None:
            upsert_options['expected_revisions'] = _check_acceptance(db, outcomes, acceptance)
        summary = upsert_forward_validation_results(db, outcomes, **upsert_options)
        if calendar is not None and not save_calendar(db, calendar):
            raise ValueError('calendar_checkpoint_unavailable')
    return summary
