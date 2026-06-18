from __future__ import annotations

import json
import logging
from datetime import datetime, time as _time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.adapters.graph_runner import build_graph_singleton, invoke_graph
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
from ai_stock_sentinel.analysis.application.analyze_position import build_position_analyze_initial_state
from ai_stock_sentinel.analysis.application.analyze_stock import build_analyze_initial_state, raw_cache_inputs
from ai_stock_sentinel.analysis.application.response_builder import (
    build_analyze_risk_language as _response_build_analyze_risk_language,
    build_response as _response_build_response,
    build_response_from_cache as _response_build_response_from_cache,
    compute_bollinger_position as _response_compute_bollinger_position,
    compute_technical_indicators as _response_compute_technical_indicators,
    display_symbol_name as _response_display_symbol_name,
    extract_indicators as _response_extract_indicators,
    indicators_with_position_risk_from_full_result as _response_indicators_with_position_risk_from_full_result,
    position_cache_matches as _response_position_cache_matches,
    position_risk_language_snapshot as _response_position_risk_language_snapshot,
    position_risk_language_snapshot_from_result as _response_position_risk_language_snapshot_from_result,
)
from ai_stock_sentinel.analysis.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    CachedAnalyzeResponse,
    HistoryEntry,
    PositionAnalyzeRequest,
    TechnicalIndicators,
)
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.config import STRATEGY_VERSION
from ai_stock_sentinel.data_sources.symbol_metadata import resolve_symbol_name
from ai_stock_sentinel.data_sources.yfinance_client import check_symbol_exists
from ai_stock_sentinel.db.models import StockAnalysisCache, StockRawData, UserPortfolio
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.services.history_loader import (
    backfill_yesterday_indicators,
    load_yesterday_context,
)
from ai_stock_sentinel.shared_context import (
    SHARED_CONTEXT_CONSUMER_ANALYZE,
    SHARED_CONTEXT_CONSUMER_POSITION,
    read_shared_context_for_symbol as _read_shared_context_for_symbol,
)
from ai_stock_sentinel.user_models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])

_TZ_TAIPEI = ZoneInfo("Asia/Taipei")


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


def _extract_indicators(result: dict) -> dict:
    return _response_extract_indicators(result)


def _position_risk_language_snapshot_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return _response_position_risk_language_snapshot_from_result(result)


def _position_risk_language_snapshot(position_analysis: dict[str, Any]) -> dict[str, Any]:
    return _response_position_risk_language_snapshot(position_analysis)


def _indicators_with_position_risk_from_full_result(
    indicators: dict[str, Any] | None,
    full_result: dict[str, Any] | None,
) -> dict[str, Any]:
    return _response_indicators_with_position_risk_from_full_result(indicators, full_result)


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


def _position_cache_matches(full_result: dict[str, Any], payload: PositionAnalyzeRequest) -> bool:
    return _response_position_cache_matches(full_result, payload)


def _build_response(result: dict[str, Any]) -> AnalyzeResponse:
    return _response_build_response(result, symbol_name_resolver=resolve_symbol_name)


def has_active_portfolio(user_id: int, symbol: str, db: Session) -> bool:
    return db.execute(
        select(func.count()).select_from(UserPortfolio).where(
            UserPortfolio.user_id == user_id,
            UserPortfolio.symbol == symbol,
            UserPortfolio.is_active == True,
        )
    ).scalar() > 0


def upsert_analysis_log(db: Session, data: dict) -> None:
    """UPSERT 分析結果至 daily_analysis_log（含 user_id）。"""
    db.execute(
        text("""
            INSERT INTO daily_analysis_log (
                user_id, symbol, record_date, signal_confidence, strategy_version, action_tag,
                recommended_action, indicators, final_verdict,
                prev_action_tag, prev_confidence, analysis_is_final
            ) VALUES (
                :user_id, :symbol, CURRENT_DATE, :signal_confidence, :strategy_version, :action_tag,
                :recommended_action, CAST(:indicators AS jsonb), :final_verdict,
                (SELECT action_tag FROM daily_analysis_log
                 WHERE user_id = :user_id AND symbol = :symbol
                   AND record_date = CURRENT_DATE - 1),
                (SELECT signal_confidence FROM daily_analysis_log
                 WHERE user_id = :user_id AND symbol = :symbol
                   AND record_date = CURRENT_DATE - 1),
                :analysis_is_final
            )
            ON CONFLICT (user_id, symbol, record_date) DO UPDATE SET
                signal_confidence  = EXCLUDED.signal_confidence,
                strategy_version   = EXCLUDED.strategy_version,
                action_tag         = EXCLUDED.action_tag,
                recommended_action = EXCLUDED.recommended_action,
                indicators         = EXCLUDED.indicators,
                final_verdict      = EXCLUDED.final_verdict,
                analysis_is_final  = EXCLUDED.analysis_is_final
        """),
        {
            "user_id": data.get("user_id"),
            "symbol": data.get("symbol"),
            "signal_confidence": data.get("signal_confidence"),
            "strategy_version": STRATEGY_VERSION,
            "action_tag": data.get("action_tag"),
            "recommended_action": data.get("recommended_action"),
            "indicators": json.dumps(data.get("indicators") or {}),
            "final_verdict": data.get("final_verdict"),
            "analysis_is_final": data.get("is_final", False),
        },
    )
    db.commit()


def _maybe_upsert_log(
    db: Session,
    user_id: int,
    symbol: str,
    cache: StockAnalysisCache,
    is_final: bool,
) -> None:
    """若使用者有 active 持倉，寫入 daily_analysis_log（從快取命中路徑呼叫）。"""
    if not has_active_portfolio(user_id, symbol, db):
        return
    upsert_analysis_log(
        db,
        {
            "user_id": user_id,
            "symbol": symbol,
            "signal_confidence": float(cache.signal_confidence) if cache.signal_confidence else None,
            "action_tag": cache.action_tag,
            "recommended_action": cache.recommended_action,
            "indicators": _indicators_with_position_risk_from_full_result(cache.indicators, cache.full_result),
            "final_verdict": cache.final_verdict,
            "is_final": is_final,
        },
    )


def _maybe_upsert_log_from_result(
    db: Session,
    user_id: int,
    symbol: str,
    result: dict,
    is_final: bool,
) -> None:
    """若使用者有 active 持倉，寫入 daily_analysis_log（從分析結果路徑呼叫）。"""
    if not has_active_portfolio(user_id, symbol, db):
        return
    upsert_analysis_log(
        db,
        {
            "user_id": user_id,
            "symbol": symbol,
            "signal_confidence": result.get("signal_confidence"),
            "action_tag": result.get("action_plan_tag"),
            "recommended_action": result.get("recommended_action"),
            "indicators": _extract_indicators(result),
            "final_verdict": result.get("analysis"),
            "is_final": is_final,
        },
    )


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


def _build_graph_singleton():
    return build_graph_singleton()


_graph = _build_graph_singleton()


def get_graph():
    return _graph


def _check_symbol_exists(symbol: str) -> None:
    """yfinance 輕量驗證：代號無效時拋 HTTP 404，避免跑 LLM。"""
    if not check_symbol_exists(symbol):
        raise HTTPException(status_code=404, detail=f"查詢目標不存在：{symbol}")


def fetch_symbol_history(db: Session, symbol: str, days: int = 30):
    from datetime import date, timedelta

    since = date.today() - timedelta(days=days)
    result = db.execute(
        select(StockAnalysisCache)
        .where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date >= since,
        )
        .order_by(StockAnalysisCache.record_date)
    )
    return list(result.scalars().all())


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    payload: AnalyzeRequest,
    graph=Depends(get_graph),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyzeResponse:
    now_time = datetime.now(_TZ_TAIPEI).time()

    if not payload.skip_ai:
        cache = get_analysis_cache(db, payload.symbol, analysis_type="general")
        if cache:
            hit = _handle_cache_hit(cache, now_time)
            if hit:
                _maybe_upsert_log(db, current_user.id, payload.symbol, cache, hit.is_final)
                return _with_shared_context(
                    _build_response_from_cache(hit, payload.symbol, full_result=cache.full_result),
                    db,
                    symbol=payload.symbol,
                    consumer=SHARED_CONTEXT_CONSUMER_ANALYZE,
                )

    raw_cache = None
    if payload.skip_ai:
        raw_cache = get_recent_raw_data(db, payload.symbol, max_age_seconds=600)

    cached_snapshot = None
    cached_institutional = None
    cached_fundamental = None
    cached_fundamental_context = None

    if raw_cache:
        logger.info(json.dumps({
            "event": "raw_data_cache_hit_10m",
            "symbol": payload.symbol,
            "fetched_at": str(raw_cache.fetched_at),
        }))
        cached_snapshot, cached_institutional, cached_fundamental, cached_fundamental_context = raw_cache_inputs(raw_cache)
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
        cached_fundamental_context=cached_fundamental_context,
    )

    try:
        result: dict[str, Any] = invoke_graph(graph, initial_state)
    except Exception as exc:
        return AnalyzeResponse(
            errors=[
                AnalyzeResponse.ErrorDetail(
                    code="ANALYZE_RUNTIME_ERROR",
                    message=str(exc),
                )
            ]
        )

    is_final = now_time >= MARKET_CLOSE
    _response = _build_response(result)

    if not payload.skip_ai:
        upsert_analysis_cache(
            db,
            {
                "symbol": payload.symbol,
                "analysis_type": "general",
                "signal_confidence": result.get("signal_confidence"),
                "action_tag": result.get("action_plan_tag"),
                "recommended_action": result.get("recommended_action"),
                "indicators": _extract_indicators(result),
                "final_verdict": result.get("analysis"),
                "is_final": is_final,
                "full_result": _response.model_dump(),
            },
        )
        _maybe_upsert_log_from_result(db, current_user.id, payload.symbol, result, is_final)

    if not raw_cache:
        fetch_and_store_raw_data(
            db,
            payload.symbol,
            technical=result.get("snapshot"),
            institutional=result.get("institutional_flow"),
            fundamental=result.get("fundamental_data"),
            raw_data_is_final=is_final,
        )

    response = _response
    response.is_final = is_final
    response.intraday_disclaimer = INTRADAY_DISCLAIMER if not is_final else None
    return _with_shared_context(
        response,
        db,
        symbol=payload.symbol,
        consumer=SHARED_CONTEXT_CONSUMER_ANALYZE,
    )


@router.get("/history/{symbol}", response_model=list[HistoryEntry])
def get_symbol_history(
    symbol: str,
    days: int = 30,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[HistoryEntry]:
    logs = fetch_symbol_history(db=db, symbol=symbol, days=days)
    return [
        HistoryEntry(
            record_date=str(log.record_date),
            signal_confidence=float(log.signal_confidence) if log.signal_confidence else None,
            action_tag=log.action_tag,
            prev_action_tag=log.prev_action_tag,
            prev_confidence=float(log.prev_confidence) if log.prev_confidence else None,
            analysis_is_final=bool(log.analysis_is_final),
            indicators=log.indicators,
            final_verdict=log.final_verdict,
        )
        for log in logs
    ]


@router.post("/analyze/position", response_model=AnalyzeResponse)
def analyze_position(
    payload: PositionAnalyzeRequest,
    graph=Depends(get_graph),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyzeResponse:
    now_time = datetime.now(_TZ_TAIPEI).time()

    cache = get_analysis_cache(db, payload.symbol, analysis_type="position")
    if cache:
        hit = _handle_cache_hit(cache, now_time)
        if hit:
            full = cache.full_result or {}
            if _position_cache_matches(full, payload):
                _maybe_upsert_log(db, current_user.id, payload.symbol, cache, hit.is_final)
                return _with_shared_context(
                    _build_response_from_cache(hit, payload.symbol, full_result=full),
                    db,
                    symbol=payload.symbol,
                    consumer=SHARED_CONTEXT_CONSUMER_POSITION,
                )

    _check_symbol_exists(payload.symbol)
    backfill_yesterday_indicators(db, payload.symbol)
    prev_context = load_yesterday_context(payload.symbol, db)

    initial_state = build_position_analyze_initial_state(
        payload,
        now_time=now_time,
        market_close=MARKET_CLOSE,
        prev_context=prev_context,
    )

    try:
        result: dict[str, Any] = invoke_graph(graph, initial_state)
    except Exception as exc:
        return AnalyzeResponse(
            errors=[
                AnalyzeResponse.ErrorDetail(
                    code="ANALYZE_RUNTIME_ERROR",
                    message=str(exc),
                )
            ]
        )

    is_final = now_time >= MARKET_CLOSE
    _response = _build_response(result)
    full_result = _response.model_dump()
    full_result["_position_request"] = {
        "entry_price": payload.entry_price,
        "entry_date": payload.entry_date,
        "quantity": payload.quantity,
    }
    upsert_analysis_cache(
        db,
        {
            "symbol": payload.symbol,
            "analysis_type": "position",
            "signal_confidence": result.get("signal_confidence"),
            "action_tag": result.get("action_plan_tag"),
            "indicators": _extract_indicators(result),
            "final_verdict": result.get("analysis"),
            "is_final": is_final,
            "full_result": full_result,
        },
    )
    fetch_and_store_raw_data(
        db,
        payload.symbol,
        technical=result.get("snapshot"),
        institutional=result.get("institutional_flow"),
        fundamental=result.get("fundamental_data"),
        raw_data_is_final=is_final,
    )
    _maybe_upsert_log_from_result(db, current_user.id, payload.symbol, result, is_final)

    response = _response
    response.is_final = is_final
    response.intraday_disclaimer = INTRADAY_DISCLAIMER if not is_final else None
    return _with_shared_context(
        response,
        db,
        symbol=payload.symbol,
        consumer=SHARED_CONTEXT_CONSUMER_POSITION,
    )
