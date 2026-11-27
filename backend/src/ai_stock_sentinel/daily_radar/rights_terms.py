"""Exchange terms used to explain a validation window's price basis."""

from ai_stock_sentinel.calibration import twse_rights_recovery as twse
from ai_stock_sentinel.calibration.official_corporate_actions import _numeric


def read_terms(symbol, day, row, receipt, capture, *, disclosures=None):
    mixed_notice = symbol.endswith(".TW") and row.get("權/息") == "權息"
    if symbol.endswith(".TW"):
        raw, receipt = capture(
            twse.RIGHTS_DETAIL,
            {"response": "json", "STK_NO": symbol.split(".")[0], "T1": day.strftime("%Y%m%d")},
        )
        if not isinstance(raw, dict):
            raise ValueError("twse_rights_invalid_report")
        fields = [
            "(每股配發現金股利)除息",
            "A. 按普通股股東持股比例每千股無償配股",
            "B. 員工紅利轉增資",
            "C. (有償) 現金增資",
            "每股認購金額",
            "按股東持股比例每千股認購",
        ]
        details = twse._report_rows(raw, ["股票代號", *fields])
        if len(details) != 1 or str(details[0]["股票代號"]).strip() != symbol.split(".")[0]:
            raise ValueError("twse_rights_detail_identity_mismatch")
        cash, free, bonus, issued, price, shares = (
            twse._amount(details[0][field], unit)
            for field, unit in zip(fields, ["元／股", "股", "股", "股", "元／股", "股"], strict=True)
        )
        if bonus:
            return None
        if mixed_notice:
            detail = {field.strip(): value for field, value in details[0].items()}
            allocations = ("a. 公開承銷", "b. 員工認購", "c. 原股東認購")
            amounts = [twse._amount(detail.get(k), "股") for k in allocations]
            if (len(detail) != len(details[0]) or sum(amounts) != issued
                    or issued > 0 and amounts[-1] <= 0):
                return None
    else:
        cash, free, issued, price, shares = (
            _numeric(row[field])
            for field in (
                "現金股利",
                "每仟股無償配股",
                "現金增資股數",
                "現金增資認購價",
                "按持股比例仟股認購",
            )
        )
    if (cash > 0) != mixed_notice or min(free, issued, price, shares) < 0:
        return None
    if free > 0 and issued == price == shares == 0:
        kind = "stock_distribution"
    elif free == 0 and min(issued, price, shares) > 0:
        kind = "cash_rights_issue"
    else:
        return None
    result = {
        "symbol": symbol,
        "date": str(day),
        "kind": kind,
        **({"cash_per_share": cash} if mixed_notice else {}),
        **({"issued_shares": issued} if mixed_notice and kind == "cash_rights_issue" else {}),
        "free_shares_per_thousand": free,
        "subscription_price": price,
        "subscription_shares_per_thousand": shares,
        "source": receipt,
    }
    if disclosures and result.get("issued_shares"):
        from ai_stock_sentinel.daily_radar.disclosed_rights import mixed_issue_disclosure
        proof = mixed_issue_disclosure(symbol, day, result, disclosures)
        if not proof:
            return None
        result["cash_disclosure"] = proof
    return result


def supported_cash_issue_window(events, signal, target, terms_for):
    """A paid issue can share a window with verified cash dividends or other paid issues."""
    issue = False
    seen = set()
    for event in events or ():
        day = event["effective_date"]
        if event.get("start_date", day) <= target < day:
            return False
        if not signal < day <= target:
            continue
        if event["family"] != "rights" or day in seen:
            return False
        seen.add(day)
        if event.get("notice") in {"息", "除息"} and (event.get("cash_dividend") or 0) > 0:
            continue
        if event.get("notice") not in {"權", "除權", "權息"} or (terms_for(event) or {}).get("kind") != "cash_rights_issue":
            return False
        issue = True
    return issue
