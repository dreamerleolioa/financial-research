from datetime import date
from sqlalchemy import select
from ai_stock_sentinel.db.models import DailyRadarPreparedRun,RadarWorkItem
from ai_stock_sentinel.daily_radar.integrity import background_gaps
from ai_stock_sentinel.daily_radar.jobs import create_job,reconcile_job
from test_radar_jobs import db,NOW


def test_missing_live_contexts_are_not_proven_by_completed_step_flags(db):
    row=DailyRadarPreparedRun(run_date=date(2026,11,6),market='TW',status='prepared',selected_symbols=['2330.TW'],
        symbol_count=1,universe=[],errors=[],step_statuses={'refresh-lending':{'status':'completed'},'refresh-full-margin':{'status':'completed'}})
    db.add(row);db.commit()
    assert background_gaps(db,row)=={'refresh-lending':['2330.TW'],'refresh-full-margin':['2330.TW']}


def test_readiness_audit_requeues_deleted_raw_data_even_with_completed_status(db,monkeypatch):
    from ai_stock_sentinel.daily_radar import readiness
    row=DailyRadarPreparedRun(run_date=date(2026,11,6),market='TW',status='prepared',selected_symbols=['2330.TW'],
        symbol_count=1,universe=[],errors=[],step_statuses={'prepare-universe':{'status':'completed'}})
    db.add(row);db.commit()
    jid=create_job(db,run_date=row.run_date,lanes=['publication'],now=NOW).id
    for work in db.scalars(select(RadarWorkItem)):work.state='ready'
    db.commit()
    report={'data_ready':False,'publication':{'status':'completed','output_complete':True,'input_manifest':{'matches_current_inputs':False}},
        'validation':{'signal_close':{'ready':True},'next_open':{'ready':True}},
        'scoring':{'ready':False,'steps':{s:{'status':'completed'} for s in ('refresh-ohlcv','refresh-lending','refresh-full-margin','refresh-market-context')},
            'blockers':[{'reason':'daily_radar_raw_data_incomplete','missing_symbols':['2330.TW']}]}}
    monkeypatch.setattr(readiness,'read_readiness',lambda *a,**k:report)
    reconcile_job(db,jid,now=NOW,audit=True)
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='ohlcv')).state=='pending'
    assert db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage=='scoring')).state=='pending'
