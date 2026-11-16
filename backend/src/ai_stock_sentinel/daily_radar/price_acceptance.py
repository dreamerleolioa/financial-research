"""Versioned prices for Taiwan validation windows.

Official final bars determine the raw prices. A signal-close window may
translate them to the signal-date basis only with a verified adjustment.
"""
from dataclasses import dataclass, field
from math import isclose
from statistics import median
from collections.abc import Mapping
from datetime import timedelta

from ai_stock_sentinel.calibration.forward_validation import OHLC_PRICE_FIELDS, number
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

PRICE_EVIDENCE = "validation_price_evidence"
VERSION = "radar-validation-prices-v1"
RETRYABLE_PRICE_REASONS = frozenset({
    "adjustment_factor_unverified", "conflicting_price_records", "official_corporate_action_in_window",
    "official_validation_events_unverified", "official_validation_price_missing", "signal_price_anchor_mismatch",
})
PRICE_REL_TOL = 1e-7
PRICE_ABS_TOL = .005
FACTOR_TOL = 1e-5


@dataclass(frozen=True)
class OfficialPriceEvidence:
    stock: Mapping
    benchmark: Mapping
    events: list | None
    report_sources: tuple = ()
    event_coverage: frozenset | None = None


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


def _window(rows, dates):
    values = []
    for day in dates:
        row = rows.get(day) or {}
        quote = _quote(row)
        if quote is None or row.get("date") != day or not row.get("price_source"):
            return None
        values.append({"date": day, **quote, "price_source": row["price_source"],
                       **({"official_price_report": _report_version(row["official_price_report"])}
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


def _report_version(proof):
    return {key: proof[key] for key in ("source_url", "snapshot_sha256") if key in proof}


def _price_lock(candidate, basis, stock, benchmark, official, *, vendor=(), factors=()):
    source_versions = {row["price_source"] for row in [*stock, *benchmark]}
    vendor_versions = {proof["snapshot_sha256"] for row in vendor
                       if (proof := row.get("source_capture")) and proof.get("snapshot_sha256")}
    data = {
        "version": VERSION, "return_basis": basis, "symbol": candidate["symbol"],
        "signal_date": candidate["record_date"], "stock": stock, "benchmark": benchmark,
        "source_versions": sorted(source_versions), "vendor_versions": sorted(vendor_versions),
        "event_reports": [_report_version(proof) for proof in official.report_sources],
        "events": official.events, "factors": list(factors),
    }
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
    stock = _window(official.stock, dates)
    benchmark = _window(official.benchmark, dates)
    if not stock or not benchmark:
        return AcceptedPrices(reason="official_validation_price_missing")
    anchor = number((candidate.get("input_snapshot", {}).get("ohlcv") or {}).get("close"))
    if anchor is None or not isclose(anchor, stock[0]["close"], abs_tol=PRICE_ABS_TOL, rel_tol=PRICE_REL_TOL):
        return AcceptedPrices(reason="signal_price_anchor_mismatch")

    adjusted = _provider_index(adjusted_rows, set(dates))
    if adjusted is None:
        return AcceptedPrices(reason="conflicting_price_records")
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
    for before, after, prev in zip(ratios[:-1], ratios[1:], stock[:-1], strict=True):
        day = dates[len(factors)]
        dividend = cash.get(day, 0.0)
        if dividend >= prev["close"]:
            return AcceptedPrices(reason="adjustment_factor_unverified")
        expected = prev["close"] / (prev["close"] - dividend)
        if not isclose(after / before, expected, rel_tol=FACTOR_TOL):
            return AcceptedPrices(reason="adjustment_factor_unverified")
        factor *= expected
        factors.append(factor)
    # Keep the original entry and resistance/support scale. Transform every
    # OHLC component together using verified effective-day events.
    anchor_factor = anchor / stock[0]["close"]
    prices = [row | {key: row[key] * factor * anchor_factor for key in OHLC_PRICE_FIELDS}
              for row, factor in zip(stock, factors, strict=True)]
    return _price_lock(candidate, "signal_close", prices, benchmark, official, vendor=source, factors=factors)


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
    def research_rows(rows, cash_values):
        return [row | {"price_basis": "unadjusted_price", "corporate_actions_checked": True,
                       "corporate_action_source": "official_reports", "stock_splits": 0,
                       "dividends": cash_values.get(row["date"], 0.0)} for row in rows]
    return _price_lock(candidate, "next_open", research_rows(stock, cash),
                       research_rows(benchmark, {}), official, vendor=vendor.values())


def refused_prices(row, reason):
    return row | {"status": "skipped", "skip_reason": reason, "target_date": None,
                  "outcome": {"expected_target_date": row.get("target_date")
                              or row.get("outcome", {}).get("expected_target_date")}}


def lock_reviewed_next_open(row):
    """The official repair evaluator already retains the full used inputs."""
    evidence = row["outcome"]["reviewed_official_window"]
    lock = {"version": VERSION, "return_basis": "next_open", "symbol": row["symbol"],
            "signal_date": row["signal_date"], "reviewed_official_window_sha256": fingerprint(evidence)}
    return row | {"outcome": row["outcome"] | {PRICE_EVIDENCE: lock | {"sha256": fingerprint(lock)}}}


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

    def capture_twse_report(self, path, params):
        payload, source = self.provider.capture_twse_report(path, params)
        self.sources.append(source)
        return payload, source

    def capture_tpex_report(self, path, params):
        payload, source = self.provider.capture_tpex_report(path, params)
        self.sources.append(source)
        return payload, source

    def collect(self, symbol, start, end):
        from ai_stock_sentinel.daily_radar.next_open_action_scope import _events
        from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy
        from ai_stock_sentinel.daily_radar.trading_calendar import months
        from ai_stock_sentinel.daily_radar.work_queue import LostLease

        def collect_prices(ticker, first, last):
            try:
                rows = self.provider.official_research_history(ticker, start_date=first, end_date=last)
            except (SourceDependencyBusy, LostLease):
                raise
            except (ValueError, RuntimeError, OSError):
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
            try:
                month_events = _events(symbol, first, last, self)
            except (SourceDependencyBusy, LostLease):
                raise
            except (ValueError, RuntimeError, OSError):
                continue
            events.extend(month_events)
            sources.extend(self.sources)
            event_coverage.update(str(first + timedelta(days=i)) for i in range((last - first).days + 1))
        return OfficialPriceEvidence(stock, benchmark, events, tuple(sources), frozenset(event_coverage))
