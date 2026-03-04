from dataclasses import dataclass, field
from typing import List, Literal


@dataclass
class AnalysisDetail:
    summary: str
    risks: list[str] = field(default_factory=list)
    technical_signal: Literal["bullish", "bearish", "sideways"] = "sideways"


@dataclass
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
