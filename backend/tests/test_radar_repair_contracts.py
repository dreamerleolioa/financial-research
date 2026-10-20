"""Regression cases for bounded validation transport and diagnostic handling."""
from datetime import date, datetime, timezone
from http.client import IncompleteRead
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

DAY = date(2026, 10, 15)


def _script(filename):
    spec = importlib.util.spec_from_file_location(filename, Path(__file__).parents[2] / '.github/scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_truncated_first_validation_retains_diagnostics_and_runs_second(tmp_path):
    calls = []
    def request(endpoint, token, payload):
        calls.append(payload['return_basis'])
        if payload['return_basis'] == 'signal_close':
            raise IncompleteRead(b'{', 20)
        return {'status': 'completed', 'as_of_date': str(DAY), 'retryable_skipped_count': 0,
            'report': {'remaining_retryable_window_count': 0}}
    assert _script('run_daily_radar_validation.py').run(endpoint='https://example.invalid', token='private',
        run_date=str(DAY), output_dir=tmp_path, request=request, sleep=lambda _: None) == 1
    assert calls == ['signal_close', 'signal_close', 'next_open']
    assert 'IncompleteRead' in (tmp_path / 'signal_close-attempt-1.json').read_text()
    assert (tmp_path / 'next_open-attempt-1.json').exists()


def test_validation_redacts_escaped_token_in_artifacts_and_console(tmp_path, capsys):
    token = 'secret"\\\nvalue'
    def request(endpoint, token, payload):
        return {'status': 'completed', 'as_of_date': str(DAY), 'retryable_skipped_count': 0,
            'echo': token, 'report': {'remaining_retryable_window_count': 0, 'current_run_gaps': [token]}}
    assert _script('run_daily_radar_validation.py').run(endpoint='https://example.invalid', token=token,
        run_date=str(DAY), output_dir=tmp_path, request=request) == 0
    import json
    for path in tmp_path.glob('*.json'):
        assert json.loads(path.read_text())['echo'] == '[redacted]'
    assert 'secret' not in capsys.readouterr().out


def test_request_deadline_rejects_response_that_swallowed_alarm():
    import time
    from daily_radar_http import request_deadline
    with pytest.raises(TimeoutError):
        with request_deadline(.01):
            try:
                time.sleep(.05)
            except TimeoutError:
                time.sleep(.02)
