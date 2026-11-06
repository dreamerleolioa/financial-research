from datetime import date, datetime
from types import SimpleNamespace
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
        "timestamp": [int(datetime(2026, 10, day, tzinfo=ZoneInfo("Asia/Taipei")).timestamp()) for day in (13, 14)]}]}}


def recover(provider, prices=None, symbol="2330.TW"):
    return provider.repair(prices or {}, required_dates_by_symbol={symbol: [DAY]}, as_of_date=DAY)


@pytest.mark.parametrize("fault", ["json", "http", "transport"])
def test_index_transient_source_failure_retries_once_with_diagnostics(fault):
    import json
    from curl_cffi import requests
    calls = []
    class Response:
        status_code = 503 if fault == "http" else 200
        headers = {"content-type": "text/html"}
        def raise_for_status(self):
            if fault == "http":
                raise requests.exceptions.HTTPError("upstream unavailable")
        def json(self):
            raise json.JSONDecodeError("not JSON", "<html>", 0)
    def get(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            if fault == "transport":
                raise requests.exceptions.ConnectionError("connection lost")
            return Response()
        return official(url, **kwargs)
    provider = RecoveringResearchPriceProvider(official_getter=get)
    rows = recover(provider, symbol="TAIEX")["TAIEX"]
    assert rows[0]["open"] == 100
    assert rows[0]["source_date"] == str(DAY)
    assert len(calls) == provider.request_count == 2
    diagnostic = next(row for row in provider.diagnostics if row["stage"] == "source_request")
    assert diagnostic["attempt"] == 1 and diagnostic["will_retry"] is True
    assert diagnostic["source_url"].endswith("MI_5MINS_HIST")
    if fault != "transport":
        assert diagnostic["http_status"] == Response.status_code
        assert diagnostic["content_type"] == "text/html"


@pytest.mark.parametrize("limit", ["request", "deadline", "attempts"])
def test_index_json_retry_never_exceeds_shared_budget_and_caches_failure(limit):
    import json
    elapsed = [0]
    calls = []
    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}
        def json(self):
            raise json.JSONDecodeError("not JSON", "<html>", 0)
    def get(url, **kwargs):
        calls.append(kwargs)
        if limit == "deadline":
            elapsed[0] += 61
        return Response()
    provider = RecoveringResearchPriceProvider(official_getter=get,
        max_requests=1 if limit == "request" else 64, clock=lambda: elapsed[0])
    assert recover(provider, symbol="TAIEX")["TAIEX"] == []
    assert recover(provider, symbol="TAIEX")["TAIEX"] == []
    assert len(calls) == (2 if limit == "attempts" else 1)
    assert provider.request_count == len(calls)
    assert provider.diagnostics[0]["http_status"] == 200
    assert provider.diagnostics[0]["reason"] == "research_source_invalid_json"
    if limit != "attempts":
        assert any(row.get("budget_limit") == ("request_limit" if limit == "request" else "deadline")
                   for row in provider.diagnostics)


def test_index_auth_failure_is_not_retried():
    from curl_cffi import requests
    class Response:
        status_code = 403
        headers = {"content-type": "text/html"}
        def raise_for_status(self):
            raise requests.exceptions.HTTPError("denied")
    provider = RecoveringResearchPriceProvider(official_getter=lambda *args, **kwargs: Response())
    assert recover(provider, symbol="TAIEX")["TAIEX"] == []
    assert provider.request_count == 1
    assert provider.diagnostics[0]["http_status"] == 403
    assert provider.diagnostics[0]["will_retry"] is False


def test_index_late_success_cannot_exceed_deadline():
    elapsed = [0]
    def get(url, **kwargs):
        elapsed[0] = 61
        return official(url, **kwargs)
    provider = RecoveringResearchPriceProvider(official_getter=get, clock=lambda: elapsed[0])
    assert recover(provider, symbol="TAIEX")["TAIEX"] == []
    assert provider.request_count == 1
    assert provider.diagnostics[-1]["budget_limit"] == "deadline"


def test_index_valid_json_contract_mismatch_is_not_retried():
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return official(url, **kwargs) | {"date": "20260901"}
    provider = RecoveringResearchPriceProvider(official_getter=get)
    assert recover(provider, symbol="TAIEX")["TAIEX"] == []
    assert len(calls) == 1
    assert provider.diagnostics[-1]["reason"] == "official_research_date_or_status_mismatch"


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
    assert provider.diagnostics[0]["budget_limit"] == "request_limit"


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


def test_fresh_quote_metadata_cannot_authorize_a_truncated_action_history():
    def truncated(url, **kwargs):
        payload = actions(url, **kwargs)
        payload["chart"]["result"][0]["timestamp"] = [int(datetime(2026, 10, 13, tzinfo=ZoneInfo("Asia/Taipei")).timestamp())]
        return payload
    provider = RecoveringResearchPriceProvider(official_getter=official, actions_getter=truncated)
    assert recover(provider)["2330.TW"] == []


def test_action_history_endpoints_cannot_hide_an_unverified_internal_date():
    def missing_middle(url, **kwargs):
        payload = actions(url, **kwargs)
        payload["chart"]["result"][0]["timestamp"] = [
            int(datetime(2026, 10, day, tzinfo=ZoneInfo("Asia/Taipei")).timestamp()) for day in (12, 14)]
        return payload
    provider = RecoveringResearchPriceProvider(official_getter=official, actions_getter=missing_middle)
    original = {"2330.TW": [{"date": "2026-10-14", "open": 100, "high": 103,
        "low": 99, "close": 102, "corporate_actions_checked": True}]}
    def month(url, **kwargs):
        payload = official(url, **kwargs)
        payload["data"][0][0] = "115/10/13"
        return payload
    provider.official_getter = month
    rows = provider.repair(original, required_dates_by_symbol={"2330.TW": [date(2026, 10, 12), date(2026, 10, 13), DAY]},
                           as_of_date=DAY)["2330.TW"]
    assert [row["date"] for row in rows] == ["2026-10-14"]
    assert provider.diagnostics[-1]["reason"] == "research_corporate_action_coverage_unverified"


def test_complete_yahoo_prices_still_reject_conflicting_official_archive(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("complete archived dates need no recovery HTTP requests")
    monkeypatch.setattr("ai_stock_sentinel.calibration.research_prices.get_taiwan_daily_bars",
        lambda *args, **kwargs: [SimpleNamespace(trade_date=DAY, open=100, high=103,
            low=99, close=102, source_dataset="TWSE_MI_INDEX")])
    provider = RecoveringResearchPriceProvider(session=object(), official_getter=forbidden,
                                               actions_getter=forbidden)
    original = {"date": str(DAY), "open": 100, "high": 103, "low": 99,
                "close": 101, "corporate_actions_checked": True}
    row = recover(provider, {"2330.TW": [original]})["2330.TW"][0]
    assert row["close"] == 101
    assert row["ohlc_conflict_fields"] == ["close"]
    assert "ohlc_conflict_fields" not in original
    diagnostic = provider.diagnostics[-1]
    assert diagnostic["reason"] == "research_official_price_conflict"
    assert diagnostic["conflicting_prices"] == [{"date": str(DAY), "field": "close",
                                                "provider_value": 101, "official_value": 102}]


def test_complete_matching_archive_needs_no_extra_recovery_requests(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("matching prices must not consume the recovery request budget")
    monkeypatch.setattr("ai_stock_sentinel.calibration.research_prices.get_taiwan_daily_bars",
        lambda *args, **kwargs: [SimpleNamespace(trade_date=DAY, open=100, high=103,
            low=99, close=102, source_dataset="TWSE_MI_INDEX")])
    provider = RecoveringResearchPriceProvider(session=object(), official_getter=forbidden,
                                               actions_getter=forbidden)
    original = {"date": str(DAY), "open": 100, "high": 103, "low": 99,
                "close": 102, "corporate_actions_checked": True}
    assert recover(provider, {"2330.TW": [original]})["2330.TW"] == [original]
    assert provider.request_count == 0
    assert provider.diagnostics == []


@pytest.mark.parametrize("remote_failure", [False, True])
def test_archived_conflict_is_marked_before_recovering_another_missing_date(monkeypatch, remote_failure):
    previous = date(2026, 10, 13)
    monkeypatch.setattr("ai_stock_sentinel.calibration.research_prices.get_taiwan_daily_bars",
        lambda *args, **kwargs: [SimpleNamespace(trade_date=previous, open=100, high=103,
            low=99, close=102, source_dataset="TWSE_MI_INDEX")])
    calls = []
    def remote(url, **kwargs):
        calls.append(url)
        if remote_failure:
            raise TimeoutError()
        payload = official(url, **kwargs)
        payload["data"].insert(0, ["115/10/13", "1000", "101000", "100", "103", "99", "101"])
        return payload
    original = {"date": str(previous), "open": 100, "high": 103, "low": 99,
                "close": 101, "corporate_actions_checked": True}
    provider = RecoveringResearchPriceProvider(session=object(), official_getter=remote, actions_getter=actions)
    rows = provider.repair({"2330.TW": [original]},
        required_dates_by_symbol={"2330.TW": [previous, DAY]}, as_of_date=DAY)["2330.TW"]
    assert rows[0]["ohlc_conflict_fields"] == ["close"]
    assert rows[0]["close"] == 101
    assert provider.diagnostics[-1]["reason"] == "research_official_price_conflict"
    assert calls == []


def test_remote_gap_recovery_preserves_an_existing_matching_archive(monkeypatch):
    previous = date(2026, 10, 13)
    monkeypatch.setattr("ai_stock_sentinel.calibration.research_prices.get_taiwan_daily_bars",
        lambda *args, **kwargs: [SimpleNamespace(trade_date=previous, open=100, high=103,
            low=99, close=102, source_dataset="TWSE_MI_INDEX")])
    def remote(url, **kwargs):
        payload = official(url, **kwargs)
        payload["data"].insert(0, ["115/10/13", "1000", "101000", "100", "103", "99", "101"])
        return payload
    original = {"date": str(previous), "open": 100, "high": 103, "low": 99,
                "close": 102, "corporate_actions_checked": True}
    provider = RecoveringResearchPriceProvider(session=object(), official_getter=remote, actions_getter=actions)
    rows = provider.repair({"2330.TW": [original]},
        required_dates_by_symbol={"2330.TW": [previous, DAY]}, as_of_date=DAY)["2330.TW"]
    assert [row["date"] for row in rows] == [str(previous), str(DAY)]
    assert "ohlc_conflict_fields" not in rows[0]
