import pytest
from pydantic import ValidationError

from ai_stock_sentinel.portfolio.entry_record_contract import (
    ADD_ENTRY_CONDITION_VALUES,
    DEFAULT_STOP_RULE_VALUES,
    ENTRY_REASON_VALUES,
    PLANNED_HOLDING_PERIOD_VALUES,
    EntryRecordContext,
)


def test_entry_record_option_values_match_phase_a_taxonomy():
    assert ENTRY_REASON_VALUES == (
        "breakout_confirmation",
        "pullback_held_support",
        "pullback_held_ma20",
        "institutional_flow_strengthened",
        "fundamental_thesis_improved",
        "event_or_news_catalyst",
        "long_term_accumulation",
        "value_revaluation",
        "other",
        "not_recorded",
    )
    assert PLANNED_HOLDING_PERIOD_VALUES == (
        "short_term",
        "swing",
        "medium_term",
        "long_term",
        "not_recorded",
    )
    assert DEFAULT_STOP_RULE_VALUES == (
        "break_20d_low",
        "break_ma20",
        "break_ma60",
        "cost_minus_pct",
        "fixed_price",
        "no_stop_recorded",
        "not_recorded",
    )
    assert ADD_ENTRY_CONDITION_VALUES == (
        "no_add_entry",
        "breakout_above_prior_high",
        "pullback_holds_ma20",
        "pullback_holds_support",
        "institutional_flow_continues",
        "profit_threshold_reached",
        "data_quality_complete_only",
        "no_averaging_down",
        "custom_plan_required",
        "not_recorded",
    )


@pytest.mark.parametrize(("field", "allowed_values"), [
    ("entry_reason", ENTRY_REASON_VALUES),
    ("planned_holding_period", PLANNED_HOLDING_PERIOD_VALUES),
    ("default_stop_rule", DEFAULT_STOP_RULE_VALUES),
    ("add_entry_condition", ADD_ENTRY_CONDITION_VALUES),
])
def test_entry_record_context_accepts_allowed_fixed_options(field: str, allowed_values: tuple[str, ...]):
    for value in allowed_values:
        context = EntryRecordContext(**{field: value})

        assert getattr(context, field) == value


@pytest.mark.parametrize(("field", "value"), [
    ("entry_reason", "planned_scale_in"),
    ("planned_holding_period", "intraday"),
    ("default_stop_rule", "trailing_stop"),
    ("add_entry_condition", "average_down_allowed"),
])
def test_entry_record_context_rejects_invalid_fixed_options(field: str, value: str):
    with pytest.raises(ValidationError):
        EntryRecordContext(**{field: value})


def test_entry_record_context_preserves_missing_explicit_null_and_not_recorded():
    missing = EntryRecordContext()
    explicit_null = EntryRecordContext(entry_reason=None)
    not_recorded = EntryRecordContext(entry_reason="not_recorded")

    assert "entry_reason" not in missing.model_fields_set
    assert missing.entry_reason is None
    assert "entry_reason" in explicit_null.model_fields_set
    assert explicit_null.entry_reason is None
    assert "entry_reason" in not_recorded.model_fields_set
    assert not_recorded.entry_reason == "not_recorded"


def test_entry_record_context_note_does_not_fill_fixed_options():
    context = EntryRecordContext(note="Breakout and MA20 support mentioned by user.")

    assert context.note == "Breakout and MA20 support mentioned by user."
    assert context.entry_reason is None
    assert context.planned_holding_period is None
    assert context.default_stop_rule is None
    assert context.add_entry_condition is None
    assert context.model_fields_set == {"note"}


def test_entry_record_context_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        EntryRecordContext(price_action="breakout")
