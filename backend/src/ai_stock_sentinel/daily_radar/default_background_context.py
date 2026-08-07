from __future__ import annotations

from collections.abc import Iterable
from datetime import date
import os

from ai_stock_sentinel.daily_radar.background_context import BackgroundContextPayload
from ai_stock_sentinel.daily_radar.finmind_background_context import FinMindBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.official_background_context import (
    OfficialBackgroundChipContextProvider,
    OfficialBackgroundContextError,
    is_official_background_supported_symbol,
)
from ai_stock_sentinel.daily_radar.tdcc_background_context import TdccWeeklyMajorHoldersProvider


_FINMIND_CONTEXT_TYPES = {"full_margin", "lending"}
_TDCC_CONTEXT_TYPES = {"weekly_major_holders"}
_BACKGROUND_PROVIDER_MODES = {"finmind_only", "official_first", "official_only"}


class DefaultBackgroundChipContextProvider:
    """Route each background context type to its official/source-appropriate provider."""

    provider_name = "default_background_chip_context_provider"

    def __init__(
        self,
        *,
        finmind_provider: FinMindBackgroundChipContextProvider | None = None,
        official_provider: OfficialBackgroundChipContextProvider | None = None,
        tdcc_provider: TdccWeeklyMajorHoldersProvider | None = None,
        provider_mode: str | None = None,
    ) -> None:
        self._finmind_provider = finmind_provider or FinMindBackgroundChipContextProvider()
        self._official_provider = official_provider or OfficialBackgroundChipContextProvider()
        self._tdcc_provider = tdcc_provider or TdccWeeklyMajorHoldersProvider()
        self._provider_mode = provider_mode or os.getenv(
            "DAILY_RADAR_BACKGROUND_PROVIDER_MODE",
            "finmind_only",
        )
        if self._provider_mode not in _BACKGROUND_PROVIDER_MODES:
            raise ValueError("invalid DAILY_RADAR_BACKGROUND_PROVIDER_MODE")

    def fetch(
        self,
        *,
        symbols: list[str],
        context_types: list[str],
        run_date: date,
        market: str,
    ) -> Iterable[BackgroundContextPayload]:
        finmind_types = [context_type for context_type in context_types if context_type in _FINMIND_CONTEXT_TYPES]
        tdcc_types = [context_type for context_type in context_types if context_type in _TDCC_CONTEXT_TYPES]

        payloads: dict[tuple[str, str], BackgroundContextPayload] = {}
        for context_type in finmind_types:
            if self._provider_mode == "finmind_only":
                for payload in self._finmind_provider.fetch(
                    symbols=symbols,
                    context_types=[context_type],
                    run_date=run_date,
                    market=market,
                ):
                    payloads[(payload.symbol, payload.context_type)] = payload
                continue

            official_symbols = symbols
            fallback_symbols: list[str] = []
            if self._provider_mode == "official_first":
                official_symbols = [
                    symbol for symbol in symbols if is_official_background_supported_symbol(symbol)
                ]
                fallback_symbols = [symbol for symbol in symbols if symbol not in official_symbols]
            try:
                if official_symbols:
                    for payload in self._official_provider.fetch(
                        symbols=official_symbols,
                        context_types=[context_type],
                        run_date=run_date,
                        market=market,
                    ):
                        payloads[(payload.symbol, payload.context_type)] = payload
            except OfficialBackgroundContextError:
                if self._provider_mode != "official_first":
                    raise
                fallback_symbols = list(symbols)
            if fallback_symbols:
                for payload in self._finmind_provider.fetch(
                    symbols=fallback_symbols,
                    context_types=[context_type],
                    run_date=run_date,
                    market=market,
                ):
                    payloads[(payload.symbol, payload.context_type)] = payload
        if tdcc_types:
            for payload in self._tdcc_provider.fetch(
                symbols=symbols,
                context_types=tdcc_types,
                run_date=run_date,
                market=market,
            ):
                payloads[(payload.symbol, payload.context_type)] = payload

        for symbol in symbols:
            for context_type in context_types:
                payload = payloads.get((symbol, context_type))
                if payload is not None:
                    yield payload


__all__ = ["DefaultBackgroundChipContextProvider"]
