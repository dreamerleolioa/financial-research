from ai_stock_sentinel.graph.nodes import fetch_fundamental_node


def _base_state():
    return {
        "symbol": "2330.TW",
        "snapshot": {"current_price": 1785.0},
        "fundamental_data": None,
        "fundamental_context": None,
        "errors": [],
    }


def test_writes_fundamental_data_on_success():
    mock_result = {"ttm_eps": 39.1, "pe_band": "fair"}

    def mock_fetcher(symbol, current_price):
        return mock_result

    result = fetch_fundamental_node(_base_state(), fetcher=mock_fetcher)
    assert result["fundamental_data"] == mock_result
    assert result["fundamental_context"]  # 非空字串


def test_handles_fetcher_error_dict():
    def mock_fetcher(symbol, current_price):
        return {"error": "NO_DATA", "message": "empty"}

    result = fetch_fundamental_node(_base_state(), fetcher=mock_fetcher)
    assert result["fundamental_data"]["error"] == "NO_DATA"
    assert "基本面" in result["fundamental_context"]


def test_handles_missing_snapshot():
    state = _base_state()
    state["snapshot"] = None

    def mock_fetcher(symbol, current_price):
        return {"ttm_eps": 10.0}

    result = fetch_fundamental_node(state, fetcher=mock_fetcher)
    # snapshot 缺失時 current_price=0，不拋例外
    assert "fundamental_data" in result
