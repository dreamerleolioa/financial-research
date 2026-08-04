from __future__ import annotations

import json
import importlib.util
from copy import deepcopy
from datetime import date
from io import StringIO
from pathlib import Path
from time import perf_counter
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.analysis.calibration import (
    ANALYSIS_FORWARD_VALIDATION_VERSION,
    build_general_analysis_monthly_report,
    capture_general_analysis_calibration_sample,
    evaluate_general_analysis_forward_validation,
    general_validation_samples,
    upsert_general_analysis_validation_results,
)
from ai_stock_sentinel.analysis.confidence_scorer import (
    CONFIDENCE_CONFIG_VERSION,
    ConfidenceScoringConfig,
    adjust_confidence_by_divergence,
)
from ai_stock_sentinel.config import STRATEGY_VERSION
from ai_stock_sentinel.calibration.router import (
    _exclude_persisted_general_analysis_windows,
)
from ai_stock_sentinel.calibration.price_provider import get_forward_price_provider
from ai_stock_sentinel.calibration.governance import (
    independent_block_count,
    independent_sample_counts_by_window,
    required_block_counts,
)
from ai_stock_sentinel import api
from ai_stock_sentinel.db.models import (
    AnalysisCalibrationSample,
    AnalysisForwardValidationResult,
    StockRawData,
)
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.db.session import get_db


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


def test_confidence_config_preserves_baseline_and_can_replay_one_parameter_step() -> None:
    baseline, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    candidate, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
        config=ConfidenceScoringConfig(positive_sentiment_points=6),
    )

    assert baseline == 70
    assert candidate == 71
    assert ConfidenceScoringConfig().distribution_penalty == -10


def test_final_general_analysis_capture_is_append_only_deduplicated_and_private() -> None:
    engine = _engine()
    Base.metadata.create_all(engine, tables=[AnalysisCalibrationSample.__table__])
    result = _analysis_result()

    with Session(engine) as session:
        first = capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 1, 5),
            result=result,
            is_final=True,
        )
        second = capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 1, 5),
            result=result,
            is_final=True,
        )
        session.commit()

        assert first is not None
        assert second is first
        assert session.scalar(select(func.count()).select_from(AnalysisCalibrationSample)) == 1
        stored = session.execute(select(AnalysisCalibrationSample)).scalar_one()

    encoded = json.dumps(stored.replay_input, ensure_ascii=False, sort_keys=True)
    assert stored.analysis_type == "general"
    assert stored.market == "TW"
    assert stored.benchmark_symbol == "TAIEX"
    assert stored.analysis_is_final is True
    assert stored.strategy_type == "short_term"
    assert stored.replay_input["schema_version"] == "general-analysis-replay-input-v1"
    assert stored.replay_input["news_sentiment"] == "positive"
    assert "user_id" not in encoded
    assert "private note" not in encoded
    assert "full news body" not in encoded


def test_general_calibration_canonicalizes_same_day_reruns_and_current_versions() -> None:
    engine = _engine()
    Base.metadata.create_all(engine, tables=[AnalysisCalibrationSample.__table__])
    with Session(engine) as session:
        first = capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 1, 5),
            result=_analysis_result(),
            is_final=True,
        )
        assert first is not None
        changed = deepcopy(_analysis_result())
        changed["cleaned_news"]["sentiment_label"] = "negative"
        rerun = capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 1, 5),
            result=changed,
            is_final=True,
        )
        assert rerun is first
        session.add(
            AnalysisCalibrationSample(
                symbol="2330.TW",
                record_date=date(2026, 1, 5),
                analysis_type="general",
                market="TW",
                benchmark_symbol="TAIEX",
                strategy_version=STRATEGY_VERSION,
                confidence_config_version="legacy-confidence",
                input_hash=first.input_hash,
                replay_input=deepcopy(first.replay_input),
                output_snapshot=deepcopy(first.output_snapshot),
                analysis_is_final=True,
            )
        )
        session.commit()
        first_id = first.id

        samples = general_validation_samples(session)

    assert [sample["sample_id"] for sample in samples] == [first_id]


def test_intraday_general_analysis_is_not_captured() -> None:
    engine = _engine()
    Base.metadata.create_all(engine, tables=[AnalysisCalibrationSample.__table__])
    with Session(engine) as session:
        captured = capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 1, 5),
            result=_analysis_result(),
            is_final=False,
        )
        assert captured is None
        assert session.scalar(select(func.count()).select_from(AnalysisCalibrationSample)) == 0


def test_non_tw_general_analysis_is_not_added_to_tw_calibration() -> None:
    engine = _engine()
    Base.metadata.create_all(engine, tables=[AnalysisCalibrationSample.__table__])
    with Session(engine) as session:
        captured = capture_general_analysis_calibration_sample(
            session,
            symbol="AAPL",
            record_date=date(2026, 1, 5),
            result=_analysis_result(),
            is_final=True,
        )

        assert captured is None
        assert session.scalar(select(func.count()).select_from(AnalysisCalibrationSample)) == 0


def test_general_analysis_forward_validation_persists_all_windows_idempotently() -> None:
    engine = _engine()
    Base.metadata.create_all(
        engine,
        tables=[
            AnalysisCalibrationSample.__table__,
            AnalysisForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 1, 5),
            result=_analysis_result(),
            is_final=True,
        )
        session.commit()
        samples = general_validation_samples(session)
        prices = [
            _price("2026-01-05", 100),
            _price("2026-01-06", 101),
            _price("2026-01-07", 102),
            _price("2026-01-08", 103),
            _price("2026-01-09", 104),
            _price("2026-01-12", 105),
        ]
        benchmark = [
            _price("2026-01-05", 1000),
            _price("2026-01-06", 1001),
            _price("2026-01-07", 1002),
            _price("2026-01-08", 1003),
            _price("2026-01-09", 1004),
            _price("2026-01-12", 1005),
        ]
        report, outcomes = evaluate_general_analysis_forward_validation(
            samples,
            price_series_by_symbol={"2330.TW": prices},
            benchmark_prices=benchmark,
            as_of_date=date(2026, 1, 12),
            windows=[5],
            due_only=True,
        )
        first = upsert_general_analysis_validation_results(session, outcomes)
        second = upsert_general_analysis_validation_results(session, outcomes)
        session.commit()

        assert report["metadata"]["track"] == "general_analysis"
        assert first["validated_count"] == 1
        assert first["retryable_skipped_count"] == 0
        assert first["terminal_skipped_count"] == 0
        assert second["records_written"] == 1
        assert session.scalar(select(func.count()).select_from(AnalysisForwardValidationResult)) == 1


def test_general_due_mode_recomputes_candidate_refresh_after_benchmark_expands(monkeypatch) -> None:
    engine = _engine()
    Base.metadata.create_all(
        engine,
        tables=[
            AnalysisCalibrationSample.__table__,
            AnalysisForwardValidationResult.__table__,
            StockRawData.__table__,
        ],
    )
    with Session(engine) as session:
        capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 6, 1),
            result=_analysis_result(),
            is_final=True,
        )
        for symbol, rows in {
            "2330.TW": [
                (date(2026, 6, 1), 100),
                (date(2026, 6, 2), 101),
                (date(2026, 6, 3), 102),
                (date(2026, 6, 4), 999),
            ],
            "TAIEX": [
                (date(2026, 6, 1), 1000),
                (date(2026, 6, 2), 1001),
                (date(2026, 6, 3), 1002),
            ],
        }.items():
            for row_date, close in rows:
                _add_raw(session, symbol, row_date, close)
        session.commit()

    class ExpandingBenchmarkProvider:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def fetch(self, symbols, *, start_date, end_date):
            ordered = list(symbols)
            self.calls.append(ordered)
            rows = {
                "TAIEX": [
                    _price("2026-06-01", 1000),
                    _price("2026-06-02", 1001),
                    _price("2026-06-03", 1002),
                    _price("2026-06-05", 1003),
                ],
                "2330.TW": [
                    _price("2026-06-01", 100),
                    _price("2026-06-02", 101),
                    _price("2026-06-03", 102),
                    _price("2026-06-04", 999),
                    _price("2026-06-05", 103),
                ],
            }
            return {symbol: rows[symbol] for symbol in ordered}

    provider = ExpandingBenchmarkProvider()
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    api.app.dependency_overrides[get_forward_price_provider] = lambda: provider
    try:
        response = TestClient(api.app).post(
            "/internal/analysis-calibration/forward-validation/run",
            json={
                "mode": "due",
                "market": "TW",
                "as_of_date": "2026-06-05",
                "windows": [3],
                "benchmark_symbol": "TAIEX",
            },
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)
        api.app.dependency_overrides.pop(get_forward_price_provider, None)

    assert response.status_code == 200
    assert response.json()["validated_count"] == 1
    assert provider.calls == [["TAIEX"], ["2330.TW"]]

    with Session(engine) as session:
        result = session.execute(select(AnalysisForwardValidationResult)).scalar_one()

    assert result.target_date == date(2026, 6, 5)


def test_general_analysis_retryable_skip_is_retried_but_stale_is_terminal() -> None:
    engine = _engine()
    Base.metadata.create_all(
        engine,
        tables=[
            AnalysisCalibrationSample.__table__,
            AnalysisForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        sample = capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 1, 5),
            result=_analysis_result(),
            is_final=True,
        )
        assert sample is not None
        session.flush()
        retryable_summary = upsert_general_analysis_validation_results(
            session,
            [
                {
                    "sample_id": sample.id,
                    "window_days": 5,
                    "validation_version": ANALYSIS_FORWARD_VALIDATION_VERSION,
                    "status": "skipped",
                    "signal_date": "2026-01-05",
                    "benchmark_symbol": "TAIEX",
                    "outcome": {},
                    "skip_reason": "missing_benchmark",
                }
            ],
        )

        pending = _exclude_persisted_general_analysis_windows(
            session,
            {f"id:{sample.id}": [5]},
        )
        terminal_summary = upsert_general_analysis_validation_results(
            session,
            [
                {
                    "sample_id": sample.id,
                    "window_days": 5,
                    "validation_version": ANALYSIS_FORWARD_VALIDATION_VERSION,
                    "status": "skipped",
                    "signal_date": "2026-01-05",
                    "benchmark_symbol": "TAIEX",
                    "outcome": {},
                    "skip_reason": "stale_candidate_price",
                }
            ],
        )
        stale = _exclude_persisted_general_analysis_windows(
            session,
            {f"id:{sample.id}": [5]},
        )
        result = session.execute(select(AnalysisForwardValidationResult)).scalar_one()
        result.status = "validated"
        result.skip_reason = None
        session.flush()
        completed = _exclude_persisted_general_analysis_windows(
            session,
            {f"id:{sample.id}": [5]},
        )

    assert retryable_summary["skipped_count"] == 1
    assert retryable_summary["retryable_skipped_count"] == 1
    assert retryable_summary["terminal_skipped_count"] == 0
    assert terminal_summary["skipped_count"] == 1
    assert terminal_summary["retryable_skipped_count"] == 0
    assert terminal_summary["terminal_skipped_count"] == 1
    assert pending == {f"id:{sample.id}": [5]}
    assert stale == {}
    assert completed == {}


def test_general_legacy_v1_result_does_not_block_current_validation_version() -> None:
    engine = _engine()
    Base.metadata.create_all(
        engine,
        tables=[
            AnalysisCalibrationSample.__table__,
            AnalysisForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        sample = capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 1, 5),
            result=_analysis_result(),
            is_final=True,
        )
        assert sample is not None
        session.flush()
        session.add(
            AnalysisForwardValidationResult(
                sample_id=sample.id,
                window_days=5,
                validation_version="general-analysis-forward-validation-v1",
                status="validated",
                signal_date=date(2026, 1, 5),
                target_date=date(2026, 1, 12),
                benchmark_symbol="TAIEX",
                outcome={"forward_return_pct": 1.0},
                skip_reason=None,
            )
        )
        session.flush()

        pending = _exclude_persisted_general_analysis_windows(
            session,
            {f"id:{sample.id}": [5]},
        )

    assert ANALYSIS_FORWARD_VALIDATION_VERSION == "general-analysis-forward-validation-v2"
    assert pending == {f"id:{sample.id}": [5]}


def test_general_monthly_report_runs_with_insufficient_mature_samples() -> None:
    engine = _engine()
    Base.metadata.create_all(
        engine,
        tables=[
            AnalysisCalibrationSample.__table__,
            AnalysisForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        capture_general_analysis_calibration_sample(
            session,
            symbol="2330.TW",
            record_date=date(2026, 1, 5),
            result=_analysis_result(),
            is_final=True,
        )
        session.commit()
        report, markdown = build_general_analysis_monthly_report(
            session,
            through_year=2026,
            through_month=1,
            min_sample_count=1,
        )

    assert report["cohort"]["cohort_complete"] is False
    assert report["auto_change_eligible"] is False
    assert all(
        candidate["eligibility_reason"] == "insufficient_mature_months"
        for candidate in report["candidate_configs"]
    )
    assert "# General Analysis Confidence Review" in markdown


def test_general_monthly_report_uses_six_mature_months_and_date_blocks() -> None:
    engine = _engine()
    Base.metadata.create_all(
        engine,
        tables=[
            AnalysisCalibrationSample.__table__,
            AnalysisForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        for month in range(1, 7):
            result = deepcopy(_analysis_result())
            result["cleaned_news"]["sentiment_label"] = (
                "positive" if month % 2 else "neutral"
            )
            sample = capture_general_analysis_calibration_sample(
                session,
                symbol=f"23{month:02d}.TW",
                record_date=date(2026, month, 5),
                result=result,
                is_final=True,
            )
            assert sample is not None
            session.flush()
            for window in (5, 10, 20):
                session.add(
                    AnalysisForwardValidationResult(
                        sample_id=sample.id,
                        window_days=window,
                        validation_version=ANALYSIS_FORWARD_VALIDATION_VERSION,
                        status="validated",
                        signal_date=sample.record_date,
                        target_date=date(2026, month, min(25, 5 + window)),
                        benchmark_symbol="TAIEX",
                        outcome={
                            "forward_return_pct": float(month + window),
                            "excess_return_vs_benchmark_pct": float(month),
                            "max_adverse_excursion_pct": -float(month) / 10,
                            "hit_above_threshold": True,
                        },
                        skip_reason=None,
                    )
                )
        session.commit()

        report, _ = build_general_analysis_monthly_report(
            session,
            through_year=2026,
            through_month=6,
            min_sample_count=1,
        )

    assert report["cohort"]["training_months"] == [
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
    ]
    assert report["cohort"]["holdout_month"] == "2026-06"
    assert all(row["maturity_complete"] is True for row in report["completeness_watermarks"])
    candidate = report["candidate_configs"][0]
    assert set(candidate["training"]["before"]) == {"5", "10", "20"}
    assert candidate["training_block_bootstrap"]["seed"] == 20260724
    assert candidate["training_block_bootstrap"]["block_count"] == 5
    assert candidate["coverage"]["training_independent_samples_by_window"] == {
        "5": 5,
        "10": 5,
        "20": 5,
    }
    assert candidate["coverage"]["holdout_independent_samples_by_window"] == {
        "5": 1,
        "10": 1,
        "20": 1,
    }
    assert candidate["coverage"]["training_block_count"] == 5
    assert candidate["coverage"]["holdout_block_count"] == 1


def test_general_monthly_replay_coverage_uses_only_optimizer_scope_strategies() -> None:
    engine = _engine()
    Base.metadata.create_all(
        engine,
        tables=[
            AnalysisCalibrationSample.__table__,
            AnalysisForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        for month in range(1, 7):
            for strategy_index, strategy_type in enumerate(
                ("short_term", "defensive_wait"),
            ):
                result = deepcopy(_analysis_result())
                result["strategy_type"] = strategy_type
                sample = capture_general_analysis_calibration_sample(
                    session,
                    symbol=f"{month + 2}{strategy_index}00.TW",
                    record_date=date(2026, month, 5),
                    result=result,
                    is_final=True,
                )
                assert sample is not None
                session.flush()
                for window in (5, 10, 20):
                    session.add(
                        AnalysisForwardValidationResult(
                            sample_id=sample.id,
                            window_days=window,
                            validation_version=ANALYSIS_FORWARD_VALIDATION_VERSION,
                            status="validated",
                            signal_date=sample.record_date,
                            target_date=date(2026, month, min(25, 5 + window)),
                            benchmark_symbol="TAIEX",
                            outcome={
                                "forward_return_pct": float(month),
                                "excess_return_vs_benchmark_pct": float(month),
                                "max_adverse_excursion_pct": -1.0,
                                "hit_above_threshold": True,
                            },
                            skip_reason=None,
                        )
                    )
        session.commit()

        report, _ = build_general_analysis_monthly_report(
            session,
            through_year=2026,
            through_month=6,
            min_sample_count=1,
        )

    coverage = report["coverage"]
    assert coverage["all_selected_validation_rows"] == 36
    assert coverage["selected_validation_rows"] == 18
    assert coverage["optimizer_scope_validation_rows"] == 18
    assert coverage["selected_samples"] == 6
    assert coverage["replay_eligible_samples"] == 6
    assert coverage["replay_coverage"] == 1.0
    assert coverage["meets_threshold"] is True
    assert coverage["exclusion_reasons"] == {
        "strategy_type_excluded:defensive_wait": 6,
    }
    assert all(
        candidate["eligibility_reason"] != "replay_coverage_below_threshold"
        for candidate in report["candidate_configs"]
    )


def test_governance_counts_signals_and_dates_instead_of_validation_rows() -> None:
    rows = [
        {
            "sample_id": sample_id,
            "record_date": "2026-06-05",
            "window_days": window,
        }
        for sample_id in range(1, 8)
        for window in (5, 10, 20)
    ]

    assert independent_sample_counts_by_window(
        rows,
        sample_key="sample_id",
    ) == {"5": 7, "10": 7, "20": 7}
    assert independent_block_count(rows, block_key="record_date") == 1
    assert required_block_counts() == (20, 5)


def test_general_monthly_report_aggregates_watermarks_and_bounds_detail_to_six_months() -> None:
    engine = _engine()
    Base.metadata.create_all(
        engine,
        tables=[
            AnalysisCalibrationSample.__table__,
            AnalysisForwardValidationResult.__table__,
        ],
    )
    with Session(engine) as session:
        record_months = [
            date(2025 + offset // 12, offset % 12 + 1, 5)
            for offset in range(19)
        ]
        for index, record_date in enumerate(record_months, start=1):
            sample = capture_general_analysis_calibration_sample(
                session,
                symbol=f"24{index:02d}.TW",
                record_date=record_date,
                result=_analysis_result(),
                is_final=True,
            )
            assert sample is not None
            if index == len(record_months):
                sample.replay_input = {"schema_version": "legacy-incomplete"}
            session.flush()
            for window in (5, 10, 20):
                session.add(
                    AnalysisForwardValidationResult(
                        sample_id=sample.id,
                        window_days=window,
                        validation_version=ANALYSIS_FORWARD_VALIDATION_VERSION,
                        status="validated",
                        signal_date=sample.record_date,
                        target_date=date(
                            record_date.year,
                            record_date.month,
                            min(25, 5 + window),
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
            report, _ = build_general_analysis_monthly_report(
                session,
                through_year=2026,
                through_month=7,
                min_sample_count=1,
            )
        elapsed_seconds = perf_counter() - started_at
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)

    assert report["cohort"]["selected_months"] == [
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
    ]
    assert report["coverage"]["selected_validation_rows"] == 18
    assert report["coverage"]["selected_samples"] == 6
    assert report["coverage"]["replay_eligible_samples"] == 5
    assert report["coverage"]["replay_coverage"] == 0.8333
    assert report["coverage"]["meets_threshold"] is False
    assert report["coverage"]["exclusion_reasons"] == {
        "replay_input_incomplete": 1,
    }
    assert all(
        candidate["eligibility_reason"] == "replay_coverage_below_threshold"
        for candidate in report["candidate_configs"]
    )
    assert len(report["completeness_watermarks"]) == 19
    assert elapsed_seconds < 5.0
    select_statements = [
        (statement, parameters)
        for statement, parameters in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(select_statements) == 3
    assert any(
        "GROUP BY" in statement
        and "analysis_forward_validation_results" in statement
        for statement, _parameters in select_statements
    )
    assert all(
        "2000-01-01" not in str(parameters)
        for _statement, parameters in select_statements
    )
    assert any(
        "analysis_calibration_samples.record_date >= ?" in statement
        for statement, _parameters in select_statements
    )


def test_general_calibration_track_does_not_import_daily_radar_evaluator_or_auth() -> None:
    root = Path(__file__).parents[1] / "src" / "ai_stock_sentinel"
    calibration_source = (root / "analysis" / "calibration.py").read_text(
        encoding="utf-8"
    )
    router_source = (root / "calibration" / "router.py").read_text(
        encoding="utf-8"
    )

    assert "daily_radar.forward_validation" not in calibration_source
    assert "daily_radar.forward_validation" not in router_source
    assert "daily_radar.auth" not in router_source
    assert "calibration.forward_validation" in calibration_source
    assert "calibration.forward_validation" in router_source


def test_general_monthly_internal_api_returns_json_and_markdown(monkeypatch) -> None:
    engine = _engine()
    Base.metadata.create_all(
        engine,
        tables=[
            AnalysisCalibrationSample.__table__,
            AnalysisForwardValidationResult.__table__,
        ],
    )
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    api.app.dependency_overrides[get_db] = lambda: Session(engine)
    try:
        response = TestClient(api.app).post(
            "/internal/analysis-calibration/monthly",
            json={"year": 2026, "month": 1, "min_sample_count": 1},
            headers={"Authorization": "Bearer test-token"},
        )
    finally:
        api.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["through_month"] == "2026-01"
    assert payload["report_json"]["metadata"]["track"] == "general_analysis"
    assert "# General Analysis Confidence Review" in payload["report_markdown"]


def test_forward_validation_and_monthly_workflows_fail_on_gaps_and_encrypt_artifacts() -> None:
    root = Path(__file__).parents[2]
    daily = (root / ".github" / "workflows" / "analysis-forward-validation.yml").read_text(
        encoding="utf-8"
    )
    daily_radar = (root / ".github" / "workflows" / "daily-radar.yml").read_text(
        encoding="utf-8"
    )
    monthly = (root / ".github" / "workflows" / "monthly-analysis-calibration.yml").read_text(
        encoding="utf-8"
    )

    assert 'cron: "50 15 * * 1-5"' in daily_radar
    assert "run-forward-validation" in daily_radar
    assert "/internal/daily-radar/forward-validation/run" in daily_radar
    assert "/internal/analysis-calibration/forward-validation/run" in daily
    assert '.status == "completed" and .retryable_skipped_count == 0' in daily_radar
    assert '.status == "completed" and .retryable_skipped_count == 0' in daily
    assert '.status == "completed" and .skipped_count == 0' not in daily_radar
    assert '.status == "completed" and .skipped_count == 0' not in daily
    assert "retryable_skipped_count" in daily_radar
    assert "terminal_skipped_count" in daily_radar
    assert "retryable_skipped_count" in daily
    assert "terminal_skipped_count" in daily
    assert "skip_reasons: (.report.skip_reasons // {})" in daily_radar
    assert "skip_reasons: (.report.skip_reasons // {})" in daily
    assert 'cron: "0 10 6 * *"' in monthly
    assert "actions/upload-artifact@v4" in monthly
    assert "CALIBRATION_REPORT_PASSPHRASE" in monthly
    assert "--passphrase-fd 0" in monthly
    assert "path: artifacts/*.tar.gz.gpg" in monthly
    assert "issues: write" not in monthly
    assert "contents: write" not in monthly


def test_analysis_calibration_migration_creates_append_only_tables_and_constraints() -> None:
    upgrade_sql = _render_migration_sql("upgrade")
    downgrade_sql = _render_migration_sql("downgrade")

    assert "CREATE TABLE analysis_calibration_samples" in upgrade_sql
    assert "CREATE TABLE analysis_forward_validation_results" in upgrade_sql
    assert "uq_analysis_calibration_sample_identity" in upgrade_sql
    assert "market VARCHAR(20)" in upgrade_sql
    assert "benchmark_symbol VARCHAR(40)" in upgrade_sql
    assert "uq_analysis_forward_validation_sample_window_version" in upgrade_sql
    assert "FOREIGN KEY(sample_id) REFERENCES analysis_calibration_samples (id)" in upgrade_sql
    assert "DROP TABLE analysis_forward_validation_results" in downgrade_sql
    assert "DROP TABLE analysis_calibration_samples" in downgrade_sql


def test_calibration_identity_migration_deduplicates_and_aligns_unique_key() -> None:
    migration = _load_identity_migration()
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    operations = Operations(context)
    original_op = migration.op
    migration.op = operations
    try:
        migration.upgrade()
    finally:
        migration.op = original_op

    upgrade_sql = buffer.getvalue()
    assert "ROW_NUMBER() OVER" in upgrade_sql
    assert "UPDATE analysis_forward_validation_results" in upgrade_sql
    assert "DELETE FROM analysis_calibration_samples" in upgrade_sql
    assert (
        "UNIQUE (analysis_type, market, symbol, record_date, strategy_version, "
        "confidence_config_version)"
    ) in upgrade_sql
    assert "input_hash)" not in upgrade_sql.split("ADD CONSTRAINT", 1)[-1]


def _analysis_result() -> dict:
    return {
        "cleaned_news": {
            "sentiment_label": "positive",
            "sentiment_strength": 1.0,
            "items": [{"body": "full news body"}],
        },
        "institutional_flow": {"flow_label": "institutional_accumulation"},
        "technical_signal": "bullish",
        "cleaned_news_quality": {"quality_flags": []},
        "snapshot": {
            "current_price": 100,
            "recent_closes": [98, 99, 100],
            "user_note": "private note",
        },
        "signal_confidence": 70,
        "strategy_type": "short_term",
        "action_plan_tag": "opportunity",
        "action_plan": {"conviction_level": "high"},
    }


def _price(row_date: str, close: float) -> dict:
    return {
        "date": row_date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
    }


def _add_raw(session: Session, symbol: str, row_date: date, close: float) -> None:
    session.add(
        StockRawData(
            symbol=symbol,
            record_date=row_date,
            technical={
                "ohlcv": {
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                },
            },
            institutional={},
            fundamental={},
            raw_data_is_final=True,
        )
    )


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0a1b2c3d4e5f_add_analysis_calibration_tables.py"
    )
    spec = importlib.util.spec_from_file_location("analysis_calibration_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_identity_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "2c3d4e5f6a7b_align_calibration_sample_identity.py"
    )
    spec = importlib.util.spec_from_file_location("calibration_identity_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_migration_sql(direction: str) -> str:
    migration = _load_migration()
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    operations = Operations(context)
    original_op = migration.op
    migration.op = operations
    try:
        getattr(migration, direction)()
    finally:
        migration.op = original_op
    return buffer.getvalue()
