from dataclasses import dataclass, field
from typing import List, Literal


@dataclass(slots=True)
class AnalysisDetail:
    summary: str
    risks: list[str] = field(default_factory=list)
    technical_signal: Literal["bullish", "bearish", "sideways"] = "sideways"
    institutional_flow: str | None = None
    sentiment_label: str | None = None
    tech_insight: str | None = None
    inst_insight: str | None = None
    news_insight: str | None = None
    final_verdict: str | None = None
    fundamental_insight: str | None = None


@dataclass(slots=True)
class StockSnapshot:
    symbol: str
    currency: str
    current_price: float
    previous_close: float
    day_open: float
    day_high: float
    day_low: float
    volume: int
    recent_closes: List[float]
    fetched_at: str
    volume_source: str = "realtime"
    high_20d: float | None = None       # 近 20 日最高收盤價
    low_20d: float | None = None        # 近 20 日最低收盤價
    support_20d: float | None = None    # 近 20 日支撐位（low_20d × 0.99）
    resistance_20d: float | None = None  # 近 20 日壓力位（high_20d × 1.01）

    def __post_init__(self) -> None:
        closes = self.recent_closes
        if len(closes) >= 2:
            window = closes[-20:]
            self.high_20d = max(window)
            self.low_20d = min(window)
            self.support_20d = self.low_20d * 0.99
            self.resistance_20d = self.high_20d * 1.01
