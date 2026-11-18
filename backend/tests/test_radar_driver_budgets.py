import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from run_daily_radar_jobs import run


DAY = '2026-11-16'


@pytest.mark.parametrize('require_lanes', [(), ('publication',), ('signal_close', 'next_open')])
def test_work_budget_stops_before_partial_request_and_preserves_progress(tmp_path, require_lanes):
    now, advances, requests = [0], [], []
    identity = {'job_id': 2, 'requested_run_date': DAY, 'effective_run_date': DAY}

    def read(url, token, **kw):
        requests.append(url)
        if url.endswith('/capabilities'):
            return {'enabled': True, 'version': 'radar-durable-v1'}
        assert kw['timeout'] == 180
        now[0] += 136
        return identity | {'scoring': {'ready': True}, 'publication': {
            'status': 'completed', 'output_complete': True,
            'input_manifest': {'matches_current_inputs': True}},
            'validation': {lane: {'ready': True} for lane in ('signal_close', 'next_open')}}

    def write(url, token, payload, **kw):
        if url.endswith('/jobs'):
            if require_lanes:
                assert payload['lanes'] == list(require_lanes)
            return identity
        advances.append(kw['timeout'])
        assert kw['timeout'] == 180
        now[0] += 173
        return identity | {'advanced_count': 1, 'runnable_now': True}

    result = run(endpoint='https://example.test', token='secret', output_dir=tmp_path,
                 run_date=DAY, max_seconds=400, clock=lambda: now[0], read=read, write=write,
                 require_lanes=require_lanes)
    assert len(advances) == 2
    assert result['status'] == 'recorded_or_advanced'
    assert result['budget_exhausted'] is True
    assert result['advanced_requests'] == 2
    assert json.loads((tmp_path / 'jobs.json').read_text())['advanced_requests'] == 2
    if require_lanes:
        assert requests[-1].endswith('&scope=publication') == (require_lanes == ('publication',))
        assert result['readiness_deadline_passed'] is True


def test_deadline_budget_can_complete_more_than_one_thousand_windows(tmp_path):
    identity = {'job_id': 2, 'requested_run_date': DAY, 'effective_run_date': DAY}
    completed = [0]
    def read(url, token, **kwargs):
        if url.endswith('/capabilities'):
            return {'enabled': True, 'version': 'radar-durable-v1'}
        return identity | {'validation': {lane: {'ready': completed[0] == 1088}
            for lane in ('signal_close', 'next_open')}}
    def write(url, token, payload, **kwargs):
        if url.endswith('/jobs'):
            assert payload['lanes'] == ['signal_close', 'next_open']
            return identity
        completed[0] += 1
        return identity | {'advanced_count': int(completed[0] < 1088)}
    result = run(endpoint='https://example.test', token='secret', output_dir=tmp_path,
        run_date=DAY, max_work=2000, max_seconds=1200, require_lanes=('signal_close','next_open'),
        read=read, write=write)
    assert result['status'] == 'recorded_or_advanced'
    assert result['advanced_requests'] == 1088
    assert result['readiness_deadline_passed'] is True


def test_failed_audit_keeps_advance_evidence_and_sanitized_error(tmp_path):
    identity = {'job_id': 2, 'requested_run_date': DAY, 'effective_run_date': DAY}
    def read(url, token, **kw):
        if url.endswith('/capabilities'):
            return {'enabled': True, 'version': 'radar-durable-v1'}
        assert json.loads((tmp_path / 'jobs.json').read_text())['advanced_requests'] == 1
        raise TimeoutError('secret provider body')
    def write(url, *args, **kw):
        return identity | {'advanced_count': 0}
    result = run(endpoint='https://example.test', token='secret', output_dir=tmp_path,
                 run_date=DAY, require_lanes=('publication',), read=read, write=write)
    assert result['status'] == 'failed'
    assert result['advanced_requests'] == 1
    assert result['errors'][-1] == {'phase': 'readiness', 'error_type': 'TimeoutError'}
    assert 'provider body' not in (tmp_path / 'jobs.json').read_text()


def test_registration_is_checkpointed_when_no_work_request_fits(tmp_path):
    now = [0]
    def read(*args, **kwargs):
        now[0] += 20
        return {'enabled': True, 'version': 'radar-durable-v1'}
    def write(url, *args, **kwargs):
        assert url.endswith('/jobs')
        now[0] += 20
        return {'job_id': 7, 'requested_run_date': DAY, 'effective_run_date': DAY}
    result = run(endpoint='https://example.test', token='secret', output_dir=tmp_path,
                 run_date=DAY, max_seconds=60, clock=lambda: now[0], read=read, write=write)
    assert result['advanced_requests'] == 0
    assert result['stop_reason'] == 'time_budget'
    saved = json.loads((tmp_path / 'jobs.json').read_text())
    assert saved['job_refs'] == [{'job_id': 7, 'run_date': DAY}]


@pytest.mark.parametrize('schedule,step,lanes,limit', [
    ('*/30 * * * *', '', None, 150),
    ('30 9 * * 1-5', '', None, 150),
    ('15 18 * * 1-5', '', 'signal_close,next_open', 2000),
    ('30 16 * * 1-5', '', 'publication', 2000),
    ('0 23 * * 1-5', '', 'publication', 2000),
    ('', 'run-forward-validation', 'signal_close,next_open', 2000),
])
def test_actual_workflow_assigns_capacity_without_extending_time(tmp_path, schedule, step, lanes, limit):
    filename = 'daily-radar-validation.yml' if lanes == 'signal_close,next_open' else 'daily-radar.yml'
    workflow = (Path(__file__).parents[2]/'.github/workflows'/filename).read_text()
    section = workflow.split('      - name: Register intent or resume stored dates', 1)[1]
    script = textwrap.dedent(section.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
    stub = tmp_path/'python'
    stub.write_text(f'#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    stub.chmod(0o755)
    result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, check=True,
        env=dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ['PATH'], RUNNER_TEMP=str(tmp_path),
                 DAILY_RADAR_SCHEDULE=schedule, DAILY_RADAR_STEP=step, DAILY_RADAR_RUN_DATE=DAY))
    arguments = json.loads(result.stdout)
    assert arguments[0] == '.github/scripts/run_daily_radar_jobs.py'
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-work', type=int)
    parser.add_argument('--max-seconds', type=int)
    parser.add_argument('--require-lanes')
    parser.add_argument('--resume', action='store_true')
    args, _ = parser.parse_known_args(arguments[1:])
    assert args.max_work == limit
    assert args.max_seconds == 1200
    assert args.require_lanes == lanes
    assert args.resume == (schedule == '*/30 * * * *')


def test_radar_can_finish_copying_memberships_with_occupied_general_lease(tmp_path):
    calls=[]
    def read(url,*args,**kwargs):
        return {'enabled':True,'version':'radar-durable-v1','scoped_resume':True,'parallel_window_advance':True}
    def write(url,*args,**kwargs):
        if '/resume?' in url:
            return {'jobs':[{'job_id':7,'run_date':DAY}]}
        calls.append(url)
        return {'requested_run_date':DAY,'effective_run_date':DAY,'advanced_count':0,
                'handoff_advanced':True,'handoff_pending':len(calls)<3,'runnable_now':True,
                'advance_slot_busy':True}
    result=run(endpoint='https://local.invalid',token='',output_dir=tmp_path,owner='radar',
               resume=True,max_work=6,read=read,write=write)
    assert len(calls)==3 and result['errors']==[] and result['stop_reason']=='idle'
