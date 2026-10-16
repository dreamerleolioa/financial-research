import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def runner():
    path = Path(__file__).parents[2] / ".github/scripts/prepare_daily_radar_evidence.py"
    spec = importlib.util.spec_from_file_location("radar_evidence_preparation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence(day="2026-10-15", missing=None):
    lanes = {lane: [] for lane in ("technical", "institutional", "margin", "fundamental")}
    lanes["fundamental"] = missing or []
    return {"status": "completed", "run_date": day, "step": "refresh-ai-evidence",
        "missing_by_lane": lanes, "evidence_ready": not missing, "errors": []}


def _manual_refresh(tmp_path, body, *, http_status=200, raw_body=None):
    """Execute the workflow's real shell with curl replaced before any network I/O."""
    workflow = (Path(__file__).parents[2] / '.github/workflows/daily-radar.yml').read_text()
    job = workflow.split('\n  refresh-ai-evidence:\n', 1)[1].split('\n  refresh-market-context:', 1)[0]
    script = textwrap.dedent(job.split('        run: |\n', 1)[1])
    (tmp_path / 'response.json').write_text(json.dumps(body) if raw_body is None else raw_body)
    fake_curl = tmp_path / 'curl'
    fake_curl.write_text(f'#!{sys.executable}\n' + textwrap.dedent('''
        import json, os, pathlib, sys
        root = pathlib.Path(os.environ['STUB_ROOT'])
        args = sys.argv[1:]
        response_file = pathlib.Path(args[args.index('-o') + 1])
        response_file.write_text((root / 'response.json').read_text())
        (root / 'request.json').write_text(json.dumps({
            'payload': json.loads(args[args.index('--data') + 1]),
            'endpoint': args[args.index('-X') + 2], 'response_file': str(response_file),
        }))
        print(os.environ['STUB_HTTP_STATUS'], end='')
    '''))
    fake_curl.chmod(0o755)
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script],
        env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}', 'TMPDIR': str(tmp_path),
            'STUB_ROOT': str(tmp_path), 'STUB_HTTP_STATUS': str(http_status),
            'ZEABUR_BACKEND_URL': 'https://example.invalid',
            'DAILY_RADAR_INTERNAL_TOKEN': 'test-only',
            'DAILY_RADAR_ENDPOINT': '/internal/daily-radar/refresh-ai-evidence',
            'DAILY_RADAR_RUN_DATE': '2026-10-15'},
        capture_output=True, text=True, timeout=30)
    request = json.loads((tmp_path / 'request.json').read_text())
    return result, request


def _manual_readiness_cases():
    yield pytest.param(evidence(), True, id='complete')
    value = evidence()
    value['missing_by_lane']['optional'] = []
    yield pytest.param(value, True, id='extra-empty-lane')
    for lane in ('technical', 'institutional', 'margin', 'fundamental'):
        value = evidence()
        value['evidence_ready'] = False
        value['missing_by_lane'][lane] = ['2355.TW']
        yield pytest.param(value, False, id=f'{lane}-gap')
        value = evidence()
        del value['missing_by_lane'][lane]
        yield pytest.param(value, False, id=f'{lane}-absent')
        for invalid in (None, {}, 'invalid', 0):
            value = evidence()
            value['missing_by_lane'][lane] = invalid
            yield pytest.param(value, False, id=f'{lane}-type-{type(invalid).__name__}')
    for field, invalid in (
        ('evidence_ready', False), ('evidence_ready', None), ('evidence_ready', 'true'), ('evidence_ready', 1),
        ('errors', [{'code': 'provider_failed'}]), ('errors', None), ('errors', {}),
        ('run_date', '2026-10-16'), ('step', 'refresh-ohlcv'), ('status', 'failed'),
        ('missing_by_lane', None), ('missing_by_lane', []),
    ):
        value = evidence()
        value[field] = invalid
        yield pytest.param(value, False, id=f'{field}-{invalid}')
    for field in ('evidence_ready', 'errors', 'run_date', 'step', 'status', 'missing_by_lane'):
        value = evidence()
        del value[field]
        yield pytest.param(value, False, id=f'{field}-absent')
    for invalid in (['2355.TW'], None, 'invalid'):
        value = evidence()
        value['missing_by_lane']['extra'] = invalid
        yield pytest.param(value, False, id=f'extra-lane-{invalid}')
    value = evidence()
    value['missing_by_lane']['margin'] = ['2355.TW']
    yield pytest.param(value, False, id='ready-true-with-gap')
    for invalid in (None, [], 'completed', 1):
        yield pytest.param(invalid, False, id=f'body-{invalid}')


@pytest.mark.parametrize('body,ready', list(_manual_readiness_cases()))
def test_manual_refresh_gate_matches_finalize(tmp_path, body, ready):
    result, request = _manual_refresh(tmp_path, body)
    assert runner().evidence_is_ready(body, '2026-10-15') is ready
    assert (result.returncode == 0) is ready, result.stdout + result.stderr
    assert request['payload'] == {'market': 'TW', 'run_date': '2026-10-15'}
    assert request['endpoint'] == 'https://example.invalid/internal/daily-radar/refresh-ai-evidence'


def test_manual_refresh_keeps_gap_diagnostics_when_rejecting_completed_materialization(tmp_path):
    body = evidence(missing=['2355.TW'])
    body['missing_evidence_details'] = {'fundamental': [{'symbol': '2355.TW', 'reason': 'no_eps_history'}]}
    result, request = _manual_refresh(tmp_path, body)
    assert result.returncode != 0
    assert 'evidence_ready: false' in result.stdout
    assert '2355.TW' in result.stdout and 'no_eps_history' in result.stdout
    assert json.loads(Path(request['response_file']).read_text()) == body


@pytest.mark.parametrize('http_status', [403, 500])
def test_manual_refresh_http_failure_cannot_pass_even_with_ready_body(tmp_path, http_status):
    result, _ = _manual_refresh(tmp_path, evidence(), http_status=http_status)
    assert result.returncode != 0


@pytest.mark.parametrize('raw_body', ['', '{bad-json', json.dumps(evidence()) + '\n' + json.dumps(evidence())])
def test_manual_refresh_rejects_empty_malformed_or_multiple_json_responses(tmp_path, raw_body):
    result, _ = _manual_refresh(tmp_path, None, raw_body=raw_body)
    assert result.returncode != 0


def test_finalization_orders_writes_and_verifies_complete_pool(tmp_path):
    calls = []
    def post(endpoint, token, payload, timeout):
        step = endpoint.rsplit("/", 1)[-1]
        calls.append((step, payload.copy()))
        if step == "backfill":
            return {"status": "ok", "job_id": "job", "raw_pool_date": "2026-10-15",
                "next_after_symbol": None, "errors": [], "data_gaps": [],
                "purpose": "radar_ttm_eps", "radar_stage": payload["radar_stage"],
                "cache_ready_as_of": True, "remaining_gap_count": 0}
        if step == "refresh-ai-evidence":
            return evidence()
        return {"status": "completed", "step": step, "run_date": "2026-10-15", "missing_record_count": 0, "missing_symbols": []}
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="finalize", output_dir=tmp_path, request=post) == 0
    assert [c[0] for c in calls] == ["refresh-ohlcv", "refresh-managed-raw-data", "backfill", "refresh-ai-evidence"]
    assert calls[2][1]["radar_stage"] == "raw_pool"
    assert all("private" not in p.read_text() for p in tmp_path.glob("*.json"))


@pytest.mark.parametrize("fault", ["provider", "wrong_date", "missing_lane", "missing_eps", "invalid_json"])
def test_failures_remain_fail_closed_and_still_collect_evidence(tmp_path, fault):
    calls = []
    def post(endpoint, token, payload, timeout):
        step = endpoint.rsplit("/", 1)[-1]
        calls.append(step)
        if step == "backfill":
            if fault == "provider":
                raise TimeoutError("source unavailable")
            return {"status": "ok", "job_id": "job", "raw_pool_date": "2026-10-15",
                "next_after_symbol": None, "errors": [], "data_gaps": [],
                "purpose": "radar_ttm_eps", "radar_stage": payload["radar_stage"],
                "cache_ready_as_of": True, "remaining_gap_count": 0}
        if step == "refresh-ai-evidence":
            result = evidence(missing=["2355.TW"] if fault == "missing_eps" else None)
            if fault == "wrong_date": result["run_date"] = "2026-10-16"
            if fault == "missing_lane": del result["missing_by_lane"]["margin"]
            if fault == "invalid_json": return []
            return result
        return {"status": "completed", "step": step, "run_date": "2026-10-15", "missing_record_count": 0, "missing_symbols": []}
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="finalize", output_dir=tmp_path, request=post) == 1
    assert calls[-1] == "refresh-ai-evidence"


def test_continuation_uses_server_cursor_and_budget_never_claims_readiness(tmp_path):
    calls = []
    def post(endpoint, token, payload, timeout):
        calls.append(payload.copy())
        return {"status": "ok", "job_id": "job", "raw_pool_date": "2026-10-15",
            "next_after_symbol": f"{len(calls):04}.TW", "errors": [], "data_gaps": [],
            "purpose": "radar_ttm_eps", "radar_stage": "prepared", "cache_ready_as_of": False, "remaining_gap_count": 1}
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="prewarm", output_dir=tmp_path, request=post) == 1
    assert len(calls) == 6
    assert calls[1] == {"scope": "managed", "limit": 10, "job_id": "job", "after_symbol": "0001.TW",
        "raw_pool_date": "2026-10-15", "radar_stage": "prepared"}


def test_cross_day_rejection_has_no_retry_and_keeps_diagnostics(tmp_path):
    from urllib.error import HTTPError
    from io import BytesIO
    calls = []
    def post(endpoint, token, payload, timeout):
        calls.append(payload)
        raise HTTPError(endpoint, 409, "closed", {}, BytesIO(b'{"detail":{"code":"radar_fundamental_date_closed"}}'))
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="prewarm", output_dir=tmp_path, request=post) == 1
    assert len(calls) == 1
    assert "radar_fundamental_date_closed" in (tmp_path / "backfill-1.json").read_text()


def test_unrelated_maintenance_conflict_is_never_drained_by_radar(tmp_path):
    from urllib.error import HTTPError
    from io import BytesIO
    calls = []
    def post(endpoint, token, payload, timeout):
        calls.append(payload.copy())
        raise HTTPError(endpoint, 409, "running", {}, BytesIO(b'{"detail":{"code":"fundamental_backfill_job_running","job_id":"old","expected_after_symbol":"2355.TW"}}'))
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="prewarm", output_dir=tmp_path, request=post) == 1
    assert len(calls) == 1
    assert "job_id" not in calls[0]


def test_time_budget_stops_requests_and_preserves_unfinished_state(tmp_path):
    times = iter([0, 1801])
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="prewarm", output_dir=tmp_path, request=lambda *args: pytest.fail("budget expired"),
        clock=lambda: next(times)) == 1


def test_response_returning_after_deadline_cannot_complete_preparation(tmp_path):
    elapsed = [0]
    def post(endpoint, token, payload, timeout):
        elapsed[0] = 1801
        return {"status": "ok", "cache_ready_as_of": True, "raw_pool_date": "2026-10-15",
            "job_id": None, "next_after_symbol": None, "errors": [], "data_gaps": [],
                "purpose": "radar_ttm_eps", "radar_stage": payload["radar_stage"],
                "cache_ready_as_of": True, "remaining_gap_count": 0}
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="prewarm", output_dir=tmp_path, request=post, clock=lambda: elapsed[0]) == 1


def test_redirect_rejects_forwarding_internal_token():
    from urllib.error import HTTPError
    from urllib.request import Request
    with pytest.raises(HTTPError, match="evidence_redirect_rejected"):
        runner()._NoRedirect().redirect_request(Request("https://example.invalid", headers={"Authorization": "Bearer private"}),
            None, 302, "redirect", {}, "https://elsewhere.invalid")


def test_cache_attestation_can_finish_without_creating_a_job(tmp_path):
    def post(endpoint, token, payload, timeout):
        return {"status": "ok", "cache_ready_as_of": True, "raw_pool_date": "2026-10-15",
            "job_id": None, "next_after_symbol": None, "errors": [], "data_gaps": [],
                "purpose": "radar_ttm_eps", "radar_stage": payload["radar_stage"],
                "cache_ready_as_of": True, "remaining_gap_count": 0}
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="prewarm", output_dir=tmp_path, request=post) == 0


def test_wall_clock_timeout_interrupts_blocking_response_and_restores_signal():
    import signal
    import time
    previous = signal.getsignal(signal.SIGALRM)
    with pytest.raises(TimeoutError, match="evidence_http_wall_clock_timeout"):
        with runner().request_deadline(0.02):
            time.sleep(1)
    assert signal.getsignal(signal.SIGALRM) == previous


def test_incomplete_http_body_does_not_prevent_final_evidence_diagnostics(tmp_path):
    from http.client import IncompleteRead
    calls = []
    def post(endpoint, token, payload, timeout):
        step = endpoint.rsplit("/", 1)[-1]
        calls.append(step)
        if step == "refresh-ohlcv": raise IncompleteRead(b"partial")
        if step == "refresh-ai-evidence": return evidence()
        if step == "backfill": return {"status": "ok", "cache_ready_as_of": True,
            "job_id": None, "raw_pool_date": "2026-10-15", "next_after_symbol": None, "errors": [], "data_gaps": [],
                "purpose": "radar_ttm_eps", "radar_stage": payload["radar_stage"],
                "cache_ready_as_of": True, "remaining_gap_count": 0}
        return {"status": "completed", "step": step, "run_date": "2026-10-15", "missing_record_count": 0}
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="finalize", output_dir=tmp_path, request=post) == 1
    assert calls[-1] == "refresh-ai-evidence"
