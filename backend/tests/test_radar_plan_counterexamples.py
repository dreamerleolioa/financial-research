"""Acceptance counterexamples: these must pass before adopting the prototype."""
from datetime import date, timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from curl_cffi.requests.exceptions import HTTPError
from test_radar_jobs import db
from test_radar_job_postgres import pg_engine
from test_radar_work_postgres import setup_queue
from test_radar_target_suspension import proof, evaluate
from test_radar_price_acceptance import CANDIDATE, DAYS, bar
from test_daily_radar_forward_validation import _add_run, _add_candidate
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result, RadarWorkItem
from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results, exclude_persisted_daily_radar_windows
from ai_stock_sentinel.daily_radar.trading_status import matches, KEY, REASON
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore
from ai_stock_sentinel.daily_radar.work_queue import database_now
from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceEvidence
from ai_stock_sentinel.daily_radar.validation_execution import _collect_locked_signal_prices
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest


def test_legacy_completed_return_is_preserved_when_suspension_arrives(db):
    candidate=_add_candidate(db,_add_run(db,run_date=DAYS[0]),symbol=CANDIDATE['symbol'])
    incoming=evaluate(proof(),[])|{'candidate_id':candidate.id}
    db.add(Result(candidate_id=candidate.id,signal_date=DAYS[0],window_days=1,
        validation_version=incoming['validation_version'],benchmark_symbol='TAIEX',evaluation_as_of_date=DAYS[-1],
        status='validated',target_date=DAYS[-1],outcome={'forward_return_pct':10.0,'entry_price':100.0,'exit_price':110.0}))
    db.commit()
    upsert_forward_validation_results(db,[incoming]);db.commit()
    saved=db.scalar(select(Result))
    assert saved.status=='validated' and saved.outcome['forward_return_pct']==10.0


def test_real_curl_exception_retry_after_holds_provider_gate(pg_engine,monkeypatch):
    setup_queue(pg_engine);monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    response=SimpleNamespace(status_code=503,headers={'Retry-After':'900'})
    def fail():raise HTTPError('server retry',code=22,response=response)
    with pytest.raises(HTTPError):
        SourceSnapshotStore(pg_engine).capture_sdk(operation='forward-adjusted-history',params={
            'symbol':'2330.TW','start_date':'2026-11-09','end_date':'2026-11-16','auto_adjust':True,'actions':False},fetch=fail)
    with Session(pg_engine) as db:
        gate=db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='provider-gate'))
        assert gate.state=='waiting_provider' and gate.retry_at>=database_now(db)+timedelta(seconds=895)


def test_unrelated_stock_quote_cannot_prove_a_suspension():
    wrong=proof()
    wrong['sources'][0]['source_url']='https://www.twse.com.tw/exchangeReport/STOCK_DAY?stockNo=9999&date=20200101'
    assert not matches(wrong,symbol=CANDIDATE['symbol'],target=DAYS[-1])


def test_adding_another_window_does_not_change_the_first_halt_decision():
    later=date(2026,6,5);sessions=[*DAYS,later]
    def collector(symbol,start,end):
        stock={str(DAYS[0]):bar(DAYS[0],100),str(later):bar(later,101)}
        benchmark={str(d):bar(d,10000) for d in sessions}
        item=OfficialPriceEvidence(stock,benchmark,[])
        halt=proof()|{'resume_date':str(later)} if end==DAYS[-1] else None
        return SimpleNamespace(**(vars(item)|{'target_suspension':halt}))
    def run(windows):
        return _collect_locked_signal_prices([CANDIDATE],None,calendar=SimpleNamespace(sessions=sessions,through=later),
            request=DailyRadarForwardValidationRunRequest(windows=windows),collect_official=collector,
            fetch_prices=lambda *args,**kwargs:{CANDIDATE['symbol']:[bar(DAYS[0],100),bar(later,101)]})[0]
    alone=run([1]);combined=run([1,2])
    assert alone['skip_reason']==REASON
    assert combined['skip_reason']==alone['skip_reason']


def test_wrong_calendar_target_remains_in_pending_work(db):
    later=date(2026,6,5)
    candidate=_add_candidate(db,_add_run(db,run_date=DAYS[0]),symbol=CANDIDATE['symbol'])
    row=evaluate(proof(),[])|{'candidate_id':candidate.id,'target_date':str(later),'evaluation_as_of_date':str(later)}
    row['outcome']['expected_target_date']=str(later)
    row['outcome'][KEY]=proof(target=later, resume="115/06/08")
    # This evidence really proves June 5. Only the independent calendar rejects that date for the window.
    assert matches(row['outcome'][KEY], symbol=CANDIDATE['symbol'], target=later)
    # This row predates the candidate write guard. Insert singly to exercise the read side as well.
    def seed_legacy():
        db.add(Result(candidate_id=candidate.id,signal_date=DAYS[0],window_days=1,
            validation_version=row['validation_version'],benchmark_symbol='TAIEX',
            evaluation_as_of_date=later,status='skipped',target_date=later,skip_reason=REASON,outcome=row['outcome']))
        db.commit()
    with pytest.raises(ValueError,match='invalid_target_suspension_evidence'):
        upsert_forward_validation_results(db,[row],calendar=SimpleNamespace(sessions=[*DAYS,later]))
    seed_legacy()
    remaining=exclude_persisted_daily_radar_windows(db,{f'id:{candidate.id}':[1]},as_of_date=later,
        calendar=SimpleNamespace(sessions=[*DAYS,later]))
    assert remaining=={f'id:{candidate.id}':[1]}
