from __future__ import annotations

from datetime import date, datetime, timedelta
from math import isfinite
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.analysis.position_lifecycle import (
    _build_review_framework,
    _lifecycle_tier,
    _next_operation_rules,
    _point_in_time_values,
    build_position_lifecycle_analysis,
    build_position_lifecycle_analysis_from_rows,
)
from ai_stock_sentinel.analysis.metrics import calc_rsi, ma
from ai_stock_sentinel.calibration.repository import (
    completed_price_rows_from_raw_data,
    load_price_series_from_raw_data,
)
from ai_stock_sentinel.db.models import PositionEvent, PositionLifecyclePlan, SharedBackgroundContext, StockRawData
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
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            PositionEvent.__table__,
            PositionLifecyclePlan.__table__,
            StockRawData.__table__,
            SharedBackgroundContext.__table__,
        ],
    )
    with Session(engine) as session:
        yield session


def _event(
    event_id: int,
    event_type: str,
    event_date: date,
    price: float,
    quantity: int,
    *,
    fees=0,
    taxes=0,
    plan_adherence: str | None = None,
    reason_code: str | None = None,
):
    return SimpleNamespace(
        id=event_id,
        user_id=1,
        position_group_id="group-life",
        symbol="2330.TW",
        event_type=event_type,
        event_date=event_date,
        price=price,
        quantity=quantity,
        fees=fees,
        taxes=taxes,
        reason_category=None,
        reason_code=reason_code,
        plan_adherence=plan_adherence,
        confidence_level=None,
        source="user_recorded_at_event_time",
        data_quality_note=None,
        note="raw user note excluded from evidence",
        created_at=datetime.combine(event_date, datetime.min.time()) + timedelta(minutes=event_id),
    )


def _row(record_date: date, close: float, volume: float = 1000, symbol: str = "2330.TW"):
    return SimpleNamespace(
        symbol=symbol,
        record_date=record_date,
        technical={
            "ohlcv": {
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": volume,
            },
            "data_dates": {"ohlcv": record_date.isoformat()},
        },
    )


def _all_numbers(value):
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [number for item in value.values() for number in _all_numbers(item)]
    if isinstance(value, (list, tuple)):
        return [number for item in value for number in _all_numbers(item)]
    return []


def _snapshot_row(record_date: date, closes: list[float], symbol: str = "2330.TW"):
    return SimpleNamespace(
        symbol=symbol,
        record_date=record_date,
        technical={
            "current_price": closes[-1],
            "recent_closes": closes,
            "recent_highs": [close + 1 for close in closes],
            "recent_lows": [close - 1 for close in closes],
            "recent_volumes": [1000 + index for index, _close in enumerate(closes)],
            "data_dates": {"ohlcv": record_date.isoformat()},
        },
    )


def _daily_radar_row(
    record_date: date,
    closes: list[float],
    *,
    ma20: float,
    ma60: float,
    rsi14: float,
    volume_ratio: float,
    symbol: str = "2330.TW",
):
    start_date = record_date - timedelta(days=len(closes) - 1)
    return SimpleNamespace(
        symbol=symbol,
        record_date=record_date,
        raw_data_is_final=True,
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
                "ma20": ma20,
                "ma60": ma60,
                "rsi14": rsi14,
                "volume_ratio": volume_ratio,
            },
            "data_dates": {"ohlcv": record_date.isoformat()},
        },
    )


def _plan(**overrides):
    values = {
        "position_group_id": "group-life",
        "symbol": "2330.TW",
        "planned_risk_amount": 200,
        "planned_stop_price": 90,
        "planned_holding_period": None,
        "default_stop_rule": None,
        "add_entry_condition": None,
        "source": "user_recorded_at_event_time",
        "created_after_entry": False,
        "thesis": "excluded thesis",
        "planned_invalidation": "excluded invalidation",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _base_events():
    return [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=2, taxes=None, plan_adherence="yes"),
        _event(2, "add_entry", date(2026, 1, 12), 90, 10, fees=1, taxes=0, plan_adherence="partial"),
        _event(3, "manual_adjustment", date(2026, 1, 13), 125, 99, fees=None, taxes=None),
        _event(4, "partial_exit", date(2026, 1, 14), 120, 5, fees=2, taxes=1, plan_adherence="yes"),
        _event(5, "full_exit", date(2026, 1, 16), 80, 15, fees=3, taxes=None, plan_adherence="no"),
    ]


def _base_rows():
    pre_entry = [_row(date(2025, 11, 12) + timedelta(days=index), 100, 1000 + index) for index in range(59)]
    holding = [
        _row(date(2026, 1, 10), 100, 1200),
        _row(date(2026, 1, 11), 105, 1200),
        _row(date(2026, 1, 12), 90, 2200),
        _row(date(2026, 1, 13), 130, 1200),
        _row(date(2026, 1, 14), 120, 1200),
        _row(date(2026, 1, 15), 70, 2600),
        _row(date(2026, 1, 16), 80, 1200),
    ]
    return pre_entry + holding


def test_lifecycle_metrics_weighted_cost_realized_pnl_and_fees_taxes():
    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    metrics = result["lifecycle_metrics"]
    assert metrics["weighted_average_entry_price"] == pytest.approx(95.15)
    assert metrics["total_realized_pnl"] == pytest.approx(-109)
    assert metrics["total_return_pct_on_weighted_cost"] == pytest.approx(-5.7278)
    assert metrics["max_position_size"] == 20
    assert metrics["max_capital_at_risk"] == pytest.approx(1903)
    assert metrics["average_entry_price_over_time"] == [
        {"event_id": 1, "date": "2026-01-10", "position_size": 10, "average_entry_price": 100.2},
        {"event_id": 2, "date": "2026-01-12", "position_size": 20, "average_entry_price": 95.15},
    ]
    assert metrics["final_exit_date"] == "2026-01-16"
    assert metrics["total_holding_days_from_first_entry"] == 6
    assert metrics["active_exposure_days"] == 6
    assert metrics["max_unrealized_profit_pct"] == pytest.approx(36.6264)
    assert metrics["max_unrealized_drawdown_pct"] == pytest.approx(-26.4319)
    assert metrics["profit_giveback_pct"] == pytest.approx(52.5486)
    assert evidence["metrics"]["lifecycle"]["total_realized_pnl"] == pytest.approx(-109)
    assert any("缺少交易稅" in note for note in result["data_quality"]["notes"])


def test_lifecycle_metrics_exclude_full_exit_day_close_from_holding_path():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0),
        _event(2, "full_exit", date(2026, 1, 12), 105, 10, fees=0, taxes=0),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _row(date(2026, 1, 10), 100),
            _row(date(2026, 1, 11), 110),
            _row(date(2026, 1, 12), 50),
        ],
    )

    metrics = result["lifecycle_metrics"]
    assert metrics["max_unrealized_profit_pct"] == pytest.approx(10)
    assert metrics["max_unrealized_drawdown_pct"] == pytest.approx(0)
    assert metrics["profit_giveback_pct"] == pytest.approx(5)


def test_no_manual_tax_requirement_when_event_taxes_are_omitted():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=None),
        _event(2, "full_exit", date(2026, 1, 11), 110, 10, fees=0, taxes=None),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 110)],
    )

    assert result["lifecycle_metrics"]["total_realized_pnl"] == pytest.approx(100)
    assert "missing_ledger_taxes" in result["data_quality"]["insufficient_data"]


def test_entry_and_exit_sequence_metrics():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    entry = result["entry_sequence"]
    assert entry["entry_count"] == 2
    assert entry["add_entry_count"] == 1
    assert entry["initial_entry_vs_ma20_pct"] == pytest.approx(0)
    assert entry["each_add_entry_vs_ma20_pct"] == [pytest.approx(-10.2244)]
    assert entry["average_up_count"] == 0
    assert entry["average_down_count"] == 1
    assert entry["add_after_breakdown_count"] == 1
    assert entry["add_after_confirmation_count"] == 0
    assert entry["time_between_entries"] == [2]
    assert entry["price_distance_between_entries"] == [pytest.approx(-10)]

    exit_sequence = result["exit_sequence"]
    assert exit_sequence["exit_count"] == 2
    assert exit_sequence["partial_exit_count"] == 1
    assert exit_sequence["first_exit_return_pct"] == pytest.approx(26.1167)
    assert exit_sequence["final_exit_return_pct"] == pytest.approx(-15.9222)
    assert exit_sequence["percentage_sold_before_peak"] == pytest.approx(0)
    assert exit_sequence["percentage_sold_after_breakdown"] == pytest.approx(75)
    assert exit_sequence["profit_protected_by_partial_exits"] == pytest.approx(121.25)
    assert exit_sequence["residual_position_giveback_pct"] == pytest.approx(0)


def test_lifecycle_uses_daily_radar_price_history_and_completed_indicator_snapshot():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0),
        _event(2, "full_exit", date(2026, 1, 11), 105, 10, fees=0, taxes=0),
    ]
    prior_closes = [80 + index * 0.25 for index in range(80)]
    entry_day_closes = prior_closes[1:] + [100]
    rows = [
        _daily_radar_row(
            date(2026, 1, 9),
            prior_closes,
            ma20=96.5,
            ma60=91.5,
            rsi14=58.2,
            volume_ratio=1.15,
        ),
        _daily_radar_row(
            date(2026, 1, 10),
            entry_day_closes,
            ma20=97.2,
            ma60=92.1,
            rsi14=61.4,
            volume_ratio=1.32,
        ),
    ]

    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=rows,
        plan=_plan(),
    )

    entry_snapshot, exit_snapshot = result["event_indicator_snapshots"]
    assert entry_snapshot["ma20"] == pytest.approx(ma(prior_closes, 20))
    assert entry_snapshot["ma60"] == pytest.approx(ma(prior_closes, 60))
    assert entry_snapshot["rsi14"] == pytest.approx(calc_rsi(prior_closes, period=14))
    assert entry_snapshot["volume_ratio"] == pytest.approx(1.15)
    assert exit_snapshot["ma20"] == pytest.approx(ma(entry_day_closes, 20))
    assert exit_snapshot["ma60"] == pytest.approx(ma(entry_day_closes, 60))
    assert exit_snapshot["rsi14"] == pytest.approx(calc_rsi(entry_day_closes, period=14))
    assert exit_snapshot["volume_ratio"] == pytest.approx(1.32)
    assert not any(
        key.endswith(("_ma20", "_ma60", "_rsi14", "_volume_ratio"))
        for key in result["data_quality"]["insufficient_data"]
    )
    assert evidence["market_snapshot"]["quality"]["trading_bar_count"] >= 60
    prior_bar = next(
        bar
        for bar in evidence["market_snapshot"]["bars"]
        if bar["record_date"] == "2026-01-09"
    )
    assert prior_bar["bar"]["indicators"] == {
        "ma20": 96.5,
        "ma60": 91.5,
        "rsi14": 58.2,
        "volume_ratio": 1.15,
    }


def test_market_gap_only_marks_affected_dimension_and_preserves_record_quality():
    review = _build_review_framework(
        labels=["insufficient_data"],
        lifecycle_metrics={"total_realized_pnl": -100, "total_return_pct_on_weighted_cost": -1},
        next_operation_rules=[],
        source_refs=["data_quality.insufficient_data"],
        decision_context_insufficient=False,
        evidence_gaps=["full_exit_2026-01-11_ma60"],
    )

    assert review["dimensions"]["entry"]["status"] == "not_observed"
    assert review["dimensions"]["position_management"]["status"] == "not_observed"
    assert review["dimensions"]["risk_exit"]["status"] == "insufficient"
    assert review["dimensions"]["record_quality"]["status"] == "sufficient"
    assert review["dimensions"]["record_quality"]["summary"] == (
        "操作原因、事件與計畫紀錄可用；技術行情缺口只限制受影響的判讀面向。"
    )
    assert [item["title"] for item in review["feedback"]["next_actions"]] == ["系統補齊技術行情"]


def test_entry_sequence_add_entry_count_is_zero_for_initial_entry_only():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[_event(1, "initial_entry", date(2026, 1, 10), 100, 10)],
        market_rows=[_snapshot_row(date(2026, 1, 10), list(range(81, 101)))],
    )

    assert result["entry_sequence"]["entry_count"] == 1
    assert result["entry_sequence"]["add_entry_count"] == 0


def test_entry_sequence_add_entry_count_counts_multiple_explicit_add_entries():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10),
        _event(2, "add_entry", date(2026, 1, 11), 105, 5, plan_adherence="yes", reason_code="planned_scale_in"),
        _event(3, "add_entry", date(2026, 1, 12), 95, 5, plan_adherence="no", reason_code="averaging_down"),
        _event(4, "partial_exit", date(2026, 1, 13), 110, 5),
        _event(5, "manual_adjustment", date(2026, 1, 14), 108, 1),
    ]

    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), list(range(81, 101))),
            _snapshot_row(date(2026, 1, 11), list(range(86, 106))),
            _snapshot_row(date(2026, 1, 12), list(range(76, 96))),
            _snapshot_row(date(2026, 1, 13), list(range(91, 111))),
            _snapshot_row(date(2026, 1, 14), list(range(89, 109))),
        ],
    )

    assert result["entry_sequence"]["entry_count"] == 3
    assert result["entry_sequence"]["add_entry_count"] == 2
    assert evidence["metrics"]["entry_sequence"]["add_entry_count"] == 2
    assert [event["event_type"] for event in evidence["events"]] == [
        "initial_entry",
        "add_entry",
        "add_entry",
        "partial_exit",
        "manual_adjustment",
    ]


def test_advanced_internal_risk_path_and_scores():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    advanced = result["advanced_internal"]
    assert advanced["planned_1r_amount"] == pytest.approx(200)
    assert advanced["realized_r_multiple"] == pytest.approx(-0.545)
    assert advanced["mae_pct"] == pytest.approx(-26.4319)
    assert advanced["mae_r_multiple"] == pytest.approx(-2.515)
    assert advanced["mfe_pct"] == pytest.approx(36.6264)
    assert advanced["mfe_r_multiple"] == pytest.approx(3.485)
    assert advanced["mfe_capture_rate"] == pytest.approx(-15.6384)
    assert advanced["declared_plan_adherence_score"] == pytest.approx(62.5)
    assert advanced["observed_plan_adherence_score"] is None
    assert advanced["plan_adherence_score"] is None
    assert advanced["decision_quality_score"] is None
    assert advanced["capital_at_risk_by_event"][-1]["capital_at_risk"] == pytest.approx(0)
    assert advanced["exposure_curve"][1]["position_size"] == 20
    assert advanced["benchmark_relative_return_pct"] is None
    assert advanced["sector_relative_return_pct"] is None


def test_objective_plan_adherence_uses_observed_facts_not_self_report() -> None:
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, plan_adherence="no"),
        _event(2, "add_entry", date(2026, 1, 11), 105, 5, plan_adherence="no"),
        _event(3, "full_exit", date(2026, 1, 20), 120, 15, plan_adherence="no"),
    ]
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 61),
            _snapshot_row(date(2026, 1, 11), [100] * 60 + [105]),
            _snapshot_row(date(2026, 1, 20), [100] * 60 + [120]),
        ],
        plan=_plan(
            setup_type="pullback",
            planned_holding_period="short_term",
            add_entry_condition="no_averaging_down",
        ),
    )

    advanced = result["advanced_internal"]
    objective = advanced["objective_plan_adherence"]
    assert advanced["declared_plan_adherence_score"] == 0
    assert objective["status"] == "sufficient"
    assert objective["evaluated_check_count"] == 2
    assert objective["passed_check_count"] == 2
    assert objective["score"] == 100
    assert advanced["observed_plan_adherence_score"] == 100
    assert advanced["plan_adherence_score"] == 100


def test_objective_plan_adherence_keeps_single_check_as_limited_evidence() -> None:
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[
            _event(1, "initial_entry", date(2026, 1, 10), 100, 10),
            _event(2, "full_exit", date(2026, 1, 11), 110, 10),
        ],
        market_rows=[],
        plan=_plan(add_entry_condition="no_add_entry"),
    )

    advanced = result["advanced_internal"]
    assert advanced["objective_plan_adherence"]["status"] == "limited_evidence"
    assert advanced["objective_plan_adherence"]["score"] == 100
    assert advanced["observed_plan_adherence_score"] is None


def test_objective_stop_rule_detects_delayed_response_from_completed_bars() -> None:
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10),
        _event(2, "full_exit", date(2026, 1, 15), 85, 10),
    ]
    rows = [
        _row(date(2026, 1, 9), 100),
        _row(date(2026, 1, 10), 100),
        _row(date(2026, 1, 11), 89),
        _row(date(2026, 1, 12), 88),
        _row(date(2026, 1, 13), 87),
        _row(date(2026, 1, 14), 86),
        _row(date(2026, 1, 15), 85),
    ]
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=rows,
        plan=_plan(
            planned_holding_period="short_term",
            default_stop_rule="fixed_price",
            planned_stop_price=90,
        ),
    )

    objective = result["advanced_internal"]["objective_plan_adherence"]
    stop_check = next(
        check for check in objective["checks"] if check["code"] == "default_stop_rule"
    )
    assert stop_check["status"] == "fail"
    assert objective["score"] == 50
    assert result["advanced_internal"]["observed_plan_adherence_score"] == 50


def test_objective_fixed_stop_uses_intraday_low_not_only_close() -> None:
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10),
        _event(2, "full_exit", date(2026, 1, 12), 99, 10),
    ]
    rows = [
        _row(date(2026, 1, 9), 100),
        SimpleNamespace(
            symbol="2330.TW",
            record_date=date(2026, 1, 11),
            technical={
                "ohlcv": {"open": 100, "high": 101, "low": 89, "close": 100, "volume": 1000},
                "data_dates": {"ohlcv": "2026-01-11"},
            },
        ),
    ]
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=rows,
        plan=_plan(default_stop_rule="fixed_price", planned_stop_price=90),
    )

    stop_check = next(
        check
        for check in result["advanced_internal"]["objective_plan_adherence"]["checks"]
        if check["code"] == "default_stop_rule"
    )
    assert stop_check["status"] == "pass"


def test_objective_fixed_stop_uses_embedded_trade_date_not_cache_observation_date() -> None:
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[
            _event(1, "initial_entry", date(2026, 1, 10), 100, 1),
            _event(2, "full_exit", date(2026, 1, 11), 100, 1),
        ],
        market_rows=[SimpleNamespace(
            symbol="2330.TW",
            record_date=date(2026, 1, 10),
            raw_data_is_final=True,
            technical={
                "ohlcv": {"open": 100, "high": 101, "low": 80, "close": 100, "volume": 1000},
                "data_dates": {"ohlcv": "2026-01-09"},
            },
        )],
        plan=_plan(default_stop_rule="fixed_price", planned_stop_price=90),
    )

    stop_check = next(
        check
        for check in result["advanced_internal"]["objective_plan_adherence"]["checks"]
        if check["code"] == "default_stop_rule"
    )
    assert stop_check["status"] == "unobservable"
    assert result["advanced_internal"]["observed_plan_adherence_score"] is None


def test_objective_fixed_stop_does_not_use_ambiguous_entry_day_low() -> None:
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[
            _event(1, "initial_entry", date(2026, 1, 10), 100, 1),
            _event(2, "full_exit", date(2026, 1, 11), 100, 1),
        ],
        market_rows=[SimpleNamespace(
            symbol="2330.TW",
            record_date=date(2026, 1, 10),
            raw_data_is_final=True,
            technical={
                "ohlcv": {"open": 100, "high": 101, "low": 80, "close": 100, "volume": 1000},
                "data_dates": {"ohlcv": "2026-01-10"},
            },
        )],
        plan=_plan(default_stop_rule="fixed_price", planned_stop_price=90),
    )

    stop_check = next(
        check
        for check in result["advanced_internal"]["objective_plan_adherence"]["checks"]
        if check["code"] == "default_stop_rule"
    )
    assert stop_check["status"] == "unobservable"


def test_objective_pullback_requires_ma20_proximity_not_only_price_above_ma20() -> None:
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[
            _event(1, "initial_entry", date(2026, 1, 10), 100, 10),
            _event(2, "add_entry", date(2026, 1, 11), 120, 1),
            _event(3, "full_exit", date(2026, 1, 12), 121, 11),
        ],
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 61),
            _snapshot_row(date(2026, 1, 11), [100] * 61),
            _snapshot_row(date(2026, 1, 12), [100] * 61),
        ],
        plan=_plan(add_entry_condition="pullback_holds_ma20"),
    )

    add_check = next(
        check
        for check in result["advanced_internal"]["objective_plan_adherence"]["checks"]
        if check["code"] == "add_entry_condition"
    )
    assert add_check["status"] == "fail"


@pytest.mark.parametrize(
    ("planned_holding_period", "exit_date", "expected_status"),
    [
        ("short_term", date(2026, 2, 9), "fail"),
        ("long_term", date(2026, 2, 10), "fail"),
        ("long_term", date(2026, 7, 9), "pass"),
    ],
)
def test_objective_holding_period_uses_documented_day_ranges(
    planned_holding_period: str,
    exit_date: date,
    expected_status: str,
) -> None:
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[
            _event(1, "initial_entry", date(2026, 1, 10), 100, 1),
            _event(2, "full_exit", exit_date, 101, 1),
        ],
        plan=_plan(planned_holding_period=planned_holding_period),
    )

    holding_check = next(
        check
        for check in result["advanced_internal"]["objective_plan_adherence"]["checks"]
        if check["code"] == "holding_period"
    )
    assert holding_check["status"] == expected_status


def test_relative_performance_uses_matched_cash_flows_and_prior_completed_bars() -> None:
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10),
        _event(2, "add_entry", date(2026, 1, 11), 105, 5),
        _event(3, "full_exit", date(2026, 1, 20), 120, 15),
    ]
    benchmark_rows = [
        {"date": "2026-01-09", "close": 1000},
        {"date": "2026-01-10", "close": 1010},
        {"date": "2026-01-19", "close": 1100},
        {"date": "2026-01-20", "close": 5000},
    ]
    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _row(date(2026, 1, 9), 99),
            _row(date(2026, 1, 10), 101),
            _row(date(2026, 1, 19), 119),
        ],
        plan=_plan(),
        benchmark_rows=benchmark_rows,
        sector_benchmark_rows=benchmark_rows,
        sector_benchmark_symbol="TWSE-SEMICONDUCTOR",
    )

    actual_return = result["lifecycle_metrics"]["total_return_pct_on_weighted_cost"]
    benchmark_units = 1000 / 1000 + 525 / 1010
    benchmark_return = (benchmark_units * 1100 - 1525) / 1525 * 100
    advanced = result["advanced_internal"]
    assert advanced["benchmark_relative_status"] == "available"
    assert advanced["benchmark_return_pct"] == pytest.approx(benchmark_return, abs=1e-4)
    assert advanced["benchmark_relative_return_pct"] == pytest.approx(
        actual_return - benchmark_return,
        abs=1e-4,
    )
    assert advanced["sector_relative_status"] == "available"
    assert evidence["relative_performance_snapshot"]["methodology"] == (
        "matched_cash_flow_aligned_prior_completed_bar_v1"
    )


def test_relative_performance_applies_entry_and_exit_costs_without_investing_fees() -> None:
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[
            _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=10, taxes=0),
            _event(2, "full_exit", date(2026, 1, 20), 100, 10, fees=10, taxes=0),
        ],
        market_rows=[
            _row(date(2026, 1, 9), 100),
            _row(date(2026, 1, 19), 100),
        ],
        plan=_plan(),
        benchmark_rows=[
            {"date": "2026-01-09", "close": 1000},
            {"date": "2026-01-19", "close": 1000},
        ],
    )

    assert result["advanced_internal"]["benchmark_return_pct"] == pytest.approx(
        -1.9802,
        abs=1e-4,
    )


def test_completed_price_rows_use_embedded_trade_date_not_observation_date() -> None:
    rows = completed_price_rows_from_raw_data([
        SimpleNamespace(
            record_date=date(2026, 1, 8),
            raw_data_is_final=True,
            technical={
                "ohlcv": {"close": 101},
                "data_dates": {"ohlcv": "2026-01-07"},
            },
        ),
        SimpleNamespace(
            record_date=date(2026, 1, 11),
            raw_data_is_final=True,
            technical={"ohlcv": {"close": 999}},
        ),
    ])

    assert rows == [
        {"date": "2026-01-07", "open": None, "high": None, "low": None, "close": 101.0},
    ]


def test_completed_price_rows_keep_real_ohlc_when_close_history_overlaps() -> None:
    rows = completed_price_rows_from_raw_data([SimpleNamespace(
        raw_data_is_final=True,
        technical={
            "price_history": [
                {"date": "2026-01-07", "open": 95, "high": 110, "low": 80, "close": 100},
            ],
            "recent_closes": [100],
            "recent_close_dates": ["2026-01-07"],
        },
    )])

    assert rows == [
        {"date": "2026-01-07", "open": 95.0, "high": 110.0, "low": 80.0, "close": 100.0},
    ]


def test_raw_price_loader_preserves_ohlc_across_later_close_only_observation(
    db_session: Session,
) -> None:
    db_session.add_all([
        StockRawData(
            symbol="TAIEX",
            record_date=date(2026, 1, 7),
            raw_data_is_final=True,
            technical={
                "ohlcv": {"open": 95, "high": 110, "low": 80, "close": 100},
                "data_dates": {"ohlcv": "2026-01-07"},
            },
        ),
        StockRawData(
            symbol="TAIEX",
            record_date=date(2026, 1, 8),
            raw_data_is_final=True,
            technical={
                "price_history": [{"date": "2026-01-07", "close": 101}],
                "ohlcv": {"open": 101, "high": 102, "low": 99, "close": 101},
                "data_dates": {"ohlcv": "2026-01-08"},
            },
        ),
    ])
    db_session.commit()

    rows = load_price_series_from_raw_data(
        db_session,
        symbols=["TAIEX"],
        start_date=date(2026, 1, 7),
        end_date=date(2026, 1, 8),
    )["TAIEX"]

    assert rows[0] == {
        "date": "2026-01-07",
        "open": 95.0,
        "high": 110.0,
        "low": 80.0,
        "close": 101.0,
    }


def test_relative_performance_aligns_to_embedded_stock_trade_date() -> None:
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[
            _event(1, "initial_entry", date(2026, 1, 9), 100, 1),
            _event(2, "full_exit", date(2026, 1, 10), 101, 1),
        ],
        market_rows=[SimpleNamespace(
            record_date=date(2026, 1, 8),
            raw_data_is_final=True,
            technical={
                "ohlcv": {"close": 100},
                "data_dates": {"ohlcv": "2026-01-07"},
            },
        )],
        plan=_plan(),
        benchmark_rows=[{"date": "2026-01-07", "close": 1000}],
    )

    assert result["advanced_internal"]["benchmark_relative_status"] == "available"


@pytest.mark.parametrize("invalid_close", [float("nan"), float("inf"), float("-inf")])
def test_relative_performance_rejects_non_finite_benchmark_prices(
    invalid_close: float,
) -> None:
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
        benchmark_rows=[
            {"date": "2025-12-31", "close": invalid_close},
            {"date": "2026-01-01", "close": invalid_close},
        ],
    )

    advanced = result["advanced_internal"]
    assert advanced["benchmark_relative_status"] == "unavailable_missing_benchmark_series"
    assert advanced["benchmark_return_pct"] is None
    assert advanced["benchmark_relative_return_pct"] is None


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("price", float("inf")),
        ("fees", float("inf")),
        ("taxes", float("-inf")),
    ],
)
def test_relative_performance_rejects_non_finite_event_cash_flows(
    field: str,
    invalid_value: float,
) -> None:
    events = _base_events()
    setattr(events[0], field, invalid_value)
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=_base_rows(),
        plan=_plan(),
        benchmark_rows=[
            {"date": "2025-12-31", "close": 1000},
            {"date": "2026-01-01", "close": 1010},
            {"date": "2026-01-02", "close": 1020},
        ],
    )

    assert result["advanced_internal"]["benchmark_relative_status"] == (
        "unavailable_invalid_event_cash_flow"
    )
    assert all(isfinite(number) for number in _all_numbers(result))


def test_plan_risk_derives_from_stop_when_amount_missing():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(planned_risk_amount=None, planned_stop_price=90),
    )

    assert result["advanced_internal"]["planned_1r_amount"] == pytest.approx(103)


def test_point_in_time_indicators_do_not_use_future_market_data():
    events = [_event(1, "initial_entry", date(2026, 3, 1), 64, 10)]
    rows = [
        _snapshot_row(date(2026, 3, 1), list(range(1, 65))),
        _snapshot_row(date(2026, 3, 2), [1000] * 64),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=rows,
    )

    snapshot = result["event_indicator_snapshots"][0]
    assert snapshot["ma20"] == pytest.approx(53.5)
    assert snapshot["ma60"] == pytest.approx(33.5)
    assert snapshot["rsi14"] == pytest.approx(100)
    assert snapshot["event_price_vs_ma20_pct"] == pytest.approx(19.6262)


def test_lifecycle_prefers_longer_prior_price_history_over_short_same_day_history():
    prior_closes = [80 + index * 0.25 for index in range(80)]
    short_same_day_closes = [98, 99, 100]
    rows = [
        _daily_radar_row(
            date(2026, 1, 9),
            prior_closes,
            ma20=96.5,
            ma60=91.5,
            rsi14=58.2,
            volume_ratio=1.15,
        ),
        _daily_radar_row(
            date(2026, 1, 10),
            short_same_day_closes,
            ma20=99,
            ma60=99,
            rsi14=50,
            volume_ratio=1,
        ),
    ]

    values = _point_in_time_values(rows, date(2026, 1, 10))

    assert values["closes"] == prior_closes


def test_lifecycle_does_not_use_indicator_snapshot_without_completed_indicator_date():
    row = _daily_radar_row(
        date(2026, 1, 9),
        [90 + index for index in range(10)],
        ma20=95,
        ma60=90,
        rsi14=60,
        volume_ratio=1.5,
    )
    row.technical["data_dates"]["technical_indicators"] = "2026-01-10"
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[_event(1, "initial_entry", date(2026, 1, 10), 100, 10)],
        market_rows=[row],
    )

    snapshot = result["event_indicator_snapshots"][0]
    assert snapshot["ma20"] is None
    assert snapshot["ma60"] is None
    assert snapshot["rsi14"] is None
    assert snapshot["volume_ratio"] is None
    assert "initial_entry_2026-01-10_volume_ratio" in result["data_quality"]["insufficient_data"]


def test_lifecycle_uses_completed_indicators_from_same_day_stale_cache():
    as_of = date(2026, 1, 10)
    row = _daily_radar_row(
        as_of,
        [98, 99, 100],
        ma20=96.5,
        ma60=91.5,
        rsi14=58.2,
        volume_ratio=1.15,
    )
    row.technical["data_dates"] = {
        "ohlcv": "2026-01-09",
        "technical_indicators": "2026-01-09",
    }

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[_event(1, "initial_entry", as_of, 100, 10)],
        market_rows=[row],
    )

    snapshot = result["event_indicator_snapshots"][0]
    assert snapshot["ma20"] == pytest.approx(96.5)
    assert snapshot["ma60"] == pytest.approx(91.5)
    assert snapshot["rsi14"] == pytest.approx(58.2)
    assert snapshot["volume_ratio"] == pytest.approx(1.15)
    assert not any(
        key.startswith("initial_entry_2026-01-10_")
        for key in result["data_quality"]["insufficient_data"]
    )


def test_lifecycle_same_day_stale_snapshot_keeps_last_completed_bar():
    row = _snapshot_row(date(2026, 3, 2), [10, 11, 12])
    row.technical["data_dates"] = {"ohlcv": "2026-03-01"}

    values = _point_in_time_values([row], date(2026, 3, 2))

    assert values["closes"] == [10, 11, 12]


def test_lifecycle_same_day_legacy_snapshot_without_data_date_keeps_completed_history():
    row = _snapshot_row(date(2026, 3, 2), [10, 11, 12])
    row.technical.pop("data_dates")

    values = _point_in_time_values([row], date(2026, 3, 2))

    assert values["closes"] == [10, 11]
    assert values["volumes"] == [1000, 1001]


def test_lifecycle_same_day_snapshot_keeps_prior_volume_when_current_volume_is_missing():
    row = _snapshot_row(date(2026, 3, 2), [10, 11, 12])
    row.technical["recent_volumes"] = [100, 200]
    row.technical["data_dates"] = {"ohlcv": "2026-03-02"}

    values = _point_in_time_values([row], date(2026, 3, 2))

    assert values["closes"] == [10, 11]
    assert values["volumes"] == [100, 200]


def test_evidence_payload_is_compact_and_excludes_forbidden_raw_context():
    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    assert 0 < len(evidence["detected_events"]) <= 8
    assert evidence["market_regime_snapshots"]
    assert evidence["data_quality"]["notes"]
    assert "benchmark_relative_return_pct" not in evidence["data_quality"]["insufficient_data"]
    assert "sector_relative_return_pct" not in evidence["data_quality"]["insufficient_data"]
    assert not _contains_forbidden_key(evidence)
    assert not _contains_forbidden_key(result)


def test_lifecycle_evidence_payload_contains_copyable_ai_context_fields():
    _, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    assert set(evidence) == {
        "position_group_id",
        "symbol",
        "metrics",
        "events",
        "indicator_snapshots",
        "detected_events",
        "market_regime_snapshots",
        "shared_context",
        "decision_context",
        "plan_snapshot",
        "relative_performance_snapshot",
        "market_snapshot",
        "source_data",
        "data_quality",
    }
    assert evidence["shared_context"]["point_in_time"] is True
    assert evidence["shared_context"]["data_quality"]["blocking"] is False
    assert set(evidence["metrics"]) == {"lifecycle", "entry_sequence", "exit_sequence", "advanced_internal"}
    assert evidence["metrics"]["lifecycle"]["total_realized_pnl"] == pytest.approx(-109)
    assert evidence["metrics"]["entry_sequence"]["average_down_count"] == 1
    assert evidence["metrics"]["exit_sequence"]["percentage_sold_after_breakdown"] == pytest.approx(75)
    assert evidence["metrics"]["advanced_internal"]["mfe_capture_rate"] == pytest.approx(-15.6384)

    first_event = evidence["events"][0]
    assert set(first_event) >= {
        "event_key",
        "event_type",
        "event_date",
        "price",
        "quantity",
        "fees",
        "taxes",
        "plan_adherence",
        "source",
    }
    first_snapshot = evidence["indicator_snapshots"][0]
    assert set(first_snapshot) >= {
        "event_key",
        "event_type",
        "event_date",
        "ma20",
        "ma60",
        "rsi14",
        "volume_ratio",
        "event_price_vs_ma20_pct",
        "event_price_vs_ma60_pct",
        "market_regime",
    }
    assert evidence["detected_events"]
    assert evidence["market_regime_snapshots"]
    assert evidence["source_data"] == {
        "symbol": "2330.TW",
        "event_count": 5,
        "market_row_count": len(_base_rows()),
        "first_market_date": "2025-11-12",
        "last_market_date": "2026-01-16",
        "plan_present": True,
    }


def test_insufficient_market_data_preserves_ledger_metrics_and_marks_context_insufficient():
    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=[],
        plan=None,
    )

    assert result["lifecycle_metrics"]["total_realized_pnl"] == pytest.approx(-109)
    assert result["data_quality"]["status"] == "insufficient"
    assert "holding_path_prices" in result["data_quality"]["insufficient_data"]
    assert result["decision_context"] == {
        "status": "insufficient",
        "has_plan": False,
        "historical_judgment_eligible": False,
        "source": None,
            "created_after_entry": None,
            "setup_type": None,
            "planned_holding_period": None,
        "default_stop_rule": None,
        "add_entry_condition": None,
    }
    assert evidence["metrics"]["advanced_internal"]["planned_1r_amount"] is None
    assert "intent" not in str(evidence).lower()
    assert result["lifecycle_review"]["classification"]["primary_label"] == "insufficient_data"
    assert "insufficient_data" in result["lifecycle_review"]["classification"]["labels"]
    assert "premature_scale_out" not in result["lifecycle_review"]["classification"]["labels"]
    review = result["lifecycle_review"]
    assert review["outcome"]["status"] == "loss"
    assert review["process_quality"]["status"] == "mixed"
    assert review["feedback"]["keep"][0]["label"] == "disciplined_scale_out"
    assert review["feedback"]["improve"] == []
    assert review["feedback"]["next_actions"][0]["title"] == "系統補齊技術行情"


def test_review_framework_with_only_insufficient_evidence_gives_capture_feedback():
    review = _build_review_framework(
        labels=["insufficient_data"],
        lifecycle_metrics={"total_realized_pnl": None, "total_return_pct_on_weighted_cost": None},
        next_operation_rules=[],
        source_refs=["data_quality.insufficient_data"],
        decision_context_insufficient=True,
        evidence_gaps=["event_facts"],
    )

    assert review["outcome"]["status"] == "insufficient"
    assert review["process_quality"]["status"] == "insufficient"
    assert review["feedback"]["keep"] == []
    assert review["feedback"]["improve"] == []
    assert [item["title"] for item in review["feedback"]["next_actions"]] == [
        "先補交易紀錄",
        "先補操作計畫",
    ]


def test_lifecycle_review_classifies_phase_d_patterns_and_template_refs():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    review = result["lifecycle_review"]
    classification = review["classification"]
    assert classification["primary_label"] == "late_scale_out"
    assert set(classification["labels"]) >= {
        "averaging_down_into_weakness",
        "disciplined_scale_out",
        "risk_reduction_exit",
        "late_scale_out",
    }
    assert classification["tier"] == "needs_review"
    assert set(review) >= {
        "overall_conclusion",
        "what_worked",
        "what_needs_review",
        "event_level_evidence",
        "next_operation_rules",
        "data_quality_notes",
    }
    assert _all_text_items_have_source_refs(review)
    assert _all_text_items_contain_chinese(review)
    assert "本次生命週期檢討層級為需檢討" in review["overall_conclusion"]["text"]
    assert any("完整結案前的部分結案" in item["text"] for item in review["what_worked"])
    assert any("弱勢中新增批次" in item["text"] for item in review["what_needs_review"])
    assert any("降低曝險或結案觸發條件" in item["text"] for item in review["next_operation_rules"])
    assert any("資料品質" in item["text"] for item in review["data_quality_notes"])
    assert any("發生初始進場" in item["text"] for item in review["event_level_evidence"])
    assert all("initial_entry" not in item["text"] for item in review["event_level_evidence"])
    assert all("market_regime" not in item["text"] for item in review["event_level_evidence"])
    assert review["outcome"]["status"] == "loss"
    assert review["outcome"]["label"] == "結果虧損"
    assert review["process_quality"]["status"] == "mixed"
    assert review["process_quality"]["label"] == "流程有好有壞"
    assert set(review["dimensions"]) == {"entry", "position_management", "risk_exit", "record_quality"}
    assert review["dimensions"]["entry"]["status"] == "needs_review"
    assert review["dimensions"]["risk_exit"]["status"] == "mixed"
    assert {item["label"] for item in review["feedback"]["keep"]} >= {
        "disciplined_scale_out",
        "risk_reduction_exit",
    }
    assert {item["label"] for item in review["feedback"]["improve"]} >= {
        "averaging_down_into_weakness",
        "late_scale_out",
    }


def test_lifecycle_review_includes_backfilled_plan_provenance_caveat():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(source="user_backfilled", created_after_entry=True),
    )

    review = result["lifecycle_review"]
    assert result["decision_context"] == {
        "status": "retrospective_only",
        "has_plan": True,
        "historical_judgment_eligible": False,
        "source": "user_backfilled",
            "created_after_entry": True,
            "setup_type": None,
            "planned_holding_period": None,
        "default_stop_rule": None,
        "add_entry_condition": None,
    }
    assert any("事後補填" in caveat["text"] for caveat in review["classification"]["caveats"])
    assert any("不視為原始進場" in item["text"] for item in review["data_quality_notes"])
    assert result["advanced_internal"]["plan_adherence_score"] is None
    assert result["advanced_internal"]["decision_quality_score"] is None
    assert review["classification"]["tier"] != "constructive"


def test_lifecycle_review_phase_e_no_averaging_down_plan_flags_lower_add_below_ma20():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0),
        _event(2, "add_entry", date(2026, 1, 11), 95, 5, fees=0, taxes=0),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 20),
            _snapshot_row(date(2026, 1, 11), [100] * 20 + [95]),
        ],
        plan=_plan(add_entry_condition="no_averaging_down"),
    )

    review = result["lifecycle_review"]
    assert result["decision_context"]["add_entry_condition"] == "no_averaging_down"
    assert review["classification"]["tier"] == "needs_review"
    assert "add_entry_plan_violation" in review["classification"]["labels"]
    assert any("新增批次條件記錄為不攤平" in item["text"] for item in review["classification"]["reasons"])
    assert any("event_facts.id:2" in item["source_refs"] for item in review["classification"]["reasons"])
    assert _all_text_items_have_source_refs(review)


def test_lifecycle_review_phase_e_pullback_held_ma20_reason_adds_traceable_positive_support():
    events = [
        _event(
            1,
            "initial_entry",
            date(2026, 1, 10),
            101,
            10,
            fees=0,
            taxes=0,
            reason_code="pullback_held_ma20",
        ),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_snapshot_row(date(2026, 1, 10), [100] * 20 + [101])],
        plan=_plan(),
    )

    review = result["lifecycle_review"]
    assert "ma20_pullback_supported" in review["classification"]["labels"]
    assert any("拉回守住 MA20" in item["text"] for item in review["classification"]["reasons"])
    assert any("拉回守住 MA20" in item["text"] for item in review["what_worked"])
    support_reason = next(item for item in review["classification"]["reasons"] if "拉回守住 MA20" in item["text"])
    assert "event_facts.id:1" in support_reason["source_refs"]
    assert "event_indicator_snapshots.id:1" in support_reason["source_refs"]


def test_lifecycle_review_phase_e_break_ma20_stop_rule_without_acted_context_needs_review():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 1, 11), 101, 10, fees=0, taxes=0),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [105] * 19 + [100]),
            _snapshot_row(date(2026, 1, 11), [105] * 20 + [101]),
        ],
        plan=_plan(default_stop_rule="break_ma20"),
    )

    review = result["lifecycle_review"]
    assert result["decision_context"]["default_stop_rule"] == "break_ma20"
    assert review["classification"]["tier"] == "needs_review"
    assert "unacted_stop_rule_break" in review["classification"]["labels"]
    assert any("預設風險控制規則為跌破 MA20" in item["text"] for item in review["what_needs_review"])


def test_lifecycle_review_phase_e_planned_holding_period_needs_review_without_hard_judgment():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 3, 20), 112, 10, fees=0, taxes=0, plan_adherence="partial"),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 20),
            _snapshot_row(date(2026, 3, 20), [112] * 20),
        ],
        plan=_plan(planned_holding_period="short_term"),
    )

    review = result["lifecycle_review"]
    assert result["decision_context"]["planned_holding_period"] == "short_term"
    assert "holding_period_needs_review" in review["classification"]["labels"]
    assert review["classification"]["tier"] == "needs_review"
    assert any("不是硬性錯誤" in item["text"] for item in review["classification"]["reasons"])
    assert any(
        "decision_context.planned_holding_period" in item["source_refs"]
        for item in review["classification"]["reasons"]
    )


def test_lifecycle_review_phase_e_missing_decision_context_does_not_hard_judge_fixed_option_violations():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0),
        _event(2, "full_exit", date(2026, 1, 11), 101, 10, fees=0, taxes=0),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 20),
            _snapshot_row(date(2026, 1, 11), [105] * 19 + [101]),
        ],
        plan=None,
    )

    classification = result["lifecycle_review"]["classification"]
    assert result["decision_context"] == {
        "status": "insufficient",
        "has_plan": False,
        "historical_judgment_eligible": False,
        "source": None,
            "created_after_entry": None,
            "setup_type": None,
            "planned_holding_period": None,
        "default_stop_rule": None,
        "add_entry_condition": None,
    }
    assert classification["primary_label"] == "insufficient_data"
    assert classification["tier"] == "insufficient_context"
    assert "add_entry_plan_violation" not in classification["labels"]
    assert "unacted_stop_rule_break" not in classification["labels"]


def test_lifecycle_review_phase_e_backfilled_plan_keeps_provenance_caveat_with_fixed_facts():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(
            source="user_backfilled",
            created_after_entry=True,
            planned_holding_period="swing",
            default_stop_rule="break_ma20",
            add_entry_condition="no_averaging_down",
        ),
    )

    assert result["decision_context"] == {
        "status": "retrospective_only",
        "has_plan": True,
        "historical_judgment_eligible": False,
        "source": "user_backfilled",
            "created_after_entry": True,
            "setup_type": None,
            "planned_holding_period": "swing",
        "default_stop_rule": "break_ma20",
        "add_entry_condition": "no_averaging_down",
    }
    assert any("事後補填" in caveat["text"] for caveat in result["lifecycle_review"]["classification"]["caveats"])
    assert "add_entry_plan_violation" not in result["lifecycle_review"]["classification"]["labels"]
    assert "unacted_stop_rule_break" not in result["lifecycle_review"]["classification"]["labels"]
    assert "holding_period_needs_review" not in result["lifecycle_review"]["classification"]["labels"]
    assert not _contains_forbidden_key(result)


def test_lifecycle_review_premature_scale_out_requires_recorded_context():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(
            2,
            "partial_exit",
            date(2026, 1, 11),
            102,
            5,
            fees=0,
            taxes=0,
            plan_adherence="no",
            reason_code="emotional_exit",
        ),
        _event(3, "full_exit", date(2026, 1, 13), 140, 5, fees=0, taxes=0, plan_adherence="yes"),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), list(range(81, 101))),
            _snapshot_row(date(2026, 1, 11), list(range(101, 122))),
            _snapshot_row(date(2026, 1, 12), list(range(121, 142))),
        ],
        plan=_plan(),
    )

    assert result["lifecycle_review"]["classification"]["primary_label"] == "premature_scale_out"
    assert "premature_scale_out" in result["lifecycle_review"]["classification"]["labels"]


def test_lifecycle_review_missing_context_does_not_infer_premature_scale_out():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0),
        _event(2, "partial_exit", date(2026, 1, 11), 102, 5, fees=0, taxes=0),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 102)],
        plan=None,
    )

    labels = result["lifecycle_review"]["classification"]["labels"]
    assert "insufficient_data" in labels
    assert "premature_scale_out" not in labels
    assert any("不會被直接判定為過早" in caveat["text"] for caveat in result["lifecycle_review"]["classification"]["caveats"])


def test_declared_plan_adherence_does_not_create_coherent_classification():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 1, 11), 110, 10, fees=0, taxes=0, plan_adherence="yes"),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 61),
            _snapshot_row(date(2026, 1, 11), [100] * 61 + [110]),
        ],
        plan=_plan(),
    )

    assert result["advanced_internal"]["declared_plan_adherence_score"] == pytest.approx(100)
    assert result["advanced_internal"]["observed_plan_adherence_score"] is None
    assert "coherent_position_management" not in result["lifecycle_review"]["classification"]["labels"]
    assert result["lifecycle_review"]["classification"]["primary_label"] == "unclassified"
    assert result["lifecycle_review"]["classification"]["labels"] == ["unclassified"]
    assert result["data_quality"] == {"status": "ok", "notes": [], "insufficient_data": []}
    assert result["lifecycle_review"]["classification"]["tier"] != "constructive"


def test_real_market_evidence_gaps_remain_insufficient_instead_of_unclassified():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 1, 11), 110, 10, fees=0, taxes=0, plan_adherence="yes"),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 110)],
        plan=_plan(),
    )

    classification = result["lifecycle_review"]["classification"]
    assert result["decision_context"]["status"] == "present"
    assert result["data_quality"]["status"] == "insufficient"
    assert classification["primary_label"] == "insufficient_data"
    assert "insufficient_data" in classification["labels"]
    assert any(
        "事件當下的技術行情覆蓋不足" in caveat["text"]
        for caveat in classification["caveats"]
    )


def test_optional_planned_r_gaps_do_not_override_constructive_scale_out():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "partial_exit", date(2026, 1, 11), 120, 5, fees=0, taxes=0, plan_adherence="yes"),
        _event(3, "full_exit", date(2026, 1, 12), 110, 5, fees=0, taxes=0, plan_adherence="yes"),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 61),
            _snapshot_row(date(2026, 1, 11), [100] * 61 + [120]),
            _snapshot_row(date(2026, 1, 12), [100] * 61 + [120, 110]),
        ],
        plan=_plan(planned_risk_amount=None, planned_stop_price=None),
    )

    classification = result["lifecycle_review"]["classification"]
    assert result["advanced_internal"]["planned_1r_amount"] is None
    assert result["advanced_internal"]["realized_r_multiple"] is None
    assert "planned_1r_amount" not in result["data_quality"]["insufficient_data"]
    assert result["data_quality"]["status"] == "ok"
    assert classification["primary_label"] == "disciplined_scale_out"
    assert classification["tier"] == "constructive"
    assert "insufficient_data" not in classification["labels"]
    next_rule_text = result["lifecycle_review"]["next_operation_rules"][0]["text"]
    assert "已辨識出可追溯的正向部位管理模式" in next_rule_text
    assert "未命中既定模式" not in next_rule_text
    review = result["lifecycle_review"]
    assert review["outcome"]["status"] == "profit"
    assert review["process_quality"]["status"] == "disciplined"
    assert review["process_quality"]["risk_labels"] == []
    assert review["feedback"]["improve"] == []
    assert review["feedback"]["keep"][0]["label"] == "disciplined_scale_out"


@pytest.mark.parametrize(
    ("label", "expected_source_ref"),
    [
        ("ma20_pullback_supported", "event_indicator_snapshots.event_price_vs_ma20_pct"),
        ("disciplined_scale_out", "exit_sequence.profit_protected_by_partial_exits"),
        ("risk_reduction_exit", "exit_sequence.percentage_sold_after_breakdown"),
        ("coherent_position_management", "advanced_internal.plan_adherence_score"),
    ],
)
def test_constructive_operation_rule_fallback_acknowledges_matched_pattern(
    label: str,
    expected_source_ref: str,
):
    rules = _next_operation_rules([label], False)

    assert len(rules) == 1
    assert "已辨識出可追溯的正向部位管理模式" in rules[0]["text"]
    assert "未命中既定模式" not in rules[0]["text"]
    assert expected_source_ref in rules[0]["source_refs"]


@pytest.mark.parametrize(
    "label",
    [
        "ma20_pullback_supported",
        "disciplined_scale_out",
        "risk_reduction_exit",
        "coherent_position_management",
    ],
)
def test_constructive_primary_labels_use_constructive_tier(label: str):
    assert _lifecycle_tier(label, [label]) == "constructive"


def test_operation_rule_fallback_distinguishes_unclassified_and_insufficient_results():
    unclassified = _next_operation_rules([], False)
    insufficient = _next_operation_rules(["insufficient_data"], False)
    insufficient_with_constructive = _next_operation_rules(
        ["disciplined_scale_out", "insufficient_data"],
        False,
    )

    assert "未命中既定模式" in unclassified[0]["text"]
    assert "證據缺口" in insufficient[0]["text"]
    assert "未命中既定模式" not in insufficient[0]["text"]
    assert insufficient[0]["source_refs"] == ["data_quality.insufficient_data"]
    assert "證據缺口" in insufficient_with_constructive[0]["text"]
    assert "正向部位管理模式" not in insufficient_with_constructive[0]["text"]


def test_unclassified_fallback_does_not_claim_unobserved_position_management():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 2256, 5, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 1, 11), 2102, 5, fees=0, taxes=0, plan_adherence="yes"),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="3665.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [2000] * 61 + [2256]),
            _snapshot_row(date(2026, 1, 11), [2000] * 61 + [2256, 2102]),
        ],
        plan=_plan(planned_risk_amount=None, planned_stop_price=None),
    )

    review = result["lifecycle_review"]
    classification = review["classification"]
    assert result["lifecycle_metrics"]["total_realized_pnl"] == pytest.approx(-770)
    assert result["exit_sequence"]["partial_exit_count"] == 0
    assert result["exit_sequence"]["profit_protected_by_partial_exits"] == pytest.approx(0)
    assert classification["primary_label"] == "unclassified"
    assert classification["tier"] == "mixed"
    assert "未命中可辨識的正向或需檢討模式" in classification["reasons"][0]["text"]
    assert "資料足以完成檢討" in review["overall_conclusion"]["text"]
    assert "Phase C" not in str(review)
    assert review["what_needs_review"][0]["text"].startswith("目前固定規則")
    assert "不代表已證明操作正確" in review["what_needs_review"][0]["text"]
    assert "分批保護獲利" not in str(review["next_operation_rules"])
    assert "不額外推定做對或做錯" in review["next_operation_rules"][0]["text"]
    assert review["next_operation_rules"][0]["source_refs"] == [
        "entry_sequence",
        "exit_sequence",
        "decision_context",
    ]


def test_lifecycle_shared_context_caveat_does_not_override_classification():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 1, 11), 110, 10, fees=0, taxes=0, plan_adherence="yes"),
    ]
    base_result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 110)],
        plan=_plan(),
    )
    shared_context = {
        "version": "lifecycle-shared-context-v1",
        "consumer": "lifecycle_review",
        "point_in_time": True,
        "events": [
            {
                "event_key": "id:1",
                "event_type": "initial_entry",
                "event_date": "2026-01-10",
                "shared_context": {
                    "version": "shared-context-read-v1",
                    "symbol": "2330.TW",
                    "consumer": "lifecycle_review",
                    "reference_date": "2026-01-10",
                    "point_in_time": True,
                    "contexts": [
                        {
                            "context_type": "lending",
                            "source": {"domain": "background_context", "provider": "fixture"},
                            "as_of_date": None,
                            "freshness": "missing",
                            "missing_reason": "context_cache_missing",
                            "replay_key": "background_context:2330.TW:lending:missing",
                            "applicable_consumers": ["lifecycle_review"],
                            "payload": {},
                        }
                    ],
                    "caveats": [],
                    "data_quality": {
                        "status": "missing",
                        "freshness_counts": {"fresh": 0, "stale": 0, "missing": 1, "unknown": 0},
                        "missing_reasons": ["context_cache_missing"],
                        "blocking": False,
                        "point_in_time": True,
                    },
                },
            }
        ],
        "data_quality": {
            "status": "missing",
            "freshness_counts": {"fresh": 0, "stale": 0, "missing": 1, "unknown": 0},
            "missing_reasons": ["context_cache_missing"],
            "blocking": False,
            "point_in_time": True,
        },
    }

    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 110)],
        plan=_plan(),
        shared_context=shared_context,
    )

    assert result["lifecycle_review"]["classification"]["primary_label"] == (
        base_result["lifecycle_review"]["classification"]["primary_label"]
    )
    assert result["lifecycle_review"]["classification"]["tier"] == (
        base_result["lifecycle_review"]["classification"]["tier"]
    )
    assert result["shared_context"] == shared_context
    assert evidence["shared_context"] == shared_context
    assert any("背景脈絡" in item["text"] for item in result["lifecycle_review"]["classification"]["caveats"])
    assert all("shared context" not in item["text"] for item in result["lifecycle_review"]["classification"]["caveats"])


def test_db_builder_scopes_user_group_and_performs_no_writes(db_session: Session):
    db_session.add_all([
        User(id=1, google_sub="user-1", email="user1@example.com"),
        User(id=2, google_sub="user-2", email="user2@example.com"),
        PositionEvent(
            id=1,
            user_id=1,
            position_group_id="shared-group",
            symbol="2330.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=100,
            quantity=10,
            fees=0,
            taxes=0,
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 1, 9, 0, 0),
        ),
        PositionEvent(
            id=2,
            user_id=2,
            position_group_id="shared-group",
            symbol="9999.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=999,
            quantity=1,
            fees=0,
            taxes=0,
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 1, 8, 0, 0),
        ),
        PositionLifecyclePlan(
            user_id=1,
            position_group_id="shared-group",
            symbol="2330.TW",
            planned_risk_amount=50,
            source="user_backfilled",
            created_after_entry=False,
        ),
        StockRawData(
            symbol="2330.TW",
            record_date=date(2026, 1, 1),
            technical={"ohlcv": {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}},
            raw_data_is_final=True,
        ),
    ])
    db_session.commit()
    statements: list[str] = []

    @event.listens_for(db_session.bind, "before_cursor_execute")
    def _capture_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().lower())

    result, evidence = build_position_lifecycle_analysis(db_session, user_id=1, position_group_id="shared-group")

    assert result["symbol"] == "2330.TW"
    assert evidence["source_data"]["event_count"] == 1
    assert all(
        statement.startswith(("select", "pragma"))
        for statement in statements
    )
    assert not any(statement.startswith(("insert", "update", "delete")) for statement in statements)
    assert any("position_event" in statement for statement in statements)
    assert any("position_lifecycle_plan" in statement for statement in statements)
    assert any("stock_raw_data" in statement for statement in statements)


def test_db_builder_excludes_non_final_market_rows(db_session: Session):
    db_session.add_all([
        User(id=1, google_sub="user-1", email="user1@example.com"),
        PositionEvent(
            id=1,
            user_id=1,
            position_group_id="final-only-group",
            symbol="2330.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=100,
            quantity=10,
            fees=0,
            taxes=0,
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 1, 9, 0, 0),
        ),
        PositionEvent(
            id=2,
            user_id=1,
            position_group_id="final-only-group",
            symbol="2330.TW",
            event_type="full_exit",
            event_date=date(2026, 1, 3),
            price=110,
            quantity=10,
            fees=0,
            taxes=0,
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 3, 9, 0, 0),
        ),
        StockRawData(
            symbol="2330.TW",
            record_date=date(2025, 1, 1),
            technical={"ohlcv": {"close": 50}},
            raw_data_is_final=True,
        ),
        StockRawData(
            symbol="2330.TW",
            record_date=date(2026, 1, 1),
            technical={"ohlcv": {"close": 100}},
            raw_data_is_final=True,
        ),
        StockRawData(
            symbol="2330.TW",
            record_date=date(2026, 1, 2),
            technical={"ohlcv": {"close": 200}},
            raw_data_is_final=False,
        ),
    ])
    db_session.commit()

    result, evidence = build_position_lifecycle_analysis(
        db_session,
        user_id=1,
        position_group_id="final-only-group",
    )

    assert evidence["source_data"]["market_row_count"] == 1
    assert evidence["market_snapshot"]["quality"]["trading_bar_count"] == 1
    assert evidence["market_snapshot"]["quality"]["row_count"] == 1
    assert result["lifecycle_metrics"]["max_unrealized_profit_pct"] == pytest.approx(0)


def test_lifecycle_evidence_compacts_overlapping_trailing_market_history() -> None:
    rows = [
        SimpleNamespace(
            symbol="2330.TW",
            record_date=date(2026, 1, 3),
            raw_data_is_final=True,
            technical={
                "ohlcv": {
                    "open": 102.5,
                    "high": 999,
                    "low": 1,
                    "close": 999,
                    "volume": 9999,
                },
                "recent_closes": [101, 102, 103],
                "recent_highs": [102, 103, 104],
                "recent_lows": [100, 101, 102],
                "recent_volumes": [1000, 1100, 1200],
                "recent_close_dates": [
                    "2026-01-01",
                    "2026-01-02",
                    "2026-01-03",
                ],
                "recent_high_dates": [
                    "2026-01-01",
                    "2026-01-02",
                    "2026-01-03",
                ],
                "recent_low_dates": [
                    "2026-01-01",
                    "2026-01-02",
                    "2026-01-03",
                ],
                "recent_volume_dates": [
                    "2026-01-01",
                    "2026-01-02",
                    "2026-01-03",
                ],
                "data_dates": {"ohlcv": "2026-01-03"},
            },
        ),
        SimpleNamespace(
            symbol="2330.TW",
            record_date=date(2026, 1, 4),
            raw_data_is_final=True,
            technical={
                "ohlcv": {"close": 104},
                "recent_closes": [102, 103, 104],
                "recent_highs": [105],
                "recent_lows": [103],
                "recent_volumes": [1300],
                "recent_close_dates": [
                    "2026-01-02",
                    "2026-01-03",
                    "2026-01-04",
                ],
                "recent_high_dates": [
                    "2026-01-04",
                ],
                "recent_low_dates": [
                    "2026-01-04",
                ],
                "recent_volume_dates": [
                    "2026-01-04",
                ],
                "data_dates": {"ohlcv": "2026-01-04"},
            },
        ),
    ]
    _, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="compact-group",
        symbol="2330.TW",
        events=[
            _event(1, "initial_entry", date(2026, 1, 3), 103, 1),
            _event(2, "full_exit", date(2026, 1, 4), 104, 1),
        ],
        market_rows=rows,
        plan=_plan(),
    )

    snapshot = evidence["market_snapshot"]
    assert snapshot["quality"]["row_count"] == 2
    assert snapshot["quality"]["persisted_bar_count"] == 4
    assert snapshot["quality"]["covered_dates"] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
    ]
    assert len(snapshot["bars"]) == 4
    assert all(not bar["trailing_dates"] for bar in snapshot["bars"])
    assert all(not bar["trailing_series"] for bar in snapshot["bars"])
    bars_by_date = {bar["data_date"]: bar["bar"] for bar in snapshot["bars"]}
    assert bars_by_date["2026-01-03"] == {
        "close": 103,
        "high": 104,
        "low": 102,
        "open": 102.5,
        "volume": 1200,
    }
    assert bars_by_date["2026-01-04"] == {
        "close": 104,
        "high": 105,
        "low": 103,
        "volume": 1300,
    }


def _contains_forbidden_key(value) -> bool:
    forbidden = {
        "ohlcv",
        "kline",
        "klines",
        "recent_closes",
        "recent_highs",
        "recent_lows",
        "recent_volumes",
        "raw_llm_prompt",
        "thesis",
        "planned_invalidation",
        "intent",
        "inferred_intent",
        "template",
        "template_fields",
        "note",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden:
                return True
            if _contains_forbidden_key(child):
                return True
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _all_text_items_have_source_refs(value) -> bool:
    if isinstance(value, dict):
        if "text" in value and (not isinstance(value.get("source_refs"), list) or not value["source_refs"]):
            return False
        return all(_all_text_items_have_source_refs(child) for child in value.values())
    if isinstance(value, list):
        return all(_all_text_items_have_source_refs(child) for child in value)
    return True


def _all_text_items_contain_chinese(value) -> bool:
    if isinstance(value, dict):
        if "text" in value and not _contains_chinese(value["text"]):
            return False
        return all(_all_text_items_contain_chinese(child) for child in value.values())
    if isinstance(value, list):
        return all(_all_text_items_contain_chinese(child) for child in value)
    return True


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)
