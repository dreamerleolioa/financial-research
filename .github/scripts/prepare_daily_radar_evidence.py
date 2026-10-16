"""Bounded date-scoped fundamental preparation and ordered nightly evidence refresh."""
from __future__ import annotations

import argparse
from datetime import date
from http.client import HTTPException
import os
from pathlib import Path
import time
from urllib.error import HTTPError

from daily_radar_http import post, request_deadline, _read_json, _NoRedirect, diagnostic_json


def evidence_is_ready(response, run_date):
    if not isinstance(response, dict):
        return False
    lanes = response.get("missing_by_lane")
    return (response.get("status") == "completed" and response.get("run_date") == run_date
        and response.get("step") == "refresh-ai-evidence" and response.get("evidence_ready") is True
        and response.get("errors") == [] and isinstance(lanes, dict)
        and all(lane in lanes for lane in ("technical", "institutional", "margin", "fundamental"))
        and all(isinstance(items, list) and not items for items in lanes.values()))


def run(*, base_url, token, run_date, mode, output_dir, request=post, clock=time.monotonic):
    if date.fromisoformat(run_date).isoformat() != run_date or mode not in ("prewarm", "finalize"):
        raise ValueError("invalid_evidence_preparation_context")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    deadline = clock() + 1800
    payload = {"market": "TW", "run_date": run_date}
    failures = []

    def call(path, data, name, reserve=0):
        try:
            remaining = deadline - reserve - clock()
            if remaining <= 0:
                response = {"error_type": "TimeoutError", "code": "evidence_preparation_budget_exhausted"}
            else:
                with request_deadline(min(300, remaining)):
                    try:
                        response = request(f"{base_url.rstrip('/')}{path}", token, data, min(300, remaining))
                    except HTTPError as exc:
                        try:
                            detail = _read_json(exc)
                        except (ValueError, OSError, HTTPException):
                            detail = None
                        response = {"http_status": exc.code, "error_type": "HTTPError", "response": detail}
                if clock() > deadline - reserve:
                    response = {"error_type": "TimeoutError", "code": "evidence_preparation_budget_exhausted"}
        except (OSError, ValueError, HTTPException) as exc:
            response = {"error_type": type(exc).__name__}
        # The injected test token and a misbehaving upstream must never leak credentials into artifacts.
        serialized = diagnostic_json(response, token)
        (output_dir / f"{name}.json").write_text(serialized)
        return response

    def refresh(step):
        response = call(f"/internal/daily-radar/{step}", payload, step, reserve=300)
        ready = (isinstance(response, dict) and response.get("status") == "completed"
            and response.get("step") == step and response.get("run_date") == run_date)
        if step == "refresh-managed-raw-data":
            ready = ready and type(response.get("missing_record_count")) is int and response["missing_record_count"] == 0
        if step == "refresh-ohlcv":
            ready = ready and response.get("missing_symbols") == []
        if not ready:
            failures.append(step)

    if mode == "finalize":
        refresh("refresh-ohlcv")
        refresh("refresh-managed-raw-data")

    stage = "prepared" if mode == "prewarm" else "raw_pool"
    initial = {"scope": "managed", "limit": 10, "raw_pool_date": run_date, "radar_stage": stage}
    next_payload = dict(initial)
    expected_job = None
    backfill_completed = False
    pagination_completed = False
    gap_count = 0
    for attempt in range(1, 7):
        response = call("/internal/fundamentals/backfill", next_payload, f"backfill-{attempt}",
            reserve=300 if mode == "finalize" else 0)
        if not isinstance(response, dict):
            break
        if (response.get("purpose") != "radar_ttm_eps" or response.get("radar_stage") != stage
                or response.get("raw_pool_date") != run_date
                or response.get("status") not in ("ok", "partial")
                or not isinstance(response.get("errors"), list) or not isinstance(response.get("data_gaps"), list)
                or type(response.get("remaining_gap_count")) is not int or response["remaining_gap_count"] < 0
                or type(response.get("cache_ready_as_of")) is not bool
                or response["cache_ready_as_of"] != (response["remaining_gap_count"] == 0)
                or "next_after_symbol" not in response):
            break
        job = response.get("job_id")
        cursor = response["next_after_symbol"]
        if job is None:
            if (cursor is None and response["status"] == "ok"
                    and response["cache_ready_as_of"] and not response["errors"] and not response["data_gaps"]):
                backfill_completed = pagination_completed = True
            break
        if (not isinstance(job, str) or not job or (expected_job is not None and job != expected_job)
                or (cursor is not None and (not isinstance(cursor, str) or not cursor))
                or (response["status"] == "partial" and not response["errors"] and not response["data_gaps"])):
            break
        gap_count += len(response["data_gaps"])
        if response["errors"]:
            failures.append("fundamental_provider_failed")
        if cursor is None:
            pagination_completed = True
            backfill_completed = response["cache_ready_as_of"] and not response["errors"] and not response["data_gaps"]
            next_payload = dict(initial)  # Completed snapshots cannot be resumed; a future attempt rechecks the pool.
            if not backfill_completed:
                failures.append("fundamental_coverage_incomplete")
            break
        previous = next_payload.get("after_symbol")
        if previous is not None and cursor <= previous:
            break
        expected_job = job
        next_payload = {**initial, "job_id": job, "after_symbol": cursor}
    if not backfill_completed:
        failures.append("fundamental_preparation_incomplete")

    ready = None
    if mode == "finalize":
        response = call("/internal/daily-radar/refresh-ai-evidence", payload, "refresh-ai-evidence")
        ready = evidence_is_ready(response, run_date)
        if not ready:
            failures.append("ai_evidence_incomplete")
    summary = {"run_date": run_date, "mode": mode, "backfill_completed": backfill_completed,
        "evidence_ready": ready, "pagination_completed": pagination_completed, "data_gap_count": gap_count, "failures": failures,
        "continuation": next_payload if not backfill_completed else None}
    (output_dir / "summary.json").write_text(diagnostic_json(summary, token))
    print(diagnostic_json(summary, token, indent=None))
    return int(bool(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--mode", choices=("prewarm", "finalize"), required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    raise SystemExit(run(base_url=os.environ["ZEABUR_BACKEND_URL"],
        token=os.environ["DAILY_RADAR_INTERNAL_TOKEN"], run_date=args.run_date,
        mode=args.mode, output_dir=args.output_dir))
