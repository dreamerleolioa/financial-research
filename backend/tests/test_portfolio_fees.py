from decimal import Decimal

from ai_stock_sentinel.portfolio.fees import (
    BrokerFeeConfig,
    calculate_broker_fee,
    calculate_sell_transaction_tax,
    transaction_tax_config,
)


def test_broker_fee_applies_discount_and_minimum_separately() -> None:
    config = BrokerFeeConfig(
        fee_rate=Decimal("0.001425"),
        fee_discount=Decimal("0.28"),
        minimum_fee=Decimal("20"),
    )

    assert calculate_broker_fee(Decimal("1000"), config) == Decimal("20.00")
    assert calculate_broker_fee(Decimal("100000"), config) == Decimal("39.90")


def test_broker_fee_actual_fee_override_wins() -> None:
    config = BrokerFeeConfig(minimum_fee=Decimal("20"))

    assert calculate_broker_fee(Decimal("100000"), config, actual_fee=Decimal("7")) == Decimal("7.00")


def test_sell_side_tax_uses_product_dependent_defaults_and_explicit_override() -> None:
    stock_config = transaction_tax_config(product="common_stock", market="TW")
    etf_config = transaction_tax_config(product="etf", market="TW")

    assert calculate_sell_transaction_tax(Decimal("100000"), stock_config) == Decimal("300.00")
    assert calculate_sell_transaction_tax(Decimal("100000"), etf_config) == Decimal("100.00")
    assert calculate_sell_transaction_tax(Decimal("100000"), stock_config, explicit_tax=Decimal("0")) == Decimal("0.00")
