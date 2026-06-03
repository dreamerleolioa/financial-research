from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from ai_stock_sentinel.daily_radar.institutional_universe_provider import (
    TWSE_FOREIGN_BUY_TOP_REPORT,
    TWSE_TRUST_BUY_TOP_REPORT,
    TwseRwdInstitutionalUniverseProvider,
)
from ai_stock_sentinel.daily_radar.universe import (
    InstitutionalLeaderRow,
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


def _twse_foreign_row(*, stock_id: str, buy: str | int, sell: str | int, net: str | int) -> list[str]:
    return [" ", f"{stock_id}  ", "測試", "0", "0", "0", "0", "0", "0", str(buy), str(sell), str(net)]


def _twse_trust_row(*, stock_id: str, buy: str | int, sell: str | int, net: str | int) -> list[str]:
    return [" ", f"{stock_id}  ", "測試", str(buy), str(sell), str(net)]


def _twse_payload(rows: list[list[str]], *, stat: str = "OK") -> dict[str, Any]:
    return {"stat": stat, "data": rows}


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
        (TWSE_FOREIGN_BUY_TOP_REPORT, "20260601"): _twse_payload(
            [_twse_foreign_row(stock_id="3711", buy=40, sell=0, net=40)]
        ),
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
        (TWSE_TRUST_BUY_TOP_REPORT, "20260602"): _twse_payload(
            [
                _twse_trust_row(stock_id="3711", buy=35, sell=0, net=35),
                _twse_trust_row(stock_id="2201", buy=20, sell=0, net=20),
            ]
        ),
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
    assert len(calls) == 22
