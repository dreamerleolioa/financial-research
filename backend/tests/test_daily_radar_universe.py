from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from ai_stock_sentinel.daily_radar.institutional_universe_provider import (
    FINMIND_INSTITUTIONAL_DATASET,
    FINMIND_MARKET_DATA_URL,
    FinMindMarketInstitutionalUniverseProvider,
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


class _FakeFinMindResponse:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"status": 200, "data": self._rows}


def _institutional_row(
    *,
    row_date: str,
    stock_id: str,
    name: str,
    buy: float,
    sell: float,
    volume: float | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": row_date,
        "stock_id": stock_id,
        "name": name,
        "buy": buy,
        "sell": sell,
    }
    if volume is not None:
        row["Trading_Volume"] = volume
    return row


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


def test_finmind_market_provider_fetches_all_market_rows_and_feeds_selector() -> None:
    calls: list[dict[str, str]] = []
    same_day_rows = [
        _institutional_row(row_date="2026-06-02", stock_id="2330", name="Foreign_Investors", buy=100, sell=10),
        _institutional_row(row_date="2026-06-02", stock_id="2330", name="Investment_Trust", buy=30, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="2454", name="Foreign_Investors", buy=60, sell=10),
        _institutional_row(row_date="2026-06-02", stock_id="2454", name="Investment_Trust", buy=70, sell=20),
        _institutional_row(row_date="2026-06-02", stock_id="2303", name="Dealer", buy=500, sell=0),
    ]
    recent_rows = [
        _institutional_row(row_date="2026-05-29", stock_id="3711", name="Foreign_Investors", buy=30, sell=0),
        _institutional_row(row_date="2026-06-01", stock_id="3711", name="Foreign_Investors", buy=40, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="3711", name="Investment_Trust", buy=35, sell=0),
        _institutional_row(row_date="2026-06-01", stock_id="2330", name="Foreign_Investors", buy=10, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="2330", name="Foreign_Investors", buy=10, sell=0),
    ]

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeFinMindResponse:
        assert url == FINMIND_MARKET_DATA_URL
        assert timeout == 15
        assert params["dataset"] == FINMIND_INSTITUTIONAL_DATASET
        assert "data_id" not in params
        assert "stock_id" not in params
        calls.append(dict(params))
        if params["start_date"] == params["end_date"]:
            return _FakeFinMindResponse(same_day_rows)
        return _FakeFinMindResponse(recent_rows)

    provider = FinMindMarketInstitutionalUniverseProvider(api_token="test-token", request_get=fake_get)

    universe = select_dual_track_universe(provider, date(2026, 6, 2), market="TW", track_limit=2)

    assert [entry.symbol for entry in universe] == ["2330.TW", "2454.TW", "3711.TW"]
    assert universe[0].same_day_score == pytest.approx(90.0)
    assert universe[1].same_day_score == pytest.approx(50.0)
    assert universe[2].primary_track == "recent_accumulation"
    assert calls == [
        {
            "dataset": FINMIND_INSTITUTIONAL_DATASET,
            "start_date": "2026-06-02",
            "end_date": "2026-06-02",
            "token": "test-token",
        },
        {
            "dataset": FINMIND_INSTITUTIONAL_DATASET,
            "start_date": "2026-05-23",
            "end_date": "2026-06-02",
            "token": "test-token",
        },
    ]


def test_finmind_same_day_leaders_rank_by_buy_sell_value_when_available() -> None:
    rows = [
        {
            "date": "2026-06-02",
            "stock_id": "2330",
            "name": "Foreign_Investors",
            "buy": 1,
            "sell": 0,
            "buy_value": 1_000,
            "sell_value": 0,
        },
        {
            "date": "2026-06-02",
            "stock_id": "2454",
            "name": "Foreign_Investors",
            "buy": 100,
            "sell": 0,
            "buy_value": 50,
            "sell_value": 0,
        },
    ]

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeFinMindResponse:
        assert "data_id" not in params
        assert "stock_id" not in params
        return _FakeFinMindResponse(rows)

    provider = FinMindMarketInstitutionalUniverseProvider(api_token="test-token", request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=2)

    assert [row.symbol for row in leaders] == ["2330.TW", "2454.TW"]
    assert leaders[0].score == pytest.approx(1_000.0)
    assert leaders[1].score == pytest.approx(50.0)


def test_finmind_same_day_leaders_limit_is_final_combined_cap_after_ranking() -> None:
    rows = [
        _institutional_row(row_date="2026-06-02", stock_id="2330", name="Foreign_Investors", buy=100, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="2454", name="Foreign_Investors", buy=90, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="2303", name="Foreign_Investors", buy=80, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="3711", name="Investment_Trust", buy=95, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="3034", name="Investment_Trust", buy=85, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="1101", name="Investment_Trust", buy=75, sell=0),
    ]

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeFinMindResponse:
        assert "data_id" not in params
        assert "stock_id" not in params
        return _FakeFinMindResponse(rows)

    provider = FinMindMarketInstitutionalUniverseProvider(api_token="test-token", request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=2)

    assert [row.symbol for row in leaders] == ["2330.TW", "3711.TW"]
    assert [row.rank for row in leaders] == [1, 2]
    assert [row.actor for row in leaders] == ["foreign", "trust"]


def test_finmind_same_day_foreign_leader_survives_trust_selling_same_symbol() -> None:
    rows = [
        _institutional_row(row_date="2026-06-02", stock_id="2330", name="Foreign_Investors", buy=100, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="2330", name="Investment_Trust", buy=0, sell=150),
        _institutional_row(row_date="2026-06-02", stock_id="2454", name="Investment_Trust", buy=80, sell=0),
    ]

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeFinMindResponse:
        assert "data_id" not in params
        assert "stock_id" not in params
        return _FakeFinMindResponse(rows)

    provider = FinMindMarketInstitutionalUniverseProvider(api_token="test-token", request_get=fake_get)

    leaders = provider.same_day_institutional_leaders(run_date=date(2026, 6, 2), market="TW", limit=50)

    assert [row.symbol for row in leaders] == ["2330.TW", "2454.TW"]
    assert leaders[0].actor == "foreign"
    assert leaders[0].net_buy == pytest.approx(100.0)
    assert leaders[0].source_dates == ("2026-06-02",)


def test_finmind_recent_accumulation_prefers_concentration_when_volume_exists() -> None:
    calls: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    for row_date in ["2026-05-27", "2026-05-28", "2026-05-29", "2026-06-01", "2026-06-02"]:
        rows.append(
            _institutional_row(
                row_date=row_date,
                stock_id="3711",
                name="Foreign_Investors",
                buy=20,
                sell=10,
                volume=100,
            )
        )
        rows.append(
            _institutional_row(
                row_date=row_date,
                stock_id="2454",
                name="Investment_Trust",
                buy=70,
                sell=20,
                volume=10_000,
            )
        )

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeFinMindResponse:
        assert "data_id" not in params
        assert "stock_id" not in params
        calls.append(dict(params))
        return _FakeFinMindResponse(rows)

    provider = FinMindMarketInstitutionalUniverseProvider(api_token="test-token", request_get=fake_get)

    leaders = provider.recent_accumulation_leaders(run_date=date(2026, 6, 2), market="TW", limit=2)

    assert [row.symbol for row in leaders] == ["3711.TW", "2454.TW"]
    assert [row.rank for row in leaders] == [1, 2]
    assert leaders[0].consecutive_buy_days == 5
    assert leaders[0].cumulative_net_buy == pytest.approx(50.0)
    assert leaders[0].concentration == pytest.approx(0.1)
    assert leaders[0].source_dates == ("2026-05-27", "2026-05-28", "2026-05-29", "2026-06-01", "2026-06-02")
    assert len(calls) == 1


def test_finmind_recent_accumulation_falls_back_to_cumulative_net_without_volume() -> None:
    rows = [
        _institutional_row(row_date="2026-05-29", stock_id="1101", name="Foreign_Investors", buy=30, sell=0),
        _institutional_row(row_date="2026-06-01", stock_id="1101", name="Foreign_Investors", buy=30, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="1101", name="Investment_Trust", buy=30, sell=0),
        _institutional_row(row_date="2026-05-29", stock_id="2201", name="Foreign_Investors", buy=20, sell=0),
        _institutional_row(row_date="2026-06-01", stock_id="2201", name="Foreign_Investors", buy=20, sell=0),
        _institutional_row(row_date="2026-06-02", stock_id="2201", name="Investment_Trust", buy=20, sell=0),
    ]

    def fake_get(url: str, *, params: dict[str, str], timeout: int) -> _FakeFinMindResponse:
        assert "data_id" not in params
        assert "stock_id" not in params
        return _FakeFinMindResponse(rows)

    provider = FinMindMarketInstitutionalUniverseProvider(api_token="test-token", request_get=fake_get)

    leaders = provider.recent_accumulation_leaders(run_date=date(2026, 6, 2), market="TW", limit=2)

    assert [row.symbol for row in leaders] == ["1101.TW", "2201.TW"]
    assert leaders[0].score is not None
    assert leaders[0].score > leaders[1].score
