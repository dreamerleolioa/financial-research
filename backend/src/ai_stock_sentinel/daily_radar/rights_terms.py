"""Exchange terms used to explain a validation window's price basis."""

from ai_stock_sentinel.calibration import twse_rights_recovery as twse
from ai_stock_sentinel.calibration.official_corporate_actions import _numeric


def read_terms(symbol, day, row, receipt, capture):
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
    if cash or min(free, issued, price, shares) < 0:
        return None
    if free > 0 and issued == price == shares == 0:
        kind = "stock_distribution"
    elif free == 0 and min(issued, price, shares) > 0:
        kind = "cash_rights_issue"
    else:
        return None
    return {
        "symbol": symbol,
        "date": str(day),
        "kind": kind,
        "free_shares_per_thousand": free,
        "subscription_price": price,
        "subscription_shares_per_thousand": shares,
        "source": receipt,
    }
