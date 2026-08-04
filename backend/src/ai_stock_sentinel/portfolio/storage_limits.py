from decimal import Decimal


POSTGRES_INTEGER_MAX = 2_147_483_647

# PostgreSQL NUMERIC(p, s) accepts at most p - s integer digits. Minimums keep
# positive domain values from rounding down to zero at the persisted scale.
PORTFOLIO_PRICE_MIN = Decimal("0.01")
PORTFOLIO_PRICE_MAX = Decimal("99999999.99")
POSITION_EVENT_MONEY_MAX = Decimal("99999999.99")
REALIZED_PNL_MAX = Decimal("9999999999.99")
REALIZED_RETURN_PCT_MAX = Decimal("999999.9999")
LIFECYCLE_PRICE_MIN = Decimal("0.0001")
LIFECYCLE_PRICE_MAX = Decimal("99999999.9999")
LIFECYCLE_MONEY_MAX = Decimal("9999999999.99")
LIFECYCLE_PERCENT_MAX = Decimal("9999.9999")
