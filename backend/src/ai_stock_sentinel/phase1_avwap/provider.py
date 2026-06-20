from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from ai_stock_sentinel.data_sources.finmind_client import FinMindClient
from ai_stock_sentinel.phase1_avwap.calculator import DailyPriceBar


DEFAULT_PHASE1_DATASET = "TaiwanStockPrice"
DEFAULT_ADJUSTMENT_MODE = "unadjusted"


class FinMindDailyPriceProvider:
    def __init__(self, *, client: FinMindClient | None = None) -> None:
        self._client = client or FinMindClient()

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        rows = self._client.fetch_data(
            dataset=DEFAULT_PHASE1_DATASET,
            data_id=_finmind_data_id(symbol),
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        return normalize_finmind_daily_price_rows(rows)


def normalize_finmind_daily_price_rows(rows: list[Mapping[str, Any]]) -> list[DailyPriceBar]:
    normalized: list[DailyPriceBar] = []
    for row in rows:
        trade_date = date.fromisoformat(str(row["date"]))
        open_price = _number(row, "open")
        high = _number(row, "max", "high")
        low = _number(row, "min", "low")
        close = _number(row, "close")
        volume = _number(row, "Trading_Volume", "volume")
        amount = _optional_number(row, "Trading_money", "amount")
        estimated_amount = False
        if amount is None:
            amount = ((high + low + close) / 3) * volume
            estimated_amount = True
        normalized.append(
            DailyPriceBar(
                trade_date=trade_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
                estimated_amount=estimated_amount,
            )
        )
    return sorted(normalized, key=lambda bar: bar.trade_date)


def _finmind_data_id(symbol: str) -> str:
    return symbol.upper().removesuffix(".TW").removesuffix(".TWO")


def _number(row: Mapping[str, Any], *keys: str) -> float:
    value = _optional_number(row, *keys)
    if value is None:
        raise ValueError(f"missing numeric field: {'/'.join(keys)}")
    return value


def _optional_number(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        return float(value)
    return None


__all__ = [
    "DEFAULT_ADJUSTMENT_MODE",
    "DEFAULT_PHASE1_DATASET",
    "FinMindDailyPriceProvider",
    "normalize_finmind_daily_price_rows",
]
