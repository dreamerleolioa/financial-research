from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from typing import Any, Callable

from ai_stock_sentinel.analysis.confidence_scorer import BASE_CONFIDENCE, compute_confidence, derive_technical_score
from ai_stock_sentinel.analysis.metrics import adx as calc_adx, atr as calc_atr, bollinger_bands as calc_bollinger, calc_bias, calc_rsi, donchian_channel as calc_donchian, ma as calc_ma, macd as calc_macd, mfi as calc_mfi, obv as calc_obv, stochastic_kd as calc_kd
from ai_stock_sentinel.analysis.strategy_generator import calculate_action_plan_tag, generate_action_plan, generate_strategy
from ai_stock_sentinel.data_sources.yfinance_client import YFinanceCrawler
from ai_stock_sentinel.graph.state import GraphState
from ai_stock_sentinel.technical.profile import build_technical_profile_from_snapshot


logger = logging.getLogger(__name__)


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
    """呼叫 fetcher 取得基本面估值資料。"""
    symbol = state["symbol"]
    snapshot = state.get("snapshot") or {}
    current_price = float(snapshot.get("current_price") or 0)
    fund = fetcher(symbol, current_price)
    return {"fundamental_data": fund}


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

    return {
        "institutional_flow": inst_result,
        "fundamental_data": fund_result,
    }


def crawl_node(state: GraphState, *, crawler: YFinanceCrawler) -> dict[str, Any]:
    """抓取股票快照，回傳更新的 state keys。"""
    if state.get("snapshot") is not None:
        return {}
    try:
        snapshot = crawler.fetch_basic_snapshot(symbol=state["symbol"])
        return {"snapshot": asdict(snapshot), "errors": []}
    except Exception:
        logger.exception("market_snapshot_fetch_failed", extra={"symbol": state["symbol"]})
        return {
            "snapshot": None,
            "errors": state["errors"]
            + [{"code": "CRAWL_ERROR", "message": "無法取得市場快照，請稍後再試。"}],
        }


def _check_sufficiency(state: GraphState) -> tuple[bool, bool, bool]:
    """Return deterministic data sufficiency without any news dependency."""
    snapshot_missing = state.get("snapshot") is None
    return not snapshot_missing, False, snapshot_missing


def judge_node(state: GraphState) -> dict[str, Any]:
    """判斷市場快照是否足以進入 deterministic analysis。"""
    data_sufficient, requires_news_refresh, requires_fundamental_update = _check_sufficiency(state)
    return {
        "data_sufficient": data_sufficient,
        "requires_news_refresh": requires_news_refresh,
        "requires_fundamental_update": requires_fundamental_update,
    }


def preprocess_node(state: GraphState) -> dict[str, Any]:
    """將快照數值轉換成 deterministic analysis inputs。"""
    snapshot = state.get("snapshot")
    if not snapshot:
        return {}

    recent_closes = snapshot.get("recent_closes", [])

    closes_list = [float(v) for v in recent_closes if v is not None]
    rsi14_val: float | None = None
    if len(closes_list) >= 15:
        rsi14_val = calc_rsi(closes_list, period=14)

    updates: dict[str, Any] = {
        "high_20d": snapshot.get("high_20d"),
        "low_20d": snapshot.get("low_20d"),
        "support_20d": snapshot.get("support_20d"),
        "resistance_20d": snapshot.get("resistance_20d"),
        "rsi14": rsi14_val,
        "entry_price": state.get("entry_price"),
    }
    technical_payload = build_technical_profile_from_snapshot(
        snapshot,
        is_final=bool(state.get("is_final", True)),
    )
    if technical_payload and isinstance(technical_payload.get("technical_profile"), dict):
        updates["technical_profile"] = technical_payload["technical_profile"]

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
    technical_profile: dict[str, Any] | None = None,
) -> str:
    """Derive technical_signal from the canonical profile, falling back to raw indicators."""
    if technical_profile is not None:
        tech_score = derive_technical_score(
            closes,
            rsi=rsi,
            bias=None,
            technical_profile=technical_profile,
        )
        if tech_score >= 60:
            return "bullish"
        if tech_score <= 40:
            return "bearish"
        return "sideways"

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
    """計算 confidence_score 與 cross_validation_note。

    輸入：
    - state["institutional_flow"]["flow_label"]
    - 由 recent_closes 推導的 technical_signal

    輸出：
    - confidence_score: int（= signal_confidence，向後相容別名）
    - signal_confidence: int
    - data_confidence: int
    - cross_validation_note: str
    """
    # flow_label — 無 API key 時 inst_flow_data 含 'error' 鍵，視為 unknown
    inst_flow_data = state.get("institutional_flow")
    inst_flow: str = "unknown"
    institutional_available = bool(
        isinstance(inst_flow_data, dict)
        and not inst_flow_data.get("error")
        and inst_flow_data.get("flow_label")
    )
    if institutional_available:
        inst_flow = str(inst_flow_data["flow_label"])

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
    technical_profile = state.get("technical_profile") if isinstance(state.get("technical_profile"), dict) else None
    profile_data_quality = technical_profile.get("data_quality") if technical_profile else None
    profile_lookback = (
        profile_data_quality.get("lookback_days_available")
        if isinstance(profile_data_quality, dict)
        else None
    )
    raw_technical_available = len(closes) >= 20
    profile_technical_available = bool(
        isinstance(profile_lookback, int) and not isinstance(profile_lookback, bool) and profile_lookback >= 20
    )
    technical_available = raw_technical_available or profile_technical_available
    technical_signal = _derive_technical_signal(
        closes,
        rsi=state.get("rsi14"),
        highs=highs,
        lows=lows,
        volumes=volumes,
        technical_profile=technical_profile if profile_technical_available else None,
    )

    result_dict = compute_confidence(
        BASE_CONFIDENCE,
        news_sentiment="neutral",
        inst_flow=inst_flow,
        technical_signal=technical_signal,
        news_available=False,
        institutional_available=institutional_available,
        technical_available=technical_available,
        news_dimension_enabled=False,
    )

    return {
        "confidence_score": result_dict["signal_confidence"],  # 向後相容
        "signal_confidence": result_dict["signal_confidence"],
        "data_confidence": result_dict["data_confidence"],
        "cross_validation_note": result_dict["cross_validation_note"],
        "technical_signal": technical_signal,
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

    sentiment_label: str | None = None

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
        "technical_profile": state.get("technical_profile") if isinstance(state.get("technical_profile"), dict) else None,
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
        technical_signal = state.get("technical_signal") or "neutral"
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
