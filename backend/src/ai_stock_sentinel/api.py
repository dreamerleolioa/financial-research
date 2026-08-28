from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ai_stock_sentinel.analysis.router import router as analysis_router
from ai_stock_sentinel.auth.router import router as auth_router
from ai_stock_sentinel.calibration.router import router as calibration_router
from ai_stock_sentinel.config import configure_logging
from ai_stock_sentinel.daily_radar.router import router as daily_radar_router
from ai_stock_sentinel.data_sources.fundamental.router import router as fundamental_router
from ai_stock_sentinel.portfolio.history_router import router as history_router
from ai_stock_sentinel.portfolio.router import router as portfolio_router
from ai_stock_sentinel.watchlist.router import router as watchlist_router

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    from alembic import command
    from alembic.config import Config

    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    except Exception:
        logging.getLogger(__name__).exception("Alembic migration failed")
        raise
    yield


app = FastAPI(title="AI Stock Sentinel API", version="v1", lifespan=lifespan)


def _sanitize_validation_error_value(value: Any) -> Any:
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
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
    expose_headers=["Retry-After"],
    allow_credentials=True,
)

app.include_router(analysis_router)
app.include_router(auth_router)
app.include_router(portfolio_router)
app.include_router(history_router)
app.include_router(daily_radar_router)
app.include_router(calibration_router)
app.include_router(watchlist_router)
app.include_router(fundamental_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
