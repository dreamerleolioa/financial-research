"""Retiring the personal AI report must preserve product data refreshes."""
from datetime import date
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_stock_sentinel.api import app
from ai_stock_sentinel.data_sources.fundamental import service
from ai_stock_sentinel.data_sources.fundamental.router import backfill_fundamentals_endpoint
from ai_stock_sentinel.data_sources.fundamental.schemas import FundamentalBackfillRequest
from ai_stock_sentinel.daily_radar.schemas import DailyRadarRefreshStepResponse, DailyRadarManagedRawDataRefreshResponse
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, StockAnalysisCache, FundamentalBackfillJob


DAY = date(2026, 10, 20)


def runner():
    path = Path(__file__).parents[2] / ".github/scripts/refresh_daily_radar_raw_data.py"
    spec = importlib.util.spec_from_file_location("raw_refresh", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("fault", [None, "http", "json", "wrong_date", "missing", "errors"])
def test_nightly_raw_refresh_keeps_order_and_failure_diagnostics(tmp_path, fault):
    calls = []

    def request(endpoint, token, payload, timeout):
        step = endpoint.rsplit("/", 1)[1]
        calls.append((step, payload.copy()))
        if len(calls) == 1 and fault in {"http", "json"}:
            raise OSError("offline") if fault == "http" else ValueError("invalid JSON")
        response_type = (DailyRadarRefreshStepResponse if step == "refresh-ohlcv"
                         else DailyRadarManagedRawDataRefreshResponse)
        result = response_type(status="completed", step=step, run_date=DAY, market="TW").model_dump(mode="json")
        if len(calls) == 1:
            if fault == "wrong_date":
                result["run_date"] = "2026-10-19"
            elif fault == "missing":
                result["missing_symbols"] = ["2330.TW"]
            elif fault == "errors":
                result["errors"] = ["provider_failed"]
        return result

    result = runner().run(base_url="https://example.invalid", token="private",
                          run_date=str(DAY), output_dir=tmp_path, request=request)
    assert result == int(fault is not None)
    assert [step for step, _ in calls] == ["refresh-ohlcv", "refresh-managed-raw-data"]
    assert all(payload == {"market": "TW", "run_date": str(DAY)} for _, payload in calls)
    assert (tmp_path / "summary.json").exists()


@pytest.mark.parametrize("missing_count", [None, True, 1, "0"])
def test_managed_raw_refresh_requires_numeric_zero_missing_records(tmp_path, missing_count):
    def request(endpoint, token, payload, timeout):
        return {"status": "completed", "step": endpoint.rsplit("/", 1)[1],
                "run_date": str(DAY), "missing_symbols": [],
                "missing_record_count": missing_count, "error_codes": []}
    assert runner().run(base_url="https://example.invalid", token="private",
                        run_date=str(DAY), output_dir=tmp_path, request=request) == 1


def test_fundamental_targets_use_radar_and_recent_analysis_without_ai_pool(monkeypatch):
    # Deliberately omit stock_raw_data: a retired AI pool must never be queried.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    DailyRadarPreparedRun.__table__.create(engine)
    StockAnalysisCache.__table__.create(engine)
    monkeypatch.setattr(service, "today_taipei", lambda: DAY)
    with Session(engine) as session:
        session.add_all([
            DailyRadarPreparedRun(run_date=DAY, market="TW", selected_symbols=["2330.TW"],
                universe=[], symbol_count=1,
                step_statuses={"refresh-ai-evidence": {"status": "completed"}}),
            StockAnalysisCache(symbol="2454.TW", record_date=DAY, analysis_type="general"),
            StockAnalysisCache(symbol="3008.TW", record_date=date(2026, 8, 1), analysis_type="general"),
            StockAnalysisCache(symbol="6488.TWO", record_date=DAY, analysis_type="position"),
            StockAnalysisCache(symbol="2317.TW", record_date=date(2026, 10, 21), analysis_type="general"),
        ])
        session.commit()
        assert service.resolve_managed_fundamental_symbols(session) == ["2330.TW", "2454.TW"]
    engine.dispose()


def test_fundamental_targets_include_recent_symbols_beyond_raw_refresh_cap(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    DailyRadarPreparedRun.__table__.create(engine)
    StockAnalysisCache.__table__.create(engine)
    monkeypatch.setattr(service, "today_taipei", lambda: DAY)
    symbols = [f"{number}.TW" for number in range(1100, 1351)]
    with Session(engine) as session:
        session.add_all(StockAnalysisCache(symbol=symbol, record_date=DAY, analysis_type="general")
                        for symbol in symbols)
        session.commit()
        assert service.resolve_managed_fundamental_symbols(session) == symbols
    engine.dispose()


@pytest.mark.parametrize("payload", [{"raw_pool_date": str(DAY)}, {"radar_stage": "prepared"}])
def test_retired_backfill_arguments_are_rejected(payload):
    with pytest.raises(ValidationError):
        FundamentalBackfillRequest.model_validate(payload)


def test_ai_evidence_endpoint_is_retired():
    assert TestClient(app).post("/internal/daily-radar/refresh-ai-evidence", json={}).status_code == 404


def test_retained_report_jobs_cannot_be_resumed_as_maintenance():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    FundamentalBackfillJob.__table__.create(engine)
    with Session(engine) as session:
        job = FundamentalBackfillJob(id="retired-report", purpose="radar_ttm_eps",
            raw_pool_date=DAY, radar_stage="prepared", symbols=["2330.TW"])
        session.add(job)
        session.commit()
        assert service.get_oldest_running_fundamental_backfill_job(session) is None
        with pytest.raises(HTTPException) as error:
            backfill_fundamentals_endpoint(FundamentalBackfillRequest(job_id=job.id), db=session)
        assert error.value.detail["code"] == "fundamental_backfill_purpose_mismatch"
        assert job.status == "running" and job.next_after_symbol is None
    engine.dispose()


def test_nightly_diagnostics_redact_upstream_token_echo(tmp_path, capsys):
    token = 'secret"\\value'
    def request(endpoint, supplied_token, payload, timeout):
        return {"status": "failed", "echo": token}
    assert runner().run(base_url="https://example.invalid", token=token,
        run_date=str(DAY), output_dir=tmp_path, request=request) == 1
    assert "secret" not in capsys.readouterr().out
    assert all("secret" not in path.read_text() for path in tmp_path.glob("*.json"))


def test_nightly_budget_exhaustion_stops_further_network_requests(tmp_path):
    elapsed = [0]
    calls = []
    def request(endpoint, token, payload, timeout):
        calls.append(endpoint)
        elapsed[0] = 1801
        return {"status": "completed", "step": "refresh-ohlcv", "run_date": str(DAY),
                "missing_symbols": [], "errors": []}
    assert runner().run(base_url="https://example.invalid", token="private",
        run_date=str(DAY), output_dir=tmp_path, request=request, clock=lambda: elapsed[0]) == 1
    assert len(calls) == 1


@pytest.mark.parametrize("recovered", [False, True])
def test_raw_refresh_preserves_symbol_diagnostics_without_leaking_token(tmp_path, capsys, recovered):
    actual_date = "2026-10-20" if recovered else "2026-10-19"
    diagnostic = {"2323.TW": {"requested_run_date": "2026-10-20", "actual_data_date": actual_date,
                              "attempts": [{"stage": "raw_adjustment", "reason": "private-token"}]}}
    def request(endpoint, token, payload, timeout):
        step = endpoint.rsplit("/", 1)[1]
        if step == "refresh-ohlcv":
            return {"status": "completed" if recovered else "failed", "step": step, "run_date": "2026-10-20",
                    "missing_symbols": [] if recovered else ["2323.TW"], "errors": [],
                    "symbol_diagnostics": diagnostic}
        return {"status": "completed", "step": step, "run_date": "2026-10-20",
                "missing_record_count": 0, "error_codes": []}
    assert runner().run(base_url="https://example.invalid", token="private-token", run_date="2026-10-20",
                        output_dir=tmp_path, request=request) == int(not recovered)
    output = capsys.readouterr().out
    saved = (tmp_path/"refresh-ohlcv.json").read_text()
    assert "private-token" not in output + saved
    assert json.loads(saved)["symbol_diagnostics"]["2323.TW"]["actual_data_date"] == actual_date
    if not recovered:
        assert "2323.TW" in output
