from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
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
    if data_date is None:
        # Legacy StockRawData rows did not persist the provider's final OHLCV
        # date. Conservatively treat their final price bar as unproven rather
        # than discarding the entire trailing history.
        data_date = as_of
    if data_date > as_of:
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
        # High/low series are independently dropna'd by the provider. Without
        # per-value dates, a mismatched length cannot prove that its final
        # value predates the event, so trim every non-empty price series.
        "highs": highs[:-1],
        "lows": lows[:-1],
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
    coverage_start: date | None = None,
    coverage_end: date | None = None,
) -> dict[str, Any]:
    bars = [_market_row_payload(row) for row in rows]
    bars_fingerprint = hashlib.sha256(_canonical_json(bars).encode("utf-8")).hexdigest()
    coverage = _market_bar_coverage(
        bars,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    return {
        "provider": provider,
        "fetched_at": _canonical_value(fetched_at) if fetched_at is not None else None,
        "quality": {
            "status": "available" if bars else "insufficient",
            "missing_reason": missing_reason,
            "row_count": len(bars),
            **coverage,
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
    volume_dates = (
        technical.get("recent_volume_dates")
        if isinstance(technical.get("recent_volume_dates"), list)
        and len(technical.get("recent_volume_dates")) == len(closes)
        else []
    )
    data_dates = technical.get("data_dates") if isinstance(technical.get("data_dates"), dict) else {}
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
        "data_date": _canonical_value(data_dates.get("ohlcv")),
        "trailing_dates": _canonical_value(volume_dates),
        "bar": _canonical_value({
            "open": ohlcv.get("open"),
            "high": ohlcv.get("high"),
            "low": ohlcv.get("low"),
            "close": ohlcv.get("close", technical.get("current_price")),
            "volume": ohlcv.get("volume"),
        }),
        "trailing_series": _canonical_value(series),
    }


def market_snapshot_regressed(
    existing_market: object,
    new_market: object,
    *,
    provider_upgrade_min_coverage_ratio: float,
) -> bool:
    """Return whether replacing ``existing_market`` would lose usable bars.

    ``row_count`` is retained for evidence compatibility but is not a coverage
    unit: one canonical row may embed many trailing bars while one provider row
    represents one trading day. New snapshots therefore compare normalized
    trading-bar coverage and date bounds.
    """
    if not isinstance(existing_market, dict) or not isinstance(new_market, dict):
        return False
    existing_quality = _snapshot_quality(existing_market)
    new_quality = _snapshot_quality(new_market)
    existing_missing = bool(existing_quality.get("missing_reason"))
    new_missing = bool(new_quality.get("missing_reason"))
    existing_count = existing_quality.get("trading_bar_count")
    new_count = new_quality.get("trading_bar_count")

    if not existing_missing and new_missing:
        return True

    upgrading_provider = existing_missing and not new_missing
    if isinstance(existing_count, int) and isinstance(new_count, int) and existing_count > new_count:
        if not upgrading_provider:
            return True
        existing_coverage_version = existing_quality.get("coverage_version")
        if existing_coverage_version == "market-coverage-v1":
            required_count = math.ceil(existing_count * provider_upgrade_min_coverage_ratio)
        else:
            # Old evidence only recorded outer rows. Require enough history for
            # MA60, but do not compare its incompatible legacy row_count 1:1.
            required_count = min(60, existing_count)
        if new_count < required_count:
            return True

    if _dated_coverage_shrunk(existing_quality, new_quality):
        return True
    return False


def _snapshot_quality(market: dict[str, Any]) -> dict[str, Any]:
    raw_quality = market.get("quality")
    quality = dict(raw_quality) if isinstance(raw_quality, dict) else {}
    if isinstance(quality.get("trading_bar_count"), int):
        return quality
    legacy_coverage = _market_bar_coverage(
        market.get("bars") if isinstance(market.get("bars"), list) else [],
        coverage_start=None,
        coverage_end=None,
    )
    quality.update(legacy_coverage)
    quality["coverage_version"] = "legacy-derived"
    if not market.get("bars") and isinstance(quality.get("row_count"), int):
        quality["trading_bar_count"] = quality["row_count"]
    return quality


def _dated_coverage_shrunk(existing_quality: dict[str, Any], new_quality: dict[str, Any]) -> bool:
    if existing_quality.get("coverage_basis") != "dated_bars":
        return False
    if new_quality.get("coverage_basis") != "dated_bars":
        return True
    existing_start = _parse_date(existing_quality.get("date_start"))
    existing_end = _parse_date(existing_quality.get("date_end"))
    new_start = _parse_date(new_quality.get("date_start"))
    new_end = _parse_date(new_quality.get("date_end"))
    if existing_start is not None and (new_start is None or new_start > existing_start):
        return True
    return existing_end is not None and (new_end is None or new_end < existing_end)


def _market_bar_coverage(
    bars: list[dict[str, Any]],
    *,
    coverage_start: date | None,
    coverage_end: date | None,
) -> dict[str, Any]:
    dated_bars: set[date] = set()
    undated_series_count = 0
    for payload in bars:
        series = payload.get("trailing_series") if isinstance(payload.get("trailing_series"), list) else []
        raw_series_dates = payload.get("trailing_dates")
        series_dates = (
            [_parse_date(value) for value in raw_series_dates]
            if isinstance(raw_series_dates, list)
            else []
        )
        valid_series_dates = [value for value in series_dates if _date_in_window(value, coverage_start, coverage_end)]
        if valid_series_dates:
            dated_bars.update(valid_series_dates)
        elif series:
            undated_series_count = max(undated_series_count, len(series))

        bar_date = _parse_date(payload.get("data_date")) or _parse_date(payload.get("record_date"))
        if _date_in_window(bar_date, coverage_start, coverage_end):
            dated_bars.add(bar_date)

    if coverage_start is not None and coverage_end is not None and undated_series_count:
        undated_series_count = min(undated_series_count, _weekday_count(coverage_start, coverage_end))
    trading_bar_count = max(len(dated_bars), undated_series_count)
    coverage_basis = "estimated_trailing_series" if undated_series_count > len(dated_bars) else "dated_bars"
    return {
        "coverage_version": "market-coverage-v1",
        "coverage_basis": coverage_basis,
        "trading_bar_count": trading_bar_count,
        "date_start": min(dated_bars).isoformat() if dated_bars else None,
        "date_end": max(dated_bars).isoformat() if dated_bars else None,
    }


def _date_in_window(value: date | None, start: date | None, end: date | None) -> bool:
    if value is None:
        return False
    return (start is None or value >= start) and (end is None or value < end)


def _weekday_count(start: date, end: date) -> int:
    current = start
    count = 0
    while current < end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


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
