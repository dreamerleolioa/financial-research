"""Explain original versus adjusted quotes using dated exchange subscription terms."""

from math import isclose
from urllib.parse import parse_qs, urlsplit

from ai_stock_sentinel.calibration.forward_validation import OHLC_PRICE_FIELDS, number
from ai_stock_sentinel.daily_radar.price_acceptance import (
    _observed_forward_open,
    _price_lock,
    _provider_index,
    _quote,
    _quotes_follow_calendar,
    _source_identity,
    _window,
    quotes_match,
    research_rows,
)
from ai_stock_sentinel.daily_radar.terminal_integrity import _source


def _issue(event, terms, symbol, previous):
    day = event["effective_date"]
    if (
        event["family"] != "rights"
        or event.get("notice") not in {"權", "除權"}
        or terms.get("date") != day
        or terms.get("kind") != "cash_rights_issue"
        or terms.get("symbol") != symbol
        or not _source(terms.get("source"))
    ):
        return None
    price, shares = terms["subscription_price"], terms["subscription_shares_per_thousand"]
    rate = shares / 1000
    if min(price, rate) <= 0:
        return None
    factor = (previous + price * rate) / (1 + rate) / previous
    receipt = terms["source"]
    source = _source_identity(receipt)
    if symbol.endswith(".TW"):
        source["snapshot_sha256"] = receipt["snapshot_sha256"]
    else:
        source["source_url"] = urlsplit(source["source_url"])._replace(query="").geturl()
    url = urlsplit(receipt["source_url"])
    query = parse_qs(url.query)
    if symbol.endswith(".TW"):
        if (
            url.hostname != "www.twse.com.tw"
            or url.path != "/rwd/zh/exRight/TWT49UDetail"
            or query.get("STK_NO") != [symbol.split(".")[0]]
            or query.get("T1") != [day.replace("-", "")]
        ):
            return None
    else:
        if (
            url.hostname != "www.tpex.org.tw"
            or url.path != "/www/zh-tw/bulletin/exDailyQ"
            or not query.get("startDate")
            or not query.get("endDate")
            or not query["startDate"][0] <= day.replace("-", "/") <= query["endDate"][0]
        ):
            return None
    return factor, terms | {"source": source, "previous_close": previous, "vendor_factor": factor}


def _event_factors(events, official, symbol, stock):
    previous = {row["date"]: before["close"] for before, row in zip(stock[:-1], stock[1:], strict=True)}
    factors, dividends, terms = {}, {}, []
    for event in events:
        day = event["effective_date"]
        prior = previous[day]
        if (
            day in factors
            or event.get("family") != "rights"
            or number(event.get("previous_close")) is None
            or not isclose(number(event["previous_close"]), prior, abs_tol=0.005, rel_tol=1e-7)
        ):
            return None
        if event.get("notice") in {"息", "除息"}:
            cash = number(event.get("cash_dividend"))
            if cash is None or not 0 < cash < prior:
                return None
            dividends[day] = cash
            factors[day] = (prior - cash) / prior
        else:
            issue = _issue(event, official.rights_terms.get(day, {}), symbol, prior)
            if issue is None:
                return None
            factors[day], proof = issue
            terms.append(proof)
    return (factors, dividends, terms) if terms else None


def _subsequent_factors(day, factors):
    # Only successive, still-future events can explain a back-adjusted quote.
    product = 1.0
    for effective, factor in factors.items():
        if effective > day:
            product *= factor
            yield product


def _matches_basis(observed, raw, factors):
    if (observed := _quote(observed)) is None:
        return False
    if quotes_match(observed, raw):
        return True
    return any(
        all(
            number(observed.get(k)) is not None
            and isclose(observed[k], raw[k] * scale, rel_tol=1e-7, abs_tol=0.00005)
            for k in OHLC_PRICE_FIELDS
        )
        for scale in _subsequent_factors(raw["date"], factors)
    )


def _research_prices(vendor, vendor_benchmark, stock, benchmark, factors, dividends):
    dates = {r["date"] for r in stock}
    index = _provider_index(vendor_benchmark, dates)
    if not index or set(index) != dates:
        return None
    for raw in benchmark:
        row = index[raw["date"]]
        if _quote(row) is None or not quotes_match(row, raw):
            return None
        if raw["date"] != stock[0]["date"] and (
            row.get("dividends") != 0 or row.get("stock_splits") not in (0, 1)
        ):
            return None
    for row in vendor:
        day = row["date"]
        if row.get("price_basis") != "unadjusted_price":
            return None
        if day == stock[0]["date"]:
            continue
        if row.get("corporate_actions_checked") is not True or row.get("stock_splits") not in (0, 1):
            return None
        cash = number(row.get("dividends"))
        expected = dividends.get(day, 0.0)
        if cash == expected:
            continue
        # Dividend values in raw-price feeds can themselves be scaled by a
        # later issue. Reconcile the quoted amount before excluding the cash.
        if (
            cash is None
            or not expected
            or not any(
                isclose(cash, expected * f, rel_tol=0, abs_tol=0.0000005)
                for f in (1.0, *_subsequent_factors(day, factors))
            )
        ):
            return None
    return research_rows(stock, dividends), research_rows(benchmark, {})


def accept_rights_prices(candidate, sessions, vendor_rows, official, *, basis, vendor_benchmark=()):
    dates = list(map(str, sessions))
    scope = set(dates[1:])
    symbol = candidate["symbol"]
    if (
        official.events is None
        or official.event_coverage is None
        or not scope.issubset(official.event_coverage)
        or not _quotes_follow_calendar(official, dates)
    ):
        return None
    if any(
        e.get("start_date", e["effective_date"]) <= dates[-1] < e["effective_date"] for e in official.events
    ):
        return None
    events = sorted(
        (e for e in official.events if e["effective_date"] in scope), key=lambda e: e["effective_date"]
    )
    if not events or (symbol.endswith(".TW") and basis == "next_open" and len(events) == 1):
        return None  # Keep existing single-issue TWSE next-open recovery and its proof format.
    stock = _window(official.stock, dates)
    benchmark = _window(official.benchmark, dates)
    by_date = _provider_index(vendor_rows, set(dates))
    if not stock or not benchmark or not by_date or set(by_date) != set(dates):
        return None
    vendor = [by_date[d] for d in dates]
    if basis == "signal_close" and not all(_observed_forward_open(row) for row in vendor):
        return None
    versions = {(bar.get("source_capture") or {}).get("snapshot_sha256") for bar in vendor}
    if len(versions) != 1:
        return None
    parsed = _event_factors(events, official, symbol, stock)
    if not parsed:
        return None
    factors, dividends, terms = parsed
    if not all(_matches_basis(row, raw, factors) for row, raw in zip(vendor, stock, strict=True)):
        return None
    if basis == "next_open":
        prices = _research_prices(vendor, vendor_benchmark, stock, benchmark, factors, dividends)
        if prices is None:
            return None
        return _price_lock(
            candidate,
            basis,
            *prices,
            official,
            vendor=vendor,
            rights_terms=terms,
            vendor_benchmark=sorted(
                (r for r in vendor_benchmark if r["date"] in by_date), key=lambda r: r["date"]
            ),
        )
    anchor = number((candidate.get("input_snapshot", {}).get("ohlcv") or {}).get("close"))
    if anchor is None or not isclose(anchor, stock[0]["close"], rel_tol=1e-7, abs_tol=0.005):
        return None
    cumulative = 1.0
    scales = []
    prices = []
    for row in stock:
        cumulative /= factors.get(row["date"], 1.0)
        scales.append(cumulative)
        scale = cumulative * anchor / stock[0]["close"]
        prices.append(row | {k: row[k] * scale for k in OHLC_PRICE_FIELDS})
    return _price_lock(
        candidate, basis, prices, benchmark, official, vendor=vendor, factors=scales, rights_terms=terms
    )
