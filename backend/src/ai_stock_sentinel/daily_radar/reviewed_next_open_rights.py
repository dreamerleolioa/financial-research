"""Reviewed TWSE evidence for three existing next-open conflicts."""

from dataclasses import dataclass
from datetime import date
from math import isclose

from ai_stock_sentinel.calibration.forward_validation import number


SIGNAL_DATE = date(2026, 10, 1)
TARGET_DATE = date(2026, 11, 3)
WINDOW_DAYS = 20
BENCHMARK = "TAIEX"
VALIDATION_VERSION = "daily-radar-next-open-price-v2"
PROVENANCE_KEY = "reviewed_rights_recovery"
CONFLICT_REASON = "conflicting_price_records"
SESSIONS = (
    *(date(2026, 10, day) for day in (2, 5, 6, 7, 8, 12, 13, 14, 15, 16, 19, 20, 21, 22, 27, 28, 29, 30)),
    date(2026, 11, 2), TARGET_DATE,
)
TWSE = "https://www.twse.com.tw"
OHLC = ("open", "high", "low", "close")


@dataclass(frozen=True)
class RightsIssue:
    ex_date: date
    previous_close: float
    subscription_price: float
    subscription_shares_per_thousand: float
    captured_at: str
    snapshot_sha256: str

    @property
    def factor(self):
        rate = self.subscription_shares_per_thousand / 1000
        return (self.previous_close + self.subscription_price * rate) / (1 + rate) / self.previous_close


# TWSE TWT49U/TWT49UDetail show paid rights only in this window. The dated
# TWTAUU and TWTB8U reports had no matching events. Times and hashes identify
# the saved UTF-8 JSON snapshots from the read-only investigation.
EVENTS = {
    "1727.TW": RightsIssue(date(2026, 10, 13), 103.50, 81.0, 86.23682261,
                           "2026-11-04T03:05:08.777+00:00",
                           "471a6b4b2637d6021d9183c7a15d97a6563b3f949a888c23dbf4435520f3cd33"),
    "2890.TW": RightsIssue(date(2026, 10, 7), 45.40, 35.15, 34.61290969,
                           "2026-11-04T03:05:08.726+00:00",
                           "8440dc75a7d9bd80f0990690ccbd77db99ed63d1716f73cdd15f80b6011a0be9"),
    "8021.TW": RightsIssue(date(2026, 10, 6), 560.0, 388.0, 21.72000885,
                           "2026-11-04T03:05:08.707+00:00",
                           "b9bcd26c5dcb4bbae37eca16b2518de4ad5a07dad66e07fb4eee11b095a192f8"),
}
ACTION_REPORT_CAPTURES = (
    ("/rwd/zh/exRight/TWT49U", "2026-11-04T03:03:42.700+00:00",
     "fdd50845c6603eabb6b62d4b420ea638c53b3b45dcff7574fe48598f05e5e3ef"),
    ("/exchangeReport/TWTAUU", "2026-11-04T03:05:09.807+00:00",
     "2d288ec120fd626663bacab4e155e7fef9a2200c8bacb3bfd9096433d6280cf2"),
    ("/rwd/zh/change/TWTB8U", "2026-11-04T03:05:43.142+00:00",
     "9ed98e4f0ae65e7b924016d17aaf95d18882a5f63cb897722ce6f2581b37fffb"),
)


def matches_window(symbol, signal_date, window_days, validation_version):
    return (symbol in EVENTS and signal_date == SIGNAL_DATE and window_days == WINDOW_DAYS
            and validation_version == VALIDATION_VERSION)


def _daily_rows(rows, expected_dates, source):
    by_date = {}
    expected = {day.isoformat() for day in expected_dates}
    for row in rows:
        day = row.get("date")
        values = [number(row.get(field)) for field in OHLC]
        if (day not in expected or day in by_date or row.get("price_source") != source
                or row.get("ohlc_conflict_fields") or any(value is None for value in values)):
            raise ValueError("reviewed_rights_invalid_price_row")
        open_, high, low, close = values
        if not 0 < low <= min(open_, close) <= max(open_, close) <= high:
            raise ValueError("reviewed_rights_invalid_price_row")
        by_date[day] = row | dict(zip(OHLC, values))
    if by_date.keys() != expected:
        raise ValueError("reviewed_rights_incomplete_price_window")
    return by_date


def _unadjusted_rows(rows, action_source):
    return [row | {"price_basis": "unadjusted_price", "corporate_actions_checked": True,
                   "corporate_action_source": action_source, "dividends": 0, "stock_splits": 0}
            for row in rows]


def official_benchmark(rows):
    official = _daily_rows(rows, (SIGNAL_DATE, *SESSIONS), "twse_index_history")
    return _unadjusted_rows(official.values(), "index_not_applicable")


def official_stock(symbol, rows, yahoo_rows):
    event = EVENTS[symbol]
    official = _daily_rows(rows, SESSIONS, "twse_stock_day")
    # The official calendar owns session membership. Ignore Yahoo-only dates.
    yahoo = _daily_rows((row for row in yahoo_rows if row.get("date") in official), SESSIONS, "yfinance")
    ex_day = event.ex_date.isoformat()
    previous = official[max(day for day in official if day < ex_day)]
    if not isclose(previous["close"], event.previous_close, rel_tol=0, abs_tol=0.005):
        raise ValueError("reviewed_rights_reference_close_mismatch")
    adjusted_dates = []
    for day, raw in sorted(official.items()):
        observed = yahoo[day]
        if (observed.get("stock_splits") not in (None, 0, 1)
                or observed.get("dividends") not in (None, 0)):
            raise ValueError("reviewed_rights_unexpected_corporate_action")
        if all(isclose(raw[field], observed[field], rel_tol=1e-7, abs_tol=0.005) for field in OHLC):
            continue
        if day >= ex_day or not all(
                isclose(raw[field] * event.factor, observed[field], rel_tol=1e-7, abs_tol=0.00005)
                for field in OHLC):
            raise ValueError("reviewed_rights_unexplained_price_conflict")
        adjusted_dates.append(day)

    stock_no = symbol.split(".")[0]
    detail = f"{TWSE}/rwd/zh/exRight/TWT49UDetail?STK_NO={stock_no}&T1={event.ex_date:%Y%m%d}&response=json"
    date_range = f"response=json&startDate={SIGNAL_DATE:%Y%m%d}&endDate={TARGET_DATE:%Y%m%d}"
    evidence = {
        "evidence_version": "reviewed-twse-rights-2026-11-03-v1",
        "event": {"type": "cash_rights_issue", "symbol": symbol, "ex_date": ex_day,
                  "previous_close": event.previous_close, "subscription_price": event.subscription_price,
                  "subscription_shares_per_thousand": event.subscription_shares_per_thousand,
                  "cash_dividend_per_share": 0, "free_shares_per_thousand": 0, "source_url": detail,
                  "captured_at": event.captured_at, "snapshot_sha256": event.snapshot_sha256},
        "window_action_sources": [
            {"source_url": f"{TWSE}{path}?{date_range}", "captured_at": captured_at, "snapshot_sha256": digest}
            for path, captured_at, digest in ACTION_REPORT_CAPTURES
        ],
        "price_sources": {
            "stock": [f"{TWSE}/exchangeReport/STOCK_DAY?response=json&date={month}&stockNo={stock_no}"
                      for month in ("20261001", "20261101")],
            "benchmark": [f"{TWSE}/indicesReport/MI_5MINS_HIST?response=json&date={month}"
                          for month in ("20261001", "20261101")],
        },
        "session_dates": [day.isoformat() for day in SESSIONS],
        "yahoo_factor": event.factor,
        "yahoo_adjusted_dates": adjusted_dates,
        "rights_value_included": False,
        "subscription_cash_flow_included": False,
    }
    return _unadjusted_rows(official.values(), "reviewed_twse_rights_window"), evidence
