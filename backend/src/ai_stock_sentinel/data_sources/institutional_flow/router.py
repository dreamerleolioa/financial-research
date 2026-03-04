"""Provider Router：依固定優先序 + 市場分流自動選擇資料源。"""
from __future__ import annotations

import logging
from typing import Sequence

from ai_stock_sentinel.data_sources.institutional_flow.interface import (
    InstitutionalFlowData,
    InstitutionalFlowError,
    InstitutionalFlowProvider,
)

logger = logging.getLogger(__name__)


def _detect_market(symbol: str) -> str:
    """根據後綴判斷市場：'.TW' → twse，'.TWO' → tpex，其他 → unknown。"""
    upper = symbol.upper()
    if upper.endswith(".TWO"):
        return "tpex"
    if upper.endswith(".TW"):
        return "twse"
    return "unknown"


class InstitutionalFlowRouter:
    """
    依固定優先序嘗試各 Provider，Primary 失敗自動切 Fallback。

    優先序由 providers 參數決定，呼叫端負責傳入正確順序：
      FinMindProvider → TwseOpenApiProvider → TpexProvider

    上市 (.TW)：全部 Provider 皆可嘗試
    上櫃 (.TWO)：跳過僅支援上市的 Provider（標有 twse_only=True）
    """

    def __init__(self, providers: Sequence[InstitutionalFlowProvider]):
        if not providers:
            raise ValueError("providers 不可為空")
        self._providers = list(providers)

    @property
    def providers(self) -> list[InstitutionalFlowProvider]:
        return list(self._providers)

    def fetch_institutional_flow(self, symbol: str, days: int = 5) -> InstitutionalFlowData:
        """
        依優先序嘗試 Provider，回傳第一個成功的結果。
        全部失敗時拋出 InstitutionalFlowError（錯誤碼 INSTITUTIONAL_FETCH_ERROR）。
        """
        market = _detect_market(symbol)
        last_error: Exception | None = None
        attempted: list[str] = []

        for provider in self._providers:
            # 上櫃標的：跳過標有 twse_only 的 Provider
            if market == "tpex" and getattr(provider, "twse_only", False):
                logger.debug("[Router] %s 不支援上櫃標的，跳過", provider.name)
                continue

            attempted.append(provider.name)
            try:
                logger.info("[Router] 嘗試 %s（symbol=%s, days=%d）", provider.name, symbol, days)
                data = provider.fetch_daily_flow(symbol=symbol, days=days)
                logger.info("[Router] 命中 %s（symbol=%s）", provider.name, symbol)
                return data
            except InstitutionalFlowError as exc:
                logger.warning("[Router] %s 失敗：[%s] %s", provider.name, exc.code, exc)
                last_error = exc
            except Exception as exc:
                logger.warning("[Router] %s 意外錯誤：%s", provider.name, exc)
                last_error = exc

        providers_tried = ", ".join(attempted) if attempted else "(none)"
        raise InstitutionalFlowError(
            code="INSTITUTIONAL_FETCH_ERROR",
            message=(
                f"所有 Provider 均失敗（嘗試順序：{providers_tried}）。"
                f" 最後錯誤：{last_error}"
            ),
        )
