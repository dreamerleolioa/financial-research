# backend/tests/test_db_models.py
import uuid

from sqlalchemy import CheckConstraint, UniqueConstraint
from ai_stock_sentinel.db.models import (
    DailyAnalysisLog,
    DailyRadarCandidate,
    DailyRadarRun,
    POSITION_EVENT_CONFIDENCE_LEVELS,
    POSITION_EVENT_ENTRY_REASON_CODES,
    POSITION_EVENT_EXIT_REASON_CODES,
    POSITION_EVENT_PLAN_ADHERENCE_VALUES,
    POSITION_EVENT_REASON_CATEGORIES,
    POSITION_LIFECYCLE_ADD_ENTRY_CONDITIONS,
    POSITION_LIFECYCLE_DEFAULT_STOP_RULES,
    POSITION_LIFECYCLE_HOLDING_PERIODS,
    POSITION_LIFECYCLE_SETUP_TYPES,
    POSITION_EVENT_SOURCES,
    POSITION_EVENT_TYPES,
    PositionEvent,
    PositionLifecyclePlan,
    PositionLifecycleReview,
    StockAnalysisCache,
    StockRawData,
    TradeReview,
    UserPortfolio,
)
from ai_stock_sentinel.db.session import Base
from sqlalchemy.dialects.postgresql import JSONB


def test_user_table_exists_in_base():
    """users 表應在 Base.metadata 中。"""
    assert "users" in Base.metadata.tables


def test_user_portfolio_model_columns():
    cols = {c.name for c in UserPortfolio.__table__.columns}
    assert {
        "id", "user_id", "symbol", "entry_price", "quantity", "entry_date", "is_active",
        "exit_date", "exit_price", "exit_quantity", "exit_fees", "exit_taxes",
        "realized_pnl", "realized_return_pct", "holding_days", "position_group_id",
    } <= cols


def test_user_portfolio_position_group_id_default_is_unique_uuid() -> None:
    first = UserPortfolio.__table__.c.position_group_id.default.arg(None)
    second = UserPortfolio.__table__.c.position_group_id.default.arg(None)

    assert len(first) == 36
    assert len(second) == 36
    assert uuid.UUID(first).version == 4
    assert uuid.UUID(second).version == 4
    assert first != second


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


def test_trade_review_model_columns_and_unique_portfolio() -> None:
    cols = {c.name for c in TradeReview.__table__.columns}
    unique_constraints = {
        constraint.name
        for constraint in TradeReview.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert {
        "id", "portfolio_id", "user_id", "position_group_id",
        "symbol", "review_version", "review_result", "evidence_payload",
        "llm_summary", "created_at", "updated_at",
    } <= cols
    assert "uq_trade_review_portfolio_id" in unique_constraints


def test_trade_review_json_payload_fields_are_required_and_present() -> None:
    review_result = {
        "data_quality": {},
        "trade_result": {},
        "entry_review": {},
        "holding_review": {},
        "exit_review": {},
        "operation_review": {},
    }
    evidence_payload = {
        "trade": {},
        "position_group_id": "group-1",
        "path_metrics": {},
        "entry_indicators": {},
        "exit_indicators": {},
        "detected_events": [],
        "data_quality": {},
    }
    review = TradeReview(
        portfolio_id=1,
        user_id=1,
        position_group_id="group-1",
        symbol="2330.TW",
        review_version="trade-review-v1",
        review_result=review_result,
        evidence_payload=evidence_payload,
        llm_summary=None,
    )

    assert set(review.review_result) == set(review_result)
    assert set(review.evidence_payload) == set(evidence_payload)
    assert TradeReview.__table__.c.review_result.nullable is False
    assert TradeReview.__table__.c.evidence_payload.nullable is False
    assert isinstance(TradeReview.__table__.c.review_result.type, JSONB)
    assert isinstance(TradeReview.__table__.c.evidence_payload.type, JSONB)


def test_position_lifecycle_review_model_columns_unique_constraint_and_indexes() -> None:
    cols = {c.name for c in PositionLifecycleReview.__table__.columns}
    unique_constraints = {
        constraint.name
        for constraint in PositionLifecycleReview.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    index_columns = {
        index.name: tuple(column.name for column in index.columns)
        for index in PositionLifecycleReview.__table__.indexes
    }

    assert {
        "id", "user_id", "position_group_id", "symbol", "review_version",
        "review_result", "evidence_payload", "llm_summary", "created_at", "updated_at",
    } <= cols
    assert "portfolio_id" not in cols
    assert "uq_position_lifecycle_review_user_group_version" in unique_constraints
    assert index_columns["idx_position_lifecycle_review_user_id"] == ("user_id",)
    assert index_columns["idx_position_lifecycle_review_position_group_id"] == ("position_group_id",)
    assert index_columns["idx_position_lifecycle_review_symbol"] == ("symbol",)
    assert index_columns["idx_position_lifecycle_review_user_group"] == ("user_id", "position_group_id")


def test_position_lifecycle_review_json_payload_fields_are_required_and_present() -> None:
    review_result = {
        "position_group_id": "group-1",
        "symbol": "2330.TW",
        "lifecycle_review": {},
        "data_quality": {},
    }
    evidence_payload = {
        "position_group_id": "group-1",
        "symbol": "2330.TW",
        "metrics": {},
        "events": [],
        "data_quality": {},
    }
    review = PositionLifecycleReview(
        user_id=1,
        position_group_id="group-1",
        symbol="2330.TW",
        review_version="position-lifecycle-review-v1",
        review_result=review_result,
        evidence_payload=evidence_payload,
        llm_summary=None,
    )

    assert set(review.review_result) == set(review_result)
    assert set(review.evidence_payload) == set(evidence_payload)
    assert PositionLifecycleReview.__table__.c.review_result.nullable is False
    assert PositionLifecycleReview.__table__.c.evidence_payload.nullable is False
    assert isinstance(PositionLifecycleReview.__table__.c.review_result.type, JSONB)
    assert isinstance(PositionLifecycleReview.__table__.c.evidence_payload.type, JSONB)


def test_position_event_model_columns_indexes_and_supported_values() -> None:
    cols = {c.name for c in PositionEvent.__table__.columns}
    index_columns = {
        index.name: tuple(column.name for column in index.columns)
        for index in PositionEvent.__table__.indexes
    }
    check_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in PositionEvent.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "id", "user_id", "position_group_id", "symbol", "event_type", "event_date",
        "price", "quantity", "fees", "taxes", "source_portfolio_id", "note",
        "reason_category", "reason_code", "plan_adherence", "confidence_level",
        "source", "data_quality_note", "created_at", "updated_at",
    } <= cols
    assert POSITION_EVENT_TYPES == (
        "initial_entry", "add_entry", "partial_exit", "full_exit", "manual_adjustment",
    )
    assert POSITION_EVENT_SOURCES == (
        "synthetic_from_portfolio_row", "user_backfilled", "user_recorded_at_event_time",
        "manual_record_correction", "not_recorded",
    )
    assert index_columns["idx_position_event_user_id"] == ("user_id",)
    assert index_columns["idx_position_event_position_group_id"] == ("position_group_id",)
    assert index_columns["idx_position_event_symbol"] == ("symbol",)
    assert index_columns["idx_position_event_event_date"] == ("event_date",)
    assert index_columns["idx_position_event_user_group_date"] == ("user_id", "position_group_id", "event_date")
    assert all(event_type in check_constraints["ck_position_event_event_type"] for event_type in POSITION_EVENT_TYPES)
    assert all(source in check_constraints["ck_position_event_source"] for source in POSITION_EVENT_SOURCES)
    assert all(category in check_constraints["ck_position_event_reason_category"] for category in POSITION_EVENT_REASON_CATEGORIES)
    assert all(code in check_constraints["ck_position_event_reason_code"] for code in POSITION_EVENT_ENTRY_REASON_CODES)
    assert all(code in check_constraints["ck_position_event_reason_code"] for code in POSITION_EVENT_EXIT_REASON_CODES)
    assert all(value in check_constraints["ck_position_event_plan_adherence"] for value in POSITION_EVENT_PLAN_ADHERENCE_VALUES)
    assert all(level in check_constraints["ck_position_event_confidence_level"] for level in POSITION_EVENT_CONFIDENCE_LEVELS)


def test_position_event_decision_fields_are_intent_sensitive_and_nullable() -> None:
    event = PositionEvent(
        user_id=1,
        position_group_id="group-1",
        symbol="2330.TW",
        event_type="initial_entry",
        event_date="2026-01-01",
        price=900,
        quantity=100,
        source="user_recorded_at_event_time",
    )

    assert event.reason_category is None
    assert event.reason_code is None
    assert event.plan_adherence is None
    assert event.confidence_level is None
    assert PositionEvent.__table__.c.reason_category.nullable is True
    assert PositionEvent.__table__.c.reason_code.nullable is True
    assert PositionEvent.__table__.c.plan_adherence.nullable is True
    assert PositionEvent.__table__.c.confidence_level.nullable is True


def test_position_lifecycle_plan_model_fields_allowed_values_and_indexes() -> None:
    cols = {c.name for c in PositionLifecyclePlan.__table__.columns}
    index_columns = {
        index.name: tuple(column.name for column in index.columns)
        for index in PositionLifecyclePlan.__table__.indexes
    }
    check_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in PositionLifecyclePlan.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    unique_constraints = {
        constraint.name
        for constraint in PositionLifecyclePlan.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert {
        "id", "user_id", "position_group_id", "symbol", "source_portfolio_id",
        "thesis", "setup_type", "planned_holding_period", "default_stop_rule",
        "add_entry_condition", "planned_invalidation",
        "planned_stop_price", "planned_target_or_scale_out_rule", "planned_risk_amount",
        "planned_risk_pct", "position_sizing_rationale", "source", "created_after_entry",
        "created_at", "updated_at",
    } <= cols
    assert "uq_position_lifecycle_plan_group" in unique_constraints
    assert index_columns["idx_position_lifecycle_plan_user_id"] == ("user_id",)
    assert index_columns["idx_position_lifecycle_plan_position_group_id"] == ("position_group_id",)
    assert index_columns["idx_position_lifecycle_plan_symbol"] == ("symbol",)
    assert all(setup_type in check_constraints["ck_position_lifecycle_plan_setup_type"] for setup_type in POSITION_LIFECYCLE_SETUP_TYPES)
    assert all(period in check_constraints["ck_position_lifecycle_plan_holding_period"] for period in POSITION_LIFECYCLE_HOLDING_PERIODS)
    assert all(rule in check_constraints["ck_position_lifecycle_plan_default_stop_rule"] for rule in POSITION_LIFECYCLE_DEFAULT_STOP_RULES)
    assert all(condition in check_constraints["ck_position_lifecycle_plan_add_entry_condition"] for condition in POSITION_LIFECYCLE_ADD_ENTRY_CONDITIONS)
    assert all(source in check_constraints["ck_position_lifecycle_plan_source"] for source in POSITION_EVENT_SOURCES)


def test_position_lifecycle_plan_intent_sensitive_fields_are_nullable() -> None:
    plan = PositionLifecyclePlan(
        user_id=1,
        position_group_id="group-1",
        symbol="2330.TW",
        source="user_backfilled",
        created_after_entry=True,
    )

    assert plan.thesis is None
    assert plan.setup_type is None
    assert plan.planned_holding_period is None
    assert plan.default_stop_rule is None
    assert plan.add_entry_condition is None
    assert plan.planned_invalidation is None
    assert plan.planned_stop_price is None
    assert plan.planned_risk_pct is None
    assert plan.source == "user_backfilled"
    assert plan.created_after_entry is True


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
