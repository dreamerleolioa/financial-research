from dataclasses import dataclass
from typing import List


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
