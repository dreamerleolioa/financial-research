from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_REPEAT_STATUSES
from ai_stock_sentinel.daily_radar.cooldown import (
    COOLDOWN_REPEAT_STATUS_LABELS,
    CooldownConfig,
    apply_cooldown_status,
    repeat_status_label,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "daily_radar"
RUN_DATE = "2026-05-29"
EXPECTED_OBSERVATION_LABELS = {
    DAILY_RADAR_REPEAT_STATUSES[0]: "首次觀察",
    DAILY_RADAR_REPEAT_STATUSES[1]: "連續觀察",
    DAILY_RADAR_REPEAT_STATUSES[2]: "訊號升級",
    DAILY_RADAR_REPEAT_STATUSES[3]: "訊號冷卻",
}


def _history_records() -> list[dict[str, Any]]:
    payload = json.loads((FIXTURE_DIR / "history_candidates.json").read_text(encoding="utf-8"))
    return list(payload["records"])


def _candidate(
    symbol: str,
    *,
    score: int,
    bucket: str = "institutional_accumulation",
    secondary_buckets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": symbol,
        "primary_bucket": bucket,
        "secondary_buckets": secondary_buckets or [],
        "observation_score": score,
        "risk_labels": [],
    }


def test_cooldown_marks_symbol_as_new_when_no_recent_history_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Cooldown status decisions must stay offline")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    results = apply_cooldown_status(
        [_candidate("2317.TW", score=74)],
        _history_records(),
        run_date=RUN_DATE,
        config=CooldownConfig(lookback_days=5),
    )

    assert results == [
        {
            "symbol": "2317.TW",
            "name": "2317.TW",
            "primary_bucket": "institutional_accumulation",
            "secondary_buckets": [],
            "observation_score": 74,
            "risk_labels": [],
            "repeat_status": DAILY_RADAR_REPEAT_STATUSES[0],
        }
    ]


def test_cooldown_marks_recent_stable_candidate_as_repeat() -> None:
    results = apply_cooldown_status(
        [_candidate("2330.TW", score=81, bucket="institutional_accumulation")],
        _history_records(),
        run_date=RUN_DATE,
        config=CooldownConfig(lookback_days=5, score_upgrade_threshold=8),
    )

    assert results[0]["repeat_status"] == DAILY_RADAR_REPEAT_STATUSES[1]
    assert results[0]["observation_score"] == 81


def test_cooldown_marks_recent_score_or_bucket_improvement_as_upgraded() -> None:
    score_upgrade, bucket_upgrade = apply_cooldown_status(
        [
            _candidate("2330.TW", score=87, bucket="institutional_accumulation"),
            _candidate("3661.TW", score=61, bucket="institutional_accumulation"),
        ],
        _history_records(),
        run_date=RUN_DATE,
        config=CooldownConfig(lookback_days=5, score_upgrade_threshold=8),
    )

    assert score_upgrade["repeat_status"] == DAILY_RADAR_REPEAT_STATUSES[2]
    assert bucket_upgrade["repeat_status"] == DAILY_RADAR_REPEAT_STATUSES[2]


def test_cooldown_excludes_insufficient_recent_signal_by_default() -> None:
    results = apply_cooldown_status(
        [_candidate("2330.TW", score=57, bucket="institutional_accumulation")],
        _history_records(),
        run_date=RUN_DATE,
        config=CooldownConfig(lookback_days=5, min_current_signal_score=60),
    )

    assert results == []


def test_cooldown_can_include_low_signal_or_absent_recent_history_as_cooled_down() -> None:
    low_signal, absent_today = apply_cooldown_status(
        [_candidate("2330.TW", score=57, bucket="institutional_accumulation")],
        _history_records(),
        run_date=RUN_DATE,
        config=CooldownConfig(lookback_days=5, min_current_signal_score=60),
        include_cooled_down=True,
    )

    assert low_signal["repeat_status"] == DAILY_RADAR_REPEAT_STATUSES[3]
    assert low_signal["cooldown_reason"] == "current_signal_below_threshold"
    assert low_signal["observation_score"] == 57
    assert absent_today["repeat_status"] == DAILY_RADAR_REPEAT_STATUSES[3]
    assert absent_today["cooldown_reason"] == "absent_from_current_candidates"
    assert absent_today["symbol"] == "3661.TW"


def test_cooldown_labels_are_observation_language_and_derive_from_shared_statuses() -> None:
    assert set(COOLDOWN_REPEAT_STATUS_LABELS) == set(DAILY_RADAR_REPEAT_STATUSES)
    assert COOLDOWN_REPEAT_STATUS_LABELS == EXPECTED_OBSERVATION_LABELS
    for status, expected_label in EXPECTED_OBSERVATION_LABELS.items():
        assert repeat_status_label(status) == expected_label
