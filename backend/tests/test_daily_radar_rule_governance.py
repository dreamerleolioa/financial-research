from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel import api
from ai_stock_sentinel.daily_radar.forward_validation import (
    build_forward_validation_report,
    forward_validation_fixture_inputs,
)
from ai_stock_sentinel.daily_radar.rule_governance import (
    DEFAULT_ABLATION_GROUPS,
    build_ablation_report,
    build_monthly_rule_review_report,
    validation_rows_from_results,
)
from ai_stock_sentinel.daily_radar.rule_registry import (
    SCORING_ACTIVE_TIERS,
    assert_rule_can_affect_score,
    get_rule_registry,
)
from ai_stock_sentinel.daily_radar.scoring import RULE_VERSION, SCORING_VERSION
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarForwardValidationResult,
    DailyRadarRun,
)
from ai_stock_sentinel.db.session import Base, get_db


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


ROOT = Path(__file__).parents[1]
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "daily_radar"


def test_rule_registry_covers_every_scoring_rule_and_active_score_driver() -> None:
    registry = get_rule_registry()
    scoring_source = (ROOT / "src" / "ai_stock_sentinel" / "daily_radar" / "scoring.py").read_text(
        encoding="utf-8"
    )
    scoring_rule_codes = set(re.findall(r'_rule\("([^"]+)"', scoring_source))

    assert scoring_rule_codes
    assert scoring_rule_codes <= set(registry)
    for code in scoring_rule_codes:
        assert registry[code].tier in SCORING_ACTIVE_TIERS
        assert registry[code].owner_module == "daily_radar.scoring"
        assert registry[code].first_version
        assert registry[code].last_reviewed_version == RULE_VERSION

    for label in ("overextended", "flow_conflict", "margin_crowding", "market_weakness", "data_gap"):
        assert f"risk_label_{label}" in registry


def test_context_only_rules_are_governed_out_of_live_scoring() -> None:
    registry = get_rule_registry()
    context_only_codes = {
        code for code, entry in registry.items()
        if entry.tier in {"context_only", "deprecated"}
    }
    scoring_source = (ROOT / "src" / "ai_stock_sentinel" / "daily_radar" / "scoring.py").read_text(
        encoding="utf-8"
    )

    assert {"news_sentiment_context", "fundamental_valuation_context"} <= context_only_codes
    for code in context_only_codes:
        assert f'_rule("{code}"' not in scoring_source
        with pytest.raises(ValueError):
            assert_rule_can_affect_score(code)


def test_ablation_report_fixture_is_deterministic_and_marks_low_samples() -> None:
    candidates, prices_by_symbol, benchmark_prices, benchmark_symbol = forward_validation_fixture_inputs(
        fixture_dir=FIXTURE_DIR,
        run_date=date(2026, 5, 29),
        market="TW",
    )
    evaluation = build_forward_validation_report(
        candidates,
        price_series_by_symbol=prices_by_symbol,
        benchmark_prices=benchmark_prices,
        market="TW",
        sample_source="fixture",
        as_of_date=date(2026, 6, 26),
        windows=[5, 10, 20],
        benchmark_symbol=benchmark_symbol,
    )

    first = build_ablation_report(
        evaluation.outcomes,
        market="TW",
        sample_source="fixture",
        min_sample_count=20,
    )
    second = build_ablation_report(
        json.loads(json.dumps(evaluation.outcomes, ensure_ascii=False)),
        market="TW",
        sample_source="fixture",
        min_sample_count=20,
    )

    assert first == second
    assert {row["group"] for row in first["ablation_groups"]} == set(DEFAULT_ABLATION_GROUPS)
    assert first["metadata"]["positioning"] == "rule_quality_governance_diagnostic_not_live_scoring_change"
    assert first["sample_summary"]["validated_by_window"] == {"5": 4, "10": 4, "20": 4}
    assert first["insufficient_sample_cases"]
    assert first["version_manifest"]["live_scoring_changed"] is False
    assert first["version_manifest"]["live_ranking_changed"] is False
    json.dumps(first, ensure_ascii=False, sort_keys=True)


def test_monthly_rule_review_api_uses_persisted_validation_results_and_returns_artifacts(monkeypatch) -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        _add_validation_result(session, candidate)
        session.commit()

    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    try:
        response = TestClient(api.app).post(
            "/internal/daily-radar/rule-review/monthly",
            json={"market": "TW", "year": 2026, "month": 6, "min_sample_count": 1},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["month"] == "2026-06"
    assert payload["report_json"]["metadata"]["sample_source"] == "production_db"
    assert payload["report_json"]["sample_summary"]["validated_sample_count"] == 1
    assert payload["report_json"]["human_approval_boundary"] == {
        "automated_report": True,
        "updates_live_scoring": False,
        "requires_human_approved_versioned_strategy_update": True,
    }
    assert "# Daily Radar Rule Review 2026-06" in payload["report_markdown"]


def test_monthly_rule_review_report_keeps_scoring_versions_unchanged() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run = _add_run(session)
        candidate = _add_candidate(session, run)
        _add_validation_result(session, candidate)
        report = build_monthly_rule_review_report(
            session,
            market="TW",
            year=2026,
            month=6,
            min_sample_count=1,
        )

    manifest = report.json_report["version_manifest"]
    assert manifest["scoring_version"] == SCORING_VERSION
    assert manifest["rule_version"] == RULE_VERSION
    assert manifest["live_scoring_changed"] is False
    assert manifest["automated_recommendations_only"] is True
    assert report.json_report["baseline_config"]["primary_bucket_weight"] == 0.8
    assert report.json_report["auto_change_eligible"] is False
    candidate_parameters = {
        candidate["parameter"]
        for candidate in report.json_report["candidate_configs"]
    }
    assert {
        "primary_bucket_weight",
        "cross_confirmation_weight",
        "market_context_weight",
        "freshness_weight",
        "relative_strength_weight",
        "secondary_bucket_threshold",
    } == candidate_parameters
    assert not candidate_parameters & {
        "overextended_penalty",
        "data_gap_penalty",
        "rule_scores",
        "prefilter_rules",
    }
    assert all(
        sum(
            value != report.json_report["baseline_config"][key]
            for key, value in candidate["candidate_config"].items()
        ) == 1
        for candidate in report.json_report["candidate_configs"]
    )
    assert all(
        candidate["step"] > 0
        for candidate in report.json_report["candidate_configs"]
        if candidate["parameter"] in {"market_context_weight", "freshness_weight"}
    )
    threshold_candidates = [
        candidate
        for candidate in report.json_report["candidate_configs"]
        if candidate["parameter"] == "secondary_bucket_threshold"
    ]
    assert threshold_candidates
    assert {
        candidate["holdout_primary_metric"]["metric"]
        for candidate in threshold_candidates
    } == {"secondary_bucket_average_excess_return_pct"}


def test_monthly_review_does_not_fall_back_to_validated_older_same_day_run() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        old_run = _add_run(session)
        old_run.created_at = datetime(2026, 6, 1, 10, tzinfo=timezone.utc)
        old_candidate = _add_candidate(session, old_run)
        _add_validation_result(session, old_candidate)
        latest_run = _add_run(session)
        latest_run.created_at = datetime(2026, 6, 2, 1, tzinfo=timezone.utc)
        _add_candidate(session, latest_run)
        session.commit()

        rows = validation_rows_from_results(
            session,
            market="TW",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 30),
        )

    assert rows == []


def test_daily_radar_monthly_report_aggregates_watermarks_and_bounds_detail_to_six_months() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            DailyRadarForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        run_months = [
            date(2025 + offset // 12, offset % 12 + 1, 1)
            for offset in range(19)
        ]
        for index, run_date in enumerate(run_months, start=1):
            run = _add_run(session)
            run.run_date = run_date
            run.created_at = datetime(
                run_date.year,
                run_date.month,
                1,
                tzinfo=timezone.utc,
            )
            candidate = _add_candidate(session, run)
            candidate.symbol = f"24{index:02d}.TW"
            candidate.data_dates = {"ohlcv": run.run_date.isoformat()}
            for window in (5, 10, 20):
                session.add(
                    DailyRadarForwardValidationResult(
                        candidate_id=candidate.id,
                        window_days=window,
                        validation_version="daily-radar-forward-validation-v1",
                        status="validated",
                        signal_date=run.run_date,
                        target_date=date(
                            run_date.year,
                            run_date.month,
                            min(25, 1 + window),
                        ),
                        benchmark_symbol="TAIEX",
                        outcome={
                            "forward_return_pct": float(index),
                            "excess_return_vs_benchmark_pct": float(index),
                            "max_adverse_excursion_pct": -1.0,
                            "hit_above_threshold": True,
                        },
                        skip_reason=None,
                    )
                )
        session.commit()

    statements: list[tuple[str, object]] = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        started_at = perf_counter()
        with Session(engine) as session:
            report = build_monthly_rule_review_report(
                session,
                market="TW",
                year=2026,
                month=7,
                min_sample_count=1,
            )
        elapsed_seconds = perf_counter() - started_at
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)

    payload = report.json_report
    assert payload["cohort"]["selected_months"] == [
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
    ]
    assert payload["replay_coverage"]["selected_validation_rows"] == 18
    assert payload["replay_coverage"]["selected_samples"] == 6
    assert payload["replay_coverage"]["coverage"] == 0.0
    assert payload["replay_coverage"]["meets_threshold"] is False
    assert payload["replay_coverage"]["exclusion_reasons"] == {
        "replay_input_incomplete": 6,
    }
    assert all(
        candidate["eligibility_reason"] == "replay_coverage_below_threshold"
        for candidate in payload["candidate_configs"]
    )
    assert len(payload["completeness_watermarks"]) == 19
    assert elapsed_seconds < 5.0
    select_statements = [
        (statement, parameters)
        for statement, parameters in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(select_statements) == 3
    assert any(
        "GROUP BY" in statement
        and "daily_radar_forward_validation_results" in statement
        for statement, _parameters in select_statements
    )
    assert all(
        "2000-01-01" not in str(parameters)
        for _statement, parameters in select_statements
    )
    detail_statements = [
        statement
        for statement, _parameters in select_statements
        if "GROUP BY" not in statement
    ]
    assert detail_statements
    assert all(
        "daily_radar_runs.run_date >= ?" in statement
        for statement in detail_statements
    )


def test_rule_review_workflow_calls_cloud_api_and_uploads_artifacts() -> None:
    workflow = (ROOT.parent / ".github" / "workflows" / "monthly-analysis-calibration.yml").read_text(
        encoding="utf-8"
    )

    assert "/internal/daily-radar/rule-review/monthly" in workflow
    assert "/internal/analysis-calibration/monthly" in workflow
    assert "${{ secrets.DAILY_RADAR_API_BASE_URL }}" in workflow
    assert "${{ secrets.DAILY_RADAR_INTERNAL_TOKEN }}" in workflow
    assert "Authorization: Bearer ${CALIBRATION_INTERNAL_TOKEN}" in workflow
    assert "reports/calibration/monthly" in workflow
    assert "manifest.json" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "min_replay_coverage: 0.9" in workflow
    assert "retention-days: 30" in workflow


def _sqlite_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _add_run(session: Session) -> DailyRadarRun:
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
    return run


def _add_candidate(session: Session, run: DailyRadarRun) -> DailyRadarCandidate:
    candidate = DailyRadarCandidate(
        run_id=run.id,
        symbol="2330.TW",
        name="TSMC",
        primary_bucket="institutional_accumulation",
        secondary_buckets=["price_volume_strengthening"],
        observation_score=88,
        bucket_scores={"institutional_accumulation": 88},
        risk_labels=["market_weakness"],
        matched_rules=[
            {"rule_id": "institutional_consecutive_flow"},
            {"rule_id": "price_volume_obv_rising"},
        ],
        explanation="Observation summary.",
        repeat_status="new",
        score_breakdown={
            "market_context": {"details": {"regime": "constructive"}},
            "relative_strength": {"freshness": "fresh", "relative_value": 0.03},
        },
        input_snapshot={"market_context": {"regime": "constructive"}},
        data_dates={"ohlcv": "2026-06-01"},
    )
    session.add(candidate)
    session.flush()
    return candidate


def _add_validation_result(session: Session, candidate: DailyRadarCandidate) -> None:
    session.add(
        DailyRadarForwardValidationResult(
            candidate_id=candidate.id,
            window_days=5,
            validation_version="daily-radar-forward-validation-v1",
            status="validated",
            signal_date=date(2026, 6, 1),
            target_date=date(2026, 6, 8),
            benchmark_symbol="TAIEX",
            outcome={
                "forward_return_pct": 10.0,
                "excess_return_vs_benchmark_pct": 8.0,
                "hit_above_threshold": True,
            },
            skip_reason=None,
        )
    )
