from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.analysis import trade_review as trade_review_module
from ai_stock_sentinel.analysis.trade_review import (
    _iter_history_bars,
    _point_in_time_values,
    build_trade_review_payload,
    ensure_trade_review_market_data,
)
from ai_stock_sentinel.analysis.review_sources import (
    attach_source_fingerprint,
    completed_trailing_series,
    market_snapshot_payload,
    market_snapshot_regressed,
)
from ai_stock_sentinel.db.models import StockRawData, UserPortfolio
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.user_models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, compiler, **kw):
    return "JSON"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, UserPortfolio.__table__, StockRawData.__table__])
    with Session(engine) as session:
        yield session


def _portfolio(
    *,
    entry_date: date = date(2026, 3, 1),
    exit_date: date = date(2026, 3, 5),
    entry_price: float = 100,
    exit_price: float = 110,
    realized_return_pct: float = 10,
    holding_days: int = 4,
) -> UserPortfolio:
    return UserPortfolio(
        id=42,
        user_id=1,
        position_group_id="group-review",
        symbol="2330.TW",
        entry_price=entry_price,
        quantity=100,
        entry_date=entry_date,
        is_active=False,
        exit_date=exit_date,
        exit_price=exit_price,
        exit_quantity=100,
        realized_pnl=1000,
        realized_return_pct=realized_return_pct,
        holding_days=holding_days,
    )


def _raw_row(symbol: str, record_date: date, close: float, volume: float = 1000) -> StockRawData:
    return StockRawData(
        symbol=symbol,
        record_date=record_date,
        technical={
            "ohlcv": {
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": volume,
                "avg_volume_20": volume,
            },
            "indicators": {},
            "data_dates": {"ohlcv": record_date.isoformat()},
        },
        raw_data_is_final=True,
    )


def _snapshot_raw_row(
    symbol: str,
    record_date: date,
    closes: list[float],
    volumes: list[float] | None = None,
) -> StockRawData:
    volumes = volumes if volumes is not None else [1000 + offset for offset, _ in enumerate(closes)]
    return StockRawData(
        symbol=symbol,
        record_date=record_date,
        technical={
            "current_price": closes[-1],
            "recent_closes": closes,
            "recent_highs": [close + 1 for close in closes],
            "recent_lows": [close - 1 for close in closes],
            "recent_volumes": volumes,
            "data_dates": {"ohlcv": record_date.isoformat()},
        },
        raw_data_is_final=True,
    )


def _daily_radar_raw_row(
    record_date: date,
    closes: list[float],
    *,
    volume_ratio: float,
) -> StockRawData:
    start_date = record_date - timedelta(days=len(closes) - 1)
    return StockRawData(
        symbol="2330.TW",
        record_date=record_date,
        technical={
            "recent_closes": closes[-20:],
            "recent_close_dates": [
                (start_date + timedelta(days=index)).isoformat()
                for index in range(max(0, len(closes) - 20), len(closes))
            ],
            "price_history": [
                {
                    "date": (start_date + timedelta(days=index)).isoformat(),
                    "close": close,
                }
                for index, close in enumerate(closes)
            ],
            "ohlcv": {
                "open": closes[-1],
                "high": closes[-1] + 1,
                "low": closes[-1] - 1,
                "close": closes[-1],
                "volume": 1_000,
            },
            "indicators": {
                "ma20": sum(closes[-20:]) / 20,
                "ma60": sum(closes[-60:]) / 60,
                "rsi14": 60,
                "volume_ratio": volume_ratio,
            },
            "data_dates": {"ohlcv": record_date.isoformat()},
        },
        raw_data_is_final=True,
    )


def _add_rows(db_session: Session, symbol: str, start: date, closes: list[float], volumes: list[float] | None = None) -> None:
    for offset, close in enumerate(closes):
        volume = volumes[offset] if volumes is not None else 1000 + offset
        db_session.add(_raw_row(symbol, start + timedelta(days=offset), close, volume=volume))


def _history_bars(start: date, closes: list[float]) -> list[dict]:
    return [
        {
            "date": start + timedelta(days=offset),
            "open": close - 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1000 + offset,
        }
        for offset, close in enumerate(closes)
    ]


def test_completed_trailing_series_aligns_ohlc_by_trading_date():
    trading_dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(22)]
    close_by_date = {
        value_date: 100.0 + offset
        for offset, value_date in enumerate(trading_dates)
    }
    high_dates = trading_dates[1:]
    low_dates = trading_dates[:-1]
    technical = {
        "recent_close_dates": [value.isoformat() for value in trading_dates],
        "recent_high_dates": [value.isoformat() for value in high_dates],
        "recent_low_dates": [value.isoformat() for value in low_dates],
        "recent_volume_dates": [value.isoformat() for value in trading_dates],
        "data_dates": {"ohlcv": trading_dates[-1].isoformat()},
    }

    completed = completed_trailing_series(
        technical,
        trading_dates[-1] + timedelta(days=1),
        closes=[close_by_date[value] for value in trading_dates],
        highs=[close_by_date[value] + 1 for value in high_dates],
        lows=[close_by_date[value] - 1 for value in low_dates],
        volumes=[1000.0 + offset for offset in range(len(trading_dates))],
    )

    common_dates = trading_dates[1:-1]
    assert completed is not None
    assert completed["closes"] == [close_by_date[value] for value in trading_dates]
    assert completed["ohlc_closes"] == [close_by_date[value] for value in common_dates]
    assert completed["highs"] == [close_by_date[value] + 1 for value in common_dates]
    assert completed["lows"] == [close_by_date[value] - 1 for value in common_dates]


def test_market_regime_does_not_treat_misaligned_ohlc_as_high_volatility():
    trading_dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(21)]
    closes = [100.0 + offset * 10 for offset in range(len(trading_dates))]
    as_of = trading_dates[-1] + timedelta(days=1)
    row = _snapshot_raw_row("2330.TW", as_of, closes)
    row.technical.update({
        "recent_close_dates": [value.isoformat() for value in trading_dates],
        "recent_highs": [close + 1 for close in closes[1:]],
        "recent_high_dates": [value.isoformat() for value in trading_dates[1:]],
        "recent_lows": [close - 1 for close in closes[:-1]],
        "recent_low_dates": [value.isoformat() for value in trading_dates[:-1]],
        "recent_volume_dates": [value.isoformat() for value in trading_dates],
        "data_dates": {"ohlcv": trading_dates[-1].isoformat()},
    })

    assert trade_review_module._classify_market_regime([row], as_of) == "strong_momentum"


def test_market_regime_requires_twenty_aligned_ohlc_bars_for_volatility() -> None:
    trading_dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(21)]
    closes = [100.0 + offset * 10 for offset in range(len(trading_dates))]
    as_of = trading_dates[-1] + timedelta(days=1)
    row = _snapshot_raw_row("2330.TW", as_of, closes)
    row.technical.update({
        "recent_close_dates": [value.isoformat() for value in trading_dates],
        "recent_highs": [closes[-1] * 1.2],
        "recent_high_dates": [trading_dates[-1].isoformat()],
        "recent_lows": [closes[-1] * 0.8],
        "recent_low_dates": [trading_dates[-1].isoformat()],
        "recent_volume_dates": [value.isoformat() for value in trading_dates],
        "data_dates": {"ohlcv": trading_dates[-1].isoformat()},
    })

    assert trade_review_module._classify_market_regime([row], as_of) == "strong_momentum"


def test_market_snapshot_evidence_binds_independent_ohlc_values_to_dates():
    snapshot = market_snapshot_payload(
        [{
            "record_date": "2026-01-03",
            "raw_data_is_final": True,
            "technical": {
                "recent_closes": [100.0, 101.0, 102.0],
                "recent_close_dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "recent_highs": [102.0, 103.0],
                "recent_high_dates": ["2026-01-02", "2026-01-03"],
                "recent_lows": [99.0, 100.0],
                "recent_low_dates": ["2026-01-01", "2026-01-02"],
                "recent_volumes": [1000.0, 1100.0, 1200.0],
                "recent_volume_dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "data_dates": {"ohlcv": "2026-01-03"},
            },
        }],
        provider="fixture",
    )

    series = snapshot["bars"][0]["trailing_series"]
    assert series == [
        {"close": 100.0, "high": None, "low": 99.0, "volume": 1000.0},
        {"close": 101.0, "high": 102.0, "low": 100.0, "volume": 1100.0},
        {"close": 102.0, "high": 103.0, "low": None, "volume": 1200.0},
    ]


def test_compact_market_snapshot_fingerprint_keeps_complete_overlapping_fields() -> None:
    def snapshot(high: float) -> dict:
        return market_snapshot_payload(
            [
                {
                    "record_date": "2026-01-01",
                    "raw_data_is_final": True,
                    "technical": {
                        "ohlcv": {
                            "open": 98,
                            "high": 999,
                            "low": 1,
                            "close": 999,
                            "volume": 9999,
                        },
                        "recent_closes": [100],
                        "recent_highs": [high],
                        "recent_lows": [99],
                        "recent_volumes": [1000],
                        "recent_close_dates": ["2026-01-01"],
                        "recent_high_dates": ["2026-01-01"],
                        "recent_low_dates": ["2026-01-01"],
                        "recent_volume_dates": ["2026-01-01"],
                        "data_dates": {"ohlcv": "2026-01-01"},
                    },
                },
            ],
            provider="stock_raw_data_read_only",
            compact=True,
        )

    first = snapshot(101)
    changed = snapshot(110)

    assert first["bars"][0]["bar"] == {
        "close": 100,
        "high": 101,
        "low": 99,
        "open": 98,
        "volume": 1000,
    }
    assert changed["bars"][0]["bar"]["high"] == 110
    assert first["bars_fingerprint"] != changed["bars_fingerprint"]


def test_compact_market_snapshot_prefers_later_completed_history_over_earlier_partial_outer() -> None:
    snapshot = market_snapshot_payload(
        [
            {
                "record_date": "2026-01-02",
                "raw_data_is_final": True,
                "technical": {
                    "ohlcv": {
                        "open": 998,
                        "high": 999,
                        "low": 1,
                        "close": 999,
                        "volume": 9999,
                    },
                    "recent_closes": [100],
                    "recent_highs": [101],
                    "recent_lows": [99],
                    "recent_volumes": [1000],
                    "recent_close_dates": ["2026-01-01"],
                    "recent_high_dates": ["2026-01-01"],
                    "recent_low_dates": ["2026-01-01"],
                    "recent_volume_dates": ["2026-01-01"],
                    "data_dates": {"ohlcv": "2026-01-02"},
                },
            },
            {
                "record_date": "2026-01-03",
                "raw_data_is_final": True,
                "technical": {
                    "recent_closes": [100, 102],
                    "recent_highs": [101, 103],
                    "recent_lows": [99, 100],
                    "recent_volumes": [1000, 1200],
                    "recent_close_dates": ["2026-01-01", "2026-01-02"],
                    "recent_high_dates": ["2026-01-01", "2026-01-02"],
                    "recent_low_dates": ["2026-01-01", "2026-01-02"],
                    "recent_volume_dates": ["2026-01-01", "2026-01-02"],
                },
            },
        ],
        provider="stock_raw_data_read_only",
        compact=True,
    )

    bars_by_date = {bar["data_date"]: bar["bar"] for bar in snapshot["bars"]}
    assert bars_by_date["2026-01-02"] == {
        "close": 102,
        "high": 103,
        "low": 100,
        "open": 998,
        "volume": 1200,
    }


def test_compact_market_snapshot_accepts_later_completed_corrections_without_losing_fields() -> None:
    snapshot = market_snapshot_payload(
        [
            {
                "record_date": "2026-01-01",
                "raw_data_is_final": True,
                "technical": {
                    "recent_closes": [100],
                    "recent_highs": [101],
                    "recent_lows": [99],
                    "recent_volumes": [1000],
                    "recent_close_dates": ["2026-01-01"],
                    "recent_high_dates": ["2026-01-01"],
                    "recent_low_dates": ["2026-01-01"],
                    "recent_volume_dates": ["2026-01-01"],
                },
            },
            {
                "record_date": "2026-01-02",
                "raw_data_is_final": True,
                "technical": {
                    "recent_closes": [102],
                    "recent_lows": [100],
                    "recent_volumes": [1200],
                    "recent_close_dates": ["2026-01-01"],
                    "recent_low_dates": ["2026-01-01"],
                    "recent_volume_dates": ["2026-01-01"],
                },
            },
        ],
        provider="stock_raw_data_read_only",
        compact=True,
    )

    assert snapshot["bars"][0]["bar"] == {
        "close": 102,
        "high": 101,
        "low": 100,
        "volume": 1200,
    }


def test_source_fingerprint_ignores_fetch_time_but_changes_with_market_content():
    first = {"market_snapshot": {"fetched_at": "2026-08-04T01:00:00Z", "bars": [{"close": 100.0}]}}
    same_content = {"market_snapshot": {"fetched_at": "2026-08-04T02:00:00Z", "bars": [{"close": 100.0}]}}
    changed_content = {"market_snapshot": {"fetched_at": "2026-08-04T02:00:00Z", "bars": [{"close": 101.0}]}}

    first_fingerprint = attach_source_fingerprint(first, ruleset_version="trade-review-v3")
    same_fingerprint = attach_source_fingerprint(same_content, ruleset_version="trade-review-v3")
    changed_fingerprint = attach_source_fingerprint(changed_content, ruleset_version="trade-review-v3")

    assert same_fingerprint == first_fingerprint
    assert changed_fingerprint != first_fingerprint


def test_history_parser_accepts_single_ticker_yfinance_multiindex_frame():
    columns = pd.MultiIndex.from_product(
        [["Close", "High", "Low", "Open", "Volume"], ["2330.TW"]],
    )
    history = pd.DataFrame(
        [[100.0, 101.0, 99.0, 100.0, 1234.0]],
        index=pd.to_datetime(["2026-03-01"]),
        columns=columns,
    )

    assert _iter_history_bars(history) == [
        {
            "date": date(2026, 3, 1),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1234.0,
        },
    ]


def test_history_parser_sorts_and_deduplicates_provider_bars_by_date():
    history = [
        {"date": date(2026, 3, 2), "open": 102, "high": 103, "low": 101, "close": 102, "volume": 1200},
        {"date": date(2026, 3, 1), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        {"date": date(2026, 3, 2), "open": 104, "high": 105, "low": 103, "close": 104, "volume": 1400},
    ]

    bars = _iter_history_bars(history)

    assert [bar["date"] for bar in bars] == [date(2026, 3, 1), date(2026, 3, 2)]
    assert bars[-1]["close"] == 104


def test_same_day_stale_snapshot_keeps_last_completed_bar():
    row = _snapshot_raw_row("2330.TW", date(2026, 3, 2), [10, 11, 12])
    row.technical["data_dates"] = {"ohlcv": "2026-03-01"}

    values = _point_in_time_values([row], date(2026, 3, 2))

    assert values["closes"] == [10, 11, 12]


def test_same_day_stale_snapshot_uses_independent_high_low_dates():
    row = _snapshot_raw_row("2330.TW", date(2026, 3, 2), [10, 11])
    row.technical["data_dates"] = {"ohlcv": "2026-03-01"}
    row.technical["recent_highs"] = [11, 12, 99]
    row.technical["recent_high_dates"] = ["2026-02-28", "2026-03-01", "2026-03-02"]
    row.technical["recent_lows"] = [9, 10, 1]
    row.technical["recent_low_dates"] = ["2026-02-28", "2026-03-01", "2026-03-02"]

    values = _point_in_time_values([row], date(2026, 3, 2))

    assert values["closes"] == [10, 11]
    assert values["highs"] == [11, 12]
    assert values["lows"] == [9, 10]


def test_same_day_stale_snapshot_trims_undated_high_low_even_when_lengths_match():
    row = _snapshot_raw_row("2330.TW", date(2026, 3, 2), [10, 11])
    row.technical["data_dates"] = {"ohlcv": "2026-03-01"}
    row.technical["recent_highs"] = [12, 99]
    row.technical["recent_lows"] = [9, 1]

    values = _point_in_time_values([row], date(2026, 3, 2))

    assert values["closes"] == [10, 11]
    assert values["highs"] == [12]
    assert values["lows"] == [9]


def test_same_day_legacy_snapshot_without_data_date_drops_only_unproven_final_bar():
    row = _snapshot_raw_row("2330.TW", date(2026, 3, 2), [10, 11, 12], volumes=[100, 200, 300])
    row.technical.pop("data_dates")

    values = _point_in_time_values([row], date(2026, 3, 2))

    assert values["closes"] == [10, 11]
    assert values["highs"] == [11, 12]
    assert values["lows"] == [9, 10]
    assert values["volumes"] == [100, 200]


def test_legacy_snapshot_trims_unproven_high_low_when_series_lengths_differ():
    row = _snapshot_raw_row("2330.TW", date(2026, 3, 2), [10, 11, 12], volumes=[100, 200, 300])
    row.technical.pop("data_dates")
    row.technical["recent_highs"] = [11, 13]
    row.technical["recent_lows"] = [9, 11]

    values = _point_in_time_values([row], date(2026, 3, 2))

    assert values["closes"] == [10, 11]
    assert values["highs"] == [11]
    assert values["lows"] == [9]


def test_same_day_snapshot_does_not_drop_prior_volume_when_current_volume_is_missing():
    row = _snapshot_raw_row("2330.TW", date(2026, 3, 2), [10, 11, 12], volumes=[100, 200])
    row.technical["data_dates"] = {"ohlcv": "2026-03-02"}

    values = _point_in_time_values([row], date(2026, 3, 2))

    assert values["closes"] == [10, 11]
    assert values["volumes"] == [100, 200]


def test_same_day_snapshot_trusts_explicit_prior_volume_dates_over_equal_series_lengths():
    as_of = date(2026, 3, 2)
    row = _snapshot_raw_row("2330.TW", as_of, [10, 11, 12], volumes=[100, 200, 300])
    row.technical["recent_volume_dates"] = ["2026-02-27", "2026-02-28", "2026-03-01"]

    values = _point_in_time_values([row], as_of)

    assert values["closes"] == [10, 11]
    assert values["volumes"] == [100, 200, 300]


def test_path_metrics_compute_max_profit_drawdown_and_giveback(db_session: Session):
    portfolio = _portfolio()
    db_session.add(portfolio)
    _add_rows(db_session, "2330.TW", date(2026, 3, 1), [100, 120, 90, 110, 105])
    db_session.commit()

    review_result, evidence_payload = build_trade_review_payload(db_session, portfolio)

    metrics = review_result["trade_result"]
    assert metrics["entry_date"] == "2026-03-01"
    assert metrics["exit_date"] == "2026-03-05"
    assert metrics["entry_price"] == 100
    assert metrics["exit_price"] == 110
    assert metrics["realized_pnl"] == 1000
    assert metrics["realized_return_pct"] == pytest.approx(10)
    assert metrics["holding_days"] == 4
    assert metrics["highest_close_during_holding"] == 120
    assert metrics["lowest_close_during_holding"] == 90
    assert metrics["max_profit_pct"] == pytest.approx(20)
    assert metrics["max_drawdown_pct"] == pytest.approx(-10)
    assert metrics["profit_giveback_pct"] == pytest.approx(10)
    assert evidence_payload["path_metrics"]["max_profit_pct"] == pytest.approx(20)


def test_trade_review_path_excludes_exit_day_close(db_session: Session):
    portfolio = _portfolio(
        entry_date=date(2026, 3, 1),
        exit_date=date(2026, 3, 3),
        entry_price=100,
        exit_price=105,
    )
    db_session.add(portfolio)
    _add_rows(db_session, "2330.TW", date(2026, 3, 1), [100, 110, 50])
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    metrics = review_result["trade_result"]
    assert metrics["highest_close_during_holding"] == pytest.approx(110)
    assert metrics["lowest_close_during_holding"] == pytest.approx(100)
    assert metrics["max_drawdown_pct"] == pytest.approx(0)


def test_entry_and_exit_indicators_use_point_in_time_slices(db_session: Session):
    entry_date = date(2026, 3, 2)
    exit_date = date(2026, 3, 4)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=60,
        exit_price=1000,
        realized_return_pct=1566.6667,
        holding_days=2,
    )
    db_session.add(portfolio)
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), list(range(1, 61)) + [1000, 1000])
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    entry_indicators = review_result["trade_result"]["entry_indicators"]
    exit_indicators = review_result["trade_result"]["exit_indicators"]
    assert entry_indicators["market_regime"] == "strong_momentum"
    assert exit_indicators["market_regime"] == "strong_momentum"
    assert entry_indicators["ma20"] == pytest.approx(50.5)
    assert entry_indicators["ma60"] == pytest.approx(30.5)
    assert entry_indicators["rsi14"] == pytest.approx(100)
    assert entry_indicators["entry_vs_ma20_pct"] == pytest.approx(18.8119)
    assert entry_indicators["entry_vs_ma60_pct"] == pytest.approx(96.7213)
    assert exit_indicators["ma20"] != pytest.approx(50.5)
    assert exit_indicators["ma20"] == pytest.approx(146.35)
    assert exit_indicators["exit_vs_ma20_pct"] == pytest.approx(583.2935)


def test_snapshot_raw_rows_compute_path_metrics_without_ohlcv(db_session: Session):
    portfolio = _portfolio()
    db_session.add(portfolio)
    for offset, close in enumerate([100, 120, 90, 110, 105]):
        history = [80 + day for day in range(60)] + [close]
        db_session.add(_snapshot_raw_row("2330.TW", date(2026, 3, 1) + timedelta(days=offset), history))
    db_session.commit()

    review_result, evidence_payload = build_trade_review_payload(db_session, portfolio)

    metrics = review_result["trade_result"]
    assert metrics["highest_close_during_holding"] == 120
    assert metrics["lowest_close_during_holding"] == 90
    assert metrics["max_profit_pct"] == pytest.approx(20)
    assert metrics["max_drawdown_pct"] == pytest.approx(-10)
    assert "holding_path_prices" not in evidence_payload["data_quality"]["insufficient_data"]


def test_point_in_time_indicators_use_snapshot_recent_arrays_without_ohlcv(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 3)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=64,
        exit_price=1000,
        realized_return_pct=1462.5,
        holding_days=2,
    )
    db_session.add(portfolio)
    db_session.add(_snapshot_raw_row("2330.TW", entry_date, list(range(1, 65))))
    db_session.add(_snapshot_raw_row("2330.TW", exit_date, list(range(1, 65)) + [1000, 1000]))
    db_session.commit()

    review_result, evidence_payload = build_trade_review_payload(db_session, portfolio)

    entry_indicators = review_result["trade_result"]["entry_indicators"]
    exit_indicators = review_result["trade_result"]["exit_indicators"]
    assert entry_indicators["ma20"] == pytest.approx(53.5)
    assert entry_indicators["ma60"] == pytest.approx(33.5)
    assert entry_indicators["rsi14"] == pytest.approx(100)
    assert exit_indicators["ma20"] == pytest.approx(102.25)
    assert exit_indicators["exit_vs_ma20_pct"] == pytest.approx(877.9951, rel=1e-4)
    assert evidence_payload["data_quality"]["status"] == "ok"


def test_entry_indicators_use_latest_snapshot_at_or_before_entry_date(db_session: Session):
    entry_date = date(2026, 3, 1)
    portfolio = _portfolio(entry_date=entry_date, exit_date=date(2026, 3, 3), entry_price=64, exit_price=1000)
    db_session.add(portfolio)
    db_session.add(_snapshot_raw_row("2330.TW", entry_date, list(range(1, 65))))
    db_session.add(_snapshot_raw_row("2330.TW", date(2026, 3, 2), [1000] * 64))
    db_session.add(_snapshot_raw_row("2330.TW", date(2026, 3, 3), [1000] * 64))
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    entry_indicators = review_result["trade_result"]["entry_indicators"]
    exit_indicators = review_result["trade_result"]["exit_indicators"]
    assert entry_indicators["ma20"] == pytest.approx(53.5)
    assert exit_indicators["ma20"] == pytest.approx(1000)


def test_ensure_trade_review_market_data_builds_isolated_bounded_snapshot(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 5)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date)
    db_session.add(portfolio)
    db_session.commit()
    calls = []

    def fake_fetcher(symbol: str, start: date, end: date):
        calls.append((symbol, start, end))
        return _history_bars(entry_date - timedelta(days=70), list(range(1, 76))) + [
            {"date": exit_date + timedelta(days=1), "open": 999, "high": 999, "low": 999, "close": 999, "volume": 999},
        ]

    snapshot = ensure_trade_review_market_data(db_session, portfolio, fetcher=fake_fetcher)
    rows = snapshot.rows

    assert calls == [("2330.TW", entry_date - timedelta(days=120), exit_date)]
    assert rows
    assert rows[-1].record_date < exit_date
    assert all(row.record_date < exit_date for row in rows)
    assert db_session.query(StockRawData).filter(StockRawData.symbol == "2330.TW").count() == 0


def test_ensure_trade_review_market_data_does_not_mutate_existing_canonical_rows(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 5)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date)
    db_session.add(portfolio)
    existing = _raw_row("2330.TW", entry_date, 123)
    db_session.add(existing)
    db_session.commit()

    def fake_fetcher(_symbol: str, _start: date, _end: date):
        return _history_bars(entry_date - timedelta(days=70), list(range(1, 76)))

    snapshot = ensure_trade_review_market_data(db_session, portfolio, fetcher=fake_fetcher)

    stored = db_session.query(StockRawData).filter(
        StockRawData.symbol == "2330.TW",
        StockRawData.record_date == entry_date,
    ).one()
    assert stored.technical["ohlcv"]["close"] == 123
    assert snapshot.rows


def test_ensure_trade_review_market_data_does_not_reuse_canonical_rows_as_review_snapshot(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 5)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date)
    db_session.add(portfolio)
    _add_rows(db_session, "2330.TW", entry_date - timedelta(days=70), list(range(1, 76)))
    db_session.commit()

    calls = []

    def fake_fetcher(symbol: str, start: date, end: date):
        calls.append((symbol, start, end))
        return _history_bars(entry_date - timedelta(days=70), list(range(1, 76)))

    ensure_trade_review_market_data(db_session, portfolio, fetcher=fake_fetcher)

    assert calls == [("2330.TW", entry_date - timedelta(days=120), exit_date)]


def test_ensure_trade_review_market_data_prefers_rich_fallback_over_partial_provider(db_session: Session):
    entry_date = date(2026, 6, 1)
    exit_date = date(2026, 6, 5)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date)
    fallback = _snapshot_raw_row("2330.TW", entry_date, list(range(1, 81)))
    db_session.add_all([portfolio, fallback])
    db_session.commit()

    snapshot = ensure_trade_review_market_data(
        db_session,
        portfolio,
        fetcher=lambda *_args: _history_bars(entry_date, [999]),
    )

    assert snapshot.rows == [fallback]
    assert snapshot.evidence["provider"] == "stock_raw_data_read_only_fallback"
    assert snapshot.evidence["quality"]["missing_reason"] == "provider_coverage_below_fallback"
    assert snapshot.evidence["quality"]["row_count"] == 1
    assert snapshot.evidence["quality"]["trading_bar_count"] == 80


def test_trade_review_fallback_compacts_overlapping_dated_history(
    db_session: Session,
) -> None:
    entry_date = date(2026, 6, 1)
    exit_date = date(2026, 6, 5)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date)
    closes = list(range(1, 81))
    dates = [
        (entry_date - timedelta(days=78) + timedelta(days=offset)).isoformat()
        for offset in range(len(closes))
    ]
    rows = [
        _snapshot_raw_row("2330.TW", entry_date, closes),
        _snapshot_raw_row("2330.TW", entry_date + timedelta(days=1), closes),
    ]
    for row in rows:
        row.technical = dict(row.technical) | {
            "recent_close_dates": dates,
            "recent_high_dates": dates,
            "recent_low_dates": dates,
            "recent_volume_dates": dates,
        }
    db_session.add_all([portfolio, *rows])
    db_session.commit()

    snapshot = ensure_trade_review_market_data(
        db_session,
        portfolio,
        fetcher=lambda *_args: [],
    )

    assert snapshot.rows == rows
    assert snapshot.evidence["quality"]["row_count"] == 2
    assert snapshot.evidence["quality"]["persisted_bar_count"] == 80
    assert len(snapshot.evidence["bars"]) == 80
    assert all(not bar["trailing_series"] for bar in snapshot.evidence["bars"])


def test_ensure_trade_review_market_data_marks_tiny_provider_response_as_partial_coverage(
    db_session: Session,
):
    entry_date = date(2026, 6, 1)
    portfolio = _portfolio(entry_date=entry_date, exit_date=date(2026, 6, 5))

    snapshot = ensure_trade_review_market_data(
        db_session,
        portfolio,
        fetcher=lambda *_args: _history_bars(entry_date, [999]),
    )

    assert snapshot.evidence["provider"] == "yfinance"
    assert snapshot.evidence["quality"]["status"] == "insufficient"
    assert snapshot.evidence["quality"]["missing_reason"] == "provider_coverage_insufficient"
    assert snapshot.evidence["quality"]["trading_bar_count"] == 1


def test_ensure_trade_review_market_data_uses_trading_bars_for_provider_upgrade(db_session: Session):
    entry_date = date(2026, 6, 1)
    exit_date = date(2026, 6, 5)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date)
    fallback = _snapshot_raw_row("2330.TW", entry_date, list(range(1, 81)))
    db_session.add_all([portfolio, fallback])
    db_session.commit()

    provider_start = entry_date - timedelta(days=71)
    snapshot = ensure_trade_review_market_data(
        db_session,
        portfolio,
        fetcher=lambda *_args: _history_bars(provider_start, list(range(1, 73))),
    )

    assert snapshot.evidence["provider"] == "yfinance"
    assert snapshot.evidence["quality"]["row_count"] == 72
    assert snapshot.evidence["quality"]["trading_bar_count"] == 72


def test_market_snapshot_does_not_estimate_known_out_of_window_series_dates():
    snapshot = market_snapshot_payload(
        [{
            "record_date": "2026-04-30",
            "technical": {
                "recent_closes": list(range(80)),
                "recent_close_dates": [f"2025-01-{day:02d}" for day in range(1, 29)]
                + [f"2025-02-{day:02d}" for day in range(1, 29)]
                + [f"2025-03-{day:02d}" for day in range(1, 25)],
                "recent_highs": list(range(80)),
                "recent_lows": list(range(80)),
                "recent_volumes": list(range(80)),
                "recent_volume_dates": [f"2026-01-{day:02d}" for day in range(1, 29)]
                + [f"2026-02-{day:02d}" for day in range(1, 29)]
                + [f"2026-03-{day:02d}" for day in range(1, 25)],
            },
        }],
        provider="fallback",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 5, 1),
    )

    assert snapshot["quality"]["trading_bar_count"] == 0
    assert snapshot["quality"]["date_start"] is None
    assert snapshot["quality"]["date_end"] is None


def test_market_snapshot_counts_only_usable_final_trading_bars():
    non_final = _raw_row("2330.TW", date(2026, 1, 8), 100)
    non_final.raw_data_is_final = False
    empty = StockRawData(
        symbol="2330.TW",
        record_date=date(2026, 1, 9),
        technical={},
        raw_data_is_final=True,
    )
    weekend_snapshot = market_snapshot_payload(
        [{
            "record_date": "2026-01-10",
            "technical": {
                "recent_closes": [100, 101],
                "recent_close_dates": ["2026-01-08", "2026-01-09"],
                "recent_highs": [101, 102],
                "recent_lows": [99, 100],
                "recent_volumes": [1000, 1100],
                "recent_volume_dates": ["2026-01-08", "2026-01-09"],
            },
        }],
        provider="fallback",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 12),
    )
    invalid_snapshot = market_snapshot_payload(
        [non_final, empty],
        provider="fallback",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 12),
    )

    assert weekend_snapshot["quality"]["trading_bar_count"] == 2
    assert weekend_snapshot["quality"]["date_end"] == "2026-01-09"
    assert weekend_snapshot["quality"]["holding_covered_dates"] == []
    assert invalid_snapshot["quality"]["trading_bar_count"] == 0
    assert invalid_snapshot["quality"]["status"] == "insufficient"


def test_market_snapshot_uses_close_dates_instead_of_equal_length_volume_dates():
    snapshot = market_snapshot_payload(
        [{
            "record_date": "2026-01-10",
            "technical": {
                "recent_closes": [100, 101],
                "recent_close_dates": ["2026-01-08", "2026-01-09"],
                "recent_volumes": [1000, 1100],
                "recent_volume_dates": ["2026-01-07", "2026-01-08"],
            },
        }],
        provider="fallback",
        coverage_start=date(2026, 1, 1),
        coverage_end=date(2026, 1, 12),
    )

    assert snapshot["quality"]["covered_dates"] == ["2026-01-08", "2026-01-09"]


def test_provider_upgrade_cannot_drop_holding_period_dates_at_ninety_percent_total_coverage():
    existing_dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(80)]
    holding_dates = existing_dates[40:48]
    provider_dates = existing_dates[:40] + existing_dates[48:]

    def quality(dates: list[date], *, missing_reason: str | None) -> dict:
        return {
            "quality": {
                "coverage_version": "market-coverage-v1",
                "coverage_basis": "dated_bars",
                "trading_bar_count": len(dates),
                "covered_dates": [value.isoformat() for value in dates],
                "holding_covered_dates": [
                    value.isoformat() for value in dates if value in holding_dates
                ],
                "date_start": existing_dates[0].isoformat(),
                "date_end": existing_dates[-1].isoformat(),
                "missing_reason": missing_reason,
            },
        }

    assert market_snapshot_regressed(
        quality(existing_dates, missing_reason="provider_fetch_failed_or_empty"),
        quality(provider_dates, missing_reason=None),
        provider_upgrade_min_coverage_ratio=0.9,
    ) is True


def test_legacy_dated_bars_can_self_heal_to_material_fallback():
    legacy_provider = {
        "quality": {"missing_reason": None, "row_count": 1},
        "bars": [{
            "record_date": "2026-01-01",
            "data_date": "2026-01-01",
            "raw_data_is_final": True,
            "trailing_dates": [],
            "trailing_series": [],
            "bar": {"close": 100},
        }],
    }
    rich_fallback = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "estimated_trailing_series",
            "trading_bar_count": 80,
            "covered_dates": [],
            "holding_covered_dates": [],
            "date_start": None,
            "date_end": None,
            "missing_reason": "provider_fetch_failed_or_empty",
        },
    }

    assert market_snapshot_regressed(
        legacy_provider,
        rich_fallback,
        provider_upgrade_min_coverage_ratio=0.9,
    ) is False


@pytest.mark.parametrize(("existing_count", "expected_regressed"), [(59, False), (60, True)])
def test_current_coverage_material_fallback_recovery_requires_fewer_than_sixty_bars(
    existing_count: int,
    expected_regressed: bool,
):
    provider_dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(existing_count)]
    current_provider = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "dated_bars",
            "trading_bar_count": existing_count,
            "covered_dates": [value.isoformat() for value in provider_dates],
            "holding_covered_dates": [value.isoformat() for value in provider_dates],
            "date_start": provider_dates[0].isoformat(),
            "date_end": provider_dates[-1].isoformat(),
            "missing_reason": None,
        },
    }
    estimated_fallback = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "estimated_trailing_series",
            "trading_bar_count": 67,
            "covered_dates": [],
            "holding_covered_dates": [],
            "date_start": None,
            "date_end": None,
            "missing_reason": "provider_fetch_failed_or_empty",
        },
    }

    assert market_snapshot_regressed(
        current_provider,
        estimated_fallback,
        provider_upgrade_min_coverage_ratio=0.9,
    ) is expected_regressed


def test_healthy_legacy_dated_bars_do_not_downgrade_to_estimated_fallback():
    legacy_dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(60)]
    legacy_provider = {
        "quality": {"missing_reason": None, "row_count": len(legacy_dates)},
        "bars": [
            {
                "record_date": value.isoformat(),
                "data_date": value.isoformat(),
                "raw_data_is_final": True,
                "trailing_dates": [],
                "trailing_series": [],
                "bar": {"close": 100},
            }
            for value in legacy_dates
        ],
    }
    degraded_fallback = {
        "quality": {
            "coverage_version": "market-coverage-v1",
            "coverage_basis": "estimated_trailing_series",
            "trading_bar_count": 67,
            "covered_dates": [],
            "holding_covered_dates": [],
            "date_start": None,
            "date_end": None,
            "missing_reason": "provider_fetch_failed_or_empty",
        },
    }

    assert market_snapshot_regressed(
        legacy_provider,
        degraded_fallback,
        provider_upgrade_min_coverage_ratio=0.9,
    ) is True


def test_trade_review_fallback_query_excludes_non_final_rows(db_session: Session):
    entry_date = date(2026, 3, 1)
    portfolio = _portfolio(entry_date=entry_date, exit_date=date(2026, 3, 5))
    final_row = _raw_row("2330.TW", entry_date, 100)
    non_final_row = _raw_row("2330.TW", entry_date + timedelta(days=1), 101)
    non_final_row.raw_data_is_final = False
    db_session.add_all([portfolio, final_row, non_final_row])
    db_session.commit()

    snapshot = ensure_trade_review_market_data(db_session, portfolio, fetcher=lambda *_args: [])

    assert snapshot.rows == [final_row]


def test_trade_review_download_uses_single_level_columns_and_bounded_timeout(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_download(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(trade_review_module.yf, "download", fake_download)

    trade_review_module._download_trade_review_history("2330.TW", date(2026, 1, 1), date(2026, 1, 2))

    assert captured["kwargs"]["multi_level_index"] is False
    assert captured["kwargs"]["timeout"] == 10


def test_trade_review_provider_capacity_falls_back_without_calling_provider(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    class NoCapacity:
        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is False
            return False

        def release(self) -> None:
            raise AssertionError("unacquired provider capacity must not be released")

    entry_date = date(2026, 3, 1)
    portfolio = _portfolio(entry_date=entry_date, exit_date=date(2026, 3, 5))
    db_session.add_all([portfolio, _raw_row("2330.TW", entry_date, 123)])
    db_session.commit()
    monkeypatch.setattr(trade_review_module, "_TRADE_REVIEW_PROVIDER_SEMAPHORE", NoCapacity())

    def fail_fetcher(*_args):
        raise AssertionError("capacity exhaustion must skip the provider")

    snapshot = ensure_trade_review_market_data(db_session, portfolio, fetcher=fail_fetcher)

    assert snapshot.evidence["provider"] == "stock_raw_data_read_only_fallback"
    assert snapshot.evidence["quality"]["missing_reason"] == "provider_capacity_exhausted"
    assert snapshot.evidence["fetched_at"] is not None


def test_trade_review_fallback_is_bounded_to_the_review_lookback(db_session: Session):
    entry_date = date(2026, 6, 1)
    portfolio = _portfolio(entry_date=entry_date, exit_date=date(2026, 6, 5))
    old_row = _raw_row("2330.TW", entry_date - timedelta(days=121), 100)
    bounded_row = _raw_row("2330.TW", entry_date - timedelta(days=120), 101)
    db_session.add_all([portfolio, old_row, bounded_row])
    db_session.commit()

    snapshot = ensure_trade_review_market_data(
        db_session,
        portfolio,
        fetcher=lambda *_args: [],
    )

    assert [row.record_date for row in snapshot.rows] == [bounded_row.record_date]


def test_point_in_time_indicators_exclude_same_day_close(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 3)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date, entry_price=60, exit_price=1000)
    db_session.add(portfolio)
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), list(range(1, 61)) + [1000, 2000])
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    entry = review_result["trade_result"]["entry_indicators"]
    exit_ = review_result["trade_result"]["exit_indicators"]
    assert entry["ma20"] == pytest.approx(49.5)
    assert exit_["ma20"] == pytest.approx(98.45)


def test_phase3_entry_review_classifies_breakout_with_market_regime_and_confidence(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 3)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=125,
        exit_price=130,
        realized_return_pct=4,
        holding_days=2,
    )
    db_session.add(portfolio)
    pre_entry = [100 + offset * 0.3 for offset in range(60)]
    closes = pre_entry + [125, 128, 130]
    volumes = [1000] * 60 + [3000, 1200, 1200]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), closes, volumes)
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    entry_review = review_result["entry_review"]
    assert entry_review["classification"] == "breakout_entry"
    assert entry_review["confidence"] in {"high", "medium", "low"}
    assert entry_review["market_regime"] in {"uptrend", "strong_momentum"}
    assert review_result["trade_result"]["entry_indicators"]["market_regime"] == entry_review["market_regime"]
    assert entry_review["supporting_signals"]
    assert "conflicting_signals" in entry_review
    assert "caveats" in entry_review


def test_trade_review_keeps_codes_stable_but_returns_chinese_prose(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 6)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date, entry_price=100, exit_price=108, realized_return_pct=8, holding_days=5)
    db_session.add(portfolio)
    pre_entry = [90 + offset * 0.2 for offset in range(60)]
    closes = pre_entry + [100, 118, 116, 112, 109, 108]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), closes)
    db_session.commit()

    review_result, evidence_payload = build_trade_review_payload(db_session, portfolio)

    entry_review = review_result["entry_review"]
    exit_review = review_result["exit_review"]
    data_quality = review_result["data_quality"]
    assert entry_review["classification"] in {"breakout_entry", "pullback_entry", "chase_entry", "weak_entry", "range_entry"}
    assert entry_review["confidence"] in {"high", "medium", "low"}
    assert entry_review["market_regime"] in {"uptrend", "strong_momentum", "range_bound", "high_volatility", "downtrend"}
    assert "進場" in entry_review["summary"]
    assert any("進場" in signal or "行情" in signal for signal in entry_review["supporting_signals"])
    assert "結案" in exit_review["summary"]
    assert any("結案" in signal or "持有期間" in signal for signal in exit_review["supporting_signals"])
    assert data_quality["status"] in {"ok", "insufficient"}
    assert data_quality == evidence_payload["data_quality"]


def test_entry_review_ignores_post_entry_future_data_for_classification(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 3)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100,
        exit_price=250,
        realized_return_pct=150,
        holding_days=2,
    )
    db_session.add(portfolio)
    closes = [100] * 60 + [100, 250, 250]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), closes)
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    entry_review = review_result["entry_review"]
    assert entry_review["market_regime"] == "range_bound"
    assert entry_review["classification"] == "pullback_entry"
    assert entry_review["classification"] != "chase_entry"


def test_exit_review_classifies_profit_protection_after_giveback(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 6)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100,
        exit_price=108,
        realized_return_pct=8,
        holding_days=5,
    )
    db_session.add(portfolio)
    pre_entry = [90 + offset * 0.2 for offset in range(60)]
    closes = pre_entry + [100, 118, 116, 112, 109, 108]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), closes)
    db_session.commit()

    review_result, evidence_payload = build_trade_review_payload(db_session, portfolio)

    exit_review = review_result["exit_review"]
    assert exit_review["classification"] == "profit_protection_exit"
    assert exit_review["market_regime"] in {"uptrend", "strong_momentum", "range_bound"}
    assert review_result["trade_result"]["exit_indicators"]["market_regime"] == exit_review["market_regime"]
    assert exit_review["supporting_signals"]
    assert evidence_payload["detected_events"] == review_result["holding_review"]["detected_events"]
    conclusion = review_result["user_readable_conclusion"]
    assert conclusion["overall_verdict"] == "reasonable"
    assert conclusion["overall_verdict_label"] == "這次結案節奏合理"
    assert any("回吐" in item for item in conclusion["evidence"])


def test_exit_review_does_not_default_to_reasonable_without_supporting_evidence(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 5)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100,
        exit_price=100,
        realized_return_pct=0,
        holding_days=4,
    )
    db_session.add(portfolio)
    pre_entry = [80 + offset * 0.3 for offset in range(60)]
    holding = [100, 102, 101, 101, 100]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), pre_entry + holding)
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    exit_review = review_result["exit_review"]
    conclusion = review_result["user_readable_conclusion"]
    assert exit_review["classification"] == "unclassified_exit"
    assert not any("均線或支撐轉弱" in item for item in exit_review["supporting_signals"])
    assert conclusion["overall_verdict"] == "unclassified"
    assert conclusion["overall_verdict_label"] == "證據不足以判斷結案節奏"
    assert "不足以判斷" in conclusion["one_sentence_reason"]


def test_exit_review_keeps_reasonable_when_technical_break_has_evidence(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 5)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100,
        exit_price=100,
        realized_return_pct=0,
        holding_days=4,
    )
    db_session.add(portfolio)
    pre_entry = [120.0] * 60
    holding = [110, 108, 105, 102, 100]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), pre_entry + holding)
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    assert review_result["exit_review"]["classification"] == "technical_break_exit"
    assert review_result["exit_review"]["supporting_signals"]
    assert review_result["user_readable_conclusion"]["overall_verdict"] == "reasonable"


def test_user_readable_conclusion_marks_small_profit_above_mas_as_early(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 5)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100,
        exit_price=104,
        realized_return_pct=4,
        holding_days=4,
    )
    db_session.add(portfolio)
    pre_entry = [90 + offset * 0.2 for offset in range(60)]
    holding = [100, 102, 103, 104, 104]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), pre_entry + holding)
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    conclusion = review_result["user_readable_conclusion"]
    assert set(conclusion) == {"overall_verdict", "overall_verdict_label", "one_sentence_reason", "evidence", "next_time_rules"}
    assert conclusion["overall_verdict"] == "early"
    assert conclusion["overall_verdict_label"] == "這次結案節奏偏早"
    assert "提前小幅獲利結案" in conclusion["one_sentence_reason"]
    assert any("高於 MA20" in item and "高於 MA60" in item for item in conclusion["evidence"])
    assert any("保留核心部位" in rule for rule in conclusion["next_time_rules"])


def test_user_readable_conclusion_marks_late_stop_as_late(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 5)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100,
        exit_price=90,
        realized_return_pct=-10,
        holding_days=4,
    )
    db_session.add(portfolio)
    pre_entry = [100] * 60
    holding = [100, 96, 92, 88, 90]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), pre_entry + holding)
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    conclusion = review_result["user_readable_conclusion"]
    assert review_result["exit_review"]["classification"] == "late_stop_exit"
    assert conclusion["overall_verdict"] == "late"
    assert conclusion["overall_verdict_label"] == "這次結案節奏偏晚"
    assert any("最大可承受虧損" in rule for rule in conclusion["next_time_rules"])


def test_evidence_payload_contains_position_group_without_full_ohlcv_arrays(db_session: Session):
    portfolio = _portfolio()
    db_session.add(portfolio)
    _add_rows(db_session, "2330.TW", date(2026, 3, 1), [100, 101, 102, 103, 104])
    db_session.commit()

    review_result, evidence_payload = build_trade_review_payload(db_session, portfolio)

    assert evidence_payload["position_group_id"] == "group-review"
    assert evidence_payload["trade"]["position_group_id"] == "group-review"
    assert evidence_payload["trade"]["return_pct"] == pytest.approx(10)
    assert "detected_events" in evidence_payload
    assert "detected_events" in review_result["holding_review"]
    assert not _contains_forbidden_ohlcv_key(evidence_payload)
    assert not _contains_forbidden_ohlcv_key(review_result)


def test_holding_detected_events_are_capped_and_concise(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 20)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100,
        exit_price=95,
        realized_return_pct=-5,
        holding_days=19,
    )
    db_session.add(portfolio)
    pre_entry = [100 + offset * 0.1 for offset in range(60)]
    holding = [105, 110, 108, 112, 106, 104, 102, 98, 101, 96, 94, 99, 93, 97, 92, 96, 91, 95, 90, 95]
    volumes = [1000] * 60 + [1000, 1000, 2500, 1000, 2600, 2700, 2800, 3000, 1000, 3200, 3300, 1000, 3400, 1000, 3500, 1000, 3600, 1000, 3700, 1000]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), pre_entry + holding, volumes)
    db_session.commit()

    review_result, evidence_payload = build_trade_review_payload(db_session, portfolio)

    events = review_result["holding_review"]["detected_events"]
    assert 0 < len(events) <= 8
    assert events == evidence_payload["detected_events"]
    assert all(set(event) == {"date", "type", "summary", "evidence"} for event in events)
    assert not _contains_forbidden_ohlcv_key(events)


def test_holding_events_ignore_pre_entry_high_when_tracking_running_high(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 3)
    portfolio = _portfolio(
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100,
        exit_price=104,
        realized_return_pct=4,
        holding_days=2,
    )
    db_session.add(portfolio)
    pre_entry = [150] * 60
    holding = [100, 106, 104]
    _add_rows(db_session, "2330.TW", date(2025, 12, 31), pre_entry + holding)
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    event_types = [event["type"] for event in review_result["holding_review"]["detected_events"]]
    assert "new_high_continuation" in event_types
    assert "profit_giveback" not in event_types


def test_insufficient_data_adds_notes_and_insufficient_data(db_session: Session):
    portfolio = _portfolio()
    db_session.add(portfolio)
    db_session.commit()

    review_result, evidence_payload = build_trade_review_payload(db_session, portfolio)

    data_quality = review_result["data_quality"]
    assert data_quality == evidence_payload["data_quality"]
    assert data_quality["status"] == "insufficient"
    assert data_quality["notes"]
    assert "holding_path_prices" in data_quality["insufficient_data"]
    assert "entry_ma20" in data_quality["insufficient_data"]
    assert "exit_rsi14" in data_quality["insufficient_data"]
    assert review_result["entry_review"]["classification"] == "insufficient_data"
    assert review_result["entry_review"]["confidence"] == "low"
    assert review_result["exit_review"]["classification"] == "insufficient_data"
    conclusion = review_result["user_readable_conclusion"]
    assert conclusion["overall_verdict"] == "insufficient"
    assert conclusion["overall_verdict_label"] == "資料不足"
    assert conclusion["evidence"]
    assert any("補資料" in rule for rule in conclusion["next_time_rules"])


def test_trade_review_fallback_uses_daily_radar_price_history_and_indicators(db_session: Session):
    portfolio = _portfolio(
        entry_date=date(2026, 1, 10),
        exit_date=date(2026, 1, 11),
        entry_price=100,
        exit_price=105,
        holding_days=1,
    )
    prior_closes = [80 + index * 0.25 for index in range(80)]
    entry_day_closes = prior_closes[1:] + [100]
    db_session.add_all([
        portfolio,
        _daily_radar_raw_row(date(2026, 1, 9), prior_closes, volume_ratio=1.15),
        _daily_radar_raw_row(date(2026, 1, 10), entry_day_closes, volume_ratio=1.32),
    ])
    db_session.commit()

    review_result, evidence = build_trade_review_payload(db_session, portfolio)

    entry_indicators = review_result["trade_result"]["entry_indicators"]
    exit_indicators = review_result["trade_result"]["exit_indicators"]
    assert entry_indicators["ma20"] is not None
    assert entry_indicators["ma60"] is not None
    assert entry_indicators["rsi14"] is not None
    assert entry_indicators["volume_ratio"] == pytest.approx(1.15)
    assert exit_indicators["volume_ratio"] == pytest.approx(1.32)
    assert not any(
        key.startswith(("entry_", "exit_"))
        for key in review_result["data_quality"]["insufficient_data"]
    )
    assert evidence["market_snapshot"]["quality"]["trading_bar_count"] >= 60


def _contains_forbidden_ohlcv_key(value) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"ohlcv", "kline", "klines", "close_prices", "open_prices", "high_prices", "low_prices", "volumes"}:
                return True
            if _contains_forbidden_ohlcv_key(child):
                return True
    if isinstance(value, list):
        return any(_contains_forbidden_ohlcv_key(child) for child in value)
    return False
