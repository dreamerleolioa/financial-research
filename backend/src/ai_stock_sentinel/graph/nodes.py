from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from typing import Any, Callable

import pandas as pd

from ai_stock_sentinel.analysis.confidence_scorer import BASE_CONFIDENCE, compute_confidence, derive_technical_score
from ai_stock_sentinel.analysis.metrics import adx as calc_adx, atr as calc_atr, bollinger_bands as calc_bollinger, donchian_channel as calc_donchian, macd as calc_macd, mfi as calc_mfi, obv as calc_obv, stochastic_kd as calc_kd
from ai_stock_sentinel.analysis.quality_gate import QualityGate
from ai_stock_sentinel.analysis.context_generator import calc_bias, calc_rsi, ma as calc_ma, generate_technical_context, generate_fundamental_context
from ai_stock_sentinel.analysis.interface import StockAnalyzer
from ai_stock_sentinel.analysis.strategy_generator import calculate_action_plan_tag, generate_action_plan, generate_strategy
from ai_stock_sentinel.analysis.news_cleaner import FinancialNewsCleaner
from ai_stock_sentinel.data_sources.rss_news_client import RssNewsClient
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.models import AnalysisDetail, StockSnapshot
from ai_stock_sentinel.technical.profile import build_technical_profile_from_snapshot


def _round_value(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _pct_distance(value: float | None, reference: float | None) -> float | None:
    if value is None or reference in (None, 0):
        return None
    return round((float(value) - float(reference)) / float(reference) * 100, 2)


def _holding_days(entry_date: str | None) -> int | None:
    if not entry_date:
        return None
    try:
        return max((date.today() - date.fromisoformat(entry_date)).days, 0)
    except ValueError:
        return None


def _bollinger_position(bb: dict[str, Any] | None, close: float | None) -> str | None:
    if not bb or close is None:
        return None
    upper = bb.get("bollinger_upper")
    lower = bb.get("bollinger_lower")
    if upper is None or lower is None:
        return None
    band_range = upper - lower
    if band_range <= 0:
        return "flat"
    if close >= upper * 0.99:
        return "near_upper"
    if close <= lower * 1.01:
        return "near_lower"
    if close >= (lower + band_range * 0.5):
        return "above_mid"
    return "below_mid"


def _build_llm_signal_summary(state: GraphState, snapshot: StockSnapshot) -> str:
    closes = [float(v) for v in snapshot.recent_closes if v is not None]
    highs = [float(v) for v in snapshot.recent_highs if v is not None]
    lows = [float(v) for v in snapshot.recent_lows if v is not None]
    volumes = [float(v) for v in snapshot.recent_volumes if v is not None]
    close = closes[-1] if closes else snapshot.current_price

    aligned_hilo = len(highs) == len(closes) and len(lows) == len(closes)
    aligned_volume = len(volumes) == len(closes)
    high_source = highs if aligned_hilo else closes
    low_source = lows if aligned_hilo else closes
    bb = calc_bollinger(closes) if closes else None
    macd_data = calc_macd(closes) if closes else None
    kd_data = calc_kd(closes, highs, lows) if aligned_hilo else None
    adx_data = calc_adx(closes, highs, lows) if aligned_hilo else None
    atr_data = calc_atr(closes, highs, lows) if aligned_hilo else None
    mfi_data = calc_mfi(closes, highs, lows, volumes) if aligned_hilo and aligned_volume else None
    donchian_data = calc_donchian(closes, highs, lows) if aligned_hilo else None
    obv_data = calc_obv(closes, volumes) if aligned_volume else None

    cleaned_news = state.get("cleaned_news") or {}
    inst = state.get("institutional_flow") or {}
    action_plan = state.get("action_plan") or {}
    technical_payload = build_technical_profile_from_snapshot(
        asdict(snapshot),
        is_final=bool(state.get("is_final", True)),
    )
    technical_profile = technical_payload.get("technical_profile") if technical_payload else None

    packet = {
        "rule_based_labels": {
            "technical_signal": state.get("technical_signal"),
            "institutional_flow": None if inst.get("error") else inst.get("flow_label"),
            "sentiment_label": cleaned_news.get("sentiment_label"),
            "confidence_score": state.get("confidence_score"),
            "data_confidence": state.get("data_confidence"),
            "cross_validation_note": state.get("cross_validation_note"),
        },
        "technical_profile": technical_profile,
        "technical_evidence": {
            "close": _round_value(close),
            "ma5": _round_value(calc_ma(closes, 5)),
            "ma20": _round_value(calc_ma(closes, 20)),
            "ma60": _round_value(calc_ma(closes, 60)),
            "high_20d": _round_value(max(high_source[-20:]) if len(high_source) >= 20 else None),
            "low_20d": _round_value(min(low_source[-20:]) if len(low_source) >= 20 else None),
            "high_60d": _round_value(max(high_source[-60:]) if len(high_source) >= 60 else None),
            "low_60d": _round_value(min(low_source[-60:]) if len(low_source) >= 60 else None),
            "rsi14": _round_value(state.get("rsi14")),
            "bollinger_position": _bollinger_position(bb, close),
            "macd_bias": macd_data.get("macd_bias") if macd_data else None,
            "kd_signal": kd_data.get("kd_signal") if kd_data else None,
            "kd_zone": kd_data.get("kd_zone") if kd_data else None,
            "kd_k": _round_value(kd_data.get("k") if kd_data else None, 1),
            "kd_d": _round_value(kd_data.get("d") if kd_data else None, 1),
            "adx": _round_value(adx_data.get("adx") if adx_data else None, 1),
            "adx_trend_strength": adx_data.get("trend_strength") if adx_data else None,
            "adx_trend_direction": adx_data.get("trend_direction") if adx_data else None,
            "obv_signal": obv_data.get("obv_signal") if obv_data else None,
            "obv_trend_20d": obv_data.get("obv_trend_20d") if obv_data else None,
            "obv_trend_mid_long": obv_data.get("obv_trend_mid_long") if obv_data else None,
            "obv_trend_mid_long_window": obv_data.get("obv_trend_mid_long_window") if obv_data else None,
            "atr": _round_value(atr_data.get("atr") if atr_data else None, 2),
            "atr_pct": _round_value(atr_data.get("atr_pct") if atr_data else None, 2),
            "volatility_level": atr_data.get("volatility_level") if atr_data else None,
            "mfi": _round_value(mfi_data.get("mfi") if mfi_data else None, 1),
            "mfi_signal": mfi_data.get("mfi_signal") if mfi_data else None,
            "donchian_position": donchian_data.get("donchian_position") if donchian_data else None,
            "support_20d": _round_value(state.get("support_20d")),
            "resistance_20d": _round_value(state.get("resistance_20d")),
        },
        "news_evidence": {
            "sentiment_score": _round_value(cleaned_news.get("sentiment_score"), 3),
            "sentiment_strength": _round_value(cleaned_news.get("sentiment_strength"), 2),
            "sentiment_counts": cleaned_news.get("sentiment_counts"),
        },
        "strategy_evidence": {
            "strategy_type": state.get("strategy_type"),
            "action_plan_tag": state.get("action_plan_tag"),
            "conviction_level": action_plan.get("conviction_level"),
        },
    }
    if state.get("entry_price") is not None:
        packet["position_evidence"] = {
            "entry_price": _round_value(state.get("entry_price")),
            "entry_date": state.get("entry_date"),
            "quantity": state.get("quantity"),
            "holding_days": state.get("holding_days"),
            "profit_loss_pct": _round_value(state.get("profit_loss_pct")),
            "position_status": state.get("position_status"),
            "trailing_stop": _round_value(state.get("trailing_stop")),
            "distance_to_trailing_stop_pct": _round_value(state.get("distance_to_trailing_stop_pct")),
            "distance_to_support_pct": _round_value(state.get("distance_to_support_pct")),
            "unrealized_pnl": _round_value(state.get("unrealized_pnl")),
            "recommended_action": state.get("recommended_action"),
            "exit_reason": state.get("exit_reason"),
        }
    return json.dumps(packet, ensure_ascii=False, sort_keys=True)


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
    # error response 也視為已存在：institutional_flow error 通常為 API key 未設定等永久性問題，
    # retry 重抓不會恢復，快取 error 是正確行為。
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
    if state.get("snapshot") is not None:
        return {}
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
    if state.get("skip_ai"):
        return True, False, False
    requires_news_refresh = False
    requires_fundamental_update = False

    # 規則 1：snapshot 缺失 → 設 requires_fundamental_update=True 使 data_sufficient=False，
    # 觸發 retry loop 重新 crawl（_route 透過 data_sufficient 間接處理此情況）
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
    if recent_closes:
        df_data: dict[str, Any] = {"Close": recent_closes}
        recent_highs = snapshot.get("recent_highs") or []
        recent_lows = snapshot.get("recent_lows") or []
        recent_volumes = snapshot.get("recent_volumes") or []
        if len(recent_highs) == len(recent_closes):
            df_data["High"] = recent_highs
        if len(recent_lows) == len(recent_closes):
            df_data["Low"] = recent_lows
        if len(recent_volumes) == len(recent_closes):
            df_data["Volume"] = recent_volumes
        df_price = pd.DataFrame(df_data)
    else:
        df_price = pd.DataFrame()

    inst_data: dict[str, Any] | None = state.get("institutional_flow")  # type: ignore[assignment]
    if inst_data and not inst_data.get("error"):
        recent_volumes = snapshot.get("recent_volumes") or []
        if recent_volumes:
            avg_volume = sum(float(value) for value in recent_volumes[-20:]) / len(recent_volumes[-20:])
            inst_data = {**inst_data, "avg_daily_volume": avg_volume}

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


def _derive_technical_signal(
    closes: list[float],
    rsi: float | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> str:
    """由 close/ma5/ma20/RSI/BIAS/MACD/布林通道推導 technical_signal（多條件加權）。"""
    if len(closes) < 20:
        return "sideways"
    close = closes[-1]
    ma20 = calc_ma(closes, 20)
    if rsi is None:
        rsi = calc_rsi(closes, period=14)
    bias = calc_bias(close, ma20) if ma20 is not None else None
    macd_data = calc_macd(closes)
    bb = calc_bollinger(closes)
    aligned_highs = highs if highs and len(highs) == len(closes) else []
    aligned_lows = lows if lows and len(lows) == len(closes) else []
    aligned_volumes = volumes if volumes and len(volumes) == len(closes) else []
    kd_data = calc_kd(closes, aligned_highs, aligned_lows) if aligned_highs and aligned_lows else None
    adx_data = calc_adx(closes, aligned_highs, aligned_lows) if aligned_highs and aligned_lows else None
    atr_data = calc_atr(closes, aligned_highs, aligned_lows) if aligned_highs and aligned_lows else None
    mfi_data = calc_mfi(closes, aligned_highs, aligned_lows, aligned_volumes) if aligned_highs and aligned_lows and aligned_volumes else None
    donchian_data = calc_donchian(closes, aligned_highs, aligned_lows) if aligned_highs and aligned_lows else None
    obv_data = calc_obv(closes, aligned_volumes) if aligned_volumes else None

    tech_score = derive_technical_score(
        closes,
        rsi=rsi,
        bias=bias,
        macd_data=macd_data,
        bb=bb,
        kd_data=kd_data,
        adx_data=adx_data,
        obv_data=obv_data,
        atr_data=atr_data,
        mfi_data=mfi_data,
        donchian_data=donchian_data,
    )

    if tech_score >= 60:
        return "bullish"
    if tech_score <= 40:
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
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []
    if snapshot:
        raw_closes = snapshot.get("recent_closes", [])
        closes = [float(v) for v in raw_closes if v is not None]
        highs = [float(v) for v in snapshot.get("recent_highs", []) if v is not None]
        lows = [float(v) for v in snapshot.get("recent_lows", []) if v is not None]
        volumes = [float(v) for v in snapshot.get("recent_volumes", []) if v is not None]
    technical_signal = _derive_technical_signal(
        closes,
        rsi=state.get("rsi14"),
        highs=highs,
        lows=lows,
        volumes=volumes,
    )

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
        sentiment_strength=float(cleaned_news.get("sentiment_strength", 1.0)) if cleaned_news else 1.0,
    )

    note = result_dict["cross_validation_note"]
    if date_unknown:
        note = note + "（注意：新聞日期不明，時效性未驗證）"

    return {
        "confidence_score": result_dict["signal_confidence"],  # 向後相容
        "signal_confidence": result_dict["signal_confidence"],
        "data_confidence": result_dict["data_confidence"],
        "cross_validation_note": note,
        "technical_signal": technical_signal,
    }


def _build_news_content(raw_item: dict[str, Any]) -> str:
    parts: list[str] = []
    if raw_item.get("published_at"):
        parts.append(f"日期: {raw_item['published_at']}")
    if raw_item.get("title"):
        parts.append(f"標題: {raw_item['title']}")
    if raw_item.get("summary"):
        parts.append(f"摘要: {raw_item['summary']}")
    return "\n".join(parts)


def _aggregate_cleaned_news(cleaned_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not cleaned_items:
        return None

    weights = [1.0, 0.85, 0.7, 0.55, 0.4]
    sentiment_values = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
    weighted_sum = 0.0
    total_weight = 0.0
    counts = {"positive": 0, "neutral": 0, "negative": 0}

    mentioned_numbers: list[str] = []
    seen_numbers: set[str] = set()
    for idx, item in enumerate(cleaned_items[:5]):
        sentiment = item.get("sentiment_label") or "neutral"
        if sentiment not in counts:
            sentiment = "neutral"
        counts[sentiment] += 1
        weight = weights[idx] if idx < len(weights) else weights[-1]
        weighted_sum += sentiment_values[sentiment] * weight
        total_weight += weight
        for number in item.get("mentioned_numbers") or []:
            number_str = str(number)
            if number_str not in seen_numbers:
                seen_numbers.add(number_str)
                mentioned_numbers.append(number_str)

    sentiment_score = weighted_sum / total_weight if total_weight else 0.0
    if sentiment_score >= 0.25:
        aggregate_label = "positive"
    elif sentiment_score <= -0.25:
        aggregate_label = "negative"
    else:
        aggregate_label = "neutral"

    sentiment_strength = 0.0
    if aggregate_label != "neutral":
        sentiment_strength = 1.0 + min(0.6, max(0.0, (abs(sentiment_score) - 0.25) / 0.75 * 0.6))

    latest = cleaned_items[0]
    return {
        "date": latest.get("date", "unknown"),
        "title": latest.get("title", "unknown"),
        "mentioned_numbers": mentioned_numbers[:20],
        "sentiment_label": aggregate_label,
        "sentiment_strength": round(sentiment_strength, 2),
        "sentiment_score": round(sentiment_score, 3),
        "sentiment_counts": counts,
    }


def analyze_node(state: GraphState, *, analyzer: StockAnalyzer) -> dict[str, Any]:
    """執行分析，回傳 analysis_detail (AnalysisDetail) 與向後相容的 analysis (str)。

    傳入 technical_context、institutional_context、confidence_score、cross_validation_note
    供 Skeptic Mode prompt 使用；LLM 不得修改分數。
    """
    if state.get("skip_ai"):
        return {
            "analysis": "AI 分析已跳過，僅顯示基本面與技術指標。",
            "analysis_detail": None,
        }
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
        counts = cleaned.get("sentiment_counts")
        if counts:
            parts.append(
                "新聞情緒分布："
                f"正面 {counts.get('positive', 0)}、中性 {counts.get('neutral', 0)}、負面 {counts.get('negative', 0)}"
            )
        nums = cleaned.get("mentioned_numbers") or []
        if nums:
            parts.append(f"新聞數值線索：{', '.join(str(n) for n in nums)}")
        sentiment = cleaned.get("sentiment_label")
        if sentiment:
            strength = cleaned.get("sentiment_strength")
            if strength is not None:
                parts.append(f"聚合情緒判斷：{sentiment}（強度 {strength}）")
            else:
                parts.append(f"情緒判斷：{sentiment}")
        cleaned_items = state.get("cleaned_news_items") or []
        if cleaned_items:
            titles = [item.get("title") for item in cleaned_items[:3] if item.get("title")]
            if titles:
                parts.append("代表新聞：" + "；".join(str(title) for title in titles))
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
            "exit_reason": state.get("exit_reason"),
            "distance_to_trailing_stop_pct": state.get("distance_to_trailing_stop_pct"),
            "distance_to_support_pct": state.get("distance_to_support_pct"),
            "unrealized_pnl": state.get("unrealized_pnl"),
            "holding_days": state.get("holding_days"),
        }

    result = analyzer.analyze(
        snapshot,
        signal_summary=_build_llm_signal_summary(state, snapshot),
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
    if not news_content or state.get("skip_ai"):
        return {"cleaned_news": None, "cleaned_news_items": []}
    try:
        raw_items = state.get("raw_news_items") or []
        if raw_items:
            cleaned_items: list[dict[str, Any]] = []
            for raw_item in raw_items[:5]:
                item_content = _build_news_content(raw_item)
                if not item_content:
                    continue
                cleaned_items.append(news_cleaner.clean(item_content).model_dump())
            return {
                "cleaned_news": _aggregate_cleaned_news(cleaned_items),
                "cleaned_news_items": cleaned_items,
            }

        cleaned = news_cleaner.clean(news_content).model_dump()
        return {"cleaned_news": cleaned, "cleaned_news_items": [cleaned]}
    except Exception as exc:
        return {
            "cleaned_news": None,
            "cleaned_news_items": [],
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
    """透過 RSS 抓取新聞，回傳 raw_news_items 與 news_content（最多五篇標題+摘要）。"""
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

    # 將最多五篇組成結構化 news_content，供後續清潔與除錯檢視。
    # 用明確欄位標籤，避免 LLM 把時間戳誤識為標題
    news_content: str | None = None
    if items:
        parts: list[str] = []
        for idx, item in enumerate(items[:5], start=1):
            parts.append(f"新聞{idx}:")
            if item.published_at:
                parts.append(f"日期: {item.published_at}")
            if item.title:
                parts.append(f"標題: {item.title}")
            if item.summary:
                parts.append(f"摘要: {item.summary}")
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
    highs = [float(v) for v in (snapshot or {}).get("recent_highs", []) if v is not None]
    lows = [float(v) for v in (snapshot or {}).get("recent_lows", []) if v is not None]
    volumes = [float(v) for v in (snapshot or {}).get("recent_volumes", []) if v is not None]

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

    macd_data = calc_macd(closes) if len(closes) >= 35 else None
    bb = calc_bollinger(closes)
    kd_data = calc_kd(closes, highs, lows) if highs and lows and len(highs) == len(closes) and len(lows) == len(closes) else None
    adx_data = calc_adx(closes, highs, lows) if highs and lows and len(highs) == len(closes) and len(lows) == len(closes) else None
    atr_data = calc_atr(closes, highs, lows) if highs and lows and len(highs) == len(closes) and len(lows) == len(closes) else None
    mfi_data = calc_mfi(closes, highs, lows, volumes) if highs and lows and volumes and len(highs) == len(closes) and len(lows) == len(closes) and len(volumes) == len(closes) else None
    donchian_data = calc_donchian(closes, highs, lows) if highs and lows and len(highs) == len(closes) and len(lows) == len(closes) else None
    obv_data = calc_obv(closes, volumes) if volumes and len(volumes) == len(closes) else None

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
        "macd_data": macd_data,
        "bb": bb,
        "kd_data": kd_data,
        "adx_data": adx_data,
        "obv_data": obv_data,
        "atr_data": atr_data,
        "mfi_data": mfi_data,
        "donchian_data": donchian_data,
    }

    strategy = generate_strategy(technical_context_data, inst_data)

    # 計算 action_plan_tag（燈號）：使用 state 中已計算的 rsi14 和 confidence_score
    flow_label_for_tag: str | None = (inst_data or {}).get("flow_label") if inst_data else None
    action_plan_tag = calculate_action_plan_tag(
        rsi14=rsi,
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
        closes = [float(value) for value in recent_closes_list if value is not None]
        highs = [float(value) for value in snapshot_d.get("recent_highs", []) if value is not None]
        lows = [float(value) for value in snapshot_d.get("recent_lows", []) if value is not None]
        volumes = [float(value) for value in snapshot_d.get("recent_volumes", []) if value is not None]
        aligned_hilo = len(highs) == len(closes) and len(lows) == len(closes)
        aligned_volume = len(volumes) == len(closes)
        bb = calc_bollinger(closes) if closes else None
        macd_data = calc_macd(closes) if closes else None
        kd_data = calc_kd(closes, highs, lows) if aligned_hilo else None
        adx_data = calc_adx(closes, highs, lows) if aligned_hilo else None
        atr_data = calc_atr(closes, highs, lows) if aligned_hilo else None
        mfi_data = calc_mfi(closes, highs, lows, volumes) if aligned_hilo and aligned_volume else None
        donchian_data = calc_donchian(closes, highs, lows) if aligned_hilo else None
        obv_data = calc_obv(closes, volumes) if aligned_volume else None
        bollinger_position = _bollinger_position(bb, current_close)

        trailing_stop, trailing_stop_reason = compute_trailing_stop(
            profit_loss_pct=profit_loss_pct,
            entry_price=entry_price,
            support_20d=support_20d_val,
            ma10=ma10,
            high_20d=high_20d_val,
            current_close=current_close,
            kd_zone=kd_data.get("kd_zone") if kd_data else None,
            macd_bias=macd_data.get("macd_bias") if macd_data else None,
            adx_trend_strength=adx_data.get("trend_strength") if adx_data else None,
            adx_trend_direction=adx_data.get("trend_direction") if adx_data else None,
            obv_signal=obv_data.get("obv_signal") if obv_data else None,
            atr_value=atr_data.get("atr") if atr_data else None,
            mfi_signal=mfi_data.get("mfi_signal") if mfi_data else None,
        )

        flow_label = inst_flow.get("flow_label", "neutral") if isinstance(inst_flow, dict) else "neutral"
        technical_signal = (
            analysis.get("technical_signal")
            if isinstance(analysis, dict) and analysis.get("technical_signal")
            else (state.get("technical_signal") or "neutral")
        )
        position_status = state.get("position_status", "at_risk") or "at_risk"

        recommended_action, exit_reason = compute_recommended_action(
            flow_label=flow_label,
            profit_loss_pct=profit_loss_pct,
            technical_signal=technical_signal,
            current_close=current_close,
            trailing_stop=trailing_stop,
            position_status=position_status,
            kd_signal=kd_data.get("kd_signal") if kd_data else None,
            kd_zone=kd_data.get("kd_zone") if kd_data else None,
            macd_bias=macd_data.get("macd_bias") if macd_data else None,
            bollinger_position=bollinger_position,
            adx_trend_strength=adx_data.get("trend_strength") if adx_data else None,
            adx_trend_direction=adx_data.get("trend_direction") if adx_data else None,
            obv_signal=obv_data.get("obv_signal") if obv_data else None,
            mfi_signal=mfi_data.get("mfi_signal") if mfi_data else None,
            donchian_position=donchian_data.get("donchian_position") if donchian_data else None,
        )

        quantity = state.get("quantity")
        unrealized_pnl = None
        if quantity is not None:
            unrealized_pnl = round((float(current_close) - float(entry_price)) * float(quantity), 2)

        updates["trailing_stop"] = trailing_stop
        updates["trailing_stop_reason"] = trailing_stop_reason
        updates["recommended_action"] = recommended_action
        updates["exit_reason"] = exit_reason
        updates["distance_to_trailing_stop_pct"] = _pct_distance(current_close, trailing_stop)
        updates["distance_to_support_pct"] = _pct_distance(current_close, support_20d_val)
        updates["unrealized_pnl"] = unrealized_pnl
        updates["holding_days"] = _holding_days(state.get("entry_date"))
    else:
        updates["trailing_stop"] = None
        updates["trailing_stop_reason"] = None
        updates["recommended_action"] = None
        updates["exit_reason"] = None
        updates["distance_to_trailing_stop_pct"] = None
        updates["distance_to_support_pct"] = None
        updates["unrealized_pnl"] = None
        updates["holding_days"] = None

    return updates
