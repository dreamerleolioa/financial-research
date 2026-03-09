from unittest.mock import patch, MagicMock
from ai_stock_sentinel.data_sources.fundamental.tools import fetch_fundamental_data
from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalData, FundamentalError


def test_returns_dict_on_success():
    mock_data = FundamentalData(symbol="2330.TW", ttm_eps=39.1, pe_current=25.6, pe_band="fair")
    with patch(
        "ai_stock_sentinel.data_sources.fundamental.tools.FinMindFundamentalProvider"
    ) as MockProvider:
        MockProvider.return_value.fetch.return_value = mock_data
        result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert isinstance(result, dict)
    assert result["ttm_eps"] == 39.1
    assert result["pe_band"] == "fair"
    assert "error" not in result


def test_returns_error_dict_on_failure():
    with patch(
        "ai_stock_sentinel.data_sources.fundamental.tools.FinMindFundamentalProvider"
    ) as MockProvider:
        MockProvider.return_value.fetch.side_effect = FundamentalError("NO_DATA", "empty")
        result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert "error" in result
    assert result["error"] == "NO_DATA"


def test_never_raises():
    with patch(
        "ai_stock_sentinel.data_sources.fundamental.tools.FinMindFundamentalProvider"
    ) as MockProvider:
        MockProvider.return_value.fetch.side_effect = RuntimeError("unexpected")
        result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert "error" in result
