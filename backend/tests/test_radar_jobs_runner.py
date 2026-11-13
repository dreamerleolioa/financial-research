import pytest
from run_daily_radar_jobs import run


def test_resume_uses_stored_dates_and_stops_without_progress(tmp_path):
    def read(url,token,**kw):
        return {'enabled':True,'version':'radar-durable-v1'} if url.endswith('capabilities') else {'jobs':[{'job_id':7,'run_date':'2026-11-06'}]}
    def write(url,token,payload,**kw):
        assert url.endswith('/7/advance') and payload=={}
        return {'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','advanced_count':0,'data_ready':False}
    result=run(endpoint='https://example.test',token='secret',output_dir=tmp_path,resume=True,read=read,write=write)
    assert result['advanced_requests']==1 and result['status']=='recorded_or_advanced'
    assert (tmp_path/'jobs.json').is_file()


def test_accepted_intent_does_not_pass_deadline(tmp_path):
    def read(url,token,**kw):
        if url.endswith('capabilities'):return {'enabled':True,'version':'radar-durable-v1'}
        return {'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','publication':{},'validation':{}}
    def write(url,token,payload,**kw):
        return {'job_id':7,'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','advanced_count':0}
    result=run(endpoint='https://example.test',token='secret',output_dir=tmp_path,run_date='2026-11-06',require_lanes=['publication'],read=read,write=write)
    assert result['status']=='failed' and not result['readiness_deadline_passed']


def test_disabled_capability_and_wrong_date_fail_closed(tmp_path):
    with pytest.raises(ValueError,match='not_ready'):
        run(endpoint='https://example.test',token='',output_dir=tmp_path,resume=True,read=lambda *a,**k:{'enabled':False})
    with pytest.raises(ValueError,match='date_mismatch'):
        run(endpoint='https://example.test',token='',output_dir=tmp_path,run_date='2026-11-06',
            read=lambda *a,**k:{'enabled':True,'version':'radar-durable-v1'},write=lambda *a,**k:{'effective_run_date':'2026-11-07'})


def test_closed_publication_is_independent_of_unfinished_validation(tmp_path):
    def read(url,token,**kw):
        if url.endswith('capabilities'):return {'enabled':True,'version':'radar-durable-v1'}
        return {'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','publication':{},'validation':{}}
    def write(url,token,payload,**kw):
        return {'job_id':7,'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06','advanced_count':0,
                'state':'pending','publication':{'state':'closed'}}
    kwargs=dict(endpoint='https://example.test',token='secret',output_dir=tmp_path,run_date='2026-11-06',read=read,write=write)
    assert run(**kwargs,require_lanes=['publication'])['status']=='recorded_or_advanced'
    assert run(**kwargs,require_lanes=['publication','next_open'])['status']=='failed'


def test_audit_requeue_continues_even_without_a_completed_work_unit(tmp_path):
    advances=[]
    def read(url,token,**kw):return {'enabled':True,'version':'radar-durable-v1'}
    def write(url,token,payload,**kw):
        result={'job_id':7,'requested_run_date':'2026-11-06','effective_run_date':'2026-11-06'}
        if url.endswith('/jobs'):return result
        advances.append(url)
        return result|{'advanced_count':0,'runnable_now':len(advances)==1}
    result=run(endpoint='https://example.test',token='secret',output_dir=tmp_path,run_date='2026-11-06',read=read,write=write)
    assert len(advances)==2 and result['advanced_requests']==2


def test_registered_history_stays_in_readiness_without_repeating_alert(tmp_path):
    import json

    gap = {
        'candidate_id': 9, 'candidate_revision': 'candidate-revision',
        'calendar_revision': 'calendar-revision', 'return_basis': 'signal_close',
        'validation_version': 'daily-radar-forward-validation-v2', 'window_days': 5,
        'expected_target_date': '2026-06-10', 'state': 'missing_diagnostic',
        'reason': 'observation_diagnostic_incomplete', 'next_action': 'repair_diagnostic',
        'gap_revision': 'saved-diagnostic-revision',
    }
    baseline = tmp_path / 'known.json'
    baseline.write_text(json.dumps({'version': 'radar-known-validation-gaps-v1',
        'as_of_date': '2026-11-12', 'gaps': [gap]}))

    def scheduled(day, gaps, *, truncated=False):
        def read(url, token, **kw):
            if url.endswith('capabilities'):
                return {'enabled': True, 'version': 'radar-durable-v1'}
            return {'requested_run_date': day, 'effective_run_date': day, 'data_ready': False,
                    'validation': {'signal_close': {'ready': False, 'calendar_ready': True,
                        'unresolved_count': len(gaps), 'gaps_truncated': truncated, 'gaps': gaps}}}

        def write(url, token, payload, **kw):
            return {'job_id': 7, 'requested_run_date': day, 'effective_run_date': day, 'advanced_count': 0}

        return run(endpoint='https://example.test', token='secret', output_dir=tmp_path,
                   run_date=day, require_lanes=['signal_close'], read=read, write=write,
                   known_validation_gaps=baseline)

    for day in ('2026-11-12', '2026-11-13'):
        result = scheduled(day, [gap])
        assert result['status'] == 'recorded_or_advanced'
        assert result['readiness_deadline_passed'] is False
        assert result['validation_alerts']['signal_close'] == {'known': 1, 'new': 0}
    assert scheduled('2026-11-13', [gap, gap | {'window_days': 10}])['status'] == 'failed'
    assert scheduled('2026-11-13', [gap | {'calendar_revision': 'changed'}])['status'] == 'failed'
    assert scheduled('2026-11-13', [gap | {'gap_revision': 'different-evidence'}])['status'] == 'failed'
    assert scheduled('2026-11-13', [gap], truncated=True)['status'] == 'failed'
    assert scheduled('2026-11-11', [gap])['status'] == 'failed'


@pytest.mark.parametrize('schedule, step, expected_lanes, use_baseline', [
    ('50 15 * * 1-5', '', 'signal_close,next_open', True),
    ('0 23 * * 1-5', '', 'publication,signal_close,next_open', True),
    ('30 16 * * 1-5', '', 'publication', False),
    ('*/30 * * * *', '', None, False),
    ('', 'run-forward-validation', 'signal_close,next_open', True),
])
def test_durable_workflow_passes_reviewed_baseline_at_validation_deadlines(
        tmp_path, schedule, step, expected_lanes, use_baseline):
    import json
    import os
    from pathlib import Path
    import subprocess

    import yaml

    from run_daily_radar_jobs import validation_gap_key

    root = Path(__file__).parents[2]
    workflow = yaml.load((root / '.github/workflows/daily-radar.yml').read_text(), Loader=yaml.BaseLoader)
    commands = next(step['run'] for step in workflow['jobs']['durable-radar']['steps'] if 'run' in step)
    stub = tmp_path / 'python'
    stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    stub.chmod(0o700)
    env = os.environ | {
        'PATH': f'{tmp_path}:{os.environ["PATH"]}', 'RUNNER_TEMP': str(tmp_path),
        'DAILY_RADAR_SCHEDULE': schedule, 'DAILY_RADAR_STEP': step, 'DAILY_RADAR_RUN_DATE': '2026-11-12',
    }
    output = subprocess.run(['bash', '-c', commands], check=True, cwd=root, env=env,
                            text=True, capture_output=True).stdout.splitlines()
    assert output[0] == '.github/scripts/run_daily_radar_jobs.py'
    if expected_lanes:
        assert output[output.index('--require-lanes') + 1] == expected_lanes
    else:
        assert '--resume' in output and '--require-lanes' not in output
    assert ('--known-validation-gaps' in output) is use_baseline
    if use_baseline:
        baseline = json.loads((root / output[output.index('--known-validation-gaps') + 1]).read_text())
        assert baseline['version'] == 'radar-known-validation-gaps-v1'
        assert baseline['gaps'] and len({validation_gap_key(g) for g in baseline['gaps']}) == len(baseline['gaps'])
