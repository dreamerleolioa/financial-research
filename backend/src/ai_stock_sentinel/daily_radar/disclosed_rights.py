"""Recognize a vendor's trailing pre-rights adjustment using a dated MOPS filing."""
from datetime import timedelta
from decimal import Decimal
from math import isclose

from ai_stock_sentinel.calibration.forward_validation import number
from ai_stock_sentinel.calibration.official_corporate_actions import _roc_date
from ai_stock_sentinel.daily_radar import price_acceptance as prices

URL = "https://mopsov.twse.com.tw/mops/web/ajax_t108sb27"
HEADER = ("公司代號 公司名稱 股利所屬期間 權利分派基準日 股票股利 現金股利 現金增資總股數(股) "
          "現金增資認股比率(%) 現金增資認購價(元/股) 參加分派總股數 公告日期 公告時間 普通股每股面額").split()
SUBHEADER = ("盈餘轉增資配股(元/股) 法定盈餘公積、資本公積轉增資配股(元/股) 除權交易日 "
             "盈餘分配之股東現金股利(元/股) 法定盈餘公積、資本公積發放之現金(元/股) 特別股配發現金股利(元/股) "
             "除息交易日 現金股利發放日").split()


def report_payload(response, *, stock_code):
    from bs4 import BeautifulSoup
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser", from_encoding="utf-8")
    output = []
    recognized = False
    for table in soup.select("table.hasBorder"):
        rows = table.find_all("tr")
        headers = [[cell.get_text(strip=True) for cell in row.find_all("th")] for row in rows[:2]]
        if headers != [HEADER, SUBHEADER]:
            continue
        recognized = True
        for row in rows[2:]:
            cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
            if cells:
                if len(cells) != 19:
                    raise ValueError("invalid_mops_rights_disclosure")
                if cells[0] == stock_code:
                    output.append(cells)
    if not recognized:
        raise ValueError("invalid_mops_rights_disclosure")
    return {"rows": output}


def subscription_terms(rows, symbol, target):
    ex_date = target + timedelta(days=1)
    applicable = []
    for cells in rows:
        if len(cells) != 19 or cells[0] != symbol.removesuffix(".TW"):
            continue
        if _roc_date(cells[16]) > target:
            continue
        if cells[10] and _roc_date(cells[10]) == ex_date:
            return None
        if not cells[6] or _roc_date(cells[6]) != ex_date:
            continue
        applicable.append(cells)
    if len(applicable) != 1:
        return None
    cells, = applicable
    ratio, price = number(cells[13]), number(cells[14])
    if (any(cells[i] and number(cells[i]) != 0 for i in (4, 5, 7, 8, 9)) or cells[10] or cells[11]
            or not ratio or ratio <= 0 or not price or price <= 0
            or not (issued := number(cells[12].replace(",", ""))) or issued <= 0):
        return None
    return {"ex_date": str(ex_date), "disclosed_on": str(_roc_date(cells[16])), "ratio": float(Decimal(cells[13]) / 100),
            "subscription_price": price}


def dated_subscription_filing(symbol, day, capture):
    reports = []
    ex_date = day + timedelta(days=1)
    for cutoff in (day.replace(day=1) - timedelta(days=1), day):
        report, receipt = capture(symbol, cutoff)
        for row in report["rows"]:
            if len(row) != 19 or row[0] != symbol.removesuffix(".TW"):
                continue
            if any(row[i] and _roc_date(row[i]) == ex_date for i in (6, 10)):
                if not cutoff.replace(day=1) <= _roc_date(row[16]) <= cutoff:
                    return None
        reports.append((report["rows"], receipt))
    terms = subscription_terms([row for rows, _ in reports for row in rows], symbol, day)
    if terms:
        return terms, next(receipt for rows, receipt in reports if subscription_terms(rows, symbol, day))
    return None


def subscription_factor(terms, close):
    ratio = terms["ratio"]
    return (close + terms["subscription_price"] * ratio) / ((1 + ratio) * close)


def _matches(row, raw, factor):
    return all(isclose(row[k], raw[k] * factor, rel_tol=1e-7, abs_tol=.00005) for k in prices.OHLC_PRICE_FIELDS)


def recover(candidate, sessions, vendor, official, capture):
    dates = list(map(str, sessions))
    days = set(dates[1:])
    if (not candidate["symbol"].endswith(".TW") or not days.issubset(official.event_coverage or ())
            or set(dates).intersection(official.unavailable_dates | official.partial_stock_dates | official.unavailable_terms)
            or official.events is None or any(e["effective_date"] in days for e in official.events)
            or not prices._quotes_follow_calendar(official, dates)):
        return None
    stock = prices._window(official.stock, dates)
    benchmark = prices._window(official.benchmark, dates)
    index = prices._provider_index(vendor, set(dates))
    if not stock or not benchmark or not index or set(index) != set(dates):
        return None
    anchor = number(candidate["input_snapshot"]["ohlcv"].get("close"))
    if anchor is None or not isclose(anchor, stock[0]["close"], rel_tol=1e-7, abs_tol=.00005):
        return None
    source = [index[d] for d in dates]
    versions = {(r.get("source_capture") or {}).get("snapshot_sha256") for r in source}
    if (len(versions) != 1 or None in versions or any(not prices._quote(r) or not prices._observed_forward_open(r)
                                                       for r in source)):
        return None
    first_changed = next((i for i, (r, raw) in enumerate(zip(source, stock)) if not _matches(r, raw, 1)), None)
    if not first_changed or _matches(source[-1], stock[-1], 1):
        return None
    observed_factor = source[-1]["close"] / stock[-1]["close"]
    if not all(_matches(source[i], stock[i], observed_factor) for i in range(first_changed, len(dates))):
        return None
    report, receipt = capture(candidate["symbol"], sessions[-1])
    terms = subscription_terms(report["rows"], candidate["symbol"], sessions[-1])
    if not terms:
        return None
    factor = subscription_factor(terms, stock[-1]["close"])
    if not all(_matches(source[i], stock[i], factor) and not _matches(source[i], stock[i], 1)
               for i in range(first_changed, len(dates))):
        return None
    evidence = {"kind": "disclosed_post_window_rights", **terms, "first_adjusted_date": dates[first_changed],
                "vendor_factor": factor, "source": receipt["source_url"], "snapshot_sha256": receipt["snapshot_sha256"]}
    return prices._price_lock(candidate, "signal_close", stock, benchmark, official, vendor=source,
                              factors=[1.0] * len(stock), basis_evidence=evidence)


def mixed_issue_disclosure(symbol, day, terms, capture):
    """Reconcile the TWSE six-decimal dividend with the company's full declaration."""
    from decimal import ROUND_DOWN
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

    matches = []
    previous_month = day.replace(day=1) - timedelta(days=1)
    for cutoff in (previous_month, day):
        report, receipt = capture(symbol, cutoff)
        if fingerprint(report) != receipt['snapshot_sha256']:
            return None
        for row in report['rows']:
            if len(row) != 19 or row[0] != symbol.removesuffix('.TW'):
                continue
            if not row[6] or _roc_date(row[6]) != day:
                if row[10] and _roc_date(row[10]) == day:
                    return None
                continue
            if not cutoff.replace(day=1) <= _roc_date(row[16]) <= cutoff:
                return None
            matches.append((row, receipt))
    if len(matches) != 1:
        return None
    row, receipt = matches[0]
    if (not row[10] or _roc_date(row[10]) != day or _roc_date(row[16]) > day
            or any(Decimal(row[i] or '0') != 0 for i in (4, 5, 9))
            or Decimal(row[12].replace(',', '')) != Decimal(str(terms['issued_shares']))
            or Decimal(row[13]) * 10 != Decimal(str(terms['subscription_shares_per_thousand']))
            or Decimal(row[14]) != Decimal(str(terms['subscription_price']))):
        return None
    components = [Decimal(row[i] or '0') for i in (7, 8)]
    if any(not value.is_finite() or value < 0 for value in components):
        return None
    cash = sum(components)
    if cash <= 0 or cash.quantize(Decimal('.000001'), rounding=ROUND_DOWN) != Decimal(str(terms['cash_per_share'])):
        return None
    return {'declared_cash_per_share': str(cash), 'source_url': receipt['source_url'],
            'snapshot_sha256': receipt['snapshot_sha256']}
