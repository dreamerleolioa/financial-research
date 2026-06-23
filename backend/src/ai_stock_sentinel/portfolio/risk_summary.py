from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


PORTFOLIO_RISK_SUMMARY_VERSION = "portfolio-risk-summary-v1"
PHASE1_CURRENT_DAY_LISTS_VERSION = "phase1-current-day-lists-v1"
PHASE1_CURRENT_DAY_LIST_KEYS = (
    "pullback_observation_candidates",
    "breakout_confirmation_candidates",
    "holding_management_candidates",
    "holding_risk_alerts",
    "overheated_do_not_chase_candidates",
)
PHASE1_CURRENT_DAY_IMPLEMENTED_LISTS = (
    "holding_management_candidates",
    "holding_risk_alerts",
)
PHASE1_CURRENT_DAY_PENDING_LISTS = tuple(
    key for key in PHASE1_CURRENT_DAY_LIST_KEYS if key not in PHASE1_CURRENT_DAY_IMPLEMENTED_LISTS
)
STALE_PRICE_MAX_AGE_DAYS = 5
POSITION_RISK_WATCH_PCT = 2.0
POSITION_RISK_ELEVATED_PCT = 5.0
SYMBOL_CONCENTRATION_WATCH_PCT = 25.0
SYMBOL_CONCENTRATION_ELEVATED_PCT = 35.0
TOTAL_RISK_WATCH_PCT = 5.0
TOTAL_RISK_CONSTRAINED_PCT = 10.0


def build_portfolio_risk_summary(
    positions: list[Any],
    *,
    plans_by_group: dict[str, Any] | None = None,
    raw_data_by_symbol: dict[str, Any] | None = None,
    symbol_names_by_symbol: dict[str, str | None] | None = None,
    phase1_position_states_by_symbol: dict[str, dict[str, Any]] | None = None,
    weekly_major_holders_by_symbol: dict[str, dict[str, Any]] | None = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    as_of = as_of_date or date.today()
    plans = plans_by_group or {}
    raw_rows = raw_data_by_symbol or {}
    symbol_names = symbol_names_by_symbol or {}
    phase1_states = phase1_position_states_by_symbol
    weekly_major_holders = weekly_major_holders_by_symbol or {}

    position_drafts: list[dict[str, Any]] = []
    portfolio_value = Decimal("0")
    total_unrealized_pnl = Decimal("0")
    total_at_risk = Decimal("0")
    aggregate_caveat_counts: dict[str, int] = defaultdict(int)

    for position in positions:
        symbol = str(getattr(position, "symbol", ""))
        quantity = _to_decimal(getattr(position, "quantity", None))
        entry_price = _to_decimal(getattr(position, "entry_price", None))
        plan = plans.get(str(getattr(position, "position_group_id", "")))
        raw_row = raw_rows.get(symbol)
        current_price = _extract_current_price(raw_row)
        defense_reference, defense_source = _extract_defense_reference(plan)
        caveats: list[dict[str, str]] = []

        if quantity is None or quantity <= 0:
            caveats.append(_caveat("zero_quantity", "持股數量為 0 或缺漏，暫不估計此部位風險。"))
        if current_price is None:
            caveats.append(_caveat("missing_price", "缺少可用的近期價格，暫不估計此部位風險。"))
        elif _is_stale(raw_row, as_of):
            caveats.append(_caveat("stale_price", f"最新價格日期超過 {STALE_PRICE_MAX_AGE_DAYS} 天，估算需附帶資料時效限制。"))
        if defense_reference is None:
            caveats.append(_caveat("missing_defense_reference", "缺少風險控制參考價，暫不估計此部位風險。"))

        market_value = None
        unrealized_pnl = None
        estimated_risk_amount = None
        if quantity is not None and quantity > 0 and current_price is not None:
            market_value = current_price * quantity
            portfolio_value += market_value
            if entry_price is not None:
                unrealized_pnl = (current_price - entry_price) * quantity
                total_unrealized_pnl += unrealized_pnl
            if defense_reference is not None:
                estimated_risk_amount = max(Decimal("0"), current_price - defense_reference) * quantity
                total_at_risk += estimated_risk_amount

        for caveat in caveats:
            aggregate_caveat_counts[caveat["code"]] += 1

        position_draft = {
            "symbol": symbol,
            "name": symbol_names.get(symbol),
            "quantity": _float_or_none(quantity),
            "current_price": _float_or_none(current_price),
            "entry_price": _float_or_none(entry_price),
            "market_value": _float_or_none(market_value),
            "unrealized_pnl": _float_or_none(unrealized_pnl),
            "defense_reference": {
                "price": _float_or_none(defense_reference),
                "source": defense_source,
            },
            "estimated_risk_amount": _float_or_none(estimated_risk_amount),
            "estimated_risk_pct_of_portfolio": None,
            "risk_state": "data_incomplete",
            "discipline_triggers": [],
            "data_quality": _position_data_quality(caveats),
            "_raw": {
                "estimated_risk_amount": estimated_risk_amount,
                "market_value": market_value,
                "current_price": current_price,
                "defense_reference": defense_reference,
                "plan": plan,
                "has_incomplete_caveat": any(
                    caveat["code"] in {"zero_quantity", "missing_price", "missing_defense_reference"}
                    for caveat in caveats
                ),
                "has_stale_caveat": any(caveat["code"] == "stale_price" for caveat in caveats),
            },
        }
        if phase1_states is not None:
            group_key = str(getattr(position, "position_group_id", "") or "")
            position_draft["phase1_position_state"] = (
                phase1_states.get(group_key)
                or phase1_states.get(symbol)
                or phase1_states.get(symbol.upper())
            )
        if symbol in weekly_major_holders:
            position_draft["weekly_major_holders"] = dict(weekly_major_holders[symbol])
        position_drafts.append(position_draft)

    for draft in position_drafts:
        raw = draft.pop("_raw")
        market_value = raw["market_value"]
        risk_amount = raw["estimated_risk_amount"]
        concentration_pct = _pct(market_value, portfolio_value)
        risk_pct = _pct(risk_amount, portfolio_value)
        draft["estimated_risk_pct_of_portfolio"] = risk_pct
        draft["portfolio_weight_pct"] = concentration_pct
        draft["risk_state"] = _risk_state(raw, risk_pct, concentration_pct)
        draft["discipline_triggers"] = _discipline_triggers(raw, risk_pct, concentration_pct)

    concentration = _build_symbol_concentration(position_drafts, portfolio_value)
    shared_exposures = _build_shared_exposures(position_drafts, positions, plans, portfolio_value)
    phase1_current_day_lists = _build_phase1_current_day_lists(position_drafts)
    total_risk_pct = _pct(total_at_risk, portfolio_value)

    return {
        "version": PORTFOLIO_RISK_SUMMARY_VERSION,
        "as_of_date": as_of.isoformat(),
        "portfolio_value": _round_money(portfolio_value),
        "total_unrealized_pnl": _round_money(total_unrealized_pnl),
        "total_at_risk": _round_money(total_at_risk),
        "total_at_risk_pct": total_risk_pct,
        "position_risks": position_drafts,
        "phase1_current_day_lists": phase1_current_day_lists,
        "concentration": concentration,
        "shared_exposures": shared_exposures,
        "risk_budget_status": _risk_budget_status(total_risk_pct, aggregate_caveat_counts),
        "data_quality": _portfolio_data_quality(aggregate_caveat_counts),
    }


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _extract_current_price(raw_row: Any) -> Decimal | None:
    if raw_row is None:
        return None
    technical = getattr(raw_row, "technical", None) or {}
    candidates = [
        technical.get("close_price"),
        technical.get("close"),
        technical.get("current_price"),
    ]
    ohlcv = technical.get("ohlcv")
    if isinstance(ohlcv, dict):
        candidates.append(ohlcv.get("close"))
    recent_closes = technical.get("recent_closes")
    if isinstance(recent_closes, list) and recent_closes:
        candidates.append(recent_closes[-1])
    for candidate in candidates:
        value = _to_decimal(candidate)
        if value is not None and value > 0:
            return value
    return None


def _extract_defense_reference(plan: Any) -> tuple[Decimal | None, str | None]:
    if plan is None:
        return None, None
    planned_stop_price = _to_decimal(getattr(plan, "planned_stop_price", None))
    if planned_stop_price is not None and planned_stop_price > 0:
        return planned_stop_price, "planned_stop_price"
    return None, None


def _is_stale(raw_row: Any, as_of_date: date) -> bool:
    if raw_row is None:
        return False
    record_date = getattr(raw_row, "record_date", None)
    if record_date is None:
        return True
    if hasattr(record_date, "date"):
        record_date = record_date.date()
    return (as_of_date - record_date).days > STALE_PRICE_MAX_AGE_DAYS


def _caveat(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _position_data_quality(caveats: list[dict[str, str]]) -> dict[str, Any]:
    if any(caveat["code"] in {"zero_quantity", "missing_price", "missing_defense_reference"} for caveat in caveats):
        status = "insufficient"
    elif caveats:
        status = "caution"
    else:
        status = "ok"
    return {"status": status, "caveats": caveats}


def _portfolio_data_quality(caveat_counts: dict[str, int]) -> dict[str, Any]:
    if any(caveat_counts.get(code, 0) for code in {"zero_quantity", "missing_price", "missing_defense_reference"}):
        status = "insufficient"
    elif any(caveat_counts.values()):
        status = "caution"
    else:
        status = "ok"
    caveats = [
        {"code": code, "count": count}
        for code, count in sorted(caveat_counts.items())
        if count > 0
    ]
    return {
        "status": status,
        "caveats": caveats,
        "price_stale_after_days": STALE_PRICE_MAX_AGE_DAYS,
    }


def _risk_state(raw: dict[str, Any], risk_pct: float | None, concentration_pct: float | None) -> str:
    if raw["has_incomplete_caveat"]:
        return "data_incomplete"
    current_price = raw["current_price"]
    defense_reference = raw["defense_reference"]
    if current_price is not None and defense_reference is not None and current_price <= defense_reference:
        return "defense_reference_touched"
    if (risk_pct is not None and risk_pct >= POSITION_RISK_ELEVATED_PCT) or (
        concentration_pct is not None and concentration_pct >= SYMBOL_CONCENTRATION_ELEVATED_PCT
    ):
        return "elevated"
    if raw["has_stale_caveat"] or (risk_pct is not None and risk_pct >= POSITION_RISK_WATCH_PCT) or (
        concentration_pct is not None and concentration_pct >= SYMBOL_CONCENTRATION_WATCH_PCT
    ):
        return "watch"
    return "contained"


def _discipline_triggers(raw: dict[str, Any], risk_pct: float | None, concentration_pct: float | None) -> list[str]:
    triggers: list[str] = []
    if raw["has_incomplete_caveat"]:
        triggers.append("資料不足，暫不估計此部位風險。")
    current_price = raw["current_price"]
    defense_reference = raw["defense_reference"]
    if current_price is not None and defense_reference is not None and current_price <= defense_reference:
        triggers.append("價格已觸及或低於風險控制參考，需優先檢查紀律條件。")
    if risk_pct is not None and risk_pct >= POSITION_RISK_ELEVATED_PCT:
        triggers.append(f"單一部位估計曝險占投資組合 {risk_pct:.2f}%，高於 {POSITION_RISK_ELEVATED_PCT:.0f}% 檢查線。")
    elif risk_pct is not None and risk_pct >= POSITION_RISK_WATCH_PCT:
        triggers.append(f"單一部位估計曝險占投資組合 {risk_pct:.2f}%，高於 {POSITION_RISK_WATCH_PCT:.0f}% 觀察線。")
    if concentration_pct is not None and concentration_pct >= SYMBOL_CONCENTRATION_ELEVATED_PCT:
        triggers.append(f"單一標的市值占投資組合 {concentration_pct:.2f}%，高於 {SYMBOL_CONCENTRATION_ELEVATED_PCT:.0f}% 集中度檢查線。")
    elif concentration_pct is not None and concentration_pct >= SYMBOL_CONCENTRATION_WATCH_PCT:
        triggers.append(f"單一標的市值占投資組合 {concentration_pct:.2f}%，高於 {SYMBOL_CONCENTRATION_WATCH_PCT:.0f}% 觀察線。")
    if raw["has_stale_caveat"]:
        triggers.append("價格資料時效偏舊，需先確認資料品質再解讀估算。")
    return triggers


def _build_symbol_concentration(position_risks: list[dict[str, Any]], portfolio_value: Decimal) -> dict[str, Any]:
    rows = []
    for risk in position_risks:
        market_value = _to_decimal(risk.get("market_value"))
        pct = _pct(market_value, portfolio_value)
        status = "ok"
        if pct is not None and pct >= SYMBOL_CONCENTRATION_ELEVATED_PCT:
            status = "elevated"
        elif pct is not None and pct >= SYMBOL_CONCENTRATION_WATCH_PCT:
            status = "watch"
        rows.append({
            "type": "symbol",
            "key": risk["symbol"],
            "market_value": risk["market_value"],
            "pct_of_portfolio": pct,
            "status": status,
        })
    rows.sort(key=lambda row: (row["pct_of_portfolio"] is not None, row["pct_of_portfolio"] or 0, row["key"]), reverse=True)
    return {"by_symbol": rows}


def _build_shared_exposures(
    position_risks: list[dict[str, Any]],
    positions: list[Any],
    plans_by_group: dict[str, Any],
    portfolio_value: Decimal,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    risks_by_symbol = {risk["symbol"]: risk for risk in position_risks}
    for position in positions:
        symbol = str(getattr(position, "symbol", ""))
        risk = risks_by_symbol.get(symbol)
        if not risk:
            continue
        market_value = _to_decimal(risk.get("market_value")) or Decimal("0")
        plan = plans_by_group.get(str(getattr(position, "position_group_id", "")))
        exposure_values = [
            ("risk_state", risk.get("risk_state")),
            ("setup_type", getattr(plan, "setup_type", None) if plan is not None else None),
            ("default_stop_rule", getattr(plan, "default_stop_rule", None) if plan is not None else None),
        ]
        for exposure_type, key in exposure_values:
            if key in (None, "", "not_recorded", "no_stop_recorded"):
                continue
            bucket_key = (exposure_type, str(key))
            bucket = buckets.setdefault(bucket_key, {
                "type": exposure_type,
                "key": str(key),
                "symbols": [],
                "_market_value": Decimal("0"),
                "count": 0,
            })
            bucket["symbols"].append(symbol)
            bucket["_market_value"] += market_value
            bucket["count"] += 1
    exposures = []
    for bucket in buckets.values():
        market_value = bucket.pop("_market_value")
        bucket["symbols"] = sorted(set(bucket["symbols"]))
        bucket["market_value"] = _round_money(market_value)
        bucket["pct_of_portfolio"] = _pct(market_value, portfolio_value)
        exposures.append(bucket)
    exposures.sort(key=lambda row: (row["pct_of_portfolio"] is not None, row["pct_of_portfolio"] or 0, row["type"], row["key"]), reverse=True)
    return exposures


def _build_phase1_current_day_lists(position_risks: list[dict[str, Any]]) -> dict[str, Any]:
    lists = {key: [] for key in PHASE1_CURRENT_DAY_LIST_KEYS}
    for risk in position_risks:
        state = risk.get("phase1_position_state")
        if not isinstance(state, dict):
            continue
        position_state = str(state.get("state") or "")
        if position_state in {"hold", "add_watch", "profit_take_watch"}:
            lists["holding_management_candidates"].append(_phase1_holding_observation_item(risk, state))
        elif position_state in {"warning", "exit_risk"}:
            lists["holding_risk_alerts"].append(_phase1_holding_observation_item(risk, state))
    for key in PHASE1_CURRENT_DAY_LIST_KEYS:
        lists[key].sort(key=_phase1_observation_sort_key)
    return {
        "version": PHASE1_CURRENT_DAY_LISTS_VERSION,
        "implemented_lists": list(PHASE1_CURRENT_DAY_IMPLEMENTED_LISTS),
        "pending_lists": list(PHASE1_CURRENT_DAY_PENDING_LISTS),
        **lists,
    }


def _phase1_holding_observation_item(risk: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    position_state = str(state.get("state") or "data_unavailable")
    display_anchor = state.get("display_anchor") if isinstance(state.get("display_anchor"), dict) else None
    return {
        "symbol": risk["symbol"],
        "name": risk.get("name"),
        "label": state.get("label"),
        "position_state": position_state,
        "close": risk.get("current_price"),
        "holding_avg_cost": risk.get("entry_price"),
        "display_anchor": display_anchor,
        "matched_rules": list(state.get("matched_rules") or []),
        "current_day_observation": _phase1_current_day_observation_text(position_state, display_anchor),
        "data_quality": dict(state.get("data_quality") or {}),
    }


def _phase1_current_day_observation_text(position_state: str, display_anchor: dict[str, Any] | None) -> str:
    anchor_type = str(display_anchor.get("type")) if display_anchor else "phase1_anchor"
    if position_state == "add_watch":
        return f"觀察回測 {anchor_type} 後是否維持支撐。"
    if position_state == "profit_take_watch":
        return "結構偏熱，觀察是否等待均線或 AVWAP 支撐重新整理。"
    if position_state == "warning":
        return f"觀察是否重新站回 {anchor_type}，避免結構轉弱擴大。"
    if position_state == "exit_risk":
        return f"已跌破 {anchor_type} 觀察線，優先檢查風險控制條件。"
    return f"觀察 {anchor_type} 是否維持支撐，結構仍偏健康。"


def _phase1_observation_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    priority = {
        "exit_risk": 0,
        "warning": 1,
        "profit_take_watch": 2,
        "add_watch": 3,
        "hold": 4,
        "overheated": 5,
        "strong_breakout": 6,
        "pullback_watch": 7,
    }
    return priority.get(str(item.get("position_state") or ""), 99), str(item.get("symbol") or "")


def _risk_budget_status(total_risk_pct: float | None, caveat_counts: dict[str, int]) -> dict[str, Any]:
    blocking_data_gap = any(caveat_counts.get(code, 0) for code in {"zero_quantity", "missing_price", "missing_defense_reference"})
    if total_risk_pct is None:
        status = "unknown"
    elif total_risk_pct >= TOTAL_RISK_CONSTRAINED_PCT:
        status = "constrained"
    elif total_risk_pct >= TOTAL_RISK_WATCH_PCT:
        status = "watch"
    else:
        status = "available"
    notes = []
    if blocking_data_gap:
        notes.append("部分部位資料不足，風險預算狀態需搭配 data_quality 解讀。")
    return {
        "status": status,
        "total_at_risk_pct": total_risk_pct,
        "watch_threshold_pct": TOTAL_RISK_WATCH_PCT,
        "constrained_threshold_pct": TOTAL_RISK_CONSTRAINED_PCT,
        "notes": notes,
    }


def _pct(numerator: Decimal | None, denominator: Decimal | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(float((numerator / denominator) * Decimal("100")), 4)


def _float_or_none(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _round_money(value: Decimal) -> float:
    return round(float(value), 4)
