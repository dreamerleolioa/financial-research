from ai_stock_sentinel.analysis.context_generator import generate_fundamental_context


def test_full_data_generates_narrative():
    data = {
        "ttm_eps": 39.1,
        "pe_current": 25.6,
        "pe_mean": 22.0,
        "pe_std": 4.0,
        "pe_band": "expensive",
        "pe_percentile": 75.0,
        "annual_cash_dividend": 16.0,
        "dividend_yield": 1.6,
        "yield_signal": "low_yield",
    }
    result = generate_fundamental_context(data)
    assert "25.6" in result or "25" in result
    assert "expensive" in result or "偏貴" in result
    assert "1.6" in result or "低" in result


def test_empty_data_returns_placeholder():
    result = generate_fundamental_context({})
    assert "基本面" in result


def test_error_dict_returns_placeholder():
    result = generate_fundamental_context({"error": "NO_DATA"})
    assert "基本面" in result


def test_none_returns_placeholder():
    result = generate_fundamental_context(None)
    assert "基本面" in result
