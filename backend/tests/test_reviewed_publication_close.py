from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from ai_stock_sentinel.daily_radar import history_repair

DAY = date(2026, 7, 15)
DAYS = [DAY + timedelta(days=i) for i in range(7)]


def bar(d, close):
    return dict(date=str(d), open=close, high=close + 1, low=close - 1, close=close)


def inputs():
    stock = {str(d): bar(d, c) for d, c in zip(DAYS, [90, 100, 99, 50, 51, 52, 53])}
    idx = {str(d): bar(d, 200 + i) for i, d in enumerate(DAYS)}
    scope = dict(
        stock=stock,
        index=idx,
        events=[
            dict(date=str(DAYS[3]), kind="rights", previous=99, cash="0", free="1000", shares="0", price="0")
        ],
        errors={},
    )
    c = dict(
        candidate_id=1,
        symbol="2330.TW",
        record_date=str(DAYS[1]),
        selection_status="selected",
        data_dates={"ohlcv": str(DAYS[1])},
        input_snapshot={
            "ohlcv": stock[str(DAY)],
            "indicators": {"support_level": 70, "resistance_level": 130},
        },
    )
    c["observation_origin"] = {
        "scope": "available_public_history",
        "first_seen_date": c["record_date"],
        "candidate_id": 1,
    }
    before = dict(
        candidate_id=1,
        status="validated",
        signal_date=c["record_date"],
        target_date=str(DAYS[-1]),
        window_days=5,
        validation_version=history_repair.FORWARD_VALIDATION_VERSION,
        outcome={
            "entry_price": 90,
            "forward_return_pct": -41.11,
            "target_date": str(DAYS[-1]),
            "observation_diagnostic": {"status": "insufficient_data"},
        },
    )
    return c, before, scope, SimpleNamespace(sessions=tuple(DAYS), through=DAYS[-1])


def test_published_close_correction_is_separate_and_retains_target():
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review

    c, before, scope, cal = inputs()
    result = review.evaluate(c, c["input_snapshot"], before, cal, scope, earlier_date=str(DAY))
    assert result["version"] == review.VERSION and result["metrics"]["entry_price"] == 100
    assert result["metrics"]["target_price"] == 106 and result["metrics"]["forward_return_pct"] == 6
    assert result["target_date"] == before["target_date"]
    assert result["original_entry_price"] == 90 and before["outcome"]["forward_return_pct"] == -41.11
    assert result["diagnostic"]["status"] == "unconfirmed"


@pytest.mark.parametrize(
    "damage",
    [
        "stale_four_prices",
        "missing_bar",
        "wrong_target",
        "unverified_event",
        "duplicate_event",
        "negative_terms",
        "changed_reference",
    ],
)
def test_incomplete_or_contradictory_evidence_preserves_gap(damage):
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review

    c, before, scope, cal = inputs()
    if damage == "stale_four_prices":
        c["input_snapshot"]["ohlcv"] = c["input_snapshot"]["ohlcv"] | {"low": 25}
    elif damage == "missing_bar":
        del scope["stock"][str(DAYS[2])]
    elif damage == "wrong_target":
        before["target_date"] = str(DAYS[-2])
    elif damage == "unverified_event":
        scope["errors"][DAYS[2].strftime("%Y-%m")] = "source_unavailable"
    elif damage == "duplicate_event":
        scope["events"] *= 2
    elif damage == "negative_terms":
        scope["events"][0]["free"] = "-1000"
    elif damage == "changed_reference":
        scope["events"][0]["previous"] = 98
    with pytest.raises(ValueError):
        review.evaluate(c, c["input_snapshot"], before, cal, scope, earlier_date=str(DAY))


def test_matching_close_with_different_intraday_prices_is_disclosed():
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review

    c, before, scope, cal = inputs()
    c["input_snapshot"]["ohlcv"] = dict(scope["stock"][c["record_date"]], high=109)
    result = review.evaluate(c, c["input_snapshot"], before, cal, scope)
    assert result["original_quote_status"] == "publication_close_only_verified"


def test_holiday_action_requires_proven_next_trading_reference():
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review

    c, before, scope, cal = inputs()
    offday = "2026-07-19"
    cal.sessions = tuple(d for d in cal.sessions if str(d) != offday)
    before["window_days"] -= 1
    scope["events"][0].update(date=offday, previous=50, free="0", cash="1", resumed_quote_date="2026-07-20")
    del scope["stock"][offday]
    del scope["index"][offday]
    assert (
        review.evaluate(c, c["input_snapshot"], before, cal, scope, earlier_date=str(DAY))[
            "corporate_actions"
        ][0]["date"]
        == offday
    )
    del scope["events"][0]["resumed_quote_date"]
    with pytest.raises(ValueError):
        review.evaluate(c, c["input_snapshot"], before, cal, scope, earlier_date=str(DAY))


def test_proven_halt_keeps_original_target_and_uses_only_traded_bars(monkeypatch):
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review, trading_status

    c, before, scope, cal = inputs()
    lost = str(DAYS[2])
    scope["events"] = []
    del scope["stock"][lost]
    scope["suspensions"] = {lost: {"target_date": lost}}
    monkeypatch.setattr(
        trading_status, "matches", lambda proof, *, symbol, target: proof == {"target_date": str(target)}
    )
    result = review.evaluate(c, c["input_snapshot"], before, cal, scope, earlier_date=str(DAY))
    assert result["suspended_sessions"] == [lost] and result["target_date"] == before["target_date"]
    target = before["target_date"]
    del scope["stock"][target]
    scope["suspensions"][target] = {"target_date": target}
    result = review.evaluate(c, c["input_snapshot"], before, cal, scope, earlier_date=str(DAY))
    assert result["metrics"] is None and result["status"] == "target_session_suspended"
    del scope["suspensions"][target]
    with pytest.raises(ValueError):
        review.evaluate(c, c["input_snapshot"], before, cal, scope, earlier_date=str(DAY))


def test_unverified_source_keeps_original_candle_and_no_inferred_date():
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review

    candidate, before, scope, calendar = inputs()
    candidate["input_snapshot"]["ohlcv"] = bar(DAY, 77)
    corrected = review.evaluate(
        candidate, candidate["input_snapshot"], before, calendar, scope, unverified=True
    )
    assert corrected["original_quote_status"] == review.UNVERIFIED
    assert corrected["original_quote_date"] is None and corrected["original_ohlc"]["close"] == 77
    assert corrected["metrics"]["entry_price"] == 100


@pytest.mark.parametrize("fault", ["body", "symbol", "host", "query", "duplicated_parameter"])
def test_frozen_official_report_cannot_be_substituted(fault):
    from ai_stock_sentinel.daily_radar.publication_close_reports import Reports

    host, path = "https://www.twse.com.tw", "/exchangeReport/STOCK_DAY"
    params = {"stockNo": "2330", "response": "json", "date": "20260701"}
    url = host + path + "?response=json&stockNo=2330&date=20260701"
    body = {"stat": "OK", "data": []}
    request = {"url": host + path, "params": params}
    proof = {
        "source_url": url,
        "captured_at": "2026-11-20T00:00:00+00:00",
        "snapshot_sha256": history_repair.fingerprint(body),
    }
    record = {"request": request, "payload": body, "proof": proof}
    reader = Reports(reports={history_repair.fingerprint(request): record})
    assert reader.capture_twse_report(path, params) == (body, proof)
    if fault == "body":
        body["stat"] = "incomplete"
    elif fault == "symbol":
        proof["source_url"] = url.replace("2330", "2317")
    elif fault == "host":
        proof["source_url"] = url.replace("twse", "tpex")
    elif fault == "query":
        proof["source_url"] = url.replace("20260701", "20260801")
    else:
        proof["source_url"] = url + "&stockNo=2330"
    with pytest.raises(ValueError):
        reader.capture_twse_report(path, params)


def test_market_calendar_and_official_quotes_cannot_disagree():
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review

    candidate, before, scope, calendar = inputs()
    closed = DAYS[4]
    calendar.sessions = tuple(d for d in calendar.sessions if d != closed)
    before["window_days"] -= 1
    with pytest.raises(ValueError, match="calendar"):
        review.evaluate(
            candidate, candidate["input_snapshot"], before, calendar, scope, earlier_date=str(DAY)
        )


@pytest.mark.parametrize("marker", ["legacy", {"publication_close_correction": []}])
def test_malformed_historical_marker_keeps_suspended_target_open(marker):
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review

    candidate, before, _, _ = inputs()
    before["outcome"].update(
        historical_repair=marker, observation_diagnostic={"status": "target_session_suspended"}
    )
    assert not review.has_suspended_target(candidate, before, DAYS[-1])


def test_missing_original_quote_date_is_replaced_only_for_evaluation():
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review

    c, before, scope, calendar = inputs()
    c["data_dates"] = {}
    c["input_snapshot"]["ohlcv"] = bar(DAY, 76)
    result = review.evaluate(c, c["input_snapshot"], before, calendar, scope, unverified=True)
    assert result["diagnostic"]["status"] == "unconfirmed" and c["data_dates"] == {}


def test_verified_suspended_target_is_accounted_for_in_reports(monkeypatch):
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review, trading_status
    from ai_stock_sentinel.daily_radar.observation_validation import observation_report

    c, before, scope, cal = inputs()
    target = before["target_date"]
    del scope["stock"][target]
    scope["suspensions"] = {target: {"target_date": target}}
    monkeypatch.setattr(
        trading_status, "matches", lambda proof, *, symbol, target: proof == {"target_date": str(target)}
    )
    corrected = review.evaluate(c, c["input_snapshot"], before, cal, scope, earlier_date=str(DAY))
    after = before | {
        "outcome": before["outcome"]
        | {
            "historical_repair": {"version": review.VERSION, "publication_close_correction": corrected},
            "observation_diagnostic": corrected["diagnostic"] | {"price_basis_version": review.VERSION},
        }
    }
    stats = observation_report([c], [after], [5], as_of_date=cal.through)["cohorts"][0]["windows"]["5"][
        "all_selected"
    ]
    assert stats["missing_diagnostic_count"] == 0 and stats["coverage_complete"]
    assert (
        stats["status_counts"] == {"target_session_suspended": 1}
        and stats["evaluated_observation_count"] == 0
    )
    assert review.performance_rows([c], [after])[0]["status"] == "skipped" and after["status"] == "validated"


def test_stock_only_halt_can_resume_in_the_following_month(monkeypatch):
    from ai_stock_sentinel.daily_radar import reviewed_publication_close as review

    july, aug = date(2026, 7, 31), date(2026, 8, 3)

    class Provider:
        def official_research_history(self, symbol, *, start_date, end_date):
            return [
                bar(d, 100)
                for d in (july, aug)
                if start_date <= d <= end_date and (symbol == "TAIEX" or d != july)
            ]

    monkeypatch.setattr(review, "_prices", lambda rows, days, key: {r["date"]: r for r in rows})
    monkeypatch.setattr(
        review.next_open_action_scope,
        "_events",
        lambda sym, start, end, p: (
            [{"family": "rights", "effective_date": str(july), "cash_terms": "1", "previous_close": 101}]
            if start.month == 7
            else []
        ),
    )

    def reference(symbol, event, first, last, provider, stock):
        assert str(aug) in stock
        event["resumed_quote_date"] = str(aug)

    monkeypatch.setattr(review, "_resumption_reference", reference)
    assert review.collect("1234.TW", july, aug, Provider())["events"][0]["resumed_quote_date"] == str(aug)
