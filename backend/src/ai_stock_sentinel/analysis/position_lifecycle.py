from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.metrics import calc_rsi, ma
from ai_stock_sentinel.analysis.review_sources import completed_trailing_series, market_snapshot_payload
from ai_stock_sentinel.db.models import PositionEvent, PositionLifecyclePlan, StockRawData
from ai_stock_sentinel.shared_context import (
    SHARED_CONTEXT_CONSUMER_LIFECYCLE,
    aggregate_shared_context_quality,
    read_shared_context_for_symbol,
)


ENTRY_TYPES = {"initial_entry", "add_entry"}
EXIT_TYPES = {"partial_exit", "full_exit"}
MAX_DETECTED_EVENTS = 8
POSITION_LIFECYCLE_LOOKBACK_DAYS = 120


def build_position_lifecycle_analysis(
    db: Session,
    *,
    user_id: int,
    position_group_id: str,
) -> tuple[dict, dict]:
    with db.no_autoflush:
        events = db.execute(
            select(PositionEvent)
            .where(
                PositionEvent.user_id == user_id,
                PositionEvent.position_group_id == position_group_id,
            )
            .order_by(PositionEvent.event_date.asc(), PositionEvent.created_at.asc(), PositionEvent.id.asc())
        ).scalars().all()
        plan = db.execute(
            select(PositionLifecyclePlan).where(
                PositionLifecyclePlan.user_id == user_id,
                PositionLifecyclePlan.position_group_id == position_group_id,
            )
        ).scalar_one_or_none()

        symbol = _event_value(events[0], "symbol") if events else _event_value(plan, "symbol")
        analysis_start = _analysis_start_date(events)
        analysis_end = _analysis_end_date(events)
        market_rows: list[StockRawData] = []
        if symbol is not None and analysis_end is not None:
            market_query = select(StockRawData).where(
                StockRawData.symbol == symbol,
                StockRawData.record_date <= analysis_end,
                StockRawData.raw_data_is_final.is_(True),
            )
            if analysis_start is not None:
                market_query = market_query.where(
                    StockRawData.record_date
                    >= analysis_start
                    - timedelta(days=POSITION_LIFECYCLE_LOOKBACK_DAYS)
                )
            market_rows = db.execute(
                market_query
                .order_by(StockRawData.record_date.asc())
            ).scalars().all()
        shared_context = _build_event_shared_context(
            db,
            symbol=symbol or "",
            events=events,
        )

    return build_position_lifecycle_analysis_from_rows(
        position_group_id=position_group_id,
        symbol=symbol or "",
        events=events,
        market_rows=market_rows,
        plan=plan,
        shared_context=shared_context,
    )


def build_position_lifecycle_analysis_from_rows(
    *,
    position_group_id: str,
    symbol: str,
    events,
    market_rows=(),
    plan=None,
    shared_context: dict[str, Any] | None = None,
) -> tuple[dict, dict]:
    ordered_events = _sort_events(list(events or ()))
    ordered_rows = _sort_market_rows(list(market_rows or ()))
    data_quality = _empty_data_quality()

    if not ordered_events:
        _add_note(data_quality, "events_missing", "No PositionEvent rows were available for this position_group_id.")

    accounting = _build_accounting_timeline(ordered_events, data_quality)
    event_snapshots = _build_event_indicator_snapshots(ordered_events, ordered_rows, data_quality)
    lifecycle_metrics = _build_lifecycle_metrics(ordered_events, ordered_rows, accounting, data_quality)
    entry_sequence = _build_entry_sequence(ordered_events, accounting, event_snapshots)
    exit_sequence = _build_exit_sequence(ordered_events, accounting, event_snapshots, ordered_rows)
    decision_context = _build_decision_context(plan, data_quality)
    advanced_internal = _build_advanced_internal(
        ordered_events,
        ordered_rows,
        accounting,
        lifecycle_metrics,
        plan,
        decision_context,
    )
    detected_events = _detect_market_events(ordered_events, ordered_rows)
    market_regime_snapshots = _market_regime_snapshots(event_snapshots)
    shared_context_payload = shared_context or _empty_lifecycle_shared_context(symbol)
    source_data = _source_data(symbol, ordered_events, ordered_rows, plan)
    event_facts = _compact_events(ordered_events)
    finalized_data_quality = _finalize_data_quality(data_quality)
    lifecycle_review = _build_lifecycle_review(
        lifecycle_metrics,
        entry_sequence,
        exit_sequence,
        advanced_internal,
        event_snapshots,
        event_facts,
        decision_context,
        finalized_data_quality,
        shared_context_payload,
    )

    result = {
        "position_group_id": position_group_id,
        "symbol": symbol,
        "lifecycle_metrics": lifecycle_metrics,
        "entry_sequence": entry_sequence,
        "exit_sequence": exit_sequence,
        "advanced_internal": advanced_internal,
        "event_indicator_snapshots": event_snapshots,
        "event_facts": event_facts,
        "decision_context": decision_context,
        "shared_context": shared_context_payload,
        "data_quality": finalized_data_quality,
        "lifecycle_review": lifecycle_review,
    }
    evidence_payload = {
        "position_group_id": position_group_id,
        "symbol": symbol,
        "metrics": {
            "lifecycle": lifecycle_metrics,
            "entry_sequence": entry_sequence,
            "exit_sequence": exit_sequence,
            "advanced_internal": advanced_internal,
        },
        "events": event_facts,
        "indicator_snapshots": event_snapshots,
        "detected_events": detected_events,
        "market_regime_snapshots": market_regime_snapshots,
        "shared_context": shared_context_payload,
        "decision_context": decision_context,
        "plan_snapshot": _plan_source_data(plan),
        "market_snapshot": market_snapshot_payload(
            ordered_rows,
            provider="stock_raw_data_read_only",
            holding_start=_analysis_start_date(ordered_events),
            holding_end=(
                _analysis_end_date(ordered_events) + timedelta(days=1)
                if _analysis_end_date(ordered_events) is not None
                else None
            ),
            compact=True,
        ),
        "source_data": source_data,
        "data_quality": result["data_quality"],
    }
    return result, evidence_payload


def _build_lifecycle_review(
    lifecycle_metrics: dict[str, Any],
    entry_sequence: dict[str, Any],
    exit_sequence: dict[str, Any],
    advanced_internal: dict[str, Any],
    snapshots: list[dict[str, Any]],
    event_facts: list[dict[str, Any]],
    decision_context: dict[str, Any],
    data_quality: dict[str, Any],
    shared_context: dict[str, Any],
) -> dict[str, Any]:
    labels: list[str] = []
    reasons: list[dict[str, Any]] = []
    caveats: list[dict[str, Any]] = []
    what_worked: list[dict[str, Any]] = []
    what_needs_review: list[dict[str, Any]] = []
    event_level_evidence: list[dict[str, Any]] = []
    next_operation_rules: list[dict[str, Any]] = []
    data_quality_notes: list[dict[str, Any]] = []

    snapshot_by_key = {snapshot["event_key"]: snapshot for snapshot in snapshots}
    add_after_breakdown_count = entry_sequence.get("add_after_breakdown_count") or 0
    average_down_count = entry_sequence.get("average_down_count") or 0
    partial_exit_count = exit_sequence.get("partial_exit_count") or 0
    profit_protected = _number(exit_sequence.get("profit_protected_by_partial_exits")) or 0.0
    sold_before_peak_pct = _number(exit_sequence.get("percentage_sold_before_peak"))
    sold_after_breakdown_pct = _number(exit_sequence.get("percentage_sold_after_breakdown"))
    profit_giveback_pct = _number(lifecycle_metrics.get("profit_giveback_pct"))
    final_exit_return_pct = _number(exit_sequence.get("final_exit_return_pct"))
    realized_pnl = _number(lifecycle_metrics.get("total_realized_pnl"))
    plan_adherence_score = _number(advanced_internal.get("plan_adherence_score"))
    decision_context_insufficient = decision_context.get("status") != "present"
    decision_context_backfilled = (
        decision_context.get("source") == "user_backfilled"
        or decision_context.get("created_after_entry") is True
    )
    planned_holding_period = decision_context.get("planned_holding_period")
    add_entry_condition = decision_context.get("add_entry_condition")
    default_stop_rule = decision_context.get("default_stop_rule")
    historical_judgment_eligible = decision_context.get("historical_judgment_eligible") is True
    total_holding_days = lifecycle_metrics.get("total_holding_days_from_first_entry")

    if not event_facts or entry_sequence.get("entry_count", 0) == 0:
        _append_label(labels, "insufficient_data")
        item = _text_item(
            "缺少進場事件事實，因此這次生命週期檢討只能標記為資料不足。",
            ["event_facts", "entry_sequence.entry_count"],
        )
        caveats.append(item)
        what_needs_review.append(item)

    if decision_context_insufficient:
        _append_label(labels, "insufficient_data")
        item = _text_item(
            "決策脈絡不足；與動機有關的出場判讀只能依照已記錄的原因代碼或計畫遵循欄位，不推論未記錄意圖。",
            ["decision_context.status", "data_quality.insufficient_data"],
        )
        caveats.append(item)
        data_quality_notes.append(item)

    if decision_context_backfilled:
        item = _text_item(
            "此操作計畫為使用者事後補填，可改善未來檢討脈絡，但不視為原始進場當下已存在的計畫。",
            ["decision_context.source", "decision_context.created_after_entry"],
        )
        caveats.append(item)
        data_quality_notes.append(item)

    evidence_gaps = [
        key
        for key in data_quality.get("insufficient_data", [])
        if key != "decision_context"
    ]
    if evidence_gaps:
        _append_label(labels, "insufficient_data")
        item = _text_item(
            "部分事件、ledger 或市場證據不足；請依資料品質欄位確認缺口，不把缺少的證據解讀為已記錄事實。",
            ["data_quality.insufficient_data"],
        )
        caveats.append(item)
        data_quality_notes.append(item)

    shared_context_note = _shared_context_data_quality_note(shared_context)
    if shared_context_note is not None:
        caveats.append(shared_context_note)
        data_quality_notes.append(shared_context_note)

    if average_down_count > 0 and add_after_breakdown_count > 0:
        _append_label(labels, "averaging_down_into_weakness")
        item = _text_item(
            "加碼序列出現攤平，而且加碼時價格低於 MA20 或處於下降趨勢，屬於弱勢中加碼。",
            ["entry_sequence.average_down_count", "entry_sequence.add_after_breakdown_count"],
        )
        reasons.append(item)
        what_needs_review.append(item)

    ma20_support_events = _ma20_pullback_support_events(event_facts, snapshot_by_key)
    if ma20_support_events:
        _append_label(labels, "ma20_pullback_supported")
        refs = _event_and_snapshot_refs(ma20_support_events)
        item = _text_item(
            "進場原因代碼記錄為拉回守住 MA20，且事件當下價格位於 MA20 附近或上方，形成可追溯的正向進場支撐。",
            refs + ["event_facts.reason_code", "event_indicator_snapshots.event_price_vs_ma20_pct"],
        )
        reasons.append(item)
        what_worked.append(item)

    add_entry_plan_violations = _add_entry_plan_violation_events(
        event_facts,
        snapshot_by_key,
        add_entry_condition if historical_judgment_eligible else None,
    )
    if add_entry_plan_violations:
        _append_label(labels, "add_entry_plan_violation")
        refs = _event_and_snapshot_refs(add_entry_plan_violations)
        item = _text_item(
            "加碼條件記錄為不攤平，但加碼事件價格低於前一次進場且事件當下低於 MA20，屬於固定加碼條件的違反。",
            refs + ["decision_context.add_entry_condition", "event_indicator_snapshots.event_price_vs_ma20_pct"],
        )
        reasons.append(item)
        what_needs_review.append(item)

    stop_rule_violations = _unacted_stop_rule_break_events(
        event_facts,
        snapshot_by_key,
        default_stop_rule if historical_judgment_eligible else None,
    )
    if stop_rule_violations:
        _append_label(labels, "unacted_stop_rule_break")
        refs = _event_and_snapshot_refs(stop_rule_violations)
        item = _text_item(
            "預設停損規則為跌破 MA20，出場事件當下已低於 MA20，但沒有記錄已執行停損原因或計畫遵循，需檢討是否延遲處理。",
            refs + ["decision_context.default_stop_rule", "event_facts.reason_code", "event_facts.plan_adherence"],
        )
        reasons.append(item)
        what_needs_review.append(item)

    holding_period_review = _holding_period_review(
        planned_holding_period if historical_judgment_eligible else None,
        total_holding_days,
    )
    if holding_period_review is not None:
        _append_label(labels, "holding_period_needs_review")
        item = _text_item(
            holding_period_review,
            ["decision_context.planned_holding_period", "lifecycle_metrics.total_holding_days_from_first_entry"],
        )
        reasons.append(item)
        what_needs_review.append(item)

    disciplined_refs = ["exit_sequence.partial_exit_count", "exit_sequence.profit_protected_by_partial_exits"]
    if partial_exit_count > 0 and profit_protected > 0:
        _append_label(labels, "disciplined_scale_out")
        item = _text_item(
            "完整出清前的部分出場已先鎖定已實現獲利，對整體部位有保護效果。",
            disciplined_refs,
        )
        reasons.append(item)
        what_worked.append(item)

    if sold_after_breakdown_pct is not None and sold_after_breakdown_pct > 0 and (final_exit_return_pct is None or final_exit_return_pct <= 0):
        _append_label(labels, "risk_reduction_exit")
        item = _text_item(
            "破位條件出現後，出場動作降低了剩餘曝險，屬於風險縮減。",
            ["exit_sequence.percentage_sold_after_breakdown", "exit_sequence.final_exit_return_pct"],
        )
        reasons.append(item)
        what_worked.append(item)

    recorded_premature_context_events = [
        event for event in event_facts
        if event.get("event_type") == "partial_exit"
        and (event.get("plan_adherence") == "no" or event.get("reason_code") == "emotional_exit")
    ]
    premature_events = [
        event for event in recorded_premature_context_events
        if sold_before_peak_pct is not None
        and sold_before_peak_pct >= 50
        and snapshot_by_key.get(event["event_key"], {}).get("market_regime") in {"uptrend", "strong_momentum"}
    ]
    if premature_events:
        _append_label(labels, "premature_scale_out")
        refs = [_event_fact_ref(event) for event in premature_events]
        item = _text_item(
            "部分出場有未遵循計畫或情緒性出場紀錄，且發生在上升趨勢或強動能快照中，後續又出現更高價格，因此標記為可能過早減碼。",
            refs + ["exit_sequence.percentage_sold_before_peak", "event_indicator_snapshots.market_regime"],
        )
        reasons.append(item)
        what_needs_review.append(item)
    elif recorded_premature_context_events:
        _append_label(labels, "insufficient_data")
        item = _text_item(
            "雖然有未遵循計畫或情緒性出場紀錄，但缺少動能快照與賣在後續高點前的完整證據，因此不直接判定為過早減碼。",
            [_event_fact_ref(event) for event in recorded_premature_context_events] + ["exit_sequence.percentage_sold_before_peak", "event_indicator_snapshots.market_regime"],
        )
        caveats.append(item)
        data_quality_notes.append(item)
    elif partial_exit_count > 0 and decision_context_insufficient:
        item = _text_item(
            "部分出場不會被直接判定為過早，因為沒有已記錄的未遵循計畫或情緒性出場脈絡。",
            ["decision_context.status", "event_facts.plan_adherence", "event_facts.reason_code"],
        )
        caveats.append(item)
        data_quality_notes.append(item)

    if (
        (sold_after_breakdown_pct is not None and sold_after_breakdown_pct >= 50)
        or (profit_giveback_pct is not None and profit_giveback_pct >= 25)
    ) and (final_exit_return_pct is None or final_exit_return_pct <= 0):
        _append_label(labels, "late_scale_out")
        item = _text_item(
            "較大比例的出場發生在轉弱或明顯獲利回吐之後，且最終出場報酬未轉正，出場節奏偏晚。",
            ["exit_sequence.percentage_sold_after_breakdown", "lifecycle_metrics.profit_giveback_pct", "exit_sequence.final_exit_return_pct"],
        )
        reasons.append(item)
        what_needs_review.append(item)

    coherent = (
        historical_judgment_eligible
        and plan_adherence_score is not None
        and plan_adherence_score >= 75
        and realized_pnl is not None
        and realized_pnl >= 0
        and "averaging_down_into_weakness" not in labels
        and "premature_scale_out" not in labels
        and "late_scale_out" not in labels
    )
    if coherent:
        _append_label(labels, "coherent_position_management")
        item = _text_item(
            "部位處理大致符合已記錄的計畫遵循狀態，且最終已實現損益為非負，整體管理一致。",
            ["advanced_internal.plan_adherence_score", "lifecycle_metrics.total_realized_pnl", "decision_context.status"],
        )
        reasons.append(item)
        what_worked.append(item)

    for event in event_facts:
        if event.get("event_type") not in ENTRY_TYPES | EXIT_TYPES:
            continue
        event_level_evidence.append(_event_evidence_item(event, snapshot_by_key.get(event["event_key"])))

    if not what_worked:
        what_worked.append(_text_item(
            "目前可用的生命週期指標沒有辨識出明確的正向部位管理模式。",
            ["entry_sequence", "exit_sequence", "lifecycle_metrics"],
        ))
    if not what_needs_review:
        what_needs_review.append(_text_item(
            "目前固定規則沒有辨識出重大部位管理警訊；這不代表已證明操作正確。",
            ["entry_sequence", "exit_sequence", "advanced_internal"],
        ))
    if not data_quality_notes:
        data_quality_notes.append(_text_item(
            "資料品質沒有額外增加生命週期檢討限制；本次判讀以目前可用的生命週期指標為準。",
            ["data_quality.status"],
        ))

    primary_label = _primary_lifecycle_label(labels)
    next_operation_rules.extend(_next_operation_rules(labels, decision_context_insufficient))
    tier = _lifecycle_tier(primary_label, labels)
    source_refs = _unique_refs([ref for item in reasons + caveats for ref in item["source_refs"]])
    if not source_refs:
        source_refs = ["entry_sequence", "exit_sequence", "decision_context"]
    if reasons:
        classification_reasons = reasons
    elif primary_label == "unclassified":
        classification_reasons = [_text_item(
            "目前固定生命週期規則未命中可辨識的正向或需檢討模式，因此保留為暫無適用分類。",
            source_refs,
        )]
    else:
        classification_reasons = [_text_item(
            "除了目前選定的主要分類外，沒有其他固定生命週期分類規則被觸發。",
            source_refs,
        )]
    overall_conclusion_text = (
        "本次生命週期資料足以完成檢討，但目前固定規則未命中明確的正向或需檢討模式。"
        if primary_label == "unclassified"
        else f"本次生命週期檢討層級為{_lifecycle_tier_text(tier)}；主要分類為{_lifecycle_label_text(primary_label)}。"
    )
    review_framework = _build_review_framework(
        labels=labels,
        lifecycle_metrics=lifecycle_metrics,
        next_operation_rules=next_operation_rules,
        source_refs=source_refs,
        decision_context_insufficient=decision_context_insufficient,
        evidence_gaps=evidence_gaps,
    )

    return {
        **review_framework,
        "classification": {
            "primary_label": primary_label,
            "labels": labels or [primary_label],
            "tier": tier,
            "reasons": classification_reasons,
            "caveats": caveats,
            "source_refs": source_refs,
        },
        "overall_conclusion": _text_item(
            overall_conclusion_text,
            source_refs,
        ),
        "what_worked": what_worked,
        "what_needs_review": what_needs_review,
        "event_level_evidence": event_level_evidence,
        "next_operation_rules": next_operation_rules,
        "data_quality_notes": data_quality_notes,
    }


def _append_label(labels: list[str], label: str) -> None:
    if label not in labels:
        labels.append(label)


def _primary_lifecycle_label(labels: list[str]) -> str:
    for label in (
        "premature_scale_out",
        "unacted_stop_rule_break",
        "add_entry_plan_violation",
        "late_scale_out",
        "averaging_down_into_weakness",
        "holding_period_needs_review",
        "insufficient_data",
        "ma20_pullback_supported",
        "risk_reduction_exit",
        "disciplined_scale_out",
        "coherent_position_management",
    ):
        if label in labels:
            return label
    return "unclassified"


def _lifecycle_tier(primary_label: str, labels: list[str]) -> str:
    if primary_label in {
        "premature_scale_out",
        "unacted_stop_rule_break",
        "add_entry_plan_violation",
        "late_scale_out",
        "averaging_down_into_weakness",
        "holding_period_needs_review",
    }:
        return "needs_review"
    if primary_label == "insufficient_data" or "insufficient_data" in labels:
        return "insufficient_context"
    if primary_label in {
        "ma20_pullback_supported",
        "risk_reduction_exit",
        "disciplined_scale_out",
        "coherent_position_management",
    }:
        return "constructive"
    return "mixed"


def _lifecycle_label_text(label: str) -> str:
    return {
        "insufficient_data": "資料不足",
        "unclassified": "暫無適用分類",
        "averaging_down_into_weakness": "弱勢中新增批次",
        "add_entry_plan_violation": "新增批次計畫偏離",
        "ma20_pullback_supported": "拉回守住 MA20 支撐",
        "unacted_stop_rule_break": "風險控制規則未明確執行",
        "holding_period_needs_review": "持有週期需檢討",
        "disciplined_scale_out": "分批降低曝險保護獲利",
        "risk_reduction_exit": "破位後降低風險",
        "premature_scale_out": "可能過早降低曝險",
        "late_scale_out": "風險處理偏晚",
        "coherent_position_management": "部位管理一致",
    }.get(label, label)


def _lifecycle_tier_text(tier: str) -> str:
    return {
        "needs_review": "需檢討",
        "insufficient_context": "脈絡不足",
        "constructive": "具建設性",
        "mixed": "混合結論",
    }.get(tier, tier)


_PROCESS_STRENGTH_LABELS = {
    "ma20_pullback_supported",
    "disciplined_scale_out",
    "risk_reduction_exit",
    "coherent_position_management",
}
_PROCESS_RISK_LABELS = {
    "averaging_down_into_weakness",
    "add_entry_plan_violation",
    "unacted_stop_rule_break",
    "holding_period_needs_review",
    "premature_scale_out",
    "late_scale_out",
}


def _build_review_framework(
    *,
    labels: list[str],
    lifecycle_metrics: dict[str, Any],
    next_operation_rules: list[dict[str, Any]],
    source_refs: list[str],
    decision_context_insufficient: bool,
    evidence_gaps: list[str],
) -> dict[str, Any]:
    realized_pnl = _number(lifecycle_metrics.get("total_realized_pnl"))
    realized_return_pct = _number(lifecycle_metrics.get("total_return_pct_on_weighted_cost"))
    if realized_pnl is None:
        outcome_status = "insufficient"
        outcome_label = "損益資料不足"
        outcome_summary = "目前沒有足夠的已實現損益資料可判斷結果。"
    elif realized_pnl > 0:
        outcome_status = "profit"
        outcome_label = "結果獲利"
        outcome_summary = "這筆完整交易最終為獲利；獲利結果不等同於操作流程必然正確。"
    elif realized_pnl < 0:
        outcome_status = "loss"
        outcome_label = "結果虧損"
        outcome_summary = "這筆完整交易最終為虧損；虧損結果不等同於每個操作決策都錯誤。"
    else:
        outcome_status = "breakeven"
        outcome_label = "結果持平"
        outcome_summary = "這筆完整交易最終接近持平，仍需獨立檢查操作流程。"

    strength_labels = [label for label in labels if label in _PROCESS_STRENGTH_LABELS]
    risk_labels = [label for label in labels if label in _PROCESS_RISK_LABELS]
    has_evidence_gap = "insufficient_data" in labels
    if strength_labels and risk_labels:
        process_status = "mixed"
        process_label = "流程有好有壞"
        process_summary = "同一筆交易同時有可保留的做法與需要修正的決策，不能只用損益下結論。"
    elif risk_labels:
        process_status = "needs_review"
        process_label = "流程需要改善"
        process_summary = "已辨識出具體的執行風險，應先修正命中的行為再評估下次操作。"
    elif strength_labels and has_evidence_gap:
        process_status = "mixed"
        process_label = "有可取處但證據不完整"
        process_summary = "已有可追溯的正向做法，但資料缺口仍限制整體流程判斷。"
    elif strength_labels:
        process_status = "disciplined"
        process_label = "流程大致有紀律"
        process_summary = "已辨識出可重複的正向操作模式，仍應持續保留觸發條件與執行紀錄。"
    else:
        process_status = "insufficient"
        process_label = "流程暫無足夠判斷"
        process_summary = (
            "目前資料有缺口，先補齊紀錄再判斷操作流程。"
            if has_evidence_gap
            else "現有固定規則未命中可驗證模式，暫不把這筆交易判定為做對或做錯。"
        )

    dimensions = {
        "entry": _review_dimension(
            label="進場品質",
            labels=labels,
            strength_labels={"ma20_pullback_supported"},
            risk_labels={"averaging_down_into_weakness", "add_entry_plan_violation"},
            strength_summary="進場理由與事件當下證據可互相核對。",
            risk_summary="進場或新增批次行為命中需要修正的規則。",
            source_refs=["entry_sequence", "event_facts", "event_indicator_snapshots"],
            has_evidence_gap=has_evidence_gap,
        ),
        "position_management": _review_dimension(
            label="部位管理",
            labels=labels,
            strength_labels={"disciplined_scale_out", "coherent_position_management"},
            risk_labels={"holding_period_needs_review"},
            strength_summary="部位調整呈現可追溯的紀律或一致性。",
            risk_summary="持有週期或部位調整需要重新核對原計畫。",
            source_refs=["entry_sequence", "exit_sequence", "decision_context"],
            has_evidence_gap=has_evidence_gap,
        ),
        "risk_exit": _review_dimension(
            label="風險與出場",
            labels=labels,
            strength_labels={"disciplined_scale_out", "risk_reduction_exit"},
            risk_labels={"unacted_stop_rule_break", "premature_scale_out", "late_scale_out"},
            strength_summary="降低曝險或風險處理有明確可追溯的效果。",
            risk_summary="出場時機或風險規則執行命中需要改善的條件。",
            source_refs=["exit_sequence", "event_facts", "event_indicator_snapshots"],
            has_evidence_gap=has_evidence_gap,
        ),
        "record_quality": {
            "label": "紀錄品質",
            "status": "insufficient" if has_evidence_gap else "sufficient",
            "summary": (
                "決策脈絡、事件帳本或市場證據仍有缺口。"
                if has_evidence_gap
                else "目前紀錄足以支持這次規則化判讀。"
            ),
            "source_refs": ["data_quality", "decision_context"],
        },
    }

    keep = _structured_feedback_for_labels(strength_labels, kind="keep")
    improve = _structured_feedback_for_labels(risk_labels, kind="improve")
    next_actions = [
        {
            "title": "下次操作規則",
            "action": item["text"],
            "source_refs": item["source_refs"],
        }
        for item in next_operation_rules
        if not (
            decision_context_insufficient
            and "decision_context.status" in item["source_refs"]
        )
        and not (
            evidence_gaps
            and "data_quality.insufficient_data" in item["source_refs"]
        )
    ]
    if decision_context_insufficient:
        next_actions.insert(0, {
            "title": "先補操作計畫",
            "action": "下次進場前先記錄持有週期、新增批次條件與風險控制規則，讓結案時能核對原始計畫。",
            "source_refs": ["decision_context.status"],
        })
    if evidence_gaps:
        next_actions.insert(0, {
            "title": "先補齊證據",
            "action": "依資料品質提示補齊事件帳本與事件當下資料，再進行交易品質判斷。",
            "source_refs": ["data_quality.insufficient_data"],
        })

    return {
        "outcome": {
            "status": outcome_status,
            "label": outcome_label,
            "summary": outcome_summary,
            "total_realized_pnl": realized_pnl,
            "total_return_pct": realized_return_pct,
            "source_refs": [
                "lifecycle_metrics.total_realized_pnl",
                "lifecycle_metrics.total_return_pct_on_weighted_cost",
            ],
        },
        "process_quality": {
            "status": process_status,
            "label": process_label,
            "summary": process_summary,
            "strength_labels": strength_labels,
            "risk_labels": risk_labels,
            "source_refs": source_refs,
        },
        "dimensions": dimensions,
        "feedback": {
            "keep": keep,
            "improve": improve,
            "next_actions": _deduplicate_feedback(next_actions),
        },
    }


def _review_dimension(
    *,
    label: str,
    labels: list[str],
    strength_labels: set[str],
    risk_labels: set[str],
    strength_summary: str,
    risk_summary: str,
    source_refs: list[str],
    has_evidence_gap: bool,
) -> dict[str, Any]:
    strengths = strength_labels.intersection(labels)
    risks = risk_labels.intersection(labels)
    if strengths and risks:
        status = "mixed"
        summary = f"{strength_summary}{risk_summary}"
    elif risks:
        status = "needs_review"
        summary = risk_summary
    elif strengths:
        status = "strength"
        summary = strength_summary
    elif has_evidence_gap:
        status = "insufficient"
        summary = "目前證據不足，暫不判定此面向做得好或不好。"
    else:
        status = "not_observed"
        summary = "目前固定規則未命中此面向的明確模式。"
    return {
        "label": label,
        "status": status,
        "summary": summary,
        "source_refs": source_refs,
    }


def _structured_feedback_for_labels(
    labels: list[str],
    *,
    kind: Literal["keep", "improve"],
) -> list[dict[str, Any]]:
    feedback_by_label = {
        "ma20_pullback_supported": (
            "保留可核對的進場理由",
            "進場理由與 MA20 事件快照相互支持。",
            "下次延續在進場當下記錄理由與支撐依據。",
            ["event_facts.reason_code", "event_indicator_snapshots.event_price_vs_ma20_pct"],
        ),
        "disciplined_scale_out": (
            "保留分批保護獲利",
            "部分結案先鎖定獲利，降低了完整部位的回吐風險。",
            "下次沿用事前定義的分批條件，並記錄每次降低曝險的原因。",
            ["exit_sequence.partial_exit_count", "exit_sequence.profit_protected_by_partial_exits"],
        ),
        "risk_reduction_exit": (
            "保留破位後降低曝險",
            "轉弱後的結案動作確實降低剩餘曝險。",
            "下次繼續把破位條件寫成可直接執行的風險動作。",
            ["exit_sequence.percentage_sold_after_breakdown", "exit_sequence.final_exit_return_pct"],
        ),
        "coherent_position_management": (
            "保留一致的部位管理",
            "部位調整與已記錄計畫大致一致。",
            "下次繼續在每個事件記錄是否符合原計畫。",
            ["decision_context", "event_facts.plan_adherence"],
        ),
        "averaging_down_into_weakness": (
            "停止在弱勢中攤平",
            "新增批次同時出現成本下修與弱勢證據。",
            "下次只有在已記錄的轉強條件成立後才新增批次。",
            ["entry_sequence.average_down_count", "entry_sequence.add_after_breakdown_count"],
        ),
        "add_entry_plan_violation": (
            "遵守新增批次條件",
            "實際新增批次與原先不攤平的條件衝突。",
            "新增批次前逐項核對價格位置與原計畫，不符合就不執行。",
            ["decision_context.add_entry_condition", "event_facts", "event_indicator_snapshots"],
        ),
        "unacted_stop_rule_break": (
            "讓風險規則可執行",
            "風險條件觸發後，紀錄中沒有可核對的對應動作。",
            "下次把風險規則寫成觸發條件、動作與紀錄欄位三件事。",
            ["decision_context.default_stop_rule", "event_facts.reason_code", "event_facts.plan_adherence"],
        ),
        "holding_period_needs_review": (
            "校準持有週期",
            "實際持有時間與原計畫的時間框架差距較大。",
            "若策略中途改變，當下更新計畫與下一次檢查日期。",
            ["decision_context.planned_holding_period", "lifecycle_metrics.total_holding_days_from_first_entry"],
        ),
        "premature_scale_out": (
            "避免無依據地過早降低曝險",
            "降低曝險時有偏離計畫或情緒性紀錄，且後續價格仍走高。",
            "下次先確認原計畫的降低曝險條件成立，再執行部分結案。",
            ["event_facts.plan_adherence", "event_facts.reason_code", "exit_sequence.percentage_sold_before_peak"],
        ),
        "late_scale_out": (
            "提前定義風險出口",
            "較大比例的結案發生在轉弱或明顯回吐之後。",
            "下次在進場前定義回吐或破位的降低曝險條件，觸發時直接執行。",
            ["lifecycle_metrics.profit_giveback_pct", "exit_sequence.percentage_sold_after_breakdown"],
        ),
    }
    allowed_labels = _PROCESS_STRENGTH_LABELS if kind == "keep" else _PROCESS_RISK_LABELS
    items = []
    for label in labels:
        if label not in allowed_labels:
            continue
        title, observation, action, refs = feedback_by_label[label]
        items.append({
            "label": label,
            "title": title,
            "observation": _risk_language_text(observation),
            "action": _risk_language_text(action),
            "source_refs": refs,
        })
    return items


def _deduplicate_feedback(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated = []
    seen_actions = set()
    for item in items:
        action = item.get("action")
        if action in seen_actions:
            continue
        seen_actions.add(action)
        deduplicated.append(item)
    return deduplicated


def _event_evidence_item(event: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    refs = [_event_fact_ref(event)]
    snapshot_text = "沒有可用的事件當下指標快照"
    if snapshot is not None:
        refs.append(f"event_indicator_snapshots.{event['event_key']}")
        snapshot_text = f"事件當下市場狀態為 {snapshot.get('market_regime')}，價格相對 MA20 為 {snapshot.get('event_price_vs_ma20_pct')}%"
    return _text_item(
        f"{event.get('event_date')} 發生 {event.get('event_type')}，價格 {event.get('price')}、數量 {event.get('quantity')}；{snapshot_text}。",
        refs,
    )


def _next_operation_rules(
    labels: list[str],
    decision_context_insufficient: bool,
) -> list[dict[str, Any]]:
    primary_label = _primary_lifecycle_label(labels)
    rules: list[dict[str, Any]] = []
    if "add_entry_plan_violation" in labels:
        rules.append(_text_item(
            "若計畫寫明不攤平，未來加碼前必須先確認價格沒有低於前次進場且仍守在 MA20 附近或上方。",
            ["decision_context.add_entry_condition", "event_indicator_snapshots.event_price_vs_ma20_pct"],
        ))
    if "averaging_down_into_weakness" in labels:
        rules.append(_text_item(
            "下次若要加碼虧損部位，應先記錄明確的轉強觸發條件，避免在弱勢中單純攤平。",
            ["entry_sequence.average_down_count", "entry_sequence.add_after_breakdown_count"],
        ))
    if "unacted_stop_rule_break" in labels:
        rules.append(_text_item(
            "若預設停損是跌破 MA20，出場或未出場的當下紀錄應明確標示停損原因與是否符合計畫。",
            ["decision_context.default_stop_rule", "event_facts.reason_code", "event_facts.plan_adherence"],
        ))
    if "holding_period_needs_review" in labels:
        rules.append(_text_item(
            "下次建立計畫時，將預期持有週期與檢查節奏一起記錄，避免事後把短線、中線或長期計畫混用。",
            ["decision_context.planned_holding_period", "lifecycle_metrics.total_holding_days_from_first_entry"],
        ))
    if "late_scale_out" in labels:
        rules.append(_text_item(
            "在明顯獲利回吐或破位曝險累積前，先定義減碼或出場觸發條件。",
            ["lifecycle_metrics.profit_giveback_pct", "exit_sequence.percentage_sold_after_breakdown"],
        ))
    if "premature_scale_out" in labels:
        rules.append(_text_item(
            "未來部分出場前，先記錄這次出場是否符合原計畫與具體原因。",
            ["event_facts.plan_adherence", "event_facts.reason_code"],
        ))
    if decision_context_insufficient:
        rules.append(_text_item(
            "下次操作前先記錄計畫脈絡，避免後續檢討只能依賴缺漏資料。",
            ["decision_context.status"],
        ))
    if not rules:
        constructive_source_refs = {
            "ma20_pullback_supported": [
                "event_facts.reason_code",
                "event_indicator_snapshots.event_price_vs_ma20_pct",
            ],
            "disciplined_scale_out": [
                "exit_sequence.partial_exit_count",
                "exit_sequence.profit_protected_by_partial_exits",
            ],
            "risk_reduction_exit": [
                "exit_sequence.percentage_sold_after_breakdown",
                "exit_sequence.final_exit_return_pct",
            ],
            "coherent_position_management": [
                "advanced_internal.plan_adherence_score",
                "lifecycle_metrics.total_realized_pnl",
            ],
        }.get(primary_label)
        if constructive_source_refs is not None:
            rules.append(_text_item(
                "本次已辨識出可追溯的正向部位管理模式；下次可繼續保留相同類型的觸發條件與執行紀錄，供後續檢討核對。",
                constructive_source_refs,
            ))
        elif primary_label == "unclassified":
            rules.append(_text_item(
                "未命中既定模式時，不額外推定做對或做錯；下次仍應記錄可核對的部位調整與最終出場觸發條件。",
                ["entry_sequence", "exit_sequence", "decision_context"],
            ))
        elif primary_label == "insufficient_data":
            rules.append(_text_item(
                "本次仍有事件、ledger 或市場證據缺口；下次先補齊資料品質提示中的缺失，再判讀部位管理模式。",
                ["data_quality.insufficient_data"],
            ))
        else:
            rules.append(_text_item(
                "下次仍應記錄可核對的部位調整與最終出場觸發條件，供後續檢討核對。",
                ["entry_sequence", "exit_sequence", "decision_context"],
            ))
    return rules


def _event_fact_ref(event: dict[str, Any]) -> str:
    return f"event_facts.{event.get('event_key')}"


def _event_and_snapshot_refs(events: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    for event in events:
        refs.append(_event_fact_ref(event))
        refs.append(f"event_indicator_snapshots.{event['event_key']}")
    return _unique_refs(refs)


def _ma20_pullback_support_events(
    event_facts: list[dict[str, Any]],
    snapshot_by_key: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    supported = []
    for event in event_facts:
        if event.get("event_type") not in ENTRY_TYPES:
            continue
        if event.get("reason_code") != "pullback_held_ma20":
            continue
        vs_ma20 = _number(snapshot_by_key.get(event["event_key"], {}).get("event_price_vs_ma20_pct"))
        if vs_ma20 is not None and vs_ma20 >= -2.0:
            supported.append(event)
    return supported


def _add_entry_plan_violation_events(
    event_facts: list[dict[str, Any]],
    snapshot_by_key: dict[str, dict[str, Any]],
    add_entry_condition: Any,
) -> list[dict[str, Any]]:
    if add_entry_condition != "no_averaging_down":
        return []
    previous_entry_price = None
    violations = []
    for event in event_facts:
        if event.get("event_type") not in ENTRY_TYPES:
            continue
        price = _number(event.get("price"))
        if event.get("event_type") == "add_entry" and price is not None and previous_entry_price is not None:
            vs_ma20 = _number(snapshot_by_key.get(event["event_key"], {}).get("event_price_vs_ma20_pct"))
            if price < previous_entry_price and _is_negative(vs_ma20):
                violations.append(event)
        if price is not None:
            previous_entry_price = price
    return violations


def _unacted_stop_rule_break_events(
    event_facts: list[dict[str, Any]],
    snapshot_by_key: dict[str, dict[str, Any]],
    default_stop_rule: Any,
) -> list[dict[str, Any]]:
    if default_stop_rule != "break_ma20":
        return []
    acted_stop_reasons = {"ma20_lost", "stop_loss", "support_broken", "risk_reduction"}
    violations = []
    for event in event_facts:
        if event.get("event_type") not in EXIT_TYPES:
            continue
        vs_ma20 = _number(snapshot_by_key.get(event["event_key"], {}).get("event_price_vs_ma20_pct"))
        if not _is_negative(vs_ma20):
            continue
        if event.get("reason_code") in acted_stop_reasons:
            continue
        if event.get("plan_adherence") in {"yes", "partial"}:
            continue
        violations.append(event)
    return violations


def _holding_period_review(planned_holding_period: Any, total_holding_days: Any) -> str | None:
    days = _number(total_holding_days)
    if planned_holding_period in {None, "not_recorded"} or days is None:
        return None
    if planned_holding_period == "short_term" and days > 30:
        return "計畫持有週期為短線，但實際持有天數明顯超過短線範圍；這不是硬性錯誤，但需要檢討是否有更新計畫。"
    if planned_holding_period == "swing" and (days < 5 or days > 90):
        return "計畫持有週期為波段，但實際持有天數落在波段常見範圍外；需檢討出場節奏是否符合原先檢查週期。"
    if planned_holding_period == "medium_term" and (days < 14 or days > 150):
        return "計畫持有週期為中線，但實際持有天數與中線計畫差距較大；需檢討是否有紀錄策略轉換。"
    if planned_holding_period == "long_term" and days < 30:
        return "計畫持有週期為長期，但實際持有時間偏短；這只能標示為需檢討，不能直接判定出場錯誤。"
    return None


def _text_item(text: str, source_refs: list[str]) -> dict[str, Any]:
    return {"text": _risk_language_text(text), "source_refs": _unique_refs(source_refs)}


def _risk_language_text(text: str) -> str:
    replacements = {
        "加碼條件": "新增批次條件",
        "加碼事件": "新增批次事件",
        "弱勢中加碼": "弱勢中新增批次",
        "未來加碼前": "未來新增批次前",
        "若要加碼": "若要新增批次",
        "加碼": "新增批次",
        "預設停損規則": "預設風險控制規則",
        "停損原因": "風險控制原因",
        "停損": "風險控制",
        "部分出場": "部分結案",
        "最終出場": "最終結案",
        "完整出清": "完整結案",
        "出場事件": "結案事件",
        "出場動作": "結案動作",
        "出場規則": "結案規則",
        "出場節奏": "結案節奏",
        "未出場": "未結案",
        "出場": "結案",
        "賣在後續高點前": "降低曝險發生在後續高點前",
        "減碼": "降低曝險",
    }
    rewritten = text
    for source, target in replacements.items():
        rewritten = rewritten.replace(source, target)
    return rewritten


def _unique_refs(source_refs: list[str]) -> list[str]:
    refs: list[str] = []
    for source_ref in source_refs:
        if source_ref and source_ref not in refs:
            refs.append(source_ref)
    return refs or ["lifecycle_metrics"]


def _build_accounting_timeline(events: list[Any], data_quality: dict[str, Any]) -> dict[str, Any]:
    position_size = 0.0
    cost_basis = 0.0
    total_entry_cost = 0.0
    total_entry_quantity = 0.0
    realized_pnl = 0.0
    realized_cost_basis = 0.0
    max_position_size = 0.0
    max_capital_at_risk = 0.0
    average_entry_price_over_time: list[dict[str, Any]] = []
    capital_at_risk_by_event: list[dict[str, Any]] = []
    exposure_curve: list[dict[str, Any]] = []
    event_metrics: dict[str, dict[str, Any]] = {}

    for index, event in enumerate(events):
        event_type = _event_value(event, "event_type")
        price = _number(_event_value(event, "price")) or 0.0
        quantity = _number(_event_value(event, "quantity")) or 0.0
        fees = _ledger_amount(event, "fees", index, data_quality)
        taxes = _ledger_amount(event, "taxes", index, data_quality)
        before_average_cost = cost_basis / position_size if position_size > 0 else None
        realized_for_event = 0.0
        sold_cost = 0.0

        if event_type in ENTRY_TYPES:
            event_cost = price * quantity + fees + taxes
            position_size += quantity
            cost_basis += event_cost
            total_entry_cost += event_cost
            total_entry_quantity += quantity
            if position_size > 0:
                average_entry_price_over_time.append({
                    "event_id": _event_value(event, "id"),
                    "date": _date_to_iso(_event_value(event, "event_date")),
                    "position_size": _round_quantity(position_size),
                    "average_entry_price": _round_price(cost_basis / position_size),
                })
        elif event_type in EXIT_TYPES:
            exit_quantity = min(quantity, position_size)
            if quantity > position_size:
                _add_note(
                    data_quality,
                    "exit_quantity_exceeds_position",
                    "An exit event quantity exceeded the tracked open position size; realized accounting used available quantity only.",
                )
            if exit_quantity > 0 and before_average_cost is not None:
                sold_cost = before_average_cost * exit_quantity
                proceeds = price * exit_quantity - fees - taxes
                realized_for_event = proceeds - sold_cost
                realized_pnl += realized_for_event
                realized_cost_basis += sold_cost
                cost_basis -= sold_cost
                position_size -= exit_quantity
                if position_size <= 1e-9:
                    position_size = 0.0
                    cost_basis = 0.0
        elif event_type == "manual_adjustment":
            _add_note(
                data_quality,
                "manual_adjustment_not_accounted",
                "manual_adjustment rows were included as event facts but excluded from cost-basis accounting.",
            )

        max_position_size = max(max_position_size, position_size)
        max_capital_at_risk = max(max_capital_at_risk, cost_basis)
        event_key = _event_key(event, index)
        event_metrics[event_key] = {
            "position_size_after": _round_quantity(position_size),
            "cost_basis_after": _round_money(cost_basis),
            "average_cost_before": _round_price(before_average_cost),
            "realized_pnl": _round_money(realized_for_event),
            "sold_cost_basis": _round_money(sold_cost),
        }
        compact_point = {
            "event_id": _event_value(event, "id"),
            "date": _date_to_iso(_event_value(event, "event_date")),
            "event_type": event_type,
            "position_size": _round_quantity(position_size),
            "capital_at_risk": _round_money(cost_basis),
        }
        capital_at_risk_by_event.append(compact_point)
        exposure_curve.append({
            **compact_point,
            "cost_basis": _round_money(cost_basis),
        })

    return {
        "event_metrics": event_metrics,
        "total_realized_pnl": _round_money(realized_pnl),
        "realized_cost_basis": _round_money(realized_cost_basis),
        "total_return_pct_on_weighted_cost": _round_pct(_pct_from_ratio(_safe_div(realized_pnl, realized_cost_basis))),
        "max_position_size": _round_quantity(max_position_size),
        "max_capital_at_risk": _round_money(max_capital_at_risk),
        "average_entry_price_over_time": average_entry_price_over_time,
        "weighted_average_entry_price": _round_price(_safe_div(total_entry_cost, total_entry_quantity)),
        "total_entry_cost": _round_money(total_entry_cost),
        "total_entry_quantity": _round_quantity(total_entry_quantity),
        "capital_at_risk_by_event": capital_at_risk_by_event,
        "exposure_curve": exposure_curve,
    }


def _build_lifecycle_metrics(
    events: list[Any],
    rows: list[Any],
    accounting: dict[str, Any],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    entry_events = [event for event in events if _event_value(event, "event_type") in ENTRY_TYPES]
    exit_events = [event for event in events if _event_value(event, "event_type") in EXIT_TYPES]
    first_entry_date = _event_value(entry_events[0], "event_date") if entry_events else None
    final_exit_event = next((event for event in reversed(exit_events) if _event_value(event, "event_type") == "full_exit"), None)
    final_exit_date = _event_value(final_exit_event, "event_date") if final_exit_event is not None else None
    analysis_end = final_exit_date or _analysis_end_date(events)
    path_points = _market_points(
        rows,
        first_entry_date,
        analysis_end,
        end_inclusive=final_exit_date is None,
    )
    weighted_entry = _number(accounting.get("weighted_average_entry_price"))

    max_profit_pct = None
    max_drawdown_pct = None
    profit_giveback_pct = None
    if not path_points:
        _add_note(data_quality, "holding_path_prices", "No market close rows were available during the lifecycle exposure window.")
    elif weighted_entry is None or weighted_entry == 0:
        _add_note(data_quality, "weighted_entry_price", "Weighted entry price was unavailable for path metrics.")
    else:
        closes = [point["close"] for point in path_points]
        max_profit_pct = (max(closes) - weighted_entry) / weighted_entry * 100
        max_drawdown_pct = (min(closes) - weighted_entry) / weighted_entry * 100
        final_price = _number(_event_value(final_exit_event, "price")) if final_exit_event is not None else closes[-1]
        if final_price is not None:
            profit_giveback_pct = max(0.0, max_profit_pct - (final_price - weighted_entry) / weighted_entry * 100)

    return {
        "total_realized_pnl": accounting["total_realized_pnl"],
        "total_return_pct_on_weighted_cost": accounting["total_return_pct_on_weighted_cost"],
        "max_position_size": accounting["max_position_size"],
        "max_capital_at_risk": accounting["max_capital_at_risk"],
        "average_entry_price_over_time": accounting["average_entry_price_over_time"],
        "weighted_average_entry_price": accounting["weighted_average_entry_price"],
        "final_exit_date": _date_to_iso(final_exit_date),
        "total_holding_days_from_first_entry": _days_between(first_entry_date, final_exit_date),
        "active_exposure_days": _active_exposure_days(events, analysis_end),
        "max_unrealized_profit_pct": _round_pct(max_profit_pct),
        "max_unrealized_drawdown_pct": _round_pct(max_drawdown_pct),
        "profit_giveback_pct": _round_pct(profit_giveback_pct),
    }


def _build_entry_sequence(
    events: list[Any],
    accounting: dict[str, Any],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    entries = [event for event in events if _event_value(event, "event_type") in ENTRY_TYPES]
    add_entries = [event for event in entries if _event_value(event, "event_type") == "add_entry"]
    snapshot_by_key = {snapshot["event_key"]: snapshot for snapshot in snapshots}
    event_metrics = accounting["event_metrics"]
    average_up_count = 0
    average_down_count = 0
    add_after_breakdown_count = 0
    add_after_confirmation_count = 0
    add_vs_ma20: list[float | None] = []

    for index, event in enumerate(events):
        if _event_value(event, "event_type") != "add_entry":
            continue
        key = _event_key(event, index)
        price = _number(_event_value(event, "price"))
        average_before = _number(event_metrics.get(key, {}).get("average_cost_before"))
        if price is not None and average_before is not None:
            if price > average_before:
                average_up_count += 1
            elif price < average_before:
                average_down_count += 1
        snapshot = snapshot_by_key.get(key, {})
        vs_ma20 = snapshot.get("event_price_vs_ma20_pct")
        add_vs_ma20.append(vs_ma20)
        if _is_negative(vs_ma20) or snapshot.get("market_regime") == "downtrend":
            add_after_breakdown_count += 1
        if _is_positive(vs_ma20) and snapshot.get("market_regime") in {"uptrend", "strong_momentum"}:
            add_after_confirmation_count += 1

    entry_dates = [_event_value(event, "event_date") for event in entries]
    entry_prices = [_number(_event_value(event, "price")) for event in entries]
    initial_key = _event_key(entries[0], events.index(entries[0])) if entries else None
    initial_snapshot = snapshot_by_key.get(initial_key, {}) if initial_key is not None else {}
    return {
        "entry_count": len(entries),
        "add_entry_count": len(add_entries),
        "initial_entry_vs_ma20_pct": initial_snapshot.get("event_price_vs_ma20_pct"),
        "each_add_entry_vs_ma20_pct": add_vs_ma20,
        "average_up_count": average_up_count,
        "average_down_count": average_down_count,
        "add_after_breakdown_count": add_after_breakdown_count,
        "add_after_confirmation_count": add_after_confirmation_count,
        "time_between_entries": [_days_between(previous, current) for previous, current in zip(entry_dates, entry_dates[1:])],
        "price_distance_between_entries": [
            _round_pct((current - previous) / previous * 100) if previous else None
            for previous, current in zip(entry_prices, entry_prices[1:])
        ],
    }


def _build_exit_sequence(
    events: list[Any],
    accounting: dict[str, Any],
    snapshots: list[dict[str, Any]],
    rows: list[Any],
) -> dict[str, Any]:
    exits = [event for event in events if _event_value(event, "event_type") in EXIT_TYPES]
    partial_exits = [event for event in exits if _event_value(event, "event_type") == "partial_exit"]
    event_metrics = accounting["event_metrics"]
    snapshot_by_key = {snapshot["event_key"]: snapshot for snapshot in snapshots}
    exit_returns: list[float | None] = []
    full_exit_returns: list[float | None] = []
    partial_profit = 0.0
    exit_quantity_total = sum(_number(_event_value(event, "quantity")) or 0.0 for event in exits)
    sold_after_breakdown = 0.0

    for index, event in enumerate(events):
        if _event_value(event, "event_type") not in EXIT_TYPES:
            continue
        key = _event_key(event, index)
        metrics = event_metrics.get(key, {})
        price = _number(_event_value(event, "price"))
        average_cost = _number(metrics.get("average_cost_before"))
        exit_return = _round_pct((price - average_cost) / average_cost * 100) if price is not None and average_cost else None
        exit_returns.append(exit_return)
        if _event_value(event, "event_type") == "full_exit":
            full_exit_returns.append(exit_return)
        if _event_value(event, "event_type") == "partial_exit":
            partial_profit += max(0.0, _number(metrics.get("realized_pnl")) or 0.0)
        snapshot = snapshot_by_key.get(key, {})
        if _is_negative(snapshot.get("event_price_vs_ma20_pct")) or snapshot.get("market_regime") == "downtrend":
            sold_after_breakdown += _number(_event_value(event, "quantity")) or 0.0

    peak_date = _peak_market_date(events, rows)
    sold_before_peak = 0.0
    if peak_date is not None:
        for event in exits:
            event_date = _event_value(event, "event_date")
            if isinstance(event_date, date) and event_date < peak_date:
                sold_before_peak += _number(_event_value(event, "quantity")) or 0.0

    return {
        "exit_count": len(exits),
        "partial_exit_count": len(partial_exits),
        "first_exit_return_pct": exit_returns[0] if exit_returns else None,
        "final_exit_return_pct": full_exit_returns[-1] if full_exit_returns else None,
        "percentage_sold_before_peak": _round_pct(_pct_from_ratio(_safe_div(sold_before_peak, exit_quantity_total))),
        "percentage_sold_after_breakdown": _round_pct(_pct_from_ratio(_safe_div(sold_after_breakdown, exit_quantity_total))),
        "profit_protected_by_partial_exits": _round_money(partial_profit),
        "residual_position_giveback_pct": _residual_position_giveback_pct(events, rows, accounting),
    }


def _build_advanced_internal(
    events: list[Any],
    rows: list[Any],
    accounting: dict[str, Any],
    lifecycle_metrics: dict[str, Any],
    plan: Any,
    decision_context: dict[str, Any],
) -> dict[str, Any]:
    historical_judgment_eligible = decision_context.get("historical_judgment_eligible") is True
    planned_r = _planned_r_amount(plan, accounting) if historical_judgment_eligible else None
    planned_r_value = _number(planned_r)
    realized_pnl = _number(accounting.get("total_realized_pnl"))
    weighted_entry = _number(accounting.get("weighted_average_entry_price"))
    max_position_size = _number(accounting.get("max_position_size")) or 0.0
    mae_pct = _number(lifecycle_metrics.get("max_unrealized_drawdown_pct"))
    mfe_pct = _number(lifecycle_metrics.get("max_unrealized_profit_pct"))
    mae_amount = weighted_entry * max_position_size * mae_pct / 100 if weighted_entry and mae_pct is not None else None
    mfe_amount = weighted_entry * max_position_size * mfe_pct / 100 if weighted_entry and mfe_pct is not None else None
    declared_plan_score = _plan_adherence_score(events, plan)
    observed_plan_score = None
    capture_rate = _round_pct(_safe_div(realized_pnl, mfe_amount) * 100) if mfe_amount and mfe_amount > 0 else None

    return {
        "planned_1r_amount": _round_money(planned_r),
        "realized_r_multiple": _round_ratio(_safe_div(realized_pnl, planned_r_value)),
        "mae_pct": _round_pct(mae_pct),
        "mae_r_multiple": _round_ratio(_safe_div(mae_amount, planned_r_value)),
        "mfe_pct": _round_pct(mfe_pct),
        "mfe_r_multiple": _round_ratio(_safe_div(mfe_amount, planned_r_value)),
        "mfe_capture_rate": capture_rate,
        "declared_plan_adherence_score": declared_plan_score,
        "observed_plan_adherence_score": observed_plan_score,
        "plan_adherence_score": observed_plan_score,
        "decision_quality_score": None,
        "capital_at_risk_by_event": accounting["capital_at_risk_by_event"],
        "exposure_curve": accounting["exposure_curve"],
        "benchmark_relative_return_pct": None,
        "sector_relative_return_pct": None,
    }


def _build_event_indicator_snapshots(events: list[Any], rows: list[Any], data_quality: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_type = _event_value(event, "event_type")
        if event_type not in ENTRY_TYPES | EXIT_TYPES:
            continue
        event_date = _event_value(event, "event_date")
        price = _number(_event_value(event, "price"))
        values = _point_in_time_values(rows, event_date)
        closes = values["closes"]
        volumes = values["volumes"]
        ma20 = ma(closes, 20)
        ma60 = ma(closes, 60)
        rsi14 = calc_rsi(closes, period=14)
        volume_ratio = None
        if len(volumes) >= 20:
            average_volume = sum(volumes[-20:]) / 20
            volume_ratio = volumes[-1] / average_volume if average_volume else None
        for key, required, actual in (
            ("ma20", 20, len(closes)),
            ("ma60", 60, len(closes)),
            ("rsi14", 15, len(closes)),
            ("volume_ratio", 20, len(volumes)),
        ):
            if actual < required:
                _add_note(
                    data_quality,
                    f"{event_type}_{_date_to_iso(event_date)}_{key}",
                    f"Insufficient point-in-time rows for {event_type} {key}: {actual}/{required}.",
                )
        snapshots.append({
            "event_key": _event_key(event, index),
            "event_id": _event_value(event, "id"),
            "event_type": event_type,
            "event_date": _date_to_iso(event_date),
            "ma20": _round_price(ma20),
            "ma60": _round_price(ma60),
            "rsi14": _round_pct(rsi14),
            "volume_ratio": _round_ratio(volume_ratio),
            "event_price_vs_ma20_pct": _distance_pct(price, ma20),
            "event_price_vs_ma60_pct": _distance_pct(price, ma60),
            "market_regime": _classify_market_regime(
                closes,
                values.get("ohlc_closes", []),
                values["highs"],
                values["lows"],
            ),
        })
    return snapshots


def _planned_r_amount(plan: Any, accounting: dict[str, Any]) -> float | None:
    risk_amount = _number(_event_value(plan, "planned_risk_amount"))
    if risk_amount is not None and risk_amount > 0:
        return risk_amount
    stop_price = _number(_event_value(plan, "planned_stop_price"))
    weighted_entry = _number(accounting.get("weighted_average_entry_price"))
    max_position_size = _number(accounting.get("max_position_size"))
    if stop_price is None or weighted_entry is None or max_position_size is None:
        return None
    risk_per_share = weighted_entry - stop_price
    if risk_per_share <= 0 or max_position_size <= 0:
        return None
    return risk_per_share * max_position_size


def _plan_adherence_score(events: list[Any], plan: Any) -> float | None:
    values = []
    for event in events:
        adherence = _event_value(event, "plan_adherence")
        if adherence == "yes":
            values.append(100.0)
        elif adherence == "partial":
            values.append(50.0)
        elif adherence == "no":
            values.append(0.0)
    if values:
        return _round_score(sum(values) / len(values))
    return None if plan is None else 50.0


def _active_exposure_days(events: list[Any], analysis_end: date | None) -> int | None:
    if analysis_end is None:
        return None
    position_size = 0.0
    active_days = 0
    previous_date: date | None = None
    for event in events:
        event_date = _event_value(event, "event_date")
        if not isinstance(event_date, date):
            continue
        if previous_date is not None and event_date > previous_date and position_size > 0:
            active_days += (event_date - previous_date).days
        event_type = _event_value(event, "event_type")
        quantity = _number(_event_value(event, "quantity")) or 0.0
        if event_type in ENTRY_TYPES:
            position_size += quantity
        elif event_type in EXIT_TYPES:
            position_size = max(0.0, position_size - quantity)
        previous_date = event_date
    if previous_date is not None and analysis_end > previous_date and position_size > 0:
        active_days += (analysis_end - previous_date).days
    return active_days


def _residual_position_giveback_pct(events: list[Any], rows: list[Any], accounting: dict[str, Any]) -> float | None:
    final_size = None
    for metrics in accounting["event_metrics"].values():
        final_size = _number(metrics.get("position_size_after"))
    if not final_size or final_size <= 0:
        return 0.0
    first_entry = next((event for event in events if _event_value(event, "event_type") in ENTRY_TYPES), None)
    start = _event_value(first_entry, "event_date") if first_entry is not None else None
    end = _analysis_end_date(events)
    points = _market_points(rows, start, end)
    weighted_entry = _number(accounting.get("weighted_average_entry_price"))
    if not points or not weighted_entry:
        return None
    closes = [point["close"] for point in points]
    return _round_pct(max(0.0, (max(closes) - closes[-1]) / weighted_entry * 100))


def _detect_market_events(events: list[Any], rows: list[Any]) -> list[dict[str, Any]]:
    first_entry = next((event for event in events if _event_value(event, "event_type") in ENTRY_TYPES), None)
    if first_entry is None:
        return []
    start = _event_value(first_entry, "event_date")
    end = _analysis_end_date(events)
    points = _market_points(
        rows,
        start,
        end,
        end_inclusive=_full_exit_date(events) is None,
    )
    detected: list[dict[str, Any]] = []
    running_high = None
    previous_close = None
    prior_closes: list[float] = []
    prior_volumes: list[float] = []
    for point in points:
        close = point["close"]
        volume = point.get("volume")
        ma20 = ma(prior_closes + [close], 20)
        average_volume = sum(prior_volumes[-19:] + [volume]) / len(prior_volumes[-19:] + [volume]) if volume is not None else None
        if running_high is not None and close > running_high:
            _append_detected(detected, point["date"], "new_high", {"close": _round_price(close)})
        if running_high is not None and close <= running_high * 0.95:
            _append_detected(detected, point["date"], "profit_giveback", {"close": _round_price(close), "running_high": _round_price(running_high)})
        if ma20 is not None and previous_close is not None and previous_close >= ma20 and close < ma20:
            _append_detected(detected, point["date"], "ma20_break", {"close": _round_price(close), "ma20": _round_price(ma20)})
        if previous_close is not None and close < previous_close and volume is not None and average_volume and volume / average_volume >= 1.5:
            _append_detected(detected, point["date"], "volume_down_day", {"close": _round_price(close), "volume_ratio": _round_ratio(volume / average_volume)})
        if len(detected) >= MAX_DETECTED_EVENTS:
            return detected
        running_high = max(running_high, close) if running_high is not None else close
        previous_close = close
        prior_closes.append(close)
        if volume is not None:
            prior_volumes.append(volume)
    return detected


def _append_detected(events: list[dict[str, Any]], event_date: date, event_type: str, evidence: dict[str, Any]) -> None:
    if len(events) >= MAX_DETECTED_EVENTS:
        return
    if any(event["date"] == _date_to_iso(event_date) and event["type"] == event_type for event in events):
        return
    events.append({"date": _date_to_iso(event_date), "type": event_type, "evidence": evidence})


def _market_points(
    rows: list[Any],
    start: date | None,
    end: date | None,
    *,
    end_inclusive: bool = True,
) -> list[dict[str, Any]]:
    if start is None or end is None:
        return []
    points = []
    for row in rows:
        row_date = _event_value(row, "record_date")
        if (
            not isinstance(row_date, date)
            or row_date < start
            or row_date > end
            or (not end_inclusive and row_date == end)
        ):
            continue
        close = _close(row)
        if close is None:
            continue
        points.append({
            "date": row_date,
            "close": close,
            "high": _high(row),
            "low": _low(row),
            "volume": _volume(row),
        })
    return points


def _point_in_time_values(rows: list[Any], as_of: date | None) -> dict[str, list[float]]:
    if as_of is None:
        return {"closes": [], "ohlc_closes": [], "highs": [], "lows": [], "volumes": []}
    same_day_row = next(
        (row for row in reversed(rows) if _event_value(row, "record_date") == as_of),
        None,
    )
    same_day_closes = _technical_values(same_day_row, "recent_closes") if same_day_row is not None else []
    if same_day_closes:
        completed = completed_trailing_series(
            _event_value(same_day_row, "technical"),
            as_of,
            closes=same_day_closes,
            highs=_technical_values(same_day_row, "recent_highs"),
            lows=_technical_values(same_day_row, "recent_lows"),
            volumes=_technical_values(same_day_row, "recent_volumes"),
        )
        if completed is not None:
            return completed
    latest_row = None
    for row in rows:
        row_date = _event_value(row, "record_date")
        if isinstance(row_date, date) and row_date < as_of:
            latest_row = row
    if latest_row is not None:
        closes = _technical_values(latest_row, "recent_closes")
        if closes:
            completed = completed_trailing_series(
                _event_value(latest_row, "technical"),
                as_of,
                closes=closes,
                highs=_technical_values(latest_row, "recent_highs"),
                lows=_technical_values(latest_row, "recent_lows"),
                volumes=_technical_values(latest_row, "recent_volumes"),
            )
            if completed is not None:
                return completed
    point_rows = [row for row in rows if isinstance(_event_value(row, "record_date"), date) and _event_value(row, "record_date") < as_of]
    aligned_ohlc = [
        (close, high, low)
        for row in point_rows
        for close, high, low in [(_close(row), _high(row), _low(row))]
        if close is not None and high is not None and low is not None
    ]
    return {
        "closes": [value for row in point_rows for value in [_close(row)] if value is not None],
        "ohlc_closes": [close for close, _high_value, _low_value in aligned_ohlc],
        "highs": [high for _close_value, high, _low_value in aligned_ohlc],
        "lows": [low for _close_value, _high_value, low in aligned_ohlc],
        "volumes": [value for row in point_rows for value in [_volume(row)] if value is not None],
    }


def _classify_market_regime(
    closes: list[float],
    ohlc_closes: list[float],
    highs: list[float],
    lows: list[float],
) -> str:
    if len(closes) < 20:
        return "insufficient_data"
    latest_close = closes[-1]
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60)
    rsi14 = calc_rsi(closes, period=14)
    recent = closes[-20:]
    recent_return = _distance_pct(latest_close, recent[0])
    recent_range = _distance_pct(max(recent), min(recent))
    average_range = None
    if len(ohlc_closes) >= 20 and len(highs) >= 20 and len(lows) >= 20:
        ranges = [
            (high - low) / close * 100
            for high, low, close in zip(highs[-20:], lows[-20:], ohlc_closes[-20:], strict=True)
            if close
        ]
        average_range = sum(ranges) / len(ranges) if ranges else None
    if average_range is not None and average_range >= 6:
        return "high_volatility"
    if ma20 is not None and rsi14 is not None and latest_close > ma20 and rsi14 >= 75:
        if _is_positive(_distance_pct(latest_close, ma20), 6) or _is_positive(recent_return, 12):
            return "strong_momentum"
    if ma20 is not None and latest_close < ma20 and (ma60 is None or ma20 < ma60):
        return "downtrend"
    if ma20 is not None and latest_close > ma20 and (ma60 is None or ma20 > ma60):
        return "uptrend"
    if recent_range is not None and recent_range <= 12:
        return "range_bound"
    return "range_bound"


def _peak_market_date(events: list[Any], rows: list[Any]) -> date | None:
    first_entry = next((event for event in events if _event_value(event, "event_type") in ENTRY_TYPES), None)
    if first_entry is None:
        return None
    points = _market_points(
        rows,
        _event_value(first_entry, "event_date"),
        _analysis_end_date(events),
        end_inclusive=_full_exit_date(events) is None,
    )
    if not points:
        return None
    return max(points, key=lambda point: point["close"])["date"]


def _compact_events(events: list[Any]) -> list[dict[str, Any]]:
    compact = []
    for index, event in enumerate(events):
        compact.append({
            "event_key": _event_key(event, index),
            "id": _event_value(event, "id"),
            "event_type": _event_value(event, "event_type"),
            "event_date": _date_to_iso(_event_value(event, "event_date")),
            "price": _round_price(_number(_event_value(event, "price"))),
            "quantity": _round_quantity(_number(_event_value(event, "quantity"))),
            "fees": _round_money(_number(_event_value(event, "fees")) or 0.0),
            "taxes": _round_money(_number(_event_value(event, "taxes")) or 0.0),
            "reason_category": _event_value(event, "reason_category"),
            "reason_code": _event_value(event, "reason_code"),
            "plan_adherence": _event_value(event, "plan_adherence"),
            "confidence_level": _event_value(event, "confidence_level"),
            "source": _event_value(event, "source"),
            "data_quality_note": _event_value(event, "data_quality_note"),
        })
    return compact


def _market_regime_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regimes = []
    seen = set()
    for snapshot in snapshots:
        key = (snapshot["event_date"], snapshot["market_regime"])
        if key in seen:
            continue
        seen.add(key)
        regimes.append({
            "date": snapshot["event_date"],
            "market_regime": snapshot["market_regime"],
        })
    return regimes


def _build_decision_context(plan: Any, data_quality: dict[str, Any]) -> dict[str, Any]:
    if plan is None:
        _add_note(data_quality, "decision_context", "No PositionLifecyclePlan row was available; decision context is insufficient.")
        return {
            "status": "insufficient",
            "has_plan": False,
            "historical_judgment_eligible": False,
            "source": None,
            "created_after_entry": None,
            "planned_holding_period": None,
            "default_stop_rule": None,
            "add_entry_condition": None,
        }
    source = _event_value(plan, "source")
    created_after_entry = _event_value(plan, "created_after_entry")
    historical_judgment_eligible = source == "user_recorded_at_event_time" and created_after_entry is not True
    return {
        "status": "present" if historical_judgment_eligible else "retrospective_only",
        "has_plan": True,
        "historical_judgment_eligible": historical_judgment_eligible,
        "source": source,
        "created_after_entry": created_after_entry,
        "planned_holding_period": _event_value(plan, "planned_holding_period"),
        "default_stop_rule": _event_value(plan, "default_stop_rule"),
        "add_entry_condition": _event_value(plan, "add_entry_condition"),
    }


def _source_data(symbol: str, events: list[Any], rows: list[Any], plan: Any) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "event_count": len(events),
        "market_row_count": len(rows),
        "first_market_date": _date_to_iso(_event_value(rows[0], "record_date")) if rows else None,
        "last_market_date": _date_to_iso(_event_value(rows[-1], "record_date")) if rows else None,
        "plan_present": plan is not None,
    }


def _plan_source_data(plan: Any) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "source": _event_value(plan, "source"),
        "created_after_entry": _event_value(plan, "created_after_entry"),
        "planned_holding_period": _event_value(plan, "planned_holding_period"),
        "default_stop_rule": _event_value(plan, "default_stop_rule"),
        "add_entry_condition": _event_value(plan, "add_entry_condition"),
        "planned_stop_price": _number(_event_value(plan, "planned_stop_price")),
        "planned_risk_amount": _number(_event_value(plan, "planned_risk_amount")),
        "planned_risk_pct": _number(_event_value(plan, "planned_risk_pct")),
    }


def _build_event_shared_context(
    db: Session,
    *,
    symbol: str,
    events: list[Any],
) -> dict[str, Any]:
    if not symbol or not events:
        return _empty_lifecycle_shared_context(symbol)

    event_contexts: list[dict[str, Any]] = []
    for index, event in enumerate(_sort_events(events)):
        event_date = _event_value(event, "event_date")
        shared_context = read_shared_context_for_symbol(
            db,
            symbol=symbol,
            consumer=SHARED_CONTEXT_CONSUMER_LIFECYCLE,
            reference_date=event_date if isinstance(event_date, date) else None,
            point_in_time=True,
        )
        event_contexts.append(
            {
                "event_key": _event_key(event, index),
                "event_type": _event_value(event, "event_type"),
                "event_date": _date_to_iso(event_date),
                "shared_context": shared_context,
            }
        )

    return {
        "version": "lifecycle-shared-context-v1",
        "consumer": SHARED_CONTEXT_CONSUMER_LIFECYCLE,
        "point_in_time": True,
        "events": event_contexts,
        "data_quality": aggregate_shared_context_quality(
            [
                item["shared_context"]
                for item in event_contexts
                if isinstance(item.get("shared_context"), dict)
            ]
        ),
    }


def _empty_lifecycle_shared_context(symbol: str) -> dict[str, Any]:
    return {
        "version": "lifecycle-shared-context-v1",
        "consumer": SHARED_CONTEXT_CONSUMER_LIFECYCLE,
        "point_in_time": True,
        "events": [],
        "data_quality": {
            "status": "unknown",
            "freshness_counts": {"fresh": 0, "stale": 0, "missing": 0, "unknown": 0},
            "missing_reasons": ["events_missing"] if not symbol else [],
            "blocking": False,
            "point_in_time": True,
        },
    }


def _shared_context_data_quality_note(shared_context: dict[str, Any]) -> dict[str, Any] | None:
    data_quality = shared_context.get("data_quality") if isinstance(shared_context, dict) else None
    if not isinstance(data_quality, dict):
        return None
    missing_reasons = [str(reason) for reason in data_quality.get("missing_reasons") or []]
    status = data_quality.get("status")
    if status not in {"missing", "partial"} and not missing_reasons:
        return None

    if "future_context_excluded" in missing_reasons:
        text = "部分 shared context 的資料日期晚於事件日期，已排除以避免使用未來資料回評當時決策。"
    elif missing_reasons:
        text = "部分 shared context 缺漏或過舊，本次生命週期檢討只把它作為資料品質 caveat。"
    else:
        text = "部分 shared context 非 fresh，本次生命週期檢討只把它作為資料品質 caveat。"
    return _text_item(
        text,
        ["shared_context.events", "shared_context.data_quality"],
    )


def _sort_events(events: list[Any]) -> list[Any]:
    return sorted(events, key=lambda event: (
        _event_value(event, "event_date") or date.min,
        _event_value(event, "created_at") or datetime.min,
        _event_value(event, "id") or 0,
    ))


def _sort_market_rows(rows: list[Any]) -> list[Any]:
    return sorted(rows, key=lambda row: _event_value(row, "record_date") or date.min)


def _analysis_end_date(events: list[Any]) -> date | None:
    event_dates = [_event_value(event, "event_date") for event in events if isinstance(_event_value(event, "event_date"), date)]
    return max(event_dates) if event_dates else None


def _analysis_start_date(events: list[Any]) -> date | None:
    entry_dates = [
        _event_value(event, "event_date")
        for event in events
        if _event_value(event, "event_type") in ENTRY_TYPES
        and isinstance(_event_value(event, "event_date"), date)
    ]
    if entry_dates:
        return min(entry_dates)
    event_dates = [
        _event_value(event, "event_date")
        for event in events
        if isinstance(_event_value(event, "event_date"), date)
    ]
    return min(event_dates) if event_dates else None


def _full_exit_date(events: list[Any]) -> date | None:
    for event in reversed(events):
        if _event_value(event, "event_type") != "full_exit":
            continue
        event_date = _event_value(event, "event_date")
        return event_date if isinstance(event_date, date) else None
    return None


def _event_key(event: Any, index: int) -> str:
    event_id = _event_value(event, "id")
    return f"id:{event_id}" if event_id is not None else f"idx:{index}"


def _ledger_amount(event: Any, key: str, index: int, data_quality: dict[str, Any]) -> float:
    raw_value = _event_value(event, key, missing=None)
    if raw_value is None:
        _add_note(
            data_quality,
            f"missing_ledger_{key}",
            f"Event {_event_key(event, index)} had missing {key}; 0 was used for ledger accounting.",
        )
        return 0.0
    return _number(raw_value) or 0.0


def _close(row: Any) -> float | None:
    return _latest_technical_value(row, "close", "recent_closes")


def _high(row: Any) -> float | None:
    return _latest_technical_value(row, "high", "recent_highs")


def _low(row: Any) -> float | None:
    return _latest_technical_value(row, "low", "recent_lows")


def _volume(row: Any) -> float | None:
    return _latest_technical_value(row, "volume", "recent_volumes")


def _latest_technical_value(row: Any, ohlcv_key: str, recent_key: str) -> float | None:
    ohlcv_value = _number(_technical_section(row, "ohlcv").get(ohlcv_key))
    if ohlcv_value is not None:
        return ohlcv_value
    values = _technical_values(row, recent_key)
    return values[-1] if values else None


def _technical_section(row: Any, key: str) -> dict[str, Any]:
    technical = _event_value(row, "technical")
    if not isinstance(technical, dict):
        return {}
    section = technical.get(key)
    return section if isinstance(section, dict) else {}


def _technical_values(row: Any, key: str) -> list[float]:
    technical = _event_value(row, "technical")
    if not isinstance(technical, dict):
        return []
    raw_values = technical.get(key)
    if not isinstance(raw_values, list):
        return []
    return [number for value in raw_values for number in [_number(value)] if number is not None]


def _event_value(obj: Any, key: str, missing: Any = None) -> Any:
    if obj is None:
        return missing
    if isinstance(obj, dict):
        return obj.get(key, missing)
    return getattr(obj, key, missing)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _pct_from_ratio(value: float | None) -> float | None:
    return value * 100 if value is not None else None


def _distance_pct(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base == 0:
        return None
    return _round_pct((value - base) / base * 100)


def _days_between(start: date | None, end: date | None) -> int | None:
    if not isinstance(start, date) or not isinstance(end, date):
        return None
    return (end - start).days


def _is_positive(value: float | None, threshold: float = 0.0) -> bool:
    return value is not None and value > threshold


def _is_negative(value: float | None, threshold: float = 0.0) -> bool:
    return value is not None and value < threshold


def _round_price(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _round_money(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _round_pct(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _round_ratio(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _round_score(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _round_quantity(value: float | None) -> float | int | None:
    if value is None:
        return None
    rounded = round(value, 6)
    return int(rounded) if rounded == int(rounded) else rounded


def _date_to_iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, date) else None


def _empty_data_quality() -> dict[str, Any]:
    return {"notes": [], "insufficient_data": []}


def _add_note(data_quality: dict[str, Any], key: str, note: str) -> None:
    if key not in data_quality["insufficient_data"]:
        data_quality["insufficient_data"].append(key)
    if note not in data_quality["notes"]:
        data_quality["notes"].append(note)


def _finalize_data_quality(data_quality: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "insufficient" if data_quality["insufficient_data"] else "ok",
        "notes": data_quality["notes"],
        "insufficient_data": data_quality["insufficient_data"],
    }
