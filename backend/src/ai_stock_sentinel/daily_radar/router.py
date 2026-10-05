"""Daily Radar HTTP composition; workflow and presentation live in dedicated modules."""

from fastapi import APIRouter

from ai_stock_sentinel.daily_radar import (
    evidence_router,
    maintenance_router,
    read_router,
    refresh_router,
    run_router,
)

router = APIRouter(tags=["daily-radar"])
router.include_router(refresh_router.router)
router.include_router(evidence_router.router)
router.include_router(maintenance_router.router)
router.include_router(run_router.router)
router.include_router(read_router.router)

__all__ = ["router"]
