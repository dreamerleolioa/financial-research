from threading import Event
from time import sleep

from run_daily_radar_jobs import run

DATES = {'7': '2026-11-15', '8': '2026-11-16', '9': '2026-11-17'}


def exercise(tmp_path, write, *, limit, job_ids=('7',)):
    def read(url, token, **kwargs):
        if url.endswith('/capabilities'):
            return {'enabled': True, 'version': 'radar-durable-v1', 'parallel_window_advance': True}
        return {'jobs': [{'job_id': key, 'run_date': DATES[key]} for key in job_ids]}
    return run(endpoint='https://radar.invalid', token='', output_dir=tmp_path, resume=True,
               max_work=limit, max_seconds=5, read=read, write=write)


def status(path, **fields):
    day = DATES[path.split('/')[-2]]
    return {'requested_run_date': day, 'effective_run_date': day, 'advanced_count': 0, **fields}


def test_initial_advances_get_budget_before_parallel_probes(tmp_path):
    paths = []
    def write(url, token, payload, **kwargs):
        paths.append(url)
        if '/7/' in url and '?' not in url:
            sleep(.4)
        return status(url)
    exercise(tmp_path, write, limit=3, job_ids=('7', '8', '9'))
    assert [path.split('/')[-2] for path in paths] == ['7', '8', '9']
    assert all('?' not in path for path in paths)


def test_late_response_does_not_hide_internal_failure(tmp_path):
    secondary = Event()
    def write(url, token, payload, **kwargs):
        if '?' in url:
            secondary.set()
            return status(url, observed_at='2026-11-18T09:04:15+00:00',
                          lanes={'signal_close': {'states': {'failed_internal': 1}}})
        assert secondary.wait(2)
        sleep(.1)
        return status(url, observed_at='2026-11-18T09:04:14+00:00',
                      lanes={'signal_close': {'states': {'running': 1}}})
    result = exercise(tmp_path, write, limit=2)
    assert result['status'] == 'failed'
    assert result['jobs'][0]['lanes']['signal_close']['states'] == {'failed_internal': 1}


def test_busy_lease_and_failed_probe_do_not_repeatedly_consume_budget(tmp_path):
    paths = []
    def write(url, token, payload, **kwargs):
        paths.append(url)
        if '?' in url:
            raise TimeoutError()
        sleep(.35)
        return status(url, runnable_now=True, advance_slot_busy=True)
    exercise(tmp_path, write, limit=6)
    assert len(paths) == 2


def test_newer_window_response_preserves_followup_after_old_normal_response(tmp_path):
    secondary = Event()
    general_count = 0
    def write(url, token, payload, **kwargs):
        nonlocal general_count
        if '?' in url:
            secondary.set()
            return status(url, runnable_now=True, observed_at='2026-11-18T09:04:15+00:00')
        general_count += 1
        if general_count == 1:
            assert secondary.wait(2)
            sleep(.1)
            return status(url, observed_at='2026-11-18T09:04:14+00:00')
        return status(url, observed_at='2026-11-18T09:04:16+00:00')
    result = exercise(tmp_path, write, limit=10)
    assert general_count == 2
    assert result['advanced_requests'] == 3


def test_failed_advance_spends_budget_and_is_journaled_before_post(tmp_path):
    import json
    def read(url, token, **kwargs):
        if url.endswith('/capabilities'):
            return {'enabled': True, 'version': 'radar-durable-v1'}
        return {'jobs': [{'job_id': key, 'run_date': DATES[key]} for key in DATES]}
    def write(url, token, payload, **kwargs):
        checkpoint = json.loads((tmp_path / 'jobs.json').read_text())
        assert checkpoint['advanced_requests'] == 1
        assert checkpoint['in_flight'][0]['request_number'] == 1
        raise TimeoutError()
    result = run(endpoint='https://radar.invalid', token='', output_dir=tmp_path, resume=True,
                 max_work=1, max_seconds=5, read=read, write=write)
    assert result['advanced_requests'] == 1
    assert result['status'] == 'failed'
    assert len(result['errors']) == 1 and result['errors'][0]['error_type'] == 'TimeoutError'


def test_same_checkpoint_resume_accounts_for_an_interrupted_post(tmp_path):
    import json
    (tmp_path / 'jobs.json').write_text(json.dumps({
        'status': 'in_progress', 'advanced_requests': 2, 'settled_windows': 7,
        'in_flight': [{'job_id': '7', 'slot': 'normal', 'request_number': 2}],
    }))
    paths = []
    def write(url, token, payload, **kwargs):
        paths.append(url)
        return status(url, settled_windows=3)
    result = exercise(tmp_path, write, limit=3, job_ids=('7', '8'))
    assert len(paths) == 1
    assert result['advanced_requests'] == 3
    assert result['settled_windows'] == 10
    assert result['interrupted_requests'][0]['request_number'] == 2


def test_interrupted_resume_does_not_erase_recorded_failure(tmp_path):
    import json
    (tmp_path / 'jobs.json').write_text(json.dumps({
        'status': 'in_progress', 'advanced_requests': 1,
        'errors': [{'job_id': '7', 'error_type': 'TimeoutError'}],
    }))
    def no_remaining_budget(*args, **kwargs):
        raise AssertionError('interrupted invocation has spent the request budget')
    result = exercise(tmp_path, no_remaining_budget, limit=1)
    assert result['advanced_requests'] == 1
    assert result['status'] == 'failed'
    assert result['errors'][0]['error_type'] == 'TimeoutError'


def test_delayed_busy_read_does_not_override_release_at_same_sqlite_timestamp(tmp_path):
    released = Event()
    attempts = []
    stamp = '2026-11-18T09:04:14.501000+00:00'
    def write(url, token, payload, **kwargs):
        if '?' in url:
            released.set()
            return status(url, normal_observed_at=stamp, normal_slot_busy=False, runnable_now=True)
        attempts.append(url)
        if len(attempts) == 1:
            assert released.wait(2)
            sleep(.1)
            return status(url, normal_observed_at=stamp, advance_slot_busy=True, runnable_now=True)
        return status(url, normal_observed_at='2026-11-18T09:04:14.502000+00:00', normal_slot_busy=False)
    exercise(tmp_path, write, limit=6)
    assert len(attempts) == 2


def test_empty_probe_during_handoff_can_commit_during_next_general_timeout(tmp_path):
    empty, committed = Event(), Event()
    general = 0
    def write(url, token, payload, **kwargs):
        nonlocal general
        if '?' in url:
            if general == 1:
                empty.set()
                return status(url, handoff_pending=True, runnable_now=True)
            committed.set()
            return status(url, advanced_count=1)
        general += 1
        if general == 1:
            assert empty.wait(1.5)
            sleep(.08)
            return status(url, handoff_advanced=True, runnable_now=True)
        assert committed.wait(1.5)
        return status(url)
    result = exercise(tmp_path, write, limit=4)
    assert committed.is_set() and result['errors'] == []


def test_busy_inherited_lease_allows_pages_and_then_independent_window(tmp_path):
    calls=[]
    def write(url,*args,**kwargs):
        if '?' in url:
            calls.append('window')
            return status(url,advanced_count=int(calls.count('window')==1),normal_slot_busy=True)
        calls.append('page')
        more = calls.count('page') < 3
        return status(url,handoff_advanced=True,handoff_pending=more,runnable_now=True,
                      normal_slot_busy=True,advance_slot_busy=True)
    exercise(tmp_path,write,limit=6)
    assert calls[:4]==['page','page','page','window']


def test_stale_empty_probe_after_final_page_cannot_pause_ready_windows(tmp_path):
    first_probe, second_normal, committed=Event(),Event(),Event()
    pages,probes=0,0
    def write(url,*args,**kwargs):
        nonlocal pages,probes
        if '?' in url:
            probes+=1
            if probes==1:
                first_probe.set()
                assert second_normal.wait(1.5)
                return status(url,blocked_by_handoff=True,handoff_pending=False,runnable_now=True)
            committed.set()
            return status(url,advanced_count=1)
        pages+=1
        if pages==1:
            assert first_probe.wait(1.5)
            return status(url,handoff_advanced=True,handoff_pending=False,runnable_now=True)
        second_normal.set()
        assert committed.wait(1.5)
        return status(url)
    result=exercise(tmp_path,write,limit=4)
    assert committed.is_set() and result['errors']==[]


def test_http_failure_records_status_without_transferring_response_stream(tmp_path):
    import pickle
    from urllib.error import HTTPError
    from radar_window_driver import _advance
    from time import monotonic
    def fail(*args, **kwargs):
        raise HTTPError('https://example.invalid', 503, 'private body', {}, open(__file__, 'rb'))
    def invoke(url, *args, **kwargs):
        return _advance(fail, url, 'token', 10, monotonic() + 10)
    result = exercise(tmp_path, invoke, limit=1)
    error, = result['errors']
    assert error['error_type'] == 'HTTPError' and error['http_status'] == 503
    assert error['slot'] == 'normal'
    assert 'private' not in str(error)
    try:
        invoke('https://example.invalid')
    except Exception as exc:
        assert type(pickle.loads(pickle.dumps(exc))) is type(exc)
