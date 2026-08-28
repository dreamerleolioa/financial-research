from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated

from pydantic import Field


POSTGRES_INTEGER_MAX = 2_147_483_647

# PostgreSQL NUMERIC(p, s) accepts at most p - s integer digits. Minimums keep
# positive domain values from rounding down to zero at the persisted scale.
PORTFOLIO_PRICE_MIN = Decimal("0.01")
PORTFOLIO_PRICE_MAX = Decimal("99999999.99")
PORTFOLIO_PRICE_QUANTUM = Decimal("0.01")
POSITION_EVENT_MONEY_MAX = Decimal("99999999.99")
POSITION_EVENT_MONEY_QUANTUM = Decimal("0.01")
REALIZED_PNL_MAX = Decimal("9999999999.99")
REALIZED_PNL_QUANTUM = Decimal("0.01")
REALIZED_RETURN_PCT_MAX = Decimal("999999.9999")
REALIZED_RETURN_PCT_QUANTUM = Decimal("0.0001")
LIFECYCLE_PRICE_MIN = Decimal("0.0001")
LIFECYCLE_PRICE_MAX = Decimal("99999999.9999")
LIFECYCLE_MONEY_MAX = Decimal("9999999999.99")
LIFECYCLE_PERCENT_MAX = Decimal("9999.9999")
# The HTTP/UI contract uses JSON numbers. Keep every accepted cent distinguishable
# after a JavaScript Number round trip instead of exposing the wider DB-only range.
PORTFOLIO_CASH_BALANCE_MAX = Decimal("9999999999999.99")
PORTFOLIO_CASH_BALANCE_QUANTUM = Decimal("0.01")

PortfolioPrice = Annotated[
    Decimal,
    Field(
        ge=PORTFOLIO_PRICE_MIN,
        le=PORTFOLIO_PRICE_MAX,
        max_digits=10,
        decimal_places=2,
        allow_inf_nan=False,
    ),
]
PositionEventMoney = Annotated[
    Decimal,
    Field(
        ge=0,
        le=POSITION_EVENT_MONEY_MAX,
        max_digits=10,
        decimal_places=2,
        allow_inf_nan=False,
    ),
]
LifecyclePrice = Annotated[
    Decimal,
    Field(
        ge=LIFECYCLE_PRICE_MIN,
        le=LIFECYCLE_PRICE_MAX,
        max_digits=12,
        decimal_places=4,
        allow_inf_nan=False,
    ),
]
LifecycleMoney = Annotated[
    Decimal,
    Field(
        ge=0,
        le=LIFECYCLE_MONEY_MAX,
        max_digits=12,
        decimal_places=2,
        allow_inf_nan=False,
    ),
]
LifecyclePercent = Annotated[
    Decimal,
    Field(
        ge=0,
        le=LIFECYCLE_PERCENT_MAX,
        max_digits=8,
        decimal_places=4,
        allow_inf_nan=False,
    ),
]
PortfolioCashBalance = Annotated[
    Decimal,
    Field(
        ge=0,
        le=PORTFOLIO_CASH_BALANCE_MAX,
        max_digits=15,
        decimal_places=2,
        allow_inf_nan=False,
    ),
]


def quantize_for_storage(value: Decimal, quantum: Decimal) -> Decimal:
    """Match PostgreSQL NUMERIC scale explicitly before persistence."""
    return value.quantize(quantum, rounding=ROUND_HALF_UP)
