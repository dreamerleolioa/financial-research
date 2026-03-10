"""InstitutionalFlowProvider 介面與資料結構定義。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class InstitutionalFlowData:
    """統一輸出 schema，無論哪個 Provider 回傳的欄位都對應此結構。"""

    symbol: str
    period_days: int

    # 三大法人累計買賣超（單位：張）
    foreign_buy: float | None = None       # 外資買超
    investment_trust_buy: float | None = None  # 投信買超
    dealer_buy: float | None = None        # 自營商買超

    # 衍生累計欄位（由 Provider 或 Router 計算）
    foreign_net_cumulative: float | None = None
    trust_net_cumulative: float | None = None
    dealer_net_cumulative: float | None = None
    three_party_net: float | None = None
    consecutive_buy_days: int | None = None

    # 融資融券
    margin_delta: float | None = None       # 融資餘額變化（張）
    margin_balance_delta_pct: float | None = None  # 融資餘額變化%
    short_balance_delta_pct: float | None = None   # 融券餘額變化%

    # 籌碼標籤（rule-based，非 LLM）
    flow_label: str = "neutral"  # institutional_accumulation / retail_chasing / distribution / neutral

    # 元資料
    source_provider: str = ""          # 實際命中的 provider 名稱
    warnings: list[str] = field(default_factory=list)  # 欄位漂移告警


class InstitutionalFlowError(Exception):
    """Provider 無法取得資料時拋出，攜帶錯誤碼供上層記錄。"""

    def __init__(self, code: str, message: str, provider: str = ""):
        super().__init__(message)
        self.code = code
        self.provider = provider


@runtime_checkable
class InstitutionalFlowProvider(Protocol):
    """所有 Provider 必須實作此介面。"""

    @property
    def name(self) -> str:
        """Provider 識別名稱。"""
        ...

    def fetch_daily_flow(self, symbol: str, days: int) -> InstitutionalFlowData:
        """
        拉取法人資料。

        Args:
            symbol: 股票代碼，例如 '2330.TW' 或 '6488.TWO'
            days: 回溯天數

        Returns:
            InstitutionalFlowData，核心欄位已填入或為 None（欄位缺漏）

        Raises:
            InstitutionalFlowError: 無法取得資料（網路、限流、解析失敗）
        """
        ...
