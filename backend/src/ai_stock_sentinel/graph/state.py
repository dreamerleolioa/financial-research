from __future__ import annotations

from typing import Any, TypedDict

from ai_stock_sentinel.models import AnalysisDetail


class GraphState(TypedDict):
    symbol: str
    news_content: str | None
    snapshot: dict[str, Any] | None
    analysis: str | None
    cleaned_news: dict[str, Any] | None
    raw_news_items: list[dict[str, Any]] | None
    data_sufficient: bool
    retry_count: int
    errors: list[dict[str, str]]
    requires_news_refresh: bool
    requires_fundamental_update: bool
    technical_context: str | None
    institutional_context: str | None
    institutional_flow: dict[str, Any] | None
    strategy_type: str | None
    entry_zone: str | None
    stop_loss: str | None
    holding_period: str | None
    confidence_score: int | None
    cross_validation_note: str | None
    analysis_detail: AnalysisDetail | None
