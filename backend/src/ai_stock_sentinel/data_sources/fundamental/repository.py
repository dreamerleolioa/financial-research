from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from ai_stock_sentinel.clock import TAIPEI_TZ
from ai_stock_sentinel.data_sources.fundamental.normalizers import (
    NormalizedDividendEvent,
    NormalizedFundamentalPeriod,
)
from ai_stock_sentinel.db.models import CompanyDividendEvent, CompanyFundamentalPeriod


@dataclass(frozen=True)
class LoadedFundamentalPeriod:
    id: int
    symbol: str
    market: str
    fiscal_year: int
    fiscal_quarter: int
    period_end: date
    statement_scope: str
    industry_schema: str
    cumulative_eps: Decimal | None
    quarter_eps: Decimal | None
    quarter_eps_source_provider: str
    quarter_eps_source_dataset: str
    source_report_date: date | None
    availability_quality: str
    source_provider: str
    source_dataset: str
    first_observed_at: datetime
    last_observed_at: datetime


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
    now = datetime.now(timezone.utc)
    if _is_postgresql(session):
        session.execute(
            _postgres_fundamental_period_upsert(materialized, observed_at=now)
        )
        session.flush()
        return len(materialized)

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
            row = CompanyFundamentalPeriod(**_fundamental_period_values(period, observed_at=now))
            session.add(row)
            by_identity[identity] = row
        else:
            row.last_observed_at = now
        written += 1
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
    now = datetime.now(timezone.utc)
    if _is_postgresql(session):
        session.execute(_postgres_dividend_event_upsert(materialized, observed_at=now))
        session.flush()
        return len(materialized)

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
            row = CompanyDividendEvent(**_dividend_event_values(event, observed_at=now))
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
) -> list[LoadedFundamentalPeriod]:
    return load_latest_fundamental_periods_for_symbols(
        session,
        symbols=[symbol],
        as_of_date=as_of_date,
        allow_historical_unknown=allow_historical_unknown,
    ).get(symbol.upper(), [])


def load_latest_fundamental_periods_for_symbols(
    session: Session,
    *,
    symbols: Iterable[str],
    as_of_date: date | None = None,
    allow_historical_unknown: bool = True,
) -> dict[str, list[LoadedFundamentalPeriod]]:
    normalized_symbols = sorted({symbol.upper() for symbol in symbols})
    if not normalized_symbols:
        return {}
    statement = select(CompanyFundamentalPeriod).where(
        CompanyFundamentalPeriod.symbol.in_(normalized_symbols)
    )
    if not allow_historical_unknown:
        statement = statement.where(
            CompanyFundamentalPeriod.availability_quality == "observed"
        )
    rows_by_symbol: dict[str, list[CompanyFundamentalPeriod]] = {
        symbol: [] for symbol in normalized_symbols
    }
    for row in session.scalars(statement).all():
        rows_by_symbol[row.symbol].append(row)
    return {
        symbol: _select_latest_fundamental_periods(
            rows,
            as_of_date=as_of_date,
        )
        for symbol, rows in rows_by_symbol.items()
    }


def _select_latest_fundamental_periods(
    rows: Iterable[CompanyFundamentalPeriod],
    *,
    as_of_date: date | None,
) -> list[LoadedFundamentalPeriod]:
    by_period: dict[tuple[int, int, str], CompanyFundamentalPeriod] = {}
    visible_rows: list[CompanyFundamentalPeriod] = []
    for row in rows:
        if (
            as_of_date is not None
            and _taipei_observation_date(row.first_observed_at) > as_of_date
        ):
            continue
        visible_rows.append(row)
        key = (row.fiscal_year, row.fiscal_quarter, row.statement_scope)
        current = by_period.get(key)
        if current is None or _period_revision_sort_key(
            row,
            as_of_date=as_of_date,
        ) > _period_revision_sort_key(current, as_of_date=as_of_date):
            by_period[key] = row
    selected = sorted(
        by_period.values(),
        key=lambda row: (row.fiscal_year, row.fiscal_quarter),
    )
    return _materialize_loaded_periods(
        selected,
        direct_quarter_candidates=visible_rows,
        as_of_date=as_of_date,
    )


def load_latest_dividend_events(
    session: Session,
    *,
    symbol: str,
    as_of_date: date | None = None,
) -> list[CompanyDividendEvent]:
    return load_latest_dividend_events_for_symbols(
        session,
        symbols=[symbol],
        as_of_date=as_of_date,
    ).get(symbol.upper(), [])


def load_latest_dividend_events_for_symbols(
    session: Session,
    *,
    symbols: Iterable[str],
    as_of_date: date | None = None,
) -> dict[str, list[CompanyDividendEvent]]:
    normalized_symbols = sorted({symbol.upper() for symbol in symbols})
    if not normalized_symbols:
        return {}
    rows_by_symbol: dict[str, list[CompanyDividendEvent]] = {
        symbol: [] for symbol in normalized_symbols
    }
    rows = session.scalars(
        select(CompanyDividendEvent).where(
            CompanyDividendEvent.symbol.in_(normalized_symbols)
        )
    ).all()
    for row in rows:
        rows_by_symbol[row.symbol].append(row)
    return {
        symbol: _select_latest_dividend_events(rows, as_of_date=as_of_date)
        for symbol, rows in rows_by_symbol.items()
    }


def _select_latest_dividend_events(
    rows: Iterable[CompanyDividendEvent],
    *,
    as_of_date: date | None,
) -> list[CompanyDividendEvent]:
    by_event: dict[tuple[str, str, str], CompanyDividendEvent] = {}
    for row in rows:
        if (
            as_of_date is not None
            and _taipei_observation_date(row.first_observed_at) > as_of_date
        ):
            continue
        key = (row.source_provider, row.source_dataset, row.event_key)
        current = by_event.get(key)
        if current is None or _revision_observed_sort_key(
            row,
            as_of_date=as_of_date,
        ) > _revision_observed_sort_key(current, as_of_date=as_of_date):
            by_event[key] = row
    return sorted(
        by_event.values(),
        key=lambda row: (row.dividend_year, row.ex_dividend_date or date.min, row.event_key),
    )


def _materialize_loaded_periods(
    rows: Iterable[CompanyFundamentalPeriod],
    *,
    direct_quarter_candidates: Iterable[CompanyFundamentalPeriod] = (),
    as_of_date: date | None = None,
) -> list[LoadedFundamentalPeriod]:
    materialized: list[LoadedFundamentalPeriod] = []
    by_period: dict[tuple[str, int, int, str], CompanyFundamentalPeriod] = {}
    direct_by_period: dict[tuple[str, int, int, str], CompanyFundamentalPeriod] = {}
    for candidate in direct_quarter_candidates:
        if candidate.quarter_eps is None:
            continue
        key = (
            candidate.symbol,
            candidate.fiscal_year,
            candidate.fiscal_quarter,
            candidate.statement_scope,
        )
        current = direct_by_period.get(key)
        if current is None or _period_revision_sort_key(
            candidate,
            as_of_date=as_of_date,
        ) > _period_revision_sort_key(current, as_of_date=as_of_date):
            direct_by_period[key] = candidate
    for row in rows:
        key = (row.symbol, row.fiscal_year, row.fiscal_quarter, row.statement_scope)
        by_period[key] = row
        quarter_value_source = row
        cumulative = row.cumulative_eps
        if cumulative is None:
            quarter_eps = row.quarter_eps
        elif row.fiscal_quarter == 1:
            quarter_eps = cumulative
        else:
            previous = by_period.get(
                (row.symbol, row.fiscal_year, row.fiscal_quarter - 1, row.statement_scope)
            )
            previous_cumulative = previous.cumulative_eps if previous is not None else None
            if (
                previous is not None
                and previous.fiscal_quarter == 1
                and previous_cumulative is None
            ):
                # Q1 discrete EPS is also its year-to-date cumulative EPS, so a
                # bootstrap Q1 can safely anchor an official Q2 cumulative row.
                previous_cumulative = previous.quarter_eps
            quarter_eps = (
                cumulative - previous_cumulative
                if previous_cumulative is not None
                else None
            )
            if quarter_eps is None and (fallback := direct_by_period.get(key)) is not None:
                quarter_eps = fallback.quarter_eps
                quarter_value_source = fallback
        materialized.append(
            LoadedFundamentalPeriod(
                id=row.id,
                symbol=row.symbol,
                market=row.market,
                fiscal_year=row.fiscal_year,
                fiscal_quarter=row.fiscal_quarter,
                period_end=row.period_end,
                statement_scope=row.statement_scope,
                industry_schema=row.industry_schema,
                cumulative_eps=row.cumulative_eps,
                quarter_eps=quarter_eps,
                quarter_eps_source_provider=quarter_value_source.source_provider,
                quarter_eps_source_dataset=quarter_value_source.source_dataset,
                source_report_date=row.source_report_date,
                availability_quality=row.availability_quality,
                source_provider=row.source_provider,
                source_dataset=row.source_dataset,
                first_observed_at=row.first_observed_at,
                last_observed_at=row.last_observed_at,
            )
        )
    return materialized


def _revision_observed_sort_key(
    row: CompanyFundamentalPeriod | CompanyDividendEvent,
    *,
    as_of_date: date | None,
) -> tuple[float, int]:
    # Current reads follow the latest observation, while historical reads must
    # not let a future re-observation leak back into an earlier point in time.
    observed_at = row.last_observed_at if as_of_date is None else row.first_observed_at
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return observed_at.timestamp(), row.id


def _taipei_observation_date(observed_at: datetime) -> date:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return observed_at.astimezone(TAIPEI_TZ).date()


def _period_revision_sort_key(
    row: CompanyFundamentalPeriod,
    *,
    as_of_date: date | None,
) -> tuple[int, float, int]:
    quality_priority = 1 if row.availability_quality == "observed" else 0
    observed_at, row_id = _revision_observed_sort_key(row, as_of_date=as_of_date)
    return quality_priority, observed_at, row_id


def _is_postgresql(session: Session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _fundamental_period_values(
    period: NormalizedFundamentalPeriod,
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "symbol": period.symbol,
        "market": period.market,
        "fiscal_year": period.fiscal_year,
        "fiscal_quarter": period.fiscal_quarter,
        "period_end": period.period_end,
        "statement_scope": period.statement_scope,
        "industry_schema": period.industry_schema,
        "cumulative_eps": period.cumulative_eps,
        "quarter_eps": period.quarter_eps,
        "source_report_date": period.source_report_date,
        "availability_quality": period.availability_quality,
        "source_provider": period.source_provider,
        "source_dataset": period.source_dataset,
        "payload_hash": period.payload_hash,
        "raw_payload": period.raw_payload,
        "first_observed_at": observed_at,
        "last_observed_at": observed_at,
    }


def _dividend_event_values(
    event: NormalizedDividendEvent,
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "symbol": event.symbol,
        "market": event.market,
        "dividend_year": event.dividend_year,
        "period_start": event.period_start,
        "period_end": event.period_end,
        "period_label": event.period_label,
        "sequence": event.sequence,
        "decision_status": event.decision_status,
        "board_date": event.board_date,
        "shareholder_date": event.shareholder_date,
        "ex_dividend_date": event.ex_dividend_date,
        "earnings_cash_per_share": event.earnings_cash_per_share,
        "legal_reserve_cash_per_share": event.legal_reserve_cash_per_share,
        "capital_reserve_cash_per_share": event.capital_reserve_cash_per_share,
        "total_cash_per_share": event.total_cash_per_share,
        "source_provider": event.source_provider,
        "source_dataset": event.source_dataset,
        "event_key": event.event_key,
        "payload_hash": event.payload_hash,
        "raw_payload": event.raw_payload,
        "first_observed_at": observed_at,
        "last_observed_at": observed_at,
    }


def _postgres_fundamental_period_upsert(
    periods: Iterable[NormalizedFundamentalPeriod],
    *,
    observed_at: datetime,
):
    values_by_identity: dict[tuple[str, int, int, str, str], dict[str, Any]] = {}
    for period in periods:
        identity = (
            period.symbol,
            period.fiscal_year,
            period.fiscal_quarter,
            period.statement_scope,
            period.payload_hash,
        )
        values_by_identity[identity] = _fundamental_period_values(
            period,
            observed_at=observed_at,
        )
    statement = postgresql_insert(CompanyFundamentalPeriod).values(
        list(values_by_identity.values())
    )
    return statement.on_conflict_do_update(
        constraint="uq_company_fundamental_period_revision",
        set_={"last_observed_at": statement.excluded.last_observed_at},
    )


def _postgres_dividend_event_upsert(
    events: Iterable[NormalizedDividendEvent],
    *,
    observed_at: datetime,
):
    values_by_identity: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for event in events:
        identity = (
            event.symbol,
            event.source_provider,
            event.source_dataset,
            event.event_key,
            event.payload_hash,
        )
        values_by_identity[identity] = _dividend_event_values(
            event,
            observed_at=observed_at,
        )
    statement = postgresql_insert(CompanyDividendEvent).values(
        list(values_by_identity.values())
    )
    return statement.on_conflict_do_update(
        constraint="uq_company_dividend_event_revision",
        set_={"last_observed_at": statement.excluded.last_observed_at},
    )


__all__ = [
    "LoadedFundamentalPeriod",
    "load_latest_dividend_events",
    "load_latest_fundamental_periods",
    "store_dividend_events",
    "store_fundamental_periods",
]
