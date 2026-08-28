from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date
import math
from typing import Any, cast

from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_BUCKETS, DAILY_RADAR_RISK_LABELS
from ai_stock_sentinel.daily_radar.data_quality import (
    DAILY_RADAR_REPLAY_INPUT_VERSION,
    missing_scoring_fields,
)
from ai_stock_sentinel.daily_radar.relative_strength import (
    DEFAULT_RELATIVE_STRENGTH_LOOKBACK_DAYS,
    calculate_relative_strength,
)
from ai_stock_sentinel.daily_radar.types import DailyRadarBucket, DailyRadarRiskLabel
from ai_stock_sentinel.technical.metrics import normalize_price_value_pct


SCORING_VERSION = "daily-radar-scoring-v2.7"
RULE_VERSION = "daily-radar-rules-v2.6"
SCORING_CONFIG_VERSION = "daily-radar-scoring-config-v2"

RULE_SCORE_ADJUSTMENTS: dict[str, int] = {
    "institutional_consecutive_flow": 28,
    "institutional_multi_day_flow": 22,
    "institutional_early_flow": 14,
    "institutional_aligned_participants": 18,
    "institutional_net_positive": 8,
    "institutional_same_day_net_buy": 8,
    "institutional_constructive_state": 18,
    "institutional_volume_confirmed_state": 12,
    "institutional_early_stabilization": 6,
    "institutional_flow_ratio_high": 14,
    "institutional_flow_ratio_constructive": 8,
    "institutional_flow_ratio_positive": 4,
    "institutional_not_overextended": 12,
    "institutional_margin_contained": 6,
    "institutional_close_below_open": -8,
    "price_volume_expanded_participation": 25,
    "price_volume_constructive_participation": 10,
    "price_volume_near_range_high": 18,
    "price_volume_ma20_reclaim": 15,
    "price_volume_obv_rising": 12,
    "price_volume_obv_turning": 8,
    "price_volume_mfi_confirmed": 10,
    "price_volume_macd_positive": 10,
    "price_volume_close_above_previous": 6,
    "price_volume_volume_without_range_reclaim": -10,
    "bottoming_low_holds_support_zone": 20,
    "bottoming_close_recovers": 10,
    "bottoming_macd_improving": 18,
    "bottoming_macd_positive": 8,
    "bottoming_kd_low_turn": 18,
    "bottoming_bias_near_midline": 12,
    "bottoming_participation_turning": 12,
    "bottoming_rsi_mid_recovery": 10,
    "bottoming_margin_easing": 8,
    "bottoming_participation_insufficient": -12,
    "support_retest_near_key_level": 22,
    "support_retest_reclaimed_area": 18,
    "support_retest_ma20_area": 16,
    "support_retest_ma60_area": 10,
    "support_retest_orderly_participation": 12,
    "support_retest_atr_contained": 10,
    "support_retest_participation_stable": 12,
    "support_retest_margin_not_expanding": 8,
    "support_retest_macd_stable": 7,
    "support_retest_close_below_support": -20,
}

RULE_BUCKETS: dict[str, str] = (
    {
        rule_code: "institutional_accumulation"
        for rule_code in RULE_SCORE_ADJUSTMENTS
        if rule_code.startswith("institutional_")
    }
    | {
        rule_code: "price_volume_strengthening"
        for rule_code in RULE_SCORE_ADJUSTMENTS
        if rule_code.startswith("price_volume_")
    }
    | {
        rule_code: "bottoming_reversal"
        for rule_code in RULE_SCORE_ADJUSTMENTS
        if rule_code.startswith("bottoming_")
    }
    | {
        rule_code: "support_retest"
        for rule_code in RULE_SCORE_ADJUSTMENTS
        if rule_code.startswith("support_retest_")
    }
)

RULE_SIGNAL_FAMILIES: dict[str, str] = {
    "institutional_consecutive_flow": "institutional_flow",
    "institutional_multi_day_flow": "institutional_flow",
    "institutional_early_flow": "institutional_flow",
    "institutional_aligned_participants": "institutional_flow",
    "institutional_net_positive": "institutional_flow",
    "institutional_same_day_net_buy": "institutional_flow",
    "institutional_constructive_state": "institutional_flow",
    "institutional_volume_confirmed_state": "institutional_flow",
    "institutional_early_stabilization": "institutional_flow",
    "institutional_flow_ratio_high": "institutional_flow",
    "institutional_flow_ratio_constructive": "institutional_flow",
    "institutional_flow_ratio_positive": "institutional_flow",
    "institutional_not_overextended": "momentum",
    "institutional_margin_contained": "margin",
    "institutional_close_below_open": "price_structure",
    "price_volume_expanded_participation": "participation",
    "price_volume_constructive_participation": "participation",
    "price_volume_near_range_high": "price_structure",
    "price_volume_ma20_reclaim": "price_structure",
    "price_volume_obv_rising": "participation",
    "price_volume_obv_turning": "participation",
    "price_volume_mfi_confirmed": "participation",
    "price_volume_macd_positive": "momentum",
    "price_volume_close_above_previous": "price_structure",
    "price_volume_volume_without_range_reclaim": "price_structure",
    "bottoming_low_holds_support_zone": "price_structure",
    "bottoming_close_recovers": "price_structure",
    "bottoming_macd_improving": "momentum",
    "bottoming_macd_positive": "momentum",
    "bottoming_kd_low_turn": "momentum",
    "bottoming_bias_near_midline": "momentum",
    "bottoming_participation_turning": "participation",
    "bottoming_rsi_mid_recovery": "momentum",
    "bottoming_margin_easing": "margin",
    "bottoming_participation_insufficient": "participation",
    "support_retest_near_key_level": "price_structure",
    "support_retest_reclaimed_area": "price_structure",
    "support_retest_ma20_area": "price_structure",
    "support_retest_ma60_area": "price_structure",
    "support_retest_orderly_participation": "participation",
    "support_retest_atr_contained": "volatility",
    "support_retest_participation_stable": "participation",
    "support_retest_margin_not_expanding": "margin",
    "support_retest_macd_stable": "momentum",
    "support_retest_close_below_support": "price_structure",
}

BUCKET_SIGNAL_FAMILY_CAPS: dict[str, dict[str, int]] = {
    "institutional_accumulation": {"institutional_flow": 65},
    "price_volume_strengthening": {
        "participation": 40,
        "price_structure": 36,
        "momentum": 10,
    },
    "bottoming_reversal": {
        "price_structure": 30,
        "momentum": 50,
        "participation": 12,
        "margin": 8,
    },
    "support_retest": {
        "price_structure": 50,
        "participation": 20,
        "volatility": 10,
        "momentum": 7,
        "margin": 8,
    },
}


@dataclass(frozen=True)
class ScoringConfig:
    primary_bucket_weight: float = 0.8
    cross_confirmation_weight: float = 1.0
    market_context_weight: float = 1.0
    freshness_weight: float = 1.0
    relative_strength_weight: float = 1.0
    secondary_bucket_threshold: int = 55
    relative_strength_lookback_days: int = DEFAULT_RELATIVE_STRENGTH_LOOKBACK_DAYS
    overextended_penalty: int = -18
    flow_conflict_penalty: int = -12
    margin_crowding_penalty: int = -15
    market_weakness_penalty: int = -12
    data_gap_penalty: int = -18
    weak_market_component: int = -4
    supportive_market_component: int = 4
    data_gap_freshness_component: int = -12
    fresh_freshness_component: int = 4

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


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
    excluded_rule_codes: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    active_config = config or ScoringConfig()
    excluded = excluded_rule_codes or frozenset()
    normalized = _normalize_record(record)
    ohlcv = normalized["ohlcv"]
    indicators = normalized["indicators"]
    technical_profile = normalized["technical_profile"]
    flow = normalized["institutional_flow"]
    margin = normalized["margin"]

    raw_bucket_rule_sets = {
        "institutional_accumulation": _score_institutional_accumulation(ohlcv, indicators, flow, margin),
        "price_volume_strengthening": _score_price_volume_strengthening(ohlcv, indicators),
        "bottoming_reversal": _score_bottoming_reversal(ohlcv, indicators, margin),
        "support_retest": _score_support_retest(ohlcv, indicators, margin),
    }
    if excluded:
        raw_bucket_rule_sets = {
            bucket: _without_rules(rule_set, excluded)
            for bucket, rule_set in raw_bucket_rule_sets.items()
        }
    capped_bucket_rule_sets = {
        bucket: _apply_signal_family_caps(bucket, rule_set)
        for bucket, rule_set in raw_bucket_rule_sets.items()
    }
    bucket_rule_sets = {
        bucket: (int(round(result["effective_score"])), result["rules"])
        for bucket, result in capped_bucket_rule_sets.items()
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
        config=active_config,
    )
    risk_penalties = [
        penalty
        for penalty in risk_penalties
        if _risk_penalty_rule_code(penalty) not in excluded
    ]
    risk_labels = _risk_labels(risk_penalties)
    cross_confirmation = _cross_confirmation(
        ohlcv,
        indicators,
        flow,
        excluded_rule_codes=excluded,
    )
    market_component = _market_context_component(
        market_context,
        risk_labels,
        config=active_config,
        excluded_rule_codes=excluded,
    )
    freshness_component = _freshness_component(
        risk_labels,
        config=active_config,
        excluded_rule_codes=excluded,
    )
    relative_strength_component = _relative_strength_component(
        normalized,
        market_context=market_context,
        lookback_days=active_config.relative_strength_lookback_days,
    )
    if "relative_strength" in excluded:
        relative_strength_component = dict(relative_strength_component) | {
            "score": 0,
            "excluded_from_score": True,
        }
    risk_adjustment = sum(int(penalty["score_adjustment"]) for penalty in risk_penalties)
    primary_bucket_score = bucket_scores[primary_bucket]
    weighted_primary_bucket_score = primary_bucket_score * active_config.primary_bucket_weight
    weighted_cross_confirmation_score = float(cross_confirmation["score"]) * active_config.cross_confirmation_weight
    weighted_market_component_score = float(market_component["score"]) * active_config.market_context_weight
    weighted_freshness_component_score = float(freshness_component["score"]) * active_config.freshness_weight
    weighted_relative_strength_score = float(relative_strength_component["score"]) * active_config.relative_strength_weight
    observation_score = _clamp_score(
        weighted_primary_bucket_score
        + weighted_cross_confirmation_score
        + weighted_market_component_score
        + weighted_freshness_component_score
        + weighted_relative_strength_score
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
            "bucket_signal_families": {
                bucket: {
                    "raw_score": result["raw_score"],
                    "effective_score": round(float(result["effective_score"]), 4),
                    "capped_points": result["capped_points"],
                    "families": result["families"],
                }
                for bucket, result in capped_bucket_rule_sets.items()
            },
            "primary_bucket_score": primary_bucket_score,
            "weighted_primary_bucket_score": weighted_primary_bucket_score,
            "cross_confirmation": _weighted_component(
                cross_confirmation,
                active_config.cross_confirmation_weight,
                weighted_cross_confirmation_score,
            ),
            "market_context": _weighted_component(
                market_component,
                active_config.market_context_weight,
                weighted_market_component_score,
            ),
            "relative_strength": _weighted_component(
                relative_strength_component,
                active_config.relative_strength_weight,
                weighted_relative_strength_score,
            ),
            "freshness": _weighted_component(
                freshness_component,
                active_config.freshness_weight,
                weighted_freshness_component_score,
            ),
            "technical_profile": _technical_profile_breakdown(technical_profile),
            "risk_penalties": risk_penalties,
            "risk_adjustment": risk_adjustment,
            "observation_score": observation_score,
            "config_version": SCORING_CONFIG_VERSION,
            "config": active_config.to_dict(),
        },
        "data_dates": _candidate_data_dates(normalized["data_dates"], market_context, relative_strength_component),
        "input_snapshot": {
            "versions": {
                "scoring_version": SCORING_VERSION,
                "rule_version": RULE_VERSION,
                "config_version": SCORING_CONFIG_VERSION,
            },
            "ohlcv": dict(ohlcv),
            "indicators": dict(indicators),
            "technical_profile": dict(technical_profile),
            "price_history": _price_history_trace(normalized["price_history"]),
            "institutional_flow": dict(flow),
            "universe": _universe_trace(flow),
            "margin": dict(margin),
            "market_context": dict(_mapping(market_context).get("market", {})),
            "relative_strength": _weighted_component(
                relative_strength_component,
                active_config.relative_strength_weight,
                weighted_relative_strength_score,
            ),
            "evidence": [_relative_strength_evidence(normalized["symbol"], relative_strength_component)],
            "replay_input": {
                "schema_version": DAILY_RADAR_REPLAY_INPUT_VERSION,
                "record": {
                    "symbol": normalized["symbol"],
                    "name": normalized["name"],
                    "record_date": normalized["record_date"],
                    "ohlcv": dict(ohlcv),
                    "indicators": dict(indicators),
                    "technical_profile": dict(technical_profile),
                    "price_history": list(normalized["price_history"]),
                    "institutional_flow": dict(flow),
                    "margin": dict(margin),
                    "data_dates": dict(normalized["data_dates"]),
                },
                "market_context": dict(_mapping(market_context)),
                "prefilter_result": dict(_mapping(prefilter_result)),
                "baseline_config": active_config.to_dict(),
            },
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
    foreign_cumulative_net = _float(flow.get("foreign_cumulative_net_shares"))
    trust_cumulative_net = _float(flow.get("trust_cumulative_net_shares"))
    same_day_actor = str(flow.get("same_day_actor") or "").strip().lower()
    same_day_net_buy = _float(flow.get("same_day_net_buy"))
    if foreign_net > 0 and trust_net > 0:
        score += 18
        rules.append(_rule("institutional_aligned_participants", "外資與投信方向一致", foreign_net=foreign_net, investment_trust_net=trust_net))
    elif (
        three_party_net > 0
        or foreign_cumulative_net > 0
        or trust_cumulative_net > 0
    ):
        score += 8
        net_positive_label = (
            "三大法人合計轉正"
            if three_party_net > 0
            else "外資或投信累計買超"
        )
        rules.append(
            _rule("institutional_net_positive", net_positive_label,
                three_party_net=three_party_net,
                foreign_cumulative_net=foreign_cumulative_net,
                trust_cumulative_net=trust_cumulative_net,
            )
        )
    elif (
        _has_any_universe_track(
            flow,
            {"same_day_institutional", "foreign_same_day", "trust_same_day"},
        )
        and same_day_actor in {"foreign", "trust"}
        and same_day_net_buy > 0
    ):
        score += 8
        rules.append(_rule("institutional_same_day_net_buy", "單一法人當日淨買超", same_day_actor=same_day_actor, same_day_net_buy=same_day_net_buy))

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

    if _has_numbers(indicators, "rsi14", "bias20", "mfi14", "volume_ratio") and not _is_overextended(indicators):
        score += 12
        rules.append(_rule("institutional_not_overextended", "價格未大幅脫離均線", rsi14=_float(indicators.get("rsi14")), bias20=_float(indicators.get("bias20"))))
    if _has_numbers(margin, "margin_delta_pct", "margin_to_volume") and _float(margin.get("margin_delta_pct")) <= 2 and _float(margin.get("margin_to_volume")) < 2:
        score += 6
        rules.append(_rule("institutional_margin_contained", "融資變化維持溫和", margin_delta_pct=_float(margin.get("margin_delta_pct"))))

    if _has_numbers(ohlcv, "close", "open") and _float(ohlcv.get("close")) < _float(ohlcv.get("open")):
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

    if (
        _has_numbers(ohlcv, "close")
        and _has_numbers(indicators, "resistance_level")
        and resistance
        and close >= resistance * 0.995
    ):
        score += 18
        rules.append(_rule("price_volume_near_range_high", "收盤接近整理區上緣", close=close, resistance_level=resistance))
    if _has_numbers(ohlcv, "close") and _has_numbers(indicators, "ma5", "ma20") and close > ma20 and ma5 > ma20:
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
    if _has_numbers(ohlcv, "close", "previous_close") and close > previous_close:
        score += 6
        rules.append(_rule("price_volume_close_above_previous", "收盤高於前一交易日", close=close, previous_close=previous_close))
    if (
        _has_numbers(ohlcv, "close")
        and _has_numbers(indicators, "resistance_level", "volume_ratio")
        and close < resistance * 0.98
        and volume_ratio >= 2.2
    ):
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
    macd_histogram_pct = _macd_hist_pct(ohlcv, indicators)

    if _has_numbers(ohlcv, "low") and _has_numbers(indicators, "support_level", "atr14") and support and low <= support + atr14:
        score += 20
        rules.append(_rule("bottoming_low_holds_support_zone", "低點守住支撐區", low=low, support_level=support, atr14=atr14))
    if _has_numbers(ohlcv, "close", "previous_close") and close > previous_close:
        score += 10
        rules.append(_rule("bottoming_close_recovers", "收盤較前一交易日回穩", close=close, previous_close=previous_close))
    if macd_histogram_pct is not None and -0.25 <= macd_histogram_pct <= 0.5:
        score += 18
        rules.append(_rule("bottoming_macd_improving", "MACD 柱狀體跌勢收斂", macd_hist_pct=macd_histogram_pct))
    elif macd_histogram_pct is not None and macd_histogram_pct > 0:
        score += 8
        rules.append(_rule("bottoming_macd_positive", "MACD 柱狀體轉正", macd_hist_pct=macd_histogram_pct))
    if _has_numbers(indicators, "kd_k", "kd_d") and kd_k > kd_d and kd_k <= 35:
        score += 18
        rules.append(_rule("bottoming_kd_low_turn", "KD 低位翻正", kd_k=kd_k, kd_d=kd_d))
    if _has_numbers(indicators, "bias20") and _float(indicators.get("bias20")) <= 1:
        score += 12
        rules.append(_rule("bottoming_bias_near_midline", "二十日乖離收斂", bias20=_float(indicators.get("bias20"))))
    if str(indicators.get("obv_trend") or "") in {"turning_up", "flat_to_up"}:
        score += 12
        rules.append(_rule("bottoming_participation_turning", "參與度由低位轉穩", obv_trend=str(indicators.get("obv_trend"))))
    if 35 <= _float(indicators.get("rsi14")) <= 55:
        score += 10
        rules.append(_rule("bottoming_rsi_mid_recovery", "RSI 回到中性修復區", rsi14=_float(indicators.get("rsi14"))))
    if _has_numbers(margin, "margin_delta_pct") and _float(margin.get("margin_delta_pct")) <= 0:
        score += 8
        rules.append(_rule("bottoming_margin_easing", "融資餘額未同步升高", margin_delta_pct=_float(margin.get("margin_delta_pct"))))
    if _has_numbers(indicators, "volume_ratio") and _float(indicators.get("volume_ratio")) < 0.95:
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

    if _has_numbers(ohlcv, "low") and _has_numbers(indicators, "support_level", "atr14") and support and low <= support + atr14:
        score += 22
        rules.append(_rule("support_retest_near_key_level", "盤中回測支撐區", low=low, support_level=support, atr14=atr14))
    if (
        _has_numbers(ohlcv, "close", "previous_close")
        and _has_numbers(indicators, "support_level")
        and support
        and close >= support
        and close > previous_close
    ):
        score += 18
        rules.append(_rule("support_retest_reclaimed_area", "收盤收復支撐區", close=close, support_level=support, previous_close=previous_close))
    if _has_numbers(ohlcv, "close") and _has_numbers(indicators, "ma20") and ma20 and abs(close - ma20) / ma20 <= 0.02:
        score += 16
        rules.append(_rule("support_retest_ma20_area", "收盤貼近 MA20", close=close, ma20=ma20))
    if _has_numbers(ohlcv, "close") and _has_numbers(indicators, "ma60") and ma60 and abs(close - ma60) / ma60 <= 0.03:
        score += 10
        rules.append(_rule("support_retest_ma60_area", "收盤貼近 MA60", close=close, ma60=ma60))
    if 0.95 <= volume_ratio <= 1.4:
        score += 12
        rules.append(_rule("support_retest_orderly_participation", "量能維持溫和", volume_ratio=volume_ratio))
    if _has_numbers(ohlcv, "close") and close and _has_numbers(indicators, "atr14") and atr14 / close <= 0.04:
        score += 10
        rules.append(_rule("support_retest_atr_contained", "ATR 波動可控", atr14=atr14, close=close))
    if str(indicators.get("obv_trend") or "") in {"flat_to_up", "turning_up", "rising"}:
        score += 12
        rules.append(_rule("support_retest_participation_stable", "OBV 未再轉弱", obv_trend=str(indicators.get("obv_trend"))))
    if _has_numbers(margin, "margin_delta_pct") and _float(margin.get("margin_delta_pct")) <= 0:
        score += 8
        rules.append(_rule("support_retest_margin_not_expanding", "融資未同步擴張", margin_delta_pct=_float(margin.get("margin_delta_pct"))))
    macd_histogram_pct = _macd_hist_pct(ohlcv, indicators)
    if macd_histogram_pct is not None and macd_histogram_pct >= -0.15:
        score += 7
        rules.append(_rule("support_retest_macd_stable", "MACD 柱狀體未明顯轉弱", macd_hist_pct=macd_histogram_pct))
    if _has_numbers(ohlcv, "close") and _has_numbers(indicators, "support_level") and support and close < support:
        score -= 20
        rules.append(_rule("support_retest_close_below_support", "收盤跌破支撐區", close=close, support_level=support))

    return score, rules


def _risk_penalties(
    record: Mapping[str, Any],
    *,
    market_context: Mapping[str, Any] | None,
    prefilter_result: Mapping[str, Any] | None,
    config: ScoringConfig,
) -> list[dict[str, Any]]:
    indicators = _mapping(record.get("indicators"))
    flow = _mapping(record.get("institutional_flow"))
    margin = _mapping(record.get("margin"))
    penalties: list[dict[str, Any]] = []
    risk_flags = _risk_flags(flow, margin)
    prefilter_codes = _prefilter_reason_codes(prefilter_result)
    missing_fields = missing_scoring_fields(
        ohlcv=_mapping(record.get("ohlcv")),
        indicators=indicators,
        institutional_flow=flow,
        margin=margin,
    )

    if "overextended" in risk_flags or _is_overextended(indicators):
        penalties.append(_penalty("overextended", config.overextended_penalty, "短期指標過熱", rsi14=_float(indicators.get("rsi14")), bias20=_float(indicators.get("bias20")), mfi14=_float(indicators.get("mfi14"))))

    if _has_flow_conflict(flow):
        penalties.append(_penalty("flow_conflict", config.flow_conflict_penalty, "法人方向分歧", flow_state=str(flow.get("flow_state") or ""), three_party_net_shares=_float(flow.get("three_party_net_shares"))))

    margin_delta_pct = _finite_float_or_none(margin.get("margin_delta_pct"))
    margin_to_volume = _finite_float_or_none(margin.get("margin_to_volume"))
    if (
        "margin_crowding" in risk_flags
        or (margin_delta_pct is not None and margin_delta_pct >= 10)
        or (margin_to_volume is not None and margin_to_volume >= 4)
    ):
        details: dict[str, Any] = {}
        if margin_delta_pct is not None:
            details["margin_delta_pct"] = margin_delta_pct
        else:
            details["margin_delta_pct_unavailable_reason"] = str(
                margin.get("margin_delta_pct_unavailable_reason") or "missing"
            )
        if margin_to_volume is not None:
            details["margin_to_volume"] = margin_to_volume
        penalties.append(
            _penalty(
                "margin_crowding",
                config.margin_crowding_penalty,
                "融資籌碼擁擠",
                **details,
            )
        )

    if _has_market_weakness(market_context):
        penalties.append(_penalty("market_weakness", config.market_weakness_penalty, "大盤背景轉弱", market=_mapping(_mapping(market_context).get("market"))))

    if (
        "data_gap" in risk_flags
        or "stale_data" in risk_flags
        or "data_gap" in prefilter_codes
        or "stale_core_data" in prefilter_codes
        or _int(indicators.get("missing_trading_days_60")) > 0
        or _has_stale_core_dates(record)
        or _symbol_context_has_flag(record, market_context, "data_gap")
        or _symbol_context_has_flag(record, market_context, "stale_data")
        or bool(missing_fields)
    ):
        penalties.append(_penalty(
            "data_gap",
            config.data_gap_penalty,
            "資料完整度或時效不足",
            missing_trading_days_60=_int(indicators.get("missing_trading_days_60")),
            missing_fields=missing_fields,
            data_dates=dict(_mapping(record.get("data_dates"))),
        ))

    return penalties


def _cross_confirmation(
    ohlcv: Mapping[str, Any],
    indicators: Mapping[str, Any],
    flow: Mapping[str, Any],
    *,
    excluded_rule_codes: set[str] | frozenset[str],
) -> dict[str, Any]:
    components: list[str] = []
    score = 0
    if (
        "cross_confirmation_institutional_flow" not in excluded_rule_codes
        and _float(flow.get("three_party_net_shares")) > 0
        and _int(flow.get("consecutive_positive_days")) >= 2
    ):
        score += 3
        components.append("institutional_flow")
    if (
        "cross_confirmation_price_volume" not in excluded_rule_codes
        and _float(indicators.get("volume_ratio")) >= 1.05
        and str(indicators.get("obv_trend") or "")
        in {"rising", "rising_fast", "turning_up", "flat_to_up"}
    ):
        score += 3
        components.append("price_volume")
    technical_confirmed = (
        (_has_numbers(indicators, "macd_histogram") and _float(indicators.get("macd_histogram")) >= 0)
        or (_has_numbers(indicators, "kd_k", "kd_d") and _float(indicators.get("kd_k")) > _float(indicators.get("kd_d")))
        or (
            _has_numbers(ohlcv, "close")
            and _has_numbers(indicators, "ma20")
            and _float(ohlcv.get("close")) > _float(indicators.get("ma20"))
        )
    )
    if (
        "cross_confirmation_technical" not in excluded_rule_codes
        and technical_confirmed
    ):
        score += 2
        components.append("technical")
    return {"score": min(8, score), "components": components}


def _market_context_component(
    market_context: Mapping[str, Any] | None,
    risk_labels: list[DailyRadarRiskLabel],
    *,
    config: ScoringConfig,
    excluded_rule_codes: set[str] | frozenset[str],
) -> dict[str, Any]:
    market = _mapping(_mapping(market_context).get("market"))
    if "market_weakness" in risk_labels:
        if "market_context_weakness_penalty" in excluded_rule_codes:
            return {"score": 0, "label": "weak_excluded", "details": dict(market)}
        return {"score": config.weak_market_component, "label": "weak", "details": dict(market)}
    if market.get("above_ma20") is True and market.get("above_ma60") is True and str(market.get("volatility_state") or "") in {"normal", "stable"}:
        if "market_context_supportive" in excluded_rule_codes:
            return {"score": 0, "label": "supportive_excluded", "details": dict(market)}
        return {"score": config.supportive_market_component, "label": "supportive", "details": dict(market)}
    return {"score": 0, "label": "neutral", "details": dict(market)}


def _freshness_component(
    risk_labels: list[DailyRadarRiskLabel],
    *,
    config: ScoringConfig,
    excluded_rule_codes: set[str] | frozenset[str],
) -> dict[str, Any]:
    if "data_gap" in risk_labels:
        if "freshness_data_gap_penalty" in excluded_rule_codes:
            return {"score": 0, "label": "data_gap_excluded"}
        return {"score": config.data_gap_freshness_component, "label": "data_gap"}
    if "freshness_bonus" in excluded_rule_codes:
        return {"score": 0, "label": "fresh_excluded"}
    return {"score": config.fresh_freshness_component, "label": "fresh"}


def _weighted_component(
    component: Mapping[str, Any],
    weight: float,
    weighted_score: float,
) -> dict[str, Any]:
    return dict(component) | {
        "weight": weight,
        "weighted_score": weighted_score,
    }


def _normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    source_record = _mapping(record.get("source_record"))
    if source_record:
        ohlcv = _mapping(source_record.get("ohlcv"))
        indicators = _mapping(source_record.get("indicators"))
        technical_profile = _mapping(source_record.get("technical_profile"))
        flow = _mapping(source_record.get("institutional_flow"))
        margin = _mapping(source_record.get("margin"))
    else:
        ohlcv = _mapping(record.get("ohlcv"))
        indicators = _mapping(record.get("indicators"))
        technical_profile = _mapping(record.get("technical_profile"))
        flow = _mapping(record.get("institutional_flow"))
        margin = _mapping(record.get("margin"))

    return {
        "symbol": str(record.get("symbol")),
        "name": str(record.get("name")),
        "record_date": str(record.get("record_date")),
        "ohlcv": ohlcv,
        "indicators": indicators,
        "technical_profile": technical_profile,
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


def _technical_profile_breakdown(technical_profile: Mapping[str, Any]) -> dict[str, Any]:
    if not technical_profile:
        return {"freshness": "missing", "missing_reason": "technical_profile_missing"}
    return {
        "version": technical_profile.get("version"),
        "formula_versions": dict(_mapping(technical_profile.get("formula_versions"))),
        "data_quality": dict(_mapping(technical_profile.get("data_quality"))),
        "score_summary": dict(_mapping(technical_profile.get("score_summary"))),
        "primary_score_inputs": _layer_impacts(technical_profile.get("primary_score_inputs")),
        "risk_overheat_filters": _layer_impacts(technical_profile.get("risk_overheat_filters")),
        "secondary_evidence": _layer_impacts(technical_profile.get("secondary_evidence")),
    }


def _layer_impacts(value: Any) -> dict[str, dict[str, Any]]:
    layers: dict[str, dict[str, Any]] = {}
    for key, item in _mapping(value).items():
        signal = _mapping(item)
        layers[str(key)] = {
            "state": signal.get("state"),
            "impact": signal.get("impact"),
            "reason": signal.get("reason"),
        }
    return layers


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


def _has_universe_track(flow: Mapping[str, Any], track: str) -> bool:
    if str(flow.get("universe_primary_track") or "").strip() == track:
        return True
    raw_tracks = flow.get("institutional_universe_tracks")
    return isinstance(raw_tracks, (list, tuple, set, frozenset)) and track in {
        str(value).strip() for value in raw_tracks
    }


def _has_any_universe_track(
    flow: Mapping[str, Any],
    tracks: set[str] | frozenset[str],
) -> bool:
    return any(_has_universe_track(flow, track) for track in tracks)


def _risk_labels(penalties: list[dict[str, Any]]) -> list[DailyRadarRiskLabel]:
    found = {penalty["label"] for penalty in penalties}
    return [label for label in DAILY_RADAR_RISK_LABELS if label in found]


def _rule(rule_id: str, label: str, **details: Any) -> dict[str, Any]:
    return {"rule_id": rule_id, "label": label, "details": details}


def _without_rules(
    rule_set: tuple[int, list[dict[str, Any]]],
    excluded_rule_codes: set[str] | frozenset[str],
) -> tuple[int, list[dict[str, Any]]]:
    _score, rules = rule_set
    remaining = [
        rule
        for rule in rules
        if str(rule.get("rule_id")) not in excluded_rule_codes
    ]
    return (
        sum(
            RULE_SCORE_ADJUSTMENTS[str(rule["rule_id"])]
            for rule in remaining
        ),
        remaining,
    )


def _apply_signal_family_caps(
    bucket: str,
    rule_set: tuple[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    raw_score, rules = rule_set
    calculated_raw_score = sum(
        RULE_SCORE_ADJUSTMENTS[str(rule["rule_id"])] for rule in rules
    )
    if calculated_raw_score != raw_score:
        raise ValueError(
            f"Daily Radar rule score drift for {bucket}: "
            f"declared={raw_score}, calculated={calculated_raw_score}"
        )
    caps = BUCKET_SIGNAL_FAMILY_CAPS.get(bucket, {})
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        rule_id = str(rule["rule_id"])
        family = RULE_SIGNAL_FAMILIES.get(rule_id, "independent")
        grouped.setdefault(family, []).append(rule)

    family_breakdown: dict[str, dict[str, Any]] = {}
    enriched_by_id: dict[str, dict[str, Any]] = {}
    effective_score = 0.0
    for family, family_rules in grouped.items():
        adjustments = [RULE_SCORE_ADJUSTMENTS[str(rule["rule_id"])] for rule in family_rules]
        positive_raw = sum(max(0, adjustment) for adjustment in adjustments)
        negative_raw = sum(min(0, adjustment) for adjustment in adjustments)
        cap = caps.get(family)
        positive_effective = min(positive_raw, cap) if cap is not None else positive_raw
        scale = positive_effective / positive_raw if positive_raw else 1.0
        family_effective = float(positive_effective + negative_raw)
        effective_score += family_effective
        family_breakdown[family] = {
            "raw_positive_score": positive_raw,
            "negative_score": negative_raw,
            "cap": cap,
            "effective_score": round(family_effective, 4),
            "capped_points": positive_raw - positive_effective,
            "rule_codes": [str(rule["rule_id"]) for rule in family_rules],
        }
        effective_adjustments = [
            round(adjustment * scale, 4) if adjustment > 0 else float(adjustment)
            for adjustment in adjustments
        ]
        positive_indexes = [
            index for index, adjustment in enumerate(adjustments) if adjustment > 0
        ]
        if positive_indexes:
            residual = round(
                positive_effective
                - sum(effective_adjustments[index] for index in positive_indexes),
                4,
            )
            last_positive_index = positive_indexes[-1]
            effective_adjustments[last_positive_index] = round(
                effective_adjustments[last_positive_index] + residual,
                4,
            )
        for rule, adjustment, effective_adjustment in zip(
            family_rules,
            adjustments,
            effective_adjustments,
            strict=True,
        ):
            enriched_by_id[str(rule["rule_id"])] = dict(rule) | {
                "signal_family": family,
                "raw_score_adjustment": adjustment,
                "effective_score_adjustment": effective_adjustment,
                "capped_points": round(
                    max(0.0, float(adjustment) - effective_adjustment),
                    4,
                ),
            }

    return {
        "raw_score": raw_score,
        "effective_score": effective_score,
        "capped_points": round(float(raw_score) - effective_score, 4),
        "families": family_breakdown,
        "rules": [enriched_by_id[str(rule["rule_id"])] for rule in rules],
    }


def _risk_penalty_rule_code(penalty: Mapping[str, Any]) -> str:
    return f"risk_label_{penalty.get('label')}"


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
        if data_date is None:
            return True
        lag_days = (record_date - data_date).days
        if lag_days < 0 or lag_days > 2:
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


def _finite_float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _has_numbers(payload: Mapping[str, Any], *keys: str) -> bool:
    return all(
        not isinstance(payload.get(key), bool)
        and payload.get(key) is not None
        for key in keys
    )


def _macd_hist_pct(
    ohlcv: Mapping[str, Any],
    indicators: Mapping[str, Any],
) -> float | None:
    normalized = _finite_float_or_none(indicators.get("macd_hist_pct"))
    if normalized is not None:
        return normalized
    return normalize_price_value_pct(
        _finite_float_or_none(indicators.get("macd_histogram")),
        _finite_float_or_none(ohlcv.get("close")),
    )


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    return int(value)


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _clamp_score(value: float | int) -> int:
    return max(0, min(100, int(round(value))))


__all__ = [
    "BUCKET_SIGNAL_FAMILY_CAPS",
    "RULE_VERSION",
    "RULE_BUCKETS",
    "RULE_SIGNAL_FAMILIES",
    "SCORING_CONFIG_VERSION",
    "ScoringConfig",
    "SCORING_VERSION",
    "score_daily_radar_record",
    "score_daily_radar_records",
]
