import pytest
from ai_stock_sentinel.analysis.position_scorer import (
    compute_position_metrics,
    compute_trailing_stop,
    compute_recommended_action,
)


# ── compute_position_metrics ──────────────────────────────────────────────────

class TestComputePositionMetrics:
    def test_profitable_safe(self):
        result = compute_position_metrics(
            entry_price=980.0,
            current_price=1050.0,
            support_20d=960.0,
        )
        assert result["profit_loss_pct"] == pytest.approx(7.14, abs=0.01)
        assert result["position_status"] == "profitable_safe"
        assert result["cost_buffer_to_support"] == pytest.approx(20.0)

    def test_at_risk_upper(self):
        result = compute_position_metrics(
            entry_price=980.0,
            current_price=1010.0,  # +3.06%
            support_20d=960.0,
        )
        assert result["position_status"] == "at_risk"

    def test_at_risk_lower(self):
        result = compute_position_metrics(
            entry_price=980.0,
            current_price=945.0,  # -3.57%
            support_20d=920.0,
        )
        assert result["position_status"] == "at_risk"

    def test_under_water(self):
        result = compute_position_metrics(
            entry_price=980.0,
            current_price=900.0,  # -8.16%
            support_20d=880.0,
        )
        assert result["position_status"] == "under_water"
        assert result["profit_loss_pct"] == pytest.approx(-8.16, abs=0.01)

    def test_narrative_profitable_safe(self):
        result = compute_position_metrics(980.0, 1050.0, 960.0)
        assert "獲利" in result["position_narrative"]

    def test_narrative_at_risk(self):
        result = compute_position_metrics(980.0, 1010.0, 960.0)
        assert "震盪" in result["position_narrative"]

    def test_narrative_under_water(self):
        result = compute_position_metrics(980.0, 900.0, 880.0)
        assert "套牢" in result["position_narrative"]


# ── compute_trailing_stop ─────────────────────────────────────────────────────

class TestComputeTrailingStop:
    def test_breakeven_protection_when_5pct_profit(self):
        # profit >= 5%: trailing_stop = max(entry_price, support_20d)
        stop, reason = compute_trailing_stop(
            profit_loss_pct=7.0,
            entry_price=980.0,
            support_20d=960.0,
            ma10=970.0,
            high_20d=1060.0,
            current_close=1050.0,
        )
        assert stop == 980.0  # max(980, 960)
        assert "成本價" in reason

    def test_trailing_stop_at_new_high(self):
        # close >= high_20d: trailing_stop = max(MA10, support_20d)
        stop, reason = compute_trailing_stop(
            profit_loss_pct=12.0,
            entry_price=980.0,
            support_20d=960.0,
            ma10=1055.0,
            high_20d=1060.0,
            current_close=1065.0,  # >= high_20d, triggers trailing
        )
        assert stop == max(1055.0, 960.0)
        assert "移動停利" in reason

    def test_trailing_stop_prefers_ma10_over_support(self):
        stop, _ = compute_trailing_stop(
            profit_loss_pct=15.0,
            entry_price=980.0,
            support_20d=1000.0,
            ma10=1020.0,
            high_20d=1060.0,
            current_close=1065.0,
        )
        assert stop == 1020.0  # max(1020, 1000)

    def test_under_water_stop(self):
        # profit < -5%: trailing_stop = entry_price * 0.93
        stop, reason = compute_trailing_stop(
            profit_loss_pct=-8.0,
            entry_price=980.0,
            support_20d=880.0,
            ma10=920.0,
            high_20d=1000.0,
            current_close=900.0,
        )
        assert stop == pytest.approx(980.0 * 0.93)
        assert "停損" in reason

    def test_at_risk_uses_support(self):
        # -5% <= profit < 5%: trailing_stop = support_20d
        stop, reason = compute_trailing_stop(
            profit_loss_pct=2.0,
            entry_price=980.0,
            support_20d=950.0,
            ma10=970.0,
            high_20d=1010.0,
            current_close=1000.0,
        )
        assert stop == 950.0
        assert "支撐" in reason


# ── compute_recommended_action ────────────────────────────────────────────────

class TestComputeRecommendedAction:
    def test_trim_when_distribution_and_profitable(self):
        action, reason = compute_recommended_action(
            flow_label="distribution",
            profit_loss_pct=5.0,
            technical_signal="bullish",
            current_close=1050.0,
            trailing_stop=980.0,
            position_status="profitable_safe",
        )
        assert action == "Trim"
        assert reason is not None

    def test_exit_when_distribution_and_loss(self):
        action, reason = compute_recommended_action(
            flow_label="distribution",
            profit_loss_pct=-3.0,
            technical_signal="neutral",
            current_close=950.0,
            trailing_stop=980.0,
            position_status="at_risk",
        )
        assert action == "Exit"

    def test_exit_when_bearish_below_trailing_stop(self):
        action, reason = compute_recommended_action(
            flow_label="accumulation",
            profit_loss_pct=2.0,
            technical_signal="bearish",
            current_close=940.0,
            trailing_stop=960.0,  # close < trailing_stop
            position_status="at_risk",
        )
        assert action == "Exit"

    def test_exit_when_deep_underwater(self):
        action, reason = compute_recommended_action(
            flow_label="neutral",
            profit_loss_pct=-12.0,
            technical_signal="bearish",
            current_close=860.0,
            trailing_stop=911.4,
            position_status="under_water",
        )
        assert action == "Exit"

    def test_hold_when_all_fine(self):
        action, reason = compute_recommended_action(
            flow_label="accumulation",
            profit_loss_pct=8.0,
            technical_signal="bullish",
            current_close=1060.0,
            trailing_stop=980.0,
            position_status="profitable_safe",
        )
        assert action == "Hold"
        assert reason is None

    def test_exit_reason_not_null_for_distribution_profit(self):
        """Spec §7: when flow=distribution and profit>0, exit_reason must not be null."""
        action, reason = compute_recommended_action(
            flow_label="distribution",
            profit_loss_pct=10.0,
            technical_signal="bullish",
            current_close=1080.0,
            trailing_stop=980.0,
            position_status="profitable_safe",
        )
        assert reason is not None
        assert len(reason) > 0
