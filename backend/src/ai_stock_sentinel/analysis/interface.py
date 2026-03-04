from typing import Protocol

from ai_stock_sentinel.models import AnalysisDetail, StockSnapshot


class StockAnalyzer(Protocol):
    def analyze(
        self,
        snapshot: StockSnapshot,
        *,
        technical_context: str | None = None,
        institutional_context: str | None = None,
        confidence_score: int | None = None,
        cross_validation_note: str | None = None,
    ) -> AnalysisDetail:
        ...
