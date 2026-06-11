from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_cache_records
from ai_stock_sentinel.daily_radar.prefilter import prefilter_record
from ai_stock_sentinel.daily_radar.raw_data import (
    YFinanceBatchTechnicalFetcher,
    ensure_daily_radar_raw_rows,
)
from ai_stock_sentinel.db.models import StockRawData
from ai_stock_sentinel.db.session import Base


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[StockRawData.__table__])
    with Session(engine) as session:
        yield session


class FakeBatchFetcher:
    def __init__(self, payloads: dict[str, dict[str, Any]] | None = None) -> None:
        self.payloads = payloads or {}
        self.calls: list[tuple[list[str], date]] = []

    def fetch(self, symbols: list[str], *, run_date: date) -> dict[str, dict[str, Any]]:
        self.calls.append((list(symbols), run_date))
        return {symbol: self.payloads.get(symbol) or _technical_payload(symbol, run_date) for symbol in symbols}


def _technical_payload(symbol: str, run_date: date) -> dict[str, Any]:
    return {
        "name": f"{symbol} fixture",
        "ohlcv": {
            "open": 100.0,
            "high": 108.0,
            "low": 98.0,
            "close": 106.0,
            "previous_close": 102.0,
            "volume": 4_000_000,
            "avg_volume_20": 2_000_000,
        },
        "indicators": {
            "ma5": 104.0,
            "ma20": 101.0,
            "ma60": 96.0,
            "rsi14": 62.0,
            "bias20": 4.95,
            "volume_ratio": 2.0,
            "missing_trading_days_60": 0,
            "mfi14": 64.0,
            "macd": 1.2,
            "macd_signal": 0.8,
            "macd_histogram": 0.4,
            "kd_k": 72.0,
            "kd_d": 65.0,
            "atr14": 3.2,
            "support_level": 95.0,
            "resistance_level": 110.0,
            "obv": 12_000_000,
            "obv_trend": "rising",
        },
        "data_dates": {"ohlcv": run_date.isoformat(), "technical_indicators": run_date.isoformat()},
    }


def _add_raw_data(
    session: Session,
    *,
    symbol: str,
    record_date: date,
    is_final: bool,
    technical: dict[str, Any] | None = None,
) -> StockRawData:
    row = StockRawData(
        symbol=symbol,
        record_date=record_date,
        technical=technical or _technical_payload(symbol, record_date),
        institutional={},
        fundamental={"margin": {}},
        raw_data_is_final=is_final,
    )
    session.add(row)
    session.flush()
    return row


def test_ensure_daily_radar_raw_rows_fetches_missing_symbols_once_and_preserves_selected_order(
    db_session: Session,
) -> None:
    run_date = date(2026, 6, 2)
    _add_raw_data(db_session, symbol="2330.TW", record_date=run_date, is_final=True)
    fetcher = FakeBatchFetcher()

    rows = ensure_daily_radar_raw_rows(
        db_session,
        run_date,
        ["2330.TW", "2317.TW", "2454.TW"],
        technical_fetcher=fetcher,
        institutional_payloads_by_symbol={
            "2317.TW": {
                "flow_label": "institutional_accumulation",
                "foreign_net_cumulative": 4200,
                "trust_net_cumulative": 1800,
                "three_party_net": 6500,
                "consecutive_buy_days": 5,
                "data_dates": {"institutional_flow": run_date.isoformat()},
            },
            "9999.TW": {"universe_primary_track": "price_volume"},
        },
    )

    assert fetcher.calls == [(["2317.TW", "2454.TW"], run_date)]
    assert [row.symbol for row in rows] == ["2330.TW", "2317.TW", "2454.TW"]
    assert all(row.record_date == run_date and row.raw_data_is_final for row in rows)

    loaded_records = load_daily_radar_cache_records(rows)
    loaded_by_symbol = {record["symbol"]: record for record in loaded_records}
    assert loaded_by_symbol["2317.TW"]["ohlcv"]["close"] == 106.0
    assert loaded_by_symbol["2317.TW"]["indicators"]["ma20"] == 101.0
    assert loaded_by_symbol["2317.TW"]["institutional_flow"]["foreign_net_shares"] == 4200
    assert loaded_by_symbol["2317.TW"]["data_dates"] == {
        "ohlcv": run_date.isoformat(),
        "technical_indicators": run_date.isoformat(),
        "institutional_flow": run_date.isoformat(),
        "margin": run_date.isoformat(),
    }
    prefilter_result = prefilter_record(loaded_by_symbol["2317.TW"])
    assert "stale_core_data" not in {reason["code"] for reason in prefilter_result["prefilter_reasons"]}
    assert loaded_by_symbol["2454.TW"]["margin"] == {}
    assert loaded_by_symbol["2454.TW"]["data_dates"]["margin"] == run_date.isoformat()
    assert "9999.TW" not in loaded_by_symbol
    assert db_session.query(StockRawData).filter(StockRawData.symbol == "9999.TW").one_or_none() is None


def test_ensure_daily_radar_raw_rows_updates_non_final_rows_without_refetching_final_rows(
    db_session: Session,
) -> None:
    run_date = date(2026, 6, 2)
    final_row = _add_raw_data(db_session, symbol="2330.TW", record_date=run_date, is_final=True)
    draft_row = _add_raw_data(
        db_session,
        symbol="2317.TW",
        record_date=run_date,
        is_final=False,
        technical=_technical_payload("2317.TW", run_date) | {"name": "draft"},
    )
    fetcher = FakeBatchFetcher({"2317.TW": _technical_payload("2317.TW", run_date) | {"name": "finalized"}})

    rows = ensure_daily_radar_raw_rows(
        db_session,
        run_date,
        ["2330.TW", "2317.TW"],
        technical_fetcher=fetcher,
    )

    assert fetcher.calls == [(["2317.TW"], run_date)]
    assert [row.id for row in rows] == [final_row.id, draft_row.id]
    assert rows[1].raw_data_is_final is True
    assert rows[1].technical["name"] == "finalized"


def test_ensure_daily_radar_raw_rows_does_not_call_fetcher_when_all_selected_rows_are_final(
    db_session: Session,
) -> None:
    run_date = date(2026, 6, 2)
    _add_raw_data(db_session, symbol="2330.TW", record_date=run_date, is_final=True)
    _add_raw_data(db_session, symbol="2317.TW", record_date=run_date, is_final=True)
    fetcher = FakeBatchFetcher()

    rows = ensure_daily_radar_raw_rows(
        db_session,
        run_date,
        ["2317.TW", "2330.TW"],
        technical_fetcher=fetcher,
    )

    assert fetcher.calls == []
    assert [row.symbol for row in rows] == ["2317.TW", "2330.TW"]


def test_ensure_daily_radar_raw_rows_refreshes_existing_final_institutional_payload_without_refetch(
    db_session: Session,
) -> None:
    run_date = date(2026, 6, 2)
    existing_technical = _technical_payload("2330.TW", run_date) | {"name": "cached technical"}
    existing_row = _add_raw_data(
        db_session,
        symbol="2330.TW",
        record_date=run_date,
        is_final=True,
        technical=existing_technical,
    )
    existing_row.institutional = {"institutional_flow": {"flow_label": "stale"}}
    existing_row.fundamental = {"margin": {"short_balance": 123}}
    db_session.flush()
    fresh_institutional = {
        "flow_label": "institutional_accumulation",
        "same_day_actor": "foreign",
        "same_day_net_buy": 24_680.0,
        "foreign_net_shares": 24_680.0,
        "data_dates": {"institutional_flow": run_date.isoformat()},
    }
    fetcher = FakeBatchFetcher()

    rows = ensure_daily_radar_raw_rows(
        db_session,
        run_date,
        ["2330.TW", "2454.TW"],
        technical_fetcher=fetcher,
        institutional_payloads_by_symbol={"2330.TW": fresh_institutional},
    )

    assert fetcher.calls == [(["2454.TW"], run_date)]
    assert [row.symbol for row in rows] == ["2330.TW", "2454.TW"]
    assert rows[0].id == existing_row.id
    assert rows[0].technical == existing_technical
    assert rows[0].fundamental == {"margin": {"short_balance": 123}}
    assert rows[0].raw_data_is_final is True
    assert rows[0].institutional == fresh_institutional

    stored_row = db_session.get(StockRawData, existing_row.id)
    assert stored_row is not None
    assert stored_row.institutional == fresh_institutional


def test_default_yfinance_batch_fetcher_uses_one_grouped_download_without_ticker_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeYFinance:
        def download(self, symbols: list[str], **kwargs: Any) -> pd.DataFrame:
            calls.append({"symbols": symbols, "kwargs": kwargs})
            dates = pd.bdate_range(end="2026-06-02", periods=60)
            columns = pd.MultiIndex.from_product(
                [["2330.TW", "2454.TW"], ["Open", "High", "Low", "Close", "Volume"]]
            )
            data = []
            for index in range(60):
                row = []
                for _symbol in ["2330.TW", "2454.TW"]:
                    price = 100.0 + index
                    row.extend([price - 1, price + 2, price - 3, price, 1_000_000 + index * 10_000])
                data.append(row)
            return pd.DataFrame(data, index=dates, columns=columns)

        def Ticker(self, symbol: str) -> object:
            raise AssertionError(f"per-symbol yfinance Ticker call is forbidden: {symbol}")

    monkeypatch.setattr("ai_stock_sentinel.daily_radar.raw_data.yf", FakeYFinance())

    payloads = YFinanceBatchTechnicalFetcher().fetch(["2330.TW", "2454.TW"], run_date=date(2026, 6, 2))

    assert calls == [
        {
            "symbols": ["2330.TW", "2454.TW"],
            "kwargs": {
                "group_by": "ticker",
                "start": date(2026, 2, 2),
                "end": date(2026, 6, 3),
                "interval": "1d",
                "threads": True,
                "progress": False,
            },
        }
    ]
    assert payloads["2330.TW"]["ohlcv"]["close"] == 159.0
    assert payloads["2454.TW"]["indicators"]["missing_trading_days_60"] == 0
    assert len(payloads["2330.TW"]["price_history"]) == 60
    assert payloads["2330.TW"]["price_history"][-1] == {"date": "2026-06-02", "close": 159.0}
    assert {"ma5", "ma20", "ma60", "rsi14", "bias20", "volume_ratio"} <= set(
        payloads["2330.TW"]["indicators"]
    )


def test_yfinance_batch_fetcher_ignores_future_rows_after_run_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeYFinance:
        def download(self, symbols: list[str], **kwargs: Any) -> pd.DataFrame:
            columns = pd.MultiIndex.from_product([["2330.TW"], ["Open", "High", "Low", "Close", "Volume"]])
            return pd.DataFrame(
                [
                    [100.0, 103.0, 99.0, 101.0, 1_000_000],
                    [200.0, 204.0, 198.0, 202.0, 2_000_000],
                    [900.0, 999.0, 890.0, 999.0, 9_000_000],
                ],
                index=pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03"]),
                columns=columns,
            )

        def Ticker(self, symbol: str) -> object:
            raise AssertionError(f"per-symbol yfinance Ticker call is forbidden: {symbol}")

    monkeypatch.setattr("ai_stock_sentinel.daily_radar.raw_data.yf", FakeYFinance())

    payloads = YFinanceBatchTechnicalFetcher().fetch(["2330.TW"], run_date=date(2026, 6, 2))

    assert payloads["2330.TW"]["ohlcv"]["close"] == 202.0
    assert payloads["2330.TW"]["ohlcv"]["previous_close"] == 101.0
    assert payloads["2330.TW"]["data_dates"]["ohlcv"] == "2026-06-02"
    assert payloads["2330.TW"]["price_history"] == [
        {"date": "2026-06-01", "close": 101.0},
        {"date": "2026-06-02", "close": 202.0},
    ]


def test_empty_yfinance_symbol_response_does_not_create_or_finalize_raw_data(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    run_date = date(2026, 6, 2)

    class FakeYFinance:
        def download(self, symbols: list[str], **kwargs: Any) -> pd.DataFrame:
            columns = pd.MultiIndex.from_product(
                [["2330.TW", "2454.TW"], ["Open", "High", "Low", "Close", "Volume"]]
            )
            return pd.DataFrame(
                [[100.0, 103.0, 99.0, 101.0, 1_000_000, None, None, None, None, None]],
                index=pd.to_datetime(["2026-06-02"]),
                columns=columns,
            )

        def Ticker(self, symbol: str) -> object:
            raise AssertionError(f"per-symbol yfinance Ticker call is forbidden: {symbol}")

    monkeypatch.setattr("ai_stock_sentinel.daily_radar.raw_data.yf", FakeYFinance())

    rows = ensure_daily_radar_raw_rows(
        db_session,
        run_date,
        ["2330.TW", "2454.TW"],
        technical_fetcher=YFinanceBatchTechnicalFetcher(),
    )

    stored_rows = db_session.query(StockRawData).filter(StockRawData.record_date == run_date).all()
    assert [row.symbol for row in rows] == ["2330.TW"]
    assert [(row.symbol, row.raw_data_is_final) for row in stored_rows] == [("2330.TW", True)]
