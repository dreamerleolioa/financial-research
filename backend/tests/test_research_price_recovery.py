from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider


DAY = date(2026, 10, 14)


def official(url, **kwargs):
    if "MI_5MINS" in url:
        return {"stat": "OK", "date": "20261001", "fields": ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"],
                "data": [["115/10/14", "100", "103", "99", "102"]]}
    return {"stat": "OK", "date": "20261001", "title": "115年10月 2330 各日成交資訊",
            "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
            "data": [["115/10/14", "1000", "102000", "100", "103", "99", "102"]]}


def actions(url, **kwargs):
    return {"chart": {"error": None, "result": [{"meta": {"symbol": "2330.TW",
        "exchangeTimezoneName": "Asia/Taipei", "regularMarketTime": int(datetime(2026, 10, 14, 13, 30, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())},
        "timestamp": [int(datetime(2026, 10, 13, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())]}]}}


def recover(provider, prices=None, symbol="2330.TW"):
    return provider.repair(prices or {}, required_dates_by_symbol={symbol: [DAY]}, as_of_date=DAY)


def test_official_prices_and_independent_action_evidence_recover_missing_session():
    provider = RecoveringResearchPriceProvider(official_getter=official, actions_getter=actions)
    row = recover(provider)["2330.TW"][0]
    assert row["close"] == 102
    assert row["date"] == "2026-10-14"
    assert row["price_source"] == "twse_stock_day"
    assert row["corporate_actions_checked"] is True
    assert row["corporate_action_source"] == "yahoo_chart_events"
    assert row["price_basis"] == "unadjusted_price"


def test_official_index_requires_real_open_and_does_not_call_stock_actions():
    def forbidden(*args, **kwargs):
        pytest.fail("an index is not a stock corporate action")
    row = recover(RecoveringResearchPriceProvider(official_getter=official, actions_getter=forbidden), symbol="TAIEX")["TAIEX"][0]
    assert (row["open"], row["close"]) == (100, 102)


def test_actions_failure_never_becomes_zero_dividend_or_split():
    def failed(*args, **kwargs):
        raise TimeoutError()
    provider = RecoveringResearchPriceProvider(official_getter=official, actions_getter=failed)
    assert recover(provider).get("2330.TW", []) == []
    assert provider.diagnostics[-1]["stage"] == "corporate_actions"


def test_conflicting_prices_are_preserved_for_evaluator_rejection():
    original = {"date": str(DAY), "open": None, "high": 103, "low": 99, "close": 999}
    provider = RecoveringResearchPriceProvider(official_getter=official, actions_getter=actions)
    result = recover(provider, {"2330.TW": [original]})
    assert result["2330.TW"][0]["close"] == 999
    assert "close" in result["2330.TW"][0]["ohlc_conflict_fields"]


def test_non_stock_never_calls_recovery_sources():
    def forbidden(*args, **kwargs):
        pytest.fail("ETF must not reach a provider")
    assert recover(RecoveringResearchPriceProvider(official_getter=forbidden, actions_getter=forbidden), symbol="0050.TW") == {}


def test_recovery_budget_and_failed_action_cache_are_bounded():
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return official(url, **kwargs)
    provider = RecoveringResearchPriceProvider(official_getter=get, actions_getter=actions, max_requests=1)
    assert recover(provider)["2330.TW"] == []
    assert recover(provider)["2330.TW"] == []
    assert len(calls) == 1
    assert provider.request_count == 1


def test_corporate_actions_are_preserved_instead_of_hiding_a_split():
    def split(url, **kwargs):
        payload = actions(url, **kwargs)
        payload["chart"]["result"][0]["events"] = {"splits": {"event": {
            "date": int(datetime(2026, 10, 14, tzinfo=ZoneInfo("Asia/Taipei")).timestamp()),
            "numerator": 2, "denominator": 1}}}
        return payload
    row = recover(RecoveringResearchPriceProvider(official_getter=official, actions_getter=split))["2330.TW"][0]
    assert row["stock_splits"] == 2


def test_wrong_symbol_action_response_cannot_authorize_price_repair():
    def wrong(url, **kwargs):
        payload = actions(url, **kwargs)
        payload["chart"]["result"][0]["meta"]["symbol"] = "2454.TW"
        return payload
    assert recover(RecoveringResearchPriceProvider(official_getter=official, actions_getter=wrong))["2330.TW"] == []


def test_actions_for_only_later_dates_cannot_authorize_historical_repair():
    def wrong_range(url, **kwargs):
        payload = actions(url, **kwargs)
        payload["chart"]["result"][0]["timestamp"] = [int(datetime(2026, 10, 15, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())]
        return payload
    assert recover(RecoveringResearchPriceProvider(official_getter=official, actions_getter=wrong_range))["2330.TW"] == []
