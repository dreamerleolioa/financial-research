from datetime import date, timedelta

import pytest
from sqlalchemy import select

from ai_stock_sentinel.db.models import DailyRadarPreparedRun, RadarWorkItem, TaiwanInstitutionalReportSnapshot
from ai_stock_sentinel.daily_radar.jobs import create_job, reconcile_job
from test_radar_jobs import db, NOW
from test_radar_job_postgres import pg_engine


def blocked_scoring(db, state='waiting_provider'):
    row = DailyRadarPreparedRun(run_date=date(2026, 11, 6), market='TW',
        selected_symbols=['2330.TW', '2317.TW'], symbol_count=2, universe=[],
        market_context={'probe': True}, step_statuses={
            **{s: {'status': 'completed'} for s in ('prepare-universe', 'refresh-institutional-flows', 'refresh-ohlcv',
                'refresh-lending', 'refresh-full-margin', 'refresh-market-context')},
            'market-session': {'status': 'open', 'run_date': '2026-11-06', 'market': 'TW',
                               'provider': 'twse', 'dataset': 'MI_INDEX'}})
    db.add(row); db.commit()
    for market in ('TW', 'TWO'):
        db.add(TaiwanInstitutionalReportSnapshot(market=market, trade_date=row.run_date,
            dataset='taiwan_institutional_flow', source_provider='fixture', source_dataset='fixture',
            status='completed', row_count=1, payload_hash='a'*64))
    db.commit()
    job = create_job(db, run_date=row.run_date, lanes=['publication'], now=NOW)
    for w in db.scalars(select(RadarWorkItem)):
        w.state = state if w.stage == 'scoring' else 'ready'
        if w.stage == 'scoring': w.reason = 'daily_radar_raw_data_incomplete'
    db.commit()
    return job


@pytest.mark.parametrize('state', ['waiting_provider', 'needs_review', 'failed_internal'])
def test_scoring_failure_does_not_prevent_actual_prerequisite_audit(db, state):
    job = blocked_scoring(db, state)
    reconcile_job(db, job.id, now=NOW, audit=True)
    rows = db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage == 'ohlcv')).all()
    assert all(w.state == 'pending' for w in rows)
    scoring = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'scoring'))
    assert scoring.state == 'waiting_dependency'
    assert job.scope['intent_complete'] is False


def test_recovery_requeues_only_the_missing_symbol_and_parks_scoring(db, monkeypatch):
    from ai_stock_sentinel.daily_radar import readiness, integrity
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    job = blocked_scoring(db)
    row = db.scalar(select(DailyRadarPreparedRun))
    row.step_statuses = dict(row.step_statuses) | {'refresh-lending': {
        'status': 'partial', 'missing_symbols': ['2330.TW']}}
    db.commit()
    monkeypatch.setattr(readiness, '_require_complete_daily_radar_raw_rows', lambda *a, **k: [])
    monkeypatch.setattr(integrity, 'background_gaps', lambda *a: {'refresh-lending': ['2330.TW']})
    reconcile_job(db, job.id, now=NOW, audit=True)
    for w in db.scalars(select(RadarWorkItem)):
        if w.stage == 'scoring': assert w.state == 'waiting_dependency'
        elif w.stage == 'lending' and w.scope['symbol'] == '2330.TW': assert w.state == 'pending'
        else: assert w.state == 'ready'


def test_live_prerequisite_lease_is_not_reset_by_recovery_audit(db):
    job = blocked_scoring(db)
    work = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'ohlcv'))
    work.state, work.lease_token, work.lease_expires_at = 'running', 'other-owner', NOW + timedelta(minutes=3)
    db.commit()
    reconcile_job(db, job.id, now=NOW, audit=True)
    assert (work.state, work.lease_token) == ('running', 'other-owner')
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'scoring')).state == 'waiting_dependency'


def test_partial_audit_does_not_scan_validation_history(db, monkeypatch):
    from ai_stock_sentinel.daily_radar import readiness
    job = blocked_scoring(db)
    def unexpected(*a, **k): raise AssertionError('full history scanned during prerequisite repair')
    monkeypatch.setattr(readiness, 'validation_inventory', unexpected)
    reconcile_job(db, job.id, now=NOW, audit=True)
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'ohlcv')).state == 'pending'


def test_missing_market_context_is_requeued_even_when_step_is_completed(db, monkeypatch):
    from ai_stock_sentinel.daily_radar import readiness
    job = blocked_scoring(db)
    row = db.scalar(select(DailyRadarPreparedRun)); row.market_context = None; db.commit()
    monkeypatch.setattr(readiness, '_require_complete_daily_radar_raw_rows', lambda *a, **k: [])
    reconcile_job(db, job.id, now=NOW, audit=True)
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'market-context')).state == 'pending'
    assert all(w.state == 'ready' for w in db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage == 'ohlcv')))


def test_dependency_parking_preserves_provider_retry_after(db):
    from ai_stock_sentinel.daily_radar.work_queue import aware
    job = blocked_scoring(db)
    parent = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'prepare'))
    child = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'ohlcv'))
    parent.state = 'pending'; child.state = 'waiting_provider'; child.retry_at = NOW + timedelta(hours=1)
    db.commit(); reconcile_job(db, job.id, now=NOW, audit=True)
    assert child.state == 'waiting_dependency'
    parent.state = 'ready'; db.commit(); reconcile_job(db, job.id, now=NOW)
    assert child.state == 'pending'
    assert aware(child.retry_at) == NOW + timedelta(hours=1)


def test_postgres_recovery_does_not_revoke_a_lease_claimed_after_the_audit_read(pg_engine, monkeypatch):
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import Base
    from ai_stock_sentinel.daily_radar import readiness
    from ai_stock_sentinel.daily_radar.work_queue import claim_work
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        job = blocked_scoring(db)
        work = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'ohlcv'))
        original = readiness.scoring_prerequisite_inputs
        claimed = []
        def audit(*a, **k):
            result = original(*a, **k)
            with Session(pg_engine) as other:
                other.execute(update(RadarWorkItem).where(RadarWorkItem.id == work.id).values(state='pending'))
                claimed.append(claim_work(other, work_id=work.id, now=NOW))
                other.commit()
            return result
        monkeypatch.setattr(readiness, 'scoring_prerequisite_inputs', audit)
        reconcile_job(db, job.id, now=NOW, audit=True)
        db.refresh(work)
        assert (work.state, work.lease_token) == ('running', claimed[0].token)
        assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'scoring')).state == 'waiting_dependency'


def test_failed_scoring_recovers_prerequisites_then_publishes(db, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from ai_stock_sentinel.db.models import DailyRadarRun, StockRawData
    from ai_stock_sentinel.daily_radar import readiness
    from ai_stock_sentinel.daily_radar.work_executor import advance_job
    from ai_stock_sentinel.daily_radar.work_handlers import Collected
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'false')
    job = blocked_scoring(db)
    symbols = {'2330.TW', '2317.TW'}
    def complete(rows, **kwargs):
        missing = sorted(symbols - {r.symbol for r in rows})
        if missing:
            raise HTTPException(409, detail={'code': 'daily_radar_raw_data_incomplete', 'missing_symbols': missing})
        return rows
    monkeypatch.setattr(readiness, '_require_complete_daily_radar_raw_rows', complete)
    monkeypatch.setattr(readiness, 'read_readiness', lambda *a, **k: {'data_ready': True,
        'publication': {'status': 'completed', 'output_complete': True, 'input_manifest': {'matches_current_inputs': True}},
        'scoring': {'ready': True, 'steps': {}, 'blockers': []},
        'validation': {'signal_close': {'ready': True}, 'next_open': {'ready': True}}})
    calls = []
    def collect(claim):
        calls.append((claim.stage, claim.scope.get('symbol')))
        if claim.stage == 'ohlcv':
            return Collected(lambda db: (db.add(StockRawData(symbol=claim.scope['symbol'],
                record_date=job.run_date, raw_data_is_final=True)) or {'state': 'ready'}))
        assert claim.stage == 'scoring'
        complete(db.scalars(select(StockRawData)).all())
        def publish(db):
            db.add(DailyRadarRun(run_date=job.run_date, market='TW', status='completed', candidate_count=0))
            return {'state': 'ready'}
        return Collected(publish)
    for i in range(4):
        result = advance_job(db, job.id, handlers=SimpleNamespace(collect=collect), now=NOW+timedelta(seconds=i))
    assert result['intent_complete'] is True
    assert [stage for stage, _ in calls] == ['scoring', 'ohlcv', 'ohlcv', 'scoring']
    assert {symbol for stage, symbol in calls if stage == 'ohlcv'} == symbols
    assert len(db.scalars(select(DailyRadarRun)).all()) == 1


def test_real_handlers_persist_recollected_open_proof_before_reusing_prepare(db, monkeypatch):
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_executor import advance_job
    from ai_stock_sentinel.daily_radar.market_session import TwseMarketSessionProvider, MarketSessionResult
    from ai_stock_sentinel.daily_radar import readiness, pipeline_support
    job = blocked_scoring(db)
    row = db.scalar(select(DailyRadarPreparedRun))
    row.step_statuses = dict(row.step_statuses) | {'market-session': {}}
    db.commit()
    monkeypatch.setattr(readiness, '_require_complete_daily_radar_raw_rows', lambda *a, **k: [])
    monkeypatch.setattr(TwseMarketSessionProvider, 'resolve', lambda self, **k:
        MarketSessionResult('open', job.run_date, 'TW', 'twse', 'MI_INDEX'))
    monkeypatch.setattr(pipeline_support, '_required_institutional_archive_details', lambda *a, **k: {})
    reconcile_job(db, job.id, now=NOW, audit=True)
    for _ in range(2): advance_job(db, job.id, handlers=WorkHandlers(db), now=NOW)
    db.refresh(row)
    assert row.step_statuses['market-session']['status'] == 'open'
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'prepare')).state == 'ready'
    assert row.selected_symbols == ['2330.TW', '2317.TW']


@pytest.mark.parametrize('gap_state', ['ready', 'pending'])
def test_later_job_attaches_retained_shared_dependency_closure(db, gap_state):
    from ai_stock_sentinel.daily_radar.jobs import unit
    from ai_stock_sentinel.daily_radar.work_queue import claim_work
    from ai_stock_sentinel.db.models import RadarJobWork
    original = blocked_scoring(db)
    prep = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'prepare'))
    session = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'market-session'))
    gap = unit(db, original, 'market-bars', scope={'market': 'TWO', 'trade_date': '2026-11-05'}, deps=[session], now=NOW)
    gap.state = gap_state
    prep.dependencies = list(prep.dependencies) + [gap.work_key]
    prep.result = {'reused': True}  # Successful completion no longer carries archive_gaps.
    db.commit()
    later = create_job(db, run_date=original.run_date, lanes=['publication', 'signal_close'], now=NOW)
    row = db.scalar(select(DailyRadarPreparedRun)); db.delete(row); db.commit()
    reconcile_job(db, later.id, now=NOW, audit=True)
    link = db.scalar(select(RadarJobWork).where(RadarJobWork.job_id == later.id, RadarJobWork.work_id == gap.id))
    assert link is not None and link.active and link.required
    assert prep.state == ('pending' if gap_state == 'ready' else 'waiting_dependency')
    if gap_state == 'pending':
        assert claim_work(db, job_id=later.id, now=NOW).work_id == gap.id


@pytest.mark.parametrize('valid_new_proof', [False, True])
def test_session_refresh_preserves_concurrent_preparation(db, monkeypatch, valid_new_proof):
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.market_session import TwseMarketSessionProvider, MarketSessionResult
    job = blocked_scoring(db)
    monkeypatch.setattr(TwseMarketSessionProvider, 'resolve', lambda self, **k:
        MarketSessionResult('open', job.run_date, 'TW', 'twse', 'MI_INDEX'))
    bundle = WorkHandlers(db).collect_market_session(SimpleNamespace(run_date=job.run_date))
    row = db.scalar(select(DailyRadarPreparedRun))
    row.selected_symbols = ['2454.TW']
    proof = dict(row.step_statuses['market-session']) | {'revision': 'newer'} if valid_new_proof else {}
    row.step_statuses = dict(row.step_statuses) | {'market-session': proof}
    db.commit()
    if valid_new_proof:
        assert bundle.apply(db)['state'] == 'ready'
        db.commit()
    else:
        with pytest.raises(ValueError, match='market_session_prepared_revision_changed'): bundle.apply(db)
        db.rollback()
    db.refresh(row)
    assert row.selected_symbols == ['2454.TW']
    assert row.step_statuses['market-session'] == proof


def test_missing_shared_dependency_requires_review(db):
    job = blocked_scoring(db)
    prep = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'prepare'))
    prep.dependencies = list(prep.dependencies) + ['f'*64]
    db.commit(); reconcile_job(db, job.id, now=NOW)
    assert (prep.state, prep.reason, prep.retry_at) == ('needs_review', 'radar_dependency_missing', None)
    prep.dependencies = prep.dependencies[:-1]; db.commit(); reconcile_job(db, job.id, now=NOW)
    assert prep.state == 'pending' and prep.reason is None


@pytest.mark.parametrize('without_prepared', [False, True])
def test_missing_institutional_market_requeues_archive_and_prepare(db, monkeypatch, without_prepared):
    from ai_stock_sentinel.daily_radar import readiness, integrity
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'true')
    job = blocked_scoring(db)
    missing = db.scalar(select(TaiwanInstitutionalReportSnapshot).where(TaiwanInstitutionalReportSnapshot.market == 'TWO'))
    db.delete(missing)
    if without_prepared: db.delete(db.scalar(select(DailyRadarPreparedRun)))
    db.commit()
    monkeypatch.setattr(readiness, '_require_complete_daily_radar_raw_rows', lambda *a, **k: [])
    monkeypatch.setattr(integrity, 'background_gaps', lambda *a: {})
    reconcile_job(db, job.id, now=NOW, audit=True)
    for work in db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage == 'institutional')):
        assert work.state == ('pending' if work.scope['market'] == 'TWO' else 'ready')
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'prepare')).state == 'waiting_dependency'
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'scoring')).state == 'waiting_dependency'


def test_postgres_different_lanes_reconcile_shared_work_without_lock_inversion(pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import Base
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED', 'false')
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        original = blocked_scoring(db, 'ready')
        later = create_job(db, run_date=original.run_date, lanes=['publication', 'signal_close'], now=NOW)
        ids = [original.id, later.id]
    start = Barrier(2)
    def audit(job_id):
        with Session(pg_engine) as db:
            start.wait(timeout=10)
            return reconcile_job(db, job_id, now=NOW, audit=True).state
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(audit, ids)) == ['pending', 'pending']
    with Session(pg_engine) as db:
        assert all(w.state == 'pending' for w in db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage == 'ohlcv')))
        assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'scoring')).state == 'waiting_dependency'


@pytest.mark.parametrize('score_state', ['needs_review', 'failed_internal'])
@pytest.mark.parametrize('repair_state', ['pending', 'running'])
def test_scoring_waits_for_a_sole_gap_already_being_repaired(db, monkeypatch, score_state, repair_state):
    from fastapi import HTTPException
    from ai_stock_sentinel.daily_radar import readiness
    job = blocked_scoring(db, score_state)
    work = next(w for w in db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage == 'ohlcv'))
                if w.scope['symbol'] == '2330.TW')
    work.state = repair_state
    if repair_state == 'running':
        work.lease_token, work.lease_expires_at = 'active-owner', NOW+timedelta(minutes=3)
    db.commit()
    def missing(*a, **k):
        raise HTTPException(409, detail={'code': 'daily_radar_raw_data_incomplete', 'missing_symbols': ['2330.TW']})
    monkeypatch.setattr(readiness, '_require_complete_daily_radar_raw_rows', missing)
    reconcile_job(db, job.id, now=NOW, audit=True)
    scoring = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'scoring'))
    assert scoring.state == 'waiting_dependency'
    assert work.state == repair_state
    assert work.lease_token == ('active-owner' if repair_state == 'running' else None)
    work.state, work.lease_token, work.lease_expires_at = 'ready', None, None
    db.commit(); reconcile_job(db, job.id, now=NOW)
    assert scoring.state == 'pending'


@pytest.mark.parametrize('field,bad', [('payload_hash', 'bad'), ('source_provider', ''), ('source_dataset', ' ')])
def test_institutional_collector_refetches_invalid_existing_snapshot(db, monkeypatch, field, bad):
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.institutional_flow_provider import OfficialTaiwanInstitutionalReportProvider
    from ai_stock_sentinel.daily_radar.institutional_flow import InstitutionalFlowRow, InstitutionalReport
    from ai_stock_sentinel.daily_radar.pipeline_support import _required_institutional_archive_details
    job = blocked_scoring(db)
    row = db.scalar(select(TaiwanInstitutionalReportSnapshot).where(TaiwanInstitutionalReportSnapshot.market == 'TWO'))
    setattr(row, field, bad); db.commit()
    report = InstitutionalReport('TWO', job.run_date, 'fixture-repaired', 'fixture-report', (
        InstitutionalFlowRow('6488.TWO', 'TWO', 'fixture', job.run_date, 1, 1, 1, 3),))
    calls = []
    def fetch(self, **kwargs): calls.append(kwargs); return report
    monkeypatch.setattr(OfficialTaiwanInstitutionalReportProvider, 'fetch_market', fetch)
    bundle = WorkHandlers(db).collect_institutional(SimpleNamespace(scope={'market': 'TWO'}, run_date=job.run_date))
    assert len(calls) == 1
    assert bundle.apply(db)['state'] == 'ready'
    db.commit(); db.refresh(row)
    assert row.payload_hash == report.payload_hash
    assert _required_institutional_archive_details(db, run_date=job.run_date)['records_written'] == 2


def test_healthy_institutional_snapshot_reuses_without_fetch(db, monkeypatch):
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.institutional_flow_provider import OfficialTaiwanInstitutionalReportProvider
    job = blocked_scoring(db)
    def unexpected(*a, **k): raise AssertionError('healthy snapshot refetched')
    monkeypatch.setattr(OfficialTaiwanInstitutionalReportProvider, 'fetch_market', unexpected)
    result = WorkHandlers(db).collect_institutional(SimpleNamespace(scope={'market': 'TWO'}, run_date=job.run_date)).apply(db)
    assert result == {'state': 'ready', 'result': {'reused': True}}
