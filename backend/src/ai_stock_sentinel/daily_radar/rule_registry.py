from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final, Literal

from ai_stock_sentinel.daily_radar.scoring import RULE_VERSION


RuleTier = Literal[
    "driver",
    "confirming_evidence",
    "risk_modifier",
    "context_only",
    "deprecated",
]
RuleValidationStatus = Literal[
    "validated",
    "monitoring",
    "insufficient_sample",
    "needs_ablation_review",
    "deprecated",
]


@dataclass(frozen=True)
class RuleRegistryEntry:
    rule_code: str
    description: str
    tier: RuleTier
    owner_module: str
    validation_status: RuleValidationStatus
    first_version: str
    last_reviewed_version: str
    ablation_group: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


SCORING_ACTIVE_TIERS: Final[set[RuleTier]] = {"driver", "confirming_evidence", "risk_modifier"}


def get_rule_registry() -> dict[str, RuleRegistryEntry]:
    return dict(RULE_REGISTRY)


def get_rule_entry(rule_code: str) -> RuleRegistryEntry:
    try:
        return RULE_REGISTRY[rule_code]
    except KeyError as exc:
        raise KeyError(f"Daily Radar rule is missing from registry: {rule_code}") from exc


def assert_rule_can_affect_score(rule_code: str) -> None:
    entry = get_rule_entry(rule_code)
    if entry.tier not in SCORING_ACTIVE_TIERS:
        raise ValueError(f"Rule {rule_code} is tier={entry.tier} and must not affect score")


def registry_payload() -> list[dict[str, str | None]]:
    return [RULE_REGISTRY[code].as_dict() for code in sorted(RULE_REGISTRY)]


def _entry(
    rule_code: str,
    description: str,
    tier: RuleTier,
    owner_module: str,
    validation_status: RuleValidationStatus = "monitoring",
    *,
    ablation_group: str | None = None,
    first_version: str = "daily-radar-rules-v2.1c",
    last_reviewed_version: str = RULE_VERSION,
) -> RuleRegistryEntry:
    return RuleRegistryEntry(
        rule_code=rule_code,
        description=description,
        tier=tier,
        owner_module=owner_module,
        validation_status=validation_status,
        first_version=first_version,
        last_reviewed_version=last_reviewed_version,
        ablation_group=ablation_group,
    )


RULE_REGISTRY: Final[dict[str, RuleRegistryEntry]] = {
    entry.rule_code: entry
    for entry in [
        _entry("institutional_consecutive_flow", "法人連續多日累積。", "driver", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_multi_day_flow", "法人多日累積。", "driver", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_early_flow", "法人初步累積。", "confirming_evidence", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_aligned_participants", "外資與投信方向一致。", "driver", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_net_positive", "三大法人合計轉正。", "confirming_evidence", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_constructive_state", "籌碼狀態支持觀察。", "driver", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_volume_confirmed_state", "籌碼與量能同步。", "confirming_evidence", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_early_stabilization", "籌碼初步穩定。", "confirming_evidence", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_flow_ratio_high", "法人淨流量占均量偏高。", "driver", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_flow_ratio_constructive", "法人淨流量占均量轉強。", "confirming_evidence", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_flow_ratio_positive", "法人淨流量為正。", "confirming_evidence", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("institutional_not_overextended", "法人累積時價格未過度延伸。", "risk_modifier", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("institutional_margin_contained", "法人累積時融資變化溫和。", "risk_modifier", "daily_radar.scoring", ablation_group="margin_related_risk_labels"),
        _entry("institutional_close_below_open", "法人累積但收盤轉弱。", "risk_modifier", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("price_volume_expanded_participation", "成交量明顯高於二十日均量。", "driver", "daily_radar.scoring", ablation_group="obv"),
        _entry("price_volume_constructive_participation", "成交量溫和放大。", "confirming_evidence", "daily_radar.scoring", ablation_group="obv"),
        _entry("price_volume_near_range_high", "收盤接近整理區上緣。", "driver", "daily_radar.scoring", ablation_group="donchian"),
        _entry("price_volume_ma20_reclaim", "收盤站上 MA20 且短均線轉強。", "driver", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("price_volume_obv_rising", "OBV 同步走升。", "confirming_evidence", "daily_radar.scoring", ablation_group="obv"),
        _entry("price_volume_obv_turning", "OBV 由平轉強。", "confirming_evidence", "daily_radar.scoring", ablation_group="obv"),
        _entry("price_volume_mfi_confirmed", "MFI 顯示資金參與。", "confirming_evidence", "daily_radar.scoring", ablation_group="mfi"),
        _entry("price_volume_macd_positive", "MACD 柱狀體為正。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("price_volume_close_above_previous", "收盤高於前一交易日。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("price_volume_volume_without_range_reclaim", "量能放大但仍未接近區間上緣。", "risk_modifier", "daily_radar.scoring", ablation_group="donchian"),
        _entry("bottoming_low_holds_support_zone", "低點守住支撐區。", "driver", "daily_radar.scoring", ablation_group="donchian"),
        _entry("bottoming_close_recovers", "收盤較前一交易日回穩。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("bottoming_macd_improving", "MACD 柱狀體跌勢收斂。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("bottoming_macd_positive", "MACD 柱狀體轉正。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("bottoming_kd_low_turn", "KD 低位翻正。", "confirming_evidence", "daily_radar.scoring", ablation_group="kd"),
        _entry("bottoming_bias_near_midline", "二十日乖離收斂。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("bottoming_participation_turning", "參與度由低位轉穩。", "confirming_evidence", "daily_radar.scoring", ablation_group="obv"),
        _entry("bottoming_rsi_mid_recovery", "RSI 回到中性修復區。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("bottoming_margin_easing", "融資餘額未同步升高。", "risk_modifier", "daily_radar.scoring", ablation_group="margin_related_risk_labels"),
        _entry("bottoming_participation_insufficient", "量能參與仍不足。", "risk_modifier", "daily_radar.scoring", ablation_group="obv"),
        _entry("support_retest_near_key_level", "盤中回測支撐區。", "driver", "daily_radar.scoring", ablation_group="donchian"),
        _entry("support_retest_reclaimed_area", "收盤收復支撐區。", "driver", "daily_radar.scoring", ablation_group="donchian"),
        _entry("support_retest_ma20_area", "收盤貼近 MA20。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("support_retest_ma60_area", "收盤貼近 MA60。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("support_retest_orderly_participation", "量能維持溫和。", "confirming_evidence", "daily_radar.scoring", ablation_group="obv"),
        _entry("support_retest_atr_contained", "ATR 波動可控。", "risk_modifier", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("support_retest_participation_stable", "OBV 未再轉弱。", "confirming_evidence", "daily_radar.scoring", ablation_group="obv"),
        _entry("support_retest_margin_not_expanding", "融資未同步擴張。", "risk_modifier", "daily_radar.scoring", ablation_group="margin_related_risk_labels"),
        _entry("support_retest_macd_stable", "MACD 柱狀體未明顯轉弱。", "confirming_evidence", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("support_retest_close_below_support", "收盤跌破支撐區。", "risk_modifier", "daily_radar.scoring", ablation_group="donchian"),
        _entry("risk_label_overextended", "短線指標偏熱風險。", "risk_modifier", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("risk_label_flow_conflict", "法人方向分歧風險。", "risk_modifier", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("risk_label_margin_crowding", "融資籌碼擁擠風險。", "risk_modifier", "daily_radar.scoring", ablation_group="margin_related_risk_labels"),
        _entry("risk_label_market_weakness", "大盤背景轉弱風險。", "risk_modifier", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("risk_label_data_gap", "資料完整度或時效不足風險。", "risk_modifier", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("cross_confirmation_institutional_flow", "籌碼交叉確認。", "confirming_evidence", "daily_radar.scoring", ablation_group="institutional_flow"),
        _entry("cross_confirmation_price_volume", "量價交叉確認。", "confirming_evidence", "daily_radar.scoring", ablation_group="obv"),
        _entry("cross_confirmation_technical", "技術交叉確認。", "confirming_evidence", "daily_radar.scoring", ablation_group="kd"),
        _entry("market_context_supportive", "大盤背景支持加分。", "risk_modifier", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("market_context_weakness_penalty", "大盤弱勢扣分。", "risk_modifier", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("freshness_bonus", "核心資料新鮮度加分。", "risk_modifier", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("freshness_data_gap_penalty", "資料缺口扣分。", "risk_modifier", "daily_radar.scoring", ablation_group="market_regime_penalty"),
        _entry("relative_strength", "相對大盤強弱分數。", "confirming_evidence", "daily_radar.scoring", ablation_group="relative_strength"),
        _entry("news_sentiment_context", "新聞情緒目前僅作背景脈絡。", "context_only", "daily_radar.explanations", "insufficient_sample", ablation_group="news_sentiment"),
        _entry("fundamental_valuation_context", "基本面估值目前僅作背景脈絡。", "context_only", "daily_radar.explanations", "insufficient_sample", ablation_group="fundamental_valuation"),
    ]
}


__all__ = [
    "RULE_REGISTRY",
    "RuleRegistryEntry",
    "RuleTier",
    "RuleValidationStatus",
    "SCORING_ACTIVE_TIERS",
    "assert_rule_can_affect_score",
    "get_rule_entry",
    "get_rule_registry",
    "registry_payload",
]
