from unittest.mock import MagicMock, patch
import pytest
from ai_stock_sentinel.data_sources.fundamental.finmind_provider import FinMindFundamentalProvider
from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalData, FundamentalError

MOCK_FINANCIAL_ROWS = [
    # 近 8 季 EPS（簡化）
    {"date": "2024-03-31", "type": "EPS", "value": 8.5},
    {"date": "2024-06-30", "type": "EPS", "value": 9.2},
    {"date": "2024-09-30", "type": "EPS", "value": 10.1},
    {"date": "2024-12-31", "type": "EPS", "value": 11.3},
    {"date": "2023-03-31", "type": "EPS", "value": 7.0},
    {"date": "2023-06-30", "type": "EPS", "value": 7.5},
    {"date": "2023-09-30", "type": "EPS", "value": 8.0},
    {"date": "2023-12-31", "type": "EPS", "value": 8.2},
]

MOCK_DIVIDEND_ROWS = [
    {"date": "2024-07-01", "CashEarningsDistribution": 16.0},
    {"date": "2023-07-01", "CashEarningsDistribution": 13.0},
]


def _make_provider(token="fake-token"):
    return FinMindFundamentalProvider(api_token=token)


@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_fetch_calculates_ttm_eps(mock_fetch):
    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return MOCK_FINANCIAL_ROWS
        return MOCK_DIVIDEND_ROWS
    mock_fetch.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=1000.0)

    assert isinstance(result, FundamentalData)
    # TTM = 最近 4 季：8.5+9.2+10.1+11.3 = 39.1
    assert result.ttm_eps == pytest.approx(39.1, abs=0.01)
    assert result.pe_current == pytest.approx(1000.0 / 39.1, abs=0.1)


@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_pe_band_cheap(mock_fetch):
    # 設定歷史 PE 均值=30, std=5；當前 PE=20 → cheap
    rows = [{"date": f"202{i}-03-31", "type": "EPS", "value": 2.0} for i in range(5)]
    rows += [{"date": f"202{i}-06-30", "type": "EPS", "value": 2.0} for i in range(5)]
    rows += [{"date": f"202{i}-09-30", "type": "EPS", "value": 2.0} for i in range(5)]
    rows += [{"date": f"202{i}-12-31", "type": "EPS", "value": 2.0} for i in range(5)]

    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return rows
        return []
    mock_fetch.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=100.0)
    # ttm_eps = 8.0, pe = 12.5
    assert result.pe_current == pytest.approx(100.0 / 8.0, abs=0.1)
    assert result.pe_band in ("cheap", "fair", "expensive", "unknown")


@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_dividend_yield_high(mock_fetch):
    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return MOCK_FINANCIAL_ROWS
        return MOCK_DIVIDEND_ROWS
    mock_fetch.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=200.0)
    # annual dividend = 16.0, yield = 8% → high_yield
    assert result.dividend_yield == pytest.approx(16.0 / 200.0 * 100, abs=0.01)
    assert result.yield_signal == "high_yield"


@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_raises_when_no_eps_data(mock_fetch):
    mock_fetch.return_value = []
    provider = _make_provider()
    with pytest.raises(FundamentalError) as exc_info:
        provider.fetch("2330.TW", current_price=1000.0)
    assert exc_info.value.code == "FINMIND_NO_EPS_DATA"


@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_historical_prices")
@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_pe_band_uses_historical_prices(mock_fetch_dataset, mock_fetch_prices):
    """歷史 PE 必須使用各季末真實股價，而非今日股價"""
    # 8 季 EPS，每季 10 元
    rows = [
        {"date": "2022-03-31", "type": "EPS", "value": 10.0},
        {"date": "2022-06-30", "type": "EPS", "value": 10.0},
        {"date": "2022-09-30", "type": "EPS", "value": 10.0},
        {"date": "2022-12-31", "type": "EPS", "value": 10.0},
        {"date": "2023-03-31", "type": "EPS", "value": 10.0},
        {"date": "2023-06-30", "type": "EPS", "value": 10.0},
        {"date": "2023-09-30", "type": "EPS", "value": 10.0},
        {"date": "2023-12-31", "type": "EPS", "value": 10.0},
    ]
    # 各季末股價（與今日股價 1000.0 完全不同）
    mock_fetch_prices.return_value = {
        "2022-03-31": 400.0,
        "2022-06-30": 420.0,
        "2022-09-30": 440.0,
        "2022-12-31": 460.0,
        "2023-03-31": 480.0,
        "2023-06-30": 500.0,
        "2023-09-30": 520.0,
        "2023-12-31": 540.0,
    }

    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return rows
        return []
    mock_fetch_dataset.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=1000.0)

    # TTM EPS = 40.0（最近4季合計）
    assert result.ttm_eps == pytest.approx(40.0, abs=0.01)
    # pe_current 用今日股價
    assert result.pe_current == pytest.approx(1000.0 / 40.0, abs=0.1)
    # pe_mean 應反映歷史股價，遠低於 1000/40=25
    # 歷史各窗口 pe（共5窗口，range(4,9)）：
    #   i=4: date=2022-12-31, price=460, eps=40 → 11.5
    #   i=5: date=2023-03-31, price=480, eps=40 → 12.0
    #   i=6: date=2023-06-30, price=500, eps=40 → 12.5
    #   i=7: date=2023-09-30, price=520, eps=40 → 13.0
    #   i=8: date=2023-12-31, price=540, eps=40 → 13.5
    # mean = 12.5
    assert result.pe_mean is not None
    assert result.pe_mean == pytest.approx(12.5, abs=0.5)
    # pe_current=25 遠高於歷史均值，應為 expensive
    assert result.pe_band == "expensive"
    # pe_percentile：25 高於所有歷史 PE → 100%
    assert result.pe_percentile == pytest.approx(100.0, abs=1.0)


@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_historical_prices")
@patch("ai_stock_sentinel.data_sources.fundamental.finmind_provider.FinMindFundamentalProvider._fetch_dataset")
def test_pe_band_falls_back_when_no_historical_prices(mock_fetch_dataset, mock_fetch_prices):
    """歷史股價取得失敗時，pe_band 應為 unknown，流程不中斷"""
    rows = [
        {"date": "2022-03-31", "type": "EPS", "value": 10.0},
        {"date": "2022-06-30", "type": "EPS", "value": 10.0},
        {"date": "2022-09-30", "type": "EPS", "value": 10.0},
        {"date": "2022-12-31", "type": "EPS", "value": 10.0},
        {"date": "2023-03-31", "type": "EPS", "value": 10.0},
        {"date": "2023-06-30", "type": "EPS", "value": 10.0},
    ]
    mock_fetch_prices.return_value = {}  # 無歷史股價

    def side_effect(dataset, **kwargs):
        if dataset == "TaiwanStockFinancialStatements":
            return rows
        return []
    mock_fetch_dataset.side_effect = side_effect

    provider = _make_provider()
    result = provider.fetch("2330.TW", current_price=500.0)

    # pe_current 仍正常計算
    assert result.pe_current is not None
    # pe_band 無法計算歷史分佈，應為 unknown
    assert result.pe_band == "unknown"
    assert result.pe_mean is None
    assert result.pe_std is None
    assert result.pe_percentile is None
    assert any("歷史股價" in w for w in result.warnings)
