from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict as _asdict, is_dataclass
from datetime import date as _date, datetime, time as _time, timezone as _timezone
from zoneinfo import ZoneInfo
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.auth.router import router as auth_router
from ai_stock_sentinel.config import configure_logging, STRATEGY_VERSION
from ai_stock_sentinel.db.models import StockAnalysisCache, StockRawData, UserPortfolio
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.graph.builder import build_graph
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.main import build_graph_deps
from ai_stock_sentinel.data_sources.fundamental.tools import fetch_fundamental_data
from ai_stock_sentinel.data_sources.institutional_flow.tools import fetch_institutional_flow
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler, check_symbol_exists
from ai_stock_sentinel.services.history_loader import (
    backfill_yesterday_indicators,
    load_yesterday_context,
)
from ai_stock_sentinel.user_models.user import User

configure_logging()
logger = logging.getLogger(__name__)


class AnalyzeRequest(BaseModel):
    symbol: str = Field(default="2330.TW", min_length=1)
    news_text: str | None = None


class PositionAnalyzeRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    entry_price: float = Field(gt=0)
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
    is_final: bool = True
    intraday_disclaimer: str | None = None
    strategy_version: str | None = None

    class ErrorDetail(BaseModel):
        code: str
        message: str

    errors: list[ErrorDetail] = Field(default_factory=list)


# ─── 快取常數 ───────────────────────────────────────────────
_TZ_TAIPEI = ZoneInfo("Asia/Taipei")
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
    strategy_version: str | None = None


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

    - analysis_is_final=TRUE：直接回傳
    - analysis_is_final=FALSE + 盤中：回傳含免責聲明
    - analysis_is_final=FALSE + 收盤後：回傳 None（強制重新分析）
    """
    # 版本一致性檢查：快取版本與當前版本不一致時，視為失效
    if cache.strategy_version != STRATEGY_VERSION:
        logger.info(json.dumps({
            "event": "cache_version_mismatch",
            "symbol": cache.symbol,
            "cache_version": cache.strategy_version,
            "current_version": STRATEGY_VERSION,
        }))
        return None  # 觸發重新分析

    if cache.analysis_is_final:
        return _build_analysis_response(
            symbol=cache.symbol,
            action_tag=cache.action_tag,
            signal_confidence=float(cache.signal_confidence) if cache.signal_confidence else None,
            recommended_action=cache.recommended_action,
            final_verdict=cache.final_verdict,
            is_final=True,
            strategy_version=cache.strategy_version,
        )
    if now_time < MARKET_CLOSE:
        return _build_analysis_response(
            symbol=cache.symbol,
            action_tag=cache.action_tag,
            signal_confidence=float(cache.signal_confidence) if cache.signal_confidence else None,
            recommended_action=cache.recommended_action,
            final_verdict=cache.final_verdict,
            is_final=False,
            strategy_version=cache.strategy_version,
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
    """UPSERT 分析結果至 stock_analysis_cache（跨使用者共用）。"""
    db.execute(
        text("""
            INSERT INTO stock_analysis_cache (
                symbol, record_date, signal_confidence, strategy_version, action_tag,
                recommended_action, indicators, final_verdict,
                prev_action_tag, prev_confidence, analysis_is_final, full_result, updated_at
            ) VALUES (
                :symbol, CURRENT_DATE, :signal_confidence, :strategy_version, :action_tag,
                :recommended_action, CAST(:indicators AS jsonb), :final_verdict,
                (SELECT action_tag FROM stock_analysis_cache
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1),
                (SELECT signal_confidence FROM stock_analysis_cache
                 WHERE symbol = :symbol AND record_date = CURRENT_DATE - 1),
                :analysis_is_final, CAST(:full_result AS jsonb), NOW()
            )
            ON CONFLICT (symbol, record_date) DO UPDATE SET
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
            "symbol":             data.get("symbol"),
            "signal_confidence":  data.get("signal_confidence"),
            "strategy_version":   STRATEGY_VERSION,
            "action_tag":         data.get("action_tag"),
            "recommended_action": data.get("recommended_action"),
            "indicators":         json.dumps(data.get("indicators") or {}),
            "final_verdict":      data.get("final_verdict"),
            "analysis_is_final":  data.get("is_final", False),
            "full_result":        json.dumps(data.get("full_result") or {}),
        }
    )
    db.commit()


def _extract_indicators(result: dict) -> dict:
    """從 graph result 提取 indicators JSONB 快照。"""
    snapshot = result.get("snapshot") or {}
    inst = result.get("institutional_flow") or {}
    action_plan = result.get("action_plan") or {}
    cleaned_news = result.get("cleaned_news") or {}
    return {
        "ma5":             snapshot.get("ma5"),
        "ma20":            snapshot.get("ma20"),
        "ma60":            snapshot.get("ma60"),
        "rsi_14":          result.get("rsi14"),
        "close_price":     snapshot.get("current_price"),
        "volume_ratio":    snapshot.get("volume_ratio"),
        "strategy_type":   result.get("strategy_type"),
        "conviction_level": action_plan.get("conviction_level"),
        "sentiment_label": cleaned_news.get("sentiment_label"),
        "flow_label":      inst.get("flow_label") if not inst.get("error") else None,
        "technical_signal": result.get("technical_signal"),
        "institutional": {
            "foreign_net": inst.get("foreign_net"),
            "trust_net":   inst.get("trust_net"),
            "dealer_net":  inst.get("dealer_net"),
        } if not inst.get("error") else None,
    }


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
            "user_id":            data.get("user_id"),
            "symbol":             data.get("symbol"),
            "signal_confidence":  data.get("signal_confidence"),
            "strategy_version":   STRATEGY_VERSION,
            "action_tag":         data.get("action_tag"),
            "recommended_action": data.get("recommended_action"),
            "indicators":         json.dumps(data.get("indicators") or {}),
            "final_verdict":      data.get("final_verdict"),
            "analysis_is_final":  data.get("is_final", False),
        }
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
    upsert_analysis_log(db, {
        "user_id":            user_id,
        "symbol":             symbol,
        "signal_confidence":  float(cache.signal_confidence) if cache.signal_confidence else None,
        "action_tag":         cache.action_tag,
        "recommended_action": cache.recommended_action,
        "indicators":         cache.indicators or {},
        "final_verdict":      cache.final_verdict,
        "is_final":           is_final,  # mapped to analysis_is_final in upsert_analysis_log
    })


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
    upsert_analysis_log(db, {
        "user_id":            user_id,
        "symbol":             symbol,
        "signal_confidence":  result.get("signal_confidence"),
        "action_tag":         result.get("action_plan_tag"),
        "recommended_action": result.get("recommended_action"),
        "indicators":         _extract_indicators(result),
        "final_verdict":      result.get("analysis"),
        "is_final":           is_final,
    })


def _build_response_from_cache(
    hit: CachedAnalyzeResponse,
    symbol: str,
    full_result: dict | None = None,
) -> AnalyzeResponse:
    """把快取命中的結果轉成 AnalyzeResponse。

    若 full_result 存在，直接用它還原完整欄位；
    否則 fallback 到精簡欄位（舊資料相容）。
    """
    if full_result:
        try:
            resp = AnalyzeResponse.model_validate(full_result)
            resp.is_final = hit.is_final  # CachedAnalyzeResponse.is_final → AnalyzeResponse.is_final (API 對外欄位)
            resp.intraday_disclaimer = hit.intraday_disclaimer
            resp.strategy_version = hit.strategy_version  # 快取命中時回傳快取的版本（可能為 NULL）
            return resp
        except Exception:
            # Schema drift — fallback to sparse fields from cache metadata
            pass
    return AnalyzeResponse(
        snapshot={},
        analysis=hit.final_verdict or "",
        signal_confidence=int(hit.signal_confidence) if hit.signal_confidence is not None else None,
        action_plan_tag=hit.action_tag,
        is_final=hit.is_final,
        intraday_disclaimer=hit.intraday_disclaimer,
        strategy_version=hit.strategy_version,
    )


def verify_internal_api_key(x_internal_api_key: str = Header(default=None)):
    if not INTERNAL_API_KEY:
        raise HTTPException(status_code=503, detail="Internal API key not configured")
    if x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


class FetchRawDataRequest(BaseModel):
    symbol: str
    date: str = "today"


# ─── Graph Singleton ─────────────────────────────────────────
def _build_graph_singleton():
    crawler, analyzer, rss_client, news_cleaner = build_graph_deps()
    return build_graph(crawler=crawler, analyzer=analyzer, rss_client=rss_client, news_cleaner=news_cleaner)

_graph = _build_graph_singleton()

def get_graph():
    return _graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    from alembic import command
    from alembic.config import Config
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        logging.getLogger(__name__).error("Alembic migration failed: %s", exc, exc_info=True)
    yield


app = FastAPI(title="AI Stock Sentinel API", version="v1", lifespan=lifespan)

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

from ai_stock_sentinel.portfolio.history_router import router as history_router
app.include_router(history_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _check_symbol_exists(symbol: str) -> None:
    """yfinance 輕量驗證：代號無效時拋 HTTP 404，避免跑 LLM。"""
    if not check_symbol_exists(symbol):
        raise HTTPException(status_code=404, detail=f"查詢目標不存在：{symbol}")


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
    if analysis_detail is not None:
        from ai_stock_sentinel.analysis.langchain_analyzer import PROMPT_HASH
        analysis_detail = {**analysis_detail, "prompt_hash": PROMPT_HASH}
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
        strategy_version=STRATEGY_VERSION,
    )


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    payload: AnalyzeRequest,
    graph=Depends(get_graph),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyzeResponse:
    now_time = datetime.now(_TZ_TAIPEI).time()

    # L1：快取命中檢查
    cache = get_analysis_cache(db, payload.symbol)
    if cache:
        hit = _handle_cache_hit(cache, now_time)
        if hit:
            _maybe_upsert_log(db, current_user.id, payload.symbol, cache, hit.is_final)
            return _build_response_from_cache(hit, payload.symbol, full_result=cache.full_result)

    _check_symbol_exists(payload.symbol)
    backfill_yesterday_indicators(db, payload.symbol)
    prev_context = load_yesterday_context(payload.symbol, db)

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
        "prev_context": prev_context,
        "is_final": now_time >= MARKET_CLOSE,
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

    is_final = now_time >= MARKET_CLOSE
    _response = _build_response(result)
    upsert_analysis_cache(db, {
        "symbol":             payload.symbol,
        "signal_confidence":  result.get("signal_confidence"),
        "action_tag":         result.get("action_plan_tag"),
        "recommended_action": result.get("recommended_action"),
        "indicators":         _extract_indicators(result),
        "final_verdict":      result.get("analysis"),
        "is_final":           is_final,
        "full_result":        _response.model_dump(),
    })
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
    return response


@app.post("/internal/fetch-raw-data")
def fetch_raw_data_endpoint(
    payload: FetchRawDataRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_internal_api_key),
):
    """n8n cron 呼叫的內部端點，抓取原始數據並存入 stock_raw_data。

    只抓資料（technical/institutional/fundamental），不跑 LLM 分析。
    """
    from datetime import date as _date_type

    record_date = _date_type.today() if payload.date == "today" else _date_type.fromisoformat(payload.date)

    crawler = YFinanceCrawler()
    try:
        snapshot = crawler.fetch_basic_snapshot(payload.symbol)
        technical = _asdict(snapshot) if is_dataclass(snapshot) else dict(snapshot)
    except Exception:
        raise HTTPException(status_code=404, detail=f"查詢目標不存在：{payload.symbol}")

    if not technical.get("recent_closes"):
        raise HTTPException(status_code=404, detail=f"查詢目標不存在：{payload.symbol}")

    institutional = fetch_institutional_flow(payload.symbol, days=10)

    current_price = float(technical.get("current_price") or 0)
    fundamental = fetch_fundamental_data(payload.symbol, current_price)

    fetch_and_store_raw_data(
        db,
        payload.symbol,
        technical=technical,
        institutional=institutional,
        fundamental=fundamental,
        raw_data_is_final=True,
    )

    # raw data 已定稿，將同日盤中未定稿的分析 log 一併標記為 final
    db.execute(
        text("""
            UPDATE daily_analysis_log
            SET analysis_is_final = TRUE
            WHERE symbol          = :symbol
              AND record_date     = :record_date
              AND analysis_is_final = FALSE
        """),
        {"symbol": payload.symbol, "record_date": record_date.isoformat()},
    )
    db.commit()

    return {"status": "ok", "symbol": payload.symbol, "record_date": record_date.isoformat()}


def fetch_and_store_raw_data(
    db: Session,
    symbol: str,
    *,
    technical: dict | None,
    institutional: dict | None,
    fundamental: dict | None,
    raw_data_is_final: bool = False,
) -> None:
    """將 graph result 的原始資料 UPSERT 至 stock_raw_data（今日）。

    - technical      ← graph result["snapshot"]
    - institutional  ← graph result["institutional_flow"]
    - fundamental    ← graph result["fundamental_data"]

    使用 ON CONFLICT (symbol, record_date) DO UPDATE，同日只存一筆。
    若籌碼面含 'error' 鍵，仍寫入（保留原始錯誤資訊以供 debug）。
    """
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
            "symbol":             symbol,
            "technical":          json.dumps(technical or {}),
            "institutional":      json.dumps(institutional or {}),
            "fundamental":        json.dumps(fundamental or {}),
            "raw_data_is_final":  raw_data_is_final,
        }
    )
    db.commit()


class HistoryEntry(BaseModel):
    record_date:       str
    signal_confidence: float | None
    action_tag:        str | None
    prev_action_tag:   str | None
    prev_confidence:   float | None
    analysis_is_final: bool
    indicators:        dict[str, Any] | None
    final_verdict:     str | None


def fetch_symbol_history(
    symbol: str,
    db: Session,
    *,
    days: int = 30,
) -> list:
    """從 stock_analysis_cache 讀取指定股票的歷史診斷紀錄。

    查 stock_analysis_cache（不含 user_id，跨使用者共用），供即時分析視窗
    查看該股歷史趨勢，不需要使用者有持倉。
    若需查詢持倉診斷變化，請使用 GET /portfolio/{id}/history
    （查 daily_analysis_log，含 user_id，已在 Phase 7 實作）。
    """
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


@app.get("/history/{symbol}", response_model=list[HistoryEntry])
def get_symbol_history(
    symbol: str,
    days: int = 30,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[HistoryEntry]:
    logs = fetch_symbol_history(symbol, db, days=days)
    return [
        HistoryEntry(
            record_date=       str(log.record_date),
            signal_confidence= float(log.signal_confidence) if log.signal_confidence else None,
            action_tag=        log.action_tag,
            prev_action_tag=   log.prev_action_tag,
            prev_confidence=   float(log.prev_confidence) if log.prev_confidence else None,
            analysis_is_final= bool(log.analysis_is_final),
            indicators=        log.indicators,
            final_verdict=     log.final_verdict,
        )
        for log in logs
    ]


@app.post("/analyze/position", response_model=AnalyzeResponse)
def analyze_position(
    payload: PositionAnalyzeRequest,
    graph=Depends(get_graph),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AnalyzeResponse:
    now_time = datetime.now(_TZ_TAIPEI).time()

    # L1：快取命中檢查（position 分析須確認 full_result 含 position_analysis，否則強制重跑）
    cache = get_analysis_cache(db, payload.symbol)
    if cache:
        hit = _handle_cache_hit(cache, now_time)
        if hit:
            full = cache.full_result or {}
            if full.get("position_analysis") is not None:
                _maybe_upsert_log(db, current_user.id, payload.symbol, cache, hit.is_final)
                return _build_response_from_cache(hit, payload.symbol, full_result=full)

    _check_symbol_exists(payload.symbol)
    backfill_yesterday_indicators(db, payload.symbol)
    prev_context = load_yesterday_context(payload.symbol, db)

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
        "prev_context": prev_context,
        "is_final": now_time >= MARKET_CLOSE,
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

    is_final = now_time >= MARKET_CLOSE
    _response = _build_response(result)
    upsert_analysis_cache(db, {
        "symbol":             payload.symbol,
        "signal_confidence":  result.get("signal_confidence"),
        "action_tag":         result.get("action_plan_tag"),
        "recommended_action": result.get("recommended_action"),
        "indicators":         _extract_indicators(result),
        "final_verdict":      result.get("analysis"),
        "is_final":           is_final,
        "full_result":        _response.model_dump(),
    })
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
    return response
