import importlib.util
import json
from functools import partial
from pathlib import Path
import pytest


def complete_readiness(endpoint,token):
    from urllib.parse import parse_qs,urlsplit
    day=parse_qs(urlsplit(endpoint).query)['run_date'][0]
    return {'version':'radar-readiness-v1','market':'TW','requested_run_date':day,'effective_run_date':day,
        'publication':{},'scoring':{},'validation':{b:{'ready':True} for b in ('signal_close','next_open')}}


def runner():
    path = Path(__file__).parents[2] / ".github/scripts/run_daily_radar_validation.py"
    spec = importlib.util.spec_from_file_location("radar_validation_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.run=partial(module.run,readiness_request=complete_readiness)
    return module


def conflict(endpoint, body):
    from io import BytesIO
    from urllib.error import HTTPError
    return HTTPError(endpoint, 409, "Conflict", {}, BytesIO(body))


@pytest.mark.parametrize('latency,budget,expected', [(442, 900, 0), (901, 900, 1), (8, 10, 0)])
def test_default_validation_transport_uses_the_basis_budget(tmp_path, monkeypatch, latency, budget, expected):
    from contextlib import contextmanager
    from io import BytesIO
    import daily_radar_http

    module = runner()
    now = [0]
    requests = []

    @contextmanager
    def deadline(seconds):
        started = now[0]
        yield
        if now[0] - started > seconds:
            raise TimeoutError('wall clock limit exceeded')

    class Opener:
        def open(self, request, *, timeout):
            requests.append(timeout)
            now[0] += min(latency, timeout)
            if latency > timeout:
                raise TimeoutError('socket timeout')
            return BytesIO(json.dumps({'status': 'completed', 'as_of_date': '2026-11-11',
                'retryable_skipped_count': 0, 'report': {'remaining_retryable_window_count': 0}}).encode())

    monkeypatch.setattr(daily_radar_http, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(module, 'request_deadline', deadline)
    assert module.run(endpoint='https://test.invalid', token='fixture', run_date='2026-11-11',
        output_dir=tmp_path, return_bases=('signal_close',), basis_timeout=budget,
        clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds)) == expected
    assert requests == [budget]
    assert now[0] <= budget


@pytest.mark.parametrize("basis", ["signal_close", "next_open"])
@pytest.mark.parametrize("code", ["validation_inputs_changed", "validation_results_changed"])
def test_conflict_recollects_the_same_full_window_request(tmp_path, basis, code):
    calls = []

    def post(endpoint, token, payload):
        calls.append(dict(payload))
        if len(calls) == 1:
            raise conflict(endpoint, json.dumps({"detail": {"code": code, "body": token}}).encode())
        return {"status": "completed", "as_of_date": payload["as_of_date"], "retryable_skipped_count": 0,
                "report": {"remaining_retryable_window_count": 0}}

    assert runner().run(endpoint="https://test.invalid", token="private-fixture", run_date="2026-11-06",
        output_dir=tmp_path, request=post, sleep=lambda _: None, return_bases=(basis,)) == 0
    assert calls == [{"mode": "due", "market": "TW", "as_of_date": "2026-11-06", "benchmark_symbol": "TAIEX",
                      "windows": [5, 10, 20, 40, 60], "return_basis": basis}] * 2
    first = (tmp_path / f"{basis}-attempt-1.json").read_text()
    assert json.loads(first)["validation_error"] == {"error_type": "HTTPError", "http_status": 409, "code": code}
    assert "private-fixture" not in first


@pytest.mark.parametrize("body,reason,code", [
    (b'{"detail":{"code":"newer_validation_result_preserved"}}', "needs_review", "newer_validation_result_preserved"),
    (b'{"detail":{"code":"unknown_private"}}', "non_retryable_response", None),
    (b'{"detail":{"code":[]}}', "non_retryable_response", None),
    (b'private-invalid-json', "non_retryable_response", None),
    (b'{"detail":{"code":"validation_results_changed"}}' + b' ' * 4096, "non_retryable_response", None),
])
def test_only_known_validation_conflicts_are_actionable(tmp_path, body, reason, code):
    def post(endpoint, *args):
        raise conflict(endpoint, body)

    assert runner().run(endpoint="https://test.invalid", token="private-fixture", run_date="2026-11-06",
        output_dir=tmp_path, request=post, sleep=lambda _: None, return_bases=("next_open",)) == 1
    artifacts = list(tmp_path.glob("*.json"))
    assert len(artifacts) == 1
    result = json.loads(artifacts[0].read_text())
    assert result["validation_error"] == ({"error_type": "HTTPError", "http_status": 409}
                                          | ({"code": code} if code else {}))
    assert result["runner"]["stop_reason"] == reason


def test_conflict_body_is_read_inside_budget_and_closed(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from io import BytesIO
    from urllib.error import HTTPError

    module = runner()
    now, active = 0, False
    bodies = []

    @contextmanager
    def deadline(seconds):
        nonlocal active
        assert 0 < seconds <= 10
        active = True
        try:
            yield
        finally:
            active = False

    class Body(BytesIO):
        def read(self, size):
            nonlocal now
            assert active and 0 < size <= 4097
            now += 4
            return super().read(size)

    def post(endpoint, *args):
        body = Body(b'{"detail":{"code":"validation_results_changed"}}')
        bodies.append(body)
        raise HTTPError(endpoint, 409, "Conflict", {}, body)

    def sleep(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr(module, "request_deadline", deadline)
    assert module.run(endpoint="https://test.invalid", token="fixture", run_date="2026-11-06",
        output_dir=tmp_path, request=post, clock=lambda: now, sleep=sleep, basis_timeout=10,
        return_bases=("next_open",)) == 1
    assert len(bodies) == 2 and all(body.closed for body in bodies)
    result = json.loads((tmp_path / "next_open-attempt-2.json").read_text())
    assert result["runner"]["stop_reason"] == "budget_exhausted"


@pytest.mark.parametrize("failure", ["truncated", "timeout", "reset"])
def test_unreadable_conflict_body_keeps_http_status_and_does_not_retry(tmp_path, failure):
    from http.client import IncompleteRead
    from io import BytesIO
    from urllib.error import HTTPError

    bodies = []

    class Body(BytesIO):
        def read(self, size):
            if failure == "truncated":
                raise IncompleteRead(b'private-partial-body')
            if failure == "timeout":
                raise TimeoutError('private-timeout')
            raise ConnectionResetError('private-reset')

    def post(endpoint, *args):
        body = Body()
        bodies.append(body)
        raise HTTPError(endpoint, 409, "Conflict", {}, body)

    assert runner().run(endpoint="https://test.invalid", token="private", run_date="2026-11-06",
        output_dir=tmp_path, request=post, sleep=lambda _: None, return_bases=("next_open",)) == 1
    assert len(bodies) == 1 and bodies[0].closed
    artifact = (tmp_path / "next_open-attempt-1.json").read_text()
    result = json.loads(artifact)
    assert result['validation_error'] == {'error_type': 'HTTPError', 'http_status': 409}
    assert result['runner']['stop_reason'] == 'non_retryable_response'
    assert 'private' not in artifact


@pytest.mark.parametrize("basis,limit", [("signal_close", 2), ("next_open", 6)])
def test_persistent_conflict_stops_at_existing_attempt_limit(tmp_path, basis, limit):
    def post(endpoint, *args):
        raise conflict(endpoint, b'{"detail":{"code":"validation_results_changed"}}')

    assert runner().run(endpoint="https://test.invalid", token="fixture", run_date="2026-11-06",
        output_dir=tmp_path, request=post, sleep=lambda _: None, return_bases=(basis,)) == 1
    assert len(list(tmp_path.glob("*.json"))) == limit
    last = json.loads((tmp_path / f"{basis}-attempt-{limit}.json").read_text())
    assert last["runner"]["stop_reason"] == "attempt_limit"


def test_recollection_resets_the_previous_remaining_baseline(tmp_path):
    calls = []

    def post(endpoint, token, payload):
        calls.append(dict(payload))
        if len(calls) == 2:
            raise conflict(endpoint, b'{"detail":{"code":"validation_inputs_changed"}}')
        remaining = 2 if len(calls) < 4 else 0
        return {"status": "completed", "as_of_date": payload["as_of_date"], "retryable_skipped_count": remaining,
                "report": {"remaining_retryable_window_count": remaining}}

    assert runner().run(endpoint="https://test.invalid", token="fixture", run_date="2026-11-06",
        output_dir=tmp_path, request=post, sleep=lambda _: None, return_bases=("next_open",)) == 0
    assert len(calls) == 4 and all(payload == calls[0] for payload in calls)


def test_unverified_legacy_returns_require_review_even_with_zero_retryable_skips(tmp_path,capsys):
    calls=[]
    def post(endpoint,token,payload):
        calls.append(payload['return_basis'])
        return {'status':'completed','as_of_date':'2026-11-06','retryable_skipped_count':0,
            'report':{'remaining_retryable_window_count':0,
                'skip_reasons':{'validation_input_unverified':int(payload['return_basis']=='next_open')}}}
    assert runner().run(endpoint='test',token='private',run_date='2026-11-06',output_dir=tmp_path,
        request=post,sleep=lambda _:None)==1
    assert calls==['signal_close','next_open']
    assert json.loads((tmp_path/'next_open-attempt-1.json').read_text())['runner']['stop_reason']=='needs_review'


def test_runner_checks_shared_full_readiness_before_declaring_completion(tmp_path):
    def post(endpoint,token,payload):
        return {'status':'completed','as_of_date':'2026-11-06','retryable_skipped_count':0,
            'report':{'remaining_retryable_window_count':0}}
    calls=[]
    def readiness(endpoint,token):
        calls.append(endpoint)
        response=complete_readiness(endpoint,token)
        response['validation']['next_open']={'ready':False,'reasons':{'validation_input_unverified':1813},
            'gaps':[{'next_action':'needs_review'}]}
        return response
    assert runner().run(endpoint='https://backend.invalid/internal/daily-radar/forward-validation/run',
        token='private',run_date='2026-11-06',output_dir=tmp_path,request=post,readiness_request=readiness)==1
    assert len(calls)==2 and all('run_date=2026-11-06' in c for c in calls)
    artifact=json.loads((tmp_path/'next_open-attempt-1.json').read_text())
    assert artifact['runner']['stop_reason']=='needs_review'
    assert artifact['readiness']['validation']['next_open']['ready'] is False


def test_failed_readiness_request_never_inherits_tentative_completion(tmp_path):
    calls=[]
    def post(endpoint,token,payload):
        calls.append(payload['return_basis'])
        return {'status':'completed','as_of_date':'2026-11-06','retryable_skipped_count':0,
            'records_written':3,'report':{'remaining_retryable_window_count':0,
                'source_recovery':[{'stage':'official_prices','recovered_dates':['2026-11-03']}]}}
    def unavailable(endpoint,token): raise TimeoutError()
    assert runner().run(endpoint='test',token='private',run_date='2026-11-06',output_dir=tmp_path,
        request=post,readiness_request=unavailable,sleep=lambda _:None)==1
    assert calls==['signal_close']*2+['next_open']*2
    for path in tmp_path.glob('*.json'):
        artifact=json.loads(path.read_text())
        assert artifact['runner']['stop_reason']!='completed'
        assert artifact['records_written']==3
        assert artifact['report']['source_recovery'][0]['recovered_dates']==['2026-11-03']
        assert artifact['readiness_error']['error_type']=='TimeoutError'


def test_wrong_date_readiness_fails_both_bases_without_retries(tmp_path):
    def post(endpoint,token,payload):
        return {'status':'completed','as_of_date':'2026-11-06','retryable_skipped_count':0,
            'report':{'remaining_retryable_window_count':0}}
    def wrong(endpoint,token):
        return complete_readiness(endpoint,token) | {'effective_run_date':'2026-11-05'}
    assert runner().run(endpoint='test',token='private',run_date='2026-11-06',output_dir=tmp_path,
        request=post,readiness_request=wrong)==1
    assert len(list(tmp_path.glob('*.json')))==2


def test_readiness_request_uses_remaining_basis_budget(tmp_path,monkeypatch):
    from contextlib import contextmanager
    module=runner();now=[0];deadlines=[]
    @contextmanager
    def deadline(seconds):
        deadlines.append(seconds);yield
    monkeypatch.setattr(module,'request_deadline',deadline)
    def post(endpoint,token,payload):
        now[0]+=899
        return {'status':'completed','as_of_date':'2026-11-06','retryable_skipped_count':0,
            'report':{'remaining_retryable_window_count':0}}
    def late(endpoint,token):
        now[0]+=2
        return complete_readiness(endpoint,token)
    assert module.run(endpoint='test',token='private',run_date='2026-11-06',output_dir=tmp_path,
        request=post,readiness_request=late,clock=lambda:now[0],sleep=lambda _:None)==1
    assert deadlines==[900,1,900,1]
    assert all(json.loads(p.read_text())['runner']['stop_reason']=='budget_exhausted' for p in tmp_path.glob('*.json'))


def test_one_basis_can_run_without_executing_the_other(tmp_path):
    calls = []
    def post(endpoint, token, payload):
        calls.append(payload["return_basis"])
        return {"status": "completed", "as_of_date": "2026-11-06",
            "retryable_skipped_count": 0, "report": {"remaining_retryable_window_count": 0}}
    assert runner().run(endpoint="test", token="private", run_date="2026-11-06",
        output_dir=tmp_path, request=post, return_bases=("next_open",)) == 0
    assert calls == ["next_open"]


def test_first_basis_failure_does_not_prevent_second_basis_and_dates_never_change(tmp_path):
    calls = []
    def post(endpoint, token, payload):
        calls.append(dict(payload))
        return {"status": "completed", "as_of_date": "2026-10-14",
                "retryable_skipped_count": int(payload["return_basis"] == "signal_close"),
                "report": {"remaining_retryable_window_count": 0}}
    status = runner().run(endpoint="test", token="private", run_date="2026-10-14",
                          output_dir=tmp_path, request=post, sleep=lambda _: None)
    assert status == 1
    assert [c["return_basis"] for c in calls] == ["signal_close", "signal_close", "next_open"]
    assert {c["as_of_date"] for c in calls} == {"2026-10-14"}
    assert len(list(tmp_path.glob("*.json"))) == 3
    assert all("private" not in p.read_text() for p in tmp_path.glob("*.json"))


def test_transient_gap_recovers_but_terminal_count_never_blocks(tmp_path):
    calls = []
    def post(endpoint, token, payload):
        calls.append(payload["return_basis"])
        return {"status": "completed", "as_of_date": "2026-10-14",
                "retryable_skipped_count": int(len(calls) == 1), "terminal_skipped_count": 12,
                "report": {"remaining_retryable_window_count": 0}}
    assert runner().run(endpoint="test", token="private", run_date="2026-10-14",
                        output_dir=tmp_path, request=post, sleep=lambda _: None) == 0
    assert len(calls) == 3


def test_bad_payload_and_auth_failure_still_run_the_other_basis(tmp_path):
    from urllib.error import HTTPError
    calls = []
    def post(endpoint, token, payload):
        calls.append(payload["return_basis"])
        if payload["return_basis"] == "signal_close":
            raise HTTPError(endpoint, 403, "denied", None, None)
        return []
    assert runner().run(endpoint="test", token="private", run_date="2026-10-14",
                        output_dir=tmp_path, request=post, sleep=lambda _: None) == 1
    assert calls == ["signal_close", "next_open"]


def test_redirect_cannot_forward_internal_authorization():
    from urllib.request import Request
    from urllib.error import HTTPError
    import pytest
    with pytest.raises(HTTPError):
        runner()._NoRedirect().redirect_request(Request("https://backend.invalid", headers={"Authorization": "private"}),
            None, 302, "redirect", {}, "https://other.invalid")


def test_zero_current_skips_do_not_hide_an_unprocessed_resume_backlog(tmp_path):
    calls = []
    def post(endpoint, token, payload):
        calls.append(payload["return_basis"])
        return {"status": "completed", "as_of_date": "2026-10-14", "retryable_skipped_count": 0,
                "report": {"remaining_retryable_window_count": int(payload["return_basis"] == "next_open")}}
    assert runner().run(endpoint="test", token="private", run_date="2026-10-14",
        output_dir=tmp_path, request=post, sleep=lambda _: None) == 1
    assert calls == ["signal_close", "next_open", "next_open"]


def test_legacy_response_does_not_print_missing_diagnostics_as_an_empty_gap_list(tmp_path, capsys):
    import json
    def post(endpoint, token, payload):
        return {"status": "completed", "as_of_date": "2026-10-21", "retryable_skipped_count": 0,
                "report": {"remaining_retryable_window_count": 0}}
    assert runner().run(endpoint="test", token="private", run_date="2026-10-21",
        output_dir=tmp_path, request=post, sleep=lambda _: None) == 0
    summary = json.loads(capsys.readouterr().out.splitlines()[0])
    assert summary["current_run_gaps"] is None
    assert summary["current_run_skip_reasons"] is None
    assert summary["current_run_diagnostics_available"] is False


def test_next_open_continues_persisted_progress_beyond_two_requests(tmp_path, capsys):
    remaining = iter([222, 123, 30, 0])
    calls = []
    def post(endpoint, token, payload):
        calls.append(dict(payload))
        count = next(remaining) if payload["return_basis"] == "next_open" else 0
        return {"status": "completed", "as_of_date": "2026-11-03",
                "retryable_skipped_count": count,
                "report": {"remaining_retryable_window_count": count}}
    assert runner().run(endpoint="test", token="private", run_date="2026-11-03",
        output_dir=tmp_path, request=post, sleep=lambda _: None) == 0
    assert [c["return_basis"] for c in calls] == ["signal_close"] + ["next_open"] * 4
    assert {c["as_of_date"] for c in calls} == {"2026-11-03"}
    assert len(list(tmp_path.glob("next_open-attempt-*.json"))) == 4


def test_next_open_stops_when_remaining_backlog_no_longer_decreases(tmp_path, capsys):
    calls = []
    def post(endpoint, token, payload):
        calls.append(payload["return_basis"])
        count = 123 if payload["return_basis"] == "next_open" else 0
        return {"status": "completed", "as_of_date": "2026-11-03",
                "retryable_skipped_count": count,
                "report": {"remaining_retryable_window_count": count}}
    assert runner().run(endpoint="test", token="private", run_date="2026-11-03",
        output_dir=tmp_path, request=post, sleep=lambda _: None) == 1
    assert calls == ["signal_close", "next_open", "next_open"]
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert summaries[-2]["stop_reason"] == "no_progress"
    artifact = json.loads((tmp_path / "next_open-attempt-2.json").read_text())
    assert artifact["runner"]["stop_reason"] == "no_progress"


def test_next_open_progress_cannot_exceed_six_requests(tmp_path, capsys):
    calls = []
    def post(endpoint, token, payload):
        calls.append(payload["return_basis"])
        count = 100 - len(calls) if payload["return_basis"] == "next_open" else 0
        return {"status": "completed", "as_of_date": "2026-11-03",
                "retryable_skipped_count": count,
                "report": {"remaining_retryable_window_count": count}}
    assert runner().run(endpoint="test", token="private", run_date="2026-11-03",
        output_dir=tmp_path, request=post, sleep=lambda _: None) == 1
    assert calls.count("next_open") == 6
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert summaries[-2]["stop_reason"] == "attempt_limit"


def test_complete_http_request_is_bounded_and_other_basis_keeps_its_budget(tmp_path, capsys):
    now = [0]
    calls = []
    def post(endpoint, token, payload):
        calls.append(payload["return_basis"])
        if payload["return_basis"] == "signal_close":
            now[0] += 901
        return {"status": "completed", "as_of_date": "2026-11-03",
                "retryable_skipped_count": 0, 'records_written':3,
                "report": {"remaining_retryable_window_count": 0,
                    'source_recovery':[{'stage':'official_prices','recovered_dates':['2026-11-02']}]}}
    assert runner().run(endpoint="test", token="private", run_date="2026-11-03",
        output_dir=tmp_path, request=post, sleep=lambda _: None, clock=lambda: now[0]) == 1
    assert calls == ["signal_close", "next_open"]
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert summaries[0]["stop_reason"] == "budget_exhausted"
    assert summaries[1]["status"] == "completed"
    artifact=json.loads((tmp_path/'signal_close-attempt-1.json').read_text())
    assert artifact['records_written']==3
    assert artifact['report']['source_recovery'][0]['recovered_dates']==['2026-11-02']
    assert artifact['validation_response_received'] is True
    assert artifact['validation_error']=={'error_type':'TimeoutError'}
    assert artifact['readiness_error'] is None


def test_post_deadline_exit_retains_response_without_carrying_it_into_the_next_attempt(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from urllib.error import HTTPError
    module=runner();entered=[0];calls=[]
    @contextmanager
    def deadline(seconds):
        entered[0]+=1;current=entered[0]
        yield
        if current==1:raise TimeoutError('private')
    monkeypatch.setattr(module,'request_deadline',deadline)
    def post(endpoint,token,payload):
        calls.append(payload['return_basis'])
        if calls==['signal_close','signal_close']:raise HTTPError(endpoint,403,'private',None,None)
        return {'status':'completed','as_of_date':'2026-11-03','retryable_skipped_count':0,
            'records_written':3,'echo':'private','report':{'remaining_retryable_window_count':0}}
    assert module.run(endpoint='test',token='private',run_date='2026-11-03',output_dir=tmp_path,
        request=post,sleep=lambda _:None)==1
    assert calls==['signal_close','signal_close','next_open']
    first=json.loads((tmp_path/'signal_close-attempt-1.json').read_text())
    second=json.loads((tmp_path/'signal_close-attempt-2.json').read_text())
    assert first['records_written']==3 and first['validation_response_received'] is True
    assert first['validation_error']=={'error_type':'TimeoutError'}
    assert second['validation_response_received'] is False and second['validation_response'] is None
    assert second['validation_error']=={'error_type':'HTTPError','http_status':403}
    assert json.loads((tmp_path/'next_open-attempt-1.json').read_text())['runner']['stop_reason']=='completed'
    assert all('private' not in path.read_text() for path in tmp_path.glob('*.json'))


@pytest.mark.parametrize('body,expected',[(None,None),(['private'],['[redacted]']),('private','[redacted]')])
def test_received_invalid_post_body_is_retained_as_evidence_without_completion(tmp_path,body,expected):
    def post(endpoint,token,payload):return body
    assert runner().run(endpoint='test',token='private',run_date='2026-11-03',output_dir=tmp_path,
        request=post,sleep=lambda _:None)==1
    artifacts=[json.loads(path.read_text()) for path in tmp_path.glob('*.json')]
    assert len(artifacts)==2
    for artifact in artifacts:
        assert artifact['validation_response_received'] is True
        assert artifact['validation_response']==expected
        assert artifact['validation_error']=={'error_type':'ValueError'}
        assert artifact['runner']['stop_reason']=='non_retryable_response'


def test_recovery_errors_are_visible_and_secrets_remain_redacted(tmp_path, capsys):
    error = {"symbol": "1727.TW", "stage": "official_prices",
             "reason": "research_official_price_conflict", "extra": "private"}
    def post(endpoint, token, payload):
        return {"status": "completed", "as_of_date": "2026-11-03",
                "retryable_skipped_count": 1,
                "report": {"remaining_retryable_window_count": 1,
                           "source_recovery_summary": {"error_count": 1},
                           "source_recovery": [error]}}
    assert runner().run(endpoint="test", token="private", run_date="2026-11-03",
        output_dir=tmp_path, request=post, sleep=lambda _: None) == 1
    output = capsys.readouterr().out
    assert "private" not in output
    summary = json.loads(output.splitlines()[0])
    assert summary["source_recovery_summary"]["error_count"] == 1
    assert summary["source_recovery_errors"][0]["reason"] == "research_official_price_conflict"


def test_next_open_transport_failure_is_limited_without_data_progress(tmp_path, capsys):
    calls = []
    def post(endpoint, token, payload):
        calls.append(payload["return_basis"])
        if payload["return_basis"] == "next_open":
            raise TimeoutError()
        return {"status": "completed", "as_of_date": "2026-11-03", "retryable_skipped_count": 0}
    assert runner().run(endpoint="test", token="private", run_date="2026-11-03",
        output_dir=tmp_path, request=post, sleep=lambda _: None) == 1
    assert calls == ["signal_close", "next_open", "next_open"]
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert summaries[-2]["stop_reason"] == "transport_retry_limit"


def test_recovery_budget_limits_the_remaining_request_and_wait(tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager
    now = [0]
    deadlines = []
    sleeps = []
    module = runner()
    @contextmanager
    def deadline(seconds):
        deadlines.append(seconds)
        yield
    monkeypatch.setattr(module, "request_deadline", deadline)
    def post(endpoint, token, payload):
        if payload["return_basis"] == "next_open":
            now[0] += 898
        return {"status": "completed", "as_of_date": "2026-11-03",
                "retryable_skipped_count": int(payload["return_basis"] == "next_open"),
                "report": {"remaining_retryable_window_count": 1}}
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    assert module.run(endpoint="test", token="private", run_date="2026-11-03",
        output_dir=tmp_path, request=post, sleep=sleep, clock=lambda: now[0]) == 1
    assert sleeps == [2]
    assert deadlines == [900, 180, 900]
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert summaries[-2]["stop_reason"] == "budget_exhausted"
