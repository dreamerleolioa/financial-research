from __future__ import annotations

from dataclasses import asdict as _asdict, is_dataclass
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ai_stock_sentinel.graph.builder import build_graph
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.main import build_graph_deps


class AnalyzeRequest(BaseModel):
    symbol: str = Field(default="2330.TW", min_length=1)
    news_text: str | None = None


class AnalyzeResponse(BaseModel):
    snapshot: dict[str, Any] = Field(default_factory=dict)
    analysis: str = ""
    analysis_detail: dict[str, Any] | None = None
    cleaned_news: dict[str, Any] | None = None
    confidence_score: int | None = None
    cross_validation_note: str | None = None
    strategy_type: str | None = None
    entry_zone: str | None = None
    stop_loss: str | None = None
    holding_period: str | None = None
    cleaned_news_quality: dict[str, Any] | None = None
    news_display: dict[str, Any] | None = None
    news_display_items: list[dict[str, Any]] = Field(default_factory=list)
    data_confidence: int | None = None
    signal_confidence: int | None = None

    class ErrorDetail(BaseModel):
        code: str
        message: str

    errors: list[ErrorDetail] = Field(default_factory=list)


def get_graph():
    crawler, analyzer, rss_client, news_cleaner = build_graph_deps()
    return build_graph(crawler=crawler, analyzer=analyzer, rss_client=rss_client, news_cleaner=news_cleaner)


app = FastAPI(title="AI Stock Sentinel API", version="v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    payload: AnalyzeRequest,
    graph=Depends(get_graph),
) -> AnalyzeResponse:
    initial_state: GraphState = {
        "symbol": payload.symbol,
        "news_content": payload.news_text,
        "snapshot": None,
        "analysis": None,
        "analysis_detail": None,
        "cleaned_news": None,
        "raw_news_items": None,
        "data_sufficient": False,
        "retry_count": 0,
        "errors": [],
        "requires_news_refresh": False,
        "requires_fundamental_update": False,
        "technical_context": None,
        "institutional_context": None,
        "institutional_flow": None,
        "strategy_type": None,
        "entry_zone": None,
        "stop_loss": None,
        "holding_period": None,
        "confidence_score": None,
        "cross_validation_note": None,
        "cleaned_news_quality": None,
        "news_display": None,
        "news_display_items": [],
        "data_confidence": None,
        "signal_confidence": None,
    }

    try:
        result: dict[str, Any] = graph.invoke(initial_state)
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
    raw_detail = result.get("analysis_detail")
    analysis_detail: dict[str, Any] | None = None
    if raw_detail is not None:
        if is_dataclass(raw_detail) and not isinstance(raw_detail, type):
            analysis_detail = _asdict(raw_detail)
        elif isinstance(raw_detail, dict):
            analysis_detail = raw_detail
    response_errors: list[AnalyzeResponse.ErrorDetail] = [
        AnalyzeResponse.ErrorDetail(code=e["code"], message=e["message"])
        for e in result.get("errors", [])
    ]

    if not isinstance(snapshot, dict):
        response_errors.append(
            AnalyzeResponse.ErrorDetail(
                code="MISSING_SNAPSHOT",
                message="Graph result missing valid snapshot payload.",
            )
        )
        snapshot = {}

    if not isinstance(analysis, str):
        response_errors.append(
            AnalyzeResponse.ErrorDetail(
                code="MISSING_ANALYSIS",
                message="Graph result missing valid analysis payload.",
            )
        )
        analysis = ""

    return AnalyzeResponse(
        snapshot=snapshot,
        analysis=analysis,
        analysis_detail=analysis_detail,
        cleaned_news=result.get("cleaned_news"),
        cleaned_news_quality=result.get("cleaned_news_quality"),
        news_display=result.get("news_display"),
        news_display_items=result.get("news_display_items") or [],
        confidence_score=result.get("confidence_score"),
        cross_validation_note=result.get("cross_validation_note"),
        strategy_type=result.get("strategy_type"),
        entry_zone=result.get("entry_zone"),
        stop_loss=result.get("stop_loss"),
        holding_period=result.get("holding_period"),
        data_confidence=result.get("data_confidence"),
        signal_confidence=result.get("signal_confidence"),
        errors=response_errors,
    )
