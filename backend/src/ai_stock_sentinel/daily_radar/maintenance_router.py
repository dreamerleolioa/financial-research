"""Backfills, market-session resolution, and forward-validation maintenance."""

from __future__ import annotations

from contextlib import suppress
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.price_provider import (
    ForwardPriceProvider,
    get_forward_price_provider,
    get_research_price_provider,
)
from ai_stock_sentinel.daily_radar.trading_calendar import get_trading_calendar_provider, CalendarUnavailableError
from ai_stock_sentinel.daily_radar import dependencies
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.background_context import (
    BackgroundChipContextProvider,
    update_background_chip_context_cache,
)
from ai_stock_sentinel.daily_radar.dependencies import (
    get_daily_radar_background_chip_context_provider,
    get_daily_radar_market_session_provider,
    get_taiwan_institutional_report_provider,
)
from ai_stock_sentinel.daily_radar.forward_validation import load_price_series_from_raw_data
from ai_stock_sentinel.daily_radar.institutional_archive_universe import (
    InstitutionalArchiveUniverseError,
)
from ai_stock_sentinel.daily_radar.institutional_flow_service import (
    InstitutionalReportProvider,
    backfill_taiwan_institutional_flows,
)
from ai_stock_sentinel.daily_radar.institutional_universe_replay import (
    build_institutional_universe_replay_report,
)
from ai_stock_sentinel.daily_radar.market_session import (
    MarketSessionProvider,
    MarketSessionProviderError,
)
from ai_stock_sentinel.daily_radar.name_backfill import (
    SymbolNameResolver,
    backfill_daily_radar_symbol_names,
    get_daily_radar_symbol_name_resolver,
)
from ai_stock_sentinel.daily_radar.repository import BACKGROUND_CONTEXT_TYPES
from ai_stock_sentinel.daily_radar.rule_governance import build_monthly_rule_review_report
from ai_stock_sentinel.daily_radar.research_validation import run_research_validation
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarChipContextUpdateRequest,
    DailyRadarChipContextUpdateResponse,
    DailyRadarForwardValidationRunRequest,
    DailyRadarForwardValidationRunResponse,
    DailyRadarInstitutionalFlowsBackfillRequest,
    DailyRadarInstitutionalFlowsBackfillResponse,
    DailyRadarInstitutionalUniverseReplayRequest,
    DailyRadarInstitutionalUniverseReplayResponse,
    DailyRadarMarketSessionRequest,
    DailyRadarMarketSessionResponse,
    DailyRadarMonthlyRuleReviewRequest,
    DailyRadarMonthlyRuleReviewResponse,
    DailyRadarNameBackfillRequest,
    DailyRadarNameBackfillResponse,
)
from ai_stock_sentinel.db.session import get_db


router = APIRouter()


logger = logging.getLogger(__name__)


@router.post(
    "/internal/daily-radar/market-session",
    response_model=DailyRadarMarketSessionResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def resolve_daily_radar_market_session_endpoint(
    payload: DailyRadarMarketSessionRequest | None = None,
    provider: MarketSessionProvider = Depends(get_daily_radar_market_session_provider),
) -> DailyRadarMarketSessionResponse:
    request = payload or DailyRadarMarketSessionRequest()
    run_date = request.run_date or dependencies._backend_today()
    try:
        result = provider.resolve(run_date=run_date, market=request.market)
    except MarketSessionProviderError as exc:
        logger.exception(
            "Daily Radar market-session lookup failed run_date=%s market=%s code=%s",
            run_date.isoformat(),
            request.market,
            exc.code,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "message": "Daily Radar market-session lookup failed.",
            },
        ) from exc
    return DailyRadarMarketSessionResponse(
        status=result.status,
        run_date=result.run_date,
        market=result.market,
        provider=result.provider,
        dataset=result.dataset,
    )


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
    "/internal/daily-radar/backfill-institutional-flows",
    response_model=DailyRadarInstitutionalFlowsBackfillResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def backfill_daily_radar_institutional_flows_endpoint(
    payload: DailyRadarInstitutionalFlowsBackfillRequest,
    db: Session = Depends(get_db),
    provider: InstitutionalReportProvider = Depends(
        get_taiwan_institutional_report_provider
    ),
    market_session_provider: MarketSessionProvider = Depends(
        get_daily_radar_market_session_provider
    ),
) -> DailyRadarInstitutionalFlowsBackfillResponse:
    try:
        result = backfill_taiwan_institutional_flows(
            db,
            start_date=payload.start_date,
            end_date=payload.end_date,
            as_of_date=dependencies._backend_today(),
            provider=provider,
            market_session_provider=market_session_provider,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={
                "code": "institutional_flow_backfill_range_invalid",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Taiwan institutional flow archive backfill failed")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "institutional_flow_archive_backfill_failed",
                "error_type": exc.__class__.__name__,
            },
        ) from exc
    return DailyRadarInstitutionalFlowsBackfillResponse(
        status=result["status"],
        start_date=payload.start_date,
        end_date=payload.end_date,
        market=payload.market,
        records_written=result["records_written"],
        dates_requested=result["dates_requested"],
        dates_attempted=result["dates_attempted"],
        dates_completed=result["dates_completed"],
        dates_reused=result["dates_reused"],
        dates_repaired=result["dates_repaired"],
        skipped_dates=result["skipped_dates"],
        snapshots=result["snapshots"],
        errors=result["errors"],
    )


@router.post(
    "/internal/daily-radar/institutional-universe-replay",
    response_model=DailyRadarInstitutionalUniverseReplayResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def replay_daily_radar_institutional_universe_endpoint(
    payload: DailyRadarInstitutionalUniverseReplayRequest,
    db: Session = Depends(get_db),
) -> DailyRadarInstitutionalUniverseReplayResponse:
    if payload.run_date > dependencies._backend_today():
        raise HTTPException(
            status_code=422,
            detail={"code": "institutional_universe_replay_date_in_future"},
        )
    try:
        report = build_institutional_universe_replay_report(
            db,
            run_date=payload.run_date,
            market=payload.market,
            track_limit=payload.track_limit,
            max_symbols=payload.max_symbols,
        )
    except InstitutionalArchiveUniverseError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "market": exc.market,
                "query_date": exc.query_date.isoformat(),
            },
        ) from exc
    return DailyRadarInstitutionalUniverseReplayResponse(
        run_date=payload.run_date,
        market=payload.market,
        report=report,
    )


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
    run_date = request.run_date or dependencies._backend_today()
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
    price_provider: ForwardPriceProvider = Depends(get_forward_price_provider),
    research_price_provider: ForwardPriceProvider = Depends(get_research_price_provider),
    calendar_provider = Depends(get_trading_calendar_provider),
) -> DailyRadarForwardValidationRunResponse:
    request = payload or DailyRadarForwardValidationRunRequest()
    as_of_date = request.as_of_date or dependencies._backend_today()
    if request.return_basis == "next_open":
        try:
            result = run_research_validation(db, request, as_of_date=as_of_date, price_provider=research_price_provider, calendar_provider=calendar_provider)
        except CalendarUnavailableError as exc:
            raise HTTPException(status_code=503, detail={"code": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        db.commit()
        return DailyRadarForwardValidationRunResponse(**result)
    from ai_stock_sentinel.daily_radar.validation_execution import run_signal_close_validation
    return run_signal_close_validation(db, request, as_of_date=as_of_date,
        price_provider=price_provider, calendar_provider=calendar_provider,
        raw_price_loader=load_price_series_from_raw_data)


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
        benchmark_symbol=payload.benchmark_symbol,
        validation_version=payload.validation_version,
        min_sample_count=payload.min_sample_count,
        min_validated_coverage=payload.min_validated_coverage,
        min_replay_coverage=payload.min_replay_coverage,
    )
    return DailyRadarMonthlyRuleReviewResponse(
        status="completed",
        market=payload.market,
        month=f"{payload.year:04d}-{payload.month:02d}",
        report_json=report.json_report,
        report_markdown=report.markdown_report,
    )
