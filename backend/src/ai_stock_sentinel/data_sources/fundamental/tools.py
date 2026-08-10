from __future__ import annotations
import logging
import os
from dataclasses import asdict

from ai_stock_sentinel.data_sources.fundamental.finmind_provider import FinMindFundamentalProvider
from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalError
from ai_stock_sentinel.data_sources.fundamental.official_provider import OfficialCachedFundamentalProvider
from ai_stock_sentinel.db.session import create_session

logger = logging.getLogger(__name__)


def fetch_fundamental_data(symbol: str, current_price: float) -> dict:
    """高階工具函式：取得基本面估值資料，失敗時回傳帶 error 鍵的 dict，不拋例外。"""
    provider_mode = os.getenv("FUNDAMENTAL_PROVIDER_MODE", "finmind_only")
    if provider_mode not in {"finmind_only", "official_cache_first", "official_cache_only"}:
        return {
            "error": "FUNDAMENTAL_PROVIDER_MODE_INVALID",
            "message": "Invalid FUNDAMENTAL_PROVIDER_MODE",
            "symbol": symbol,
        }
    if provider_mode == "finmind_only":
        return _fetch_with_provider(FinMindFundamentalProvider(), symbol=symbol, current_price=current_price)

    try:
        session = create_session()
    except Exception as exc:
        logger.exception("Unable to create fundamental cache session")
        return {
            "error": "FUNDAMENTAL_DATABASE_UNAVAILABLE",
            "message": str(exc),
            "symbol": symbol,
        }
    try:
        provider = OfficialCachedFundamentalProvider(
            session,
            provider_mode=provider_mode,
        )
        result = _fetch_with_provider(provider, symbol=symbol, current_price=current_price)
        if "error" in result:
            _safe_session_rollback(session)
        else:
            try:
                session.commit()
            except Exception as exc:
                logger.exception("Unable to commit fundamental cache session")
                _safe_session_rollback(session)
                return {
                    "error": "FUNDAMENTAL_DATABASE_UNAVAILABLE",
                    "message": str(exc),
                    "symbol": symbol,
                }
        return result
    finally:
        _safe_session_close(session)


def _fetch_with_provider(provider, *, symbol: str, current_price: float) -> dict:
    try:
        data = provider.fetch(symbol, current_price)
        return asdict(data)
    except FundamentalError as e:
        logger.warning("FundamentalProvider error [%s]: %s", e.code, e)
        return {"error": e.code, "message": str(e), "symbol": symbol}
    except Exception as e:
        logger.exception("Unexpected error in fetch_fundamental_data")
        return {"error": "FUNDAMENTAL_UNKNOWN_ERROR", "message": str(e), "symbol": symbol}


def _safe_session_rollback(session) -> None:
    try:
        session.rollback()
    except Exception:
        logger.exception("Unable to roll back fundamental cache session")


def _safe_session_close(session) -> None:
    try:
        session.close()
    except Exception:
        logger.exception("Unable to close fundamental cache session")
