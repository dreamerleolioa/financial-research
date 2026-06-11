from __future__ import annotations

import json
import socket
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_BUCKETS, DAILY_RADAR_RISK_LABELS
from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_fixture_records
from ai_stock_sentinel.daily_radar.prefilter import prefilter_record
from ai_stock_sentinel.daily_radar.scoring import score_daily_radar_record, score_daily_radar_records


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "daily_radar"
FIXTURE_FILES = (
    "ohlcv.json",
    "institutional_flow.json",
    "margin.json",
    "market_context.json",
    "history_candidates.json",
)
EXPECTED_BUCKET_SYMBOLS = {
    "institutional_accumulation": "2330.TW",
    "price_volume_strengthening": "2454.TW",
    "bottoming_reversal": "3034.TW",
    "support_retest": "2303.TW",
}
EDGE_CASE_SYMBOLS = {
    "stale_data": "1101.TW",
    "data_gap": "2603.TW",
    "overextended": "3661.TW",
    "margin_crowding": "1605.TW",
}
REQUIRED_OHLCV_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "previous_close",
    "volume",
    "avg_volume_20",
    "turnover_value_million",
}
REQUIRED_INDICATOR_FIELDS = {
    "ma5",
    "ma20",
    "ma60",
    "rsi14",
    "bias20",
    "macd_histogram",
    "kd_k",
    "kd_d",
    "obv_trend",
    "mfi14",
    "atr14",
    "support_level",
    "resistance_level",
    "volume_ratio",
    "missing_trading_days_60",
}
REQUIRED_INSTITUTIONAL_FIELDS = {
    "foreign_net_shares",
    "investment_trust_net_shares",
    "dealer_net_shares",
    "three_party_net_shares",
    "consecutive_positive_days",
    "consecutive_negative_days",
    "dominant_participant",
    "flow_state",
    "net_flow_to_avg_volume",
    "risk_flags",
}
REQUIRED_MARGIN_FIELDS = {
    "margin_balance",
    "margin_delta",
    "margin_delta_pct",
    "short_balance",
    "short_delta",
    "short_delta_pct",
    "margin_to_volume",
    "risk_flags",
}
PROHIBITED_FIXTURE_COPY = (
    "recommendation",
    "recommended",
    "buy",
    "sell",
    "target_price",
    "win_rate",
    "買進",
    "賣出",
    "加碼",
    "出場建議",
    "目標價",
    "勝率",
    "推薦",
)


def _load_fixture(file_name: str) -> dict[str, Any]:
    path = FIXTURE_DIR / file_name
    assert path.is_file(), f"Missing Daily Radar fixture: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_all_fixtures() -> dict[str, dict[str, Any]]:
    return {file_name: _load_fixture(file_name) for file_name in FIXTURE_FILES}


def _records_by_symbol(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {record["symbol"]: record for record in payload["records"]}


def _joined_records_by_symbol() -> dict[str, dict[str, Any]]:
    return {record["symbol"]: record for record in load_daily_radar_fixture_records(FIXTURE_DIR)}


def _market_context() -> dict[str, Any]:
    return _load_fixture("market_context.json")


def _weak_market_context() -> dict[str, Any]:
    market_context = deepcopy(_market_context())
    market_context["market"] |= {
        "above_ma20": False,
        "above_ma60": False,
        "volatility_state": "elevated",
        "market_risk_flags": ["market_weakness"],
    }
    return market_context


def _price_history(start: date, closes: list[float]) -> list[dict[str, Any]]:
    return [
        {"date": (start + timedelta(days=index)).isoformat(), "close": close}
        for index, close in enumerate(closes)
    ]


def _market_context_with_benchmark(closes: list[float]) -> dict[str, Any]:
    market_context = deepcopy(_market_context())
    market_context["market"]["data_date"] = "2026-05-29"
    market_context["benchmark"] = {
        "symbol": "TAIEX",
        "yfinance_symbol": "^TWII",
        "price_history": _price_history(date(2026, 5, 9), closes),
        "data_dates": {"market_index": "2026-05-29"},
    }
    return market_context


def _assert_score_contract(result: dict[str, Any]) -> None:
    assert set(result["bucket_scores"]) == set(DAILY_RADAR_BUCKETS)
    assert 0 <= result["observation_score"] <= 100
    assert result["primary_bucket"] in DAILY_RADAR_BUCKETS
    assert all(bucket in DAILY_RADAR_BUCKETS for bucket in result["secondary_buckets"])
    assert all(label in DAILY_RADAR_RISK_LABELS for label in result["risk_labels"])
    assert result["score_breakdown"]["bucket_scores"] == result["bucket_scores"]
    assert {
        "primary_bucket_score",
        "cross_confirmation",
        "market_context",
        "freshness",
        "risk_penalties",
        "risk_adjustment",
        "observation_score",
    } <= set(result["score_breakdown"])


def test_daily_radar_fixtures_load_from_local_json_files_without_network(monkeypatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Daily Radar fixture tests must not open network connections")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    payloads = _load_all_fixtures()

    assert set(payloads) == set(FIXTURE_FILES)
    for file_name, payload in payloads.items():
        assert (FIXTURE_DIR / file_name).is_relative_to(Path(__file__).parent)
        assert payload["fixture_version"] == "daily-radar-contract-v1"


def test_daily_radar_core_fixture_records_are_joinable_and_complete() -> None:
    payloads = _load_all_fixtures()
    ohlcv_records = _records_by_symbol(payloads["ohlcv.json"])
    flow_records = _records_by_symbol(payloads["institutional_flow.json"])
    margin_records = _records_by_symbol(payloads["margin.json"])

    expected_symbols = set(EXPECTED_BUCKET_SYMBOLS.values()) | set(EDGE_CASE_SYMBOLS.values())
    assert set(ohlcv_records) == expected_symbols
    assert set(flow_records) == expected_symbols
    assert set(margin_records) == expected_symbols

    for symbol in expected_symbols:
        ohlcv_record = ohlcv_records[symbol]
        flow_record = flow_records[symbol]
        margin_record = margin_records[symbol]

        assert ohlcv_record["record_date"] == "2026-05-29"
        assert flow_record["record_date"] == "2026-05-29"
        assert margin_record["record_date"] == "2026-05-29"
        assert ohlcv_record["data_dates"]
        assert flow_record["data_dates"]
        assert margin_record["data_dates"]
        assert REQUIRED_OHLCV_FIELDS <= set(ohlcv_record["ohlcv"])
        assert REQUIRED_INDICATOR_FIELDS <= set(ohlcv_record["indicators"])
        assert REQUIRED_INSTITUTIONAL_FIELDS <= set(flow_record["institutional_flow"])
        assert REQUIRED_MARGIN_FIELDS <= set(margin_record["margin"])
        assert ohlcv_record["ohlcv"]["volume"] > 0


def test_daily_radar_fixtures_seed_all_four_observation_buckets() -> None:
    payloads = _load_all_fixtures()
    ohlcv_records = _records_by_symbol(payloads["ohlcv.json"])
    flow_records = _records_by_symbol(payloads["institutional_flow.json"])
    margin_records = _records_by_symbol(payloads["margin.json"])

    for bucket, symbol in EXPECTED_BUCKET_SYMBOLS.items():
        assert ohlcv_records[symbol]["expected_bucket_seed"] == bucket
        assert flow_records[symbol]["expected_bucket_seed"] == bucket
        assert margin_records[symbol]["expected_bucket_seed"] == bucket


def test_daily_radar_fixtures_include_prefilter_edge_cases() -> None:
    payloads = _load_all_fixtures()
    ohlcv_records = _records_by_symbol(payloads["ohlcv.json"])
    flow_records = _records_by_symbol(payloads["institutional_flow.json"])
    margin_records = _records_by_symbol(payloads["margin.json"])

    for fixture_case, symbol in EDGE_CASE_SYMBOLS.items():
        assert ohlcv_records[symbol]["fixture_case"] == fixture_case
        assert flow_records[symbol]["fixture_case"] == fixture_case
        assert margin_records[symbol]["fixture_case"] == fixture_case

    stale_symbol = EDGE_CASE_SYMBOLS["stale_data"]
    assert ohlcv_records[stale_symbol]["data_dates"]["ohlcv"] < ohlcv_records[stale_symbol]["record_date"]
    assert "stale_data" in flow_records[stale_symbol]["institutional_flow"]["risk_flags"]

    gap_symbol = EDGE_CASE_SYMBOLS["data_gap"]
    assert ohlcv_records[gap_symbol]["indicators"]["missing_trading_days_60"] > 0
    assert "data_gap" in margin_records[gap_symbol]["margin"]["risk_flags"]

    overextended_symbol = EDGE_CASE_SYMBOLS["overextended"]
    assert ohlcv_records[overextended_symbol]["indicators"]["rsi14"] >= 80
    assert ohlcv_records[overextended_symbol]["indicators"]["bias20"] >= 20
    assert "overextended" in margin_records[overextended_symbol]["margin"]["risk_flags"]

    crowded_symbol = EDGE_CASE_SYMBOLS["margin_crowding"]
    assert margin_records[crowded_symbol]["margin"]["margin_delta_pct"] >= 10
    assert margin_records[crowded_symbol]["margin"]["margin_to_volume"] >= 4
    assert "margin_crowding" in margin_records[crowded_symbol]["margin"]["risk_flags"]


def test_daily_radar_auxiliary_fixtures_cover_market_context_and_history() -> None:
    payloads = _load_all_fixtures()
    market_context = payloads["market_context.json"]
    history_records = payloads["history_candidates.json"]["records"]

    assert market_context["record_date"] == "2026-05-29"
    assert market_context["data_dates"]
    assert market_context["data_dates"]["market_index"] == "2026-05-29"
    assert market_context["market"]["index_symbol"] == "TAIEX"
    assert market_context["market"]["regime"] == "constructive"
    assert market_context["market"]["freshness"] == "fresh"
    assert market_context["market"]["volatility_state"] == "normal"
    assert {override["fixture_case"] for override in market_context["symbol_overrides"]} == {
        "stale_data",
        "data_gap",
    }
    assert {record["repeat_status"] for record in history_records} >= {"repeat", "cooled_down"}
    assert {record["primary_bucket"] for record in history_records} >= {
        "institutional_accumulation",
        "support_retest",
    }


def test_daily_radar_fixture_text_uses_observation_risk_language_only() -> None:
    fixture_text = "\n".join(
        (FIXTURE_DIR / file_name).read_text(encoding="utf-8").lower()
        for file_name in FIXTURE_FILES
    )

    for prohibited in PROHIBITED_FIXTURE_COPY:
        assert prohibited.lower() not in fixture_text


def test_daily_radar_scoring_assigns_each_seed_fixture_primary_bucket() -> None:
    records = _joined_records_by_symbol()
    scored = score_daily_radar_records(
        records.values(),
        market_context=_market_context(),
        prefilter_results=[prefilter_record(record) for record in records.values()],
    )
    scored_by_symbol = {result["symbol"]: result for result in scored}

    for bucket, symbol in EXPECTED_BUCKET_SYMBOLS.items():
        result = scored_by_symbol[symbol]

        _assert_score_contract(result)
        assert result["primary_bucket"] == bucket
        assert result["bucket_scores"][bucket] == max(result["bucket_scores"].values())
        assert result["score_breakdown"]["primary_bucket_score"] == result["bucket_scores"][bucket]
        assert result["score_breakdown"]["cross_confirmation"]["score"] > 0
        assert result["score_breakdown"]["market_context"]["score"] > 0
        assert result["score_breakdown"]["freshness"]["score"] > 0


def test_daily_radar_scoring_keeps_secondary_buckets_for_other_matched_setups() -> None:
    records = _joined_records_by_symbol()

    price_volume = score_daily_radar_record(records["2454.TW"], market_context=_market_context())
    bottoming = score_daily_radar_record(records["3034.TW"], market_context=_market_context())

    assert price_volume["primary_bucket"] == "price_volume_strengthening"
    assert "institutional_accumulation" in price_volume["secondary_buckets"]
    assert price_volume["bucket_scores"]["institutional_accumulation"] >= 55

    assert bottoming["primary_bucket"] == "bottoming_reversal"
    assert "support_retest" in bottoming["secondary_buckets"]
    assert bottoming["bucket_scores"]["support_retest"] >= 55


def test_daily_radar_scoring_preserves_traceable_bucket_rules_and_breakdown() -> None:
    result = score_daily_radar_record(_joined_records_by_symbol()["2303.TW"], market_context=_market_context())
    rule_ids = {rule["rule_id"] for rule in result["matched_rules"]}
    breakdown = result["score_breakdown"]

    assert result["primary_bucket"] == "support_retest"
    assert "support_retest_near_key_level" in rule_ids
    assert "support_retest_reclaimed_area" in rule_ids
    assert breakdown["bucket_scores"] == result["bucket_scores"]
    assert breakdown["cross_confirmation"]["components"]
    assert breakdown["market_context"]["label"] == "supportive"
    assert breakdown["market_context"]["details"]["regime"] == "constructive"
    assert breakdown["freshness"]["label"] == "fresh"
    assert breakdown["risk_penalties"] == []
    assert result["data_dates"]["market_index"] == "2026-05-29"
    assert result["input_snapshot"]["market_context"]["regime"] == "constructive"
    assert result["scoring_version"] == "daily-radar-scoring-v2.1c"
    assert result["rule_version"] == "daily-radar-rules-v2.1c"
    assert breakdown["scoring_version"] == "daily-radar-scoring-v2.1c"
    assert breakdown["rule_version"] == "daily-radar-rules-v2.1c"


def test_daily_radar_scoring_applies_relative_strength_component_and_replayable_trace() -> None:
    record = deepcopy(_joined_records_by_symbol()["2303.TW"])
    record["price_history"] = _price_history(date(2026, 5, 9), [100.0 + index for index in range(21)])

    result = score_daily_radar_record(
        record,
        market_context=_market_context_with_benchmark([100.0 + index * 0.25 for index in range(21)]),
    )

    relative_strength = result["score_breakdown"]["relative_strength"]
    evidence = result["input_snapshot"]["evidence"][0]

    assert relative_strength["freshness"] == "fresh"
    assert relative_strength["benchmark_symbol"] == "TAIEX"
    assert relative_strength["lookback_days"] == 20
    assert relative_strength["relative_value"] > 0
    assert relative_strength["score"] == 6
    assert result["data_dates"]["relative_strength"] == "2026-05-29"
    assert result["input_snapshot"]["relative_strength"] == relative_strength
    assert result["input_snapshot"]["versions"] == {
        "scoring_version": "daily-radar-scoring-v2.1c",
        "rule_version": "daily-radar-rules-v2.1c",
    }
    assert evidence["evidence_type"] == "relative_strength"
    assert evidence["source"]["domain"] == "daily_trigger_signal"
    assert evidence["source"]["provider"] == "deterministic_relative_strength"
    assert evidence["as_of_date"] == "2026-05-29"
    assert evidence["freshness"] == "fresh"
    assert evidence["missing_reason"] is None
    assert evidence["replay_key"] == "relative_strength:2303.TW:TAIEX:2026-05-29:L20"
    assert evidence["applicable_consumers"] == ["daily_radar"]


def test_daily_radar_scoring_penalizes_relative_underperformance_without_risk_label() -> None:
    record = deepcopy(_joined_records_by_symbol()["2303.TW"])
    record["price_history"] = _price_history(date(2026, 5, 9), [100.0 + index * 0.1 for index in range(21)])
    missing_baseline = score_daily_radar_record(record, market_context=_market_context())

    result = score_daily_radar_record(
        record,
        market_context=_market_context_with_benchmark([100.0 + index for index in range(21)]),
    )

    assert result["score_breakdown"]["relative_strength"]["score"] == -6
    assert result["score_breakdown"]["relative_strength"]["relative_value"] < 0
    assert result["observation_score"] < missing_baseline["observation_score"]
    assert "data_gap" not in result["risk_labels"]


@pytest.mark.parametrize(
    "fixture_case, symbol, expected_label, clean_symbol",
    [
        ("data_gap", "2603.TW", "data_gap", "3034.TW"),
        ("overextended", "3661.TW", "overextended", "2454.TW"),
        ("margin_crowding", "1605.TW", "margin_crowding", "2303.TW"),
        ("stale_data", "1101.TW", "data_gap", "2330.TW"),
    ],
)
def test_daily_radar_scoring_applies_fixture_risk_penalties_and_labels(
    fixture_case: str,
    symbol: str,
    expected_label: str,
    clean_symbol: str,
) -> None:
    records = _joined_records_by_symbol()
    risky = score_daily_radar_record(
        records[symbol],
        market_context=_market_context(),
        prefilter_result=prefilter_record(records[symbol]),
    )
    clean = score_daily_radar_record(records[clean_symbol], market_context=_market_context())

    assert records[symbol]["fixture_case"] == fixture_case
    assert expected_label in risky["risk_labels"]
    assert risky["observation_score"] < clean["observation_score"]
    assert risky["score_breakdown"]["risk_adjustment"] < 0
    assert any(penalty["label"] == expected_label for penalty in risky["score_breakdown"]["risk_penalties"])


def test_daily_radar_scoring_applies_flow_conflict_and_market_weakness_penalties() -> None:
    record = deepcopy(_joined_records_by_symbol()["2330.TW"])
    clean = score_daily_radar_record(record, market_context=_market_context())

    record["institutional_flow"] |= {
        "flow_state": "conflict",
        "foreign_net_shares": 1800,
        "investment_trust_net_shares": -1600,
        "three_party_net_shares": -120,
        "consecutive_negative_days": 3,
    }
    conflict = score_daily_radar_record(record, market_context=_market_context())
    weak_market = score_daily_radar_record(_joined_records_by_symbol()["2330.TW"], market_context=_weak_market_context())

    assert "flow_conflict" in conflict["risk_labels"]
    assert conflict["observation_score"] < clean["observation_score"]
    assert any(penalty["label"] == "flow_conflict" for penalty in conflict["score_breakdown"]["risk_penalties"])

    assert "market_weakness" in weak_market["risk_labels"]
    assert weak_market["observation_score"] < clean["observation_score"]
    assert weak_market["score_breakdown"]["market_context"]["label"] == "weak"
    assert any(
        penalty["label"] == "market_weakness"
        and penalty["details"]["market"]["market_risk_flags"] == ["market_weakness"]
        for penalty in weak_market["score_breakdown"]["risk_penalties"]
    )


def test_daily_radar_scoring_keeps_missing_market_context_neutral_without_faking_signal() -> None:
    missing_context = {
        "record_date": "2026-05-29",
        "data_dates": {},
        "market": {
            "index_symbol": "TAIEX",
            "regime": "unknown",
            "freshness": "missing",
            "missing_reason": "market_index_ohlcv_missing",
            "market_risk_flags": ["market_context_missing"],
        },
    }

    result = score_daily_radar_record(_joined_records_by_symbol()["2330.TW"], market_context=missing_context)

    assert "market_weakness" not in result["risk_labels"]
    assert result["score_breakdown"]["market_context"]["label"] == "neutral"
    assert result["score_breakdown"]["market_context"]["score"] == 0
    assert result["input_snapshot"]["market_context"]["missing_reason"] == "market_index_ohlcv_missing"


def test_daily_radar_scoring_output_uses_observation_risk_language_only() -> None:
    results = score_daily_radar_records(_joined_records_by_symbol().values(), market_context=_market_context())
    output_text = json.dumps(results, ensure_ascii=False).lower()

    for prohibited in PROHIBITED_FIXTURE_COPY:
        assert prohibited.lower() not in output_text
