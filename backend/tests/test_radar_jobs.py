from datetime import date, datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from test_radar_work_queue import jsonb
from ai_stock_sentinel.db.models import Base, RadarJob, RadarWorkItem, RadarJobWork
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
