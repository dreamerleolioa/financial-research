from datetime import date

import pytest
from pydantic import ValidationError

from ai_stock_sentinel.portfolio.schemas import (
    AddEntryRequest,
    BackfillLifecyclePlanRequest,
    ClosePortfolioRequest,
)
from ai_stock_sentinel.portfolio.storage_limits import (
    LIFECYCLE_MONEY_MAX,
    LIFECYCLE_PERCENT_MAX,
    LIFECYCLE_PRICE_MAX,
    POSITION_EVENT_MONEY_MAX,
)


@pytest.mark.parametrize(("model", "payload", "field"), [
    (
        AddEntryRequest,
        {
            "event_date": date(2026, 1, 1),
            "price": 900,
            "quantity": 1,
            "reason_code": "planned_scale_in",
            "plan_adherence": "yes",
            "confidence_level": "high",
        },
        "fees",
    ),
    (
        ClosePortfolioRequest,
        {"exit_date": date(2026, 1, 1), "exit_price": 900, "exit_quantity": 1},
        "taxes",
    ),
])
def test_event_cost_inputs_reject_postgresql_numeric_overflow(model, payload, field):
    with pytest.raises(ValidationError):
        model(**payload, **{field: float(POSITION_EVENT_MONEY_MAX + 1)})


@pytest.mark.parametrize(("field", "maximum"), [
    ("planned_stop_price", LIFECYCLE_PRICE_MAX),
    ("planned_risk_amount", LIFECYCLE_MONEY_MAX),
    ("planned_risk_pct", LIFECYCLE_PERCENT_MAX),
])
def test_lifecycle_plan_inputs_match_postgresql_numeric_upper_bounds(field, maximum):
    assert getattr(BackfillLifecyclePlanRequest(**{field: float(maximum)}), field) == float(maximum)

    with pytest.raises(ValidationError):
        BackfillLifecyclePlanRequest(**{field: float(maximum + 1)})
