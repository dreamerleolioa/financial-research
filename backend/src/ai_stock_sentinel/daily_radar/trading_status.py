"""Positive official evidence for a fixed target session that cannot trade.

A bounded upcoming reduction search proves a halt only when its published
interval contains the target. No match never proves that trading was normal.
"""
from datetime import time, timedelta
from ai_stock_sentinel.calibration import official_corporate_actions as actions, twse_rights_recovery as rights
from ai_stock_sentinel.calibration.forward_validation import parse_date

TWSE_HALT = "/rwd/zh/afterTrading/TWTAWU"
TPEX_HALT = "/www/zh-tw/bulletin/sprcHis"

REASON = 'target_session_suspended'
KEY = 'official_target_suspension'
VERSION = 'official-target-suspension-v1'


def _parse_suspension(provider, symbol, target):
    # Use the established reduction parsers, including stock identity and terms.
    # Broader or unrecognized suspensions stay unresolved, never inferred from a gap.
    end = target + timedelta(days=31)
    if symbol.endswith('.TW'):
        params = {'response':'json','startDate':target.strftime('%Y%m%d'),'endDate':end.strftime('%Y%m%d')}
        raw, source = provider.capture_twse_report(rights.REDUCTION, params)
        rows = rights._dated_rows(raw, params, '恢復買賣日期', [], allow_empty_status=True)
        matches = [(d,r) for d,r in rows if str(r['股票代號']).strip()==symbol.split('.')[0]]
        if len(matches)!=1: return None
        resume, notice=matches[0]
        action, detail=actions._twse_reduction(symbol.split('.')[0],resume,notice,provider.capture_twse_report)
        sources=[source,detail]
    elif symbol.endswith('.TWO'):
        matches, source=actions._tpex_events(provider.capture_tpex_report,actions.TPEX_REDUCTIONS,
            (target,end),symbol.split('.')[0],'恢復買賣日期','股票代號',
            ['最後交易日之收盤價格','減資原因','詳細資料'])
        if len(matches)!=1: return None
        resume, notice=matches[0]
        action=actions._tpex_reduction(symbol,resume,notice)
        sources=[source]
    else:
        return None
    if action is None or not action.halt_start <= target < action.effective_date:
        return None
    return {'version':VERSION,'symbol':symbol,'target_date':str(target),'halt_start':str(action.halt_start),
            'resume_date':str(action.effective_date),'event_type':'cash_reduction','sources':sources}


def _report_rows(table, required):
    fields, data = table["fields"], table["data"]
    if (not isinstance(fields, list) or len(set(fields)) != len(fields)
            or not set(required).issubset(fields) or not isinstance(data, list)
            or any(not isinstance(row, list) or len(row) != len(fields) for row in data)):
        raise ValueError("official_halt_report_format_invalid")
    return [dict(zip(fields, row, strict=True)) for row in data]


def _parse_historical_halt(provider, symbol, target):
    code = symbol.split(".")[0]
    stop_day, stop_time, resume_day, resume_time = (
        "暫停交易日期", "暫停交易時間", "恢復交易日期", "恢復交易時間")
    times = (stop_day, stop_time, resume_day, resume_time)
    if symbol.endswith(".TW"):
        params = {"response": "json", "startDate": (target - timedelta(days=365)).strftime("%Y%m%d"),
                  "endDate": target.strftime("%Y%m%d"), "stockNo": code, "selectType": "ALL"}
        report, proof = provider.capture_twse_report(TWSE_HALT, params)
        if report.get("stat") != "OK" or any(report.get("params", {}).get(k) != v for k, v in params.items()):
            if report.get("stat") == "很抱歉，沒有符合條件的資料!" and report.get("data") in (None, []):
                return None
            raise ValueError("official_halt_report_scope_invalid")
        rows = _report_rows(report, ("證券代號", *times))
        transitions = [(r[stop_day], r[stop_time], r[resume_day], r[resume_time])
                       for r in rows if str(r["證券代號"]).strip() == code]
    elif symbol.endswith(".TWO"):
        params = {"date": str(target.year), "cate": "1"}
        report, proof = provider.capture_tpex_report(TPEX_HALT, params)
        tables = report.get("tables", [])
        if (report.get("stat") != "ok" or report.get("date") != params["date"]
                or len(tables) != 1 or tables[0].get("date") != params["date"]
                or tables[0].get("totalCount") != len(tables[0].get("data", []))):
            raise ValueError("official_halt_report_scope_invalid")
        rows = _report_rows(tables[0], ("有價證券代號", "有價證券類別", *times))
        own = [r for r in rows if r["有價證券類別"] == "上櫃股票" and str(r["有價證券代號"]).strip() == code]
        # OTC reports publish starts and resumptions as separate rows.
        changes = sorted((actions._roc_date(r[day]), time.fromisoformat(str(r[hh]).zfill(5)), day)
                         for r in own for day, hh in ((stop_day, stop_time), (resume_day, resume_time))
                         if r[day] not in ("-", "", None))
        pending = None
        transitions = []
        for day, hh, kind in changes:
            if kind == stop_day:
                pending = (day, hh) if pending is None else None
            elif pending is not None:
                transitions.append((*pending, day, hh))
                pending = None
    else:
        return None
    hits = []
    for start, start_at, resume, resume_at in transitions:
        if start in ("-", "", None) or resume in ("-", "", None):
            continue
        start = start if not isinstance(start, str) else actions._roc_date(start)
        resume = resume if not isinstance(resume, str) else actions._roc_date(resume)
        start_at = time.fromisoformat(str(start_at).zfill(5))
        resume_at = time.fromisoformat(str(resume_at).zfill(5))
        if ((start, start_at) <= (target, time(9)) and (resume, resume_at) > (target, time(13, 30))):
            hits.append((start, start_at, resume, resume_at))
    if len(hits) != 1:
        return None
    start, start_at, resume, resume_at = hits[0]
    return {"version": VERSION, "symbol": symbol, "target_date": str(target), "halt_start": str(start),
            "halt_time": str(start_at), "resume_date": str(resume), "resume_time": str(resume_at),
            "event_type": "exchange_halt", "sources": [proof]}


def collect_target_suspension(provider, symbol, target):
    from types import SimpleNamespace
    reports = []
    def capture(method, path, params):
        payload, source = getattr(provider, method)(path, params)
        reports.append(payload)
        return payload, source
    replay = SimpleNamespace(capture_twse_report=lambda p,q: capture("capture_twse_report",p,q),
                             capture_tpex_report=lambda p,q: capture("capture_tpex_report",p,q))
    from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy
    from ai_stock_sentinel.daily_radar.work_queue import LostLease

    error = None
    try:
        event = _parse_suspension(replay, symbol, target)
    except LostLease:
        raise
    except (SourceDependencyBusy, ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        error, event = exc, None
    if not event:
        reports.clear()
        event = _parse_historical_halt(replay, symbol, target)
    if not event and error:
        raise error
    return event | {"reports": reports} if event else None


def matches(proof, *, symbol, target):
    from types import SimpleNamespace
    from urllib.parse import urlsplit, parse_qsl
    from ai_stock_sentinel.daily_radar.terminal_integrity import _source
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
    if not isinstance(proof, dict) or target is None:
        return False
    try:
        reports, sources = proof["reports"], proof["sources"]
        if not isinstance(reports, list) or not isinstance(sources, list) or len(reports) != len(sources):
            return False
        items = iter(zip(reports,sources))
        def read(host,path,params):
            raw, source = next(items)
            if not _source(source) or fingerprint(raw) != source["snapshot_sha256"]:
                raise ValueError("suspension_snapshot_mismatch")
            url = urlsplit(source["source_url"])
            if (url.netloc != host or url.path != path or url.fragment
                    or sorted(parse_qsl(url.query,keep_blank_values=True)) != sorted(params.items())):
                raise ValueError("suspension_request_mismatch")
            return raw, source
        replay = SimpleNamespace(capture_twse_report=lambda p,q: read("www.twse.com.tw",p,q),
                                 capture_tpex_report=lambda p,q: read("www.tpex.org.tw",p,q))
        parser = _parse_historical_halt if proof.get("event_type") == "exchange_halt" else _parse_suspension
        expected = parser(replay, symbol, target)
        return expected is not None and next(items,None) is None and expected | {"reports":reports} == proof
    except (KeyError, ValueError, TypeError, StopIteration, AttributeError, IndexError, OverflowError):
        return False


def expected_target(calendar, signal, window, cutoff):
    if calendar is None or cutoff is None or window < 1 or signal not in calendar.sessions:
        return None
    days = [day for day in calendar.sessions if signal < day <= cutoff]
    return days[window-1] if len(days) >= window else None


def proven_suspension(official, symbol, target):
    from ai_stock_sentinel.daily_radar.price_acceptance import has_trade
    return not has_trade(official.stock, target) and matches(official.target_suspension, symbol=symbol, target=target)


def suspended_outcome(row, candidate, days, official):
    if (row.get('skip_reason') not in {'missing_future_price','missing_benchmark'}
            or not proven_suspension(official, candidate['symbol'], days[-1])):
        return None
    from ai_stock_sentinel.daily_radar.validation_inputs import bind_validation_input
    return bind_validation_input(candidate,row|{'status':'skipped','skip_reason':REASON,'target_date':str(days[-1]),
        'outcome':{'expected_target_date':str(days[-1]),KEY:official.target_suspension}})


CONFLICT = "validated_return_conflicts_with_suspension"


def suspension_conflicts(existing, incoming, *, calendar, symbol):
    """A preserved result and new evidence disagree about the exact target."""
    from ai_stock_sentinel.daily_radar.price_acceptance import PRICE_EVIDENCE, verify_price_lock
    from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION

    if (existing is None or (existing.skip_reason != REASON and incoming.get("skip_reason") != REASON)
            or existing.target_date != parse_date(incoming.get("target_date"))):
        return False
    signal = parse_date(incoming["signal_date"])
    cutoff = parse_date(incoming.get("evaluation_as_of_date"))
    target = expected_target(calendar, signal, incoming["window_days"], cutoff)
    if target is None or existing.target_date != target:
        return False
    if incoming.get("status") == "skipped" and incoming.get("skip_reason") == REASON:
        return existing.status == "validated" and matches(incoming["outcome"].get(KEY), symbol=symbol, target=target)
    if (existing.status != "skipped" or existing.skip_reason != REASON or incoming.get("status") != "validated"
            or not matches((existing.outcome or {}).get(KEY), symbol=symbol, target=target)):
        return False
    basis = "next_open" if incoming["validation_version"] == RESEARCH_VALIDATION_VERSION else "signal_close"
    if (incoming.get("outcome") or {}).get(PRICE_EVIDENCE):
        verify_price_lock(incoming, symbol=symbol, basis=basis)
        return True
    from ai_stock_sentinel.daily_radar.window_identity import window_error
    sessions = [day for day in calendar.sessions if signal < day <= target]
    return window_error(incoming, signal=signal, sessions=sessions, basis=basis) is None
