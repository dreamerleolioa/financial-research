from __future__ import annotations

from datetime import date

import pytest

from ai_stock_sentinel.data_sources import official_http
from ai_stock_sentinel.daily_radar.background_context import BackgroundContextPayload
from ai_stock_sentinel.daily_radar.default_background_context import DefaultBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.official_background_context import (
    OfficialBackgroundChipContextProvider,
    OfficialBackgroundContextError,
    TPEX_MARGIN_URL,
    TWSE_LENDING_URL,
    TWSE_MARGIN_URL,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_twse_margin_uses_responsive_official_route() -> None:
    assert TWSE_MARGIN_URL == "https://www.twse.com.tw/exchangeReport/MI_MARGN"


def test_twse_margin_falls_back_to_rwd_route_for_same_date() -> None:
    fallback_url = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
    calls: list[tuple[str, str]] = []

    def fake_get(url: str, *, params: dict, **_kwargs):
        calls.append((url, params["date"]))
        if url == TWSE_MARGIN_URL:
            raise TimeoutError("primary route timed out")
        assert url == fallback_url
        return _FakeResponse(
            _twse_margin_payload(
                params["date"],
                [["2330", "台積電", "0", "0", "0", "900", "1,000", "0", "0", "0", "0", "40", "50", "0", "0", ""]],
            )
        )

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=1,
        max_lookback_calendar_days=1,
    )

    [payload] = list(
        provider.fetch(
            symbols=["2330.TW"],
            context_types=["full_margin"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
    )

    assert calls == [
        (TWSE_MARGIN_URL, "20260610"),
        (fallback_url, "20260610"),
    ]
    assert payload.payload["latest_margin_balance"] == 1000.0


def test_twse_margin_skips_failed_historical_date_after_latest_succeeds() -> None:
    fallback_url = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"

    def fake_get(url: str, *, params: dict, **_kwargs):
        if params["date"] == "20260609":
            raise TimeoutError(f"{url} timed out")
        return _FakeResponse(
            _twse_margin_payload(
                params["date"],
                [["2330", "台積電", "0", "0", "0", "900", "1,000", "0", "0", "0", "0", "40", "50", "0", "0", ""]],
            )
        )

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=2,
        max_lookback_calendar_days=3,
    )

    [payload] = list(
        provider.fetch(
            symbols=["2330.TW"],
            context_types=["full_margin"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
    )

    assert fallback_url != TWSE_MARGIN_URL
    assert payload.payload["data_dates"] == ["2026-06-08", "2026-06-10"]


def test_twse_margin_keeps_latest_date_failure_fail_closed() -> None:
    def fake_get(url: str, *, params: dict, **_kwargs):
        raise TimeoutError(f"{url} timed out for {params['date']}")

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=1,
        max_lookback_calendar_days=1,
    )

    with pytest.raises(OfficialBackgroundContextError, match="official_request_failed"):
        list(
            provider.fetch(
                symbols=["2330.TW"],
                context_types=["full_margin"],
                run_date=date(2026, 6, 10),
                market="TW",
            )
        )


def test_twse_margin_fails_when_request_gaps_leave_lookback_incomplete() -> None:
    def fake_get(url: str, *, params: dict, **_kwargs):
        if params["date"] != "20260610":
            raise TimeoutError(f"{url} timed out for {params['date']}")
        return _FakeResponse(
            _twse_margin_payload(
                params["date"],
                [["2330", "台積電", "0", "0", "0", "900", "1,000", "0", "0", "0", "0", "40", "50", "0", "0", ""]],
            )
        )

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=2,
        max_lookback_calendar_days=2,
    )

    with pytest.raises(
        OfficialBackgroundContextError,
        match="official_margin_lookback_incomplete",
    ):
        list(
            provider.fetch(
                symbols=["2330.TW"],
                context_types=["full_margin"],
                run_date=date(2026, 6, 10),
                market="TW",
            )
        )


def test_twse_margin_retries_fallback_with_total_three_attempt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_url = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
    calls: list[str] = []

    def curl_get(url: str, **kwargs):
        calls.append(url)
        if url == TWSE_MARGIN_URL or calls.count(fallback_url) == 1:
            raise official_http.curl_requests.exceptions.Timeout("temporary timeout")
        return _FakeResponse(
            _twse_margin_payload(
                kwargs["params"]["date"],
                [["2330", "台積電", "0", "0", "0", "900", "1,000", "0", "0", "0", "0", "40", "50", "0", "0", ""]],
            )
        )

    monkeypatch.setattr(official_http.curl_requests, "get", curl_get)
    monkeypatch.setattr(official_http.time, "sleep", lambda _seconds: None)
    provider = OfficialBackgroundChipContextProvider(
        lookback_trading_days=1,
        max_lookback_calendar_days=1,
    )

    [payload] = list(
        provider.fetch(
            symbols=["2330.TW"],
            context_types=["full_margin"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
    )

    assert calls == [TWSE_MARGIN_URL, fallback_url, fallback_url]
    assert payload.payload["latest_margin_balance"] == 1000.0


def test_twse_margin_stops_after_historical_request_failure_budget() -> None:
    calls: list[tuple[str, str, int]] = []

    def fake_get(url: str, *, params: dict, timeout: int, **_kwargs):
        calls.append((url, params["date"], timeout))
        if params["date"] != "20260610":
            raise TimeoutError(f"{url} timed out for {params['date']}")
        return _FakeResponse(
            _twse_margin_payload(
                params["date"],
                [["2330", "台積電", "0", "0", "0", "900", "1,000", "0", "0", "0", "0", "40", "50", "0", "0", ""]],
            )
        )

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=3,
        max_lookback_calendar_days=37,
        timeout=30,
    )

    with pytest.raises(
        OfficialBackgroundContextError,
        match="official_margin_request_failure_budget_exceeded",
    ):
        list(
            provider.fetch(
                symbols=["2330.TW"],
                context_types=["full_margin"],
                run_date=date(2026, 6, 10),
                market="TW",
            )
        )

    assert [call[1] for call in calls] == [
        "20260610",
        "20260609",
        "20260609",
        "20260608",
        "20260608",
    ]
    assert [call[2] for call in calls] == [30, 10, 10, 10, 10]


def _twse_margin_payload(
    payload_date: str,
    rows: list[list[str]],
) -> dict:
    return {
        "stat": "OK",
        "date": payload_date,
        "tables": [
            {"fields": ["項目"], "data": []},
            {
                "fields": [
                    "代號",
                    "名稱",
                    "買進",
                    "賣出",
                    "現金償還",
                    "前日餘額",
                    "今日餘額",
                    "次一營業日限額",
                    "買進",
                    "賣出",
                    "現券償還",
                    "前日餘額",
                    "今日餘額",
                    "次一營業日限額",
                    "資券互抵",
                    "註記",
                ],
                "data": rows,
            },
        ],
    }


def _tpex_margin_payload(payload_date: str, rows: list[list[str]]) -> dict:
    return {
        "stat": "ok",
        "date": payload_date,
        "tables": [
            {
                "fields": [
                    "代號",
                    "名稱",
                    "前資餘額(張)",
                    "資買",
                    "資賣",
                    "現償",
                    "資餘額",
                    "資屬證金",
                    "資使用率(%)",
                    "資限額",
                    "前券餘額(張)",
                    "券賣",
                    "券買",
                    "券償",
                    "券餘額",
                    "券屬證金",
                    "券使用率(%)",
                    "券限額",
                    "資券相抵(張)",
                    "備註",
                ],
                "data": rows,
            }
        ],
    }


def test_official_margin_fetches_each_market_date_once_for_many_symbols() -> None:
    calls: list[tuple[str, str]] = []

    twse_by_date = {
        "20260610": _twse_margin_payload(
            "20260610",
            [
                ["2330", "台積電", "0", "0", "0", "1,000", "1,200", "0", "0", "0", "0", "50", "80", "0", "0", ""],
                ["2317", "鴻海", "0", "0", "0", "2,000", "2,100", "0", "0", "0", "0", "60", "70", "0", "0", ""],
            ],
        ),
        "20260609": _twse_margin_payload(
            "20260609",
            [
                ["2330", "台積電", "0", "0", "0", "900", "1,000", "0", "0", "0", "0", "40", "50", "0", "0", ""],
                ["2317", "鴻海", "0", "0", "0", "1,900", "2,000", "0", "0", "0", "0", "50", "60", "0", "0", ""],
            ],
        ),
    }
    tpex_by_date = {
        "2026/06/10": _tpex_margin_payload(
            "20260610",
            [["8069", "元太", "300", "0", "0", "0", "350", "0", "0", "0", "20", "0", "0", "0", "25", "0", "0", "0", "0", ""]],
        ),
        "2026/06/09": _tpex_margin_payload(
            "20260609",
            [["8069", "元太", "250", "0", "0", "0", "300", "0", "0", "0", "15", "0", "0", "0", "20", "0", "0", "0", "0", ""]],
        ),
    }

    def fake_get(url: str, *, params: dict, **kwargs):
        calls.append((url, params["date"]))
        if url == TWSE_MARGIN_URL:
            return _FakeResponse(twse_by_date[params["date"]])
        if url == TPEX_MARGIN_URL:
            return _FakeResponse(tpex_by_date[params["date"]])
        raise AssertionError(url)

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=2,
        max_lookback_calendar_days=2,
    )
    payloads = list(
        provider.fetch(
            symbols=["2330.TW", "2317.TW", "8069.TWO"],
            context_types=["full_margin"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
    )

    assert len(calls) == 4
    by_symbol = {payload.symbol: payload for payload in payloads}
    assert by_symbol["2330.TW"].payload["latest_margin_balance"] == 1200.0
    assert by_symbol["2330.TW"].payload["margin_balance_delta"] == 300.0
    assert by_symbol["2330.TW"].payload["short_balance_delta"] == 40.0
    assert by_symbol["2317.TW"].payload["latest_margin_balance"] == 2100.0
    assert by_symbol["8069.TWO"].payload["latest_margin_balance"] == 350.0
    assert by_symbol["8069.TWO"].source["dataset"] == "TPEX_margin_balance"
    assert by_symbol["8069.TWO"].source["market"] == "TWO"
    assert by_symbol["8069.TWO"].payload["unit"] == "trading_lots"


def test_official_tpex_margin_counts_unique_payload_dates_only() -> None:
    calls: list[str] = []

    latest = _tpex_margin_payload(
        "20260610",
        [["8069", "元太", "300", "0", "0", "0", "350", "0", "0", "0", "20", "0", "0", "0", "25", "0", "0", "0", "0", ""]],
    )
    earlier = _tpex_margin_payload(
        "20260608",
        [["8069", "元太", "250", "0", "0", "0", "300", "0", "0", "0", "15", "0", "0", "0", "20", "0", "0", "0", "0", ""]],
    )

    def fake_get(url: str, *, params: dict, **kwargs):
        assert url == TPEX_MARGIN_URL
        calls.append(params["date"])
        return _FakeResponse(
            earlier if params["date"] == "2026/06/08" else latest
        )

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=2,
        max_lookback_calendar_days=3,
    )

    [payload] = list(
        provider.fetch(
            symbols=["8069.TWO"],
            context_types=["full_margin"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
    )

    assert calls == ["2026/06/10", "2026/06/09", "2026/06/08"]
    assert payload.payload["row_count"] == 2
    assert payload.payload["data_dates"] == ["2026-06-08", "2026-06-10"]
    assert payload.payload["margin_balance_delta"] == 100.0


def test_official_margin_marks_zero_baseline_percentage_as_not_applicable() -> None:
    latest = _twse_margin_payload(
        "20260818",
        [["4590", "富田", "0", "0", "0", "0", "262", "0", "0", "0", "0", "0", "0", "0", "0", "0"]],
    )
    earlier = _twse_margin_payload(
        "20260817",
        [["4590", "富田", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0"]],
    )

    def fake_get(_url: str, *, params: dict, **_kwargs):
        return _FakeResponse(latest if params["date"] == "20260818" else earlier)

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=2,
        max_lookback_calendar_days=2,
    )
    [payload] = list(
        provider.fetch(
            symbols=["4590.TW"],
            context_types=["full_margin"],
            run_date=date(2026, 8, 18),
            market="TW",
        )
    )

    assert payload.freshness == "fresh"
    assert payload.payload["latest_margin_balance"] == 262.0
    assert payload.payload["margin_balance_delta"] == 262.0
    assert payload.payload["margin_balance_delta_pct"] is None
    assert payload.payload["margin_balance_delta_pct_unavailable_reason"] == "baseline_zero"


def test_official_lending_reads_live_top_level_schema_and_aggregates_by_date() -> None:
    calls = 0

    def fake_get(url: str, *, params: dict, **kwargs):
        nonlocal calls
        calls += 1
        assert url == TWSE_LENDING_URL
        return _FakeResponse(
            {
                "stat": "OK",
                "fields": [
                    "成交日期",
                    "證券代號名稱",
                    "交易方式",
                    "成交數量(交易單位)",
                ],
                "data": [
                    ["115年06月09日", "2330 台積電", "競價", "100"],
                    ["115年06月10日", "2330 台積電", "競價", "125"],
                    ["115年06月10日", "2330 台積電", "議借", "25"],
                    ["115年06月10日", "8069 元太", "競價", "80"],
                ],
            }
        )

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=2,
        max_lookback_calendar_days=2,
        lending_window_days=7,
    )
    payloads = list(
        provider.fetch(
            symbols=["2330.TW", "8069.TWO"],
            context_types=["lending"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
    )

    assert calls == 1
    by_symbol = {payload.symbol: payload for payload in payloads}
    assert by_symbol["2330.TW"].payload["latest_daily_lending_volume"] == 150.0
    assert by_symbol["2330.TW"].payload["period_lending_volume"] == 250.0
    assert by_symbol["2330.TW"].payload["lending_volume_delta"] == 50.0
    assert by_symbol["8069.TWO"].payload["latest_daily_lending_volume"] == 80.0
    assert by_symbol["2330.TW"].payload["unit"] == "twse_lending_trading_unit"


def test_official_lending_zero_fills_market_dates_without_symbol_activity() -> None:
    def fake_get(url: str, *, params: dict, **kwargs):
        return _FakeResponse(
            {
                "stat": "OK",
                "fields": ["成交日期", "證券代號名稱", "成交數量(交易單位)"],
                "data": [
                    ["115年06月09日", "2330 台積電", "100"],
                    ["115年06月10日", "2330 台積電", "125"],
                ],
            }
        )

    provider = OfficialBackgroundChipContextProvider(
        request_get=fake_get,
        lookback_trading_days=2,
        max_lookback_calendar_days=2,
    )

    payload = next(
        item
        for item in provider.fetch(
            symbols=["2454.TW"],
            context_types=["lending"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
        if item.symbol == "2454.TW"
    )

    assert payload.freshness == "fresh"
    assert payload.missing_reason is None
    assert payload.as_of_date == date(2026, 6, 10)
    assert payload.payload["data_dates"] == ["2026-06-09", "2026-06-10"]
    assert payload.payload["latest_daily_lending_volume"] == 0.0
    assert payload.payload["period_lending_volume"] == 0.0
    assert payload.payload["zero_filled_day_count"] == 2


class _MissingOfficialProvider:
    def fetch(self, *, symbols: list[str], context_types: list[str], run_date: date, market: str):
        return [
            BackgroundContextPayload(
                symbol=symbol,
                context_type=context_types[0],
                applicable_consumers=("daily_radar",),
                source={"provider": "official"},
                as_of_date=None,
                freshness="missing",
                payload={},
                missing_reason="official_no_data",
                replay_key=f"missing:{symbol}:{context_types[0]}",
            )
            for symbol in symbols
        ]


class _FailingOfficialProvider:
    def fetch(self, *, symbols: list[str], context_types: list[str], run_date: date, market: str):
        raise OfficialBackgroundContextError("official_request_failed", dataset="fixture")


class _CountingFinMindProvider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, *, symbols: list[str], context_types: list[str], run_date: date, market: str):
        self.calls += 1
        return [
            BackgroundContextPayload(
                symbol=symbol,
                context_type=context_types[0],
                applicable_consumers=("daily_radar",),
                source={"provider": "finmind"},
                as_of_date=run_date,
                freshness="fresh",
                payload={"fallback": True},
                missing_reason=None,
                replay_key=f"fallback:{symbol}:{context_types[0]}",
            )
            for symbol in symbols
        ]


class _EmptyTdccProvider:
    def fetch(self, *, symbols: list[str], context_types: list[str], run_date: date, market: str):
        return []


def test_official_first_does_not_fallback_for_legitimate_symbol_no_data() -> None:
    finmind = _CountingFinMindProvider()
    provider = DefaultBackgroundChipContextProvider(
        finmind_provider=finmind,  # type: ignore[arg-type]
        official_provider=_MissingOfficialProvider(),  # type: ignore[arg-type]
        tdcc_provider=_EmptyTdccProvider(),  # type: ignore[arg-type]
        provider_mode="official_first",
    )

    payloads = list(
        provider.fetch(
            symbols=["2330.TW"],
            context_types=["full_margin"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
    )

    assert finmind.calls == 0
    assert payloads[0].missing_reason == "official_no_data"
    assert payloads[0].source["provider"] == "official"


def test_official_first_falls_back_only_when_whole_dataset_fails() -> None:
    finmind = _CountingFinMindProvider()
    provider = DefaultBackgroundChipContextProvider(
        finmind_provider=finmind,  # type: ignore[arg-type]
        official_provider=_FailingOfficialProvider(),  # type: ignore[arg-type]
        tdcc_provider=_EmptyTdccProvider(),  # type: ignore[arg-type]
        provider_mode="official_first",
    )

    payloads = list(
        provider.fetch(
            symbols=["2330.TW"],
            context_types=["full_margin"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
    )

    assert finmind.calls == 1
    assert payloads[0].source["provider"] == "finmind"


def test_official_first_keeps_existing_fallback_for_unsupported_etf() -> None:
    finmind = _CountingFinMindProvider()
    provider = DefaultBackgroundChipContextProvider(
        finmind_provider=finmind,  # type: ignore[arg-type]
        official_provider=_MissingOfficialProvider(),  # type: ignore[arg-type]
        tdcc_provider=_EmptyTdccProvider(),  # type: ignore[arg-type]
        provider_mode="official_first",
    )

    payloads = list(
        provider.fetch(
            symbols=["0050.TW"],
            context_types=["full_margin"],
            run_date=date(2026, 6, 10),
            market="TW",
        )
    )

    assert finmind.calls == 1
    assert payloads[0].source["provider"] == "finmind"


def test_official_only_propagates_dataset_failure() -> None:
    provider = DefaultBackgroundChipContextProvider(
        finmind_provider=_CountingFinMindProvider(),  # type: ignore[arg-type]
        official_provider=_FailingOfficialProvider(),  # type: ignore[arg-type]
        tdcc_provider=_EmptyTdccProvider(),  # type: ignore[arg-type]
        provider_mode="official_only",
    )

    with pytest.raises(OfficialBackgroundContextError):
        list(
            provider.fetch(
                symbols=["2330.TW"],
                context_types=["full_margin"],
                run_date=date(2026, 6, 10),
                market="TW",
            )
        )


def test_default_background_provider_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="DAILY_RADAR_BACKGROUND_PROVIDER_MODE"):
        DefaultBackgroundChipContextProvider(provider_mode="unknown")
