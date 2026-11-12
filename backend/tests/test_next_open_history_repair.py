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
from test_next_open_corporate_actions import replay

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

    def official_research_history(self,symbol,*,start_date,end_date,max_rows=None):
        if self.check:self.check()
        from ai_stock_sentinel.daily_radar.history_repair import fingerprint
        from urllib.parse import urlencode
        rows=self.fetch([symbol],start_date=start_date,end_date=end_date)[symbol]
        for row in rows:
            month=row['date'][:7].replace('-','')+'01'
            path='/indicesReport/MI_5MINS_HIST' if symbol=='TAIEX' else '/exchangeReport/STOCK_DAY'
            params={'response':'json','date':month}
            if symbol!='TAIEX':params['stockNo']=symbol.split('.')[0]
            row.update(price_source='twse_index_history' if symbol=='TAIEX' else 'twse_stock_day',
                official_price_report={'source_url':'https://www.twse.com.tw'+path+'?'+urlencode(params),
                    'snapshot_sha256':fingerprint({'fixture':symbol,'month':month}),
                    'captured_at':'2026-11-10T00:00:00+00:00'})
        return rows[:max_rows] if max_rows else rows


def seed(db, *, closed=()):
    cal = calendar(date(2025,9,1), DAY, closed=closed)
    c = add_candidate(db, add_run(db, SIGNAL))
    c.data_dates = {'ohlcv': str(SIGNAL)}
    c.input_snapshot = dict(c.input_snapshot) | {'ohlcv': {'close': 100}}
    future = [d for d in cal.sessions if d>SIGNAL]
    rows=[]
    for w in (5,10,20,40,60):
        row=Result(candidate_id=c.id,window_days=w,validation_version=RESEARCH_VALIDATION_VERSION,
            status='validated',signal_date=SIGNAL,target_date=future[w-1],benchmark_symbol='TAIEX',
            evaluation_as_of_date=DAY,outcome={'return_basis':'next_open','price_basis':'unadjusted_price',
                'forward_return_pct':2,'historical_repair':{'fixture':'original reviewed evidence'}})
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
        assert row.outcome['historical_repair']['prior_evidence']['historical_repair']==before[row.id]['outcome']['historical_repair']
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


@pytest.mark.parametrize('extra_source', ['stock', 'benchmark', 'both'])
def test_calculation_evidence_uses_only_official_sessions_for_all_windows(db, extra_source):
    closed = date(2026, 6, 3)
    cal, candidate, ids, before = seed(db, closed=(closed,))
    tool = api()
    request = tool.inventory(db, calendar=cal)
    bind = db.get_bind()
    db.rollback()

    class ExtraClosedDayPrices(Prices):
        def fetch(self, symbols, *, start_date, end_date):
            result = super().fetch(symbols, start_date=start_date, end_date=end_date)
            for symbol, rows in result.items():
                wanted = extra_source == 'both' or (symbol == 'TAIEX') == (extra_source == 'benchmark')
                if wanted and start_date <= closed <= end_date:
                    rows.append(price(str(closed), open_=9999, close=9999))
                    rows.sort(key=lambda row: row['date'])
            return result

        def official_research_history(self, symbol, *, start_date, end_date, max_rows=None):
            return Prices(cal).official_research_history(symbol, start_date=start_date,
                end_date=end_date, max_rows=max_rows)

    clean = tool.capture_evidence(bind, request, calendar=cal, provider=Prices(cal))
    expected = tool.build_repair_plan(bind, request, calendar=cal, evidence=clean)
    captured = tool.capture_evidence(bind, request, calendar=cal, provider=ExtraClosedDayPrices(cal))
    plan = tool.build_repair_plan(bind, request, calendar=cal, evidence=captured)
    assert {row['after']['window_days'] for row in plan['results']} == {5, 10, 20, 40, 60}
    for actual, baseline in zip(plan['results'], expected['results'], strict=True):
        outcome = actual['after']['outcome']
        proof = outcome['reviewed_official_window']
        assert [r['date'] for r in proof['calculation_stock']] == proof['session_dates']
        assert [r['date'] for r in proof['calculation_benchmark']] == [str(SIGNAL), *proof['session_dates']]
        assert {k: v for k, v in outcome.items() if k != 'historical_repair'} == {
            k: v for k, v in baseline['after']['outcome'].items() if k != 'historical_repair'}
    assert {r.id: result_snapshot(r) for r in db.scalars(select(Result))} == before


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
    with pytest.raises(ValueError,match='next_open_repair_incomplete'):
        tool.build_repair_plan(bind,request,calendar=cal,evidence=evidence)
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


@pytest.mark.parametrize('gap',[None,'nested_prior','missing_announcement','stock_gap','index_gap'])
@pytest.mark.parametrize('price_state',['adjusted','matching','matching_precision'])
def test_unmarked_yahoo_cannot_downgrade_existing_official_rights(scenario,monkeypatch,gap,price_state):
    from datetime import timedelta
    from test_twse_rights_recovery import SIGNAL as signal,TARGET,DAYS
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    from ai_stock_sentinel.calibration.price_provider import get_research_price_provider
    engine,run,prices,quotes,calls,faults=scenario
    response=run();assert response.status_code==200,response.text
    client=run.__closure__[0].cell_contents
    old=client.app.dependency_overrides[get_research_price_provider]()
    provider=RecoveringResearchPriceProvider(official_getter=old.official_getter)
    if price_state != 'adjusted':
        for symbol in quotes:
            quotes[symbol]=[deepcopy(r)|{'price_source':'yfinance','dividends':0,'stock_splits':0}
                            for r in prices[symbol]]
            if price_state == 'matching_precision':
                for row in quotes[symbol]:
                    row.update({field:row[field]+0.000001 for field in ('open','high','low','close')})
    def yahoo(self,symbols,**kwargs):
        return {s:[deepcopy(r)|{'price_basis':'unadjusted_price','corporate_actions_checked':True}
                   for r in quotes[s]] for s in symbols}
    monkeypatch.setattr(YFinanceResearchPriceProvider,'fetch',yahoo)
    TaiwanDailyBar.__table__.create(engine,checkfirst=True)
    closed={signal+timedelta(days=i) for i in range((TARGET-signal).days+1)}-set(DAYS)
    cal=calendar(signal,TARGET,closed)
    with Session(engine) as db:
        rows=list(db.scalars(select(Result).where(Result.status=='validated')));assert len(rows)==3
        returns={r.id:r.outcome['forward_return_pct'] for r in rows}
        for row in rows:
            value=dict(row.outcome);value.pop('validation_input')
            if gap=='nested_prior':
                prior=deepcopy(value);value.pop('official_rights_recovery')
                value['historical_repair']={'prior_evidence':prior}
            row.outcome=value
        db.scalar(select(DailyRadarPreparedRun).where(DailyRadarPreparedRun.run_date==TARGET)).step_statuses = {
            'validation-calendar':cal.payload()};db.commit()
        before={r.id:result_snapshot(r) for r in rows}
        if gap=='missing_announcement':faults.add(gap)
        if gap=='stock_gap':prices['2890.TW']=[p for p in prices['2890.TW'] if p['date']!=str(DAYS[10])]
        if gap=='index_gap':prices['TAIEX']=[p for p in prices['TAIEX'] if p['date']!=str(DAYS[10])]
        tool=api();request=tool.inventory(db,calendar=cal);db.rollback()
        evidence=tool.capture_evidence(engine,request,calendar=cal,provider=provider)
        if gap in {'missing_announcement','stock_gap','index_gap'}:
            with pytest.raises(ValueError,match='next_open_repair_incomplete'):
                tool.build_repair_plan(engine,request,calendar=cal,evidence=evidence)
        else:
            plan=tool.build_repair_plan(engine,request,calendar=cal,evidence=evidence)
            assert len(plan['results'])==3
            for entry in plan['results']:
                assert entry['after']['outcome']['forward_return_pct']==returns[entry['id']]
                assert entry['after']['outcome'].get('official_rights_recovery')
                proof=entry['after']['outcome']['official_rights_recovery']
                assert proof['verification_mode']==('adjustment_recovered' if price_state=='adjusted' else 'raw_prices_matched')
            for defect in ('date','subscription','report_hash','verification_mode','replay_mode'):
                changed=deepcopy(plan);outcome=changed['results'][0]['after']['outcome']
                if defect=='date':outcome['official_rights_recovery']['event']['ex_date']=str(TARGET)
                if defect=='subscription':outcome['official_rights_recovery']['event']['subscription_price']+=1
                if defect=='verification_mode':outcome['official_rights_recovery']['verification_mode']='raw_prices_matched' if price_state=='adjusted' else 'adjustment_recovered'
                if defect=='replay_mode':outcome['reviewed_official_window']['event_replay'].pop('rights_verification')
                if defect=='report_hash':
                    call=next(c for c in outcome['reviewed_official_window']['event_replay']['calls']
                        if c['request']['method']=='capture_twse_report')
                    call['value'][1]['snapshot_sha256']='0'*64
                changed['sha256']=tool.fingerprint({k:v for k,v in changed.items() if k!='sha256'})
                with pytest.raises(ValueError,match='next_open_official_event_'):
                    tool.apply_repair_plan(db,changed,expected_sha256=changed['sha256'])
                db.rollback()
        assert {r.id:result_snapshot(r) for r in rows}==before
        if gap in {None,'nested_prior'}:
            tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256']);db.commit()
            assert validation_inventory(db,as_of_date=TARGET)['next_open']['input_unverified_count']==0
            tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'],rollback=True);db.commit()
            assert {r.id:result_snapshot(r) for r in rows}==before
            if price_state=='adjusted':
                legacy=deepcopy(plan)
                for entry in legacy['results']:
                    payload=entry['after']['outcome']
                    payload['official_rights_recovery'].pop('verification_mode')
                    payload['reviewed_official_window']['event_replay'].pop('rights_verification')
                legacy['sha256']=tool.fingerprint({k:v for k,v in legacy.items() if k!='sha256'})
                tool.apply_repair_plan(db,legacy,expected_sha256=legacy['sha256']);db.commit()
                tool.apply_repair_plan(db,legacy,expected_sha256=legacy['sha256'],rollback=True);db.commit()
                assert {r.id:result_snapshot(r) for r in rows}==before


@pytest.mark.parametrize('defect',['missing_day','wrong_symbol','wrong_return'])
def test_apply_rechecks_full_official_proof_even_after_review_hash_changes(db,defect):
    tool,cal,c,ids,before,request,evidence=captured(db)
    plan=tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=evidence)
    outcome=plan['results'][0]['after']['outcome']
    if defect=='missing_day':outcome['reviewed_official_window']['stock'].pop(2)
    if defect=='wrong_symbol':
        outcome['reviewed_official_window']['stock'][0]['official_price_report']['source_url']='https://www.twse.com.tw/exchangeReport/STOCK_DAY?date=20260601&stockNo=9999'
    if defect=='wrong_return':outcome['forward_return_pct']+=1
    plan['sha256']=tool.fingerprint({k:v for k,v in plan.items() if k!='sha256'})
    with pytest.raises(ValueError,match='next_open_official|twse_rights_incomplete_price_window'):
        tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'])
    assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before


def test_legacy_artifacts_cannot_apply_but_legacy_applied_rows_can_rollback(db):
    from ai_stock_sentinel.daily_radar.history_repair import MUTABLE_FIELDS
    tool,cal,c,ids,before,request,evidence=captured(db)
    old=deepcopy(evidence);old['version']='daily-radar-next-open-capture-v1'
    old['sha256']=tool.fingerprint({k:v for k,v in old.items() if k!='sha256'})
    with pytest.raises(ValueError,match='next_open_capture_hash'):
        tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=old)
    legacy=tool.build_repair_plan(db.get_bind(),request,calendar=cal,evidence=evidence)
    legacy['version']=tool.LEGACY_VERSION
    for entry in legacy['results']:
        entry['after']['outcome'].pop('reviewed_official_window')
        entry['after']['outcome'].pop('validation_window', None)
        entry['after']['outcome']['historical_repair']['version']=tool.LEGACY_VERSION
    legacy['sha256']=tool.fingerprint({k:v for k,v in legacy.items() if k!='sha256'})
    with pytest.raises(ValueError,match='next_open_legacy_manifest_apply_disabled'):
        tool.apply_repair_plan(db,legacy,expected_sha256=legacy['sha256'])
    for entry in legacy['results']:
        row=db.get(Result,entry['id'])
        for field in MUTABLE_FIELDS:
            value=deepcopy(entry['after'][field])
            if field in ('target_date','evaluation_as_of_date') and value:value=date.fromisoformat(value)
            setattr(row,field,value)
    db.commit()
    tool.apply_repair_plan(db,legacy,expected_sha256=legacy['sha256'],rollback=True);db.commit()
    assert {r.id:result_snapshot(r) for r in db.scalars(select(Result))}==before


def test_signal_level_skip_also_releases_private_reader_before_official_io(db):
    cal,c,ids,before=seed(db);c.data_dates={};db.commit();tool=api()
    request=tool.inventory(db,calendar=cal);bind=db.get_bind();db.rollback()
    def released():assert bind.pool.checkedout()==0
    evidence=tool.capture_evidence(bind,request,calendar=cal,provider=Prices(cal,check=released))
    assert all(c['request']['method']!='fetch' for c in evidence['calls'])


@pytest.mark.parametrize('case',[0,1,2])
def test_prior_distribution_and_reduction_keep_fresh_full_window_proof(replay,monkeypatch,case):
    from datetime import timedelta
    from test_next_open_corporate_actions import CASES,TARGET,_iso_roc
    from ai_stock_sentinel.db.models import TaiwanDailyBar,DailyRadarCandidate
    from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    archive,fault,engine,run,calls=replay
    symbol,signal,window,reason,_=CASES[case]
    assert run(symbol,signal,window,seed=True)['terminal_skipped_count']==1
    closure={k:c.cell_contents for k,c in zip(run.__code__.co_freevars,run.__closure__)}
    provider=RecoveringResearchPriceProvider(official_getter=closure['official_get'])
    monkeypatch.setattr(YFinanceResearchPriceProvider,'fetch',lambda self,*a,**k:closure['yahoo'](*a,**k))
    positive={date.fromisoformat(_iso_roc(row[0])) for key,value in archive.items() if key.startswith('twse-index-')
              for row in value.get('tables',[value])[0]['data']}
    closed={signal+timedelta(days=i) for i in range((TARGET-signal).days+1)}-positive
    cal=calendar(signal,TARGET,closed)
    TaiwanDailyBar.__table__.create(engine,checkfirst=True)
    with Session(engine) as db:
        row=db.scalar(select(Result));before=result_snapshot(row)
        candidate=db.get(DailyRadarCandidate,row.candidate_id);candidate.observation_score+=1
        db.add(DailyRadarPreparedRun(run_date=TARGET,market='TW',symbol_count=0,selected_symbols=[],universe=[],
            step_statuses={'validation-calendar':cal.payload()}));db.commit()
        tool=api();request=tool.inventory(db,calendar=cal);assert len(request['results'])==1;db.rollback()
        evidence=tool.capture_evidence(engine,request,calendar=cal,provider=provider)
        plan=tool.build_repair_plan(engine,request,calendar=cal,evidence=evidence)
        assert plan['results'][0]['after']['skip_reason']==reason
        if case in (0,1):
            for defect in ('remove_precision','loosen_precision'):
                changed=deepcopy(plan);proof=changed['results'][0]['after']['outcome']['official_corporate_action']
                if defect=='remove_precision':proof.pop('distribution_precision')
                else:proof['distribution_precision']['share_ratio_tolerance']='1'
                changed['sha256']=tool.fingerprint({k:v for k,v in changed.items() if k!='sha256'})
                with pytest.raises(ValueError,match='next_open_official_event_replay_mismatch'):
                    tool.apply_repair_plan(db,changed,expected_sha256=changed['sha256'])
                db.rollback()
        tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256']);db.commit();db.refresh(row)
        assert row.outcome['official_corporate_action']['session_dates']==before['outcome']['official_corporate_action']['session_dates']
        tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'],rollback=True);db.commit();db.refresh(row)
        assert result_snapshot(row)==before


def test_official_verification_preserves_existing_float_tolerance(db):
    cal,c,ids,before=seed(db);tool=api();request=tool.inventory(db,calendar=cal);bind=db.get_bind();db.rollback()
    class Rounded(Prices):
        official=False
        def fetch(self,*args,**kwargs):
            values=super().fetch(*args,**kwargs)
            if not self.official:
                for rows in values.values():
                    for row in rows:
                        for field in ('open','high','low','close'):row[field]+=0.000001
            return values
        def official_research_history(self,*args,**kwargs):
            self.official=True
            try:return super().official_research_history(*args,**kwargs)
            finally:self.official=False
    evidence=tool.capture_evidence(bind,request,calendar=cal,provider=Rounded(cal))
    plan=tool.build_repair_plan(bind,request,calendar=cal,evidence=evidence)
    assert len(plan['results'])==5
    assert tool.apply_repair_plan(db,plan,expected_sha256=plan['sha256'])['written']==5


@pytest.mark.parametrize('has_event',[False,True])
def test_prior_split_cannot_become_a_validated_return_when_event_disappears(db,has_event):
    cal,c,ids,before=seed(db);tool=api()
    for row in db.scalars(select(Result)):
        row.outcome=dict(row.outcome)|{'split_event':{'date':'2026-06-03','ratio':2}}
    db.commit();request=tool.inventory(db,calendar=cal);bind=db.get_bind();db.rollback()
    class Split(Prices):
        def fetch(self,*args,**kwargs):
            values=super().fetch(*args,**kwargs)
            if has_event:
                for symbol,rows in values.items():
                    if symbol!='TAIEX':
                        for row in rows:
                            if row['date']=='2026-06-03':row['stock_splits']=2
            return values
    evidence=tool.capture_evidence(bind,request,calendar=cal,provider=Split(cal))
    if has_event:
        plan=tool.build_repair_plan(bind,request,calendar=cal,evidence=evidence)
        assert {e['after']['skip_reason'] for e in plan['results']}=={'stock_split_in_window'}
        changed=deepcopy(plan);changed['results'][0]['after']['outcome']['split_event']['ratio']=3
        changed['sha256']=tool.fingerprint({k:v for k,v in changed.items() if k!='sha256'})
        with pytest.raises(ValueError,match='next_open_official_split_unverified'):
            tool.apply_repair_plan(db,changed,expected_sha256=changed['sha256'])
    else:
        with pytest.raises(ValueError,match='next_open_repair_incomplete'):
            tool.build_repair_plan(bind,request,calendar=cal,evidence=evidence)


@pytest.mark.parametrize('tamper_input',[False,True])
def test_newly_discovered_distribution_cannot_be_changed_to_a_validated_return(replay,monkeypatch,tamper_input):
    from datetime import timedelta
    from test_next_open_corporate_actions import CASES,TARGET,_iso_roc
    from ai_stock_sentinel.db.models import TaiwanDailyBar
    from ai_stock_sentinel.calibration import twse_rights_recovery as rights
    from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
    from ai_stock_sentinel.daily_radar.research_validation import evaluate_research_window
    archive,fault,engine,run,calls=replay
    symbol,signal,window,reason,_=CASES[0]
    run(symbol,signal,window,seed=True)
    closure={k:c.cell_contents for k,c in zip(run.__code__.co_freevars,run.__closure__)}
    provider=RecoveringResearchPriceProvider(official_getter=closure['official_get'])
    monkeypatch.setattr(YFinanceResearchPriceProvider,'fetch',lambda self,*a,**k:closure['yahoo'](*a,**k))
    positive={date.fromisoformat(_iso_roc(row[0])) for key,value in archive.items() if key.startswith('twse-index-')
        for row in value.get('tables',[value])[0]['data']}
    cal=calendar(signal,TARGET,{signal+timedelta(days=i) for i in range((TARGET-signal).days+1)}-positive)
    TaiwanDailyBar.__table__.create(engine,checkfirst=True)
    with Session(engine) as db:
        row=db.scalar(select(Result));row.status='validated';row.skip_reason=None
        row.outcome={'return_basis':'next_open','price_basis':'unadjusted_price','historical_repair':{'fixture':'legacy'}}
        db.add(DailyRadarPreparedRun(run_date=TARGET,market='TW',symbol_count=0,selected_symbols=[],universe=[],
            step_statuses={'validation-calendar':cal.payload()}));db.commit()
        before=result_snapshot(row);tool=api();request=tool.inventory(db,calendar=cal);db.rollback()
        evidence=tool.capture_evidence(engine,request,calendar=cal,provider=provider)
        plan=tool.build_repair_plan(engine,request,calendar=cal,evidence=evidence)
        assert plan['results'][0]['after']['skip_reason']==reason
        changed=deepcopy(plan);entry=changed['results'][0];outcome=entry['after']['outcome']
        proof=outcome['reviewed_official_window']
        proof['calculation_stock']=[r for r in rights._unadjusted_rows(proof['stock'],'test') if r['date']!=str(signal)]
        proof['calculation_benchmark']=rights._unadjusted_rows(proof['benchmark'],'test')
        candidate=forward_validation_candidates_from_runs(db,market='TW',candidate_ids=[row.candidate_id],end_date=TARGET)[0]
        result=evaluate_research_window(candidate,price_series=proof['calculation_stock'],
            benchmark_prices=proof['calculation_benchmark'],window_days=window,as_of_date=TARGET,
            benchmark_symbol='TAIEX',trading_calendar=cal.sessions)
        assert result['status']=='validated'
        entry['after'].update(status='validated',skip_reason=None,target_date=result['target_date'],
            outcome=result['outcome']|{k:outcome[k] for k in ('reviewed_official_window','official_corporate_action','historical_repair')})
        if tamper_input:
            frozen=proof['event_replay'];frozen['row']['status']='validated'
            frozen['row']['outcome']=result['outcome']|frozen['row']['outcome']
            entry['after']['outcome']=deepcopy(frozen['row']['outcome'])|{
                k:outcome[k] for k in ('reviewed_official_window','official_corporate_action','historical_repair')}
            entry['after']['skip_reason']=reason
        changed['sha256']=tool.fingerprint({k:v for k,v in changed.items() if k!='sha256'})
        with pytest.raises(ValueError,match='next_open_official_event_|next_open_repair_incomplete'):
            tool.apply_repair_plan(db,changed,expected_sha256=changed['sha256'])
        assert result_snapshot(row)==before
        db.rollback()
        changed=deepcopy(plan);outcome=changed['results'][0]['after']['outcome']
        outcome['forward_return_pct']=123.45
        outcome['reviewed_official_window']['event_replay']['row']['outcome']['forward_return_pct']=123.45
        changed['sha256']=tool.fingerprint({k:v for k,v in changed.items() if k!='sha256'})
        with pytest.raises(ValueError,match='next_open_official_event_identity_invalid'):
            tool.apply_repair_plan(db,changed,expected_sha256=changed['sha256'])
        assert result_snapshot(row)==before
