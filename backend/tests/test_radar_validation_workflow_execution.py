import importlib.util
from pathlib import Path


def runner():
    path = Path(__file__).parents[2] / ".github/scripts/run_daily_radar_validation.py"
    spec = importlib.util.spec_from_file_location("radar_validation_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
