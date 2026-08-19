from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

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
    create_fundamental_backfill_job,
    fundamental_raw_pool_date_is_completed,
    get_fundamental_backfill_job,
    refresh_official_fundamentals,
    resolve_managed_fundamental_symbols,
    resolve_pending_fundamental_backfill_symbols,
)
from ai_stock_sentinel.data_sources.fundamental.router import router as fundamental_router
from ai_stock_sentinel.db.models import (
    CompanyDividendEvent,
    CompanyFundamentalPeriod,
    DailyRadarPreparedRun,
    FundamentalBackfillJob,
    StockRawData,
    TaiwanDailyBar,
    UserPortfolio,
    UserWatchlist,
)
from ai_stock_sentinel.db.session import Base, get_db
from ai_stock_sentinel.user_models.user import User


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
            FundamentalBackfillJob.__table__,
        ],
    )
    return Session(engine), engine


def _managed_symbol_db_session() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            UserPortfolio.__table__,
            UserWatchlist.__table__,
            DailyRadarPreparedRun.__table__,
            StockRawData.__table__,
        ],
    )
    return Session(engine), engine


def _load_migration(
    filename: str = "4e5f6a7b8c9d_add_fundamental_version_tables.py",
) -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / filename
    )
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_sql(
    direction: str,
    filename: str = "4e5f6a7b8c9d_add_fundamental_version_tables.py",
) -> str:
    migration = _load_migration(filename)
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


def test_fundamental_backfill_job_migration_is_additive_and_reversible() -> None:
    filename = "5f6a7b8c9d0e_add_fundamental_backfill_jobs.py"
    upgrade_sql = _migration_sql("upgrade", filename)
    downgrade_sql = _migration_sql("downgrade", filename)

    assert "CREATE TABLE fundamental_backfill_jobs" in upgrade_sql
    assert "ck_fundamental_backfill_job_status" in upgrade_sql
    assert "next_after_symbol VARCHAR(20)" in upgrade_sql
    assert "DROP TABLE fundamental_backfill_jobs" in downgrade_sql


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


def test_official_cached_fundamental_fetch_as_of_excludes_future_observations() -> None:
    session, engine = _db_session()
    try:
        periods = normalize_finmind_statement_rows(
            [
                {"date": "2025-03-31", "type": "EPS", "value": "2"},
                {"date": "2025-06-30", "type": "EPS", "value": "3"},
                {"date": "2025-09-30", "type": "EPS", "value": "4"},
                {"date": "2025-12-31", "type": "EPS", "value": "5"},
            ],
            symbol="2330.TW",
        )
        store_fundamental_periods(session, periods)
        future_observed_at = datetime(2026, 8, 14, tzinfo=timezone.utc)
        for row in session.scalars(select(CompanyFundamentalPeriod)).all():
            row.first_observed_at = future_observed_at
            row.last_observed_at = future_observed_at
        session.flush()
        provider = OfficialCachedFundamentalProvider(
            session,
            provider_mode="official_cache_only",
        )

        current = provider.fetch("2330.TW", 140)
        historical = provider.fetch_as_of(
            "2330.TW",
            140,
            as_of_date=date(2026, 8, 13),
        )

        assert current.ttm_eps == 14
        assert historical.ttm_eps is None
        assert historical.pe_current is None
    finally:
        session.close()
        engine.dispose()


def test_as_of_date_uses_taipei_observation_date() -> None:
    session, engine = _db_session()
    try:
        store_fundamental_periods(
            session,
            normalize_finmind_statement_rows(
                [
                    {"date": "2025-09-30", "type": "EPS", "value": "2"},
                    {"date": "2025-12-31", "type": "EPS", "value": "3"},
                    {"date": "2026-03-31", "type": "EPS", "value": "4"},
                    {"date": "2026-06-30", "type": "EPS", "value": "5"},
                ],
                symbol="2330.TW",
            ),
        )
        next_taipei_day = datetime(2026, 8, 17, 23, 35, tzinfo=timezone.utc)
        for row in session.scalars(select(CompanyFundamentalPeriod)).all():
            row.first_observed_at = next_taipei_day
            row.last_observed_at = next_taipei_day
        session.flush()
        provider = OfficialCachedFundamentalProvider(
            session,
            provider_mode="official_cache_only",
        )

        prior_day = provider.fetch_as_of(
            "2330.TW",
            140,
            as_of_date=date(2026, 8, 17),
        )
        observed_day = provider.fetch_as_of(
            "2330.TW",
            140,
            as_of_date=date(2026, 8, 18),
        )

        assert prior_day.ttm_eps is None
        assert observed_day.ttm_eps == 14
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


def test_bootstrap_q1_anchors_official_q2_cumulative_gap() -> None:
    session, engine = _db_session()
    try:
        store_fundamental_periods(
            session,
            normalize_finmind_statement_rows(
                [
                    {"date": "2025-09-30", "type": "EPS", "value": "3"},
                    {"date": "2025-12-31", "type": "EPS", "value": "4"},
                    {"date": "2026-03-31", "type": "EPS", "value": "5"},
                    {"date": "2026-06-30", "type": "EPS", "value": "6"},
                ],
                symbol="2330.TW",
            ),
        )
        store_fundamental_periods(
            session,
            normalize_official_statement_rows(
                [_statement_row(2026, 2, "11")],
                market="TW",
                industry_schema="ci",
                source_dataset="TWSE_ci",
            ),
        )

        periods = load_latest_fundamental_periods(session, symbol="2330.TW")
        result = OfficialCachedFundamentalProvider(
            session,
            provider_mode="official_cache_only",
        ).fetch("2330.TW", 180)

        assert periods[-1].quarter_eps == Decimal("6")
        assert periods[-1].cumulative_eps == Decimal("11")
        assert periods[-1].source_provider == "official_openapi"
        assert result.ttm_eps == 18
        assert result.source_provider == "OfficialCachedFundamental+FinMindFundamental"
    finally:
        session.close()
        engine.dispose()


def test_direct_bootstrap_quarter_fills_later_official_cumulative_gap() -> None:
    session, engine = _db_session()
    try:
        store_fundamental_periods(
            session,
            normalize_finmind_statement_rows(
                [
                    {"date": "2025-12-31", "type": "EPS", "value": "3"},
                    {"date": "2026-03-31", "type": "EPS", "value": "4"},
                    {"date": "2026-06-30", "type": "EPS", "value": "5"},
                    {"date": "2026-09-30", "type": "EPS", "value": "6"},
                ],
                symbol="2330.TW",
            ),
        )
        store_fundamental_periods(
            session,
            normalize_official_statement_rows(
                [_statement_row(2026, 3, "15")],
                market="TW",
                industry_schema="ci",
                source_dataset="TWSE_ci",
            ),
        )

        periods = load_latest_fundamental_periods(session, symbol="2330.TW")
        result = OfficialCachedFundamentalProvider(
            session,
            provider_mode="official_cache_only",
        ).fetch("2330.TW", 180)

        assert periods[-1].quarter_eps == Decimal("6")
        assert periods[-1].source_provider == "official_openapi"
        assert periods[-1].quarter_eps_source_provider == "finmind_bootstrap"
        assert result.ttm_eps == 18
        assert result.source_provider == "OfficialCachedFundamental+FinMindFundamental"
    finally:
        session.close()
        engine.dispose()


def test_provider_does_not_use_stale_ttm_when_latest_period_is_incomplete() -> None:
    session, engine = _db_session()
    try:
        store_fundamental_periods(
            session,
            normalize_finmind_statement_rows(
                [
                    {"date": "2025-06-30", "type": "EPS", "value": "2"},
                    {"date": "2025-09-30", "type": "EPS", "value": "3"},
                    {"date": "2025-12-31", "type": "EPS", "value": "4"},
                    {"date": "2026-03-31", "type": "EPS", "value": "5"},
                ],
                symbol="2330.TW",
            ),
        )
        store_fundamental_periods(
            session,
            normalize_official_statement_rows(
                [_statement_row(2026, 3, "18")],
                market="TW",
                industry_schema="ci",
                source_dataset="TWSE_ci",
            ),
        )

        result = OfficialCachedFundamentalProvider(
            session,
            provider_mode="official_cache_only",
        ).fetch("2330.TW", 140)

        assert result.ttm_eps is None
        assert "基本面快取缺少四個連續單季 EPS，TTM EPS 無法計算" in result.warnings
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
        assert result.datasets_skipped == 0
        assert result.datasets_failed == 13
        assert result.records_written == 0
        assert result.skipped_datasets == []
        assert all("normalized dataset is empty" in error for error in result.errors)
    finally:
        session.close()
        engine.dispose()


def test_official_refresh_skips_known_unpublished_statement_placeholders() -> None:
    session, engine = _db_session()

    def request_get(url: str, timeout: int):
        if "t187ap06_L_" in url:
            return _Response(
                [
                    {
                        "出表日期": "1150810",
                        "年度": "",
                        "季別": "",
                        "公司代號": "",
                        "公司名稱": "",
                        "基本每股盈餘（元）": "",
                    }
                ]
            )
        if "mopsfin_t187ap06_O_fh" in url:
            return _Response(
                [
                    {
                        "出表日期": "1150809",
                        "年度": "",
                        "季別": "",
                        "公司代號": "",
                        "公司名稱": "",
                        "基本每股盈餘（元）": "",
                    }
                ]
            )
        if "mopsfin_t187ap06_O_" in url:
            return _Response(
                [
                    {
                        "Date": "1150809",
                        "Year": "",
                        "Season": "",
                        "SecuritiesCompanyCode": "",
                        "CompanyName": "",
                        "BasicEarningsPerShare": "",
                    }
                ]
            )
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
        return _Response({"tables": []})

    try:
        store_fundamental_periods(
            session,
            normalize_official_statement_rows(
                [_statement_row(2025, 4, "14")],
                market="TW",
                industry_schema="basi",
                source_dataset="TWSE_basi",
            ),
        )
        session.commit()
        existing = session.scalar(select(CompanyFundamentalPeriod))
        assert existing is not None
        previous_last_observed_at = existing.last_observed_at

        result = refresh_official_fundamentals(session, request_get=request_get)
        session.commit()

        assert result.status == "ok"
        assert result.datasets_succeeded == 2
        assert result.datasets_skipped == 12
        assert result.datasets_failed == 0
        assert result.records_written == 1
        assert result.skipped_datasets == sorted(
            f"{exchange}_{schema}"
            for schema in ("basi", "bd", "ci", "fh", "ins", "mim")
            for exchange in ("TWSE", "TPEX")
        )
        assert result.errors == []
        assert session.scalar(select(CompanyDividendEvent.symbol)) == "2330.TW"
        retained = session.scalars(select(CompanyFundamentalPeriod)).all()
        assert len(retained) == 1
        assert retained[0].last_observed_at == previous_last_observed_at
    finally:
        session.close()
        engine.dispose()


def test_official_refresh_does_not_skip_nonblank_unparseable_statement_rows() -> None:
    session, engine = _db_session()

    def request_get(url: str, timeout: int):
        if "t187ap06_L_basi" in url:
            return _Response(
                [
                    {
                        "出表日期": "1150810",
                        "年度": "",
                        "季別": "",
                        "公司代號": "",
                        "公司名稱": "unexpected content",
                    }
                ]
            )
        if "exDailyQ" in url:
            return _Response({"tables": []})
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
        return _Response([_statement_row(2025, 4, "14")])

    try:
        result = refresh_official_fundamentals(session, request_get=request_get)

        assert result.status == "partial"
        assert result.datasets_succeeded == 13
        assert result.datasets_skipped == 0
        assert result.datasets_failed == 1
        assert result.skipped_datasets == []
        assert result.errors == [
            "TWSE_basi: refresh failed: normalized dataset is empty"
        ]
    finally:
        session.close()
        engine.dispose()


def test_official_refresh_does_not_skip_placeholder_with_invalid_report_date() -> None:
    session, engine = _db_session()

    def request_get(url: str, timeout: int):
        if "t187ap06_L_basi" in url:
            return _Response(
                [
                    {
                        "出表日期": "not-a-date",
                        "年度": "",
                        "季別": "",
                        "公司代號": "",
                    }
                ]
            )
        if "exDailyQ" in url:
            return _Response({"tables": []})
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
        return _Response([_statement_row(2025, 4, "14")])

    try:
        result = refresh_official_fundamentals(session, request_get=request_get)

        assert result.status == "partial"
        assert result.datasets_skipped == 0
        assert result.datasets_failed == 1
        assert result.errors == [
            "TWSE_basi: refresh failed: normalized dataset is empty"
        ]
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


def test_managed_backfill_symbols_include_latest_final_ai_raw_pool() -> None:
    session, engine = _managed_symbol_db_session()
    try:
        user = User(google_sub="fixture", email="fixture@example.com")
        session.add(user)
        session.flush()
        session.add_all(
            [
                UserPortfolio(
                    user_id=user.id,
                    symbol="2330.TW",
                    entry_price=100,
                    quantity=1,
                    entry_date=date(2026, 8, 1),
                ),
                UserWatchlist(user_id=user.id, symbol="2454.TW"),
                DailyRadarPreparedRun(
                    run_date=date(2026, 8, 17),
                    market="TW",
                    selected_symbols=["3008.TW", "SPY"],
                    universe=[],
                    symbol_count=2,
                    step_statuses={"refresh-ai-evidence": {"status": "completed"}},
                ),
                StockRawData(
                    symbol="1304.TW",
                    record_date=date(2026, 8, 17),
                    technical={},
                    raw_data_is_final=True,
                ),
                StockRawData(
                    symbol="1319.TW",
                    record_date=date(2026, 8, 16),
                    technical={},
                    raw_data_is_final=True,
                ),
                StockRawData(
                    symbol="1440.TW",
                    record_date=date(2026, 8, 17),
                    technical={},
                    raw_data_is_final=False,
                ),
                StockRawData(
                    symbol="AAPL",
                    record_date=date(2026, 8, 18),
                    technical={},
                    raw_data_is_final=True,
                ),
            ]
        )
        session.commit()

        symbols = resolve_managed_fundamental_symbols(session)

        assert symbols == ["1304.TW", "2330.TW", "2454.TW", "3008.TW"]

        session.add(
            StockRawData(
                symbol="1504.TW",
                record_date=date(2026, 8, 18),
                technical={},
                raw_data_is_final=True,
            )
        )
        session.commit()

        assert resolve_managed_fundamental_symbols(session) == [
            "1304.TW",
            "2330.TW",
            "2454.TW",
            "3008.TW",
        ]
        assert resolve_managed_fundamental_symbols(
            session,
            raw_pool_date=date(2026, 8, 17),
        ) == ["1304.TW", "2330.TW", "2454.TW", "3008.TW"]
        assert fundamental_raw_pool_date_is_completed(
            session,
            record_date=date(2026, 8, 17),
        )
        assert not fundamental_raw_pool_date_is_completed(
            session,
            record_date=date(2026, 8, 18),
        )

        session.add(
            DailyRadarPreparedRun(
                run_date=date(2026, 8, 18),
                market="TW",
                selected_symbols=["3008.TW"],
                universe=[],
                symbol_count=1,
                step_statuses={"refresh-ai-evidence": {"status": "completed"}},
            )
        )
        session.commit()

        assert resolve_managed_fundamental_symbols(session) == [
            "1504.TW",
            "2330.TW",
            "2454.TW",
            "3008.TW",
        ]
    finally:
        session.close()
        engine.dispose()


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


def test_finmind_backfill_does_not_advance_cursor_after_partial_failure() -> None:
    session, engine = _db_session()
    provider = _BackfillProvider()
    provider.fetch_statement_rows = MagicMock(side_effect=RuntimeError("temporary outage"))
    try:
        result = backfill_fundamentals(
            session,
            symbols=["2330.TW", "2454.TW"],
            limit=1,
            provider=provider,
        )

        assert result.status == "partial"
        assert result.next_after_symbol is None
        assert result.errors == [
            "2330.TW: statement backfill failed: temporary outage"
        ]
    finally:
        session.close()
        engine.dispose()


def test_finmind_backfill_skips_already_sufficient_cached_lanes() -> None:
    session, engine = _db_session()
    provider = _BackfillProvider()
    try:
        store_fundamental_periods(
            session,
            normalize_finmind_statement_rows(
                [
                    {
                        "date": date(year, quarter * 3, 31 if quarter in {1, 4} else 30).isoformat(),
                        "type": "EPS",
                        "value": str(quarter),
                    }
                    for year in (2025, 2026)
                    for quarter in range(1, 5)
                ],
                symbol="2330.TW",
            ),
        )
        store_dividend_events(
            session,
            normalize_finmind_dividend_rows(
                [
                    {
                        "date": "2026-07-01",
                        "year": "115年",
                        "CashEarningsDistribution": "4",
                    }
                ],
                symbol="2330.TW",
            ),
        )

        result = backfill_fundamentals(
            session,
            symbols=["2330.TW"],
            provider=provider,
        )

        assert result.status == "ok"
        assert result.records_written == 0
        assert provider.statement_calls == []
        assert provider.dividend_calls == []
    finally:
        session.close()
        engine.dispose()


def test_fundamental_backfill_job_freezes_only_pending_symbols() -> None:
    session, engine = _db_session()
    try:
        store_fundamental_periods(
            session,
            normalize_finmind_statement_rows(
                [
                    {
                        "date": date(year, quarter * 3, 31 if quarter in {1, 4} else 30).isoformat(),
                        "type": "EPS",
                        "value": str(quarter),
                    }
                    for year in (2025, 2026)
                    for quarter in range(1, 5)
                ],
                symbol="2330.TW",
            ),
        )
        store_dividend_events(
            session,
            normalize_finmind_dividend_rows(
                [
                    {
                        "date": "2026-07-01",
                        "year": "115年",
                        "CashEarningsDistribution": "4",
                    }
                ],
                symbol="2330.TW",
            ),
        )

        pending = resolve_pending_fundamental_backfill_symbols(
            session,
            symbols=["2330.TW", "1304.TW"],
        )
        job = create_fundamental_backfill_job(
            session,
            symbols=pending,
            raw_pool_date=date(2026, 8, 17),
        )
        session.commit()

        loaded = get_fundamental_backfill_job(session, job_id=job.id)

        assert pending == ["1304.TW"]
        assert loaded is not None
        assert loaded.symbols == ["1304.TW"]
        assert loaded.raw_pool_date == date(2026, 8, 17)
        assert loaded.next_after_symbol is None
        assert loaded.status == "running"
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
    assert "backfill_job_id:" in text
    assert "backfill_raw_pool_date:" in text
    assert 'after_symbol="${BACKFILL_AFTER_SYMBOL}"' in text
    assert 'job_id="${BACKFILL_JOB_ID}"' in text
    assert 'raw_pool_date="${BACKFILL_RAW_POOL_DATE}"' in text
    assert "{raw_pool_date:$raw_pool_date}" in text
    assert "BACKFILL_NEXT_AFTER_SYMBOL" in text
    assert "BACKFILL_JOB_ID" in text
    assert "Backfill partially failed" in text
    assert "exit 2" in text
    assert "limit:10" in text
    assert "X-Internal-Token" in text


def test_internal_fundamental_endpoints_require_auth_and_commit(monkeypatch) -> None:
    session, engine = _db_session()
    app = FastAPI()
    app.include_router(fundamental_router)
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-token")
    refresh_result = FundamentalRefreshResult(
        status="ok",
        datasets_succeeded=14,
        datasets_skipped=0,
        datasets_failed=0,
        records_written=12,
        skipped_datasets=[],
        errors=[],
    )
    backfill_result = FundamentalBackfillResult("ok", ["2330.TW"], 2, None, [])
    created_job = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        raw_pool_date=date(2026, 8, 17),
        symbols=["2330.TW"],
        next_after_symbol=None,
        status="running",
    )
    resumed_job = SimpleNamespace(
        id=created_job.id,
        raw_pool_date=date(2026, 8, 17),
        symbols=["2330.TW", "2454.TW"],
        next_after_symbol="2330.TW",
        status="running",
    )
    try:
        with (
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.refresh_official_fundamentals",
                return_value=refresh_result,
            ),
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.resolve_latest_fundamental_raw_pool_date",
                return_value=date(2026, 8, 17),
            ),
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.resolve_fundamental_raw_pool_symbols",
                return_value=["2330.TW"],
            ),
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.fundamental_raw_pool_date_is_completed",
                return_value=True,
            ) as completed_pool,
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.resolve_managed_fundamental_symbols",
                return_value=["2330.TW"],
            ),
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.resolve_pending_fundamental_backfill_symbols",
                return_value=["2330.TW"],
            ),
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.create_fundamental_backfill_job",
                return_value=created_job,
            ),
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.get_fundamental_backfill_job",
                return_value=resumed_job,
            ),
            patch(
                "ai_stock_sentinel.data_sources.fundamental.router.backfill_fundamentals",
                return_value=backfill_result,
            ),
            patch.object(session, "add"),
            patch.object(session, "commit", wraps=session.commit) as commit,
        ):
            client = TestClient(app)
            assert client.post("/internal/fundamentals/refresh").status_code == 401
            refreshed = client.post(
                "/internal/fundamentals/refresh",
                headers={"X-Internal-Token": "test-token"},
            )
            unpinned_resume = client.post(
                "/internal/fundamentals/backfill",
                headers={"X-Internal-Token": "test-token"},
                json={"scope": "managed", "after_symbol": "1802.TW"},
            )
            backfilled = client.post(
                "/internal/fundamentals/backfill",
                headers={"X-Internal-Token": "test-token"},
                json={"scope": "managed", "limit": 10},
            )
            completed_pool.return_value = False
            incomplete_pool = client.post(
                "/internal/fundamentals/backfill",
                headers={"X-Internal-Token": "test-token"},
                json={"scope": "managed", "raw_pool_date": "2026-08-16"},
            )
            mismatched_cursor = client.post(
                "/internal/fundamentals/backfill",
                headers={"X-Internal-Token": "test-token"},
                json={
                    "scope": "managed",
                    "job_id": resumed_job.id,
                    "after_symbol": "2454.TW",
                },
            )
            resumed_job.status = "completed"
            completed_job = client.post(
                "/internal/fundamentals/backfill",
                headers={"X-Internal-Token": "test-token"},
                json={"scope": "managed", "job_id": resumed_job.id},
            )

        assert refreshed.json()["datasets_succeeded"] == 14
        assert refreshed.json()["datasets_skipped"] == 0
        assert refreshed.json()["skipped_datasets"] == []
        assert unpinned_resume.status_code == 422
        assert unpinned_resume.json()["detail"]["code"] == (
            "fundamental_backfill_job_id_required"
        )
        assert backfilled.json()["symbols_processed"] == ["2330.TW"]
        assert backfilled.json()["job_id"]
        assert backfilled.json()["raw_pool_date"] == "2026-08-17"
        assert incomplete_pool.status_code == 409
        assert incomplete_pool.json()["detail"] == {
            "code": "fundamental_backfill_raw_pool_not_completed",
            "raw_pool_date": "2026-08-16",
        }
        assert mismatched_cursor.status_code == 409
        assert mismatched_cursor.json()["detail"] == {
            "code": "fundamental_backfill_cursor_mismatch",
            "expected_after_symbol": "2330.TW",
        }
        assert completed_job.status_code == 409
        assert completed_job.json()["detail"] == {
            "code": "fundamental_backfill_job_completed"
        }
        assert commit.call_count == 2
    finally:
        session.close()
        engine.dispose()
