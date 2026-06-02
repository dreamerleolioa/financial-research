from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from ai_stock_sentinel.daily_radar.constants import (
    DAILY_RADAR_BUCKETS,
    DAILY_RADAR_REPEAT_STATUSES,
    DAILY_RADAR_RISK_LABELS,
)
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarCandidateResponse,
    DailyRadarMatchedRule,
    DailyRadarRunResponse,
)


FORBIDDEN_TERMS = (
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


def _candidate_payload() -> dict[str, Any]:
    return {
        "symbol": "2330.TW",
        "name": "台積電",
        "primary_bucket": DAILY_RADAR_BUCKETS[1],
        "secondary_buckets": [DAILY_RADAR_BUCKETS[0]],
        "observation_score": 82,
        "risk_labels": [DAILY_RADAR_RISK_LABELS[3]],
        "repeat_status": DAILY_RADAR_REPEAT_STATUSES[0],
        "explanation": "量價轉強觀察：今日收盤站回 MA20，成交量高於 20 日均量，隔日留意量能是否延續。",
        "score_breakdown": {
            "bucket_scores": {
                DAILY_RADAR_BUCKETS[1]: 82,
                DAILY_RADAR_BUCKETS[0]: 68,
            },
            "cross_confirmation": 6,
            "market_context": 2,
            "freshness": 4,
            "risk_adjustment": -3,
            "observation_score": 82,
        },
        "matched_rules": [
            DailyRadarMatchedRule(
                rule_id="price_volume_close_above_ma20",
                label="收盤站回 MA20 且量能同步放大",
                details={"close_above_ma20": True, "volume_ratio": 1.42},
            )
        ],
    }


def _run_response() -> DailyRadarRunResponse:
    return DailyRadarRunResponse(
        run_date=date(2026, 6, 1),
        status="completed",
        data_dates={
            "ohlcv": date(2026, 6, 1),
            "institutional_flow": date(2026, 6, 1),
            "margin": date(2026, 5, 31),
            "market_index": date(2026, 6, 1),
        },
        market_context={
            "index_symbol": "TAIEX",
            "trend_state": "above_ma20",
            "volatility_state": "stable",
            "notes": ["大盤維持主要均線上方，整體波動未擴大"],
        },
        candidates=[DailyRadarCandidateResponse(**_candidate_payload())],
    )


def test_daily_radar_run_response_contains_required_contract_keys() -> None:
    dumped = _run_response().model_dump(mode="json")

    json.dumps(dumped, ensure_ascii=False)
    assert set(dumped) >= {
        "run_date",
        "status",
        "data_dates",
        "market_context",
        "candidates",
    }

    candidate = dumped["candidates"][0]
    assert set(candidate) >= {
        "symbol",
        "name",
        "primary_bucket",
        "secondary_buckets",
        "observation_score",
        "risk_labels",
        "repeat_status",
        "explanation",
        "score_breakdown",
        "matched_rules",
    }


def test_daily_radar_contract_uses_observation_semantics() -> None:
    schema_text = json.dumps(DailyRadarRunResponse.model_json_schema(), ensure_ascii=False)
    dumped_text = json.dumps(_run_response().model_dump(mode="json"), ensure_ascii=False)
    field_text = json.dumps(
        {
            "run": list(DailyRadarRunResponse.model_fields),
            "candidate": list(DailyRadarCandidateResponse.model_fields),
            "matched_rule": list(DailyRadarMatchedRule.model_fields),
        },
        ensure_ascii=False,
    )
    contract_text = f"{schema_text}\n{dumped_text}\n{field_text}".lower()

    for term in FORBIDDEN_TERMS:
        assert term.lower() not in contract_text


def test_daily_radar_candidate_constrains_shared_contract_values() -> None:
    candidate = DailyRadarCandidateResponse(**_candidate_payload())

    assert candidate.primary_bucket in DAILY_RADAR_BUCKETS
    assert candidate.secondary_buckets[0] in DAILY_RADAR_BUCKETS
    assert candidate.risk_labels[0] in DAILY_RADAR_RISK_LABELS
    assert candidate.repeat_status in DAILY_RADAR_REPEAT_STATUSES

    with pytest.raises(ValidationError):
        DailyRadarCandidateResponse(**(_candidate_payload() | {"primary_bucket": "unsupported_bucket"}))

    with pytest.raises(ValidationError):
        DailyRadarCandidateResponse(**(_candidate_payload() | {"risk_labels": ["unknown_risk"]}))

    with pytest.raises(ValidationError):
        DailyRadarCandidateResponse(**(_candidate_payload() | {"repeat_status": "unknown_status"}))


@pytest.mark.parametrize("score", [-1, 101])
def test_daily_radar_observation_score_stays_in_score_range(score: int) -> None:
    with pytest.raises(ValidationError):
        DailyRadarCandidateResponse(**(_candidate_payload() | {"observation_score": score}))
