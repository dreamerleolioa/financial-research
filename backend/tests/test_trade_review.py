from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.analysis.trade_review import build_trade_review_payload, ensure_trade_review_market_data
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


def test_entry_and_exit_indicators_use_point_in_time_slices(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 3)
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
    assert entry_indicators["ma20"] == pytest.approx(54.5)
    assert entry_indicators["ma60"] == pytest.approx(34.5)
    assert entry_indicators["rsi14"] == pytest.approx(100)
    assert exit_indicators["ma20"] == pytest.approx(149.95)
    assert exit_indicators["exit_vs_ma20_pct"] == pytest.approx(566.888963, rel=1e-4)
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
    assert entry_indicators["ma20"] == pytest.approx(54.5)
    assert exit_indicators["ma20"] == pytest.approx(1000)


def test_ensure_trade_review_market_data_backfills_bounded_ohlcv_rows(db_session: Session):
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

    ensure_trade_review_market_data(db_session, portfolio, fetcher=fake_fetcher)

    assert calls == [("2330.TW", entry_date - timedelta(days=120), exit_date + timedelta(days=1))]
    rows = db_session.query(StockRawData).filter(StockRawData.symbol == "2330.TW").order_by(StockRawData.record_date).all()
    assert rows
    assert rows[-1].record_date == exit_date
    assert all(row.record_date <= exit_date for row in rows)
    assert rows[-1].technical["ohlcv"]["close"] == 75
    assert rows[-1].raw_data_is_final is True


def test_ensure_trade_review_market_data_preserves_existing_final_ohlcv_rows(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 5)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date)
    db_session.add(portfolio)
    existing = _raw_row("2330.TW", entry_date, 123)
    db_session.add(existing)
    db_session.commit()

    def fake_fetcher(_symbol: str, _start: date, _end: date):
        return _history_bars(entry_date - timedelta(days=70), list(range(1, 76)))

    ensure_trade_review_market_data(db_session, portfolio, fetcher=fake_fetcher)

    stored = db_session.query(StockRawData).filter(
        StockRawData.symbol == "2330.TW",
        StockRawData.record_date == entry_date,
    ).one()
    assert stored.technical["ohlcv"]["close"] == 123


def test_ensure_trade_review_market_data_skips_fetch_when_data_sufficient(db_session: Session):
    entry_date = date(2026, 3, 1)
    exit_date = date(2026, 3, 5)
    portfolio = _portfolio(entry_date=entry_date, exit_date=exit_date)
    db_session.add(portfolio)
    _add_rows(db_session, "2330.TW", entry_date - timedelta(days=70), list(range(1, 76)))
    db_session.commit()

    def fake_fetcher(_symbol: str, _start: date, _end: date):
        raise AssertionError("fetcher should not be called when data is sufficient")

    ensure_trade_review_market_data(db_session, portfolio, fetcher=fake_fetcher)


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
    pre_entry = [100 + offset * 0.3 for offset in range(59)]
    closes = pre_entry + [125, 128, 130]
    volumes = [1000] * 59 + [3000, 1200, 1200]
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), closes, volumes)
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
    pre_entry = [90 + offset * 0.2 for offset in range(59)]
    closes = pre_entry + [100, 118, 116, 112, 109, 108]
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), closes)
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
    assert "出場" in exit_review["summary"]
    assert any("出場" in signal or "持有期間" in signal for signal in exit_review["supporting_signals"])
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
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), closes)
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
    pre_entry = [90 + offset * 0.2 for offset in range(59)]
    closes = pre_entry + [100, 118, 116, 112, 109, 108]
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), closes)
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
    assert conclusion["overall_verdict_label"] == "這次出場合理"
    assert any("回吐" in item for item in conclusion["evidence"])


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
    pre_entry = [90 + offset * 0.2 for offset in range(59)]
    holding = [100, 102, 103, 104, 104]
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), pre_entry + holding)
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    conclusion = review_result["user_readable_conclusion"]
    assert set(conclusion) == {"overall_verdict", "overall_verdict_label", "one_sentence_reason", "evidence", "next_time_rules"}
    assert conclusion["overall_verdict"] == "early"
    assert conclusion["overall_verdict_label"] == "這次出場偏早"
    assert "提前小賺離場" in conclusion["one_sentence_reason"]
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
    pre_entry = [100] * 59
    holding = [100, 96, 92, 88, 90]
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), pre_entry + holding)
    db_session.commit()

    review_result, _ = build_trade_review_payload(db_session, portfolio)

    conclusion = review_result["user_readable_conclusion"]
    assert review_result["exit_review"]["classification"] == "late_stop_exit"
    assert conclusion["overall_verdict"] == "late"
    assert conclusion["overall_verdict_label"] == "這次出場偏晚"
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
    pre_entry = [100 + offset * 0.1 for offset in range(59)]
    holding = [105, 110, 108, 112, 106, 104, 102, 98, 101, 96, 94, 99, 93, 97, 92, 96, 91, 95, 90, 95]
    volumes = [1000] * 59 + [1000, 1000, 2500, 1000, 2600, 2700, 2800, 3000, 1000, 3200, 3300, 1000, 3400, 1000, 3500, 1000, 3600, 1000, 3700, 1000]
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), pre_entry + holding, volumes)
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
    pre_entry = [150] * 59
    holding = [100, 106, 104]
    _add_rows(db_session, "2330.TW", date(2026, 1, 1), pre_entry + holding)
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
