from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from typing import Any, Callable

import pandas as pd

from ai_stock_sentinel.analysis.confidence_scorer import BASE_CONFIDENCE, compute_confidence, derive_technical_score
from ai_stock_sentinel.analysis.quality_gate import QualityGate
from ai_stock_sentinel.analysis.context_generator import calc_bias, calc_rsi, ma as calc_ma, generate_technical_context, generate_fundamental_context
from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.analysis.strategy_generator import calculate_action_plan_tag, generate_action_plan, generate_strategy
from ai_stock_sentinel.analysis.news_cleaner import FinancialNewsCleaner
from ai_stock_sentinel.data_sources.rss_news_client import RssNewsClient
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.models import AnalysisDetail, StockSnapshot


def fetch_institutional_node(
    state: GraphState,
    *,
    fetcher: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """呼叫 fetcher 取得籌碼資料，寫入 institutional_flow。

    fetcher 為可呼叫物件，接受 symbol str，回傳 dict（成功）或含 'error' 鍵的 dict（失敗）。
    失敗時仍寫入 institutional_flow（帶 error 欄位），流程不中斷。
    """
    symbol = state["symbol"]
    flow = fetcher(symbol)
    return {"institutional_flow": flow}


def fetch_fundamental_node(
    state: GraphState,
    *,
    fetcher: Callable[[str, float], dict[str, Any]],
) -> dict[str, Any]:
    """呼叫 fetcher 取得基本面估值資料，並產出 fundamental_context 敘事字串。"""
    symbol = state["symbol"]
    snapshot = state.get("snapshot") or {}
    current_price = float(snapshot.get("current_price") or 0)
    fund = fetcher(symbol, current_price)
    context = generate_fundamental_context(fund)
    return {"fundamental_data": fund, "fundamental_context": context}


def fetch_external_data_node(
    state: GraphState,
    *,
    institutional_fetcher: Callable[[str], dict[str, Any]],
    fundamental_fetcher: Callable[[str, float], dict[str, Any]],
) -> dict[str, Any]:
    """並行抓取籌碼面與基本面資料，寫入 institutional_flow 與 fundamental_data。

    使用 asyncio.gather + run_in_executor 將兩個同步 fetcher 丟入 thread pool
    並行執行，不需修改底層 provider。
    """
    # Skip guard：若 external data 已存在（前一輪 retry 已抓過），不重複呼叫外部 API
    if state.get("institutional_flow") is not None and state.get("fundamental_data") is not None:
        return {}

    symbol = state["symbol"]
    snapshot = state.get("snapshot") or {}
    current_price = float(snapshot.get("current_price") or 0)

    async def _run() -> tuple[dict[str, Any], dict[str, Any]]:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=2) as pool:
            inst_future = loop.run_in_executor(pool, institutional_fetcher, symbol)
            fund_future = loop.run_in_executor(pool, fundamental_fetcher, symbol, current_price)
            return await asyncio.gather(inst_future, fund_future)

    inst_result, fund_result = asyncio.run(_run())

    context = generate_fundamental_context(fund_result)
    return {
        "institutional_flow": inst_result,
        "fundamental_data": fund_result,
        "fundamental_context": context,
    }


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

    # 規則 2 & 3：新聞相關
    cleaned_news = state["cleaned_news"]
    news_content = state.get("news_content")

    # 規則 2：完全沒有新聞資料（無清潔結果也無原文）
    if cleaned_news is None and not news_content:
        requires_news_refresh = True

    # 規則 3 & 4：已有 cleaned_news 時，判斷新鮮度與數字完整性
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
    """判斷資料是否充分：snapshot 完整，且至少有可用新聞資料（原文或 cleaned_news）。"""
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
        technical_context, institutional_context = generate_technical_context(
            df_price,
            inst_data,
            support_20d=snapshot.get("support_20d"),
            resistance_20d=snapshot.get("resistance_20d"),
            high_20d=snapshot.get("high_20d"),
            low_20d=snapshot.get("low_20d"),
        )
    except Exception as exc:
        return {
            "technical_context": f"技術敘事產出失敗：{exc}",
            "institutional_context": "技術敘事產出失敗，籌碼敘事略過。",
            "errors": state["errors"] + [{"code": "PREPROCESS_ERROR", "message": str(exc)}],
        }

    # rsi14 數值獨立寫入 state，供 strategy_node 燈號判斷使用
    closes_list = [float(v) for v in recent_closes if v is not None]
    rsi14_val: float | None = None
    if len(closes_list) >= 15:
        rsi14_val = calc_rsi(closes_list, period=14)

    updates: dict[str, Any] = {
        "technical_context": technical_context,
        "institutional_context": institutional_context,
        "high_20d": snapshot.get("high_20d"),
        "low_20d": snapshot.get("low_20d"),
        "support_20d": snapshot.get("support_20d"),
        "resistance_20d": snapshot.get("resistance_20d"),
        "rsi14": rsi14_val,
        "entry_price": state.get("entry_price"),
    }

    # ── Position Diagnosis (only when entry_price is provided) ──
    entry_price = state.get("entry_price")
    if entry_price is not None:
        from ai_stock_sentinel.analysis.position_scorer import compute_position_metrics
        support_20d = state.get("support_20d") or (
            snapshot.get("support_20d") if snapshot else None
        )
        current_price = snapshot.get("current_price") if snapshot else None
        if current_price and support_20d:
            pos_metrics = compute_position_metrics(
                entry_price=entry_price,
                current_price=current_price,
                support_20d=support_20d,
            )
            updates.update(pos_metrics)
        else:
            updates.update({
                "profit_loss_pct": None,
                "cost_buffer_to_support": None,
                "position_status": None,
                "position_narrative": None,
            })
    else:
        updates.update({
            "profit_loss_pct": None,
            "cost_buffer_to_support": None,
            "position_status": None,
            "position_narrative": None,
        })

    return updates


def _derive_technical_signal(closes: list[float], rsi: float | None = None) -> str:
    """由 close/ma5/ma20/RSI/BIAS 推導 technical_signal（多條件加權）。"""
    if len(closes) < 20:
        return "sideways"
    close = closes[-1]
    ma20 = calc_ma(closes, 20)
    if rsi is None:
        rsi = calc_rsi(closes, period=14)
    bias = calc_bias(close, ma20) if ma20 is not None else None

    tech_score = derive_technical_score(closes, rsi=rsi, bias=bias)

    if tech_score >= 60:  # score >= 60 → net +2 or +3 in weighted model (majority bullish)
        return "bullish"
    if tech_score <= 40:  # score <= 40 → net -2 or -3 in weighted model (majority bearish)
        return "bearish"
    return "sideways"


def score_node(state: GraphState) -> dict[str, Any]:
    """計算 confidence_score 與 cross_validation_note（純 rule-based，不呼叫 LLM）。

    輸入：
    - state["cleaned_news"]["sentiment_label"]
    - state["institutional_flow"]["flow_label"]
    - 由 recent_closes 推導的 technical_signal

    輸出：
    - confidence_score: int（= signal_confidence，向後相容別名）
    - signal_confidence: int
    - data_confidence: int
    - cross_validation_note: str
    """
    # sentiment_label
    cleaned_news = state.get("cleaned_news")
    news_sentiment: str = "neutral"
    if cleaned_news:
        news_sentiment = cleaned_news.get("sentiment_label") or "neutral"

    # flow_label — 無 API key 時 inst_flow_data 含 'error' 鍵，視為 unknown
    inst_flow_data = state.get("institutional_flow")
    inst_flow: str = "unknown"
    if inst_flow_data and not inst_flow_data.get("error"):
        inst_flow = inst_flow_data.get("flow_label") or "unknown"

    # technical_signal
    snapshot = state.get("snapshot")
    closes: list[float] = []
    if snapshot:
        raw_closes = snapshot.get("recent_closes", [])
        closes = [float(v) for v in raw_closes if v is not None]
    technical_signal = _derive_technical_signal(closes, rsi=state.get("rsi14"))

    # DATE_UNKNOWN 旗標
    quality = state.get("cleaned_news_quality") or {}
    flags = quality.get("quality_flags") or []
    date_unknown = "DATE_UNKNOWN" in flags

    result_dict = compute_confidence(
        BASE_CONFIDENCE,
        news_sentiment=news_sentiment,
        inst_flow=inst_flow,
        technical_signal=technical_signal,
        date_unknown=date_unknown,
    )

    note = result_dict["cross_validation_note"]
    if date_unknown:
        note = note + "（注意：新聞日期不明，時效性未驗證）"

    return {
        "confidence_score": result_dict["signal_confidence"],  # 向後相容
        "signal_confidence": result_dict["signal_confidence"],
        "data_confidence": result_dict["data_confidence"],
        "cross_validation_note": note,
    }


def analyze_node(state: GraphState, *, analyzer: StockAnalyzer) -> dict[str, Any]:
    """執行分析，回傳 analysis_detail (AnalysisDetail) 與向後相容的 analysis (str)。

    傳入 technical_context、institutional_context、confidence_score、cross_validation_note
    供 Skeptic Mode prompt 使用；LLM 不得修改分數。
    """
    snapshot_dict = state["snapshot"]
    if not snapshot_dict:
        return {
            "analysis": None,
            "analysis_detail": None,
            "errors": state["errors"] + [{"code": "MISSING_SNAPSHOT", "message": "No snapshot available for analysis."}],
        }
    snapshot = StockSnapshot(**snapshot_dict)

    # 組合消息面摘要供 LLM 使用
    cleaned = state.get("cleaned_news")
    news_summary: str | None = None
    if cleaned:
        parts: list[str] = []
        if cleaned.get("title"):
            parts.append(f"標題：{cleaned['title']}")
        nums = cleaned.get("mentioned_numbers") or []
        if nums:
            parts.append(f"新聞數值線索：{', '.join(str(n) for n in nums)}")
        sentiment = cleaned.get("sentiment_label")
        if sentiment:
            parts.append(f"情緒判斷：{sentiment}")
        news_summary = "\n".join(parts) if parts else None

    position_context = None
    if state.get("entry_price") is not None:
        position_context = {
            "entry_price": state.get("entry_price"),
            "profit_loss_pct": state.get("profit_loss_pct"),
            "position_status": state.get("position_status"),
            "position_narrative": state.get("position_narrative"),
            "trailing_stop": state.get("trailing_stop"),
            "trailing_stop_reason": state.get("trailing_stop_reason"),
            "recommended_action": state.get("recommended_action"),
        }

    result = analyzer.analyze(
        snapshot,
        news_summary=news_summary,
        technical_context=state.get("technical_context"),
        institutional_context=state.get("institutional_context"),
        confidence_score=state.get("confidence_score"),
        cross_validation_note=state.get("cross_validation_note"),
        fundamental_context=state.get("fundamental_context"),
        position_context=position_context,
        prev_context=state.get("prev_context"),
    )
    return {
        "analysis": result.summary,
        "analysis_detail": result,
    }


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


def quality_gate_node(state: GraphState) -> dict[str, Any]:
    """對 cleaned_news 執行四項品質規則，產出 cleaned_news_quality。

    - check_title → TITLE_LOW_QUALITY
    - normalize_date → DATE_UNKNOWN
    - filter_numbers → NO_FINANCIAL_NUMBERS
    - compute_quality → quality_score (0-100)
    """
    cleaned = state.get("cleaned_news")
    if not cleaned:
        return {"cleaned_news_quality": None}

    flags: list[str] = []

    title_result = QualityGate.check_title(cleaned.get("title", ""))
    flags.extend(title_result.flags)

    date_result = QualityGate.normalize_date(cleaned.get("date", ""))
    flags.extend(date_result.flags)

    numbers_result = QualityGate.filter_numbers(cleaned.get("mentioned_numbers", []))
    flags.extend(numbers_result.flags)

    score_result = QualityGate.compute_quality(flags)

    result: dict[str, Any] = {
        "cleaned_news_quality": {
            "quality_score": score_result.quality_score,
            "quality_flags": score_result.quality_flags,
        }
    }

    # 治標：TITLE_LOW_QUALITY 時從 raw_news_items 回填原始 RSS 標題
    if "TITLE_LOW_QUALITY" in flags:
        raw_items = state.get("raw_news_items") or []
        if raw_items:
            fallback_title = raw_items[0].get("title", "")
            if fallback_title:
                result["cleaned_news"] = {**cleaned, "title": fallback_title}

    # 產出 news_display（向後相容，保留單筆）
    news_display: dict[str, Any] | None = None
    raw_items = state.get("raw_news_items") or []
    if cleaned and raw_items:
        first_raw = raw_items[0]
        normalized_date = date_result.date
        display_date: str | None = normalized_date if normalized_date != "unknown" else None
        news_display = {
            "title": first_raw.get("title", ""),
            "date": display_date,
            "source_url": first_raw.get("url") or None,
        }

    result["news_display"] = news_display

    # 產出 news_display_items（最多 5 筆，供前端顯示近期新聞連結）
    news_display_items: list[dict[str, Any]] = []
    for raw_item in raw_items[:5]:
        item_date_str = raw_item.get("published_at") or raw_item.get("pub_date") or "unknown"
        item_date_result = QualityGate.normalize_date(item_date_str)
        normalized_item_date: str | None = (
            item_date_result.date if item_date_result.date != "unknown" else None
        )
        news_display_items.append({
            "title": raw_item.get("title", ""),
            "date": normalized_item_date,
            "source_url": raw_item.get("url") or None,
        })

    result["news_display_items"] = news_display_items

    return result


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

    # 將最新一篇組成結構化 news_content 供後續清潔
    # 用明確欄位標籤，避免 LLM 把時間戳誤識為標題
    news_content: str | None = None
    if items:
        first = items[0]
        parts: list[str] = []
        if first.published_at:
            parts.append(f"日期: {first.published_at}")
        if first.title:
            parts.append(f"標題: {first.title}")
        if first.summary:
            parts.append(f"摘要: {first.summary}")
        news_content = "\n".join(parts) if parts else None

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
    ma60: float | None = calc_ma(closes, 60)
    bias: float | None = calc_bias(close, ma20) if close is not None and ma20 is not None else None
    rsi: float | None = state.get("rsi14")

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
        "ma60": ma60,
        "support_20d": state.get("support_20d"),
        "low_20d": state.get("low_20d"),
        "sentiment_label": sentiment_label,
    }

    strategy = generate_strategy(technical_context_data, inst_data)

    # 計算 action_plan_tag（燈號）：使用 state 中已計算的 rsi14 和 confidence_score
    rsi14_val: float | None = state.get("rsi14")  # type: ignore[assignment]
    flow_label_for_tag: str | None = (inst_data or {}).get("flow_label") if inst_data else None
    action_plan_tag = calculate_action_plan_tag(
        rsi14=rsi14_val,
        flow_label=flow_label_for_tag,
        confidence_score=state.get("confidence_score"),
    )

    action_plan = generate_action_plan(
        strategy_type=strategy["strategy_type"],
        entry_zone=strategy["entry_zone"],
        stop_loss=strategy["stop_loss"],
        flow_label=flow_label_for_tag,
        confidence_score=state.get("confidence_score"),
        resistance_20d=state.get("resistance_20d"),
        support_20d=state.get("support_20d"),
        data_confidence=state.get("data_confidence"),
        is_final=state["is_final"],
        rsi=rsi,
        sentiment_label=sentiment_label,
        bias=bias,
        close=close,
        ma5=ma5,
        ma20=ma20,
    )

    updates: dict[str, Any] = {
        "strategy_type": strategy["strategy_type"],
        "entry_zone": strategy["entry_zone"],
        "stop_loss": strategy["stop_loss"],
        "holding_period": strategy["holding_period"],
        "action_plan_tag": action_plan_tag,
        "action_plan": action_plan,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
    }

    # ── Position trailing stop (only when entry_price is provided) ──
    entry_price = state.get("entry_price")
    if entry_price is not None:
        from ai_stock_sentinel.analysis.position_scorer import (
            compute_trailing_stop,
            compute_recommended_action,
        )
        snapshot_d = state.get("snapshot") or {}
        inst_flow = state.get("institutional_flow") or {}
        analysis = state.get("analysis_detail") or {}

        profit_loss_pct = state.get("profit_loss_pct", 0.0) or 0.0
        support_20d_val = state.get("support_20d") or snapshot_d.get("support_20d", 0.0)
        high_20d_val = state.get("high_20d") or snapshot_d.get("high_20d", 0.0)
        current_close = snapshot_d.get("current_price", entry_price)

        # MA10: derive from recent_closes if available
        recent_closes_list = snapshot_d.get("recent_closes", [])
        ma10 = sum(recent_closes_list[-10:]) / len(recent_closes_list[-10:]) if len(recent_closes_list) >= 10 else current_close

        trailing_stop, trailing_stop_reason = compute_trailing_stop(
            profit_loss_pct=profit_loss_pct,
            entry_price=entry_price,
            support_20d=support_20d_val,
            ma10=ma10,
            high_20d=high_20d_val,
            current_close=current_close,
        )

        flow_label = inst_flow.get("flow_label", "neutral") if isinstance(inst_flow, dict) else "neutral"
        technical_signal = analysis.get("technical_signal", "neutral") if isinstance(analysis, dict) else "neutral"
        position_status = state.get("position_status", "at_risk") or "at_risk"

        recommended_action, exit_reason = compute_recommended_action(
            flow_label=flow_label,
            profit_loss_pct=profit_loss_pct,
            technical_signal=technical_signal,
            current_close=current_close,
            trailing_stop=trailing_stop,
            position_status=position_status,
        )

        updates["trailing_stop"] = trailing_stop
        updates["trailing_stop_reason"] = trailing_stop_reason
        updates["recommended_action"] = recommended_action
        updates["exit_reason"] = exit_reason
    else:
        updates["trailing_stop"] = None
        updates["trailing_stop_reason"] = None
        updates["recommended_action"] = None
        updates["exit_reason"] = None

    return updates
