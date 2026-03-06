"""generate_technical_context：純 rule-based，把數值轉為敘事字串。

不呼叫 LLM，僅依據技術指標與籌碼數值，以固定規則輸出中文敘事，
供 analyze_node 的 Prompt 使用。
"""
from __future__ import annotations

from typing import Any

import pandas as pd


# ─── 技術指標計算 ────────────────────────────────────────────────────────────

def calc_bias(close: float, ma: float) -> float | None:
    """BIAS = (close - MA) / MA * 100"""
    if ma == 0:
        return None
    return (close - ma) / ma * 100


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI 標準公式（Wilder 平均法）。資料不足時回傳 None。"""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


# 保留私有別名，避免現有測試直接引用私有名稱時中斷
_calc_bias = calc_bias
_calc_rsi = calc_rsi
_ma = ma


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
    foreign = inst.get("foreign_buy")
    trust = inst.get("investment_trust_buy")
    dealer = inst.get("dealer_buy")
    three_net = inst.get("three_party_net")
    consecutive = inst.get("consecutive_buy_days")
    margin_delta = inst.get("margin_delta")
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

    # 融資
    if margin_delta is not None:
        if margin_delta > 0:
            parts.append(f"融資餘額增加 {margin_delta:.0f} 張（散戶追多，留意籌碼浮動）")
        elif margin_delta < 0:
            parts.append(f"融資餘額減少 {abs(margin_delta):.0f} 張（籌碼沉澱趨健康）")

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
    volumes = (
        [float(v) for v in df_price["Volume"].dropna().tolist()]
        if "Volume" in df_price.columns
        else []
    )

    close = closes[-1] if closes else 0.0

    ma5 = _ma(closes, 5)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    bias = _calc_bias(close, ma20) if ma20 is not None else None
    rsi = _calc_rsi(closes, period=14)

    lines = [
        f"【技術位階】當前收盤價 {close:.2f}。",
        _ma_narrative(close, ma5, ma20, ma60),
        f"【乖離分析】{_bias_narrative(bias)}",
        f"【RSI 動能】{_rsi_narrative(rsi)}",
        f"【量能】{_volume_narrative(volumes) if volumes else '無成交量資料。'}",
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
