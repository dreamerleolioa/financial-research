from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ai_stock_sentinel.analysis.position_lifecycle import (
    build_position_lifecycle_analysis,
    build_position_lifecycle_analysis_from_rows,
)
from ai_stock_sentinel.db.models import PositionEvent, PositionLifecyclePlan, SharedBackgroundContext, StockRawData
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.user_models.user import User


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, compiler, **kw):
    return "JSON"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            PositionEvent.__table__,
            PositionLifecyclePlan.__table__,
            StockRawData.__table__,
            SharedBackgroundContext.__table__,
        ],
    )
    with Session(engine) as session:
        yield session


def _event(
    event_id: int,
    event_type: str,
    event_date: date,
    price: float,
    quantity: int,
    *,
    fees=0,
    taxes=0,
    plan_adherence: str | None = None,
    reason_code: str | None = None,
):
    return SimpleNamespace(
        id=event_id,
        user_id=1,
        position_group_id="group-life",
        symbol="2330.TW",
        event_type=event_type,
        event_date=event_date,
        price=price,
        quantity=quantity,
        fees=fees,
        taxes=taxes,
        reason_category=None,
        reason_code=reason_code,
        plan_adherence=plan_adherence,
        confidence_level=None,
        source="user_recorded_at_event_time",
        data_quality_note=None,
        note="raw user note excluded from evidence",
        created_at=datetime.combine(event_date, datetime.min.time()) + timedelta(minutes=event_id),
    )


def _row(record_date: date, close: float, volume: float = 1000, symbol: str = "2330.TW"):
    return SimpleNamespace(
        symbol=symbol,
        record_date=record_date,
        technical={
            "ohlcv": {
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": volume,
            },
        },
    )


def _snapshot_row(record_date: date, closes: list[float], symbol: str = "2330.TW"):
    return SimpleNamespace(
        symbol=symbol,
        record_date=record_date,
        technical={
            "current_price": closes[-1],
            "recent_closes": closes,
            "recent_highs": [close + 1 for close in closes],
            "recent_lows": [close - 1 for close in closes],
            "recent_volumes": [1000 + index for index, _close in enumerate(closes)],
        },
    )


def _plan(**overrides):
    values = {
        "position_group_id": "group-life",
        "symbol": "2330.TW",
        "planned_risk_amount": 200,
        "planned_stop_price": 90,
        "planned_holding_period": None,
        "default_stop_rule": None,
        "add_entry_condition": None,
        "source": "user_recorded_at_event_time",
        "created_after_entry": False,
        "thesis": "excluded thesis",
        "planned_invalidation": "excluded invalidation",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _base_events():
    return [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=2, taxes=None, plan_adherence="yes"),
        _event(2, "add_entry", date(2026, 1, 12), 90, 10, fees=1, taxes=0, plan_adherence="partial"),
        _event(3, "manual_adjustment", date(2026, 1, 13), 125, 99, fees=None, taxes=None),
        _event(4, "partial_exit", date(2026, 1, 14), 120, 5, fees=2, taxes=1, plan_adherence="yes"),
        _event(5, "full_exit", date(2026, 1, 16), 80, 15, fees=3, taxes=None, plan_adherence="no"),
    ]


def _base_rows():
    pre_entry = [_row(date(2025, 11, 12) + timedelta(days=index), 100, 1000 + index) for index in range(59)]
    holding = [
        _row(date(2026, 1, 10), 100, 1200),
        _row(date(2026, 1, 11), 105, 1200),
        _row(date(2026, 1, 12), 90, 2200),
        _row(date(2026, 1, 13), 130, 1200),
        _row(date(2026, 1, 14), 120, 1200),
        _row(date(2026, 1, 15), 70, 2600),
        _row(date(2026, 1, 16), 80, 1200),
    ]
    return pre_entry + holding


def test_lifecycle_metrics_weighted_cost_realized_pnl_and_fees_taxes():
    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    metrics = result["lifecycle_metrics"]
    assert metrics["weighted_average_entry_price"] == pytest.approx(95.15)
    assert metrics["total_realized_pnl"] == pytest.approx(-109)
    assert metrics["total_return_pct_on_weighted_cost"] == pytest.approx(-5.7278)
    assert metrics["max_position_size"] == 20
    assert metrics["max_capital_at_risk"] == pytest.approx(1903)
    assert metrics["average_entry_price_over_time"] == [
        {"event_id": 1, "date": "2026-01-10", "position_size": 10, "average_entry_price": 100.2},
        {"event_id": 2, "date": "2026-01-12", "position_size": 20, "average_entry_price": 95.15},
    ]
    assert metrics["final_exit_date"] == "2026-01-16"
    assert metrics["total_holding_days_from_first_entry"] == 6
    assert metrics["active_exposure_days"] == 6
    assert metrics["max_unrealized_profit_pct"] == pytest.approx(36.6264)
    assert metrics["max_unrealized_drawdown_pct"] == pytest.approx(-26.4319)
    assert metrics["profit_giveback_pct"] == pytest.approx(52.5486)
    assert evidence["metrics"]["lifecycle"]["total_realized_pnl"] == pytest.approx(-109)
    assert any("missing taxes" in note for note in result["data_quality"]["notes"])


def test_no_manual_tax_requirement_when_event_taxes_are_omitted():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=None),
        _event(2, "full_exit", date(2026, 1, 11), 110, 10, fees=0, taxes=None),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 110)],
    )

    assert result["lifecycle_metrics"]["total_realized_pnl"] == pytest.approx(100)
    assert "missing_ledger_taxes" in result["data_quality"]["insufficient_data"]


def test_entry_and_exit_sequence_metrics():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    entry = result["entry_sequence"]
    assert entry["entry_count"] == 2
    assert entry["add_entry_count"] == 1
    assert entry["initial_entry_vs_ma20_pct"] == pytest.approx(0)
    assert entry["each_add_entry_vs_ma20_pct"] == [pytest.approx(-9.7744)]
    assert entry["average_up_count"] == 0
    assert entry["average_down_count"] == 1
    assert entry["add_after_breakdown_count"] == 1
    assert entry["add_after_confirmation_count"] == 0
    assert entry["time_between_entries"] == [2]
    assert entry["price_distance_between_entries"] == [pytest.approx(-10)]

    exit_sequence = result["exit_sequence"]
    assert exit_sequence["exit_count"] == 2
    assert exit_sequence["partial_exit_count"] == 1
    assert exit_sequence["first_exit_return_pct"] == pytest.approx(26.1167)
    assert exit_sequence["final_exit_return_pct"] == pytest.approx(-15.9222)
    assert exit_sequence["percentage_sold_before_peak"] == pytest.approx(0)
    assert exit_sequence["percentage_sold_after_breakdown"] == pytest.approx(75)
    assert exit_sequence["profit_protected_by_partial_exits"] == pytest.approx(121.25)
    assert exit_sequence["residual_position_giveback_pct"] == pytest.approx(0)


def test_entry_sequence_add_entry_count_is_zero_for_initial_entry_only():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=[_event(1, "initial_entry", date(2026, 1, 10), 100, 10)],
        market_rows=[_snapshot_row(date(2026, 1, 10), list(range(81, 101)))],
    )

    assert result["entry_sequence"]["entry_count"] == 1
    assert result["entry_sequence"]["add_entry_count"] == 0


def test_entry_sequence_add_entry_count_counts_multiple_explicit_add_entries():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10),
        _event(2, "add_entry", date(2026, 1, 11), 105, 5, plan_adherence="yes", reason_code="planned_scale_in"),
        _event(3, "add_entry", date(2026, 1, 12), 95, 5, plan_adherence="no", reason_code="averaging_down"),
        _event(4, "partial_exit", date(2026, 1, 13), 110, 5),
        _event(5, "manual_adjustment", date(2026, 1, 14), 108, 1),
    ]

    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), list(range(81, 101))),
            _snapshot_row(date(2026, 1, 11), list(range(86, 106))),
            _snapshot_row(date(2026, 1, 12), list(range(76, 96))),
            _snapshot_row(date(2026, 1, 13), list(range(91, 111))),
            _snapshot_row(date(2026, 1, 14), list(range(89, 109))),
        ],
    )

    assert result["entry_sequence"]["entry_count"] == 3
    assert result["entry_sequence"]["add_entry_count"] == 2
    assert evidence["metrics"]["entry_sequence"]["add_entry_count"] == 2
    assert [event["event_type"] for event in evidence["events"]] == [
        "initial_entry",
        "add_entry",
        "add_entry",
        "partial_exit",
        "manual_adjustment",
    ]


def test_advanced_internal_risk_path_and_scores():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    advanced = result["advanced_internal"]
    assert advanced["planned_1r_amount"] == pytest.approx(200)
    assert advanced["realized_r_multiple"] == pytest.approx(-0.545)
    assert advanced["mae_pct"] == pytest.approx(-26.4319)
    assert advanced["mae_r_multiple"] == pytest.approx(-2.515)
    assert advanced["mfe_pct"] == pytest.approx(36.6264)
    assert advanced["mfe_r_multiple"] == pytest.approx(3.485)
    assert advanced["mfe_capture_rate"] == pytest.approx(-15.6384)
    assert advanced["plan_adherence_score"] == pytest.approx(62.5)
    assert advanced["decision_quality_score"] == pytest.approx(28.47)
    assert advanced["capital_at_risk_by_event"][-1]["capital_at_risk"] == pytest.approx(0)
    assert advanced["exposure_curve"][1]["position_size"] == 20
    assert advanced["benchmark_relative_return_pct"] is None
    assert advanced["sector_relative_return_pct"] is None


def test_plan_risk_derives_from_stop_when_amount_missing():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(planned_risk_amount=None, planned_stop_price=90),
    )

    assert result["advanced_internal"]["planned_1r_amount"] == pytest.approx(103)


def test_point_in_time_indicators_do_not_use_future_market_data():
    events = [_event(1, "initial_entry", date(2026, 3, 1), 64, 10)]
    rows = [
        _snapshot_row(date(2026, 3, 1), list(range(1, 65))),
        _snapshot_row(date(2026, 3, 2), [1000] * 64),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=rows,
    )

    snapshot = result["event_indicator_snapshots"][0]
    assert snapshot["ma20"] == pytest.approx(54.5)
    assert snapshot["ma60"] == pytest.approx(34.5)
    assert snapshot["rsi14"] == pytest.approx(100)
    assert snapshot["event_price_vs_ma20_pct"] == pytest.approx(17.4312)


def test_evidence_payload_is_compact_and_excludes_forbidden_raw_context():
    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    assert 0 < len(evidence["detected_events"]) <= 8
    assert evidence["market_regime_snapshots"]
    assert evidence["data_quality"]["notes"]
    assert not _contains_forbidden_key(evidence)
    assert not _contains_forbidden_key(result)


def test_lifecycle_evidence_payload_contains_copyable_ai_context_fields():
    _, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    assert set(evidence) == {
        "position_group_id",
        "symbol",
        "metrics",
        "events",
        "indicator_snapshots",
        "detected_events",
        "market_regime_snapshots",
        "shared_context",
        "source_data",
        "data_quality",
    }
    assert evidence["shared_context"]["point_in_time"] is True
    assert evidence["shared_context"]["data_quality"]["blocking"] is False
    assert set(evidence["metrics"]) == {"lifecycle", "entry_sequence", "exit_sequence", "advanced_internal"}
    assert evidence["metrics"]["lifecycle"]["total_realized_pnl"] == pytest.approx(-109)
    assert evidence["metrics"]["entry_sequence"]["average_down_count"] == 1
    assert evidence["metrics"]["exit_sequence"]["percentage_sold_after_breakdown"] == pytest.approx(75)
    assert evidence["metrics"]["advanced_internal"]["mfe_capture_rate"] == pytest.approx(-15.6384)

    first_event = evidence["events"][0]
    assert set(first_event) >= {
        "event_key",
        "event_type",
        "event_date",
        "price",
        "quantity",
        "fees",
        "taxes",
        "plan_adherence",
        "source",
    }
    first_snapshot = evidence["indicator_snapshots"][0]
    assert set(first_snapshot) >= {
        "event_key",
        "event_type",
        "event_date",
        "ma20",
        "ma60",
        "rsi14",
        "volume_ratio",
        "event_price_vs_ma20_pct",
        "event_price_vs_ma60_pct",
        "market_regime",
    }
    assert evidence["detected_events"]
    assert evidence["market_regime_snapshots"]
    assert evidence["source_data"] == {
        "symbol": "2330.TW",
        "event_count": 5,
        "market_row_count": len(_base_rows()),
        "first_market_date": "2025-11-12",
        "last_market_date": "2026-01-16",
        "plan_present": True,
    }


def test_insufficient_market_data_preserves_ledger_metrics_and_marks_context_insufficient():
    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=[],
        plan=None,
    )

    assert result["lifecycle_metrics"]["total_realized_pnl"] == pytest.approx(-109)
    assert result["data_quality"]["status"] == "insufficient"
    assert "holding_path_prices" in result["data_quality"]["insufficient_data"]
    assert result["decision_context"] == {
        "status": "insufficient",
        "has_plan": False,
        "source": None,
        "created_after_entry": None,
        "planned_holding_period": None,
        "default_stop_rule": None,
        "add_entry_condition": None,
    }
    assert evidence["metrics"]["advanced_internal"]["planned_1r_amount"] is None
    assert "intent" not in str(evidence).lower()
    assert result["lifecycle_review"]["classification"]["primary_label"] == "insufficient_data"
    assert "insufficient_data" in result["lifecycle_review"]["classification"]["labels"]
    assert "premature_scale_out" not in result["lifecycle_review"]["classification"]["labels"]


def test_lifecycle_review_classifies_phase_d_patterns_and_template_refs():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(),
    )

    review = result["lifecycle_review"]
    classification = review["classification"]
    assert classification["primary_label"] == "late_scale_out"
    assert set(classification["labels"]) >= {
        "averaging_down_into_weakness",
        "disciplined_scale_out",
        "risk_reduction_exit",
        "late_scale_out",
    }
    assert classification["tier"] == "needs_review"
    assert set(review) >= {
        "overall_conclusion",
        "what_worked",
        "what_needs_review",
        "event_level_evidence",
        "next_operation_rules",
        "data_quality_notes",
    }
    assert _all_text_items_have_source_refs(review)
    assert _all_text_items_contain_chinese(review)
    assert "本次生命週期檢討層級為需檢討" in review["overall_conclusion"]["text"]
    assert any("完整出清前的部分出場" in item["text"] for item in review["what_worked"])
    assert any("弱勢中加碼" in item["text"] for item in review["what_needs_review"])
    assert any("減碼或出場觸發條件" in item["text"] for item in review["next_operation_rules"])
    assert any("資料品質" in item["text"] for item in review["data_quality_notes"])
    assert any("發生 initial_entry" in item["text"] for item in review["event_level_evidence"])


def test_lifecycle_review_includes_backfilled_plan_provenance_caveat():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(source="user_backfilled", created_after_entry=True),
    )

    review = result["lifecycle_review"]
    assert result["decision_context"] == {
        "status": "present",
        "has_plan": True,
        "source": "user_backfilled",
        "created_after_entry": True,
        "planned_holding_period": None,
        "default_stop_rule": None,
        "add_entry_condition": None,
    }
    assert any("事後補填" in caveat["text"] for caveat in review["classification"]["caveats"])
    assert any("不視為原始進場" in item["text"] for item in review["data_quality_notes"])


def test_lifecycle_review_phase_e_no_averaging_down_plan_flags_lower_add_below_ma20():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0),
        _event(2, "add_entry", date(2026, 1, 11), 95, 5, fees=0, taxes=0),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 20),
            _snapshot_row(date(2026, 1, 11), [100] * 19 + [95]),
        ],
        plan=_plan(add_entry_condition="no_averaging_down"),
    )

    review = result["lifecycle_review"]
    assert result["decision_context"]["add_entry_condition"] == "no_averaging_down"
    assert review["classification"]["tier"] == "needs_review"
    assert "add_entry_plan_violation" in review["classification"]["labels"]
    assert any("加碼條件記錄為不攤平" in item["text"] for item in review["classification"]["reasons"])
    assert any("event_facts.id:2" in item["source_refs"] for item in review["classification"]["reasons"])
    assert _all_text_items_have_source_refs(review)


def test_lifecycle_review_phase_e_pullback_held_ma20_reason_adds_traceable_positive_support():
    events = [
        _event(
            1,
            "initial_entry",
            date(2026, 1, 10),
            101,
            10,
            fees=0,
            taxes=0,
            reason_code="pullback_held_ma20",
        ),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_snapshot_row(date(2026, 1, 10), [100] * 19 + [101])],
        plan=_plan(),
    )

    review = result["lifecycle_review"]
    assert "ma20_pullback_supported" in review["classification"]["labels"]
    assert any("拉回守住 MA20" in item["text"] for item in review["classification"]["reasons"])
    assert any("拉回守住 MA20" in item["text"] for item in review["what_worked"])
    support_reason = next(item for item in review["classification"]["reasons"] if "拉回守住 MA20" in item["text"])
    assert "event_facts.id:1" in support_reason["source_refs"]
    assert "event_indicator_snapshots.id:1" in support_reason["source_refs"]


def test_lifecycle_review_phase_e_break_ma20_stop_rule_without_acted_context_needs_review():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 1, 11), 101, 10, fees=0, taxes=0),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 20),
            _snapshot_row(date(2026, 1, 11), [105] * 19 + [101]),
        ],
        plan=_plan(default_stop_rule="break_ma20"),
    )

    review = result["lifecycle_review"]
    assert result["decision_context"]["default_stop_rule"] == "break_ma20"
    assert review["classification"]["tier"] == "needs_review"
    assert "unacted_stop_rule_break" in review["classification"]["labels"]
    assert any("預設停損規則為跌破 MA20" in item["text"] for item in review["what_needs_review"])


def test_lifecycle_review_phase_e_planned_holding_period_needs_review_without_hard_judgment():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 3, 20), 112, 10, fees=0, taxes=0, plan_adherence="partial"),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 20),
            _snapshot_row(date(2026, 3, 20), [112] * 20),
        ],
        plan=_plan(planned_holding_period="short_term"),
    )

    review = result["lifecycle_review"]
    assert result["decision_context"]["planned_holding_period"] == "short_term"
    assert "holding_period_needs_review" in review["classification"]["labels"]
    assert review["classification"]["tier"] == "needs_review"
    assert any("不是硬性錯誤" in item["text"] for item in review["classification"]["reasons"])
    assert any(
        "decision_context.planned_holding_period" in item["source_refs"]
        for item in review["classification"]["reasons"]
    )


def test_lifecycle_review_phase_e_missing_decision_context_does_not_hard_judge_fixed_option_violations():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0),
        _event(2, "full_exit", date(2026, 1, 11), 101, 10, fees=0, taxes=0),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), [100] * 20),
            _snapshot_row(date(2026, 1, 11), [105] * 19 + [101]),
        ],
        plan=None,
    )

    classification = result["lifecycle_review"]["classification"]
    assert result["decision_context"] == {
        "status": "insufficient",
        "has_plan": False,
        "source": None,
        "created_after_entry": None,
        "planned_holding_period": None,
        "default_stop_rule": None,
        "add_entry_condition": None,
    }
    assert classification["primary_label"] == "insufficient_data"
    assert classification["tier"] == "insufficient_context"
    assert "add_entry_plan_violation" not in classification["labels"]
    assert "unacted_stop_rule_break" not in classification["labels"]


def test_lifecycle_review_phase_e_backfilled_plan_keeps_provenance_caveat_with_fixed_facts():
    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=_base_events(),
        market_rows=_base_rows(),
        plan=_plan(
            source="user_backfilled",
            created_after_entry=True,
            planned_holding_period="swing",
            default_stop_rule="break_ma20",
            add_entry_condition="no_averaging_down",
        ),
    )

    assert result["decision_context"] == {
        "status": "present",
        "has_plan": True,
        "source": "user_backfilled",
        "created_after_entry": True,
        "planned_holding_period": "swing",
        "default_stop_rule": "break_ma20",
        "add_entry_condition": "no_averaging_down",
    }
    assert any("事後補填" in caveat["text"] for caveat in result["lifecycle_review"]["classification"]["caveats"])
    assert not _contains_forbidden_key(result)


def test_lifecycle_review_premature_scale_out_requires_recorded_context():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(
            2,
            "partial_exit",
            date(2026, 1, 11),
            102,
            5,
            fees=0,
            taxes=0,
            plan_adherence="no",
            reason_code="emotional_exit",
        ),
        _event(3, "full_exit", date(2026, 1, 12), 140, 5, fees=0, taxes=0, plan_adherence="yes"),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[
            _snapshot_row(date(2026, 1, 10), list(range(81, 101))),
            _snapshot_row(date(2026, 1, 11), list(range(101, 121))),
            _snapshot_row(date(2026, 1, 12), list(range(121, 141))),
        ],
        plan=_plan(),
    )

    assert result["lifecycle_review"]["classification"]["primary_label"] == "premature_scale_out"
    assert "premature_scale_out" in result["lifecycle_review"]["classification"]["labels"]


def test_lifecycle_review_missing_context_does_not_infer_premature_scale_out():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0),
        _event(2, "partial_exit", date(2026, 1, 11), 102, 5, fees=0, taxes=0),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 102)],
        plan=None,
    )

    labels = result["lifecycle_review"]["classification"]["labels"]
    assert "insufficient_data" in labels
    assert "premature_scale_out" not in labels
    assert any("不會被直接判定為過早" in caveat["text"] for caveat in result["lifecycle_review"]["classification"]["caveats"])


def test_lifecycle_review_classifies_coherent_position_management():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 1, 11), 110, 10, fees=0, taxes=0, plan_adherence="yes"),
    ]

    result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 110)],
        plan=_plan(),
    )

    assert result["lifecycle_review"]["classification"]["primary_label"] == "coherent_position_management"
    assert result["lifecycle_review"]["classification"]["tier"] == "constructive"


def test_lifecycle_shared_context_caveat_does_not_override_classification():
    events = [
        _event(1, "initial_entry", date(2026, 1, 10), 100, 10, fees=0, taxes=0, plan_adherence="yes"),
        _event(2, "full_exit", date(2026, 1, 11), 110, 10, fees=0, taxes=0, plan_adherence="yes"),
    ]
    base_result, _ = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 110)],
        plan=_plan(),
    )
    shared_context = {
        "version": "lifecycle-shared-context-v1",
        "consumer": "lifecycle_review",
        "point_in_time": True,
        "events": [
            {
                "event_key": "id:1",
                "event_type": "initial_entry",
                "event_date": "2026-01-10",
                "shared_context": {
                    "version": "shared-context-read-v1",
                    "symbol": "2330.TW",
                    "consumer": "lifecycle_review",
                    "reference_date": "2026-01-10",
                    "point_in_time": True,
                    "contexts": [
                        {
                            "context_type": "lending",
                            "source": {"domain": "background_context", "provider": "fixture"},
                            "as_of_date": None,
                            "freshness": "missing",
                            "missing_reason": "context_cache_missing",
                            "replay_key": "background_context:2330.TW:lending:missing",
                            "applicable_consumers": ["lifecycle_review"],
                            "payload": {},
                        }
                    ],
                    "caveats": [],
                    "data_quality": {
                        "status": "missing",
                        "freshness_counts": {"fresh": 0, "stale": 0, "missing": 1, "unknown": 0},
                        "missing_reasons": ["context_cache_missing"],
                        "blocking": False,
                        "point_in_time": True,
                    },
                },
            }
        ],
        "data_quality": {
            "status": "missing",
            "freshness_counts": {"fresh": 0, "stale": 0, "missing": 1, "unknown": 0},
            "missing_reasons": ["context_cache_missing"],
            "blocking": False,
            "point_in_time": True,
        },
    }

    result, evidence = build_position_lifecycle_analysis_from_rows(
        position_group_id="group-life",
        symbol="2330.TW",
        events=events,
        market_rows=[_row(date(2026, 1, 10), 100), _row(date(2026, 1, 11), 110)],
        plan=_plan(),
        shared_context=shared_context,
    )

    assert result["lifecycle_review"]["classification"]["primary_label"] == (
        base_result["lifecycle_review"]["classification"]["primary_label"]
    )
    assert result["lifecycle_review"]["classification"]["tier"] == (
        base_result["lifecycle_review"]["classification"]["tier"]
    )
    assert result["shared_context"] == shared_context
    assert evidence["shared_context"] == shared_context
    assert any("shared context" in item["text"] for item in result["lifecycle_review"]["classification"]["caveats"])


def test_db_builder_scopes_user_group_and_performs_no_writes(db_session: Session):
    db_session.add_all([
        User(id=1, google_sub="user-1", email="user1@example.com"),
        User(id=2, google_sub="user-2", email="user2@example.com"),
        PositionEvent(
            id=1,
            user_id=1,
            position_group_id="shared-group",
            symbol="2330.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=100,
            quantity=10,
            fees=0,
            taxes=0,
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 1, 9, 0, 0),
        ),
        PositionEvent(
            id=2,
            user_id=2,
            position_group_id="shared-group",
            symbol="9999.TW",
            event_type="initial_entry",
            event_date=date(2026, 1, 1),
            price=999,
            quantity=1,
            fees=0,
            taxes=0,
            source="user_recorded_at_event_time",
            created_at=datetime(2026, 1, 1, 8, 0, 0),
        ),
        PositionLifecyclePlan(
            user_id=1,
            position_group_id="shared-group",
            symbol="2330.TW",
            planned_risk_amount=50,
            source="user_backfilled",
            created_after_entry=False,
        ),
        StockRawData(
            symbol="2330.TW",
            record_date=date(2026, 1, 1),
            technical={"ohlcv": {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}},
            raw_data_is_final=True,
        ),
    ])
    db_session.commit()
    statements: list[str] = []

    @event.listens_for(db_session.bind, "before_cursor_execute")
    def _capture_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().lower())

    result, evidence = build_position_lifecycle_analysis(db_session, user_id=1, position_group_id="shared-group")

    assert result["symbol"] == "2330.TW"
    assert evidence["source_data"]["event_count"] == 1
    assert all(statement.startswith("select") for statement in statements)
    assert not any(statement.startswith(("insert", "update", "delete")) for statement in statements)
    assert any("position_event" in statement for statement in statements)
    assert any("position_lifecycle_plan" in statement for statement in statements)
    assert any("stock_raw_data" in statement for statement in statements)


def _contains_forbidden_key(value) -> bool:
    forbidden = {
        "ohlcv",
        "kline",
        "klines",
        "recent_closes",
        "recent_highs",
        "recent_lows",
        "recent_volumes",
        "raw_llm_prompt",
        "thesis",
        "planned_invalidation",
        "intent",
        "inferred_intent",
        "template",
        "template_fields",
        "note",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden:
                return True
            if _contains_forbidden_key(child):
                return True
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _all_text_items_have_source_refs(value) -> bool:
    if isinstance(value, dict):
        if "text" in value and (not isinstance(value.get("source_refs"), list) or not value["source_refs"]):
            return False
        return all(_all_text_items_have_source_refs(child) for child in value.values())
    if isinstance(value, list):
        return all(_all_text_items_have_source_refs(child) for child in value)
    return True


def _all_text_items_contain_chinese(value) -> bool:
    if isinstance(value, dict):
        if "text" in value and not _contains_chinese(value["text"]):
            return False
        return all(_all_text_items_contain_chinese(child) for child in value.values())
    if isinstance(value, list):
        return all(_all_text_items_contain_chinese(child) for child in value)
    return True


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)
