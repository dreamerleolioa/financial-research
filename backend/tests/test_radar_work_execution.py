from datetime import date
import pytest
from types import SimpleNamespace
from sqlalchemy import select, func
from ai_stock_sentinel.db.models import RadarWorkItem, RadarWorkAttempt, DailyRadarRun, DailyRadarPreparedRun
from ai_stock_sentinel.daily_radar.jobs import create_job, reconcile_job
from ai_stock_sentinel.daily_radar.work_executor import advance_job
from ai_stock_sentinel.daily_radar.work_handlers import Collected
from test_radar_jobs import db, NOW


def test_claim_commits_before_collect_and_domain_acceptance_is_atomic(db):
    jid = create_job(db, run_date=date(2026,11,6), lanes=['publication'], now=NOW).id
    db.rollback()
    def collect(claim):
        assert not db.in_transaction()
        assert db.get(RadarWorkAttempt, claim.attempt_id).state == 'running'
        db.rollback()
        def apply(session):
            session.add(DailyRadarRun(run_date=claim.run_date, market='TW', status='completed'))
            return {'state':'ready','result':{'status':'open'}}
        return Collected(apply)
    result = advance_job(db, jid, handlers=SimpleNamespace(collect=collect), now=NOW)
    assert result['effective_run_date']=='2026-11-06'
    assert db.scalar(select(func.count()).select_from(DailyRadarRun))==1
    assert db.scalar(select(RadarWorkAttempt)).state=='ready'


def test_failed_acceptance_rolls_back_domain_and_redacts_exception(db):
    jid = create_job(db, run_date=date(2026,11,6), lanes=['publication'], now=NOW).id
    def collect(claim):
        def apply(session):
            session.add(DailyRadarRun(run_date=claim.run_date, market='TW', status='completed'))
            session.flush()
            raise RuntimeError('SECRET PRIVATE.TW provider body')
        return Collected(apply)
    result = advance_job(db,jid,handlers=SimpleNamespace(collect=collect),now=NOW)
    assert db.scalar(select(func.count()).select_from(DailyRadarRun))==0
    attempt = db.scalar(select(RadarWorkAttempt))
    assert attempt.state=='waiting_provider'
    assert 'PRIVATE' not in str(result)+str(attempt.result)+str(attempt.reason)


def test_reprepare_supersedes_prior_generation(db):
    row=DailyRadarPreparedRun(run_date=date(2026,11,6),market='TW',status='prepared',selected_symbols=['2330.TW'],
        symbol_count=1,universe=[],errors=[],step_statuses={'prepare-universe':{'updated_at':'first'}})
    db.add(row);db.commit()
    jid=create_job(db,run_date=row.run_date,lanes=['publication'],now=NOW).id
    old=list(db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage=='ohlcv')))
    row.selected_symbols,row.step_statuses=['2317.TW'],{'prepare-universe':{'updated_at':'second'}}
    db.commit();reconcile_job(db,jid,now=NOW)
    assert old[0].state=='superseded'
    active=db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage=='ohlcv',RadarWorkItem.state!='superseded')).all()
    assert [w.scope['symbol'] for w in active]==['2317.TW']


def test_real_window_handlers_resume_both_bases_without_replaying_finished_window(db, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    from test_daily_radar_forward_validation import _add_run, _add_candidate, _price
    from radar_calendar_support import calendar
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED','true')
    day=date(2026,6,8); now=datetime(2026,6,9,2,tzinfo=timezone.utc)
    candidate=_add_candidate(db,_add_run(db));cid=candidate.id
    candidate.input_snapshot = dict(candidate.input_snapshot) | {'indicators': {'support_level': 96, 'resistance_level': 104}}
    candidate.data_dates={'ohlcv':'2026-06-01'}
    db.add(DailyRadarPreparedRun(run_date=day,market='TW',status='prepared',selected_symbols=[],symbol_count=0,
        universe=[],errors=[],step_statuses={'validation-calendar':calendar(day-timedelta(days=364),day).payload()}))
    db.commit()
    rows=[_price(d,100,110,90,100) for d in ['2026-06-01','2026-06-02','2026-06-03','2026-06-04','2026-06-05','2026-06-08']]
    calls=[]
    def prices(provider,symbols,**kw):
        assert not db.in_transaction()
        calls.append(tuple(symbols))
        return {s:[dict(r)|{'price_basis':'unadjusted_price','price_source':'fixture','corporate_actions_checked':True,
                            'dividends':0,'stock_splits':0} for r in rows] for s in symbols}
    monkeypatch.setattr(YFinanceForwardPriceProvider,'fetch',prices)
    monkeypatch.setattr(RecoveringResearchPriceProvider,'fetch',prices)
    monkeypatch.setattr(RecoveringResearchPriceProvider,'repair',lambda provider,series,**kwargs:series)
    jid=create_job(db,run_date=day,lanes=['signal_close','next_open'],now=now).id
    for _ in range(8):
        handlers=WorkHandlers(db)  # New process/request state each time.
        handlers.collect_market_session=lambda claim:Collected(lambda session:{'state':'ready','result':{'status':'open'}})
        result=advance_job(db,jid,handlers=handlers,now=now)
        if result['advanced_count']==0:break
    saved=db.scalars(select(DailyRadarForwardValidationResult)).all()
    assert len(saved)==2 and all(r.status=='validated' and r.candidate_id==cid for r in saved), [(r.validation_version,r.status,r.skip_reason) for r in saved]
    assert len({r.validation_version for r in saved})==2
    windows=db.scalars(select(RadarWorkItem).where(RadarWorkItem.stage=='window')).all()
    assert len(windows)==2 and all(w.state=='ready' and w.attempt_count==1 for w in windows), [(w.lane,w.state,w.reason,w.attempt_count) for w in windows]
    previous=len(calls)
    advance_job(db,jid,handlers=WorkHandlers(db),now=now)
    assert len(calls)==previous


def test_source_lease_loss_does_not_permanently_supersede_domain_work(db):
    from ai_stock_sentinel.daily_radar.work_queue import LostLease
    jid=create_job(db,run_date=date(2026,11,6),lanes=['publication'],now=NOW).id
    def collect(claim):raise LostLease('radar_work_lease_lost')
    advance_job(db,jid,handlers=SimpleNamespace(collect=collect),now=NOW)
    work=db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='market-session'))
    assert work.state=='waiting_provider' and work.retry_at is not None


@pytest.mark.parametrize('changed',[False,True])
def test_conflict_rechecks_only_sources_and_wakes_window_only_on_changed_content(db,monkeypatch,changed):
    from datetime import datetime,timezone
    from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
    from ai_stock_sentinel.daily_radar.jobs import member
    from ai_stock_sentinel.daily_radar.work_queue import ensure_work,claim_work,finish_work
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    store=SourceSnapshotStore(db.get_bind(),clock=lambda:datetime(2026,1,1,tzinfo=timezone.utc))
    params={'symbol':'2330.TW','start_date':'2026-06-01','end_date':'2026-06-08','auto_adjust':False,'actions':True}
    request=store._sdk_request('research-unadjusted-history',params)
    _,proof=store.record(request,{'rows':[{'date':'2026-06-01','close':100}]})
    job=create_job(db,run_date=date(2026,11,6),lanes=['publication'],now=NOW)
    window=ensure_work(db,identity={'conflict':changed},stage='window',lane='next_open',run_date=job.run_date,
        scope={},input_revision='a'*64,state='needs_review',retry_at=NOW)
    window.reason='conflicting_price_records';window.result={'source_observations':[proof]};member(db,job,window);db.commit()
    reconcile_job(db,job.id,now=NOW)
    check=db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='source-recheck'))
    claim=claim_work(db,work_id=check.id,now=NOW);db.commit()
    calls=[]
    def fetch(self,symbols,**kwargs):
        assert not db.in_transaction();calls.append(symbols)
        return {'2330.TW':[{'date':'2026-06-01','close':101 if changed else 100}]}
    monkeypatch.setattr(YFinanceResearchPriceProvider,'fetch',fetch)
    handler=WorkHandlers(db)
    collected=handler.collect_source_recheck(claim);db.rollback()
    result=collected.apply(db)
    finish_work(db,claim,state=result['state'],result=result['result'],reason=result['reason'],retry_at=result['retry_at'],now=NOW)
    db.commit();reconcile_job(db,job.id,now=NOW)
    db.refresh(window)
    assert len(calls)==1
    assert window.state==('pending' if changed else 'needs_review')


def test_real_ohlcv_unit_fetches_only_target_and_does_not_mark_partial_cohort_complete(db,monkeypatch):
    from ai_stock_sentinel.daily_radar.work_queue import WorkClaim
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar import dependencies
    from ai_stock_sentinel.db.models import StockRawData
    from test_daily_radar_api import _technical_payload,FakeBatchTechnicalFetcher
    day=date(2026,11,6)
    db.add(DailyRadarPreparedRun(run_date=day,market='TW',status='prepared',selected_symbols=['2330.TW','2317.TW'],
        symbol_count=2,universe=[],errors=[],step_statuses={}))
    db.commit()
    fetcher=FakeBatchTechnicalFetcher({'2330.TW':_technical_payload('2330.TW',day)})
    monkeypatch.setattr(dependencies,'get_daily_radar_technical_fetcher',lambda session:fetcher)
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    handlers=WorkHandlers(db)
    claim=WorkClaim(1,'token',1,'a'*64,'ohlcv','publication',day,{'symbol':'2330.TW'})
    collected=handlers.collect(claim)
    db.rollback()
    result=collected.apply(db)
    assert result['state']=='ready'
    row=db.scalar(select(DailyRadarPreparedRun))
    assert row.step_statuses['refresh-ohlcv']['status']=='failed'
    assert row.step_statuses['refresh-ohlcv']['missing_symbols']==['2317.TW']
    db.rollback()
    assert db.scalar(select(func.count()).select_from(StockRawData))==0


def test_new_source_revision_rejects_old_collected_result_before_domain_mutation(db):
    from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
    from ai_stock_sentinel.daily_radar.work_executor import fence_sources
    from ai_stock_sentinel.daily_radar.work_queue import LostLease
    from datetime import datetime,timezone,timedelta
    clock=[datetime(2026,11,6,tzinfo=timezone.utc)]
    old=SourceSnapshotStore(db.get_bind(),clock=lambda:clock[0])
    request=old._sdk_request('research-unadjusted-history',{'symbol':'2330.TW','start_date':'2026-06-01',
        'end_date':'2026-06-08','auto_adjust':False,'actions':True})
    old.record(request,{'rows':[{'date':'2026-06-01','close':100}]})
    clock[0]+=timedelta(seconds=1)
    newer=SourceSnapshotStore(db.get_bind(),clock=lambda:clock[0])
    newer.record(request,{'rows':[{'date':'2026-06-01','close':101}]})
    with pytest.raises(LostLease,match='source_revision_changed'):
        fence_sources(db,old)
    db.rollback()


def test_owned_rejection_retains_current_attempt_dependencies_after_domain_rollback(db):
    from ai_stock_sentinel.daily_radar.work_queue import LostLease
    jid=create_job(db,run_date=date(2026,11,6),lanes=['publication'],now=NOW).id
    previous=db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='market-session'))
    previous.result={'source_observations':[{'request_hash':'previous-attempt'}],'source_captured_count':999}
    db.commit()
    store=SimpleNamespace(seen={},captured_count=0,dependencies={'source-key':{'state':'waiting_provider'}})
    def collect(claim):
        def apply(session):
            session.add(DailyRadarRun(run_date=claim.run_date,market='TW',status='completed'))
            session.flush()
            raise LostLease('radar_prepared_generation_changed')
        return Collected(apply)
    advance_job(db,jid,handlers=SimpleNamespace(collect=collect,store=store),now=NOW)
    assert db.scalar(select(func.count()).select_from(DailyRadarRun))==0
    attempt=db.scalar(select(RadarWorkAttempt))
    work=db.get(RadarWorkItem,attempt.work_id)
    assert attempt.state==work.state=='superseded'
    assert attempt.result==work.result=={'source_observations':[],'source_captured_count':0,
        'source_dependencies':store.dependencies}


def test_rejected_old_owner_cannot_replace_new_owner_diagnostics(db):
    from sqlalchemy.orm import Session
    jid=create_job(db,run_date=date(2026,11,6),lanes=['publication'],now=NOW).id
    def collect(claim):
        with Session(db.get_bind()) as other:
            work=other.get(RadarWorkItem,claim.work_id)
            work.lease_token='new-owner';work.result={'owner':'new'};other.commit()
        def forbidden(session): raise AssertionError('must not accept old owner')
        return Collected(forbidden)
    advance_job(db,jid,handlers=SimpleNamespace(collect=collect),now=NOW)
    attempt=db.scalar(select(RadarWorkAttempt))
    work=db.get(RadarWorkItem,attempt.work_id)
    assert work.lease_token=='new-owner' and work.result=={'owner':'new'}
    assert attempt.state=='running' and attempt.result=={}


def test_missing_source_snapshot_reports_hashes_without_exception_text(db):
    from ai_stock_sentinel.daily_radar.work_executor import fence_sources,SourceRevisionChanged
    with pytest.raises(SourceRevisionChanged) as caught:
        fence_sources(db,SimpleNamespace(seen={'a'*64:{'snapshot_sha256':'b'*64}}))
    assert caught.value.rejection=={'reason':'radar_source_revision_changed','request_hash':'a'*64,
        'expected_snapshot_sha256':'b'*64,'actual_snapshot_sha256':None}
    unsafe=SourceRevisionChanged('SECRET private URL','SECRET token','private symbol')
    assert unsafe.rejection=={'reason':'radar_source_revision_changed','request_hash':None,
        'expected_snapshot_sha256':None,'actual_snapshot_sha256':None}


def test_avwap_reuses_fresh_target_and_keeps_auxiliary_partial(db,monkeypatch):
    from ai_stock_sentinel.db.models import Phase1AvwapSnapshot
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_queue import WorkClaim
    from ai_stock_sentinel.phase1_avwap.provider import ArchiveFirstDailyPriceProvider
    day=date(2026,11,6)
    db.add(DailyRadarPreparedRun(run_date=day,market='TW',status='prepared',selected_symbols=['2330.TW','2317.TW'],
        symbol_count=2,universe=[],errors=[],step_statuses={}))
    db.add(Phase1AvwapSnapshot(symbol='2330.TW',data_date=day,freshness='fresh',payload={}))
    db.commit()
    def forbidden(*args,**kwargs):raise AssertionError('fresh snapshot must not use provider')
    monkeypatch.setattr(ArchiveFirstDailyPriceProvider,'fetch_history_result',forbidden)
    handler=WorkHandlers(db);claim=WorkClaim(1,'token',1,'a'*64,'avwap','auxiliary',day,{'symbol':'2330.TW'})
    collected=handler.collect(claim);db.rollback();result=collected.apply(db)
    assert result['state']=='ready'
    step=db.scalar(select(DailyRadarPreparedRun)).step_statuses['refresh-avwap']
    assert step['status']=='failed' and step['missing_symbols']==['2317.TW']


def test_avwap_verified_short_history_is_terminal_and_releases_provider_gate(db,monkeypatch):
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_queue import WorkClaim
    from ai_stock_sentinel.phase1_avwap.provider import ArchiveFirstDailyPriceProvider,InsufficientListedHistory
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    day=date(2026,11,6)
    db.add(DailyRadarPreparedRun(run_date=day,market='TW',status='prepared',selected_symbols=['2330.TW'],
        symbol_count=1,universe=[],errors=[],step_statuses={}))
    db.commit()
    def short(*args,**kwargs):
        assert not db.in_transaction()
        raise InsufficientListedHistory('insufficient_listed_history',source_provider='twse',
            source_dataset='TWSE_STOCK_DAY',source_trace={'route':'archive_first','required_trading_bars':60,'rows_used':9})
    monkeypatch.setattr(ArchiveFirstDailyPriceProvider,'fetch_history_result',short)
    handler=WorkHandlers(db);claim=WorkClaim(1,'token',1,'a'*64,'avwap','auxiliary',day,{'symbol':'2330.TW'})
    collected=handler.collect(claim);db.rollback();result=collected.apply(db)
    assert result['state']=='terminal' and result['reason']=='insufficient_listed_history'
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='provider-gate')).state=='ready'
    step=db.scalar(select(DailyRadarPreparedRun)).step_statuses['refresh-avwap']
    assert step['status']=='completed' and step['not_applicable_symbols']==['2330.TW']
