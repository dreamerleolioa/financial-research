"""Prepared-run validation and evidence materialization shared by Radar workflows."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from datetime import date
from typing import Any, Literal
import logging
import math

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar import dependencies
from ai_stock_sentinel.daily_radar.background_context import (
    BackgroundChipContextProvider,
    BackgroundContextPayload,
    update_background_chip_context_cache,
)
from ai_stock_sentinel.daily_radar.data_quality import (
    margin_evidence_is_complete,
    missing_daily_radar_candidate_technical_fields,
)
from ai_stock_sentinel.daily_radar.dependencies import DAILY_RADAR_REQUIRED_REFRESH_STEPS
from ai_stock_sentinel.daily_radar.institutional_archive_universe import (
    InstitutionalArchiveUniverseError,
)
from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
    get_completed_institutional_snapshot,
)
from ai_stock_sentinel.daily_radar.institutional_payloads import _institutional_payload, _mapping
from ai_stock_sentinel.daily_radar.institutional_universe_provider import (
    InstitutionalUniverseProviderError,
)
from ai_stock_sentinel.daily_radar.managed_raw_data import ManagedRawDataSelection
from ai_stock_sentinel.daily_radar.raw_data import (
    current_daily_radar_raw_rows,
    insufficient_history_daily_radar_raw_rows,
)
from ai_stock_sentinel.daily_radar.repository import (
    get_daily_radar_prepared_run,
    get_shared_background_context_trace_by_symbol,
    update_daily_radar_prepared_step_status,
)
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarManagedRawDataRefreshRequest,
    DailyRadarManagedRawDataRefreshResponse,
    DailyRadarRefreshStepRequest,
    DailyRadarRefreshStepResponse,
)
from ai_stock_sentinel.daily_radar.universe import (
    DailyRadarUniverseEntry,
    DailyRadarUniverseProvider,
    is_daily_radar_supported_symbol,
)
from ai_stock_sentinel.data_sources.fundamental.interface import PointInTimeFundamentalProvider
from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_industry
from ai_stock_sentinel.db.models import DailyRadarPreparedRun
from ai_stock_sentinel.phase1_avwap.service import (
    DailyPriceProvider,
    refresh_phase1_avwap_snapshots_for_symbols,
)
from ai_stock_sentinel.phase1_avwap.universe import resolve_phase1_refresh_symbol_set


logger = logging.getLogger(__name__)


def _refresh_phase1_avwap_for_daily_radar(
    db: Session,
    *,
    symbols: list[str],
    run_date: date,
    provider: DailyPriceProvider,
) -> None:
    try:
        refresh_symbol_set = resolve_phase1_refresh_symbol_set(db, seed_symbols=symbols)
        refresh_symbols = refresh_symbol_set.symbols
        result = refresh_phase1_avwap_snapshots_for_symbols(
            db,
            symbols=refresh_symbols,
            data_date=run_date,
            provider=provider,
        )
        if result.missing_symbols:
            logger.warning(
                "[DailyRadar] Phase 1 AVWAP refresh completed with missing snapshots run_date=%s missing_symbol_reasons=%s",
                run_date.isoformat(),
                result.missing_symbol_reasons,
            )
        if refresh_symbol_set.skipped_symbol_reasons:
            logger.info(
                "[DailyRadar] Phase 1 AVWAP refresh skipped unsupported symbols run_date=%s skipped_symbol_reasons=%s",
                run_date.isoformat(),
                refresh_symbol_set.skipped_symbol_reasons,
            )
    except Exception:
        with suppress(Exception):
            db.rollback()
        logger.exception(
            "[DailyRadar] Phase 1 AVWAP refresh failed; continuing with read-only missing trace run_date=%s",
            run_date.isoformat(),
        )


def _refresh_daily_radar_context_step(
    db: Session,
    *,
    payload: DailyRadarRefreshStepRequest | None,
    provider: BackgroundChipContextProvider,
    context_type: str,
    step: str,
) -> DailyRadarRefreshStepResponse:
    request = payload or DailyRadarRefreshStepRequest()
    run_date = request.run_date or dependencies._backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    result = update_background_chip_context_cache(
        db,
        run_date=run_date,
        market=request.market,
        provider=provider,
        symbols=list(prepared.selected_symbols),
        context_types=[context_type],
        reuse_same_day_fresh=True,
        require_same_day_fresh=True,
    )
    update_daily_radar_prepared_step_status(
        db,
        prepared,
        step=step,
        status=str(result["status"]),
        details={
            "symbol_count": int(result["symbol_count"]),
            "records_written": int(result["records_written"]),
            "reused_symbols": list(result.get("reused_symbols") or []),
            "missing_symbols": list(result.get("missing_symbols") or []),
            "not_applicable_symbols": list(result.get("not_applicable_symbols") or []),
            "missing_symbol_reasons": dict(result.get("missing_symbol_reasons") or {}),
            "errors": list(result["errors"]),
        },
    )
    db.commit()
    return DailyRadarRefreshStepResponse(
        status=result["status"],
        step=step,
        run_date=run_date,
        market=str(result["market"]),
        symbol_count=int(result["symbol_count"]),
        records_written=int(result["records_written"]),
        reused_symbols=list(result.get("reused_symbols") or []),
        missing_symbols=list(result.get("missing_symbols") or []),
        not_applicable_symbols=list(result.get("not_applicable_symbols") or []),
        missing_symbol_reasons=dict(result.get("missing_symbol_reasons") or {}),
        errors=list(result["errors"]),
    )


def _prepared_run_or_404(db: Session, *, run_date: date, market: str):
    prepared = get_daily_radar_prepared_run(db, run_date=run_date, market=market)
    if prepared is None:
        raise HTTPException(
            status_code=404,
            detail=f"Daily Radar prepared universe is missing for {market} on {run_date.isoformat()}.",
        )
    return prepared


def _should_refresh_daily_run_chip_context(market: str) -> bool:
    return market.upper() == "TW"


def _capped_daily_radar_universe(
    universe: list[DailyRadarUniverseEntry],
    *,
    max_symbols: int,
) -> list[DailyRadarUniverseEntry]:
    return universe[: max(0, max_symbols)]


def _universe_provider_error_type(exc: Exception) -> str:
    if isinstance(exc, InstitutionalUniverseProviderError):
        return exc.error_type
    if isinstance(exc, InstitutionalArchiveUniverseError):
        return exc.code
    return exc.__class__.__name__


def _universe_provider_name(provider: DailyRadarUniverseProvider) -> str:
    return str(getattr(provider, "name", provider.__class__.__name__))


def _required_institutional_archive_details(
    db: Session,
    *,
    run_date: date,
) -> dict[str, Any]:
    snapshots: dict[str, Any] = {}
    missing_markets: list[str] = []
    for market in ("TW", "TWO"):
        snapshot = get_completed_institutional_snapshot(
            db,
            market=market,
            trade_date=run_date,
        )
        payload_hash = snapshot.payload_hash if snapshot is not None else None
        if (
            snapshot is None
            or snapshot.row_count <= 0
            or not snapshot.source_provider.strip()
            or not snapshot.source_dataset.strip()
            or payload_hash is None
            or len(payload_hash) != 64
            or any(character not in "0123456789abcdef" for character in payload_hash)
        ):
            missing_markets.append(market)
            continue
        snapshots[market] = {
            "snapshot_id": snapshot.id,
            "row_count": snapshot.row_count,
            "source_provider": snapshot.source_provider,
            "source_dataset": snapshot.source_dataset,
            "payload_hash": snapshot.payload_hash,
        }
    if missing_markets:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "daily_radar_institutional_archive_incomplete",
                "message": (
                    "Daily Radar institutional archive is incomplete; "
                    "prepare-universe is blocked."
                ),
                "run_date": run_date.isoformat(),
                "required_markets": ["TW", "TWO"],
                "missing_markets": missing_markets,
            },
        )
    return {
        "records_written": sum(
            int(details["row_count"]) for details in snapshots.values()
        ),
        "markets": snapshots,
    }


def _raise_if_required_refresh_steps_missing(prepared: Any) -> None:
    step_statuses = dict(prepared.step_statuses or {})
    incomplete_steps = [
        step
        for step in DAILY_RADAR_REQUIRED_REFRESH_STEPS
        if (step_statuses.get(step) or {}).get("status") != "completed"
    ]
    if not incomplete_steps:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "daily_radar_refresh_steps_incomplete",
            "message": "Daily Radar refresh steps are incomplete; run scoring is blocked.",
            "incomplete_steps": incomplete_steps,
            "step_statuses": step_statuses,
        },
    )


def _institutional_payloads_by_symbol(
    universe: list[DailyRadarUniverseEntry],
    *,
    run_date: date,
) -> dict[str, dict[str, Any]]:
    return {entry.symbol: _institutional_payload(entry, run_date=run_date) for entry in universe}


def _full_margin_contexts_by_symbol(
    session: Session,
    *,
    symbols: Iterable[str],
    run_date: date,
) -> dict[str, dict[str, Any]]:
    traces = get_shared_background_context_trace_by_symbol(
        session,
        symbols=symbols,
        context_types=("full_margin",),
        reference_date=run_date,
        point_in_time=True,
    )
    return {
        symbol: dict(contexts[0]) if contexts else {}
        for symbol, contexts in traces.items()
    }


def _add_institutional_volume_ratios(
    rows: Iterable[Any],
    *,
    selected_symbols: set[str],
) -> None:
    for row in rows:
        if row.symbol in selected_symbols:
            continue
        institutional = dict(_mapping(row.institutional))
        flow = dict(_mapping(institutional.get("institutional_flow")))
        avg_volume = _mapping(row.technical).get("ohlcv", {}).get("avg_volume_20")
        net_flow = flow.get("three_party_net_shares")
        if _finite_number(avg_volume) and float(avg_volume) > 0 and _finite_number(net_flow):
            ratio = float(net_flow) / float(avg_volume)
            institutional["net_flow_to_avg_volume"] = ratio
            flow["net_flow_to_avg_volume"] = ratio
            institutional["institutional_flow"] = flow
            row.institutional = institutional


def _merge_institutional_evidence(
    official: Mapping[str, Any] | None,
    canonical: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(_mapping(official))
    merged.update({key: value for key, value in canonical.items() if key != "institutional_flow"})
    flow = dict(_mapping(official).get("institutional_flow"))
    flow.update(_mapping(canonical.get("institutional_flow")))
    merged["institutional_flow"] = flow
    return merged


class _PreparedBatchTechnicalFetcher:
    def __init__(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        self._payloads = payloads

    def fetch(
        self,
        symbols: Iterable[str],
        *,
        run_date: date,
    ) -> Mapping[str, Mapping[str, Any]]:
        del run_date
        return {
            symbol: self._payloads[symbol]
            for symbol in symbols
            if symbol in self._payloads
        }


class _PreparedBackgroundContextProvider:
    def __init__(self, payloads: Iterable[BackgroundContextPayload]) -> None:
        self._payloads = list(payloads)

    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> Iterable[BackgroundContextPayload]:
        del run_date, market
        symbol_set = set(symbols)
        context_type_set = set(context_types)
        return [
            payload
            for payload in self._payloads
            if payload.symbol in symbol_set and payload.context_type in context_type_set
        ]


def _prefetch_ai_background_evidence(
    provider: BackgroundChipContextProvider,
    *,
    symbols: list[str],
    context_types: Iterable[str],
    fresh_pairs: set[tuple[str, str]],
    run_date: date,
    market: str,
) -> tuple[list[BackgroundContextPayload], list[dict[str, Any]]]:
    batches: dict[tuple[str, ...], list[str]] = {}
    for context_type in context_types:
        missing_symbols = tuple(
            symbol
            for symbol in symbols
            if (symbol, context_type) not in fresh_pairs
        )
        if missing_symbols:
            batches.setdefault(missing_symbols, []).append(context_type)

    payloads: list[BackgroundContextPayload] = []
    errors: list[dict[str, Any]] = []
    for fetch_symbols, fetch_context_types in batches.items():
        try:
            payloads.extend(
                provider.fetch(
                    symbols=list(fetch_symbols),
                    context_types=fetch_context_types,
                    run_date=run_date,
                    market=market,
                )
            )
        except Exception as exc:
            errors.append(
                {
                    "code": "background_context_provider_failed",
                    "message": str(exc),
                    "error_type": exc.__class__.__name__,
                }
            )
    return payloads, errors


def _materialize_ai_business_fundamentals(
    rows: Iterable[Any],
    *,
    provider: PointInTimeFundamentalProvider,
    as_of_date: date,
    industry_resolver: Callable[[str], str | None] | None = None,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for row in rows:
        technical = _mapping(row.technical)
        close = _mapping(technical.get("ohlcv")).get("close")
        if not _finite_number(close) or float(close) <= 0:
            continue
        try:
            data = provider.fetch_as_of(row.symbol, float(close), as_of_date=as_of_date)
        except Exception as exc:
            errors.append(
                {
                    "symbol": row.symbol,
                    "lane": "fundamental",
                    "error_type": exc.__class__.__name__,
                }
            )
            continue
        existing = dict(_mapping(row.fundamental))
        projected = {
            "ttm_eps": data.ttm_eps,
            "annual_cash_dividend": data.annual_cash_dividend,
            "dividend_yield": data.dividend_yield,
            "pe_current": data.pe_current,
            "pe_mean": data.pe_mean,
            "pe_std": data.pe_std,
            "pe_percentile": data.pe_percentile,
            "pe_band": data.pe_band,
            "yield_signal": data.yield_signal,
            "source_provider": data.source_provider,
            "warnings": list(data.warnings),
        }
        industry = data.industry or _safe_resolve_industry(
            row.symbol,
            resolver=industry_resolver,
        )
        if industry:
            projected["industry"] = industry
        existing.update(projected)
        data_dates = dict(_mapping(existing.get("data_dates")))
        data_dates["fundamental"] = as_of_date.isoformat()
        existing["data_dates"] = data_dates
        row.fundamental = existing
    return errors


def _materialize_industry_classifications(
    rows: Iterable[Any],
    *,
    resolver: Callable[[str], str | None] | None = None,
) -> None:
    for row in rows:
        industry = _safe_resolve_industry(row.symbol, resolver=resolver)
        if not industry:
            continue
        fundamental = dict(_mapping(row.fundamental))
        fundamental["industry"] = industry
        row.fundamental = fundamental


def _industry_classifications_by_symbol(
    symbols: Iterable[str],
    *,
    resolver: Callable[[str], str | None] | None = None,
) -> dict[str, str]:
    classifications: dict[str, str] = {}
    for symbol in symbols:
        industry = _safe_resolve_industry(symbol, resolver=resolver)
        if industry:
            classifications[symbol] = industry
    return classifications


def _safe_resolve_industry(
    symbol: str,
    *,
    resolver: Callable[[str], str | None] | None = None,
) -> str | None:
    try:
        return (resolver or resolve_symbol_industry)(symbol)
    except Exception as exc:
        logger.warning(
            "Official industry classification unavailable error_type=%s",
            exc.__class__.__name__,
        )
        return None


def _ai_evidence_missing_by_lane(rows: Iterable[Any]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {
        "technical": [],
        "institutional": [],
        "margin": [],
        "fundamental": [],
    }
    for row in rows:
        technical = _mapping(row.technical)
        institutional = _mapping(row.institutional)
        flow = _mapping(institutional.get("institutional_flow")) or institutional
        fundamental = _mapping(row.fundamental)
        margin = _mapping(fundamental.get("margin"))
        if missing_daily_radar_candidate_technical_fields(technical, record_date=row.record_date):
            missing["technical"].append(row.symbol)
        if not all(
            _finite_number(flow.get(field))
            for field in ("foreign_net_shares", "investment_trust_net_shares", "three_party_net_shares")
        ):
            missing["institutional"].append(row.symbol)
        else:
            institutional_date = _mapping(flow.get("data_dates")).get("institutional_flow")
            if str(institutional_date or "") != row.record_date.isoformat():
                missing["institutional"].append(row.symbol)
        if not margin_evidence_is_complete(margin, record_date=row.record_date, symbol=row.symbol):
            missing["margin"].append(row.symbol)
        if (
            not _finite_number(fundamental.get("ttm_eps"))
            or str(_mapping(fundamental.get("data_dates")).get("fundamental") or "")
            != row.record_date.isoformat()
        ):
            missing["fundamental"].append(row.symbol)
    return missing


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _supported_daily_radar_symbols(symbols: Iterable[str]) -> tuple[list[str], dict[str, str]]:
    supported_symbols: list[str] = []
    skipped_symbol_reasons: dict[str, str] = {}
    for symbol in symbols:
        normalized = str(symbol).strip()
        if not normalized:
            continue
        if is_daily_radar_supported_symbol(normalized):
            supported_symbols.append(normalized)
            continue
        skipped_symbol_reasons[normalized] = "unsupported_daily_radar_symbol"
    return supported_symbols, skipped_symbol_reasons


def _managed_selected_overlap_count(
    selection: ManagedRawDataSelection,
    selected_symbols: Iterable[str],
) -> int:
    selected_symbol_set = {str(symbol) for symbol in selected_symbols}
    return sum(symbol in selected_symbol_set for symbol in selection.symbols)


def _finish_managed_raw_data_refresh(
    db: Session,
    *,
    prepared: DailyRadarPreparedRun | None,
    request: DailyRadarManagedRawDataRefreshRequest,
    run_date: date,
    selection: ManagedRawDataSelection,
    selected_overlap_count: int,
    status: Literal["completed", "failed"],
    reused_record_count: int = 0,
    records_written: int = 0,
    missing_record_count: int = 0,
    error_codes: list[str] | None = None,
) -> DailyRadarManagedRawDataRefreshResponse:
    safe_error_codes = list(error_codes or [])
    response_details = {
        "target_symbol_count": len(selection.symbols),
        "active_symbol_count": 0,  # Retained internal response contract; personal holdings are retired.
        "recent_analysis_symbol_count": selection.recent_analysis_symbol_count,
        "overlap_symbol_count": 0,
        "selected_overlap_count": selected_overlap_count,
        "reused_record_count": reused_record_count,
        "records_written": records_written,
        "missing_record_count": missing_record_count,
        "deferred_recent_symbol_count": selection.deferred_recent_symbol_count,
        "error_codes": safe_error_codes,
    }
    if prepared is not None:
        update_daily_radar_prepared_step_status(
            db,
            prepared,
            step="refresh-managed-raw-data",
            status=status,
            details={
                "required_for_scoring": False,
                **response_details,
            },
        )
    db.commit()
    return DailyRadarManagedRawDataRefreshResponse(
        status=status,
        run_date=run_date,
        market=request.market,
        **response_details,
    )


def _require_complete_daily_radar_raw_rows(
    rows: Iterable[Any],
    *,
    selected_symbols: Iterable[str],
    run_date: date,
) -> list[Any]:
    selected_symbol_list = list(selected_symbols)
    if not selected_symbol_list:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "daily_radar_selected_universe_empty",
                "run_date": run_date.isoformat(),
            },
        )
    rows = list(rows)
    reusable_rows = current_daily_radar_raw_rows(rows, run_date=run_date)
    # 短歷史資料只能進入 prefilter 的 data_gap 排除路徑，不能補值取得評分資格。
    reusable_rows += insufficient_history_daily_radar_raw_rows(rows, run_date=run_date)
    reusable_symbols = {row.symbol for row in reusable_rows}
    missing_symbols = [symbol for symbol in selected_symbol_list if symbol not in reusable_symbols]
    if missing_symbols:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "daily_radar_raw_data_incomplete",
                "run_date": run_date.isoformat(),
                "missing_symbols": missing_symbols,
            },
        )
    return reusable_rows
