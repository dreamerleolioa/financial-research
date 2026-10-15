from __future__ import annotations

import re


_SUPPORTED_TAIWAN_SYMBOL_PATTERN = re.compile(r"^[1-9][0-9]{3}\.(?:TW|TWO)$")
SUPPORTED_TAIWAN_MARKET_MESSAGE = "目前僅支援台灣上市（.TW）與上櫃（.TWO）個股，不支援 ETF、權證或特別股分析"


def normalize_taiwan_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


def is_supported_taiwan_symbol(symbol: str) -> bool:
    return _SUPPORTED_TAIWAN_SYMBOL_PATTERN.fullmatch(normalize_taiwan_symbol(symbol)) is not None


def validate_taiwan_symbol(symbol: str) -> str:
    normalized = normalize_taiwan_symbol(symbol)
    if not is_supported_taiwan_symbol(normalized):
        raise ValueError(SUPPORTED_TAIWAN_MARKET_MESSAGE)
    return normalized


def taiwan_stock_symbol_predicate(column):
    """Portable SQL equivalent of the stock-only boundary, before loading prices."""
    from sqlalchemy import and_, func, or_

    value = func.upper(func.trim(column))
    return and_(
        or_(value.like("____.TW"), value.like("____.TWO")),
        func.substr(value, 1, 1).between("1", "9"),
        *(func.substr(value, i, 1).between("0", "9") for i in range(2, 5)),
    )
