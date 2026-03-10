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

    if profit_loss_pct > 5 and entry_price > support_20d:
        position_status = "profitable_safe"
    elif -5 <= profit_loss_pct <= 5:
        position_status = "at_risk"
    else:
        position_status = "under_water"

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
) -> tuple[float, str]:
    """Return (trailing_stop_price, reason_string). Rule-based only."""
    # Trailing stop at new high (takes priority over breakeven check)
    if current_close >= high_20d:
        stop = max(ma10, support_20d)
        return round(stop, 2), "移動停利：股價創近 20 日新高，防守位上移至 MA10 與支撐位較高值"

    # Breakeven protection
    if profit_loss_pct >= 5:
        stop = max(entry_price, support_20d)
        return round(stop, 2), "獲利超過 5%，停損位上移至成本價保本"

    # Underwater defence
    if profit_loss_pct < -5:
        stop = entry_price * 0.93
        return round(stop, 2), "套牢防守：停損位設於成本價 -7%"

    # At-risk: hold support
    return round(support_20d, 2), "成本邊緣震盪，防守位參考近 20 日支撐"


def compute_recommended_action(
    flow_label: str,
    profit_loss_pct: float,
    technical_signal: str,
    current_close: float,
    trailing_stop: float,
    position_status: str,
) -> tuple[str, str | None]:
    """Return (action, exit_reason). action: Hold | Trim | Exit."""
    # Rule 1: distribution + profitable → Trim
    if flow_label == "distribution" and profit_loss_pct > 0:
        return "Trim", "法人持續出貨，建議逢高分批減碼保護獲利"

    # Rule 2: distribution + loss → Exit
    if flow_label == "distribution" and profit_loss_pct <= 0:
        return "Exit", "法人出貨且持股虧損，建議停損出場"

    # Rule 3: bearish + price below trailing stop → Exit
    if technical_signal == "bearish" and current_close < trailing_stop:
        return "Exit", f"技術面轉空且收盤價 {current_close} 跌破防守線 {trailing_stop}，建議出場"

    # Rule 4: deep underwater → Exit
    if position_status == "under_water" and profit_loss_pct < -10:
        return "Exit", f"深度套牢（{profit_loss_pct:.1f}%），建議停損出場"

    return "Hold", None
