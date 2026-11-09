import json
import pytest

from check_daily_radar_readiness import run


def response():
    return {"version": "radar-readiness-v1", "market": "TW", "requested_run_date": "2026-11-06",
        "effective_run_date": "2026-11-06", "publication": {"state": "published", "status": "completed", "run_id": 187},
        "scoring": {"ready": True, "blockers": []}, "data_ready": False,
        "validation": {"signal_close": {"ready": True}, "next_open": {"ready": False, "unresolved_count": 5}}}


def test_publication_and_validation_have_independent_exit_codes(tmp_path, capsys):
    kwargs = dict(endpoint="https://backend.invalid", token="private", run_date="2026-11-06", output_dir=tmp_path,
        request=lambda *args: response())
    assert run(**kwargs, scope="publication") == 0
    assert run(**kwargs, scope="next_open") == 1
    assert run(**kwargs, scope="all") == 1
    assert json.loads(capsys.readouterr().out.splitlines()[0])["publication"]["run_id"] == 187


def test_wrong_date_and_missing_readiness_are_not_success(tmp_path):
    payload = response() | {"effective_run_date": "2026-11-05", "data_ready": True, "secret": "private"}
    assert run(endpoint="https://backend.invalid", token="private", run_date="2026-11-06", output_dir=tmp_path,
        request=lambda *args: payload, scope="all") == 1
    assert "private" not in (tmp_path / "readiness-all.json").read_text()


@pytest.mark.parametrize("payload", [None, [], {"data_ready": True}, response() | {"validation": []},
    response() | {"scoring": None}, response() | {"validation": {"next_open": []}}])
def test_malformed_response_is_retained_and_fails_cleanly(tmp_path, payload):
    assert run(endpoint="https://backend.invalid", token="private", run_date="2026-11-06", output_dir=tmp_path,
        request=lambda *args: payload) == 1


def test_truncated_http_response_preserves_failure_artifact(tmp_path):
    from http.client import IncompleteRead
    def request(*args):
        raise IncompleteRead(b'private', 100)
    assert run(endpoint="https://backend.invalid", token="private", run_date="2026-11-06",
        output_dir=tmp_path, request=request) == 1
    artifact = (tmp_path / "readiness-all.json").read_text()
    assert "IncompleteRead" in artifact
    assert "private" not in artifact
