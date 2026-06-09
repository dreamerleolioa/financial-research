from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.metrics import calc_rsi, ma
from ai_stock_sentinel.db.models import PositionEvent, PositionLifecyclePlan, StockRawData


ENTRY_TYPES = {"initial_entry", "add_entry"}
EXIT_TYPES = {"partial_exit", "full_exit"}
MAX_DETECTED_EVENTS = 8


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
        analysis_end = _analysis_end_date(events)
        market_rows: list[StockRawData] = []
        if symbol is not None and analysis_end is not None:
            market_rows = db.execute(
                select(StockRawData)
                .where(
                    StockRawData.symbol == symbol,
                    StockRawData.record_date <= analysis_end,
                )
                .order_by(StockRawData.record_date.asc())
            ).scalars().all()

    return build_position_lifecycle_analysis_from_rows(
        position_group_id=position_group_id,
        symbol=symbol or "",
        events=events,
        market_rows=market_rows,
        plan=plan,
    )


def build_position_lifecycle_analysis_from_rows(
    *,
    position_group_id: str,
    symbol: str,
    events,
    market_rows=(),
    plan=None,
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
    advanced_internal = _build_advanced_internal(
        ordered_events,
        ordered_rows,
        accounting,
        lifecycle_metrics,
        plan,
        data_quality,
    )
    detected_events = _detect_market_events(ordered_events, ordered_rows)
    market_regime_snapshots = _market_regime_snapshots(event_snapshots)
    decision_context = _build_decision_context(plan, data_quality)
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

    if average_down_count > 0 and add_after_breakdown_count > 0:
        _append_label(labels, "averaging_down_into_weakness")
        item = _text_item(
            "加碼序列出現攤平，而且加碼時價格低於 MA20 或處於下降趨勢，屬於弱勢中加碼。",
            ["entry_sequence.average_down_count", "entry_sequence.add_after_breakdown_count"],
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
        not decision_context_insufficient
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
            "目前 Phase C 指標中沒有辨識出明確的正向部位管理模式。",
            ["entry_sequence", "exit_sequence", "lifecycle_metrics"],
        ))
    if not what_needs_review:
        what_needs_review.append(_text_item(
            " deterministic 規則目前沒有辨識出重大部位管理警訊。",
            ["entry_sequence", "exit_sequence", "advanced_internal"],
        ))
    if not data_quality_notes:
        data_quality_notes.append(_text_item(
            "資料品質沒有額外增加生命週期檢討限制；本次判讀以目前 Phase C 指標為準。",
            ["data_quality.status"],
        ))

    next_operation_rules.extend(_next_operation_rules(labels, decision_context_insufficient))
    primary_label = _primary_lifecycle_label(labels)
    tier = _lifecycle_tier(primary_label, labels)
    source_refs = _unique_refs([ref for item in reasons + caveats for ref in item["source_refs"]])
    if not source_refs:
        source_refs = ["entry_sequence", "exit_sequence", "decision_context"]

    return {
        "classification": {
            "primary_label": primary_label,
            "labels": labels or [primary_label],
            "tier": tier,
            "reasons": reasons or [_text_item("除了目前選定的主要分類外，沒有其他 deterministic 生命週期分類規則被觸發。", source_refs)],
            "caveats": caveats,
            "source_refs": source_refs,
        },
        "overall_conclusion": _text_item(
            f"本次生命週期檢討層級為{_lifecycle_tier_text(tier)}；主要分類為{_lifecycle_label_text(primary_label)}。",
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
        "late_scale_out",
        "averaging_down_into_weakness",
        "insufficient_data",
        "risk_reduction_exit",
        "disciplined_scale_out",
        "coherent_position_management",
    ):
        if label in labels:
            return label
    return "insufficient_data"


def _lifecycle_tier(primary_label: str, labels: list[str]) -> str:
    if primary_label in {"premature_scale_out", "late_scale_out", "averaging_down_into_weakness"}:
        return "needs_review"
    if primary_label == "insufficient_data" or "insufficient_data" in labels:
        return "insufficient_context"
    if primary_label in {"disciplined_scale_out", "coherent_position_management"}:
        return "constructive"
    return "mixed"


def _lifecycle_label_text(label: str) -> str:
    return {
        "insufficient_data": "資料不足",
        "averaging_down_into_weakness": "弱勢中攤平加碼",
        "disciplined_scale_out": "分批出場保護獲利",
        "risk_reduction_exit": "破位後降低風險",
        "premature_scale_out": "可能過早減碼",
        "late_scale_out": "出場偏晚",
        "coherent_position_management": "部位管理一致",
    }.get(label, label)


def _lifecycle_tier_text(tier: str) -> str:
    return {
        "needs_review": "需檢討",
        "insufficient_context": "脈絡不足",
        "constructive": "具建設性",
        "mixed": "混合結論",
    }.get(tier, tier)


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


def _next_operation_rules(labels: list[str], decision_context_insufficient: bool) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    if "averaging_down_into_weakness" in labels:
        rules.append(_text_item(
            "下次若要加碼虧損部位，應先記錄明確的轉強觸發條件，避免在弱勢中單純攤平。",
            ["entry_sequence.average_down_count", "entry_sequence.add_after_breakdown_count"],
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
        rules.append(_text_item(
            "下次生命週期可延續已記錄的計畫遵循、分批保護獲利與最終出場規則。",
            ["advanced_internal.plan_adherence_score", "exit_sequence.profit_protected_by_partial_exits"],
        ))
    return rules


def _event_fact_ref(event: dict[str, Any]) -> str:
    return f"event_facts.{event.get('event_key')}"


def _text_item(text: str, source_refs: list[str]) -> dict[str, Any]:
    return {"text": text, "source_refs": _unique_refs(source_refs)}


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
    path_points = _market_points(rows, first_entry_date, analysis_end)
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
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    planned_r = _planned_r_amount(plan, accounting)
    if planned_r is None:
        _add_note(data_quality, "planned_1r_amount", "Plan risk was unavailable; R-multiple metrics are null.")
    planned_r_value = _number(planned_r)
    realized_pnl = _number(accounting.get("total_realized_pnl"))
    weighted_entry = _number(accounting.get("weighted_average_entry_price"))
    max_position_size = _number(accounting.get("max_position_size")) or 0.0
    mae_pct = _number(lifecycle_metrics.get("max_unrealized_drawdown_pct"))
    mfe_pct = _number(lifecycle_metrics.get("max_unrealized_profit_pct"))
    mae_amount = weighted_entry * max_position_size * mae_pct / 100 if weighted_entry and mae_pct is not None else None
    mfe_amount = weighted_entry * max_position_size * mfe_pct / 100 if weighted_entry and mfe_pct is not None else None
    plan_score = _plan_adherence_score(events, plan)
    capture_rate = _round_pct(_safe_div(realized_pnl, mfe_amount) * 100) if mfe_amount and mfe_amount > 0 else None

    _add_note(data_quality, "benchmark_relative_return_pct", "Benchmark market data was unavailable for this lifecycle analysis.")
    _add_note(data_quality, "sector_relative_return_pct", "Sector market data was unavailable for this lifecycle analysis.")

    return {
        "planned_1r_amount": _round_money(planned_r),
        "realized_r_multiple": _round_ratio(_safe_div(realized_pnl, planned_r_value)),
        "mae_pct": _round_pct(mae_pct),
        "mae_r_multiple": _round_ratio(_safe_div(mae_amount, planned_r_value)),
        "mfe_pct": _round_pct(mfe_pct),
        "mfe_r_multiple": _round_ratio(_safe_div(mfe_amount, planned_r_value)),
        "mfe_capture_rate": capture_rate,
        "plan_adherence_score": plan_score,
        "decision_quality_score": _decision_quality_score(realized_pnl, planned_r_value, capture_rate, plan_score),
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
            "market_regime": _classify_market_regime(closes, values["highs"], values["lows"]),
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


def _decision_quality_score(
    realized_pnl: float | None,
    planned_r: float | None,
    capture_rate: float | None,
    plan_score: float | None,
) -> float | None:
    if planned_r is None or planned_r <= 0 or realized_pnl is None:
        return None
    score = 50.0 + _safe_div(realized_pnl, planned_r) * 20
    if capture_rate is not None:
        score += (capture_rate - 50.0) * 0.2
    if plan_score is not None:
        score += (plan_score - 50.0) * 0.2
    return _round_score(max(0.0, min(100.0, score)))


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
    points = _market_points(rows, start, end)
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


def _market_points(rows: list[Any], start: date | None, end: date | None) -> list[dict[str, Any]]:
    if start is None or end is None:
        return []
    points = []
    for row in rows:
        row_date = _event_value(row, "record_date")
        if not isinstance(row_date, date) or row_date < start or row_date > end:
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
        return {"closes": [], "highs": [], "lows": [], "volumes": []}
    latest_row = None
    for row in rows:
        row_date = _event_value(row, "record_date")
        if isinstance(row_date, date) and row_date <= as_of:
            latest_row = row
    if latest_row is not None:
        closes = _technical_values(latest_row, "recent_closes")
        if closes:
            return {
                "closes": closes,
                "highs": _technical_values(latest_row, "recent_highs"),
                "lows": _technical_values(latest_row, "recent_lows"),
                "volumes": _technical_values(latest_row, "recent_volumes"),
            }
    point_rows = [row for row in rows if isinstance(_event_value(row, "record_date"), date) and _event_value(row, "record_date") <= as_of]
    return {
        "closes": [value for row in point_rows for value in [_close(row)] if value is not None],
        "highs": [value for row in point_rows for value in [_high(row)] if value is not None],
        "lows": [value for row in point_rows for value in [_low(row)] if value is not None],
        "volumes": [value for row in point_rows for value in [_volume(row)] if value is not None],
    }


def _classify_market_regime(closes: list[float], highs: list[float], lows: list[float]) -> str:
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
    if len(highs) >= 20 and len(lows) >= 20 and len(highs) == len(lows):
        ranges = [(high - low) / close * 100 for high, low, close in zip(highs[-20:], lows[-20:], closes[-20:]) if close]
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
    points = _market_points(rows, _event_value(first_entry, "event_date"), _analysis_end_date(events))
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
        return {"status": "insufficient", "has_plan": False, "source": None, "created_after_entry": None}
    return {
        "status": "present",
        "has_plan": True,
        "source": _event_value(plan, "source"),
        "created_after_entry": _event_value(plan, "created_after_entry"),
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
