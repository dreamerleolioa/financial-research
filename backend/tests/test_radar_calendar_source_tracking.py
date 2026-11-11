from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.daily_radar import work_executor
from ai_stock_sentinel.daily_radar.jobs import create_job
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore, fingerprint
from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
from ai_stock_sentinel.daily_radar.work_queue import ensure_work
from ai_stock_sentinel.db.models import Base, DailyRadarPreparedRun, RadarSourceSnapshot, RadarWorkItem, RadarWorkAttempt
from test_radar_job_postgres import pg_engine

DAY = date(2026, 6, 8)
NOW = datetime(2026, 6, 9, 2, tzinfo=timezone.utc)


@pytest.fixture
def local(pg_engine, monkeypatch):
    Base.metadata.create_all(pg_engine)
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED', 'true')
    calls = []
    def request(provider, getter, url, *, params):
        calls.append(params['date'])
        month = date.fromisoformat(params['date'][:4]+'-'+params['date'][4:6]+'-01')
        days = [month+timedelta(days=i) for i in range(monthrange(month.year, month.month)[1])
                if (month+timedelta(days=i)).weekday() < 5]
        return {'stat': 'OK', 'date': params['date'], 'fields': ['日期'],
                'data': [[f'{d.year-1911}/{d.month:02}/{d.day:02}'] for d in days]}
    monkeypatch.setattr(RecoveringResearchPriceProvider, '_request', request)
    with Session(pg_engine) as db:
        row = DailyRadarPreparedRun(run_date=DAY, market='TW', selected_symbols=[], universe=[],
            symbol_count=0, step_statuses={})
        db.add(row); db.commit()
        job = create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
        market = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'market-session'))
        market.state = 'ready'; db.commit()
        yield db, job, row, calls


def owner(db):
    return db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'calendar'))


def test_new_calendar_tracks_every_month_for_executor_fences(local):
    db, job, prepared, calls = local
    work_executor.advance_job(db, job.id, now=NOW)
    work = owner(db)
    assert work.state == 'ready'
    reports = prepared.step_statuses['validation-calendar']['reports']
    assert len(reports) == len(calls) == 13
    assert len(work.result['source_observations']) == 13
    assert work.result['source_captured_count'] == 13
    assert {p['snapshot_sha256'] for p in work.result['source_observations']} == {
        p['snapshot_sha256'] for p in reports}


def test_month_revision_changed_after_collection_is_not_accepted(local, monkeypatch):
    db, job, prepared, calls = local
    class RevisedSource(WorkHandlers):
        def collect(self, claim):
            result = super().collect(claim)
            with Session(self.store.bind) as reader:
                latest = reader.scalar(select(RadarSourceSnapshot).order_by(RadarSourceSnapshot.id.desc()))
                request, payload = latest.request, dict(latest.payload)
            payload['data'] = payload['data'][:1] + payload['data'][2:]
            SourceSnapshotStore(self.store.bind).record(request, payload)
            return result
    monkeypatch.setattr(work_executor, 'WorkHandlers', RevisedSource)
    work_executor.advance_job(db, job.id, now=NOW)
    assert len(calls) == 13
    assert owner(db).state == 'waiting_provider'
    assert owner(db).reason == 'radar_source_lease_lost'
    assert 'validation-calendar' not in prepared.step_statuses
    result=owner(db).result
    assert len(result['source_observations'])==13
    assert result['source_captured_count']==13
    assert isinstance(result['source_dependencies'],dict)
    rejection=result['source_rejection']
    assert rejection['reason']=='radar_source_revision_changed'
    expected=next(p for p in result['source_observations'] if p['request_hash']==rejection['request_hash'])
    latest=db.scalar(select(RadarSourceSnapshot).where(RadarSourceSnapshot.request_hash==rejection['request_hash'])
        .order_by(RadarSourceSnapshot.captured_at.desc(),RadarSourceSnapshot.id.desc()).limit(1))
    assert rejection['expected_snapshot_sha256']==expected['snapshot_sha256']
    assert rejection['actual_snapshot_sha256']==latest.payload_hash!=rejection['expected_snapshot_sha256']
    attempt=db.scalar(select(RadarWorkAttempt).where(RadarWorkAttempt.work_id==owner(db).id))
    assert attempt.result==result


def test_cached_months_still_record_observations_without_new_requests(local):
    db, job, prepared, calls = local
    work_executor.advance_job(db, job.id, now=NOW)
    work = owner(db)
    prepared.step_statuses = {}
    work.state, work.result, work.retry_at = 'pending', {}, NOW
    db.commit()
    work_executor.advance_job(db, job.id, now=NOW)
    assert work.state == 'ready' and len(calls) == 13
    assert len(work.result['source_observations']) == 13
    assert work.result['source_captured_count'] == 0


def test_calendar_preserves_busy_provider_dependency_and_makes_no_request(local):
    db, job, prepared, calls = local
    later = datetime.now(timezone.utc) + timedelta(minutes=10)
    gate = ensure_work(db, identity={'kind': 'provider_gate', 'provider': 'twse'},
        stage='provider-gate', lane='source', run_date=None, scope={'provider': 'twse'},
        input_revision=fingerprint({'provider': 'twse'}), state='waiting_provider', retry_at=later)
    gate_key = gate.work_key
    db.commit()
    work_executor.advance_job(db, job.id, now=NOW)
    work = owner(db)
    assert work.state == 'waiting_provider'
    assert not calls and 'validation-calendar' not in prepared.step_statuses
    assert gate_key in work.result['source_dependencies']
    assert len(work.result['source_dependencies']) == 2
