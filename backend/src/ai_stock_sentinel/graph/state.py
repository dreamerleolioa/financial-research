from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict):
    symbol: str
    news_content: str | None
    snapshot: dict[str, Any] | None
    analysis: str | None
    cleaned_news: dict[str, Any] | None
    data_sufficient: bool
    retry_count: int
    errors: list[dict[str, str]]
    requires_news_refresh: bool
    requires_fundamental_update: bool
