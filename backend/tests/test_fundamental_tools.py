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


def test_invalid_provider_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("FUNDAMENTAL_PROVIDER_MODE", "unexpected")

    result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert result["error"] == "FUNDAMENTAL_PROVIDER_MODE_INVALID"


def test_official_cache_mode_owns_commits_and_session_lifecycle(monkeypatch):
    mock_data = FundamentalData(symbol="2330.TW", ttm_eps=40)
    session = MagicMock()
    provider = MagicMock()
    provider.fetch.return_value = mock_data
    monkeypatch.setenv("FUNDAMENTAL_PROVIDER_MODE", "official_cache_first")
    with (
        patch(
            "ai_stock_sentinel.data_sources.fundamental.tools.create_session",
            return_value=session,
        ),
        patch(
            "ai_stock_sentinel.data_sources.fundamental.tools.OfficialCachedFundamentalProvider",
            return_value=provider,
        ),
    ):
        result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert result["ttm_eps"] == 40
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_official_cache_mode_returns_error_when_database_is_unavailable(monkeypatch):
    monkeypatch.setenv("FUNDAMENTAL_PROVIDER_MODE", "official_cache_only")
    with patch(
        "ai_stock_sentinel.data_sources.fundamental.tools.create_session",
        side_effect=RuntimeError("database unavailable"),
    ):
        result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert result["error"] == "FUNDAMENTAL_DATABASE_UNAVAILABLE"


def test_official_cache_mode_converts_commit_failure_to_error_dict(monkeypatch):
    mock_data = FundamentalData(symbol="2330.TW", ttm_eps=40)
    session = MagicMock()
    session.commit.side_effect = RuntimeError("commit failed")
    provider = MagicMock()
    provider.fetch.return_value = mock_data
    monkeypatch.setenv("FUNDAMENTAL_PROVIDER_MODE", "official_cache_first")
    with (
        patch(
            "ai_stock_sentinel.data_sources.fundamental.tools.create_session",
            return_value=session,
        ),
        patch(
            "ai_stock_sentinel.data_sources.fundamental.tools.OfficialCachedFundamentalProvider",
            return_value=provider,
        ),
    ):
        result = fetch_fundamental_data("2330.TW", current_price=1000.0)

    assert result == {
        "error": "FUNDAMENTAL_DATABASE_UNAVAILABLE",
        "message": "commit failed",
        "symbol": "2330.TW",
    }
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()
