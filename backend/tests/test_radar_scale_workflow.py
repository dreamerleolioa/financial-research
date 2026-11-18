"""Real PostgreSQL/HTTP/driver rehearsal; providers use fixed, explicit evidence.

RADAR_SCALE_CANDIDATES=15500 selects the production-volume acceptance run.
The default retains the same lifecycle with a smaller CI dataset.
"""
from datetime import date, timedelta
import json
import os
import time
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text, select, update, func
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import (Base, DailyRadarPreparedRun, StockRawData,
    SharedBackgroundContext, TaiwanDailyBar, RadarWorkItem, RadarWorkAttempt)
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.daily_radar import jobs, jobs_router, readiness_router
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.planning_inventory import inventory_input_revision
from ai_stock_sentinel.daily_radar.readiness import read_readiness
from ai_stock_sentinel.daily_radar.run_router import execute_prepared_scoring
from ai_stock_sentinel.daily_radar.schemas import DailyRadarRefreshStepRequest
from radar_calendar_support import calendar
from run_daily_radar_jobs import run as drive
from test_daily_radar_api import (_technical_payload, _market_context,
    _completed_segmented_step_statuses, _persist_required_institutional_archive)
from test_radar_job_postgres import pg_engine, _migration

DAY = date(2026, 11, 16)


def seed_history(db, count, missing):
    days = [date(2026, 1, 5) + timedelta(days=i) for i in range(110)
            if (date(2026, 1, 5) + timedelta(days=i)).weekday() < 5][:(count + 229)//230]
    db.execute(text("INSERT INTO daily_radar_runs(run_date,market,status,universe_count,prefilter_count,candidate_count) "
                    "SELECT d,'TW','completed',230,230,230 FROM unnest(CAST(:days AS date[])) d"), {'days': days})
    # Large, TOASTed replay blobs with compact validation inputs. The repeated
    # block compresses to roughly 16 KiB while decompression yields 128 KiB.
    import hashlib
    noise = ''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(256)) * 8
    snapshot = {'ohlcv': {'close': 100}, 'indicators': {'support_level':90,'resistance_level':110,
                'ma20':100,'ma60':100}, 'versions': {'scoring_version': 's1'},
                'selection_version': 'v1', 'replay_history': noise}
    db.execute(text("""
        INSERT INTO daily_radar_candidates(run_id,symbol,name,primary_bucket,secondary_buckets,
          observation_score,bucket_scores,risk_labels,matched_rules,explanation,repeat_status,
          score_breakdown,input_snapshot,data_dates,selection_status)
        SELECT r.id, (1200+(g-1)%230)::text || '.TW', 'scale', 'support_retest','[]',
          90,'{}','[]','[]','fixture','new','{}',CAST(:snapshot AS jsonb),
          jsonb_build_object('ohlcv',(r.run_date-1)::text),'selected'
        FROM generate_series(1,:count) g JOIN daily_radar_runs r
          ON r.run_date=(CAST(:days AS date[]))[1+(g-1)/230]
    """), {'snapshot': json.dumps(snapshot), 'count': count, 'days': days})
    db.execute(text("""
        INSERT INTO daily_radar_forward_validation_results(candidate_id,signal_date,window_days,
            validation_version,benchmark_symbol,evaluation_as_of_date,status,skip_reason,outcome)
        SELECT c.id,r.run_date,w.days,v.version,'TAIEX',:day,'skipped','stale_candidate_price','{}'
        FROM daily_radar_candidates c JOIN daily_radar_runs r ON r.id=c.run_id
        CROSS JOIN (VALUES (1,5),(2,10),(3,20),(4,40),(5,60)) w(n,days)
        CROSS JOIN (VALUES (0,'daily-radar-forward-validation-v2'),(1,'daily-radar-next-open-price-v2')) v(n,version)
        WHERE (c.id-1)*10+(w.n-1)*2+v.n+1 <= :saved
    """), {'day': DAY, 'saved': count*10-missing})
    # Exercise both accepted returns and evidence-backed terminal outcomes in
    # the missing tail. Never change inputs of a pre-existing saved window.
    db.execute(text("""
        UPDATE daily_radar_candidates c SET data_dates=jsonb_build_object('ohlcv',r.run_date::text)
        FROM daily_radar_runs r WHERE r.id=c.run_id AND (c.id-1)*10 >= :saved AND c.id%2=0
    """), {'saved': count*10-missing})
    db.commit()


def seed_publication(db):
    symbols = [f'{1200+i}.TW' for i in range(230)]
    steps = _completed_segmented_step_statuses(DAY)
    steps['validation-calendar'] = calendar(DAY-timedelta(days=364), DAY).payload()
    market_context = _market_context()
    market_context['record_date'] = str(DAY)
    market_context['data_dates'] = {'market_index': str(DAY)}
    market_context['market']['data_date'] = str(DAY)
    db.add(DailyRadarPreparedRun(run_date=DAY, market='TW', selected_symbols=symbols,
        symbol_count=230, universe=[], market_context=market_context, step_statuses=steps))
    for symbol in symbols:
        technical = _technical_payload(symbol, DAY)
        technical['ohlcv']['avg_volume_20'] = 4_000_000
        db.add(StockRawData(symbol=symbol, record_date=DAY, raw_data_is_final=True,
            technical=technical,
            institutional={'institutional_flow': {'three_party_net_shares':1000000,
                'consecutive_positive_days':3,'flow_state':'buying','net_flow_to_avg_volume':.5},
                'data_dates':{'institutional_flow':str(DAY)}},
            fundamental={'margin':{'margin_delta_pct':1.,'margin_to_volume':.1},
                'data_dates':{'margin':str(DAY)}}))
        quote = technical['ohlcv']
        db.add(TaiwanDailyBar(symbol=symbol, market='TW', trade_date=DAY, name=symbol,
            **{k:quote[k] for k in ('open','high','low','close')}, volume=4_000_000, amount=424_000_000,
            dataset='taiwan_market_daily_ohlcv', adjustment_mode='unadjusted',
            source_provider='official', source_dataset='fixture', is_final=True))
        for kind in ('lending','full_margin'):
            db.add(SharedBackgroundContext(symbol=symbol, context_type=kind,
                applicable_consumers=['daily_radar'], source={'provider':'fixture'},
                as_of_date=DAY, freshness='fresh', payload={'latest_margin_balance':400, 'margin_balance_delta_pct':1},
                replay_key=f'{symbol}:{kind}:{DAY}'))
    _persist_required_institutional_archive(db, run_date=DAY, include_market_bars=False)
    result = execute_prepared_scoring(db, payload=DailyRadarRefreshStepRequest(run_date=DAY))
    assert result.status == 'completed', result.errors[:1]
    # This is the persisted state after completed publication refreshes. Each
    # prerequisite is also checked against its actual stored data by readiness.
    jobs.create_job(db, run_date=DAY, lanes=['publication'])
    db.execute(update(RadarWorkItem).where(RadarWorkItem.lane == 'publication').values(state='ready'))
    db.commit()
    return result


def test_large_inventory_publication_resume_and_final_audit(pg_engine, monkeypatch, tmp_path):
    count = int(os.getenv('RADAR_SCALE_CANDIDATES', '230'))
    missing = 1088 if count >= 10000 else 40
    monkeypatch.setenv('DAILY_RADAR_DURABLE_EXECUTION_ENABLED','true')
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED','true')
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceCollector
    from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceEvidence
    def bars(start, end):
        return [dict(date=str(start+timedelta(days=i)),open=100.,high=110.,low=90.,close=100.,
                     price_basis='unadjusted_price',price_source='fixture',corporate_actions_checked=True,
                     dividends=0.,stock_splits=0.) for i in range((end-start).days+1)
                if (start+timedelta(days=i)).weekday()<5]
    def fixed_prices(provider, symbols, *, start_date, end_date):
        return {symbol:bars(start_date,end_date) for symbol in symbols}
    def fixed_official(provider, symbol, start, end):
        values={row['date']:row for row in bars(start,end)}
        return OfficialPriceEvidence(values, values, [])
    monkeypatch.setattr(YFinanceForwardPriceProvider, 'fetch', fixed_prices)
    monkeypatch.setattr(RecoveringResearchPriceProvider, 'fetch', fixed_prices)
    monkeypatch.setattr(OfficialPriceCollector, 'collect', fixed_official)
    Base.metadata.create_all(pg_engine)
    started = time.monotonic()
    with pg_engine.begin() as conn:
        conn.execute(text('DROP TRIGGER radar_validation_projection_v1 ON daily_radar_candidates'))
        conn.execute(text('DROP FUNCTION radar_validation_projection_v1()'))
        conn.execute(text('ALTER TABLE daily_radar_candidates DROP COLUMN validation_input_hash, DROP COLUMN validation_projection'))
    with Session(pg_engine) as db:
        seed_history(db, count, missing)
    t = time.monotonic()
    with pg_engine.begin() as conn:
        _migration(conn, 'ef6a7b8c9d0e_add_radar_validation_projection.py')
        assert conn.scalar(text('SELECT count(*) FROM daily_radar_candidates WHERE length(validation_input_hash)=32 AND validation_projection IS NOT NULL')) == count
    migration_seconds = time.monotonic()-t
    with Session(pg_engine) as db:
        publication = seed_publication(db)
        check = read_readiness(db, run_date=DAY, scope='publication')
        assert check['publication']['output_complete'] is True
        assert check['publication']['input_manifest']['matches_current_inputs'] is True
        assert check['scoring']['ready'] is True
        assert check['data_ready'] is None
        t = time.monotonic()
        revision = inventory_input_revision(db, as_of_date=DAY)
        epoch_seconds = time.monotonic()-t
        assert epoch_seconds < 5
        before = read_readiness(db, run_date=DAY)
        assert sum(x['unresolved_count'] for x in before['validation'].values()) == missing
        assert before['data_ready'] is False
        job = jobs.create_job(db, run_date=DAY, lanes=['signal_close','next_open'])
        job_id = job.id
        db.commit()
    app = FastAPI()
    app.include_router(jobs_router.router)
    app.include_router(readiness_router.router)
    def session():
        with Session(pg_engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[require_daily_radar_internal_auth] = lambda: None
    results, latencies = [], []
    with TestClient(app) as client:
        def request(method, url, token, payload=None, **kwargs):
            parsed = urlsplit(url)
            t = time.monotonic()
            response = client.request(method, parsed.path + ('?'+parsed.query if parsed.query else ''), json=payload)
            latencies.append(time.monotonic()-t)
            response.raise_for_status()
            return response.json()
        def read(url, token, **kw): return request('GET',url,token,**kw)
        def write(url, token, payload, **kw): return request('POST',url,token,payload,**kw)
        published = drive(endpoint='https://fixture.test',token='fixture',output_dir=tmp_path/'publication',
                          run_date=str(DAY),require_lanes=('publication',),read=read,write=write)
        assert published['readiness_deadline_passed'] is True
        # A bounded early pass checkpoints progress. The actual deadline pass
        # must complete the day's 1,088 gaps within its own existing time budget.
        early = drive(endpoint='https://fixture.test',token='fixture',output_dir=tmp_path/'early',
                      resume=True,max_work=150,max_seconds=1200,read=read,write=write)
        results.append(early)
        result = drive(endpoint='https://fixture.test',token='fixture',output_dir=tmp_path/'deadline',
                       run_date=str(DAY),require_lanes=('signal_close','next_open'),
                       max_work=2000,max_seconds=1200,read=read,write=write)
        results.append(result)
        assert result['status'] == 'recorded_or_advanced', result
        assert result['readiness_deadline_passed'] is True, result
        current = client.get(f'/internal/daily-radar/jobs/{job_id}').json()
        assert current['intent_complete'], current
        final = client.get(f'/internal/daily-radar/readiness?run_date={DAY}').json()
        (tmp_path/'final-readiness.json').write_text(json.dumps(final,indent=2))
        assert final['data_ready'] is True, final
        assert all(v['ready'] for v in final['validation'].values())
        assert final['publication']['run_id'] == publication.run_id
        # Another scheduler invocation must not recreate every completed window.
        again = drive(endpoint='https://fixture.test',token='fixture',output_dir=tmp_path/'again',
                      resume=True,read=read,write=write)
        assert again['advanced_requests'] == 0
    with Session(pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(RadarWorkItem).where(RadarWorkItem.stage=='window')) <= missing
        assert db.scalar(select(func.count()).select_from(RadarWorkAttempt).where(RadarWorkAttempt.state=='failed_internal')) == 0
        assert inventory_input_revision(db, as_of_date=DAY) == revision
        recovered = dict(db.execute(text("""
            SELECT status,count(*) FROM daily_radar_forward_validation_results
            WHERE id > :saved GROUP BY status
        """), {'saved':count*10-missing}).all())
        assert recovered.get('validated', 0) > 0, recovered
        assert recovered.get('skipped', 0) > 0, recovered
        assert sum(recovered.values()) == missing
    metrics = {'candidates':count,'history_windows':count*10,'recovered_windows':missing,
               'published_candidates':publication.candidate_count,'driver_invocations':len(results),
               'recovered_statuses':recovered,'migration_seconds':migration_seconds,
               'epoch_seconds':epoch_seconds,'max_request_seconds':max(latencies),
               'total_seconds':time.monotonic()-started}
    (tmp_path/'scale-metrics.json').write_text(json.dumps(metrics,indent=2))
    print('RADAR_SCALE_METRICS',json.dumps(metrics),flush=True)
