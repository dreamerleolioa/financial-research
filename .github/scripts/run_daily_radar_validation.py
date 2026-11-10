"""Run both validation bases independently, retaining bounded failure evidence."""
import argparse
from http.client import HTTPException
from datetime import date
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode

from daily_radar_http import get, post, request_deadline, _NoRedirect, diagnostic_json
from check_daily_radar_readiness import valid_readiness, scope_ready


def run(*, endpoint, token, run_date, output_dir, request=post, sleep=time.sleep,
        attempts=6, basis_timeout=900, clock=time.monotonic,
        return_bases=("signal_close", "next_open"), readiness_request=get):
    if date.fromisoformat(run_date).isoformat() != run_date:
        raise ValueError("invalid_validation_date")
    if type(attempts) is not int or attempts < 1 or basis_timeout <= 0:
        raise ValueError("invalid_validation_budget")
    if not return_bases or len(set(return_bases)) != len(return_bases) or any(
            basis not in {"signal_close", "next_open"} for basis in return_bases):
        raise ValueError("invalid_return_bases")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    failed = []
    for basis in return_bases:
        payload = {"mode": "due", "market": "TW", "as_of_date": run_date,
                   "windows": [5, 10, 20, 40, 60], "return_basis": basis, "benchmark_symbol": "TAIEX"}
        ready = False
        deadline = clock() + basis_timeout
        attempt_limit = attempts if basis == "next_open" else min(attempts, 2)
        previous_remaining = None
        transport_failures = 0
        for attempt in range(1, attempt_limit + 1):
            retryable = False
            valid = False
            readiness = None
            validation_response = None
            validation_response_received = False
            validation_error = None
            readiness_error = None
            phase = 'validation'
            failure = None
            needs_review = False
            readiness_blocked = False
            try:
                remaining_time = deadline - clock()
                if remaining_time <= 0:
                    raise TimeoutError("validation_budget_exhausted")
                with request_deadline(min(300, remaining_time)):
                    response = request(endpoint, token, payload)
                    # Save before context exit: the deadline can expire after
                    # the server's complete response has already been decoded.
                    validation_response = response
                    validation_response_received = True
                if clock() > deadline:
                    raise TimeoutError("validation_budget_exhausted")
                if not isinstance(response, dict):
                    raise ValueError("validation_response_invalid")
                report = response.get("report") if isinstance(response.get("report"), dict) else {}
                remaining = report.get("remaining_retryable_window_count") if basis == "next_open" else 0
                valid = (response.get("status") == "completed" and response.get("as_of_date") == run_date
                         and type(response.get("retryable_skipped_count")) is int
                         and response["retryable_skipped_count"] >= 0
                         and type(remaining) is int and remaining >= 0)
                if not valid:
                    raise ValueError('validation_response_invalid')
                ready = valid and response["retryable_skipped_count"] == 0 and remaining == 0
                # Compatibility with deployed reports predating the readiness
                # gate: protected legacy returns must never be called completed.
                for key in ('skip_reasons', 'current_run_skip_reasons'):
                    reasons = report.get(key, {})
                    if not isinstance(reasons, dict) or any(type(v) is not int or v < 0 for v in reasons.values()):
                        raise ValueError('validation_skip_reasons_invalid')
                    needs_review |= reasons.get('validation_input_unverified', 0) > 0
                ready = ready and not needs_review
                if ready:
                    phase = 'readiness'
                    remaining_time = deadline - clock()
                    if remaining_time <= 0:
                        raise TimeoutError('validation_budget_exhausted')
                    root = endpoint.removesuffix('/internal/daily-radar/forward-validation/run')
                    with request_deadline(min(180, remaining_time)):
                        readiness = readiness_request(root + '/internal/daily-radar/readiness?' + urlencode(
                            {'market': 'TW', 'run_date': run_date}), token)
                    if clock() > deadline:
                        raise TimeoutError('validation_budget_exhausted')
                    if not valid_readiness(readiness, run_date):
                        raise ValueError('invalid_readiness_response')
                    ready = scope_ready(readiness, run_date, basis)
                    readiness_blocked = not ready
                    inventory = readiness['validation'][basis]
                    reasons, gaps = inventory.get('reasons', {}), inventory.get('gaps', [])
                    unverified = inventory.get('input_unverified_count', 0)
                    if (not isinstance(reasons, dict) or not isinstance(gaps, list)
                            or any(not isinstance(g, dict) for g in gaps)
                            or any(type(v) is not int or v < 0 for v in reasons.values())
                            or type(unverified) is not int or unverified < 0):
                        raise ValueError('invalid_readiness_diagnostics')
                    needs_review = (unverified > 0 or reasons.get('validation_input_unverified', 0) > 0
                        or any(g.get('next_action') == 'needs_review' for g in gaps))
                    ready = ready and not needs_review
                retryable = valid and not ready
                if needs_review or readiness_blocked:
                    retryable = False
                transport_failures = 0
            except HTTPError as exc:
                ready = valid = False
                failure = response = {"error_type": "HTTPError", "http_status": exc.code}
                retryable = exc.code in (408, 425, 429) or 500 <= exc.code < 600
                transport_failures += 1
            except (OSError, HTTPException) as exc:
                ready = valid = False
                failure = response = {"error_type": type(exc).__name__}
                retryable = True
                transport_failures += 1
            except (ValueError, TypeError) as exc:
                ready = valid = False
                failure = response = {"error_type": type(exc).__name__}
            if failure is not None:
                if phase == 'readiness':
                    readiness_error = failure
                else:
                    validation_error = failure
                if validation_response_received and isinstance(validation_response, dict):
                    response = validation_response
            stop_reason = None
            if ready:
                stop_reason = "completed"
            elif needs_review:
                stop_reason = 'needs_review'
            elif readiness_blocked:
                stop_reason = 'readiness_not_ready'
            elif clock() >= deadline:
                stop_reason = "budget_exhausted"
            elif not retryable:
                stop_reason = "non_retryable_response"
            elif transport_failures >= 2:
                stop_reason = "transport_retry_limit"
            elif (valid and basis == "next_open" and previous_remaining is not None
                  and remaining >= previous_remaining):
                stop_reason = "no_progress"
            elif attempt == attempt_limit:
                stop_reason = "attempt_limit"
            diagnostic = response | {"readiness": readiness, "readiness_error": readiness_error,
                                    'validation_response': validation_response,
                                    'validation_response_received': validation_response_received,
                                    'validation_error': validation_error,
                                    "runner": {"return_basis": basis, "attempt": attempt,
                                                 "stop_reason": stop_reason}}
            (output_dir / f"{basis}-attempt-{attempt}.json").write_text(diagnostic_json(diagnostic, token))
            report = response.get("report") if isinstance(response.get("report"), dict) else {}
            recovery = report.get("source_recovery")
            recovery_errors = ([row for row in recovery if isinstance(row, dict)
                                and (row.get("error_type") or row.get("reason"))]
                               if isinstance(recovery, list) else None)
            summary = {key: response.get(key) for key in (
                "status", "as_of_date", "candidate_count", "records_written", "validated_count",
                "skipped_count", "retryable_skipped_count", "terminal_skipped_count", "error_type", "http_status")}
            summary.update(return_basis=basis, attempt=attempt,
                stop_reason=stop_reason,
                readiness_ready=ready if phase == 'readiness' else None,
                validation_response_received=validation_response_received,
                validation_error=validation_error,
                readiness_error=readiness_error,
                current_run_skip_reasons=report.get("current_run_skip_reasons"),
                remaining_retryable_window_count=report.get("remaining_retryable_window_count"),
                persisted_skip_reasons=report.get("skip_reasons", {}),
                current_run_gaps=report.get("current_run_gaps"),
                source_recovery_summary=report.get("source_recovery_summary"),
                source_recovery_errors=recovery_errors[:8] if recovery_errors is not None else None,
                current_run_diagnostics_available=(isinstance(report.get("current_run_skip_reasons"), dict)
                                                   and isinstance(report.get("current_run_gaps"), list)))
            print(diagnostic_json(summary, token, indent=None), flush=True)
            if stop_reason:
                break
            if valid and basis == "next_open":
                previous_remaining = remaining
            if attempt < attempts:
                sleep(min(5, max(0, deadline - clock())))
        if not ready:
            failed.append(basis)
    print(diagnostic_json({"run_date": run_date, "failed_return_bases": failed}, token, indent=None), flush=True)
    return int(bool(failed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--return-basis", choices=("both", "signal_close", "next_open"), default="both")
    args = parser.parse_args()
    return run(endpoint=os.environ["ZEABUR_BACKEND_URL"].rstrip("/") + "/internal/daily-radar/forward-validation/run",
               token=os.environ["DAILY_RADAR_INTERNAL_TOKEN"], run_date=str(args.run_date), output_dir=args.output_dir,
               return_bases=("signal_close", "next_open") if args.return_basis == "both" else (args.return_basis,))


if __name__ == "__main__":
    raise SystemExit(main())
