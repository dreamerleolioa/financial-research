"""generate_technical_context：純 rule-based，把數值轉為敘事字串。

不呼叫 LLM，僅依據技術指標與籌碼數值，以固定規則輸出中文敘事，
供 analyze_node 的 Prompt 使用。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ai_stock_sentinel.analysis.metrics import adx, atr, bollinger_bands, calc_bias, calc_rsi, donchian_channel, ma, macd, mfi, obv, stochastic_kd


# ─── 敘事生成規則 ────────────────────────────────────────────────────────────

def _bias_narrative(bias: float | None) -> str:
    if bias is None:
        return "乖離率資料不足，無法判斷。"
    if bias > 10:
        return f"乖離率 {bias:.1f}%，明顯高估，短線過熱風險高，宜觀望等回調。"
    if bias > 5:
        return f"乖離率 {bias:.1f}%，偏高，追高風險存在。"
    if bias < -10:
        return f"乖離率 {bias:.1f}%，明顯低估，超跌區間，留意超跌反彈機會。"
    if bias < -5:
        return f"乖離率 {bias:.1f}%，偏低，股價相對均線折價，可留意底部訊號。"
    return f"乖離率 {bias:.1f}%，股價接近均線，無明顯高低估訊號。"


def _rsi_narrative(rsi: float | None) -> str:
    if rsi is None:
        return "RSI 資料不足（需至少 15 個交易日收盤價）。"
    if rsi >= 80:
        return f"RSI {rsi:.1f}，嚴重超買，短線拉回機率高。"
    if rsi >= 70:
        return f"RSI {rsi:.1f}，超買區間，動能強但追高需謹慎。"
    if rsi <= 20:
        return f"RSI {rsi:.1f}，嚴重超賣，超跌反彈機率高。"
    if rsi <= 30:
        return f"RSI {rsi:.1f}，超賣區間，可留意底部反彈機會。"
    if rsi >= 50:
        return f"RSI {rsi:.1f}，動能偏多，多方格局。"
    return f"RSI {rsi:.1f}，動能偏空，多方力道不足。"


def _ma_narrative(close: float, ma5: float | None, ma20: float | None, ma60: float | None) -> str:
    parts: list[str] = []
    if ma5 is not None:
        tag = "站上" if close > ma5 else "跌破"
        parts.append(f"MA5（{ma5:.1f}）{tag}")
    if ma20 is not None:
        tag = "站上" if close > ma20 else "跌破"
        parts.append(f"MA20（{ma20:.1f}）{tag}")
    if ma60 is not None:
        tag = "站上" if close > ma60 else "跌破"
        parts.append(f"MA60（{ma60:.1f}）{tag}")
    if not parts:
        return "均線資料不足。"
    return "收盤價 " + "、".join(parts) + "。"


def _volume_narrative(volumes: list[float]) -> str:
    if len(volumes) < 2:
        return "量能資料不足。"
    recent = volumes[-1]
    avg = sum(volumes[:-1]) / len(volumes[:-1])
    if avg == 0:
        return "量能資料異常。"
    ratio = recent / avg
    if ratio > 2.0:
        return f"今日爆量（成交量為近期均量 {ratio:.1f} 倍），需確認是否為主力進出。"
    if ratio > 1.5:
        return f"今日放量（成交量為近期均量 {ratio:.1f} 倍），量能配合度佳。"
    if ratio < 0.5:
        return f"今日縮量（成交量為近期均量 {ratio:.1f} 倍），市場觀望，動能不足。"
    return f"今日量能正常（近期均量 {ratio:.1f} 倍），無異常訊號。"


# ─── 籌碼敘事 ───────────────────────────────────────────────────────────────

def _inst_narrative(inst: dict[str, Any]) -> str:
    """根據 InstitutionalFlowData（或 dict）產出籌碼敘事。"""
    foreign = inst.get("foreign_net_cumulative")
    trust = inst.get("trust_net_cumulative")
    dealer = inst.get("dealer_net_cumulative")
    three_net = inst.get("three_party_net")
    consecutive = inst.get("consecutive_buy_days")
    margin_delta = inst.get("margin_delta")
    short_delta = inst.get("short_delta")
    securities_lending_delta = inst.get("securities_lending_delta")
    foreign_holding_ratio_delta_pct = inst.get("foreign_holding_ratio_delta_pct")
    major_holder_ratio = inst.get("major_holder_ratio")
    major_holder_ratio_delta_pct = inst.get("major_holder_ratio_delta_pct")
    dominant_buyer = inst.get("dominant_buyer")
    dominant_seller = inst.get("dominant_seller")
    consecutive_sell_days = inst.get("consecutive_sell_days")
    flow_strength = inst.get("flow_strength")
    avg_daily_volume = inst.get("avg_daily_volume")
    flow_label = inst.get("flow_label", "neutral")
    source = inst.get("source_provider", "")
    error = inst.get("error")

    if error:
        return f"籌碼資料取得失敗（{error}），無法進行法人分析。"

    parts: list[str] = []

    # 三大法人匯總
    if three_net is not None:
        direction = "買超" if three_net >= 0 else "賣超"
        parts.append(f"三大法人近期合計{direction} {abs(three_net):.0f} 張")
        if isinstance(avg_daily_volume, (int, float)) and avg_daily_volume > 0:
            ratio = abs(three_net) / avg_daily_volume * 100
            if ratio >= 5:
                parts.append(f"法人買賣超約占近期均量 {ratio:.1f}%（相對規模偏大）")
            elif ratio >= 1:
                parts.append(f"法人買賣超約占近期均量 {ratio:.1f}%（具觀察意義）")
            else:
                parts.append(f"法人買賣超約占近期均量 {ratio:.1f}%（相對規模有限）")

    # 外資
    if foreign is not None:
        direction = "買超" if foreign >= 0 else "賣超"
        parts.append(f"外資{direction} {abs(foreign):.0f} 張")

    # 投信
    if trust is not None:
        direction = "買超" if trust >= 0 else "賣超"
        parts.append(f"投信{direction} {abs(trust):.0f} 張")

    # 自營商
    if dealer is not None:
        direction = "買超" if dealer >= 0 else "賣超"
        parts.append(f"自營商{direction} {abs(dealer):.0f} 張")

    # 連買天數
    if consecutive is not None and consecutive > 0:
        parts.append(f"法人連續買超 {consecutive} 日")
    if consecutive_sell_days is not None and consecutive_sell_days > 0:
        parts.append(f"法人連續賣超 {consecutive_sell_days} 日")

    if dominant_buyer and dominant_buyer != "none":
        parts.append(f"主導買方為{_actor_name(dominant_buyer)}")
    if dominant_seller and dominant_seller != "none":
        parts.append(f"主導賣方為{_actor_name(dominant_seller)}")

    # 融資
    if margin_delta is not None:
        if margin_delta > 0:
            parts.append(f"融資餘額增加 {margin_delta:.0f} 張（散戶追多，留意籌碼浮動）")
        elif margin_delta < 0:
            parts.append(f"融資餘額減少 {abs(margin_delta):.0f} 張（籌碼沉澱趨健康）")

    if short_delta is not None:
        if short_delta > 0:
            parts.append(f"融券餘額增加 {short_delta:.0f} 張（空方壓力升高）")
        elif short_delta < 0:
            parts.append(f"融券餘額減少 {abs(short_delta):.0f} 張（空方回補）")

    if securities_lending_delta is not None:
        if securities_lending_delta > 0:
            parts.append(f"借券成交增加 {securities_lending_delta:.0f} 張（潛在空方籌碼增加）")
        elif securities_lending_delta < 0:
            parts.append(f"借券成交減少 {abs(securities_lending_delta):.0f} 張（空方壓力降溫）")

    if foreign_holding_ratio_delta_pct is not None:
        direction = "上升" if foreign_holding_ratio_delta_pct > 0 else "下降"
        parts.append(f"外資持股比例{direction} {abs(foreign_holding_ratio_delta_pct):.2f} 個百分點")

    if major_holder_ratio is not None:
        parts.append(f"大戶持股比例約 {major_holder_ratio:.2f}%")
    if major_holder_ratio_delta_pct is not None:
        direction = "上升" if major_holder_ratio_delta_pct > 0 else "下降"
        parts.append(f"大戶持股比例{direction} {abs(major_holder_ratio_delta_pct):.2f} 個百分點")

    if flow_strength:
        strength_text = {"strong": "強", "moderate": "中等", "weak": "弱"}.get(str(flow_strength), str(flow_strength))
        parts.append(f"籌碼訊號強度：{strength_text}")

    # 標籤補充
    label_map = {
        "institutional_accumulation": "整體籌碼訊號：法人吸籌，籌碼轉佳。",
        "retail_chasing": "整體籌碼訊號：散戶追高，法人未同步，需謹慎。",
        "distribution": "整體籌碼訊號：法人賣超，出貨疑慮，宜觀望。",
        "neutral": "整體籌碼訊號：中性，無明顯方向。",
    }
    label_note = label_map.get(flow_label, "")

    if not parts:
        return "籌碼欄位均為空，無法進行法人分析。"

    summary = "；".join(parts) + "。"
    if source:
        summary += f"（資料來源：{source}）"
    if label_note:
        summary += f" {label_note}"

    return summary


def _actor_name(actor: str) -> str:
    return {
        "foreign": "外資",
        "trust": "投信",
        "dealer": "自營商",
        "mixed": "多方混合",
    }.get(actor, actor)


# ─── 布林通道敘事 ────────────────────────────────────────────────────────────

def _bollinger_narrative(close: float, bb: dict | None) -> str:
    if bb is None:
        return "布林通道資料不足（需至少 20 個交易日收盤價）。"
    mid = bb["bollinger_mid"]
    upper = bb["bollinger_upper"]
    lower = bb["bollinger_lower"]
    bandwidth = bb["bollinger_bandwidth"]

    if mid is None or upper is None or lower is None:
        return "布林通道資料不足，無法判斷。"

    band_range = upper - lower
    parts: list[str] = []

    if band_range > 0:
        pct_from_lower = (close - lower) / band_range
        if close >= upper * 0.99:
            parts.append("價格貼近上軌，短線偏熱，追價風險提高。")
        elif close >= mid and pct_from_lower >= 0.6:
            parts.append("價格位於中軌上方且未碰上軌，趨勢偏強但尚未極端。")
        elif close <= lower * 1.01:
            parts.append("價格接近下軌，偏弱或超跌，留意是否止跌反彈。")
        else:
            parts.append("價格位於布林中軌附近，趨勢中性。")
    else:
        parts.append("布林通道極度收斂，市場波動極低。")

    if bandwidth is not None:
        if bandwidth > 0.1:
            parts.append("通道擴張，波動放大，趨勢行情可能延續。")
        elif bandwidth < 0.03:
            parts.append("通道收斂，波動壓縮，留意後續方向表態。")

    return f"布林中軌 {mid:.2f}，上軌 {upper:.2f}，下軌 {lower:.2f}。" + "".join(parts)


# ─── MACD 敘事 ────────────────────────────────────────────────────────────────

def _macd_narrative(m: dict | None) -> str:
    if m is None:
        return "MACD 資料不足（需至少 35 個交易日收盤價）。"

    macd_line = m["macd_line"]
    signal = m["macd_signal"]
    hist = m["macd_hist"]
    bias = m["macd_bias"]

    if macd_line is None or signal is None or hist is None:
        return "MACD 資料不足，無法判斷。"

    parts: list[str] = []

    # 零軸位置
    if macd_line > 0:
        parts.append("MACD 線位於零軸上方，中期偏多。")
    else:
        parts.append("MACD 線位於零軸下方，中期偏弱。")

    # 多空動能方向
    if bias == "bullish":
        if macd_line > signal:
            parts.append("MACD 線在訊號線上方且柱狀體為正，多方動能增強。")
        else:
            parts.append("MACD 柱狀體轉正，多方動能開始回升。")
    elif bias == "bearish":
        if macd_line < signal:
            parts.append("MACD 線在訊號線下方且柱狀體為負，空方動能主導。")
        else:
            parts.append("MACD 柱狀體轉負，多方動能減弱。")
    else:
        parts.append("MACD 柱狀體接近零軸，多空動能均衡。")

    # 交叉偵測：macd_line 剛跨越 signal（透過 hist 接近 0 且 bias 剛換向無法精確偵測，
    # 此處依 bias 方向給出方向性敘事）
    return "".join(parts)


# ─── KD / ADX / OBV 敘事 ─────────────────────────────────────────────────────

def _kd_narrative(kd: dict | None) -> str:
    if kd is None:
        return "KD 資料不足（需最近高低收盤價）。"

    k_value = kd.get("k")
    d_value = kd.get("d")
    signal = kd.get("kd_signal")
    zone = kd.get("kd_zone")
    if k_value is None or d_value is None:
        return "KD 資料不足，無法判斷。"

    parts = [f"K={k_value:.1f}、D={d_value:.1f}。"]
    if signal == "bullish_cross" and zone == "oversold":
        parts.append("低檔黃金交叉，短線反彈訊號較明確。")
    elif signal == "bearish_cross" and zone == "overbought":
        parts.append("高檔死亡交叉，短線轉弱風險升高。")
    elif zone == "overbought":
        parts.append("KD 位於高檔，動能強但追價需謹慎。")
    elif zone == "oversold":
        parts.append("KD 位於低檔，留意止跌或反彈訊號。")
    elif signal == "bullish_cross":
        parts.append("K 值上穿 D 值，短線動能轉強。")
    elif signal == "bearish_cross":
        parts.append("K 值下穿 D 值，短線動能轉弱。")
    else:
        parts.append("KD 無明確交叉訊號。")
    return "".join(parts)


def _adx_narrative(adx_data: dict | None) -> str:
    if adx_data is None:
        return "ADX 資料不足（需最近高低收盤價）。"

    adx_value = adx_data.get("adx")
    strength = adx_data.get("trend_strength")
    direction = adx_data.get("trend_direction")
    if adx_value is None:
        return "ADX 資料不足，無法判斷趨勢強度。"

    direction_text = {"bullish": "偏多", "bearish": "偏空"}.get(str(direction), "中性")
    if strength == "strong":
        return f"ADX {adx_value:.1f}，趨勢強度明確，方向{direction_text}。"
    if strength == "weak":
        return f"ADX {adx_value:.1f}，趨勢強度偏弱，盤整環境下趨勢訊號需打折。"
    return f"ADX {adx_value:.1f}，趨勢強度中等，方向{direction_text}。"


def _obv_narrative(obv_data: dict | None) -> str:
    if obv_data is None:
        return "OBV 資料不足（需成交量序列）。"

    signal = obv_data.get("obv_signal")
    trend_20d = _obv_trend_text(obv_data.get("obv_trend_20d"), "20 日")
    mid_long_window = obv_data.get("obv_trend_mid_long_window")
    mid_long_label = f"中長期（{mid_long_window}）" if mid_long_window else "中長期"
    mid_long_trend = _obv_trend_text(obv_data.get("obv_trend_mid_long"), mid_long_label)
    suffix = "" if not trend_20d and not mid_long_trend else f" {', '.join(part for part in [trend_20d, mid_long_trend] if part)}。"
    if signal == "price_volume_confirm":
        return f"OBV 隨股價同步上升，量價確認多方動能。{suffix}"
    if signal == "bearish_divergence":
        return f"股價上升但 OBV 未同步走高，量價背離，需提防拉高出貨。{suffix}"
    if signal == "bullish_divergence":
        return f"股價回落但 OBV 未同步轉弱，可能有承接力道。{suffix}"
    if signal == "price_volume_weak":
        return f"OBV 隨股價同步走弱，賣壓尚未解除。{suffix}"
    return f"OBV 變化中性，量價未出現明顯確認或背離。{suffix}"


def _obv_trend_text(trend: object, label: str) -> str | None:
    text = {
        "rising": "上升",
        "falling": "下降",
        "flat": "盤整",
    }.get(str(trend))
    if text is None:
        return None
    return f"{label} OBV 趨勢{text}"


def _atr_narrative(atr_data: dict | None) -> str:
    if atr_data is None:
        return "ATR 資料不足（需最近高低收盤價）。"
    value = atr_data.get("atr")
    pct = atr_data.get("atr_pct")
    level = atr_data.get("volatility_level")
    if value is None or pct is None:
        return "ATR 資料不足，無法判斷波動風險。"
    level_text = {"high": "偏高", "medium": "中等", "low": "偏低"}.get(str(level), "未知")
    return f"ATR {value:.2f}（約 {pct:.2f}%），波動水準{level_text}，停損/停利需依波動調整。"


def _mfi_narrative(mfi_data: dict | None) -> str:
    if mfi_data is None:
        return "MFI 資料不足（需高低收與成交量）。"
    value = mfi_data.get("mfi")
    signal = mfi_data.get("mfi_signal")
    if value is None:
        return "MFI 資料不足，無法判斷資金流。"
    if signal == "overbought":
        return f"MFI {value:.1f}，資金流過熱，追價風險升高。"
    if signal == "oversold":
        return f"MFI {value:.1f}，資金流低檔，留意反彈或賣壓鈍化。"
    if signal == "bullish_flow":
        return f"MFI {value:.1f}，資金流偏多，買盤推動力道尚可。"
    if signal == "bearish_flow":
        return f"MFI {value:.1f}，資金流偏弱，買盤支撐不足。"
    return f"MFI {value:.1f}，資金流中性。"


def _donchian_narrative(donchian_data: dict | None) -> str:
    if donchian_data is None:
        return "Donchian 通道資料不足（需最近高低價）。"
    upper = donchian_data.get("donchian_upper")
    lower = donchian_data.get("donchian_lower")
    position = donchian_data.get("donchian_position")
    if upper is None or lower is None:
        return "Donchian 通道資料不足，無法判斷突破/跌破。"
    text = f"20 日 Donchian 上緣 {upper:.2f}、下緣 {lower:.2f}。"
    if position == "breakout_up":
        return text + "收盤突破近期高檔區間，趨勢突破訊號明確。"
    if position == "breakdown_down":
        return text + "收盤跌破近期低檔區間，破底風險升高。"
    if position == "near_upper":
        return text + "價格接近區間上緣，留意是否有效突破。"
    if position == "near_lower":
        return text + "價格接近區間下緣，需觀察支撐是否守住。"
    return text + "價格仍在區間內，尚未出現明確突破。"


# ─── 支撐壓力位敘事 ──────────────────────────────────────────────────────────

def _price_level_narrative(
    close: float | None,
    support: float | None,
    resistance: float | None,
    high_20d: float | None,
    low_20d: float | None,
) -> str:
    if None in (close, support, resistance):
        return "近20日支撐壓力位資料不足，無法判斷位階。"
    lines = [
        f"近20日高點：{high_20d:.1f}，低點：{low_20d:.1f}",
        f"支撐參考位：{support:.1f}（近20日低點 -1%）",
        f"壓力參考位：{resistance:.1f}（近20日高點 +1%）",
    ]
    assert close is not None and support is not None and resistance is not None
    if close <= support * 1.02:
        lines.append("現價接近支撐位，下檔空間有限，可留意反彈機會。")
    elif close >= resistance * 0.98:
        lines.append("現價接近壓力位，上漲動能需確認突破，注意回測風險。")
    else:
        lines.append("現價處於支撐與壓力之間，位階中立。")
    return "\n".join(lines)


# ─── 主入口 ─────────────────────────────────────────────────────────────────

def generate_technical_context(
    df_price: pd.DataFrame,
    inst_data: dict[str, Any] | None = None,
    *,
    support_20d: float | None = None,
    resistance_20d: float | None = None,
    high_20d: float | None = None,
    low_20d: float | None = None,
) -> tuple[str, str]:
    """
    將價格 DataFrame 與籌碼 dict 轉換為敘事字串。

    Args:
        df_price: 需包含 'Close' 欄位（可選 'Volume'），index 為日期，
                  依日期升序排列（最新資料在最後）。
        inst_data: InstitutionalFlowData 序列化後的 dict，或 None。

    Returns:
        (technical_context, institutional_context)
        - technical_context: BIAS / RSI / 均線 / 量能敘事（str）
        - institutional_context: 法人籌碼敘事（str）
    """
    if df_price is None or df_price.empty or "Close" not in df_price.columns:
        technical = "價格資料不足，無法產出技術分析敘事。"
        institutional = _inst_narrative(inst_data or {})
        return technical, institutional

    closes = [float(v) for v in df_price["Close"].dropna().tolist()]
    highs = (
        [float(v) for v in df_price["High"].dropna().tolist()]
        if "High" in df_price.columns
        else []
    )
    lows = (
        [float(v) for v in df_price["Low"].dropna().tolist()]
        if "Low" in df_price.columns
        else []
    )
    volumes = (
        [float(v) for v in df_price["Volume"].dropna().tolist()]
        if "Volume" in df_price.columns
        else []
    )

    close = closes[-1] if closes else 0.0

    ma5 = ma(closes, 5)
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60)
    bias = calc_bias(close, ma20) if ma20 is not None else None
    rsi = calc_rsi(closes, period=14)
    bb = bollinger_bands(closes)
    macd_data = macd(closes)
    kd_data = stochastic_kd(closes, highs, lows) if highs and lows else None
    adx_data = adx(closes, highs, lows) if highs and lows else None
    atr_data = atr(closes, highs, lows) if highs and lows else None
    mfi_data = mfi(closes, highs, lows, volumes) if highs and lows and volumes else None
    donchian_data = donchian_channel(closes, highs, lows) if highs and lows else None
    obv_data = obv(closes, volumes) if volumes else None

    lines = [
        f"【技術位階】當前收盤價 {close:.2f}。",
        _ma_narrative(close, ma5, ma20, ma60),
        f"【乖離分析】{_bias_narrative(bias)}",
        f"【RSI 動能】{_rsi_narrative(rsi)}",
        f"【量能】{_volume_narrative(volumes) if volumes else '無成交量資料。'}",
        f"【布林通道】{_bollinger_narrative(close, bb)}",
        f"【MACD】{_macd_narrative(macd_data)}",
        f"【KD】{_kd_narrative(kd_data)}",
        f"【ADX】{_adx_narrative(adx_data)}",
        f"【OBV】{_obv_narrative(obv_data)}",
        f"【ATR】{_atr_narrative(atr_data)}",
        f"【MFI】{_mfi_narrative(mfi_data)}",
        f"【Donchian】{_donchian_narrative(donchian_data)}",
    ]
    technical = " ".join(lines)
    price_level = _price_level_narrative(
        close=close if closes else None,
        support=support_20d,
        resistance=resistance_20d,
        high_20d=high_20d,
        low_20d=low_20d,
    )
    technical = technical + "\n【支撐壓力位】" + price_level

    institutional = _inst_narrative(inst_data or {})
    return technical, institutional


# ─── 基本面敘事 ────────────────────────────────────────────────────────────────

def generate_fundamental_context(fund: dict | None) -> str:
    """根據 FundamentalData dict 產出基本面估值敘事。

    fund 可為 None、空 dict、或含 error 鍵的 dict，均安全處理。
    """
    if not fund or fund.get("error"):
        return "基本面資料不足或抓取失敗，無法產出估值敘事。"

    parts: list[str] = []

    # PE
    pe = fund.get("pe_current")
    pe_band = fund.get("pe_band", "unknown")
    pe_pct = fund.get("pe_percentile")
    pe_mean = fund.get("pe_mean")

    _band_map = {"cheap": "偏低（便宜）", "fair": "合理", "expensive": "偏貴（昂貴）", "unknown": "未知"}
    if pe is not None:
        parts.append(f"當前本益比（PE）{pe:.1f} 倍，估值位階{_band_map.get(pe_band, pe_band)}")
    if pe_mean is not None:
        parts.append(f"歷史 PE 均值 {pe_mean:.1f} 倍")
    if pe_pct is not None:
        parts.append(f"PE 百分位 {pe_pct:.0f}%（高於 {pe_pct:.0f}% 的歷史觀測）")

    # 殖利率
    dy = fund.get("dividend_yield")
    yield_sig = fund.get("yield_signal", "unknown")
    _yield_map = {"high_yield": "高殖利率（≥5%）", "mid_yield": "中殖利率（3–5%）", "low_yield": "低殖利率（<3%）", "unknown": "未知"}
    if dy is not None:
        parts.append(f"現金殖利率 {dy:.2f}%，屬{_yield_map.get(yield_sig, yield_sig)}")

    # TTM EPS
    ttm = fund.get("ttm_eps")
    if ttm is not None:
        parts.append(f"近四季合計 EPS {ttm:.2f} 元")

    if not parts:
        return "基本面資料欄位不完整，敘事略過。"

    return "【基本面估值】" + "；".join(parts) + "。"
