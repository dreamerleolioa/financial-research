from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

import pandas as pd

from ai_stock_sentinel.analysis.confidence_scorer import BASE_CONFIDENCE, adjust_confidence_by_divergence
from ai_stock_sentinel.analysis.context_generator import calc_bias, calc_rsi, ma as calc_ma, generate_technical_context
from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.analysis.strategy_generator import generate_strategy
from ai_stock_sentinel.analysis.news_cleaner import FinancialNewsCleaner
from ai_stock_sentinel.data_sources.rss_news_client import RssNewsClient
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.models import StockSnapshot


def crawl_node(state: GraphState, *, crawler: YFinanceCrawler) -> dict[str, Any]:
    """抓取股票快照，回傳更新的 state keys。"""
    try:
        snapshot = crawler.fetch_basic_snapshot(symbol=state["symbol"])
        return {"snapshot": asdict(snapshot), "errors": []}
    except Exception as exc:
        return {
            "snapshot": None,
            "errors": state["errors"] + [{"code": "CRAWL_ERROR", "message": str(exc)}],
        }


NEWS_STALENESS_DAYS = 7


def _check_sufficiency(state: GraphState) -> tuple[bool, bool, bool]:
    """
    回傳 (data_sufficient, requires_news_refresh, requires_fundamental_update)。
    """
    requires_news_refresh = False
    requires_fundamental_update = False

    # 規則 1：snapshot 缺失
    if state["snapshot"] is None:
        requires_fundamental_update = True

    # 規則 2 & 3：新聞相關（只在有提供新聞時才判斷）
    cleaned_news = state["cleaned_news"]
    if cleaned_news is not None:
        # 規則 2：新聞過舊
        news_date_str = cleaned_news.get("date")
        if news_date_str:
            try:
                news_date = date.fromisoformat(news_date_str)
                if (date.today() - news_date).days > NEWS_STALENESS_DAYS:
                    requires_news_refresh = True
            except ValueError:
                requires_news_refresh = True

        # 規則 3：數字不足
        mentioned_numbers = cleaned_news.get("mentioned_numbers", [])
        if not mentioned_numbers:
            requires_news_refresh = True

    data_sufficient = not requires_news_refresh and not requires_fundamental_update
    return data_sufficient, requires_news_refresh, requires_fundamental_update


def judge_node(state: GraphState) -> dict[str, Any]:
    """判斷資料是否充分：snapshot 完整、新聞新鮮（≤7天）且含數字。"""
    data_sufficient, requires_news_refresh, requires_fundamental_update = _check_sufficiency(state)
    return {
        "data_sufficient": data_sufficient,
        "requires_news_refresh": requires_news_refresh,
        "requires_fundamental_update": requires_fundamental_update,
    }


def preprocess_node(state: GraphState) -> dict[str, Any]:
    """將快照數值與籌碼資料轉換成敘事字串，供 analyze_node Prompt 使用。

    - 從 state["snapshot"]["recent_closes"] 建立 df_price
    - 從 state.get("institutional_flow") 取得籌碼 dict（可為 None）
    - 產出 technical_context 與 institutional_context 存回 state
    """
    snapshot = state.get("snapshot")
    if not snapshot:
        return {
            "technical_context": "缺少快照資料，無法產出技術分析敘事。",
            "institutional_context": "缺少快照資料，無法產出籌碼敘事。",
        }

    recent_closes = snapshot.get("recent_closes", [])
    df_price = pd.DataFrame({"Close": recent_closes}) if recent_closes else pd.DataFrame()

    inst_data: dict[str, Any] | None = state.get("institutional_flow")  # type: ignore[assignment]

    try:
        technical_context, institutional_context = generate_technical_context(df_price, inst_data)
    except Exception as exc:
        return {
            "technical_context": f"技術敘事產出失敗：{exc}",
            "institutional_context": "技術敘事產出失敗，籌碼敘事略過。",
            "errors": state["errors"] + [{"code": "PREPROCESS_ERROR", "message": str(exc)}],
        }

    return {
        "technical_context": technical_context,
        "institutional_context": institutional_context,
    }


def _derive_technical_signal(closes: list[float]) -> str:
    """由 close/ma5/ma20/RSI 推導 technical_signal。"""
    if len(closes) < 20:
        return "sideways"
    close = closes[-1]
    ma5 = calc_ma(closes, 5)
    ma20 = calc_ma(closes, 20)
    rsi = calc_rsi(closes, period=14)

    if ma5 is not None and ma20 is not None and rsi is not None:
        if close > ma5 > ma20 and 50 <= rsi <= 70:
            return "bullish"
        if (ma5 is not None and ma20 is not None and ma5 < ma20) or (rsi is not None and rsi < 30):
            return "bearish"
    return "sideways"


def score_node(state: GraphState) -> dict[str, Any]:
    """計算 confidence_score 與 cross_validation_note（純 rule-based，不呼叫 LLM）。

    輸入：
    - state["cleaned_news"]["sentiment_label"]
    - state["institutional_flow"]["flow_label"]
    - 由 recent_closes 推導的 technical_signal

    輸出：{"confidence_score": int, "cross_validation_note": str}
    """
    # sentiment_label
    cleaned_news = state.get("cleaned_news")
    news_sentiment: str = "neutral"
    if cleaned_news:
        news_sentiment = cleaned_news.get("sentiment_label") or "neutral"

    # flow_label
    inst_flow_data = state.get("institutional_flow")
    inst_flow: str = "neutral"
    if inst_flow_data:
        inst_flow = inst_flow_data.get("flow_label") or "neutral"

    # technical_signal
    snapshot = state.get("snapshot")
    closes: list[float] = []
    if snapshot:
        raw_closes = snapshot.get("recent_closes", [])
        closes = [float(v) for v in raw_closes if v is not None]
    technical_signal = _derive_technical_signal(closes)

    confidence_score, cross_validation_note = adjust_confidence_by_divergence(
        BASE_CONFIDENCE,
        news_sentiment=news_sentiment,
        inst_flow=inst_flow,
        technical_signal=technical_signal,
    )

    return {
        "confidence_score": confidence_score,
        "cross_validation_note": cross_validation_note,
    }


def analyze_node(state: GraphState, *, analyzer: StockAnalyzer) -> dict[str, Any]:
    """執行分析，回傳 analysis 字串。

    傳入 technical_context、institutional_context、confidence_score、cross_validation_note
    供 Skeptic Mode prompt 使用；LLM 不得修改分數。
    """
    snapshot_dict = state["snapshot"]
    if not snapshot_dict:
        return {
            "analysis": None,
            "errors": state["errors"] + [{"code": "MISSING_SNAPSHOT", "message": "No snapshot available for analysis."}],
        }
    snapshot = StockSnapshot(**snapshot_dict)
    analysis = analyzer.analyze(
        snapshot,
        technical_context=state.get("technical_context"),
        institutional_context=state.get("institutional_context"),
        confidence_score=state.get("confidence_score"),
        cross_validation_note=state.get("cross_validation_note"),
    )
    return {"analysis": analysis}


def clean_node(state: GraphState, *, news_cleaner: FinancialNewsCleaner) -> dict[str, Any]:
    """將 news_content 清潔成結構化 cleaned_news；若無 news_content 則跳過。"""
    news_content = state["news_content"]
    if not news_content:
        return {"cleaned_news": None}
    try:
        cleaned = news_cleaner.clean(news_content)
        return {"cleaned_news": cleaned.model_dump()}
    except Exception as exc:
        return {
            "cleaned_news": None,
            "errors": state["errors"] + [{"code": "CLEAN_ERROR", "message": str(exc)}],
        }


def fetch_news_node(state: GraphState, *, rss_client: RssNewsClient) -> dict[str, Any]:
    """透過 RSS 抓取新聞，回傳 raw_news_items 與 news_content（取第一篇標題+摘要）。"""
    symbol = state["symbol"]
    # 用股票代碼（去掉 .TW 後綴）作查詢詞，例如 "2330 台積電"
    query = symbol.split(".")[0]
    try:
        items = rss_client.fetch_news(query=query)
    except Exception as exc:
        return {
            "raw_news_items": [],
            "errors": state["errors"] + [{"code": "RSS_FETCH_ERROR", "message": str(exc)}],
        }

    raw_dicts = [asdict(item) for item in items]

    # 將最新一篇的 title + summary 合併成 news_content 供後續清潔
    news_content: str | None = None
    if items:
        first = items[0]
        parts = [p for p in [first.published_at, first.title, first.summary] if p]
        news_content = "\n".join(parts)

    return {
        "raw_news_items": raw_dicts,
        "news_content": news_content,
    }


def strategy_node(state: GraphState) -> dict[str, Any]:
    """從 snapshot 數值與籌碼資料產出策略建議，純 rule-based。"""
    snapshot = state.get("snapshot")

    # 從 recent_closes 計算技術指標
    closes: list[float] = []
    if snapshot:
        raw_closes = snapshot.get("recent_closes", [])
        closes = [float(v) for v in raw_closes if v is not None]

    close: float | None = closes[-1] if closes else None
    ma5: float | None = calc_ma(closes, 5)
    ma20: float | None = calc_ma(closes, 20)
    bias: float | None = calc_bias(close, ma20) if close is not None and ma20 is not None else None
    rsi: float | None = calc_rsi(closes, period=14) if closes else None

    # 從 cleaned_news 取 sentiment_label
    cleaned_news = state.get("cleaned_news")
    sentiment_label: str | None = None
    if cleaned_news:
        sentiment_label = cleaned_news.get("sentiment_label")

    # 籌碼資料
    inst_data: dict[str, Any] | None = state.get("institutional_flow")  # type: ignore[assignment]

    technical_context_data: dict[str, Any] = {
        "bias": bias,
        "rsi": rsi,
        "close": close,
        "ma5": ma5,
        "ma20": ma20,
        "sentiment_label": sentiment_label,
    }

    strategy = generate_strategy(technical_context_data, inst_data)

    return {
        "strategy_type": strategy["strategy_type"],
        "entry_zone": strategy["entry_zone"],
        "stop_loss": strategy["stop_loss"],
        "holding_period": strategy["holding_period"],
    }
