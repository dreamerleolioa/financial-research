from __future__ import annotations

from dataclasses import asdict
from unittest.mock import MagicMock

from ai_stock_sentinel.graph.nodes import crawl_node, fetch_institutional_node, judge_node
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.models import StockSnapshot


def _make_snapshot() -> dict:
    return asdict(StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=99.5,
        day_high=101.0,
        day_low=98.5,
        volume=123456,
        recent_closes=[98.0, 99.0, 100.0],
        fetched_at="2026-03-03T00:00:00+00:00",
    ))


def _base_state(**overrides) -> GraphState:
    state: GraphState = {
        "symbol": "2330.TW",
        "news_content": None,
        "snapshot": None,
        "analysis": None,
        "analysis_detail": None,
        "cleaned_news": None,
        "cleaned_news_quality": None,
        "news_display": None,
        "news_display_items": [],
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "technical_context": None,
        "institutional_context": None,
        "institutional_flow": None,
        "strategy_type": None,
        "entry_zone": None,
        "stop_loss": None,
        "holding_period": None,
        "confidence_score": None,
        "cross_validation_note": None,
        "is_final": True,
    }
    state.update(overrides)
    return state


def test_crawl_node_returns_snapshot() -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.return_value = StockSnapshot(
        symbol="2330.TW",
        currency="TWD",
        current_price=100.0,
        previous_close=99.0,
        day_open=99.5,
        day_high=101.0,
        day_low=98.5,
        volume=123456,
        recent_closes=[98.0, 99.0, 100.0],
        fetched_at="2026-03-03T00:00:00+00:00",
    )

    result = crawl_node(_base_state(), crawler=mock_crawler)

    assert result["snapshot"]["symbol"] == "2330.TW"
    assert result["errors"] == []


def test_judge_node_accepts_snapshot_without_news() -> None:
    state = _base_state(snapshot=_make_snapshot())

    result = judge_node(state)

    assert result["data_sufficient"] is True
    assert result["requires_news_refresh"] is False


def test_crawl_node_accumulates_errors_on_failure() -> None:
    mock_crawler = MagicMock()
    mock_crawler.fetch_basic_snapshot.side_effect = RuntimeError("network timeout")

    prior_errors = [{"code": "PRIOR_ERROR", "message": "earlier error"}]
    state = _base_state(errors=prior_errors)
    result = crawl_node(state, crawler=mock_crawler)

    assert result["snapshot"] is None
    assert len(result["errors"]) == 2
    assert result["errors"][0]["code"] == "PRIOR_ERROR"
    assert result["errors"][1]["code"] == "CRAWL_ERROR"
    assert "network timeout" in result["errors"][1]["message"]


# ── fetch_institutional_node ─────────────────────────────────────────────────

def test_fetch_institutional_node_writes_flow_to_state() -> None:
    """成功時，institutional_flow 應被寫入 state。"""
    mock_flow_data = {
        "symbol": "2330.TW",
        "foreign_buy": 1000.0,
        "investment_trust_buy": 200.0,
        "dealer_buy": 50.0,
        "margin_delta": None,
        "flow_label": "institutional_accumulation",
        "source_provider": "twse",
    }
    mock_fetcher = MagicMock(return_value=mock_flow_data)

    state = _base_state(snapshot=_make_snapshot())
    result = fetch_institutional_node(state, fetcher=mock_fetcher)

    assert result["institutional_flow"] is not None
    assert result["institutional_flow"]["flow_label"] == "institutional_accumulation"
    mock_fetcher.assert_called_once_with("2330.TW")


def test_fetch_institutional_node_stores_error_dict_on_failure() -> None:
    """fetcher 失敗回傳 error dict 時，仍寫入 institutional_flow，流程不中斷。"""
    mock_flow_data = {
        "symbol": "2330.TW",
        "error": "INSTITUTIONAL_FETCH_ERROR",
        "error_message": "all providers failed",
    }
    mock_fetcher = MagicMock(return_value=mock_flow_data)

    state = _base_state(snapshot=_make_snapshot())
    result = fetch_institutional_node(state, fetcher=mock_fetcher)

    assert result["institutional_flow"]["error"] == "INSTITUTIONAL_FETCH_ERROR"
    assert result.get("errors", []) == []  # 不額外累積 errors，flow 本身帶 error 欄位


# ── preprocess_node rsi14 ─────────────────────────────────────────────────────

def test_preprocess_node_writes_rsi14_float_to_state() -> None:
    """25 筆以上資料 → preprocess_node 應計算 rsi14 並寫入 state。"""
    from ai_stock_sentinel.graph.nodes import preprocess_node
    closes = [float(90 + i) for i in range(25)]  # 25 筆遞增
    snapshot = {"symbol": "2330.TW", "recent_closes": closes}
    state = _base_state(snapshot=snapshot)
    result = preprocess_node(state)
    assert result["rsi14"] is not None
    assert isinstance(result["rsi14"], float)
    assert 0.0 <= result["rsi14"] <= 100.0


def test_preprocess_node_rsi14_is_none_when_insufficient_data() -> None:
    """少於 15 筆資料 → rsi14 應為 None。"""
    from ai_stock_sentinel.graph.nodes import preprocess_node
    closes = [100.0] * 14  # 只有 14 筆，不足 15
    snapshot = {"symbol": "2330.TW", "recent_closes": closes}
    state = _base_state(snapshot=snapshot)
    result = preprocess_node(state)
    assert result["rsi14"] is None


# ── strategy_node action_plan_tag ─────────────────────────────────────────────

def test_strategy_node_returns_action_plan_tag() -> None:
    """strategy_node 應回傳 action_plan_tag 欄位。"""
    from ai_stock_sentinel.graph.nodes import strategy_node
    closes = [float(90 + i) for i in range(25)]
    state = _base_state(
        snapshot={"recent_closes": closes},
        rsi14=25.0,
        confidence_score=80,
        institutional_flow={"flow_label": "institutional_accumulation"},
    )
    result = strategy_node(state)
    assert "action_plan_tag" in result
    assert result["action_plan_tag"] in ("opportunity", "overheated", "neutral")


def test_strategy_node_action_plan_contains_new_fields() -> None:
    """strategy_node 產出的 action_plan 必須包含 evidence-based 新欄位。"""
    from ai_stock_sentinel.graph.nodes import strategy_node
    closes = [float(90 + i) for i in range(25)]
    state = _base_state(
        snapshot={"recent_closes": closes},
        rsi14=55.0,
        confidence_score=75,
        data_confidence=75,
        is_final=True,
        institutional_flow={"flow_label": "institutional_accumulation"},
    )
    result = strategy_node(state)
    action_plan = result.get("action_plan")
    assert action_plan is not None, "action_plan should not be None"
    assert "conviction_level" in action_plan
    assert action_plan["conviction_level"] in ("low", "medium", "high")
    assert "thesis_points" in action_plan
    assert isinstance(action_plan["thesis_points"], list)
    assert "invalidation_conditions" in action_plan
    assert isinstance(action_plan["invalidation_conditions"], list)
    assert "upgrade_triggers" in action_plan
    assert isinstance(action_plan["upgrade_triggers"], list)
    assert "downgrade_triggers" in action_plan
    assert isinstance(action_plan["downgrade_triggers"], list)
    assert "suggested_position_size" in action_plan
    assert isinstance(action_plan["suggested_position_size"], str)


def test_strategy_node_action_plan_conviction_low_when_low_confidence() -> None:
    """confidence_score < 60 時，action_plan.conviction_level 應為 low。"""
    from ai_stock_sentinel.graph.nodes import strategy_node
    closes = [float(90 + i) for i in range(25)]
    state = _base_state(
        snapshot={"recent_closes": closes},
        rsi14=55.0,
        confidence_score=50,  # < 60 → guardrail
        institutional_flow={"flow_label": "institutional_accumulation"},
    )
    result = strategy_node(state)
    action_plan = result.get("action_plan")
    assert action_plan is not None
    assert action_plan["conviction_level"] == "low"


def test_strategy_node_action_plan_position_size_zero_for_defensive_wait() -> None:
    """defensive_wait 策略時，action_plan.suggested_position_size 應為 0%。"""
    from ai_stock_sentinel.graph.nodes import strategy_node
    # 高 bias → defensive_wait
    closes = [100.0] * 25
    closes[-1] = 115.0  # 大幅拉升造成 bias > 10
    state = _base_state(
        snapshot={"recent_closes": closes},
        confidence_score=80,
        institutional_flow={"flow_label": "neutral"},
    )
    result = strategy_node(state)
    action_plan = result.get("action_plan")
    assert action_plan is not None
    if result.get("strategy_type") == "defensive_wait":
        assert action_plan["suggested_position_size"] == "0%"


# -- Position Diagnosis node tests --

from ai_stock_sentinel.analysis.position_scorer import compute_position_metrics  # noqa: E402


def _base_position_state():
    """Minimal GraphState with position fields for testing."""
    return {
        "symbol": "2330.TW",
        "entry_price": 980.0,
        "entry_date": None,
        "quantity": None,
        # snapshot fields required by preprocess_node
        "snapshot": {
            "symbol": "2330.TW",
            "currency": "TWD",
            "current_price": 1050.0,
            "volume": 10000,
            "recent_closes": [1040.0, 1045.0, 1050.0],
            "recent_highs": [1045.0, 1050.0, 1060.0],
            "recent_lows": [1035.0, 1040.0, 1045.0],
            "recent_volumes": [8000, 9000, 10000],
            "high_20d": 1060.0,
            "low_20d": 960.0,
            "support_20d": 960.0,
            "resistance_20d": 1060.0,
        },
        "news_content": "",
        "cleaned_news": [],
        "institutional_flow": None,
        "fundamental_data": None,
        "errors": [],
        "is_final": True,
    }


def test_preprocess_node_computes_position_metrics_when_entry_price_set():
    from ai_stock_sentinel.graph.nodes import preprocess_node

    state = _base_position_state()
    result = preprocess_node(state)

    assert "profit_loss_pct" in result
    assert "position_status" in result
    assert "position_narrative" in result
    assert result["position_status"] in ("profitable_safe", "at_risk", "under_water")


def test_preprocess_node_skips_position_metrics_when_no_entry_price():
    from ai_stock_sentinel.graph.nodes import preprocess_node

    state = _base_position_state()
    state["entry_price"] = None
    result = preprocess_node(state)

    assert result.get("profit_loss_pct") is None
    assert result.get("position_status") is None


def test_strategy_node_computes_trailing_stop_when_position_mode():
    from ai_stock_sentinel.graph.nodes import strategy_node

    state = _base_position_state()
    # Add required preprocess outputs
    state.update({
        "profit_loss_pct": 7.14,
        "position_status": "profitable_safe",
        "position_narrative": "獲利安全區",
        "technical_context": "",
        "rsi14": 55.0,
        "support_20d": 960.0,
        "resistance_20d": 1060.0,
        "high_20d": 1060.0,
        "low_20d": 960.0,
        "technical_signal": "bullish",
        "confidence_score": 70,
    })
    result = strategy_node(state)

    assert "trailing_stop" in result
    assert result["trailing_stop"] is not None
    assert "trailing_stop_reason" in result
    assert "recommended_action" in result
    assert result["recommended_action"] in ("Hold", "Trim", "Exit")
    assert result["distance_to_trailing_stop_pct"] is not None
    assert result["distance_to_support_pct"] is not None
    assert "unrealized_pnl" in result
    assert "holding_days" in result


# ── fetch_external_data_node concurrency ──────────────────────────────────────

def test_fetch_external_data_node_fetches_concurrently() -> None:
    """fetch_external_data_node 應用 asyncio.gather 同時抓取籌碼面與基本面，不依序執行。"""
    import time
    call_order: list[str] = []

    def fake_institutional_fetcher(symbol: str) -> dict:
        call_order.append("institutional_start")
        time.sleep(0.05)
        call_order.append("institutional_end")
        return {"symbol": symbol, "flow_label": "neutral"}

    def fake_fundamental_fetcher(symbol: str, current_price: float) -> dict:
        call_order.append("fundamental_start")
        time.sleep(0.05)
        call_order.append("fundamental_end")
        return {"pe_ratio": None, "dividend_yield": None}

    from ai_stock_sentinel.graph.nodes import fetch_external_data_node

    state = _base_state(snapshot=_make_snapshot())
    fetch_external_data_node(
        state,
        institutional_fetcher=fake_institutional_fetcher,
        fundamental_fetcher=fake_fundamental_fetcher,
    )

    # 並發執行時，兩個 start 應都在任一 end 之前出現
    first_end_idx = min(call_order.index("institutional_end"), call_order.index("fundamental_end"))
    assert call_order.index("institutional_start") < first_end_idx
    assert call_order.index("fundamental_start") < first_end_idx


def test_fetch_external_data_node_skips_when_data_already_present():
    """institutional_flow 和 fundamental_data 都已存在時，不應再呼叫外部 fetcher。"""
    inst_calls = []
    fund_calls = []

    def mock_inst(symbol):
        inst_calls.append(symbol)
        return {"flow": "new"}

    def mock_fund(symbol, price):
        fund_calls.append(symbol)
        return {"pe": 99}

    state = _base_state(
        snapshot={"current_price": 100.0, "recent_closes": []},
        institutional_flow={"flow": "existing"},
        fundamental_data={"pe": 20},
    )

    from ai_stock_sentinel.graph.nodes import fetch_external_data_node
    result = fetch_external_data_node(
        state,
        institutional_fetcher=mock_inst,
        fundamental_fetcher=mock_fund,
    )

    assert inst_calls == [], "institutional fetcher should not be called"
    assert fund_calls == [], "fundamental fetcher should not be called"
    assert result == {}


def test_fetch_external_data_node_skips_when_previous_fetch_errored():
    """institutional_flow 含 error key 時も skip する：API key 未設定等の永久的エラーは retry で回復しない。"""
    inst_calls = []

    def mock_inst(symbol):
        inst_calls.append(symbol)
        return {"flow_label": "buy"}

    state = _base_state(
        snapshot={"current_price": 100.0, "recent_closes": []},
        institutional_flow={"error": "API key not configured"},
        fundamental_data={"pe": 20},
    )

    from ai_stock_sentinel.graph.nodes import fetch_external_data_node
    result = fetch_external_data_node(
        state,
        institutional_fetcher=mock_inst,
        fundamental_fetcher=lambda s, p: {},
    )

    assert inst_calls == [], "error response is treated as cached — no retry for permanent failures"
    assert result == {}
