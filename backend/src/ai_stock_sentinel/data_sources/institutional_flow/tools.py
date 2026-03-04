"""
fetch_institutional_flow：高階工具函式，封裝 Router 邏輯，供 LangGraph nodes 呼叫。

使用方式：
    from ai_stock_sentinel.data_sources.institutional_flow.tools import fetch_institutional_flow

    data = fetch_institutional_flow("2330.TW", days=5)
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import Any

from ai_stock_sentinel.data_sources.institutional_flow.finmind_provider import FinMindProvider
from ai_stock_sentinel.data_sources.institutional_flow.interface import InstitutionalFlowError
from ai_stock_sentinel.data_sources.institutional_flow.router import InstitutionalFlowRouter
from ai_stock_sentinel.data_sources.institutional_flow.tpex_provider import TpexProvider
from ai_stock_sentinel.data_sources.institutional_flow.twse_provider import TwseOpenApiProvider

logger = logging.getLogger(__name__)


def _build_default_router() -> InstitutionalFlowRouter:
    """建立預設 Router（FinMind → TWSE → TPEX），讀取環境變數 FINMIND_API_TOKEN。"""
    token = os.environ.get("FINMIND_API_TOKEN", "")
    return InstitutionalFlowRouter(
        providers=[
            FinMindProvider(api_token=token),
            TwseOpenApiProvider(),
            TpexProvider(),
        ]
    )


# 模組層級單例（懶建立），可被測試替換
_router: InstitutionalFlowRouter | None = None


def _get_router() -> InstitutionalFlowRouter:
    global _router
    if _router is None:
        _router = _build_default_router()
    return _router


def set_router(router: InstitutionalFlowRouter) -> None:
    """測試用：替換全局 Router（例如注入 mock provider）。"""
    global _router
    _router = router


def fetch_institutional_flow(
    symbol: str,
    days: int = 5,
    *,
    router: InstitutionalFlowRouter | None = None,
) -> dict[str, Any]:
    """
    拉取法人籌碼資料，輸出統一 JSON schema。

    Args:
        symbol: 股票代碼（例如 '2330.TW'、'6488.TWO'）
        days: 回溯天數（預設 5）
        router: 若傳入則使用指定 Router（測試/自訂用）

    Returns:
        dict，包含統一欄位（與 InstitutionalFlowData 對應）。
        失敗時回傳帶 `error` 鍵的 dict，錯誤碼為 INSTITUTIONAL_FETCH_ERROR。

    流程不拋出例外（防禦性設計），失敗時記錄 warning 並回傳 error dict。
    """
    _r = router or _get_router()
    try:
        data = _r.fetch_institutional_flow(symbol=symbol, days=days)
        result = asdict(data)
        logger.info(
            "[fetch_institutional_flow] 成功（symbol=%s, provider=%s, flow_label=%s）",
            symbol,
            data.source_provider,
            data.flow_label,
        )
        return result
    except InstitutionalFlowError as exc:
        logger.warning("[fetch_institutional_flow] 失敗：[%s] %s", exc.code, exc)
        return {
            "symbol": symbol,
            "period_days": days,
            "error": exc.code,
            "error_message": str(exc),
        }
    except Exception as exc:
        logger.error("[fetch_institutional_flow] 意外錯誤：%s", exc)
        return {
            "symbol": symbol,
            "period_days": days,
            "error": "INSTITUTIONAL_FETCH_ERROR",
            "error_message": str(exc),
        }
