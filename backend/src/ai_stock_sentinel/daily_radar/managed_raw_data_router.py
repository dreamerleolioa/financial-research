"""Refresh final raw data for recent general analyses."""
from __future__ import annotations
import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ai_stock_sentinel.daily_radar import dependencies
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.dependencies import get_daily_radar_technical_fetcher
from ai_stock_sentinel.daily_radar.managed_raw_data import (
    MANAGED_RAW_DATA_SYMBOL_LIMIT,
    select_managed_raw_data_symbols,
)
from ai_stock_sentinel.daily_radar.pipeline_support import (
    _finish_managed_raw_data_refresh,
    _industry_classifications_by_symbol,
    _managed_selected_overlap_count,
    _materialize_industry_classifications,
)
from ai_stock_sentinel.daily_radar.raw_data import (
    BatchTechnicalFetcher,
    current_daily_radar_raw_rows,
    ensure_daily_radar_raw_rows,
)
from ai_stock_sentinel.daily_radar.repository import (
    get_daily_radar_prepared_run,
    get_final_raw_data_rows_for_symbols,
)
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarManagedRawDataRefreshRequest,
    DailyRadarManagedRawDataRefreshResponse,
)
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
