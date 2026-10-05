"""Prepare the Radar universe and refresh the required source datasets."""

from __future__ import annotations

from contextlib import suppress
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar import dependencies
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.background_context import BackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.dependencies import (
    get_daily_radar_background_chip_context_provider,
    get_daily_radar_market_context_provider,
    get_daily_radar_technical_fetcher,
    get_daily_radar_universe_provider,
    get_phase1_avwap_daily_price_provider,
    get_taiwan_institutional_report_provider,
    get_taiwan_market_bar_provider,
)
from ai_stock_sentinel.daily_radar.institutional_flow_service import (
    InstitutionalReportProvider,
    refresh_taiwan_institutional_flows,
)
from ai_stock_sentinel.daily_radar.institutional_payloads import (
    _prepared_universe_entries,
    _universe_entry_payload,
)
from ai_stock_sentinel.daily_radar.institutional_universe_provider import (
    InstitutionalUniverseProviderError,
)
from ai_stock_sentinel.daily_radar.market_bar_provider import OfficialTaiwanMarketBarProvider
from ai_stock_sentinel.daily_radar.market_bar_service import refresh_taiwan_market_bars
from ai_stock_sentinel.daily_radar.market_context import (
    MarketIndexContextProvider,
    market_context_refresh_error,
)
from ai_stock_sentinel.daily_radar.pipeline_support import (
    _capped_daily_radar_universe,
    _full_margin_contexts_by_symbol,
    _institutional_payloads_by_symbol,
    _prepared_run_or_404,
    _refresh_daily_radar_context_step,
    _required_institutional_archive_details,
    _supported_daily_radar_symbols,
    _universe_provider_error_type,
    _universe_provider_name,
)
from ai_stock_sentinel.daily_radar.raw_data import (
    BatchTechnicalFetcher,
    ensure_daily_radar_raw_rows,
    insufficient_history_daily_radar_raw_rows,
    reusable_daily_radar_raw_rows,
)
from ai_stock_sentinel.daily_radar.repository import (
    get_final_raw_data_rows_for_date,
    update_daily_radar_prepared_market_context,
    update_daily_radar_prepared_step_status,
    upsert_daily_radar_prepared_run,
)
from ai_stock_sentinel.daily_radar.market_exploration import load_market_exploration, MarketExplorationReadinessError, attach_official_turnover
from ai_stock_sentinel.daily_radar.universe import merge_discovery_universe
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarInstitutionalFlowsRefreshRequest,
    DailyRadarInstitutionalFlowsRefreshResponse,
    DailyRadarMarketBarsRefreshRequest,
    DailyRadarMarketBarsRefreshResponse,
    DailyRadarPreparedRunRequest,
    DailyRadarPreparedRunResponse,
    DailyRadarRefreshStepRequest,
    DailyRadarRefreshStepResponse,
)
from ai_stock_sentinel.daily_radar.universe import (
    DailyRadarUniverseProvider,
    refresh_daily_radar_universe_technical_tracks,
    select_daily_radar_universe,
)
from ai_stock_sentinel.db.models import StockRawData
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.phase1_avwap.service import (
    DailyPriceProvider,
    refresh_phase1_avwap_snapshots_for_symbols,
)
from ai_stock_sentinel.phase1_avwap.universe import resolve_phase1_refresh_symbol_set


router = APIRouter()


logger = logging.getLogger(__name__)


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
    run_date = request.run_date or dependencies._backend_today()
    institutional_archive = _required_institutional_archive_details(
        db,
        run_date=run_date,
    )
    existing_technical_rows = get_final_raw_data_rows_for_date(db, run_date=run_date)
    try:
        discoveries, exploration_audit = load_market_exploration(db, run_date=run_date)
    except MarketExplorationReadinessError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "market_exploration_archive_incomplete", "run_date": run_date.isoformat(),
            "missing_markets": exc.missing_markets,
        }) from exc
    try:
        universe = select_daily_radar_universe(
            universe_provider,
            run_date=run_date,
            market=request.market,
            track_limit=50,
            technical_records=existing_technical_rows,
        )
    except Exception as exc:
        with suppress(Exception):
            db.rollback()
        error_type = _universe_provider_error_type(exc)
        provider_name = _universe_provider_name(universe_provider)
        if isinstance(exc, InstitutionalUniverseProviderError):
            logger.warning(
                "Daily Radar universe provider failed provider=twse_rwd report_id=%s query_date=%s error_type=%s",
                exc.report_id,
                exc.query_date.isoformat(),
                error_type,
            )
        else:
            logger.exception(
                "Daily Radar universe selection failed provider=%s error_type=%s",
                provider_name,
                error_type,
            )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daily_radar_universe_provider_failed",
                "message": "Daily Radar universe provider request failed.",
                "error_type": error_type,
                "provider": provider_name,
            },
        ) from exc
    capped_universe = _capped_daily_radar_universe(
        merge_discovery_universe(universe, discoveries),
        max_symbols=request.max_symbols,
    )
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
        step="refresh-institutional-flows",
        status="completed",
        details=institutional_archive,
    )
    update_daily_radar_prepared_step_status(
        db,
        prepared,
        step="prepare-universe",
        status="completed",
        details={"symbol_count": len(selected_symbols), "market_exploration": exploration_audit},
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
    run_date = request.run_date or dependencies._backend_today()
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
    "/internal/daily-radar/refresh-institutional-flows",
    response_model=DailyRadarInstitutionalFlowsRefreshResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_daily_radar_institutional_flows_endpoint(
    payload: DailyRadarInstitutionalFlowsRefreshRequest | None = None,
    db: Session = Depends(get_db),
    provider: InstitutionalReportProvider = Depends(
        get_taiwan_institutional_report_provider
    ),
) -> DailyRadarInstitutionalFlowsRefreshResponse:
    request = payload or DailyRadarInstitutionalFlowsRefreshRequest()
    run_date = request.run_date or dependencies._backend_today()
    try:
        result = refresh_taiwan_institutional_flows(
            db,
            trade_date=run_date,
            provider=provider,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Taiwan institutional flow archive refresh failed")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "institutional_flow_archive_refresh_failed",
                "error_type": exc.__class__.__name__,
            },
        ) from exc
    return DailyRadarInstitutionalFlowsRefreshResponse(
        status=result["status"],
        run_date=run_date,
        market=request.market,
        records_written=result["records_written"],
        markets_attempted=result["markets_attempted"],
        markets_completed=result["markets_completed"],
        snapshots=result["snapshots"],
        errors=result["errors"],
    )


@router.post(
    "/internal/daily-radar/refresh-market-bars",
    response_model=DailyRadarMarketBarsRefreshResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_daily_radar_market_bars_endpoint(
    payload: DailyRadarMarketBarsRefreshRequest | None = None,
    db: Session = Depends(get_db),
    provider: OfficialTaiwanMarketBarProvider = Depends(get_taiwan_market_bar_provider),
) -> DailyRadarMarketBarsRefreshResponse:
    request = payload or DailyRadarMarketBarsRefreshRequest()
    if request.start_date is not None or request.end_date is not None:
        if request.start_date is None or request.end_date is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "market_bar_range_incomplete"},
            )
        start_date = request.start_date
        end_date = request.end_date
    else:
        start_date = end_date = request.run_date or dependencies._backend_today()
    try:
        result = refresh_taiwan_market_bars(
            db,
            start_date=start_date,
            end_date=end_date,
            provider=provider,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={"code": "market_bar_range_invalid", "message": str(exc)},
        ) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Taiwan market bar refresh failed")
        raise HTTPException(
            status_code=503,
            detail={"code": "market_bar_refresh_failed", "error_type": exc.__class__.__name__},
        ) from exc
    return DailyRadarMarketBarsRefreshResponse(
        status=result["status"],
        start_date=start_date,
        end_date=end_date,
        market=request.market,
        records_written=result["records_written"],
        dates_attempted=result["dates_attempted"],
        dates_with_data=result["dates_with_data"],
        skipped_dates=result["skipped_dates"],
        errors=result["errors"],
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
    market_bar_provider: OfficialTaiwanMarketBarProvider = Depends(get_taiwan_market_bar_provider),
) -> DailyRadarRefreshStepResponse:
    request = payload or DailyRadarRefreshStepRequest()
    run_date = request.run_date or dependencies._backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    universe = _prepared_universe_entries(prepared.universe)
    selected_symbols, skipped_symbol_reasons = _supported_daily_radar_symbols(prepared.selected_symbols)
    if skipped_symbol_reasons:
        skipped_symbols = set(skipped_symbol_reasons)
        universe = [entry for entry in universe if entry.symbol not in skipped_symbols]
        prepared.selected_symbols = selected_symbols
        prepared.symbol_count = len(selected_symbols)
    if os.getenv("DAILY_RADAR_TW_OHLCV_PROVIDER_MODE", "yfinance_only") != "yfinance_only":
        refresh_taiwan_market_bars(
            db,
            start_date=run_date,
            end_date=run_date,
            provider=market_bar_provider,
        )
        db.flush()
    rows = ensure_daily_radar_raw_rows(
        db,
        run_date,
        selected_symbols,
        technical_fetcher=technical_fetcher,
        institutional_payloads_by_symbol=_institutional_payloads_by_symbol(universe, run_date=run_date),
        margin_contexts_by_symbol=_full_margin_contexts_by_symbol(
            db,
            symbols=selected_symbols,
            run_date=run_date,
        ),
    )
    attach_official_turnover(db, rows, run_date=run_date)
    refreshed_universe = refresh_daily_radar_universe_technical_tracks(universe, rows)
    prepared.universe = [_universe_entry_payload(entry) for entry in refreshed_universe]
    row_symbols = {row.symbol for row in rows}
    # 最新日線存在但歷史不足屬於候選資格問題，保留原 universe 交給 prefilter 排除。
    stored_rows = db.scalars(select(StockRawData).where(
        StockRawData.record_date == run_date,
        StockRawData.symbol.in_(selected_symbols),
    )).all()
    short_history_symbols = {
        row.symbol for row in insufficient_history_daily_radar_raw_rows(stored_rows, run_date=run_date)
        if row.raw_data_is_final
    }
    skipped_symbol_reasons.update({symbol: "insufficient_technical_history"
                                   for symbol in selected_symbols if symbol in short_history_symbols})
    missing_symbols = [symbol for symbol in selected_symbols
                       if symbol not in row_symbols and symbol not in short_history_symbols]
    structurally_reusable_symbols = {
        row.symbol
        for row in reusable_daily_radar_raw_rows(stored_rows)
    }
    missing_symbol_reasons = {
        symbol: (
            "technical_data_dates_not_run_date"
            if symbol in structurally_reusable_symbols
            else "technical_record_missing_or_incomplete"
        )
        for symbol in missing_symbols
    }
    status = "failed" if missing_symbols else "completed"
    update_daily_radar_prepared_step_status(
        db,
        prepared,
        step="refresh-ohlcv",
        status=status,
        details={
            "symbol_count": len(selected_symbols),
            "records_written": len(rows),
            "missing_symbols": missing_symbols,
            "missing_symbol_reasons": missing_symbol_reasons,
            "skipped_symbols": list(skipped_symbol_reasons),
            "skipped_symbol_reasons": skipped_symbol_reasons,
        },
    )
    db.commit()
    return DailyRadarRefreshStepResponse(
        status=status,
        step="refresh-ohlcv",
        run_date=run_date,
        market=request.market,
        symbol_count=len(selected_symbols),
        records_written=len(rows),
        missing_symbols=missing_symbols,
        missing_symbol_reasons=missing_symbol_reasons,
        skipped_symbols=list(skipped_symbol_reasons),
        skipped_symbol_reasons=skipped_symbol_reasons,
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
    run_date = request.run_date or dependencies._backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    market_context = dict(
        market_context_provider.build(run_date=run_date, market=request.market)
    )
    error = market_context_refresh_error(market_context, run_date=run_date)
    reused_existing_context = False
    if error is not None and prepared.market_context:
        reused_existing_context = (
            market_context_refresh_error(dict(prepared.market_context), run_date=run_date) is None
        )
    status = "completed" if error is None or reused_existing_context else "failed"
    errors = [] if status == "completed" else [error]
    records_written = 0 if reused_existing_context else 1
    if not reused_existing_context:
        update_daily_radar_prepared_market_context(
            db,
            prepared,
            market_context=market_context,
            status=(
                "market_context_ready"
                if status == "completed"
                else f"market_context_{error['freshness']}"
            ),
        )
    update_daily_radar_prepared_step_status(
        db,
        prepared,
        step="refresh-market-context",
        status=status,
        details={
            "symbol_count": len(prepared.selected_symbols),
            "records_written": records_written,
            "reused_existing_context": reused_existing_context,
            "provider_trace": market_context.get("provider_trace", {}),
            "errors": errors,
        },
    )
    db.commit()
    return DailyRadarRefreshStepResponse(
        status=status,
        step="refresh-market-context",
        run_date=run_date,
        market=request.market,
        symbol_count=len(prepared.selected_symbols),
        records_written=records_written,
        errors=errors,
    )
