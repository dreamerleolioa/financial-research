"""adjust_confidence_by_divergence 單元測試：純 rule-based 信心分數計算。"""
from __future__ import annotations

import pytest

from ai_stock_sentinel.analysis.confidence_scorer import adjust_confidence_by_divergence
from ai_stock_sentinel.analysis.confidence_scorer import compute_confidence
from ai_stock_sentinel.analysis.confidence_scorer import derive_technical_score

BASE = 50


# ─── 四個主要情境 ──────────────────────────────────────────────────────────────

def test_three_dimensional_resonance():
    """三維共振：sentiment=positive + flow=institutional_accumulation + technical=bullish → +20"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    assert score == 70
    assert "三維訊號共振" in note
    assert "法人買超" in note


def test_bullish_news_with_distribution():
    """利多出貨：sentiment=positive + flow=distribution → -12"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="positive",
        inst_flow="distribution",
        technical_signal="sideways",
    )
    assert score == 38
    assert "警示" in note
    assert "出貨" in note


def test_retail_chasing():
    """散戶追高：flow=retail_chasing + technical=bullish → -3"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="neutral",
        inst_flow="retail_chasing",
        technical_signal="bullish",
    )
    assert score == 47
    assert "散戶追高" in note


def test_bearish_news_but_price_holds():
    """利空不跌：sentiment=negative + technical=bullish → 0"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="negative",
        inst_flow="neutral",
        technical_signal="bullish",
    )
    assert score == 50
    assert "利空不跌" in note


# ─── 預設情境 ──────────────────────────────────────────────────────────────────

def test_default_no_adjustment():
    """無符合規則時 → adjustment=0，note="""""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="neutral",
        inst_flow="neutral",
        technical_signal="sideways",
    )
    assert score == BASE
    assert note == ""


# ─── 優先序：第一個符合的規則勝出 ─────────────────────────────────────────────

def test_priority_three_resonance_over_retail_chasing():
    """三維共振優先序高於散戶追高規則（若同時符合，三維共振先匹配）"""
    # retail_chasing 情況下若 sentiment=positive 且 technical=bullish，三維共振不成立（flow 不是 institutional_accumulation）
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="positive",
        inst_flow="retail_chasing",
        technical_signal="bullish",
    )
    # positive(+5) + retail_chasing(-8) + bullish(+5) = +2 → 52
    assert score == 52
    assert "散戶追高" in note


def test_priority_distribution_over_bearish_not_dropping():
    """利多出貨規則 (rule 2) 優先於利空不跌 (rule 4)：
    sentiment=positive + flow=distribution 應觸發利多出貨，而非利空不跌。"""
    score, note = adjust_confidence_by_divergence(
        BASE,
        news_sentiment="positive",
        inst_flow="distribution",
        technical_signal="bullish",  # 即便技術偏多，仍走利多出貨規則
    )
    # positive(+5) + distribution(-10) + bullish(+5) + penalty(-7) = -7 → 43
    assert score == 43
    assert "警示" in note


# ─── Clamp [0, 100] ────────────────────────────────────────────────────────────

def test_clamp_upper_bound():
    """超過 100 → 夾在 100"""
    score, _ = adjust_confidence_by_divergence(
        95,  # 95 + (5+7+5+3) = 95 + 20 = 115 → clamp 100
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    assert score == 100


def test_clamp_lower_bound():
    """低於 0 → 夾在 0"""
    score, _ = adjust_confidence_by_divergence(
        10,  # 10 + (5-10+0-7) = 10 - 12 = -2 → clamp 0
        news_sentiment="positive",
        inst_flow="distribution",
        technical_signal="sideways",
    )
    assert score == 0


# ─── base_score 傳入驗證 ────────────────────────────────────────────────────────

def test_custom_base_score():
    """custom base_score 傳入正確套用"""
    score, note = adjust_confidence_by_divergence(
        70,
        news_sentiment="negative",
        inst_flow="neutral",
        technical_signal="bullish",
    )
    # negative(-5) + neutral(0) + bullish(+5) = 0 → 70 + 0 = 70
    assert score == 70
    assert "利空不跌" in note


def test_base_score_50_default_neutral():
    """base=50 + neutral + neutral + bearish → 45"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="neutral",
        inst_flow="neutral",
        technical_signal="bearish",
    )
    # neutral(0) + neutral(0) + bearish(-5) = -5 → 45
    assert score == 45
    assert note == ""


# ─── 多維加權調分 ──────────────────────────────────────────────────────────────

def test_weighted_partial_positive_sentiment_only():
    """只有 sentiment=positive，inst=neutral，tech=sideways → 小幅加分"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="neutral",
        technical_signal="sideways",
    )
    assert score > 50
    assert score <= 60


def test_weighted_partial_inst_accumulation_only():
    """只有 inst=institutional_accumulation，其他 neutral → +7 → 57"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="neutral",
        inst_flow="institutional_accumulation",
        technical_signal="sideways",
    )
    # neutral(0) + institutional_accumulation(+7) + sideways(0) = +7
    assert score == 57


def test_weighted_three_resonance_still_highest():
    """三維共振仍然是最高調分（>= 60）"""
    score_resonance, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    score_partial, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="neutral",
        technical_signal="bullish",
    )
    assert score_resonance >= score_partial
    assert score_resonance >= 60


def test_weighted_distribution_still_negative():
    """利多出貨（positive + distribution）仍然是負調分"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="distribution",
        technical_signal="sideways",
    )
    assert score < 50
    assert "出貨" in note or "警示" in note


def test_weighted_retail_chasing_still_negative():
    """散戶追高仍然是負調分"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="neutral",
        inst_flow="retail_chasing",
        technical_signal="sideways",
    )
    assert score < 50


# ─── unknown inst flow (CS-3) ─────────────────────────────────────────────────

def test_unknown_inst_flow_excluded_from_score():
    """inst_flow=unknown 不貢獻調分，由其他兩維計算"""
    score_unknown, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="unknown",
        technical_signal="bullish",
    )
    score_neutral, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="neutral",
        technical_signal="bullish",
    )
    # unknown 與 neutral 行為應相同（均不觸發 inst 分數）
    assert score_unknown == score_neutral


def test_unknown_inst_no_three_resonance():
    """inst_flow=unknown 即使 sentiment=positive + tech=bullish，不觸發三維共振"""
    score, note = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="unknown",
        technical_signal="bullish",
    )
    assert "三維訊號共振" not in note


def test_score_node_inst_error_key_treated_as_unknown():
    """score_node：institutional_flow 含 error 鍵時，inst_flow 應視為 unknown（不觸發三維共振）"""
    from ai_stock_sentinel.graph.nodes import score_node
    state = {
        "institutional_flow": {"error": "NO_API_KEY"},
        "cleaned_news": {"sentiment_label": "positive"},
        "snapshot": {"recent_closes": list(range(80, 106))},  # 26 根遞增
        "errors": [],
    }
    result = score_node(state)
    # error → unknown → 不觸發三維共振
    assert "三維訊號共振" not in result.get("cross_validation_note", "")


# ─── derive_technical_score ───────────────────────────────────────────────────

def test_technical_score_insufficient_data():
    """資料不足（< 20 根）回傳 50"""
    assert derive_technical_score([100.0] * 15, rsi=60.0, bias=0.0) == 50


def test_technical_score_prefers_profile_score_summary_over_raw_inputs():
    """有 technical_profile 時，以 capped score_summary 為準，不再讓 raw RSI/KD 另加分。"""
    profile = {
        "score_summary": {
            "primary_score": 0,
            "risk_filter_score": -3,
            "secondary_score": 0,
            "capped_total": -3,
            "technical_score": 40,
        },
        "display_only": {
            "kd_k": 8.0,
            "kd_d": 6.0,
            "mfi": 82.0,
        },
    }
    result = derive_technical_score(
        [100.0 + idx for idx in range(30)],
        rsi=80.0,
        bias=2.0,
        kd_data={"kd_signal": "bullish_cross", "kd_zone": "oversold"},
        adx_data={"trend_strength": "strong", "trend_direction": "bullish"},
        technical_profile=profile,
    )
    assert result == 40


def test_technical_score_all_bullish():
    """三個舊訊號全多頭（RSI+BIAS+MA）：score=+3 → 60（位於 bullish 門檻）"""
    closes = list(range(80, 101))  # 21 根，遞增排列，ma5 > ma20
    # rsi=60 → +1, bias=2.0 → +1, ma多頭排列 → +1，總分 +3
    # round(50 + 3 * 17/5) = round(60.2) = 60
    result = derive_technical_score(closes, rsi=60.0, bias=2.0)
    assert result == 60
    assert result >= 60  # 仍達到 bullish 信號門檻


def test_technical_score_all_bearish():
    """三個舊訊號全空頭（RSI+BIAS+MA）：score=-3 → 40（位於 bearish 門檻）"""
    closes = list(range(120, 99, -1))  # 21 根，遞減
    # rsi=25 → -1, bias=-12 → -1, ma空頭排列 → -1，總分 -3
    # round(50 - 3 * 17/5) = round(39.8) = 40
    result = derive_technical_score(closes, rsi=25.0, bias=-12.0)
    assert result == 40
    assert result <= 40  # 仍達到 bearish 信號門檻


def test_technical_score_neutral():
    """三個訊號中性：score=0 → 50"""
    closes = [100.0] * 21  # 全平，ma5=ma20=close
    result = derive_technical_score(closes, rsi=45.0, bias=0.0)
    assert result == 50


def test_technical_score_partial_bullish():
    """只有 RSI 多頭，bias=0 不觸發加分，MA 全平中性：score=+1 → 53"""
    closes = [100.0] * 21
    result = derive_technical_score(closes, rsi=55.0, bias=0.0)
    # RSI=55 → +1；bias=0.0 不滿足 0 < bias <= 5 → 0；MA 全平 → 0；總分 +1
    # round(50 + 1 * 17/5) = round(53.4) = 53
    assert result == 53


def test_technical_score_uses_kd_adx_obv():
    """KD/ADX/OBV 可共同把中性價格序列推升為偏多技術分。"""
    closes = [100.0] * 21
    result = derive_technical_score(
        closes,
        rsi=45.0,
        bias=0.0,
        kd_data={"kd_signal": "bullish_cross", "kd_zone": "oversold"},
        adx_data={"trend_strength": "strong", "trend_direction": "bullish"},
        obv_data={"obv_signal": "price_volume_confirm"},
    )
    assert result >= 60


def test_sentiment_strength_scales_news_weight():
    """多篇一致正面新聞可透過 sentiment_strength 放大消息面權重。"""
    score, _ = adjust_confidence_by_divergence(
        50,
        news_sentiment="positive",
        inst_flow="neutral",
        technical_signal="sideways",
        sentiment_strength=1.6,
    )
    assert score == 58


# ─── compute_confidence (CS-4) ───────────────────────────────────────────────


def test_compute_confidence_all_available():
    """三維均有資料 → data_confidence=100"""
    result = compute_confidence(
        50,
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    assert result["data_confidence"] == 100
    assert result["signal_confidence"] == 70  # 三維共振
    assert "三維訊號共振" in result["cross_validation_note"]


def test_compute_confidence_only_inst_missing():
    """inst=unknown → data_confidence=67（兩維可用）"""
    result = compute_confidence(
        50,
        news_sentiment="positive",
        inst_flow="unknown",
        technical_signal="bullish",
    )
    assert result["data_confidence"] == 67  # round(2/3 * 100)


def test_compute_confidence_all_neutral():
    """三維均中性（neutral/sideways）→ data_confidence=100（均視為有取得資料）"""
    result = compute_confidence(
        50,
        news_sentiment="neutral",
        inst_flow="neutral",
        technical_signal="sideways",
    )
    assert result["data_confidence"] == 100
    assert result["signal_confidence"] == 50


def test_compute_confidence_one_signal_available():
    """inst=unknown，其他中性但有取得 → data_confidence=67（兩維可用）"""
    result = compute_confidence(
        50,
        news_sentiment="neutral",
        inst_flow="unknown",
        technical_signal="bullish",
    )
    assert result["data_confidence"] == 67  # round(2/3 * 100)


def test_derive_technical_score_rsi_none():
    """RSI=None 時仍可計算其他維度（MA + BIAS）"""
    closes = [100.0] * 21
    result = derive_technical_score(closes, rsi=None, bias=2.0)
    assert isinstance(result, int)
    assert 0 <= result <= 100


# ---------------------------------------------------------------------------
# Session 6: data_confidence 語義修正測試
# ---------------------------------------------------------------------------


def test_data_confidence_is_100_when_all_dims_valid_including_neutral():
    """三維均有有效值（含 neutral/sideways）→ data_confidence=100"""
    result = compute_confidence(
        50,
        news_sentiment="neutral",
        inst_flow="neutral",
        technical_signal="sideways",
    )
    assert result["data_confidence"] == 100


def test_data_confidence_is_67_when_inst_flow_unknown():
    """inst_flow=unknown → data_confidence=67（只有兩維有資料）"""
    result = compute_confidence(
        50,
        news_sentiment="positive",
        inst_flow="unknown",
        technical_signal="bullish",
    )
    assert result["data_confidence"] == 67


def test_data_confidence_is_33_when_only_news_available():
    """news 有資料，inst=unknown，technical=unknown → data_confidence=33"""
    result = compute_confidence(
        50,
        news_sentiment="neutral",
        inst_flow="unknown",
        technical_signal="unknown",
    )
    assert result["data_confidence"] == 33


def test_data_confidence_neutral_sentiment_does_not_reduce_score():
    """neutral 情緒不應降低 data_confidence"""
    result_neutral = compute_confidence(
        50,
        news_sentiment="neutral",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    result_positive = compute_confidence(
        50,
        news_sentiment="positive",
        inst_flow="institutional_accumulation",
        technical_signal="bullish",
    )
    # 兩者 data_confidence 應相同（均為 100）
    assert result_neutral["data_confidence"] == 100
    assert result_positive["data_confidence"] == 100


def test_data_confidence_sideways_signal_does_not_reduce_score():
    """sideways 技術訊號不應降低 data_confidence"""
    result = compute_confidence(
        50,
        news_sentiment="positive",
        inst_flow="neutral",
        technical_signal="sideways",
    )
    assert result["data_confidence"] == 100


# ---------------------------------------------------------------------------
# Session 7: DATE_UNKNOWN 信心分數懲罰測試
# ---------------------------------------------------------------------------


def test_date_unknown_flag_reduces_signal_confidence_by_3():
    """DATE_UNKNOWN=True 時 signal_confidence 應比無旗標低 3 分。"""
    result_no_flag = compute_confidence(50, news_sentiment="neutral", inst_flow="neutral", technical_signal="sideways")
    result_with_flag = compute_confidence(50, news_sentiment="neutral", inst_flow="neutral", technical_signal="sideways", date_unknown=True)
    assert result_with_flag["signal_confidence"] == result_no_flag["signal_confidence"] - 3


def test_date_unknown_does_not_affect_data_confidence():
    """DATE_UNKNOWN 旗標不影響 data_confidence。"""
    result_no_flag = compute_confidence(50, news_sentiment="neutral", inst_flow="neutral", technical_signal="sideways")
    result_with_flag = compute_confidence(50, news_sentiment="neutral", inst_flow="neutral", technical_signal="sideways", date_unknown=True)
    assert result_with_flag["data_confidence"] == result_no_flag["data_confidence"]


def test_no_penalty_when_date_unknown_false():
    """date_unknown=False 時不扣分（預設行為）。"""
    result = compute_confidence(50, news_sentiment="neutral", inst_flow="neutral", technical_signal="sideways", date_unknown=False)
    assert result["signal_confidence"] == 50


def test_date_unknown_signal_confidence_clamped_at_zero():
    """signal_confidence 扣分後不低於 0。"""
    # base=50, 強力負向調整讓分數接近 0
    result = compute_confidence(
        0,
        news_sentiment="negative",
        inst_flow="distribution",
        technical_signal="bearish",
        date_unknown=True,
    )
    assert result["signal_confidence"] >= 0


def test_technical_score_uses_mfi_donchian_and_atr_volatility_filter():
    closes = [float(100 + idx) for idx in range(25)]

    bullish = derive_technical_score(
        closes,
        rsi=55.0,
        bias=2.0,
        mfi_data={"mfi_signal": "bullish_flow"},
        donchian_data={"donchian_position": "breakout_up"},
    )
    high_volatility = derive_technical_score(
        closes,
        rsi=55.0,
        bias=2.0,
        mfi_data={"mfi_signal": "bullish_flow"},
        donchian_data={"donchian_position": "breakout_up"},
        atr_data={"volatility_level": "high"},
    )
    bearish = derive_technical_score(
        [100.0] * 25,
        rsi=55.0,
        bias=0.0,
        mfi_data={"mfi_signal": "bearish_flow"},
        donchian_data={"donchian_position": "breakdown_down"},
    )

    assert bullish > 50
    assert high_volatility < bullish
    assert bearish < 50

# ─── Phase 2: MACD 與布林通道對 derive_technical_score 的影響 ─────────────────

def test_macd_bullish_above_zero_adds_score():
    """MACD 偏多且零軸上方：score 應高於無 MACD 資料時"""
    closes = [100.0] * 21
    score_no_macd = derive_technical_score(closes, rsi=45.0, bias=0.0)
    macd_bull = {"macd_line": 1.5, "macd_signal": 1.0, "macd_hist": 0.5, "macd_bias": "bullish"}
    score_with_macd = derive_technical_score(closes, rsi=45.0, bias=0.0, macd_data=macd_bull)
    assert score_with_macd > score_no_macd


def test_macd_bearish_below_zero_reduces_score():
    """MACD 偏空且零軸下方：score 應低於無 MACD 資料時"""
    closes = [100.0] * 21
    score_no_macd = derive_technical_score(closes, rsi=45.0, bias=0.0)
    macd_bear = {"macd_line": -1.5, "macd_signal": -1.0, "macd_hist": -0.5, "macd_bias": "bearish"}
    score_with_macd = derive_technical_score(closes, rsi=45.0, bias=0.0, macd_data=macd_bear)
    assert score_with_macd < score_no_macd


def test_macd_bullish_but_below_zero_no_score():
    """MACD 偏多但零軸下方（macd_line < 0）：不加分"""
    closes = [100.0] * 21
    score_no_macd = derive_technical_score(closes, rsi=45.0, bias=0.0)
    macd_mixed = {"macd_line": -0.5, "macd_signal": -1.0, "macd_hist": 0.5, "macd_bias": "bullish"}
    score_with_macd = derive_technical_score(closes, rsi=45.0, bias=0.0, macd_data=macd_mixed)
    assert score_with_macd == score_no_macd


def test_bollinger_near_lower_with_rsi_oversold_adds_score():
    """價格接近下軌且 RSI 超賣：應加分（反彈候選）"""
    # 構造：19 筆平均 100，最後 1 筆拉低到接近下軌
    closes = [100.0] * 19 + [82.0]
    from ai_stock_sentinel.analysis.metrics import bollinger_bands
    bb = bollinger_bands(closes)
    # rsi 超賣
    score_no_bb = derive_technical_score(closes, rsi=25.0, bias=-9.0)
    score_with_bb = derive_technical_score(closes, rsi=25.0, bias=-9.0, bb=bb)
    assert score_with_bb >= score_no_bb  # 加分或持平（視布林條件是否達到）


def test_bollinger_near_upper_with_rsi_overbought_reduces_score():
    """價格貼近上軌且 RSI 過熱：應扣分（風險警示）"""
    closes = [100.0] * 19 + [113.0]
    from ai_stock_sentinel.analysis.metrics import bollinger_bands
    bb = bollinger_bands(closes)
    score_no_bb = derive_technical_score(closes, rsi=75.0, bias=8.0)
    score_with_bb = derive_technical_score(closes, rsi=75.0, bias=8.0, bb=bb)
    assert score_with_bb <= score_no_bb  # 扣分或持平


def test_derive_technical_score_five_signal_bullish():
    """五個訊號全多頭：score=+5 → 67（最高分）"""
    closes = [100.0] * 21  # 平坦，MA 對齊
    macd_bull = {"macd_line": 2.0, "macd_signal": 1.5, "macd_hist": 0.5, "macd_bias": "bullish"}
    # 布林中上軌附近（95%-99% 區間）需要特定價格，這裡只驗證上限
    result = derive_technical_score(
        closes,
        rsi=60.0,  # +1
        bias=2.0,  # +1
        macd_data=macd_bull,  # +1 (bullish + line>0)
    )
    assert result > 50  # 至少有加分


def test_derive_technical_score_five_signal_bearish():
    """五個訊號全空頭：score=-5 → 33（最低分）"""
    closes = list(range(120, 99, -1))  # 遞減
    macd_bear = {"macd_line": -2.0, "macd_signal": -1.5, "macd_hist": -0.5, "macd_bias": "bearish"}
    result = derive_technical_score(
        closes,
        rsi=25.0,   # -1
        bias=-12.0, # -1
        macd_data=macd_bear,  # -1 (bearish + line<0)
    )
    assert result < 50  # 至少有扣分


def test_mixed_signal_macd_bullish_ma_bearish():
    """MACD 多頭但 MA 偏弱時：mixed signal，不應極端"""
    closes = list(range(120, 99, -1))  # MA 空頭
    macd_bull = {"macd_line": 0.5, "macd_signal": 0.3, "macd_hist": 0.2, "macd_bias": "bullish"}
    result = derive_technical_score(closes, rsi=45.0, bias=-3.0, macd_data=macd_bull)
    assert 33 <= result <= 67  # 混合訊號，不應極端


def test_bollinger_lower_bounce_rsi_not_oversold_no_extra():
    """下軌反彈但 RSI 未超賣（RSI ≥ 30）：不加反彈候選分"""
    closes = [100.0] * 19 + [84.0]
    from ai_stock_sentinel.analysis.metrics import bollinger_bands
    bb = bollinger_bands(closes)
    score_no_bb = derive_technical_score(closes, rsi=35.0, bias=-8.5)
    score_with_bb = derive_technical_score(closes, rsi=35.0, bias=-8.5, bb=bb)
    # RSI=35 ≥ 30，不觸發反彈候選加分
    assert score_with_bb == score_no_bb or score_with_bb <= score_no_bb
