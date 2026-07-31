from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ai_stock_sentinel.taiwan_symbols import validate_taiwan_symbol


class WatchlistCreateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=500)

    _validate_symbol = field_validator("symbol", mode="before")(validate_taiwan_symbol)


class WatchlistUpdateRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=500)


class WatchlistReorderRequest(BaseModel):
    item_ids: list[int] = Field(min_length=0)
