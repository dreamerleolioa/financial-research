from __future__ import annotations

import json
from typing import Any

import pytest

from ai_stock_sentinel.daily_radar.explanations import generate_candidate_explanation


PROHIBITED_EXPLANATION_COPY = (
    "買進",
    "賣出",
    "加碼",
    "出場",
    "必買",
    "目標價",
    "推薦",
    "buy",
    "sell",
    "recommendation",
    "target_price",
    "win_rate",
)


def _candidate(
    primary_bucket: str,
    *,
    symbol: str,
    name: str,
    risk_labels: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name,
        "primary_bucket": primary_bucket,
        "secondary_buckets": ["institutional_accumulation"]
        if primary_bucket != "institutional_accumulation"
        else ["price_volume_strengthening"],
        "observation_score": 78,
        "risk_labels": risk_labels or [],
        "repeat_status": "new",
        "score_breakdown": {
            "bucket_scores": {
                primary_bucket: 78,
                "institutional_accumulation": 65,
            },
            "cross_confirmation": 6,
            "market_context": 2,
            "freshness": 4,
            "risk_adjustment": -2 if risk_labels else 0,
            "observation_score": 78,
        },
        "matched_rules": [
            {
                "rule_id": f"{primary_bucket}_primary",
                "label": "主要規則命中且資料一致",
                "details": {"score": 78, "volume_ratio": 1.42},
            },
            {
                "rule_id": "cross_confirmation",
                "label": "量能與籌碼同步改善",
                "details": {"cross_confirmation": 6},
            },
        ],
        "input_snapshot": {
            "ohlcv": {
                "close": 52.4,
                "previous_close": 51.6,
                "volume": 12800000,
                "avg_volume_20": 9200000,
            },
            "indicators": {
                "ma20": 51.8,
                "ma60": 49.7,
                "rsi14": 57.2,
                "volume_ratio": 1.39,
                "support_level": 50.8,
                "resistance_level": 55.2,
            },
            "institutional_flow": {
                "consecutive_positive_days": 3,
                "three_party_net_shares": 1450,
                "flow_state": "consistent_accumulation",
            },
            "margin": {
                "margin_delta_pct": -1.8,
                "margin_to_volume": 0.42,
            },
            "data_dates": {
                "ohlcv": "2026-05-29",
                "institutional_flow": "2026-05-29",
                "margin": "2026-05-29",
            },
        },
    }


@pytest.mark.parametrize(
    ("bucket", "expected_phrase"),
    [
        ("institutional_accumulation", "法人籌碼延續"),
        ("price_volume_strengthening", "量價結構轉強"),
        ("bottoming_reversal", "低位修復"),
        ("support_retest", "支撐回測"),
    ],
)
def test_generate_candidate_explanation_covers_each_observation_bucket(
    bucket: str,
    expected_phrase: str,
) -> None:
    explanation = generate_candidate_explanation(
        _candidate(bucket, symbol="2330.TW", name="TSMC", risk_labels=["market_weakness"])
    )

    assert expected_phrase in explanation["setup_summary"]
    assert "TSMC" in explanation["setup_summary"]
    assert 3 <= len(explanation["evidence_points"]) <= 5
    assert 1 <= len(explanation["risk_notes"]) <= 3
    assert explanation["risk_notes"][0]["label"] == "market_weakness"
    assert explanation["next_day_focus"].startswith("隔日觀察重點：")
    assert explanation["text"].startswith(explanation["setup_summary"])


def test_generate_candidate_explanation_defaults_to_data_and_market_risk_note() -> None:
    explanation = generate_candidate_explanation(
        _candidate("support_retest", symbol="2303.TW", name="UMC")
    )

    assert len(explanation["risk_notes"]) == 1
    assert explanation["risk_notes"][0] == {
        "label": "market_and_data_context",
        "note": "資料更新與大盤波動仍需納入隔日觀察。",
    }


def test_generate_candidate_explanation_is_deterministic_for_same_payload() -> None:
    candidate = _candidate(
        "price_volume_strengthening",
        symbol="2454.TW",
        name="MediaTek",
        risk_labels=["overextended", "flow_conflict", "data_gap", "market_weakness"],
    )

    first = generate_candidate_explanation(candidate)
    second = generate_candidate_explanation(json.loads(json.dumps(candidate, ensure_ascii=False)))

    assert first == second
    assert [note["label"] for note in first["risk_notes"]] == [
        "overextended",
        "flow_conflict",
        "data_gap",
    ]


def test_generated_explanation_copy_uses_observation_language_only() -> None:
    explanations = [
        generate_candidate_explanation(
            _candidate(bucket, symbol=f"{index}.TW", name=f"Name {index}", risk_labels=["data_gap"])
        )
        for index, bucket in enumerate(
            [
                "institutional_accumulation",
                "price_volume_strengthening",
                "bottoming_reversal",
                "support_retest",
            ],
            start=1,
        )
    ]
    generated_text = json.dumps(explanations, ensure_ascii=False).lower()

    for prohibited in PROHIBITED_EXPLANATION_COPY:
        assert prohibited.lower() not in generated_text
