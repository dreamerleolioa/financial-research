from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_stock_sentinel.daily_radar.constants import (
    DAILY_RADAR_BACKGROUND_CONTEXT_TYPES,
    DAILY_RADAR_BUCKETS,
    DAILY_RADAR_REPEAT_STATUSES,
    DAILY_RADAR_RISK_LABELS,
)
from ai_stock_sentinel.daily_radar.forward_validation import (
    DEFAULT_BENCHMARK_SYMBOL,
    DEFAULT_FORWARD_WINDOWS,
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


class DailyRadarPreparedRunRequest(BaseModel):
    run_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)
    max_symbols: int = Field(default=250, ge=1, le=250)


class DailyRadarPreparedRunResponse(BaseModel):
    status: Literal["prepared", "completed"]
    run_date: date
    market: str
    symbol_count: int
    selected_symbols: list[str] = Field(default_factory=list)
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
    records_written: int = 0
    reused_symbols: list[str] = Field(default_factory=list)
    fetched_symbols: list[str] = Field(default_factory=list)
    missing_symbols: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


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
    windows: list[int] = Field(default_factory=lambda: list(DEFAULT_FORWARD_WINDOWS))
    benchmark_symbol: str = Field(default=DEFAULT_BENCHMARK_SYMBOL, min_length=1, max_length=40)


class DailyRadarForwardValidationRunResponse(BaseModel):
    status: Literal["completed"]
    mode: Literal["due", "range"]
    market: str
    as_of_date: date
    candidate_count: int
    records_written: int
    validated_count: int
    skipped_count: int
    report: dict[str, Any]


class DailyRadarMonthlyRuleReviewRequest(BaseModel):
    market: str = Field(default="TW", min_length=1, max_length=20)
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    validation_version: str | None = Field(default=None, min_length=1, max_length=80)
    min_sample_count: int = Field(default=DEFAULT_MIN_SAMPLE_COUNT, ge=1, le=10_000)


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
                "scoring_version": "daily-radar-scoring-v2.1c",
                "rule_version": "daily-radar-rules-v2.1c",
                "score_breakdown": {
                    "scoring_version": "daily-radar-scoring-v2.1c",
                    "rule_version": "daily-radar-rules-v2.1c",
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
    scoring_version: str | None = Field(default=None, examples=["daily-radar-scoring-v2.1c"])
    rule_version: str | None = Field(default=None, examples=["daily-radar-rules-v2.1c"])
    bucket_scores: dict[str, Any] = Field(default_factory=dict)
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    data_dates: dict[str, date] = Field(default_factory=dict)
    matched_rules: list[DailyRadarMatchedRule] = Field(default_factory=list)
    background_context_labels: list[dict[str, Any]] = Field(default_factory=list)


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
    candidates: list[DailyRadarCandidateResponse] = Field(default_factory=list)


__all__ = [
    "DailyRadarChipContextUpdateRequest",
    "DailyRadarChipContextUpdateResponse",
    "DailyRadarCandidateResponse",
    "DailyRadarForwardValidationRunRequest",
    "DailyRadarForwardValidationRunResponse",
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
