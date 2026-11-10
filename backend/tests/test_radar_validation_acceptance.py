"""Acceptance contracts shared by the legacy validation endpoints."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar import history_repair as repair, maintenance_router
from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.research_validation import run_research_validation, RESEARCH_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.validation_execution import run_signal_close_validation
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, DailyRadarForwardValidationResult as Result
from test_next_open_history_repair import db, Prices, SIGNAL, DAY
from test_radar_job_postgres import pg_engine
from test_radar_public_window_eligibility import seed_basis
from radar_calendar_support import calendar

WINDOWS = [5, 10, 20, 40, 60]


def request(basis):
    return DailyRadarForwardValidationRunRequest(mode='range', return_basis=basis, windows=WINDOWS,
        start_date=SIGNAL, end_date=SIGNAL, as_of_date=DAY)


def run(db, basis, cal, *, fetch=None, prepare_only=False):
    prices = Prices(cal)
    kwargs = dict(as_of_date=DAY, price_provider=prices, prepare_only=prepare_only,
        calendar_provider=SimpleNamespace(fetch=fetch or (lambda **kw: cal)))
    if basis == 'next_open':
        return run_research_validation(db, request(basis), **kwargs)
    return run_signal_close_validation(db, request(basis), **kwargs,
        raw_price_loader=lambda db, **kw: prices.fetch(kw['symbols'], start_date=kw['start_date'], end_date=kw['end_date']))


def snapshots(db):
    return {r.id: repair._storage_fingerprint(repair.result_snapshot(r)) for r in db.scalars(select(Result))}


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
@pytest.mark.parametrize('fault', ['absent', 'removed_during_fetch', 'conflict_during_fetch'])
def test_rejected_calendar_cannot_leave_any_partial_result_even_if_caller_commits(db, basis, fault):
    cal = seed_basis(db, basis)
    version = FORWARD_VALIDATION_VERSION if basis == 'signal_close' else RESEARCH_VALIDATION_VERSION
    row = db.scalar(select(Result).where(Result.window_days == 20, Result.validation_version == version))
    row.outcome = row.outcome | {'forward_return_pct': 777}
    if fault == 'absent': db.delete(db.scalar(select(DailyRadarPreparedRun)))
    db.commit()
    before = snapshots(db)
    def fetch(**kwargs):
        assert db.get_bind().pool.checkedout() == 0
        if fault != 'absent':
            with Session(db.get_bind()) as other:
                prepared = other.scalar(select(DailyRadarPreparedRun))
                if fault == 'removed_during_fetch': other.delete(prepared)
                else: prepared.step_statuses = {'validation-calendar':
                    calendar(cal.start, DAY, {SIGNAL+timedelta(days=1)}).payload()}
                other.commit()
        return cal
    error = 'calendar_evidence_conflict' if fault == 'conflict_during_fetch' else 'calendar_checkpoint_unavailable'
    with pytest.raises(ValueError, match=error): run(db, basis, cal, fetch=fetch)
    db.commit(); db.expire_all()
    assert snapshots(db) == before


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_successful_acceptance_keeps_all_window_identities(db, basis):
    cal = seed_basis(db, basis)
    result = run(db, basis, cal)
    db.commit()
    assert (result['validated_count'] if isinstance(result, dict) else result.validated_count) == 5
    for row in db.scalars(select(Result)):
        proof = row.outcome['validation_window']
        assert proof['window_days'] == row.window_days
        assert proof['target_date'] == str(row.target_date)


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_prepare_only_needs_no_checkpoint_and_writes_no_results(db, basis):
    cal = seed_basis(db, basis)
    db.delete(db.scalar(select(DailyRadarPreparedRun))); db.commit()
    before = snapshots(db)
    bundle = run(db, basis, cal, prepare_only=True)
    assert len(bundle.outcomes) == 5 and bundle.calendar.payload() == cal.payload()
    db.commit()
    assert snapshots(db) == before and db.scalar(select(DailyRadarPreparedRun)) is None


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_endpoint_exposes_missing_checkpoint_as_422(db, monkeypatch, basis):
    from ai_stock_sentinel.calibration.price_provider import get_forward_price_provider, get_research_price_provider
    from ai_stock_sentinel.daily_radar.trading_calendar import get_trading_calendar_provider
    from ai_stock_sentinel.db.session import get_db
    cal = seed_basis(db, basis)
    db.delete(db.scalar(select(DailyRadarPreparedRun))); db.commit()
    before = snapshots(db)
    prices = Prices(cal)
    app = FastAPI(); app.include_router(maintenance_router.router)
    app.dependency_overrides[get_db] = lambda: db
    for dependency in (get_forward_price_provider, get_research_price_provider):
        app.dependency_overrides[dependency] = lambda: prices
    app.dependency_overrides[get_trading_calendar_provider] = lambda: SimpleNamespace(fetch=lambda **kw: cal)
    monkeypatch.setenv('DAILY_RADAR_INTERNAL_TOKEN', 'acceptance-test')
    monkeypatch.setattr(maintenance_router, 'load_price_series_from_raw_data',
        lambda db, **kw: prices.fetch(kw['symbols'], start_date=kw['start_date'], end_date=kw['end_date']))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/internal/daily-radar/forward-validation/run',
            json=request(basis).model_dump(mode='json'), headers={'Authorization': 'Bearer acceptance-test'})
    assert response.status_code == 422, response.text
    assert response.json()['detail'] == 'calendar_checkpoint_unavailable'
    db.commit(); db.expire_all()
    assert snapshots(db) == before


def test_durable_calendar_ledger_remains_valid_without_prepared_checkpoint(db, monkeypatch):
    from ai_stock_sentinel.daily_radar.work_queue import ensure_work
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    cal = seed_basis(db, 'next_open')
    candidate_id = db.scalar(select(Result.candidate_id))
    version = db.scalar(select(Result.validation_version))
    db.delete(db.scalar(select(DailyRadarPreparedRun)))
    work = ensure_work(db, identity={'fixture': 'calendar-ledger'}, stage='calendar', lane='validation',
        run_date=DAY, scope={}, input_revision=repair.fingerprint({'fixture': 'calendar-ledger'}), state='ready')
    work.result = {'calendar': cal.payload()}; db.commit()
    before = snapshots(db)
    claim = SimpleNamespace(scope={'candidate_id': candidate_id, 'return_basis': 'next_open',
        'validation_version': version, 'window_days': 20, 'signal_date': str(SIGNAL), 'as_of_date': str(DAY)})
    collection = WorkHandlers(db).collect_window(claim)
    db.rollback()
    accepted = collection.apply(db); db.commit()
    assert accepted['state'] == 'ready' and snapshots(db) == before
    assert load_calendar(db, start_date=SIGNAL, as_of_date=DAY).payload() == cal.payload()


@pytest.mark.parametrize('checkpoint_present', [False, True])
def test_zero_result_acceptance_still_requires_and_saves_calendar(db, checkpoint_present):
    from ai_stock_sentinel.daily_radar.validation_preparation import accept_validation_results
    from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar
    cal = seed_basis(db, 'next_open')
    prepared = db.scalar(select(DailyRadarPreparedRun))
    if checkpoint_present: prepared.step_statuses = {}
    else: db.delete(prepared)
    db.commit()
    before = snapshots(db)
    if checkpoint_present:
        assert accept_validation_results(db, [], calendar=cal)['records_written'] == 0
    else:
        with pytest.raises(ValueError, match='calendar_checkpoint_unavailable'):
            accept_validation_results(db, [], calendar=cal)
    db.commit()
    assert snapshots(db) == before
    assert (load_calendar(db, start_date=SIGNAL, as_of_date=DAY) is not None) is checkpoint_present
