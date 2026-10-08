from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_stock_sentinel.calibration.governance import DEFAULT_MIN_REPLAY_COVERAGE
from ai_stock_sentinel.daily_radar.constants import (
    DAILY_RADAR_VALIDATION_WINDOWS,
    DAILY_RADAR_BACKGROUND_CONTEXT_TYPES,
    DAILY_RADAR_BUCKETS,
    DAILY_RADAR_REPEAT_STATUSES,
    DAILY_RADAR_RISK_LABELS,
)
from ai_stock_sentinel.daily_radar.forward_validation import (
    DEFAULT_BENCHMARK_SYMBOL,
)
from ai_stock_sentinel.daily_radar.rule_governance import DEFAULT_MIN_SAMPLE_COUNT
from ai_stock_sentinel.daily_radar.types import (
    DailyRadarBucket,
    DailyRadarRepeatStatus,
    DailyRadarRiskLabel,
)


class DailyRadarRunRequest(BaseModel):
    run_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)


class DailyRadarRunTriggerResponse(BaseModel):
    run_id: int
    run_date: date
    market: str
    status: Literal["completed", "running", "failed", "stale_data"]
    universe_count: int
    prefilter_count: int
    candidate_count: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None


class DailyRadarMarketSessionRequest(BaseModel):
    run_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)


class DailyRadarMarketSessionResponse(BaseModel):
    status: Literal["open", "closed"]
    run_date: date
    market: str
    provider: str
    dataset: str


class DailyRadarPreparedRunRequest(BaseModel):
    run_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)
    max_symbols: int = Field(default=250, ge=1, le=250)


class DailyRadarPreparedRunResponse(BaseModel):
    status: Literal["prepared", "completed", "scored"]
    run_date: date
    market: str
    symbol_count: int
    selected_symbols: list[str] = Field(default_factory=list)
    step_statuses: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class DailyRadarRefreshStepRequest(BaseModel):
    run_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)


class DailyRadarRefreshStepResponse(BaseModel):
    status: Literal["completed", "failed"]
    step: str
    run_date: date
    market: str
    symbol_count: int = 0
    selected_symbol_count: int = 0
    records_written: int = 0
    reused_symbols: list[str] = Field(default_factory=list)
    fetched_symbols: list[str] = Field(default_factory=list)
    not_applicable_symbols: list[str] = Field(default_factory=list)
    missing_symbols: list[str] = Field(default_factory=list)
    missing_symbol_reasons: dict[str, str] = Field(default_factory=dict)
    skipped_symbols: list[str] = Field(default_factory=list)
    skipped_symbol_reasons: dict[str, str] = Field(default_factory=dict)
    missing_by_lane: dict[str, list[str]] = Field(default_factory=dict)
    provider_counts: dict[str, int] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class DailyRadarManagedRawDataRefreshRequest(BaseModel):
    run_date: date | None = None
    market: Literal["TW"] = "TW"


class DailyRadarManagedRawDataRefreshResponse(BaseModel):
    status: Literal["completed", "failed"]
    step: Literal["refresh-managed-raw-data"] = "refresh-managed-raw-data"
    run_date: date
    market: str
    target_symbol_count: int = Field(default=0, ge=0)
    active_symbol_count: int = Field(default=0, ge=0)
    recent_analysis_symbol_count: int = Field(default=0, ge=0)
    overlap_symbol_count: int = Field(default=0, ge=0)
    selected_overlap_count: int = Field(default=0, ge=0)
    reused_record_count: int = Field(default=0, ge=0)
    records_written: int = Field(default=0, ge=0)
    missing_record_count: int = Field(default=0, ge=0)
    deferred_recent_symbol_count: int = Field(default=0, ge=0)
    error_codes: list[str] = Field(default_factory=list)


class DailyRadarMarketBarsRefreshRequest(BaseModel):
    run_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)


class DailyRadarMarketBarsRefreshResponse(BaseModel):
    status: Literal["completed", "failed"]
    start_date: date
    end_date: date
    market: str
    records_written: int = 0
    dates_attempted: list[str] = Field(default_factory=list)
    dates_with_data: list[str] = Field(default_factory=list)
    skipped_dates: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class DailyRadarInstitutionalFlowsRefreshRequest(BaseModel):
    run_date: date | None = None
    market: Literal["TW"] = "TW"


class DailyRadarInstitutionalSnapshotResponse(BaseModel):
    market: Literal["TW", "TWO"]
    row_count: int = Field(ge=1)
    source_provider: str = Field(min_length=1)
    source_dataset: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DailyRadarInstitutionalFlowsRefreshResponse(BaseModel):
    status: Literal["completed", "failed"]
    step: Literal["refresh-institutional-flows"] = "refresh-institutional-flows"
    run_date: date
    market: Literal["TW"] = "TW"
    records_written: int = Field(default=0, ge=0)
    markets_attempted: list[Literal["TW", "TWO"]] = Field(default_factory=list)
    markets_completed: list[Literal["TW", "TWO"]] = Field(default_factory=list)
    snapshots: list[DailyRadarInstitutionalSnapshotResponse] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class DailyRadarInstitutionalFlowsBackfillRequest(BaseModel):
    start_date: date
    end_date: date
    market: Literal["TW"] = "TW"


class DailyRadarInstitutionalBackfillSnapshotResponse(
    DailyRadarInstitutionalSnapshotResponse
):
    trade_date: date


class DailyRadarInstitutionalFlowsBackfillResponse(BaseModel):
    status: Literal["completed", "failed"]
    start_date: date
    end_date: date
    market: Literal["TW"] = "TW"
    records_written: int = Field(default=0, ge=0)
    dates_requested: list[date] = Field(default_factory=list)
    dates_attempted: list[date] = Field(default_factory=list)
    dates_completed: list[date] = Field(default_factory=list)
    dates_reused: list[date] = Field(default_factory=list)
    dates_repaired: list[date] = Field(default_factory=list)
    skipped_dates: list[date] = Field(default_factory=list)
    snapshots: list[DailyRadarInstitutionalBackfillSnapshotResponse] = Field(
        default_factory=list
    )
    errors: list[dict[str, Any]] = Field(default_factory=list)


class DailyRadarInstitutionalUniverseReplayRequest(BaseModel):
    run_date: date
    market: Literal["TW"] = "TW"
    track_limit: int = Field(default=50, ge=1, le=100)
    max_symbols: int = Field(default=250, ge=1, le=250)


class DailyRadarInstitutionalUniverseReplayResponse(BaseModel):
    status: Literal["completed"] = "completed"
    run_date: date
    market: Literal["TW"] = "TW"
    report: dict[str, Any]


class DailyRadarChipContextUpdateRequest(BaseModel):
    run_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)
    symbols: list[str] | None = None
    context_types: list[str] = Field(default_factory=lambda: list(DAILY_RADAR_BACKGROUND_CONTEXT_TYPES))


class DailyRadarChipContextUpdateResponse(BaseModel):
    status: Literal["completed", "failed"]
    run_date: date
    market: str
    symbol_count: int
    context_types: list[str]
    records_written: int
    errors: list[dict[str, Any]] = Field(default_factory=list)


class DailyRadarForwardValidationRunRequest(BaseModel):
    mode: Literal["due", "range"] = "due"
    market: str = Field(default="TW", min_length=1, max_length=20)
    as_of_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    windows: list[int] = Field(default_factory=lambda: list(DAILY_RADAR_VALIDATION_WINDOWS), min_length=1)
    benchmark_symbol: str = Field(default=DEFAULT_BENCHMARK_SYMBOL, min_length=1, max_length=40)
    return_basis: Literal["signal_close", "next_open"] = "signal_close"


class DailyRadarObservationStats(BaseModel):
    signal_date_count: int
    selected_count: int
    evaluated_observation_count: int
    evaluated_signal_date_count: int
    evaluated_distinct_symbol_count: int
    missing_outcome_count: int
    missing_diagnostic_count: int
    immature_observation_count: int
    excluded_repeat_count: int
    ranking_pool_complete: bool
    skipped_validation_count: int
    status_counts: dict[str, int]
    missing_reasons: dict[str, int]
    coverage_complete: bool
    confirmation_rate: float | None
    invalidation_rate: float | None
    mean_lead_trading_days: float | None
    mean_waiting_max_adverse_excursion_pct: float | None
    means_scope: str


class DailyRadarPoolQualityStats(BaseModel):
    sample_count: int
    evaluated_count: int
    signal_date_count: int
    distinct_symbol_count: int
    missing_outcome_count: int
    skipped_count: int
    immature_count: int
    missing_metric_count: int
    benchmark_symbols: list[str]
    coverage_complete: bool
    positive_excess_count: int
    positive_excess_rate: float | None
    median_excess_return_pct: float | None


class DailyRadarPoolComparison(BaseModel):
    selected: DailyRadarPoolQualityStats
    top_3: DailyRadarPoolQualityStats
    top_5: DailyRadarPoolQualityStats
    comparable_shadow: DailyRadarPoolQualityStats
    population_scope: Literal["observed_daily_comparable_pool"]
    observed_positive_capture_share: float | None


class DailyRadarResearchQualityStats(DailyRadarPoolQualityStats):
    median_return_pct: float | None
    worst_decile_mean_return_pct: float | None
    worst_adverse_excursion_pct: float | None
    median_adverse_excursion_pct: float | None
    risk_sample_count: int
    missing_risk_count: int
    missing_reasons: dict[str, int]


class DailyRadarResearchConfidence(BaseModel):
    status: Literal["estimated", "insufficient_blocks", "incomplete_coverage", "calendar_missing", "sparse_comparable_dates", "strategy_unknown"]
    method: Literal["paired_daily_moving_block_bootstrap"]
    level: float
    block_trading_days: int
    minimum_blocks: int
    paired_date_count: int
    effective_block_count: int | None
    mean_difference_pct: float | None
    lower_pct: float | None
    upper_pct: float | None


class DailyRadarResearchPoolComparison(DailyRadarPoolComparison):
    selected: DailyRadarResearchQualityStats
    top_3: DailyRadarResearchQualityStats
    top_5: DailyRadarResearchQualityStats
    comparable_shadow: DailyRadarResearchQualityStats
    confidence: DailyRadarResearchConfidence


class DailyRadarResearchComparison(BaseModel):
    validation_version: str
    return_basis: Literal["next_open"]
    price_basis: Literal["unadjusted_price"]
    dividends_included: Literal[False]
    cost_model: Literal["assumed_total_cost_percentage_points"]
    last_evaluated_date: date | None
    calendar_through_date: date | None
    cost_scenarios: dict[str, DailyRadarResearchPoolComparison]


class DailyRadarValidationCohort(BaseModel):
    id: str
    strategy: dict[str, str]
    signal_start_date: date
    signal_end_date: date
    windows: dict[str, dict[str, DailyRadarObservationStats]]
    pool_comparison: dict[str, DailyRadarPoolComparison] = Field(default_factory=dict)
    research_pool_comparison: dict[str, DailyRadarResearchComparison] = Field(default_factory=dict)


class DailyRadarValidationResponse(BaseModel):
    diagnostic_version: str
    as_of_date: date
    sample_start_date: date
    sample_end_date: date
    lookback_days: int
    calendar_through_date: date | None
    last_evaluated_date: date | None
    default_cohort_id: str | None
    cohorts: list[DailyRadarValidationCohort]


class DailyRadarForwardValidationRunResponse(BaseModel):
    status: Literal["completed"]
    mode: Literal["due", "range"]
    market: str
    as_of_date: date
    candidate_count: int
    records_written: int
    validated_count: int
    skipped_count: int
    retryable_skipped_count: int
    terminal_skipped_count: int
    report: dict[str, Any]


class DailyRadarMonthlyRuleReviewRequest(BaseModel):
    market: str = Field(default="TW", min_length=1, max_length=20)
    benchmark_symbol: str = Field(
        default=DEFAULT_BENCHMARK_SYMBOL,
        min_length=1,
        max_length=40,
    )
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    validation_version: str | None = Field(default=None, min_length=1, max_length=80)
    min_sample_count: int = Field(default=DEFAULT_MIN_SAMPLE_COUNT, ge=1, le=10_000)
    min_validated_coverage: float = Field(default=0.9, ge=0, le=1)
    min_replay_coverage: float = Field(
        default=DEFAULT_MIN_REPLAY_COVERAGE,
        ge=0,
        le=1,
    )


class DailyRadarMonthlyRuleReviewResponse(BaseModel):
    status: Literal["completed"]
    market: str
    month: str
    report_json: dict[str, Any]
    report_markdown: str


class DailyRadarNameBackfillRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=10_000)
    dry_run: bool = False


class DailyRadarNameBackfillResponse(BaseModel):
    status: Literal["completed"]
    dry_run: bool
    scanned: int
    updated_candidates: int
    updated_raw_rows: int
    unresolved_symbols: list[str]


class DailyRadarMatchedRule(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "rule_id": "price_volume_close_above_ma20",
                "label": "收盤站回 MA20 且量能同步放大",
                "details": {"close_above_ma20": True, "volume_ratio": 1.42},
            }
        }
    )

    rule_id: str = Field(examples=["price_volume_close_above_ma20"])
    label: str = Field(examples=["收盤站回 MA20 且量能同步放大"])
    details: dict[str, Any] = Field(default_factory=dict)


class DailyRadarCandidateResponse(BaseModel):
    research_status: Literal["trend_forming", "waiting_for_consolidation", "structure_watch", "data_pending"] = "data_pending"
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "symbol": "2330.TW",
                "name": "台積電",
                "primary_bucket": DAILY_RADAR_BUCKETS[1],
                "secondary_buckets": [DAILY_RADAR_BUCKETS[0]],
                "observation_score": 82,
                "risk_labels": [DAILY_RADAR_RISK_LABELS[3]],
                "repeat_status": DAILY_RADAR_REPEAT_STATUSES[0],
                "explanation": "量價轉強觀察：今日收盤站回 MA20，成交量高於 20 日均量，隔日留意量能是否延續。",
                "scoring_version": "daily-radar-scoring-v2.7",
                "rule_version": "daily-radar-rules-v2.6",
                "score_breakdown": {
                    "scoring_version": "daily-radar-scoring-v2.7",
                    "rule_version": "daily-radar-rules-v2.6",
                    "bucket_scores": {
                        DAILY_RADAR_BUCKETS[1]: 82,
                        DAILY_RADAR_BUCKETS[0]: 68,
                    },
                    "cross_confirmation": 6,
                    "market_context": 2,
                    "freshness": 4,
                    "risk_adjustment": -3,
                    "observation_score": 82,
                },
                "matched_rules": [
                    {
                        "rule_id": "price_volume_close_above_ma20",
                        "label": "收盤站回 MA20 且量能同步放大",
                        "details": {"close_above_ma20": True, "volume_ratio": 1.42},
                    }
                ],
                "background_context_labels": [
                    {
                        "context_type": "weekly_major_holders",
                        "label": "大戶持股集中背景",
                        "source": {"domain": "background_context", "provider": "shared_background_context_cache"},
                        "as_of_date": "2026-05-31",
                        "freshness": "fresh",
                        "missing_reason": None,
                        "replay_key": "background_context:2330.TW:weekly_major_holders:2026-05-31",
                        "applicable_consumers": ["daily_radar"],
                    }
                ],
            }
        }
    )

    symbol: str = Field(examples=["2330.TW"], min_length=1)
    name: str = Field(examples=["台積電"], min_length=1)
    primary_bucket: DailyRadarBucket = Field(examples=[DAILY_RADAR_BUCKETS[1]])
    secondary_buckets: list[DailyRadarBucket] = Field(
        default_factory=list,
        examples=[[DAILY_RADAR_BUCKETS[0]]],
    )
    observation_score: int = Field(ge=0, le=100, examples=[82])
    risk_labels: list[DailyRadarRiskLabel] = Field(
        default_factory=list,
        examples=[[DAILY_RADAR_RISK_LABELS[3]]],
    )
    repeat_status: DailyRadarRepeatStatus = Field(
        examples=[DAILY_RADAR_REPEAT_STATUSES[0]],
    )
    explanation: str = Field(
        examples=[
            "量價轉強觀察：今日收盤站回 MA20，成交量高於 20 日均量，隔日留意量能是否延續。"
        ],
    )
    scoring_version: str | None = Field(default=None, examples=["daily-radar-scoring-v2.7"])
    rule_version: str | None = Field(default=None, examples=["daily-radar-rules-v2.6"])
    bucket_scores: dict[str, Any] = Field(default_factory=dict)
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    data_dates: dict[str, date] = Field(default_factory=dict)
    matched_rules: list[DailyRadarMatchedRule] = Field(default_factory=list)
    background_context_labels: list[dict[str, Any]] = Field(default_factory=list)


class DailyRadarDiscoverySummary(BaseModel):
    version: Literal["candidate-pool-discovery-v1"]
    run_date: date
    scanned_symbol_count: int
    eligible_symbol_count: int
    discovered_symbol_count: int
    track_counts: dict[str, int]
    excluded_reason_counts: dict[str, int]


class DailyRadarPoolSummary(BaseModel):
    version: str
    population_scope: Literal["scored_raw_records"]
    input_record_count: int
    state_counts: dict[str, int]
    duplicate_record_count: int
    unclassified_record_count: int
    comparable_shadow_count: int
    eligibility_audit_shadow_count: int
    reason_counts: dict[str, int]
    discovery_summary: DailyRadarDiscoverySummary | None = None


class DailyRadarRunResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "run_date": "2026-06-01",
                "status": "completed",
                "data_dates": {
                    "ohlcv": "2026-06-01",
                    "institutional_flow": "2026-06-01",
                    "margin": "2026-05-31",
                    "market_index": "2026-06-01",
                },
                "market_context": {
                    "index_symbol": "TAIEX",
                    "trend_state": "above_ma20",
                    "volatility_state": "stable",
                    "notes": ["大盤維持主要均線上方，整體波動未擴大"],
                },
                "candidates": [DailyRadarCandidateResponse.model_config["json_schema_extra"]["example"]],
            }
        }
    )

    run_date: date = Field(examples=["2026-06-01"])
    status: Literal["completed", "running", "failed", "stale_data"] = Field(
        examples=["completed"],
    )
    data_dates: dict[str, date] = Field(
        default_factory=dict,
        examples=[
            {
                "ohlcv": "2026-06-01",
                "institutional_flow": "2026-06-01",
                "margin": "2026-05-31",
                "market_index": "2026-06-01",
            }
        ],
    )
    market_context: dict[str, Any] = Field(default_factory=dict)
    pool_summary: DailyRadarPoolSummary | None = None
    candidates: list[DailyRadarCandidateResponse] = Field(default_factory=list)


__all__ = [
    "DailyRadarChipContextUpdateRequest",
    "DailyRadarChipContextUpdateResponse",
    "DailyRadarCandidateResponse",
    "DailyRadarForwardValidationRunRequest",
    "DailyRadarForwardValidationRunResponse",
    "DailyRadarInstitutionalBackfillSnapshotResponse",
    "DailyRadarInstitutionalFlowsBackfillRequest",
    "DailyRadarInstitutionalFlowsBackfillResponse",
    "DailyRadarInstitutionalFlowsRefreshRequest",
    "DailyRadarInstitutionalFlowsRefreshResponse",
    "DailyRadarInstitutionalSnapshotResponse",
    "DailyRadarInstitutionalUniverseReplayRequest",
    "DailyRadarInstitutionalUniverseReplayResponse",
    "DailyRadarMarketSessionRequest",
    "DailyRadarMarketSessionResponse",
    "DailyRadarManagedRawDataRefreshRequest",
    "DailyRadarManagedRawDataRefreshResponse",
    "DailyRadarMatchedRule",
    "DailyRadarMonthlyRuleReviewRequest",
    "DailyRadarMonthlyRuleReviewResponse",
    "DailyRadarNameBackfillRequest",
    "DailyRadarNameBackfillResponse",
    "DailyRadarPreparedRunRequest",
    "DailyRadarPreparedRunResponse",
    "DailyRadarRefreshStepRequest",
    "DailyRadarRefreshStepResponse",
    "DailyRadarRunRequest",
    "DailyRadarRunResponse",
    "DailyRadarRunTriggerResponse",
]
