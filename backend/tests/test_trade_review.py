from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.analysis.trade_review import build_trade_review_payload
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


def _add_rows(db_session: Session, symbol: str, start: date, closes: list[float], volumes: list[float] | None = None) -> None:
    for offset, close in enumerate(closes):
        volume = volumes[offset] if volumes is not None else 1000 + offset
        db_session.add(_raw_row(symbol, start + timedelta(days=offset), close, volume=volume))


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
