from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.market_bar_provider import (
    OfficialMarketBarProviderError,
    OfficialTaiwanMarketBarProvider,
)
from ai_stock_sentinel.daily_radar.market_bar_repository import upsert_taiwan_daily_bars


def refresh_taiwan_market_bars(
    session: Session,
    *,
    start_date: date,
    end_date: date,
    provider: OfficialTaiwanMarketBarProvider | None = None,
    max_workers: int = 4,
) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if (end_date - start_date).days > 179:
        raise ValueError("market bar refresh range cannot exceed 180 calendar days")
    active_provider = provider or OfficialTaiwanMarketBarProvider()
    trading_dates: list[date] = []
    skipped_date_values: set[date] = set()
    cursor = start_date
    while cursor <= end_date:
        if cursor.weekday() >= 5:
            skipped_date_values.add(cursor)
        else:
            trading_dates.append(cursor)
        cursor += timedelta(days=1)

    records_by_date: dict[date, int] = {trade_date: 0 for trade_date in trading_dates}
    errors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 4))) as executor:
        futures = {
            executor.submit(
                active_provider.fetch_market,
                market=market,
                trade_date=trade_date,
            ): (trade_date, market)
            for trade_date in trading_dates
            for market in ("TW", "TWO")
        }
        for future in as_completed(futures):
            trade_date, market = futures[future]
            try:
                bars = future.result()
            except OfficialMarketBarProviderError as exc:
                errors.append(
                    {
                        "code": exc.code,
                        "market": market,
                        "trade_date": trade_date.isoformat(),
                    }
                )
                continue
            records_by_date[trade_date] += upsert_taiwan_daily_bars(session, bars)
            session.flush()

    dates_with_data = sorted(
        trade_date.isoformat()
        for trade_date, count in records_by_date.items()
        if count > 0
    )
    skipped_date_values.update(
        trade_date for trade_date, count in records_by_date.items() if count == 0
    )
    errors.sort(key=lambda item: (item["trade_date"], item["market"], item["code"]))
    return {
        "status": "completed" if not errors else "failed",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "records_written": sum(records_by_date.values()),
        "dates_attempted": [trade_date.isoformat() for trade_date in trading_dates],
        "dates_with_data": dates_with_data,
        "skipped_dates": sorted(trade_date.isoformat() for trade_date in skipped_date_values),
        "errors": errors,
    }


__all__ = ["refresh_taiwan_market_bars"]
