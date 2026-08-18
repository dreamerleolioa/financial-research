from __future__ import annotations

from datetime import date, timedelta
import statistics
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.data_sources.fundamental.finmind_provider import FinMindFundamentalProvider
from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalData, FundamentalError
from ai_stock_sentinel.data_sources.fundamental.normalizers import (
    normalize_finmind_dividend_rows,
    normalize_finmind_statement_rows,
)
from ai_stock_sentinel.data_sources.fundamental.repository import (
    LoadedFundamentalPeriod,
    load_latest_dividend_events,
    load_latest_fundamental_periods,
    store_dividend_events,
    store_fundamental_periods,
)
from ai_stock_sentinel.daily_radar.market_bar_repository import get_taiwan_daily_bars
from ai_stock_sentinel.db.models import CompanyDividendEvent


class OfficialCachedFundamentalProvider:
    name = "OfficialCachedFundamental"

    def __init__(
        self,
        session: Session,
        *,
        fallback_provider: FinMindFundamentalProvider | None = None,
        provider_mode: str = "official_cache_first",
    ) -> None:
        if provider_mode not in {"official_cache_first", "official_cache_only"}:
            raise ValueError("invalid FUNDAMENTAL_PROVIDER_MODE for official provider")
        self._session = session
        self._fallback_provider = fallback_provider or FinMindFundamentalProvider()
        self._provider_mode = provider_mode

    def fetch(self, symbol: str, current_price: float) -> FundamentalData:
        return self._fetch(symbol, current_price, as_of_date=None)

    def fetch_as_of(
        self,
        symbol: str,
        current_price: float,
        *,
        as_of_date: date,
    ) -> FundamentalData:
        return self._fetch(symbol, current_price, as_of_date=as_of_date)

    def _fetch(
        self,
        symbol: str,
        current_price: float,
        *,
        as_of_date: date | None,
    ) -> FundamentalData:
        normalized_symbol = symbol.strip().upper()
        warnings: list[str] = []
        periods = load_latest_fundamental_periods(
            self._session,
            symbol=normalized_symbol,
            as_of_date=as_of_date,
        )
        dividends = load_latest_dividend_events(
            self._session,
            symbol=normalized_symbol,
            as_of_date=as_of_date,
        )
        if self._provider_mode == "official_cache_first":
            if as_of_date is not None:
                raise ValueError("point-in-time fundamental reads cannot bootstrap future history")
            periods, dividends = self._bootstrap_missing_history(
                normalized_symbol,
                periods=periods,
                dividends=dividends,
                warnings=warnings,
            )

        ttm_eps, latest_period_index = _latest_ttm(periods)
        if ttm_eps is None:
            warnings.append("基本面快取缺少四個連續單季 EPS，TTM EPS 無法計算")
        pe_current = current_price / ttm_eps if ttm_eps is not None and ttm_eps > 0 else None
        historical_pes = self._historical_pes(
            normalized_symbol,
            periods=periods,
            latest_period_index=latest_period_index,
        )
        pe_mean: float | None = None
        pe_std: float | None = None
        pe_band = "unknown"
        pe_percentile: float | None = None
        if pe_current is not None and len(historical_pes) >= 4:
            pe_mean = statistics.mean(historical_pes)
            pe_std = statistics.stdev(historical_pes) if len(historical_pes) >= 2 else 0.0
            if pe_std > 0:
                if pe_current < pe_mean - pe_std:
                    pe_band = "cheap"
                elif pe_current > pe_mean + pe_std:
                    pe_band = "expensive"
                else:
                    pe_band = "fair"
            else:
                pe_band = "fair"
            pe_percentile = (
                sum(1 for historical_pe in historical_pes if historical_pe <= pe_current)
                / len(historical_pes)
                * 100
            )
        elif pe_current is not None:
            warnings.append("有效歷史 PE 窗口不足 4 個，PE Band 無法計算")

        annual_cash_dividend, dividend_warning = _latest_complete_annual_dividend(dividends)
        if dividend_warning:
            warnings.append(dividend_warning)
        dividend_yield = (
            annual_cash_dividend / current_price * 100
            if annual_cash_dividend is not None and current_price > 0
            else None
        )
        yield_signal = "unknown"
        if dividend_yield is not None:
            if dividend_yield >= 5:
                yield_signal = "high_yield"
            elif dividend_yield >= 3:
                yield_signal = "mid_yield"
            else:
                yield_signal = "low_yield"

        return FundamentalData(
            symbol=normalized_symbol,
            ttm_eps=ttm_eps,
            pe_current=pe_current,
            pe_mean=pe_mean,
            pe_std=pe_std,
            pe_band=pe_band,
            pe_percentile=pe_percentile,
            annual_cash_dividend=annual_cash_dividend,
            dividend_yield=dividend_yield,
            yield_signal=yield_signal,
            source_provider=_fundamental_source_provider(periods, dividends),
            warnings=warnings,
        )

    def _bootstrap_missing_history(
        self,
        symbol: str,
        *,
        periods: list[LoadedFundamentalPeriod],
        dividends: list[CompanyDividendEvent],
        warnings: list[str],
    ) -> tuple[list[LoadedFundamentalPeriod], list[CompanyDividendEvent]]:
        if not fundamental_period_history_is_sufficient(periods):
            try:
                rows = self._fallback_provider.fetch_statement_rows(symbol)
                store_fundamental_periods(
                    self._session,
                    normalize_finmind_statement_rows(rows, symbol=symbol),
                )
                periods = load_latest_fundamental_periods(self._session, symbol=symbol)
                warnings.append("歷史 EPS 由 FinMind 一次性 bootstrap 補入本地版本庫")
            except FundamentalError as exc:
                warnings.append(f"歷史 EPS bootstrap 失敗：{exc.code}")
        if not dividend_history_is_sufficient(dividends):
            try:
                rows = self._fallback_provider.fetch_dividend_rows(symbol)
                store_dividend_events(
                    self._session,
                    normalize_finmind_dividend_rows(rows, symbol=symbol),
                )
                dividends = load_latest_dividend_events(self._session, symbol=symbol)
                warnings.append("歷史股利由 FinMind 一次性 bootstrap 補入本地版本庫")
            except FundamentalError as exc:
                warnings.append(f"歷史股利 bootstrap 失敗：{exc.code}")
        return periods, dividends

    def _historical_pes(
        self,
        symbol: str,
        *,
        periods: list[LoadedFundamentalPeriod],
        latest_period_index: int | None,
    ) -> list[float]:
        if latest_period_index is None or len(periods) < 4:
            return []
        period_ends = [row.period_end for row in periods]
        prices = self._local_period_prices(symbol, period_ends)
        missing_dates = [period_end.isoformat() for period_end in period_ends if period_end not in prices]
        if missing_dates and self._provider_mode == "official_cache_first":
            prices.update(
                {
                    date.fromisoformat(period_end): price
                    for period_end, price in self._fallback_provider.fetch_historical_prices(
                        symbol,
                        missing_dates,
                    ).items()
                }
            )
        historical_pes: list[float] = []
        for index in range(3, len(periods)):
            window = periods[index - 3 : index + 1]
            if not _periods_are_contiguous(window):
                continue
            eps_values = [float(row.quarter_eps) for row in window if row.quarter_eps is not None]
            if len(eps_values) != 4:
                continue
            window_eps = sum(eps_values)
            price = prices.get(window[-1].period_end)
            if window_eps > 0 and price is not None and price > 0:
                historical_pes.append(price / window_eps)
        return historical_pes[-24:]

    def _local_period_prices(self, symbol: str, period_ends: list[date]) -> dict[date, float]:
        if not period_ends:
            return {}
        bars = get_taiwan_daily_bars(
            self._session,
            symbols=[symbol],
            start_date=min(period_ends) - timedelta(days=10),
            end_date=max(period_ends),
        )
        prices: dict[date, float] = {}
        for period_end in period_ends:
            candidates = [bar for bar in bars if bar.trade_date <= period_end]
            if candidates:
                prices[period_end] = float(candidates[-1].close)
        return prices


def _latest_ttm(
    periods: list[LoadedFundamentalPeriod],
) -> tuple[float | None, int | None]:
    if len(periods) < 4:
        return None, None
    latest_period_index = len(periods) - 1
    window = periods[-4:]
    if not _periods_are_contiguous(window):
        return None, None
    values = [float(row.quarter_eps) for row in window if row.quarter_eps is not None]
    if len(values) != 4:
        return None, None
    return sum(values), latest_period_index


def _periods_are_contiguous(periods: list[LoadedFundamentalPeriod]) -> bool:
    if len(periods) != 4:
        return False
    indexes = [row.fiscal_year * 4 + row.fiscal_quarter for row in periods]
    return all(right - left == 1 for left, right in zip(indexes, indexes[1:]))


def _latest_complete_annual_dividend(
    events: list[CompanyDividendEvent],
) -> tuple[float | None, str | None]:
    by_year: dict[int, list[CompanyDividendEvent]] = {}
    for event in events:
        if event.total_cash_per_share is not None:
            by_year.setdefault(event.dividend_year, []).append(event)
    for dividend_year in sorted(by_year, reverse=True):
        year_events = by_year[dividend_year]
        official_value = _complete_annual_dividend_for_events(
            [event for event in year_events if event.source_provider != "finmind_bootstrap"],
            dividend_year=dividend_year,
        )
        if official_value is not None:
            return official_value, None
        canonical_value = _complete_annual_dividend_for_events(
            _prefer_dividend_period_sources(year_events),
            dividend_year=dividend_year,
        )
        if canonical_value is not None:
            return canonical_value, None
    if by_year:
        return None, "股利事件尚不足以證明完整年度金額，未以部分年度資料冒充年股利"
    return None, "基本面快取沒有可用的完整年度現金股利"


def fundamental_period_history_is_sufficient(
    periods: list[LoadedFundamentalPeriod],
) -> bool:
    return len(periods) >= 7 and _latest_ttm(periods)[0] is not None


def dividend_history_is_sufficient(events: list[CompanyDividendEvent]) -> bool:
    return _latest_complete_annual_dividend(events)[0] is not None


def _complete_annual_dividend_for_events(
    events: list[CompanyDividendEvent],
    *,
    dividend_year: int,
) -> float | None:
    annual = [
        event
        for event in events
        if event.period_start == date(dividend_year, 1, 1)
        and event.period_end == date(dividend_year, 12, 31)
    ]
    if annual:
        latest = max(annual, key=_observed_event_key)
        return float(latest.total_cash_per_share)
    bounded = [
        event
        for event in events
        if event.period_start is not None and event.period_end is not None
    ]
    ordered = sorted(bounded, key=lambda event: (event.period_start, event.period_end))
    if ordered and _covers_full_year_without_overlap(ordered, dividend_year=dividend_year):
        return sum(float(event.total_cash_per_share) for event in ordered)
    return None


def _prefer_dividend_period_sources(
    events: list[CompanyDividendEvent],
) -> list[CompanyDividendEvent]:
    by_period: dict[tuple[date | None, date | None], list[CompanyDividendEvent]] = {}
    for event in events:
        by_period.setdefault((event.period_start, event.period_end), []).append(event)
    preferred: list[CompanyDividendEvent] = []
    for period_events in by_period.values():
        priority = max(_dividend_source_priority(event) for event in period_events)
        preferred.extend(
            event
            for event in period_events
            if _dividend_source_priority(event) == priority
        )
    return preferred


def _dividend_source_priority(event: CompanyDividendEvent) -> int:
    return 0 if event.source_provider == "finmind_bootstrap" else 1


def _fundamental_source_provider(
    periods: list[LoadedFundamentalPeriod],
    dividends: list[CompanyDividendEvent],
) -> str:
    uses_finmind = any(
        period.source_provider == "finmind_bootstrap"
        or period.quarter_eps_source_provider == "finmind_bootstrap"
        for period in periods
    ) or any(
        dividend.source_provider == "finmind_bootstrap"
        for dividend in dividends
    )
    return (
        "OfficialCachedFundamental+FinMindFundamental"
        if uses_finmind
        else "OfficialCachedFundamental"
    )


def _covers_full_year_without_overlap(
    events: list[CompanyDividendEvent],
    *,
    dividend_year: int,
) -> bool:
    expected = date(dividend_year, 1, 1)
    for event in events:
        if event.period_start != expected or event.period_end is None:
            return False
        expected = event.period_end + timedelta(days=1)
    return expected == date(dividend_year + 1, 1, 1)


def _observed_event_key(event: CompanyDividendEvent) -> tuple[float, int]:
    observed_at = event.first_observed_at
    if observed_at.tzinfo is None:
        from datetime import timezone

        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return observed_at.timestamp(), event.id


__all__ = [
    "OfficialCachedFundamentalProvider",
    "dividend_history_is_sufficient",
    "fundamental_period_history_is_sufficient",
]
