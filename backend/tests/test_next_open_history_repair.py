from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import Base, DailyRadarPreparedRun, DailyRadarForwardValidationResult as Result
from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.history_repair import result_snapshot
from ai_stock_sentinel.daily_radar.readiness import validation_inventory
from test_radar_job_postgres import pg_engine
from test_daily_radar_validation_read import add_run, add_candidate
from test_daily_radar_research_validation import price
from radar_calendar_support import calendar
from test_twse_rights_recovery import scenario

DAY = date(2026,8,31)
SIGNAL = date(2026,6,1)


@pytest.fixture(params=['sqlite', 'postgres'])
def db(request, tmp_path):
    engine = (create_engine('sqlite:///'+str(tmp_path/'repair.sqlite')) if request.param=='sqlite'
        else request.getfixturevalue('pg_engine'))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    if request.param=='sqlite': engine.dispose()


class Prices:
    def __init__(self, cal, *, missing=(), check=None):
        self.days = [d for d in cal.sessions if d >= SIGNAL]
        self.missing, self.check, self.calls, self.diagnostics = set(missing), check, 0, []

    def fetch(self, symbols, *, start_date, end_date):
        if self.check: self.check()
        self.calls += 1
        return {symbol: [price(str(d), open_=100+i, close=100+i) for i,d in enumerate(self.days)
            if start_date<=d<=end_date and not (symbol!='TAIEX' and d in self.missing)] for symbol in symbols}

    def repair(self, prices, **kwargs):
        if self.check: self.check()
        return deepcopy(prices)


def seed(db):
    cal = calendar(date(2025,9,1), DAY)
    c = add_candidate(db, add_run(db, SIGNAL))
    c.data_dates = {'ohlcv': str(SIGNAL)}
    c.input_snapshot = dict(c.input_snapshot) | {'ohlcv': {'close': 100}}
    future = [d for d in cal.sessions if d>SIGNAL]
    rows=[]
    for w in (5,10,20,40,60):
        row=Result(candidate_id=c.id,window_days=w,validation_version=RESEARCH_VALIDATION_VERSION,
            status='validated',signal_date=SIGNAL,target_date=future[w-1],benchmark_symbol='TAIEX',
            evaluation_as_of_date=DAY,outcome={'return_basis':'next_open','price_basis':'unadjusted_price',
                'forward_return_pct':2,'official_rights_recovery':{'fixture':'original official evidence'}})
        db.add(row); rows.append(row)
    db.add(DailyRadarPreparedRun(run_date=DAY,market='TW',symbol_count=0,selected_symbols=[],universe=[],
        step_statuses={'validation-calendar':cal.payload()}))
    db.commit()
    ids=[r.id for r in rows]
    before={r.id:result_snapshot(r) for r in rows}
    return cal,c,ids,before


def api():
    from ai_stock_sentinel.daily_radar import next_open_history_repair
    return next_open_history_repair


def captured(db):
    cal,c,ids,before=seed(db)
    tool=api(); request=tool.inventory(db,calendar=cal)
    bind=db.get_bind();db.rollback()
    evidence=tool.capture_evidence(bind,request,calendar=cal,provider=Prices(cal))
    return tool,cal,c,ids,before,request,evidence


def test_protected_legacy_all_windows_have_a_reviewed_apply_and_rollback(db):
    tool,cal,c,ids,before,request,evidence=captured(db)
    assert {r['id'] for r in request['results']}==set(ids)
    plan=tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=evidence)
    assert len(plan['results'])==5
    receipt=tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256']);db.commit()
    assert receipt['written']==5
    report=validation_inventory(db,as_of_date=DAY)['next_open']
    assert report['validated_count']==5 and report['ready'] is True
    for row in db.scalars(select(Result)):
        assert row.outcome['historical_repair']['prior_evidence']['official_rights_recovery']==before[row.id]['outcome']['official_rights_recovery']
        late=result_snapshot(row) | {'outcome':{'forward_return_pct':999}}
        assert upsert_forward_validation_results(db,[late])['records_written']==0
    db.commit()
    assert tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'])['already_applied']==5
    db.commit()
    tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'],rollback=True);db.commit()
    assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before


def test_capture_releases_its_reader_before_provider_io(db):
    cal,c,ids,before=seed(db);tool=api();request=tool.inventory(db,calendar=cal)
    bind=db.get_bind();db.rollback()
    def check(): assert bind.pool.checkedout()==0
    tool.capture_evidence(bind,request,calendar=cal,provider=Prices(cal,check=check))
    assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before


def test_incomplete_full_window_does_not_create_a_manifest(db):
    cal,c,ids,before=seed(db);tool=api();request=tool.inventory(db,calendar=cal)
    bind=db.get_bind();db.rollback()
    evidence=tool.capture_evidence(bind,request,calendar=cal,provider=Prices(cal,missing=(date(2026,6,4),)))
    with pytest.raises(ValueError,match='next_open_repair_incomplete'):
        tool.build_repair_plan(bind,request,calendar=cal,evidence=evidence)
    assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before


def test_capture_and_manifest_reject_input_drift_and_tampering(db):
    tool,cal,c,ids,before,request,evidence=captured(db)
    bad=deepcopy(evidence);bad['sha256']='0'*64
    with pytest.raises(ValueError,match='next_open_capture_hash'):
        tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=bad)
    plan=tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=evidence)
    c.observation_score += 1; db.commit()
    with pytest.raises(ValueError,match='next_open_capture_inputs_changed'):
        tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=evidence)
    with pytest.raises(ValueError,match='repair_validation_inputs_changed'):
        tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'])
    assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before


def test_private_collection_does_not_rollback_flushed_caller_writes(db):
    cal,c,ids,before=seed(db);tool=api();request=tool.inventory(db,calendar=cal)
    c.observation_score += 1;db.flush()
    expected=c.observation_score
    baseline=db.get_bind().pool.checkedout()
    def check(): assert db.get_bind().pool.checkedout()==baseline
    tool.capture_evidence(db.get_bind(),request,calendar=cal,provider=Prices(cal,check=check))
    db.commit();db.refresh(c)
    assert c.observation_score==expected
    assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before


def test_entire_batch_is_rejected_when_one_result_changes(db):
    tool,cal,c,ids,before,request,evidence=captured(db)
    plan=tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=evidence)
    row=db.get(Result,ids[-1]);row.outcome={'intervening':'write'};db.commit()
    with pytest.raises(ValueError,match='repair_row_changed'):
        tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'])
    assert result_snapshot(db.get(Result,ids[0]))==before[ids[0]]


def test_official_rights_recovery_is_replayed_offline_with_original_evidence(scenario):
    from datetime import timedelta
    from test_twse_rights_recovery import SIGNAL as signal, TARGET, DAYS, TERMS
    from ai_stock_sentinel.calibration.price_provider import get_research_price_provider
    engine,run,_,_,calls,_=scenario
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    TaiwanDailyBar.__table__.create(engine,checkfirst=True)
    response=run();assert response.status_code==200,response.text
    client=run.__closure__[0].cell_contents
    provider=client.app.dependency_overrides[get_research_price_provider]()
    closed={signal+timedelta(days=i) for i in range((TARGET-signal).days+1)}-set(DAYS)
    cal=calendar(signal,TARGET,closed)
    with Session(engine) as db:
        rows=list(db.scalars(select(Result).where(Result.status=='validated')))
        assert len(rows)==3
        for row in rows:
            payload=dict(row.outcome);payload.pop('validation_input');row.outcome=payload
        prepared=db.scalar(select(DailyRadarPreparedRun).where(DailyRadarPreparedRun.run_date==TARGET))
        if prepared is None:
            prepared=DailyRadarPreparedRun(run_date=TARGET,market='TW',symbol_count=0,selected_symbols=[],universe=[])
            db.add(prepared)
        prepared.step_statuses={'validation-calendar':cal.payload()};db.commit()
        tool=api();request=tool.inventory(db,calendar=cal)
        request['results']=[r for r in request['results'] if r['id'] in {x.id for x in rows}]
        db.rollback()
        evidence=tool.capture_evidence(engine,request,calendar=cal,provider=provider)
        count=len(calls)
        plan=tool.build_repair_plan(engine,request,calendar=cal,evidence=evidence)
        assert len(calls)==count  # Planning cannot reach the provider.
        tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256']);db.commit()
        for row in rows:
            db.refresh(row)
            assert row.outcome['validation_input']['candidate_revision']
            assert row.outcome['official_rights_recovery']
            assert row.outcome['historical_repair']['prior_evidence']['official_rights_recovery']


def test_cli_capture_plan_apply_and_rollback_are_separate(tmp_path,monkeypatch):
    import importlib.util
    import json
    from pathlib import Path
    path=Path(__file__).parents[1]/'scripts'/'repair_daily_radar_next_open.py'
    spec=importlib.util.spec_from_file_location('next_open_cli',path)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    url='sqlite:///'+str(tmp_path/'cli.sqlite')
    engine=create_engine(url);Base.metadata.create_all(engine)
    with Session(engine) as db:
        cal,_,_,before=seed(db)
    engine.dispose();monkeypatch.setenv('DATABASE_URL',url)
    calfile=tmp_path/'calendar.json';calfile.write_text(json.dumps(cal.payload()))
    inv=tmp_path/'inventory.json';capture=tmp_path/'capture.json';planfile=tmp_path/'plan.json'
    cli.main(['inventory','--calendar',str(calfile),'--output',str(inv)])
    common=['--calendar',str(calfile),'--request',str(inv)]
    cli.main(['capture',*common,'--output',str(capture)],provider=Prices(cal))
    plan=cli.main(['plan',*common,'--evidence',str(capture),'--output',str(planfile)])
    for f in (inv,capture,planfile): assert f.stat().st_mode & 0o777==0o600
    common=['--manifest',str(planfile),'--apply-sha256',plan['sha256']]
    receipt=tmp_path/'receipt.json'
    cli.main(['apply',*common,'--output',str(receipt)])
    with pytest.raises(FileExistsError): cli.main(['rollback',*common,'--output',str(receipt)])
    cli.main(['rollback',*common,'--output',str(tmp_path/'rollback.json')])
    engine=create_engine(url)
    with Session(engine) as db:
        assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before
    engine.dispose()


def test_calendar_drift_and_wrong_basis_manifest_are_rejected(db):
    tool,cal,c,ids,before,request,evidence=captured(db)
    plan=tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=evidence)
    wrong=deepcopy(plan);wrong['results'][0]['after']['validation_version']='wrong'
    wrong['sha256']=tool.fingerprint({k:v for k,v in wrong.items() if k!='sha256'})
    with pytest.raises(ValueError,match='repair_immutable_identity_changed'):
        tool.apply_repair_plan(db,wrong,expected_sha256=wrong['sha256'])
    db.rollback()
    prepared=db.scalar(select(DailyRadarPreparedRun))
    prepared.step_statuses={};db.commit()
    with pytest.raises(ValueError,match='repair_calendar_changed'):
        tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'])
    assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before


@pytest.mark.parametrize('official_close',[90,101])
def test_default_detached_capture_still_checks_complete_yahoo_against_official_archive(db,monkeypatch,official_close):
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    cal,c,ids,before=seed(db);tool=api();request=tool.inventory(db,calendar=cal)
    day=next(d for d in cal.sessions if d>SIGNAL)
    db.add(TaiwanDailyBar(symbol=c.symbol,trade_date=day,market='TW',open=official_close,
        high=official_close+2,low=official_close-5,close=official_close,
        dataset='taiwan_market_daily_ohlcv',source_provider='twse',source_dataset='TWSE_STOCK_DAY',is_final=True,volume=100))
    db.commit();bind=db.get_bind();db.rollback()
    prices=Prices(cal)
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED','true')
    def yahoo(self,*args,**kwargs):
        assert bind.pool.checkedout()==0
        return prices.fetch(*args,**kwargs)
    monkeypatch.setattr(YFinanceResearchPriceProvider,'fetch',yahoo)
    def no_network(*args,**kwargs): raise RuntimeError('offline test')
    monkeypatch.setattr(RecoveringResearchPriceProvider,'_request',no_network)
    evidence=tool.capture_evidence(bind,request,calendar=cal)
    assert evidence['archive_prices'][c.symbol][str(day)]['close']==official_close
    if official_close==90:
        with pytest.raises(ValueError,match='next_open_repair_incomplete'):
            tool.build_repair_plan(bind,request,calendar=cal,evidence=evidence)
    else:
        assert len(tool.build_repair_plan(bind,request,calendar=cal,evidence=evidence)['results'])==5
    assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before


def test_common_calendar_check_is_once_per_locked_batch(db,monkeypatch):
    tool,cal,c,ids,before,request,evidence=captured(db)
    plan=tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=evidence)
    original=tool._calendar_context;calls=[]
    def checked(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(tool,'_calendar_context',checked)
    tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'])
    assert len(calls)==1
