"""TwseOpenApiProvider：Fallback #1，上市標的官方來源（TWSE OpenAPI）。"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ai_stock_sentinel.data_sources.institutional_flow.interface import (
    InstitutionalFlowData,
    InstitutionalFlowError,
)

logger = logging.getLogger(__name__)

# TWSE 開放資料 API（三大法人）
_TWSE_INST_API = "https://openapi.twse.com.tw/v1/fund/TWT38U"
# TWSE 融資融券 API
_TWSE_MARGIN_API = "https://openapi.twse.com.tw/v1/fund/MI_MARGN"


class TwseOpenApiProvider:
    """
    Fallback #1：TWSE OpenAPI，官方上市標的三大法人資料。

    限制：
    - 僅支援上市（.TW）標的
    - 免費、無限流，但只提供當日最新一筆
    - 無歷史查詢（days 參數在此 Provider 實際上只拿最新 1 日）
    """

    name = "TwseOpenApi"
    twse_only = True  # Router 用此旗標跳過上櫃標的

    def fetch_daily_flow(self, symbol: str, days: int = 5) -> InstitutionalFlowData:
        """
        拉取 TWSE 最新三大法人資料。

        注意：TWSE OpenAPI TWT38U 回傳「當日所有上市股票」的三大法人資料，
        需在本地過濾指定 stock_id。
        """
        try:
            import requests
        except ImportError as e:
            raise InstitutionalFlowError(
                code="MISSING_DEPENDENCY",
                message="requests 套件未安裝",
                provider=self.name,
            ) from e

        stock_id = _strip_suffix(symbol)
        warnings: list[str] = []

        # ---- 三大法人 ----
        inst_row = self._fetch_institution_row(requests=requests, stock_id=stock_id)
        if inst_row is None:
            raise InstitutionalFlowError(
                code="TWSE_NO_DATA",
                message=f"TWSE OpenAPI 找不到 stock_id={stock_id} 的法人資料",
                provider=self.name,
            )

        # TWSE TWT38U 欄位映射
        foreign_buy = _safe_float(inst_row.get("Foreign_Investors_Buy"))
        foreign_sell = _safe_float(inst_row.get("Foreign_Investors_Sell"))
        investment_trust_buy = _safe_float(inst_row.get("Investment_Trust_Buy"))
        investment_trust_sell = _safe_float(inst_row.get("Investment_Trust_Sell"))
        dealer_buy = _safe_float(inst_row.get("Dealer_Buy"))
        dealer_sell = _safe_float(inst_row.get("Dealer_Sell"))

        # 欄位漂移檢查
        if foreign_buy is None:
            warnings.append("TWSE: Foreign_Investors_Buy 欄位缺失或解析失敗")
        if investment_trust_buy is None:
            warnings.append("TWSE: Investment_Trust_Buy 欄位缺失或解析失敗")
        if dealer_buy is None:
            warnings.append("TWSE: Dealer_Buy 欄位缺失或解析失敗")

        foreign_net = _safe_sub(foreign_buy, foreign_sell)
        trust_net = _safe_sub(investment_trust_buy, investment_trust_sell)
        dealer_net = _safe_sub(dealer_buy, dealer_sell)
        three_party_net = _safe_sum(foreign_net, trust_net, dealer_net)

        # TWSE 版本：days 為 1，融資暫不支援（需另一支 API，骨架已留）
        margin_delta: float | None = None
        margin_balance_delta_pct: float | None = None
        warnings.append("TWSE: margin_delta 需另調 MI_MARGN API（本版骨架暫未串接）")

        flow_label = _determine_flow_label(three_party_net=three_party_net, margin_balance_delta_pct=None)

        return InstitutionalFlowData(
            symbol=symbol,
            period_days=1,  # TWSE OpenAPI 單日
            foreign_buy=foreign_buy,
            investment_trust_buy=investment_trust_buy,
            dealer_buy=dealer_buy,
            foreign_net_cumulative=foreign_net,
            trust_net_cumulative=trust_net,
            dealer_net_cumulative=dealer_net,
            three_party_net=three_party_net,
            consecutive_buy_days=None,  # 單日資料無法計算
            margin_delta=margin_delta,
            margin_balance_delta_pct=margin_balance_delta_pct,
            flow_label=flow_label,
            source_provider=self.name,
            warnings=warnings,
        )

    def _fetch_institution_row(self, *, requests, stock_id: str) -> dict | None:
        """從 TWSE TWT38U API 取回指定股票的當日法人資料列。"""
        try:
            resp = requests.get(_TWSE_INST_API, timeout=15, headers={"Accept": "application/json"})
            resp.raise_for_status()
            rows: list[dict] = resp.json()
        except Exception as exc:
            raise InstitutionalFlowError(
                code="TWSE_REQUEST_ERROR",
                message=f"TWSE OpenAPI 請求失敗：{exc}",
                provider=self.name,
            ) from exc

        # 依 Code 欄位過濾
        for row in rows:
            code = row.get("Code") or row.get("code") or row.get("股票代號") or ""
            if str(code).strip() == stock_id:
                return row

        return None


# ---- 工具函式 ----

def _strip_suffix(symbol: str) -> str:
    return symbol.split(".")[0]


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        # TWSE 欄位常有千分位逗號
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _safe_sub(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a - b


def _safe_sum(*vals: float | None) -> float | None:
    filtered = [v for v in vals if v is not None]
    return sum(filtered) if filtered else None


def _determine_flow_label(
    three_party_net: float | None,
    margin_balance_delta_pct: float | None,
) -> str:
    if three_party_net is None:
        return "neutral"
    if margin_balance_delta_pct is not None and margin_balance_delta_pct > 5 and three_party_net < 0:
        return "retail_chasing"
    if three_party_net < -1000:
        return "distribution"
    if three_party_net > 1000:
        return "institutional_accumulation"
    return "neutral"
