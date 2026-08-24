from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class FundamentalBackfillRequest(BaseModel):
    scope: Literal["managed"] = "managed"
    symbols: list[str] | None = Field(default=None, max_length=250)
    after_symbol: str | None = Field(default=None, max_length=20)
    job_id: str | None = Field(default=None, max_length=36)
    raw_pool_date: date | None = None
    resume_running_job: bool = False
    limit: int = Field(default=10, ge=1, le=10)


class FundamentalRefreshResponse(BaseModel):
    status: Literal["ok", "partial"]
    datasets_succeeded: int
    datasets_skipped: int
    datasets_failed: int
    records_written: int
    skipped_datasets: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class FundamentalBackfillResponse(BaseModel):
    status: Literal["ok", "partial"]
    symbols_processed: list[str] = Field(default_factory=list)
    records_written: int
    next_after_symbol: str | None = None
    job_id: str
    raw_pool_date: date | None = None
    errors: list[str] = Field(default_factory=list)


__all__ = [
    "FundamentalBackfillRequest",
    "FundamentalBackfillResponse",
    "FundamentalRefreshResponse",
]
