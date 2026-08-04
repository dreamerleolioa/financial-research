from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable


def completed_trailing_series(
    technical: Any,
    as_of: date,
    *,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
) -> dict[str, list[float]] | None:
    """Return only bars known to be complete before ``as_of``.

    A cache row's ``record_date`` is its observation date, not necessarily the
    date of the final OHLC bar.  The embedded data date is therefore the source
    of truth for deciding whether the trailing series needs its final bar
    removed.
    """
    if not isinstance(technical, dict):
        return None
    data_dates = technical.get("data_dates")
    data_date = _parse_date(data_dates.get("ohlcv")) if isinstance(data_dates, dict) else None
    if data_date is None or data_date > as_of:
        return None
    if data_date < as_of:
        return {"closes": closes, "highs": highs, "lows": lows, "volumes": volumes}

    close_count = len(closes)
    volume_dates = technical.get("recent_volume_dates")
    parsed_volume_dates = (
        [_parse_date(value) for value in volume_dates]
        if isinstance(volume_dates, list) and len(volume_dates) == len(volumes)
        else []
    )
    latest_volume_date = parsed_volume_dates[-1] if parsed_volume_dates else None
    if latest_volume_date is not None:
        volume_includes_as_of = latest_volume_date == as_of
    else:
        volume_includes_as_of = len(volumes) == close_count
    return {
        "closes": closes[:-1],
        "highs": highs[:-1] if len(highs) == close_count else highs,
        "lows": lows[:-1] if len(lows) == close_count else lows,
        "volumes": volumes[:-1] if volume_includes_as_of else volumes,
    }


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def attach_source_fingerprint(
    evidence_payload: dict[str, Any],
    *,
    ruleset_version: str,
) -> str:
    """Persist a stable digest of every input used by a deterministic review."""
    evidence_payload["ruleset_version"] = ruleset_version
    fingerprint_input = {
        key: value
        for key, value in evidence_payload.items()
        if key not in {"source_fingerprint", "generated_at"}
    }
    fingerprint = hashlib.sha256(_canonical_json(_fingerprint_value(fingerprint_input)).encode("utf-8")).hexdigest()
    evidence_payload["source_fingerprint"] = fingerprint
    return fingerprint


def market_snapshot_payload(
    rows: Iterable[Any],
    *,
    provider: str,
    fetched_at: datetime | None = None,
    missing_reason: str | None = None,
) -> dict[str, Any]:
    bars = [_market_row_payload(row) for row in rows]
    bars_fingerprint = hashlib.sha256(_canonical_json(bars).encode("utf-8")).hexdigest()
    return {
        "provider": provider,
        "fetched_at": _canonical_value(fetched_at) if fetched_at is not None else None,
        "quality": {
            "status": "available" if bars else "insufficient",
            "missing_reason": missing_reason,
            "row_count": len(bars),
        },
        "bars_fingerprint": bars_fingerprint,
        "bars": bars,
    }


def _market_row_payload(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        record_date = row.get("record_date", row.get("date"))
        technical = row.get("technical") or {}
    else:
        record_date = getattr(row, "record_date", None)
        technical = getattr(row, "technical", None) or {}
    ohlcv = technical.get("ohlcv") if isinstance(technical.get("ohlcv"), dict) else {}
    closes = technical.get("recent_closes") if isinstance(technical.get("recent_closes"), list) else []
    highs = technical.get("recent_highs") if isinstance(technical.get("recent_highs"), list) else []
    lows = technical.get("recent_lows") if isinstance(technical.get("recent_lows"), list) else []
    volumes = technical.get("recent_volumes") if isinstance(technical.get("recent_volumes"), list) else []
    series = [
        {
            "close": closes[index],
            "high": highs[index] if index < len(highs) else None,
            "low": lows[index] if index < len(lows) else None,
            "volume": volumes[index] if index < len(volumes) else None,
        }
        for index in range(len(closes))
    ]
    return {
        "record_date": _canonical_value(record_date),
        "bar": _canonical_value({
            "open": ohlcv.get("open"),
            "high": ohlcv.get("high"),
            "low": ohlcv.get("low"),
            "close": ohlcv.get("close", technical.get("current_price")),
            "volume": ohlcv.get("volume"),
        }),
        "trailing_series": _canonical_value(series),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(_canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, datetime):
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _fingerprint_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _fingerprint_value(item)
            for key, item in value.items()
            if key not in {"fetched_at", "generated_at"}
        }
    if isinstance(value, list):
        return [_fingerprint_value(item) for item in value]
    return value
