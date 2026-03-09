from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalData, FundamentalError


def test_fundamental_data_defaults():
    d = FundamentalData(symbol="2330.TW")
    assert d.ttm_eps is None
    assert d.pe_current is None
    assert d.pe_band == "unknown"
    assert d.pe_percentile is None
    assert d.dividend_yield is None
    assert d.yield_signal == "unknown"
    assert d.source_provider == ""
    assert d.warnings == []


def test_fundamental_error_carries_code():
    err = FundamentalError(code="NO_DATA", message="empty", provider="FinMind")
    assert err.code == "NO_DATA"
    assert err.provider == "FinMind"
