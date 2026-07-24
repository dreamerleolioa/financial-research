from __future__ import annotations

import json
import importlib.util
from copy import deepcopy
from datetime import date
from io import StringIO
from pathlib import Path
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
    build_general_analysis_monthly_report,
    capture_general_analysis_calibration_sample,
    evaluate_general_analysis_forward_validation,
    general_validation_samples,
    upsert_general_analysis_validation_results,
)
from ai_stock_sentinel.analysis.confidence_scorer import (
    ConfidenceScoringConfig,
    adjust_confidence_by_divergence,
)
from ai_stock_sentinel.calibration.router import (
    _exclude_persisted_general_analysis_windows,
)
from ai_stock_sentinel import api
from ai_stock_sentinel.db.models import (
    AnalysisCalibrationSample,
    AnalysisForwardValidationResult,
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
        assert second["records_written"] == 1
        assert session.scalar(select(func.count()).select_from(AnalysisForwardValidationResult)) == 1


def test_general_analysis_skipped_window_is_retryable() -> None:
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
        result = AnalysisForwardValidationResult(
            sample_id=sample.id,
            window_days=5,
            validation_version="general-analysis-forward-validation-v1",
            status="skipped",
            signal_date=date(2026, 1, 5),
            benchmark_symbol="TAIEX",
            outcome={"skip_reason": "missing_benchmark"},
            skip_reason="missing_benchmark",
        )
        session.add(result)
        session.flush()

        pending = _exclude_persisted_general_analysis_windows(
            session,
            {f"id:{sample.id}": [5]},
        )
        result.status = "validated"
        session.flush()
        completed = _exclude_persisted_general_analysis_windows(
            session,
            {f"id:{sample.id}": [5]},
        )

    assert pending == {f"id:{sample.id}": [5]}
    assert completed == {}


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
                        validation_version="general-analysis-forward-validation-v1",
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
    assert '.status == "completed" and .skipped_count == 0' in daily_radar
    assert '.status == "completed" and .skipped_count == 0' in daily
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
