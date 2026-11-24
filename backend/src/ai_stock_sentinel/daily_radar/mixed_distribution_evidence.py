"""Replay TWSE reports for a single mixed distribution in a signal-close window."""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from math import isclose
from urllib.parse import parse_qsl, urlsplit

from ai_stock_sentinel.calibration import twse_rights_recovery as twse
from ai_stock_sentinel.calibration.official_corporate_actions import _decimal_text
from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.daily_radar.price_acceptance import PRICE_ABS_TOL, PRICE_REL_TOL, _quote
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
from ai_stock_sentinel.phase1_avwap.provider import _twse_trade_date

PRICE_PATHS = {"/exchangeReport/STOCK_DAY", "/indicesReport/MI_5MINS_HIST"}
MARKET_PATHS = {twse.EX_RIGHTS: "資料日期", twse.REDUCTION: "恢復買賣日期", twse.FACE_VALUE: "恢復買賣日期"}


def _official_query(url):
    uri = urlsplit(url)
    query = parse_qsl(uri.query, keep_blank_values=True)
    params = dict(query)
    if (uri.scheme != "https" or uri.netloc != "www.twse.com.tw" or uri.fragment
            or len(params) != len(query) or params.get("response") != "json"):
        raise ValueError("invalid_mixed_distribution_source")
    return uri.path, params


def _terms_match_listing(row, detail):
    previous = Decimal(_decimal_text(row["除權息前收盤價"]))
    cash_field = "(每股配發現金股利)除息"
    free_field = "A. 按普通股股東持股比例每千股無償配股"
    (terms,) = twse._report_rows(detail, [cash_field, free_field])
    cash = Decimal(_decimal_text(terms[cash_field], "元／股"))
    free = Decimal(_decimal_text(terms[free_field], "股"))
    reference = (previous - cash) / (1 + free / 1000)
    # TWT49U displays the reference price to cents and the combined value to six decimals.
    return all(Decimal(_decimal_text(row[field])) == value.quantize(Decimal(unit), rounding=ROUND_HALF_UP)
               for field, value, unit in (("除權息參考價", reference, ".01"),
                                          ("權值+息值", previous - reference, ".000001")))


def _action_reports(proof, code, dates):
    start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    days = {start + timedelta(days=n) for n in range(1, (end-start).days + 1)}
    coverage = {path: {} for path in MARKET_PATHS}
    listing_url = listing_row = detail_url = None
    action = proof["action"]
    ex_date = date.fromisoformat(action["effective_date"])
    for url, payload in proof["action_reports"].items():
        path, params = _official_query(url)
        if path == twse.RIGHTS_DETAIL:
            if detail_url or params != {"response": "json", "STK_NO": code, "T1": ex_date.strftime("%Y%m%d")}:
                return None
            detail_url = url
            continue
        if path not in MARKET_PATHS or set(params) != {"response", "startDate", "endDate"}:
            return None
        first, last = (date.fromisoformat(params[k]) for k in ("startDate", "endDate"))
        if first > last or first > end or start > last:
            return None
        seen = {}
        rows = twse._dated_rows(payload, params, MARKET_PATHS[path], [], allow_empty_status=path == twse.REDUCTION)
        for day, row in rows:
            if day not in days or str(row["股票代號"]).strip() != code:
                continue
            if day in seen or path != twse.EX_RIGHTS or day != ex_date or row["權/息"] != "權息":
                return None
            seen[day] = twse.market_event_facts(path, row)
            listing_url, listing_row = url, row
        # Covering snapshots can overlap. A missing notice on either side of
        # the intersection is a disagreement, just like different terms.
        for day in days:
            if first <= day <= last:
                observation = seen.get(day)
                if day in coverage[path] and coverage[path][day] != observation:
                    return None
                coverage[path][day] = observation
    if (not listing_url or not detail_url or any(observed.keys() != days for observed in coverage.values())
            or not _terms_match_listing(listing_row, proof["action_reports"][detail_url])):
        return None
    return {url: proof["action_reports"][url] for url in (listing_url, detail_url)}


def _prices_match(proof, symbol, dates, published_close):
    keys = {(path, day[:7].replace("-", "")) for path in PRICE_PATHS for day in dates}
    stock, benchmark = {}, {}
    for url, raw in proof["price_reports"].items():
        path, params = _official_query(url)
        month = params["date"][:6]
        if (path, month) not in keys:
            return False
        keys.remove((path, month))
        ticker, prices = ("TAIEX", benchmark) if path == "/indicesReport/MI_5MINS_HIST" else (symbol, stock)
        expected = {"response": "json", "date": month + "01"}
        if ticker != "TAIEX":
            expected["stockNo"] = symbol.split(".")[0]
        if (params != expected or raw["stat"] != "OK" or str(raw["date"])[:6] != month
                or not isinstance(raw["data"], list) or ("total" in raw and
                (type(raw["total"]) is not int or raw["total"] != len(raw["data"])))):
            return False
        raw_days = [str(_twse_trade_date(str(row["日期"]))) for row in twse._report_rows(raw, ["日期"])]
        if (len(raw_days) != len(set(raw_days)) or any(day[:7].replace("-", "") != month for day in raw_days)
                or any(day not in dates for day in raw_days if dates[0] <= day <= dates[-1])):
            return False
        for row in RecoveringResearchPriceProvider._twse_month_rows(ticker, raw):
            day = row["date"]
            if dates[0] <= day <= dates[-1]:
                if day in prices or _quote(row) is None:
                    return False
                prices[day] = row["close"]
    return (not keys and set(stock) == set(benchmark) == set(dates) and stock == proof["stock_closes"]
            and published_close is not None
            and isclose(stock[dates[0]], published_close, abs_tol=PRICE_ABS_TOL, rel_tol=PRICE_REL_TOL))


def verified_action_reports(proof, symbol, dates, published_close):
    """Only the selected listing/detail feed the shared distribution-terms parser."""
    from ai_stock_sentinel.daily_radar.terminal_integrity import _source

    reports = proof["action_reports"] | proof["price_reports"]
    receipts = {s["source_url"]: s for s in proof["sources"]}
    if (len(receipts) != len(proof["sources"]) or reports.keys() != receipts.keys()
            or any(not _source(src) or src["snapshot_sha256"] != fingerprint(reports[url])
                   for url, src in receipts.items())
            or not _prices_match(proof, symbol, dates, published_close)):
        return None
    return _action_reports(proof, symbol.split(".")[0], dates)
