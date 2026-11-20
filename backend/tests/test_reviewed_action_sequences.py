import json
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from ai_stock_sentinel.calibration import twse_rights_recovery as rights
from ai_stock_sentinel.daily_radar import next_open_official_repair as repair
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint


@pytest.fixture
def sequence():
    fixtures = json.loads((Path(__file__).parent / "fixtures/reviewed_action_sequences.json").read_text())

    def case(symbol, fault=None, start=0):
        data = fixtures[symbol]
        bars, official, index = [], {}, {}
        for day, *values in data["prices"]:
            official[day] = dict(zip(rights.OHLC, values[:4])) | {
                "date": day,
                "price_source": "twse_stock_day",
            }
            index[day] = dict.fromkeys(rights.OHLC, 100.0) | {
                "date": day,
                "price_source": "twse_index_history",
            }
            bars.append(
                dict(zip((*rights.OHLC, "dividends", "stock_splits"), values[4:]))
                | {
                    "date": day,
                    "price_source": "yfinance",
                    "price_basis": "unadjusted_price",
                    "volume": 100,
                    "corporate_actions_checked": True,
                }
            )
        dates = tuple(date.fromisoformat(r["date"]) for r in bars)

        def receipt(path, params, raw):
            return {
                "source_url": rights.TWSE + path + "?" + urlencode(params),
                "snapshot_sha256": fingerprint(raw),
                "captured_at": "2026-11-20T06:00:00+00:00",
            }

        def stock_history(ticker, *, start_date, end_date):
            from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider

            reader = RecoveringResearchPriceProvider()
            reader.capture_twse_report = capture
            return reader.official_research_history(ticker, start_date=start_date, end_date=end_date)

        def capture(path, params):
            if path in ("/indicesReport/MI_5MINS_HIST", "/exchangeReport/STOCK_DAY"):
                is_index = path.startswith("/indices")
                dataset = index if is_index else official
                fields = (
                    ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"]
                    if is_index
                    else ["日期", "開盤價", "最高價", "最低價", "收盤價", "成交股數", "成交金額"]
                )
                rows = []
                for day, bar in dataset.items():
                    d = date.fromisoformat(day)
                    if d.strftime("%Y%m") == params["date"][:6]:
                        rows.append(
                            [
                                f"{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}",
                                *[bar[k] for k in rights.OHLC],
                                *([] if is_index else [100, 1000]),
                            ]
                        )
                raw = dict(
                    stat="OK",
                    date=params["date"],
                    title=f" {symbol.split('.')[0]} ",
                    fields=fields,
                    data=rows,
                )
                return raw, receipt(path, params, raw)
            events = data["events"]
            if path == rights.RIGHTS_DETAIL:
                d = params["T1"]
                (i,) = [i for i, e in enumerate(events) if e["effective_date"].replace("-", "") == d]
                terms = data["terms"][i].copy()
                if fault == "identity":
                    terms["股票代號"] = "1234"
                raw = dict(stat="OK", fields=list(terms), data=[list(terms.values())])
            elif path == rights.EX_RIGHTS:
                rows = []
                for e in events:
                    d = date.fromisoformat(e["effective_date"])
                    if params["startDate"] <= d.strftime("%Y%m%d") <= params["endDate"]:
                        rows.append(
                            [
                                f"{d.year - 1911}/{d:%m/%d}",
                                symbol.split(".")[0],
                                e["previous_close"],
                                e["notice"],
                                symbol.split(".")[0] + "," + d.strftime("%Y%m%d"),
                            ]
                        )
                if fault == "duplicate":
                    rows += rows[:1]
                raw = dict(
                    stat="OK",
                    strDate=params["startDate"],
                    endDate=params["endDate"],
                    fields=["資料日期", "股票代號", "除權息前收盤價", "權/息", "詳細資料"],
                    data=rows,
                )
            else:
                raw = dict(
                    stat="OK",
                    strDate=params["startDate"],
                    endDate=params["endDate"],
                    fields=["恢復買賣日期", "股票代號", "停止買賣前收盤價格", "減資原因", "詳細資料"],
                    data=[],
                )
            return raw, receipt(path, params, raw)

        def chart(ticker, d):
            zone = ZoneInfo("Asia/Taipei")
            start = int(datetime.combine(d, time(), zone).timestamp())
            stamp = start + 9 * 3600
            r = next(i for i in bars if i["date"] == str(d))
            rec = dict(
                timestamp=[stamp],
                meta={"symbol": ticker, "exchangeTimezoneName": zone.key},
                indicators={"quote": [{k: [r[k]] for k in (*rights.OHLC, "volume")}]},
            )
            events = data["chart_events"].get(str(d))
            if events:
                rec["events"] = events
            raw = {"chart": {"result": [rec], "error": None}}
            return raw, {
                "source_url": f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?"
                + urlencode(dict(period1=start, period2=start + 86400, interval="1d", events="div,splits")),
                "snapshot_sha256": fingerprint(raw) if fault != "hash" else "d" * 64,
                "captured_at": "2026-11-20T06:00:00+00:00",
            }

        provider = SimpleNamespace(
            official_research_history=stock_history, capture_twse_report=capture, capture_split_day=chart
        )
        bench = [
            r
            | dict(
                price_source="yfinance",
                price_basis="unadjusted_price",
                corporate_actions_checked=True,
                dividends=0,
                stock_splits=0,
            )
            for r in index.values()
        ]
        if fault == "price":
            bars[-1]["low"] -= 0.02
        if fault == "benchmark":
            bench[-1]["open"] -= 0.02
        if fault == "spurious_cash":
            bars[-1]["dividends"] = 1
        if fault == "wrong_cash":
            next(r for r in bars if r["dividends"])["dividends"] += 0.00001
        if fault == "gap":
            bars.pop(1)
        target = dates[-1]
        req = {
            "signal_date": str(dates[start]),
            "symbol": symbol,
            "window_days": len(dates) - 1 - start,
            "outcome": {"expected_target_date": str(target)},
            "status": "skipped",
        }
        cal = SimpleNamespace(sessions=dates, through=target)
        return req, bars, bars, bench, cal, provider

    return case


@pytest.mark.parametrize("symbol", ["2890.TW", "7610.TW", "2885.TW"])
def test_complete_event_sequence_finishes_without_a_return(sequence, symbol):
    row, bars, marked, bench, cal, p = sequence(symbol)
    result = repair.reviewed_action_sequence.classify(row, bars, marked, bench, calendar=cal, provider=p)
    assert (
        result["skip_reason"] == "stock_distribution_in_window"
        and result["target_date"] == row["outcome"]["expected_target_date"]
    )
    assert "forward_return_pct" not in result["outcome"]
    proof = result["outcome"]["official_corporate_action"]["reviewed_action_sequence"]
    assert proof["events"] and len(proof["events"]) == (1 if symbol == "7610.TW" else 2)


@pytest.mark.parametrize(
    "fault", ["price", "benchmark", "spurious_cash", "wrong_cash", "hash", "identity", "duplicate", "gap"]
)
def test_unexplained_event_or_bar_keeps_the_window_open(sequence, fault):
    row, bars, marked, bench, cal, p = sequence("2890.TW", fault)
    with pytest.raises(ValueError):
        repair.reviewed_action_sequence.classify(row, bars, marked, bench, calendar=cal, provider=p)


def test_split_on_first_executable_day_keeps_the_signal_as_reference(sequence):
    row, bars, marked, bench, cal, p = sequence("2890.TW", start=2)
    result = repair.reviewed_action_sequence.classify(row, bars, marked, bench, calendar=cal, provider=p)
    proof = result["outcome"]["official_corporate_action"]
    assert proof["yahoo_adjusted_dates"] == []
    assert proof["reviewed_action_sequence"]["signal_reference"]["date"] == row["signal_date"]
    assert proof["event"]["effective_date"] == proof["session_dates"][0]


def test_unadjusted_target_cannot_have_an_archived_conflict(sequence):
    row, bars, _, bench, cal, provider = sequence("7610.TW")
    marked = [dict(r) for r in bars]
    marked[-1]["ohlc_conflict_fields"] = ["high"]
    with pytest.raises(ValueError):
        repair.reviewed_action_sequence.classify(row, bars, marked, bench, calendar=cal, provider=provider)


def test_exact_integer_mixed_ratio_is_a_valid_single_point():
    sequence = repair.reviewed_action_sequence
    import numpy as np

    stock = {"2026-07-01": dict.fromkeys(rights.OHLC, 120.0)}
    vendor = {"2026-07-01": dict.fromkeys(rights.OHLC, float(np.float32(100.0)))}
    interval = sequence._mixed_ratio(
        {"denominator": "1000", "numerator": "1200", "free": "200", "effective_date": "2026-07-02"},
        list(stock),
        stock,
        vendor,
    )
    assert float(interval["lower"]) == float(interval["upper"]) == 1.2


def test_monthly_payload_must_back_the_normalized_bars(sequence):
    row, bars, marked, bench, cal, provider = sequence("2885.TW")
    read = provider.official_research_history

    def inconsistent(ticker, **kwargs):
        rows = read(ticker, **kwargs)
        if ticker == row["symbol"]:
            rows[-1] = rows[-1] | {"high": rows[-1]["high"] + 0.01}
        return rows

    provider.official_research_history = inconsistent
    with pytest.raises(ValueError, match="official_history_changed"):
        repair.reviewed_action_sequence.classify(row, bars, marked, bench, calendar=cal, provider=provider)


def test_cash_detail_must_agree_with_inventory(sequence):
    _, _, _, _, _, provider = sequence("2885.TW")
    event = {"effective_date": "2026-07-21", "notice": "息", "cash_terms": "2.1"}
    with pytest.raises(ValueError, match="conflicting_cash_detail"):
        repair.reviewed_action_sequence._terms("2885.TW", event, provider)


@pytest.mark.parametrize("inventory_outage", [False, True])
def test_first_session_distribution_uses_sequence_through_collection_and_replay(sequence, inventory_outage):
    from ai_stock_sentinel.daily_radar.next_open_history_repair import _Tape

    row, bars, _, bench, cal, provider = sequence("7610.TW", start=2)
    read = provider.official_research_history

    def history(ticker, *, start_date, end_date, max_rows=None):
        return read(ticker, start_date=start_date, end_date=end_date)[:max_rows]

    provider.official_research_history = history
    provider.fetch = lambda *args, **kwargs: {row["symbol"]: bars, "TAIEX": bench}
    tape = _Tape(provider)
    tape.fetch([row["symbol"], "TAIEX"])
    c = dict(
        candidate_id=11,
        symbol=row["symbol"],
        record_date=row["signal_date"],
        data_dates={"ohlcv": row["signal_date"]},
    )
    before = row | {"skip_reason": "stock_split_in_window", "outcome": {}}
    if inventory_outage:
        original = provider.capture_twse_report

        def unavailable(path, params):
            if path == rights.EX_RIGHTS:
                raise OSError("offline")
            return original(path, params)

        provider.capture_twse_report = unavailable
        with pytest.raises(ValueError, match="inventory_unavailable"):
            repair.collect_window(c, before, before, calendar=cal, provider=tape)
        return
    result = repair.collect_window(c, before, before, calendar=cal, provider=tape)
    assert result["skip_reason"] == "stock_distribution_in_window"
    repair.verify_evidence(c, result, calendar=cal, prior={})


def test_active_scope_cannot_drop_one_of_two_proved_rights_events(sequence):
    from ai_stock_sentinel.daily_radar import next_open_action_scope as scope

    row, bars, marked, bench, cal, provider = sequence("2885.TW")
    result = repair.reviewed_action_sequence.classify(
        row, bars, marked, bench, calendar=cal, provider=provider
    )
    proof = result["outcome"]["official_corporate_action"]["reviewed_action_sequence"]
    result["outcome"][repair.KEY] = {
        "event_replay": {"sequence_verification": proof["version"], "benchmark": bench}
    }
    all_events = scope._events(row["symbol"], cal.sessions[0], cal.sessions[-1], provider)
    active = [e for e in all_events if e["effective_date"] > row["signal_date"]]
    assert len(active) == 2
    frozen = {
        "symbol": row["symbol"],
        "signal_date": row["signal_date"],
        "session_dates": [str(d) for d in cal.sessions[1:]],
        "current_events": active,
    }
    scope.result_matches(frozen, result)
    with pytest.raises(ValueError, match="scope_event_unresolved"):
        scope.result_matches(frozen | {"current_events": [e for e in active if e["notice"] != "息"]}, result)


def test_first_session_positive_inventory_requires_a_vendor_chart(sequence):
    row, bars, marked, index, calendar, provider = sequence("7610.TW", start=2)
    del provider.capture_split_day
    with pytest.raises(ValueError, match="chart_unavailable"):
        repair.reviewed_action_sequence.classify(
            row, bars, marked, index, calendar=calendar, provider=provider, require_inventory=True
        )
