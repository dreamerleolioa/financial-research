"""Provider factories and shared policy for Daily Radar HTTP workflows."""

from __future__ import annotations

from datetime import date

from fastapi import Depends
from sqlalchemy.orm import Session

from ai_stock_sentinel.clock import today_taipei
from ai_stock_sentinel.daily_radar.background_context import BackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.default_background_context import (
    DefaultBackgroundChipContextProvider,
)
from ai_stock_sentinel.daily_radar.institutional_archive_universe import (
    ArchivedInstitutionalUniverseProvider,
)
from ai_stock_sentinel.daily_radar.institutional_flow_provider import (
    OfficialTaiwanInstitutionalReportProvider,
)
from ai_stock_sentinel.daily_radar.institutional_flow_service import InstitutionalReportProvider
from ai_stock_sentinel.daily_radar.market_bar_provider import OfficialTaiwanMarketBarProvider
from ai_stock_sentinel.daily_radar.market_context import (
    MarketIndexContextProvider,
    YFinanceMarketIndexContextProvider,
)
from ai_stock_sentinel.daily_radar.market_session import (
    MarketSessionProvider,
    TwseMarketSessionProvider,
)
from ai_stock_sentinel.daily_radar.raw_data import (
    BatchTechnicalFetcher,
    LocalFirstBatchTechnicalFetcher,
)
from ai_stock_sentinel.daily_radar.universe import DailyRadarUniverseProvider
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.phase1_avwap.provider import (
    ArchiveFirstDailyPriceProvider,
    TwseDailyPriceProvider,
)
from ai_stock_sentinel.phase1_avwap.service import DailyPriceProvider


DAILY_RUN_REFRESH_CONTEXT_TYPES = ("lending", "full_margin")


DAILY_RADAR_MAX_UNIVERSE_SYMBOLS = 250


DAILY_RADAR_REQUIRED_REFRESH_STEPS = (
    "refresh-institutional-flows",
    "refresh-lending",
    "refresh-full-margin",
    "refresh-ohlcv",
    "refresh-market-context",
)


def get_daily_radar_universe_provider(
    db: Session = Depends(get_db),
) -> DailyRadarUniverseProvider:
    return ArchivedInstitutionalUniverseProvider(db)


def get_daily_radar_technical_fetcher(
    db: Session = Depends(get_db),
) -> BatchTechnicalFetcher:
    return LocalFirstBatchTechnicalFetcher(db)


def get_taiwan_market_bar_provider() -> OfficialTaiwanMarketBarProvider:
    return OfficialTaiwanMarketBarProvider()


def get_taiwan_institutional_report_provider() -> InstitutionalReportProvider:
    return OfficialTaiwanInstitutionalReportProvider()


def get_daily_radar_market_context_provider() -> MarketIndexContextProvider:
    return YFinanceMarketIndexContextProvider()


def get_daily_radar_market_session_provider() -> MarketSessionProvider:
    return TwseMarketSessionProvider()


def get_daily_radar_background_chip_context_provider() -> BackgroundChipContextProvider:
    return DefaultBackgroundChipContextProvider()


def get_phase1_avwap_daily_price_provider(
    db: Session = Depends(get_db),
) -> DailyPriceProvider:
    return ArchiveFirstDailyPriceProvider(db, fallback_provider=TwseDailyPriceProvider())


def _backend_today() -> date:
    return today_taipei()
