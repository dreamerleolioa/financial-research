from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_BUCKETS, DAILY_RADAR_RISK_LABELS
from ai_stock_sentinel.daily_radar.relative_strength import (
    DEFAULT_RELATIVE_STRENGTH_LOOKBACK_DAYS,
    calculate_relative_strength,
)
from ai_stock_sentinel.daily_radar.types import DailyRadarBucket, DailyRadarRiskLabel


SCORING_VERSION = "daily-radar-scoring-v2.1c"
RULE_VERSION = "daily-radar-rules-v2.1c"


@dataclass(frozen=True)
class ScoringConfig:
    secondary_bucket_threshold: int = 55
    relative_strength_lookback_days: int = DEFAULT_RELATIVE_STRENGTH_LOOKBACK_DAYS


def score_daily_radar_records(
    records: Iterable[Mapping[str, Any]],
    *,
    market_context: Mapping[str, Any] | None = None,
    prefilter_results: Iterable[Mapping[str, Any]] | None = None,
    config: ScoringConfig | None = None,
) -> list[dict[str, Any]]:
    prefilter_by_symbol = {
        str(result.get("symbol")): result
        for result in (prefilter_results or [])
    }
    return [
        score_daily_radar_record(
            record,
            market_context=market_context,
            prefilter_result=prefilter_by_symbol.get(str(record.get("symbol"))),
            config=config,
        )
        for record in records
    ]


def score_daily_radar_record(
    record: Mapping[str, Any],
    *,
    market_context: Mapping[str, Any] | None = None,
    prefilter_result: Mapping[str, Any] | None = None,
    config: ScoringConfig | None = None,
) -> dict[str, Any]:
    active_config = config or ScoringConfig()
    normalized = _normalize_record(record)
    ohlcv = normalized["ohlcv"]
    indicators = normalized["indicators"]
    flow = normalized["institutional_flow"]
    margin = normalized["margin"]

    bucket_rule_sets = {
        "institutional_accumulation": _score_institutional_accumulation(ohlcv, indicators, flow, margin),
        "price_volume_strengthening": _score_price_volume_strengthening(ohlcv, indicators),
        "bottoming_reversal": _score_bottoming_reversal(ohlcv, indicators, margin),
        "support_retest": _score_support_retest(ohlcv, indicators, margin),
    }
    bucket_scores = {
        bucket: _clamp_score(score)
        for bucket, (score, _rules) in bucket_rule_sets.items()
    }
    primary_bucket = cast(
        DailyRadarBucket,
        max(DAILY_RADAR_BUCKETS, key=lambda bucket: bucket_scores[bucket]),
    )
    secondary_buckets = [
        cast(DailyRadarBucket, bucket)
        for bucket in DAILY_RADAR_BUCKETS
        if bucket != primary_bucket and bucket_scores[bucket] >= active_config.secondary_bucket_threshold
    ]

    risk_penalties = _risk_penalties(
        normalized,
        market_context=market_context,
        prefilter_result=prefilter_result,
    )
    risk_labels = _risk_labels(risk_penalties)
    cross_confirmation = _cross_confirmation(ohlcv, indicators, flow)
    market_component = _market_context_component(market_context, risk_labels)
    freshness_component = _freshness_component(risk_labels)
    relative_strength_component = _relative_strength_component(
        normalized,
        market_context=market_context,
        lookback_days=active_config.relative_strength_lookback_days,
    )
    risk_adjustment = sum(int(penalty["score_adjustment"]) for penalty in risk_penalties)
    primary_bucket_score = bucket_scores[primary_bucket]
    weighted_primary_bucket_score = round(primary_bucket_score * 0.8)
    observation_score = _clamp_score(
        weighted_primary_bucket_score
        + int(cross_confirmation["score"])
        + int(market_component["score"])
        + int(freshness_component["score"])
        + int(relative_strength_component["score"])
        + risk_adjustment
    )

    matched_rules = [
        rule
        for bucket in DAILY_RADAR_BUCKETS
        for rule in bucket_rule_sets[bucket][1]
        if bucket_scores[bucket] >= active_config.secondary_bucket_threshold
    ]

    return {
        "symbol": normalized["symbol"],
        "name": normalized["name"],
        "record_date": normalized["record_date"],
        "primary_bucket": primary_bucket,
        "secondary_buckets": secondary_buckets,
        "observation_score": observation_score,
        "bucket_scores": bucket_scores,
        "risk_labels": risk_labels,
        "repeat_status": "new",
        "explanation": "",
        "scoring_version": SCORING_VERSION,
        "rule_version": RULE_VERSION,
        "matched_rules": matched_rules,
        "score_breakdown": {
            "scoring_version": SCORING_VERSION,
            "rule_version": RULE_VERSION,
            "bucket_scores": bucket_scores,
            "primary_bucket_score": primary_bucket_score,
            "weighted_primary_bucket_score": weighted_primary_bucket_score,
            "cross_confirmation": cross_confirmation,
            "market_context": market_component,
            "relative_strength": relative_strength_component,
            "freshness": freshness_component,
            "risk_penalties": risk_penalties,
            "risk_adjustment": risk_adjustment,
            "observation_score": observation_score,
        },
        "data_dates": _candidate_data_dates(normalized["data_dates"], market_context, relative_strength_component),
        "input_snapshot": {
            "versions": {
                "scoring_version": SCORING_VERSION,
                "rule_version": RULE_VERSION,
            },
            "ohlcv": dict(ohlcv),
            "indicators": dict(indicators),
            "price_history": _price_history_trace(normalized["price_history"]),
            "institutional_flow": dict(flow),
            "universe": _universe_trace(flow),
            "margin": dict(margin),
            "market_context": dict(_mapping(market_context).get("market", {})),
            "relative_strength": relative_strength_component,
            "evidence": [_relative_strength_evidence(normalized["symbol"], relative_strength_component)],
        },
    }


def _score_institutional_accumulation(
    ohlcv: Mapping[str, Any],
    indicators: Mapping[str, Any],
    flow: Mapping[str, Any],
    margin: Mapping[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    score = 0
    rules: list[dict[str, Any]] = []
    positive_days = _int(flow.get("consecutive_positive_days"))
    if positive_days >= 5:
        score += 28
        rules.append(_rule("institutional_consecutive_flow", "法人連續累積觀察", days=positive_days))
    elif positive_days >= 3:
        score += 22
        rules.append(_rule("institutional_multi_day_flow", "法人多日累積觀察", days=positive_days))
    elif positive_days >= 2:
        score += 14
        rules.append(_rule("institutional_early_flow", "法人初步累積觀察", days=positive_days))

    foreign_net = _float(flow.get("foreign_net_shares"))
    trust_net = _float(flow.get("investment_trust_net_shares"))
    three_party_net = _float(flow.get("three_party_net_shares"))
    if foreign_net > 0 and trust_net > 0:
        score += 18
        rules.append(_rule("institutional_aligned_participants", "外資與投信方向一致", foreign_net=foreign_net, investment_trust_net=trust_net))
    elif three_party_net > 0:
        score += 8
        rules.append(_rule("institutional_net_positive", "三大法人合計轉正", three_party_net=three_party_net))

    flow_state = str(flow.get("flow_state") or "")
    if flow_state in {"consistent_accumulation", "support_area_accumulation"}:
        score += 18
        rules.append(_rule("institutional_constructive_state", "籌碼狀態支持觀察", flow_state=flow_state))
    elif flow_state == "volume_confirmed_accumulation":
        score += 12
        rules.append(_rule("institutional_volume_confirmed_state", "籌碼與量能同步觀察", flow_state=flow_state))
    elif flow_state == "early_stabilization":
        score += 6
        rules.append(_rule("institutional_early_stabilization", "籌碼初步穩定", flow_state=flow_state))

    net_flow_ratio = _float(flow.get("net_flow_to_avg_volume"))
    if net_flow_ratio >= 0.15:
        score += 14
        rules.append(_rule("institutional_flow_ratio_high", "法人淨流量占均量偏高", net_flow_to_avg_volume=net_flow_ratio))
    elif net_flow_ratio >= 0.05:
        score += 8
        rules.append(_rule("institutional_flow_ratio_constructive", "法人淨流量占均量轉強", net_flow_to_avg_volume=net_flow_ratio))
    elif net_flow_ratio > 0:
        score += 4
        rules.append(_rule("institutional_flow_ratio_positive", "法人淨流量為正", net_flow_to_avg_volume=net_flow_ratio))

    if not _is_overextended(indicators):
        score += 12
        rules.append(_rule("institutional_not_overextended", "價格未大幅脫離均線", rsi14=_float(indicators.get("rsi14")), bias20=_float(indicators.get("bias20"))))
    if _float(margin.get("margin_delta_pct")) <= 2 and _float(margin.get("margin_to_volume")) < 2:
        score += 6
        rules.append(_rule("institutional_margin_contained", "融資變化維持溫和", margin_delta_pct=_float(margin.get("margin_delta_pct"))))

    if _float(ohlcv.get("close")) < _float(ohlcv.get("open")):
        score -= 8
        rules.append(_rule("institutional_close_below_open", "法人累積但收盤轉弱", close=_float(ohlcv.get("close")), open=_float(ohlcv.get("open"))))

    return score, rules


def _score_price_volume_strengthening(
    ohlcv: Mapping[str, Any],
    indicators: Mapping[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    score = 0
    rules: list[dict[str, Any]] = []
    close = _float(ohlcv.get("close"))
    previous_close = _float(ohlcv.get("previous_close"))
    volume_ratio = _float(indicators.get("volume_ratio"))
    resistance = _float(indicators.get("resistance_level"))
    ma5 = _float(indicators.get("ma5"))
    ma20 = _float(indicators.get("ma20"))

    if volume_ratio >= 1.5:
        score += 25
        rules.append(_rule("price_volume_expanded_participation", "成交量高於二十日均量", volume_ratio=volume_ratio))
    elif volume_ratio >= 1.25:
        score += 10
        rules.append(_rule("price_volume_constructive_participation", "成交量溫和放大", volume_ratio=volume_ratio))

    if resistance and close >= resistance * 0.995:
        score += 18
        rules.append(_rule("price_volume_near_range_high", "收盤接近整理區上緣", close=close, resistance_level=resistance))
    if close > ma20 and ma5 > ma20:
        score += 15
        rules.append(_rule("price_volume_ma20_reclaim", "收盤站上 MA20 且短均線轉強", close=close, ma20=ma20, ma5=ma5))

    obv_trend = str(indicators.get("obv_trend") or "")
    if obv_trend in {"rising", "rising_fast"}:
        score += 12
        rules.append(_rule("price_volume_obv_rising", "OBV 同步走升", obv_trend=obv_trend))
    elif obv_trend in {"turning_up", "flat_to_up"}:
        score += 8
        rules.append(_rule("price_volume_obv_turning", "OBV 由平轉強", obv_trend=obv_trend))

    if _float(indicators.get("mfi14")) >= 60:
        score += 10
        rules.append(_rule("price_volume_mfi_confirmed", "MFI 顯示資金參與", mfi14=_float(indicators.get("mfi14"))))
    if _float(indicators.get("macd_histogram")) > 0:
        score += 10
        rules.append(_rule("price_volume_macd_positive", "MACD 柱狀體為正", macd_histogram=_float(indicators.get("macd_histogram"))))
    if close > previous_close:
        score += 6
        rules.append(_rule("price_volume_close_above_previous", "收盤高於前一交易日", close=close, previous_close=previous_close))
    if close < resistance * 0.98 and volume_ratio >= 2.2:
        score -= 10
        rules.append(_rule("price_volume_volume_without_range_reclaim", "量能放大但仍未接近區間上緣", close=close, resistance_level=resistance))

    return score, rules


def _score_bottoming_reversal(
    ohlcv: Mapping[str, Any],
    indicators: Mapping[str, Any],
    margin: Mapping[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    score = 0
    rules: list[dict[str, Any]] = []
    close = _float(ohlcv.get("close"))
    low = _float(ohlcv.get("low"))
    previous_close = _float(ohlcv.get("previous_close"))
    support = _float(indicators.get("support_level"))
    atr14 = _float(indicators.get("atr14"))
    kd_k = _float(indicators.get("kd_k"))
    kd_d = _float(indicators.get("kd_d"))
    macd_histogram = _float(indicators.get("macd_histogram"))

    if support and low <= support + atr14:
        score += 20
        rules.append(_rule("bottoming_low_holds_support_zone", "低點守住支撐區", low=low, support_level=support, atr14=atr14))
    if close > previous_close:
        score += 10
        rules.append(_rule("bottoming_close_recovers", "收盤較前一交易日回穩", close=close, previous_close=previous_close))
    if -0.25 <= macd_histogram <= 0.5:
        score += 18
        rules.append(_rule("bottoming_macd_improving", "MACD 柱狀體跌勢收斂", macd_histogram=macd_histogram))
    elif macd_histogram > 0:
        score += 8
        rules.append(_rule("bottoming_macd_positive", "MACD 柱狀體轉正", macd_histogram=macd_histogram))
    if kd_k > kd_d and kd_k <= 35:
        score += 18
        rules.append(_rule("bottoming_kd_low_turn", "KD 低位翻正", kd_k=kd_k, kd_d=kd_d))
    if _float(indicators.get("bias20")) <= 1:
        score += 12
        rules.append(_rule("bottoming_bias_near_midline", "二十日乖離收斂", bias20=_float(indicators.get("bias20"))))
    if str(indicators.get("obv_trend") or "") in {"turning_up", "flat_to_up"}:
        score += 12
        rules.append(_rule("bottoming_participation_turning", "參與度由低位轉穩", obv_trend=str(indicators.get("obv_trend"))))
    if 35 <= _float(indicators.get("rsi14")) <= 55:
        score += 10
        rules.append(_rule("bottoming_rsi_mid_recovery", "RSI 回到中性修復區", rsi14=_float(indicators.get("rsi14"))))
    if _float(margin.get("margin_delta_pct")) <= 0:
        score += 8
        rules.append(_rule("bottoming_margin_easing", "融資餘額未同步升高", margin_delta_pct=_float(margin.get("margin_delta_pct"))))
    if _float(indicators.get("volume_ratio")) < 0.95:
        score -= 12
        rules.append(_rule("bottoming_participation_insufficient", "量能參與仍不足", volume_ratio=_float(indicators.get("volume_ratio"))))

    return score, rules


def _score_support_retest(
    ohlcv: Mapping[str, Any],
    indicators: Mapping[str, Any],
    margin: Mapping[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    score = 0
    rules: list[dict[str, Any]] = []
    close = _float(ohlcv.get("close"))
    low = _float(ohlcv.get("low"))
    previous_close = _float(ohlcv.get("previous_close"))
    support = _float(indicators.get("support_level"))
    atr14 = _float(indicators.get("atr14"))
    ma20 = _float(indicators.get("ma20"))
    ma60 = _float(indicators.get("ma60"))
    volume_ratio = _float(indicators.get("volume_ratio"))

    if support and low <= support + atr14:
        score += 22
        rules.append(_rule("support_retest_near_key_level", "盤中回測支撐區", low=low, support_level=support, atr14=atr14))
    if support and close >= support and close > previous_close:
        score += 18
        rules.append(_rule("support_retest_reclaimed_area", "收盤收復支撐區", close=close, support_level=support, previous_close=previous_close))
    if ma20 and abs(close - ma20) / ma20 <= 0.02:
        score += 16
        rules.append(_rule("support_retest_ma20_area", "收盤貼近 MA20", close=close, ma20=ma20))
    if ma60 and abs(close - ma60) / ma60 <= 0.03:
        score += 10
        rules.append(_rule("support_retest_ma60_area", "收盤貼近 MA60", close=close, ma60=ma60))
    if 0.95 <= volume_ratio <= 1.4:
        score += 12
        rules.append(_rule("support_retest_orderly_participation", "量能維持溫和", volume_ratio=volume_ratio))
    if close and atr14 / close <= 0.04:
        score += 10
        rules.append(_rule("support_retest_atr_contained", "ATR 波動可控", atr14=atr14, close=close))
    if str(indicators.get("obv_trend") or "") in {"flat_to_up", "turning_up", "rising"}:
        score += 12
        rules.append(_rule("support_retest_participation_stable", "OBV 未再轉弱", obv_trend=str(indicators.get("obv_trend"))))
    if _float(margin.get("margin_delta_pct")) <= 0:
        score += 8
        rules.append(_rule("support_retest_margin_not_expanding", "融資未同步擴張", margin_delta_pct=_float(margin.get("margin_delta_pct"))))
    if _float(indicators.get("macd_histogram")) >= -0.15:
        score += 7
        rules.append(_rule("support_retest_macd_stable", "MACD 柱狀體未明顯轉弱", macd_histogram=_float(indicators.get("macd_histogram"))))
    if support and close < support:
        score -= 20
        rules.append(_rule("support_retest_close_below_support", "收盤跌破支撐區", close=close, support_level=support))

    return score, rules


def _risk_penalties(
    record: Mapping[str, Any],
    *,
    market_context: Mapping[str, Any] | None,
    prefilter_result: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    indicators = _mapping(record.get("indicators"))
    flow = _mapping(record.get("institutional_flow"))
    margin = _mapping(record.get("margin"))
    penalties: list[dict[str, Any]] = []
    risk_flags = _risk_flags(flow, margin)
    prefilter_codes = _prefilter_reason_codes(prefilter_result)

    if "overextended" in risk_flags or _is_overextended(indicators):
        penalties.append(_penalty("overextended", -18, "短期指標過熱", rsi14=_float(indicators.get("rsi14")), bias20=_float(indicators.get("bias20")), mfi14=_float(indicators.get("mfi14"))))

    if _has_flow_conflict(flow):
        penalties.append(_penalty("flow_conflict", -12, "法人方向分歧", flow_state=str(flow.get("flow_state") or ""), three_party_net_shares=_float(flow.get("three_party_net_shares"))))

    if "margin_crowding" in risk_flags or _float(margin.get("margin_delta_pct")) >= 10 or _float(margin.get("margin_to_volume")) >= 4:
        penalties.append(_penalty("margin_crowding", -15, "融資籌碼擁擠", margin_delta_pct=_float(margin.get("margin_delta_pct")), margin_to_volume=_float(margin.get("margin_to_volume"))))

    if _has_market_weakness(market_context):
        penalties.append(_penalty("market_weakness", -12, "大盤背景轉弱", market=_mapping(_mapping(market_context).get("market"))))

    if (
        "data_gap" in risk_flags
        or "stale_data" in risk_flags
        or "data_gap" in prefilter_codes
        or "stale_core_data" in prefilter_codes
        or _int(indicators.get("missing_trading_days_60")) > 0
        or _has_stale_core_dates(record)
        or _symbol_context_has_flag(record, market_context, "data_gap")
        or _symbol_context_has_flag(record, market_context, "stale_data")
    ):
        penalties.append(_penalty("data_gap", -18, "資料完整度或時效不足", missing_trading_days_60=_int(indicators.get("missing_trading_days_60")), data_dates=dict(_mapping(record.get("data_dates")))))

    return penalties


def _cross_confirmation(
    ohlcv: Mapping[str, Any],
    indicators: Mapping[str, Any],
    flow: Mapping[str, Any],
) -> dict[str, Any]:
    components: list[str] = []
    score = 0
    if _float(flow.get("three_party_net_shares")) > 0 and _int(flow.get("consecutive_positive_days")) >= 2:
        score += 3
        components.append("institutional_flow")
    if _float(indicators.get("volume_ratio")) >= 1.05 and str(indicators.get("obv_trend") or "") in {"rising", "rising_fast", "turning_up", "flat_to_up"}:
        score += 3
        components.append("price_volume")
    if _float(indicators.get("macd_histogram")) >= 0 or _float(indicators.get("kd_k")) > _float(indicators.get("kd_d")) or _float(ohlcv.get("close")) > _float(indicators.get("ma20")):
        score += 2
        components.append("technical")
    return {"score": min(8, score), "components": components}


def _market_context_component(
    market_context: Mapping[str, Any] | None,
    risk_labels: list[DailyRadarRiskLabel],
) -> dict[str, Any]:
    market = _mapping(_mapping(market_context).get("market"))
    if "market_weakness" in risk_labels:
        return {"score": -4, "label": "weak", "details": dict(market)}
    if market.get("above_ma20") is True and market.get("above_ma60") is True and str(market.get("volatility_state") or "") in {"normal", "stable"}:
        return {"score": 4, "label": "supportive", "details": dict(market)}
    return {"score": 0, "label": "neutral", "details": dict(market)}


def _freshness_component(risk_labels: list[DailyRadarRiskLabel]) -> dict[str, Any]:
    if "data_gap" in risk_labels:
        return {"score": -12, "label": "data_gap"}
    return {"score": 4, "label": "fresh"}


def _normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    source_record = _mapping(record.get("source_record"))
    if source_record:
        ohlcv = _mapping(source_record.get("ohlcv"))
        indicators = _mapping(source_record.get("indicators"))
        flow = _mapping(source_record.get("institutional_flow"))
        margin = _mapping(source_record.get("margin"))
    else:
        ohlcv = _mapping(record.get("ohlcv"))
        indicators = _mapping(record.get("indicators"))
        flow = _mapping(record.get("institutional_flow"))
        margin = _mapping(record.get("margin"))

    return {
        "symbol": str(record.get("symbol")),
        "name": str(record.get("name")),
        "record_date": str(record.get("record_date")),
        "ohlcv": ohlcv,
        "indicators": indicators,
        "price_history": _as_list(source_record.get("price_history") if source_record else record.get("price_history")),
        "institutional_flow": flow,
        "margin": margin,
        "data_dates": _mapping(record.get("data_dates")),
    }


def _candidate_data_dates(
    record_data_dates: Mapping[str, Any],
    market_context: Mapping[str, Any] | None,
    relative_strength: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    data_dates = dict(record_data_dates)
    data_dates.update(dict(_mapping(_mapping(market_context).get("data_dates"))))
    data_dates.update(dict(_mapping(_mapping(relative_strength).get("data_dates"))))
    return data_dates


def _relative_strength_component(
    record: Mapping[str, Any],
    *,
    market_context: Mapping[str, Any] | None,
    lookback_days: int,
) -> dict[str, Any]:
    run_date = _parse_date(str(record.get("record_date")))
    benchmark = _mapping(_mapping(market_context).get("benchmark"))
    market = _mapping(_mapping(market_context).get("market"))
    benchmark_symbol = str(
        benchmark.get("symbol")
        or market.get("index_symbol")
        or "UNKNOWN_BENCHMARK"
    )
    if run_date is None:
        return {
            "benchmark_symbol": benchmark_symbol,
            "lookback_days": lookback_days,
            "candidate_return": None,
            "benchmark_return": None,
            "relative_value": None,
            "score": 0,
            "weight": 1.0,
            "freshness": "missing",
            "missing_reason": "record_date_missing",
            "data_dates": {},
            "aligned_dates": [],
        }
    return calculate_relative_strength(
        symbol=str(record.get("symbol")),
        candidate_price_history=_as_mapping_list(record.get("price_history")),
        benchmark_price_history=_as_mapping_list(benchmark.get("price_history")),
        benchmark_symbol=benchmark_symbol,
        run_date=run_date,
        lookback_days=lookback_days,
        benchmark_data_date=market.get("data_date") or _mapping(benchmark.get("data_dates")).get("market_index"),
    )


def _relative_strength_evidence(symbol: str, relative_strength: Mapping[str, Any]) -> dict[str, Any]:
    data_dates = _mapping(relative_strength.get("data_dates"))
    benchmark_symbol = str(relative_strength.get("benchmark_symbol") or "UNKNOWN_BENCHMARK")
    lookback_days = _int(relative_strength.get("lookback_days"))
    replay_key = relative_strength.get("replay_key") or f"relative_strength:{symbol}:{benchmark_symbol}:missing:L{lookback_days}"
    return {
        "evidence_type": "relative_strength",
        "source": {
            "domain": "daily_trigger_signal",
            "provider": "deterministic_relative_strength",
            "benchmark_symbol": benchmark_symbol,
        },
        "as_of_date": data_dates.get("relative_strength") or data_dates.get("relative_strength_benchmark"),
        "freshness": str(relative_strength.get("freshness") or "missing"),
        "missing_reason": relative_strength.get("missing_reason"),
        "replay_key": str(replay_key),
        "applicable_consumers": ["daily_radar"],
        "details": {
            "lookback_days": lookback_days,
            "candidate_return": relative_strength.get("candidate_return"),
            "benchmark_return": relative_strength.get("benchmark_return"),
            "relative_value": relative_strength.get("relative_value"),
            "score": relative_strength.get("score"),
        },
    }


def _price_history_trace(price_history: list[Any]) -> dict[str, Any]:
    price_dates = [
        parsed
        for item in _as_mapping_list(price_history)
        if (parsed := _parse_date(str(item.get("date")))) is not None
    ]
    return {
        "points": len(price_dates),
        "latest_date": max(price_dates).isoformat() if price_dates else None,
    }


def _universe_trace(flow: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: flow[key]
        for key in (
            "universe_primary_track",
            "institutional_universe_tracks",
            "universe_track_metrics",
            "scores",
        )
        if key in flow
    }


def _risk_labels(penalties: list[dict[str, Any]]) -> list[DailyRadarRiskLabel]:
    found = {penalty["label"] for penalty in penalties}
    return [label for label in DAILY_RADAR_RISK_LABELS if label in found]


def _rule(rule_id: str, label: str, **details: Any) -> dict[str, Any]:
    return {"rule_id": rule_id, "label": label, "details": details}


def _penalty(label: DailyRadarRiskLabel, score_adjustment: int, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "label": label,
        "score_adjustment": score_adjustment,
        "reason": reason,
        "details": details,
    }


def _risk_flags(*payloads: Mapping[str, Any]) -> set[str]:
    flags: set[str] = set()
    for payload in payloads:
        risk_flags = payload.get("risk_flags")
        if isinstance(risk_flags, list):
            flags.update(str(flag) for flag in risk_flags)
    return flags


def _prefilter_reason_codes(prefilter_result: Mapping[str, Any] | None) -> set[str]:
    codes: set[str] = set()
    for reason in _as_list(_mapping(prefilter_result).get("prefilter_reasons")):
        if isinstance(reason, Mapping):
            codes.add(str(reason.get("code")))
    return codes


def _has_flow_conflict(flow: Mapping[str, Any]) -> bool:
    foreign_net = _float(flow.get("foreign_net_shares"))
    trust_net = _float(flow.get("investment_trust_net_shares"))
    return (
        str(flow.get("flow_state") or "") in {"conflict", "weak_confirmation"}
        or _float(flow.get("three_party_net_shares")) < 0
        or _int(flow.get("consecutive_negative_days")) >= 3
        or (foreign_net > 0 > trust_net)
        or (trust_net > 0 > foreign_net)
    )


def _has_market_weakness(market_context: Mapping[str, Any] | None) -> bool:
    market = _mapping(_mapping(market_context).get("market"))
    flags = market.get("market_risk_flags")
    return (
        (isinstance(flags, list) and "market_weakness" in flags)
        or market.get("above_ma20") is False
        or market.get("above_ma60") is False
        or str(market.get("volatility_state") or "") in {"elevated", "high"}
    )


def _symbol_context_has_flag(
    record: Mapping[str, Any],
    market_context: Mapping[str, Any] | None,
    flag: str,
) -> bool:
    symbol = str(record.get("symbol"))
    for override in _as_list(_mapping(market_context).get("symbol_overrides")):
        if not isinstance(override, Mapping) or str(override.get("symbol")) != symbol:
            continue
        flags = override.get("context_flags")
        return isinstance(flags, list) and flag in flags
    return False


def _has_stale_core_dates(record: Mapping[str, Any]) -> bool:
    record_date = _parse_date(str(record.get("record_date")))
    if record_date is None:
        return False
    for value in _mapping(record.get("data_dates")).values():
        data_date = _parse_date(str(value))
        if data_date is None or (record_date - data_date).days > 2:
            return True
    return False


def _is_overextended(indicators: Mapping[str, Any]) -> bool:
    return (
        _float(indicators.get("rsi14")) >= 80
        or _float(indicators.get("bias20")) >= 20
        or _float(indicators.get("mfi14")) >= 85
        or _float(indicators.get("volume_ratio")) >= 2.5
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _as_mapping_list(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in _as_list(value) if isinstance(item, Mapping)]


def _float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    return float(value)


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    return int(value)


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _clamp_score(value: int) -> int:
    return max(0, min(100, int(round(value))))


__all__ = [
    "RULE_VERSION",
    "ScoringConfig",
    "SCORING_VERSION",
    "score_daily_radar_record",
    "score_daily_radar_records",
]
