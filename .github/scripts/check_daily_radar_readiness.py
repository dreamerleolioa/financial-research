"""Read persisted readiness without refreshing or changing production data."""
import argparse
from http.client import HTTPException
from datetime import date
import os
from pathlib import Path
from urllib.parse import urlencode

from daily_radar_http import get, diagnostic_json, request_deadline


def valid_readiness(result, run_date):
    return (isinstance(result, dict) and result.get('version') == 'radar-readiness-v1'
        and result.get('market') == 'TW' and result.get('requested_run_date') == run_date
        and result.get('effective_run_date') == run_date
        and all(isinstance(result.get(key), dict) for key in ('publication', 'scoring', 'validation'))
        and all(isinstance(result['validation'].get(basis), dict) for basis in ('signal_close', 'next_open')))


def scope_ready(result, run_date, scope):
    if not valid_readiness(result, run_date):
        return False
    if scope == 'publication':
        provenance = result['publication'].get('input_manifest')
        return (result['publication'].get('state') == 'published'
            and result['publication'].get('status') == 'completed'
            and result['publication'].get('output_complete', True) is True
            and result['scoring'].get('ready') is True
            and (provenance is None or (provenance.get('state') == 'recorded'
                 and provenance.get('matches_current_inputs') is True)))
    if scope == 'all':
        return result.get('data_ready') is True
    return result['validation'].get(scope, {}).get('ready') is True


def run(*, endpoint, token, run_date, output_dir, scope="all", request=get):
    if date.fromisoformat(run_date).isoformat() != run_date:
        raise ValueError("invalid_readiness_date")
    if scope not in {"all", "publication", "signal_close", "next_open"}:
        raise ValueError("invalid_readiness_scope")
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    try:
        with request_deadline(180):
            result = request(endpoint.rstrip("/") + "/internal/daily-radar/readiness?" + urlencode(
                {"market": "TW", "run_date": run_date}), token)
        if not valid_readiness(result, run_date):
            raise ValueError("invalid_readiness_response")
        ready = scope_ready(result, run_date, scope)
    except (OSError, HTTPException, ValueError, TypeError, AttributeError) as exc:
        result, ready = {"error_type": type(exc).__name__}, False
    (path / f"readiness-{scope}.json").write_text(diagnostic_json(result, token))
    print(diagnostic_json({"run_date": run_date, "scope": scope, "ready": ready,
        "publication": result.get("publication"), "scoring_ready": result.get("scoring", {}).get("ready"),
        "validation": {basis: {key: value for key, value in report.items() if key != "gaps"}
            for basis, report in result.get("validation", {}).items()},
        "error_type": result.get("error_type")}, token, indent=None), flush=True)
    return int(not ready)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scope", choices=("all", "publication", "signal_close", "next_open"), default="all")
    args = parser.parse_args()
    return run(endpoint=os.environ["ZEABUR_BACKEND_URL"], token=os.environ["DAILY_RADAR_INTERNAL_TOKEN"],
        run_date=str(args.run_date), output_dir=args.output_dir, scope=args.scope)


if __name__ == "__main__":
    raise SystemExit(main())
