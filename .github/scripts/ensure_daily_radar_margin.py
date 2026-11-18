"""Reconfirm expired margin evidence before legacy scheduled or manual scoring."""
import argparse
from datetime import date
from http.client import HTTPException
import os
from urllib.parse import urlencode

from daily_radar_http import diagnostic_json, get, post, request_deadline


def ensure_margin(endpoint, token, run_date, *, read=get, refresh=post):
    base = endpoint.rstrip("/") + "/internal/daily-radar/"
    target = {"market": "TW", "run_date": run_date}
    with request_deadline(180):
        report = read(base + "readiness?" + urlencode(target | {"scope": "publication"}), token)
    if (not isinstance(report, dict) or report.get("scope") != "publication"
            or report.get("version") != "radar-readiness-v1" or report.get("market") != "TW"
            or report.get("effective_run_date") != run_date or report.get("requested_run_date") != run_date):
        print("Invalid publication readiness for", run_date)
        return False
    scoring = report.get("scoring")
    if (not isinstance(scoring, dict) or not scoring.get("prepared_run_id")
            or type(scoring.get("symbol_count")) is not int or scoring["symbol_count"] <= 0
            or not isinstance(scoring.get("blockers"), list)
            or not all(isinstance(blocker, dict) for blocker in scoring["blockers"])
            or type(scoring.get("full_margin_ready")) is not bool):
        print("Prepared margin readiness unavailable for", run_date)
        return False
    gaps = [b for b in scoring["blockers"] if b.get("step") == "refresh-full-margin"]
    if scoring["full_margin_ready"] == bool(gaps):
        print("Inconsistent margin readiness for", run_date)
        return False
    if scoring["full_margin_ready"]:
        print(f"Margin verified for all {scoring['symbol_count']} symbols on {run_date}")
        return True

    print(diagnostic_json(gaps, token))
    with request_deadline(360):
        result = refresh(base + "refresh-full-margin", token, target)
    if not isinstance(result, dict):
        return False
    print(diagnostic_json({k: result.get(k) for k in ("run_date", "status", "records_written",
        "missing_symbols", "missing_symbol_reasons", "errors")}, token))
    return (result.get("step") == "refresh-full-margin" and result.get("market") == "TW"
            and result.get("run_date") == run_date and result.get("symbol_count") == scoring["symbol_count"]
            and result.get("status") == "completed" and result.get("missing_symbols") == []
            and result.get("missing_symbol_reasons") == {} and result.get("errors") == [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    try:
        ready = ensure_margin(os.environ["ZEABUR_BACKEND_URL"], os.environ["DAILY_RADAR_INTERNAL_TOKEN"],
                              args.run_date.isoformat())
    except (OSError, HTTPException, ValueError, TypeError, KeyError) as exc:
        print("Margin preflight failed", type(exc).__name__)
        ready = False
    raise SystemExit(0 if ready else 1)
