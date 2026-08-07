from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.data_sources.fundamental.normalizers import (
    NormalizedDividendEvent,
    NormalizedFundamentalPeriod,
)
from ai_stock_sentinel.db.models import CompanyDividendEvent, CompanyFundamentalPeriod


def store_fundamental_periods(
    session: Session,
    periods: Iterable[NormalizedFundamentalPeriod],
) -> int:
    materialized = list(periods)
    if not materialized:
        return 0
    identities = {
        (
            period.symbol,
            period.fiscal_year,
            period.fiscal_quarter,
            period.statement_scope,
            period.payload_hash,
        )
        for period in materialized
    }
    symbols = sorted({identity[0] for identity in identities})
    existing = session.scalars(
        select(CompanyFundamentalPeriod).where(CompanyFundamentalPeriod.symbol.in_(symbols))
    ).all()
    by_identity = {
        (
            row.symbol,
            row.fiscal_year,
            row.fiscal_quarter,
            row.statement_scope,
            row.payload_hash,
        ): row
        for row in existing
    }
    now = datetime.now(timezone.utc)
    written = 0
    for period in materialized:
        identity = (
            period.symbol,
            period.fiscal_year,
            period.fiscal_quarter,
            period.statement_scope,
            period.payload_hash,
        )
        row = by_identity.get(identity)
        if row is None:
            row = CompanyFundamentalPeriod(
                symbol=period.symbol,
                market=period.market,
                fiscal_year=period.fiscal_year,
                fiscal_quarter=period.fiscal_quarter,
                period_end=period.period_end,
                statement_scope=period.statement_scope,
                industry_schema=period.industry_schema,
                cumulative_eps=period.cumulative_eps,
                quarter_eps=None,
                source_report_date=period.source_report_date,
                availability_quality=period.availability_quality,
                source_provider=period.source_provider,
                source_dataset=period.source_dataset,
                payload_hash=period.payload_hash,
                raw_payload=period.raw_payload,
                first_observed_at=now,
                last_observed_at=now,
            )
            session.add(row)
            by_identity[identity] = row
        else:
            row.last_observed_at = now
        written += 1
    session.flush()
    _update_latest_discrete_eps_rows(by_identity.values())
    session.flush()
    return written


def store_dividend_events(
    session: Session,
    events: Iterable[NormalizedDividendEvent],
) -> int:
    materialized = list(events)
    if not materialized:
        return 0
    symbols = sorted({event.symbol for event in materialized})
    existing = session.scalars(
        select(CompanyDividendEvent).where(CompanyDividendEvent.symbol.in_(symbols))
    ).all()
    by_identity = {
        (
            row.symbol,
            row.source_provider,
            row.source_dataset,
            row.event_key,
            row.payload_hash,
        ): row
        for row in existing
    }
    now = datetime.now(timezone.utc)
    written = 0
    for event in materialized:
        identity = (
            event.symbol,
            event.source_provider,
            event.source_dataset,
            event.event_key,
            event.payload_hash,
        )
        row = by_identity.get(identity)
        if row is None:
            row = CompanyDividendEvent(
                symbol=event.symbol,
                market=event.market,
                dividend_year=event.dividend_year,
                period_start=event.period_start,
                period_end=event.period_end,
                period_label=event.period_label,
                sequence=event.sequence,
                decision_status=event.decision_status,
                board_date=event.board_date,
                shareholder_date=event.shareholder_date,
                ex_dividend_date=event.ex_dividend_date,
                earnings_cash_per_share=event.earnings_cash_per_share,
                legal_reserve_cash_per_share=event.legal_reserve_cash_per_share,
                capital_reserve_cash_per_share=event.capital_reserve_cash_per_share,
                total_cash_per_share=event.total_cash_per_share,
                source_provider=event.source_provider,
                source_dataset=event.source_dataset,
                event_key=event.event_key,
                payload_hash=event.payload_hash,
                raw_payload=event.raw_payload,
                first_observed_at=now,
                last_observed_at=now,
            )
            session.add(row)
            by_identity[identity] = row
        else:
            row.last_observed_at = now
        written += 1
    session.flush()
    return written


def load_latest_fundamental_periods(
    session: Session,
    *,
    symbol: str,
    as_of_date: date | None = None,
    allow_historical_unknown: bool = True,
) -> list[CompanyFundamentalPeriod]:
    statement = select(CompanyFundamentalPeriod).where(
        CompanyFundamentalPeriod.symbol == symbol.upper()
    )
    if not allow_historical_unknown:
        statement = statement.where(
            CompanyFundamentalPeriod.availability_quality == "observed"
        )
    rows = session.scalars(statement).all()
    by_period: dict[tuple[int, int, str], CompanyFundamentalPeriod] = {}
    for row in rows:
        if as_of_date is not None and row.first_observed_at.date() > as_of_date:
            continue
        key = (row.fiscal_year, row.fiscal_quarter, row.statement_scope)
        current = by_period.get(key)
        if current is None or _observed_sort_key(row) > _observed_sort_key(current):
            by_period[key] = row
    return sorted(
        by_period.values(),
        key=lambda row: (row.fiscal_year, row.fiscal_quarter),
    )


def load_latest_dividend_events(
    session: Session,
    *,
    symbol: str,
    as_of_date: date | None = None,
) -> list[CompanyDividendEvent]:
    rows = session.scalars(
        select(CompanyDividendEvent).where(CompanyDividendEvent.symbol == symbol.upper())
    ).all()
    by_event: dict[tuple[str, str, str], CompanyDividendEvent] = {}
    for row in rows:
        if as_of_date is not None and row.first_observed_at.date() > as_of_date:
            continue
        key = (row.source_provider, row.source_dataset, row.event_key)
        current = by_event.get(key)
        if current is None or _observed_sort_key(row) > _observed_sort_key(current):
            by_event[key] = row
    return sorted(
        by_event.values(),
        key=lambda row: (row.dividend_year, row.ex_dividend_date or date.min, row.event_key),
    )


def _update_latest_discrete_eps_rows(rows: Iterable[CompanyFundamentalPeriod]) -> None:
    latest_by_period: dict[tuple[str, int, int, str], CompanyFundamentalPeriod] = {}
    for row in rows:
        key = (row.symbol, row.fiscal_year, row.fiscal_quarter, row.statement_scope)
        current = latest_by_period.get(key)
        if current is None or _observed_sort_key(row) > _observed_sort_key(current):
            latest_by_period[key] = row
    for row in latest_by_period.values():
        cumulative = row.cumulative_eps
        if cumulative is None:
            row.quarter_eps = None
            continue
        if row.fiscal_quarter == 1:
            row.quarter_eps = cumulative
            continue
        previous = latest_by_period.get(
            (row.symbol, row.fiscal_year, row.fiscal_quarter - 1, row.statement_scope)
        )
        row.quarter_eps = (
            cumulative - previous.cumulative_eps
            if previous is not None and previous.cumulative_eps is not None
            else None
        )


def _observed_sort_key(row: CompanyFundamentalPeriod | CompanyDividendEvent) -> tuple[float, int]:
    observed_at = row.first_observed_at
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return observed_at.timestamp(), row.id


__all__ = [
    "load_latest_dividend_events",
    "load_latest_fundamental_periods",
    "store_dividend_events",
    "store_fundamental_periods",
]
