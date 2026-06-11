from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from ai_stock_sentinel.daily_radar.background_context import BackgroundContextPayload
from ai_stock_sentinel.daily_radar.finmind_background_context import FinMindBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.tdcc_background_context import TdccWeeklyMajorHoldersProvider


_FINMIND_CONTEXT_TYPES = {"full_margin", "lending"}
_TDCC_CONTEXT_TYPES = {"weekly_major_holders"}


class DefaultBackgroundChipContextProvider:
    """Route each background context type to its official/source-appropriate provider."""

    provider_name = "default_background_chip_context_provider"

    def __init__(
        self,
        *,
        finmind_provider: FinMindBackgroundChipContextProvider | None = None,
        tdcc_provider: TdccWeeklyMajorHoldersProvider | None = None,
    ) -> None:
        self._finmind_provider = finmind_provider or FinMindBackgroundChipContextProvider()
        self._tdcc_provider = tdcc_provider or TdccWeeklyMajorHoldersProvider()

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
        if finmind_types:
            for payload in self._finmind_provider.fetch(
                symbols=symbols,
                context_types=finmind_types,
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
