from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_BUCKETS


BUCKET_SETUP_TEMPLATES: Final[dict[str, str]] = {
    "institutional_accumulation": "法人籌碼延續觀察：{display_name} 目前歸類於法人籌碼延續型，觀察分數為 {score}。",
    "price_volume_strengthening": "量價結構轉強觀察：{display_name} 目前歸類於量價結構轉強型，觀察分數為 {score}。",
    "bottoming_reversal": "低位修復觀察：{display_name} 目前歸類於低位修復型，觀察分數為 {score}。",
    "support_retest": "支撐回測觀察：{display_name} 目前歸類於支撐回測型，觀察分數為 {score}。",
}

BUCKET_FOCUS_TEMPLATES: Final[dict[str, str]] = {
    "institutional_accumulation": "隔日觀察重點：留意法人淨流入是否延續，並確認價格是否維持在主要均線上方。",
    "price_volume_strengthening": "隔日觀察重點：留意量能是否維持高於均量，並確認收盤結構是否延續轉強。",
    "bottoming_reversal": "隔日觀察重點：留意低位修復是否持續，並確認動能指標是否逐步改善。",
    "support_retest": "隔日觀察重點：留意支撐區是否維持有效，並確認回測後的量能與價格穩定度。",
}

RISK_NOTE_TEMPLATES: Final[dict[str, str]] = {
    "overextended": "短線指標偏熱，隔日需留意波動擴大。",
    "flow_conflict": "籌碼方向不一致，需觀察後續資料是否收斂。",
    "margin_crowding": "融資籌碼偏擁擠，需留意追蹤期間的波動風險。",
    "market_weakness": "大盤結構偏弱，需把整體市場狀態納入觀察。",
    "data_gap": "資料完整度不足，需等待後續資料更新確認。",
}

DEFAULT_RISK_NOTE: Final[dict[str, str]] = {
    "label": "market_and_data_context",
    "note": "資料更新與大盤波動仍需納入隔日觀察。",
}


def generate_candidate_explanation(candidate: Mapping[str, Any]) -> dict[str, Any]:
    primary_bucket = _known_bucket(candidate.get("primary_bucket"))
    display_name = _display_name(candidate)
    score = _int(candidate.get("observation_score"))
    matched_rules = _matched_rules(candidate.get("matched_rules"))
    score_breakdown = _mapping(candidate.get("score_breakdown"))
    input_snapshot = _mapping(candidate.get("input_snapshot"))

    setup_summary = BUCKET_SETUP_TEMPLATES[primary_bucket].format(
        display_name=display_name,
        score=score,
    )
    evidence_points = _evidence_points(
        primary_bucket=primary_bucket,
        matched_rules=matched_rules,
        score_breakdown=score_breakdown,
        input_snapshot=input_snapshot,
    )
    risk_notes = _risk_notes(candidate.get("risk_labels"))
    next_day_focus = BUCKET_FOCUS_TEMPLATES[primary_bucket]
    text = _render_text(
        setup_summary=setup_summary,
        evidence_points=evidence_points,
        risk_notes=risk_notes,
        next_day_focus=next_day_focus,
    )

    return {
        "setup_summary": setup_summary,
        "evidence_points": evidence_points,
        "risk_notes": risk_notes,
        "next_day_focus": next_day_focus,
        "text": text,
    }


def generate_candidate_explanations(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [generate_candidate_explanation(candidate) for candidate in candidates]


def _evidence_points(
    *,
    primary_bucket: str,
    matched_rules: list[Mapping[str, Any]],
    score_breakdown: Mapping[str, Any],
    input_snapshot: Mapping[str, Any],
) -> list[str]:
    evidence: list[str] = []
    for rule in matched_rules[:2]:
        label = str(rule.get("label") or rule.get("rule_id") or "規則命中")
        details = _mapping(rule.get("details"))
        score_detail = _detail_number(details, "score")
        if score_detail is None:
            evidence.append(f"規則依據：{label}。")
        else:
            evidence.append(f"規則依據：{label}，分項分數 {score_detail:g}。")

    bucket_scores = _mapping(score_breakdown.get("bucket_scores"))
    bucket_score = _number(bucket_scores.get(primary_bucket))
    if bucket_score is not None:
        evidence.append(f"分數結構：主要觀察類型分數 {bucket_score:g}。")

    cross_confirmation = _number(score_breakdown.get("cross_confirmation"))
    if cross_confirmation is not None:
        evidence.append(f"交叉確認：量能、籌碼或技術項目合計 {cross_confirmation:g}。")

    evidence.extend(_input_evidence(input_snapshot))

    fallback = (
        "資料依據：候選資料已保留規則命中與分數拆解。",
        "觀察依據：主要類型與次要類型已完成排序前檢核。",
        "追蹤依據：隔日可用相同資料欄位對照變化。",
    )
    for item in fallback:
        if len(evidence) >= 3:
            break
        evidence.append(item)

    return _dedupe(evidence)[:5]


def _input_evidence(input_snapshot: Mapping[str, Any]) -> list[str]:
    ohlcv = _mapping(input_snapshot.get("ohlcv"))
    indicators = _mapping(input_snapshot.get("indicators"))
    institutional_flow = _mapping(input_snapshot.get("institutional_flow"))
    margin = _mapping(input_snapshot.get("margin"))
    data_dates = _mapping(input_snapshot.get("data_dates"))

    evidence: list[str] = []
    close = _number(ohlcv.get("close"))
    previous_close = _number(ohlcv.get("previous_close"))
    ma20 = _number(indicators.get("ma20"))
    if close is not None and previous_close is not None:
        direction = "高於" if close >= previous_close else "低於"
        evidence.append(f"價格資料：收盤 {close:g}，{direction}前一交易日 {previous_close:g}。")
    if close is not None and ma20 is not None:
        relation = "位於" if close >= ma20 else "低於"
        evidence.append(f"均線資料：收盤 {relation} MA20 {ma20:g} 附近。")

    volume_ratio = _number(indicators.get("volume_ratio"))
    if volume_ratio is not None:
        evidence.append(f"量能資料：成交量約為 20 日均量 {volume_ratio:g} 倍。")

    flow_days = _number(institutional_flow.get("consecutive_positive_days"))
    net_flow = _number(institutional_flow.get("three_party_net_shares"))
    if flow_days is not None and net_flow is not None:
        evidence.append(f"籌碼資料：法人淨流入連續 {flow_days:g} 日，合計 {net_flow:g} 張。")

    margin_delta_pct = _number(margin.get("margin_delta_pct"))
    margin_to_volume = _number(margin.get("margin_to_volume"))
    if margin_delta_pct is not None and margin_to_volume is not None:
        evidence.append(
            f"融資資料：融資變化 {margin_delta_pct:g}%，融資量能比 {margin_to_volume:g}。"
        )

    if data_dates:
        latest_date = max(str(value) for value in data_dates.values())
        evidence.append(f"資料日期：核心資料最新日期為 {latest_date}。")

    return evidence


def _risk_notes(risk_labels: Any) -> list[dict[str, str]]:
    labels = [str(label) for label in risk_labels if label] if isinstance(risk_labels, list) else []
    notes = [
        {"label": label, "note": RISK_NOTE_TEMPLATES.get(label, "未分類風險需保留觀察紀錄。")}
        for label in labels[:3]
    ]
    return notes or [dict(DEFAULT_RISK_NOTE)]


def _render_text(
    *,
    setup_summary: str,
    evidence_points: list[str],
    risk_notes: list[dict[str, str]],
    next_day_focus: str,
) -> str:
    evidence_text = " ".join(f"證據 {index}：{point}" for index, point in enumerate(evidence_points, start=1))
    risk_text = " ".join(f"風險留意 {index}：{note['note']}" for index, note in enumerate(risk_notes, start=1))
    return " ".join([setup_summary, evidence_text, risk_text, next_day_focus])


def _known_bucket(value: Any) -> str:
    bucket = str(value or "")
    if bucket in DAILY_RADAR_BUCKETS:
        return bucket
    return DAILY_RADAR_BUCKETS[0]


def _display_name(candidate: Mapping[str, Any]) -> str:
    name = str(candidate.get("name") or "").strip()
    symbol = str(candidate.get("symbol") or "").strip()
    if name and symbol:
        return f"{name} ({symbol})"
    return name or symbol or "候選標的"


def _matched_rules(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [rule for rule in value if isinstance(rule, Mapping)]


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _detail_number(details: Mapping[str, Any], key: str) -> float | None:
    return _number(details.get(key))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    number = _number(value)
    if number is None:
        return 0
    return max(0, min(100, round(number)))


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


__all__ = [
    "generate_candidate_explanation",
    "generate_candidate_explanations",
]
