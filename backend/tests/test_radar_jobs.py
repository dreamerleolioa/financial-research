from datetime import date, datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import Session
from test_radar_work_queue import jsonb
from ai_stock_sentinel.db.models import Base, DailyRadarPreparedRun, RadarJob, RadarWorkItem, RadarJobWork
from ai_stock_sentinel.daily_radar.work_queue import claim_work, finish_work
from ai_stock_sentinel.daily_radar.jobs import create_job, reconcile_job, job_status, due_jobs, stage_time

NOW = datetime(2026, 11, 7, 2, tzinfo=timezone.utc)

@pytest.fixture
def db(tmp_path):
    engine = create_engine('sqlite+pysqlite:///' + str(tmp_path / 'jobs.sqlite'))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_idempotent_intent_and_cross_midnight_resume_keep_date(db):
    job = create_job(db, run_date=date(2026, 11, 6), now=NOW)
    again = create_job(db, run_date=date(2026, 11, 6), now=NOW + timedelta(days=2))
    assert again.id == job.id
    assert due_jobs(db, now=NOW + timedelta(days=2))[0]['run_date'] == '2026-11-06'
    assert job_status(db, job.id)['data_ready'] is False
    assert db.scalar(select(func.count()).select_from(RadarJob)) == 1


def test_publication_reconcile_reads_existing_queue_in_batches(db):
    day = date(2026, 11, 6)
    symbols = [f'{code}.TW' for code in range(1000, 1221)]
    db.add(DailyRadarPreparedRun(run_date=day, market='TW', selected_symbols=symbols,
                                  universe=[], symbol_count=len(symbols)))
    db.commit()
    job_id = create_job(db, run_date=day, lanes=['publication'], now=NOW).id
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement.upper().split())

    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', record)
    try:
        reconcile_job(db, job_id, now=NOW)
    finally:
        event.remove(engine, 'before_cursor_execute', record)

    queue_writes = [sql for sql in statements if sql[0] in {'INSERT', 'UPDATE', 'DELETE'}
                    and {'RADAR_WORK_ITEMS', 'RADAR_JOB_WORK'}.intersection(sql[:3])]
    assert not queue_writes, f'Existing queue issued {len(queue_writes)} writes'
    assert len(statements) < 80
    items = db.scalars(select(RadarWorkItem).order_by(RadarWorkItem.id)).all()
    assert len(items) == 671
    scoring = next(item for item in items if item.stage == 'scoring')
    inputs = [item for item in items if item.stage in {'lending', 'full-margin', 'ohlcv', 'market-context'}]
    assert scoring.dependencies == [item.work_key for item in inputs]
    assert [item.scope['symbol'] for item in inputs if item.stage == 'ohlcv'] == symbols


def test_publication_reconcile_repairs_missing_and_changed_memberships(db):
    day = date(2026, 11, 6)
    symbols = ['2330.TW', '2317.TW']
    db.add(DailyRadarPreparedRun(run_date=day, market='TW', selected_symbols=symbols,
                                  universe=[], symbol_count=len(symbols)))
    db.commit()
    job_id = create_job(db, run_date=day, lanes=['publication'], now=NOW).id
    items = db.scalars(select(RadarWorkItem).order_by(RadarWorkItem.id)).all()
    for item in items:
        if item.stage in {'market-session', 'institutional', 'market-bars', 'prepare'}:
            item.state, item.retry_at = 'ready', None
    db.commit()
    reconcile_job(db, job_id, now=NOW)
    first, second = [item for item in items if item.stage == 'ohlcv']
    claim = claim_work(db, work_id=first.id, now=NOW)
    assert claim is not None
    membership = db.get(RadarJobWork, (job_id, first.id))
    membership.active, membership.required = False, False
    db.delete(db.get(RadarJobWork, (job_id, second.id)))
    db.commit()

    reconcile_job(db, job_id, now=NOW)

    for item in (first, second):
        membership = db.get(RadarJobWork, (job_id, item.id))
        assert membership.active and membership.required
    assert first.state == 'running' and first.lease_token == claim.token and first.attempt_count == 1
    assert second.state == 'pending'
    assert db.scalar(select(func.count()).select_from(RadarWorkItem)) == len(items)


def test_stage_times_preserve_next_day_scoring_gate():
    day = date(2026, 11, 6)
    assert stage_time(day, 'scoring') == datetime(2026, 11, 6, 16, 30, tzinfo=timezone.utc)
    assert stage_time(day, 'ohlcv') < stage_time(day, 'scoring')


def test_closed_proof_ends_publication_without_signals_but_keeps_validation_lanes(db):
    job = create_job(db, run_date=date(2026, 11, 6), now=NOW)
    claim = claim_work(db, job_id=job.id, now=NOW)
    assert claim.stage == 'market-session'
    finish_work(db, claim, state='terminal', result={'status': 'closed', 'run_date': '2026-11-06',
        'market': 'TW', 'provider': 'twse', 'dataset': 'MI_INDEX'}, now=NOW)
    db.commit()
    reconcile_job(db, job.id, now=NOW)
    status = job_status(db, job.id)
    assert status['publication']['state'] == 'closed'
    assert status['data_ready'] is False
    assert db.scalar(select(func.count()).select_from(RadarWorkItem).where(RadarWorkItem.stage == 'scoring')) == 0
    assert db.scalar(select(func.count()).select_from(RadarWorkItem).where(RadarWorkItem.stage == 'calendar')) == 1


def test_status_never_exposes_managed_symbols_or_raw_work_scope(db):
    job = create_job(db, run_date=date(2026, 11, 6), now=NOW)
    job.scope = dict(job.scope) | {'managed_symbols': ['PRIVATE.TW']}
    db.commit()
    assert 'PRIVATE' not in str(job_status(db, job.id))


def test_prepare_discovers_bounded_historical_market_gap_and_waits_for_its_unit(db):
    from ai_stock_sentinel.daily_radar.jobs import unit
    job=create_job(db,run_date=date(2026,11,6),lanes=['publication'],now=NOW)
    units=db.scalars(select(RadarWorkItem)).all()
    for work in units:
        if work.stage in {'market-session','institutional','market-bars'}:
            work.state='ready'
    prep=next(w for w in units if w.stage=='prepare')
    prep.state='waiting_dependency';prep.result={'archive_gaps':{'2026-11-05':['TWO']}}
    db.commit();reconcile_job(db,job.id,now=NOW)
    gap=db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='market-bars',RadarWorkItem.scope['trade_date'].as_string()=='2026-11-05'))
    assert gap.scope['market']=='TWO' and gap.run_date==date(2026,11,6)
    assert gap.work_key in prep.dependencies and prep.state=='waiting_dependency'
    gap.state='ready';db.commit();reconcile_job(db,job.id,now=NOW)
    assert prep.state=='pending'


@pytest.mark.parametrize('newer_first',[False,True])
def test_window_sharing_and_explicit_backdated_scope(db,newer_first):
    from datetime import timedelta
    from test_daily_radar_forward_validation import _add_run,_add_candidate
    from radar_calendar_support import calendar
    from ai_stock_sentinel.db.models import DailyRadarPreparedRun
    _add_candidate(db,_add_run(db))
    for day in (date(2026,6,8),date(2026,6,9)):
        db.add(DailyRadarPreparedRun(run_date=day,market='TW',status='prepared',selected_symbols=[],symbol_count=0,
            universe=[],errors=[],step_statuses={'validation-calendar':calendar(day-timedelta(days=364),day).payload()}))
    db.commit()
    days=[date(2026,6,8),date(2026,6,9)]
    if newer_first:days.reverse()
    for day in days:create_job(db,run_date=day,lanes=['signal_close'],now=NOW)
    windows=db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage=='window')).all()
    assert len(windows)==(2 if newer_first else 1)
    assert {w.scope['as_of_date'] for w in windows}==({'2026-06-08','2026-06-09'} if newer_first else {'2026-06-08'})


def test_existing_price_conflict_is_not_recalculated_for_each_daily_job(db, monkeypatch):
    from datetime import timedelta
    from test_daily_radar_forward_validation import _add_run, _add_candidate
    from radar_calendar_support import calendar
    from ai_stock_sentinel.db.models import DailyRadarPreparedRun
    from ai_stock_sentinel.daily_radar import readiness

    candidate = _add_candidate(db, _add_run(db))
    candidate_id = candidate.id
    gap = {
        'candidate_id': candidate_id, 'candidate_revision': 'candidate-revision',
        'calendar_revision': 'calendar-revision', 'return_basis': 'next_open',
        'validation_version': 'daily-radar-next-open-price-v2', 'window_days': 5,
        'expected_target_date': '2026-06-08', 'state': 'retryable',
        'reason': 'conflicting_price_records', 'next_action': 'needs_review',
    }
    monkeypatch.setattr(readiness, 'inventory_items', lambda *args, **kwargs: [gap])
    for day in (date(2026, 6, 8), date(2026, 6, 9)):
        db.add(DailyRadarPreparedRun(run_date=day, market='TW', status='prepared',
            selected_symbols=[], symbol_count=0, universe=[], errors=[],
            step_statuses={'validation-calendar': calendar(day-timedelta(days=364), day).payload()}))
        db.commit()
        create_job(db, run_date=day, lanes=['next_open'], now=NOW)
        if day == date(2026, 6, 8):
            initial = db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage == 'window')).one()
            claim = claim_work(db, work_id=initial.id, now=NOW)
            assert claim is not None
            finish_work(db, claim, state='needs_review', reason=gap['reason'], now=NOW)
            db.commit()
    work = db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage == 'window')).one()
    assert work.state == 'needs_review' and work.attempt_count == 1
    assert db.scalar(select(func.count()).select_from(RadarJobWork).where(
        RadarJobWork.work_id == work.id, RadarJobWork.active.is_(True))) == 2
    assert claim_work(db, work_id=work.id, now=NOW + timedelta(days=1)) is None

    work.state, work.reason, work.retry_at = 'pending', 'source_revision_changed', NOW
    db.commit()
    day = date(2026, 6, 10)
    db.add(DailyRadarPreparedRun(run_date=day, market='TW', status='prepared',
        selected_symbols=[], symbol_count=0, universe=[], errors=[],
        step_statuses={'validation-calendar': calendar(day-timedelta(days=364), day).payload()}))
    db.commit()
    create_job(db, run_date=day, lanes=['next_open'], now=NOW)
    assert claim_work(db, work_id=work.id, now=NOW).work_id == work.id


def test_cached_technical_conflict_retains_the_original_diagnostic(db):
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.raw_data import RawPayloadCollection
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers

    symbol = '3706.TW'
    day = date(2026, 11, 26)
    claim = SimpleNamespace(scope={'symbol': symbol}, run_date=day)
    diagnostic = {'conflicting_ohlc': {'provider': {'close': 59.6002}, 'official': {'close': 60.10}}}
    first = SimpleNamespace(diagnostics={symbol: diagnostic}, fetch=lambda *a, **kw: {})
    second = SimpleNamespace(diagnostics={}, fetch=lambda *a, **kw: pytest.fail('expected a cache hit'))
    for fetcher in (first, second):
        collection = RawPayloadCollection(day, (symbol,), fetcher)
        assert WorkHandlers(db)._capture_technical_payloads(collection, claim) == ({}, {symbol: diagnostic})
