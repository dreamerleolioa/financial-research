from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.raw_data import (
    LocalFirstBatchTechnicalFetcher,
    YFinanceBatchTechnicalFetcher,
    ensure_daily_radar_raw_rows,
)
from ai_stock_sentinel.db.models import StockRawData, TaiwanDailyBar
from ai_stock_sentinel.db.session import Base


RUN_DATE = date(2026, 9, 15)


def _frame(end="2026-09-14", periods=80):
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(periods)],
            "High": [102.0 + i for i in range(periods)],
            "Low": [99.0 + i for i in range(periods)],
            "Close": [101.0 + i for i in range(periods)],
            "Volume": [1_000_000 + i * 100 for i in range(periods)],
        },
        index=pd.bdate_range(end=end, periods=periods),
    )


def _repair_fixture(symbol="2330.TW", factor=0.95):
    original = _frame()
    repaired = _frame("2026-09-16", periods=82)
    for field in ("Open", "High", "Low", "Close"):
        repaired[field] /= factor
    repaired["Adj Close"] = repaired["Close"] * factor
    repaired["Dividends"] = 0.0
    repaired["Stock Splits"] = 0.0
    repaired["Repaired?"] = False
    target = pd.Timestamp(RUN_DATE)
    repaired.loc[target, "Repaired?"] = True
    repaired.loc[target, "Volume"] = 500_000
    official = SimpleNamespace(
        symbol=symbol,
        trade_date=RUN_DATE,
        adjustment_mode="unadjusted",
        dataset="taiwan_market_daily_ohlcv",
        market="TWO" if symbol.endswith(".TWO") else "TW",
        source_provider="tpex" if symbol.endswith(".TWO") else "twse",
        source_dataset="official_daily",
        is_final=True,
        open=repaired.loc[target, "Open"],
        high=repaired.loc[target, "High"],
        low=repaired.loc[target, "Low"],
        close=repaired.loc[target, "Close"],
        volume=2_000_000,
    )
    return original, repaired, official


def _fetch(monkeypatch, original, repaired, official, *, symbol="2330.TW", run_date=RUN_DATE):
    calls = []
    official_calls = []

    def download(symbols, **kwargs):
        calls.append((list(symbols), kwargs))
        return pd.concat(
            {symbol: repaired if kwargs.get("repair") else original}, axis=1
        )

    def load_official(symbols, *, run_date):
        official_calls.append((list(symbols), run_date))
        return {symbol: official} if official is not None else {}

    monkeypatch.setattr("ai_stock_sentinel.daily_radar.raw_data.yf.download", download)
    fetcher = YFinanceBatchTechnicalFetcher(
        name_resolver=lambda _: None,
        official_bar_loader=load_official,
    )
    return fetcher.fetch([symbol], run_date=run_date), calls, official_calls


@pytest.mark.parametrize("symbol", ["2330.TW", "3264.TWO"])
def test_repair_verifies_official_prices_keeps_adjustment_and_uses_official_volume(
    monkeypatch, symbol
):
    original, repaired, official = _repair_fixture(symbol)
    result, calls, official_calls = _fetch(
        monkeypatch, original, repaired, official, symbol=symbol
    )
    technical = result[symbol]
    assert technical["data_dates"]["ohlcv"] == RUN_DATE.isoformat()
    assert technical["ohlcv"]["close"] == pytest.approx(181.0)
    assert technical["ohlcv"]["volume"] == official.volume
    assert technical["price_history"][-2]["close"] == original.iloc[-1]["Close"]
    assert technical["price_history"][-1]["date"] == RUN_DATE.isoformat()
    assert len(technical["price_history"]) == 80
    assert [bar["close"] for bar in technical["price_history"][:-1]] == original["Close"].tolist()[-79:]
    provenance = technical["source_provenance"]
    assert provenance["provider"] == official.source_provider
    assert provenance["history_provider"] == "yfinance"
    assert provenance["adjustment_provider"] == "yfinance"
    assert provenance["price_basis"] == "adjusted"
    assert provenance["repair"]["official_price_provider"] == official.source_provider
    assert provenance["repair"]["volume_provider"] == official.source_provider
    assert provenance["repair"]["data_date"] == RUN_DATE.isoformat()
    assert official_calls == [([symbol], RUN_DATE)]
    assert len(calls) == 3
    assert calls[-1][1]["repair"] is True
    assert calls[-1][1]["auto_adjust"] is False
    assert calls[-1][1]["keepna"] is True
    assert "end" not in calls[-1][1]


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_official", "wrong_symbol", "wrong_date", "not_final",
        "wrong_provider", "wrong_market", "wrong_dataset", "adjusted_official",
        "wrong_open", "bad_adjustment", "changed_history", "missing_history",
        "invalid_volume", "fractional_volume", "missing_target", "duplicate_target",
        "intermediate_gap",
    ],
)
def test_unverified_repair_preserves_stale_dates(monkeypatch, invalid):
    original, repaired, official = _repair_fixture()
    target = pd.Timestamp(RUN_DATE)
    if invalid == "missing_official":
        official = None
    elif invalid == "wrong_symbol":
        official.symbol = "3264.TWO"
    elif invalid == "wrong_date":
        official.trade_date = date(2026, 9, 14)
    elif invalid == "not_final":
        official.is_final = False
    elif invalid == "wrong_provider":
        official.source_provider = "unknown"
    elif invalid == "wrong_market":
        official.market = "TWO"
    elif invalid == "wrong_dataset":
        official.dataset = "unverified"
    elif invalid == "adjusted_official":
        official.adjustment_mode = "adjusted"
    elif invalid == "wrong_open":
        official.open += 1
    elif invalid == "bad_adjustment":
        repaired.loc[target, "Adj Close"] = float("nan")
    elif invalid == "changed_history":
        repaired.loc[original.index[-1], "Adj Close"] *= 1.1
    elif invalid == "missing_history":
        repaired = repaired.loc[repaired.index >= target]
    elif invalid == "invalid_volume":
        official.volume = -1
    elif invalid == "fractional_volume":
        official.volume = 1.5
    elif invalid == "missing_target":
        repaired = repaired.drop(target)
    elif invalid == "duplicate_target":
        repaired = pd.concat([repaired, repaired.loc[[target]]])
    elif invalid == "intermediate_gap":
        original = original.iloc[:-1]
    result, calls, _ = _fetch(monkeypatch, original, repaired, official)
    assert result["2330.TW"]["data_dates"]["ohlcv"] == original.index[-1].date().isoformat()
    assert "source_provenance" not in result["2330.TW"]
    assert len(calls) <= 3


def test_healthy_data_does_not_call_repair_or_official_loader(monkeypatch):
    healthy = _frame("2026-09-15")
    result, calls, official_calls = _fetch(monkeypatch, healthy, None, None)
    assert result["2330.TW"]["data_dates"]["ohlcv"] == RUN_DATE.isoformat()
    assert len(calls) == 1
    assert official_calls == []
    assert "source_provenance" not in result["2330.TW"]


def test_repair_preserves_exchange_timezone_and_clips_future_rows(monkeypatch):
    original, repaired, official = _repair_fixture()
    original.index = original.index.tz_localize("Asia/Taipei")
    repaired.index = repaired.index.tz_localize("Asia/Taipei")
    result, _, _ = _fetch(monkeypatch, original, repaired, official)
    assert result["2330.TW"]["price_history"][-1]["date"] == RUN_DATE.isoformat()
    assert result["2330.TW"]["ohlcv"]["close"] == pytest.approx(181.0)


def test_repair_exception_preserves_evidence_and_is_bounded(monkeypatch):
    original, _, official = _repair_fixture()
    calls = []

    def download(symbols, **kwargs):
        calls.append(kwargs)
        if kwargs.get("repair"):
            raise RuntimeError("source unavailable")
        return pd.concat({"2330.TW": original}, axis=1)

    monkeypatch.setattr("ai_stock_sentinel.daily_radar.raw_data.yf.download", download)
    result = YFinanceBatchTechnicalFetcher(
        name_resolver=lambda _: None,
        official_bar_loader=lambda *args, **kwargs: {"2330.TW": official},
    ).fetch(["2330.TW"], run_date=RUN_DATE)
    assert result["2330.TW"]["data_dates"]["ohlcv"] == "2026-09-14"
    assert len(calls) == 3


def test_repair_only_fetches_stale_symbols_and_preserves_healthy_payload(monkeypatch):
    original, repaired, official = _repair_fixture("3264.TWO")
    healthy = _frame("2026-09-15")
    calls = []

    def download(symbols, **kwargs):
        calls.append(list(symbols))
        if len(calls) == 1:
            return pd.concat({"2330.TW": healthy, "3264.TWO": original}, axis=1, sort=True)
        return pd.concat({"3264.TWO": repaired if kwargs.get("repair") else original}, axis=1)

    monkeypatch.setattr("ai_stock_sentinel.daily_radar.raw_data.yf.download", download)
    fetcher = YFinanceBatchTechnicalFetcher(
        name_resolver=lambda _: None,
        official_bar_loader=lambda *args, **kwargs: {"3264.TWO": official},
    )
    result = fetcher.fetch(["2330.TW", "3264.TWO"], run_date=RUN_DATE)
    assert calls == [["2330.TW", "3264.TWO"], ["3264.TWO"], ["3264.TWO"]]
    assert result["2330.TW"]["ohlcv"]["volume"] == healthy.iloc[-1]["Volume"]
    assert "source_provenance" not in result["2330.TW"]
    assert result["3264.TWO"]["data_dates"]["ohlcv"] == RUN_DATE.isoformat()


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.mark.parametrize("valid", [True, False])
def test_production_fetcher_loads_official_archive_and_only_finalizes_verified_repair(monkeypatch, valid):
    original, repaired, official = _repair_fixture()
    if not valid:
        official.open += 1
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[StockRawData.__table__, TaiwanDailyBar.__table__])
    try:
        with Session(engine, autoflush=False) as session:
            session.add(TaiwanDailyBar(**vars(official)))
            session.flush()

            def download(symbols, **kwargs):
                return pd.concat({"2330.TW": repaired if kwargs.get("repair") else original}, axis=1)

            monkeypatch.setattr("ai_stock_sentinel.daily_radar.raw_data.yf.download", download)
            rows = ensure_daily_radar_raw_rows(
                session, RUN_DATE, ["2330.TW"],
                technical_fetcher=LocalFirstBatchTechnicalFetcher(session, name_resolver=lambda _: None),
            )
            stored = session.query(StockRawData).one()
            assert stored.raw_data_is_final is valid
            assert len(rows) == int(valid)
            if valid:
                assert stored.technical["source_provenance"]["repair"]["volume_provider"] == "twse"
                assert stored.technical["ohlcv"]["volume"] == official.volume
                session.commit()
                session.expire_all()
                assert session.query(StockRawData).one().technical["source_provenance"]["repair"]["data_date"] == RUN_DATE.isoformat()
            else:
                assert stored.technical["data_dates"]["ohlcv"] == "2026-09-14"
    finally:
        engine.dispose()


@pytest.mark.parametrize("symbol, difference", [("1101.TW", 0.05), ("1216.TW", 0.30)])
def test_official_final_bar_supplies_close_missing_from_intraday_reconstruction(monkeypatch, symbol, difference):
    original, repaired, official = _repair_fixture(symbol)
    target = pd.Timestamp(RUN_DATE)
    repaired.loc[target, "Close"] -= difference
    repaired.loc[target, "Adj Close"] = repaired.loc[target, "Close"] * 0.95
    result, calls, _ = _fetch(monkeypatch, original, repaired, official, symbol=symbol)
    technical = result[symbol]
    assert technical["data_dates"]["ohlcv"] == RUN_DATE.isoformat()
    assert technical["ohlcv"]["close"] == pytest.approx(official.close * 0.95)
    assert technical["ohlcv"]["close"] != pytest.approx(repaired.loc[target, "Adj Close"])
    assert technical["ohlcv"]["volume"] == official.volume
    assert [bar["close"] for bar in technical["price_history"][:-1]] == original["Close"].tolist()[-79:]
    assert technical["source_provenance"]["repair"]["method"] == "official_final_bar_with_yahoo_adjustment"
    assert len(calls) == 3


def test_official_final_bar_supplies_full_price_range_with_verified_adjustment(monkeypatch):
    original, repaired, official = _repair_fixture()
    official.high += 1.0
    official.close = official.high
    official.low -= 0.1
    result, _, _ = _fetch(monkeypatch, original, repaired, official)
    technical = result["2330.TW"]
    assert technical["data_dates"]["ohlcv"] == RUN_DATE.isoformat()
    for field in ("open", "high", "low", "close"):
        assert technical["ohlcv"][field] == pytest.approx(getattr(official, field) * 0.95)


@pytest.mark.parametrize("run_date", [date(2026, 8, 11), date(2026, 9, 15), date(2026, 10, 12)])
def test_official_fallback_uses_each_requested_run_date(monkeypatch, run_date):
    original, repaired, official = _repair_fixture()
    original.index = pd.bdate_range(end=pd.Timestamp(run_date) - pd.offsets.BDay(1), periods=80)
    repaired.index = pd.bdate_range(end=pd.Timestamp(run_date) + pd.offsets.BDay(1), periods=82)
    official.trade_date = run_date
    target = pd.Timestamp(run_date)
    repaired.loc[target, "Close"] -= 0.1
    repaired.loc[target, "Adj Close"] = repaired.loc[target, "Close"] * 0.95
    result, _, official_calls = _fetch(monkeypatch, original, repaired, official, run_date=run_date)
    assert set(result["2330.TW"]["data_dates"].values()) == {run_date.isoformat()}
    assert result["2330.TW"]["source_provenance"]["repair"]["data_date"] == run_date.isoformat()
    assert official_calls == [(["2330.TW"], run_date)]


def test_same_day_dividend_explains_adjustment_factor_change(monkeypatch):
    original, repaired, official = _repair_fixture()
    target = pd.Timestamp(RUN_DATE)
    previous = original.index[-1]
    repaired.loc[target, "Dividends"] = repaired.loc[previous, "Close"] * (1 - 0.95)
    repaired.loc[target, "Adj Close"] = repaired.loc[target, "Close"]
    result, _, _ = _fetch(monkeypatch, original, repaired, official)
    assert result["2330.TW"]["ohlcv"]["close"] == pytest.approx(official.close)
    assert result["2330.TW"]["source_provenance"]["repair"]["adjustment_factor"] == pytest.approx(1.0)
    assert result["2330.TW"]["price_history"][-2]["close"] == original.loc[previous, "Close"]


def test_small_adjustment_factor_cannot_jump_without_a_dividend(monkeypatch):
    original, repaired, official = _repair_fixture(factor=1e-9)
    target = pd.Timestamp(RUN_DATE)
    repaired.loc[target, "Adj Close"] = repaired.loc[target, "Close"] * 2e-9
    result, _, _ = _fetch(monkeypatch, original, repaired, official)
    assert result["2330.TW"]["data_dates"]["ohlcv"] != RUN_DATE.isoformat()


@pytest.mark.parametrize(
    "invalid",
    ["unexplained_factor", "missing_dividend", "nan_dividend", "negative_dividend",
     "excessive_dividend", "missing_split", "nan_split", "negative_split",
     "invalid_official_range", "nan_official_price", "unsorted_history"],
)
def test_unknown_adjustment_or_invalid_official_prices_remain_non_final(monkeypatch, invalid):
    original, repaired, official = _repair_fixture()
    target = pd.Timestamp(RUN_DATE)
    if invalid == "unexplained_factor":
        repaired.loc[target, "Adj Close"] = repaired.loc[target, "Close"] * 0.5
    elif invalid == "missing_dividend":
        repaired = repaired.drop(columns="Dividends")
    elif invalid == "nan_dividend":
        repaired.loc[target, "Dividends"] = float("nan")
    elif invalid == "negative_dividend":
        repaired.loc[target, "Dividends"] = -1
    elif invalid == "excessive_dividend":
        repaired.loc[target, "Dividends"] = repaired.loc[original.index[-1], "Close"]
    elif invalid == "missing_split":
        repaired = repaired.drop(columns="Stock Splits")
    elif invalid == "nan_split":
        repaired.loc[target, "Stock Splits"] = float("nan")
    elif invalid == "negative_split":
        repaired.loc[target, "Stock Splits"] = -1
    elif invalid == "invalid_official_range":
        official.close = official.high + 1
    elif invalid == "nan_official_price":
        official.high = float("nan")
    elif invalid == "unsorted_history":
        original = original.iloc[::-1]
    result, _, _ = _fetch(monkeypatch, original, repaired, official)
    assert result["2330.TW"]["data_dates"]["ohlcv"] != RUN_DATE.isoformat()
    assert "source_provenance" not in result["2330.TW"]
