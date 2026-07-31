from __future__ import annotations

import re


_SUPPORTED_TAIWAN_SYMBOL_PATTERN = re.compile(r"^[0-9A-Z]{1,10}\.(?:TW|TWO)$")
SUPPORTED_TAIWAN_MARKET_MESSAGE = "目前僅支援台灣上市（.TW）與上櫃（.TWO）股票"


def normalize_taiwan_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


def is_supported_taiwan_symbol(symbol: str) -> bool:
    return _SUPPORTED_TAIWAN_SYMBOL_PATTERN.fullmatch(normalize_taiwan_symbol(symbol)) is not None


def validate_taiwan_symbol(symbol: str) -> str:
    normalized = normalize_taiwan_symbol(symbol)
    if not is_supported_taiwan_symbol(normalized):
        raise ValueError(SUPPORTED_TAIWAN_MARKET_MESSAGE)
    return normalized
