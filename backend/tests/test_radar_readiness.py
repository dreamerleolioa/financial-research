from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.db.session import Base, get_db
from ai_stock_sentinel.db.models import (
    DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult,
    DailyRadarPreparedRun, StockRawData,
)
from ai_stock_sentinel.daily_radar import readiness_router
from ai_stock_sentinel.daily_radar.readiness import read_readiness, validation_inventory
from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
from test_daily_radar_validation_read import add_candidate, add_run, add_result
from radar_calendar_support import calendar


@pytest.fixture
def storage():
    engine = create_engine("sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult,
        DailyRadarPreparedRun, StockRawData)])
    with Session(engine) as db:
        yield db, engine
    engine.dispose()


def proof(db, start=date(2026, 6, 1), end=date(2026, 6, 30), closed=()):
    row = DailyRadarPreparedRun(run_date=end, market="TW", status="prepared",
        selected_symbols=[], universe=[], symbol_count=0, errors=[],
        step_statuses={"validation-calendar": calendar(start, end, closed).payload()})
    db.add(row)
    db.commit()
    return row


def test_published_radar_and_unfinished_bases_are_reported_independently(storage):
    db, engine = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate)
    add_result(db, candidate, version=RESEARCH_VALIDATION_VERSION, diagnostic=False)
    research = db.scalar(select(DailyRadarForwardValidationResult).where(
        DailyRadarForwardValidationResult.validation_version == RESEARCH_VALIDATION_VERSION))
    research.outcome = research.outcome | {"return_basis": "next_open", "price_basis": "unadjusted_price"}
    proof(db)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda c, cu, sql, *args: statements.append(sql))
    result = read_readiness(db, run_date=date(2026, 6, 30), lookback_days=90)
    assert result["publication"]["state"] == "not_published"
    # The historical run exists independently of readiness of today's inputs.
    assert read_readiness(db, run_date=date(2026, 6, 1))["publication"]["state"] == "published"
    assert result["validation"]["signal_close"]["validated_count"] == 1
    assert result["validation"]["next_open"]["validated_count"] == 1
    assert result["validation"]["next_open"]["ready"] is False
    assert result["validation"]["next_open"]["missing_outcome_count"] == 2
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)


def test_validated_is_not_complete_without_current_first_observation_diagnostic(storage):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate, diagnostic=False)
    proof(db)
    result = validation_inventory(db, as_of_date=date(2026, 6, 30), lookback_days=90)
    close = result["signal_close"]
    assert close["missing_diagnostic_count"] == 1
    assert any(item["reason"] == "missing_observation_diagnostic" for item in close["gaps"])


def test_official_calendar_prevents_shifted_target_and_holiday_signal(storage):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate)  # 6/8 is the fifth weekday after 6/1
    proof(db, closed=(date(2026, 6, 2),))
    result = validation_inventory(db, as_of_date=date(2026, 6, 30))
    assert result["signal_close"]["invalid_window_count"] == 1
    assert result["signal_close"]["validated_count"] == 0


def test_missing_calendar_is_unknown_not_immature(storage):
    db, _ = storage
    add_candidate(db, add_run(db, date(2026, 6, 29)))
    db.commit()
    result = validation_inventory(db, as_of_date=date(2026, 6, 30))
    assert result["signal_close"]["calendar_ready"] is False
    assert result["signal_close"]["immature_count"] == 0
    assert result["signal_close"]["unknown_maturity_count"] == 5


def test_inventory_is_bounded_but_counts_all_gaps(storage):
    db, _ = storage
    run = add_run(db, count=5)
    for i in range(5):
        add_candidate(db, run, f"{2300+i}.TW")
    proof(db)
    result = validation_inventory(db, as_of_date=date(2026, 6, 30), sample_limit=2)
    assert result["next_open"]["missing_outcome_count"] == 15
    assert len(result["next_open"]["gaps"]) == 2
    assert result["next_open"]["gaps_truncated"] is True


def test_readiness_endpoint_requires_auth_and_exact_date(storage, monkeypatch):
    db, _ = storage
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-readiness")
    app = FastAPI()
    app.include_router(readiness_router.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get("/internal/daily-radar/readiness?run_date=2026-06-30").status_code == 401
        headers = {"Authorization": "Bearer test-readiness"}
        assert client.get("/internal/daily-radar/readiness", headers=headers).status_code == 422
        result = client.get("/internal/daily-radar/readiness?run_date=2026-06-30", headers=headers)
        assert result.status_code == 200
        assert result.json()["requested_run_date"] == result.json()["effective_run_date"] == "2026-06-30"
        assert result.json()["scoring"]["ready"] is False


@pytest.mark.parametrize("status", ["already_broken_out", "already_invalidated"])
def test_legitimate_non_event_diagnostic_is_complete(storage, status):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate, status=status)
    proof(db)
    report = validation_inventory(db, as_of_date=date(2026, 6, 30))["signal_close"]
    assert report["missing_diagnostic_count"] == 0
    assert report["validated_count"] == 1


@pytest.mark.parametrize("data", [{}, {"return_basis": "next_open", "price_basis": "adjusted"},
    {"return_basis": "signal_close", "price_basis": "unadjusted_price"}])
def test_research_basis_mismatch_is_unresolved(storage, data):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate, diagnostic=False, version=RESEARCH_VALIDATION_VERSION)
    row = db.scalar(select(DailyRadarForwardValidationResult))
    row.outcome = data | {'validation_input': row.outcome['validation_input']}
    proof(db)
    report = validation_inventory(db, as_of_date=date(2026, 6, 30))["next_open"]
    assert report["validated_count"] == 0
    assert report["reasons"]["research_basis_mismatch"] == 1


def test_termination_reason_without_matching_lifecycle_evidence_is_not_terminal(storage):
    db, _ = storage
    candidate = add_candidate(db, add_run(db))
    add_result(db, candidate)
    row = db.scalar(select(DailyRadarForwardValidationResult))
    row.status, row.skip_reason, row.outcome = "skipped", "trading_terminated_in_window", {}
    proof(db)
    report = validation_inventory(db, as_of_date=date(2026, 6, 30))["signal_close"]
    assert report["terminal_count"] == 0
    assert report["retryable_count"] == 1


def test_authenticated_alert_inventory_can_return_more_than_sample_limit(storage, monkeypatch):
    db, _ = storage
    run = add_run(db)
    for i in range(35):
        add_candidate(db, run, f"{2300+i}.TW")
    proof(db)
    monkeypatch.setenv("DAILY_RADAR_INTERNAL_TOKEN", "test-readiness")
    app = FastAPI()
    app.include_router(readiness_router.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test-readiness"}
        url = "/internal/daily-radar/readiness?run_date=2026-06-30"
        sample = client.get(url, headers=headers).json()["validation"]["next_open"]
        full = client.get(url + "&include_all_validation_gaps=true", headers=headers).json()["validation"]["next_open"]
    assert sample["gaps_truncated"] and len(sample["gaps"]) == 100
    assert full["gaps_truncated"] is False
    assert full["unresolved_count"] == len(full["gaps"]) == 105
    assert full["ready"] is sample["ready"] is False
    assert all(len(gap["gap_revision"]) == 64 for gap in full["gaps"])


def test_compact_comparison_through_driver_with_oversized_inventory(storage, monkeypatch, tmp_path):
    import hashlib
    import json
    from io import BytesIO
    from daily_radar_http import _read_json
    from run_daily_radar_jobs import run

    db, _ = storage
    proof(db)
    day = '2026-06-30'
    rows = []
    for basis, known, new in [('signal_close', 1439, 1033), ('next_open', 457, 760)]:
        for i in range(known + new):
            revision = hashlib.sha256(str(i).encode()).hexdigest()
            rows.append(dict(candidate_id=i + 1, candidate_revision=revision, calendar_revision=revision,
                             signal_date='2026-06-01', symbol='2330.TW', return_basis=basis, window_days=5,
                             expected_target_date='2026-06-10', validation_version='daily-radar-test-validation-return-basis-and-source-proof-v2',
                             state='missing_diagnostic' if i < known else 'missing_outcome',
                             reason='observation_diagnostic_incomplete' if i < known else 'validation_outcome_missing',
                             next_action='repair_diagnostic' if i < known else 'waiting_provider',
                             gap_revision=revision))
    with pytest.raises(ValueError, match='evidence_response_too_large'):
        _read_json(BytesIO(json.dumps(rows, separators=(',', ':')).encode()))
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps({'version': 'radar-known-validation-gaps-v1', 'as_of_date': day,
                                    'gaps': [g for g in rows if g['state'] == 'missing_diagnostic']}))
    def inventory(*args, **kwargs):
        assert kwargs['include_gap_revision']
        yield from rows
    monkeypatch.setattr('ai_stock_sentinel.daily_radar.readiness.inventory_items', inventory)
    monkeypatch.setenv('DAILY_RADAR_INTERNAL_TOKEN', 'test-readiness')
    app = FastAPI()
    app.include_router(readiness_router.router)
    app.dependency_overrides[get_db] = lambda: db
    receipt = {'requested_run_date': day, 'effective_run_date': day, 'job_id': 37}
    with TestClient(app) as client:
        def read(url, token, **kwargs):
            assert url.endswith('/capabilities')
            return {'version': 'radar-durable-v1', 'enabled': True, 'scoped_resume': True,
                    'validation_gap_comparison': True}

        def write(url, token, body, **kwargs):
            if '/jobs' in url:
                return receipt
            path = url.removeprefix('https://example.test')
            assert client.post(path, json=body).status_code == 401
            resp = client.post(path, json=body, headers={'Authorization': 'Bearer ' + token})
            resp.raise_for_status()
            assert len(resp.content) < 32 * 1024
            return _read_json(BytesIO(resp.content))

        def scheduled():
            return run(endpoint='https://example.test', token='test-readiness', output_dir=tmp_path,
                       run_date=day, require_lanes=('signal_close','next_open'), owner='validation',
                       read=read, write=write, known_validation_gaps=baseline)

        result = scheduled()
        assert result['validation_alerts'] == {'signal_close': {'known': 1439, 'new': 1033},
                                               'next_open': {'known': 457, 'new': 760}}
        assert result['status'] == 'failed' and result['errors'] == []
        rows[:] = [g for g in rows if g['state'] == 'missing_diagnostic']
        known_only = scheduled()
        assert known_only['status'] == 'recorded_or_advanced' and not known_only['readiness_deadline_passed']
        original = rows[-1]
        rows[-1] = dict(original, gap_revision=hashlib.sha256(b'updated evidence').hexdigest())
        changed = scheduled()
        assert changed['validation_alerts']['next_open'] == {'known': 456, 'new': 1}
        assert changed['status'] == 'failed' and not changed['readiness_deadline_passed']
        rows.append(original)
        duplicate = scheduled()
        assert duplicate['status'] == 'failed' and duplicate['errors'][0]['phase'] == 'readiness'
        # This reviewed baseline is from the future relative to the requested date.
        baseline.write_text(json.dumps(json.loads(baseline.read_text()) | {'as_of_date': '2026-07-01'}))
        rows[:] = [original]
        historical = scheduled()
        assert historical['validation_alerts'] == {'signal_close': {'known': 0, 'new': 0},
                                                  'next_open': {'known': 0, 'new': None}}
        assert historical['status'] == 'failed' and historical['errors'] == []
        rows.clear()
        assert scheduled()['readiness_deadline_passed'] is True
        url = f'/internal/daily-radar/readiness/compare?run_date={day}'
        payload = {'version': 'radar-gap-comparison-v1', 'as_of_date': day, 'hashes': []}
        headers = {'Authorization': 'Bearer test-readiness'}
        for malformed in ({'as_of_date': '2026-07-01'}, {'hashes': ['abc']}, {'hashes': ['a' * 64] * 2}):
            assert client.post(url, headers=headers, json=payload | malformed).status_code == 422


def test_app_returns_bounded_comparison_validation_error(storage, monkeypatch):
    from ai_stock_sentinel.api import app

    db, _ = storage
    monkeypatch.setenv('DAILY_RADAR_INTERNAL_TOKEN', 'test-readiness')
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    response = TestClient(app).post('/internal/daily-radar/readiness/compare?run_date=2026-06-30',
        headers={'Authorization': 'Bearer test-readiness'},
        json={'version': 'radar-gap-comparison-v1', 'as_of_date': '2026-06-30', 'hashes': ['invalid'] * 15000})
    assert response.status_code == 422
    assert len(response.content) < 256
    assert response.json() == {'detail': 'invalid_validation_gap_baseline'}
