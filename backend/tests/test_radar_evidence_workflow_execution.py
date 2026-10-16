import importlib.util
from pathlib import Path

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


def test_finalization_orders_writes_and_verifies_complete_pool(tmp_path):
    calls = []
    def post(endpoint, token, payload, timeout):
        step = endpoint.rsplit("/", 1)[-1]
        calls.append((step, payload.copy()))
        if step == "backfill":
            return {"status": "ok", "job_id": "job", "raw_pool_date": "2026-10-15",
                "next_after_symbol": None, "errors": [], "data_gaps": []}
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
                "next_after_symbol": None, "errors": [], "data_gaps": []}
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
            "next_after_symbol": f"{len(calls):04}.TW", "errors": [], "data_gaps": []}
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


def test_old_job_completion_is_followed_by_a_new_date_bound_preparation(tmp_path):
    from urllib.error import HTTPError
    from io import BytesIO
    calls = []
    def post(endpoint, token, payload, timeout):
        calls.append(payload.copy())
        if len(calls) == 1:
            raise HTTPError(endpoint, 409, "running", {}, BytesIO(b'{"detail":{"code":"fundamental_backfill_job_running","job_id":"old","expected_after_symbol":"2355.TW"}}'))
        return {"status": "ok", "job_id": "old" if len(calls) == 2 else "new",
            "raw_pool_date": "2026-10-14" if len(calls) == 2 else "2026-10-15",
            "next_after_symbol": None, "errors": [], "data_gaps": []}
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="prewarm", output_dir=tmp_path, request=post) == 0
    assert calls[1] == {"scope": "managed", "limit": 10, "job_id": "old", "after_symbol": "2355.TW"}
    assert calls[2] == calls[0]


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
            "job_id": None, "next_after_symbol": None, "errors": [], "data_gaps": []}
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
            "job_id": None, "next_after_symbol": None, "errors": [], "data_gaps": []}
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
            "job_id": None, "raw_pool_date": "2026-10-15", "next_after_symbol": None, "errors": [], "data_gaps": []}
        return {"status": "completed", "step": step, "run_date": "2026-10-15", "missing_record_count": 0}
    assert runner().run(base_url="https://example.invalid", token="private", run_date="2026-10-15",
        mode="finalize", output_dir=tmp_path, request=post) == 1
    assert calls[-1] == "refresh-ai-evidence"
