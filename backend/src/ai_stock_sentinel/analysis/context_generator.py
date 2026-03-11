"""generate_technical_context：純 rule-based，把數值轉為敘事字串。

不呼叫 LLM，僅依據技術指標與籌碼數值，以固定規則輸出中文敘事，
供 analyze_node 的 Prompt 使用。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ai_stock_sentinel.analysis.metrics import ma, calc_bias, calc_rsi


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

    ma5 = ma(closes, 5)
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60)
    bias = calc_bias(close, ma20) if ma20 is not None else None
    rsi = calc_rsi(closes, period=14)

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
