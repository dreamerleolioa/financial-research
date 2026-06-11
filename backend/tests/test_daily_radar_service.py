from __future__ import annotations

import socket
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_fixture_records
from ai_stock_sentinel.daily_radar import service as service_module
from ai_stock_sentinel.daily_radar.service import run_daily_radar


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "daily_radar"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[DailyRadarRun.__table__, DailyRadarCandidate.__table__],
    )
    with Session(engine) as session:
        yield session


def test_run_daily_radar_orchestrates_fixture_prefilter_scoring_explanations_and_persistence(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Daily Radar service tests must stay offline")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    run = run_daily_radar(
        date(2026, 5, 29),
        "TW",
        session=db_session,
        fixture_dir=FIXTURE_DIR,
    )
    db_session.commit()

    assert run.status == "completed"
    assert run.finished_at is not None
    assert run.universe_count == 8
    assert run.prefilter_count == 4
    assert run.candidate_count == 4
    assert {error["code"] for error in run.errors or []} >= {"prefilter_rejected", "prefilter_stale_data"}

    candidates = db_session.scalars(
        select(DailyRadarCandidate)
        .where(DailyRadarCandidate.run_id == run.id)
        .order_by(DailyRadarCandidate.observation_score.desc(), DailyRadarCandidate.symbol.asc())
    ).all()
    assert [candidate.symbol for candidate in candidates] == ["2303.TW", "3034.TW", "2330.TW", "2454.TW"]
    by_symbol = {candidate.symbol: candidate for candidate in candidates}
    first = by_symbol["2330.TW"]
    assert first.explanation.startswith("法人籌碼延續觀察")
    assert first.repeat_status == "upgraded"
    assert first.bucket_scores
    assert first.risk_labels == []
    assert first.matched_rules
    assert first.score_breakdown["observation_score"] == first.observation_score
    assert first.input_snapshot["ohlcv"]
    assert first.input_snapshot["market_context"]["regime"] == "constructive"
    assert first.data_dates["ohlcv"] == "2026-05-29"
    assert first.data_dates["market_index"] == "2026-05-29"


def test_run_daily_radar_marks_stale_data_when_no_fresh_candidate_can_be_persisted(db_session: Session) -> None:
    stale_record = load_daily_radar_fixture_records(FIXTURE_DIR)[4]

    run = run_daily_radar(
        date(2026, 5, 29),
        "TW",
        session=db_session,
        records=[stale_record],
    )
    db_session.commit()

    assert run.status == "stale_data"
    assert run.universe_count == 1
    assert run.prefilter_count == 0
    assert run.candidate_count == 0
    assert run.errors == [
        {
            "code": "prefilter_stale_data",
            "symbol": "1101.TW",
            "reasons": ["low_liquidity", "stale_core_data"],
        }
    ]


def test_run_daily_radar_summarizes_per_symbol_errors_without_failing_full_run(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        record
        for record in load_daily_radar_fixture_records(FIXTURE_DIR)
        if record["symbol"] in {"2330.TW", "2454.TW"}
    ]
    from ai_stock_sentinel.daily_radar import service as service_module

    original_score = service_module.score_daily_radar_record

    def score_with_one_symbol_error(record: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if record["symbol"] == "2454.TW":
            raise ValueError("fixture scoring problem")
        return original_score(record, **kwargs)

    monkeypatch.setattr(service_module, "score_daily_radar_record", score_with_one_symbol_error)

    run = run_daily_radar(
        date(2026, 5, 29),
        "TW",
        session=db_session,
        records=records,
    )
    db_session.commit()

    assert run.status == "completed"
    assert run.universe_count == 2
    assert run.prefilter_count == 2
    assert run.candidate_count == 1
    assert run.errors == [
        {
            "code": "candidate_processing_error",
            "symbol": "2454.TW",
            "message": "fixture scoring problem",
        }
    ]
    assert [candidate.symbol for candidate in run.candidates] == ["2330.TW"]


def test_run_daily_radar_persists_universe_track_trace_in_candidate_input_snapshot(db_session: Session) -> None:
    record = deepcopy(_joined_record("2454.TW"))
    record["institutional_flow"] |= {
        "universe_primary_track": "price_volume",
        "institutional_universe_tracks": ["price_volume", "reversal"],
        "universe_track_metrics": {
            "price_volume": {"rank": 1, "score": 90.0, "matched": True},
            "support_retake": {"score": 0.0, "matched": False, "missing_data": True},
        },
        "scores": {"price_volume": 90.0, "reversal": 70.0},
    }

    run = run_daily_radar(
        date(2026, 5, 29),
        "TW",
        session=db_session,
        records=[record],
        market_context={"market": {"regime": "constructive"}, "data_dates": {"market_index": "2026-05-29"}},
    )
    db_session.commit()

    assert run.status == "completed"
    candidate = db_session.query(DailyRadarCandidate).filter(DailyRadarCandidate.run_id == run.id).one()
    assert candidate.input_snapshot["universe"] == {
        "universe_primary_track": "price_volume",
        "institutional_universe_tracks": ["price_volume", "reversal"],
        "universe_track_metrics": {
            "price_volume": {"rank": 1, "score": 90.0, "matched": True},
            "support_retake": {"score": 0.0, "matched": False, "missing_data": True},
        },
        "scores": {"price_volume": 90.0, "reversal": 70.0},
    }


def test_run_daily_radar_persists_relative_strength_version_and_replayable_evidence(db_session: Session) -> None:
    record = deepcopy(_joined_record("2330.TW"))
    record["price_history"] = _price_history(date(2026, 5, 9), [100.0 + index for index in range(21)])
    market_context = {
        "market": {
            "index_symbol": "TAIEX",
            "data_date": "2026-05-29",
            "regime": "constructive",
            "freshness": "fresh",
            "above_ma20": True,
            "above_ma60": True,
            "volatility_state": "normal",
            "market_risk_flags": [],
        },
        "benchmark": {
            "symbol": "TAIEX",
            "yfinance_symbol": "^TWII",
            "price_history": _price_history(date(2026, 5, 9), [100.0 + index * 0.25 for index in range(21)]),
            "data_dates": {"market_index": "2026-05-29"},
        },
        "data_dates": {"market_index": "2026-05-29"},
    }

    run = run_daily_radar(
        date(2026, 5, 29),
        "TW",
        session=db_session,
        records=[record],
        market_context=market_context,
    )
    db_session.commit()

    assert run.status == "completed"
    candidate = db_session.query(DailyRadarCandidate).filter(DailyRadarCandidate.run_id == run.id).one()
    relative_strength = candidate.score_breakdown["relative_strength"]
    evidence = candidate.input_snapshot["evidence"][0]

    assert candidate.score_breakdown["scoring_version"] == "daily-radar-scoring-v2.1c"
    assert candidate.score_breakdown["rule_version"] == "daily-radar-rules-v2.1c"
    assert relative_strength["benchmark_symbol"] == "TAIEX"
    assert relative_strength["lookback_days"] == 20
    assert relative_strength["relative_value"] > 0
    assert relative_strength["score"] == 6
    assert candidate.data_dates["relative_strength"] == "2026-05-29"
    assert evidence["evidence_type"] == "relative_strength"
    assert evidence["as_of_date"] == "2026-05-29"
    assert evidence["freshness"] == "fresh"
    assert evidence["replay_key"] == "relative_strength:2330.TW:TAIEX:2026-05-29:L20"
    assert evidence["applicable_consumers"] == ["daily_radar"]


def test_run_daily_radar_can_create_multiple_same_date_runs_and_public_reads_choose_latest(
    db_session: Session,
) -> None:
    records = [
        deepcopy(record)
        for record in load_daily_radar_fixture_records(FIXTURE_DIR)
        if record["symbol"] == "2330.TW"
    ]

    first = run_daily_radar(date(2026, 5, 29), "TW", session=db_session, records=records)
    second = run_daily_radar(date(2026, 5, 29), "TW", session=db_session, records=records)
    db_session.commit()

    runs = db_session.scalars(select(DailyRadarRun).order_by(DailyRadarRun.id)).all()

    assert [run.id for run in runs] == [first.id, second.id]
    assert all(run.status == "completed" for run in runs)
    assert len({candidate.run_id for candidate in db_session.scalars(select(DailyRadarCandidate)).all()}) == 2


def _joined_record(symbol: str) -> dict[str, Any]:
    return next(record for record in load_daily_radar_fixture_records(FIXTURE_DIR) if record["symbol"] == symbol)


def _price_history(start: date, closes: list[float]) -> list[dict[str, Any]]:
    return [
        {"date": (start + timedelta(days=index)).isoformat(), "close": close}
        for index, close in enumerate(closes)
    ]


def test_run_daily_radar_with_fixture_fallback_disabled_does_not_load_default_fixtures(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "load_daily_radar_fixture_records", lambda *args, **kwargs: pytest.fail("loaded fixtures"))
    monkeypatch.setattr(service_module, "_load_optional_json", lambda *args, **kwargs: pytest.fail("loaded fixture json"))

    run = run_daily_radar(
        date(2026, 5, 29),
        "TW",
        session=db_session,
        market_context={},
        allow_fixture_fallback=False,
    )

    assert run.status == "completed"
    assert run.universe_count == 0
    assert run.candidate_count == 0


def test_history_fallback_returns_repository_history_before_fixture_and_empty_when_disabled(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = DailyRadarRun(
        run_date=date(2026, 5, 28),
        market="TW",
        status="completed",
        universe_count=1,
        prefilter_count=1,
        candidate_count=1,
        errors=[],
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        DailyRadarCandidate(
            run_id=run.id,
            symbol="2330.TW",
            name="TSMC",
            primary_bucket="institutional_accumulation",
            secondary_buckets=[],
            observation_score=88,
            bucket_scores={"institutional_accumulation": 88},
            risk_labels=[],
            matched_rules=[],
            explanation="history fixture",
            repeat_status="new",
            score_breakdown={"observation_score": 88},
            input_snapshot={"symbol": "2330.TW"},
            data_dates={"ohlcv": "2026-05-28"},
        )
    )
    db_session.commit()
    monkeypatch.setattr(service_module, "_load_optional_json", lambda *args, **kwargs: pytest.fail("loaded fixture history"))

    history = service_module._history_from_repository_or_fixture(
        db_session,
        run_date=date(2026, 5, 29),
        market="TW",
        symbols=["2330.TW"],
        fixture_dir=FIXTURE_DIR,
        allow_fixture_fallback=False,
    )
    empty_history = service_module._history_from_repository_or_fixture(
        db_session,
        run_date=date(2026, 5, 29),
        market="TW",
        symbols=["2454.TW"],
        fixture_dir=FIXTURE_DIR,
        allow_fixture_fallback=False,
    )

    assert [item["symbol"] for item in history] == ["2330.TW"]
    assert empty_history == []
