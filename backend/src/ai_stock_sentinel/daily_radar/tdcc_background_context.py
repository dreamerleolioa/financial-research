from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from io import StringIO
from typing import Any

from ai_stock_sentinel.daily_radar.background_context import BACKGROUND_CONTEXT_ALL_CONSUMERS, BackgroundContextPayload


TDCC_WEEKLY_HOLDERS_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"

_CONTEXT_TYPE = "weekly_major_holders"
_MAJOR_HOLDER_LEVELS = frozenset({12, 13, 14, 15})
_RETAIL_HOLDER_LEVELS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9})


@dataclass(frozen=True)
class _TdccDatasetError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class TdccWeeklyMajorHoldersProvider:
    """Fetch TDCC weekly shareholder distribution for selected symbols."""

    provider_name = "tdcc_weekly_major_holders_provider"

    def __init__(
        self,
        *,
        request_get: Callable[..., Any] | None = None,
        stale_after_days: int = 14,
    ) -> None:
        self._request_get = request_get
        self._stale_after_days = stale_after_days

    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> Iterable[BackgroundContextPayload]:
        if _CONTEXT_TYPE not in context_types:
            return []

        rows_by_symbol = self._fetch_rows_by_symbol()
        payloads: list[BackgroundContextPayload] = []
        for symbol in symbols:
            stock_id = _strip_suffix(symbol)
            rows = rows_by_symbol.get(stock_id, [])
            payloads.append(self._payload_for_symbol(symbol=symbol, rows=rows, run_date=run_date, market=market))
        return payloads

    def _fetch_rows_by_symbol(self) -> dict[str, list[dict[str, str]]]:
        request_get = self._request_get
        if request_get is None:
            try:
                import requests
            except ImportError as exc:
                raise _TdccDatasetError("missing_dependency", "requests package is not installed") from exc
            request_get = requests.get

        try:
            response = request_get(TDCC_WEEKLY_HOLDERS_URL, timeout=30)
        except Exception as exc:
            raise _TdccDatasetError("tdcc_request_error", f"TDCC request failed: {exc}") from exc

        try:
            response.raise_for_status()
            text = _response_text(response)
        except Exception as exc:
            raise _TdccDatasetError("tdcc_response_error", f"TDCC response could not be read: {exc}") from exc

        rows_by_symbol: dict[str, list[dict[str, str]]] = {}
        try:
            reader = csv.DictReader(StringIO(text.lstrip("\ufeff")))
            for row in reader:
                stock_id = str(row.get("證券代號") or "").strip()
                if not stock_id:
                    continue
                rows_by_symbol.setdefault(stock_id, []).append(
                    {str(key).strip(): str(value).strip() for key, value in row.items()}
                )
        except Exception as exc:
            raise _TdccDatasetError("tdcc_parse_error", f"TDCC CSV could not be parsed: {exc}") from exc
        if not rows_by_symbol:
            raise _TdccDatasetError("tdcc_no_data", "TDCC CSV returned no shareholder distribution rows")
        return rows_by_symbol

    def _payload_for_symbol(
        self,
        *,
        symbol: str,
        rows: list[dict[str, str]],
        run_date: date,
        market: str,
    ) -> BackgroundContextPayload:
        if not rows:
            return self._missing_payload(
                symbol=symbol,
                run_date=run_date,
                market=market,
                missing_reason="tdcc_symbol_not_found",
            )

        as_of_date = _tdcc_date(rows[0].get("資料日期"))
        freshness, missing_reason = self._freshness(as_of_date=as_of_date, run_date=run_date)
        distribution = [_distribution_item(row) for row in rows]
        distribution = [item for item in distribution if item is not None]
        major_items = [item for item in distribution if item["level"] in _MAJOR_HOLDER_LEVELS]
        retail_items = [item for item in distribution if item["level"] in _RETAIL_HOLDER_LEVELS]
        total_item = next((item for item in distribution if item["level"] == 17), None)

        return BackgroundContextPayload(
            symbol=symbol,
            context_type=_CONTEXT_TYPE,
            applicable_consumers=BACKGROUND_CONTEXT_ALL_CONSUMERS,
            source={
                "domain": "background_context",
                "provider": self.provider_name,
                "dataset": "tdcc_shareholder_distribution",
                "url": TDCC_WEEKLY_HOLDERS_URL,
                "market": market,
            },
            as_of_date=as_of_date,
            freshness=freshness,
            payload={
                "provider": self.provider_name,
                "dataset": "tdcc_shareholder_distribution",
                "major_holder_levels": sorted(_MAJOR_HOLDER_LEVELS),
                "retail_holder_levels": sorted(_RETAIL_HOLDER_LEVELS),
                "major_holder_ratio": _sum_ratio(major_items),
                "major_holder_people": _sum_int(major_items, "people"),
                "major_holder_shares": _sum_int(major_items, "shares"),
                "retail_holder_ratio": _sum_ratio(retail_items),
                "total_people": total_item["people"] if total_item else None,
                "total_shares": total_item["shares"] if total_item else None,
                "distribution": distribution,
                "data_dates": [as_of_date.isoformat()] if as_of_date is not None else [],
            },
            missing_reason=missing_reason,
            replay_key=_replay_key(symbol, as_of_date),
        )

    def _freshness(self, *, as_of_date: date | None, run_date: date) -> tuple[str, str | None]:
        if as_of_date is None:
            return "missing", "tdcc_missing_as_of_date"
        age_days = (run_date - as_of_date).days
        if age_days <= self._stale_after_days:
            return "fresh", None
        return "stale", "source_stale"

    def _missing_payload(
        self,
        *,
        symbol: str,
        run_date: date,
        market: str,
        missing_reason: str,
    ) -> BackgroundContextPayload:
        return BackgroundContextPayload(
            symbol=symbol,
            context_type=_CONTEXT_TYPE,
            applicable_consumers=BACKGROUND_CONTEXT_ALL_CONSUMERS,
            source={
                "domain": "background_context",
                "provider": self.provider_name,
                "dataset": "tdcc_shareholder_distribution",
                "url": TDCC_WEEKLY_HOLDERS_URL,
                "market": market,
            },
            as_of_date=None,
            freshness="missing",
            payload={},
            missing_reason=missing_reason,
            replay_key=f"background_context:{symbol}:{_CONTEXT_TYPE}:{run_date.isoformat()}:missing:{missing_reason}",
        )


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8-sig")
    return str(getattr(response, "text", ""))


def _strip_suffix(symbol: str) -> str:
    return symbol.split(".")[0].strip()


def _tdcc_date(value: str | None) -> date | None:
    if not value:
        return None
    normalized = str(value).strip()
    try:
        return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:8]))
    except (ValueError, IndexError):
        return None


def _distribution_item(row: Mapping[str, str]) -> dict[str, Any] | None:
    level = _safe_int(row.get("持股分級"))
    if level is None:
        return None
    return {
        "level": level,
        "people": _safe_int(row.get("人數")),
        "shares": _safe_int(row.get("股數")),
        "ratio": _safe_float(row.get("占集保庫存數比例%")),
    }


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sum_ratio(items: list[dict[str, Any]]) -> float | None:
    ratios = [item["ratio"] for item in items if item.get("ratio") is not None]
    return round(sum(ratios), 4) if ratios else None


def _sum_int(items: list[dict[str, Any]], key: str) -> int | None:
    values = [item[key] for item in items if item.get(key) is not None]
    return sum(values) if values else None


def _replay_key(symbol: str, as_of_date: date | None) -> str:
    date_part = as_of_date.isoformat() if as_of_date is not None else "missing"
    return f"background_context:{symbol}:{_CONTEXT_TYPE}:{date_part}"


__all__ = [
    "TDCC_WEEKLY_HOLDERS_URL",
    "TdccWeeklyMajorHoldersProvider",
]
