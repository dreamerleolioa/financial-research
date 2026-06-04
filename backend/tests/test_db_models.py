# backend/tests/test_db_models.py
from ai_stock_sentinel.db.models import (
    DailyAnalysisLog,
    DailyRadarCandidate,
    DailyRadarRun,
    StockAnalysisCache,
    StockRawData,
    UserPortfolio,
)
from ai_stock_sentinel.db.session import Base
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB


def test_user_table_exists_in_base():
    """users 表應在 Base.metadata 中。"""
    assert "users" in Base.metadata.tables


def test_user_portfolio_model_columns():
    cols = {c.name for c in UserPortfolio.__table__.columns}
    assert {
        "id", "user_id", "symbol", "entry_price", "quantity", "entry_date", "is_active",
        "exit_date", "exit_price", "exit_quantity", "exit_fees", "exit_taxes",
        "realized_pnl", "realized_return_pct", "holding_days",
    } <= cols


def test_user_portfolio_has_active_only_unique_index():
    unique_constraints = {
        constraint.name
        for constraint in UserPortfolio.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    indexes = {index.name: index for index in UserPortfolio.__table__.indexes}
    active_index = indexes["uq_portfolio_user_symbol_active"]

    assert "uq_portfolio_user_symbol" not in unique_constraints
    assert active_index.unique is True
    assert tuple(column.name for column in active_index.columns) == ("user_id", "symbol")
    assert str(active_index.dialect_options["postgresql"]["where"]) == "is_active = true"
    assert str(active_index.dialect_options["sqlite"]["where"]) == "is_active = 1"


def test_daily_analysis_log_has_analysis_is_final():
    """DailyAnalysisLog 必須有 analysis_is_final 欄位（由 is_final rename 而來）。"""
    cols = {c.name for c in DailyAnalysisLog.__table__.columns}
    assert "analysis_is_final" in cols


def test_daily_analysis_log_model_columns():
    cols = {c.name for c in DailyAnalysisLog.__table__.columns}
    assert {
        "id", "user_id", "symbol", "record_date", "signal_confidence",
        "action_tag", "indicators", "final_verdict",
        "prev_action_tag", "prev_confidence", "analysis_is_final",
    } <= cols


def test_stock_raw_data_model_columns():
    cols = {c.name for c in StockRawData.__table__.columns}
    assert {
        "id", "symbol", "record_date", "technical", "institutional",
        "fundamental", "raw_data_is_final", "fetched_at",
    } <= cols


def test_stock_analysis_cache_model_columns():
    cols = {c.name for c in StockAnalysisCache.__table__.columns}
    assert {
        "id", "symbol", "record_date", "signal_confidence",
        "action_tag", "indicators", "final_verdict",
        "prev_action_tag", "prev_confidence", "analysis_is_final", "created_at", "updated_at",
    } <= cols


def test_stock_analysis_cache_has_full_result_column() -> None:
    from ai_stock_sentinel.db.models import StockAnalysisCache
    assert hasattr(StockAnalysisCache, "full_result")


def test_daily_radar_run_table_name_and_columns() -> None:
    assert DailyRadarRun.__tablename__ == "daily_radar_runs"

    cols = {c.name for c in DailyRadarRun.__table__.columns}
    assert {
        "id", "run_date", "market", "status", "started_at", "finished_at",
        "universe_count", "prefilter_count", "candidate_count", "errors", "created_at",
    } <= cols


def test_daily_radar_candidate_table_name_and_columns() -> None:
    assert DailyRadarCandidate.__tablename__ == "daily_radar_candidates"

    cols = {c.name for c in DailyRadarCandidate.__table__.columns}
    assert {
        "id", "run_id", "symbol", "name", "primary_bucket", "secondary_buckets",
        "observation_score", "bucket_scores", "risk_labels", "matched_rules",
        "explanation", "repeat_status", "score_breakdown", "input_snapshot",
        "data_dates", "created_at",
    } <= cols


def test_daily_radar_candidate_relates_to_run_with_back_populates() -> None:
    assert DailyRadarCandidate.__table__.c.run_id.foreign_keys
    fk = next(iter(DailyRadarCandidate.__table__.c.run_id.foreign_keys))
    assert fk.column.table.name == "daily_radar_runs"
    assert fk.column.name == "id"

    assert DailyRadarCandidate.run.property.back_populates == "candidates"
    assert DailyRadarRun.candidates.property.back_populates == "run"


def test_daily_radar_json_fields_use_jsonb_and_accept_payloads() -> None:
    run = DailyRadarRun(errors=[{"code": "fixture_gap"}])
    candidate = DailyRadarCandidate(
        bucket_scores={"momentum": 72},
        risk_labels=["stale_data"],
        matched_rules=["volume_expansion"],
        score_breakdown={"base": 60, "risk_penalty": -5},
        input_snapshot={"symbol": "2330.TW", "close": 980},
        data_dates={"ohlcv": "2026-06-01"},
    )

    assert run.errors == [{"code": "fixture_gap"}]
    assert candidate.bucket_scores == {"momentum": 72}
    assert candidate.risk_labels == ["stale_data"]
    assert candidate.matched_rules == ["volume_expansion"]
    assert candidate.score_breakdown == {"base": 60, "risk_penalty": -5}
    assert candidate.input_snapshot == {"symbol": "2330.TW", "close": 980}
    assert candidate.data_dates == {"ohlcv": "2026-06-01"}

    json_columns = {
        DailyRadarRun.__table__.c.errors,
        DailyRadarCandidate.__table__.c.bucket_scores,
        DailyRadarCandidate.__table__.c.risk_labels,
        DailyRadarCandidate.__table__.c.matched_rules,
        DailyRadarCandidate.__table__.c.score_breakdown,
        DailyRadarCandidate.__table__.c.input_snapshot,
        DailyRadarCandidate.__table__.c.data_dates,
    }
    assert all(isinstance(column.type, JSONB) for column in json_columns)


def test_daily_radar_indexes_and_unique_candidate_symbol_per_run() -> None:
    run_index_columns = {
        tuple(column.name for column in index.columns)
        for index in DailyRadarRun.__table__.indexes
    }
    candidate_index_columns = {
        tuple(column.name for column in index.columns)
        for index in DailyRadarCandidate.__table__.indexes
    }
    unique_constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in DailyRadarCandidate.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("run_date",) in run_index_columns
    assert ("symbol",) in candidate_index_columns
    assert ("primary_bucket",) in candidate_index_columns
    assert ("observation_score",) in candidate_index_columns
    assert ("run_id", "symbol") in unique_constraints
