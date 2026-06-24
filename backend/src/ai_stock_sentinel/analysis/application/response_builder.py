from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict as _asdict, is_dataclass
from typing import Any

from ai_stock_sentinel.analysis.langchain_analyzer import PROMPT_HASH
from ai_stock_sentinel.analysis.metrics import (
    adx as _adx,
    atr as _atr,
    bollinger_bands as _bollinger_bands,
    donchian_channel as _donchian_channel,
    ma as _ma,
    macd as _macd,
    mfi as _mfi,
    obv as _obv,
    stochastic_kd as _stochastic_kd,
)
from ai_stock_sentinel.analysis.position_scorer import build_position_risk_language
from ai_stock_sentinel.config import STRATEGY_VERSION
from ai_stock_sentinel.analysis.schemas import (
    AnalyzeResponse,
    CachedAnalyzeResponse,
    PositionAnalysis,
    PositionAnalyzeRequest,
    TechnicalIndicators,
)
from ai_stock_sentinel.technical.profile import build_technical_profile_from_snapshot


def compute_technical_indicators(snapshot: dict) -> TechnicalIndicators | None:
    payload = build_technical_profile_from_snapshot(snapshot)
    if not payload:
        return None
    return TechnicalIndicators.model_validate(payload["technical_indicators"])


def compute_technical_profile(snapshot: dict, *, is_final: bool = True) -> dict[str, Any] | None:
    payload = build_technical_profile_from_snapshot(snapshot, is_final=is_final)
    if not payload:
        return None
    profile = payload.get("technical_profile")
    return profile if isinstance(profile, dict) else None


def extract_indicators(result: dict) -> dict:
    snapshot = result.get("snapshot") or {}
    inst = result.get("institutional_flow") or {}
    action_plan = result.get("action_plan") or {}
    cleaned_news = result.get("cleaned_news") or {}

    recent_closes = snapshot.get("recent_closes") or []
    closes = [float(v) for v in recent_closes if v is not None]
    highs = [float(v) for v in (snapshot.get("recent_highs") or []) if v is not None]
    lows = [float(v) for v in (snapshot.get("recent_lows") or []) if v is not None]
    volumes = [float(v) for v in (snapshot.get("recent_volumes") or []) if v is not None]
    bb = _bollinger_bands(closes) if closes else None
    macd_data = _macd(closes) if closes else None
    aligned_hilo = len(highs) == len(closes) and len(lows) == len(closes)
    aligned_volume = len(volumes) == len(closes)
    kd_data = _stochastic_kd(closes, highs, lows) if aligned_hilo else None
    adx_data = _adx(closes, highs, lows) if aligned_hilo else None
    atr_data = _atr(closes, highs, lows) if aligned_hilo else None
    mfi_data = _mfi(closes, highs, lows, volumes) if aligned_hilo and aligned_volume else None
    donchian_data = _donchian_channel(closes, highs, lows) if aligned_hilo else None
    obv_data = _obv(closes, volumes) if aligned_volume else None
    bollinger_position = compute_bollinger_position(bb, snapshot.get("current_price")) if bb else None
    high_source = highs if aligned_hilo else closes
    low_source = lows if aligned_hilo else closes

    indicators = {
        "ma5": _ma(closes, 5),
        "ma20": _ma(closes, 20),
        "ma60": _ma(closes, 60),
        "high_20d": max(high_source[-20:]) if len(high_source) >= 20 else None,
        "low_20d": min(low_source[-20:]) if len(low_source) >= 20 else None,
        "high_60d": max(high_source[-60:]) if len(high_source) >= 60 else None,
        "low_60d": min(low_source[-60:]) if len(low_source) >= 60 else None,
        "rsi_14": result.get("rsi14"),
        "close_price": snapshot.get("current_price"),
        "volume_ratio": snapshot.get("volume_ratio"),
        "strategy_type": result.get("strategy_type"),
        "conviction_level": action_plan.get("conviction_level"),
        "sentiment_label": cleaned_news.get("sentiment_label"),
        "flow_label": inst.get("flow_label") if not inst.get("error") else None,
        "technical_signal": result.get("technical_signal"),
        "bollinger_mid": bb["bollinger_mid"] if bb else None,
        "bollinger_upper": bb["bollinger_upper"] if bb else None,
        "bollinger_lower": bb["bollinger_lower"] if bb else None,
        "bollinger_position": bollinger_position,
        "macd_line": macd_data["macd_line"] if macd_data else None,
        "macd_signal": macd_data["macd_signal"] if macd_data else None,
        "macd_hist": macd_data["macd_hist"] if macd_data else None,
        "macd_bias": macd_data["macd_bias"] if macd_data else None,
        "kd_k": kd_data["k"] if kd_data else None,
        "kd_d": kd_data["d"] if kd_data else None,
        "kd_signal": kd_data["kd_signal"] if kd_data else None,
        "kd_zone": kd_data["kd_zone"] if kd_data else None,
        "adx": adx_data["adx"] if adx_data else None,
        "adx_trend_strength": adx_data["trend_strength"] if adx_data else None,
        "adx_trend_direction": adx_data["trend_direction"] if adx_data else None,
        "obv": obv_data["obv"] if obv_data else None,
        "obv_signal": obv_data["obv_signal"] if obv_data else None,
        "obv_trend_20d": obv_data["obv_trend_20d"] if obv_data else None,
        "obv_trend_mid_long": obv_data["obv_trend_mid_long"] if obv_data else None,
        "obv_trend_mid_long_window": obv_data["obv_trend_mid_long_window"] if obv_data else None,
        "atr": atr_data["atr"] if atr_data else None,
        "atr_pct": atr_data["atr_pct"] if atr_data else None,
        "volatility_level": atr_data["volatility_level"] if atr_data else None,
        "mfi": mfi_data["mfi"] if mfi_data else None,
        "mfi_signal": mfi_data["mfi_signal"] if mfi_data else None,
        "donchian_upper": donchian_data["donchian_upper"] if donchian_data else None,
        "donchian_lower": donchian_data["donchian_lower"] if donchian_data else None,
        "donchian_mid": donchian_data["donchian_mid"] if donchian_data else None,
        "donchian_width_pct": donchian_data["donchian_width_pct"] if donchian_data else None,
        "donchian_position": donchian_data["donchian_position"] if donchian_data else None,
        "institutional": {
            "foreign_net": inst.get("foreign_net"),
            "trust_net": inst.get("trust_net"),
            "dealer_net": inst.get("dealer_net"),
            "three_party_net": inst.get("three_party_net"),
            "consecutive_buy_days": inst.get("consecutive_buy_days"),
            "consecutive_sell_days": inst.get("consecutive_sell_days"),
            "dominant_buyer": inst.get("dominant_buyer"),
            "dominant_seller": inst.get("dominant_seller"),
            "flow_strength": inst.get("flow_strength"),
            "margin_delta": inst.get("margin_delta"),
            "margin_balance_delta_pct": inst.get("margin_balance_delta_pct"),
            "short_delta": inst.get("short_delta"),
            "short_balance_delta_pct": inst.get("short_balance_delta_pct"),
            "securities_lending_delta": inst.get("securities_lending_delta"),
            "securities_lending_volume": inst.get("securities_lending_volume"),
            "foreign_holding_ratio": inst.get("foreign_holding_ratio"),
            "foreign_holding_ratio_delta_pct": inst.get("foreign_holding_ratio_delta_pct"),
            "major_holder_ratio": inst.get("major_holder_ratio"),
            "major_holder_ratio_delta_pct": inst.get("major_holder_ratio_delta_pct"),
            "retail_holder_ratio_delta_pct": inst.get("retail_holder_ratio_delta_pct"),
        } if not inst.get("error") else None,
    }
    if result.get("entry_price") is not None:
        indicators["position_risk_language"] = position_risk_language_snapshot_from_result(result)
    return indicators


def build_response_from_cache(
    hit: CachedAnalyzeResponse,
    symbol: str,
    *,
    full_result: dict | None = None,
    symbol_name_resolver: Callable[[str], str | None] | None = None,
) -> AnalyzeResponse:
    if full_result:
        try:
            resp = AnalyzeResponse.model_validate(full_result)
            resp.is_final = hit.is_final
            resp.intraday_disclaimer = hit.intraday_disclaimer
            resp.strategy_version = hit.strategy_version
            snapshot = dict(resp.snapshot or {})
            _hydrate_cached_technical_payload(resp, snapshot=snapshot, is_final=hit.is_final)
            resp.symbol_name = display_symbol_name(
                symbol,
                resp.symbol_name or snapshot.get("name"),
                symbol_name_resolver=symbol_name_resolver,
            )
            if resp.symbol_name:
                resp.snapshot = {**snapshot, "name": resp.symbol_name}
            return resp
        except Exception:
            pass
    symbol_name = display_symbol_name(symbol, symbol_name_resolver=symbol_name_resolver)
    return AnalyzeResponse(
        snapshot={"symbol": symbol, "name": symbol_name} if symbol_name else {"symbol": symbol},
        symbol_name=symbol_name,
        analysis=hit.final_verdict or "",
        signal_confidence=int(hit.signal_confidence) if hit.signal_confidence is not None else None,
        action_plan_tag=hit.action_tag,
        is_final=hit.is_final,
        intraday_disclaimer=hit.intraday_disclaimer,
        strategy_version=hit.strategy_version,
    )


def _hydrate_cached_technical_payload(
    response: AnalyzeResponse,
    *,
    snapshot: dict[str, Any],
    is_final: bool,
) -> None:
    payload = build_technical_profile_from_snapshot(snapshot, is_final=is_final)
    if payload:
        if response.technical_indicators is None:
            response.technical_indicators = TechnicalIndicators.model_validate(payload["technical_indicators"])
        if response.technical_profile is None and isinstance(payload.get("technical_profile"), dict):
            response.technical_profile = payload["technical_profile"]
    _set_profile_finality(response.technical_profile, is_final=is_final)


def _set_profile_finality(profile: dict[str, Any] | None, *, is_final: bool) -> None:
    if not isinstance(profile, dict):
        return
    data_quality = profile.get("data_quality")
    if isinstance(data_quality, dict):
        data_quality["is_final"] = is_final


def display_symbol_name(
    symbol: str,
    name: Any | None = None,
    *,
    symbol_name_resolver: Callable[[str], str | None] | None = None,
) -> str | None:
    normalized_symbol = str(symbol or "").strip()
    normalized_name = str(name or "").strip()
    if normalized_name and normalized_name.upper() != normalized_symbol.upper():
        return normalized_name
    resolved_name = symbol_name_resolver(normalized_symbol) if symbol_name_resolver else None
    return resolved_name or normalized_name or None


def build_response(
    result: dict[str, Any],
    *,
    symbol_name_resolver: Callable[[str], str | None] | None = None,
) -> AnalyzeResponse:
    snapshot = result.get("snapshot")
    analysis = result.get("analysis")
    raw_detail = result.get("analysis_detail")
    analysis_detail: dict[str, Any] | None = None
    if raw_detail is not None:
        if is_dataclass(raw_detail) and not isinstance(raw_detail, type):
            analysis_detail = _asdict(raw_detail)
        elif isinstance(raw_detail, dict):
            analysis_detail = raw_detail
    if analysis_detail is not None:
        analysis_detail = {**analysis_detail, "prompt_hash": PROMPT_HASH}
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
    snapshot_symbol = str(snapshot.get("symbol") or "").strip()
    symbol_name = display_symbol_name(
        snapshot_symbol,
        snapshot.get("name"),
        symbol_name_resolver=symbol_name_resolver,
    ) if snapshot_symbol else str(snapshot.get("name") or "").strip() or None
    if symbol_name:
        snapshot = {**snapshot, "name": symbol_name}

    if not isinstance(analysis, str):
        response_errors.append(
            AnalyzeResponse.ErrorDetail(
                code="MISSING_ANALYSIS",
                message="Graph result missing valid analysis payload.",
            )
        )
        analysis = ""

    inst_flow = result.get("institutional_flow")
    institutional_flow_label: str | None = None
    if inst_flow and not inst_flow.get("error"):
        institutional_flow_label = inst_flow.get("flow_label")

    sentiment_label: str | None = (
        result.get("cleaned_news", {}).get("sentiment_label")
        if result.get("cleaned_news") else None
    )

    action_plan: dict[str, Any] | None = result.get("action_plan")

    data_sources: list[str] = []
    if result.get("raw_news_items"):
        data_sources.append("google-news-rss")
    if result.get("snapshot"):
        data_sources.append("yfinance")
    institutional_flow = result.get("institutional_flow")
    if institutional_flow and not institutional_flow.get("error"):
        data_sources.append(institutional_flow.get("provider", "institutional-api"))
    fundamental_data = result.get("fundamental_data")
    if fundamental_data and not fundamental_data.get("error"):
        data_sources.append("finmind-fundamental")

    position_analysis: PositionAnalysis | None = None
    if result.get("entry_price") is not None:
        position_risk_language = build_position_risk_language(
            recommended_action=result.get("recommended_action"),
            trailing_stop=result.get("trailing_stop"),
            trailing_stop_reason=result.get("trailing_stop_reason"),
            exit_reason=result.get("exit_reason"),
            position_status=result.get("position_status"),
            position_narrative=result.get("position_narrative"),
            profit_loss_pct=result.get("profit_loss_pct"),
            distance_to_trailing_stop_pct=result.get("distance_to_trailing_stop_pct"),
            distance_to_support_pct=result.get("distance_to_support_pct"),
        )
        position_analysis = PositionAnalysis(
            entry_price=result["entry_price"],
            profit_loss_pct=result.get("profit_loss_pct"),
            position_status=result.get("position_status"),
            position_narrative=result.get("position_narrative"),
            risk_state=position_risk_language["risk_state"],
            risk_state_label=position_risk_language["risk_state_label"],
            discipline_triggers=position_risk_language["discipline_triggers"],
            observation_conditions=position_risk_language["observation_conditions"],
            risk_control_reference=position_risk_language["risk_control_reference"],
            command_language_deprecated=position_risk_language["command_language_deprecated"],
            recommended_action=result.get("recommended_action"),
            trailing_stop=result.get("trailing_stop"),
            trailing_stop_reason=result.get("trailing_stop_reason"),
            exit_reason=result.get("exit_reason"),
            distance_to_trailing_stop_pct=result.get("distance_to_trailing_stop_pct"),
            distance_to_support_pct=result.get("distance_to_support_pct"),
            unrealized_pnl=result.get("unrealized_pnl"),
            holding_days=result.get("holding_days"),
        )

    technical_payload = build_technical_profile_from_snapshot(
        snapshot if isinstance(snapshot, dict) else {},
        is_final=bool(result.get("is_final", True)),
    )
    technical_indicators = (
        TechnicalIndicators.model_validate(technical_payload["technical_indicators"])
        if technical_payload
        else None
    )
    technical_profile = (
        technical_payload.get("technical_profile")
        if technical_payload and isinstance(technical_payload.get("technical_profile"), dict)
        else None
    )
    analyze_risk_language = build_analyze_risk_language(result)

    return AnalyzeResponse(
        snapshot=snapshot,
        symbol_name=symbol_name,
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
        action_plan_tag=result.get("action_plan_tag"),
        institutional_flow_label=institutional_flow_label,
        sentiment_label=sentiment_label,
        action_plan=action_plan,
        risk_state=analyze_risk_language["risk_state"],
        risk_state_label=analyze_risk_language["risk_state_label"],
        discipline_triggers=analyze_risk_language["discipline_triggers"],
        observation_conditions=analyze_risk_language["observation_conditions"],
        risk_control_reference=analyze_risk_language["risk_control_reference"],
        command_language_deprecated=analyze_risk_language["command_language_deprecated"],
        data_sources=data_sources,
        fundamental_data=result.get("fundamental_data"),
        position_analysis=position_analysis,
        technical_indicators=technical_indicators,
        technical_profile=technical_profile,
        errors=response_errors,
        strategy_version=STRATEGY_VERSION,
    )


def position_cache_matches(full_result: dict[str, Any], payload: PositionAnalyzeRequest) -> bool:
    def same_price(value: Any) -> bool:
        try:
            return abs(float(value) - float(payload.entry_price)) < 0.0001
        except (TypeError, ValueError):
            return False

    position_analysis = full_result.get("position_analysis")
    if not isinstance(position_analysis, dict):
        return False

    cached_request = full_result.get("_position_request")
    if isinstance(cached_request, dict):
        return (
            same_price(cached_request.get("entry_price"))
            and cached_request.get("entry_date") == payload.entry_date
            and cached_request.get("quantity") == payload.quantity
        )

    if payload.entry_date is not None or payload.quantity is not None:
        return False

    return same_price(position_analysis.get("entry_price"))


def compute_bollinger_position(bb: dict, close_price: float | None) -> str | None:
    upper = bb["bollinger_upper"]
    lower = bb["bollinger_lower"]
    if not (upper and lower and close_price):
        return None
    band_range = upper - lower
    if band_range <= 0:
        return "flat"
    if close_price >= upper * 0.99:
        return "near_upper"
    if close_price <= lower * 1.01:
        return "near_lower"
    if close_price >= (lower + band_range * 0.5):
        return "above_mid"
    return "below_mid"


def position_risk_language_snapshot_from_result(result: dict[str, Any]) -> dict[str, Any]:
    risk_language = build_position_risk_language(
        recommended_action=result.get("recommended_action"),
        trailing_stop=result.get("trailing_stop"),
        trailing_stop_reason=result.get("trailing_stop_reason"),
        exit_reason=result.get("exit_reason"),
        position_status=result.get("position_status"),
        position_narrative=result.get("position_narrative"),
        profit_loss_pct=result.get("profit_loss_pct"),
        distance_to_trailing_stop_pct=result.get("distance_to_trailing_stop_pct"),
        distance_to_support_pct=result.get("distance_to_support_pct"),
    )
    return position_risk_language_snapshot(risk_language)


def position_risk_language_snapshot(position_analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "risk_state": position_analysis.get("risk_state"),
        "risk_state_label": position_analysis.get("risk_state_label"),
        "discipline_triggers": list(position_analysis.get("discipline_triggers") or []),
        "observation_conditions": list(position_analysis.get("observation_conditions") or []),
        "risk_control_reference": position_analysis.get("risk_control_reference"),
    }


def indicators_with_position_risk_from_full_result(
    indicators: dict[str, Any] | None,
    full_result: dict[str, Any] | None,
) -> dict[str, Any]:
    next_indicators = dict(indicators or {})
    if next_indicators.get("position_risk_language"):
        return next_indicators
    position_analysis = (full_result or {}).get("position_analysis")
    if isinstance(position_analysis, dict):
        next_indicators["position_risk_language"] = position_risk_language_snapshot(position_analysis)
    return next_indicators


def build_analyze_risk_language(result: dict[str, Any]) -> dict[str, Any]:
    action_plan = result.get("action_plan") if isinstance(result.get("action_plan"), dict) else {}
    action_plan_tag = str(result.get("action_plan_tag") or "")
    risk_state_map = {
        "opportunity": ("setup_observation", "可觀察 setup"),
        "overheated": ("risk_elevated", "追蹤風險升高"),
        "neutral": ("neutral", "等待條件明確"),
    }
    risk_state, risk_state_label = risk_state_map.get(action_plan_tag, ("unknown", "狀態未明"))
    observation_conditions = as_string_list(action_plan.get("thesis_points"))
    observation_conditions.extend(as_string_list(action_plan.get("upgrade_triggers")))
    discipline_triggers = as_string_list(action_plan.get("invalidation_conditions"))
    discipline_triggers.extend(as_string_list(action_plan.get("downgrade_triggers")))
    risk_control_reference = None
    if action_plan.get("defense_line") or result.get("stop_loss"):
        risk_control_reference = {
            "reference": action_plan.get("defense_line") or result.get("stop_loss"),
            "reference_type": "setup_risk_control_reference",
        }
    return {
        "risk_state": risk_state,
        "risk_state_label": risk_state_label,
        "observation_conditions": dedupe_strings(observation_conditions),
        "discipline_triggers": dedupe_strings(discipline_triggers),
        "risk_control_reference": risk_control_reference,
        "command_language_deprecated": {
            "entry_zone": result.get("entry_zone"),
            "stop_loss": result.get("stop_loss"),
            "action_plan_action": action_plan.get("action"),
            "target_zone": action_plan.get("target_zone"),
            "suggested_position_size": action_plan.get("suggested_position_size"),
        },
    }


def as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped
