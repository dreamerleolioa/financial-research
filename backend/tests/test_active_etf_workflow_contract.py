from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/active-etf-holdings.yml"


def _workflow_gate_accepts(payload: dict) -> bool:
    text = WORKFLOW.read_text(encoding="utf-8")
    gate = text.split("          jq -e '\n", maxsplit=1)[1].split(
        "\n          ' \"${response_file}\"",
        maxsplit=1,
    )[0]
    result = subprocess.run(
        ["jq", "-e", gate],
        input=json.dumps(payload),
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode == 0


def test_active_etf_workflow_has_bounded_weekday_refresh_slots() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "GitHub cron uses UTC. Taiwan time = UTC+8." in text
    assert "08:00 與 19:00" in text
    assert 'cron: "0 0 * * 1-5"' in text
    assert 'cron: "0 11 * * 1-5"' in text
    assert 'cron: "0 6 * * 1-5"' not in text
    assert "workflow_dispatch:" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 20" in text


def test_active_etf_workflow_only_allows_explicitly_unpublished_partial_results() -> None:
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
    assert '.status == "partial"' in text
    assert 'all(.code == "active_etf_holdings_not_published")' in text
    assert 'select(.code == "active_etf_holdings_not_published")' in text
    assert '((.verified_snapshots // 0) == 0)' in text
    assert '((.conflicted_snapshots // 0) == 0)' in text
    assert '(.single_source_snapshots // 0)' in text
    assert '(.snapshots_created // 0)' in text
    assert '(.snapshots_updated // 0)' in text
    assert '(.snapshots_reused // 0)' in text
    assert '== .selected_funds' in text
    assert "sk-" not in text
    assert "token=" not in text.lower()


def test_active_etf_workflow_gate_rejects_operational_failures_and_count_mismatches() -> None:
    unpublished_payload = {
        "status": "partial",
        "selected_funds": 30,
        "snapshots_created": 29,
        "snapshots_updated": 0,
        "snapshots_reused": 0,
        "verified_snapshots": 0,
        "single_source_snapshots": 29,
        "conflicted_snapshots": 0,
        "errors": [
            {
                "fund_code": "00409A",
                "code": "active_etf_holdings_not_published",
            }
        ],
    }

    assert _workflow_gate_accepts(unpublished_payload) is True
    assert (
        _workflow_gate_accepts(
            {
                **unpublished_payload,
                "errors": [
                    {
                        "fund_code": "00409A",
                        "code": "active_etf_snapshot_fetch_failed",
                    }
                ],
            }
        )
        is False
    )
    assert (
        _workflow_gate_accepts(
            {
                **unpublished_payload,
                "single_source_snapshots": 26,
                "conflicted_snapshots": 3,
            }
        )
        is False
    )
    assert (
        _workflow_gate_accepts(
            {
                **unpublished_payload,
                "snapshots_created": 28,
            }
        )
        is False
    )
