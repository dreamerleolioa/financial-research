from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(slots=True, frozen=True)
class FundamentalData:
    symbol: str

    # EPS / 本益比
    ttm_eps: float | None = None          # 近四季合計 EPS
    pe_current: float | None = None       # 當前 PE（price / ttm_eps）
    pe_mean: float | None = None          # 歷史 PE 均值（各季末真實股價計算）
    pe_std: float | None = None           # 歷史 PE 標準差（各季末真實股價計算）
    pe_band: str = "unknown"              # "cheap" | "fair" | "expensive" | "unknown"
    pe_percentile: float | None = None    # 當前 PE 在歷史真實 PE 分佈的百分位（0-100）

    # 殖利率
    annual_cash_dividend: float | None = None  # 最近年度現金股利合計
    dividend_yield: float | None = None        # 殖利率（%）
    yield_signal: str = "unknown"              # "high_yield" | "mid_yield" | "low_yield" | "unknown"

    # 元資料
    source_provider: str = ""
    warnings: list[str] = field(default_factory=list)


class FundamentalError(Exception):
    def __init__(self, code: str, message: str, provider: str = ""):
        super().__init__(message)
        self.code = code
        self.provider = provider


@runtime_checkable
class FundamentalProvider(Protocol):
    @property
    def name(self) -> str: ...

    def fetch(self, symbol: str, current_price: float) -> FundamentalData:
        """
        Raises:
            FundamentalError: 無法取得資料
        """
        ...
