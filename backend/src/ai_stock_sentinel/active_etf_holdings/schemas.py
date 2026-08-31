from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ActiveEtfRefreshRequest(BaseModel):
    fund_codes: list[str] | None = Field(default=None, max_length=50)

    @field_validator("fund_codes")
    @classmethod
    def validate_fund_codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [code.strip().upper() for code in value]
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("fund_codes must be a non-empty unique list")
        if any(len(code) != 6 or not code[:5].isdigit() or code[-1] != "A" for code in normalized):
            raise ValueError("fund_codes must contain active equity ETF codes")
        return normalized


class ActiveEtfRefreshError(BaseModel):
    fund_code: str
    code: str


class ActiveEtfRefreshResponse(BaseModel):
    status: Literal["completed", "partial"]
    expected_funds: int
    selected_funds: int
    snapshots_created: int
    snapshots_updated: int
    snapshots_reused: int
    errors: list[ActiveEtfRefreshError]


class ActiveEtfCoverageFund(BaseModel):
    fund_code: str
    name: str
    category: str | None = None
    source_provider: str
    source_url: str
    status: Literal["ready", "no_baseline", "missing"]
    data_date: date | None = None
    previous_date: date | None = None
    latest_data_date: date | None = None
    fetched_at: datetime | None = None
    change_count: int = 0
    common_scale_ratio: Decimal | None = None


class ActiveEtfChange(BaseModel):
    action: Literal["added", "increased", "decreased", "removed"]
    fund_code: str
    fund_name: str
    symbol: str
    name: str
    source_provider: str
    source_url: str
    fetched_at: datetime
    data_date: date
    previous_date: date
    current_shares: int
    previous_shares: int
    share_delta: int
    share_delta_pct: Decimal | None = None
    current_weight_pct: Decimal
    previous_weight_pct: Decimal
    weight_delta_pct_points: Decimal
    relative_share_change_pct: Decimal | None = None
    likely_fund_scale_change: bool = False


class ActiveEtfConsensus(BaseModel):
    symbol: str
    name: str
    direction: Literal["increase", "decrease", "mixed"]
    fund_count: int
    added_count: int
    increased_count: int
    decreased_count: int
    removed_count: int


class ActiveEtfDailySummary(BaseModel):
    changed_funds: int
    changed_stocks: int
    changed_rows: int
    additions: int
    increases: int
    decreases: int
    removals: int


class ActiveEtfDailyResponse(BaseModel):
    data_date: date
    available_dates: list[date]
    generated_at: datetime
    expected_funds: int
    covered_funds: int
    summary: ActiveEtfDailySummary
    funds: list[ActiveEtfCoverageFund]
    changes: list[ActiveEtfChange]
    consensus: list[ActiveEtfConsensus]
