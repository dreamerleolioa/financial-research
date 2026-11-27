"""Recover the next signal candle from a verified pre-rights technical history."""
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.daily_radar.disclosed_rights import dated_subscription_filing
from ai_stock_sentinel.daily_radar.market_bar_repository import DEFAULT_ADJUSTMENT_MODE, DEFAULT_MARKET_BAR_DATASET, get_taiwan_daily_bars
from ai_stock_sentinel.daily_radar.next_open_action_scope import _events
from ai_stock_sentinel.daily_radar.price_acceptance import verify_signal_bars
from ai_stock_sentinel.daily_radar.signal_quote import checked_quote
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore, fingerprint
from ai_stock_sentinel.daily_radar.technical_repair import PRICE_FIELDS, TechnicalRepairRejected, verified_rights_extension
from ai_stock_sentinel.daily_radar.trading_calendar import OfficialTradingCalendarProvider
from ai_stock_sentinel.db.models import RadarSourceSnapshot, StockRawData


def _official_pair(db, symbol, previous, run_date, *, lock=False, current=None):
    end = run_date if current is None else run_date - timedelta(days=1)
    bars = get_taiwan_daily_bars(db, symbols=[symbol], start_date=previous, end_date=end, lock_for_read=lock)
    if current is not None:
        if (current.symbol != symbol or current.trade_date != run_date or not current.is_final
                or current.dataset != DEFAULT_MARKET_BAR_DATASET or current.adjustment_mode != DEFAULT_ADJUSTMENT_MODE):
            raise TechnicalRepairRejected("official_bar_invalid")
        bars.append(current)
    if any(b.source_provider != "twse" or b.market != "TW" for b in bars):
        raise TechnicalRepairRejected("official_bar_invalid")
    return [{"date": str(b.trade_date), **{f.lower(): float(getattr(b, f.lower())) for f in PRICE_FIELDS},
             "volume": b.volume} for b in bars]


def _prior_row(symbol, run_date):
    return select(StockRawData).where(
        StockRawData.symbol == symbol, StockRawData.record_date < run_date,
        StockRawData.record_date >= run_date - timedelta(days=14), StockRawData.raw_data_is_final.is_(True),
    ).order_by(StockRawData.record_date.desc()).limit(1)


def verify_at_persistence(db, symbol, run_date, proof):
    """Fence the mutable inputs of a cached repair until the raw row is committed."""
    previous = date.fromisoformat(proof["previous_anchor"]["date"])
    # A range archive can hold yesterday while an inline upsert already holds today.
    # Contention aborts the repair without waiting for another writer's row.
    try:
        with db.begin_nested():
            bars = _official_pair(db, symbol, previous, run_date, lock=True)
            row = db.scalar(_prior_row(symbol, run_date).with_for_update(read=True, nowait=True)
                            .execution_options(populate_existing=True))
    except OperationalError as exc:
        if getattr(exc.orig, "pgcode", None) != "55P03":
            raise
        raise TechnicalRepairRejected("rights_extension_inputs_busy") from exc
    sources = ((row.technical or {}).get("source_provenance") or {}) if row else {}
    if (row is None or row.record_date != previous or sources.get("accepted_adjusted_history") != proof["previous_history"]
            or sources.get("official_signal_anchor") != proof["previous_anchor"]
            or bars != proof["official_bars"]):
        raise TechnicalRepairRejected("rights_extension_inputs_changed")


def _previous_history(db, store, symbol, run_date, start, current):
    row = db.scalar(_prior_row(symbol, run_date))
    if row is None:
        raise TechnicalRepairRejected("prior_accepted_history_missing")
    tech = row.technical or {}
    sources = tech.get("source_provenance") or {}
    anchor = sources.get("official_signal_anchor")
    receipt = sources.get("accepted_adjusted_history") or {}
    quote = checked_quote(tech.get("signal_quote"), symbol=symbol, trade_date=row.record_date, ohlcv=tech.get("ohlcv"))
    verified, _ = verify_signal_bars(db, [row], run_date=row.record_date)
    if (not anchor or anchor != verified.get(symbol) or not receipt.get("snapshot_id")
            or quote.get("history_receipt") != receipt):
        raise TechnicalRepairRejected("prior_official_anchor_mismatch")
    capture = db.get(RadarSourceSnapshot, receipt["snapshot_id"])
    if capture is None:
        raise TechnicalRepairRejected("prior_accepted_history_missing")
    payload, actual = store._read(capture)
    params = capture.request.get("params", {})
    if (actual != receipt or capture.request.get("operation") != "technical-adjusted-history"
            or params.get("stage") != "accepted_after_existing_integrity_checks"
            or params.get("symbol") != symbol or params.get("run_date") != str(row.record_date)
            or params.get("start_date", "9999") > str(start)
            or payload.get("price_basis") != "adjusted_price"):
        raise TechnicalRepairRejected("prior_history_identity_mismatch")
    frame = pd.DataFrame.from_records(payload["records"]).set_index("date")
    frame.index = pd.to_datetime(frame.index)
    frame = frame.loc[frame.index >= pd.Timestamp(start), [*PRICE_FIELDS, "Volume"]]
    if frame.empty or frame.index[-1].date() != row.record_date:
        raise TechnicalRepairRejected("prior_history_dates_invalid")
    if float(frame.iloc[-1]["Volume"]) != quote["ohlcv"]["volume"]:
        raise TechnicalRepairRejected("prior_official_anchor_mismatch")
    candles = _official_pair(db, symbol, row.record_date, run_date, current=current)
    return row.record_date, frame, candles, receipt, anchor


def recover(store, symbol, frame, *, run_date, start, current):
    with Session(store.bind) as reader:
        previous, prior, bars, receipt, anchor = _previous_history(reader, store, symbol, run_date, start, current)
    # A separate store enumerates exactly the reports used by this correction,
    # including cache hits. Forward their receipts to the durable acceptance fence.
    evidence = SourceSnapshotStore(store.bind, clock=store.clock, ttl=store.ttl)
    provider = RecoveringResearchPriceProvider(session=None, source_store=evidence)
    try:
        matched = dated_subscription_filing(symbol, run_date, provider.capture_rights_disclosures)
        if not matched:
            raise TechnicalRepairRejected("post_session_rights_unverified")
        terms, filing = matched
        calendar = OfficialTradingCalendarProvider(None, source_store=evidence).fetch(start_date=previous, end_date=run_date)
        events = _events(symbol, previous.replace(day=1), run_date, provider)
        changes = [e for e in events if e['effective_date'] > str(previous)]
        verified = verified_rights_extension(run_date=run_date, prior=prior, observed=frame, bars=bars,
            sessions=[str(d) for d in calendar.sessions], terms=terms, events=changes)
    finally:
        store.captured_count += evidence.captured_count
        store.dependencies.update(evidence.dependencies)
        store.seen.update(evidence.seen)
    proof = {'kind': 'verified_post_session_rights', 'previous_history': receipt,
             'previous_anchor': anchor, 'filing': filing, 'terms': terms, 'official_bars': bars,
             'observed_pair': [{k: float(row[k]) for k in PRICE_FIELDS} | {'date': str(i.date())}
                               for i, row in frame.iterrows() if i.date() in (previous, run_date)],
             'sources': list(evidence.seen.values())}
    return verified, {'provider': 'twse', 'history_provider': 'accepted_adjusted_history',
                      'price_basis': 'adjusted_price', 'repair': proof | {'sha256': fingerprint(proof)}}
