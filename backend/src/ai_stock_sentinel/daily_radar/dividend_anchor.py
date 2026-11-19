"""Verify a published signal's price units from its frozen technical inputs."""
from collections.abc import Mapping
from datetime import date
from math import isclose

from ai_stock_sentinel.calibration.forward_validation import number
from ai_stock_sentinel.daily_radar import price_acceptance as prices
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

LEVELS = ("support_level", "resistance_level", "ma20")


def _snapshot_inputs(snapshot):
    def pick(data, keys):
        return {k: data.get(k) for k in keys} if isinstance(data, Mapping) else {}
    if not isinstance(snapshot, Mapping):
        return None
    replay = snapshot.get("replay_input")
    record = replay.get("record") if isinstance(replay, Mapping) else None
    if not isinstance(record, Mapping) or not isinstance(record.get("technical_profile"), Mapping):
        return None
    history = record.get("price_history")
    if not isinstance(history, list) or len(history) < 21:
        return None
    return {"symbol": record.get("symbol"), "date": record.get("record_date"),
            "history": history[-21:], "quality": (record.get("technical_profile") or {}).get("data_quality"),
            "ohlcv": pick(snapshot.get("ohlcv"), prices.OHLC_PRICE_FIELDS),
            "record_ohlcv": pick(record.get("ohlcv"), prices.OHLC_PRICE_FIELDS),
            "levels": pick(snapshot.get("indicators"), LEVELS), "record_levels": pick(record.get("indicators"), LEVELS)}


def _close(actual, expected):
    observed = number(actual)
    return observed is not None and isclose(observed, expected, rel_tol=1e-7, abs_tol=.00005)


def prove_history(candidate, snapshot, stock, index, scale):
    data = _snapshot_inputs(snapshot)
    if not data or data["symbol"] != candidate["symbol"] or data["date"] != candidate["record_date"]:
        return None
    history = data["history"]
    days = [r.get("date") for r in history if isinstance(r, dict)]
    if (len(days) != 21 or not all(isinstance(d, str) for d in days) or days != sorted(set(days))
            or days[-1] != data["date"] or [r["date"] for r in stock] != days or [r["date"] for r in index] != days
            or any(r.get("price_source") != "twse_stock_day" or not prices._quote(r) for r in stock)
            or any(r.get("price_source") != "twse_index_history" or not prices._quote(r) for r in index)):
        return None
    quality = data["quality"]
    if not isinstance(quality, Mapping):
        return None
    levels = data["levels"]
    projected = candidate["input_snapshot"]
    if (quality.get("ohlcv_aligned") is not True or quality.get("price_level_completed_bars_only") is not True
            or quality.get("price_level_basis") != "ohlc_high_low" or quality.get("data_date") != days[-1]
            or quality.get("price_level_data_date") != days[-2] or data["record_ohlcv"] != data["ohlcv"]
            or data["record_levels"] != levels or data["ohlcv"].get("close") != projected["ohlcv"].get("close")
            or any(levels.get(k) != projected["indicators"].get(k) for k in LEVELS)):
        return None
    if (any(not _close(data["ohlcv"].get(k), stock[-1][k] * scale) for k in prices.OHLC_PRICE_FIELDS)
            or any(not _close(r.get("close"), raw["close"] * scale) for r, raw in zip(history, stock))
            or not _close(levels.get("support_level"), min(r["low"] for r in stock[:-1]) * scale)
            or not _close(levels.get("resistance_level"), max(r["high"] for r in stock[:-1]) * scale)
            or not _close(levels.get("ma20"), sum(r["close"] for r in stock[-20:]) / 20 * scale)):
        return None
    return {"kind": "verified_ex_dividend_anchor", "scale": scale, "reference_dates": days,
            "candidate_history_sha256": fingerprint(data), "official_history_sha256": fingerprint([
                [r["date"], *[r[k] for k in prices.OHLC_PRICE_FIELDS]] for r in stock]),
            "official_sources": sorted({r["official_price_report"]["source_url"] for r in [*stock, *index]
                                        if r.get("official_price_report")})}


def recover(candidate, days, vendor, official, provider, load_snapshot):
    dates = list(map(str, days))
    needed = set(dates[1:])
    if (not candidate["symbol"].endswith(".TW") or not needed.issubset(official.event_coverage or ())
            or set(dates).intersection(official.unavailable_dates | official.partial_stock_dates | official.unavailable_terms)
            or official.events is None or not prices._quotes_follow_calendar(official, dates)):
        return None
    events = [e for e in official.events if e["effective_date"] in needed]
    if len(events) != 1 or events[0]["effective_date"] != dates[1] or events[0].get("notice") not in {"息", "除息"}:
        return None
    cash = prices._cash_dividends(events, needed)
    raw = number(official.stock.get(dates[0], {}).get("close"))
    if (not raw or not cash or not 0 < cash[dates[1]] < raw or not _close(events[0].get("previous_close"), raw)
            or not _close(candidate["input_snapshot"]["ohlcv"].get("close"), raw - cash[dates[1]])):
        return None
    snapshot = candidate["input_snapshot"]
    if not _snapshot_inputs(snapshot) and load_snapshot is not None:
        snapshot = load_snapshot(candidate["candidate_id"])
    inputs = _snapshot_inputs(snapshot)
    if not inputs:
        return None
    point = inputs["history"][0]
    first_date = point.get("date") if isinstance(point, dict) else None
    start = date.fromisoformat(first_date) if isinstance(first_date, str) else None
    if start is None or not 20 <= (days[0] - start).days <= 60:
        return None
    def history(ticker):
        return provider.official_research_history(ticker, start_date=start, end_date=days[0])
    stock = history(candidate["symbol"])
    if not stock or not prices.quotes_match(stock[-1], official.stock[dates[0]]):
        return None
    proof = prove_history(candidate, snapshot, stock, history("TAIEX"), (raw - cash[dates[1]]) / raw)
    if not proof:
        return None
    proof.update({"ex_date": dates[1], "raw_close": raw, "cash_per_share": cash[dates[1]]})
    return prices._signal_close_prices(candidate, days, vendor, official, anchor_evidence=proof)


def check_published_inputs(session, candidate_id, row):
    price_proof = (row.get("outcome") or {}).get(prices.PRICE_EVIDENCE) or {}
    basis = price_proof.get("basis_evidence") or {}
    if basis.get("kind") == "verified_ex_dividend_anchor":
        from sqlalchemy import select
        from ai_stock_sentinel.db.models import DailyRadarCandidate
        snapshot = session.scalar(select(DailyRadarCandidate.input_snapshot).where(
            DailyRadarCandidate.id == candidate_id).with_for_update(read=True))
        if basis["candidate_history_sha256"] != fingerprint(_snapshot_inputs(snapshot)):
            from ai_stock_sentinel.daily_radar.validation_preparation import ValidationAcceptanceConflict
            raise ValidationAcceptanceConflict("signal_price_anchor_inputs_changed")
