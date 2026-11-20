"""Freeze the actual signal-day candle independently of subsequent price revisions."""
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite

from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

QUOTE_FIELDS = ('open', 'high', 'low', 'close', 'volume')
VERSION = 'radar-signal-quote-v1'


def capture_quote(*, symbol, trade_date, ohlcv, provider, basis, captured_at, history_receipt=None, adjustment=None):
    data = {
        'version': VERSION, 'symbol': symbol, 'trade_date': str(trade_date),
        'provider': provider, 'price_basis': basis, 'captured_at': captured_at,
        'ohlcv': {key: ohlcv[key] for key in QUOTE_FIELDS},
    }
    if adjustment is not None:
        data['adjustment'] = deepcopy(adjustment)
    if history_receipt is not None:
        data['history_receipt'] = deepcopy(history_receipt)
    return data | {'sha256': fingerprint(data)}


def checked_quote(quote, *, symbol, trade_date, ohlcv):
    if not isinstance(quote, dict) or not isinstance(ohlcv, Mapping):
        raise ValueError('signal_quote_identity_mismatch')
    data = {k: v for k, v in quote.items() if k != 'sha256'}
    candle = data.get('ohlcv') or {}
    try:
        captured = datetime.fromisoformat(data['captured_at'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('signal_quote_capture_time_invalid') from exc
    if (data.get('version') != VERSION or quote.get('sha256') != fingerprint(data)
            or data.get('symbol') != symbol or data.get('trade_date') != str(trade_date)
            or not data.get('provider') or not data.get('price_basis')
            or captured.tzinfo is None or not isinstance(candle, dict)
            or any(type(candle.get(k)) not in (int, float) or not isfinite(candle[k])
                   or candle[k] != ohlcv.get(k) for k in QUOTE_FIELDS)
            or not 0 < candle['low'] <= min(candle['open'], candle['close'])
            <= max(candle['open'], candle['close']) <= candle['high'] or candle['volume'] < 0):
        raise ValueError('signal_quote_identity_mismatch')
    return deepcopy(quote)


def archive_time(value):
    # SQLite reads timezone-aware database columns back without their UTC offset.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def valid_raw_quote(technical, symbol, record_date):
    quote = technical.get('signal_quote')
    if quote is None:
        return True
    try:
        checked_quote(quote, symbol=symbol, trade_date=record_date, ohlcv=technical.get('ohlcv') or {})
    except ValueError:
        return False
    return True
