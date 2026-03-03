from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from ai_stock_sentinel.agents.crawler_agent import StockCrawlerAgent
from ai_stock_sentinel.main import build_agent


class AnalyzeRequest(BaseModel):
    symbol: str = Field(default="2330.TW", min_length=1)
    news_text: str | None = None


class AnalyzeResponse(BaseModel):
    snapshot: dict[str, Any] = Field(default_factory=dict)
    analysis: str = ""
    cleaned_news: dict[str, Any] | None = None

    class ErrorDetail(BaseModel):
        code: str
        message: str

    errors: list[ErrorDetail] = Field(default_factory=list)


def get_agent() -> StockCrawlerAgent:
    return build_agent()


app = FastAPI(title="AI Stock Sentinel API", version="v1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    payload: AnalyzeRequest,
    agent: StockCrawlerAgent = Depends(get_agent),
) -> AnalyzeResponse:
    try:
        result = agent.run(symbol=payload.symbol, news_content=payload.news_text)
    except Exception as exc:
        return AnalyzeResponse(
            errors=[
                AnalyzeResponse.ErrorDetail(
                    code="ANALYZE_RUNTIME_ERROR",
                    message=str(exc),
                )
            ]
        )

    snapshot = result.get("snapshot")
    analysis = result.get("analysis")
    response_errors: list[AnalyzeResponse.ErrorDetail] = []

    if not isinstance(snapshot, dict):
        response_errors.append(
            AnalyzeResponse.ErrorDetail(
                code="MISSING_SNAPSHOT",
                message="Agent result missing valid snapshot payload.",
            )
        )
        snapshot = {}

    if not isinstance(analysis, str):
        response_errors.append(
            AnalyzeResponse.ErrorDetail(
                code="MISSING_ANALYSIS",
                message="Agent result missing valid analysis payload.",
            )
        )
        analysis = ""

    return AnalyzeResponse(
        snapshot=snapshot,
        analysis=analysis,
        cleaned_news=result.get("cleaned_news"),
        errors=response_errors,
    )
