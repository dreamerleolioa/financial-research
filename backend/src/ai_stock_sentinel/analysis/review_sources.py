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
    aligned_ohlc = _completed_aligned_ohlc(
        technical,
        as_of=as_of,
        closes=closes,
        highs=highs,
        lows=lows,
    )
    if aligned_ohlc is not None:
        aligned_ohlc["closes"] = _completed_independent_series(
            closes,
            technical.get("recent_close_dates"),
            as_of=as_of,
            close_count=len(closes),
            close_includes_as_of=data_date == as_of,
            shorter_series_is_complete=True,
        )
        aligned_ohlc["volumes"] = _completed_independent_series(
            volumes,
            technical.get("recent_volume_dates"),
            as_of=as_of,
            close_count=len(closes),
            close_includes_as_of=data_date == as_of,
            shorter_series_is_complete=True,
        )
        return aligned_ohlc
    if data_date < as_of:
        return {
            "closes": closes,
            "ohlc_closes": [],
            "highs": _completed_independent_series(
                highs,
                technical.get("recent_high_dates"),
                as_of=as_of,
                close_count=len(closes),
                close_includes_as_of=False,
            ),
            "lows": _completed_independent_series(
                lows,
                technical.get("recent_low_dates"),
                as_of=as_of,
                close_count=len(closes),
                close_includes_as_of=False,
            ),
            "volumes": _completed_independent_series(
                volumes,
                technical.get("recent_volume_dates"),
                as_of=as_of,
                close_count=len(closes),
                close_includes_as_of=False,
                shorter_series_is_complete=True,
            ),
        }

    close_count = len(closes)
    return {
        "closes": closes[:-1],
        "ohlc_closes": [],
        "highs": _completed_independent_series(
            highs,
            technical.get("recent_high_dates"),
            as_of=as_of,
            close_count=close_count,
            close_includes_as_of=True,
        ),
        "lows": _completed_independent_series(
            lows,
            technical.get("recent_low_dates"),
            as_of=as_of,
            close_count=close_count,
            close_includes_as_of=True,
        ),
        "volumes": _completed_independent_series(
            volumes,
            technical.get("recent_volume_dates"),
            as_of=as_of,
            close_count=close_count,
            close_includes_as_of=True,
            shorter_series_is_complete=True,
        ),
    }


def _completed_aligned_ohlc(
    technical: dict[str, Any],
    *,
    as_of: date,
    closes: list[float],
    highs: list[float],
    lows: list[float],
) -> dict[str, list[float]] | None:
    close_series = _dated_series(closes, technical.get("recent_close_dates"))
    high_series = _dated_series(highs, technical.get("recent_high_dates"))
    low_series = _dated_series(lows, technical.get("recent_low_dates"))
    if close_series is None or high_series is None or low_series is None:
        return None

    highs_by_date = dict(high_series)
    lows_by_date = dict(low_series)
    common_dates = [
        value_date
        for value_date, _value in close_series
        if value_date < as_of
        and value_date in highs_by_date
        and value_date in lows_by_date
    ]
    closes_by_date = dict(close_series)
    return {
        "ohlc_closes": [closes_by_date[value_date] for value_date in common_dates],
        "highs": [highs_by_date[value_date] for value_date in common_dates],
        "lows": [lows_by_date[value_date] for value_date in common_dates],
    }


def _dated_series(values: list[Any], raw_dates: Any) -> list[tuple[date, Any]] | None:
    if not isinstance(raw_dates, list) or len(raw_dates) != len(values):
        return None
    parsed_dates = [_parse_date(value) for value in raw_dates]
    if any(value is None for value in parsed_dates):
        return None
    resolved_dates = [value for value in parsed_dates if value is not None]
    if len(set(resolved_dates)) != len(resolved_dates):
        return None
    return list(zip(resolved_dates, values, strict=True))


def _completed_independent_series(
    values: list[float],
    raw_dates: Any,
    *,
    as_of: date,
    close_count: int,
    close_includes_as_of: bool,
    shorter_series_is_complete: bool = False,
) -> list[float]:
    if isinstance(raw_dates, list) and len(raw_dates) == len(values):
        parsed_dates = [_parse_date(value) for value in raw_dates]
        if all(value is not None for value in parsed_dates):
            return [
                value
                for value, value_date in zip(values, parsed_dates, strict=True)
                if value_date is not None and value_date < as_of
            ]
    if close_includes_as_of:
        if shorter_series_is_complete and len(values) < close_count:
            return values
        return values[:-1]
    if shorter_series_is_complete:
        return values
    # Without per-series dates, equal lengths do not prove that independently
    # dropna-filtered High/Low values belong to the same trading dates as Close.
    # Keep only the prefix whose final value cannot be the event-day tail.
    return values[:-1]


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
    holding_start: date | None = None,
    holding_end: date | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    source_bars = [_market_row_payload(row) for row in rows]
    bars = _compact_market_bars(source_bars) if compact else source_bars
    bars_fingerprint = hashlib.sha256(_canonical_json(bars).encode("utf-8")).hexdigest()
    coverage = _market_bar_coverage(
        bars,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        holding_start=holding_start,
        holding_end=holding_end,
    )
    return {
        "provider": provider,
        "fetched_at": _canonical_value(fetched_at) if fetched_at is not None else None,
        "quality": {
            "status": "available" if coverage["trading_bar_count"] > 0 else "insufficient",
            "missing_reason": missing_reason,
            "row_count": len(source_bars),
            **({"persisted_bar_count": len(bars)} if compact else {}),
            **coverage,
        },
        "bars_fingerprint": bars_fingerprint,
        "bars": bars,
    }


def _compact_market_bars(
    bars: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dated_bars: dict[date, dict[str, Any]] = {}
    longest_undated: dict[str, Any] | None = None
    longest_undated_count = 0
    for payload in bars:
        if payload.get("raw_data_is_final") is False:
            continue
        series = (
            payload.get("trailing_series")
            if isinstance(payload.get("trailing_series"), list)
            else []
        )
        raw_dates = payload.get("trailing_dates")
        parsed_dates = (
            [_parse_date(value) for value in raw_dates]
            if isinstance(raw_dates, list)
            else []
        )
        has_dated_series = len(parsed_dates) == len(series) and bool(series)
        if has_dated_series:
            for bar_date, series_bar in zip(
                parsed_dates,
                series,
                strict=True,
            ):
                if (
                    bar_date is None
                    or not isinstance(series_bar, dict)
                    or not _is_usable_price(series_bar.get("close"))
                ):
                    continue
                dated_bars[bar_date] = _single_market_bar_payload(
                    bar_date,
                    series_bar,
                    raw_data_is_final=payload.get("raw_data_is_final"),
                )
        elif series:
            usable_count = sum(
                1
                for series_bar in series
                if isinstance(series_bar, dict)
                and _is_usable_price(series_bar.get("close"))
            )
            if usable_count > longest_undated_count:
                longest_undated = payload
                longest_undated_count = usable_count

        bar = payload.get("bar") if isinstance(payload.get("bar"), dict) else {}
        bar_date = _parse_date(payload.get("data_date")) or _parse_date(
            payload.get("record_date")
        )
        if bar_date is not None and _is_usable_price(bar.get("close")):
            existing_payload = dated_bars.get(bar_date)
            existing_bar = (
                existing_payload.get("bar")
                if isinstance(existing_payload, dict)
                and isinstance(existing_payload.get("bar"), dict)
                else {}
            )
            merged_bar = dict(existing_bar)
            merged_bar.update(
                {key: value for key, value in bar.items() if value is not None}
            )
            dated_bars[bar_date] = _single_market_bar_payload(
                bar_date,
                merged_bar,
                raw_data_is_final=payload.get("raw_data_is_final"),
            )

    compacted = [dated_bars[value] for value in sorted(dated_bars)]
    if longest_undated is not None:
        compacted.append(longest_undated)
    return compacted


def _single_market_bar_payload(
    bar_date: date,
    bar: dict[str, Any],
    *,
    raw_data_is_final: Any,
) -> dict[str, Any]:
    date_value = bar_date.isoformat()
    return {
        "record_date": date_value,
        "data_date": date_value,
        "raw_data_is_final": (
            raw_data_is_final
            if isinstance(raw_data_is_final, bool)
            else None
        ),
        "trailing_dates": [],
        "bar": _canonical_value(bar),
        "trailing_series": [],
    }


def _market_row_payload(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        record_date = row.get("record_date", row.get("date"))
        technical = row.get("technical") or {}
        raw_data_is_final = row.get("raw_data_is_final")
    else:
        record_date = getattr(row, "record_date", None)
        technical = getattr(row, "technical", None) or {}
        raw_data_is_final = getattr(row, "raw_data_is_final", None)
    ohlcv = technical.get("ohlcv") if isinstance(technical.get("ohlcv"), dict) else {}
    closes = technical.get("recent_closes") if isinstance(technical.get("recent_closes"), list) else []
    highs = technical.get("recent_highs") if isinstance(technical.get("recent_highs"), list) else []
    lows = technical.get("recent_lows") if isinstance(technical.get("recent_lows"), list) else []
    volumes = technical.get("recent_volumes") if isinstance(technical.get("recent_volumes"), list) else []
    close_series = _dated_series(closes, technical.get("recent_close_dates"))
    high_series = _dated_series(highs, technical.get("recent_high_dates"))
    low_series = _dated_series(lows, technical.get("recent_low_dates"))
    volume_series = _dated_series(volumes, technical.get("recent_volume_dates"))
    close_value_dates = [value_date for value_date, _value in close_series or []]
    close_dates = [value_date.isoformat() for value_date in close_value_dates]
    highs_by_date = dict(high_series) if high_series is not None else None
    lows_by_date = dict(low_series) if low_series is not None else None
    volumes_by_date = dict(volume_series) if volume_series is not None else None
    data_dates = technical.get("data_dates") if isinstance(technical.get("data_dates"), dict) else {}
    series = [
        {
            "close": closes[index],
            "high": highs_by_date.get(value_date) if highs_by_date is not None else None,
            "low": lows_by_date.get(value_date) if lows_by_date is not None else None,
            "volume": volumes_by_date.get(value_date) if volumes_by_date is not None else None,
        }
        for index, value_date in enumerate(close_value_dates)
    ]
    if not close_dates:
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
        "raw_data_is_final": raw_data_is_final if isinstance(raw_data_is_final, bool) else None,
        "trailing_dates": _canonical_value(close_dates),
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

    material_fallback_recovery = _material_fallback_recovery(
        existing_quality,
        new_quality,
        provider_upgrade_min_coverage_ratio=provider_upgrade_min_coverage_ratio,
    )
    if not existing_missing and new_missing and not material_fallback_recovery:
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

    if _dated_coverage_shrunk(
        existing_quality,
        new_quality,
        upgrading_provider=upgrading_provider,
        allow_estimated_recovery=material_fallback_recovery,
        provider_upgrade_min_coverage_ratio=provider_upgrade_min_coverage_ratio,
    ):
        return True
    return False


def _material_fallback_recovery(
    existing_quality: dict[str, Any],
    new_quality: dict[str, Any],
    *,
    provider_upgrade_min_coverage_ratio: float,
) -> bool:
    if bool(existing_quality.get("missing_reason")) or not bool(new_quality.get("missing_reason")):
        return False
    if existing_quality.get("coverage_version") not in {"market-coverage-v1", "legacy-derived"}:
        return False
    if new_quality.get("coverage_version") != "market-coverage-v1":
        return False
    existing_count = existing_quality.get("trading_bar_count")
    new_count = new_quality.get("trading_bar_count")
    if not isinstance(existing_count, int) or not isinstance(new_count, int) or new_count <= existing_count:
        return False
    if existing_count >= 60:
        return False
    required_count = max(
        60,
        math.ceil(existing_count / provider_upgrade_min_coverage_ratio),
    )
    return new_count >= required_count


def _snapshot_quality(market: dict[str, Any]) -> dict[str, Any]:
    raw_quality = market.get("quality")
    quality = dict(raw_quality) if isinstance(raw_quality, dict) else {}
    if isinstance(quality.get("trading_bar_count"), int):
        return quality
    legacy_coverage = _market_bar_coverage(
        market.get("bars") if isinstance(market.get("bars"), list) else [],
        coverage_start=None,
        coverage_end=None,
        holding_start=None,
        holding_end=None,
    )
    quality.update(legacy_coverage)
    if market.get("bars"):
        quality["coverage_version"] = "legacy-derived"
    elif isinstance(quality.get("row_count"), int):
        quality["trading_bar_count"] = quality["row_count"]
        quality["coverage_version"] = "legacy-row-count"
    else:
        quality["coverage_version"] = "legacy-derived"
    return quality


def _dated_coverage_shrunk(
    existing_quality: dict[str, Any],
    new_quality: dict[str, Any],
    *,
    upgrading_provider: bool,
    allow_estimated_recovery: bool,
    provider_upgrade_min_coverage_ratio: float,
) -> bool:
    existing_holding_dates = _quality_dates(existing_quality, "holding_covered_dates")
    new_holding_dates = _quality_dates(new_quality, "holding_covered_dates")
    if (
        existing_holding_dates
        and not existing_holding_dates.issubset(new_holding_dates)
        and not allow_estimated_recovery
    ):
        return True
    if existing_quality.get("coverage_basis") != "dated_bars":
        return False
    if new_quality.get("coverage_basis") != "dated_bars":
        return not allow_estimated_recovery
    existing_start = _parse_date(existing_quality.get("date_start"))
    existing_end = _parse_date(existing_quality.get("date_end"))
    new_start = _parse_date(new_quality.get("date_start"))
    new_end = _parse_date(new_quality.get("date_end"))
    if existing_start is not None and (new_start is None or new_start > existing_start):
        return True
    if existing_end is not None and (new_end is None or new_end < existing_end):
        return True

    existing_dates = _quality_dates(existing_quality, "covered_dates")
    new_dates = _quality_dates(new_quality, "covered_dates")
    if not existing_dates or not new_dates:
        return False
    existing_count = existing_quality.get("trading_bar_count")
    new_count = new_quality.get("trading_bar_count")
    if upgrading_provider and isinstance(existing_count, int) and isinstance(new_count, int) and new_count < existing_count:
        required_overlap = math.ceil(len(existing_dates) * provider_upgrade_min_coverage_ratio)
        return len(existing_dates & new_dates) < required_overlap
    return not existing_dates.issubset(new_dates)


def _quality_dates(quality: dict[str, Any], key: str) -> set[date]:
    raw_dates = quality.get(key)
    if not isinstance(raw_dates, list):
        return set()
    return {parsed for value in raw_dates if (parsed := _parse_date(value)) is not None}


def _market_bar_coverage(
    bars: list[dict[str, Any]],
    *,
    coverage_start: date | None,
    coverage_end: date | None,
    holding_start: date | None,
    holding_end: date | None,
) -> dict[str, Any]:
    dated_bars: set[date] = set()
    undated_series_count = 0
    for payload in bars:
        if payload.get("raw_data_is_final") is False:
            continue
        series = payload.get("trailing_series") if isinstance(payload.get("trailing_series"), list) else []
        raw_series_dates = payload.get("trailing_dates")
        series_dates = (
            [_parse_date(value) for value in raw_series_dates]
            if isinstance(raw_series_dates, list)
            else []
        )
        valid_series_dates = [
            value
            for value, series_bar in zip(series_dates, series, strict=False)
            if _date_in_window(value, coverage_start, coverage_end)
            and isinstance(series_bar, dict)
            and _is_usable_price(series_bar.get("close"))
        ]
        if valid_series_dates:
            dated_bars.update(valid_series_dates)
        elif not raw_series_dates:
            usable_series_count = sum(
                1
                for series_bar in series
                if isinstance(series_bar, dict) and _is_usable_price(series_bar.get("close"))
            )
            undated_series_count = max(undated_series_count, usable_series_count)

        bar = payload.get("bar") if isinstance(payload.get("bar"), dict) else {}
        if not series and _is_usable_price(bar.get("close")):
            bar_date = _parse_date(payload.get("data_date")) or _parse_date(payload.get("record_date"))
            if _date_in_window(bar_date, coverage_start, coverage_end):
                dated_bars.add(bar_date)

    if coverage_start is not None and coverage_end is not None and undated_series_count:
        undated_series_count = min(undated_series_count, _weekday_count(coverage_start, coverage_end))
    trading_bar_count = max(len(dated_bars), undated_series_count)
    coverage_basis = "estimated_trailing_series" if undated_series_count > len(dated_bars) else "dated_bars"
    holding_dates = (
        {
            value
            for value in dated_bars
            if _date_in_window(value, holding_start, holding_end)
        }
        if holding_start is not None or holding_end is not None
        else set()
    )
    return {
        "coverage_version": "market-coverage-v1",
        "coverage_basis": coverage_basis,
        "trading_bar_count": trading_bar_count,
        "covered_dates": [value.isoformat() for value in sorted(dated_bars)],
        "holding_covered_dates": [value.isoformat() for value in sorted(holding_dates)],
        "date_start": min(dated_bars).isoformat() if dated_bars else None,
        "date_end": max(dated_bars).isoformat() if dated_bars else None,
    }


def _is_usable_price(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        number = float(value)
        return math.isfinite(number) and number > 0
    except (TypeError, ValueError):
        return False


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
