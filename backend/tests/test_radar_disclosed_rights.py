from datetime import date
import pytest

from ai_stock_sentinel.daily_radar import disclosed_rights as rights


def row():
    return ["5434", "崇越", "不適用", "115/11/24", "", "", "115/11/18", "", "", "", "", "",
            "2,500,000", "0.966455076", "400.00", "194,007,983", "115/11/11", "15:47:00", "新台幣10元"]


def test_uses_only_previously_disclosed_next_day_subscription():
    assert rights.subscription_terms([row()], "5434.TW", date(2026, 11, 17))["ratio"] == .00966455076


@pytest.mark.parametrize("column,value", [(0,"1234"),(4,"1"),(6,"115/11/17"),(7,"1"),(10,"115/11/18"),
                                           (13,"0"),(14,"0"),(16,"115/11/18")])
def test_rejects_wrong_identity_mixed_or_late_disclosure(column, value):
    r = row()
    r[column] = value
    assert rights.subscription_terms([r], "5434.TW", date(2026, 11, 17)) is None


def test_ambiguous_notice_fails_closed():
    assert rights.subscription_terms([row(), row()], "5434.TW", date(2026, 11, 17)) is None


@pytest.mark.parametrize("fault", [None, "one_wrong_low", "missing_benchmark", "unannounced", "mixed_capture",
                                   "noncontiguous", "ambiguous_open", "in_window_event", "uncovered"])
def test_only_a_complete_matching_suffix_recovers_official_prices(fault):
    from datetime import timedelta
    from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceEvidence
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
    dates = [date(2026,11,17) - timedelta(days=i) for i in (6,5,1,0)]
    def bars(source):
        return {str(d): {"date":str(d),"open":471., "low":469., "high":483., "close":478.,
                         "price_source":source} for d in dates}
    stock, index = bars("twse_stock_day"), bars("twse_index_history")
    factor = (478 + 400*.00966455076) / (478*(1+.00966455076))
    vendor = [r | {k:r[k] * (factor if i else 1) for k in ("open","high","low","close")}
              | {"source_capture":{"snapshot_sha256":"a"*64}} for i,r in enumerate(stock.values())]
    events=[];coverage=set(stock)
    if fault=="one_wrong_low": vendor[2]["low"]-=.1
    if fault=="missing_benchmark": index.pop(str(dates[0]))
    if fault=="mixed_capture": vendor[0]["source_capture"]={"snapshot_sha256":"b"*64}
    if fault=="noncontiguous": vendor[2].update(stock[str(dates[2])])
    if fault=="ambiguous_open": vendor[0]["open_observed"]=False
    if fault=="in_window_event": events.append({"effective_date":str(dates[1])})
    if fault=="uncovered": coverage.remove(str(dates[1]))
    notice=row()
    if fault=="unannounced": notice[16]="115/11/18"
    report={"rows":[notice]}
    official=OfficialPriceEvidence(stock,index,events,event_coverage=frozenset(coverage))
    candidate={"symbol":"5434.TW","record_date":str(dates[0]),"input_snapshot":{"ohlcv":{"close":"478.00"}}}
    result=rights.recover(candidate,dates,vendor,official,lambda *_:(report,{
        "source_url":rights.URL,"snapshot_sha256":fingerprint(report)}))
    if fault:
        assert result is None
    else:
        assert [r["close"] for r in result.stock]==[478.]*len(dates)
        assert result.proof["basis_evidence"]["ex_date"]=="2026-11-18"
        assert result.proof["factors"]==[1.]*len(dates)


def test_report_parser_requires_two_identified_header_rows():
    from types import SimpleNamespace
    def cells(tag, values):
        return "<tr>"+"".join(f"<{tag}>{v}</{tag}>" for v in values)+"</tr>"
    body = '<table class="hasBorder">'+cells('th', rights.HEADER)+cells('th',rights.SUBHEADER)+cells('td',row())+'</table>'
    def parse(html):
        return rights.report_payload(SimpleNamespace(content=html.encode("utf-8"), raise_for_status=lambda:None), stock_code="5434")
    assert parse(body)=={"rows":[row()]}
    with pytest.raises(ValueError):
        parse(body.replace("現金增資認股比率(%)","不同單位"))


def test_separate_dividend_filing_for_same_ex_date_conflicts():
    cash = row()
    cash[6], cash[7], cash[10] = "", "1.2", "115/11/18"
    assert rights.subscription_terms([row(),cash],"5434.TW",date(2026,11,17)) is None


@pytest.mark.parametrize("retryable,expected", [(True,"validation_price_source_unavailable"),
                                                 (False,"adjustment_factor_unverified")])
def test_signal_collector_distinguishes_provider_backoff_from_unproved_prices(retryable,expected):
    from types import SimpleNamespace
    from test_radar_price_acceptance import bar,official,CANDIDATE,DAYS
    from ai_stock_sentinel.calibration.research_prices import ResearchSourceError
    from ai_stock_sentinel.daily_radar.validation_execution import _collect_locked_signal_prices
    prices=[bar(d,100.) for d in DAYS]
    def unavailable(*args,**kwargs):
        raise ResearchSourceError("unavailable",retryable=retryable,http_status=503)
    row,=_collect_locked_signal_prices([CANDIDATE],None,
        calendar=SimpleNamespace(sessions=DAYS,through=DAYS[-1]),request=SimpleNamespace(windows=[1],benchmark_symbol="TAIEX"),
        collect_official=lambda *_:official(prices),fetch_prices=lambda *_,**kw:{CANDIDATE["symbol"]:[prices[0],bar(DAYS[1],95.)]},
        recover_basis=unavailable)
    assert row["skip_reason"]==expected


def test_snapshot_enabled_api_captures_adjusted_quotes_without_durable_queue(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.session import Base
    from ai_stock_sentinel.calibration.price_provider import get_forward_price_provider
    from ai_stock_sentinel.daily_radar import maintenance_router, validation_execution
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest

    monkeypatch.setenv("DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED", "true")
    monkeypatch.setenv("DAILY_RADAR_DURABLE_EXECUTION_ENABLED", "false")
    day = date(2026,11,17)
    symbol = "5434.TW"
    engine = create_engine(f"sqlite:///{tmp_path / 'captures.sqlite'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        provider = get_forward_price_provider(db)
        monkeypatch.setattr(provider,"fetch",lambda *_,**kw:{symbol:[{"date":str(day),"close":478.}]})
        def collect(db,request,*,price_provider,**kwargs):
            return price_provider.fetch([symbol],start_date=day,end_date=day)[symbol][0]
        monkeypatch.setattr(validation_execution,"run_signal_close_validation",collect)
        row = maintenance_router.run_daily_radar_forward_validation_endpoint(
            DailyRadarForwardValidationRunRequest(as_of_date=day),db,provider,object(),object())
    assert row["source_capture"]["snapshot_sha256"] and row["close"] == 478.
    engine.dispose()
