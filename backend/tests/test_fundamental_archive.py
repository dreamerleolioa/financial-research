from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.market_bar_provider import MarketDailyBar
from ai_stock_sentinel.daily_radar.market_bar_repository import upsert_taiwan_daily_bars
from ai_stock_sentinel.data_sources.fundamental.normalizers import (
    normalize_finmind_dividend_rows,
    normalize_finmind_statement_rows,
    normalize_official_statement_rows,
    normalize_tpex_ex_dividend_payload,
    normalize_twse_dividend_rows,
)
from ai_stock_sentinel.data_sources.fundamental.official_provider import (
    OfficialCachedFundamentalProvider,
)
from ai_stock_sentinel.data_sources.fundamental.repository import (
    _postgres_dividend_event_upsert,
    _postgres_fundamental_period_upsert,
    load_latest_dividend_events,
    load_latest_fundamental_periods,
    store_dividend_events,
    store_fundamental_periods,
)
from ai_stock_sentinel.data_sources.fundamental.service import (
    FundamentalBackfillResult,
    FundamentalRefreshResult,
    backfill_fundamentals,
    refresh_official_fundamentals,
)
from ai_stock_sentinel.data_sources.fundamental.router import router as fundamental_router
from ai_stock_sentinel.db.models import (
    CompanyDividendEvent,
    CompanyFundamentalPeriod,
    TaiwanDailyBar,
)
from ai_stock_sentinel.db.session import Base, get_db


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


def _db_session() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            TaiwanDailyBar.__table__,
            CompanyFundamentalPeriod.__table__,
            CompanyDividendEvent.__table__,
        ],
    )
    return Session(engine), engine


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "4e5f6a7b8c9d_add_fundamental_version_tables.py"
    )
    spec = importlib.util.spec_from_file_location("fundamental_version_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_sql(direction: str) -> str:
    migration = _load_migration()
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    operations = Operations(context)
    original_op = migration.op
    migration.op = operations
    try:
        getattr(migration, direction)()
    finally:
        migration.op = original_op
    return buffer.getvalue()


def _statement_row(year: int, quarter: int, eps: str) -> dict[str, str]:
    return {
        "出表日期": f"{year - 1911}1114",
        "年度": str(year - 1911),
        "季別": str(quarter),
        "公司代號": "2330",
        "基本每股盈餘（元）": eps,
    }


def test_fundamental_migration_is_additive_and_reversible() -> None:
    upgrade_sql = _migration_sql("upgrade")
    downgrade_sql = _migration_sql("downgrade")

    assert "CREATE TABLE company_fundamental_periods" in upgrade_sql
    assert "CREATE TABLE company_dividend_events" in upgrade_sql
    assert "uq_company_fundamental_period_revision" in upgrade_sql
    assert "uq_company_dividend_event_revision" in upgrade_sql
    assert downgrade_sql.index("DROP TABLE company_dividend_events") < downgrade_sql.index(
        "DROP TABLE company_fundamental_periods"
    )


def test_official_normalizers_preserve_period_and_dividend_provenance() -> None:
    periods = normalize_official_statement_rows(
        [_statement_row(2025, 3, "9.25")],
        market="TW",
        industry_schema="ci",
        source_dataset="TWSE_ci",
    )
    dividends = normalize_twse_dividend_rows(
        [
            {
                "公司代號": "2330",
                "股利年度": "114",
                "股利所屬期間": "114/01/01~114/12/31",
                "股利所屬年(季)度": "114年",
                "期別": "1",
                "決議（擬議）進度": "股東會確認",
                "股東配發-盈餘分配之現金股利(元/股)": "4.0",
                "股東配發-法定盈餘公積發放之現金(元/股)": "0.5",
                "股東配發-資本公積發放之現金(元/股)": "0.25",
            }
        ]
    )
    tpex = normalize_tpex_ex_dividend_payload(
        {
            "tables": [
                {
                    "fields": ["除權息日期", "代號", "現金股利"],
                    "data": [["115/06/10", "8069", "2.5"]],
                }
            ]
        }
    )

    assert periods[0].symbol == "2330.TW"
    assert periods[0].fiscal_year == 2025
    assert periods[0].period_end == date(2025, 9, 30)
    assert periods[0].cumulative_eps == Decimal("9.25")
    assert dividends[0].total_cash_per_share == Decimal("4.75")
    assert dividends[0].period_start == date(2025, 1, 1)
    assert tpex[0].symbol == "8069.TWO"
    assert tpex[0].ex_dividend_date == date(2026, 6, 10)


def test_repository_keeps_restatements_and_derives_discrete_quarter_eps() -> None:
    session, engine = _db_session()
    try:
        first = normalize_official_statement_rows(
            [_statement_row(2025, 1, "2"), _statement_row(2025, 2, "5")],
            market="TW",
            industry_schema="ci",
            source_dataset="TWSE_ci",
        )
        store_fundamental_periods(session, first)
        restated = normalize_official_statement_rows(
            [_statement_row(2025, 2, "5.5")],
            market="TW",
            industry_schema="ci",
            source_dataset="TWSE_ci",
        )
        store_fundamental_periods(session, restated)
        session.commit()

        rows = session.scalars(select(CompanyFundamentalPeriod)).all()
        latest = load_latest_fundamental_periods(session, symbol="2330.TW")
        assert len(rows) == 3
        assert [row.quarter_eps for row in latest] == [Decimal("2.000000"), Decimal("3.500000")]
    finally:
        session.close()
        engine.dispose()


def test_current_period_revision_uses_latest_observation_after_payload_reverts() -> None:
    session, engine = _db_session()
    try:
        revision_a = normalize_official_statement_rows(
            [_statement_row(2025, 1, "2")],
            market="TW",
            industry_schema="ci",
            source_dataset="TWSE_ci",
        )
        revision_b = normalize_official_statement_rows(
            [_statement_row(2025, 1, "3")],
            market="TW",
            industry_schema="ci",
            source_dataset="TWSE_ci",
        )
        with patch(
            "ai_stock_sentinel.data_sources.fundamental.repository.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.side_effect = [
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 2, 1, tzinfo=timezone.utc),
                datetime(2026, 3, 1, tzinfo=timezone.utc),
            ]
            store_fundamental_periods(session, revision_a)
            store_fundamental_periods(session, revision_b)
            store_fundamental_periods(session, revision_a)
        session.commit()

        current = load_latest_fundamental_periods(session, symbol="2330.TW")
        historical = load_latest_fundamental_periods(
            session,
            symbol="2330.TW",
            as_of_date=date(2026, 2, 15),
        )

        assert [row.cumulative_eps for row in current] == [Decimal("2")]
        assert [row.cumulative_eps for row in historical] == [Decimal("3")]
    finally:
        session.close()
        engine.dispose()


def test_current_dividend_revision_uses_latest_observation_after_payload_reverts() -> None:
    session, engine = _db_session()
    try:
        common = {
            "公司代號": "2330",
            "股利年度": "114",
            "股利所屬期間": "114/01/01~114/12/31",
            "股利所屬年(季)度": "114年",
            "期別": "1",
        }
        revision_a = normalize_twse_dividend_rows(
            [{**common, "股東配發-盈餘分配之現金股利(元/股)": "4"}]
        )
        revision_b = normalize_twse_dividend_rows(
            [{**common, "股東配發-盈餘分配之現金股利(元/股)": "5"}]
        )
        with patch(
            "ai_stock_sentinel.data_sources.fundamental.repository.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.side_effect = [
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 2, 1, tzinfo=timezone.utc),
                datetime(2026, 3, 1, tzinfo=timezone.utc),
            ]
            store_dividend_events(session, revision_a)
            store_dividend_events(session, revision_b)
            store_dividend_events(session, revision_a)
        session.commit()

        current = load_latest_dividend_events(session, symbol="2330.TW")
        historical = load_latest_dividend_events(
            session,
            symbol="2330.TW",
            as_of_date=date(2026, 2, 15),
        )

        assert [row.total_cash_per_share for row in current] == [Decimal("4")]
        assert [row.total_cash_per_share for row in historical] == [Decimal("5")]
    finally:
        session.close()
        engine.dispose()


def test_quarter_eps_as_of_read_is_not_rewritten_by_later_prior_quarter_revision() -> None:
    session, engine = _db_session()
    try:
        store_fundamental_periods(
            session,
            normalize_official_statement_rows(
                [_statement_row(2025, 1, "2"), _statement_row(2025, 2, "5")],
                market="TW",
                industry_schema="ci",
                source_dataset="TWSE_ci",
            ),
        )
        rows = session.scalars(select(CompanyFundamentalPeriod)).all()
        for row in rows:
            row.first_observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        store_fundamental_periods(
            session,
            normalize_official_statement_rows(
                [_statement_row(2025, 1, "2.5")],
                market="TW",
                industry_schema="ci",
                source_dataset="TWSE_ci",
            ),
        )
        restated_q1 = max(
            session.scalars(
                select(CompanyFundamentalPeriod).where(
                    CompanyFundamentalPeriod.fiscal_quarter == 1
                )
            ).all(),
            key=lambda row: row.id,
        )
        restated_q1.first_observed_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
        session.commit()

        before_restatement = load_latest_fundamental_periods(
            session,
            symbol="2330.TW",
            as_of_date=date(2026, 1, 15),
        )
        latest = load_latest_fundamental_periods(session, symbol="2330.TW")

        assert [row.quarter_eps for row in before_restatement] == [Decimal("2"), Decimal("3")]
        assert [row.quarter_eps for row in latest] == [Decimal("2.5"), Decimal("2.5")]
        stored_q2 = session.scalar(
            select(CompanyFundamentalPeriod).where(
                CompanyFundamentalPeriod.fiscal_quarter == 2
            )
        )
        assert stored_q2 is not None
        assert stored_q2.quarter_eps is None
    finally:
        session.close()
        engine.dispose()


def test_finmind_statement_rows_remain_discrete_quarter_eps() -> None:
    session, engine = _db_session()
    try:
        periods = normalize_finmind_statement_rows(
            [
                {"date": "2019-03-31", "type": "EPS", "value": "2.37"},
                {"date": "2019-06-30", "type": "EPS", "value": "2.57"},
                {"date": "2019-09-30", "type": "EPS", "value": "3.90"},
                {"date": "2019-12-31", "type": "EPS", "value": "4.48"},
            ],
            symbol="2330.TW",
        )

        assert [period.cumulative_eps for period in periods] == [None, None, None, None]
        assert [period.quarter_eps for period in periods] == [
            Decimal("2.37"),
            Decimal("2.57"),
            Decimal("3.90"),
            Decimal("4.48"),
        ]

        store_fundamental_periods(session, periods)
        result = OfficialCachedFundamentalProvider(
            session,
            provider_mode="official_cache_only",
        ).fetch("2330.TW", 133.2)

        assert result.ttm_eps == 13.32
        assert result.pe_current == pytest.approx(10)
    finally:
        session.close()
        engine.dispose()


def test_observed_official_period_wins_over_later_finmind_bootstrap() -> None:
    session, engine = _db_session()
    try:
        store_fundamental_periods(
            session,
            normalize_official_statement_rows(
                [_statement_row(2025, 1, "2")],
                market="TW",
                industry_schema="ci",
                source_dataset="TWSE_ci",
            ),
        )
        store_fundamental_periods(
            session,
            normalize_finmind_statement_rows(
                [{"date": "2025-03-31", "type": "EPS", "value": "99"}],
                symbol="2330.TW",
            ),
        )

        latest = load_latest_fundamental_periods(session, symbol="2330.TW")

        assert len(latest) == 1
        assert latest[0].availability_quality == "observed"
        assert latest[0].quarter_eps == Decimal("2")
    finally:
        session.close()
        engine.dispose()


def test_finmind_quarterly_dividends_form_one_complete_fiscal_year() -> None:
    session, engine = _db_session()
    try:
        events = normalize_finmind_dividend_rows(
            [
                {
                    "date": "2024-09-18",
                    "year": "113年第1季",
                    "CashEarningsDistribution": "4.0",
                    "CashExDividendTradingDate": "2024-09-12",
                },
                {
                    "date": "2024-12-18",
                    "year": "113年第2季",
                    "CashEarningsDistribution": "4.0",
                    "CashExDividendTradingDate": "2024-12-12",
                },
                {
                    "date": "2025-03-24",
                    "year": "113年第3季",
                    "CashEarningsDistribution": "4.5",
                    "CashExDividendTradingDate": "2025-03-18",
                },
                {
                    "date": "2025-06-18",
                    "year": "113年第4季",
                    "CashEarningsDistribution": "4.5",
                    "CashExDividendTradingDate": "2025-06-12",
                },
            ],
            symbol="2330.TW",
        )

        assert [event.dividend_year for event in events] == [2024, 2024, 2024, 2024]
        assert [(event.period_start, event.period_end) for event in events] == [
            (date(2024, 1, 1), date(2024, 3, 31)),
            (date(2024, 4, 1), date(2024, 6, 30)),
            (date(2024, 7, 1), date(2024, 9, 30)),
            (date(2024, 10, 1), date(2024, 12, 31)),
        ]
        assert events[0].ex_dividend_date == date(2024, 9, 12)

        store_dividend_events(session, events)
        result = OfficialCachedFundamentalProvider(
            session,
            provider_mode="official_cache_only",
        ).fetch("2330.TW", 360)

        assert result.annual_cash_dividend == 17
        assert result.dividend_yield == 17 / 360 * 100
    finally:
        session.close()
        engine.dispose()


def test_official_quarterly_dividends_win_over_duplicate_finmind_periods() -> None:
    session, engine = _db_session()
    try:
        official_rows = []
        finmind_rows = []
        quarter_ranges = (
            ("113/01/01~113/03/31", "113年第1季", "2024-09-18"),
            ("113/04/01~113/06/30", "113年第2季", "2024-12-18"),
            ("113/07/01~113/09/30", "113年第3季", "2025-03-24"),
            ("113/10/01~113/12/31", "113年第4季", "2025-06-18"),
        )
        for sequence, (period, label, event_date) in enumerate(quarter_ranges, 1):
            official_rows.append(
                {
                    "公司代號": "2330",
                    "股利年度": "113",
                    "股利所屬期間": period,
                    "股利所屬年(季)度": label,
                    "期別": str(sequence),
                    "股東配發-盈餘分配之現金股利(元/股)": "5",
                }
            )
            finmind_rows.append(
                {
                    "date": event_date,
                    "year": label,
                    "CashEarningsDistribution": "4",
                }
            )

        store_dividend_events(session, normalize_twse_dividend_rows(official_rows))
        store_dividend_events(
            session,
            normalize_finmind_dividend_rows(finmind_rows, symbol="2330.TW"),
        )

        result = OfficialCachedFundamentalProvider(
            session,
            provider_mode="official_cache_only",
        ).fetch("2330.TW", 400)

        assert result.annual_cash_dividend == 20
        assert result.dividend_yield == 5
        assert result.source_provider == "OfficialCachedFundamental+FinMindFundamental"
    finally:
        session.close()
        engine.dispose()


def test_postgres_revision_writes_use_atomic_on_conflict_upserts() -> None:
    observed_at = datetime(2026, 8, 7, tzinfo=timezone.utc)
    periods = normalize_finmind_statement_rows(
        [{"date": "2025-03-31", "type": "EPS", "value": "2"}],
        symbol="2330.TW",
    )
    events = normalize_finmind_dividend_rows(
        [
            {
                "date": "2025-07-01",
                "year": "114年",
                "CashEarningsDistribution": "5",
            }
        ],
        symbol="2330.TW",
    )

    period_sql = str(
        _postgres_fundamental_period_upsert(periods, observed_at=observed_at).compile(
            dialect=postgresql.dialect()
        )
    )
    dividend_sql = str(
        _postgres_dividend_event_upsert(events, observed_at=observed_at).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "ON CONFLICT ON CONSTRAINT uq_company_fundamental_period_revision DO UPDATE" in period_sql
    assert "ON CONFLICT ON CONSTRAINT uq_company_dividend_event_revision DO UPDATE" in dividend_sql


def test_official_cache_provider_builds_ttm_pe_and_complete_annual_dividend() -> None:
    session, engine = _db_session()
    try:
        rows = []
        cumulative = {
            2024: ("1", "3", "6", "10"),
            2025: ("2", "5", "9", "14"),
        }
        for year, values in cumulative.items():
            rows.extend(_statement_row(year, quarter, value) for quarter, value in enumerate(values, 1))
        periods = normalize_official_statement_rows(
            rows,
            market="TW",
            industry_schema="ci",
            source_dataset="TWSE_ci",
        )
        store_fundamental_periods(session, periods)
        store_dividend_events(
            session,
            normalize_twse_dividend_rows(
                [
                    {
                        "公司代號": "2330",
                        "股利年度": "114",
                        "股利所屬期間": "114/01/01~114/12/31",
                        "股利所屬年(季)度": "114年",
                        "期別": "1",
                        "股東配發-盈餘分配之現金股利(元/股)": "5",
                    }
                ]
            ),
        )
        for period in periods:
            upsert_taiwan_daily_bars(
                session,
                [
                    MarketDailyBar(
                        symbol="2330.TW",
                        market="TW",
                        name="台積電",
                        trade_date=period.period_end,
                        open=Decimal("90"),
                        high=Decimal("101"),
                        low=Decimal("89"),
                        close=Decimal("100"),
                        volume=1000,
                        amount=100000,
                        source_provider="fixture",
                        source_dataset="fixture",
                    )
                ],
            )
        session.commit()

        result = OfficialCachedFundamentalProvider(
            session,
            provider_mode="official_cache_only",
        ).fetch("2330.TW", 140)

        assert result.ttm_eps == 14
        assert result.pe_current == 10
        assert result.pe_mean is not None
        assert result.annual_cash_dividend == 5
        assert result.dividend_yield == 5 / 140 * 100
        assert result.source_provider == "OfficialCachedFundamental"
    finally:
        session.close()
        engine.dispose()


class _BootstrapProvider:
    def __init__(self) -> None:
        self.statement_calls = 0
        self.dividend_calls = 0

    def fetch_statement_rows(self, symbol: str) -> list[dict]:
        self.statement_calls += 1
        rows: list[dict] = []
        for year, values in {
            2024: ("1", "2", "3", "4"),
            2025: ("2", "3", "4", "5"),
        }.items():
            rows.extend(
                {
                    "date": date(year, quarter * 3, 31 if quarter in {1, 4} else 30).isoformat(),
                    "type": "EPS",
                    "value": value,
                }
                for quarter, value in enumerate(values, 1)
            )
        return rows

    def fetch_dividend_rows(self, symbol: str) -> list[dict]:
        self.dividend_calls += 1
        return [{"date": "2025-07-01", "year": "114年", "CashEarningsDistribution": "5"}]

    def fetch_historical_prices(self, symbol: str, quarter_dates: list[str]) -> dict[str, float]:
        return {period_end: 100.0 for period_end in quarter_dates}


def test_official_cache_first_bootstraps_once_and_marks_history_unknown() -> None:
    session, engine = _db_session()
    fallback = _BootstrapProvider()
    try:
        first = OfficialCachedFundamentalProvider(
            session,
            fallback_provider=fallback,  # type: ignore[arg-type]
            provider_mode="official_cache_first",
        ).fetch("2330.TW", 140)
        session.commit()
        second = OfficialCachedFundamentalProvider(
            session,
            fallback_provider=fallback,  # type: ignore[arg-type]
            provider_mode="official_cache_first",
        ).fetch("2330.TW", 140)

        assert first.ttm_eps == second.ttm_eps == 14
        assert first.annual_cash_dividend == second.annual_cash_dividend == 5
        assert fallback.statement_calls == 1
        assert fallback.dividend_calls == 1
        assert load_latest_fundamental_periods(
            session,
            symbol="2330.TW",
            allow_historical_unknown=False,
        ) == []
    finally:
        session.close()
        engine.dispose()


class _Response:
    def __init__(self, payload) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


def test_official_refresh_is_bounded_and_persists_partial_success() -> None:
    session, engine = _db_session()
    calls: list[str] = []

    def request_get(url: str, timeout: int):
        calls.append(url)
        if "t187ap06_L_ci" in url:
            return _Response([_statement_row(2025, 4, "14")])
        if "mopsfin_t187ap06_O_bd" in url:
            raise RuntimeError("temporary outage")
        if "t187ap45_L" in url:
            return _Response(
                [
                    {
                        "公司代號": "2330",
                        "股利年度": "114",
                        "股利所屬期間": "114/01/01~114/12/31",
                        "股東配發-盈餘分配之現金股利(元/股)": "5",
                    }
                ]
            )
        if "exDailyQ" in url:
            return _Response({"tables": []})
        return _Response([_statement_row(2025, 4, "14")])

    try:
        result = refresh_official_fundamentals(session, request_get=request_get)
        session.commit()

        assert len(calls) == 16  # 14 datasets + two retries for the failed dataset.
        assert result.status == "partial"
        assert result.datasets_succeeded == 13
        assert result.datasets_failed == 1
        assert result.records_written == 12
        assert session.scalar(select(CompanyFundamentalPeriod.symbol)) == "2330.TW"
    finally:
        session.close()
        engine.dispose()


def test_official_refresh_rejects_empty_required_market_datasets() -> None:
    session, engine = _db_session()

    def request_get(url: str, timeout: int):
        if "exDailyQ" in url:
            return _Response({"tables": []})
        return _Response([])

    try:
        result = refresh_official_fundamentals(session, request_get=request_get)

        assert result.status == "partial"
        assert result.datasets_succeeded == 1
        assert result.datasets_failed == 13
        assert result.records_written == 0
        assert all("normalized dataset is empty" in error for error in result.errors)
    finally:
        session.close()
        engine.dispose()


class _BackfillProvider:
    def __init__(self) -> None:
        self.statement_calls: list[str] = []
        self.dividend_calls: list[str] = []

    def fetch_statement_rows(self, symbol: str) -> list[dict]:
        self.statement_calls.append(symbol)
        return [{"date": "2025-03-31", "type": "EPS", "value": "2"}]

    def fetch_dividend_rows(self, symbol: str) -> list[dict]:
        self.dividend_calls.append(symbol)
        return [{"date": "2025-07-01", "CashEarningsDistribution": "4"}]


def test_finmind_backfill_limits_symbols_and_returns_cursor() -> None:
    session, engine = _db_session()
    provider = _BackfillProvider()
    try:
        result = backfill_fundamentals(
            session,
            symbols=["2330.TW", "8069.TWO", "0050.TW", "bad"],
            limit=1,
            provider=provider,
        )

        assert result.symbols_processed == ["2330.TW"]
        assert result.next_after_symbol == "2330.TW"
        assert provider.statement_calls == ["2330.TW"]
        assert provider.dividend_calls == ["2330.TW"]
        assert result.records_written == 2
    finally:
        session.close()
        engine.dispose()


def test_fundamental_workflow_has_daily_refresh_and_manual_bounded_backfill() -> None:
    workflow = Path(__file__).parents[2] / ".github" / "workflows" / "fundamental-data.yml"
    text = workflow.read_text(encoding="utf-8")

    assert 'cron: "15 23 * * 0-4"' in text
    assert "/internal/fundamentals/refresh" in text
    assert "/internal/fundamentals/backfill" in text
    assert "for batch in 1 2 3 4 5 6" in text
    assert "backfill_after_symbol:" in text
    assert 'after_symbol="${BACKFILL_AFTER_SYMBOL}"' in text
    assert "BACKFILL_NEXT_AFTER_SYMBOL" in text
    assert "exit 2" in text
    assert "limit:10" in text
    assert "X-Internal-Token" in text


def test_internal_fundamental_endpoints_require_auth_and_commit(monkeypatch) -> None:
    session, engine = _db_session()
    app = FastAPI()
    app.include_router(fundamental_router)
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    refresh_result = FundamentalRefreshResult("ok", 14, 0, 12, [])
    backfill_result = FundamentalBackfillResult("ok", ["2330.TW"], 2, None, [])
    try:
        with (
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.refresh_official_fundamentals",
                return_value=refresh_result,
            ),
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.resolve_managed_fundamental_symbols",
                return_value=["2330.TW"],
            ),
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.backfill_fundamentals",
                return_value=backfill_result,
            ),
            patch.object(session, "commit", wraps=session.commit) as commit,
        ):
            client = TestClient(app)
            assert client.post("/internal/fundamentals/refresh").status_code == 401
            refreshed = client.post(
                "/internal/fundamentals/refresh",
                headers={"X-Internal-Token": "test-token"},
            )
            backfilled = client.post(
                "/internal/fundamentals/backfill",
                headers={"X-Internal-Token": "test-token"},
                json={"scope": "managed", "limit": 10},
            )

        assert refreshed.json()["datasets_succeeded"] == 14
        assert backfilled.json()["symbols_processed"] == ["2330.TW"]
        assert commit.call_count == 2
    finally:
        session.close()
        engine.dispose()
