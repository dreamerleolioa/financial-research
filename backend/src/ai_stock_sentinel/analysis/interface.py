from typing import Protocol

from ai_stock_sentinel.models import StockSnapshot


class StockAnalyzer(Protocol):
    def analyze(self, snapshot: StockSnapshot) -> str:
        ...
