"""測試 fetch_external_data_node：並行執行 institutional + fundamental fetcher。"""
from __future__ import annotations

import time

from ai_stock_sentinel.graph.nodes import fetch_external_data_node


def _base_state(symbol: str = "2330.TW") -> dict:
    return {
        "symbol": symbol,
        "snapshot": {"current_price": 1785.0},
        "institutional_flow": None,
        "fundamental_data": None,
        "fundamental_context": None,
        "errors": [],
    }


def test_writes_both_institutional_and_fundamental():
    """兩個 fetcher 的結果都應正確寫入 state。"""
    mock_inst = {"flow_label": "accumulation"}
    mock_fund = {"ttm_eps": 39.1, "pe_band": "fair"}

    result = fetch_external_data_node(
        _base_state(),
        institutional_fetcher=lambda symbol: mock_inst,
        fundamental_fetcher=lambda symbol, price: mock_fund,
    )

    assert result["institutional_flow"] == mock_inst
    assert result["fundamental_data"] == mock_fund
    assert result["fundamental_context"]  # 非空字串


def test_runs_fetchers_concurrently():
    """兩個各需 0.2 秒的 fetcher 並行後應在 ~0.2 秒內完成，而非 ~0.4 秒。"""

    def slow_inst(symbol: str) -> dict:
        time.sleep(0.2)
        return {"flow_label": "neutral"}

    def slow_fund(symbol: str, price: float) -> dict:
        time.sleep(0.2)
        return {"ttm_eps": 10.0}

    start = time.monotonic()
    fetch_external_data_node(
        _base_state(),
        institutional_fetcher=slow_inst,
        fundamental_fetcher=slow_fund,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.35, f"並行應在 0.35s 內完成，實際花了 {elapsed:.2f}s"


def test_handles_institutional_error():
    """institutional fetcher 回傳 error dict 時，結果應仍寫入 state，流程不中斷。"""
    error_result = {"error": "FETCH_ERROR", "message": "timeout"}

    result = fetch_external_data_node(
        _base_state(),
        institutional_fetcher=lambda symbol: error_result,
        fundamental_fetcher=lambda symbol, price: {"ttm_eps": 10.0},
    )

    assert result["institutional_flow"]["error"] == "FETCH_ERROR"
    assert result["fundamental_data"] is not None


def test_handles_fundamental_error():
    """fundamental fetcher 回傳 error dict 時，結果應仍寫入 state，流程不中斷。"""
    error_result = {"error": "NO_DATA", "message": "empty"}

    result = fetch_external_data_node(
        _base_state(),
        institutional_fetcher=lambda symbol: {"flow_label": "neutral"},
        fundamental_fetcher=lambda symbol, price: error_result,
    )

    assert result["fundamental_data"]["error"] == "NO_DATA"
    assert result["institutional_flow"] is not None
