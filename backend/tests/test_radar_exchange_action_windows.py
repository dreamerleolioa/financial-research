from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceEvidence, accept_signal_close_prices
from test_radar_jobs import db
from test_radar_price_acceptance import CANDIDATE, DAYS, SIGNAL, TARGET, bar, official


def test_signal_raw_cash_dividend_requires_the_complete_official_explanation():
    from dataclasses import replace

    stock = [bar(SIGNAL, 100), bar(TARGET, 98)]
    vendor = [row | {"price_source": "yfinance", "open_observed": True} for row in stock]
    dividend = {
        "family": "rights",
        "notice": "息",
        "effective_date": str(TARGET),
        "previous_close": 100,
        "cash_dividend": 2,
    }
    evidence = replace(official(stock, [dividend]), event_coverage=frozenset(map(str, DAYS)))
    result = accept_signal_close_prices(CANDIDATE, DAYS, vendor, evidence)
    assert result.stock and result.stock[-1]["close"] == 100
    assert accept_signal_close_prices(
        CANDIDATE, DAYS, [vendor[0] | {"open_observed": False}, vendor[1]], evidence
    ).reason
    assert accept_signal_close_prices(CANDIDATE, DAYS, [stock[0], vendor[1]], evidence).reason
    for bad in (dict(dividend, previous_close=99), dict(dividend, notice="權息")):
        assert accept_signal_close_prices(CANDIDATE, DAYS, vendor, official(stock, [bad])).reason
    assert accept_signal_close_prices(CANDIDATE, DAYS, vendor[:-1], evidence).reason
    assert accept_signal_close_prices(CANDIDATE, DAYS, vendor, replace(evidence, benchmark={})).reason
    changed = [vendor[0], vendor[1] | {"low": 97}]
    assert accept_signal_close_prices(CANDIDATE, DAYS, changed, evidence).reason


def reduction_window():
    from ai_stock_sentinel.daily_radar.price_acceptance import EventReport

    days = [str(SIGNAL), str(TARGET), "2026-06-05", "2026-06-08"]
    receipt = {
        "source_url": "https://www.twse.com.tw/exchangeReport/TWTAUU",
        "captured_at": "2026-06-08T12:00:00Z",
        "snapshot_sha256": "a" * 64,
    }

    def src(path):
        return receipt | {"source_url": "https://www.twse.com.tw" + path}

    stock = {
        d: bar(d, 100 if d == days[0] else 105)
        | {"official_price_report": src("/exchangeReport/STOCK_DAY?date=20260601&stockNo=5880")}
        for d in (days[0], days[-1])
    }
    bench = {
        d: bar(d, 1000) | {"official_price_report": src("/indicesReport/MI_5MINS_HIST?date=20260601")}
        for d in days
    }
    event = {
        "type": "cash_reduction",
        "symbol": CANDIDATE["symbol"],
        "effective_date": days[-1],
        "halt_start": days[1],
        "share_ratio": 0.94,
        "cash_per_share": 0.6,
        "previous_close": 100,
    }
    reports = tuple(
        EventReport(days[0], days[-1], src(path + "?startDate=20260603&endDate=20260608"))
        for path in ("/exchangeReport/TWTAUU", "/rwd/zh/exRight/TWT49U", "/rwd/zh/change/TWTB8U")
    )
    import json
    from pathlib import Path
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
    from dataclasses import replace

    notice = {
        "stat": "OK",
        "strDate": "20260603",
        "endDate": "20260608",
        "fields": ["股票代號", "恢復買賣日期", "減資原因", "詳細資料", "停止買賣前收盤價格"],
        "data": [["5880", "115/06/08", "退還股款", "5880,20260601", "100"]],
    }
    reports = (
        replace(
            reports[0], source=reports[0].source | {"snapshot_sha256": fingerprint(notice)}, payload=notice
        ),
        *reports[1:],
    )
    raw = json.loads((Path(__file__).parent / "fixtures/next_open_corporate_actions.json").read_text())[
        "twse-reduction-detail-example"
    ]
    raw["data"][0][:5] = ["5880", "測試", "115/06/04", "940 股", "0.6 元/股"]
    detail = src("/rwd/zh/reducation/TWTAVUDetail?response=json&STK_NO=5880&FILE_DATE=20260601") | {
        "snapshot_sha256": fingerprint(raw)
    }
    source = OfficialPriceEvidence(
        stock,
        bench,
        [{"family": "reduction", "effective_date": days[-1], "action": event}],
        (*reports, EventReport(days[-1], days[-1], detail, True, raw)),
        frozenset(days),
    )
    return days, source


def test_signal_cash_reduction_needs_exact_halt_and_official_evidence():
    from ai_stock_sentinel.daily_radar.signal_action_evidence import classify_action, matches

    days, source = reduction_window()
    reason, proof = classify_action(CANDIDATE, days, source)
    assert matches(proof, reason, CANDIDATE["symbol"], days)
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

    changed = deepcopy(proof)
    changed["action"]["share_ratio"] = 0.95
    changed["sha256"] = fingerprint({k: v for k, v in changed.items() if k != "sha256"})
    assert not matches(changed, reason, CANDIDATE["symbol"], days)
    changed = deepcopy(proof)
    url, notice = next(
        (url, payload) for url, payload in changed["action_reports"].items() if "TWTAUU?" in url
    )
    notice["data"].append([*notice["data"][0][:1], "115/06/05", *notice["data"][0][2:]])
    listing = next(r for r in changed["sources"] if r["source_url"] == url)
    listing["snapshot_sha256"] = fingerprint(notice)
    changed["sha256"] = fingerprint({k: v for k, v in changed.items() if k != "sha256"})
    assert not matches(changed, reason, CANDIDATE["symbol"], days)

    assert classify_action(CANDIDATE, [days[0], days[1], days[-1]], source) is None
    assert not matches(proof, reason, "2330.TW", days)
    assert not matches(proof, reason, CANDIDATE["symbol"], days[1:])
    source.stock[days[1]] = source.stock[days[0]] | {"date": days[1]}
    assert classify_action(CANDIDATE, days, source) is None


def rights_window(symbol="2890.TW"):
    from dataclasses import replace

    stock = [bar(SIGNAL, 100), bar(TARGET, 96)]
    receipt = {
        "source_url": "https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail?response=json&STK_NO=2890&T1=20260604",
        "captured_at": "2026-06-04T10:00:00Z",
        "snapshot_sha256": "a" * 64,
    }
    terms = {
        "date": str(TARGET),
        "symbol": symbol,
        "kind": "cash_rights_issue",
        "subscription_price": 50,
        "subscription_shares_per_thousand": 100,
        "source": receipt,
    }
    event = {"effective_date": str(TARGET), "family": "rights", "notice": "權", "previous_close": 100}
    basis = replace(
        official(stock, [event]), rights_terms={str(TARGET): terms}, event_coverage=frozenset(map(str, DAYS))
    )
    return dict(CANDIDATE, symbol=symbol), stock, basis


def test_signal_cash_issue_locks_the_official_reference_factor():
    from dataclasses import replace

    candidate, stock, evidence = rights_window()
    vendor = [r | {"open_observed": True} for r in stock]
    assert accept_signal_close_prices(candidate, DAYS, stock, evidence).reason
    assert accept_signal_close_prices(
        candidate, DAYS, [vendor[0], vendor[1] | {"open_observed": False}], evidence
    ).reason
    adjusted = accept_signal_close_prices(candidate, DAYS, vendor, evidence)
    assert adjusted.stock and adjusted.stock[-1]["close"] == pytest.approx(96 * 110 / 105)
    assert adjusted.proof["rights_terms"][0]["subscription_price"] == 50
    assert accept_signal_close_prices(candidate, DAYS, [stock[0], stock[1] | {"low": 94}], evidence).reason
    assert accept_signal_close_prices(candidate, DAYS, stock, replace(evidence, rights_terms={})).reason
    changed = [dict(evidence.events[0], previous_close=99)]
    assert accept_signal_close_prices(candidate, DAYS, stock, replace(evidence, events=changed)).reason


def test_otc_subscription_uses_raw_next_open_and_verifies_index_and_cash():
    from ai_stock_sentinel.daily_radar.rights_prices import accept_rights_prices

    candidate, stock, evidence = rights_window("8042.TWO")
    evidence.events[0]["notice"] = "除權"
    evidence.rights_terms[str(TARGET)]["source"]["source_url"] = (
        "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate=2026/06/03&endDate=2026/06/04"
    )
    vendor = [
        r
        | {
            "price_source": "yfinance",
            "stock_splits": 0,
            "dividends": 0,
            "corporate_actions_checked": True,
            "price_basis": "unadjusted_price",
        }
        for r in stock
    ]
    bench = [
        r | {"dividends": 0, "stock_splits": 0, "price_source": "yfinance"}
        for r in evidence.benchmark.values()
    ]

    def accept(stock_rows, index_rows):
        return accept_rights_prices(
            candidate, DAYS, stock_rows, evidence, basis="next_open", vendor_benchmark=index_rows
        )

    result = accept(vendor, bench)
    assert result and result.stock[-1]["open"] == stock[-1]["open"]
    assert result.stock[-1]["dividends"] == 0
    assert accept([vendor[0] | {"dividends": 5}, vendor[1]], bench)
    signal = {
        k: v
        for k, v in vendor[0].items()
        if k not in {"dividends", "stock_splits", "corporate_actions_checked"}
    }
    assert accept([signal, vendor[1]], bench)
    assert accept([vendor[0], vendor[1] | {"low": 96.001}], bench) is None
    assert accept(vendor, [bench[0], bench[1] | {"low": bench[1]["close"] + 0.001}]) is None
    assert accept(vendor, bench[:-1]) is None
    assert accept([vendor[0], vendor[1] | {"stock_splits": 2}], bench) is None
    assert accept(vendor, [bench[0], bench[1] | {"low": 9900}]) is None


def test_two_issues_verify_each_reference_and_the_rights_applicable_to_each_date():
    from dataclasses import replace
    from ai_stock_sentinel.daily_radar.rights_prices import accept_rights_prices

    candidate, _, source = rights_window()
    days = [SIGNAL, TARGET, date(2026, 6, 5)]
    stock = [bar(d, v) for d, v in zip(days, [100, 105, 110], strict=True)]
    benchmark = [bar(d, 1000) for d in days]
    first = source.events[0]
    second = dict(first, effective_date=str(days[-1]), previous_close=105)
    terms = source.rights_terms[str(TARGET)]
    source = replace(
        source,
        events=[second, first],
        stock={r["date"]: r for r in stock},
        benchmark={r["date"]: r for r in benchmark},
        rights_terms={
            str(TARGET): terms,
            str(days[-1]): dict(
                terms,
                date=str(days[-1]),
                subscription_price=90,
                source=terms["source"]
                | {"source_url": terms["source"]["source_url"].replace("20260604", "20260605")},
            ),
        },
        event_coverage=frozenset(map(str, days)),
    )
    factor2 = (105 + 9) / 1.1 / 105
    vendor = [
        r
        | {k: r[k] * (factor2 if i == 1 else 1) for k in ("open", "high", "low", "close")}
        | {
            "corporate_actions_checked": True,
            "price_basis": "unadjusted_price",
            "dividends": 0,
            "stock_splits": 0,
        }
        for i, r in enumerate(stock)
    ]
    bm = [r | {"dividends": 0, "stock_splits": 0} for r in benchmark]

    def accept(rows, official=source):
        return accept_rights_prices(candidate, days, rows, official, basis="next_open", vendor_benchmark=bm)

    passed = accept(vendor)
    assert passed and len(passed.proof["rights_terms"]) == 2
    assert passed.stock == [
        r
        | {
            "price_basis": "unadjusted_price",
            "corporate_actions_checked": True,
            "corporate_action_source": "official_reports",
            "dividends": 0,
            "stock_splits": 0,
        }
        for r in stock
    ]
    assert accept(vendor, replace(source, events=[first, first, second])) is None
    assert accept(vendor, replace(source, events=[first, dict(second, previous_close=100)])) is None
    assert accept([*vendor[:-1], vendor[-1] | {"low": 109}]) is None


def test_signal_action_round_trip_and_conflicting_validated_return(db):
    from ai_stock_sentinel.daily_radar.validation_execution import _collect_locked_signal_prices
    from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
    from ai_stock_sentinel.daily_radar.readiness import classify_window
    from ai_stock_sentinel.daily_radar.signal_action_evidence import KEY
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    from test_daily_radar_validation_read import add_run, add_candidate

    dates, evidence = reduction_window()
    days = list(map(date.fromisoformat, dates))
    calendar = SimpleNamespace(sessions=days, through=days[-1])
    stored = add_candidate(db, add_run(db, SIGNAL), CANDIDATE["symbol"])
    candidate = CANDIDATE | {"candidate_id": stored.id}
    (row,) = _collect_locked_signal_prices(
        [candidate],
        None,
        calendar=calendar,
        request=SimpleNamespace(windows=[3], benchmark_symbol="TAIEX"),
        collect_official=lambda *args: evidence,
        fetch_prices=lambda *args, **kwargs: {},
    )
    assert row["skip_reason"] == "cash_reduction_halt_in_window"
    active_day = dates[0]
    quote = evidence.stock[active_day]
    (conflicting,) = _collect_locked_signal_prices(
        [candidate],
        None,
        calendar=calendar,
        request=SimpleNamespace(windows=[3], benchmark_symbol="TAIEX"),
        collect_official=lambda *args: evidence,
        fetch_prices=lambda *args, **kwargs: {candidate["symbol"]: [quote, quote | {"close": 99}]},
    )
    assert conflicting["skip_reason"] == "conflicting_price_records"
    result = upsert_forward_validation_results(db, [row], calendar=calendar)
    assert result["terminal_skipped_count"] == 1
    saved = db.scalar(select(DailyRadarForwardValidationResult))
    assert (
        classify_window(
            candidate,
            dict(row, outcome=saved.outcome),
            basis="signal_close",
            window=3,
            as_of_date=days[-1],
            calendar=calendar,
        )[0]
        == "terminal"
    )
    assert upsert_forward_validation_results(db, [row], calendar=calendar)["records_written"] == 0
    bad = deepcopy(row)
    bad["outcome"][KEY]["action"]["share_ratio"] = 0.95
    with pytest.raises(ValueError, match="invalid_signal_action_evidence"):
        upsert_forward_validation_results(db, [bad], calendar=calendar)
    saved.status, saved.skip_reason, saved.outcome = (
        "skipped",
        "conflicting_price_records",
        {"conflict_fields": ["open"]},
    )
    db.flush()
    conflict = upsert_forward_validation_results(db, [row], calendar=calendar)
    assert conflict["preserved_conflicts"][0]["reason"] == "conflicting_price_records"
    assert saved.outcome == {"conflict_fields": ["open"]}
    # A pre-existing validated row owns its value, and the disagreement reaches the worker.
    saved.status, saved.skip_reason, saved.outcome = "validated", None, {"forward_return_pct": 3}
    db.flush()
    # Another writer committed after collection had recorded an absent result.
    expected = {(stored.id, row["window_days"], row["validation_version"]): None}
    conflict = upsert_forward_validation_results(db, [row], calendar=calendar, expected_revisions=expected)
    assert conflict["preserved_conflicts"][0]["reason"] == "validated_return_conflicts_with_corporate_action"
    assert saved.outcome == {"forward_return_pct": 3}


def test_unavailable_rights_detail_waits_for_the_provider():
    from dataclasses import replace
    from ai_stock_sentinel.daily_radar.price_acceptance import refused_prices

    _, _, evidence = rights_window()
    unavailable = replace(evidence, unavailable_terms=frozenset({str(TARGET)}))
    row = {"target_date": str(TARGET)}
    assert (
        refused_prices(
            row,
            "official_corporate_action_in_window",
            unavailable,
            sessions=DAYS,
            event_requires_prices=False,
        )["skip_reason"]
        == "validation_price_source_unavailable"
    )
    assert (
        refused_prices(
            row,
            "official_corporate_action_in_window",
            unavailable,
            sessions=[SIGNAL],
            event_requires_prices=False,
        )["skip_reason"]
        == "official_corporate_action_in_window"
    )


def test_signal_reduction_rejects_partial_trading_inside_official_halt(monkeypatch):
    from ai_stock_sentinel.daily_radar import next_open_action_scope
    from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceCollector
    from ai_stock_sentinel.daily_radar.signal_action_evidence import classify_action

    days, evidence = reduction_window()
    partial_day = date.fromisoformat(days[2])

    def history(symbol, *, start_date, end_date):
        return list((evidence.benchmark if symbol == "TAIEX" else evidence.stock).values())

    def events(symbol, start, end, collector, **kwargs):
        collector.sources.extend(evidence.report_sources)
        return evidence.events

    monkeypatch.setattr(next_open_action_scope, "_events", events)
    provider = SimpleNamespace(
        official_research_history=history, has_partial_official_price=lambda symbol, day: day == partial_day
    )
    collected = OfficialPriceCollector(provider).collect(
        CANDIDATE["symbol"], SIGNAL, date.fromisoformat(days[-1])
    )
    assert classify_action(CANDIDATE, days, collected) is None


def test_malformed_rights_detail_is_an_isolated_data_error():
    from ai_stock_sentinel.daily_radar.rights_terms import read_terms

    with pytest.raises(ValueError, match="invalid_report"):
        read_terms("2890.TW", TARGET, {}, {}, lambda *args: ([], {}))


def test_cash_issue_vendor_outage_remains_retryable():
    from ai_stock_sentinel.daily_radar.validation_execution import _collect_locked_signal_prices
    from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy

    candidate, stock, evidence = rights_window()

    def busy(*args, **kwargs):
        raise SourceDependencyBusy(["yahoo"], None)

    def evaluate(fetch):
        (row,) = _collect_locked_signal_prices(
            [candidate],
            None,
            calendar=SimpleNamespace(sessions=DAYS, through=TARGET),
            request=SimpleNamespace(windows=[1], benchmark_symbol="TAIEX"),
            collect_official=lambda *args: evidence,
            fetch_prices=fetch,
        )
        return row

    assert evaluate(lambda *args, **kwargs: {})["skip_reason"] == "validation_price_source_unavailable"
    later = date(2026, 6, 8)
    another = dict(candidate, candidate_id=2, record_date="2026-06-05")
    mixed = _collect_locked_signal_prices(
        [candidate, another],
        None,
        calendar=SimpleNamespace(sessions=[*DAYS, date(2026, 6, 5), later], through=later),
        request=SimpleNamespace(windows=[1], benchmark_symbol="TAIEX"),
        collect_official=lambda *args: evidence,
        fetch_prices=lambda *args, **kwargs: {candidate["symbol"]: [bar(later, 100)]},
    )
    assert mixed[0]["skip_reason"] == "validation_price_source_unavailable"
    assert evaluate(busy)["skip_reason"] == "validation_price_source_unavailable"
    assert (
        evaluate(lambda *args, **kwargs: {candidate["symbol"]: [r | {"open_observed": True} for r in stock]})[
            "status"
        ]
        == "validated"
    )


def test_a_missing_price_report_for_a_whole_halted_month_cannot_prove_no_trades():
    from ai_stock_sentinel.daily_radar.signal_action_evidence import _covers_sources
    from ai_stock_sentinel.calibration.official_corporate_actions import CASH_REDUCTION_HALT
    from ai_stock_sentinel.calibration import twse_rights_recovery as twse

    days = ["2026-05-29", "2026-06-01", "2026-06-30", "2026-07-01"]

    def receipt(path):
        return {
            "source_url": "https://www.twse.com.tw" + path,
            "snapshot_sha256": "a" * 64,
            "captured_at": "2026-07-01T10:00:00Z",
        }

    stock = lambda month: receipt(f"/exchangeReport/STOCK_DAY?stockNo=5880&date=2026{month}01")
    receipts = [
        receipt(f"{path}?startDate=20260529&endDate=20260701")
        for path in (twse.EX_RIGHTS, twse.REDUCTION, twse.FACE_VALUE)
    ]
    receipts += [receipt(f"/indicesReport/MI_5MINS_HIST?date=2026{m}01") for m in ("05", "06", "07")]
    receipts += [
        stock("05"),
        stock("07"),
        receipt("/rwd/zh/reducation/TWTAVUDetail?STK_NO=5880&FILE_DATE=20260528"),
    ]
    args = (CASH_REDUCTION_HALT, "5880.TW", days[-1], days[1], days)
    assert not _covers_sources(receipts, *args)
    assert _covers_sources(receipts + [stock("06")], *args)


def test_price_recovery_rejects_a_calendar_missing_an_official_session():
    from dataclasses import replace

    candidate, stock, evidence = rights_window()
    days = [SIGNAL, date(2026, 6, 5)]
    official_target = str(days[-1])
    evidence = replace(
        evidence,
        stock={r["date"]: r for r in [*stock, bar(days[-1], 96)]},
        benchmark={r["date"]: r for r in [*evidence.benchmark.values(), bar(days[-1], 10000)]},
        events=[evidence.events[0] | {"effective_date": official_target}],
        event_coverage=frozenset(map(str, [*DAYS, days[-1]])),
    )
    source = evidence.rights_terms[str(TARGET)]
    evidence = replace(
        evidence,
        rights_terms={
            official_target: source
            | {
                "date": official_target,
                "source": source["source"]
                | {"source_url": source["source"]["source_url"].replace("20260604", "20260605")},
            }
        },
    )
    vendor = [r | {"open_observed": True} for r in evidence.stock.values()]
    assert accept_signal_close_prices(candidate, days, vendor, evidence).reason
    dividend = {
        "family": "rights",
        "effective_date": official_target,
        "notice": "息",
        "cash_dividend": 2,
        "previous_close": 100,
    }
    assert accept_signal_close_prices(candidate, days, vendor, replace(evidence, events=[dividend])).reason
