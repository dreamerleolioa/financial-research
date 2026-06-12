from datetime import date
from decimal import Decimal
from types import SimpleNamespace

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
