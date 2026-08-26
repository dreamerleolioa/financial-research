from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / ".github" / "scripts" / "resolve_daily_radar_run_date.py"


def _resolve_run_date(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_scheduled_repair_uses_original_run_created_at_after_rerun() -> None:
    assert (
        _resolve_run_date(
            "--event-name",
            "schedule",
            "--schedule",
            "0 23 * * 1-5",
            "--run-created-at",
            "2026-07-11T00:03:11Z",
        )
        == "2026-07-10"
    )


def test_delayed_schedule_resolves_the_original_cron_slot_date() -> None:
    assert (
        _resolve_run_date(
            "--event-name",
            "schedule",
            "--schedule",
            "0 23 * * 1-5",
            "--run-created-at",
            "2026-07-13T05:37:53Z",
        )
        == "2026-07-10"
    )


def test_delayed_institutional_refresh_keeps_original_taiwan_trade_date() -> None:
    assert (
        _resolve_run_date(
            "--event-name",
            "schedule",
            "--schedule",
            "30 9 * * 1-5",
            "--run-created-at",
            "2026-08-26T17:15:00Z",
        )
        == "2026-08-26"
    )


def test_manual_dispatch_preserves_explicit_run_date() -> None:
    assert (
        _resolve_run_date(
            "--event-name",
            "workflow_dispatch",
            "--input-run-date",
            "2026-07-10",
        )
        == "2026-07-10"
    )


def test_manual_rerun_without_input_uses_original_taipei_creation_date() -> None:
    assert (
        _resolve_run_date(
            "--event-name",
            "workflow_dispatch",
            "--run-created-at",
            "2026-07-10T16:30:00Z",
        )
        == "2026-07-11"
    )
