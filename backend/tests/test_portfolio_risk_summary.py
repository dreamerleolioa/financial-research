from datetime import date
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


def _raw(symbol: str, close: float | None, record_date: date = date(2026, 6, 10)) -> SimpleNamespace:
    technical = {"close_price": close} if close is not None else {}
    return SimpleNamespace(
        symbol=symbol,
        record_date=record_date,
        technical=technical,
        raw_data_is_final=True,
    )


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
            "2330.TW": _raw("2330.TW", 120),
            "2317.TW": _raw("2317.TW", 44),
        },
        phase1_position_states_by_symbol={
            "2330.TW": {
                "state": "hold",
                "label": "續抱",
                "display_anchor": {"type": "entry", "distance_to_avwap_pct": 4.0},
                "matched_rules": ["phase1_display_anchor_supported"],
                "data_quality": {"blocking": False},
            },
            "2317.TW": {
                "state": "exit_risk",
                "label": "停損警戒",
                "display_anchor": {"type": "breakout_20d", "distance_to_avwap_pct": -3.0},
                "matched_rules": ["phase1_display_anchor_lost_by_2pct"],
                "data_quality": {"blocking": False},
            },
        },
        as_of_date=date(2026, 6, 12),
    )

    lists = summary["phase1_current_day_lists"]
    assert lists["version"] == "phase1-current-day-lists-v1"
    assert lists["implemented_lists"] == [
        "pullback_observation_candidates",
        "breakout_confirmation_candidates",
        "holding_management_candidates",
        "holding_risk_alerts",
        "overheated_do_not_chase_candidates",
    ]
    assert lists["pending_lists"] == []
    assert lists["pullback_observation_candidates"] == []
    assert lists["breakout_confirmation_candidates"] == []
    assert lists["overheated_do_not_chase_candidates"] == []
    assert [item["symbol"] for item in lists["holding_risk_alerts"]] == ["2317.TW"]
    assert lists["holding_risk_alerts"][0]["position_state"] == "exit_risk"
    assert lists["holding_risk_alerts"][0]["current_day_observation"] == "已跌破 breakout_20d 觀察線，優先檢查風險控制條件。"
    assert [item["symbol"] for item in lists["holding_management_candidates"]] == ["2330.TW"]
    assert lists["holding_management_candidates"][0]["current_day_observation"] == "觀察 entry 是否維持支撐，結構仍偏健康。"


def test_portfolio_risk_summary_builds_phase1_current_day_non_holding_lists():
    summary = build_portfolio_risk_summary(
        [_position(symbol="2330.TW", group="g1", entry_price="100", quantity=10)],
        plans_by_group={"g1": _plan(group="g1", stop="95")},
        raw_data_by_symbol={"2330.TW": _raw("2330.TW", 120)},
        phase1_current_day_observations_by_symbol={
            "2454.TW": {
                "symbol": "2454.TW",
                "state": "pullback_watch",
                "label": "建倉",
                "close": 100,
                "display_anchor": {"type": "swing_low_60d", "distance_to_avwap_pct": 3.0},
                "matched_rules": ["phase1_swing_low_anchor_supported_within_5pct"],
                "data_quality": {"blocking": False},
            },
            "2317.TW": {
                "symbol": "2317.TW",
                "state": "strong_breakout",
                "label": "建倉",
                "close": 80,
                "display_anchor": {"type": "breakout_20d", "distance_to_avwap_pct": 2.0},
                "matched_rules": ["phase1_breakout_anchor_supported_within_5pct"],
                "data_quality": {"blocking": False},
            },
            "2603.TW": {
                "symbol": "2603.TW",
                "state": "overheated",
                "label": None,
                "close": 150,
                "display_anchor": {"type": "swing_low_60d", "distance_to_avwap_pct": 12.0},
                "matched_rules": ["phase1_swing_low_extended_10pct"],
                "data_quality": {"blocking": False},
            },
            "2882.TW": {
                "symbol": "2882.TW",
                "state": "range_watch",
                "label": None,
                "close": 60,
                "display_anchor": {"type": "swing_low_60d", "distance_to_avwap_pct": 7.0},
                "matched_rules": ["phase1_no_current_day_list_match"],
                "data_quality": {"blocking": False},
            },
        },
        as_of_date=date(2026, 6, 12),
    )

    lists = summary["phase1_current_day_lists"]
    assert [item["symbol"] for item in lists["pullback_observation_candidates"]] == ["2454.TW"]
    assert lists["pullback_observation_candidates"][0]["current_day_observation"] == "觀察回測 swing_low_60d 是否維持支撐。"
    assert [item["symbol"] for item in lists["breakout_confirmation_candidates"]] == ["2317.TW"]
    assert lists["breakout_confirmation_candidates"][0]["current_day_observation"] == "觀察是否持續站穩 breakout_20d，避免追高解讀。"
    assert [item["symbol"] for item in lists["overheated_do_not_chase_candidates"]] == ["2603.TW"]
    assert lists["overheated_do_not_chase_candidates"][0]["current_day_observation"] == "距離 swing_low_60d 偏遠，先等待均線或 AVWAP 支撐重新整理。"


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

    def _read_phase1(*_args, **kwargs):
        captured["phase1_data_date"] = kwargs["data_date"]
        return {}

    def _build_summary(*_args, **kwargs):
        captured["summary_as_of_date"] = kwargs["as_of_date"]
        return {"ok": True}

    monkeypatch.setattr(risk_summary_module, "read_phase1_position_states_for_portfolio", _read_phase1)
    monkeypatch.setattr(
        risk_summary_module,
        "read_phase1_current_day_observations_for_managed_universe",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(risk_summary_module, "build_portfolio_risk_summary", _build_summary)

    result = risk_summary_module.build_user_portfolio_risk_summary(
        object(),
        user_id=1,
        symbol_name_resolver=lambda _symbol: None,
    )

    assert result == {"ok": True}
    assert captured["phase1_data_date"] == date(2026, 6, 19)
    assert captured["summary_as_of_date"] == date(2026, 6, 19)
