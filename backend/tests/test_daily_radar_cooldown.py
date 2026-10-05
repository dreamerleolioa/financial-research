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
    DAILY_RADAR_REPEAT_STATUSES[0]: "入選歷史待確認",
    DAILY_RADAR_REPEAT_STATUSES[1]: "曾列入觀察",
    DAILY_RADAR_REPEAT_STATUSES[2]: "訊號升級",
    DAILY_RADAR_REPEAT_STATUSES[3]: "訊號冷卻",
}


def _history_records() -> list[dict[str, Any]]:
    payload = json.loads((FIXTURE_DIR / "history_candidates.json").read_text(encoding="utf-8"))
    return [dict(row, scoring_version="test-v1") for row in payload["records"]]


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
        "scoring_version": "test-v1",
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

    assert len(results) == 1
    assert results[0]["symbol"] == "2317.TW"
    assert results[0]["repeat_status"] == "new"
    assert results[0]["input_snapshot"]["observation_history"]["membership_status"] == "new"



def test_cooldown_marks_recent_stable_candidate_as_repeat() -> None:
    results = apply_cooldown_status(
        [_candidate("2330.TW", score=81, bucket="institutional_accumulation")],
        _history_records(),
        run_date=RUN_DATE,
        config=CooldownConfig(lookback_days=5, score_upgrade_threshold=8),
    )

    assert results[0]["repeat_status"] == DAILY_RADAR_REPEAT_STATUSES[1]
    assert results[0]["observation_score"] == 81


def test_cooldown_marks_score_improvement_but_not_bucket_change_as_upgraded() -> None:
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
    assert bucket_upgrade["repeat_status"] == DAILY_RADAR_REPEAT_STATUSES[1]


def test_cooldown_retains_insufficient_recent_signal_with_cooled_label() -> None:
    results = apply_cooldown_status(
        [_candidate("2330.TW", score=57, bucket="institutional_accumulation")],
        _history_records(),
        run_date=RUN_DATE,
        config=CooldownConfig(lookback_days=5, min_current_signal_score=60),
    )

    assert len(results) == 1
    assert results[0]["repeat_status"] == "cooled_down"


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


def test_old_selection_is_returning_instead_of_first_observation() -> None:
    history = [dict(_candidate("2454.TW", score=70), record_date="2026-09-24")]
    result = apply_cooldown_status([_candidate("2454.TW", score=72)], history, run_date="2026-10-02")[0]
    assert result["repeat_status"] == "repeat"
    assert result["input_snapshot"]["observation_history"] == {
        "membership_status": "previously_selected",
        "first_seen_date": "2026-09-24",
        "last_seen_date": "2026-09-24",
        "appearance_count": 2,
        "consecutive_trading_days": None,
        "signal_status": "stable",
    }


def test_membership_uses_verified_trading_dates_and_counts_each_date_once() -> None:
    history = [dict(_candidate("2454.TW", score=70), record_date=day)
               for day in ["2026-09-24", "2026-09-24", "2026-09-25", "2026-10-01"]]
    result = apply_cooldown_status(
        [_candidate("2454.TW", score=72)], history, run_date="2026-10-02",
        trading_dates=["2026-09-24", "2026-09-25", "2026-10-01", "2026-10-02"],
    )[0]
    info = result["input_snapshot"]["observation_history"]
    assert info["membership_status"] == "continuing"
    assert info["appearance_count"] == 4
    assert info["consecutive_trading_days"] == 4


def test_returning_membership_requires_verified_previous_session_absence() -> None:
    history = [dict(_candidate("2454.TW", score=70), record_date="2026-09-24")]
    result = apply_cooldown_status([_candidate("2454.TW", score=72)], history, run_date="2026-10-02",
        trading_dates=["2026-09-24", "2026-10-01", "2026-10-02"])[0]
    assert result["input_snapshot"]["observation_history"]["membership_status"] == "returning"


def test_history_does_not_change_current_candidate_eligibility() -> None:
    history = [dict(_candidate("2330.TW", score=70), record_date="2026-10-01")]
    results = apply_cooldown_status(
        [_candidate("2330.TW", score=55), _candidate("2454.TW", score=55)], history, run_date="2026-10-02",
    )
    assert len(results) == 2
    assert results[0]["input_snapshot"]["observation_history"]["signal_status"] == "cooled_down"


def test_bucket_change_alone_is_not_signal_upgrade() -> None:
    history = [dict(_candidate("2330.TW", score=70, bucket="support_retest"), record_date="2026-10-01")]
    result = apply_cooldown_status([_candidate("2330.TW", score=70)], history, run_date="2026-10-02")[0]
    assert result["repeat_status"] == "repeat"


def test_scores_from_different_strategy_versions_are_not_compared() -> None:
    history = [dict(_candidate("2330.TW", score=60), record_date="2026-10-01", scoring_version="v1")]
    current = dict(_candidate("2330.TW", score=90), scoring_version="v2")
    result = apply_cooldown_status([current], history, run_date="2026-10-02")[0]
    assert result["repeat_status"] == "repeat"
    assert result["input_snapshot"]["observation_history"]["signal_status"] == "unknown"
