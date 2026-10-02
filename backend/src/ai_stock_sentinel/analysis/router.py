from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import date, datetime, time as _time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.adapters.graph_runner import build_graph_singleton, invoke_graph
from ai_stock_sentinel.analysis.calibration import (
    GENERAL_REPLAY_CACHE_KEY,
    build_general_analysis_replay_input,
    capture_general_analysis_calibration_sample,
)
from ai_stock_sentinel.analysis.application.analysis_cache import (
    INTRADAY_DISCLAIMER,
    MARKET_CLOSE,
    build_analysis_response as _cache_build_analysis_response,
    fetch_and_store_raw_data as _fetch_and_store_raw_data,
    get_analysis_cache as _get_analysis_cache,
    get_raw_data as _get_raw_data,
    get_recent_raw_data as _get_recent_raw_data,
    handle_cache_hit as _analysis_handle_cache_hit,
    latest_number as _analysis_latest_number,
    normalize_raw_technical_for_storage as _analysis_normalize_raw_technical_for_storage,
    number_or_none as _analysis_number_or_none,
    upsert_analysis_cache as _upsert_analysis_cache,
)
from ai_stock_sentinel.analysis.application.analyze_stock import build_analyze_initial_state, raw_cache_inputs
from ai_stock_sentinel.analysis.application.response_builder import (
    build_analyze_risk_language as _response_build_analyze_risk_language,
    build_response as _response_build_response,
    build_response_from_cache as _response_build_response_from_cache,
    compute_bollinger_position as _response_compute_bollinger_position,
    compute_technical_indicators as _response_compute_technical_indicators,
    display_symbol_name as _response_display_symbol_name,
    extract_indicators as _response_extract_indicators,
)
from ai_stock_sentinel.analysis.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    CachedAnalyzeResponse,
    TechnicalIndicators,
)
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.chip_stability_context import (
    chip_stability_context_from_weekly_major_holders,
    weekly_major_holders_projection_by_symbol,
)
from ai_stock_sentinel.clock import TAIPEI_TZ, today_taipei
from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.data_sources.taiwan_price_limits import fetch_taiwan_price_limits_with_deadline
from ai_stock_sentinel.data_sources.yfinance_client import check_symbol_exists
from ai_stock_sentinel.db.models import StockAnalysisCache, StockRawData
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.phase1_avwap.projection import read_phase1_observation_for_analyze as _read_phase1_observation_for_analyze
from ai_stock_sentinel.services.history_loader import (
    backfill_yesterday_indicators,
    load_yesterday_context,
)
from ai_stock_sentinel.shared_context import (
    SHARED_CONTEXT_CONSUMER_ANALYZE,
    read_shared_context_for_symbol as _read_shared_context_for_symbol,
)
from ai_stock_sentinel.user_models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])

_TZ_TAIPEI = TAIPEI_TZ


def get_analysis_cache(db: Session, symbol: str, analysis_type: str = "general") -> StockAnalysisCache | None:
    return _get_analysis_cache(db, symbol, analysis_type=analysis_type)


def get_raw_data(db: Session, symbol: str) -> StockRawData | None:
    return _get_raw_data(db, symbol)


def get_recent_raw_data(db: Session, symbol: str, max_age_seconds: int = 600) -> StockRawData | None:
    return _get_recent_raw_data(db, symbol, max_age_seconds=max_age_seconds)


def _handle_cache_hit(
    cache: StockAnalysisCache,
    now_time: _time,
) -> CachedAnalyzeResponse | None:
    return _analysis_handle_cache_hit(cache, now_time)


def _retry_general_analysis_calibration_capture_from_cache(
    db: Session,
    cache: StockAnalysisCache,
) -> None:
    full_result = cache.full_result
    if not isinstance(full_result, Mapping):
        return
    replay_input = full_result.get(GENERAL_REPLAY_CACHE_KEY)
    if not isinstance(replay_input, Mapping):
        return
    try:
        capture_general_analysis_calibration_sample(
            db,
            symbol=cache.symbol,
            record_date=cache.record_date,
            result=full_result,
            is_final=True,
            strategy_version=cache.strategy_version or STRATEGY_VERSION,
            replay_input=replay_input,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to retry general-analysis calibration capture from final cache for %s",
            cache.symbol,
        )


def _build_analysis_response(
    *,
    symbol: str,
    action_tag: str | None,
    signal_confidence: float | None,
    recommended_action: str | None,
    final_verdict: str | None,
    is_final: bool,
    strategy_version: str | None = None,
) -> CachedAnalyzeResponse:
    return _cache_build_analysis_response(
        symbol=symbol,
        action_tag=action_tag,
        signal_confidence=signal_confidence,
        recommended_action=recommended_action,
        final_verdict=final_verdict,
        is_final=is_final,
        strategy_version=strategy_version,
    )


def upsert_analysis_cache(db: Session, data: dict) -> None:
    _upsert_analysis_cache(db, data)


def fetch_and_store_raw_data(
    db: Session,
    symbol: str,
    *,
    technical: dict | None,
    institutional: dict | None,
    fundamental: dict | None,
    raw_data_is_final: bool = False,
) -> None:
    _fetch_and_store_raw_data(
        db,
        symbol,
        technical=technical,
        institutional=institutional,
        fundamental=fundamental,
        raw_data_is_final=raw_data_is_final,
    )


def _normalize_raw_technical_for_storage(technical: dict | None) -> dict:
    return _analysis_normalize_raw_technical_for_storage(technical)


def _latest_number(values: Any) -> float | None:
    return _analysis_latest_number(values)


def _number_or_none(value: Any) -> float | None:
    return _analysis_number_or_none(value)


def _compute_bollinger_position(bb: dict, close_price: float | None) -> str | None:
    return _response_compute_bollinger_position(bb, close_price)


def _compute_technical_indicators(snapshot: dict) -> TechnicalIndicators | None:
    return _response_compute_technical_indicators(snapshot)


def _extract_indicators(result: dict, *, is_final: bool) -> dict:
    return _response_extract_indicators(result, is_final=is_final)


def _build_response_from_cache(
    hit: CachedAnalyzeResponse,
    symbol: str,
    full_result: dict | None = None,
) -> AnalyzeResponse:
    return _response_build_response_from_cache(
        hit,
        symbol,
        full_result=full_result,
        symbol_name_resolver=resolve_symbol_name,
    )


def _display_symbol_name(symbol: str, name: Any | None = None) -> str | None:
    return _response_display_symbol_name(symbol, name, symbol_name_resolver=resolve_symbol_name)


def _build_analyze_risk_language(result: dict[str, Any]) -> dict[str, Any]:
    return _response_build_analyze_risk_language(result)


def _build_response(result: dict[str, Any]) -> AnalyzeResponse:
    return _response_build_response(result, symbol_name_resolver=resolve_symbol_name)


def _set_response_finality(response: AnalyzeResponse, *, is_final: bool) -> None:
    response.is_final = is_final
    response.intraday_disclaimer = INTRADAY_DISCLAIMER if not is_final else None
    profile = response.technical_profile
    if isinstance(profile, dict):
        data_quality = profile.get("data_quality")
        if isinstance(data_quality, dict):
            data_quality["is_final"] = is_final


def _with_shared_context(
    response: AnalyzeResponse,
    db: Session,
    *,
    symbol: str,
    consumer: str,
) -> AnalyzeResponse:
    response.shared_context = _read_shared_context_for_symbol(
        db,
        symbol=symbol,
        consumer=consumer,
    )
    return response


def _with_chip_stability_context(
    response: AnalyzeResponse,
    db: Session,
    *,
    symbol: str,
    consumer: str,
) -> AnalyzeResponse:
    try:
        weekly_major_holders_by_symbol = weekly_major_holders_projection_by_symbol(
            db,
            symbols=[symbol],
            consumer=consumer,
            reference_date=_today_taipei(),
        )
        response.chip_stability_context = chip_stability_context_from_weekly_major_holders(
            weekly_major_holders_by_symbol.get(symbol)
        )
    except Exception as exc:
        logger.warning(
            "chip_stability_context_read_failed",
            extra={
                "symbol": symbol,
                "consumer": consumer,
                "error_type": exc.__class__.__name__,
            },
        )
        response.chip_stability_context = None
    return response


def _with_shared_and_chip_context(
    response: AnalyzeResponse,
    db: Session,
    *,
    symbol: str,
    consumer: str,
) -> AnalyzeResponse:
    response = _with_shared_context(response, db, symbol=symbol, consumer=consumer)
    return _with_chip_stability_context(response, db, symbol=symbol, consumer=consumer)


def _with_phase1_observation(
    response: AnalyzeResponse,
    db: Session,
    *,
    user_id: int,
    symbol: str,
) -> AnalyzeResponse:
    current_price = response.snapshot.get("current_price") if isinstance(response.snapshot, dict) else None
    response.phase1_observation = _read_phase1_observation_for_analyze(
        db,
        user_id=user_id,
        symbol=symbol,
        data_date=_today_taipei(),
        current_price=current_price if isinstance(current_price, int | float) else None,
    )
    return response


def _with_analyze_response_contexts(
    response: AnalyzeResponse,
    db: Session,
    *,
    user_id: int,
    symbol: str,
) -> AnalyzeResponse:
    response = _with_price_limit_context(response, symbol=symbol)
    response = _with_shared_and_chip_context(
        response,
        db,
        symbol=symbol,
        consumer=SHARED_CONTEXT_CONSUMER_ANALYZE,
    )
    return _with_phase1_observation(
        response,
        db,
        user_id=user_id,
        symbol=symbol,
    )


def _with_price_limit_context(response: AnalyzeResponse, *, symbol: str) -> AnalyzeResponse:
    snapshot = response.snapshot
    current_price = snapshot.get("current_price") if isinstance(snapshot, dict) else None
    if not isinstance(current_price, int | float) or isinstance(current_price, bool):
        return response
    price_limits = fetch_taiwan_price_limits_with_deadline(symbol)
    response.snapshot = {
        **snapshot,
        "market_current_price": price_limits.current_price,
        "market_quote_time": price_limits.quote_time,
        "market_trade_date": price_limits.trade_date,
        "market_day_open": price_limits.day_open,
        "market_day_high": price_limits.day_high,
        "market_day_low": price_limits.day_low,
        "market_current_price_source": (
            "twse_mis"
            if price_limits.current_price is not None
            else None
        ),
        "price_limit_status": price_limits.status,
        "price_limit_quote_price": price_limits.current_price,
        "limit_up_price": price_limits.limit_up_price,
        "limit_down_price": price_limits.limit_down_price,
    }
    return response


def _today_taipei() -> date:
    return today_taipei()


def _build_graph_singleton():
    return build_graph_singleton()


_graph = _build_graph_singleton()


def get_graph():
    return _graph


def _check_symbol_exists(symbol: str) -> None:
    """yfinance 輕量驗證：代號無效時拋 HTTP 404，避免執行完整資料流程。"""
    if not check_symbol_exists(symbol):
        raise HTTPException(status_code=404, detail=f"查詢目標不存在：{symbol}")




@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    payload: AnalyzeRequest,
    graph=Depends(get_graph),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyzeResponse:
    now_time = datetime.now(_TZ_TAIPEI).time()

    if payload.should_persist_result:
        cache = get_analysis_cache(db, payload.symbol, analysis_type="general")
        if cache:
            hit = _handle_cache_hit(cache, now_time)
            if hit:
                if hit.is_final:
                    _retry_general_analysis_calibration_capture_from_cache(db, cache)
                return _with_analyze_response_contexts(
                    _build_response_from_cache(hit, payload.symbol, full_result=cache.full_result),
                    db,
                    user_id=current_user.id,
                    symbol=payload.symbol,
                )

    raw_cache = None
    if not payload.should_persist_result:
        raw_cache = get_recent_raw_data(db, payload.symbol, max_age_seconds=600)

    cached_snapshot = None
    cached_institutional = None
    cached_fundamental = None
    if raw_cache:
        logger.info(json.dumps({
            "event": "raw_data_cache_hit_10m",
            "symbol": payload.symbol,
            "fetched_at": str(raw_cache.fetched_at),
        }))
        cached_snapshot, cached_institutional, cached_fundamental = raw_cache_inputs(raw_cache)
    else:
        _check_symbol_exists(payload.symbol)

    backfill_yesterday_indicators(db, payload.symbol)
    prev_context = load_yesterday_context(payload.symbol, db)

    initial_state = build_analyze_initial_state(
        payload,
        now_time=now_time,
        market_close=MARKET_CLOSE,
        prev_context=prev_context,
        cached_snapshot=cached_snapshot,
        cached_institutional=cached_institutional,
        cached_fundamental=cached_fundamental,
    )

    try:
        result: dict[str, Any] = invoke_graph(graph, initial_state)
    except Exception:
        logger.exception("analyze_graph_failed", extra={"symbol": payload.symbol})
        return AnalyzeResponse(
            errors=[
                AnalyzeResponse.ErrorDetail(
                    code="ANALYZE_RUNTIME_ERROR",
                    message="分析暫時無法完成，請稍後再試。",
                )
            ]
        )

    is_final = now_time >= MARKET_CLOSE
    _response = _build_response({**result, "is_final": is_final})
    _set_response_finality(_response, is_final=is_final)

    if payload.should_persist_result:
        cache_full_result = _response.model_dump()
        calibration_replay_input: dict[str, Any] | None = None
        if is_final:
            calibration_replay_input = build_general_analysis_replay_input(result)
            cache_full_result[GENERAL_REPLAY_CACHE_KEY] = calibration_replay_input
        upsert_analysis_cache(
            db,
            {
                "symbol": payload.symbol,
                "analysis_type": "general",
                "signal_confidence": result.get("signal_confidence"),
                "action_tag": result.get("action_plan_tag"),
                "recommended_action": result.get("recommended_action"),
                "indicators": _extract_indicators(result, is_final=is_final),
                "final_verdict": result.get("analysis"),
                "is_final": is_final,
                "full_result": cache_full_result,
            },
        )
        if is_final:
            try:
                capture_general_analysis_calibration_sample(
                    db,
                    symbol=payload.symbol,
                    record_date=_today_taipei(),
                    result=result,
                    is_final=True,
                    replay_input=calibration_replay_input,
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed to capture general-analysis calibration sample for %s",
                    payload.symbol,
                )

    if not raw_cache:
        fetch_and_store_raw_data(
            db,
            payload.symbol,
            technical=result.get("snapshot"),
            institutional=result.get("institutional_flow"),
            fundamental=result.get("fundamental_data"),
            raw_data_is_final=is_final,
        )

    return _with_analyze_response_contexts(
        _response,
        db,
        user_id=current_user.id,
        symbol=payload.symbol,
    )
