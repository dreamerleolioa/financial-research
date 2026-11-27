"""Explain original versus adjusted quotes using dated exchange subscription terms."""

from decimal import Decimal, ROUND_HALF_UP
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
        or event.get("notice") not in {"權", "除權", "權息"}
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
    cash = number((terms.get("cash_disclosure") or {}).get("declared_cash_per_share", terms.get("cash_per_share", 0)))
    if (cash is None or not 0 <= cash < previous
            or (cash > 0) != (event.get("notice") == "權息")):
        return None
    factor = (previous - cash + price * rate) / (1 + rate) / previous
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
            if proof.get("cash_per_share"):
                dividends[day] = float((proof.get("cash_disclosure") or {}).get("declared_cash_per_share", proof["cash_per_share"]))
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


def _research_prices(vendor, vendor_benchmark, stock, benchmark, factors, dividends, terms):
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
        term = next((t for t in terms if t["date"] == day and t.get("cash_disclosure")), {})
        disclosure = term.get("cash_disclosure")
        if disclosure and cash is not None:
            if cash == term["cash_per_share"] or Decimal(str(cash)) == Decimal(disclosure["declared_cash_per_share"]).quantize(
                    Decimal(".000001"), rounding=ROUND_HALF_UP):
                continue
        # Dividend values in raw-price feeds can themselves be scaled by a
        # later issue. Reconcile the quoted amount before excluding the cash.
        later = list(_subsequent_factors(day, factors))
        amounts = [expected]
        if disclosure:
            amounts.append(term["cash_per_share"])
        else:
            later.append(1.0)
        if cash is None or not expected or not any(isclose(cash, amount * factor, rel_tol=0, abs_tol=.0000005)
                                                   for amount in amounts for factor in later):
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
    if not events or (symbol.endswith(".TW") and basis == "next_open" and len(events) == 1 and events[0].get("notice") != "權息"):
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
        # A mixed issue can be quoted on a dividend-only scale by an adjusted
        # provider. Require that one scale to explain every field of the full window.
        if basis != "signal_close" or len(events) != 1 or not terms[0].get("cash_per_share"):
            return None
        term = terms[0]
        cash = float((term.get("cash_disclosure") or {}).get("declared_cash_per_share", term["cash_per_share"]))
        dividend_factor = 1 - cash / term["previous_close"]
        for observed, raw in zip(vendor, stock, strict=True):
            scale = dividend_factor if raw["date"] < term["date"] else 1.0
            quote = _quote(observed)
            if not quote or not all(isclose(quote[k], raw[k] * scale, rel_tol=1e-7, abs_tol=.00005)
                                    for k in OHLC_PRICE_FIELDS):
                return None
    if basis == "next_open":
        prices = _research_prices(vendor, vendor_benchmark, stock, benchmark, factors, dividends, terms)
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
