"""Independent historical series anchored to the official close of publication."""

from datetime import date
from decimal import Decimal
from math import isclose

from ai_stock_sentinel.calibration import twse_rights_recovery as rights

from ai_stock_sentinel.calibration.forward_validation import OHLC_PRICE_FIELDS, evaluate_forward_window
from ai_stock_sentinel.daily_radar import (
    history_repair,
    next_open_action_scope,
    reviewed_action_sequence,
    trading_status,
)
from ai_stock_sentinel.daily_radar.next_open_official_repair import _prices
from ai_stock_sentinel.daily_radar.observation_validation import evaluate_observation
from ai_stock_sentinel.daily_radar.price_acceptance import quotes_match

VERSION = "reviewed-official-publication-close-v1"
UNVERIFIED = "original_quote_source_unverified"


def _event_terms(symbol, item, provider):
    day = item["effective_date"]
    if item["family"] == "reduction":
        return {"date": day, "kind": "reduction", **item["action"]}
    if item["family"] != "rights":
        return {"date": day, "kind": "unsupported"}
    values = dict(cash=item.get("cash_terms"), free="0", shares="0", price="0")
    if values["cash"] is None:
        if symbol.endswith(".TW"):
            detail = reviewed_action_sequence._terms(symbol, item, provider)
            values = {key: detail[key] for key in values}
        elif item.get("action", {}).get("type") == "stock_distribution":
            action = item["action"]
            values.update(
                cash=str(action["cash_per_share"]), free=str((Decimal(str(action["share_ratio"])) - 1) * 1000)
            )
        else:
            return {"date": day, "kind": "unsupported"}
    return {"date": day, "kind": "rights", "previous": item["previous_close"], **values}


def _resumption_reference(symbol, event, first, last, provider, stock):
    """The first traded bar must use the exchange's ex-dividend auction base."""
    if (
        not symbol.endswith(".TW")
        or event["kind"] != "rights"
        or Decimal(event["free"])
        or Decimal(event["shares"])
    ):
        return
    day = event["date"]
    following = next((d for d in sorted(stock) if d > day), None)
    if not following:
        return
    params = {"response": "json", "startDate": first.strftime("%Y%m%d"), "endDate": last.strftime("%Y%m%d")}
    payload, source = provider.capture_twse_report(rights.EX_RIGHTS, params)
    records = rights.dated_market_rows(
        payload, params, "資料日期", ["開盤競價基準"], path=rights.EX_RIGHTS, source=source
    )
    code = symbol.split(".")[0]
    bases = [r["開盤競價基準"] for d, r in records if str(d) == day and r["股票代號"].strip() == code]
    month = following[:7].replace("-", "") + "01"
    raw, _ = provider.capture_twse_report(
        "/exchangeReport/STOCK_DAY", {"response": "json", "date": month, "stockNo": code}
    )
    rows = rights._report_rows(raw, ["日期", "收盤價", "漲跌價差"])
    trade = date.fromisoformat(following)
    tag = f"{trade.year - 1911:03d}/{trade.month:02d}/{trade.day:02d}"
    matching = [r for r in rows if r["日期"] == tag]
    if len(bases) == 1 and len(matching) == 1:
        r = matching[0]
        change = str(r["漲跌價差"]).replace(",", "").strip()
        if change.startswith(("+", "-")) and (
            Decimal(str(r["收盤價"]).replace(",", "")) - Decimal(change)
            == Decimal(str(bases[0]).replace(",", ""))
        ):
            event["resumed_quote_date"] = following


def collect(symbol, start, end, provider):
    """Use monthly reports shared between windows. A failed month stays explicit."""
    from ai_stock_sentinel.daily_radar.trading_calendar import months
    from datetime import timedelta

    result = {"symbol": symbol, "stock": {}, "index": {}, "events": [], "errors": {}}
    pending_references = []
    for first in months(start, end):
        last = min(end, (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
        try:
            for identifier, key in ((symbol, "stock"), ("TAIEX", "index")):
                rows = provider.official_research_history(identifier, start_date=first, end_date=last)
                # Preserve missing days so each window can check its own sessions.
                days = [date.fromisoformat(row["date"]) for row in rows]
                result[key].update(_prices(rows, days, identifier))
            inventory = next_open_action_scope._events(symbol, first, last, provider)
            for item in inventory:
                event = _event_terms(symbol, item, provider)
                if event["date"] not in result["stock"]:
                    pending_references.append((event, first, last))
                result["events"].append(event)
        except (ValueError, OSError, RuntimeError) as exc:
            result["errors"][str(first)[:7]] = str(exc)
    for event, first, last in pending_references:
        try:
            _resumption_reference(symbol, event, first, last, provider, result["stock"])
        except (ValueError, OSError, RuntimeError) as exc:
            result["errors"][str(first)[:7]] = str(exc)
    return result


def _ratio(event, previous):
    if event["kind"] == "reduction":
        ref, cash, ratio = (
            Decimal(str(event[k])) for k in ("previous_close", "cash_per_share", "share_ratio")
        )
        if ref != Decimal(str(previous)) or not 0 < ratio < 1 or not 0 <= cash < ref:
            raise ValueError("publication_close_invalid_reduction")
        return (ref - cash) / (ratio * ref)
    if event["kind"] != "rights":
        raise ValueError("publication_close_unsupported_action")
    ref, cash, free, shares, price = (
        Decimal(str(event[k])) for k in ("previous", "cash", "free", "shares", "price")
    )
    if (
        not all(x.is_finite() and x >= 0 for x in (ref, cash, free, shares, price))
        or ref <= 0
        or ref != Decimal(str(previous))
        or bool(shares) != bool(price)
    ):
        raise ValueError("publication_close_invalid_action_terms")
    # Express post-event quotes in publication-date units using the theoretical
    # ex-rights reference. This is a price index, without investor cash flows.
    factor = (ref - cash + shares * price / 1000) / ((1 + (free + shares) / 1000) * ref)
    if factor <= 0:
        raise ValueError("publication_close_invalid_adjustment")
    return factor


def evaluate(candidate, full_snapshot, before, calendar, scope, *, earlier_date=None, unverified=False):
    """Compute a separate result, retaining original published thresholds and target."""
    signal = candidate["record_date"]
    target = before["target_date"]
    days = [str(s) for s in calendar.sessions if signal <= str(s) <= target]
    if (
        not days
        or days[0] != signal
        or days[-1] != target
        or len(days) != before["window_days"] + 1
        or target > str(calendar.through)
        or before["signal_date"] != signal
        or before["candidate_id"] != candidate["candidate_id"]
        or before["status"] != "validated"
        or before["validation_version"] != history_repair.FORWARD_VALIDATION_VERSION
        or before["outcome"].get("target_date") != target
        or scope.get("symbol", candidate["symbol"]) != candidate["symbol"]
    ):
        raise ValueError("publication_close_window_mismatch")
    if any(d[:7] in scope["errors"] for d in days):
        raise ValueError("publication_close_unverified_report")
    reported_days = set(scope["stock"]) | set(scope["index"])
    if {d for d in reported_days if signal <= d <= target} - set(days):
        raise ValueError("publication_close_official_calendar_conflict")
    original = full_snapshot["ohlcv"]
    stock = scope["stock"]
    suspended = [d for d in days if d not in stock]
    halts = scope.get("suspensions", {})
    if (
        signal in suspended
        or set(days) - scope["index"].keys()
        or any(
            not trading_status.matches(halts.get(d), symbol=candidate["symbol"], target=date.fromisoformat(d))
            for d in suspended
        )
    ):
        raise ValueError("publication_close_missing_session")
    anchor = stock[signal]
    prior = [str(d) for d in calendar.sessions if str(d) < signal][-2:]
    if unverified and any(quotes_match(original, stock.get(day, {})) for day in prior):
        raise ValueError("publication_close_original_source_identifiable")
    if earlier_date:
        if (
            unverified
            or earlier_date not in prior
            or earlier_date[:7] in scope["errors"]
            or not quotes_match(original, stock.get(earlier_date, {}))
            or quotes_match(original, anchor)
        ):
            raise ValueError("publication_close_prior_quote_mismatch")
    elif (
        (
            not unverified
            and not isclose(float(original["close"]), float(anchor["close"]), abs_tol=0.005, rel_tol=1e-7)
        )
        or unverified
        and quotes_match(original, anchor)
    ):
        raise ValueError("publication_close_original_quote_mismatch")
    official = {k: anchor[k] for k in OHLC_PRICE_FIELDS}
    status = (
        UNVERIFIED
        if unverified
        else "prior_session_verified"
        if earlier_date
        else ("publication_verified" if quotes_match(original, anchor) else "publication_close_only_verified")
    )
    common = {
        "version": VERSION,
        "symbol": candidate["symbol"],
        "signal_date": signal,
        "target_date": target,
        "original_entry_price": before["outcome"].get("entry_price"),
        "original_quote_date": earlier_date,
        "original_quote_status": status,
        "original_ohlc": {k: original.get(k) for k in OHLC_PRICE_FIELDS},
        "official_ohlc": official,
    }
    if suspended:
        common["suspended_sessions"] = suspended
    if target in suspended:
        return common | {
            "status": trading_status.REASON,
            "metrics": None,
            "diagnostic": {"status": trading_status.REASON, "missing_reason": None},
            trading_status.KEY: halts[target],
        }
    days = [d for d in days if d not in suspended]
    if len(days) < 2:
        raise ValueError("publication_close_no_trading_days")
    events = [e for e in scope["events"] if signal < e["date"] <= target]
    by_day = {}
    for event in events:
        day = event["date"]
        if day not in days:
            day = next((d for d in days if d > day), None)
            if day != event.get("resumed_quote_date") or day is None:
                raise ValueError("publication_close_action_session_mismatch")
        by_day[day] = event
    if len(by_day) != len(events) or set(by_day) - set(days):
        raise ValueError("publication_close_action_session_mismatch")
    multiplier = Decimal(1)
    rows = []
    for i, day in enumerate(days):
        if day in by_day:
            action = by_day[day]
            if action["kind"] == "reduction" and not (
                action["halt_start"] in suspended
                and all(
                    halts[d].get("resume_date") == day for d in suspended if action["halt_start"] <= d < day
                )
            ):
                raise ValueError("publication_close_reduction_halt_unverified")
            multiplier /= _ratio(by_day[day], stock[days[i - 1]]["close"])
        rows.append(
            {"date": day, **{k: float(Decimal(str(stock[day][k])) * multiplier) for k in OHLC_PRICE_FIELDS}}
        )
    keyed = candidate | {
        "input_snapshot": candidate["input_snapshot"] | {"ohlcv": official},
        "data_dates": (candidate.get("data_dates") or {}) | {"ohlcv": signal},
    }
    index = [scope["index"][d] for d in days]
    result = evaluate_forward_window(
        keyed,
        price_series=rows,
        benchmark_prices=index,
        adapter=history_repair.DAILY_RADAR_FORWARD_ADAPTER,
        window_days=len(days) - 1,
        as_of_date=calendar.through,
        benchmark_symbol="TAIEX",
        validation_version=VERSION,
        hit_threshold_pct=0,
        trading_calendar=[date.fromisoformat(d) for d in days],
    )
    diag = evaluate_observation(
        keyed,
        price_series=rows,
        benchmark_prices=index,
        window_days=len(days) - 1,
        as_of_date=calendar.through,
    )
    if (
        result["status"] != "validated"
        or result["target_date"] != target
        or diag["status"] == "insufficient_data"
    ):
        raise ValueError("publication_close_reference_or_metrics_incomplete")
    return common | {"metrics": result["outcome"], "diagnostic": diag, "corporate_actions": events}


def has_suspended_target(candidate, row, target):
    if isinstance(target, str):
        try:
            target = date.fromisoformat(target)
        except ValueError:
            return False
    marker = row["outcome"].get("historical_repair") or {}
    if not isinstance(marker, dict) or not isinstance(marker.get("publication_close_correction"), dict):
        return False
    correction = marker["publication_close_correction"]
    diagnostic = row["outcome"].get("observation_diagnostic") or {}
    return (
        marker.get("version") == VERSION
        and diagnostic.get("price_basis_version") == VERSION
        and diagnostic.get("status") == trading_status.REASON
        and correction.get("status") == trading_status.REASON
        and correction.get("metrics", False) is None
        and correction.get("version") == VERSION
        and correction.get("signal_date") == candidate["record_date"]
        and correction.get("target_date") == str(target)
        and correction.get("symbol") == candidate["symbol"]
        and trading_status.matches(
            correction.get(trading_status.KEY), symbol=candidate["symbol"], target=target
        )
    )


def performance_rows(candidates, outcomes):
    """Omit known suspended targets from performance samples while retaining the original records."""
    by_id = {c.get("candidate_id"): c for c in candidates}
    projected = []
    for row in outcomes:
        candidate = by_id.get(row.get("candidate_id"))
        diagnostic = (row.get("outcome") or {}).get("observation_diagnostic") or {}
        if (
            row.get("status") == "validated"
            and candidate
            and diagnostic.get("status") == trading_status.REASON
            and has_suspended_target(candidate, row, row.get("target_date"))
        ):
            row = dict(row) | {"status": "skipped", "skip_reason": trading_status.REASON}
        projected.append(row)
    return projected
