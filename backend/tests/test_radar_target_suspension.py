from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import pytest
from ai_stock_sentinel.daily_radar.trading_status import KEY, REASON, VERSION
from ai_stock_sentinel.daily_radar.validation_execution import _collect_locked_signal_prices
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.daily_radar.terminal_integrity import terminal_evidence_matches
from test_radar_price_acceptance import CANDIDATE, DAYS, bar, official
from test_radar_jobs import db


def proof(target=DAYS[-1], resume="115/06/05"):
    from ai_stock_sentinel.calibration.official_corporate_actions import TWSE_REDUCTION_DETAIL
    from ai_stock_sentinel.calibration.twse_rights_recovery import REDUCTION
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
    from ai_stock_sentinel.daily_radar.trading_status import collect_target_suspension
    from urllib.parse import urlencode
    code = CANDIDATE['symbol'].split('.')[0]
    notice = {'恢復買賣日期':resume,'股票代號':code,'減資原因':'退還股款',
              '詳細資料':code+',20260603','停止買賣前收盤價格':100}
    detail = {'股票代號：':code,'停止買賣日期：':'115/06/04','每壹仟股換發新股票：':'800 股',
              '每股退還股款：':'2 元/股','原股每股配發現金股利：':'0 元/股','減資並(有償)現金增資：':'0 股',
              '每股認購金額：':'0 元/股','按股東持股比例每千股認購：':'0 股'}
    def capture(path,params):
        row = {REDUCTION:notice,TWSE_REDUCTION_DETAIL:detail}[path]
        raw = {'stat':'OK','fields':list(row),'data':[list(row.values())]}
        if path == REDUCTION:
            raw.update(strDate=params['startDate'],endDate=params['endDate'])
        return raw,{'source_url':'https://www.twse.com.tw'+path+'?'+urlencode(params),
                    'captured_at':'2026-06-04T10:00:00+00:00','snapshot_sha256':fingerprint(raw)}
    return collect_target_suspension(SimpleNamespace(capture_twse_report=capture),CANDIDATE['symbol'],target)


def evaluate(evidence, fetched):
    base=official([bar(DAYS[0],100)])
    source=SimpleNamespace(**(vars(base)|{'target_suspension':evidence}))
    def fetch(*args,**kwargs): fetched.append(1);return {}
    return _collect_locked_signal_prices([CANDIDATE],None,
        calendar=SimpleNamespace(sessions=DAYS,through=DAYS[-1]),
        request=DailyRadarForwardValidationRunRequest(windows=[1]),
        collect_official=lambda *args:source,fetch_prices=fetch)[0]


def test_proven_target_halt_closes_without_requesting_unusable_prices():
    fetched=[];row=evaluate(proof(),fetched)
    assert row['skip_reason']==REASON
    assert row['target_date']==str(DAYS[-1])
    assert fetched==[]
    assert 'forward_return_pct' not in row['outcome']
    assert terminal_evidence_matches(CANDIDATE,row,basis='signal_close',sessions=DAYS[1:],as_of_date=DAYS[-1])


@pytest.mark.parametrize('change', [None, {'symbol':'9999.TW'}, {'target_date':'2026-06-05'},
    {'halt_start':'2026-06-05'},{'resume_date':'2026-06-04'}, {'sources':[]}, {'version':'unknown'}])
def test_missing_wrong_symbol_date_or_unproven_halt_never_closes(change):
    evidence=None if change is None else proof()|change
    row=evaluate(evidence,[])
    assert row['skip_reason']!='target_session_suspended'
    assert row['status']!='validated'


def test_forged_persisted_halt_cannot_pass_terminal_integrity():
    row=evaluate(proof(),[])
    row['outcome'][KEY]['symbol']='9999.TW'
    assert not terminal_evidence_matches(CANDIDATE,row,basis='signal_close',sessions=DAYS[1:],as_of_date=DAYS[-1])


def test_committed_suspension_survives_a_late_missing_price_writer(db):
    from sqlalchemy import select
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
    from test_daily_radar_forward_validation import _add_candidate, _add_run
    candidate=_add_candidate(db,_add_run(db,run_date=DAYS[0]),symbol=CANDIDATE['symbol'])
    row=evaluate(proof(),[])|{'candidate_id':candidate.id}
    assert upsert_forward_validation_results(db,[row],calendar=SimpleNamespace(sessions=DAYS))['records_written']==1
    db.commit()
    stale=row|{'skip_reason':'official_validation_price_missing','target_date':None,
              'outcome':{'expected_target_date':str(DAYS[-1])}}
    assert upsert_forward_validation_results(db,[stale],calendar=SimpleNamespace(sessions=DAYS))['records_written']==0
    db.commit()
    saved=db.scalar(select(DailyRadarForwardValidationResult))
    assert saved.skip_reason==REASON and saved.outcome[KEY]==proof()
