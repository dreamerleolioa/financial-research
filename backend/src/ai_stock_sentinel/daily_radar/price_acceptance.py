"""Versioned prices for Taiwan validation windows.

Official final bars determine the raw prices. A signal-close window may
translate them to the signal-date basis only with a verified adjustment.
"""
from dataclasses import dataclass, field, replace
from math import isclose
from statistics import median
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

from ai_stock_sentinel.calibration.forward_validation import OHLC_PRICE_FIELDS, number
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
from ai_stock_sentinel.daily_radar.signal_quote import capture_quote, archive_time

PRICE_EVIDENCE = "validation_price_evidence"
VENDOR_UNAVAILABLE = "supplementary_vendor_unavailable"
VERSION = "radar-validation-prices-v1"
RETRYABLE_PRICE_REASONS = frozenset({
    "adjustment_factor_unverified", "conflicting_price_records", "official_corporate_action_in_window",
    "official_validation_events_unverified", "official_validation_price_missing", "signal_price_anchor_mismatch",
    "official_suspension_report_unavailable", "validation_price_source_unavailable",
})
PRICE_REL_TOL = 1e-7
PRICE_ABS_TOL = .005
FACTOR_TOL = 1e-5


@dataclass(frozen=True)
class EventReport:
    start_date: str
    end_date: str
    source: Mapping
    detail: bool = False
    payload: dict | None = None


@dataclass(frozen=True)
class OfficialPriceEvidence:
    stock: Mapping
    benchmark: Mapping
    events: list | None
    report_sources: tuple[EventReport, ...] = ()
    event_coverage: frozenset | None = None
    target_suspension: dict | None = None
    suspension_error: str | None = None
    unavailable_dates: frozenset = frozenset()
    rights_terms: Mapping = field(default_factory=dict)
    unavailable_terms: frozenset = frozenset()
    partial_stock_dates: frozenset = frozenset()
    price_reports: Mapping = field(default_factory=dict)
    retryable_price_dates: frozenset = frozenset()


@dataclass(frozen=True)
class AcceptedPrices:
    stock: list = field(default_factory=list)
    benchmark: list = field(default_factory=list)
    proof: dict = field(default_factory=dict)
    reason: str | None = None
    vendor_capture: dict | None = None

    def with_proof(self, row):
        evidence = {PRICE_EVIDENCE: self.proof}
        if self.vendor_capture:
            evidence["vendor_capture"] = self.vendor_capture
            evidence["vendor_capture_sha256"] = fingerprint([self.proof['sha256'], self.vendor_capture])
        return row | {"outcome": row["outcome"] | evidence}


def quotes_match(actual, expected):
    return all(
        (a := number(actual.get(key))) is not None
        and (b := number(expected.get(key))) is not None
        and isclose(a, b, abs_tol=PRICE_ABS_TOL, rel_tol=PRICE_REL_TOL)
        for key in OHLC_PRICE_FIELDS
    )


def verify_signal_bars(db, rows, *, run_date, include_quote=False):
    from ai_stock_sentinel.daily_radar.market_bar_repository import get_taiwan_daily_bars

    archived = get_taiwan_daily_bars(db, symbols=[row.symbol for row in rows],
                                    start_date=run_date, end_date=run_date)
    official = {row.symbol: row for row in archived}
    accepted, rejected = {}, {}
    for row in rows:
        bar = official.get(row.symbol)
        if bar is None:
            rejected[row.symbol] = "official_signal_bar_unavailable"
            continue
        quote = {key: float(getattr(bar, key)) for key in OHLC_PRICE_FIELDS}
        actual = (row.technical or {}).get("ohlcv") or {}
        if not quotes_match(actual, quote):
            rejected[row.symbol] = "official_signal_bar_mismatch"
            continue
        evidence = {"version": VERSION, "symbol": row.symbol, "date": str(run_date),
                    "raw_ohlcv": quote, "source": bar.source_dataset}
        if include_quote:
            evidence["official_quote"] = capture_quote(
                symbol=row.symbol, trade_date=run_date, ohlcv=quote | {"volume": bar.volume},
                provider=bar.source_provider, basis=bar.adjustment_mode, captured_at=archive_time(bar.fetched_at),
            )
        accepted[row.symbol] = evidence | {"sha256": fingerprint(evidence)}
    return accepted, rejected


def current_signal_bars(db, rows, *, run_date, include_quote=False):
    from ai_stock_sentinel.daily_radar.raw_data import current_daily_radar_raw_rows
    return verify_signal_bars(db, current_daily_radar_raw_rows(rows, run_date=run_date),
                              run_date=run_date, include_quote=include_quote)


def signal_price_gaps(db, rows, *, run_date):
    return current_signal_bars(db, rows, run_date=run_date)[1]


def _quote(row):
    fields = {key: number(row.get(key)) for key in OHLC_PRICE_FIELDS}
    if (any(value is None or value <= 0 for value in fields.values())
            or not fields["low"] <= min(fields["open"], fields["close"])
            <= max(fields["open"], fields["close"]) <= fields["high"]):
        return None
    return fields


def _observed_forward_open(row):
    # Legacy adjusted-history rows substituted the close for an absent open.
    return row.get("open_observed", row.get("open") != row.get("close")) is True


def _quotes_follow_calendar(official, dates):
    sessions = set(dates)
    return all(day in sessions for bars in (official.stock, official.benchmark) for day in bars
               if dates[0] <= day <= dates[-1])


def _window(rows, dates):
    values = []
    for day in dates:
        row = rows.get(day) or {}
        quote = _quote(row)
        if quote is None or row.get("date") != day or not row.get("price_source"):
            return None
        values.append({"date": day, **quote, "price_source": row["price_source"],
                       **({"official_price_report": _source_identity(row["official_price_report"])}
                          if row.get("official_price_report") else {})})
    return values


def _provider_index(rows, dates):
    result = {}
    for row in rows:
        if row.get("date") not in dates:
            continue
        if row.get("ohlc_conflict_fields") or (row["date"] in result and result[row["date"]] != row):
            return None
        result[row["date"]] = row
    return result


def _proportional_factor(observed, baseline):
    quote = _quote(observed)
    if quote is None:
        return None
    ratios = [quote[key] / baseline[key] for key in OHLC_PRICE_FIELDS]
    factor = median(ratios)
    if all(isclose(quote[key], baseline[key] * factor, abs_tol=PRICE_ABS_TOL, rel_tol=PRICE_REL_TOL)
           for key in OHLC_PRICE_FIELDS):
        return factor
    return None


def _source_identity(source):
    return {key: source[key] for key in
            ("source_url", "provider", "source_operation", "parser_version", "sdk_version")
            if key in source}


def _event_evidence(official, dates):
    events = sorted((event for event in official.events if event["effective_date"] in dates),
                    key=lambda event: (event["effective_date"], fingerprint(event)))
    reports = {}
    for report in official.report_sources:
        covered = {day for day in dates if report.start_date <= day <= report.end_date}
        if not covered:
            continue
        source = _source_identity(report.source)
        if report.detail:
            source["snapshot_sha256"] = report.source["snapshot_sha256"]
        else:
            # Range reports prove parsed events and coverage for these dates.
            # Their query and raw checksum also describe unrelated dates/symbols.
            url = urlsplit(source["source_url"])
            source["source_url"] = url._replace(query="", fragment="").geturl()
        _, days = reports.setdefault(fingerprint(source), (source, set()))
        days.update(covered)
    return events, [source | {"dates": sorted(days)} for _, (source, days) in sorted(reports.items())]


def _price_lock(candidate, basis, stock, benchmark, official, *, vendor=(), factors=(), rights_terms=(), vendor_benchmark=(), basis_evidence=None):
    source_versions = {row["price_source"] for row in [*stock, *benchmark]}
    events, reports = _event_evidence(official, {row["date"] for row in stock[1:]})
    local_receipt = next((r["source_capture"] for r in vendor
                          if (r.get("source_capture") or {}).get("source_operation") == "local-adjusted-history"), None)
    vendor_rows = [{"date": row["date"], **_quote(row), "price_source": row.get("price_source"),
                    "source": {} if local_receipt and row.get("source_capture") == local_receipt
                              else _source_identity(row.get("source_capture") or {})}
                   for row in sorted(vendor, key=lambda row: row["date"])]
    data = {
        "version": VERSION, "return_basis": basis, "symbol": candidate["symbol"],
        "signal_date": candidate["record_date"], "stock": stock, "benchmark": benchmark,
        "source_versions": sorted(source_versions), "vendor_window_sha256": fingerprint(vendor_rows),
        "event_reports": reports, "events": events, "factors": list(factors),
    }
    if basis_evidence:
        data["basis_evidence"] = basis_evidence
    if rights_terms:
        data["rights_terms"] = list(rights_terms)
        if basis == 'next_open':
            data['rights_value_included'] = data['subscription_cash_flow_included'] = False
            data['rights_vendor_action_sha256'] = fingerprint([
                [r['date'], r.get('stock_splits'), r.get('dividends')] for r in vendor])
            data['rights_vendor_benchmark_sha256'] = fingerprint([
                [r['date'], _quote(r), _source_identity(r.get('source_capture') or {})] for r in vendor_benchmark])
    return AcceptedPrices(stock, benchmark, data | {"sha256": fingerprint(data)}, vendor_capture=local_receipt)


def _cash_dividends(events, dates):
    result = {}
    for event in events:
        day = event["effective_date"]
        if day not in dates:
            continue
        cash = number(event.get("cash_dividend"))
        if event["family"] != "rights" or cash is None or cash <= 0 or day in result:
            return None
        result[day] = cash
    return result


def _events_cover_window(official, days):
    return official.events is not None and (
        official.event_coverage is None or set(days).issubset(official.event_coverage)
    )


def accept_signal_close_prices(candidate, sessions, adjusted_rows, official):
    return _signal_close_prices(candidate, sessions, adjusted_rows, official, anchor_evidence=None)


def _signal_close_prices(candidate, sessions, adjusted_rows, official, *, anchor_evidence):
    dates = [str(day) for day in sessions]
    adjusted = _provider_index(adjusted_rows, set(dates))
    if adjusted is None:
        return AcceptedPrices(reason="conflicting_price_records")
    stock = _window(official.stock, dates)
    benchmark = _window(official.benchmark, dates)
    if not stock or not benchmark:
        if _events_cover_window(official, dates[1:]) and _cash_dividends(official.events, set(dates[1:])) is None:
            return AcceptedPrices(reason="official_corporate_action_in_window")
        return AcceptedPrices(reason="official_validation_price_missing")
    anchor = number((candidate.get("input_snapshot", {}).get("ohlcv") or {}).get("close"))
    raw_anchor = stock[0]["close"]
    expected_anchor = raw_anchor if anchor_evidence is None else raw_anchor * anchor_evidence["scale"]
    if anchor is None or not isclose(anchor, expected_anchor, abs_tol=PRICE_ABS_TOL, rel_tol=PRICE_REL_TOL):
        return AcceptedPrices(reason="signal_price_anchor_mismatch")

    if _events_cover_window(official, dates[1:]) and _cash_dividends(official.events, set(dates[1:])) is None:
        from ai_stock_sentinel.daily_radar.rights_prices import accept_rights_prices
        return (accept_rights_prices(candidate, sessions, adjusted_rows, official, basis="signal_close")
                or AcceptedPrices(reason="official_corporate_action_in_window"))
    source = [adjusted.get(day, {}) for day in dates]
    ratios = [_proportional_factor(row, raw) for row, raw in zip(source, stock, strict=True)]
    versions = {row["source_capture"].get("snapshot_sha256") for row in source if row.get("source_capture")}
    if (any(ratio is None for ratio in ratios) or len(versions) > 1
            or versions and any(not row.get("source_capture") for row in source)):
        return AcceptedPrices(reason="adjustment_factor_unverified")
    if not _events_cover_window(official, dates[1:]):
        return AcceptedPrices(reason="official_validation_events_unverified")
    cash = _cash_dividends(official.events, set(dates[1:]))
    if cash is None:
        return AcceptedPrices(reason="official_corporate_action_in_window")

    factor = 1.0
    factors = [factor]
    # Some adjusted-history feeds actually contain official raw quotes on every
    # session. A pure dividend with a matching official reference close can
    # then supply the adjustment independently of the observed vendor ratios.
    prior_closes = dict(zip(dates[1:], (r["close"] for r in stock[:-1]), strict=True))
    verified_raw = (
        bool(cash) and official.event_coverage is not None
        and set(prior_closes).issubset(official.event_coverage)
        and _quotes_follow_calendar(official, dates)
        and all(_observed_forward_open(row) and quotes_match(row, raw)
                for row, raw in zip(source, stock, strict=True))
        and all(event.get("notice") in {"息", "除息"}
                and (previous := number(event.get("previous_close"))) is not None
                and isclose(previous, prior_closes[event["effective_date"]], abs_tol=PRICE_ABS_TOL, rel_tol=PRICE_REL_TOL)
                for event in official.events if event["effective_date"] in prior_closes)
    )
    for before, after, prev in zip(ratios[:-1], ratios[1:], stock[:-1], strict=True):
        day = dates[len(factors)]
        dividend = cash.get(day, 0.0)
        if dividend >= prev["close"]:
            return AcceptedPrices(reason="adjustment_factor_unverified")
        expected = prev["close"] / (prev["close"] - dividend)
        if not verified_raw and not isclose(after / before, expected, rel_tol=FACTOR_TOL):
            return AcceptedPrices(reason="adjustment_factor_unverified")
        factor *= expected
        factors.append(factor)
    # Keep the original entry and resistance/support scale. Transform every
    # OHLC component together using verified effective-day events.
    anchor_factor = anchor / stock[0]["close"]
    prices = [row | {key: row[key] * factor * anchor_factor for key in OHLC_PRICE_FIELDS}
              for row, factor in zip(stock, factors, strict=True)]
    return _price_lock(candidate, "signal_close", prices, benchmark, official, vendor=source, factors=factors, basis_evidence=anchor_evidence)


def research_rows(rows, cash_values):
    return [row | {"price_basis": "unadjusted_price", "corporate_actions_checked": True,
                   "corporate_action_source": "official_reports", "stock_splits": 0,
                   "dividends": cash_values.get(row["date"], 0.0)} for row in rows]


def accept_next_open_prices(candidate, sessions, vendor_rows, official):
    dates = [str(day) for day in sessions]
    if not _events_cover_window(official, dates[1:]):
        return AcceptedPrices(reason="official_validation_events_unverified")
    cash = _cash_dividends(official.events, set(dates[1:]))
    if cash is None:
        return AcceptedPrices(reason="official_corporate_action_in_window")
    stock = _window(official.stock, dates)
    benchmark = _window(official.benchmark, dates)
    if not stock or not benchmark:
        return AcceptedPrices(reason="official_validation_price_missing")
    vendor = _provider_index(vendor_rows, set(dates))
    if vendor is None:
        return AcceptedPrices(reason="conflicting_price_records")
    # A provider's split-scaled series is supplementary. Where it supplies
    # candles, compare their shape before accepting the official original.
    if any(day in vendor and _proportional_factor(vendor[day], row) is None
           for day, row in zip(dates, stock, strict=True)):
        return AcceptedPrices(reason="conflicting_price_records")
    return _price_lock(candidate, "next_open", research_rows(stock, cash),
                       research_rows(benchmark, {}), official, vendor=vendor.values())


def has_trade(rows, day):
    row = rows.get(str(day))
    # Partial prices are contradictory evidence too. Placeholders contain no
    # positive price, whereas a damaged candle may still show actual trading.
    return bool(row and any((value := number(row.get(key))) is not None and value > 0
                            for key in OHLC_PRICE_FIELDS))


def same_official_inputs_without_vendor(saved, incoming):
    if not (saved.get(VENDOR_UNAVAILABLE) or incoming.get(VENDOR_UNAVAILABLE)):
        return False
    def official(value):
        return {key: item for key, item in value[PRICE_EVIDENCE].items()
                if key not in {"sha256", "vendor_window_sha256"}}
    return (saved[PRICE_EVIDENCE].get("return_basis") == "next_open"
            and official(saved) == official(incoming))


def refused_prices(row, reason, official=None, *, sessions=(), event_requires_prices=True):
    outcome = {"expected_target_date": row.get("target_date") or row.get("outcome", {}).get("expected_target_date")}
    needs_price = (reason in {"official_validation_price_missing", "official_validation_events_unverified"}
                   or event_requires_prices and reason == "official_corporate_action_in_window")
    if (reason == "official_corporate_action_in_window" and official is not None
            and getattr(official, 'unavailable_terms', frozenset()).intersection(map(str, sessions))):
        reason = "validation_price_source_unavailable"
    if official is not None and needs_price and getattr(official, "unavailable_dates", frozenset()).intersection(map(str, sessions)):
        reason = "validation_price_source_unavailable"
    if official is not None and getattr(official, "suspension_error", None):
        outcome["suspension_lookup_error"] = official.suspension_error
        if reason in {"official_validation_price_missing", "official_validation_events_unverified"}:
            reason = "official_suspension_report_unavailable"
    if reason == "official_corporate_action_in_window" and official is not None:
        if sessions:
            dates = {str(day) for day in sessions[1:]}
            events = [e for e in (official.events or []) if e['effective_date'] in dates]
            reports = [report for report in official.report_sources
                       if any(report.start_date <= day <= report.end_date for day in dates)]
        else:
            events, reports = official.events, official.report_sources
        outcome['official_event_review'] = {'events': events,
            'sources': [dict(report.source) for report in reports]}
    return row | {"status": "skipped", "skip_reason": reason, "target_date": None, "outcome": outcome}


def _stable_reviewed_input(value, detail_paths):
    if isinstance(value, dict):
        # A source receipt identifies a full captured response. Its stable
        # source and the verified prices/terms identify the calculation.
        receipt_fields = set()
        if "snapshot_sha256" in value:
            receipt_fields.update(("snapshot_id", "request_hash", "captured_at", "expires_at"))
            if urlsplit(value.get("source_url") or "").path not in detail_paths:
                receipt_fields.add("snapshot_sha256")
        return {key: _stable_reviewed_input(item, detail_paths) for key, item in value.items()
                if key not in receipt_fields}
    if isinstance(value, (list, tuple)):
        return [_stable_reviewed_input(item, detail_paths) for item in value]
    return value


def _scoped_replay_inputs(calls, event, symbol):
    from ai_stock_sentinel.calibration import twse_rights_recovery as rights
    from urllib.parse import urlencode
    from ai_stock_sentinel.daily_radar.terminal_integrity import _source

    scoped, sources = [], {}
    for call in calls:
        req = call.get('request', {})
        args = req.get('args', [])
        if (req.get('method') != 'capture_twse_report' or len(args) != 2 or call.get('failed')
                or args[0] not in (rights.EX_RIGHTS, rights.REDUCTION, rights.FACE_VALUE)):
            scoped.append(call)
            continue
        path, params = args
        raw, source = call['value']
        url = urlsplit(source.get('source_url', ''))
        if (not _source(source) or source.get('snapshot_sha256') != fingerprint(raw)
                or url._replace(query='').geturl() != rights.TWSE + path
                or ('requested_subrange' not in source and sorted(parse_qsl(url.query, keep_blank_values=True))
                    != sorted(params.items()))):
            raise ValueError('invalid_validation_price_lock')
        field = '資料日期' if path == rights.EX_RIGHTS else '恢復買賣日期'
        parsed = rights.dated_market_rows(raw, params, field, [], path=path, source=source,
                                         allow_empty_status=path == rights.REDUCTION)
        facts = sorted((rights.market_event_facts(path, r) for _, r in parsed
                        if str(r['股票代號']).strip() == symbol.split('.')[0]), key=fingerprint)
        scoped.append({'request': req, 'market_events': facts})
        sources[fingerprint(source)] = {k: v for k, v in source.items() if k != 'requested_subrange'} | {
            'source_url': rights.TWSE + path + '?' + urlencode(sorted(params.items()))}
    def scope_source(value):
        if isinstance(value, list):
            return [scope_source(v) for v in value]
        if isinstance(value, dict):
            if 'snapshot_sha256' in value and fingerprint(value) in sources:
                return sources[fingerprint(value)]
            return {k: scope_source(v) for k, v in value.items()}
        return value
    return scoped, scope_source(event)


def _reviewed_inputs_sha256(row, *, scoped=False):
    from ai_stock_sentinel.calibration import official_corporate_actions, twse_rights_recovery
    outcome = row["outcome"]
    evidence = outcome["reviewed_official_window"]
    version_input = {key: evidence[key] for key in (
        "version", "symbol", "signal_date", "session_dates", "stock", "benchmark",
        "calculation_stock", "calculation_benchmark",
    ) if key in evidence}
    replay = evidence.get("event_replay", {})
    version_input["replay_prices"] = {key: replay[key] for key in ("yahoo", "marked", "benchmark") if key in replay}
    # Recovery requests use the individual window. Keep their exact
    # report contents while dropping capture timestamps and batch receipts.
    version_input["replay_calls"] = replay.get("calls", [])
    version_input["event"] = {key: outcome[key] for key in (
        official_corporate_actions.PROVENANCE_KEY, twse_rights_recovery.PROVENANCE_KEY, "split_event",
    ) if key in outcome}
    if scoped:
        version_input['replay_calls'], version_input['event'] = _scoped_replay_inputs(
            version_input['replay_calls'], version_input['event'], row['symbol'])
    detail_paths = {twse_rights_recovery.RIGHTS_DETAIL, official_corporate_actions.TWSE_REDUCTION_DETAIL}
    return fingerprint(_stable_reviewed_input(version_input, detail_paths))


def same_reviewed_price_version(saved, incoming):
    left, right = (item.get(PRICE_EVIDENCE) or {} for item in (saved, incoming))
    if ('reviewed_inputs_sha256' not in left or 'reviewed_inputs_sha256' not in right
            or {left.get('reviewed_input_scope'), right.get('reviewed_input_scope')} != {None, 'window-events-v1'}):
        return False
    versions = []
    for payload, lock in ((saved, left), (incoming, right)):
        row = {'outcome': payload, 'symbol': lock['symbol'], 'signal_date': lock['signal_date']}
        verify_price_lock(row, symbol=lock['symbol'], basis='next_open')
        comparable = {k:v for k,v in lock.items() if k not in ('sha256','reviewed_input_scope')}
        comparable['reviewed_inputs_sha256'] = _reviewed_inputs_sha256(row, scoped=True)
        versions.append(comparable)
    return versions[0] == versions[1]


def lock_reviewed_next_open(row, official):
    """Freeze the full replay separately from the window's price version."""
    outcome = row["outcome"]
    evidence = outcome["reviewed_official_window"]
    events, reports = _event_evidence(official, set(evidence["session_dates"]))
    lock = {"version": VERSION, "return_basis": "next_open", "symbol": row["symbol"],
            "signal_date": row["signal_date"], "reviewed_inputs_sha256": _reviewed_inputs_sha256(row, scoped=True),
            "reviewed_input_scope": "window-events-v1",
            "events": events, "event_reports": reports}
    return row | {"outcome": outcome | {
        PRICE_EVIDENCE: lock | {"sha256": fingerprint(lock)},
        "reviewed_official_window_sha256": fingerprint(evidence),
    }}


def verify_price_lock(row, *, symbol, basis):
    outcome = row.get("outcome") or {}
    proof = outcome.get(PRICE_EVIDENCE)
    if proof is None:
        return
    if (not isinstance(proof, Mapping) or proof.get("version") != VERSION or proof.get("symbol") != symbol
            or proof.get("return_basis") != basis or proof.get("signal_date") != row.get("signal_date")
            or proof.get("sha256") != fingerprint({key: value for key, value in proof.items() if key != "sha256"})):
        raise ValueError("invalid_validation_price_lock")
    if 'vendor_capture' in outcome or 'vendor_capture_sha256' in outcome:
        source = outcome.get('vendor_capture')
        if (not isinstance(source, dict) or source.get('provider') != 'yfinance'
                or source.get('source_operation') != 'local-adjusted-history'
                or re.fullmatch('[0-9a-f]{64}', str(source.get('snapshot_sha256'))) is None
                or outcome.get('vendor_capture_sha256') != fingerprint([proof['sha256'], source])):
            raise ValueError('invalid_validation_price_lock')
        try:
            if datetime.fromisoformat(source['captured_at']).tzinfo is None:
                raise ValueError('missing timezone')
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('invalid_validation_price_lock') from exc
    if "reviewed_official_window_sha256" in proof:
        if proof["reviewed_official_window_sha256"] != fingerprint(outcome.get("reviewed_official_window")):
            raise ValueError("invalid_validation_price_lock")
        return
    if "reviewed_inputs_sha256" in proof:
        from ai_stock_sentinel.daily_radar.next_open_official_repair import verify_event_report_receipts

        if (not isinstance(outcome.get("reviewed_official_window"), dict)
                or outcome.get("reviewed_official_window_sha256") != fingerprint(outcome["reviewed_official_window"])
                or proof.get("reviewed_input_scope") not in (None, "window-events-v1")
                or proof["reviewed_inputs_sha256"] != _reviewed_inputs_sha256(
                    row | {'symbol': symbol}, scoped=proof.get("reviewed_input_scope") == "window-events-v1")):
            raise ValueError("invalid_validation_price_lock")
        replay = outcome["reviewed_official_window"].get("event_replay", {})
        verify_event_report_receipts(replay.get("calls", []))
        return
    stock, benchmark = proof.get("stock"), proof.get("benchmark")
    if (not isinstance(stock, list) or not isinstance(benchmark, list)
            or any(not isinstance(bar, Mapping) or not isinstance(bar.get("date"), str)
                   for bar in [*stock, *benchmark])):
        raise ValueError("invalid_validation_price_lock")
    dates = [bar["date"] for bar in stock]
    if (row["status"] != "validated" or len(dates) != row["window_days"] + 1
            or dates != sorted(set(dates)) or dates[0] != row["signal_date"] or dates[-1] != row["target_date"]
            or [bar["date"] for bar in benchmark] != dates):
        raise ValueError("invalid_validation_price_lock")


class OfficialPriceCollector:
    """Read official stock/TAIEX bars and event reports over a bounded range."""
    def __init__(self, provider, *, return_basis=None):
        self.provider = provider
        self.return_basis = return_basis
        self.sources = []
        self.detail_payloads = {}

    def capture_twse_report(self, path, params):
        return self._capture_range_report(self.provider.capture_twse_report, path, params)

    def capture_tpex_report(self, path, params):
        return self._capture_range_report(self.provider.capture_tpex_report, path, params)

    def _capture_range_report(self, capture, path, params):
        from datetime import datetime
        payload, source = capture(path, params)
        if "startDate" in params:
            start, end = (str(datetime.strptime(params[key].replace("/", ""), "%Y%m%d").date())
                          for key in ("startDate", "endDate"))
            self.sources.append(EventReport(start, end, source, payload=payload))
        else:
            self.detail_payloads[source.get("source_url")] = payload
        return payload, source

    def _capture_event_detail(self, day, source):
        self.sources.append(EventReport(str(day), str(day), source, detail=True,
                                        payload=self.detail_payloads.get(source.get("source_url"))))

    def collect(self, symbol, start, end):
        return self._for_target(symbol, end, self._collect_range(symbol, start, end))

    def collect_targets(self, symbol, start, targets):
        # Range receipts establish coverage for all the enclosed windows. Only
        # dates lacking a target quote need an additional suspension lookup.
        targets = sorted(set(targets))
        if len(targets) == 1:
            target, = targets
            return {target: self.collect(symbol, start, target)}
        evidence = self._collect_range(symbol, start, targets[-1])
        return {target: self._for_target(symbol, target, evidence) for target in targets}

    def _collect_range(self, symbol, start, end):
        from ai_stock_sentinel.daily_radar.next_open_action_scope import _events
        from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy
        from ai_stock_sentinel.daily_radar.trading_calendar import months
        from ai_stock_sentinel.daily_radar.work_queue import LostLease

        unavailable = set()
        retryable_prices = set()

        from ai_stock_sentinel.daily_radar.rights_terms import read_terms
        from ai_stock_sentinel.calibration.research_prices import ResearchSourceError
        terms, unavailable_terms = {}, set()

        def record_rights(ticker, day, row, source):
            if row.get("權/息") == "權息" and self.return_basis != "signal_close":
                return
            try:
                result = read_terms(ticker, day, row, source, self.capture_twse_report)
            except LostLease:
                raise
            except (SourceDependencyBusy, OSError):
                unavailable_terms.add(str(day))
                return
            except ResearchSourceError as exc:
                if exc.retryable:
                    unavailable_terms.add(str(day))
                return
            except (ValueError, RuntimeError):
                return  # Unsupported or contradictory terms retain the original gap.
            if result:
                terms[str(day)] = result
                if ticker.endswith(".TW") and result["kind"] == "stock_distribution":
                    self._capture_event_detail(day, result["source"])

        def add_unavailable(first, last, *, retryable_price=False):
            days = {str(first + timedelta(days=i)) for i in range((last - first).days + 1)}
            unavailable.update(days)
            if retryable_price:
                retryable_prices.update(days)

        def collect_prices(ticker, first, last):
            try:
                rows = self.provider.official_research_history(ticker, start_date=first, end_date=last)
            except LostLease:
                raise
            except (ValueError, RuntimeError, OSError) as exc:
                retryable = isinstance(exc, (SourceDependencyBusy, OSError)) or (
                    isinstance(exc, ResearchSourceError) and exc.retryable)
                add_unavailable(first, last, retryable_price=retryable)
                return {}
            return {row["date"]: row for row in rows}

        stock, benchmark, events, sources, event_coverage = {}, {}, [], [], set()
        for month in months(start, end):
            first = max(month, start)
            next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            last = min(next_month - timedelta(days=1), end)
            stock.update(collect_prices(symbol, first, last))
            benchmark.update(collect_prices("TAIEX", first, last))
            self.sources = []
            self.detail_payloads = {}
            try:
                month_events = _events(symbol, first, last, self, record_detail=self._capture_event_detail,
                                       record_rights=record_rights)
            except LostLease:
                raise
            except (SourceDependencyBusy, ValueError, RuntimeError, OSError):
                add_unavailable(first, last)
                continue
            events.extend(month_events)
            sources.extend(self.sources)
            event_coverage.update(str(first + timedelta(days=i)) for i in range((last - first).days + 1))
        partial_quote = getattr(self.provider, "has_partial_official_price", None)
        partial_stock_dates = frozenset(str(day) for day in
            (start + timedelta(days=k) for k in range((end - start).days + 1))
            if partial_quote is not None and partial_quote(symbol, day))
        return OfficialPriceEvidence(stock, benchmark, events, tuple(sources), frozenset(event_coverage),
                                     unavailable_dates=frozenset(unavailable), rights_terms=terms,
                                     unavailable_terms=frozenset(unavailable_terms), partial_stock_dates=partial_stock_dates,
                                     price_reports=getattr(self.provider, "twse_price_reports", {}),
                                     retryable_price_dates=frozenset(retryable_prices))

    def recover_signal_basis(self, reason, candidate, days, vendor, official, load_snapshot=None):
        from ai_stock_sentinel.daily_radar import disclosed_rights
        if reason == "signal_price_anchor_mismatch":
            from ai_stock_sentinel.daily_radar import dividend_anchor
            return dividend_anchor.recover(candidate, days, vendor, official, self.provider, load_snapshot)
        if reason == "adjustment_factor_unverified" and hasattr(self.provider, "capture_rights_disclosures"):
            return disclosed_rights.recover(candidate, days, vendor, official, self.provider.capture_rights_disclosures)
        return None

    def _for_target(self, symbol, target, evidence):
        from ai_stock_sentinel.calibration.research_prices import ResearchSourceError
        from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy
        from ai_stock_sentinel.daily_radar.trading_status import collect_target_suspension
        from ai_stock_sentinel.daily_radar.work_queue import LostLease

        partial_price = getattr(self.provider, "has_partial_official_price", None)
        if has_trade(evidence.stock, target) or (partial_price and partial_price(symbol, target)):
            return evidence
        try:
            return replace(evidence, target_suspension=collect_target_suspension(self.provider, symbol, target))
        except LostLease:
            raise
        except (SourceDependencyBusy, ResearchSourceError) as exc:
            return replace(evidence, suspension_error=str(exc))
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            return replace(evidence, suspension_error=type(exc).__name__)
