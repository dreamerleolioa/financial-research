from datetime import date, timedelta

from ai_stock_sentinel.daily_radar.medium_term import build_medium_term_context


def _inputs():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(70)]
    record = {"record_date": days[-1].isoformat(), "price_history": [
        {"date": day.isoformat(), "close": 100 + i} for i, day in enumerate(days)
    ]}
    context = {"benchmark": {"price_history": [
        {"date": day.isoformat(), "close": 100 + i / 5} for i, day in enumerate(days)
    ]}}
    return record, context


def test_medium_term_requires_contiguous_same_date_history_and_preserves_relative_strength():
    record, market = _inputs()
    result = build_medium_term_context(record, market)
    assert result["trend_status"] == "constructive"
    assert result["relative_strength_60d"] > 0
    assert result["ma60_slope_5d_pct"] > 0
    assert len(result["aligned_dates"]) == 65
    # A future price cannot alter the point-in-time trend.
    record["price_history"].append({"date": "2099-01-01", "close": 1})
    assert build_medium_term_context(record, market) == result


def test_medium_term_missing_candidate_session_never_relaxes_filter():
    record, market = _inputs()
    del record["price_history"][-5]
    result = build_medium_term_context(record, market)
    assert result["trend_status"] == "unknown"
    assert result["missing_reason"] == "candidate_history_gap"
    assert result["relative_strength_60d"] is None


def test_medium_term_stale_benchmark_and_short_history_are_unknown():
    record, market = _inputs()
    market["benchmark"]["price_history"].pop()
    assert build_medium_term_context(record, market)["missing_reason"] == "benchmark_not_current"
    record, market = _inputs()
    market["benchmark"]["price_history"] = market["benchmark"]["price_history"][-60:]
    assert build_medium_term_context(record, market)["trend_status"] == "unknown"
