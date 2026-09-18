"""Execute the actual workflow shell with a local HTTP stub; no external writes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def _page(*, cursor=None, errors=None, gaps=None):
    return {
        "status": "partial" if errors or gaps else "ok",
        "job_id": "test-job", "next_after_symbol": cursor,
        "errors": errors or [], "data_gaps": gaps or [],
    }


def _run_workflow(tmp_path, pages, *, event="workflow_dispatch", raw_pool_date="", http_status=200):
    workflow = (Path(__file__).parents[2] / ".github/workflows/fundamental-data.yml").read_text()
    section = workflow.split("      - name: Backfill managed", 1)[1]
    script = textwrap.dedent(section.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    (tmp_path / "pages.json").write_text(json.dumps(pages))
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""
        import json, os, pathlib, sys
        root = pathlib.Path(os.environ['STUB_ROOT'])
        log = root / 'requests.jsonl'
        index = len(log.read_text().splitlines()) if log.exists() else 0
        args = sys.argv[1:]
        with log.open('a') as file:
            file.write(args[args.index('-d') + 1] + '\\n')
        pages = json.loads((root / 'pages.json').read_text())
        pathlib.Path(args[args.index('-o') + 1]).write_text(json.dumps(pages[index]))
        print(os.environ['STUB_HTTP_STATUS'], end='')
    """))
    fake_curl.chmod(0o755)
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env={
            **os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "TMPDIR": str(tmp_path),
            "STUB_ROOT": str(tmp_path), "STUB_HTTP_STATUS": str(http_status),
            "BACKFILL_AFTER_SYMBOL": "", "BACKFILL_JOB_ID": "",
            "BACKFILL_RAW_POOL_DATE": raw_pool_date, "GITHUB_EVENT_NAME": event,
            "GITHUB_STEP_SUMMARY": str(summary), "API_BASE_URL": "https://example.invalid",
            "DAILY_RADAR_INTERNAL_TOKEN": "test-only",
        }, capture_output=True, text=True, timeout=30,
    )
    requests = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    return result, requests, summary.read_text() if summary.exists() else ""


def test_data_gap_page_continues_and_finishes_without_manual_cursor(tmp_path):
    gap = {"symbol": "3718.TWO", "reason": "no_eps_history", "period_count": 0}
    short_history = {"symbol": "7828.TWO", "reason": "insufficient_eps_history", "period_count": 6}
    result, requests, summary = _run_workflow(
        tmp_path, [_page(cursor="7714.TWO", gaps=[gap]), _page(gaps=[short_history])],
    )
    assert result.returncode == 0, result.stderr
    assert requests[0]["resume_running_job"] is True
    assert requests[1] == {"scope": "managed", "limit": 10, "job_id": "test-job", "after_symbol": "7714.TWO"}
    assert "3718.TWO" in summary and "no_eps_history" in summary
    assert len(requests) == 2
    assert "7828.TWO" in summary and "insufficient_eps_history" in summary
    assert "Data gaps observed in this run: 2" in summary


def test_provider_error_does_not_starve_later_pages_but_still_fails(tmp_path):
    result, requests, summary = _run_workflow(
        tmp_path, [_page(cursor="7714.TWO", errors=["MOPS timeout"]), _page()],
    )
    assert result.returncode == 1
    assert len(requests) == 2
    assert "MOPS timeout" in summary


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_budget_exhaustion_is_automatic_progress_for_both_entrypoints(tmp_path, event):
    result, requests, summary = _run_workflow(
        tmp_path, [_page(cursor=f"{1000 + index}.TW") for index in range(6)], event=event,
    )
    assert result.returncode == 0, result.stderr
    assert len(requests) == 6
    assert "will resume automatically" in summary


def test_explicit_raw_pool_date_does_not_request_conflicting_resume(tmp_path):
    result, requests, _ = _run_workflow(tmp_path, [_page()], raw_pool_date="2026-09-15")
    assert result.returncode == 0
    assert requests[0]["raw_pool_date"] == "2026-09-15"
    assert "resume_running_job" not in requests[0]


def test_unexplained_partial_is_not_silently_successful(tmp_path):
    page = _page()
    page["status"] = "partial"
    result, _, _ = _run_workflow(tmp_path, [page])
    assert result.returncode == 1


def test_legacy_partial_errors_remain_failures(tmp_path):
    page = _page(errors=["3718.TWO: statement backfill incomplete"])
    del page["data_gaps"]
    result, _, summary = _run_workflow(tmp_path, [page])
    assert result.returncode == 1
    assert "3718.TWO" in summary


def test_http_failure_stops_without_guessing_cursor(tmp_path):
    result, requests, _ = _run_workflow(tmp_path, [{"detail": "unavailable"}], http_status=503)
    assert result.returncode == 1
    assert len(requests) == 1
