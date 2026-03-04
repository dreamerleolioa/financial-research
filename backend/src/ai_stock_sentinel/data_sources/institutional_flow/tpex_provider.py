"""TpexProvider：Fallback #2，上櫃標的資料源（TPEX / OTC OpenAPI）。"""
from __future__ import annotations

import logging

from ai_stock_sentinel.data_sources.institutional_flow.interface import (
    InstitutionalFlowData,
    InstitutionalFlowError,
)

logger = logging.getLogger(__name__)

# TPEX 三大法人 OpenAPI
_TPEX_INST_API = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_three_institutions_trading"


class TpexProvider:
    """
    Fallback #2：TPEX（上櫃）三大法人資料。

    限制：
    - 官方 OpenAPI，回傳當日所有上櫃股票
    - 僅提供當日最新一筆（無多日歷史）
    - 融資資料需另一支 API（本版骨架預留）
    """

    name = "Tpex"

    def fetch_daily_flow(self, symbol: str, days: int = 5) -> InstitutionalFlowData:
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

        row = self._fetch_institution_row(requests=requests, stock_id=stock_id)
        if row is None:
            raise InstitutionalFlowError(
                code="TPEX_NO_DATA",
                message=f"TPEX OpenAPI 找不到 stock_id={stock_id} 的法人資料",
                provider=self.name,
            )

        # TPEX 欄位映射（欄位名稱可能與 TWSE 不同，需依實際 API 回應調整）
        # 常見欄位：ForeignInvestorsBuy / ForeignInvestorsSell / InvestmentTrustBuy...
        foreign_buy = _safe_float(
            row.get("ForeignInvestorsBuy") or row.get("Foreign_Investors_Buy") or row.get("外資買進")
        )
        foreign_sell = _safe_float(
            row.get("ForeignInvestorsSell") or row.get("Foreign_Investors_Sell") or row.get("外資賣出")
        )
        investment_trust_buy = _safe_float(
            row.get("InvestmentTrustBuy") or row.get("Investment_Trust_Buy") or row.get("投信買進")
        )
        investment_trust_sell = _safe_float(
            row.get("InvestmentTrustSell") or row.get("Investment_Trust_Sell") or row.get("投信賣出")
        )
        dealer_buy = _safe_float(
            row.get("DealerBuy") or row.get("Dealer_Buy") or row.get("自營商買進")
        )
        dealer_sell = _safe_float(
            row.get("DealerSell") or row.get("Dealer_Sell") or row.get("自營商賣出")
        )

        # 欄位漂移告警
        if foreign_buy is None:
            warnings.append("TPEX: 外資買進欄位缺失或解析失敗（欄位名稱可能漂移）")
        if investment_trust_buy is None:
            warnings.append("TPEX: 投信買進欄位缺失或解析失敗")
        if dealer_buy is None:
            warnings.append("TPEX: 自營商買進欄位缺失或解析失敗")

        warnings.append("TPEX: margin_delta 未串接（TPEX 融資 API 骨架預留）")

        foreign_net = _safe_sub(foreign_buy, foreign_sell)
        trust_net = _safe_sub(investment_trust_buy, investment_trust_sell)
        dealer_net = _safe_sub(dealer_buy, dealer_sell)
        three_party_net = _safe_sum(foreign_net, trust_net, dealer_net)

        flow_label = _determine_flow_label(three_party_net=three_party_net, margin_balance_delta_pct=None)

        return InstitutionalFlowData(
            symbol=symbol,
            period_days=1,
            foreign_buy=foreign_buy,
            investment_trust_buy=investment_trust_buy,
            dealer_buy=dealer_buy,
            foreign_net_cumulative=foreign_net,
            trust_net_cumulative=trust_net,
            dealer_net_cumulative=dealer_net,
            three_party_net=three_party_net,
            consecutive_buy_days=None,
            margin_delta=None,
            margin_balance_delta_pct=None,
            flow_label=flow_label,
            source_provider=self.name,
            warnings=warnings,
        )

    def _fetch_institution_row(self, *, requests, stock_id: str) -> dict | None:
        try:
            resp = requests.get(_TPEX_INST_API, timeout=15, headers={"Accept": "application/json"})
            resp.raise_for_status()
            rows: list[dict] = resp.json()
        except Exception as exc:
            raise InstitutionalFlowError(
                code="TPEX_REQUEST_ERROR",
                message=f"TPEX OpenAPI 請求失敗：{exc}",
                provider=self.name,
            ) from exc

        # 依股票代號過濾（TPEX 常見欄位：Code / 代號 / SecuritiesCompanyCode）
        for row in rows:
            code = (
                row.get("Code")
                or row.get("code")
                or row.get("代號")
                or row.get("SecuritiesCompanyCode")
                or ""
            )
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
