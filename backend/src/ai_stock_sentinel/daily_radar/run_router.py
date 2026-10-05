"""Execute Radar scoring from validated prepared inputs."""

from __future__ import annotations

from contextlib import suppress
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar import dependencies
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.background_context import (
    BackgroundChipContextProvider,
    update_background_chip_context_cache,
)
from ai_stock_sentinel.daily_radar.dependencies import (
    DAILY_RADAR_MAX_UNIVERSE_SYMBOLS,
    DAILY_RUN_REFRESH_CONTEXT_TYPES,
    get_daily_radar_background_chip_context_provider,
    get_daily_radar_market_context_provider,
    get_daily_radar_technical_fetcher,
    get_daily_radar_universe_provider,
    get_phase1_avwap_daily_price_provider,
)
from ai_stock_sentinel.daily_radar.market_context import MarketIndexContextProvider
from ai_stock_sentinel.daily_radar.pipeline_support import (
    _capped_daily_radar_universe,
    _full_margin_contexts_by_symbol,
    _institutional_payloads_by_symbol,
    _prepared_run_or_404,
    _raise_if_required_refresh_steps_missing,
    _refresh_phase1_avwap_for_daily_radar,
    _require_complete_daily_radar_raw_rows,
    _should_refresh_daily_run_chip_context,
)
from ai_stock_sentinel.daily_radar.presenter import run_trigger_response
from ai_stock_sentinel.daily_radar.raw_data import (
    BatchTechnicalFetcher,
    ensure_daily_radar_raw_rows,
)
from ai_stock_sentinel.daily_radar.repository import (
    BACKGROUND_CONTEXT_TYPES,
    get_final_raw_data_rows_for_date,
    get_final_raw_data_rows_for_symbols,
    get_shared_background_context_trace_by_symbol,
)
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarRefreshStepRequest,
    DailyRadarRunRequest,
    DailyRadarRunTriggerResponse,
)
from ai_stock_sentinel.daily_radar.service import run_daily_radar
from ai_stock_sentinel.daily_radar.universe import (
    DailyRadarUniverseProvider,
    select_daily_radar_universe,
)
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.phase1_avwap.service import DailyPriceProvider


router = APIRouter()


logger = logging.getLogger(__name__)


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
    run_date = request.run_date or dependencies._backend_today()
    prepared = _prepared_run_or_404(db, run_date=run_date, market=request.market)
    _raise_if_required_refresh_steps_missing(prepared)
    if not prepared.market_context:
        raise HTTPException(
            status_code=409,
            detail=f"Daily Radar market context is not prepared for {request.market} on {run_date.isoformat()}.",
        )
    selected_symbols = list(prepared.selected_symbols)
    cache_rows = _require_complete_daily_radar_raw_rows(
        get_final_raw_data_rows_for_symbols(db, run_date=run_date, symbols=selected_symbols),
        selected_symbols=selected_symbols,
        run_date=run_date,
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
        run_date = request.run_date or dependencies._backend_today()
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
        universe = _capped_daily_radar_universe(
            universe,
            max_symbols=DAILY_RADAR_MAX_UNIVERSE_SYMBOLS,
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
        margin_contexts_by_symbol = None
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
            margin_contexts_by_symbol = _full_margin_contexts_by_symbol(
                db,
                symbols=selected_symbols,
                run_date=run_date,
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
            margin_contexts_by_symbol=margin_contexts_by_symbol,
        )
        cache_rows = _require_complete_daily_radar_raw_rows(
            get_final_raw_data_rows_for_symbols(db, run_date=run_date, symbols=selected_symbols),
            selected_symbols=selected_symbols,
            run_date=run_date,
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
