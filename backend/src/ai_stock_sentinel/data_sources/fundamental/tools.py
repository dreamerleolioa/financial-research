from __future__ import annotations
import logging
from dataclasses import asdict

from ai_stock_sentinel.data_sources.fundamental.finmind_provider import FinMindFundamentalProvider
from ai_stock_sentinel.data_sources.fundamental.interface import FundamentalError

logger = logging.getLogger(__name__)


def fetch_fundamental_data(symbol: str, current_price: float) -> dict:
    """高階工具函式：取得基本面估值資料，失敗時回傳帶 error 鍵的 dict，不拋例外。"""
    provider = FinMindFundamentalProvider()
    try:
        data = provider.fetch(symbol, current_price)
        return asdict(data)
    except FundamentalError as e:
        logger.warning("FundamentalProvider error [%s]: %s", e.code, e)
        return {"error": e.code, "message": str(e), "symbol": symbol}
    except Exception as e:
        logger.exception("Unexpected error in fetch_fundamental_data")
        return {"error": "FUNDAMENTAL_UNKNOWN_ERROR", "message": str(e), "symbol": symbol}
