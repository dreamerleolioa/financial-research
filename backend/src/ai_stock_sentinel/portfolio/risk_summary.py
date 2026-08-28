from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite, sqrt
from typing import Any

from ai_stock_sentinel.chip_stability_context import chip_stability_context_from_weekly_major_holders
from ai_stock_sentinel.taiwan_symbols import is_supported_taiwan_symbol


PORTFOLIO_RISK_SUMMARY_VERSION = "portfolio-risk-summary-v2"
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
INDUSTRY_CONCENTRATION_WATCH_PCT = 40.0
INDUSTRY_CONCENTRATION_ELEVATED_PCT = 60.0
CORRELATION_MIN_OVERLAPPING_RETURNS = 20
CORRELATION_WATCH_THRESHOLD = 0.65
CORRELATION_ELEVATED_THRESHOLD = 0.8
MAX_SUPPORTED_MARKET_PRICE = Decimal("99999999.99")
BLOCKING_DATA_GAP_CODES = frozenset({
    "zero_quantity",
    "missing_price",
    "missing_defense_reference",
    "unsupported_market",
})


def build_portfolio_risk_summary(
    positions: list[Any],
    *,
    plans_by_group: dict[str, Any] | None = None,
    raw_data_by_symbol: dict[str, Any] | None = None,
    price_quotes_by_symbol: dict[str, dict[str, Any]] | None = None,
    symbol_names_by_symbol: dict[str, str | None] | None = None,
    phase1_position_states_by_symbol: dict[str, dict[str, Any]] | None = None,
    weekly_major_holders_by_symbol: dict[str, dict[str, Any]] | None = None,
    cash_balance: Any = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    as_of = as_of_date or date.today()
    plans = plans_by_group or {}
    raw_rows = raw_data_by_symbol or {}
    price_quotes = price_quotes_by_symbol or {}
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
        supported_market = is_supported_taiwan_symbol(symbol)
        quantity = _to_decimal(getattr(position, "quantity", None))
        entry_price = _to_decimal(getattr(position, "entry_price", None))
        plan = plans.get(str(getattr(position, "position_group_id", "")))
        raw_row = raw_rows.get(symbol)
        price_quote = price_quotes.get(symbol)
        current_price = _current_price_with_quote(raw_row, price_quote)
        defense_reference, defense_source = _extract_defense_reference(plan)
        caveats: list[dict[str, str]] = []

        if not supported_market:
            caveats.append(_caveat(
                "unsupported_market",
                "此既有部位不是台灣上市或上櫃股票，已排除於台幣投資組合總額與風險估算。",
            ))
        if quantity is None or quantity <= 0:
            caveats.append(_caveat("zero_quantity", "持股數量為 0 或缺漏，暫不估計此部位風險。"))
        if current_price is None:
            caveats.append(_caveat("missing_price", "缺少可用的近期價格，暫不估計此部位風險。"))
        elif not _quote_was_refreshed(price_quote) and _is_stale(raw_row, as_of):
            caveats.append(_caveat("stale_price", f"最新價格日期超過 {STALE_PRICE_MAX_AGE_DAYS} 天，估算需附帶資料時效限制。"))
        if price_quote is not None and price_quote.get("status") == "failed":
            caveats.append(_caveat("price_refresh_failed", "最新報價更新失敗，暫時沿用既有價格。"))
        elif (
            price_quote is not None
            and price_quote.get("status") == "refreshed"
            and not _quote_was_refreshed(price_quote)
        ):
            caveats.append(_caveat("price_refresh_invalid", "最新報價超出可用價格範圍，暫時沿用既有價格。"))
        if defense_reference is None:
            caveats.append(_caveat("missing_defense_reference", "缺少風險控制參考價，暫不估計此部位風險。"))

        market_value = None
        unrealized_pnl = None
        estimated_risk_amount = None
        if supported_market and quantity is not None and quantity > 0 and current_price is not None:
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
            "industry": _extract_industry(raw_row),
            "quantity": _float_or_none(quantity),
            "current_price": _float_or_none(current_price),
            "price_context": _price_context(raw_row, price_quote),
            "entry_price": _float_or_none(entry_price),
            "market_value": _float_or_none(market_value),
            "unrealized_pnl": _float_or_none(unrealized_pnl),
            "defense_reference": {
                "price": _float_or_none(defense_reference),
                "source": defense_source,
            },
            "auto_defense_prices": _extract_auto_defense_prices(raw_row),
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
                    caveat["code"] in BLOCKING_DATA_GAP_CODES
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
            weekly_major_holders_projection = dict(weekly_major_holders[symbol])
            position_draft["weekly_major_holders"] = weekly_major_holders_projection
            chip_stability_context = chip_stability_context_from_weekly_major_holders(
                weekly_major_holders_projection
            )
            if chip_stability_context is not None:
                position_draft["chip_stability_context"] = chip_stability_context
        position_drafts.append(position_draft)

    recorded_cash = _to_decimal(cash_balance)
    if recorded_cash is not None and recorded_cash < 0:
        recorded_cash = None
    account_equity = portfolio_value + recorded_cash if recorded_cash is not None else None
    risk_capital_base = account_equity if account_equity is not None else portfolio_value
    for draft in position_drafts:
        raw = draft.pop("_raw")
        market_value = raw["market_value"]
        risk_amount = raw["estimated_risk_amount"]
        concentration_pct = _pct(market_value, risk_capital_base)
        risk_pct = _pct(risk_amount, risk_capital_base)
        draft["estimated_risk_pct_of_portfolio"] = risk_pct
        draft["portfolio_weight_pct"] = concentration_pct
        draft["invested_weight_pct"] = _pct(market_value, portfolio_value)
        draft["account_equity_weight_pct"] = _pct(market_value, account_equity)
        draft["risk_state"] = _risk_state(raw, risk_pct, concentration_pct)
        draft["discipline_triggers"] = _discipline_triggers(raw, risk_pct, concentration_pct)

    concentration = _build_concentration(
        position_drafts,
        invested_market_value=portfolio_value,
        capital_base=risk_capital_base,
    )
    shared_exposures = _build_shared_exposures(position_drafts, positions, plans, risk_capital_base)
    correlation_risk = _build_correlation_risk(
        position_drafts,
        raw_rows,
        invested_market_value=portfolio_value,
    )
    phase1_current_day_lists = _build_phase1_current_day_lists(position_drafts)
    total_risk_pct = _pct(total_at_risk, risk_capital_base)

    return {
        "version": PORTFOLIO_RISK_SUMMARY_VERSION,
        "as_of_date": as_of.isoformat(),
        "portfolio_value": _round_money(portfolio_value),
        "account_capital": {
            "status": "recorded" if recorded_cash is not None else "cash_not_recorded",
            "cash_balance": _round_money(recorded_cash) if recorded_cash is not None else None,
            "invested_market_value": _round_money(portfolio_value),
            "account_equity": _round_money(account_equity) if account_equity is not None else None,
            "cash_pct_of_account_equity": _pct(recorded_cash, account_equity),
            "invested_pct_of_account_equity": _pct(portfolio_value, account_equity),
            "risk_percentage_denominator": (
                "account_equity" if account_equity is not None else "invested_market_value_fallback"
            ),
        },
        "total_unrealized_pnl": _round_money(total_unrealized_pnl),
        "total_at_risk": _round_money(total_at_risk),
        "total_at_risk_pct": total_risk_pct,
        "position_risks": position_drafts,
        "phase1_current_day_lists": phase1_current_day_lists,
        "concentration": concentration,
        "shared_exposures": shared_exposures,
        "correlation_risk": correlation_risk,
        "risk_budget_status": _risk_budget_status(
            total_risk_pct,
            aggregate_caveat_counts,
            uses_invested_fallback=recorded_cash is None,
        ),
        "data_quality": _portfolio_data_quality(aggregate_caveat_counts),
    }


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and _finite_float(number) is not None else None


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number_series(value: Any) -> list[Decimal]:
    if not isinstance(value, list):
        return []
    values: list[Decimal] = []
    for item in value:
        number = _to_decimal(item)
        if number is not None:
            values.append(number)
    return values


def _mean_last(values: list[Decimal], count: int) -> Decimal | None:
    if len(values) < count:
        return None
    window = values[-count:]
    return sum(window, Decimal("0")) / Decimal(count)


def _min_last(values: list[Decimal], count: int) -> Decimal | None:
    if len(values) < count:
        return None
    return min(values[-count:])


def _first_decimal(*values: Any) -> Decimal | None:
    for value in values:
        number = _to_decimal(value)
        if number is not None:
            return number
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
        value = _market_price_decimal(candidate)
        if value is not None:
            return value
    return None


def _current_price_with_quote(
    raw_row: Any,
    price_quote: dict[str, Any] | None,
) -> Decimal | None:
    if _quote_was_refreshed(price_quote):
        refreshed_price = _market_price_decimal(price_quote.get("current_price"))
        if refreshed_price is not None:
            return refreshed_price
    return _extract_current_price(raw_row)


def _market_price_decimal(value: Any) -> Decimal | None:
    price = _to_decimal(value)
    if price is None or price <= 0 or price > MAX_SUPPORTED_MARKET_PRICE:
        return None
    return price


def _quote_was_refreshed(price_quote: dict[str, Any] | None) -> bool:
    return bool(
        price_quote
        and price_quote.get("status") == "refreshed"
        and _market_price_decimal(price_quote.get("current_price")) is not None
    )


def _price_context(
    raw_row: Any,
    price_quote: dict[str, Any] | None,
) -> dict[str, Any]:
    if _quote_was_refreshed(price_quote):
        return {
            "refresh_status": "refreshed",
            "source": price_quote.get("source"),
            "as_of": price_quote.get("fetched_at"),
            "data_date": price_quote.get("data_date"),
            "market_session": price_quote.get("market_session", "unknown"),
            "is_final": price_quote.get("is_final"),
        }

    record_date = getattr(raw_row, "record_date", None) if raw_row is not None else None
    fetched_at = getattr(raw_row, "fetched_at", None) if raw_row is not None else None
    refresh_failed = price_quote is not None and (
        price_quote.get("status") == "failed"
        or (
            price_quote.get("status") == "refreshed"
            and not _quote_was_refreshed(price_quote)
        )
    )
    return {
        "refresh_status": "failed" if refresh_failed else "not_requested",
        "source": "stock_raw_data_fallback" if refresh_failed else "stock_raw_data",
        "as_of": _iso_string(fetched_at) or _iso_string(record_date),
        "data_date": _iso_string(record_date),
        "market_session": "closed" if raw_row is not None else "unknown",
        "is_final": bool(getattr(raw_row, "raw_data_is_final", False)) if raw_row is not None else None,
    }


def _iso_string(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _extract_defense_reference(plan: Any) -> tuple[Decimal | None, str | None]:
    if plan is None:
        return None, None
    planned_stop_price = _to_decimal(getattr(plan, "planned_stop_price", None))
    if planned_stop_price is not None and planned_stop_price > 0:
        return planned_stop_price, "planned_stop_price"
    return None, None


def _extract_auto_defense_prices(raw_row: Any) -> dict[str, float | None]:
    technical = getattr(raw_row, "technical", None) or {}
    indicators = _as_mapping(technical.get("indicators"))
    technical_indicators = _as_mapping(technical.get("technical_indicators"))
    recent_closes = _number_series(technical.get("recent_closes"))
    recent_lows = _number_series(technical.get("recent_lows")) or recent_closes
    return {
        "break_20d_low": _float_or_none(_first_decimal(
            technical.get("low_20d"),
            indicators.get("low_20d"),
            indicators.get("support_level"),
            indicators.get("support"),
            technical_indicators.get("low_20d"),
            _min_last(recent_lows, 20),
        )),
        "break_ma20": _float_or_none(_first_decimal(
            technical.get("ma20"),
            indicators.get("ma20"),
            technical_indicators.get("ma20"),
            _mean_last(recent_closes, 20),
        )),
        "break_ma60": _float_or_none(_first_decimal(
            technical.get("ma60"),
            indicators.get("ma60"),
            technical_indicators.get("ma60"),
            _mean_last(recent_closes, 60),
        )),
    }


def _extract_industry(raw_row: Any) -> str | None:
    fundamental = _as_mapping(getattr(raw_row, "fundamental", None))
    for key in ("industry", "industry_name", "industry_category", "sector"):
        value = fundamental.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


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
    if any(caveat["code"] in BLOCKING_DATA_GAP_CODES for caveat in caveats):
        status = "insufficient"
    elif caveats:
        status = "caution"
    else:
        status = "ok"
    return {"status": status, "caveats": caveats}


def _portfolio_data_quality(caveat_counts: dict[str, int]) -> dict[str, Any]:
    if any(caveat_counts.get(code, 0) for code in BLOCKING_DATA_GAP_CODES):
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


def _build_concentration(
    position_risks: list[dict[str, Any]],
    *,
    invested_market_value: Decimal,
    capital_base: Decimal,
) -> dict[str, Any]:
    rows = []
    industry_values: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    classified_market_value = Decimal("0")
    eligible_position_count = 0
    valued_position_count = 0
    classified_position_count = 0
    for risk in position_risks:
        market_value = _to_decimal(risk.get("market_value"))
        pct = _pct(market_value, capital_base)
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
        if _is_industry_coverage_eligible(risk):
            eligible_position_count += 1
            if market_value is not None and market_value > 0:
                valued_position_count += 1
        industry = risk.get("industry")
        if (
            _is_industry_coverage_eligible(risk)
            and isinstance(industry, str)
            and market_value is not None
            and market_value > 0
        ):
            industry_values[industry] += market_value
            classified_market_value += market_value
            classified_position_count += 1
    rows.sort(key=lambda row: (row["pct_of_portfolio"] is not None, row["pct_of_portfolio"] or 0, row["key"]), reverse=True)
    coverage_pct = _pct(classified_market_value, invested_market_value)
    if eligible_position_count == 0:
        coverage_status = "unavailable"
    elif classified_position_count == eligible_position_count:
        coverage_status = "available"
    elif classified_position_count > 0:
        coverage_status = "partial"
    else:
        coverage_status = "unavailable"
    industry_rows = []
    for industry, market_value in industry_values.items():
        pct_of_invested = _pct(market_value, invested_market_value)
        pct_of_capital = _pct(market_value, capital_base)
        status = "partial" if coverage_status != "available" else "ok"
        if coverage_status == "available" and pct_of_capital is not None and pct_of_capital >= INDUSTRY_CONCENTRATION_ELEVATED_PCT:
            status = "elevated"
        elif coverage_status == "available" and pct_of_capital is not None and pct_of_capital >= INDUSTRY_CONCENTRATION_WATCH_PCT:
            status = "watch"
        industry_rows.append({
            "type": "industry",
            "key": industry,
            "symbols": sorted(
                risk["symbol"]
                for risk in position_risks
                if risk.get("industry") == industry
            ),
            "market_value": _round_money(market_value),
            "pct_of_invested": pct_of_invested,
            "pct_of_capital_base": pct_of_capital,
            "status": status,
        })
    industry_rows.sort(
        key=lambda row: (row["pct_of_capital_base"] or 0, row["key"]),
        reverse=True,
    )
    return {
        "by_symbol": rows,
        "by_industry": industry_rows,
        "industry_coverage": {
            "status": coverage_status,
            "classified_market_value": _round_money(classified_market_value),
            "pct_of_invested": coverage_pct,
            "eligible_position_count": eligible_position_count,
            "valued_position_count": valued_position_count,
            "classified_position_count": classified_position_count,
            "unvalued_position_count": eligible_position_count - valued_position_count,
            "unclassified_valued_position_count": (
                valued_position_count - classified_position_count
            ),
        },
        "industry_watch_threshold_pct": INDUSTRY_CONCENTRATION_WATCH_PCT,
        "industry_elevated_threshold_pct": INDUSTRY_CONCENTRATION_ELEVATED_PCT,
    }


def _is_industry_coverage_eligible(risk: dict[str, Any]) -> bool:
    quantity = _to_decimal(risk.get("quantity"))
    caveats = _as_mapping(risk.get("data_quality")).get("caveats")
    caveat_codes = {
        caveat.get("code")
        for caveat in caveats or []
        if isinstance(caveat, dict)
    }
    return quantity is not None and quantity > 0 and "unsupported_market" not in caveat_codes


def _build_correlation_risk(
    position_risks: list[dict[str, Any]],
    raw_data_by_symbol: dict[str, Any],
    *,
    invested_market_value: Decimal,
) -> dict[str, Any]:
    eligible_position_risks = [
        risk for risk in position_risks if _is_industry_coverage_eligible(risk)
    ]
    valued_position_count = sum(
        1
        for risk in eligible_position_risks
        if (_to_decimal(risk.get("market_value")) or Decimal("0")) > 0
    )
    possible_pair_count = (
        len(eligible_position_risks) * (len(eligible_position_risks) - 1) // 2
    )
    closes_by_symbol = {
        risk["symbol"]: _dated_closes(raw_data_by_symbol.get(risk["symbol"]))
        for risk in eligible_position_risks
    }
    pairs: list[dict[str, Any]] = []
    weighted_sum = 0.0
    total_pair_weight = 0.0
    for left_index, left in enumerate(eligible_position_risks):
        for right in eligible_position_risks[left_index + 1:]:
            left_symbol = left["symbol"]
            right_symbol = right["symbol"]
            left_market_value = _to_decimal(left.get("market_value"))
            right_market_value = _to_decimal(right.get("market_value"))
            if (
                left_market_value is None
                or left_market_value <= 0
                or right_market_value is None
                or right_market_value <= 0
            ):
                continue
            aligned_returns = _aligned_pair_returns(
                closes_by_symbol[left_symbol],
                closes_by_symbol[right_symbol],
            )
            if len(aligned_returns) < CORRELATION_MIN_OVERLAPPING_RETURNS:
                continue
            correlation = _pearson(
                [item[0] for item in aligned_returns],
                [item[1] for item in aligned_returns],
            )
            if correlation is None:
                continue
            left_weight = _decimal_ratio(
                left_market_value,
                invested_market_value,
            )
            right_weight = _decimal_ratio(
                right_market_value,
                invested_market_value,
            )
            pair_weight = (left_weight or 0.0) * (right_weight or 0.0)
            weighted_sum += correlation * pair_weight
            total_pair_weight += pair_weight
            pairs.append({
                "symbols": [left_symbol, right_symbol],
                "correlation": round(correlation, 4),
                "overlapping_return_count": len(aligned_returns),
                "combined_invested_weight_pct": round(
                    ((left_weight or 0.0) + (right_weight or 0.0)) * 100,
                    4,
                ),
                "status": _correlation_status(correlation),
            })
    pairs.sort(
        key=lambda row: (row["correlation"], row["combined_invested_weight_pct"]),
        reverse=True,
    )
    weighted_average = weighted_sum / total_pair_weight if total_pair_weight > 0 else None
    pair_coverage_pct = (
        round(len(pairs) / possible_pair_count * 100, 4)
        if possible_pair_count > 0
        else None
    )
    return {
        "status": (
            "available"
            if pair_coverage_pct == 100.0
            else "partial"
            if pairs
            else "insufficient_data"
        ),
        "minimum_overlapping_return_count": CORRELATION_MIN_OVERLAPPING_RETURNS,
        "eligible_position_count": len(eligible_position_risks),
        "valued_position_count": valued_position_count,
        "possible_pair_count": possible_pair_count,
        "eligible_pair_count": len(pairs),
        "pair_coverage_pct": pair_coverage_pct,
        "weighted_average_correlation": (
            round(weighted_average, 4) if weighted_average is not None else None
        ),
        "watch_threshold": CORRELATION_WATCH_THRESHOLD,
        "elevated_threshold": CORRELATION_ELEVATED_THRESHOLD,
        "pairs": pairs,
        "interpretation": "descriptive_co_movement_not_forward_prediction",
    }


def _dated_closes(raw_row: Any) -> dict[date, Decimal]:
    technical = _as_mapping(getattr(raw_row, "technical", None))
    closes_by_date: dict[date, Decimal] = {}
    history = technical.get("price_history")
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, dict):
                continue
            item_date = _parse_date(item.get("date"))
            close = _market_price_decimal(item.get("close"))
            if item_date is not None and close is not None:
                closes_by_date[item_date] = close
    recent_closes = technical.get("recent_closes")
    recent_dates = technical.get("recent_close_dates")
    if (
        isinstance(recent_closes, list)
        and isinstance(recent_dates, list)
        and len(recent_closes) == len(recent_dates)
    ):
        for raw_date, raw_close in zip(recent_dates, recent_closes, strict=True):
            item_date = _parse_date(raw_date)
            close = _market_price_decimal(raw_close)
            if item_date is not None and close is not None:
                closes_by_date[item_date] = close
    return closes_by_date


def _aligned_pair_returns(
    left_closes: dict[date, Decimal],
    right_closes: dict[date, Decimal],
) -> list[tuple[float, float]]:
    common_dates = sorted(set(left_closes) & set(right_closes))
    aligned_returns: list[tuple[float, float]] = []
    for previous_date, current_date in zip(common_dates, common_dates[1:]):
        left_return = _finite_float(
            left_closes[current_date] / left_closes[previous_date] - Decimal("1")
        )
        right_return = _finite_float(
            right_closes[current_date] / right_closes[previous_date] - Decimal("1")
        )
        if left_return is not None and right_return is not None:
            aligned_returns.append((left_return, right_return))
    return aligned_returns


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _pearson(left: list[float], right: list[float]) -> float | None:
    if (
        len(left) != len(right)
        or len(left) < 2
        or not all(isfinite(value) for value in (*left, *right))
    ):
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_delta = [value - left_mean for value in left]
    right_delta = [value - right_mean for value in right]
    denominator = sqrt(
        sum(value * value for value in left_delta)
        * sum(value * value for value in right_delta)
    )
    if denominator == 0 or not isfinite(denominator):
        return None
    correlation = sum(
        left_value * right_value
        for left_value, right_value in zip(left_delta, right_delta, strict=True)
    ) / denominator
    return correlation if isfinite(correlation) else None


def _finite_float(value: Decimal) -> float | None:
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return None
    return converted if isfinite(converted) else None


def _decimal_ratio(numerator: Decimal | None, denominator: Decimal) -> float | None:
    if numerator is None or denominator <= 0:
        return None
    return float(numerator / denominator)


def _correlation_status(correlation: float) -> str:
    if correlation >= CORRELATION_ELEVATED_THRESHOLD:
        return "elevated"
    if correlation >= CORRELATION_WATCH_THRESHOLD:
        return "watch"
    return "contained"


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


def _risk_budget_status(
    total_risk_pct: float | None,
    caveat_counts: dict[str, int],
    *,
    uses_invested_fallback: bool = False,
) -> dict[str, Any]:
    blocking_data_gap = any(caveat_counts.get(code, 0) for code in BLOCKING_DATA_GAP_CODES)
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
    if uses_invested_fallback:
        notes.append("尚未記錄可用現金，風險百分比暫以持股市值為分母。")
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
