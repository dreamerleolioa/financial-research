"""Bounded date-scoped fundamental preparation and ordered nightly evidence refresh."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date
from http.client import HTTPException
import json
import os
import signal
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "evidence_redirect_rejected", headers, fp)


def post(endpoint, token, payload, timeout):
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("evidence_endpoint_requires_https")
    request = Request(endpoint, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        return _read_json(response)


def _read_json(response):
    body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024:
        raise ValueError("evidence_response_too_large")
    return json.loads(body)


@contextmanager
def request_deadline(seconds):
    """Bound the complete request, including DNS, headers and body, in this POSIX CLI."""
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    def expired(signum, frame):
        raise TimeoutError("evidence_http_wall_clock_timeout")
    signal.signal(signal.SIGALRM, expired)
    limit = min(seconds, previous_timer[0]) if previous_timer[0] > 0 else seconds
    signal.setitimer(signal.ITIMER_REAL, limit)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            remaining = max(0.000001, previous_timer[0] - (time.monotonic() - started))
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])


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

    def call(path, data, name):
        try:
            remaining = deadline - clock()
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
                if clock() > deadline:
                    response = {"error_type": "TimeoutError", "code": "evidence_preparation_budget_exhausted"}
        except (OSError, ValueError, HTTPException) as exc:
            response = {"error_type": type(exc).__name__}
        # The injected test token and a misbehaving upstream must never leak credentials into artifacts.
        serialized = json.dumps(response, ensure_ascii=False, indent=2)
        if token:
            serialized = serialized.replace(token, "[redacted]")
        (output_dir / f"{name}.json").write_text(serialized)
        return response

    def refresh(step):
        response = call(f"/internal/daily-radar/{step}", payload, step)
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
    draining_old_job = False
    backfill_completed = False
    gap_count = 0
    for attempt in range(1, 7):
        response = call("/internal/fundamentals/backfill", next_payload, f"backfill-{attempt}")
        if not isinstance(response, dict):
            break
        if (response.get("cache_ready_as_of") is True and response.get("status") == "ok"
                and response.get("raw_pool_date") == run_date and response.get("job_id") is None
                and "next_after_symbol" in response and response["next_after_symbol"] is None
                and response.get("errors") == [] and response.get("data_gaps") == []):
            backfill_completed = True
            break
        if response.get("http_status") == 409:
            body = response.get("response")
            detail = body.get("detail", {}) if isinstance(body, dict) else {}
            if not isinstance(detail, dict):
                break
            # Only a server-owned running job/cursor can be resumed; never guess from a transport failure.
            if (detail.get("code") != "fundamental_backfill_job_running" or expected_job
                    or not isinstance(detail.get("job_id"), str)
                    or not detail["job_id"] or "expected_after_symbol" not in detail):
                break
            cursor = detail["expected_after_symbol"]
            if cursor is not None and not isinstance(cursor, str):
                break
            expected_job = detail["job_id"]
            draining_old_job = True
            next_payload = {"scope": "managed", "limit": 10, "job_id": expected_job}
            if cursor is not None:
                next_payload["after_symbol"] = cursor
            continue
        job = response.get("job_id")
        cursor = response.get("next_after_symbol")
        if (response.get("status") not in ("ok", "partial") or not isinstance(job, str) or not job
                or (expected_job is not None and job != expected_job)
                or not isinstance(response.get("errors"), list)
                or not isinstance(response.get("data_gaps"), list)
                or (response.get("status") == "partial" and not response["errors"] and not response["data_gaps"])
                or "next_after_symbol" not in response
                or (cursor is not None and (not isinstance(cursor, str) or not cursor))
                or (not draining_old_job and response.get("raw_pool_date") != run_date)):
            break
        gap_count += len(response["data_gaps"])
        if response["errors"]:
            failures.append("fundamental_provider_failed")
        if cursor is None:
            if draining_old_job:
                # Completing unrelated maintenance does not establish today's pool coverage.
                next_payload, expected_job, draining_old_job = dict(initial), None, False
                continue
            backfill_completed = True
            break
        previous = next_payload.get("after_symbol")
        if previous is not None and cursor <= previous:
            break
        expected_job = job
        next_payload = {"scope": "managed", "limit": 10, "job_id": job, "after_symbol": cursor}
        if not draining_old_job:
            next_payload.update(raw_pool_date=run_date, radar_stage=stage)
    if not backfill_completed:
        failures.append("fundamental_preparation_incomplete")

    ready = None
    if mode == "finalize":
        response = call("/internal/daily-radar/refresh-ai-evidence", payload, "refresh-ai-evidence")
        ready = evidence_is_ready(response, run_date)
        if not ready:
            failures.append("ai_evidence_incomplete")
    summary = {"run_date": run_date, "mode": mode, "backfill_completed": backfill_completed,
        "evidence_ready": ready, "data_gap_count": gap_count, "failures": failures,
        "continuation": next_payload if not backfill_completed else None}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))
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
