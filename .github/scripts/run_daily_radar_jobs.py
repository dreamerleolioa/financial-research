"""Register immutable intent or resume stored jobs within a bounded foreground budget."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from daily_radar_http import get, post, diagnostic_json, request_deadline


# Actions runs this script directly from a checkout without installing the backend.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend' / 'src'))
from ai_stock_sentinel.radar_gap_contract import COMPARISON_VERSION, baseline_digest, digest, validation_gap_key


def readiness_error(exc):
    error = {'phase': 'readiness', 'error_type': type(exc).__name__}
    if isinstance(exc, HTTPError):
        error['http_status'] = exc.code
        exc.close()
    elif isinstance(exc, ValueError) and str(exc) in (
        'evidence_response_too_large', 'radar_readiness_date_mismatch',
        'invalid_validation_gap_comparison', 'incomplete_validation_gap_comparison',
    ):
        error['code'] = str(exc)
    return error


def comparison_alert(validation, baseline_applicable):
    counts = validation.get('comparison') or {}
    known, new, total = counts.get('known'), counts.get('new'), validation.get('unresolved_count')
    if (validation.get('calendar_ready') is not True or type(total) is not int
            or any(type(n) is not int or n < 0 for n in (known, new))
            or known + new != total or validation.get('ready') is not (total == 0)):
        raise ValueError('incomplete_validation_gap_comparison')
    if total and not baseline_applicable:
        return {'known': 0, 'new': None}
    return {'known': known, 'new': new}


def validation_alert(report, known, run_date, lane):
    if report.get('ready') is True and (known is None or report.get('unresolved_count') == 0):
        return {'known': 0, 'new': 0}
    gaps = report.get('gaps')
    if (known is None or run_date < known['as_of_date'] or report.get('calendar_ready') is not True
            or report.get('gaps_truncated') is not False or not isinstance(gaps, list)
            or not gaps or len(gaps) != report.get('unresolved_count')
            or any(gap['return_basis'] != lane for gap in gaps)):
        return {'known': 0, 'new': None}
    keys = {validation_gap_key(gap) for gap in gaps}
    if len(keys) != len(gaps):
        return {'known': 0, 'new': None}
    unchanged = keys & known['keys']
    return {'known': len(unchanged), 'new': len(keys - unchanged)}


def run(*, endpoint, token, output_dir, run_date=None, resume=False, max_work=100, max_seconds=900,
        require_lanes=(), owner=None, recheck_publication=False, read=get, write=post, clock=time.monotonic, known_validation_gaps=None):
    if (resume == (run_date is not None) or not 1 <= max_work <= 2000 or not 1 <= max_seconds <= 1800):
        raise ValueError('invalid_durable_job_scope_or_budget')
    if recheck_publication and (resume or "publication" not in require_lanes):
        raise ValueError('publication_recheck_requires_dated_publication_lane')
    if run_date is not None and date.fromisoformat(run_date).isoformat() != run_date:
        raise ValueError('invalid_run_date')
    if set(require_lanes) - {'publication', 'signal_close', 'next_open'}:
        raise ValueError('invalid_required_lanes')
    if resume and require_lanes:
        raise ValueError('deadline_check_requires_exact_date')
    owners = {'radar': ['publication', 'auxiliary'], 'validation': ['signal_close', 'next_open']}
    if owner is not None and (owner not in owners or set(require_lanes).difference(owners[owner])):
        raise ValueError('invalid_radar_job_owner')
    known = None
    if known_validation_gaps is not None:
        baseline = json.loads(Path(known_validation_gaps).read_text())
        if (baseline['version'] != 'radar-known-validation-gaps-v1'
                or date.fromisoformat(baseline['as_of_date']).isoformat() != baseline['as_of_date']):
            raise ValueError('invalid_radar_known_validation_gaps')
        known = {'as_of_date': baseline['as_of_date'], 'keys': {
            validation_gap_key(gap) for gap in baseline['gaps']
            if gap['next_action'] in {'needs_review', 'repair_diagnostic', 'waiting_provider'}
            and gap['state'] in {'missing_diagnostic', 'input_unverified', 'retryable'}
            and gap['expected_target_date'] is not None
            and gap['expected_target_date'] <= baseline['as_of_date']
        }}
    endpoint = endpoint.rstrip('/') + '/internal/daily-radar'
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    deadline = clock() + max_seconds
    work_request_seconds = min(180, max_seconds / 2)
    def call(fn, path, *args, until=None, request_limit=180):
        seconds = min(request_limit, (deadline if until is None else until) - clock())
        if seconds <= 0:
            raise TimeoutError('radar_driver_budget_exhausted')
        with request_deadline(seconds):
            return fn(endpoint + path, token, *args, timeout=seconds)
    capability = call(read, '/capabilities')
    if capability.get('enabled') is not True or capability.get('version') != 'radar-durable-v1':
        raise ValueError('radar_durable_execution_not_ready')
    if owner and capability.get('scoped_resume') is not True:
        raise ValueError('radar_scoped_resume_unavailable')
    if resume:
        jobs = (call(write, '/jobs/resume?owner=' + owner, {}) if owner else call(read, '/jobs/due'))['jobs']
    else:
        intent = {'run_date': run_date, 'market': 'TW'}
        if owner or require_lanes:
            # A publication deadline must not spend its budget collecting optional
            # evidence. Ordinary dated Radar intents continue to own auxiliary work.
            intent['lanes'] = list(require_lanes) if require_lanes else owners[owner]
        if recheck_publication:
            intent['recheck_publication'] = True
        registered = call(write, '/jobs' + ('?owner=' + owner if owner else ''), intent)
        if registered.get('effective_run_date') != run_date or registered.get('requested_run_date') != run_date:
            raise ValueError('radar_job_date_mismatch')
        jobs = [{'job_id': registered['job_id'], 'run_date': run_date}]
    advanced, summaries, errors = 0, {}, []
    in_flight = {}
    interrupted = []
    internal_failure_seen = False
    checkpoint_path = out / 'jobs.json'
    if resume and checkpoint_path.is_file():
        previous = json.loads(checkpoint_path.read_text(encoding='utf-8'))
        if previous.get('status') == 'in_progress' and previous.get('owner') == owner:
            # An interrupted invocation may already have delivered its POSTs.
            # Resume using the server queue and retain its already spent budget.
            advanced = previous.get('advanced_requests', 0)
            summaries = {job['job_id']: job for job in previous.get('jobs', []) if 'job_id' in job}
            errors = previous.get('errors', [])
            internal_failure_seen = previous.get('internal_failure_seen', False) or any(
                lane.get('states', {}).get('failed_internal', 0)
                for job in summaries.values() for lane in job.get('lanes', {}).values())
            interrupted = previous.get('interrupted_requests', []) + previous.get('in_flight', [])
    # Round-robin prevents an older provider outage from starving newer dates.
    pending = list(jobs)
    budget_exhausted = False
    def checkpoint(*, status='in_progress', deadline_passed=None, alerts=None):
        result = {'run_date': run_date, 'resume': resume, 'owner': owner, 'advanced_requests': advanced,
                  'job_refs': [{'job_id': job['job_id'], 'run_date': job['run_date']} for job in jobs],
                  'jobs': list(summaries.values()), 'errors': errors, 'in_flight': list(in_flight.values()),
                  'interrupted_requests': interrupted, 'internal_failure_seen': internal_failure_seen,
                  'validation_alerts': alerts or {}, 'budget_exhausted': budget_exhausted,
                  'stop_reason': ('time_budget' if budget_exhausted else 'work_limit'
                                  if pending and advanced >= max_work else 'request_error' if errors else 'idle'),
                  'readiness_deadline_passed': deadline_passed, 'status': status}
        temporary = out / 'jobs.json.tmp'
        temporary.write_text(diagnostic_json(result, token), encoding='utf-8')
        temporary.replace(checkpoint_path)
        return result
    def submitted(job, slot):
        nonlocal advanced
        advanced += 1
        in_flight[(job['job_id'], slot)] = {'job_id': job['job_id'], 'run_date': job['run_date'],
                                           'slot': slot, 'request_number': advanced}
        checkpoint()

    def record(job, slot, status, error):
        nonlocal internal_failure_seen
        in_flight.pop((job['job_id'], slot), None)
        if error is not None:
            errors.append({'job_id': job['job_id'], 'error_type': type(error).__name__})
        else:
            previous = summaries.get(job['job_id'])
            internal_failure_seen |= any(lane.get('states', {}).get('failed_internal', 0)
                                         for lane in status.get('lanes', {}).values())
            if previous is None or status.get('observed_at', '') >= previous.get('observed_at', ''):
                summaries[job['job_id']] = status
        checkpoint()

    checkpoint()
    if capability.get('parallel_window_advance') is True and owner != 'radar' and set(require_lanes) != {'publication'}:
        from radar_window_driver import advance_windows
        pending, budget_exhausted = advance_windows(pending, endpoint=endpoint, token=token, write=write,
            limit=max(0, max_work - advanced), deadline=deadline, seconds=work_request_seconds, clock=clock,
            record=record, submitted=submitted, owner=owner)
    else:
        while pending and advanced < max_work:
            if deadline - clock() < work_request_seconds:
                budget_exhausted = True
                break
            job = pending.pop(0)
            submitted(job, 'normal')
            try:
                path = f"/jobs/{job['job_id']}/advance"
                status = call(write, path + ('?owner=' + owner if owner else ''), {}, request_limit=work_request_seconds)
                if status.get('effective_run_date') != job['run_date'] or status.get('requested_run_date') != job['run_date']:
                    raise ValueError('radar_job_date_mismatch')
            except Exception as exc:
                record(job, 'normal', None, exc)
                continue
            record(job, 'normal', status, None)
            if (status.get('advanced_count') or (status.get('handoff_advanced') and status.get('handoff_pending'))
                    or (status.get('runnable_now') and not status.get('advance_slot_busy'))):
                pending.append(job)
    checkpoint()
    failed = bool(errors) or internal_failure_seen
    deadline_passed = not failed
    validation_alerts = {}
    if require_lanes:
        if resume:
            raise ValueError('deadline_check_requires_exact_date')
        url = '/readiness?market=TW&run_date=' + run_date
        if known is not None and set(require_lanes) - {'publication'}:
            url += '&include_all_validation_gaps=true'
        if set(require_lanes) == {'publication'}:
            url += '&scope=publication'
        if owner == 'validation':
            url += '&scope=validation'
        report = None
        try:
            # Final verification has its own budget, even after failed work.
            compact = (known is not None and capability.get('validation_gap_comparison') is True
                       and set(require_lanes) <= {'signal_close', 'next_open'})
            if compact:
                baseline_applicable = known['as_of_date'] <= run_date
                as_of = min(known['as_of_date'], run_date)
                hashes = sorted(digest(key) for key in known['keys']) if baseline_applicable else []
                payload = {'version': COMPARISON_VERSION, 'as_of_date': as_of, 'hashes': hashes}
                report = call(write, '/readiness/compare?run_date=' + run_date, payload, until=clock() + 180)
                if (report.get('version') != COMPARISON_VERSION or report.get('market') != 'TW'
                        or report.get('scope') != 'validation'
                        or report.get('baseline_digest') != baseline_digest(as_of, hashes)):
                    raise ValueError('invalid_validation_gap_comparison')
            else:
                report = call(read, url, until=clock() + 180)
            (out / 'readiness.json').write_text(diagnostic_json(report, token), encoding='utf-8')
            if report.get('requested_run_date') != run_date or report.get('effective_run_date') != run_date:
                raise ValueError('radar_readiness_date_mismatch')
            for lane in require_lanes:
                if lane == 'publication':
                    p = report.get('publication', {})
                    manifest = p.get('input_manifest') or {}
                    ready = p.get('status') == 'completed' and p.get('output_complete') is True and report.get('scoring', {}).get('ready') is True and manifest.get('matches_current_inputs') is True
                    ready |= any(s.get('publication', {}).get('state') == 'closed' for s in summaries.values())
                else:
                    validation = report.get('validation', {}).get(lane, {})
                    ready = validation.get('ready') is True
                    validation_alerts[lane] = (comparison_alert(validation, baseline_applicable) if compact
                                               else validation_alert(validation, known, run_date, lane))
                    failed |= validation_alerts[lane]['new'] != 0
                deadline_passed &= ready
                failed |= lane == 'publication' and not ready
        except Exception as exc:
            errors.append(readiness_error(exc))
            failed, deadline_passed = True, False
    failed |= any(lane.get('states', {}).get('failed_internal', 0)
                  for summary in summaries.values() for lane in summary.get('lanes', {}).values())
    result = checkpoint(status='failed' if failed else 'recorded_or_advanced',
                        deadline_passed=deadline_passed and not failed if require_lanes else None,
                        alerts=validation_alerts)
    return result


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--run-date')
    group.add_argument('--resume', action='store_true')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--max-work', type=int, default=100)
    parser.add_argument('--max-seconds', type=int, default=900)
    parser.add_argument('--owner', choices=('radar', 'validation'))
    parser.add_argument('--require-lanes', default='')
    parser.add_argument('--recheck-publication', action='store_true')
    parser.add_argument('--known-validation-gaps', type=Path)
    args = parser.parse_args()
    try:
        result = run(endpoint=os.environ['ZEABUR_BACKEND_URL'], token=os.environ['DAILY_RADAR_INTERNAL_TOKEN'],
            output_dir=args.output_dir, run_date=args.run_date, resume=args.resume, max_work=args.max_work,
            max_seconds=args.max_seconds, owner=args.owner, require_lanes=tuple(x for x in args.require_lanes.split(',') if x),
            known_validation_gaps=args.known_validation_gaps, recheck_publication=args.recheck_publication)
    except Exception as exc:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'driver-error.json').write_text(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        return 1
    print(json.dumps({'run_date': result['run_date'], 'status': result['status'],
                      'advanced_requests': result['advanced_requests'],
                      'stop_reason': result['stop_reason'],
                      'validation_alerts': result['validation_alerts'],
                      'readiness_deadline_passed': result['readiness_deadline_passed']}, ensure_ascii=False))
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
