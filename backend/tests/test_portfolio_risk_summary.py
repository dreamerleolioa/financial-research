from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ai_stock_sentinel.portfolio.risk_summary import build_portfolio_risk_summary


def _position(
    *,
    symbol: str = "2330.TW",
    group: str = "group-1",
    entry_price: str = "100",
    quantity: int = 10,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        position_group_id=group,
        entry_price=Decimal(entry_price),
        quantity=quantity,
    )


def _plan(
    *,
    group: str = "group-1",
    stop: str | None = "95",
    setup_type: str | None = "breakout",
    default_stop_rule: str | None = "fixed_price",
) -> SimpleNamespace:
    return SimpleNamespace(
        position_group_id=group,
        planned_stop_price=Decimal(stop) if stop is not None else None,
        setup_type=setup_type,
        default_stop_rule=default_stop_rule,
    )


def _raw(
    symbol: str,
    close: float | None,
    record_date: date = date(2026, 6, 10),
    *,
    low_20d: float | None = None,
    ma20: float | None = None,
    ma60: float | None = None,
    recent_closes: list[float] | None = None,
    recent_lows: list[float] | None = None,
    indicators: dict | None = None,
    price_history: list[dict] | None = None,
    fundamental: dict | None = None,
) -> SimpleNamespace:
    technical = {"close_price": close} if close is not None else {}
    if low_20d is not None:
        technical["low_20d"] = low_20d
    if ma20 is not None:
        technical["ma20"] = ma20
    if ma60 is not None:
        technical["ma60"] = ma60
    if recent_closes is not None:
        technical["recent_closes"] = recent_closes
    if recent_lows is not None:
        technical["recent_lows"] = recent_lows
    if indicators is not None:
        technical["indicators"] = indicators
    if price_history is not None:
        technical["price_history"] = price_history
    return SimpleNamespace(
        symbol=symbol,
        record_date=record_date,
        technical=technical,
        fundamental=fundamental or {},
        raw_data_is_final=True,
    )


def _price_history(dates: list[date], closes: list[float]) -> list[dict]:
    return [
        {"date": value_date.isoformat(), "close": close}
        for value_date, close in zip(dates, closes, strict=True)
    ]


def test_portfolio_risk_summary_calculates_position_risk_and_totals():
    summary = build_portfolio_risk_summary(
        [
            _position(symbol="2330.TW", group="g1", entry_price="100", quantity=10),
            _position(symbol="2317.TW", group="g2", entry_price="50", quantity=20),
        ],
        plans_by_group={
            "g1": _plan(group="g1", stop="95", setup_type="breakout"),
            "g2": _plan(group="g2", stop="45", setup_type="pullback"),
        },
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", 120),
            "2317.TW": _raw("2317.TW", 60),
        },
        as_of_date=date(2026, 6, 12),
    )

    assert summary["portfolio_value"] == 2400
    assert summary["total_unrealized_pnl"] == 400
    assert summary["total_at_risk"] == 550
    assert summary["total_at_risk_pct"] == 22.9167
    assert summary["risk_budget_status"]["status"] == "constrained"

    first = summary["position_risks"][0]
    assert first["symbol"] == "2330.TW"
    assert first["market_value"] == 1200
    assert first["estimated_risk_amount"] == 250
    assert first["estimated_risk_pct_of_portfolio"] == 10.4167
    assert first["defense_reference"] == {"price": 95.0, "source": "planned_stop_price"}


def test_portfolio_risk_summary_excludes_legacy_non_taiwan_position_from_twd_totals():
    summary = build_portfolio_risk_summary(
        [
            _position(symbol="2330.TW", group="g1", entry_price="100", quantity=10),
            _position(symbol="AAPL", group="g2", entry_price="190", quantity=10),
        ],
        plans_by_group={
            "g1": _plan(group="g1", stop="95"),
            "g2": _plan(group="g2", stop="180"),
        },
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", 120),
            "AAPL": _raw("AAPL", 210),
        },
        as_of_date=date(2026, 6, 12),
    )

    assert summary["portfolio_value"] == 1200
    assert summary["total_unrealized_pnl"] == 200
    legacy_position = summary["position_risks"][1]
    assert legacy_position["symbol"] == "AAPL"
    assert legacy_position["current_price"] == 210
    assert legacy_position["market_value"] is None
    assert legacy_position["unrealized_pnl"] is None
    assert legacy_position["data_quality"]["status"] == "insufficient"
    assert "unsupported_market" in {
        caveat["code"] for caveat in legacy_position["data_quality"]["caveats"]
    }
    assert {"code": "unsupported_market", "count": 1} in summary["data_quality"]["caveats"]


def test_portfolio_risk_summary_uses_refreshed_quote_for_all_price_math():
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95")},
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", 120, record_date=date(2026, 6, 1)),
        },
        price_quotes_by_symbol={
            "2330.TW": {
                "status": "refreshed",
                "current_price": 130,
                "source": "yfinance_fast_info",
                "fetched_at": "2026-06-12T10:30:00+08:00",
                "data_date": "2026-06-12",
                "market_session": "intraday",
                "is_final": False,
            },
        },
        as_of_date=date(2026, 6, 12),
    )

    position = summary["position_risks"][0]
    assert position["current_price"] == 130
    assert position["market_value"] == 1300
    assert position["unrealized_pnl"] == 300
    assert position["estimated_risk_amount"] == 350
    assert position["price_context"] == {
        "refresh_status": "refreshed",
        "source": "yfinance_fast_info",
        "as_of": "2026-06-12T10:30:00+08:00",
        "data_date": "2026-06-12",
        "market_session": "intraday",
        "is_final": False,
    }
    assert "stale_price" not in {caveat["code"] for caveat in position["data_quality"]["caveats"]}


def test_portfolio_risk_summary_falls_back_when_quote_refresh_fails():
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95")},
        raw_data_by_symbol={"2330.TW": _raw("2330.TW", 120)},
        price_quotes_by_symbol={
            "2330.TW": {
                "status": "failed",
                "error_code": "TimeoutError",
            },
        },
        as_of_date=date(2026, 6, 12),
    )

    position = summary["position_risks"][0]
    assert position["current_price"] == 120
    assert position["price_context"]["refresh_status"] == "failed"
    assert position["price_context"]["source"] == "stock_raw_data_fallback"
    assert {caveat["code"] for caveat in position["data_quality"]["caveats"]} == {
        "price_refresh_failed",
    }
    assert summary["data_quality"]["caveats"] == [
        {"code": "price_refresh_failed", "count": 1},
    ]


def test_portfolio_risk_summary_does_not_label_invalid_refreshed_quote_as_fresh():
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95")},
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", 100, record_date=date(2020, 1, 1)),
        },
        price_quotes_by_symbol={
            "2330.TW": {
                "status": "refreshed",
                "current_price": "1e308",
                "source": "yfinance_fast_info",
                "fetched_at": "2026-06-12T10:30:00+08:00",
                "data_date": "2026-06-12",
            },
        },
        as_of_date=date(2026, 6, 12),
    )

    position = summary["position_risks"][0]
    assert position["current_price"] == 100
    assert position["price_context"]["refresh_status"] == "failed"
    assert position["price_context"]["source"] == "stock_raw_data_fallback"
    assert {item["code"] for item in position["data_quality"]["caveats"]} == {
        "price_refresh_invalid",
        "stale_price",
    }


def test_portfolio_risk_summary_rejects_non_json_finite_extreme_price():
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95")},
        raw_data_by_symbol={"2330.TW": _raw("2330.TW", "1e308")},
        as_of_date=date(2026, 6, 12),
    )

    position = summary["position_risks"][0]
    assert position["current_price"] is None
    assert position["market_value"] is None
    assert position["risk_state"] == "data_incomplete"
    assert {item["code"] for item in position["data_quality"]["caveats"]} == {
        "missing_price",
    }


def test_industry_status_uses_account_capital_and_reports_missing_price_coverage():
    summary = build_portfolio_risk_summary(
        [
            _position(symbol="2330.TW", group="g1", quantity=10),
            _position(symbol="2317.TW", group="g2", quantity=10),
            _position(symbol="2454.TW", group="g3", quantity=10),
        ],
        plans_by_group={
            "g1": _plan(group="g1", stop="90"),
            "g2": _plan(group="g2", stop="90"),
            "g3": _plan(group="g3", stop="90"),
        },
        raw_data_by_symbol={
            "2330.TW": _raw(
                "2330.TW",
                100,
                fundamental={"industry": "半導體業"},
            ),
            "2317.TW": _raw("2317.TW", None),
            "2454.TW": _raw("2454.TW", 100),
        },
        cash_balance=3000,
        as_of_date=date(2026, 6, 12),
    )

    industry = summary["concentration"]["by_industry"][0]
    coverage = summary["concentration"]["industry_coverage"]
    assert industry["pct_of_invested"] == 50.0
    assert industry["pct_of_capital_base"] == 20.0
    assert industry["status"] == "partial"
    assert coverage["status"] == "partial"
    assert coverage["eligible_position_count"] == 3
    assert coverage["valued_position_count"] == 2
    assert coverage["classified_position_count"] == 1
    assert coverage["unvalued_position_count"] == 1
    assert coverage["unclassified_valued_position_count"] == 1


def test_correlation_uses_pair_aligned_common_close_intervals():
    start = date(2026, 1, 1)
    common_dates = [start + timedelta(days=index * 2) for index in range(21)]
    left_dates = [start + timedelta(days=index) for index in range(41)]
    common_close_by_date = {
        value_date: 100.0 + index
        for index, value_date in enumerate(common_dates)
    }
    left_closes = [
        common_close_by_date.get(value_date, 10000.0 if index % 4 == 1 else 1.0)
        for index, value_date in enumerate(left_dates)
    ]
    right_closes = [common_close_by_date[value_date] for value_date in common_dates]
    summary = build_portfolio_risk_summary(
        [
            _position(symbol="2330.TW", group="g1", quantity=10),
            _position(symbol="2317.TW", group="g2", quantity=10),
        ],
        plans_by_group={
            "g1": _plan(group="g1", stop="90"),
            "g2": _plan(group="g2", stop="90"),
        },
        raw_data_by_symbol={
            "2330.TW": _raw(
                "2330.TW",
                100,
                price_history=_price_history(left_dates, left_closes),
            ),
            "2317.TW": _raw(
                "2317.TW",
                100,
                price_history=_price_history(common_dates, right_closes),
            ),
        },
        as_of_date=date(2026, 6, 12),
    )

    correlation = summary["correlation_risk"]
    assert correlation["status"] == "available"
    assert correlation["pairs"][0]["overlapping_return_count"] == 20
    assert correlation["pairs"][0]["correlation"] == pytest.approx(1.0)


def test_correlation_coverage_includes_supported_holding_without_current_value():
    dates = [date(2026, 1, 1) + timedelta(days=index) for index in range(21)]
    history = _price_history(dates, [100.0 + index for index in range(21)])
    summary = build_portfolio_risk_summary(
        [
            _position(symbol="2330.TW", group="g1", quantity=10),
            _position(symbol="2317.TW", group="g2", quantity=10),
            _position(symbol="2454.TW", group="g3", quantity=10),
        ],
        plans_by_group={
            "g1": _plan(group="g1", stop="90"),
            "g2": _plan(group="g2", stop="90"),
            "g3": _plan(group="g3", stop="90"),
        },
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", 100, price_history=history),
            "2317.TW": _raw("2317.TW", 100, price_history=history),
            "2454.TW": _raw("2454.TW", None),
        },
        as_of_date=date(2026, 6, 12),
    )

    correlation = summary["correlation_risk"]
    assert correlation["status"] == "partial"
    assert correlation["eligible_position_count"] == 3
    assert correlation["valued_position_count"] == 2
    assert correlation["possible_pair_count"] == 3
    assert correlation["eligible_pair_count"] == 1
    assert correlation["pair_coverage_pct"] == pytest.approx(33.3333)


def test_portfolio_risk_summary_exposes_auto_defense_prices_for_plan_editing():
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95", setup_type="breakout")},
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", 120, low_20d=88, ma20=96.5, ma60=91.25),
        },
        as_of_date=date(2026, 6, 12),
    )

    assert summary["position_risks"][0]["auto_defense_prices"] == {
        "break_20d_low": 88.0,
        "break_ma20": 96.5,
        "break_ma60": 91.25,
    }


def test_portfolio_risk_summary_derives_auto_defense_prices_from_stored_history_snapshot():
    closes = [float(value) for value in range(1, 61)]
    lows = [value - 0.5 for value in closes]
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95", setup_type="breakout")},
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", None, recent_closes=closes, recent_lows=lows),
        },
        as_of_date=date(2026, 6, 12),
    )

    assert summary["position_risks"][0]["current_price"] == 60.0
    assert summary["position_risks"][0]["auto_defense_prices"] == {
        "break_20d_low": 40.5,
        "break_ma20": 50.5,
        "break_ma60": 30.5,
    }


def test_portfolio_risk_summary_uses_stored_indicator_ma_before_deriving_from_history():
    closes = [float(value) for value in range(1, 61)]
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95", setup_type="breakout")},
        raw_data_by_symbol={
            "2330.TW": _raw(
                "2330.TW",
                None,
                recent_closes=closes,
                indicators={"ma20": 123.45, "ma60": 111.11, "support_level": 98.76},
            ),
        },
        as_of_date=date(2026, 6, 12),
    )

    assert summary["position_risks"][0]["auto_defense_prices"] == {
        "break_20d_low": 98.76,
        "break_ma20": 123.45,
        "break_ma60": 111.11,
    }


def test_portfolio_risk_summary_projects_weekly_major_holders_without_changing_risk_state():
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95", setup_type="breakout")},
        raw_data_by_symbol={"2330.TW": _raw("2330.TW", 120)},
        weekly_major_holders_by_symbol={
            "2330.TW": {
                "status": "fresh",
                "as_of_date": "2026-06-13",
                "previous_as_of_date": "2026-06-06",
                "thousand_lot_holder_ratio": 38.2,
                "thousand_lot_holder_ratio_delta_pp": 1.52,
                "large_holder_400_lot_plus_ratio": 51.58,
                "large_holder_400_lot_plus_ratio_delta_pp": 0.88,
                "retail_100_lot_or_less_ratio": 38.49,
                "retail_100_lot_or_less_ratio_delta_pp": -1.1,
                "consecutive_thousand_lot_holder_ratio_increase_count": 2,
            }
        },
        as_of_date=date(2026, 6, 12),
    )

    position = summary["position_risks"][0]
    assert position["risk_state"] == "elevated"
    assert position["weekly_major_holders"] == {
        "status": "fresh",
        "as_of_date": "2026-06-13",
        "previous_as_of_date": "2026-06-06",
        "thousand_lot_holder_ratio": 38.2,
        "thousand_lot_holder_ratio_delta_pp": 1.52,
        "large_holder_400_lot_plus_ratio": 51.58,
        "large_holder_400_lot_plus_ratio_delta_pp": 0.88,
        "retail_100_lot_or_less_ratio": 38.49,
        "retail_100_lot_or_less_ratio_delta_pp": -1.1,
        "consecutive_thousand_lot_holder_ratio_increase_count": 2,
    }
    assert position["chip_stability_context"]["source"] == "tdcc_weekly_major_holders"
    assert position["chip_stability_context"]["state"] == "stable"
    assert position["chip_stability_context"]["trend"] == "strengthening"
    assert position["chip_stability_context"]["summary"] == "千張大戶持股比例連續增加，籌碼愈加穩定。"


def test_portfolio_risk_summary_builds_phase1_current_day_holding_lists():
    summary = build_portfolio_risk_summary(
        [
            _position(symbol="2330.TW", group="g1", entry_price="100", quantity=10),
            _position(symbol="2317.TW", group="g2", entry_price="50", quantity=20),
        ],
        plans_by_group={
            "g1": _plan(group="g1", stop="95"),
            "g2": _plan(group="g2", stop="45"),
        },
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", 120, record_date=date(2026, 6, 12)),
            "2317.TW": _raw("2317.TW", 44, record_date=date(2026, 6, 12)),
        },
        phase1_position_states_by_symbol={
            "2330.TW": {
                "state": "hold",
                "label": "續抱",
                "data_date": "2026-06-11",
                "display_anchor": {
                    "type": "entry",
                    "anchor_date": "2026-05-20",
                    "avwap": 115,
                    "distance_to_avwap_pct": 4.0,
                    "distance_basis": "snapshot_close",
                },
                "matched_rules": ["phase1_display_anchor_supported"],
                "data_quality": {"blocking": False},
            },
            "2317.TW": {
                "state": "exit_risk",
                "label": "停損警戒",
                "data_date": "2026-06-11",
                "display_anchor": {
                    "type": "breakout_20d",
                    "anchor_date": "2026-05-15",
                    "avwap": 45,
                    "distance_to_avwap_pct": -3.0,
                    "distance_basis": "snapshot_close",
                },
                "matched_rules": ["phase1_display_anchor_lost_by_2pct"],
                "data_quality": {"blocking": False},
            },
        },
        as_of_date=date(2026, 6, 12),
    )

    lists = summary["phase1_current_day_lists"]
    assert lists["version"] == "phase1-current-day-lists-v1"
    assert lists["implemented_lists"] == [
        "holding_management_candidates",
        "holding_risk_alerts",
    ]
    assert lists["pending_lists"] == [
        "pullback_observation_candidates",
        "breakout_confirmation_candidates",
        "overheated_do_not_chase_candidates",
    ]
    assert lists["pullback_observation_candidates"] == []
    assert lists["breakout_confirmation_candidates"] == []
    assert lists["overheated_do_not_chase_candidates"] == []
    assert [item["symbol"] for item in lists["holding_risk_alerts"]] == ["2317.TW"]
    assert lists["holding_risk_alerts"][0]["position_state"] == "exit_risk"
    assert lists["holding_risk_alerts"][0]["display_anchor"] == {
        "type": "breakout_20d",
        "anchor_date": "2026-05-15",
        "avwap": 45,
        "distance_to_avwap_pct": -2.2222,
        "distance_basis": "portfolio_current_price",
        "distance_price": 44.0,
        "distance_price_data_date": "2026-06-12",
        "distance_price_as_of": "2026-06-12",
    }
    assert lists["holding_risk_alerts"][0]["current_day_observation"] == (
        "最新價格已低於「20 日突破 AVWAP」至少 2%，請優先檢查原訂防守價；這不是賣出指令。"
    )
    assert [item["symbol"] for item in lists["holding_management_candidates"]] == ["2330.TW"]
    holding_item = lists["holding_management_candidates"][0]
    assert holding_item["display_anchor"]["distance_to_avwap_pct"] == 4.3478
    assert holding_item["display_anchor"]["distance_basis"] == "portfolio_current_price"
    assert holding_item["price_context"]["data_date"] == "2026-06-12"
    assert holding_item["avwap_data_date"] == "2026-06-11"
    assert holding_item["current_day_observation"] == (
        "最新價格仍位於「進場後 AVWAP」之上，尚未跌破這條技術觀察線。"
    )


def test_portfolio_risk_summary_reclassifies_phase1_state_with_refreshed_price():
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95")},
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", 97, record_date=date(2026, 6, 11)),
        },
        price_quotes_by_symbol={
            "2330.TW": {
                "status": "refreshed",
                "current_price": 103,
                "source": "yfinance_fast_info",
                "fetched_at": "2026-06-12T10:30:00+08:00",
                "data_date": "2026-06-12",
                "market_session": "intraday",
                "is_final": False,
            },
        },
        phase1_position_states_by_symbol={
            "2330.TW": {
                "state": "exit_risk",
                "label": "停損警戒",
                "data_date": "2026-06-11",
                "display_anchor": {
                    "type": "entry",
                    "anchor_date": "2026-05-20",
                    "avwap": 100,
                    "snapshot_close": 97,
                    "distance_to_avwap_pct": -3,
                    "distance_basis": "snapshot_close",
                },
                "matched_rules": ["phase1_display_anchor_lost_by_2pct"],
                "data_quality": {"blocking": False},
            },
        },
        as_of_date=date(2026, 6, 12),
    )

    assert summary["phase1_current_day_lists"]["holding_risk_alerts"] == []
    item = summary["phase1_current_day_lists"]["holding_management_candidates"][0]
    assert item["position_state"] == "hold"
    assert item["close"] == 103
    assert item["price_context"]["market_session"] == "intraday"
    assert item["display_anchor"]["distance_to_avwap_pct"] == 3.0
    assert item["display_anchor"]["distance_basis"] == "portfolio_current_price"
    assert item["display_anchor"]["distance_price"] == 103.0
    assert item["matched_rules"] == ["phase1_display_anchor_supported"]


def test_portfolio_risk_summary_clears_stale_phase1_distance_without_current_price():
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95")},
        raw_data_by_symbol={"2330.TW": _raw("2330.TW", None)},
        phase1_position_states_by_symbol={
            "2330.TW": {
                "state": "hold",
                "label": "續抱",
                "display_anchor": {
                    "type": "entry",
                    "avwap": 100,
                    "distance_to_avwap_pct": 5,
                    "distance_basis": "snapshot_close",
                },
                "matched_rules": ["phase1_display_anchor_supported"],
                "data_quality": {"blocking": False},
            },
        },
        as_of_date=date(2026, 6, 12),
    )

    state = summary["position_risks"][0]["phase1_position_state"]
    assert state["state"] == "data_unavailable"
    assert state["missing_reason"] == "portfolio_current_price_missing"
    assert state["display_anchor"]["distance_to_avwap_pct"] is None
    assert state["display_anchor"]["distance_basis"] == "portfolio_current_price"
    assert state["display_anchor"]["distance_price"] is None
    assert summary["phase1_current_day_lists"]["holding_management_candidates"] == []
    assert summary["phase1_current_day_lists"]["holding_risk_alerts"] == []


def test_portfolio_risk_summary_rejects_phase1_non_holding_observation_input():
    with pytest.raises(TypeError):
        build_portfolio_risk_summary(
            [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
            plans_by_group={"g1": _plan(group="g1", stop="95")},
            raw_data_by_symbol={"2330.TW": _raw("2330.TW", 120)},
            phase1_current_day_observations_by_symbol={
                "2454.TW": {
                    "symbol": "2454.TW",
                    "state": "pullback_watch",
                    "label": "建倉",
                    "close": 100,
                },
            },
            as_of_date=date(2026, 6, 12),
        )


def test_portfolio_risk_summary_reports_symbol_concentration_and_shared_exposures():
    summary = build_portfolio_risk_summary(
        [
            _position(symbol="2330.TW", group="g1", entry_price="100", quantity=10),
            _position(symbol="2317.TW", group="g2", entry_price="50", quantity=10),
        ],
        plans_by_group={
            "g1": _plan(group="g1", stop="90", setup_type="breakout"),
            "g2": _plan(group="g2", stop="45", setup_type="breakout"),
        },
        raw_data_by_symbol={
            "2330.TW": _raw("2330.TW", 120),
            "2317.TW": _raw("2317.TW", 60),
        },
        as_of_date=date(2026, 6, 12),
    )

    by_symbol = summary["concentration"]["by_symbol"]
    assert by_symbol[0]["key"] == "2330.TW"
    assert by_symbol[0]["pct_of_portfolio"] == 66.6667
    assert by_symbol[0]["status"] == "elevated"

    breakout = next(row for row in summary["shared_exposures"] if row["type"] == "setup_type")
    assert breakout["key"] == "breakout"
    assert breakout["count"] == 2
    assert breakout["symbols"] == ["2317.TW", "2330.TW"]


def test_portfolio_risk_summary_lists_missing_price_defense_zero_quantity_and_stale_caveats():
    summary = build_portfolio_risk_summary(
        [
            _position(symbol="2330.TW", group="g1", quantity=0),
            _position(symbol="2317.TW", group="g2", quantity=10),
            _position(symbol="2454.TW", group="g3", quantity=10),
        ],
        plans_by_group={
            "g1": _plan(group="g1", stop="90"),
            "g2": _plan(group="g2", stop=None),
            "g3": _plan(group="g3", stop="80"),
        },
        raw_data_by_symbol={
            "2317.TW": _raw("2317.TW", None),
            "2454.TW": _raw("2454.TW", 100, record_date=date(2026, 6, 1)),
        },
        as_of_date=date(2026, 6, 12),
    )

    caveat_counts = {item["code"]: item["count"] for item in summary["data_quality"]["caveats"]}
    assert caveat_counts["zero_quantity"] == 1
    assert caveat_counts["missing_price"] == 2
    assert caveat_counts["missing_defense_reference"] == 1
    assert caveat_counts["stale_price"] == 1
    assert summary["data_quality"]["status"] == "insufficient"

    stale = next(row for row in summary["position_risks"] if row["symbol"] == "2454.TW")
    assert stale["data_quality"]["status"] == "caution"
    assert stale["risk_state"] == "elevated"


def test_build_user_portfolio_risk_summary_uses_taipei_today_for_phase1_projection(
    monkeypatch: pytest.MonkeyPatch,
):
    import ai_stock_sentinel.portfolio.application.get_risk_summary as risk_summary_module

    captured: dict[str, object] = {}
    position = _position(symbol="2330.TW", group="g1")

    monkeypatch.setattr(risk_summary_module, "today_taipei", lambda: date(2026, 6, 19))
    monkeypatch.setattr(risk_summary_module, "list_active_portfolios", lambda *_args, **_kwargs: [position])
    monkeypatch.setattr(risk_summary_module, "list_lifecycle_plans_for_groups", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(risk_summary_module, "latest_final_raw_data_by_symbol", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(risk_summary_module, "weekly_major_holders_projection_by_symbol", lambda *_args, **_kwargs: {})

    def _read_phase1(*_args, **kwargs):
        captured["phase1_data_date"] = kwargs["data_date"]
        return {}

    def _build_summary(*_args, **kwargs):
        captured["summary_as_of_date"] = kwargs["as_of_date"]
        captured["phase1_current_day_observations"] = kwargs.get("phase1_current_day_observations_by_symbol")
        return {"ok": True}

    monkeypatch.setattr(risk_summary_module, "read_phase1_position_states_for_portfolio", _read_phase1)
    monkeypatch.setattr(risk_summary_module, "build_portfolio_risk_summary", _build_summary)

    result = risk_summary_module.build_user_portfolio_risk_summary(
        SimpleNamespace(get=lambda *_args: None),
        user_id=1,
        symbol_name_resolver=lambda _symbol: None,
    )

    assert result["ok"] is True
    assert len(result["portfolio_revision"]) == 64
    assert captured["phase1_data_date"] == date(2026, 6, 19)
    assert captured["summary_as_of_date"] == date(2026, 6, 19)
    assert captured["phase1_current_day_observations"] is None


def test_build_user_portfolio_risk_summary_degrades_when_weekly_major_holders_read_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    import ai_stock_sentinel.portfolio.application.get_risk_summary as risk_summary_module

    captured: dict[str, object] = {}
    position = _position(symbol="2330.TW", group="g1")

    monkeypatch.setattr(risk_summary_module, "list_active_portfolios", lambda *_args, **_kwargs: [position])
    monkeypatch.setattr(risk_summary_module, "list_lifecycle_plans_for_groups", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(risk_summary_module, "latest_final_raw_data_by_symbol", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(risk_summary_module, "read_phase1_position_states_for_portfolio", lambda *_args, **_kwargs: {})

    def _raise_weekly_major_holders_read_failure(*_args, **_kwargs):
        raise RuntimeError("shared background read unavailable")

    def _build_summary(*_args, **kwargs):
        captured["weekly_major_holders_by_symbol"] = kwargs["weekly_major_holders_by_symbol"]
        return {"ok": True}

    monkeypatch.setattr(
        risk_summary_module,
        "weekly_major_holders_projection_by_symbol",
        _raise_weekly_major_holders_read_failure,
    )
    monkeypatch.setattr(risk_summary_module, "build_portfolio_risk_summary", _build_summary)

    result = risk_summary_module.build_user_portfolio_risk_summary(
        SimpleNamespace(get=lambda *_args: None),
        user_id=1,
        symbol_name_resolver=lambda _symbol: None,
        as_of_date=date(2026, 6, 19),
    )

    assert result["ok"] is True
    assert len(result["portfolio_revision"]) == 64
    assert captured["weekly_major_holders_by_symbol"] == {}


def test_portfolio_revision_changes_with_non_price_lifecycle_structure():
    import ai_stock_sentinel.portfolio.application.get_risk_summary as risk_summary_module

    position = _position(symbol="2330.TW", group="g1")
    baseline = risk_summary_module._portfolio_revision(
        rows=[position],
        plans=[_plan(group="g1", setup_type="breakout", default_stop_rule="fixed_price")],
        raw_data_by_symbol={},
        phase1_position_states_by_symbol={},
        weekly_major_holders_by_symbol={},
    )
    changed = risk_summary_module._portfolio_revision(
        rows=[position],
        plans=[_plan(group="g1", setup_type="pullback", default_stop_rule="break_ma20")],
        raw_data_by_symbol={},
        phase1_position_states_by_symbol={},
        weekly_major_holders_by_symbol={},
    )

    assert baseline != changed
