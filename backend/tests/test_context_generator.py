"""generate_technical_context 單元測試：BIAS / RSI 邊界值與敘事內容驗證。"""
from __future__ import annotations

import pandas as pd
import pytest

from ai_stock_sentinel.analysis.context_generator import (
    _calc_bias,
    _calc_rsi,
    _ma,
    _price_level_narrative,
    generate_technical_context,
)


# ─── _ma ────────────────────────────────────────────────────────────────────

class TestMa:
    def test_ma5_exact(self):
        closes = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert _ma(closes, 5) == pytest.approx(30.0)

    def test_ma_insufficient_data_returns_none(self):
        assert _ma([10.0, 20.0], 5) is None

    def test_ma_uses_last_n(self):
        # 只取最後 5 筆
        closes = [100.0, 10.0, 20.0, 30.0, 40.0, 50.0]
        assert _ma(closes, 5) == pytest.approx(30.0)


# ─── _calc_bias ──────────────────────────────────────────────────────────────

class TestCalcBias:
    def test_positive_bias(self):
        """close > MA → 正乖離"""
        bias = _calc_bias(close=110.0, ma=100.0)
        assert bias == pytest.approx(10.0)

    def test_negative_bias(self):
        """close < MA → 負乖離"""
        bias = _calc_bias(close=90.0, ma=100.0)
        assert bias == pytest.approx(-10.0)

    def test_zero_bias(self):
        """close == MA → 乖離 0"""
        bias = _calc_bias(close=100.0, ma=100.0)
        assert bias == pytest.approx(0.0)

    def test_ma_zero_returns_none(self):
        """MA 為 0 時回傳 None，避免除以零"""
        assert _calc_bias(close=100.0, ma=0.0) is None

    # 邊界值
    def test_bias_just_above_5(self):
        bias = _calc_bias(close=105.1, ma=100.0)
        assert bias is not None and bias > 5

    def test_bias_just_below_minus_10(self):
        bias = _calc_bias(close=89.9, ma=100.0)
        assert bias is not None and bias < -10


# ─── _calc_rsi ───────────────────────────────────────────────────────────────

class TestCalcRsi:
    def _closes_trending_up(self, n: int = 20) -> list[float]:
        """單調上升序列 → 無下跌 → RSI 應為 100"""
        return [float(i) for i in range(1, n + 1)]

    def _closes_trending_down(self, n: int = 20) -> list[float]:
        """單調下降序列 → 無上漲 → RSI 應為 0"""
        return [float(n - i) for i in range(n)]

    def test_insufficient_data_returns_none(self):
        """少於 period+1 筆資料時回傳 None"""
        closes = [100.0] * 14  # 需要 15 筆
        assert _calc_rsi(closes, period=14) is None

    def test_all_up_returns_100(self):
        """純上漲序列，RSI 應為 100"""
        closes = self._closes_trending_up(20)
        rsi = _calc_rsi(closes, period=14)
        assert rsi == pytest.approx(100.0)

    def test_all_down_returns_0(self):
        """純下跌序列，RSI 應為 0"""
        closes = self._closes_trending_down(20)
        rsi = _calc_rsi(closes, period=14)
        assert rsi == pytest.approx(0.0)

    def test_rsi_in_valid_range(self):
        """一般波動序列，RSI 應在 [0, 100] 範圍內"""
        import random
        random.seed(42)
        closes = [100.0 + random.uniform(-5, 5) for _ in range(30)]
        rsi = _calc_rsi(closes, period=14)
        assert rsi is not None
        assert 0.0 <= rsi <= 100.0

    # 邊界值：超買/超賣閾值附近
    def test_rsi_overbought_boundary(self):
        """構造超買情境：大部分上漲，小部分下跌"""
        # 19 個大漲 + 1 個小跌
        closes = [100.0]
        for _ in range(18):
            closes.append(closes[-1] + 2.0)
        closes.append(closes[-1] - 0.1)
        rsi = _calc_rsi(closes, period=14)
        assert rsi is not None and rsi > 70, f"預期超買但 RSI={rsi}"

    def test_rsi_oversold_boundary(self):
        """構造超賣情境：大部分下跌，小部分上漲"""
        closes = [100.0]
        for _ in range(18):
            closes.append(closes[-1] - 2.0)
        closes.append(closes[-1] + 0.1)
        rsi = _calc_rsi(closes, period=14)
        assert rsi is not None and rsi < 30, f"預期超賣但 RSI={rsi}"


# ─── generate_technical_context ─────────────────────────────────────────────

def _make_df(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    data: dict = {"Close": closes}
    if volumes:
        data["Volume"] = volumes
    return pd.DataFrame(data)


class TestGenerateTechnicalContext:
    def test_returns_two_strings(self):
        df = _make_df([100.0] * 25)
        tc, ic = generate_technical_context(df)
        assert isinstance(tc, str) and len(tc) > 0
        assert isinstance(ic, str) and len(ic) > 0

    def test_empty_df_returns_fallback(self):
        tc, _ = generate_technical_context(pd.DataFrame())
        assert "不足" in tc or "無法" in tc

    def test_none_df_returns_fallback(self):
        tc, _ = generate_technical_context(None)  # type: ignore[arg-type]
        assert "不足" in tc or "無法" in tc

    def test_high_bias_triggers_overbought_narrative(self):
        """BIAS > 10% → 敘事應含「過熱」或「高估」"""
        # close = 112，MA20 ≈ 100 → BIAS ≈ 12%
        closes = [100.0] * 19 + [112.0]
        df = _make_df(closes)
        tc, _ = generate_technical_context(df)
        assert "過熱" in tc or "高估" in tc or "乖離率" in tc

    def test_low_bias_triggers_oversold_narrative(self):
        """BIAS < -10% → 敘事應含「低估」或「超跌」"""
        closes = [100.0] * 19 + [88.0]
        df = _make_df(closes)
        tc, _ = generate_technical_context(df)
        assert "低估" in tc or "超跌" in tc or "乖離率" in tc

    def test_rsi_overbought_narrative(self):
        """RSI > 70 → 敘事應提及超買"""
        closes = [float(i) for i in range(1, 21)]  # 單調上升 → RSI ≈ 100
        df = _make_df(closes)
        tc, _ = generate_technical_context(df)
        assert "超買" in tc

    def test_rsi_oversold_narrative(self):
        """RSI < 30 → 敘事應提及超賣"""
        closes = [float(20 - i) for i in range(20)]  # 單調下降 → RSI ≈ 0
        df = _make_df(closes)
        tc, _ = generate_technical_context(df)
        assert "超賣" in tc

    def test_institutional_context_with_data(self):
        """有籌碼資料時，institutional_context 應含外資敘事"""
        df = _make_df([100.0] * 25)
        inst = {
            "foreign_buy": 5000.0,
            "investment_trust_buy": 1000.0,
            "dealer_buy": -200.0,
            "three_party_net": 5800.0,
            "margin_delta": 300.0,
            "flow_label": "institutional_accumulation",
            "source_provider": "FinMind",
        }
        _, ic = generate_technical_context(df, inst)
        assert "外資" in ic
        assert "買超" in ic

    def test_institutional_context_error_case(self):
        """籌碼資料含 error 時，institutional_context 應提示失敗"""
        df = _make_df([100.0] * 25)
        inst = {"error": "INSTITUTIONAL_FETCH_ERROR", "symbol": "2330.TW"}
        _, ic = generate_technical_context(df, inst)
        assert "失敗" in ic

    def test_institutional_context_empty(self):
        """inst_data 為 None 時，institutional_context 應提示無資料"""
        df = _make_df([100.0] * 25)
        _, ic = generate_technical_context(df, None)
        assert isinstance(ic, str)
        assert "無法" in ic or "空" in ic

    def test_volume_surge_narrative(self):
        """成交量爆量 → 敘事應提及爆量"""
        closes = [100.0] * 10
        volumes = [1000.0] * 9 + [3000.0]  # 近期均量 1000，最新 3000 (3x)
        df = _make_df(closes, volumes)
        tc, _ = generate_technical_context(df)
        assert "爆量" in tc or "放量" in tc


# ─── preprocess_node 整合測試 ────────────────────────────────────────────────

class TestPreprocessNode:
    def _base_state(self, **overrides):
        state = {
            "symbol": "2330.TW",
            "news_content": None,
            "snapshot": None,
            "analysis": None,
            "cleaned_news": None,
            "raw_news_items": None,
            "data_sufficient": False,
            "retry_count": 0,
            "errors": [],
            "requires_news_refresh": False,
            "requires_fundamental_update": False,
            "technical_context": None,
            "institutional_context": None,
            "institutional_flow": None,
        }
        state.update(overrides)
        return state

    def test_preprocess_node_with_snapshot(self):
        from ai_stock_sentinel.graph.nodes import preprocess_node

        snapshot = {
            "symbol": "2330.TW",
            "recent_closes": [100.0] * 25,
        }
        state = self._base_state(snapshot=snapshot)
        result = preprocess_node(state)

        assert "technical_context" in result
        assert "institutional_context" in result
        assert isinstance(result["technical_context"], str)
        assert len(result["technical_context"]) > 0

    def test_preprocess_node_without_snapshot(self):
        from ai_stock_sentinel.graph.nodes import preprocess_node

        state = self._base_state(snapshot=None)
        result = preprocess_node(state)

        assert "缺少" in result["technical_context"]

    def test_preprocess_node_writes_price_level_fields_to_state(self):
        """preprocess_node 應將 high_20d / low_20d / support_20d / resistance_20d 寫入 state。"""
        from ai_stock_sentinel.graph.nodes import preprocess_node
        # 25 筆資料，最後 20 筆 min=95, max=114
        closes = [float(90 + i) for i in range(25)]
        snapshot = {
            "symbol": "2330.TW",
            "recent_closes": closes,
            "high_20d": 114.0,
            "low_20d": 95.0,
            "support_20d": 95.0 * 0.99,
            "resistance_20d": 114.0 * 1.01,
        }
        state = self._base_state(snapshot=snapshot)
        result = preprocess_node(state)
        assert result["high_20d"] == 114.0
        assert result["low_20d"] == 95.0
        assert result["support_20d"] == pytest.approx(95.0 * 0.99)
        assert result["resistance_20d"] == pytest.approx(114.0 * 1.01)

    def test_preprocess_node_with_inst_flow(self):
        from ai_stock_sentinel.graph.nodes import preprocess_node

        snapshot = {"symbol": "2330.TW", "recent_closes": [100.0] * 25}
        inst = {
            "foreign_buy": 8000.0,
            "investment_trust_buy": 500.0,
            "dealer_buy": 0.0,
            "three_party_net": 8500.0,
            "margin_delta": -200.0,
            "flow_label": "institutional_accumulation",
            "source_provider": "FinMind",
        }
        state = self._base_state(snapshot=snapshot, institutional_flow=inst)
        result = preprocess_node(state)

        assert "外資" in result["institutional_context"]
        assert "法人" in result["institutional_context"]


# ─── _price_level_narrative 測試 ─────────────────────────────────────────────

class TestPriceLevelNarrative:
    def test_returns_fallback_when_any_input_none(self):
        result = _price_level_narrative(close=None, support=95.0, resistance=115.0, high_20d=114.0, low_20d=96.0)
        assert "不足" in result

    def test_near_support_shows_support_note(self):
        """現價接近支撐位（<= support * 1.02）→ 顯示反彈機會提示。"""
        result = _price_level_narrative(
            close=95.0, support=94.0, resistance=115.5, high_20d=114.0, low_20d=95.0
        )
        assert "支撐" in result
        assert "反彈" in result

    def test_near_resistance_shows_resistance_note(self):
        """現價接近壓力位（>= resistance * 0.98）→ 顯示回測風險提示。"""
        result = _price_level_narrative(
            close=115.0, support=94.0, resistance=115.5, high_20d=114.0, low_20d=95.0
        )
        assert "壓力" in result or "突破" in result

    def test_neutral_zone_shows_neutral_note(self):
        """現價處於支撐與壓力之間 → 顯示中立。"""
        result = _price_level_narrative(
            close=104.0, support=90.0, resistance=120.0, high_20d=119.0, low_20d=91.0
        )
        assert "中立" in result

    def test_contains_high_low_numbers(self):
        """敘事字串應包含 high_20d 與 low_20d 的數值。"""
        result = _price_level_narrative(
            close=104.0, support=90.9, resistance=120.0, high_20d=119.0, low_20d=91.0
        )
        assert "119.0" in result
        assert "91.0" in result


class TestGenerateTechnicalContextWithPriceLevels:
    def test_includes_price_level_narrative_when_provided(self):
        """傳入 support_20d / resistance_20d 時，技術敘事應包含支撐壓力位段落。"""
        closes = [float(90 + i) for i in range(25)]
        df = _make_df(closes)
        tc, _ = generate_technical_context(
            df, None,
            support_20d=94.05,
            resistance_20d=115.14,
            high_20d=114.0,
            low_20d=95.0,
        )
        assert "支撐" in tc
        assert "壓力" in tc

    def test_includes_fallback_when_no_price_levels(self):
        """未傳入 support_20d / resistance_20d → 敘事顯示資料不足。"""
        closes = [100.0] * 25
        df = _make_df(closes)
        tc, _ = generate_technical_context(df, None)
        assert "支撐壓力位" in tc and "不足" in tc
