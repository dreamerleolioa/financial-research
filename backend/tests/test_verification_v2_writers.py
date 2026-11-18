"""Probe stale writers that reach the commit lock before an earlier owner commits."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from queue import Queue
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select,text
from sqlalchemy.orm import Session

from test_radar_job_postgres import pg_engine
from test_daily_radar_forward_validation import _add_run,_add_candidate
from test_radar_target_suspension import evaluate,proof
from test_radar_price_acceptance import DAYS,CANDIDATE
from ai_stock_sentinel.db.models import Base, DailyRadarForwardValidationResult as Result
from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
from ai_stock_sentinel.daily_radar.trading_status import matches,KEY,REASON
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint


@pytest.mark.parametrize('winner', ['legacy_return','suspension'])
def test_two_writers_wait_on_same_window_then_preserve_the_first_accepted_result(pg_engine,winner):
    Base.metadata.create_all(pg_engine)
    calendar=SimpleNamespace(sessions=DAYS)
    with Session(pg_engine) as setup:
        c=_add_candidate(setup,_add_run(setup,run_date=DAYS[0]),symbol=CANDIDATE['symbol'])
        halt=evaluate(proof(),[])|{'candidate_id':c.id}
        row=Result(candidate_id=c.id,signal_date=DAYS[0],window_days=1,
            validation_version=halt['validation_version'],benchmark_symbol='TAIEX',
            evaluation_as_of_date=DAYS[-1],status='skipped',target_date=None,
            skip_reason='official_validation_price_missing',outcome={})
        setup.add(row);setup.commit();pk=row.id
    with ThreadPoolExecutor(max_workers=1) as pool, Session(pg_engine) as owner:
        locked=owner.scalar(select(Result).where(Result.id==pk).with_for_update())
        owner_pid=owner.scalar(text('select pg_backend_pid()'))
        if winner=='legacy_return':
            locked.status='validated';locked.skip_reason=None;locked.target_date=DAYS[-1]
            locked.outcome={'forward_return_pct':10.0,'entry_price':100.0,'exit_price':110.0}
            stale=halt
        else:
            upsert_forward_validation_results(owner,[halt],calendar=calendar)
            stale=halt|{'skip_reason':'official_validation_price_missing','target_date':None,'outcome':{}}
        owner.flush()
        child=Queue()
        def contend():
            with Session(pg_engine) as db:
                child.put(db.scalar(text('select pg_backend_pid()')))
                result=upsert_forward_validation_results(db,[stale],calendar=calendar)
                db.commit();return result
        fut=pool.submit(contend);child_pid=child.get(timeout=5)
        try:
            end=time.monotonic()+5
            with pg_engine.connect() as observer:
                while True:
                    if owner_pid in observer.scalar(text('select pg_blocking_pids(:pid)'),{'pid':child_pid}):break
                    if time.monotonic()>end:raise AssertionError('no overlapping row lock observed')
                    time.sleep(.03)
        finally:owner.commit()
        summary=fut.result(timeout=10)
    with Session(pg_engine) as observer:
        saved=observer.get(Result,pk)
        assert summary['records_written']==0
        if winner=='legacy_return':
            assert summary['preserved_conflicts'][0]['reason']=='validated_return_conflicts_with_suspension'
            assert saved.status=='validated' and saved.outcome['forward_return_pct']==10.0
        else:
            assert saved.skip_reason==REASON and matches(saved.outcome[KEY],symbol=CANDIDATE['symbol'],target=DAYS[-1])


def test_mismatched_symbol_in_raw_report_with_valid_hash_rejected():
    p=proof()
    position=p['reports'][0]['fields'].index('股票代號')
    p['reports'][0]['data'][0][position]='9999'
    p['sources'][0]['snapshot_sha256']=fingerprint(p['reports'][0])
    assert not matches(p,symbol=CANDIDATE['symbol'],target=DAYS[-1])
