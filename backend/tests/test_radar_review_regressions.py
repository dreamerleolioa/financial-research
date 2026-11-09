from datetime import date
from sqlalchemy import select
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult,RadarWorkItem
from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results,FORWARD_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.jobs import create_job,unit
from ai_stock_sentinel.daily_radar.work_queue import claim_work
from test_radar_jobs import db,NOW
from test_daily_radar_forward_validation import _add_run,_add_candidate


def test_older_collected_skip_cannot_replace_newer_validated_outcome(db):
    candidate=_add_candidate(db,_add_run(db));db.commit()
    row={'candidate_id':candidate.id,'signal_date':'2026-06-01','window_days':5,'validation_version':FORWARD_VALIDATION_VERSION,
        'benchmark_symbol':'TAIEX','evaluation_as_of_date':'2026-06-09','target_date':'2026-06-08','status':'validated','skip_reason':None,'outcome':{}}
    upsert_forward_validation_results(db,[row]);db.commit()
    key=(candidate.id,5,FORWARD_VALIDATION_VERSION)
    old=row|{'evaluation_as_of_date':'2026-06-08','target_date':None,'status':'skipped','skip_reason':'missing_future_price'}
    written=upsert_forward_validation_results(db,[old],expected_revisions={key:None})
    db.commit()
    saved=db.scalar(select(DailyRadarForwardValidationResult))
    assert written['records_written']==0 and saved.status=='validated' and saved.evaluation_as_of_date==date(2026,6,9)


def test_required_scoring_is_claimed_before_slow_auxiliary(db):
    job=create_job(db,run_date=date(2026,11,6),lanes=['publication'],now=NOW)
    for work in db.scalars(select(RadarWorkItem)):work.state='ready'
    optional=unit(db,job,'avwap',scope={'symbol':'2330.TW'},lane='auxiliary',required=False,now=NOW)
    scoring=unit(db,job,'scoring',now=NOW)
    db.commit()
    claim=claim_work(db,job_id=job.id,now=NOW)
    assert claim.work_id==scoring.id and claim.work_id!=optional.id


def test_result_changed_at_same_cutoff_is_not_overwritten_by_stale_collection(db):
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_result_revision
    candidate=_add_candidate(db,_add_run(db));db.commit()
    key=(candidate.id,5,FORWARD_VALIDATION_VERSION)
    row={'candidate_id':candidate.id,'signal_date':'2026-06-01','window_days':5,'validation_version':FORWARD_VALIDATION_VERSION,
        'benchmark_symbol':'TAIEX','evaluation_as_of_date':'2026-06-08','target_date':None,'status':'skipped',
        'skip_reason':'missing_future_price','outcome':{}}
    upsert_forward_validation_results(db,[row]);db.commit()
    expected=forward_validation_result_revision(db.scalar(select(DailyRadarForwardValidationResult)))
    newer=row|{'status':'validated','target_date':'2026-06-08','skip_reason':None,'outcome':{'corrected':True}}
    upsert_forward_validation_results(db,[newer]);db.commit()
    written=upsert_forward_validation_results(db,[row],expected_revisions={key:expected});db.commit()
    saved=db.scalar(select(DailyRadarForwardValidationResult))
    assert written['records_written']==0 and saved.status=='validated' and saved.outcome=={'corrected':True}


def test_actual_window_acceptance_preserves_a_result_from_a_later_cutoff(db,monkeypatch):
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_queue import WorkClaim
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
    from ai_stock_sentinel.daily_radar.validation_preparation import ValidationPreparation
    from ai_stock_sentinel.db.models import DailyRadarPreparedRun
    from ai_stock_sentinel.daily_radar import validation_execution
    from radar_calendar_support import calendar
    signal,asof=date(2026,6,1),date(2026,6,8)
    candidate=_add_candidate(db,_add_run(db));cid=candidate.id
    proof=calendar(signal,asof)
    db.add(DailyRadarPreparedRun(run_date=asof,market='TW',status='prepared',selected_symbols=[],symbol_count=0,
        universe=[],errors=[],step_statuses={'validation-calendar':proof.payload()}));db.commit()
    candidates=forward_validation_candidates_from_runs(db,market='TW',start_date=signal,end_date=signal)
    old={'candidate_id':cid,'signal_date':str(signal),'window_days':5,'validation_version':FORWARD_VALIDATION_VERSION,
        'benchmark_symbol':'TAIEX','evaluation_as_of_date':str(asof),'target_date':None,'status':'skipped','skip_reason':'missing_future_price','outcome':{}}
    monkeypatch.setattr(validation_execution,'run_signal_close_validation',lambda *a,**k:ValidationPreparation(
        candidates=candidates,outcomes=[old],calendar=proof,as_of_date=asof,return_basis='signal_close'))
    scope={'signal_date':str(signal),'as_of_date':str(asof),'candidate_id':cid,'window_days':5,'return_basis':'signal_close','validation_version':FORWARD_VALIDATION_VERSION}
    claim=WorkClaim(1,'token',1,'a'*64,'window','signal_close',asof,scope)
    collected=WorkHandlers(db).collect_window(claim);db.rollback()
    newer=old|{'evaluation_as_of_date':'2026-06-09','target_date':'2026-06-08','status':'validated','skip_reason':None}
    upsert_forward_validation_results(db,[newer]);db.commit()
    result=collected.apply(db);db.commit()
    assert result['state']=='needs_review' and result['reason']=='newer_validation_result_preserved'
    saved=db.scalar(select(DailyRadarForwardValidationResult))
    assert saved.status=='validated' and saved.evaluation_as_of_date==date(2026,6,9)
