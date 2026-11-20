from datetime import date

import pytest


def test_vendor_reverse_split_without_refund_or_receipt_cannot_dispose_of_old_split():
    from ai_stock_sentinel.daily_radar.reviewed_reduction_window import reduction_witness

    with pytest.raises(ValueError):
        reduction_witness(
            "6173.TWO",
            {"halt_start": "2026-10-07", "share_ratio": 0.94, "cash_per_share": 0.6},
            {"2026-10-07": {"stock_splits": 0.94, "dividends": 0.6}},
            [{}, {}],
        )


def test_raw_chart_binds_vendor_units_date_and_refund():
    from datetime import datetime, time
    from urllib.parse import urlencode
    from zoneinfo import ZoneInfo
    from ai_stock_sentinel.daily_radar.history_repair import fingerprint
    from ai_stock_sentinel.daily_radar.reviewed_reduction_window import reduction_witness

    symbol, day = "6173.TWO", date(2026, 10, 7)
    start = int(datetime.combine(day, time(), ZoneInfo("Asia/Taipei")).timestamp())
    stamp = start + 9 * 3600
    params = dict(period1=start, period2=start + 86400, interval="1d", events="div,splits")
    cash, split = (
        {"amount": 0.6, "date": stamp},
        {"date": stamp, "numerator": 940.0, "denominator": 1000.0, "splitRatio": "940:1000"},
    )
    raw = {
        "chart": {
            "error": None,
            "result": [
                {
                    "timestamp": [stamp],
                    "meta": {"symbol": symbol, "exchangeTimezoneName": "Asia/Taipei"},
                    "events": {"splits": {str(stamp): split}, "dividends": {str(stamp): cash}},
                }
            ],
        }
    }
    receipt = {
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol + "?" + urlencode(params),
        "captured_at": "2026-11-20T00:00:00+00:00",
        "snapshot_sha256": fingerprint(raw),
    }
    event = {"halt_start": str(day), "share_ratio": 0.94, "cash_per_share": 0.6}
    archived = {str(day): {"stock_splits": 0.94, "dividends": 0.6}}
    reduction_witness(symbol, event, archived, [raw, receipt])
    with pytest.raises(ValueError):
        reduction_witness(symbol, event | {"cash_per_share": 1.6}, archived, [raw, receipt])
    with pytest.raises(ValueError):
        reduction_witness(symbol, event | {"share_ratio": 0.95}, archived, [raw, receipt])


def test_reduction_cannot_explain_a_benchmark_disagreement():
    from ai_stock_sentinel.daily_radar.history_repair import fingerprint
    from ai_stock_sentinel.daily_radar.reviewed_reduction_window import verify_benchmark

    day = "2026-10-02"
    official = {day: dict(date=day, open=100, high=102, low=99, close=101)}
    row = official[day] | dict(
        price_source="yfinance",
        price_basis="unadjusted_price",
        corporate_actions_checked=True,
        dividends=0,
        stock_splits=0,
    )

    def archive(bar):
        body = dict(symbol="TAIEX", basis="next_open", source="yfinance", rows=[bar])
        return body | {"sha256": fingerprint(body)}

    verify_benchmark(archive(row), [day], official)
    with pytest.raises(ValueError):
        verify_benchmark(archive(row | {"high": 105}), [day], official)


@pytest.mark.parametrize("location", ["target_date", "expected_target_date", "outcome_target"])
def test_conflicting_saved_target_cannot_be_moved(location):
    from types import SimpleNamespace
    from datetime import timedelta
    from ai_stock_sentinel.daily_radar import reviewed_reduction_window as review
    from ai_stock_sentinel.daily_radar.history_repair import fingerprint

    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(5)]
    c = {"candidate_id": 1, "record_date": str(days[0]), "symbol": "6173.TWO"}
    old = {
        "validation_version": review.RESEARCH_VALIDATION_VERSION,
        "signal_date": str(days[0]),
        "candidate_id": 1,
        "benchmark_symbol": "TAIEX",
        "status": "skipped",
        "skip_reason": "conflicting_price_records",
        "outcome": {},
        "window_days": 3,
    }
    if location == "target_date":
        old["target_date"] = str(days[-1])
    else:
        old["outcome"]["target_date" if location == "outcome_target" else location] = str(days[-1])
    history = {"symbol": c["symbol"], "basis": "next_open", "source": "yfinance", "start": str(days[0])}
    with pytest.raises(ValueError, match="original_target_mismatch"):
        review.evaluate(
            c,
            old,
            SimpleNamespace(sessions=days, through=days[-1]),
            {"symbol": c["symbol"]},
            history | {"sha256": fingerprint(history)},
            [],
            {},
        )
