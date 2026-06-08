from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.metrics import calc_rsi, ma
from ai_stock_sentinel.db.models import StockRawData, UserPortfolio


MAX_DETECTED_EVENTS = 8


def build_trade_review_payload(db: Session, portfolio: UserPortfolio) -> tuple[dict, dict]:
    rows = db.execute(
        select(StockRawData)
        .where(
            StockRawData.symbol == portfolio.symbol,
            StockRawData.record_date <= portfolio.exit_date,
        )
        .order_by(StockRawData.record_date.asc())
    ).scalars().all()

    data_quality = _build_data_quality(portfolio, rows)
    path_metrics = _build_path_metrics(portfolio, rows, data_quality)
    entry_indicators = _build_point_in_time_indicators(
        rows,
        portfolio.entry_date,
        _number(portfolio.entry_price),
        "entry",
        data_quality,
    )
    exit_indicators = _build_point_in_time_indicators(
        rows,
        portfolio.exit_date,
        _number(portfolio.exit_price),
        "exit",
        data_quality,
    )
    trade_result = {
        **path_metrics,
        "entry_indicators": entry_indicators,
        "exit_indicators": exit_indicators,
    }
    entry_market_regime = _classify_market_regime(rows, portfolio.entry_date)
    exit_market_regime = _classify_market_regime(rows, portfolio.exit_date)
    entry_indicators["market_regime"] = entry_market_regime
    exit_indicators["market_regime"] = exit_market_regime
    detected_events = _detect_holding_events(portfolio, rows)
    entry_review = _build_entry_review(portfolio, rows, entry_indicators, entry_market_regime)
    holding_review = _build_holding_review(portfolio, path_metrics, detected_events, exit_market_regime)
    exit_review = _build_exit_review(
        portfolio,
        rows,
        exit_indicators,
        path_metrics,
        detected_events,
        exit_market_regime,
    )
    operation_review = _build_operation_review(portfolio, entry_review, holding_review, exit_review, exit_market_regime)

    review_result = {
        "data_quality": data_quality,
        "trade_result": trade_result,
        "entry_review": entry_review,
        "holding_review": holding_review,
        "exit_review": exit_review,
        "operation_review": operation_review,
    }
    evidence_payload = {
        "trade": _serialize_trade(portfolio),
        "position_group_id": portfolio.position_group_id,
        "path_metrics": path_metrics,
        "entry_indicators": entry_indicators,
        "exit_indicators": exit_indicators,
        "detected_events": detected_events,
        "data_quality": data_quality,
        "source_data": {
            "symbol": portfolio.symbol,
            "rows_up_to_exit": len(rows),
            "holding_rows": len(_holding_rows(portfolio, rows)),
            "first_record_date": _date_to_iso(rows[0].record_date) if rows else None,
            "last_record_date": _date_to_iso(rows[-1].record_date) if rows else None,
        },
    }
    return review_result, evidence_payload


def _build_entry_review(
    portfolio: UserPortfolio,
    rows: list[StockRawData],
    entry_indicators: dict[str, Any],
    market_regime: str,
) -> dict[str, Any]:
    point_rows = _rows_up_to(rows, portfolio.entry_date)
    entry_close = _last_close(point_rows)
    entry_price = _number(portfolio.entry_price)
    review_price = entry_close if entry_close is not None else entry_price
    valid_closes = [_close(row) for row in point_rows]
    valid_closes = [close for close in valid_closes if close is not None]
    supporting: list[str] = []
    conflicting: list[str] = []
    caveats: list[str] = []
    classification = "range_entry"

    if market_regime == "insufficient_data" or review_price is None or len(valid_closes) < 20:
        caveats.append("Entry review has fewer than 20 usable price rows available at entry date.")
        return _review_section("insufficient_data", "low", market_regime, supporting, conflicting, caveats)

    entry_vs_ma20 = _number(entry_indicators.get("entry_vs_ma20_pct"))
    entry_vs_ma60 = _number(entry_indicators.get("entry_vs_ma60_pct"))
    rsi14 = _number(entry_indicators.get("rsi14"))
    volume_ratio = _number(entry_indicators.get("volume_ratio"))
    previous_closes = [_close(row) for row in point_rows if row.record_date < portfolio.entry_date]
    previous_closes = [close for close in previous_closes if close is not None]
    recent_high = max(previous_closes[-20:]) if previous_closes else None

    if recent_high is not None and entry_close is not None and entry_close >= recent_high * 1.01:
        supporting.append("Entry close broke above the recent 20-row high.")
        if volume_ratio is not None and volume_ratio >= 1.15:
            supporting.append("Entry volume was above its 20-row average.")
            classification = "breakout_entry"
        else:
            conflicting.append("Breakout did not have clear volume confirmation.")
            classification = "range_entry"

    if entry_vs_ma20 is not None and entry_vs_ma20 >= 8 and rsi14 is not None and rsi14 >= 70:
        supporting.append("Entry price was extended above MA20 with elevated RSI.")
        if classification == "breakout_entry" and volume_ratio is not None and volume_ratio >= 1.5:
            conflicting.append("Momentum was extended, so breakout quality is less certain.")
        else:
            classification = "chase_entry"

    if market_regime == "downtrend" or _is_negative(entry_vs_ma20, -3) and _is_negative(entry_vs_ma60, -3):
        supporting.append("Entry price was below key moving averages or in a downtrend.")
        classification = "weak_entry"

    if classification == "range_entry" and market_regime == "range_bound":
        supporting.append("Market regime was range-bound at entry.")

    if classification == "range_entry" and (_near_zero(entry_vs_ma20, 3) or _near_zero(entry_vs_ma60, 3)):
        supporting.append("Entry was near MA20 or MA60 after a pullback.")
        classification = "pullback_entry"

    if classification == "range_entry" and market_regime == "uptrend" and not supporting:
        supporting.append("Entry aligned with an uptrend but lacked a clear breakout or pullback signal.")

    if market_regime == "strong_momentum" and classification == "chase_entry":
        caveats.append("Strong momentum regimes can remain extended longer than simple overbought rules imply.")
    if market_regime == "high_volatility":
        caveats.append("High daily ranges reduce confidence in simple moving-average entry rules.")
    confidence = _confidence_for(supporting, conflicting, caveats)
    return _review_section(classification, confidence, market_regime, supporting, conflicting, caveats)


def _build_holding_review(
    portfolio: UserPortfolio,
    path_metrics: dict[str, Any],
    detected_events: list[dict[str, Any]],
    market_regime: str,
) -> dict[str, Any]:
    caveats: list[str] = []
    holding_days = portfolio.holding_days
    if holding_days is None and portfolio.exit_date is not None:
        holding_days = (portfolio.exit_date - portfolio.entry_date).days
    if holding_days is not None and holding_days < 3:
        caveats.append("Holding period was very short, so trend-event review is low confidence.")
    if path_metrics.get("max_profit_pct") is None:
        caveats.append("Holding path has insufficient usable prices for full path review.")
    risk_events = [event for event in detected_events if event["type"] != "new_high_continuation"]
    supporting = [f"Detected {event['type']} on {event['date']}." for event in detected_events[:3]]
    confidence = "low" if caveats else "medium"
    return {
        "market_regime": market_regime,
        "confidence": confidence,
        "detected_events": detected_events,
        "event_count": len(detected_events),
        "risk_event_count": len(risk_events),
        "supporting_signals": supporting,
        "conflicting_signals": [],
        "caveats": caveats,
        "summary": "Chronological holding review uses capped technical events only.",
    }


def _build_exit_review(
    portfolio: UserPortfolio,
    rows: list[StockRawData],
    exit_indicators: dict[str, Any],
    path_metrics: dict[str, Any],
    detected_events: list[dict[str, Any]],
    market_regime: str,
) -> dict[str, Any]:
    point_rows = _rows_up_to(rows, portfolio.exit_date)
    valid_closes = [_close(row) for row in point_rows]
    valid_closes = [close for close in valid_closes if close is not None]
    exit_price = _number(portfolio.exit_price)
    realized_return_pct = _number(portfolio.realized_return_pct)
    if realized_return_pct is None:
        realized_return_pct = _number(path_metrics.get("realized_return_pct"))
    supporting: list[str] = []
    conflicting: list[str] = []
    caveats: list[str] = []
    classification = "technical_break_exit"

    if market_regime == "insufficient_data" or exit_price is None or len(valid_closes) < 20:
        caveats.append("Exit review has fewer than 20 usable price rows available at exit date.")
        return _review_section("insufficient_data", "low", market_regime, supporting, conflicting, caveats)

    exit_vs_ma20 = _number(exit_indicators.get("exit_vs_ma20_pct"))
    exit_vs_ma60 = _number(exit_indicators.get("exit_vs_ma60_pct"))
    profit_giveback_pct = _number(path_metrics.get("profit_giveback_pct"))
    max_drawdown_pct = _number(path_metrics.get("max_drawdown_pct"))
    event_types = {event["type"] for event in detected_events}
    has_break_event = bool(event_types & {"ma20_break", "ma60_break", "support_break"})

    if realized_return_pct is not None and realized_return_pct <= -8 and max_drawdown_pct is not None and max_drawdown_pct <= -12:
        supporting.append("Exit followed a large realized loss and deep holding-period drawdown.")
        classification = "late_stop_exit"
    elif realized_return_pct is not None and realized_return_pct <= -3 and (has_break_event or _is_negative(exit_vs_ma20, -2)):
        supporting.append("Exit limited a losing trade after technical weakness appeared.")
        classification = "stop_loss_exit"
    elif realized_return_pct is not None and realized_return_pct >= 5 and (
        has_break_event or _is_negative(exit_vs_ma20, -1) or (profit_giveback_pct is not None and profit_giveback_pct >= 3)
    ):
        supporting.append("Exit protected realized gains after momentum cooled or giveback appeared.")
        classification = "profit_protection_exit"
    elif realized_return_pct is not None and 0 < realized_return_pct < 5 and not has_break_event and market_regime in {"uptrend", "strong_momentum"}:
        supporting.append("Exit captured a small gain while trend evidence was still constructive.")
        classification = "early_profit_exit"
    elif realized_return_pct is not None and realized_return_pct < 0 and _near_holding_low(exit_price, path_metrics):
        supporting.append("Exit occurred near the holding-period low after a loss.")
        classification = "panic_exit"
    else:
        supporting.append("Exit aligned with moving-average or support weakness available by exit date.")
        classification = "technical_break_exit"

    if has_break_event:
        supporting.append("Holding review detected a moving-average or support break before exit.")
    if _is_positive(exit_vs_ma20, 2) and classification in {"stop_loss_exit", "technical_break_exit", "panic_exit"}:
        conflicting.append("Exit price was still above MA20, weakening the technical-break interpretation.")
    if _is_positive(exit_vs_ma60, 2) and classification in {"technical_break_exit", "panic_exit"}:
        conflicting.append("Exit price was still above MA60, so longer-term trend damage was not confirmed.")
    if market_regime == "range_bound" and classification == "technical_break_exit":
        caveats.append("Range-bound regimes make single moving-average breaks noisier.")
    if market_regime == "high_volatility":
        caveats.append("High daily ranges reduce confidence in simple breakdown exit rules.")
    confidence = _confidence_for(supporting, conflicting, caveats)
    return _review_section(classification, confidence, market_regime, supporting, conflicting, caveats)


def _build_operation_review(
    portfolio: UserPortfolio,
    entry_review: dict[str, Any],
    holding_review: dict[str, Any],
    exit_review: dict[str, Any],
    market_regime: str,
) -> dict[str, Any]:
    caveats: list[str] = []
    supporting: list[str] = ["Review scope is the current closed portfolio row only."]
    conflicting: list[str] = []
    if entry_review["classification"] == "insufficient_data" or exit_review["classification"] == "insufficient_data":
        caveats.append("One or more review phases have insufficient market data.")
    if holding_review["risk_event_count"]:
        supporting.append("Holding review found risk events before exit.")
    if exit_review["classification"] in {"late_stop_exit", "panic_exit"}:
        conflicting.append("Exit review suggests execution may have been reactive or delayed.")
    confidence = _confidence_for(supporting, conflicting, caveats)
    return {
        "classification": "rule_based_trade_review",
        "confidence": confidence,
        "market_regime": market_regime,
        "supporting_signals": supporting,
        "conflicting_signals": conflicting,
        "caveats": caveats,
        "reviewed_portfolio_id": portfolio.id,
        "position_group_id": portfolio.position_group_id,
        "scope": "current_closed_row_only",
        "summary": "Operation review preserves the existing persistence/API boundary and does not aggregate same-group rows.",
    }


def _review_section(
    classification: str,
    confidence: str,
    market_regime: str,
    supporting_signals: list[str],
    conflicting_signals: list[str],
    caveats: list[str],
) -> dict[str, Any]:
    return {
        "classification": classification,
        "confidence": confidence,
        "market_regime": market_regime,
        "supporting_signals": supporting_signals,
        "conflicting_signals": conflicting_signals,
        "caveats": caveats,
        "summary": _summary_for(classification),
    }


def _summary_for(classification: str) -> str:
    summaries = {
        "breakout_entry": "Entry leaned breakout with price and volume confirmation.",
        "pullback_entry": "Entry leaned pullback near a moving-average area.",
        "chase_entry": "Entry was technically extended based on data available at entry.",
        "weak_entry": "Entry occurred against weak trend or moving-average structure.",
        "range_entry": "Entry was inside a range without a decisive breakout signal.",
        "profit_protection_exit": "Exit protected profit after cooling or giveback evidence.",
        "stop_loss_exit": "Exit reduced loss after technical weakness appeared.",
        "late_stop_exit": "Exit came after a large loss or delayed response to weakness.",
        "early_profit_exit": "Exit took a small profit before clear trend damage was visible.",
        "panic_exit": "Exit occurred near a short-term low without strong confirmation.",
        "technical_break_exit": "Exit aligned with technical weakness available by exit date.",
        "insufficient_data": "Not enough point-in-time data was available for classification.",
    }
    return summaries.get(classification, "Rule-based review completed.")


def _serialize_trade(portfolio: UserPortfolio) -> dict[str, Any]:
    realized_return_pct = _number(portfolio.realized_return_pct)
    return {
        "id": portfolio.id,
        "position_group_id": portfolio.position_group_id,
        "symbol": portfolio.symbol,
        "entry_price": _number(portfolio.entry_price),
        "entry_date": _date_to_iso(portfolio.entry_date),
        "quantity": portfolio.quantity,
        "exit_date": _date_to_iso(portfolio.exit_date),
        "exit_price": _number(portfolio.exit_price),
        "exit_quantity": portfolio.exit_quantity,
        "realized_pnl": _number(portfolio.realized_pnl),
        "realized_return_pct": realized_return_pct,
        "return_pct": realized_return_pct,
        "holding_days": portfolio.holding_days,
    }


def _build_data_quality(portfolio: UserPortfolio, rows: list[StockRawData]) -> dict[str, Any]:
    notes: list[str] = []
    insufficient_data: list[str] = []
    if not rows:
        notes.append("No StockRawData rows found for symbol up to exit_date.")
        insufficient_data.extend([
            "holding_path_prices",
            "entry_ma20",
            "entry_ma60",
            "entry_rsi14",
            "entry_volume_ratio",
            "exit_ma20",
            "exit_ma60",
            "exit_rsi14",
            "exit_volume_ratio",
        ])
    elif not _holding_rows(portfolio, rows):
        notes.append("No StockRawData rows found within entry_date..exit_date holding window.")
        insufficient_data.append("holding_path_prices")
    return {
        "status": "insufficient" if insufficient_data else "ok",
        "notes": notes,
        "insufficient_data": insufficient_data,
    }


def _build_path_metrics(
    portfolio: UserPortfolio,
    rows: list[StockRawData],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    entry_price = _number(portfolio.entry_price)
    exit_price = _number(portfolio.exit_price)
    holding_days = portfolio.holding_days
    if holding_days is None and portfolio.exit_date is not None:
        holding_days = (portfolio.exit_date - portfolio.entry_date).days

    metrics = {
        "entry_date": _date_to_iso(portfolio.entry_date),
        "exit_date": _date_to_iso(portfolio.exit_date),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "realized_pnl": _number(portfolio.realized_pnl),
        "realized_return_pct": _number(portfolio.realized_return_pct),
        "holding_days": holding_days,
        "max_profit_pct": None,
        "max_drawdown_pct": None,
        "profit_giveback_pct": None,
        "highest_close_during_holding": None,
        "lowest_close_during_holding": None,
    }
    if metrics["realized_return_pct"] is None and entry_price and exit_price is not None:
        metrics["realized_return_pct"] = _round_pct((exit_price - entry_price) / entry_price * 100)

    closes = [_close(row) for row in _holding_rows(portfolio, rows)]
    valid_closes = [close for close in closes if close is not None]
    if not valid_closes:
        _add_insufficient(data_quality, "holding_path_prices", "Holding window has no usable close prices.")
        return metrics
    if not entry_price:
        _add_insufficient(data_quality, "entry_price", "Entry price is missing or zero.")
        return metrics

    highest_close = max(valid_closes)
    lowest_close = min(valid_closes)
    metrics["highest_close_during_holding"] = _round_price(highest_close)
    metrics["lowest_close_during_holding"] = _round_price(lowest_close)
    metrics["max_profit_pct"] = _round_pct((highest_close - entry_price) / entry_price * 100)
    metrics["max_drawdown_pct"] = _round_pct((lowest_close - entry_price) / entry_price * 100)
    if exit_price is not None:
        metrics["profit_giveback_pct"] = _round_pct(max(0.0, (highest_close - exit_price) / entry_price * 100))
    return metrics


def _build_point_in_time_indicators(
    rows: list[StockRawData],
    as_of: date | None,
    trade_price: float | None,
    label: str,
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    if as_of is None:
        _add_insufficient(data_quality, f"{label}_date", f"{label}_date is missing.")
        return {}
    point_rows = [row for row in rows if row.record_date <= as_of]
    closes = [_close(row) for row in point_rows]
    volumes = [_volume(row) for row in point_rows]
    valid_closes = [close for close in closes if close is not None]
    valid_volumes = [volume for volume in volumes if volume is not None]

    result: dict[str, Any] = {"as_of_date": _date_to_iso(as_of)}
    ma20 = ma(valid_closes, 20)
    ma60 = ma(valid_closes, 60)
    rsi14 = calc_rsi(valid_closes, period=14)
    result["ma20"] = _round_price(ma20)
    result["ma60"] = _round_price(ma60)
    result["rsi14"] = _round_pct(rsi14)
    result["entry_vs_ma20_pct" if label == "entry" else "exit_vs_ma20_pct"] = _distance_pct(trade_price, ma20)
    result["entry_vs_ma60_pct" if label == "entry" else "exit_vs_ma60_pct"] = _distance_pct(trade_price, ma60)

    volume_ratio = None
    if len(valid_volumes) >= 20:
        avg_volume_20 = sum(valid_volumes[-20:]) / 20
        if avg_volume_20:
            volume_ratio = valid_volumes[-1] / avg_volume_20
    result["volume_ratio"] = _round_pct(volume_ratio)

    required_lengths = {"ma20": 20, "ma60": 60, "rsi14": 15, "volume_ratio": 20}
    for key, required_length in required_lengths.items():
        if len(valid_closes if key != "volume_ratio" else valid_volumes) < required_length:
            _add_insufficient(
                data_quality,
                f"{label}_{key}",
                f"Only {len(valid_closes if key != 'volume_ratio' else valid_volumes)} rows available for {label} {key}.",
            )
    return result


def _holding_rows(portfolio: UserPortfolio, rows: list[StockRawData]) -> list[StockRawData]:
    if portfolio.exit_date is None:
        return []
    return [row for row in rows if portfolio.entry_date <= row.record_date <= portfolio.exit_date]


def _rows_up_to(rows: list[StockRawData], as_of: date | None) -> list[StockRawData]:
    if as_of is None:
        return []
    return [row for row in rows if row.record_date <= as_of]


def _classify_market_regime(rows: list[StockRawData], as_of: date | None) -> str:
    point_rows = _rows_up_to(rows, as_of)
    closes = [_close(row) for row in point_rows]
    valid_closes = [close for close in closes if close is not None]
    if len(valid_closes) < 20:
        return "insufficient_data"

    recent_closes = valid_closes[-20:]
    latest_close = recent_closes[-1]
    ma20 = ma(valid_closes, 20)
    ma60 = ma(valid_closes, 60)
    rsi14 = calc_rsi(valid_closes, period=14)
    recent_return_pct = _distance_pct(latest_close, recent_closes[0])
    recent_range_pct = _distance_pct(max(recent_closes), min(recent_closes))
    avg_daily_range_pct = _average_daily_range_pct(point_rows[-20:])

    if avg_daily_range_pct is not None and avg_daily_range_pct >= 6:
        return "high_volatility"
    if ma20 is not None and rsi14 is not None and latest_close > ma20 and rsi14 >= 75:
        if _is_positive(_distance_pct(latest_close, ma20), 6) or _is_positive(recent_return_pct, 12):
            return "strong_momentum"
    if ma20 is not None and latest_close < ma20 and (ma60 is None or ma20 < ma60):
        return "downtrend"
    if ma20 is not None and latest_close > ma20 and (ma60 is None or ma20 > ma60):
        return "uptrend"
    if recent_range_pct is not None and recent_range_pct <= 12:
        return "range_bound"
    return "range_bound"


def _detect_holding_events(portfolio: UserPortfolio, rows: list[StockRawData]) -> list[dict[str, Any]]:
    holding_rows = _holding_rows(portfolio, rows)
    if len(holding_rows) < 2 or not _number(portfolio.entry_price):
        return []

    events: list[dict[str, Any]] = []
    entry_price = _number(portfolio.entry_price)
    running_high = entry_price
    prior_closes: list[float] = []
    prior_volumes: list[float] = []
    previous_close: float | None = None
    previous_ma20: float | None = None
    previous_ma60: float | None = None

    for row in rows:
        close = _close(row)
        volume = _volume(row)
        if close is None:
            continue
        in_holding_window = portfolio.entry_date <= row.record_date <= portfolio.exit_date
        ma20 = ma(prior_closes + [close], 20)
        ma60 = ma(prior_closes + [close], 60)
        rsi14 = calc_rsi(prior_closes + [close], period=14)
        avg_volume_20 = sum(prior_volumes[-19:] + [volume]) / len(prior_volumes[-19:] + [volume]) if volume is not None else None
        recent_support = min(prior_closes[-10:]) if len(prior_closes) >= 10 else None

        if in_holding_window and row.record_date > portfolio.entry_date:
            if running_high is not None and close > running_high and entry_price is not None and close >= entry_price * 1.05:
                _append_event(events, row, "new_high_continuation", close, "Close made a new holding-period high at least 5% above entry.")
            if previous_close is not None and close < previous_close and volume is not None and avg_volume_20 and volume / avg_volume_20 >= 1.5:
                _append_event(events, row, "volume_down_day", close, "Down day volume was at least 1.5x recent average.")
            if ma20 is not None and previous_ma20 is not None and previous_close is not None and previous_close >= previous_ma20 and close < ma20:
                _append_event(events, row, "ma20_break", close, "Close crossed below MA20 during the holding period.")
            if ma60 is not None and previous_ma60 is not None and previous_close is not None and previous_close >= previous_ma60 and close < ma60:
                _append_event(events, row, "ma60_break", close, "Close crossed below MA60 during the holding period.")
            if recent_support is not None and close < recent_support * 0.98:
                _append_event(events, row, "support_break", close, "Close broke below recent 10-row support by more than 2%.")
            if rsi14 is not None and rsi14 >= 75:
                _append_event(events, row, "rsi_overheated", close, "RSI14 was 75 or higher during the holding period.")
            if running_high is not None and entry_price is not None and running_high > entry_price and close <= running_high * 0.95:
                _append_event(events, row, "profit_giveback", close, "Close gave back at least 5% from the holding-period high.")
            if len(events) >= MAX_DETECTED_EVENTS:
                return events

        running_high = max(running_high, close) if running_high is not None else close
        prior_closes.append(close)
        if volume is not None:
            prior_volumes.append(volume)
        previous_close = close
        previous_ma20 = ma20
        previous_ma60 = ma60
    return events


def _append_event(events: list[dict[str, Any]], row: StockRawData, event_type: str, close: float, summary: str) -> None:
    if len(events) >= MAX_DETECTED_EVENTS:
        return
    if any(event["type"] == event_type and event["date"] == _date_to_iso(row.record_date) for event in events):
        return
    events.append({
        "date": _date_to_iso(row.record_date),
        "type": event_type,
        "summary": summary,
        "evidence": {"close": _round_price(close)},
    })


def _last_close(rows: list[StockRawData]) -> float | None:
    for row in reversed(rows):
        close = _close(row)
        if close is not None:
            return close
    return None


def _close(row: StockRawData) -> float | None:
    ohlcv = _technical_section(row, "ohlcv")
    return _number(ohlcv.get("close"))


def _high(row: StockRawData) -> float | None:
    ohlcv = _technical_section(row, "ohlcv")
    return _number(ohlcv.get("high"))


def _low(row: StockRawData) -> float | None:
    ohlcv = _technical_section(row, "ohlcv")
    return _number(ohlcv.get("low"))


def _volume(row: StockRawData) -> float | None:
    ohlcv = _technical_section(row, "ohlcv")
    return _number(ohlcv.get("volume"))


def _technical_section(row: StockRawData, key: str) -> dict[str, Any]:
    if not isinstance(row.technical, dict):
        return {}
    section = row.technical.get(key)
    return section if isinstance(section, dict) else {}


def _distance_pct(value: float | None, baseline: float | None) -> float | None:
    if value is None or not baseline:
        return None
    return _round_pct((value - baseline) / baseline * 100)


def _average_daily_range_pct(rows: list[StockRawData]) -> float | None:
    ranges: list[float] = []
    for row in rows:
        high = _high(row)
        low = _low(row)
        close = _close(row)
        if high is not None and low is not None and close:
            ranges.append((high - low) / close * 100)
    if not ranges:
        return None
    return _round_pct(sum(ranges) / len(ranges))


def _near_zero(value: float | None, threshold: float) -> bool:
    return value is not None and abs(value) <= threshold


def _is_positive(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _is_negative(value: float | None, threshold: float) -> bool:
    return value is not None and value <= threshold


def _near_holding_low(exit_price: float, path_metrics: dict[str, Any]) -> bool:
    low = _number(path_metrics.get("lowest_close_during_holding"))
    if low is None:
        return False
    return exit_price <= low * 1.02


def _confidence_for(supporting: list[str], conflicting: list[str], caveats: list[str]) -> str:
    if len(supporting) >= 2 and not conflicting and not caveats:
        return "high"
    if supporting and len(conflicting) <= 1:
        return "medium"
    return "low"


def _add_insufficient(data_quality: dict[str, Any], key: str, note: str) -> None:
    insufficient = data_quality.setdefault("insufficient_data", [])
    notes = data_quality.setdefault("notes", [])
    if key not in insufficient:
        insufficient.append(key)
    if note not in notes:
        notes.append(note)
    data_quality["status"] = "insufficient"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_pct(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _round_price(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _date_to_iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value
