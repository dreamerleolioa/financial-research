"""Refresh nightly product raw data in order, preserving bounded failure diagnostics."""
from __future__ import annotations

import argparse
from datetime import date
from http.client import HTTPException
import os
from pathlib import Path
import time
from urllib.error import HTTPError

from daily_radar_http import post, request_deadline, _read_json, diagnostic_json


def run(*, base_url, token, run_date, output_dir, request=post, clock=time.monotonic):
    if date.fromisoformat(run_date).isoformat() != run_date:
        raise ValueError("invalid_raw_refresh_date")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    deadline = clock() + 1800
    payload = {"market": "TW", "run_date": run_date}
    failures = []
    for step in ("refresh-ohlcv", "refresh-managed-raw-data"):
        try:
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError("raw_refresh_budget_exhausted")
            with request_deadline(min(300, remaining)):
                try:
                    response = request(f"{base_url.rstrip('/')}/internal/daily-radar/{step}",
                                       token, payload, min(300, remaining))
                except HTTPError as exc:
                    try:
                        detail = _read_json(exc)
                    except (ValueError, OSError, HTTPException):
                        detail = None
                    response = {"http_status": exc.code, "error_type": "HTTPError", "response": detail}
            if clock() > deadline:
                raise TimeoutError("raw_refresh_budget_exhausted")
        except (OSError, ValueError, HTTPException) as exc:
            response = {"error_type": type(exc).__name__}
        (output_dir / f"{step}.json").write_text(diagnostic_json(response, token))
        ready = (isinstance(response, dict) and response.get("status") == "completed"
                 and response.get("step") == step and response.get("run_date") == run_date)
        if step == "refresh-ohlcv":
            ready = ready and response.get("missing_symbols") == [] and response.get("errors") == []
        else:
            ready = (ready and type(response.get("missing_record_count")) is int
                     and response["missing_record_count"] == 0 and response.get("error_codes") == [])
        if not ready:
            failures.append(step)
            if isinstance(response, dict) and response.get("symbol_diagnostics"):
                print(diagnostic_json({"step": step, "symbol_diagnostics": response["symbol_diagnostics"]},
                                      token, indent=None))
    summary = {"run_date": run_date, "failures": failures}
    (output_dir / "summary.json").write_text(diagnostic_json(summary, token))
    print(diagnostic_json(summary, token, indent=None))
    return int(bool(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    raise SystemExit(run(base_url=os.environ["ZEABUR_BACKEND_URL"],
                        token=os.environ["DAILY_RADAR_INTERNAL_TOKEN"],
                        run_date=args.run_date, output_dir=args.output_dir))
