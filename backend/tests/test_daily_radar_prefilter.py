from __future__ import annotations

import copy
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ai_stock_sentinel.daily_radar.data_loader import (
    load_daily_radar_cache_records,
    load_daily_radar_fixture_records,
)
from ai_stock_sentinel.daily_radar.prefilter import (
    prefilter_record,
    run_stage1_prefilter,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "daily_radar"
ACCEPTED_SYMBOLS = ("2330.TW", "2454.TW", "3034.TW", "2303.TW")
EDGE_CASES = {
    "1101.TW": ("stale_data", "stale_core_data"),
    "2603.TW": ("rejected", "data_gap"),
    "3661.TW": ("rejected", "overextended"),
    "1605.TW": ("rejected", "margin_crowding"),
}


def _records_by_symbol() -> dict[str, dict[str, Any]]:
    return {record["symbol"]: record for record in load_daily_radar_fixture_records(FIXTURE_DIR)}


def _reason_codes(result: dict[str, Any]) -> set[str]:
    return {reason["code"] for reason in result["prefilter_reasons"]}


def test_loader_joins_daily_radar_fixture_records_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Daily Radar prefilter must stay offline")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    records = load_daily_radar_fixture_records(FIXTURE_DIR)

    assert len(records) == 8
    first = records[0]
    assert {
        "symbol",
        "name",
        "record_date",
        "ohlcv",
        "indicators",
        "institutional_flow",
        "margin",
        "data_dates",
        "fixture_case",
    } <= set(first)
    assert first["data_dates"] == {
        "ohlcv": "2026-05-29",
        "technical_indicators": "2026-05-29",
        "institutional_flow": "2026-05-29",
        "margin": "2026-05-29",
    }


def test_loader_reads_local_cache_like_rows_without_schema_or_orm_dependency() -> None:
    fixture_record = _records_by_symbol()["2330.TW"]
    cache_row = SimpleNamespace(
        symbol=fixture_record["symbol"],
        record_date=fixture_record["record_date"],
        technical={
            "name": fixture_record["name"],
            "ohlcv": fixture_record["ohlcv"],
            "indicators": fixture_record["indicators"],
            "data_dates": {
                "ohlcv": "2026-05-29",
                "technical_indicators": "2026-05-29",
            },
        },
        institutional={
            "institutional_flow": fixture_record["institutional_flow"],
            "data_dates": {"institutional_flow": "2026-05-29"},
        },
        fundamental={
            "margin": fixture_record["margin"],
            "data_dates": {"margin": "2026-05-29"},
        },
    )

    records = load_daily_radar_cache_records([cache_row])

    assert records == [fixture_record | {"expected_bucket_seed": None, "fixture_case": None}]


def test_loader_normalizes_flat_institutional_cache_payload_for_scoring() -> None:
    fixture_record = _records_by_symbol()["2330.TW"]
    cache_row = SimpleNamespace(
        symbol=fixture_record["symbol"],
        record_date=fixture_record["record_date"],
        technical={
            "name": fixture_record["name"],
            "ohlcv": fixture_record["ohlcv"],
            "indicators": fixture_record["indicators"],
        },
        institutional={
            "flow_label": "institutional_accumulation",
            "foreign_net_cumulative": 4200,
            "trust_net_cumulative": 1800,
            "investment_trust_buy": 1900,
            "foreign_buy": 4500,
            "three_party_net": 6500,
            "consecutive_buy_days": 5,
            "consecutive_sell_days": 0,
            "dominant_buyer": "foreign",
            "flow_strength": "strong",
            "source_provider": "finmind",
            "warnings": [],
            "data_dates": {"institutional_flow": "2026-05-29"},
        },
        fundamental={"margin": fixture_record["margin"]},
    )

    records = load_daily_radar_cache_records([cache_row])

    institutional_flow = records[0]["institutional_flow"]
    assert institutional_flow
    assert institutional_flow["flow_label"] == "institutional_accumulation"
    assert institutional_flow["foreign_net_shares"] == 4200
    assert institutional_flow["investment_trust_net_shares"] == 1800
    assert institutional_flow["three_party_net_shares"] == 6500
    assert institutional_flow["consecutive_positive_days"] == 5
    assert institutional_flow["consecutive_negative_days"] == 0
    assert institutional_flow["flow_state"] == "institutional_accumulation"


def test_prefilter_accepts_clean_fixture_records_with_debug_and_data_dates() -> None:
    records = _records_by_symbol()

    for symbol in ACCEPTED_SYMBOLS:
        result = prefilter_record(records[symbol])

        assert result["prefilter_status"] == "accepted"
        assert result["prefilter_reasons"] == []
        assert result["data_dates"] == records[symbol]["data_dates"]
        assert result["debug"]["liquidity"]["avg_turnover_value_million"] > 0
        assert result["debug"]["thresholds"]["min_price"] == pytest.approx(20.0)


@pytest.mark.parametrize("symbol, expected", EDGE_CASES.items())
def test_prefilter_rejects_fixture_edge_cases_with_stable_chinese_reasons(
    symbol: str,
    expected: tuple[str, str],
) -> None:
    expected_status, expected_code = expected
    result = prefilter_record(_records_by_symbol()[symbol])

    assert result["prefilter_status"] == expected_status
    assert expected_code in _reason_codes(result)
    for reason in result["prefilter_reasons"]:
        assert {"code", "text", "details"} <= set(reason)
        assert reason["text"]
        assert any("\u4e00" <= char <= "\u9fff" for char in reason["text"])
        assert isinstance(reason["details"], dict)


def test_prefilter_hard_gates_low_liquidity_and_minimum_price() -> None:
    record = copy.deepcopy(_records_by_symbol()["2330.TW"])
    record["ohlcv"]["close"] = 9.5
    record["ohlcv"]["avg_volume_20"] = 10_000
    record["ohlcv"]["turnover_value_million"] = 0.2

    result = prefilter_record(record)

    assert result["prefilter_status"] == "rejected"
    assert {"low_liquidity", "min_price"} <= _reason_codes(result)
    assert result["debug"]["liquidity"]["avg_turnover_value_million"] == pytest.approx(0.095)


def test_prefilter_hard_gates_weak_long_term_structure() -> None:
    record = copy.deepcopy(_records_by_symbol()["2330.TW"])
    record["ohlcv"]["close"] = 820.0
    record["indicators"]["ma5"] = 825.0
    record["indicators"]["ma20"] = 850.0
    record["indicators"]["ma60"] = 900.0
    record["indicators"]["obv_trend"] = "falling"
    record["indicators"]["volume_ratio"] = 0.82

    result = prefilter_record(record)

    assert result["prefilter_status"] == "rejected"
    assert "weak_structure" in _reason_codes(result)
    assert result["debug"]["structure"]["close_below_ma60"] is True


def test_stage1_prefilter_limits_accepted_records_to_top_n_deterministically() -> None:
    results = run_stage1_prefilter(
        load_daily_radar_fixture_records(FIXTURE_DIR),
        limit=2,
        include_rejected=False,
    )

    assert [result["symbol"] for result in results] == ["2330.TW", "2454.TW"]
    assert {result["prefilter_status"] for result in results} == {"accepted"}
