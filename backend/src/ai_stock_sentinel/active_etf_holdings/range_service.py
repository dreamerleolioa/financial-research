"""Read-only interval observations from stored MoneyDJ holdings snapshots."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ai_stock_sentinel.db.models import ActiveEtfFund, ActiveEtfHoldingSnapshot
from .schemas import ActiveEtfRangeResponse, ActiveEtfRangeFund, ActiveEtfRangePosition, ActiveEtfRangeStock, ActiveEtfRangePoint
from .service import _changes_for_snapshots


def get_active_etf_range_response(
    db: Session, *, start_date: date | None = None, end_date: date | None = None,
    symbol: str | None = None,
) -> ActiveEtfRangeResponse | None:
    available_dates = list(db.scalars(
        select(ActiveEtfHoldingSnapshot.data_date)
        .join(ActiveEtfFund)
        .where(ActiveEtfHoldingSnapshot.source_provider == "moneydj", ActiveEtfFund.enabled.is_(True))
        .distinct().order_by(ActiveEtfHoldingSnapshot.data_date.desc()).limit(366)
    ))
    if not available_dates:
        return None
    end = end_date or available_dates[0]
    start = start_date or end - timedelta(days=min(29, end.toordinal() - 1))
    if start > end or (end - start).days > 365:
        raise ValueError("active_etf_invalid_date_range")
    funds = list(db.scalars(select(ActiveEtfFund).where(ActiveEtfFund.enabled.is_(True)).order_by(ActiveEtfFund.fund_code)))
    snapshots = list(db.scalars(
        select(ActiveEtfHoldingSnapshot)
        .options(selectinload(ActiveEtfHoldingSnapshot.holdings), selectinload(ActiveEtfHoldingSnapshot.fund))
        .where(ActiveEtfHoldingSnapshot.source_provider == "moneydj",
               ActiveEtfHoldingSnapshot.fund_code.in_([fund.fund_code for fund in funds]),
               ActiveEtfHoldingSnapshot.data_date.between(start, end))
        .order_by(ActiveEtfHoldingSnapshot.data_date)
    ))
    observed_dates = sorted({snapshot.data_date for snapshot in snapshots})
    grouped = defaultdict(list)
    for snapshot in snapshots:
        grouped[snapshot.fund_code].append(snapshot)
    coverage = []
    stocks = {}
    timeline = []
    for fund in funds:
        history = grouped[fund.fund_code]
        dates = {snapshot.data_date for snapshot in history}
        missing = [day for day in observed_dates if day not in dates]
        coverage.append(ActiveEtfRangeFund(
            fund_code=fund.fund_code, fund_name=fund.name,
            first_date=history[0].data_date if history else None,
            last_date=history[-1].data_date if history else None,
            snapshot_count=len(history), missing_dates=missing,
        ))
        if not history:
            continue
        holdings = [{holding.symbol: holding for holding in snapshot.holdings} for snapshot in history]
        changes_by_date = []
        for index, snapshot in enumerate(history):
            changes = _changes_for_snapshots(snapshot, history[index - 1])[0] if index else []
            changes_by_date.append({change.symbol: change for change in changes})
        symbols = set().union(*(rows.keys() for rows in holdings))
        if symbol is not None:
            symbols &= {symbol}
        for stock_symbol in sorted(symbols):
            last_row = next(rows[stock_symbol] for rows in reversed(holdings) if stock_symbol in rows)
            first = holdings[0].get(stock_symbol)
            last = holdings[-1].get(stock_symbol)
            first_shares = first.shares if first else 0
            last_shares = last.shares if last else 0
            changes = [rows[stock_symbol] for rows in changes_by_date if stock_symbol in rows]
            comparable = len(history) >= 2
            position = ActiveEtfRangePosition(
                fund_code=fund.fund_code, fund_name=fund.name,
                first_date=history[0].data_date, last_date=history[-1].data_date,
                first_shares=first_shares, last_shares=last_shares,
                net_share_delta=last_shares - first_shares if comparable else None,
                first_weight_pct=first.weight_pct if first else Decimal(0),
                last_weight_pct=last.weight_pct if last else Decimal(0),
                weight_delta_pct_points=((last.weight_pct if last else Decimal(0)) - (first.weight_pct if first else Decimal(0))) if comparable else None,
                increase_days=sum(change.share_delta > 0 for change in changes),
                decrease_days=sum(change.share_delta < 0 for change in changes),
                added_days=sum(change.action == "added" for change in changes),
                removed_days=sum(change.action == "removed" for change in changes),
                scale_change_days=sum(change.likely_fund_scale_change for change in changes),
            )
            if stock_symbol not in stocks:
                stocks[stock_symbol] = ActiveEtfRangeStock(symbol=stock_symbol, name=last_row.name, funds=[])
            stocks[stock_symbol].funds.append(position)
            if symbol is not None:
                for index, snapshot in enumerate(history):
                    row = holdings[index].get(stock_symbol)
                    change = changes_by_date[index].get(stock_symbol)
                    timeline.append(ActiveEtfRangePoint(
                        fund_code=fund.fund_code, data_date=snapshot.data_date,
                        previous_date=history[index - 1].data_date if index else None,
                        shares=row.shares if row else 0, weight_pct=row.weight_pct if row else Decimal(0),
                        share_delta=change.share_delta if change else (0 if index else None),
                        action=change.action if change else None,
                        likely_fund_scale_change=change.likely_fund_scale_change if change else False,
                        source_url=snapshot.source_url, fetched_at=snapshot.fetched_at,
                    ))
    return ActiveEtfRangeResponse(
        start_date=start, end_date=end, available_dates=available_dates,
        observed_dates=observed_dates, funds=coverage,
        stocks=sorted(stocks.values(), key=lambda item: (-len(item.funds), item.symbol)),
        timeline=sorted(timeline, key=lambda item: (item.data_date, item.fund_code)),
    )
