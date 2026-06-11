from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.calibration import (
    DEFAULT_BUCKET_THRESHOLDS,
    DEFAULT_RANK_CUTOFFS,
    build_calibration_report,
    calibration_candidates_from_fixture,
    calibration_candidates_from_runs,
)
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun
from ai_stock_sentinel.db.session import Base


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "daily_radar"


def test_calibration_fixture_report_is_stable_and_structured() -> None:
    candidates = calibration_candidates_from_fixture(
        fixture_dir=FIXTURE_DIR,
        run_date=date(2026, 5, 29),
        market="TW",
    )

    first = build_calibration_report(
        candidates,
        market="TW",
        sample_source="fixture",
        rank_cutoffs=DEFAULT_RANK_CUTOFFS,
        bucket_thresholds=DEFAULT_BUCKET_THRESHOLDS,
    )
    second = build_calibration_report(
        candidates,
        market="TW",
        sample_source="fixture",
        rank_cutoffs=DEFAULT_RANK_CUTOFFS,
        bucket_thresholds=DEFAULT_BUCKET_THRESHOLDS,
    )

    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert first["sample_count"] == 4
    assert first["bucket_distribution"] == {
        "bottoming_reversal": 1,
        "institutional_accumulation": 1,
        "price_volume_strengthening": 1,
        "support_retest": 1,
    }
    assert first["version_manifest"]["scoring_version"] == "daily-radar-scoring-v2.1c"
    assert first["version_manifest"]["live_scoring_changed"] is False


def test_calibration_report_covers_rank_cutoff_and_bucket_threshold_impact() -> None:
    candidates = [
        _candidate("2330.TW", 92, "institutional_accumulation", {"institutional_accumulation": 92, "support_retest": 54}),
        _candidate("2454.TW", 81, "price_volume_strengthening", {"price_volume_strengthening": 81, "support_retest": 65}),
        _candidate("2303.TW", 63, "support_retest", {"support_retest": 63, "bottoming_reversal": 56}),
    ]

    report = build_calibration_report(
        candidates,
        market="TW",
        sample_source="unit",
        rank_cutoffs=[1, 2],
        bucket_thresholds=[55, 65],
    )

    assert report["rank_cutoff_impact"] == [
        {
            "cutoff": 1,
            "included": 1,
            "average_observation_score": 92.0,
            "bucket_distribution": {"institutional_accumulation": 1},
            "risk_label_counts": {},
            "relative_strength": {"fresh": 1, "missing_or_stale": 0, "positive": 1, "negative": 0, "neutral": 0},
        },
        {
            "cutoff": 2,
            "included": 2,
            "average_observation_score": 86.5,
            "bucket_distribution": {"institutional_accumulation": 1, "price_volume_strengthening": 1},
            "risk_label_counts": {},
            "relative_strength": {"fresh": 2, "missing_or_stale": 0, "positive": 2, "negative": 0, "neutral": 0},
        },
    ]
    assert report["bucket_threshold_impact"]["thresholds"] == [
        {
            "threshold": 55,
            "matched_bucket_counts": {
                "bottoming_reversal": 1,
                "institutional_accumulation": 1,
                "price_volume_strengthening": 1,
                "support_retest": 2,
            },
            "average_matched_buckets_per_sample": 1.67,
        },
        {
            "threshold": 65,
            "matched_bucket_counts": {
                "institutional_accumulation": 1,
                "price_volume_strengthening": 1,
                "support_retest": 1,
            },
            "average_matched_buckets_per_sample": 1.0,
        },
    ]


def test_calibration_report_covers_risk_penalty_overheat_and_relative_strength_impact() -> None:
    candidates = [
        _candidate(
            "2330.TW",
            84,
            "institutional_accumulation",
            {"institutional_accumulation": 84},
            risk_labels=["overextended"],
            risk_penalties=[{"label": "overextended", "score_adjustment": -18}],
            relative_strength={"freshness": "fresh", "relative_value": 0.04, "score": 3},
        ),
        _candidate(
            "2454.TW",
            70,
            "price_volume_strengthening",
            {"price_volume_strengthening": 70},
            risk_labels=["data_gap"],
            risk_penalties=[{"label": "data_gap", "score_adjustment": -18}],
            relative_strength={"freshness": "missing", "missing_reason": "insufficient_aligned_history", "score": 0},
        ),
        _candidate(
            "2303.TW",
            68,
            "support_retest",
            {"support_retest": 68},
            relative_strength={"freshness": "fresh", "relative_value": -0.07, "score": -6},
        ),
    ]

    report = build_calibration_report(candidates, market="TW", sample_source="unit")

    assert report["risk_penalty_impact"]["samples_with_penalty"] == 2
    assert report["risk_penalty_impact"]["label_counts"] == {"data_gap": 1, "overextended": 1}
    assert report["risk_penalty_impact"]["average_adjustment"] == -12.0
    assert report["overheat_impact"] == {
        "label": "overextended",
        "samples": 1,
        "average_adjustment": -18.0,
    }
    assert report["relative_strength_impact"]["fresh_samples"] == 2
    assert report["relative_strength_impact"]["positive_samples"] == 1
    assert report["relative_strength_impact"]["negative_samples"] == 1
    assert report["relative_strength_impact"]["average_score_adjustment"] == -1.0
    assert report["skip_reasons"] == {"relative_strength:insufficient_aligned_history": 1}


def test_calibration_report_records_invalid_sample_skip_reason_without_silent_drop() -> None:
    report = build_calibration_report(
        [
            _candidate("2330.TW", 84, "institutional_accumulation", {"institutional_accumulation": 84}),
            {"symbol": "BROKEN", "primary_bucket": "support_retest"},
        ],
        market="TW",
        sample_source="unit",
    )

    assert report["sample_count"] == 1
    assert report["excluded_sample_count"] == 1
    assert report["skip_reasons"] == {"invalid_sample:missing_observation_score": 1}


def test_calibration_candidates_can_be_loaded_from_persisted_runs() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[DailyRadarRun.__table__, DailyRadarCandidate.__table__])
    with Session(engine) as session:
        run = DailyRadarRun(
            run_date=date(2026, 6, 1),
            market="TW",
            status="completed",
            started_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            finished_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            universe_count=1,
            prefilter_count=1,
            candidate_count=1,
            errors=[],
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        session.add(run)
        session.flush()
        session.add(
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
                explanation="Observation summary.",
                repeat_status="new",
                score_breakdown={"relative_strength": {"freshness": "fresh", "score": 3}},
                input_snapshot={},
                data_dates={"ohlcv": "2026-06-01"},
            )
        )
        session.commit()

        candidates = calibration_candidates_from_runs(
            session,
            market="TW",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
        )

    assert candidates == [
        {
            "symbol": "2330.TW",
            "name": "TSMC",
            "record_date": "2026-06-01",
            "primary_bucket": "institutional_accumulation",
            "secondary_buckets": [],
            "observation_score": 88,
            "bucket_scores": {"institutional_accumulation": 88},
            "risk_labels": [],
            "matched_rules": [],
            "score_breakdown": {"relative_strength": {"freshness": "fresh", "score": 3}},
            "input_snapshot": {},
            "data_dates": {"ohlcv": "2026-06-01"},
        }
    ]


def _candidate(
    symbol: str,
    score: int,
    bucket: str,
    bucket_scores: dict[str, int],
    *,
    risk_labels: list[str] | None = None,
    risk_penalties: list[dict[str, Any]] | None = None,
    relative_strength: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": f"{symbol} fixture",
        "record_date": "2026-06-01",
        "primary_bucket": bucket,
        "secondary_buckets": [],
        "observation_score": score,
        "bucket_scores": bucket_scores,
        "risk_labels": risk_labels or [],
        "matched_rules": [],
        "score_breakdown": {
            "risk_penalties": risk_penalties or [],
            "relative_strength": relative_strength or {"freshness": "fresh", "relative_value": 0.03, "score": 3},
            "observation_score": score,
        },
        "input_snapshot": {},
        "data_dates": {"ohlcv": "2026-06-01"},
    }
