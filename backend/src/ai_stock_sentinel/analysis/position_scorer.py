from __future__ import annotations


_NARRATIVES = {
    "profitable_safe": "目前獲利已脫離成本區，持股安全緩衝充足。",
    "at_risk": "股價正在成本價附近震盪，需密切觀察支撐是否有效。",
    "under_water": "目前處於套牢狀態，需評估停損或攤平策略。",
}


def compute_position_metrics(
    entry_price: float,
    current_price: float,
    support_20d: float,
) -> dict:
    """Rule-based position health calculation. No LLM."""
    profit_loss_pct = (current_price - entry_price) / entry_price * 100
    cost_buffer_to_support = entry_price - support_20d

    if profit_loss_pct >= 5:
        position_status = "profitable_safe"
    elif -5 <= profit_loss_pct <= 5:
        position_status = "at_risk"
    else:
        position_status = "under_water"

    if position_status == "profitable_safe" and current_price < support_20d:
        position_status = "at_risk"

    return {
        "profit_loss_pct": round(profit_loss_pct, 2),
        "cost_buffer_to_support": round(cost_buffer_to_support, 2),
        "position_status": position_status,
        "position_narrative": _NARRATIVES[position_status],
    }


def compute_trailing_stop(
    profit_loss_pct: float,
    entry_price: float,
    support_20d: float,
    ma10: float,
    high_20d: float,
    current_close: float,
    *,
    kd_zone: str | None = None,
    macd_bias: str | None = None,
    adx_trend_strength: str | None = None,
    adx_trend_direction: str | None = None,
    obv_signal: str | None = None,
    atr_value: float | None = None,
    mfi_signal: str | None = None,
) -> tuple[float, str]:
    """Return (trailing_stop_price, reason_string). Rule-based only."""
    bearish_volume = obv_signal in {"bearish_divergence", "price_volume_weak"}
    overheated = kd_zone == "overbought"
    money_flow_hot = mfi_signal == "overbought"
    trend_confirmed = (
        adx_trend_strength == "strong"
        and adx_trend_direction == "bullish"
        and obv_signal == "price_volume_confirm"
    )

    atr_floor = None
    if atr_value is not None and atr_value > 0:
        atr_floor = current_close - atr_value * 2

    if profit_loss_pct >= 20:
        stop = max(entry_price, support_20d, ma10)
        if atr_floor is not None:
            stop = max(stop, atr_floor)
        return round(stop, 2), "獲利超過 20%，防守位上移至 MA10、支撐位與成本價較高值"

    if profit_loss_pct >= 10 and (bearish_volume or macd_bias == "bearish" or overheated or money_flow_hot):
        stop = max(entry_price, support_20d, ma10)
        if atr_floor is not None:
            stop = max(stop, atr_floor)
        return round(stop, 2), "獲利超過 10% 且動能出現降溫訊號，防守位上移以保護獲利"

    # Trailing stop at new high (takes priority over breakeven check)
    if current_close >= high_20d:
        stop = max(ma10, support_20d)
        if trend_confirmed:
            stop = max(support_20d, min(ma10, current_close * 0.95))
            if atr_floor is not None:
                stop = max(stop, atr_floor)
            return round(stop, 2), "移動停利：股價創近 20 日新高且趨勢量能確認，防守位參考 MA10 與 5% 回撤"
        if atr_floor is not None:
            stop = max(stop, atr_floor)
        return round(stop, 2), "移動停利：股價創近 20 日新高，防守位上移至 MA10 與支撐位較高值"

    # Breakeven protection
    if profit_loss_pct >= 5:
        stop = max(entry_price, support_20d)
        return round(stop, 2), "獲利超過 5%，停損位上移至成本價保本"

    # Underwater defence
    if profit_loss_pct < -5:
        stop_pct = 0.95 if bearish_volume or macd_bias == "bearish" else 0.93
        stop = entry_price * stop_pct
        if atr_value is not None and atr_value > 0:
            stop = max(stop, entry_price - atr_value * 1.5)
        loss_pct = int(round((1 - stop_pct) * 100))
        return round(stop, 2), f"套牢防守：停損位設於成本價 -{loss_pct}%"

    # At-risk: hold support
    return round(support_20d, 2), "成本邊緣震盪，防守位參考近 20 日支撐"


def compute_recommended_action(
    flow_label: str,
    profit_loss_pct: float,
    technical_signal: str,
    current_close: float,
    trailing_stop: float,
    position_status: str,
    *,
    kd_signal: str | None = None,
    kd_zone: str | None = None,
    macd_bias: str | None = None,
    bollinger_position: str | None = None,
    adx_trend_strength: str | None = None,
    adx_trend_direction: str | None = None,
    obv_signal: str | None = None,
    mfi_signal: str | None = None,
    donchian_position: str | None = None,
) -> tuple[str, str | None]:
    """Return (action, exit_reason). action: Hold | Trim | Exit."""
    bearish_volume = obv_signal in {"bearish_divergence", "price_volume_weak"}
    bearish_money_flow = mfi_signal == "bearish_flow"
    bearish_momentum = macd_bias == "bearish" or kd_signal == "bearish_cross" or bearish_money_flow
    overheat = (kd_zone == "overbought" and bollinger_position == "near_upper") or mfi_signal == "overbought"
    channel_breakdown = donchian_position == "breakdown_down"
    strong_continuation = (
        adx_trend_strength == "strong"
        and adx_trend_direction == "bullish"
        and obv_signal == "price_volume_confirm"
        and macd_bias == "bullish"
    )

    # Rule 1: distribution + profitable → Trim
    if flow_label == "distribution" and profit_loss_pct > 0:
        return "Trim", "法人持續出貨，建議逢高分批減碼保護獲利"

    # Rule 2: distribution + loss → Exit
    if flow_label == "distribution" and profit_loss_pct <= 0:
        return "Exit", "法人出貨且持股虧損，建議停損出場"

    # Rule 3: bearish + price below trailing stop → Exit
    if technical_signal == "bearish" and current_close < trailing_stop:
        return "Exit", f"技術面轉空且收盤價 {current_close} 跌破防守線 {trailing_stop}，建議出場"

    if current_close < trailing_stop and (bearish_volume or bearish_momentum):
        return "Exit", f"收盤價 {current_close} 跌破防守線 {trailing_stop}，且動能或量價訊號轉弱，建議出場"

    if channel_breakdown and (bearish_volume or bearish_momentum):
        return "Exit", "跌破 Donchian 近期低檔區間，且量價或資金流同步轉弱，建議出場"

    # Rule 4: deep underwater → Exit
    if position_status == "under_water" and profit_loss_pct < -10:
        return "Exit", f"深度套牢（{profit_loss_pct:.1f}%），建議停損出場"

    if profit_loss_pct > 0 and bearish_volume:
        return "Trim", "OBV 出現量價背離或量能轉弱，建議先分批減碼保護獲利"

    if profit_loss_pct > 0 and bearish_money_flow:
        return "Trim", "MFI 顯示資金流轉弱，建議先分批減碼保護獲利"

    if profit_loss_pct >= 10 and overheat and not strong_continuation:
        return "Trim", "獲利已達 10% 且 KD/布林位置偏高，趨勢續航證據不足，建議部分獲利了結"

    if profit_loss_pct > 0 and overheat and bearish_momentum:
        return "Trim", "短線過熱且動能轉弱，建議分批減碼降低回撤風險"

    return "Hold", None
