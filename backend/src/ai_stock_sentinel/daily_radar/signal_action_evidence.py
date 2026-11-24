"""Official evidence for a signal-close window excluded by an exchange action."""

from datetime import date
from math import isclose
from urllib.parse import parse_qs, urlsplit

from ai_stock_sentinel.calibration import official_corporate_actions as actions
from ai_stock_sentinel.calibration import twse_rights_recovery as twse
from ai_stock_sentinel.calibration.forward_validation import number
from ai_stock_sentinel.daily_radar.price_acceptance import PRICE_ABS_TOL, _quotes_follow_calendar, _window
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

KEY = "signal_close_action_evidence"
VERSION = "signal-close-exchange-action-v1"
MIXED_VERSION = "signal-close-mixed-distribution-v2"


def _covers_sources(sources, reason, symbol, day, halt, dates):
    """Require an exchange notice, instrument-specific price data and TAIEX for every month."""
    from ai_stock_sentinel.daily_radar.terminal_integrity import _source

    listed = symbol.endswith(".TW")
    code = symbol.split(".")[0]
    host = "www.twse.com.tw" if listed else "www.tpex.org.tw"
    expected = (
        {twse.EX_RIGHTS, twse.REDUCTION, twse.FACE_VALUE}
        if listed
        else {actions.TPEX_DISTRIBUTIONS, actions.TPEX_REDUCTIONS}
    )
    remaining = {path: set(dates[1:]) for path in expected}
    stock_months = {d[:7].replace("-", "") for d in dates}
    index_months = {d[:7].replace("-", "") for d in dates}
    detail = not listed
    for source in sources:
        if not _source(source):
            return False
        url = urlsplit(source["source_url"])
        params = parse_qs(url.query)

        def get(key):
            values = params.get(key, [])
            return values[0] if len(values) == 1 else ""

        month = get("date").replace("/", "")[:6]
        if url.hostname == "www.twse.com.tw" and url.path == "/indicesReport/MI_5MINS_HIST":
            index_months.discard(month)
        if url.hostname != host:
            continue
        if listed and url.path == "/exchangeReport/STOCK_DAY" and get("stockNo") == code:
            stock_months.discard(month)
        if not listed and url.path == "/www/zh-tw/afterTrading/tradingStock" and get("code") == code:
            stock_months.discard(month)
        if url.path in remaining:
            first, last = (get(k).replace("/", "") for k in ("startDate", "endDate"))
            if len(first) == len(last) == 8 and first.isdecimal() and last.isdecimal():
                remaining[url.path] = {
                    d for d in remaining[url.path] if not first <= d.replace("-", "") <= last
                }
        if listed and get("STK_NO") == code:
            if (
                reason == actions.STOCK_DISTRIBUTION
                and url.path == twse.RIGHTS_DETAIL
                and get("T1") == day.replace("-", "")
            ):
                detail = True
            if (
                reason == actions.CASH_REDUCTION_HALT
                and url.path == actions.TWSE_REDUCTION_DETAIL
                and len(get("FILE_DATE")) == 8
                and get("FILE_DATE").isdecimal()
                and get("FILE_DATE") <= halt.replace("-", "")
            ):
                detail = True
    return detail and not (stock_months or index_months or any(remaining.values()))


def _report_paths(symbol, reason):
    reduction = reason == actions.CASH_REDUCTION_HALT
    if symbol.endswith(".TW"):
        return (
            (twse.REDUCTION, actions.TWSE_REDUCTION_DETAIL)
            if reduction
            else (twse.EX_RIGHTS, twse.RIGHTS_DETAIL)
        )
    return (actions.TPEX_REDUCTIONS if reduction else actions.TPEX_DISTRIBUTIONS,)


def _reported_action(proof, symbol, day, reason, dates):
    """Reparse the original notice and detail after checking their capture hashes."""
    from ai_stock_sentinel.daily_radar.next_open_action_scope import _action_event
    from ai_stock_sentinel.daily_radar.rights_terms import read_terms

    paths = _report_paths(symbol, reason)
    listed = symbol.endswith(".TW")
    host = "www.twse.com.tw" if listed else "www.tpex.org.tw"
    sources = {s["source_url"]: s for s in proof["sources"]}
    reports = {}
    for url, payload in proof["action_reports"].items():
        uri = urlsplit(url)
        source = sources[url]
        if (
            uri.hostname != host
            or uri.path not in paths
            or uri.path in reports
            or source["snapshot_sha256"] != fingerprint(payload)
        ):
            return None
        reports[uri.path] = (payload, source, parse_qs(uri.query))
    if set(reports) != set(paths):
        return None

    def capture(path, params):
        raw, receipt, query = reports[path]
        if any(query.get(k) != [str(v)] for k, v in params.items()):
            raise ValueError("action_report_scope_mismatch")
        return raw, receipt

    listing = paths[0]
    payload, source, query = reports[listing]
    code = symbol.split(".")[0]
    effective = date.fromisoformat(day)
    reduction = reason == actions.CASH_REDUCTION_HALT
    field = "恢復買賣日期" if reduction else ("資料日期" if listed else "除權息日期")
    if listed:
        params = {k: query[k][0] for k in ("startDate", "endDate")}
        capture(listing, params)
        rows = twse._dated_rows(payload, params, field, [])
        rows = [(d, r) for d, r in rows if str(r["股票代號"]).strip() == code]
    else:
        period = [date.fromisoformat(query[k][0].replace("/", "-")) for k in ("startDate", "endDate")]
        rows, _ = actions._tpex_events(
            capture, listing, period, code, field, "股票代號" if reduction else "代號", []
        )
    # A listing can contain multiple notices for the same symbol. The single
    # action policy must also hold when replaying the source captured on write.
    relevant = [(d, r) for d, r in rows if dates[0] < str(d) <= dates[-1]]
    if len(relevant) != 1 or relevant[0][0] != effective:
        return None
    row = relevant[0][1]
    if reduction:
        action = (
            actions._twse_reduction(code, effective, row, capture)[0]
            if listed
            else actions._tpex_reduction(symbol, effective, row)
        )
        return _action_event(symbol, action) if action else None
    notices = {"權", "除權", "權息"} if listed else {"權", "除權"}
    if row["權/息"] not in notices or listed and row["詳細資料"] != f"{code},{effective:%Y%m%d}":
        return None
    terms = read_terms(symbol, effective, row, source, capture)
    if not terms or terms["kind"] != "stock_distribution":
        return None
    return _distribution_action(symbol, day, actions._numeric(row["除權息前收盤價"]), terms)


def _distribution_action(symbol, day, previous_close, terms):
    return {
        "symbol": symbol,
        "effective_date": day,
        "cash_per_share": terms.get("cash_per_share", 0),
        "halt_start": None,
        "previous_close": previous_close,
        "share_ratio": 1 + terms["free_shares_per_thousand"] / 1000,
    }


def classify_action(candidate, sessions, official):
    from ai_stock_sentinel.daily_radar.terminal_integrity import _source

    dates = list(map(str, sessions))
    scope = set(dates[1:])
    if (
        official.event_coverage is None
        or not scope.issubset(official.event_coverage)
        or official.events is None
        or official.unavailable_dates.intersection(dates)
        or any(dates[0] <= d <= dates[-1] for d in official.partial_stock_dates)
    ):
        return None
    events = [
        e
        for e in official.events
        if e["effective_date"] in scope
        or e.get("start_date", e["effective_date"]) <= dates[-1] < e["effective_date"]
    ]
    if len(events) != 1:
        return None
    (event,) = events
    effective = event["effective_date"]
    terms = official.rights_terms.get(effective) or {}
    symbol = candidate["symbol"]
    if event["family"] == "reduction":
        action = event.get("action") or {}
        reason = actions.CASH_REDUCTION_HALT
    elif event["family"] == "rights" and terms.get("kind") == "stock_distribution":
        action = _distribution_action(symbol, effective, event["previous_close"], terms)
        reason = actions.STOCK_DISTRIBUTION
    else:
        return None
    if effective not in scope:
        return None
    halt = action.get("halt_start")
    halted = {d for d in dates if halt and halt <= d < effective}
    if any(day in official.stock for day in halted) or not _quotes_follow_calendar(official, dates):
        return None
    active = [d for d in dates if d not in halted]
    stock = _window(official.stock, active)
    benchmark = _window(official.benchmark, dates)
    if not stock or not benchmark or not official.report_sources:
        return None
    anchor = number((candidate.get("input_snapshot", {}).get("ohlcv") or {}).get("close"))
    if anchor is None or not isclose(anchor, stock[0]["close"], abs_tol=PRICE_ABS_TOL, rel_tol=1e-7):
        return None
    receipts = [
        r.source for r in official.report_sources if r.start_date <= dates[-1] and dates[0] <= r.end_date
    ]
    if terms:
        receipts.append(terms["source"])
    receipts.extend(official.stock[d].get("official_price_report") for d in active)
    receipts.extend(official.benchmark[d].get("official_price_report") for d in dates)
    if not receipts or not all(_source(r) for r in receipts):
        return None
    distinct = {r["source_url"]: r for r in receipts}
    if any(r["snapshot_sha256"] != distinct[r["source_url"]]["snapshot_sha256"] for r in receipts):
        return None
    mixed = reason == actions.STOCK_DISTRIBUTION and action["cash_per_share"] > 0
    proof = {
        "version": MIXED_VERSION if mixed else VERSION,
        "reason": reason,
        "action": action,
        "action_reports": {
            r.source["source_url"]: r.payload
            for r in official.report_sources
            if r.start_date <= effective <= r.end_date
            and r.payload is not None
            and urlsplit(r.source["source_url"]).path in _report_paths(symbol, reason)
        },
        "benchmark_dates": dates,
        "stock_closes": {r["date"]: r["close"] for r in stock},
        "sources": [distinct[k] for k in sorted(distinct)],
    }
    if mixed:
        from ai_stock_sentinel.daily_radar.mixed_distribution_evidence import MARKET_PATHS, PRICE_PATHS
        proof["action_reports"] = {
            r.source["source_url"]: r.payload for r in official.report_sources
            if r.start_date <= dates[-1] and dates[0] <= r.end_date
            and (urlsplit(r.source["source_url"]).path in MARKET_PATHS
                 or r.detail and r.start_date == effective and urlsplit(r.source["source_url"]).path == twse.RIGHTS_DETAIL)
        }
        proof["price_reports"] = {url: official.price_reports.get(url) for url in distinct if urlsplit(url).path in PRICE_PATHS}
        proof["sources"] = [r for r in proof["sources"] if r["source_url"] in proof["action_reports"]
                            or r["source_url"] in proof["price_reports"]]
    proof["sha256"] = fingerprint(proof)
    if matches(proof, reason, symbol, dates, published_close=anchor):
        return reason, proof
    return None


def matches(proof, reason, symbol, dates, *, published_close=None):
    if (not isinstance(proof, dict) or proof.get("version") not in (VERSION, MIXED_VERSION)
            or reason != proof.get("reason")):
        return False
    if proof.get("sha256") != fingerprint({k: v for k, v in proof.items() if k != "sha256"}):
        return False
    action = proof.get("action")
    if not isinstance(action, dict):
        return False
    day, halt = action.get("effective_date"), action.get("halt_start")
    ratio, cash, previous = (
        number(action.get(k)) for k in ("share_ratio", "cash_per_share", "previous_close")
    )
    if (
        action.get("symbol") != symbol
        or len(dates) < 2
        or dates != sorted(set(dates))
        or day not in dates[1:]
        or proof.get("benchmark_dates") != dates
        or not proof.get("sources")
        or not isinstance(proof["sources"], list)
        or None in (ratio, cash, previous)
    ):
        return False
    if proof["version"] == MIXED_VERSION:
        if not (reason == actions.STOCK_DISTRIBUTION and symbol.endswith(".TW")
                and halt is None and ratio > 1 and 0 < cash < previous):
            return False
    elif reason == actions.CASH_REDUCTION_HALT:
        if not (halt in dates[1:] and halt < day and 0 < ratio < 1 and cash > 0):
            return False
    elif reason != actions.STOCK_DISTRIBUTION or halt is not None or ratio <= 1 or cash != 0:
        return False
    active = [d for d in dates if not halt or not halt <= d < day]
    closes = proof.get("stock_closes") or {}
    try:
        replay = proof
        if proof["version"] == MIXED_VERSION:
            from ai_stock_sentinel.daily_radar.mixed_distribution_evidence import verified_action_reports
            reports = verified_action_reports(proof, symbol, dates, number(published_close))
            if not reports:
                return False
            replay = proof | {"action_reports": reports}
        if _reported_action(replay, symbol, day, reason, dates) != action:
            return False
    except (KeyError, ValueError, TypeError, IndexError, AttributeError, ArithmeticError):
        return False
    return (
        _covers_sources(proof["sources"], reason, symbol, day, halt, dates)
        and isinstance(closes, dict)
        and set(closes) == set(active)
        and all(number(v) is not None and number(v) > 0 for v in closes.values())
        and isclose(
            number(closes[active[active.index(day) - 1]]), previous, rel_tol=1e-7, abs_tol=PRICE_ABS_TOL
        )
    )


def matches_calendar(row, calendar, *, symbol, signal, cutoff, published_close=None):
    from ai_stock_sentinel.calibration.forward_validation import parse_date

    if (
        calendar is None
        or cutoff is None
        or row["window_days"] < 1
        or signal not in calendar.sessions
        or row.get("status") != "skipped"
    ):
        return False
    sessions = [d for d in calendar.sessions if signal < d <= cutoff][: row["window_days"]]
    return (
        len(sessions) == row["window_days"]
        and parse_date(row.get("target_date")) == sessions[-1]
        and matches(
            (row.get("outcome") or {}).get(KEY),
            row["skip_reason"],
            symbol,
            list(map(str, [signal, *sessions])), published_close=published_close,
        )
    )
