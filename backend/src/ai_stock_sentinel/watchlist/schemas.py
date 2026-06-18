from __future__ import annotations

from pydantic import BaseModel, Field


class WatchlistCreateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=500)


class WatchlistUpdateRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=500)


class WatchlistReorderRequest(BaseModel):
    item_ids: list[int] = Field(min_length=0)
