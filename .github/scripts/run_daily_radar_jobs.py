"""Register immutable intent or resume stored jobs within a bounded foreground budget."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import time
from daily_radar_http import get, post, diagnostic_json, request_deadline


def run(*, endpoint, token, output_dir, run_date=None, resume=False, max_work=100, max_seconds=900,
        require_lanes=(), read=get, write=post, clock=time.monotonic):
    if (resume == (run_date is not None) or not 1 <= max_work <= 1000 or not 1 <= max_seconds <= 1800):
        raise ValueError('invalid_durable_job_scope_or_budget')
    if run_date is not None and date.fromisoformat(run_date).isoformat() != run_date:
        raise ValueError('invalid_run_date')
    if set(require_lanes) - {'publication', 'signal_close', 'next_open'}:
        raise ValueError('invalid_required_lanes')
    if resume and require_lanes:
        raise ValueError('deadline_check_requires_exact_date')
    endpoint = endpoint.rstrip('/') + '/internal/daily-radar'
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    deadline = clock() + max_seconds
    def call(fn, path, *args):
        seconds = min(180, deadline - clock())
        if seconds <= 0:
            raise TimeoutError('radar_driver_budget_exhausted')
        with request_deadline(seconds):
            return fn(endpoint + path, token, *args, timeout=seconds)
    capability = call(read, '/capabilities')
    if capability.get('enabled') is not True or capability.get('version') != 'radar-durable-v1':
        raise ValueError('radar_durable_execution_not_ready')
    if resume:
        jobs = call(read, '/jobs/due')['jobs']
    else:
        registered = call(write, '/jobs', {'run_date': run_date, 'market': 'TW'})
        if registered.get('effective_run_date') != run_date or registered.get('requested_run_date') != run_date:
            raise ValueError('radar_job_date_mismatch')
        jobs = [{'job_id': registered['job_id'], 'run_date': run_date}]
    advanced, summaries, errors = 0, {}, []
    # Round-robin prevents an older provider outage from starving newer dates.
    pending = list(jobs)
    while pending and advanced < max_work and clock() < deadline - (30 if require_lanes else 0):
        job = pending.pop(0)
        try:
            status = call(write, f"/jobs/{job['job_id']}/advance", {})
            if status.get('effective_run_date') != job['run_date'] or status.get('requested_run_date') != job['run_date']:
                raise ValueError('radar_job_date_mismatch')
            summaries[job['job_id']] = status
            advanced += 1
            if status.get('advanced_count', 0) > 0 or status.get('runnable_now') is True:
                pending.append(job)
        except Exception as exc:
            errors.append({'job_id': job['job_id'], 'error_type': type(exc).__name__})
    failed = bool(errors)
    if require_lanes:
        if resume:
            raise ValueError('deadline_check_requires_exact_date')
        report = call(read, '/readiness?market=TW&run_date=' + run_date)
        if report.get('requested_run_date') != run_date or report.get('effective_run_date') != run_date:
            raise ValueError('radar_readiness_date_mismatch')
        (out / 'readiness.json').write_text(diagnostic_json(report, token), encoding='utf-8')
        for lane in require_lanes:
            if lane == 'publication':
                p = report.get('publication', {})
                manifest = p.get('input_manifest') or {}
                ready = p.get('status') == 'completed' and p.get('output_complete') is True and report.get('scoring', {}).get('ready') is True and manifest.get('matches_current_inputs') is True
                # Official closed proof explicitly completes a no-publication intent.
                ready |= any(s.get('publication', {}).get('state') == 'closed' for s in summaries.values())
            else:
                ready = report.get('validation', {}).get(lane, {}).get('ready') is True
            failed |= not ready
    result = {'run_date': run_date, 'resume': resume, 'advanced_requests': advanced,
              'jobs': list(summaries.values()), 'errors': errors, 'readiness_deadline_passed': not failed if require_lanes else None,
              'status': 'failed' if failed else 'recorded_or_advanced'}
    (out / 'jobs.json').write_text(diagnostic_json(result, token), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--run-date')
    group.add_argument('--resume', action='store_true')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--max-work', type=int, default=100)
    parser.add_argument('--max-seconds', type=int, default=900)
    parser.add_argument('--require-lanes', default='')
    args = parser.parse_args()
    try:
        result = run(endpoint=os.environ['ZEABUR_BACKEND_URL'], token=os.environ['DAILY_RADAR_INTERNAL_TOKEN'],
            output_dir=args.output_dir, run_date=args.run_date, resume=args.resume, max_work=args.max_work,
            max_seconds=args.max_seconds, require_lanes=tuple(x for x in args.require_lanes.split(',') if x))
    except Exception as exc:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'driver-error.json').write_text(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        return 1
    print(json.dumps({'run_date': result['run_date'], 'status': result['status'],
                      'advanced_requests': result['advanced_requests']}, ensure_ascii=False))
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
