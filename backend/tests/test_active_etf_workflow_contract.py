from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/active-etf-holdings.yml"


def test_active_etf_workflow_has_bounded_weekday_refresh_slots() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "GitHub cron uses UTC. Taiwan time = UTC+8." in text
    assert 'cron: "0 0 * * 1-5"' in text
    assert 'cron: "0 6 * * 1-5"' in text
    assert "workflow_dispatch:" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 20" in text


def test_active_etf_workflow_uses_internal_auth_and_fails_on_partial_results() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "secrets.ZEABUR_BACKEND_URL" in text
    assert "secrets.DAILY_RADAR_INTERNAL_TOKEN" in text
    assert 'test -n "${ETF_API_BASE_URL}"' in text
    assert 'test -n "${ETF_INTERNAL_TOKEN}"' in text
    assert "/internal/active-etf-holdings/refresh" in text
    assert "curl --fail-with-body" in text
    assert "--connect-timeout 20" in text
    assert "--max-time 900" in text
    assert "--data '{}'" in text
    assert '.status == "completed"' in text
    assert '((.errors // []) | length == 0)' in text
    assert '((.verified_snapshots // 0) > 0)' in text
    assert '((.conflicted_snapshots // 0) == 0)' in text
    assert '(.single_source_snapshots // 0)' in text
    assert '== .selected_funds' in text
    assert "sk-" not in text
    assert "token=" not in text.lower()
