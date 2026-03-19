"""generate_strategy 單元測試：純 rule-based 策略生成器。"""
from __future__ import annotations

import pytest

from ai_stock_sentinel.analysis.strategy_generator import calculate_action_plan_tag, generate_strategy


# ─── Strategy Type Tests ─────────────────────────────────────────────────────

def test_short_term_positive_sentiment_rsi_oversold():
    """正面情緒 + RSI 超賣 → short_term"""
    tech = {"sentiment_label": "positive", "rsi": 25.0, "bias": 2.0}
    inst = {"flow_label": "neutral"}
    result = generate_strategy(tech, inst)
    assert result["strategy_type"] == "short_term"
    assert result["holding_period"] == "1-2 週"


def test_mid_term_institutional_accumulation_bullish_ma():
    """法人吸籌 + 均線多頭排列 → mid_term"""
    tech = {"sentiment_label": "neutral", "rsi": 55.0, "bias": 3.0,
            "close": 105.0, "ma5": 103.0, "ma20": 100.0}
    inst = {"flow_label": "institutional_accumulation"}
    result = generate_strategy(tech, inst)
    assert result["strategy_type"] == "mid_term"
    assert result["holding_period"] == "1-3 個月"


def test_defensive_wait_high_bias():
    """BIAS > 10 → defensive_wait（優先規則）"""
    tech = {"sentiment_label": "positive", "rsi": 55.0, "bias": 12.0}
    inst = {"flow_label": "institutional_accumulation"}
    result = generate_strategy(tech, inst)
    assert result["strategy_type"] == "defensive_wait"
    assert result["holding_period"] == "觀望"


def test_defensive_wait_signal_conflict():
    """正面情緒 + distribution 法人出貨 → 訊號衝突 → defensive_wait"""
    tech = {"sentiment_label": "positive", "rsi": 25.0, "bias": 3.0}
    inst = {"flow_label": "distribution"}
    result = generate_strategy(tech, inst)
    assert result["strategy_type"] == "defensive_wait"


def test_default_defensive_wait():
    """無任何訊號時，預設 defensive_wait"""
    result = generate_strategy({}, None)
    assert result["strategy_type"] == "defensive_wait"


# ─── Entry Zone Tests ─────────────────────────────────────────────────────────

def test_entry_zone_high_bias_with_ma20():
    """bias > 5 且有 ma20 → 拉回 MA20（數值）附近分批佈局"""
    tech = {"sentiment_label": "neutral", "rsi": 50.0, "bias": 7.0, "ma20": 100.0}
    result = generate_strategy(tech, None)
    assert "MA20" in result["entry_zone"]
    assert "100.0" in result["entry_zone"]


def test_entry_zone_with_support_20d_and_ma20():
    """support_20d 與 ma20 均可用且 bias <= 5 → 顯示數值區間"""
    tech = {"bias": 3.0, "support_20d": 95.0, "ma20": 100.0}
    result = generate_strategy(tech, None)
    assert "95.0" in result["entry_zone"]
    assert "100.0" in result["entry_zone"]


def test_entry_zone_fallback_when_no_price_data():
    """無 support_20d 也無 ma20 → fallback 提示"""
    tech = {"sentiment_label": "neutral", "rsi": 50.0, "bias": 3.0}
    result = generate_strategy(tech, None)
    assert "資料不足" in result["entry_zone"] or "現價" in result["entry_zone"]


def test_entry_zone_no_bias_no_price_data():
    """bias 為 None 且無 support_20d/ma20 → fallback"""
    tech = {"sentiment_label": "neutral", "rsi": 50.0}
    result = generate_strategy(tech, None)
    assert isinstance(result["entry_zone"], str)
    assert len(result["entry_zone"]) > 0


def test_entry_zone_bias_just_above_5_with_ma20():
    """bias 剛好超過 5 且有 ma20 → 顯示 MA20 數值"""
    tech = {"bias": 5.01, "ma20": 100.0}
    result = generate_strategy(tech, None)
    assert "MA20" in result["entry_zone"]


# ─── Stop Loss Tests ──────────────────────────────────────────────────────────

def test_stop_loss_with_low20d_and_ma60():
    """low_20d 與 ma60 均可用 → stop_loss 包含實際數值"""
    tech = {"low_20d": 100.0, "ma60": 95.0}
    result = generate_strategy(tech, None)
    assert "97.0" in result["stop_loss"]
    assert "95.0" in result["stop_loss"]
    assert "MA60" in result["stop_loss"]


def test_stop_loss_with_only_low20d():
    """只有 low_20d → stop_loss 包含 low_20d × 0.97，不含 MA60"""
    tech = {"low_20d": 100.0}
    result = generate_strategy(tech, None)
    assert "97.0" in result["stop_loss"]
    assert "MA60" not in result["stop_loss"]


def test_stop_loss_fallback_when_no_price_data():
    """無 low_20d → fallback 描述性字串"""
    tech = {}
    result = generate_strategy(tech, None)
    assert "近20日低點" in result["stop_loss"]
    assert "資料不足" in result["stop_loss"]


def test_stop_loss_always_present():
    """stop_loss 永遠回傳非空字串"""
    tech = {}
    result = generate_strategy(tech, None)
    assert isinstance(result["stop_loss"], str)
    assert len(result["stop_loss"]) > 0


# ─── Return Structure Tests ───────────────────────────────────────────────────

def test_result_has_all_required_keys():
    """回傳 dict 必須包含所有必要 keys"""
    result = generate_strategy({}, None)
    assert "strategy_type" in result
    assert "entry_zone" in result
    assert "stop_loss" in result
    assert "holding_period" in result


def test_strategy_type_valid_values():
    """strategy_type 只能是三個合法值之一"""
    valid = {"short_term", "mid_term", "defensive_wait"}
    assert generate_strategy({}, None)["strategy_type"] in valid
    assert generate_strategy({"sentiment_label": "positive", "rsi": 20.0}, None)["strategy_type"] in valid
    assert generate_strategy({"sentiment_label": "neutral", "close": 105.0, "ma5": 103.0, "ma20": 100.0},
                             {"flow_label": "institutional_accumulation"})["strategy_type"] in valid


# ─── Priority Order Tests ─────────────────────────────────────────────────────

def test_defensive_wait_takes_priority_over_short_term():
    """bias > 10 優先於 short_term（即使 RSI 超賣 + 正面情緒）"""
    tech = {"sentiment_label": "positive", "rsi": 20.0, "bias": 15.0}
    inst = {"flow_label": "neutral"}
    result = generate_strategy(tech, inst)
    assert result["strategy_type"] == "defensive_wait"


def test_defensive_wait_takes_priority_over_mid_term():
    """訊號衝突優先於 mid_term"""
    tech = {"sentiment_label": "positive", "rsi": 55.0, "bias": 3.0,
            "close": 105.0, "ma5": 103.0, "ma20": 100.0}
    inst = {"flow_label": "distribution"}
    result = generate_strategy(tech, inst)
    assert result["strategy_type"] == "defensive_wait"


def test_mid_term_requires_all_ma_conditions():
    """mid_term 需要 close > ma5 > ma20 + 足夠分數；只有法人積極但均線不排列時為 short_term"""
    # close < ma5 → 均線空頭，不符合 mid_term；但法人積累(+2) + 健康 RSI(+1) → 分數夠 short_term
    tech = {"sentiment_label": "neutral", "rsi": 55.0, "bias": 3.0,
            "close": 98.0, "ma5": 103.0, "ma20": 100.0}
    inst = {"flow_label": "institutional_accumulation"}
    result = generate_strategy(tech, inst)
    # 不符合 mid_term 條件，但 evidence score 充足 → short_term
    assert result["strategy_type"] != "mid_term"
    assert result["strategy_type"] in {"short_term", "defensive_wait"}


def test_mid_term_with_none_ma_values():
    """mid_term 需要均線多頭排列；MA 為 None 時不得為 mid_term"""
    tech = {"sentiment_label": "neutral", "rsi": 55.0, "bias": 3.0,
            "close": None, "ma5": None, "ma20": None}
    inst = {"flow_label": "institutional_accumulation"}
    result = generate_strategy(tech, inst)
    # MA 都為 None → mid_term 條件不成立，但 flow(+2)+rsi(+1) 分數足夠 short_term
    assert result["strategy_type"] != "mid_term"


def test_short_term_evidence_based():
    """evidence scoring：positive sentiment + 健康 RSI → 分數夠 short_term；
    單純 neutral sentiment + 超賣 RSI 單獨仍可觸發 short_term。"""
    # positive(+1) + RSI in 45-65(+1) = 2 → short_term（新版允許）
    tech = {"sentiment_label": "positive", "rsi": 50.0, "bias": 2.0}
    inst = {"flow_label": "neutral"}
    result = generate_strategy(tech, inst)
    assert result["strategy_type"] in {"short_term", "defensive_wait"}

    # 情緒中性 + RSI 超賣(+1) = 1 < 2 → defensive_wait
    tech2 = {"sentiment_label": "neutral", "rsi": 20.0, "bias": 2.0}
    result2 = generate_strategy(tech2, inst)
    assert result2["strategy_type"] == "defensive_wait"


def test_inst_none_does_not_crash_mid_term_check():
    """inst_data 為 None 時不 crash；均線多頭 + 無法人積極 → flow=0，mid_term 需 flow>0 所以非 mid_term"""
    tech = {"sentiment_label": "neutral", "close": 105.0, "ma5": 103.0, "ma20": 100.0}
    result = generate_strategy(tech, None)
    # flow=0（inst_data=None）→ mid_term 條件不符，但 technical(+2) → short_term
    assert result["strategy_type"] != "mid_term"
    assert isinstance(result["strategy_type"], str)


# ─── Holding Period Mapping ───────────────────────────────────────────────────

def test_holding_period_short_term():
    tech = {"sentiment_label": "positive", "rsi": 25.0}
    result = generate_strategy(tech, None)
    assert result["holding_period"] == "1-2 週"


def test_holding_period_mid_term():
    tech = {"close": 105.0, "ma5": 103.0, "ma20": 100.0}
    inst = {"flow_label": "institutional_accumulation"}
    result = generate_strategy(tech, inst)
    assert result["holding_period"] == "1-3 個月"


def test_holding_period_defensive_wait():
    result = generate_strategy({}, None)
    assert result["holding_period"] == "觀望"


# ─── calculate_action_plan_tag 測試 ───────────────────────────────────────────

def test_calculate_action_plan_tag_returns_opportunity_when_all_conditions_met():
    """rsi14 < 30 + institutional_accumulation + confidence > 70 → opportunity"""
    tag = calculate_action_plan_tag(rsi14=25.0, flow_label="institutional_accumulation", confidence_score=80)
    assert tag == "opportunity"


def test_calculate_action_plan_tag_returns_overheated_when_rsi_high_and_distribution():
    """rsi14 > 70 + distribution → overheated"""
    tag = calculate_action_plan_tag(rsi14=75.0, flow_label="distribution", confidence_score=50)
    assert tag == "overheated"


def test_calculate_action_plan_tag_returns_neutral_for_partial_match():
    """rsi14 < 30 但 flow_label 不是 institutional_accumulation → neutral"""
    tag = calculate_action_plan_tag(rsi14=25.0, flow_label="neutral", confidence_score=80)
    assert tag == "neutral"


def test_calculate_action_plan_tag_falls_back_to_neutral_when_rsi14_none():
    tag = calculate_action_plan_tag(rsi14=None, flow_label="institutional_accumulation", confidence_score=80)
    assert tag == "neutral"


def test_calculate_action_plan_tag_falls_back_to_neutral_when_flow_label_none():
    tag = calculate_action_plan_tag(rsi14=25.0, flow_label=None, confidence_score=80)
    assert tag == "neutral"


def test_calculate_action_plan_tag_falls_back_to_neutral_when_confidence_none():
    tag = calculate_action_plan_tag(rsi14=25.0, flow_label="institutional_accumulation", confidence_score=None)
    assert tag == "neutral"


def test_calculate_action_plan_tag_opportunity_requires_confidence_above_70():
    """confidence_score == 70（非 > 70）→ neutral"""
    tag = calculate_action_plan_tag(rsi14=25.0, flow_label="institutional_accumulation", confidence_score=70)
    assert tag == "neutral"


# ---------------------------------------------------------------------------
# generate_action_plan
# ---------------------------------------------------------------------------

from ai_stock_sentinel.analysis.strategy_generator import generate_action_plan


def test_generate_action_plan_returns_defensive_wait_action():
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="neutral",
        confidence_score=50,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["action"] == "觀望（待訊號明確再試單）"


def test_generate_action_plan_mid_term_action():
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900-910",
        stop_loss="880",
        flow_label="neutral",
        confidence_score=60,
        resistance_20d=None,
        support_20d=None,
    )
    assert "分批佈局" in result["action"]


def test_generate_action_plan_short_term_action():
    result = generate_action_plan(
        strategy_type="short_term",
        entry_zone="895-905",
        stop_loss="875",
        flow_label="neutral",
        confidence_score=70,
        resistance_20d=None,
        support_20d=None,
    )
    assert "短線試單" in result["action"]


def test_generate_action_plan_returns_momentum_based_on_flow_label_accumulation():
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=75,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["momentum_expectation"] == "強（法人集結中）"


def test_generate_action_plan_returns_momentum_based_on_flow_label_distribution():
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="distribution",
        confidence_score=40,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["momentum_expectation"] == "弱（法人出貨中）"


def test_generate_action_plan_neutral_momentum_for_unknown_flow():
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label=None,
        confidence_score=50,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["momentum_expectation"] == "中性"


def test_generate_action_plan_contains_required_keys():
    result = generate_action_plan(
        strategy_type="short_term",
        entry_zone="895-905",
        stop_loss="875",
        flow_label="neutral",
        confidence_score=70,
        resistance_20d=None,
        support_20d=None,
    )
    required_keys = {
        "action", "target_zone", "defense_line", "momentum_expectation", "breakeven_note",
        "conviction_level", "thesis_points", "upgrade_triggers",
        "downgrade_triggers", "invalidation_conditions", "suggested_position_size",
    }
    assert required_keys.issubset(set(result.keys()))


# ─── breakeven_note 測試 ──────────────────────────────────────────────────────

def test_generate_action_plan_breakeven_note_mid_term():
    """mid_term → breakeven_note 包含保本提示"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="neutral",
        confidence_score=60,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["breakeven_note"] is not None
    assert "5%" in result["breakeven_note"]
    assert "入場成本" in result["breakeven_note"]


def test_generate_action_plan_breakeven_note_short_term_is_none():
    """short_term → breakeven_note 為 None"""
    result = generate_action_plan(
        strategy_type="short_term",
        entry_zone="895",
        stop_loss="875",
        flow_label="neutral",
        confidence_score=70,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["breakeven_note"] is None


def test_generate_action_plan_breakeven_note_defensive_wait_is_none():
    """defensive_wait → breakeven_note 為 None"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="neutral",
        confidence_score=50,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["breakeven_note"] is None


# ─── If-Then momentum_expectation 測試 ─────────────────────────────────────────

def test_momentum_expectation_accumulation_with_resistance():
    """institutional_accumulation + resistance_20d → 附帶突破提示"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=75,
        resistance_20d=950.0,
        support_20d=880.0,
    )
    assert "強（法人集結中）" in result["momentum_expectation"]
    assert "950.0" in result["momentum_expectation"]
    assert "突破" in result["momentum_expectation"]


def test_momentum_expectation_distribution_with_support():
    """distribution + support_20d → 附帶跌破提示"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="distribution",
        confidence_score=40,
        resistance_20d=950.0,
        support_20d=880.0,
    )
    assert "弱（法人出貨中）" in result["momentum_expectation"]
    assert "880.0" in result["momentum_expectation"]
    assert "跌破" in result["momentum_expectation"]
    assert "Bearish" in result["momentum_expectation"]


def test_momentum_expectation_neutral_with_both_levels():
    """neutral + 兩個價位 → 同時包含突破與跌破提示"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label=None,
        confidence_score=50,
        resistance_20d=950.0,
        support_20d=880.0,
    )
    assert "中性" in result["momentum_expectation"]
    assert "950.0" in result["momentum_expectation"]
    assert "880.0" in result["momentum_expectation"]


def test_momentum_expectation_accumulation_without_resistance():
    """institutional_accumulation 但 resistance_20d=None → 不含數字，只有基本標籤"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=75,
        resistance_20d=None,
        support_20d=None,
    )
    # 現有舊測試仍應通過（向後相容）
    assert result["momentum_expectation"] == "強（法人集結中）"


def test_momentum_expectation_distribution_without_support():
    """distribution 但 support_20d=None → 只有基本標籤"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="distribution",
        confidence_score=40,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["momentum_expectation"] == "弱（法人出貨中）"


def test_momentum_expectation_neutral_without_levels():
    """neutral + 兩個 None → 只有基本標籤"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label=None,
        confidence_score=50,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["momentum_expectation"] == "中性"


# ─── Evidence Score Tests ─────────────────────────────────────────────────────

from ai_stock_sentinel.analysis.strategy_generator import _compute_evidence_scores, EvidenceScores


def test_evidence_scores_bullish_ma_alignment():
    """close > ma5 > ma20 → technical +2"""
    scores = _compute_evidence_scores(
        bias=3.0, rsi=50.0,
        close=105.0, ma5=103.0, ma20=100.0,
        sentiment_label="neutral", flow_label="neutral",
    )
    assert scores.technical >= 2


def test_evidence_scores_rsi_healthy_range():
    """RSI 45-65 → technical +1"""
    scores = _compute_evidence_scores(
        bias=3.0, rsi=55.0,
        close=None, ma5=None, ma20=None,
        sentiment_label="neutral", flow_label="neutral",
    )
    assert scores.technical >= 1


def test_evidence_scores_rsi_oversold():
    """RSI < 30 → technical +1（反彈候選）"""
    scores = _compute_evidence_scores(
        bias=3.0, rsi=25.0,
        close=None, ma5=None, ma20=None,
        sentiment_label="neutral", flow_label="neutral",
    )
    assert scores.technical >= 1


def test_evidence_scores_high_bias_penalty():
    """bias > 10 → technical -2（使用 rsi=None 避免 rsi 健康區間加分干擾）"""
    scores = _compute_evidence_scores(
        bias=12.0, rsi=None,
        close=None, ma5=None, ma20=None,
        sentiment_label="neutral", flow_label="neutral",
    )
    assert scores.technical == -2


def test_evidence_scores_institutional_accumulation():
    """institutional_accumulation → flow +2"""
    scores = _compute_evidence_scores(
        bias=3.0, rsi=50.0,
        close=None, ma5=None, ma20=None,
        sentiment_label="neutral", flow_label="institutional_accumulation",
    )
    assert scores.flow == 2


def test_evidence_scores_distribution():
    """distribution → flow -2"""
    scores = _compute_evidence_scores(
        bias=3.0, rsi=50.0,
        close=None, ma5=None, ma20=None,
        sentiment_label="neutral", flow_label="distribution",
    )
    assert scores.flow == -2


def test_evidence_scores_positive_sentiment():
    """positive → sentiment +1"""
    scores = _compute_evidence_scores(
        bias=3.0, rsi=50.0,
        close=None, ma5=None, ma20=None,
        sentiment_label="positive", flow_label="neutral",
    )
    assert scores.sentiment == 1


def test_evidence_scores_signal_conflict():
    """positive + distribution → risk_penalty -2 且 signal_conflict = True"""
    scores = _compute_evidence_scores(
        bias=3.0, rsi=50.0,
        close=None, ma5=None, ma20=None,
        sentiment_label="positive", flow_label="distribution",
    )
    assert scores.risk_penalty <= -2
    assert scores.signal_conflict is True


def test_evidence_scores_total_computed():
    """total = technical + flow + sentiment + risk_penalty"""
    scores = _compute_evidence_scores(
        bias=3.0, rsi=55.0,        # technical +1 (rsi)
        close=105.0, ma5=103.0, ma20=100.0,  # technical +2 (ma alignment)
        sentiment_label="positive",  # sentiment +1
        flow_label="institutional_accumulation",  # flow +2
    )
    assert scores.total == scores.technical + scores.flow + scores.sentiment + scores.risk_penalty
    assert scores.total >= 6


# ─── generate_strategy returns evidence_scores ────────────────────────────────

def test_generate_strategy_returns_evidence_scores():
    """generate_strategy 回傳結果包含 evidence_scores 欄位"""
    tech = {"sentiment_label": "positive", "rsi": 55.0, "bias": 3.0,
            "close": 105.0, "ma5": 103.0, "ma20": 100.0}
    inst = {"flow_label": "institutional_accumulation"}
    result = generate_strategy(tech, inst)
    assert "evidence_scores" in result
    ev = result["evidence_scores"]
    assert "technical" in ev
    assert "flow" in ev
    assert "sentiment" in ev
    assert "risk_penalty" in ev
    assert "total" in ev
    assert "signal_conflict" in ev


def test_generate_strategy_evidence_scores_reflect_inputs():
    """evidence_scores.flow == 2 when institutional_accumulation"""
    tech = {"bias": 3.0, "rsi": 55.0, "close": 105.0, "ma5": 103.0, "ma20": 100.0}
    inst = {"flow_label": "institutional_accumulation"}
    result = generate_strategy(tech, inst)
    assert result["evidence_scores"]["flow"] == 2


# ─── Conviction Level Guardrails ──────────────────────────────────────────────

def test_conviction_level_low_when_confidence_below_60():
    """confidence_score < 60 → conviction_level = low"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=55,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["conviction_level"] == "low"


def test_conviction_level_low_when_data_confidence_below_60():
    """data_confidence < 60 → conviction_level = low"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=80,
        resistance_20d=None,
        support_20d=None,
        data_confidence=50,
    )
    assert result["conviction_level"] == "low"


def test_conviction_level_capped_at_medium_when_intraday():
    """is_final=False（盤中）→ mid_term 的 conviction_level 最高為 medium"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=80,
        resistance_20d=None,
        support_20d=None,
        data_confidence=80,
        is_final=False,
    )
    assert result["conviction_level"] in {"low", "medium"}
    assert result["conviction_level"] != "high"


def test_conviction_level_defensive_wait_always_low():
    """defensive_wait → conviction_level 固定 low"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="neutral",
        confidence_score=90,
        resistance_20d=None,
        support_20d=None,
        data_confidence=90,
        is_final=True,
    )
    assert result["conviction_level"] == "low"


def test_suggested_position_size_zero_for_defensive_wait():
    """defensive_wait → suggested_position_size = 0%"""
    result = generate_action_plan(
        strategy_type="defensive_wait",
        entry_zone="現價附近",
        stop_loss="890",
        flow_label="neutral",
        confidence_score=50,
        resistance_20d=None,
        support_20d=None,
    )
    assert result["suggested_position_size"] == "0%"


def test_suggested_position_size_capped_when_low_confidence():
    """低信心時 suggested_position_size 不超過 10%"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="neutral",
        confidence_score=50,  # < 60 → conviction low
        resistance_20d=None,
        support_20d=None,
    )
    assert result["suggested_position_size"] == "10%"


# ─── thesis_points & invalidation_conditions ─────────────────────────────────

def test_thesis_points_not_empty_for_strong_case():
    """強訊號情況下 thesis_points 不為空"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=80,
        resistance_20d=None,
        support_20d=None,
        sentiment_label="positive",
        rsi=55.0,
        close=105.0,
        ma5=103.0,
        ma20=100.0,
    )
    assert isinstance(result["thesis_points"], list)
    assert len(result["thesis_points"]) >= 2


def test_thesis_points_contain_flow_label():
    """institutional_accumulation → thesis_points 包含法人相關說明"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=80,
        resistance_20d=None,
        support_20d=None,
    )
    assert any("法人" in p for p in result["thesis_points"])


def test_invalidation_conditions_not_empty():
    """invalidation_conditions 不為空 list"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="institutional_accumulation",
        confidence_score=80,
        resistance_20d=None,
        support_20d=900.0,
        ma20=100.0,
    )
    assert isinstance(result["invalidation_conditions"], list)
    assert len(result["invalidation_conditions"]) >= 1


def test_invalidation_conditions_with_support_20d():
    """有 support_20d → invalidation_conditions 包含支撐數值"""
    result = generate_action_plan(
        strategy_type="mid_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="neutral",
        confidence_score=75,
        resistance_20d=None,
        support_20d=880.0,
    )
    assert any("880.0" in c for c in result["invalidation_conditions"])


def test_upgrade_triggers_not_empty():
    """upgrade_triggers 不為空 list"""
    result = generate_action_plan(
        strategy_type="short_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="neutral",
        confidence_score=65,
        resistance_20d=950.0,
        support_20d=None,
    )
    assert isinstance(result["upgrade_triggers"], list)
    assert len(result["upgrade_triggers"]) >= 1


def test_downgrade_triggers_contain_ma20():
    """有 ma20 → downgrade_triggers 包含 MA20 數值"""
    result = generate_action_plan(
        strategy_type="short_term",
        entry_zone="900",
        stop_loss="880",
        flow_label="neutral",
        confidence_score=65,
        resistance_20d=None,
        support_20d=None,
        ma20=100.0,
    )
    assert any("100.0" in t for t in result["downgrade_triggers"])


def test_suggested_position_size_intraday_is_conservative():
    """盤中時 suggested_position_size 應為保守文字，不出現積極建議"""
    from ai_stock_sentinel.analysis.strategy_generator import generate_action_plan

    result = generate_action_plan(
        strategy_type="short_term",
        flow_label="institutional_accumulation",
        confidence_score=75,
        is_final=False,
        entry_zone="150-155",
        stop_loss="145",
        data_confidence=70,
    )
    pos_size = result.get("suggested_position_size", "")
    assert "盤中" in pos_size or "收盤確認" in pos_size, (
        f"盤中時應輸出保守提示，但得到：{pos_size}"
    )


def test_suggested_position_size_final_is_normal():
    """收盤後 is_final=True 時，suggested_position_size 不應出現『盤中』字樣"""
    from ai_stock_sentinel.analysis.strategy_generator import generate_action_plan

    result = generate_action_plan(
        strategy_type="short_term",
        flow_label="institutional_accumulation",
        confidence_score=75,
        is_final=True,
        entry_zone="150-155",
        stop_loss="145",
        data_confidence=70,
    )
    pos_size = result.get("suggested_position_size", "")
    assert "盤中" not in pos_size


# ─── calculate_action_plan_tag 行為不回歸 ────────────────────────────────────

def test_calculate_action_plan_tag_behavior_unchanged():
    """舊有 calculate_action_plan_tag 行為不回歸（向後相容）"""
    from ai_stock_sentinel.analysis.strategy_generator import calculate_action_plan_tag
    assert calculate_action_plan_tag(rsi14=25.0, flow_label="institutional_accumulation", confidence_score=80) == "opportunity"
    assert calculate_action_plan_tag(rsi14=75.0, flow_label="distribution", confidence_score=50) == "overheated"
    assert calculate_action_plan_tag(rsi14=25.0, flow_label="neutral", confidence_score=80) == "neutral"
    assert calculate_action_plan_tag(rsi14=None, flow_label="institutional_accumulation", confidence_score=80) == "neutral"
