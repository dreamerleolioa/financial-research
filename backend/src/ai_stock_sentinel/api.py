from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from dataclasses import asdict as _asdict, is_dataclass
from datetime import date as _date_type
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.application.analysis_cache import fetch_and_store_raw_data
from ai_stock_sentinel.analysis.router import router as analysis_router
from ai_stock_sentinel.analysis.schemas import FetchRawDataRequest
from ai_stock_sentinel.auth.router import router as auth_router
from ai_stock_sentinel.config import configure_logging
from ai_stock_sentinel.daily_radar.router import router as daily_radar_router
from ai_stock_sentinel.data_sources.fundamental.tools import fetch_fundamental_data
from ai_stock_sentinel.data_sources.institutional_flow.tools import fetch_institutional_flow
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.portfolio.history_router import router as history_router
from ai_stock_sentinel.portfolio.router import router as portfolio_router
from ai_stock_sentinel.watchlist.router import router as watchlist_router

configure_logging()

INTERNAL_API_KEY: str = os.environ.get("INTERNAL_API_KEY", "")


def verify_internal_api_key(x_internal_api_key: str = Header(default=None)):
    if not INTERNAL_API_KEY:
        raise HTTPException(status_code=503, detail="Internal API key not configured")
    if x_internal_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


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


def _sanitize_validation_error_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _sanitize_validation_error_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_validation_error_value(item) for item in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": _sanitize_validation_error_value(exc.errors())},
    )


_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174")
_allowed_origins = [origin.strip() for origin in _cors_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(analysis_router)
app.include_router(auth_router)
app.include_router(portfolio_router)
app.include_router(history_router)
app.include_router(daily_radar_router)
app.include_router(watchlist_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/internal/fetch-raw-data")
def fetch_raw_data_endpoint(
    payload: FetchRawDataRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_internal_api_key),
):
    """n8n cron 呼叫的內部端點，抓取原始數據並存入 stock_raw_data。"""
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
