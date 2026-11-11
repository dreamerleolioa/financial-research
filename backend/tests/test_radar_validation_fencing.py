"""Legacy validation must reject work computed from obsolete input/result versions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.forward_validation import (
    FORWARD_VALIDATION_VERSION, upsert_forward_validation_results,
)
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION, run_research_validation
from ai_stock_sentinel.daily_radar.validation_execution import run_signal_close_validation
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarForwardValidationResult as Result
from test_next_open_history_repair import db, Prices, SIGNAL, DAY
from test_radar_job_postgres import pg_engine
from test_radar_public_window_eligibility import seed_basis
from test_radar_validation_acceptance import snapshots
from test_daily_radar_validation_read import add_run, add_candidate
from test_twse_rights_recovery import scenario
from ai_stock_sentinel.daily_radar.history_repair import result_snapshot

WINDOWS = [5, 10, 20, 40, 60]


def version(basis):
    return FORWARD_VALIDATION_VERSION if basis == 'signal_close' else RESEARCH_VALIDATION_VERSION


def execute(db, basis, cal, *, during_fetch=lambda: None, missing=False):
    prices = Prices(cal, missing=cal.sessions if missing else ())
    request = DailyRadarForwardValidationRunRequest(mode='range', return_basis=basis, windows=WINDOWS,
        start_date=SIGNAL, end_date=SIGNAL, as_of_date=DAY)
    def fetch(**kwargs):
        assert not db.in_transaction()
        during_fetch()
        return cal
    kwargs = dict(as_of_date=DAY, price_provider=prices, calendar_provider=SimpleNamespace(fetch=fetch))
    if basis == 'next_open': return run_research_validation(db, request, **kwargs)
    return run_signal_close_validation(db, request, **kwargs,
        raw_price_loader=lambda db, **kw: prices.fetch(kw['symbols'], start_date=kw['start_date'], end_date=kw['end_date']))


def remove_basis(db, basis):
    for row in db.scalars(select(Result).where(Result.validation_version == version(basis))): db.delete(row)
    db.commit()


def assert_rejected_without_partial_writes(db, call, expected_reason, winning):
    with pytest.raises(ValueError, match=expected_reason): call()
    # Even a caller that catches the error and commits cannot save a prefix.
    db.commit()
    db.expire_all()
    assert snapshots(db) == winning()


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('missing', [False, True])
def test_late_success_or_failure_cannot_replace_a_result_accepted_during_collection(db, basis, existing, missing):
    cal = seed_basis(db, basis)
    winner = deepcopy(result_snapshot(db.scalar(select(Result).where(
        Result.validation_version == version(basis), Result.window_days == 60))))
    winner['outcome']['winning_request'] = 'completed during source IO'
    if not existing: remove_basis(db, basis)
    db.commit()
    accepted = {}
    def win():
        with Session(db.get_bind()) as other:
            upsert_forward_validation_results(other, [winner])
            other.commit()
            accepted.update(snapshots(other))
    assert_rejected_without_partial_writes(db,
        lambda: execute(db, basis, cal, during_fetch=win, missing=missing),
        'validation_results_changed', lambda: accepted)


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
@pytest.mark.parametrize('change', ['input_without_result', 'input_with_result', 'origin', 'canonical'])
def test_acceptance_rechecks_complete_candidate_and_canonical_origin(db, basis, change):
    cal = seed_basis(db, basis)
    cid = db.scalar(select(Result.candidate_id))
    if change == 'input_without_result': remove_basis(db, basis)
    db.commit()
    before = snapshots(db)
    db.rollback()
    def mutate():
        with Session(db.get_bind()) as other:
            c = other.get(DailyRadarCandidate, cid)
            if change.startswith('input'):
                c.input_snapshot = c.input_snapshot | {'ohlcv': {'close': 101}}
            elif change == 'origin':
                add_candidate(other, add_run(other, SIGNAL-timedelta(days=7)), symbol=c.symbol)
            else:
                add_candidate(other, add_run(other, SIGNAL), symbol=c.symbol)
            other.commit()
    assert_rejected_without_partial_writes(db,
        lambda: execute(db, basis, cal, during_fetch=mutate),
        'validation_inputs_changed', lambda: before)


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
@pytest.mark.parametrize('change', ['delete', 'recreate', 'newer_cutoff'])
def test_result_preimage_and_newer_cutoff_are_preserved(db, basis, change):
    cal = seed_basis(db, basis)
    if change == 'newer_cutoff':
        for row in db.scalars(select(Result).where(Result.validation_version == version(basis))):
            row.evaluation_as_of_date = DAY+timedelta(days=1)
        db.commit()
    accepted = {}
    def mutate():
        with Session(db.get_bind()) as other:
            row = other.scalar(select(Result).where(Result.validation_version == version(basis), Result.window_days == 60))
            if change in ('delete', 'recreate'):
                values = result_snapshot(row)
                other.delete(row)
                other.flush()
                if change == 'recreate':
                    # SQLite may reuse an id and second-resolution timestamp.
                    # A replacement with a distinct preimage must be preserved.
                    values['outcome']['replacement_owner'] = 'new request'
                    upsert_forward_validation_results(other, [values])
            other.commit()
            accepted.update(snapshots(other))
    expected = 'newer_validation_result_preserved' if change == 'newer_cutoff' else 'validation_results_changed'
    assert_rejected_without_partial_writes(db,
        lambda: execute(db, basis, cal, during_fetch=mutate), expected, lambda: accepted)


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_unchanged_range_recomputation_still_accepts_every_full_window(db, basis):
    cal = seed_basis(db, basis)
    for row in db.scalars(select(Result).where(Result.validation_version == version(basis))):
        row.outcome = row.outcome | {'forward_return_pct': 999}
    db.commit()
    for _ in range(2):
        result = execute(db, basis, cal)
        db.commit()
        body = result if isinstance(result, dict) else result.model_dump(mode='json')
        assert body['status'] == 'completed'
        assert body['records_written'] == 5 and body['validated_count'] == 5
    rows = list(db.scalars(select(Result).where(Result.validation_version == version(basis))))
    assert {row.window_days for row in rows} == set(WINDOWS)
    assert all(row.status == 'validated' and row.evaluation_as_of_date == DAY for row in rows)
    assert all(row.outcome['forward_return_pct'] != 999 for row in rows)


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_concurrent_legacy_acceptance_has_one_winner_and_no_lost_update(pg_engine, basis):
    from ai_stock_sentinel.db.models import Base
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as seed:
        cal = seed_basis(seed, basis)
    barrier = Barrier(2)
    def collect_and_accept():
        with Session(pg_engine) as session:
            try:
                result = execute(session, basis, cal, during_fetch=lambda: barrier.wait(timeout=30))
                session.commit()
                return 'accepted'
            except ValueError as exc:
                session.rollback()
                return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: collect_and_accept(), range(2)))
    assert sorted(results) == ['accepted', 'validation_results_changed']
    with Session(pg_engine) as session:
        rows = list(session.scalars(select(Result).where(Result.validation_version == version(basis))))
        assert len(rows) == 5 and all(r.status == 'validated' for r in rows)


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
@pytest.mark.parametrize('change', ['input', 'result', 'newer_cutoff'])
def test_endpoint_exposes_obsolete_work_as_409_without_partial_writes(db, monkeypatch, basis, change):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ai_stock_sentinel.calibration.price_provider import get_forward_price_provider, get_research_price_provider
    from ai_stock_sentinel.daily_radar import maintenance_router
    from ai_stock_sentinel.daily_radar.trading_calendar import get_trading_calendar_provider
    from ai_stock_sentinel.db.session import get_db
    from test_radar_validation_acceptance import request
    cal = seed_basis(db, basis)
    if change == 'newer_cutoff':
        for row in db.scalars(select(Result).where(Result.validation_version == version(basis))):
            row.evaluation_as_of_date = DAY+timedelta(days=1)
        db.commit()
    accepted = {}
    def mutate(**kwargs):
        with Session(db.get_bind()) as other:
            row = other.scalar(select(Result).where(Result.validation_version == version(basis), Result.window_days == 60))
            if change == 'input':
                candidate = other.get(DailyRadarCandidate, row.candidate_id)
                candidate.input_snapshot = candidate.input_snapshot | {'ohlcv': {'close': 101}}
            elif change == 'result':
                row.outcome = row.outcome | {'winning_request': 'new request'}
            other.commit()
            accepted.update(snapshots(other))
        return cal
    prices = Prices(cal)
    app = FastAPI()
    app.include_router(maintenance_router.router)
    app.dependency_overrides[get_db] = lambda: db
    for dependency in (get_forward_price_provider, get_research_price_provider):
        app.dependency_overrides[dependency] = lambda: prices
    app.dependency_overrides[get_trading_calendar_provider] = lambda: SimpleNamespace(fetch=mutate)
    monkeypatch.setenv('DAILY_RADAR_INTERNAL_TOKEN', 'fencing-test')
    monkeypatch.setattr(maintenance_router, 'load_price_series_from_raw_data',
        lambda db, **kw: prices.fetch(kw['symbols'], start_date=kw['start_date'], end_date=kw['end_date']))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/internal/daily-radar/forward-validation/run',
            json=request(basis).model_dump(mode='json'), headers={'Authorization': 'Bearer fencing-test'})
    reason = {'input': 'validation_inputs_changed', 'result': 'validation_results_changed',
        'newer_cutoff': 'newer_validation_result_preserved'}[change]
    assert response.status_code == 409, response.text
    assert response.json()['detail'] == {'code': reason}
    db.commit()
    db.expire_all()
    assert snapshots(db) == accepted


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_zero_evaluation_batch_still_rechecks_the_reported_candidate_inputs(db, basis):
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
    from ai_stock_sentinel.daily_radar.validation_preparation import (
        capture_validation_acceptance, accept_validation_results,
    )
    cal = seed_basis(db, basis)
    candidates = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)
    acceptance = capture_validation_acceptance(db, candidates, market='TW', windows=WINDOWS,
        validation_version=version(basis))
    db.rollback()
    before = snapshots(db)
    db.rollback()
    with Session(db.get_bind()) as other:
        c = other.get(DailyRadarCandidate, candidates[0]['candidate_id'])
        c.input_snapshot = c.input_snapshot | {'ohlcv': {'close': 101}}
        other.commit()
    assert_rejected_without_partial_writes(db,
        lambda: accept_validation_results(db, [], calendar=cal, acceptance=acceptance),
        'validation_inputs_changed', lambda: before)


@pytest.mark.parametrize('change', ['input', 'result'])
def test_scoped_official_recovery_still_rejects_changed_inputs_or_results(scenario, change):
    from ai_stock_sentinel.daily_radar.trading_calendar import get_trading_calendar_provider
    engine, run, *_ = scenario
    client = run.__closure__[0].cell_contents
    provider = client.app.dependency_overrides[get_trading_calendar_provider]()
    accepted = {}
    def mutate(**kwargs):
        with Session(engine) as other:
            row = other.scalar(select(Result).join(DailyRadarCandidate).where(
                DailyRadarCandidate.symbol == '1727.TW', Result.window_days == 20))
            if change == 'input': row.candidate.observation_score += 1
            else: row.outcome = row.outcome | {'new_owner': 'accepted during collection'}
            other.commit()
            accepted.update(snapshots(other))
        return provider.fetch(**kwargs)
    client.app.dependency_overrides[get_trading_calendar_provider] = lambda: SimpleNamespace(fetch=mutate)
    response = run()
    assert response.status_code == 409, response.text
    assert response.json()['detail'] == {'code': 'validation_inputs_changed' if change == 'input'
        else 'validation_results_changed'}
    with Session(engine) as session:
        assert snapshots(session) == accepted


@pytest.mark.parametrize('drift', [None, 'candidate', 'window', 'version', 'status', 'signal',
    'benchmark', 'target', 'expected_target', 'proof', 'evidence_version', 'event_type', 'event_symbol'])
def test_original_conflict_exception_requires_the_same_identity_and_official_evidence(scenario, drift):
    from ai_stock_sentinel.daily_radar.forward_validation import is_original_rights_conflict_recovery
    engine, run, *_ = scenario
    def row_query():
        return select(Result).join(DailyRadarCandidate).where(
            DailyRadarCandidate.symbol == '1727.TW', Result.window_days == 20)
    with Session(engine) as session:
        row = session.scalar(row_query())
        original = SimpleNamespace(**{field: deepcopy(getattr(row, field)) for field in (
            'candidate_id', 'window_days', 'validation_version', 'status', 'signal_date',
            'benchmark_symbol', 'evaluation_as_of_date', 'skip_reason', 'outcome')})
    response = run()
    assert response.status_code == 200, response.text
    with Session(engine) as session:
        outcome = result_snapshot(session.scalar(row_query())) | {'symbol': '1727.TW'}
    if drift == 'candidate': outcome['candidate_id'] += 1
    elif drift == 'window': outcome['window_days'] += 1
    elif drift == 'version': original.validation_version = 'daily-radar-next-open-price-v1'
    elif drift == 'status': original.status = 'validated'
    elif drift == 'signal': outcome['signal_date'] = str(original.signal_date-timedelta(days=1))
    elif drift == 'benchmark': outcome['benchmark_symbol'] = 'SPX'
    elif drift == 'target': outcome['target_date'] = str(original.evaluation_as_of_date)
    elif drift == 'expected_target': original.outcome['expected_target_date'] = str(original.signal_date)
    elif drift == 'proof': outcome['outcome'].pop('official_rights_recovery')
    elif drift == 'evidence_version': outcome['outcome']['official_rights_recovery']['evidence_version'] = 'unknown'
    elif drift == 'event_type': outcome['outcome']['official_rights_recovery']['event']['type'] = 'cash_dividend'
    elif drift == 'event_symbol': outcome['outcome']['official_rights_recovery']['event']['symbol'] = 'OTHER.TW'
    assert is_original_rights_conflict_recovery(original, outcome) is (drift is None)


def test_postgres_cli_cannot_insert_a_missing_identity_after_api_acceptance_check(pg_engine, monkeypatch, tmp_path):
    from threading import Event, current_thread
    from sqlalchemy import event
    from ai_stock_sentinel.db.models import Base
    from scripts import daily_radar_forward_validation as cli
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as seed:
        cal = seed_basis(seed, 'signal_close')
        seed.delete(seed.scalar(select(Result).where(Result.validation_version == FORWARD_VALIDATION_VERSION,
            Result.window_days == 60)))
        seed.commit()
    prices = Prices(cal)
    monkeypatch.setattr(cli, '_get_session_local', lambda: lambda: Session(pg_engine))
    monkeypatch.setattr(cli, 'load_price_series_from_raw_data',
        lambda db, **kw: prices.fetch(kw['symbols'], start_date=kw['start_date'], end_date=kw['end_date']))
    output = tmp_path/'cli-report.json'
    monkeypatch.setattr('sys.argv', ['daily_radar_forward_validation.py', '--source', 'db',
        '--start-date', str(SIGNAL), '--end-date', str(SIGNAL), '--as-of-date', str(DAY),
        '--windows', '60', '--write-results', '--output', str(output)])
    attempted = Event()
    state = {'paused': False}
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='radar-cli-race') as pool:
        def cli_write():
            try:
                cli.main()
                return 'accepted'
            except ValueError as exc:
                return str(exc)
        def before(conn, cursor, sql, params, context, many):
            if current_thread().name.startswith('radar-cli-race'):
                normalized = sql.upper()
                if 'LOCK TABLE DAILY_RADAR_RUNS' in normalized:
                    state['attempt'] = 'reservation'
                    attempted.set()
                elif normalized.lstrip().startswith('INSERT INTO DAILY_RADAR_FORWARD_VALIDATION_RESULTS'):
                    state['attempt'] = 'insert'
                    attempted.set()
        def after(conn, cursor, sql, params, context, many):
            if current_thread().name.startswith('radar-cli-race') or state['paused']:
                return
            normalized = sql.upper()
            if 'FOR UPDATE' not in normalized or 'ORDER BY DAILY_RADAR_FORWARD_VALIDATION_RESULTS.CANDIDATE_ID' not in normalized:
                return
            state['paused'] = True
            state['future'] = pool.submit(cli_write)
            assert attempted.wait(timeout=30)
            if state['attempt'] == 'insert':
                # Old CLI commits while the missing identity is still invisible
                # to this acceptance SELECT. Force that interleaving, no sleep.
                assert state['future'].result(timeout=30) == 'accepted'
        event.listen(pg_engine, 'before_cursor_execute', before)
        event.listen(pg_engine, 'after_cursor_execute', after)
        try:
            with Session(pg_engine) as session:
                result = execute(session, 'signal_close', cal)
                session.commit()
                assert result.records_written == 5
            assert state['future'].result(timeout=30) == 'validation_results_changed'
        finally:
            event.remove(pg_engine, 'before_cursor_execute', before)
            event.remove(pg_engine, 'after_cursor_execute', after)
    assert state['attempt'] == 'reservation'
    assert not output.exists()
    with Session(pg_engine) as session:
        rows = list(session.scalars(select(Result).where(Result.validation_version == FORWARD_VALIDATION_VERSION)))
        assert len(rows) == 5 and all(r.status == 'validated' for r in rows)


@pytest.mark.parametrize('write', [False, True])
def test_db_cli_preserves_read_only_mode_and_commits_unchanged_recomputation(db, monkeypatch, tmp_path, write):
    from scripts import daily_radar_forward_validation as cli
    cal = seed_basis(db, 'signal_close')
    for row in db.scalars(select(Result).where(Result.validation_version == FORWARD_VALIDATION_VERSION)):
        row.outcome = row.outcome | {'forward_return_pct': 999}
    db.commit()
    prices = Prices(cal)
    monkeypatch.setattr(cli, '_get_session_local', lambda: lambda: Session(db.get_bind()))
    monkeypatch.setattr(cli, 'load_price_series_from_raw_data',
        lambda db, **kw: prices.fetch(kw['symbols'], start_date=kw['start_date'], end_date=kw['end_date']))
    output = tmp_path/'report.json'
    argv = ['daily_radar_forward_validation.py', '--source', 'db', '--start-date', str(SIGNAL),
        '--end-date', str(SIGNAL), '--as-of-date', str(DAY), '--windows', ','.join(map(str, WINDOWS)),
        '--output', str(output)]
    monkeypatch.setattr('sys.argv', argv+(['--write-results'] if write else []))
    cli.main()
    assert output.is_file()
    with Session(db.get_bind()) as fresh:
        rows = list(fresh.scalars(select(Result).where(Result.validation_version == FORWARD_VALIDATION_VERSION)))
        assert {r.window_days for r in rows} == set(WINDOWS)
        assert all((r.outcome['forward_return_pct'] != 999) is write for r in rows)
