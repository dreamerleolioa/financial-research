from __future__ import annotations

import logging
from contextlib import suppress
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_universe_provider import TwseRwdInstitutionalUniverseProvider
from ai_stock_sentinel.daily_radar.market_context import (
    MarketIndexContextProvider,
    YFinanceMarketIndexContextProvider,
)
from ai_stock_sentinel.daily_radar.name_backfill import (
    SymbolNameResolver,
    backfill_daily_radar_symbol_names,
    get_daily_radar_symbol_name_resolver,
)
from ai_stock_sentinel.daily_radar.raw_data import (
    BatchTechnicalFetcher,
    YFinanceBatchTechnicalFetcher,
    ensure_daily_radar_raw_rows,
)
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.background_context import (
    BackgroundChipContextProvider,
    update_background_chip_context_cache,
)
from ai_stock_sentinel.daily_radar.default_background_context import DefaultBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.forward_validation import (
    DEFAULT_FORWARD_WINDOWS,
    build_forward_validation_report,
    default_due_start_date,
    due_windows_by_candidate,
    forward_validation_candidates_from_runs,
    load_price_series_from_raw_data,
    upsert_forward_validation_results,
)
from ai_stock_sentinel.daily_radar.rule_governance import (
    build_monthly_rule_review_report,
)
from ai_stock_sentinel.daily_radar.repository import (
    BACKGROUND_CONTEXT_TYPES,
    get_daily_radar_prepared_run,
    get_daily_radar_run_by_date,
    get_final_raw_data_rows_for_date,
    get_final_raw_data_rows_for_symbols,
    get_latest_daily_radar_run,
    get_shared_background_context_trace_by_symbol,
    get_symbol_candidate_history,
    update_daily_radar_prepared_market_context,
    update_daily_radar_prepared_step_status,
    upsert_daily_radar_prepared_run,
)
from ai_stock_sentinel.daily_radar.presenter import (
    history_response,
    matches_bucket,
    parse_date,
    public_run_response,
    run_trigger_response,
)
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarChipContextUpdateRequest,
    DailyRadarChipContextUpdateResponse,
    DailyRadarForwardValidationRunRequest,
    DailyRadarForwardValidationRunResponse,
    DailyRadarMonthlyRuleReviewRequest,
    DailyRadarMonthlyRuleReviewResponse,
    DailyRadarNameBackfillRequest,
    DailyRadarNameBackfillResponse,
    DailyRadarPreparedRunRequest,
    DailyRadarPreparedRunResponse,
    DailyRadarRefreshStepRequest,
    DailyRadarRefreshStepResponse,
    DailyRadarRunRequest,
    DailyRadarRunResponse,
    DailyRadarRunTriggerResponse,
)
from ai_stock_sentinel.daily_radar.service import run_daily_radar
from ai_stock_sentinel.daily_radar.universe import (
    DailyRadarUniverseEntry,
    DailyRadarUniverseProvider,
    refresh_daily_radar_universe_technical_tracks,
    select_daily_radar_universe,
)
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.clock import today_taipei
from ai_stock_sentinel.phase1_avwap.provider import TwseDailyPriceProvider
from ai_stock_sentinel.phase1_avwap.service import DailyPriceProvider, refresh_phase1_avwap_snapshots_for_symbols
from ai_stock_sentinel.phase1_avwap.universe import resolve_phase1_refresh_symbol_set


router = APIRouter(tags=["daily-radar"])
logger = logging.getLogger(__name__)

DAILY_RUN_REFRESH_CONTEXT_TYPES = ("lending", "full_margin")
DAILY_RADAR_REQUIRED_REFRESH_STEPS = (
    "refresh-lending",
    "refresh-full-margin",
    "refresh-ohlcv",
    "refresh-market-context",
)


def get_daily_radar_universe_provider() -> DailyRadarUniverseProvider:
    return TwseRwdInstitutionalUniverseProvider()


def get_daily_radar_technical_fetcher() -> BatchTechnicalFetcher:
    return YFinanceBatchTechnicalFetcher()


def get_daily_radar_market_context_provider() -> MarketIndexContextProvider:
    return YFinanceMarketIndexContextProvider()


def get_daily_radar_background_chip_context_provider() -> BackgroundChipContextProvider:
    return DefaultBackgroundChipContextProvider()


def get_phase1_avwap_daily_price_provider() -> DailyPriceProvider:
    return TwseDailyPriceProvider()


@router.post(
    "/internal/daily-radar/name-backfill",
    response_model=DailyRadarNameBackfillResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def backfill_daily_radar_names_endpoint(
    payload: DailyRadarNameBackfillRequest | None = None,
    db: Session = Depends(get_db),
    name_resolver: SymbolNameResolver = Depends(get_daily_radar_symbol_name_resolver),
) -> DailyRadarNameBackfillResponse:
    request = payload or DailyRadarNameBackfillRequest()
    try:
        result = backfill_daily_radar_symbol_names(
            db,
            limit=request.limit,
            dry_run=request.dry_run,
            name_resolver=name_resolver,
        )
        if request.dry_run:
            db.rollback()
        else:
            db.commit()
        return DailyRadarNameBackfillResponse(
            status="completed",
            dry_run=request.dry_run,
            scanned=result.scanned,
            updated_candidates=result.updated_candidates,
            updated_raw_rows=result.updated_raw_rows,
            unresolved_symbols=result.unresolved_symbols,
        )
    except Exception as exc:
        with suppress(Exception):
            db.rollback()
        logger.exception("Daily Radar name backfill failed")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daily_radar_name_backfill_failed",
                "message": "Daily Radar name backfill failed. Check backend logs for the root cause.",
                "error_type": exc.__class__.__name__,
            },
        ) from exc


@router.post(
    "/internal/daily-radar/prepare-universe",
    response_model=DailyRadarPreparedRunResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def prepare_daily_radar_universe_endpoint(
    payload: DailyRadarPreparedRunRequest | None = None,
    db: Session = Depends(get_db),
    universe_provider: DailyRadarUniverseProvider = Depends(get_daily_radar_universe_provider),
) -> DailyRadarPreparedRunResponse:
    request = payload or DailyRadarPreparedRunRequest()
    run_date = request.run_date or _backend_today()
    existing_technical_rows = get_final_raw_data_rows_for_date(db, run_date=run_date)
    universe = select_daily_radar_universe(
        universe_provider,
        run_date=run_date,
        market=request.market,
        track_limit=50,
        technical_records=existing_technical_rows,
    )
    capped_universe = universe[: request.max_symbols]
    if not capped_universe:
        raise HTTPException(
            status_code=409,
            detail=f"Daily Radar universe is empty for {request.market} on {run_date.isoformat()}.",
        )
    selected_symbols = [entry.symbol for entry in capped_universe]
    prepared = upsert_daily_radar_prepared_run(
        db,
        run_date=run_date,
        market=request.market,
        selected_symbols=selected_symbols,
        universe=[_universe_entry_payload(entry) for entry in capped_universe],
        status="prepared",
        errors=[],
    )
    update_daily_radar_prepared_step_status(
        db,
        prepared,
        step="prepare-universe",
        status="completed",
        details={"symbol_count": len(selected_symbols)},
    )
    db.commit()
    return DailyRadarPreparedRunResponse(
        status="prepared",
        run_date=prepared.run_date,
        market=prepared.market,
        symbol_count=prepared.symbol_count,
        selected_symbols=list(prepared.selected_symbols),
        step_statuses=dict(prepared.step_statuses or {}),
        errors=list(prepared.errors or []),
    )


@router.post(
    "/internal/daily-radar/refresh-avwap",
    response_model=DailyRadarRefreshStepResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_daily_radar_avwap_endpoint(
    payload: DailyRadarRefreshStepRequest | None = None,
    db: Session = Depends(get_db),
    provider: DailyPriceProvider = Depends(get_phase1_avwap_daily_price_provider),
) -> DailyRadarRefreshStepResponse:
    request = payload or DailyRadarRefreshStepRequest()
    run_date = request.run_date or _backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    refresh_symbol_set = resolve_phase1_refresh_symbol_set(db, seed_symbols=list(prepared.selected_symbols))
    refresh_symbols = refresh_symbol_set.symbols
    result = refresh_phase1_avwap_snapshots_for_symbols(
        db,
        symbols=refresh_symbols,
        data_date=run_date,
        provider=provider,
    )
    status = "failed" if result.missing_symbols else "completed"
    missing_symbol_reasons = dict(result.missing_symbol_reasons)
    update_daily_radar_prepared_step_status(
        db,
        prepared,
        step="refresh-avwap",
        status=status,
        details={
            "symbol_count": len(refresh_symbols),
            "selected_symbol_count": len(prepared.selected_symbols),
            "records_written": len(result.fetched_symbols),
            "reused_symbols": list(result.reused_symbols),
            "fetched_symbols": list(result.fetched_symbols),
            "missing_symbols": list(result.missing_symbols),
            "missing_symbol_reasons": missing_symbol_reasons,
            "skipped_symbols": list(refresh_symbol_set.skipped_symbol_reasons),
            "skipped_symbol_reasons": dict(refresh_symbol_set.skipped_symbol_reasons),
        },
    )
    db.commit()
    return DailyRadarRefreshStepResponse(
        status=status,
        step="refresh-avwap",
        run_date=run_date,
        market=request.market,
        symbol_count=len(refresh_symbols),
        records_written=len(result.fetched_symbols),
        reused_symbols=result.reused_symbols,
        fetched_symbols=result.fetched_symbols,
        missing_symbols=result.missing_symbols,
        missing_symbol_reasons=missing_symbol_reasons,
        skipped_symbols=list(refresh_symbol_set.skipped_symbol_reasons),
        skipped_symbol_reasons=dict(refresh_symbol_set.skipped_symbol_reasons),
    )


@router.post(
    "/internal/daily-radar/refresh-lending",
    response_model=DailyRadarRefreshStepResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_daily_radar_lending_endpoint(
    payload: DailyRadarRefreshStepRequest | None = None,
    db: Session = Depends(get_db),
    provider: BackgroundChipContextProvider = Depends(get_daily_radar_background_chip_context_provider),
) -> DailyRadarRefreshStepResponse:
    return _refresh_daily_radar_context_step(
        db,
        payload=payload,
        provider=provider,
        context_type="lending",
        step="refresh-lending",
    )


@router.post(
    "/internal/daily-radar/refresh-full-margin",
    response_model=DailyRadarRefreshStepResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_daily_radar_full_margin_endpoint(
    payload: DailyRadarRefreshStepRequest | None = None,
    db: Session = Depends(get_db),
    provider: BackgroundChipContextProvider = Depends(get_daily_radar_background_chip_context_provider),
) -> DailyRadarRefreshStepResponse:
    return _refresh_daily_radar_context_step(
        db,
        payload=payload,
        provider=provider,
        context_type="full_margin",
        step="refresh-full-margin",
    )


@router.post(
    "/internal/daily-radar/refresh-ohlcv",
    response_model=DailyRadarRefreshStepResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_daily_radar_ohlcv_endpoint(
    payload: DailyRadarRefreshStepRequest | None = None,
    db: Session = Depends(get_db),
    technical_fetcher: BatchTechnicalFetcher = Depends(get_daily_radar_technical_fetcher),
) -> DailyRadarRefreshStepResponse:
    request = payload or DailyRadarRefreshStepRequest()
    run_date = request.run_date or _backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    universe = _prepared_universe_entries(prepared.universe)
    rows = ensure_daily_radar_raw_rows(
        db,
        run_date,
        list(prepared.selected_symbols),
        technical_fetcher=technical_fetcher,
        institutional_payloads_by_symbol=_institutional_payloads_by_symbol(universe, run_date=run_date),
    )
    refreshed_universe = refresh_daily_radar_universe_technical_tracks(universe, rows)
    prepared.universe = [_universe_entry_payload(entry) for entry in refreshed_universe]
    row_symbols = {row.symbol for row in rows}
    missing_symbols = [symbol for symbol in prepared.selected_symbols if symbol not in row_symbols]
    status = "failed" if missing_symbols else "completed"
    update_daily_radar_prepared_step_status(
        db,
        prepared,
        step="refresh-ohlcv",
        status=status,
        details={
            "symbol_count": len(prepared.selected_symbols),
            "records_written": len(rows),
            "missing_symbols": missing_symbols,
        },
    )
    db.commit()
    return DailyRadarRefreshStepResponse(
        status=status,
        step="refresh-ohlcv",
        run_date=run_date,
        market=request.market,
        symbol_count=len(prepared.selected_symbols),
        records_written=len(rows),
        missing_symbols=missing_symbols,
    )


@router.post(
    "/internal/daily-radar/refresh-market-context",
    response_model=DailyRadarRefreshStepResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_daily_radar_market_context_endpoint(
    payload: DailyRadarRefreshStepRequest | None = None,
    db: Session = Depends(get_db),
    market_context_provider: MarketIndexContextProvider = Depends(get_daily_radar_market_context_provider),
) -> DailyRadarRefreshStepResponse:
    request = payload or DailyRadarRefreshStepRequest()
    run_date = request.run_date or _backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    update_daily_radar_prepared_market_context(
        db,
        prepared,
        market_context=dict(market_context_provider.build(run_date=run_date, market=request.market)),
    )
    update_daily_radar_prepared_step_status(
        db,
        prepared,
        step="refresh-market-context",
        status="completed",
        details={"symbol_count": len(prepared.selected_symbols), "records_written": 1},
    )
    db.commit()
    return DailyRadarRefreshStepResponse(
        status="completed",
        step="refresh-market-context",
        run_date=run_date,
        market=request.market,
        symbol_count=len(prepared.selected_symbols),
        records_written=1,
    )


@router.post(
    "/internal/daily-radar/run-scoring",
    response_model=DailyRadarRunTriggerResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def run_daily_radar_scoring_endpoint(
    payload: DailyRadarRefreshStepRequest | None = None,
    db: Session = Depends(get_db),
) -> DailyRadarRunTriggerResponse:
    request = payload or DailyRadarRefreshStepRequest()
    run_date = request.run_date or _backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    _raise_if_required_refresh_steps_missing(prepared)
    if not prepared.market_context:
        raise HTTPException(
            status_code=409,
            detail=f"Daily Radar market context is not prepared for {request.market} on {run_date.isoformat()}.",
        )
    selected_symbols = list(prepared.selected_symbols)
    cache_rows = get_final_raw_data_rows_for_symbols(db, run_date=run_date, symbols=selected_symbols)
    if not cache_rows:
        raise HTTPException(
            status_code=409,
            detail=f"No final StockRawData rows are available for prepared Daily Radar symbols on {run_date.isoformat()}.",
        )
    background_contexts_by_symbol = get_shared_background_context_trace_by_symbol(
        db,
        symbols=selected_symbols,
        context_types=BACKGROUND_CONTEXT_TYPES,
        reference_date=run_date,
        point_in_time=True,
    )
    run = run_daily_radar(
        run_date,
        request.market,
        session=db,
        cache_rows=cache_rows,
        market_context=dict(prepared.market_context),
        background_contexts_by_symbol=background_contexts_by_symbol,
        allow_fixture_fallback=False,
    )
    prepared.status = "scored"
    db.add(prepared)
    db.commit()
    return run_trigger_response(run)


@router.post(
    "/internal/daily-radar/run",
    response_model=DailyRadarRunTriggerResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def run_daily_radar_endpoint(
    payload: DailyRadarRunRequest | None = None,
    db: Session = Depends(get_db),
    universe_provider: DailyRadarUniverseProvider = Depends(get_daily_radar_universe_provider),
    technical_fetcher: BatchTechnicalFetcher = Depends(get_daily_radar_technical_fetcher),
    market_context_provider: MarketIndexContextProvider = Depends(get_daily_radar_market_context_provider),
    background_context_provider: BackgroundChipContextProvider = Depends(get_daily_radar_background_chip_context_provider),
    phase1_avwap_provider: DailyPriceProvider = Depends(get_phase1_avwap_daily_price_provider),
) -> DailyRadarRunTriggerResponse:
    failure_stage = "request_initialization"
    try:
        request = payload or DailyRadarRunRequest()
        run_date = request.run_date or _backend_today()
        market = request.market
        failure_stage = "universe_selection"
        existing_technical_rows = get_final_raw_data_rows_for_date(db, run_date=run_date)
        universe = select_daily_radar_universe(
            universe_provider,
            run_date=run_date,
            market=market,
            track_limit=50,
            technical_records=existing_technical_rows,
        )
        if not universe:
            raise HTTPException(
                status_code=409,
                detail=f"Daily Radar universe is empty for {market} on {run_date.isoformat()}.",
            )

        selected_symbols = [entry.symbol for entry in universe]
        _refresh_phase1_avwap_for_daily_radar(
            db,
            symbols=selected_symbols,
            run_date=run_date,
            provider=phase1_avwap_provider,
        )
        if _should_refresh_daily_run_chip_context(market):
            failure_stage = "daily_chip_context_update"
            chip_context_result = update_background_chip_context_cache(
                db,
                run_date=run_date,
                market=market,
                provider=background_context_provider,
                symbols=selected_symbols,
                context_types=DAILY_RUN_REFRESH_CONTEXT_TYPES,
            )
            if chip_context_result["status"] != "completed":
                logger.warning(
                    "[DailyRadar] daily chip context refresh degraded status=%s run_date=%s market=%s symbol_count=%s context_types=%s errors=%s",
                    chip_context_result["status"],
                    run_date.isoformat(),
                    market,
                    chip_context_result["symbol_count"],
                    list(chip_context_result["context_types"]),
                    chip_context_result["errors"],
                )
        background_contexts_by_symbol = get_shared_background_context_trace_by_symbol(
            db,
            symbols=selected_symbols,
            context_types=BACKGROUND_CONTEXT_TYPES,
            reference_date=run_date,
            point_in_time=True,
        )
        institutional_payloads_by_symbol = _institutional_payloads_by_symbol(universe, run_date=run_date)
        failure_stage = "raw_data_backfill"
        cache_rows = ensure_daily_radar_raw_rows(
            db,
            run_date,
            selected_symbols,
            technical_fetcher=technical_fetcher,
            institutional_payloads_by_symbol=institutional_payloads_by_symbol,
        )
        if not cache_rows:
            cache_rows = get_final_raw_data_rows_for_symbols(db, run_date=run_date, symbols=selected_symbols)
        if not cache_rows:
            raise HTTPException(
                status_code=409,
                detail=f"No final StockRawData rows are available for selected Daily Radar symbols on {run_date.isoformat()}.",
            )
        failure_stage = "market_context"
        market_context = dict(market_context_provider.build(run_date=run_date, market=market))
        failure_stage = "daily_radar_service"
        run = run_daily_radar(
            run_date,
            market,
            session=db,
            cache_rows=cache_rows,
            market_context=market_context,
            background_contexts_by_symbol=background_contexts_by_symbol,
            allow_fixture_fallback=False,
        )
        db.commit()
        return run_trigger_response(run)
    except HTTPException:
        raise
    except Exception as exc:
        with suppress(Exception):
            db.rollback()
        logger.exception("Daily Radar run failed before completion")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daily_radar_run_failed",
                "message": "Daily Radar run failed before completion. Check backend logs for the root cause.",
                "stage": failure_stage,
                "error_type": exc.__class__.__name__,
            },
        ) from exc


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
    run_date = request.run_date or _backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    result = update_background_chip_context_cache(
        db,
        run_date=run_date,
        market=request.market,
        provider=provider,
        symbols=list(prepared.selected_symbols),
        context_types=[context_type],
        reuse_same_day_fresh=True,
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


def _universe_entry_payload(entry: DailyRadarUniverseEntry) -> dict[str, Any]:
    return {
        "symbol": entry.symbol,
        "rank": entry.rank,
        "primary_track": entry.primary_track,
        "tracks": list(entry.tracks),
        "same_day_rank": entry.same_day_rank,
        "same_day_score": entry.same_day_score,
        "recent_accumulation_rank": entry.recent_accumulation_rank,
        "recent_accumulation_score": entry.recent_accumulation_score,
        "track_metrics": {track: dict(metrics) for track, metrics in entry.track_metrics.items()},
    }


def _prepared_universe_entries(payloads: list[dict[str, Any]]) -> list[DailyRadarUniverseEntry]:
    entries: list[DailyRadarUniverseEntry] = []
    for payload in payloads:
        entries.append(
            DailyRadarUniverseEntry(
                symbol=str(payload["symbol"]),
                rank=int(payload["rank"]),
                primary_track=payload["primary_track"],
                tracks=tuple(payload.get("tracks") or (payload["primary_track"],)),
                same_day_rank=_optional_int(payload.get("same_day_rank")),
                same_day_score=_optional_float(payload.get("same_day_score")),
                recent_accumulation_rank=_optional_int(payload.get("recent_accumulation_rank")),
                recent_accumulation_score=_optional_float(payload.get("recent_accumulation_score")),
                track_metrics={
                    str(track): dict(metrics)
                    for track, metrics in _mapping(payload.get("track_metrics")).items()
                    if isinstance(metrics, dict)
                },
            )
        )
    return entries


@router.post(
    "/internal/daily-radar/chip-context/update",
    response_model=DailyRadarChipContextUpdateResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def update_daily_radar_chip_context_endpoint(
    payload: DailyRadarChipContextUpdateRequest | None = None,
    db: Session = Depends(get_db),
    provider: BackgroundChipContextProvider = Depends(get_daily_radar_background_chip_context_provider),
) -> DailyRadarChipContextUpdateResponse:
    request = payload or DailyRadarChipContextUpdateRequest()
    run_date = request.run_date or _backend_today()
    logger.info(
        "[DailyRadarChipContext] update started run_date=%s market=%s context_types=%s requested_symbol_count=%s provider=%s",
        run_date.isoformat(),
        request.market,
        list(request.context_types or BACKGROUND_CONTEXT_TYPES),
        len(request.symbols) if request.symbols is not None else "latest_run",
        provider.__class__.__name__,
    )
    result = update_background_chip_context_cache(
        db,
        run_date=run_date,
        market=request.market,
        provider=provider,
        symbols=request.symbols,
        context_types=request.context_types,
    )
    db.commit()
    logger.info(
        "[DailyRadarChipContext] update completed status=%s run_date=%s market=%s symbol_count=%s context_types=%s records_written=%s errors_count=%s",
        result["status"],
        run_date.isoformat(),
        result["market"],
        result["symbol_count"],
        list(result["context_types"]),
        result["records_written"],
        len(result["errors"]),
    )
    return DailyRadarChipContextUpdateResponse(
        status=result["status"],
        run_date=run_date,
        market=str(result["market"]),
        symbol_count=int(result["symbol_count"]),
        context_types=list(result["context_types"]),
        records_written=int(result["records_written"]),
        errors=list(result["errors"]),
    )


@router.post(
    "/internal/daily-radar/forward-validation/run",
    response_model=DailyRadarForwardValidationRunResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def run_daily_radar_forward_validation_endpoint(
    payload: DailyRadarForwardValidationRunRequest | None = None,
    db: Session = Depends(get_db),
) -> DailyRadarForwardValidationRunResponse:
    request = payload or DailyRadarForwardValidationRunRequest()
    as_of_date = request.as_of_date or _backend_today()
    start_date = request.start_date
    if request.mode == "due" and start_date is None:
        start_date = default_due_start_date(as_of_date, max(request.windows or list(DEFAULT_FORWARD_WINDOWS)))
    candidates = forward_validation_candidates_from_runs(
        db,
        market=request.market,
        start_date=start_date,
        end_date=request.end_date or as_of_date,
    )
    symbols = {str(candidate["symbol"]) for candidate in candidates}
    candidate_record_dates = [
        parsed_date
        for candidate in candidates
        if (parsed_date := parse_date(candidate.get("record_date"))) is not None
    ]
    price_start_date = min(
        candidate_record_dates,
        default=start_date or as_of_date,
    )
    price_series = load_price_series_from_raw_data(
        db,
        symbols=sorted(symbols | {request.benchmark_symbol}),
        start_date=price_start_date,
        end_date=as_of_date,
    )
    windows_by_candidate = None
    if request.mode == "due":
        windows_by_candidate = due_windows_by_candidate(
            candidates,
            as_of_date=as_of_date,
            windows=request.windows,
            price_series_by_symbol={symbol: price_series.get(symbol, []) for symbol in symbols},
            benchmark_prices=price_series.get(request.benchmark_symbol, []),
        )
    evaluation = build_forward_validation_report(
        candidates,
        price_series_by_symbol={symbol: price_series.get(symbol, []) for symbol in symbols},
        benchmark_prices=price_series.get(request.benchmark_symbol, []),
        market=request.market,
        sample_source="production_db",
        as_of_date=as_of_date,
        windows=request.windows,
        benchmark_symbol=request.benchmark_symbol,
        windows_by_candidate=windows_by_candidate,
    )
    write_summary = upsert_forward_validation_results(db, evaluation.outcomes)
    db.commit()
    return DailyRadarForwardValidationRunResponse(
        status="completed",
        mode=request.mode,
        market=request.market,
        as_of_date=as_of_date,
        candidate_count=len(candidates),
        records_written=write_summary["records_written"],
        validated_count=write_summary["validated_count"],
        skipped_count=write_summary["skipped_count"],
        report=evaluation.report,
    )


@router.post(
    "/internal/daily-radar/rule-review/monthly",
    response_model=DailyRadarMonthlyRuleReviewResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def run_daily_radar_monthly_rule_review_endpoint(
    payload: DailyRadarMonthlyRuleReviewRequest,
    db: Session = Depends(get_db),
) -> DailyRadarMonthlyRuleReviewResponse:
    report = build_monthly_rule_review_report(
        db,
        market=payload.market,
        year=payload.year,
        month=payload.month,
        validation_version=payload.validation_version,
        min_sample_count=payload.min_sample_count,
    )
    return DailyRadarMonthlyRuleReviewResponse(
        status="completed",
        market=payload.market,
        month=f"{payload.year:04d}-{payload.month:02d}",
        report_json=report.json_report,
        report_markdown=report.markdown_report,
    )


@router.get("/daily-radar/latest", response_model=DailyRadarRunResponse, response_model_exclude_none=True)
def get_latest_daily_radar_endpoint(
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> DailyRadarRunResponse:
    run = get_latest_daily_radar_run(db, market=market)
    if run is None:
        raise HTTPException(status_code=404, detail="No public Daily Radar run is available.")
    return public_run_response(run, bucket=bucket, limit=limit)


@router.get("/daily-radar/symbol/{symbol}", response_model=list[dict[str, Any]])
def get_daily_radar_symbol_history_endpoint(
    symbol: str,
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    lookback_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    history = get_symbol_candidate_history(
        db,
        symbols=[symbol],
        before_date=_backend_today() + timedelta(days=1),
        lookback_days=lookback_days,
        market=market,
    )
    filtered = [history_response(item) for item in history if matches_bucket(item, bucket)]
    return filtered[:limit]


@router.get("/daily-radar/{run_date}", response_model=DailyRadarRunResponse, response_model_exclude_none=True)
def get_daily_radar_by_date_endpoint(
    run_date: date,
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> DailyRadarRunResponse:
    run = get_daily_radar_run_by_date(db, run_date=run_date, market=market)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"No public Daily Radar run is available for {run_date.isoformat()}.",
        )
    return public_run_response(run, bucket=bucket, limit=limit)


def _backend_today() -> date:
    return today_taipei()


def _should_refresh_daily_run_chip_context(market: str) -> bool:
    return market.upper() == "TW"


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


def _institutional_payload(entry: DailyRadarUniverseEntry, *, run_date: date) -> dict[str, Any]:
    same_day_metrics = dict(entry.track_metrics.get("same_day_institutional") or {})
    recent_metrics = dict(entry.track_metrics.get("recent_accumulation") or {})
    flat_payload: dict[str, Any] = {
        "flow_label": "institutional_accumulation",
        "flow_state": _flow_state(entry),
        "universe_primary_track": entry.primary_track,
        "institutional_universe_tracks": list(entry.tracks),
        "universe_track_metrics": {track: dict(metrics) for track, metrics in entry.track_metrics.items()},
        "same_day_rank": entry.same_day_rank,
        "recent_accumulation_rank": entry.recent_accumulation_rank,
        "scores": _score_payload(entry),
        "source_provider": "daily_radar_universe",
        "data_dates": {"institutional_flow": _latest_institutional_date(entry, run_date=run_date)},
    }
    _merge_same_day_metrics(flat_payload, same_day_metrics)
    _merge_recent_metrics(flat_payload, recent_metrics)
    return flat_payload | {"institutional_flow": dict(flat_payload)}


def _flow_state(entry: DailyRadarUniverseEntry) -> str:
    recent_metrics = entry.track_metrics.get("recent_accumulation") or {}
    recent_flow_state = recent_metrics.get("flow_state")
    if recent_flow_state is not None:
        return str(recent_flow_state)
    recent_positive_days = _int_metric(recent_metrics.get("consecutive_buy_days"))
    if recent_positive_days is not None:
        return "consistent_accumulation" if recent_positive_days >= 2 else "weak_confirmation"

    same_day_metrics = entry.track_metrics.get("same_day_institutional") or {}
    same_day_flow_state = same_day_metrics.get("flow_state")
    if same_day_flow_state is not None:
        return str(same_day_flow_state)
    if not any(track in {"same_day_institutional", "recent_accumulation"} for track in entry.tracks):
        return "technical_trigger"
    return "weak_confirmation"


def _score_payload(entry: DailyRadarUniverseEntry) -> dict[str, float]:
    scores: dict[str, float] = {}
    _add_score(scores, "same_day_institutional", entry.same_day_score)
    _add_score(scores, "recent_accumulation", entry.recent_accumulation_score)
    for track, metrics in entry.track_metrics.items():
        _add_score(scores, track, metrics.get("score"))
    return scores


def _add_score(scores: dict[str, float], key: str, value: float | None) -> None:
    if value is not None:
        scores[key] = float(value)


def _merge_same_day_metrics(payload: dict[str, Any], metrics: dict[str, Any]) -> None:
    same_day_actor = _normalized_actor(metrics.get("actor"))
    raw_same_day_net_buy = metrics.get("net_buy")
    same_day_net_buy = _float_metric(raw_same_day_net_buy)
    _add_payload_metric(payload, "same_day_actor", metrics.get("actor"))
    _add_payload_metric(payload, "same_day_net_buy", raw_same_day_net_buy)
    _add_payload_metric(payload, "same_day_concentration", metrics.get("concentration"))
    _add_payload_metric(payload, "same_day_source_dates", _source_dates(metrics))
    if same_day_net_buy is not None:
        if same_day_actor == "foreign":
            payload["foreign_net_shares"] = same_day_net_buy
        elif same_day_actor == "trust":
            payload["investment_trust_net_shares"] = same_day_net_buy


def _merge_recent_metrics(payload: dict[str, Any], metrics: dict[str, Any]) -> None:
    recent_actor = _normalized_actor(metrics.get("actor"))
    positive_days = _int_metric(metrics.get("consecutive_buy_days"))
    if positive_days is not None:
        payload["consecutive_buy_days"] = positive_days
        payload["consecutive_positive_days"] = positive_days

    cumulative_net_buy = _float_metric(metrics.get("cumulative_net_buy"))
    if cumulative_net_buy is not None:
        payload["cumulative_net_buy"] = cumulative_net_buy
        payload["net_buy_cumulative"] = cumulative_net_buy
        payload["three_party_net_shares"] = cumulative_net_buy
        if recent_actor == "foreign":
            payload["foreign_net_shares"] = cumulative_net_buy
        elif recent_actor == "trust":
            payload["investment_trust_net_shares"] = cumulative_net_buy

    _add_payload_metric(payload, "recent_actor", metrics.get("actor"))
    raw_recent_concentration = metrics.get("concentration")
    recent_concentration = _float_metric(raw_recent_concentration)
    _add_payload_metric(payload, "recent_concentration", raw_recent_concentration)
    _add_payload_metric(payload, "net_flow_to_avg_volume", recent_concentration)
    _add_payload_metric(payload, "recent_source_dates", _source_dates(metrics))


def _normalized_actor(value: Any) -> str | None:
    if value is None:
        return None
    actor = str(value).strip().lower()
    if actor in {"foreign", "trust", "institutional", "mixed"}:
        return actor
    return None


def _add_payload_metric(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


def _latest_institutional_date(entry: DailyRadarUniverseEntry, *, run_date: date) -> str:
    source_dates: list[str] = []
    for metrics in entry.track_metrics.values():
        source_dates.extend(_source_dates(metrics) or [])
    return max(source_dates) if source_dates else run_date.isoformat()


def _source_dates(metrics: dict[str, Any]) -> list[str] | None:
    raw_dates = metrics.get("source_dates")
    if not isinstance(raw_dates, (list, tuple)):
        return None
    source_dates = [str(value) for value in raw_dates if value]
    return source_dates or None


def _int_metric(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_metric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


__all__ = ["router"]
