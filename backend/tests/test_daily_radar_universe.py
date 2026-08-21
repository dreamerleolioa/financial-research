from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from ai_stock_sentinel.daily_radar.institutional_universe_provider import (
    InstitutionalUniverseProviderError,
    TWSE_FOREIGN_BUY_TOP_REPORT,
    TWSE_TRUST_BUY_TOP_REPORT,
    TwseRwdInstitutionalUniverseProvider,
    _DEADLINE_WORKER_SLOTS,
    _MAX_IN_FLIGHT_DEADLINE_WORKERS,
    _request_payload_with_deadline,
)
from ai_stock_sentinel.daily_radar.universe import (
    InstitutionalLeaderRow,
    is_daily_radar_supported_symbol,
    select_daily_radar_universe,
    select_dual_track_universe,
)


@dataclass
class _Provider:
    same_day_rows: list[InstitutionalLeaderRow]
    recent_rows: list[InstitutionalLeaderRow]
    calls: list[tuple[str, date, str, int]]

    def same_day_institutional_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> list[InstitutionalLeaderRow]:
        self.calls.append(("same_day", run_date, market, limit))
        return self.same_day_rows[:limit]

    def recent_accumulation_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> list[InstitutionalLeaderRow]:
        self.calls.append(("recent", run_date, market, limit))
        return self.recent_rows[:limit]


def _provider(
    same_day_rows: list[InstitutionalLeaderRow] | None = None,
    recent_rows: list[InstitutionalLeaderRow] | None = None,
) -> _Provider:
    return _Provider(same_day_rows or [], recent_rows or [], [])


class _FakeTwseResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _wait_for_deadline_workers(*, at_most: int, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = sum(
            thread.name == "twse-institutional-universe-request" and thread.is_alive()
            for thread in threading.enumerate()
        )
        if active <= at_most:
            return
        time.sleep(0.01)
    raise AssertionError("deadline request workers did not exit in time")


def _twse_foreign_row(*, stock_id: str, buy: str | int, sell: str | int, net: str | int) -> list[str]:
    return [" ", f"{stock_id}  ", "測試", "0", "0", "0", "0", "0", "0", str(buy), str(sell), str(net)]


def _twse_trust_row(*, stock_id: str, buy: str | int, sell: str | int, net: str | int) -> list[str]:
    return [" ", f"{stock_id}  ", "測試", str(buy), str(sell), str(net)]


def _twse_marked_foreign_row(*, stock_id: str, buy: str | int, sell: str | int, net: str | int) -> list[str]:
    return ["*", f"{stock_id}  ", "測試", "0", "0", "0", "0", "0", "0", str(buy), str(sell), str(net)]


def _twse_foreign_row_without_blank(*, stock_id: str, buy: str | int, sell: str | int, net: str | int) -> list[str]:
    return [f"{stock_id}  ", "測試", "0", "0", "0", "0", "0", "0", str(buy), str(sell), str(net)]


def _twse_trust_row_without_blank(*, stock_id: str, buy: str | int, sell: str | int, net: str | int) -> list[str]:
    return [f"{stock_id}  ", "測試", str(buy), str(sell), str(net)]


def _twse_payload(rows: list[list[str]], *, stat: str = "OK") -> dict[str, Any]:
    return {"stat": stat, "data": rows}


def _technical_record(
    symbol: str,
    *,
    close: float = 106.0,
    previous_close: float = 102.0,
    low: float = 98.0,
    volume_ratio: float = 1.6,
    ma5: float = 104.0,
    ma20: float = 101.0,
    support_level: float = 97.0,
    kd_k: float = 32.0,
    kd_d: float = 28.0,
    rsi14: float = 48.0,
    macd_histogram: float = 0.05,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "ohlcv": {"close": close, "previous_close": previous_close, "low": low},
        "indicators": {
            "volume_ratio": volume_ratio,
            "ma5": ma5,
            "ma20": ma20,
            "support_level": support_level,
            "kd_k": kd_k,
            "kd_d": kd_d,
            "rsi14": rsi14,
            "macd_histogram": macd_histogram,
        },
    }


def test_select_dual_track_universe_unions_top_n_tracks_with_overlap_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Daily Radar universe selection must stay offline")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    run_date = date(2026, 6, 2)
    provider = _provider(
        same_day_rows=[
            InstitutionalLeaderRow("2330.TW", rank=1, score=120.0),
            InstitutionalLeaderRow("2454.TW", rank=2, score=110.0),
            InstitutionalLeaderRow("3034.TW", rank=3, score=100.0),
            InstitutionalLeaderRow("9999.TW", rank=4, score=90.0),
        ],
        recent_rows=[
            InstitutionalLeaderRow("2454.TW", rank=1, score=75.0),
            InstitutionalLeaderRow("2303.TW", rank=2, score=70.0),
            InstitutionalLeaderRow("3711.TW", rank=3, score=65.0),
            InstitutionalLeaderRow("8888.TW", rank=4, score=60.0),
        ],
    )

    universe = select_dual_track_universe(provider, run_date, market="TW", track_limit=3)

    assert [entry.symbol for entry in universe] == [
        "2330.TW",
        "2454.TW",
        "3034.TW",
        "2303.TW",
        "3711.TW",
    ]
    assert [entry.rank for entry in universe] == [1, 2, 3, 4, 5]
    assert provider.calls == [
        ("same_day", run_date, "TW", 3),
        ("recent", run_date, "TW", 3),
    ]

    overlap = universe[1]
    assert overlap.symbol == "2454.TW"
    assert overlap.primary_track == "same_day_institutional"
    assert overlap.tracks == ("same_day_institutional", "recent_accumulation")
    assert overlap.same_day_rank == 2
    assert overlap.same_day_score == pytest.approx(110.0)
    assert overlap.recent_accumulation_rank == 1
    assert overlap.recent_accumulation_score == pytest.approx(75.0)


def test_select_dual_track_universe_dedupes_deterministically_by_first_track_order() -> None:
    provider = _provider(
        same_day_rows=[
            InstitutionalLeaderRow("2330.TW", rank=1, score=120.0),
            InstitutionalLeaderRow("2330.TW", rank=2, score=999.0),
            InstitutionalLeaderRow("2454.TW", rank=3, score=100.0),
        ],
        recent_rows=[
            InstitutionalLeaderRow("2454.TW", rank=1, score=80.0),
            InstitutionalLeaderRow("2303.TW", rank=2, score=70.0),
            InstitutionalLeaderRow("2303.TW", rank=3, score=999.0),
        ],
    )

    universe = select_dual_track_universe(provider, date(2026, 6, 2), track_limit=50)

    assert [entry.symbol for entry in universe] == ["2330.TW", "2454.TW", "2303.TW"]
    assert universe[0].same_day_rank == 1
    assert universe[0].same_day_score == pytest.approx(120.0)
    assert universe[1].tracks == ("same_day_institutional", "recent_accumulation")
    assert universe[2].primary_track == "recent_accumulation"
    assert universe[2].recent_accumulation_rank == 2
    assert universe[2].recent_accumulation_score == pytest.approx(70.0)


def test_select_dual_track_universe_returns_empty_list_for_empty_provider() -> None:
    provider = _provider()

    universe = select_dual_track_universe(provider, date(2026, 6, 2))

    assert universe == []


def test_select_dual_track_universe_keeps_legacy_institutional_only_metrics_shape() -> None:
    provider = _provider(same_day_rows=[InstitutionalLeaderRow("2330.TW", rank=1, score=120.0)])

    universe = select_dual_track_universe(provider, date(2026, 6, 2))

    assert universe[0].tracks == ("same_day_institutional",)
    assert set(universe[0].track_metrics) == {"same_day_institutional"}


def test_select_daily_radar_universe_merges_daily_trigger_tracks_deterministically() -> None:
    provider = _provider(
        same_day_rows=[
            InstitutionalLeaderRow("2330.TW", rank=1, score=120.0),
            InstitutionalLeaderRow("2454.TW", rank=2, score=110.0),
        ],
        recent_rows=[InstitutionalLeaderRow("2303.TW", rank=1, score=70.0)],
    )
    technical_records = [
        _technical_record("2454.TW", close=110.0, previous_close=105.0, volume_ratio=1.8, ma5=108.0, ma20=103.0),
        _technical_record("1101.TW", close=52.0, previous_close=50.0, volume_ratio=1.3, ma5=51.0, ma20=49.0, kd_k=70.0, kd_d=80.0, rsi14=70.0, macd_histogram=-1.0),
        _technical_record("3034.TW", close=84.0, previous_close=82.0, low=79.0, volume_ratio=0.8, ma5=70.0, ma20=90.0, support_level=78.0, kd_k=30.0, kd_d=24.0, rsi14=44.0),
        _technical_record("2303.TW", close=52.0, previous_close=48.0, support_level=50.0, kd_k=60.0, kd_d=62.0, ma5=49.0, ma20=50.0, volume_ratio=1.1),
    ]

    universe = select_daily_radar_universe(
        provider,
        date(2026, 6, 2),
        market="TW",
        track_limit=2,
        technical_records=technical_records,
    )

    assert [entry.symbol for entry in universe] == ["2330.TW", "2454.TW", "2303.TW", "1101.TW", "3034.TW"]
    assert [entry.rank for entry in universe] == [1, 2, 3, 4, 5]
    assert universe[1].primary_track == "same_day_institutional"
    assert universe[1].tracks[:2] == ("same_day_institutional", "price_volume")
    assert universe[1].track_metrics["price_volume"]["rank"] == 1
    assert universe[1].track_metrics["price_volume"]["score"] > universe[3].track_metrics["price_volume"]["score"]
    assert universe[2].tracks == ("recent_accumulation", "support_retake")
    assert universe[2].track_metrics["support_retake"]["matched"] is True
    assert universe[3].primary_track == "price_volume"
    assert universe[4].primary_track == "reversal"


def test_select_daily_radar_universe_keeps_missing_trigger_trace_for_selected_symbols() -> None:
    provider = _provider(same_day_rows=[InstitutionalLeaderRow("2330.TW", rank=1, score=120.0)])

    universe = select_daily_radar_universe(
        provider,
        date(2026, 6, 2),
        technical_records=[{"symbol": "2330.TW", "ohlcv": {"close": 100.0}, "indicators": {}}],
    )

    assert universe[0].tracks == ("same_day_institutional",)
    price_volume_trace = universe[0].track_metrics["price_volume"]
    assert price_volume_trace["matched"] is False
    assert price_volume_trace["missing_data"] is True
    assert price_volume_trace["reason"] == "insufficient_technical_data"
    assert "ohlcv.previous_close" in price_volume_trace["missing_fields"]


def test_twse_rwd_provider_fetches_top_buy_reports_and_feeds_selector() -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    payloads = {
        (TWSE_FOREIGN_BUY_TOP_REPORT, "20260602"): _twse_payload(
            [
                _twse_foreign_row(stock_id="2330", buy=100, sell=10, net=90),
                _twse_foreign_row(stock_id="2454", buy=60, sell=10, net=50),
            ]
        ),
        (TWSE_TRUST_BUY_TOP_REPORT, "20260602"): _twse_payload(
            [
                _twse_trust_row(stock_id="2330", buy=30, sell=0, net=30),
                _twse_trust_row(stock_id="2454", buy=70, sell=20, net=50),
                _twse_trust_row(stock_id="3711", buy=35, sell=0, net=35),
            ]
        ),
        (TWSE_FOREIGN_BUY_TOP_REPORT, "20260529"): _twse_payload(
            [_twse_foreign_row(stock_id="3711", buy=30, sell=0, net=30)]
        ),
        (TWSE_TRUST_BUY_TOP_REPORT, "20260529"): _twse_payload([]),
        (TWSE_FOREIGN_BUY_TOP_REPORT, "20260601"): _twse_payload(
            [_twse_foreign_row(stock_id="3711", buy=40, sell=0, net=40)]
        ),
        (TWSE_TRUST_BUY_TOP_REPORT, "20260601"): _twse_payload([]),
    }

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        assert report_id in {TWSE_FOREIGN_BUY_TOP_REPORT, TWSE_TRUST_BUY_TOP_REPORT}
        assert timeout == 15
        assert params["response"] == "json"
        assert params["date"].isdigit()
        assert len(params["date"]) == 8
        assert "dataset" not in params
        assert "data_id" not in params
        assert "stock_id" not in params
        calls.append((report_id, dict(params)))
        return _FakeTwseResponse(payloads.get((report_id, params["date"]), _twse_payload([], stat="很抱歉，沒有符合條件的資料!")))

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    universe = select_dual_track_universe(provider, date(2026, 6, 2), market="TW", track_limit=2)

    assert [entry.symbol for entry in universe] == ["2330.TW", "2454.TW", "3711.TW"]
    assert universe[0].same_day_score == pytest.approx(90.0)
    assert universe[1].same_day_score == pytest.approx(50.0)
    assert universe[2].primary_track == "recent_accumulation"
    assert calls[:2] == [
        (TWSE_FOREIGN_BUY_TOP_REPORT, {"response": "json", "date": "20260602"}),
        (TWSE_TRUST_BUY_TOP_REPORT, {"response": "json", "date": "20260602"}),
    ]


def test_twse_rwd_provider_returns_empty_for_non_ok_stat() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        return _FakeTwseResponse(_twse_payload([], stat="很抱歉，沒有符合條件的資料!"))

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(run_date=date(2026, 6, 6), market="TW", limit=50)

    assert leaders == []


def test_twse_rwd_provider_fails_closed_when_current_market_date_is_unavailable() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        return _FakeTwseResponse(_twse_payload([], stat="很抱歉，沒有符合條件的資料!"))

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    with pytest.raises(InstitutionalUniverseProviderError) as raised:
        provider.same_day_institutional_leaders(
            run_date=date(2026, 6, 2),
            market="TW",
            limit=50,
        )

    assert raised.value.code == "institutional_universe_current_date_unavailable"
    assert raised.value.error_type == "InstitutionalUniverseEmptyResponse"


def test_twse_rwd_provider_retries_unknown_current_status_before_success() -> None:
    calls = 0

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        nonlocal calls
        calls += 1
        report_id = url.rsplit("/", maxsplit=1)[-1]
        if calls == 1:
            return _FakeTwseResponse(_twse_payload([], stat="SYSTEM_ERROR"))
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(
                _twse_payload([_twse_foreign_row(stock_id="2330", buy=100, sell=0, net=100)])
            )
        return _FakeTwseResponse(_twse_payload([]))

    provider = TwseRwdInstitutionalUniverseProvider(
        request_get=fake_get,
        retry_backoff_seconds=0,
    )

    leaders = provider.same_day_institutional_leaders(
        run_date=date(2026, 6, 2),
        market="TW",
        limit=1,
    )

    assert [row.symbol for row in leaders] == ["2330.TW"]
    assert calls == 3


def test_twse_rwd_provider_retries_transient_timeout_before_success() -> None:
    calls: list[tuple[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        calls.append((report_id, params["date"]))
        if len(calls) == 1:
            raise TimeoutError("temporary TWSE timeout")
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(
                _twse_payload([_twse_foreign_row(stock_id="2330", buy=100, sell=0, net=100)])
            )
        return _FakeTwseResponse(_twse_payload([]))

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(
        run_date=date(2026, 6, 2),
        market="TW",
        limit=1,
    )

    assert [row.symbol for row in leaders] == ["2330.TW"]
    assert calls == [
        (TWSE_FOREIGN_BUY_TOP_REPORT, "20260602"),
        (TWSE_FOREIGN_BUY_TOP_REPORT, "20260602"),
        (TWSE_TRUST_BUY_TOP_REPORT, "20260602"),
    ]


def test_twse_rwd_provider_retry_budget_respects_total_deadline() -> None:
    elapsed = 0.0
    request_timeouts: list[float] = []

    def clock() -> float:
        return elapsed

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    def timeout_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeTwseResponse:
        nonlocal elapsed
        request_timeouts.append(timeout)
        elapsed += timeout
        raise TimeoutError("persistent TWSE timeout")

    provider = TwseRwdInstitutionalUniverseProvider(
        request_get=timeout_get,
        timeout=15,
        max_attempts=5,
        total_timeout_seconds=20,
        retry_backoff_seconds=0.25,
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(InstitutionalUniverseProviderError) as raised:
        provider.same_day_institutional_leaders(
            run_date=date(2026, 6, 2),
            market="TW",
            limit=1,
        )

    assert raised.value.error_type == "TimeoutError"
    assert elapsed == pytest.approx(20.0)
    assert request_timeouts == pytest.approx([15.0, 4.75])


def test_twse_rwd_provider_enforces_deadline_when_transport_ignores_timeout() -> None:
    release_request = threading.Event()

    def blocking_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeTwseResponse:
        release_request.wait(timeout=2)
        return _FakeTwseResponse(
            _twse_payload([_twse_foreign_row(stock_id="2330", buy=100, sell=0, net=100)])
        )

    provider = TwseRwdInstitutionalUniverseProvider(
        request_get=blocking_get,
        max_attempts=1,
        total_timeout_seconds=1,
        retry_backoff_seconds=0,
    )

    started_at = time.monotonic()
    try:
        with pytest.raises(InstitutionalUniverseProviderError) as raised:
            provider.same_day_institutional_leaders(
                run_date=date(2026, 6, 2),
                market="TW",
                limit=1,
            )
        elapsed = time.monotonic() - started_at
    finally:
        release_request.set()
        _wait_for_deadline_workers(at_most=0)

    assert raised.value.error_type == "TimeoutError"
    assert elapsed < 1.3


def test_twse_rwd_provider_enforces_deadline_during_response_parsing() -> None:
    release_parsing = threading.Event()

    class _SlowJsonResponse(_FakeTwseResponse):
        def json(self) -> dict[str, Any]:
            release_parsing.wait(timeout=2)
            return super().json()

    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeTwseResponse:
        return _SlowJsonResponse(_twse_payload([]))

    provider = TwseRwdInstitutionalUniverseProvider(
        request_get=fake_get,
        max_attempts=1,
        total_timeout_seconds=1,
        retry_backoff_seconds=0,
    )

    started_at = time.monotonic()
    try:
        with pytest.raises(InstitutionalUniverseProviderError) as raised:
            provider.same_day_institutional_leaders(
                run_date=date(2026, 6, 2),
                market="TW",
                limit=1,
            )
        elapsed = time.monotonic() - started_at
    finally:
        release_parsing.set()
        _wait_for_deadline_workers(at_most=0)

    assert raised.value.error_type == "TimeoutError"
    assert elapsed < 1.3


def test_twse_rwd_deadline_workers_are_process_wide_bounded() -> None:
    release_requests = threading.Event()

    def blocking_get(url: str, *, timeout: float) -> _FakeTwseResponse:
        release_requests.wait(timeout=2)
        return _FakeTwseResponse(_twse_payload([]))

    _wait_for_deadline_workers(at_most=0)
    baseline_workers = sum(
        thread.name == "twse-institutional-universe-request" and thread.is_alive()
        for thread in threading.enumerate()
    )
    try:
        for _ in range(_MAX_IN_FLIGHT_DEADLINE_WORKERS + 1):
            with pytest.raises(TimeoutError):
                _request_payload_with_deadline(
                    blocking_get,
                    "https://example.invalid/twse-test",
                    request_kwargs={"timeout": 0.03},
                    deadline=time.monotonic() + 0.03,
                    clock=time.monotonic,
                )

        active_workers = sum(
            thread.name == "twse-institutional-universe-request" and thread.is_alive()
            for thread in threading.enumerate()
        )
        assert active_workers - baseline_workers == _MAX_IN_FLIGHT_DEADLINE_WORKERS
    finally:
        release_requests.set()
        _wait_for_deadline_workers(at_most=baseline_workers)


def test_twse_rwd_worker_admission_reclamps_transport_timeout() -> None:
    observed_timeouts: list[float] = []
    release_one_slot = threading.Thread(
        target=lambda: (time.sleep(0.08), _DEADLINE_WORKER_SLOTS.release()),
        daemon=True,
    )
    held_slots = 0
    try:
        for _ in range(_MAX_IN_FLIGHT_DEADLINE_WORKERS):
            assert _DEADLINE_WORKER_SLOTS.acquire(blocking=False)
            held_slots += 1
        release_one_slot.start()

        def fake_get(url: str, *, timeout: float) -> _FakeTwseResponse:
            observed_timeouts.append(timeout)
            return _FakeTwseResponse(_twse_payload([]))

        started_at = time.monotonic()
        succeeded, _, _ = _request_payload_with_deadline(
            fake_get,
            "https://example.invalid/twse-test",
            request_kwargs={"timeout": 0.15},
            deadline=started_at + 0.15,
            clock=time.monotonic,
        )

        assert succeeded is True
        assert observed_timeouts[0] < 0.1
        assert observed_timeouts[0] > 0
    finally:
        release_one_slot.join(timeout=1)
        for _ in range(max(0, held_slots - 1)):
            _DEADLINE_WORKER_SLOTS.release()


def test_twse_rwd_provider_retries_current_ok_empty_payload_before_success() -> None:
    calls: list[str] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        calls.append(report_id)
        if len(calls) <= 2:
            return _FakeTwseResponse(_twse_payload([]))
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(
                _twse_payload([_twse_foreign_row(stock_id="2330", buy=100, sell=0, net=100)])
            )
        return _FakeTwseResponse(_twse_payload([]))

    provider = TwseRwdInstitutionalUniverseProvider(
        request_get=fake_get,
        max_attempts=3,
        retry_backoff_seconds=0,
    )

    leaders = provider.same_day_institutional_leaders(
        run_date=date(2026, 6, 2),
        market="TW",
        limit=1,
    )

    assert [row.symbol for row in leaders] == ["2330.TW"]
    assert calls == [
        TWSE_FOREIGN_BUY_TOP_REPORT,
        TWSE_TRUST_BUY_TOP_REPORT,
        TWSE_FOREIGN_BUY_TOP_REPORT,
        TWSE_TRUST_BUY_TOP_REPORT,
    ]


def test_twse_rwd_provider_reuses_current_report_rows_for_recent_track() -> None:
    calls: list[tuple[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        calls.append((report_id, params["date"]))
        if params["date"] == "20260602" and report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(
                _twse_payload([_twse_foreign_row(stock_id="2330", buy=100, sell=0, net=100)])
            )
        if params["date"] == "20260602":
            return _FakeTwseResponse(_twse_payload([]))
        return _FakeTwseResponse(_twse_payload([], stat="很抱歉，沒有符合條件的資料!"))

    provider = TwseRwdInstitutionalUniverseProvider(
        request_get=fake_get,
        recent_market_days=1,
        recent_calendar_window_days=2,
    )

    provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=1)
    provider.recent_accumulation_leaders(run_date=date(2026, 6, 2), market="TW", limit=1)

    assert calls.count((TWSE_FOREIGN_BUY_TOP_REPORT, "20260602")) == 1
    assert calls.count((TWSE_TRUST_BUY_TOP_REPORT, "20260602")) == 1


def test_twse_recent_accumulation_does_not_bridge_unknown_historical_date() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        if params["date"] == "20260601":
            raise TimeoutError("unknown historical trading date")
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT and params["date"] in {"20260529", "20260602"}:
            return _FakeTwseResponse(
                _twse_payload([_twse_foreign_row(stock_id="2330", buy=100, sell=0, net=100)])
            )
        return _FakeTwseResponse(_twse_payload([]))

    provider = TwseRwdInstitutionalUniverseProvider(
        request_get=fake_get,
        recent_market_days=5,
        recent_calendar_window_days=4,
        retry_backoff_seconds=0,
    )

    leaders = provider.recent_accumulation_leaders(
        run_date=date(2026, 6, 2),
        market="TW",
        limit=1,
    )

    assert len(leaders) == 1
    assert leaders[0].source_dates == ("2026-06-02",)
    assert leaders[0].consecutive_buy_days == 1


def test_twse_recent_accumulation_zero_flow_day_breaks_consecutive_buying() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT and params["date"] in {"20260529", "20260602"}:
            return _FakeTwseResponse(
                _twse_payload([_twse_foreign_row(stock_id="2330", buy=100, sell=0, net=100)])
            )
        return _FakeTwseResponse(_twse_payload([]))

    provider = TwseRwdInstitutionalUniverseProvider(
        request_get=fake_get,
        recent_market_days=2,
        recent_calendar_window_days=4,
        retry_backoff_seconds=0,
    )

    leaders = provider.recent_accumulation_leaders(
        run_date=date(2026, 6, 2),
        market="TW",
        limit=1,
    )

    assert len(leaders) == 1
    assert leaders[0].source_dates == ("2026-06-02",)
    assert leaders[0].consecutive_buy_days == 1


def test_twse_same_day_leaders_parse_comma_separated_net_values() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(
                _twse_payload(
                    [
                        _twse_foreign_row(stock_id="2330", buy="1,000", sell="0", net="1,000"),
                        _twse_foreign_row(stock_id="2454", buy="50", sell="0", net="50"),
                    ]
                )
            )
        return _FakeTwseResponse(_twse_payload([]))

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=2)

    assert [row.symbol for row in leaders] == ["2330.TW", "2454.TW"]
    assert leaders[0].score == pytest.approx(1_000.0)
    assert leaders[1].score == pytest.approx(50.0)


def test_daily_radar_supported_symbol_excludes_tw_etfs() -> None:
    assert is_daily_radar_supported_symbol("2330.TW") is True
    assert is_daily_radar_supported_symbol("0050.TW") is False
    assert is_daily_radar_supported_symbol("00631L.TW") is False
    assert is_daily_radar_supported_symbol("00983A.TW") is False


def test_twse_institutional_leaders_skip_etf_and_warrant_like_ids() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(
                _twse_payload(
                    [
                        _twse_foreign_row(stock_id="07652U", buy="10,000", sell="0", net="10,000"),
                        _twse_foreign_row(stock_id="00983A", buy="1,000", sell="0", net="1,000"),
                        _twse_foreign_row(stock_id="0050", buy="800", sell="0", net="800"),
                        _twse_foreign_row(stock_id="2330", buy="500", sell="0", net="500"),
                    ]
                )
            )
        return _FakeTwseResponse(_twse_payload([]))

    provider = TwseRwdInstitutionalUniverseProvider(
        request_get=fake_get,
        recent_market_days=1,
        recent_calendar_window_days=0,
    )

    same_day = provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=3)
    recent = provider.recent_accumulation_leaders(run_date=date(2026, 6, 2), market="TW", limit=3)

    assert [row.symbol for row in same_day] == ["2330.TW"]
    assert [row.symbol for row in recent] == ["2330.TW"]


def test_daily_radar_universe_excludes_etf_from_technical_tracks() -> None:
    universe = select_daily_radar_universe(
        _provider(),
        date(2026, 6, 2),
        technical_records=[_technical_record("0050.TW"), _technical_record("2330.TW")],
    )

    assert [entry.symbol for entry in universe] == ["2330.TW"]


def test_twse_same_day_leaders_support_rows_without_leading_blank_column() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(
                _twse_payload([_twse_foreign_row_without_blank(stock_id="2330", buy="1,200", sell="200", net="1,000")])
            )
        return _FakeTwseResponse(
            _twse_payload([_twse_trust_row_without_blank(stock_id="2454", buy="900", sell="100", net="800")])
        )

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=2)

    assert [(row.symbol, row.actor, row.net_buy) for row in leaders] == [
        ("2330.TW", "foreign", 1_000.0),
        ("2454.TW", "trust", 800.0),
    ]


def test_twse_same_day_leaders_keep_stock_id_at_index_one_when_marker_column_is_present() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(
                _twse_payload([_twse_marked_foreign_row(stock_id="3661", buy="44,822,002", sell="0", net="44,822,002")])
            )
        return _FakeTwseResponse(_twse_payload([]))

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=1)

    assert [(row.symbol, row.actor, row.net_buy) for row in leaders] == [("3661.TW", "foreign", 44_822_002.0)]


def test_twse_same_day_leaders_limit_is_final_combined_cap_after_ranking() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(
                _twse_payload(
                    [
                        _twse_foreign_row(stock_id="2330", buy=100, sell=0, net=100),
                        _twse_foreign_row(stock_id="2454", buy=90, sell=0, net=90),
                        _twse_foreign_row(stock_id="2303", buy=80, sell=0, net=80),
                    ]
                )
            )
        return _FakeTwseResponse(
            _twse_payload(
                [
                    _twse_trust_row(stock_id="3711", buy=95, sell=0, net=95),
                    _twse_trust_row(stock_id="3034", buy=85, sell=0, net=85),
                    _twse_trust_row(stock_id="1101", buy=75, sell=0, net=75),
                ]
            )
        )

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=2)

    assert [row.symbol for row in leaders] == ["2330.TW", "3711.TW"]
    assert [row.rank for row in leaders] == [1, 2]
    assert [row.actor for row in leaders] == ["foreign", "trust"]


def test_twse_same_day_foreign_leader_survives_trust_selling_same_symbol() -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        if report_id == TWSE_FOREIGN_BUY_TOP_REPORT:
            return _FakeTwseResponse(_twse_payload([_twse_foreign_row(stock_id="2330", buy=100, sell=0, net=100)]))
        return _FakeTwseResponse(
            _twse_payload(
                [
                    _twse_trust_row(stock_id="2330", buy=0, sell=150, net=-150),
                    _twse_trust_row(stock_id="2454", buy=80, sell=0, net=80),
                ]
            )
        )

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=50)

    assert [row.symbol for row in leaders] == ["2330.TW", "2454.TW"]
    assert leaders[0].actor == "foreign"
    assert leaders[0].net_buy == pytest.approx(100.0)
    assert leaders[0].source_dates == ("2026-06-02",)


def test_twse_recent_accumulation_queries_calendar_window_and_uses_available_market_dates() -> None:
    calls: list[tuple[str, str]] = []
    payloads = {
        (TWSE_FOREIGN_BUY_TOP_REPORT, "20260529"): _twse_payload(
            [
                _twse_foreign_row(stock_id="3711", buy=30, sell=0, net=30),
                _twse_foreign_row(stock_id="2201", buy=20, sell=0, net=20),
            ]
        ),
        (TWSE_FOREIGN_BUY_TOP_REPORT, "20260601"): _twse_payload(
            [
                _twse_foreign_row(stock_id="3711", buy=40, sell=0, net=40),
                _twse_foreign_row(stock_id="2201", buy=20, sell=0, net=20),
            ]
        ),
        (TWSE_TRUST_BUY_TOP_REPORT, "20260601"): _twse_payload([]),
        (TWSE_FOREIGN_BUY_TOP_REPORT, "20260602"): _twse_payload([]),
        (TWSE_TRUST_BUY_TOP_REPORT, "20260602"): _twse_payload(
            [
                _twse_trust_row(stock_id="3711", buy=35, sell=0, net=35),
                _twse_trust_row(stock_id="2201", buy=20, sell=0, net=20),
            ]
        ),
        (TWSE_TRUST_BUY_TOP_REPORT, "20260529"): _twse_payload([]),
    }

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeTwseResponse:
        report_id = url.rsplit("/", maxsplit=1)[-1]
        calls.append((report_id, params["date"]))
        return _FakeTwseResponse(payloads.get((report_id, params["date"]), _twse_payload([], stat="很抱歉，沒有符合條件的資料!")))

    provider = TwseRwdInstitutionalUniverseProvider(request_get=fake_get)

    leaders = provider.recent_accumulation_leaders(run_date=date(2026, 6, 2), market="TW", limit=2)

    assert [row.symbol for row in leaders] == ["3711.TW", "2201.TW"]
    assert [row.rank for row in leaders] == [1, 2]
    assert leaders[0].consecutive_buy_days == 3
    assert leaders[0].cumulative_net_buy == pytest.approx(105.0)
    assert leaders[0].concentration is None
    assert leaders[0].source_dates == ("2026-05-29", "2026-06-01", "2026-06-02")
    assert len(calls) == 14
    assert {query_date for _, query_date in calls}.isdisjoint({"20260523", "20260524", "20260530", "20260531"})
