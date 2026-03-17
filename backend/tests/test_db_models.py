# backend/tests/test_db_models.py
from ai_stock_sentinel.db.models import (
    DailyAnalysisLog,
    StockAnalysisCache,
    StockRawData,
    UserPortfolio,
)
from ai_stock_sentinel.db.session import Base


def test_user_table_exists_in_base():
    """users 表應在 Base.metadata 中。"""
    assert "users" in Base.metadata.tables


def test_user_portfolio_model_columns():
    cols = {c.name for c in UserPortfolio.__table__.columns}
    assert {"id", "user_id", "symbol", "entry_price", "quantity", "entry_date", "is_active"} <= cols


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
