from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.market_bar_provider import (
    MarketDailyBar,
    OfficialMarketBarProviderError,
    normalize_tpex_market_bars,
    normalize_twse_market_bars,
)
from ai_stock_sentinel.daily_radar.market_bar_repository import (
    get_taiwan_daily_bars,
    upsert_taiwan_daily_bars,
)
from ai_stock_sentinel.daily_radar.market_bar_service import refresh_taiwan_market_bars
from ai_stock_sentinel.daily_radar.raw_data import LocalFirstBatchTechnicalFetcher
from ai_stock_sentinel.db.models import TaiwanDailyBar
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.phase1_avwap.provider import ArchiveFirstDailyPriceProvider


def _db_session() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[TaiwanDailyBar.__table__])
    return Session(engine), engine


def _load_migration() -> ModuleType:
    path = Path(__file__).parents[1] / "alembic" / "versions" / "3d4e5f6a7b8c_add_taiwan_daily_bars.py"
    spec = importlib.util.spec_from_file_location("taiwan_daily_bars_migration", path)
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


def test_taiwan_daily_bars_migration_is_additive_and_reversible() -> None:
    upgrade_sql = _migration_sql("upgrade")
    downgrade_sql = _migration_sql("downgrade")

    assert "CREATE TABLE taiwan_daily_bars" in upgrade_sql
    assert "CONSTRAINT uq_taiwan_daily_bar_symbol_date_dataset_mode UNIQUE" in upgrade_sql
    assert "CONSTRAINT ck_taiwan_daily_bar_market CHECK" in upgrade_sql
    assert "CREATE INDEX idx_taiwan_daily_bars_symbol_date" in upgrade_sql
    assert "CREATE INDEX idx_taiwan_daily_bars_date_market" in upgrade_sql
    assert downgrade_sql.index("DROP INDEX idx_taiwan_daily_bars_date_market") < downgrade_sql.index(
        "DROP TABLE taiwan_daily_bars"
    )


def test_official_market_bar_parsers_keep_only_four_digit_ordinary_stocks() -> None:
    twse = normalize_twse_market_bars(
        {
            "stat": "OK",
            "date": "20260610",
            "tables": [
                {
                    "fields": [
                        "證券代號",
                        "證券名稱",
                        "成交股數",
                        "成交金額",
                        "開盤價",
                        "最高價",
                        "最低價",
                        "收盤價",
                    ],
                    "data": [
                        ["2330", "台積電", "10,000", "10,500,000", "1000", "1060", "995", "1050"],
                        ["0050", "ETF", "20,000", "4,000,000", "200", "205", "199", "204"],
                    ],
                }
            ],
        },
        expected_date=date(2026, 6, 10),
    )
    tpex = normalize_tpex_market_bars(
        {
            "tables": [
                {
                    "date": "115/06/10",
                    "fields": [
                        "代號",
                        "名稱",
                        "收盤 ",
                        "開盤 ",
                        "最高 ",
                        "最低",
                        "成交股數  ",
                        " 成交金額(元)",
                    ],
                    "data": [
                        ["8069", "元太", "240", "235", "242", "233", "30,000", "7,200,000"],
                        ["00679B", "債券ETF", "26", "26", "27", "25", "50,000", "1,300,000"],
                    ],
                }
            ]
        },
        expected_date=date(2026, 6, 10),
    )

    assert [bar.symbol for bar in twse] == ["2330.TW"]
    assert [bar.symbol for bar in tpex] == ["8069.TWO"]
    assert twse[0].amount == 10_500_000
    assert tpex[0].source_dataset == "TPEX_otc_quotes_no1430"


def test_tpex_market_bar_parser_treats_official_no_data_as_non_trading_day() -> None:
    assert normalize_tpex_market_bars(
        {"stat": "查無資料", "tables": []},
        expected_date=date(2026, 6, 11),
    ) == []


def _bar(symbol: str, trade_date: date, close: int, *, market: str = "TW") -> MarketDailyBar:
    suffix_market = "TWO" if symbol.endswith(".TWO") else market
    return MarketDailyBar(
        symbol=symbol,
        market=suffix_market,
        name="fixture",
        trade_date=trade_date,
        open=Decimal(close - 1),
        high=Decimal(close + 1),
        low=Decimal(close - 2),
        close=Decimal(close),
        volume=1000 + close,
        amount=(1000 + close) * close,
        source_provider="fixture",
        source_dataset="fixture_dataset",
    )


def test_market_bar_repository_upserts_same_identity_without_duplicates() -> None:
    session, engine = _db_session()
    try:
        upsert_taiwan_daily_bars(session, [_bar("2330.TW", date(2026, 6, 10), 100)])
        upsert_taiwan_daily_bars(session, [_bar("2330.TW", date(2026, 6, 10), 105)])
        session.commit()

        rows = get_taiwan_daily_bars(
            session,
            symbols=["2330.TW"],
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 10),
        )
        assert len(rows) == 1
        assert rows[0].close == Decimal("105.000000")
    finally:
        session.close()
        engine.dispose()


class _FixtureMarketProvider:
    def __init__(self, *, fail_tpex: bool = False) -> None:
        self.fail_tpex = fail_tpex

    def fetch_market(self, *, market: str, trade_date: date):
        if market == "TWO" and self.fail_tpex:
            raise OfficialMarketBarProviderError("fixture_failed", market=market)
        symbol = "2330.TW" if market == "TW" else "8069.TWO"
        return [_bar(symbol, trade_date, 100, market=market)]


def test_market_bar_service_persists_partial_success_and_reports_market_error() -> None:
    session, engine = _db_session()
    try:
        result = refresh_taiwan_market_bars(
            session,
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            provider=_FixtureMarketProvider(fail_tpex=True),  # type: ignore[arg-type]
        )
        session.commit()

        assert result["status"] == "failed"
        assert result["records_written"] == 1
        assert result["errors"] == [
            {"code": "fixture_failed", "market": "TWO", "trade_date": "2026-06-10"}
        ]
        assert session.query(TaiwanDailyBar).one().symbol == "2330.TW"
    finally:
        session.close()
        engine.dispose()


class _FallbackFetcher:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def fetch(self, symbols, *, run_date: date):
        self.calls.append(list(symbols))
        return {symbol: {"name": symbol, "fallback": True} for symbol in symbols}


def test_unadjusted_archive_does_not_replace_adjusted_technical_history() -> None:
    session, engine = _db_session()
    try:
        start = date(2026, 3, 1)
        bars = [_bar("2330.TW", start + timedelta(days=index), 100 + index) for index in range(70)]
        upsert_taiwan_daily_bars(session, bars)
        session.commit()
        fallback = _FallbackFetcher()
        fetcher = LocalFirstBatchTechnicalFetcher(
            session,
            fallback_fetcher=fallback,
            provider_mode="official_first",
            min_trading_bars=60,
        )

        payloads = fetcher.fetch(
            ["2330.TW", "AAPL"],
            run_date=start + timedelta(days=69),
        )

        assert fallback.calls == [["2330.TW", "AAPL"]]
        assert payloads["2330.TW"]["fallback"] is True
        assert payloads["AAPL"]["fallback"] is True
    finally:
        session.close()
        engine.dispose()


def test_local_official_only_does_not_call_yfinance_for_incomplete_archive() -> None:
    session, engine = _db_session()
    try:
        fallback = _FallbackFetcher()
        fetcher = LocalFirstBatchTechnicalFetcher(
            session,
            fallback_fetcher=fallback,
            provider_mode="official_only",
        )

        assert fetcher.fetch(["2330.TW", "AAPL"], run_date=date(2026, 6, 10)) == {}
        assert fallback.calls == []
    finally:
        session.close()
        engine.dispose()


class _FallbackDailyPriceProvider:
    source_provider = "fixture_fallback"
    source_dataset = "fixture_fallback_dataset"

    def __init__(self) -> None:
        self.calls = 0

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date):
        self.calls += 1
        return []


def test_phase1_avwap_provider_reads_shared_archive_before_legacy_provider() -> None:
    session, engine = _db_session()
    try:
        start = date(2026, 3, 1)
        bars = [_bar("2330.TW", start + timedelta(days=index), 100 + index) for index in range(70)]
        upsert_taiwan_daily_bars(session, bars)
        session.commit()
        fallback = _FallbackDailyPriceProvider()
        provider = ArchiveFirstDailyPriceProvider(
            session,
            fallback_provider=fallback,
            provider_mode="official_first",
            min_trading_bars=60,
        )

        history = provider.fetch_history(
            "2330.TW",
            start_date=start,
            end_date=start + timedelta(days=69),
        )

        assert len(history) == 70
        assert history[-1].close == 169.0
        assert fallback.calls == 0
        result = provider.fetch_history_result(
            "2330.TW",
            start_date=start,
            end_date=start + timedelta(days=69),
        )
        assert result.source_provider == "fixture"
        assert result.source_dataset == "fixture_dataset"
    finally:
        session.close()
        engine.dispose()


def test_phase1_avwap_provider_reports_actual_fallback_source() -> None:
    session, engine = _db_session()
    try:
        fallback = _FallbackDailyPriceProvider()
        provider = ArchiveFirstDailyPriceProvider(
            session,
            fallback_provider=fallback,
            provider_mode="official_first",
            min_trading_bars=60,
        )

        result = provider.fetch_history_result(
            "2330.TW",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 6, 10),
        )

        assert result.bars == []
        assert result.source_provider == "fixture_fallback"
        assert result.source_dataset == "fixture_fallback_dataset"
        assert fallback.calls == 1
    finally:
        session.close()
        engine.dispose()
