from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.config import STRATEGY_VERSION
from ai_stock_sentinel.db.models import StockAnalysisCache, StockRawData
from ai_stock_sentinel.analysis.schemas import CachedAnalyzeResponse


MARKET_CLOSE = time(13, 30)
INTRADAY_DISCLAIMER = (
    "⚠️ 注意：目前為盤中階段（指標未收定），"
    "以下分析僅供即時參考，不代表當日收盤定論。"
)

logger = logging.getLogger(__name__)


def get_analysis_cache(db: Session, symbol: str, analysis_type: str = "general") -> StockAnalysisCache | None:
    return db.execute(
        select(StockAnalysisCache).where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == date.today(),
            StockAnalysisCache.analysis_type == analysis_type,
        )
    ).scalar_one_or_none()


def get_raw_data(db: Session, symbol: str) -> StockRawData | None:
    return db.execute(
        select(StockRawData).where(
            StockRawData.symbol == symbol,
            StockRawData.record_date == date.today(),
        )
    ).scalar_one_or_none()


def get_recent_raw_data(db: Session, symbol: str, max_age_seconds: int = 600) -> StockRawData | None:
    raw_data = get_raw_data(db, symbol)
    if raw_data and isinstance(raw_data.fetched_at, datetime):
        now = datetime.now(raw_data.fetched_at.tzinfo or timezone.utc)
        age = now - raw_data.fetched_at
        if age.total_seconds() < max_age_seconds:
            return raw_data
    return None


def handle_cache_hit(
    cache: StockAnalysisCache,
    now_time: time,
) -> CachedAnalyzeResponse | None:
    if cache.strategy_version != STRATEGY_VERSION:
        logger.info(json.dumps({
            "event": "cache_version_mismatch",
            "symbol": cache.symbol,
            "cache_version": cache.strategy_version,
            "current_version": STRATEGY_VERSION,
        }))
        return None

    if cache.analysis_is_final:
        return build_analysis_response(
            symbol=cache.symbol,
            action_tag=cache.action_tag,
            signal_confidence=float(cache.signal_confidence) if cache.signal_confidence else None,
            recommended_action=cache.recommended_action,
            final_verdict=cache.final_verdict,
            is_final=True,
            strategy_version=cache.strategy_version,
        )
    if now_time < MARKET_CLOSE:
        return build_analysis_response(
            symbol=cache.symbol,
            action_tag=cache.action_tag,
            signal_confidence=float(cache.signal_confidence) if cache.signal_confidence else None,
            recommended_action=cache.recommended_action,
            final_verdict=cache.final_verdict,
            is_final=False,
            strategy_version=cache.strategy_version,
        )
    return None


def build_analysis_response(
    *,
    symbol: str,
    action_tag: str | None,
    signal_confidence: float | None,
    recommended_action: str | None,
    final_verdict: str | None,
    is_final: bool,
    strategy_version: str | None = None,
) -> CachedAnalyzeResponse:
    return CachedAnalyzeResponse(
        symbol=symbol,
        signal_confidence=signal_confidence,
        action_tag=action_tag,
        recommended_action=recommended_action,
        final_verdict=final_verdict,
        is_final=is_final,
        intraday_disclaimer=INTRADAY_DISCLAIMER if not is_final else None,
        strategy_version=strategy_version,
    )


def upsert_analysis_cache(db: Session, data: dict) -> None:
    db.execute(
        text("""
            INSERT INTO stock_analysis_cache (
                symbol, record_date, analysis_type, signal_confidence, strategy_version, action_tag,
                recommended_action, indicators, final_verdict,
                prev_action_tag, prev_confidence, analysis_is_final, full_result, updated_at
            ) VALUES (
                :symbol, CURRENT_DATE, :analysis_type, :signal_confidence, :strategy_version, :action_tag,
                :recommended_action, CAST(:indicators AS jsonb), :final_verdict,
                (SELECT action_tag FROM stock_analysis_cache
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1 AND analysis_type = :analysis_type),
                (SELECT signal_confidence FROM stock_analysis_cache
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1 AND analysis_type = :analysis_type),
                :analysis_is_final, CAST(:full_result AS jsonb), NOW()
            )
            ON CONFLICT (symbol, record_date, analysis_type) DO UPDATE SET
                signal_confidence  = EXCLUDED.signal_confidence,
                strategy_version   = EXCLUDED.strategy_version,
                action_tag         = EXCLUDED.action_tag,
                recommended_action = EXCLUDED.recommended_action,
                indicators         = EXCLUDED.indicators,
                final_verdict      = EXCLUDED.final_verdict,
                analysis_is_final  = EXCLUDED.analysis_is_final,
                full_result        = EXCLUDED.full_result,
                updated_at         = NOW()
        """),
        {
            "symbol": data.get("symbol"),
            "analysis_type": data.get("analysis_type", "general"),
            "signal_confidence": data.get("signal_confidence"),
            "strategy_version": STRATEGY_VERSION,
            "action_tag": data.get("action_tag"),
            "recommended_action": data.get("recommended_action"),
            "indicators": json.dumps(data.get("indicators") or {}),
            "final_verdict": data.get("final_verdict"),
            "analysis_is_final": data.get("is_final", False),
            "full_result": json.dumps(data.get("full_result") or {}),
        },
    )
    db.commit()


def fetch_and_store_raw_data(
    db: Session,
    symbol: str,
    *,
    technical: dict | None,
    institutional: dict | None,
    fundamental: dict | None,
    raw_data_is_final: bool = False,
) -> None:
    normalized_technical = normalize_raw_technical_for_storage(technical)
    db.execute(
        text("""
            INSERT INTO stock_raw_data (
                symbol, record_date, technical, institutional, fundamental, raw_data_is_final, fetched_at
            ) VALUES (
                :symbol, CURRENT_DATE,
                CAST(:technical AS jsonb),
                CAST(:institutional AS jsonb),
                CAST(:fundamental AS jsonb),
                :raw_data_is_final,
                NOW()
            )
            ON CONFLICT (symbol, record_date) DO UPDATE SET
                technical         = EXCLUDED.technical,
                institutional     = EXCLUDED.institutional,
                fundamental       = EXCLUDED.fundamental,
                raw_data_is_final = EXCLUDED.raw_data_is_final,
                fetched_at        = NOW()
        """),
        {
            "symbol": symbol,
            "technical": json.dumps(normalized_technical),
            "institutional": json.dumps(institutional or {}),
            "fundamental": json.dumps(fundamental or {}),
            "raw_data_is_final": raw_data_is_final,
        },
    )
    db.commit()


def normalize_raw_technical_for_storage(technical: dict | None) -> dict:
    normalized = dict(technical or {})
    if not normalized or isinstance(normalized.get("ohlcv"), dict):
        return normalized

    close = number_or_none(normalized.get("current_price"))
    if close is None:
        close = latest_number(normalized.get("recent_closes"))
    if close is None:
        return normalized

    high = latest_number(normalized.get("recent_highs"))
    low = latest_number(normalized.get("recent_lows"))
    volume = latest_number(normalized.get("recent_volumes"))
    if volume is None:
        volume = number_or_none(normalized.get("volume"))

    normalized["ohlcv"] = {
        "open": number_or_none(normalized.get("day_open")) or close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": volume,
    }
    return normalized


def latest_number(values: Any) -> float | None:
    if not isinstance(values, list):
        return None
    for value in reversed(values):
        number = number_or_none(value)
        if number is not None:
            return number
    return None


def number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
