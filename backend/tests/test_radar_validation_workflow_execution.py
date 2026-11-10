import importlib.util
import json
from functools import partial
from pathlib import Path


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
    assert deadlines==[300,1,300,1]
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
                "retryable_skipped_count": 0,
                "report": {"remaining_retryable_window_count": 0}}
    assert runner().run(endpoint="test", token="private", run_date="2026-11-03",
        output_dir=tmp_path, request=post, sleep=lambda _: None, clock=lambda: now[0]) == 1
    assert calls == ["signal_close", "next_open"]
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert summaries[0]["stop_reason"] == "budget_exhausted"
    assert summaries[1]["status"] == "completed"


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
    assert deadlines == [300, 180, 300]
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert summaries[-2]["stop_reason"] == "budget_exhausted"
