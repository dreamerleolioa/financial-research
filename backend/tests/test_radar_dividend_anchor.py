from datetime import date, timedelta
from copy import deepcopy

import pytest
from ai_stock_sentinel.daily_radar import dividend_anchor as anchor


def sample():
    day = date(2026, 9, 15)
    dates = [str(day - timedelta(days=20-i)) for i in range(21)]
    quote = {"open": 50., "high": 52., "low": 47., "close": 50.}
    stock = [{"date": d, "price_source": "twse_stock_day", **quote} for d in dates]
    index = [r | {"price_source":"twse_index_history"} for r in stock]
    ohlcv = {k:v*.94 for k,v in quote.items()}
    levels = {"support_level": 47*.94, "resistance_level":52*.94, "ma20":47.}
    quality = {"ohlcv_aligned":True, "data_date":dates[-1], "price_level_data_date":dates[-2],
               "price_level_basis":"ohlc_high_low", "price_level_completed_bars_only":True}
    record = {"symbol":"1234.TW", "record_date":dates[-1], "ohlcv":ohlcv.copy(), "indicators":levels.copy(),
              "technical_profile":{"data_quality":quality}, "price_history":[{"date":d,"close":47.} for d in dates]}
    snap = {"replay_input":{"record":record}, "ohlcv":ohlcv, "indicators":levels}
    candidate = {"symbol":"1234.TW","record_date":dates[-1],"input_snapshot":deepcopy(snap)}
    return candidate, snap, stock, index


@pytest.mark.parametrize("problem", [None,"support","wrong_basis","missing_stock","different_sessions","changed_close",
                                     "wrong_company","misdated_levels","published_level_changed","bad_signal_high"])
def test_candidate_scale_needs_original_levels_and_each_historical_session(problem):
    c,s,stock,index=sample()
    rec=s['replay_input']['record']
    if problem=='support':rec['indicators']['support_level']+=1
    if problem=='wrong_basis':rec['technical_profile']['data_quality']['price_level_basis']='close_only'
    if problem=='missing_stock':stock.pop(1)
    if problem=='different_sessions':index.pop(2)
    if problem=='changed_close':rec['price_history'][1]['close']+=.1
    if problem=='wrong_company':rec['symbol']='9999.TW'
    if problem=='misdated_levels':rec['technical_profile']['data_quality']['price_level_data_date']=rec['record_date']
    if problem=='published_level_changed':c['input_snapshot']['indicators']['resistance_level']+=1
    if problem=='bad_signal_high':stock[-1]['high']+=1
    assert bool(anchor.prove_history(c,s,stock,index,.94)) == (problem is None)


@pytest.mark.parametrize("fault", [None, "wrong_ex_date", "cash_mismatch", "missing_receipt_coverage",
                                   "other_event", "bad_window_high", "changed_reference"])
def test_first_session_dividend_recovers_published_units_only(fault):
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceEvidence
    c,s,history,index=sample()
    c["candidate_id"]=1
    signal=date.fromisoformat(c["record_date"])
    days=[signal+timedelta(days=i) for i in range(3)]
    quote=history[-1]; stock={str(d):quote|{"date":str(d)} for d in days}
    benchmark={k:r|{"price_source":"twse_index_history"} for k,r in stock.items()}
    vendor=[r|{field:r[field]*(.94 if i==0 else 1.) for field in ("open","high","low","close")}
            | {"open_observed":True,"source_capture":{"snapshot_sha256":"a"*64}} for i,r in enumerate(stock.values())]
    event={"effective_date":str(days[1]),"family":"rights","notice":"息","previous_close":50.,"cash_dividend":3.}
    coverage=set(stock)
    events=[event]
    if fault=="wrong_ex_date": event["effective_date"]=str(days[2])
    if fault=="cash_mismatch": event["cash_dividend"]=3.1
    if fault=="missing_receipt_coverage": coverage.remove(str(days[-1]))
    if fault=="other_event": events.append({"effective_date":str(days[2]),"family":"reduction"})
    if fault=="bad_window_high":vendor[1]["high"]+=1.
    if fault=="changed_reference":s['ohlcv']['high']+=1.
    official=OfficialPriceEvidence(stock,benchmark,events,event_coverage=frozenset(coverage))
    provider=SimpleNamespace(official_research_history=lambda ticker,**kw:index if ticker=="TAIEX" else history)
    c['input_snapshot'].pop('replay_input')
    accepted=anchor.recover(c,days,vendor,official,provider,lambda _:s)
    if fault:
        assert accepted is None or accepted.reason
    else:
        assert accepted.proof["basis_evidence"]["kind"]=="verified_ex_dividend_anchor"
        assert [r["close"] for r in accepted.stock]==pytest.approx([47.,50.,50.])


def test_acceptance_refuses_changed_published_inputs():
    from unittest.mock import Mock
    c,s,stock,index=sample()
    proof=anchor.prove_history(c,s,stock,index,.94)
    outcome={"outcome":{"validation_price_evidence":{"basis_evidence":proof}}}
    db=Mock()
    db.scalar.return_value=s
    anchor.check_published_inputs(db,123,outcome)
    s['replay_input']['record']['price_history'][-2]['close']+=1
    with pytest.raises(ValueError,match="signal_price_anchor_inputs_changed"):
        anchor.check_published_inputs(db,123,outcome)
