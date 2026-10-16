"""Run both validation bases independently, retaining bounded failure evidence."""
import argparse
from http.client import HTTPException
from datetime import date
import os
from pathlib import Path
import time
from urllib.error import HTTPError

from daily_radar_http import post, request_deadline, _NoRedirect, diagnostic_json


def run(*, endpoint, token, run_date, output_dir, request=post, sleep=time.sleep, attempts=2):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    failed = []
    for basis in ("signal_close", "next_open"):
        payload = {"mode": "due", "market": "TW", "as_of_date": run_date,
                   "windows": [5, 10, 20, 40, 60], "return_basis": basis, "benchmark_symbol": "TAIEX"}
        ready = False
        for attempt in range(1, attempts + 1):
            retryable = False
            try:
                with request_deadline(300):
                    response = request(endpoint, token, payload)
                if not isinstance(response, dict):
                    raise ValueError("validation_response_invalid")
                report = response.get("report") if isinstance(response.get("report"), dict) else {}
                remaining = report.get("remaining_retryable_window_count") if basis == "next_open" else 0
                ready = (response.get("status") == "completed" and response.get("as_of_date") == run_date
                         and type(response.get("retryable_skipped_count")) is int
                         and response["retryable_skipped_count"] == 0
                         and type(remaining) is int and remaining == 0)
                retryable = (response.get("status") == "completed" and response.get("as_of_date") == run_date
                             and type(response.get("retryable_skipped_count")) is int
                             and type(remaining) is int
                             and (response["retryable_skipped_count"] > 0 or remaining > 0))
            except HTTPError as exc:
                response = {"error_type": "HTTPError", "http_status": exc.code}
                retryable = exc.code in (408, 425, 429) or 500 <= exc.code < 600
            except (OSError, HTTPException) as exc:
                response = {"error_type": type(exc).__name__}
                retryable = True
            except (ValueError, TypeError) as exc:
                response = {"error_type": type(exc).__name__}
            (output_dir / f"{basis}-attempt-{attempt}.json").write_text(diagnostic_json(response, token))
            report = response.get("report") if isinstance(response.get("report"), dict) else {}
            summary = {key: response.get(key) for key in (
                "status", "as_of_date", "candidate_count", "records_written", "validated_count",
                "skipped_count", "retryable_skipped_count", "terminal_skipped_count", "error_type", "http_status")}
            summary.update(return_basis=basis, attempt=attempt,
                current_run_skip_reasons=report.get("current_run_skip_reasons", {}),
                remaining_retryable_window_count=report.get("remaining_retryable_window_count"),
                persisted_skip_reasons=report.get("skip_reasons", {}),
                current_run_gaps=report.get("current_run_gaps", []))
            print(diagnostic_json(summary, token, indent=None), flush=True)
            if ready or not retryable:
                break
            if attempt < attempts:
                sleep(5)
        if not ready:
            failed.append(basis)
    print(diagnostic_json({"run_date": run_date, "failed_return_bases": failed}, token, indent=None), flush=True)
    return int(bool(failed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    return run(endpoint=os.environ["ZEABUR_BACKEND_URL"].rstrip("/") + "/internal/daily-radar/forward-validation/run",
               token=os.environ["DAILY_RADAR_INTERNAL_TOKEN"], run_date=str(args.run_date), output_dir=args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
