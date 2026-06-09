from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class BrokerFeeConfig:
    fee_rate: Decimal = Decimal("0.001425")
    fee_discount: Decimal = Decimal("1")
    minimum_fee: Decimal = Decimal("20")


@dataclass(frozen=True, slots=True)
class TransactionTaxConfig:
    product: str = "common_stock"
    market: str = "TW"
    broker: str = "default"
    sell_tax_rate: Decimal = Decimal("0.003")


DEFAULT_TRANSACTION_TAX_CONFIGS: dict[tuple[str, str, str], TransactionTaxConfig] = {
    ("common_stock", "TW", "default"): TransactionTaxConfig(),
    ("etf", "TW", "default"): TransactionTaxConfig(product="etf", sell_tax_rate=Decimal("0.001")),
}


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def calculate_broker_fee(
    gross_amount: Decimal,
    config: BrokerFeeConfig = BrokerFeeConfig(),
    actual_fee: Decimal | None = None,
) -> Decimal:
    if actual_fee is not None:
        return _money(actual_fee)
    if gross_amount <= 0:
        return Decimal("0.00")
    calculated = gross_amount * config.fee_rate * config.fee_discount
    return _money(max(calculated, config.minimum_fee))


def transaction_tax_config(
    product: str = "common_stock",
    market: str = "TW",
    broker: str = "default",
) -> TransactionTaxConfig:
    return DEFAULT_TRANSACTION_TAX_CONFIGS.get(
        (product, market, broker),
        TransactionTaxConfig(product=product, market=market, broker=broker, sell_tax_rate=Decimal("0")),
    )


def calculate_sell_transaction_tax(
    gross_amount: Decimal,
    config: TransactionTaxConfig = TransactionTaxConfig(),
    explicit_tax: Decimal | None = None,
) -> Decimal:
    if explicit_tax is not None:
        return _money(explicit_tax)
    if gross_amount <= 0:
        return Decimal("0.00")
    return _money(gross_amount * config.sell_tax_rate)
