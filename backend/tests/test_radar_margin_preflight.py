from pathlib import Path

import pytest

from ensure_daily_radar_margin import ensure_margin


DAY = "2026-11-17"


def readiness(ready=True):
    return {"version": "radar-readiness-v1", "scope": "publication", "market": "TW",
            "requested_run_date": DAY, "effective_run_date": DAY,
            "scoring": {"prepared_run_id": 1, "symbol_count": 214, "full_margin_ready": ready,
                        "blockers": [] if ready else [{"step": "refresh-full-margin",
                            "missing_symbols": ["2258.TW"], "reason": "same_day_context_incomplete"}]}}


def invoke(report, result=None):
    calls = []
    def get(url, token):
        assert url.endswith(f"readiness?market=TW&run_date={DAY}&scope=publication")
        return report
    def post(url, token, payload):
        assert url.endswith("refresh-full-margin") and payload == {"market": "TW", "run_date": DAY}
        calls.append(url)
        return result
    ok = ensure_margin("https://example.invalid", "fixture", DAY, read=get, refresh=post)
    return ok, len(calls)


def test_workflow_preflights_scheduled_and_manual_scoring():
    workflow = (Path(__file__).parents[2] / ".github/workflows/daily-radar.yml").read_text()
    job = workflow.split("\n  run-scoring:", 1)[1].split("\n  run-forward-validation:", 1)[0]
    assert "workflow_dispatch" in job and "30 16 * * 1-5" in job
    assert job.index("ensure_daily_radar_margin.py") < job.index("- name: Trigger run-scoring")


def test_valid_margin_skips_the_provider_even_before_publication():
    assert invoke(readiness()) == (True, 0)


@pytest.mark.parametrize("status,missing,day,ok", [
    ("completed", [], DAY, True), ("failed", ["2258.TW"], DAY, False),
    ("completed", ["2258.TW"], DAY, False), ("completed", [], "2026-11-16", False)])
def test_missing_margin_refreshes_the_same_trading_date(status, missing, day, ok, capsys):
    result = {"market": "TW", "step": "refresh-full-margin", "run_date": day, "status": status,
              "symbol_count": 214, "missing_symbols": missing, "errors": [],
              "missing_symbol_reasons": {s: "finmind_no_data" for s in missing}}
    assert invoke(readiness(False), result) == (ok, 1)
    if missing:
        assert "finmind_no_data" in capsys.readouterr().out


@pytest.mark.parametrize("change", [{"effective_run_date": "2026-11-16"}, {"market": "TWO"},
    {"scope": "all"}, {"scoring": {"blockers": []}}, {"scoring": {"full_margin_ready": True, "symbol_count": 0}}])
def test_invalid_preflight_stops_before_mutation(change):
    assert invoke(readiness() | change) == (False, 0)
