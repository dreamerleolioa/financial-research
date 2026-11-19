"""Versioned prices for Taiwan validation windows.

Official final bars determine the raw prices. A signal-close window may
translate them to the signal-date basis only with a verified adjustment.
"""
from dataclasses import dataclass, field, replace
from math import isclose
from statistics import median
from collections.abc import Mapping
from datetime import timedelta
from urllib.parse import urlsplit

from ai_stock_sentinel.calibration.forward_validation import OHLC_PRICE_FIELDS, number
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

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


@dataclass(frozen=True)
class AcceptedPrices:
    stock: list = field(default_factory=list)
    benchmark: list = field(default_factory=list)
    proof: dict = field(default_factory=dict)
    reason: str | None = None

    def with_proof(self, row):
        return row | {"outcome": row["outcome"] | {PRICE_EVIDENCE: self.proof}}


def quotes_match(actual, expected):
    return all(
        (a := number(actual.get(key))) is not None
        and (b := number(expected.get(key))) is not None
        and isclose(a, b, abs_tol=PRICE_ABS_TOL, rel_tol=PRICE_REL_TOL)
        for key in OHLC_PRICE_FIELDS
    )


def verify_signal_bars(db, rows, *, run_date):
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
        accepted[row.symbol] = evidence | {"sha256": fingerprint(evidence)}
    return accepted, rejected


def signal_price_gaps(db, rows, *, run_date):
    from ai_stock_sentinel.daily_radar.raw_data import current_daily_radar_raw_rows
    return verify_signal_bars(db, current_daily_radar_raw_rows(rows, run_date=run_date), run_date=run_date)[1]


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


def _price_lock(candidate, basis, stock, benchmark, official, *, vendor=(), factors=(), rights_terms=(), vendor_benchmark=()):
    source_versions = {row["price_source"] for row in [*stock, *benchmark]}
    events, reports = _event_evidence(official, {row["date"] for row in stock[1:]})
    vendor_rows = [{"date": row["date"], **_quote(row), "price_source": row.get("price_source"),
                    "source": _source_identity(row.get("source_capture") or {})}
                   for row in sorted(vendor, key=lambda row: row["date"])]
    data = {
        "version": VERSION, "return_basis": basis, "symbol": candidate["symbol"],
        "signal_date": candidate["record_date"], "stock": stock, "benchmark": benchmark,
        "source_versions": sorted(source_versions), "vendor_window_sha256": fingerprint(vendor_rows),
        "event_reports": reports, "events": events, "factors": list(factors),
    }
    if rights_terms:
        data["rights_terms"] = list(rights_terms)
        if basis == 'next_open':
            data['rights_value_included'] = data['subscription_cash_flow_included'] = False
            data['rights_vendor_action_sha256'] = fingerprint([
                [r['date'], r.get('stock_splits'), r.get('dividends')] for r in vendor])
            data['rights_vendor_benchmark_sha256'] = fingerprint([
                [r['date'], _quote(r), _source_identity(r.get('source_capture') or {})] for r in vendor_benchmark])
    return AcceptedPrices(stock, benchmark, data | {"sha256": fingerprint(data)})


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
    if anchor is None or not isclose(anchor, stock[0]["close"], abs_tol=PRICE_ABS_TOL, rel_tol=PRICE_REL_TOL):
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
    return _price_lock(candidate, "signal_close", prices, benchmark, official, vendor=source, factors=factors)


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


def _reviewed_inputs_sha256(row):
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
    detail_paths = {twse_rights_recovery.RIGHTS_DETAIL, official_corporate_actions.TWSE_REDUCTION_DETAIL}
    return fingerprint(_stable_reviewed_input(version_input, detail_paths))


def lock_reviewed_next_open(row, official):
    """Freeze the full replay separately from the window's price version."""
    outcome = row["outcome"]
    evidence = outcome["reviewed_official_window"]
    events, reports = _event_evidence(official, set(evidence["session_dates"]))
    lock = {"version": VERSION, "return_basis": "next_open", "symbol": row["symbol"],
            "signal_date": row["signal_date"], "reviewed_inputs_sha256": _reviewed_inputs_sha256(row),
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
    if "reviewed_official_window_sha256" in proof:
        if proof["reviewed_official_window_sha256"] != fingerprint(outcome.get("reviewed_official_window")):
            raise ValueError("invalid_validation_price_lock")
        return
    if "reviewed_inputs_sha256" in proof:
        from ai_stock_sentinel.daily_radar.next_open_official_repair import verify_event_report_receipts

        if (not isinstance(outcome.get("reviewed_official_window"), dict)
                or outcome.get("reviewed_official_window_sha256") != fingerprint(outcome["reviewed_official_window"])
                or proof["reviewed_inputs_sha256"] != _reviewed_inputs_sha256(row)):
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
    def __init__(self, provider):
        self.provider = provider
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

        from ai_stock_sentinel.daily_radar.rights_terms import read_terms
        from ai_stock_sentinel.calibration.research_prices import ResearchSourceError
        terms, unavailable_terms = {}, set()

        def record_rights(ticker, day, row, source):
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

        def add_unavailable(first, last):
            unavailable.update(str(first + timedelta(days=i)) for i in range((last - first).days + 1))

        def collect_prices(ticker, first, last):
            try:
                rows = self.provider.official_research_history(ticker, start_date=first, end_date=last)
            except LostLease:
                raise
            except (SourceDependencyBusy, ValueError, RuntimeError, OSError):
                add_unavailable(first, last)
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
                                     unavailable_terms=frozenset(unavailable_terms), partial_stock_dates=partial_stock_dates)

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
