"""Refresh managed raw data and supplemental AI research evidence."""

from __future__ import annotations

from datetime import timedelta
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar import dependencies
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.background_context import (
    BackgroundChipContextProvider,
    same_day_background_context_is_reusable,
    update_background_chip_context_cache,
)
from ai_stock_sentinel.daily_radar.dependencies import (
    DAILY_RUN_REFRESH_CONTEXT_TYPES,
    get_daily_radar_background_chip_context_provider,
    get_daily_radar_fundamental_provider,
    get_daily_radar_institutional_evidence_provider,
    get_daily_radar_technical_fetcher,
)
from ai_stock_sentinel.daily_radar.institutional_evidence import (
    InstitutionalEvidenceProvider,
    InstitutionalEvidenceResult,
    OfficialInstitutionalEvidenceProvider,
    cached_daily_rows_from_raw_rows,
)
from ai_stock_sentinel.daily_radar.institutional_payloads import _prepared_universe_entries
from ai_stock_sentinel.daily_radar.managed_raw_data import (
    MANAGED_RAW_DATA_SYMBOL_LIMIT,
    select_managed_raw_data_symbols,
)
from ai_stock_sentinel.daily_radar.pipeline_support import (
    _PreparedBackgroundContextProvider,
    _PreparedBatchTechnicalFetcher,
    _add_institutional_volume_ratios,
    _ai_evidence_missing_by_lane,
    _finish_managed_raw_data_refresh,
    _full_margin_contexts_by_symbol,
    _industry_classifications_by_symbol,
    _institutional_payloads_by_symbol,
    _managed_selected_overlap_count,
    _materialize_ai_business_fundamentals,
    _materialize_industry_classifications,
    _merge_institutional_evidence,
    _prefetch_ai_background_evidence,
    _prepared_run_or_404,
)
from ai_stock_sentinel.daily_radar.raw_data import (
    BatchTechnicalFetcher,
    current_daily_radar_raw_rows,
    ensure_daily_radar_raw_rows,
)
from ai_stock_sentinel.daily_radar.repository import (
    get_daily_radar_prepared_run,
    get_final_raw_data_rows_for_date,
    get_final_raw_data_rows_for_symbols,
    get_shared_background_context_rows,
    update_daily_radar_prepared_step_status,
)
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarManagedRawDataRefreshRequest,
    DailyRadarManagedRawDataRefreshResponse,
    DailyRadarRefreshStepRequest,
    DailyRadarRefreshStepResponse,
)
from ai_stock_sentinel.daily_radar.universe import is_daily_radar_supported_symbol
from ai_stock_sentinel.data_sources.fundamental.interface import PointInTimeFundamentalProvider
from ai_stock_sentinel.db.models import StockRawData
from ai_stock_sentinel.db.session import get_db


router = APIRouter()


logger = logging.getLogger(__name__)


@router.post(
    "/internal/daily-radar/refresh-managed-raw-data",
    response_model=DailyRadarManagedRawDataRefreshResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_daily_radar_managed_raw_data_endpoint(
    payload: DailyRadarManagedRawDataRefreshRequest | None = None,
    db: Session = Depends(get_db),
    technical_fetcher: BatchTechnicalFetcher = Depends(
        get_daily_radar_technical_fetcher
    ),
) -> DailyRadarManagedRawDataRefreshResponse:
    request = payload or DailyRadarManagedRawDataRefreshRequest()
    run_date = request.run_date or dependencies._backend_today()
    selection = select_managed_raw_data_symbols(
        db,
        run_date=run_date,
        max_symbols=MANAGED_RAW_DATA_SYMBOL_LIMIT,
    )
    prepared = get_daily_radar_prepared_run(
        db,
        run_date=run_date,
        market=request.market,
    )
    selected_overlap_count = _managed_selected_overlap_count(
        selection,
        prepared.selected_symbols if prepared is not None else (),
    )
    existing_rows = get_final_raw_data_rows_for_symbols(
        db,
        run_date=run_date,
        symbols=selection.symbols,
    )
    reusable_before = current_daily_radar_raw_rows(existing_rows, run_date=run_date)
    # Industry metadata is public network I/O. Release the read transaction
    # before resolving it, then apply the in-memory mapping to persisted rows.
    db.rollback()
    industries_by_symbol = _industry_classifications_by_symbol(selection.symbols)
    try:
        refreshed_rows = ensure_daily_radar_raw_rows(
            db,
            run_date,
            selection.symbols,
            technical_fetcher=technical_fetcher,
        )
        _materialize_industry_classifications(
            refreshed_rows,
            resolver=industries_by_symbol.get,
        )
    except Exception as exc:
        logger.error(
            "Managed raw-data refresh failed; error_type=%s",
            exc.__class__.__name__,
        )
        db.rollback()
        prepared = get_daily_radar_prepared_run(
            db,
            run_date=run_date,
            market=request.market,
        )
        return _finish_managed_raw_data_refresh(
            db,
            prepared=prepared,
            request=request,
            run_date=run_date,
            selection=selection,
            selected_overlap_count=selected_overlap_count,
            status="failed",
            error_codes=["managed_raw_data_refresh_failed"],
        )

    missing_record_count = len(selection.symbols) - len(refreshed_rows)
    return _finish_managed_raw_data_refresh(
        db,
        prepared=prepared,
        request=request,
        run_date=run_date,
        selection=selection,
        selected_overlap_count=selected_overlap_count,
        status="failed" if missing_record_count else "completed",
        reused_record_count=len(reusable_before),
        records_written=max(0, len(refreshed_rows) - len(reusable_before)),
        missing_record_count=missing_record_count,
        error_codes=(
            ["managed_raw_data_incomplete"] if missing_record_count else []
        ),
    )


@router.post(
    "/internal/daily-radar/refresh-ai-evidence",
    response_model=DailyRadarRefreshStepResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def refresh_daily_radar_ai_evidence_endpoint(
    payload: DailyRadarRefreshStepRequest | None = None,
    db: Session = Depends(get_db),
    technical_fetcher: BatchTechnicalFetcher = Depends(get_daily_radar_technical_fetcher),
    institutional_provider: InstitutionalEvidenceProvider = Depends(
        get_daily_radar_institutional_evidence_provider
    ),
    fundamental_provider: PointInTimeFundamentalProvider = Depends(
        get_daily_radar_fundamental_provider
    ),
    background_provider: BackgroundChipContextProvider = Depends(
        get_daily_radar_background_chip_context_provider
    ),
) -> DailyRadarRefreshStepResponse:
    """Materialize evidence for the complete AI raw pool without changing scoring membership."""

    request = payload or DailyRadarRefreshStepRequest()
    run_date = request.run_date or dependencies._backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    pool_rows = [
        row
        for row in get_final_raw_data_rows_for_date(db, run_date=run_date)
        if is_daily_radar_supported_symbol(row.symbol)
    ]
    symbols = [row.symbol for row in pool_rows]
    if not symbols:
        raise HTTPException(
            status_code=409,
            detail={"code": "daily_radar_ai_raw_pool_empty", "run_date": run_date.isoformat()},
        )

    selected_symbols = set(prepared.selected_symbols or [])
    canonical_payloads = _institutional_payloads_by_symbol(
        _prepared_universe_entries(prepared.universe),
        run_date=run_date,
    )
    reusable_symbols = {
        row.symbol
        for row in current_daily_radar_raw_rows(pool_rows, run_date=run_date)
    }
    cached_institutional_rows = db.scalars(
        select(StockRawData).where(
            StockRawData.record_date >= run_date - timedelta(days=10),
            StockRawData.record_date <= run_date,
            StockRawData.raw_data_is_final.is_(True),
            StockRawData.symbol.in_(symbols),
        )
    ).all()
    cached_daily_rows = cached_daily_rows_from_raw_rows(cached_institutional_rows)
    missing_technical_symbols = [symbol for symbol in symbols if symbol not in reusable_symbols]
    fresh_background_pairs = {
        (row.symbol, row.context_type)
        for row in get_shared_background_context_rows(
            db,
            symbols=symbols,
            context_types=DAILY_RUN_REFRESH_CONTEXT_TYPES,
            reference_date=run_date,
            point_in_time=True,
        )
        if same_day_background_context_is_reusable(row, run_date=run_date)
    }
    # Do not hold a database transaction open while external providers run.
    db.rollback()
    industries_by_symbol = _industry_classifications_by_symbol(symbols)
    try:
        if isinstance(institutional_provider, OfficialInstitutionalEvidenceProvider):
            evidence_result = institutional_provider.fetch(
                symbols,
                run_date=run_date,
                cached_daily_rows=cached_daily_rows,
            )
        else:
            evidence_result = institutional_provider.fetch(symbols, run_date=run_date)
    except Exception as exc:
        logger.exception(
            "Daily Radar institutional evidence provider failed run_date=%s market=%s",
            run_date,
            request.market,
        )
        evidence_result = InstitutionalEvidenceResult(
            errors=[
                {
                    "code": "institutional_evidence_provider_failed",
                    "message": str(exc),
                    "error_type": exc.__class__.__name__,
                }
            ]
        )
    technical_payloads = technical_fetcher.fetch(missing_technical_symbols, run_date=run_date)
    background_payloads, background_fetch_errors = _prefetch_ai_background_evidence(
        background_provider,
        symbols=symbols,
        context_types=DAILY_RUN_REFRESH_CONTEXT_TYPES,
        fresh_pairs=fresh_background_pairs,
        run_date=run_date,
        market=request.market,
    )
    institutional_payloads = {
        symbol: dict(evidence_result.payloads_by_symbol[symbol])
        for symbol in symbols
        if symbol in evidence_result.payloads_by_symbol
    }
    # Canonical selected-universe evidence remains owned by prepare-universe.
    for symbol, canonical in canonical_payloads.items():
        institutional_payloads[symbol] = _merge_institutional_evidence(
            institutional_payloads.get(symbol),
            canonical,
        )

    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    pool_rows = [
        row
        for row in get_final_raw_data_rows_for_date(db, run_date=run_date)
        if is_daily_radar_supported_symbol(row.symbol)
    ]

    background_result = update_background_chip_context_cache(
        db,
        run_date=run_date,
        market=request.market,
        provider=_PreparedBackgroundContextProvider(background_payloads),
        symbols=symbols,
        context_types=DAILY_RUN_REFRESH_CONTEXT_TYPES,
        reuse_same_day_fresh=True,
    )
    rows = ensure_daily_radar_raw_rows(
        db,
        run_date,
        symbols,
        technical_fetcher=_PreparedBatchTechnicalFetcher(technical_payloads),
        institutional_payloads_by_symbol=institutional_payloads,
        margin_contexts_by_symbol=_full_margin_contexts_by_symbol(
            db,
            symbols=symbols,
            run_date=run_date,
        ),
    )
    _add_institutional_volume_ratios(rows, selected_symbols=selected_symbols)
    fundamental_errors = _materialize_ai_business_fundamentals(
        pool_rows,
        provider=fundamental_provider,
        as_of_date=run_date,
        industry_resolver=industries_by_symbol.get,
    )
    db.flush()
    refreshed_rows = [
        row
        for row in get_final_raw_data_rows_for_date(db, run_date=run_date)
        if is_daily_radar_supported_symbol(row.symbol)
    ]
    missing_by_lane = _ai_evidence_missing_by_lane(refreshed_rows)
    errors = (
        list(evidence_result.errors)
        + background_fetch_errors
        + list(background_result["errors"])
        + fundamental_errors
    )
    status = "failed" if errors else "completed"
    details = {
        "symbol_count": len(symbols),
        "selected_symbol_count": len(selected_symbols.intersection(symbols)),
        "records_written": len(rows),
        "missing_by_lane": missing_by_lane,
        "provider_counts": {
            "institutional": len(evidence_result.payloads_by_symbol),
            "fundamental": len(refreshed_rows) - len(missing_by_lane["fundamental"]),
            "background_context": int(background_result["records_written"]),
        },
        "errors": errors,
    }
    update_daily_radar_prepared_step_status(
        db,
        prepared,
        step="refresh-ai-evidence",
        status=status,
        details=details,
    )
    db.commit()
    return DailyRadarRefreshStepResponse(
        status=status,
        step="refresh-ai-evidence",
        run_date=run_date,
        market=request.market,
        symbol_count=len(symbols),
        selected_symbol_count=len(selected_symbols.intersection(symbols)),
        records_written=len(rows),
        missing_by_lane=missing_by_lane,
        provider_counts=details["provider_counts"],
        errors=errors,
    )
