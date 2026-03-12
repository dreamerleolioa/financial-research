from __future__ import annotations

import os
from dataclasses import asdict as _asdict, is_dataclass
from datetime import date as _date, datetime, time as _time
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.auth.router import router as auth_router
from ai_stock_sentinel.config import configure_logging
from ai_stock_sentinel.db.models import StockAnalysisCache, StockRawData, UserPortfolio
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.graph.builder import build_graph
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.main import build_graph_deps
from ai_stock_sentinel.user_models.user import User

configure_logging()


class AnalyzeRequest(BaseModel):
    symbol: str = Field(default="2330.TW", min_length=1)
    news_text: str | None = None


class PositionAnalyzeRequest(BaseModel):
    symbol: str
    entry_price: float
    entry_date: str | None = None
    quantity: int | None = None


class PositionAnalysis(BaseModel):
    entry_price: float
    profit_loss_pct: float | None = None
    position_status: str | None = None
    position_narrative: str | None = None
    recommended_action: str | None = None
    trailing_stop: float | None = None
    trailing_stop_reason: str | None = None
    exit_reason: str | None = None


class AnalyzeResponse(BaseModel):
    snapshot: dict[str, Any] = Field(default_factory=dict)
    analysis: str = ""
    analysis_detail: dict[str, Any] | None = None
    cleaned_news: dict[str, Any] | None = None
    confidence_score: int | None = None
    cross_validation_note: str | None = None
    strategy_type: str | None = None
    entry_zone: str | None = None
    stop_loss: str | None = None
    holding_period: str | None = None
    cleaned_news_quality: dict[str, Any] | None = None
    news_display: dict[str, Any] | None = None
    news_display_items: list[dict[str, Any]] = Field(default_factory=list)
    data_confidence: int | None = None
    signal_confidence: int | None = None
    action_plan_tag: str | None = None
    institutional_flow_label: str | None = None
    sentiment_label: str | None = None
    action_plan: dict[str, Any] | None = None
    data_sources: list[str] = Field(default_factory=list)
    fundamental_data: dict[str, Any] | None = None
    position_analysis: PositionAnalysis | None = None

    class ErrorDetail(BaseModel):
        code: str
        message: str

    errors: list[ErrorDetail] = Field(default_factory=list)


# ─── 快取常數 ───────────────────────────────────────────────
MARKET_CLOSE = _time(13, 30)
INTRADAY_DISCLAIMER = (
    "⚠️ 注意：目前為盤中階段（指標未收定），"
    "以下分析僅供即時參考，不代表當日收盤定論。"
)

INTERNAL_API_KEY: str = os.environ.get("INTERNAL_API_KEY", "")


# ─── 快取 Response Schema ────────────────────────────────────
class CachedAnalyzeResponse(BaseModel):
    symbol: str
    signal_confidence: float | None
    action_tag: str | None
    recommended_action: str | None
    final_verdict: str | None
    is_final: bool
    intraday_disclaimer: Optional[str] = None


# ─── 快取輔助函式 ─────────────────────────────────────────────

def get_analysis_cache(db: Session, symbol: str) -> StockAnalysisCache | None:
    """查詢今日的分析結果快取（L1）。"""
    return db.execute(
        select(StockAnalysisCache).where(
            StockAnalysisCache.symbol == symbol,
            StockAnalysisCache.record_date == _date.today(),
        )
    ).scalar_one_or_none()


def get_raw_data(db: Session, symbol: str) -> StockRawData | None:
    """查詢今日的原始數據快取（L2）。"""
    return db.execute(
        select(StockRawData).where(
            StockRawData.symbol == symbol,
            StockRawData.record_date == _date.today(),
        )
    ).scalar_one_or_none()


def _handle_cache_hit(
    cache: StockAnalysisCache,
    now_time: _time,
) -> CachedAnalyzeResponse | None:
    """處理 L1 快取命中邏輯。

    - is_final=TRUE：直接回傳
    - is_final=FALSE + 盤中：回傳含免責聲明
    - is_final=FALSE + 收盤後：回傳 None（強制重新分析）
    """
    if cache.is_final:
        return _build_analysis_response(
            symbol=cache.symbol,
            action_tag=cache.action_tag,
            signal_confidence=float(cache.signal_confidence) if cache.signal_confidence else None,
            recommended_action=cache.recommended_action,
            final_verdict=cache.final_verdict,
            is_final=True,
        )
    if now_time < MARKET_CLOSE:
        return _build_analysis_response(
            symbol=cache.symbol,
            action_tag=cache.action_tag,
            signal_confidence=float(cache.signal_confidence) if cache.signal_confidence else None,
            recommended_action=cache.recommended_action,
            final_verdict=cache.final_verdict,
            is_final=False,
        )
    return None  # 收盤後非定稿快取 → 強制重新分析


def _build_analysis_response(
    *,
    symbol: str,
    action_tag: str | None,
    signal_confidence: float | None,
    recommended_action: str | None,
    final_verdict: str | None,
    is_final: bool,
) -> CachedAnalyzeResponse:
    return CachedAnalyzeResponse(
        symbol=symbol,
        signal_confidence=signal_confidence,
        action_tag=action_tag,
        recommended_action=recommended_action,
        final_verdict=final_verdict,
        is_final=is_final,
        intraday_disclaimer=INTRADAY_DISCLAIMER if not is_final else None,
    )


def upsert_analysis_cache(db: Session, data: dict) -> None:
    """UPSERT 分析結果至 stock_analysis_cache（跨使用者共用）。"""
    import json
    db.execute(
        text("""
            INSERT INTO stock_analysis_cache (
                symbol, record_date, signal_confidence, action_tag,
                recommended_action, indicators, final_verdict,
                prev_action_tag, prev_confidence, is_final, updated_at
            ) VALUES (
                :symbol, CURRENT_DATE, :signal_confidence, :action_tag,
                :recommended_action, :indicators::jsonb, :final_verdict,
                (SELECT action_tag FROM stock_analysis_cache
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1),
                (SELECT signal_confidence FROM stock_analysis_cache
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1),
                :is_final, NOW()
            )
            ON CONFLICT (symbol, record_date) DO UPDATE SET
                signal_confidence  = EXCLUDED.signal_confidence,
                action_tag         = EXCLUDED.action_tag,
                recommended_action = EXCLUDED.recommended_action,
                indicators         = EXCLUDED.indicators,
                final_verdict      = EXCLUDED.final_verdict,
                is_final           = EXCLUDED.is_final,
                updated_at         = NOW()
        """),
        {
            "symbol":             data.get("symbol"),
            "signal_confidence":  data.get("signal_confidence"),
            "action_tag":         data.get("action_tag"),
            "recommended_action": data.get("recommended_action"),
            "indicators":         json.dumps(data.get("indicators") or {}),
            "final_verdict":      data.get("final_verdict"),
            "is_final":           data.get("is_final", False),
        }
    )
    db.commit()


def _extract_indicators(result: dict) -> dict:
    """從 graph result 提取 indicators JSONB 快照。"""
    snapshot = result.get("snapshot") or {}
    inst = result.get("institutional_flow") or {}
    return {
        "ma5":          snapshot.get("ma5"),
        "ma20":         snapshot.get("ma20"),
        "ma60":         snapshot.get("ma60"),
        "rsi_14":       result.get("rsi14"),
        "close_price":  snapshot.get("current_price"),
        "volume_ratio": snapshot.get("volume_ratio"),
        "institutional": {
            "foreign_net": inst.get("foreign_net"),
            "trust_net":   inst.get("trust_net"),
            "dealer_net":  inst.get("dealer_net"),
        } if not inst.get("error") else None,
    }


def verify_internal_api_key(x_internal_api_key: str = Header(default=None)):
    if not INTERNAL_API_KEY or x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


class FetchRawDataRequest(BaseModel):
    symbol: str
    date: str = "today"


def get_graph():
    crawler, analyzer, rss_client, news_cleaner = build_graph_deps()
    return build_graph(crawler=crawler, analyzer=analyzer, rss_client=rss_client, news_cleaner=news_cleaner)


app = FastAPI(title="AI Stock Sentinel API", version="v1")


@app.on_event("startup")
def run_migrations() -> None:
    import logging

    from alembic import command
    from alembic.config import Config

    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        logging.getLogger(__name__).warning("Alembic migration skipped: %s", exc)

_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174")
_allowed_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(auth_router)

from ai_stock_sentinel.portfolio.router import router as portfolio_router
app.include_router(portfolio_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _build_response(result: dict[str, Any]) -> AnalyzeResponse:
    """Shared serialization logic for both /analyze and /analyze/position."""
    snapshot = result.get("snapshot")
    analysis = result.get("analysis")
    raw_detail = result.get("analysis_detail")
    analysis_detail: dict[str, Any] | None = None
    if raw_detail is not None:
        if is_dataclass(raw_detail) and not isinstance(raw_detail, type):
            analysis_detail = _asdict(raw_detail)
        elif isinstance(raw_detail, dict):
            analysis_detail = raw_detail
    response_errors: list[AnalyzeResponse.ErrorDetail] = [
        AnalyzeResponse.ErrorDetail(code=e["code"], message=e["message"])
        for e in result.get("errors", [])
    ]

    if not isinstance(snapshot, dict):
        response_errors.append(
            AnalyzeResponse.ErrorDetail(
                code="MISSING_SNAPSHOT",
                message="Graph result missing valid snapshot payload.",
            )
        )
        snapshot = {}

    if not isinstance(analysis, str):
        response_errors.append(
            AnalyzeResponse.ErrorDetail(
                code="MISSING_ANALYSIS",
                message="Graph result missing valid analysis payload.",
            )
        )
        analysis = ""

    inst_flow = result.get("institutional_flow")
    institutional_flow_label: str | None = None
    if inst_flow and not inst_flow.get("error"):
        institutional_flow_label = inst_flow.get("flow_label")

    sentiment_label: str | None = (
        result.get("cleaned_news", {}).get("sentiment_label")
        if result.get("cleaned_news") else None
    )

    action_plan: dict[str, Any] | None = result.get("action_plan")

    _sources: list[str] = []
    if result.get("raw_news_items"):
        _sources.append("google-news-rss")
    if result.get("snapshot"):
        _sources.append("yfinance")
    _inst = result.get("institutional_flow")
    if _inst and not _inst.get("error"):
        _sources.append(_inst.get("provider", "institutional-api"))
    _fund = result.get("fundamental_data")
    if _fund and not _fund.get("error"):
        _sources.append("finmind-fundamental")

    position_analysis: PositionAnalysis | None = None
    if result.get("entry_price") is not None:
        position_analysis = PositionAnalysis(
            entry_price=result["entry_price"],
            profit_loss_pct=result.get("profit_loss_pct"),
            position_status=result.get("position_status"),
            position_narrative=result.get("position_narrative"),
            recommended_action=result.get("recommended_action"),
            trailing_stop=result.get("trailing_stop"),
            trailing_stop_reason=result.get("trailing_stop_reason"),
            exit_reason=result.get("exit_reason"),
        )

    return AnalyzeResponse(
        snapshot=snapshot,
        analysis=analysis,
        analysis_detail=analysis_detail,
        cleaned_news=result.get("cleaned_news"),
        cleaned_news_quality=result.get("cleaned_news_quality"),
        news_display=result.get("news_display"),
        news_display_items=result.get("news_display_items") or [],
        confidence_score=result.get("confidence_score"),
        cross_validation_note=result.get("cross_validation_note"),
        strategy_type=result.get("strategy_type"),
        entry_zone=result.get("entry_zone"),
        stop_loss=result.get("stop_loss"),
        holding_period=result.get("holding_period"),
        data_confidence=result.get("data_confidence"),
        signal_confidence=result.get("signal_confidence"),
        action_plan_tag=result.get("action_plan_tag"),
        institutional_flow_label=institutional_flow_label,
        sentiment_label=sentiment_label,
        action_plan=action_plan,
        data_sources=_sources,
        fundamental_data=result.get("fundamental_data"),
        position_analysis=position_analysis,
        errors=response_errors,
    )


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    payload: AnalyzeRequest,
    graph=Depends(get_graph),
    current_user: User = Depends(get_current_user),
) -> AnalyzeResponse:
    initial_state: GraphState = {
        "symbol": payload.symbol,
        "news_content": payload.news_text,
        "snapshot": None,
        "analysis": None,
        "analysis_detail": None,
        "cleaned_news": None,
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "technical_context": None,
        "institutional_context": None,
        "institutional_flow": None,
        "strategy_type": None,
        "entry_zone": None,
        "stop_loss": None,
        "holding_period": None,
        "confidence_score": None,
        "cross_validation_note": None,
        "cleaned_news_quality": None,
        "news_display": None,
        "news_display_items": [],
        "data_confidence": None,
        "signal_confidence": None,
        "high_20d": None,
        "low_20d": None,
        "support_20d": None,
        "resistance_20d": None,
        "rsi14": None,
        "action_plan_tag": None,
        "action_plan": None,
        "fundamental_data": None,
        "fundamental_context": None,
    }

    try:
        result: dict[str, Any] = graph.invoke(initial_state)
    except Exception as exc:
        return AnalyzeResponse(
            errors=[
                AnalyzeResponse.ErrorDetail(
                    code="ANALYZE_RUNTIME_ERROR",
                    message=str(exc),
                )
            ]
        )

    return _build_response(result)


@app.post("/analyze/position", response_model=AnalyzeResponse)
def analyze_position(
    payload: PositionAnalyzeRequest,
    graph=Depends(get_graph),
    current_user: User = Depends(get_current_user),
) -> AnalyzeResponse:
    initial_state: GraphState = {
        "symbol": payload.symbol,
        "entry_price": payload.entry_price,
        "entry_date": payload.entry_date,
        "quantity": payload.quantity,
        "news_content": "",
        "snapshot": None,
        "analysis": None,
        "analysis_detail": None,
        "cleaned_news": None,
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "technical_context": None,
        "institutional_context": None,
        "institutional_flow": None,
        "strategy_type": None,
        "entry_zone": None,
        "stop_loss": None,
        "holding_period": None,
        "confidence_score": None,
        "cross_validation_note": None,
        "cleaned_news_quality": None,
        "news_display": None,
        "news_display_items": [],
        "data_confidence": None,
        "signal_confidence": None,
        "high_20d": None,
        "low_20d": None,
        "support_20d": None,
        "resistance_20d": None,
        "rsi14": None,
        "action_plan_tag": None,
        "action_plan": None,
        "fundamental_data": None,
        "fundamental_context": None,
        "profit_loss_pct": None,
        "cost_buffer_to_support": None,
        "position_status": None,
        "position_narrative": None,
        "trailing_stop": None,
        "trailing_stop_reason": None,
        "recommended_action": None,
        "exit_reason": None,
    }

    try:
        result: dict[str, Any] = graph.invoke(initial_state)
    except Exception as exc:
        return AnalyzeResponse(
            errors=[
                AnalyzeResponse.ErrorDetail(
                    code="ANALYZE_RUNTIME_ERROR",
                    message=str(exc),
                )
            ]
        )

    return _build_response(result)
