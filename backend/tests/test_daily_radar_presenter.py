from __future__ import annotations

from ai_stock_sentinel.daily_radar.presenter import history_response


def test_history_response_projects_retired_technical_judgments_without_mutating_replay() -> None:
    profile = {
        "version": "technical-layer-v2",
        "signal_conflicts": [{"message": "cached composite judgment"}],
        "temporal_evidence": {
            "ma20_slope": {"state": "rising"},
            "volatility_regime": {"state": "expansion"},
        },
    }
    input_snapshot = {
        "technical_profile": profile,
        "replay_input": {
            "record": {
                "technical_profile": profile,
            }
        },
    }
    item = {
        "symbol": "2330.TW",
        "name": "台積電",
        "record_date": "2026-08-28",
        "primary_bucket": "trend_continuation",
        "secondary_buckets": [],
        "observation_score": 80,
        "risk_labels": [],
        "repeat_status": "new",
        "input_snapshot": input_snapshot,
    }

    response = history_response(item)

    public_profile = response["input_snapshot"]["technical_profile"]
    replay_profile = response["input_snapshot"]["replay_input"]["record"]["technical_profile"]
    assert "signal_conflicts" not in public_profile
    assert "volatility_regime" not in public_profile["temporal_evidence"]
    assert "signal_conflicts" not in replay_profile
    assert "volatility_regime" not in replay_profile["temporal_evidence"]
    assert "signal_conflicts" in input_snapshot["technical_profile"]
    assert "volatility_regime" in input_snapshot["replay_input"]["record"]["technical_profile"]["temporal_evidence"]
