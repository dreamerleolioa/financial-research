"""Checks on unrounded values; unavailable comparisons are not claimed as passes."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def indicator_consistency_issues(
    raw: Mapping[str, Any], *, previous_close: float | None = None,
    latest_close: float | None = None, latest_volume: float | None = None,
) -> list[str]:
    issues: list[str] = []

    def same(label: str, actual: Any, expected: Any) -> None:
        if actual is None or expected is None:
            return
        if not (math.isfinite(actual) and math.isfinite(expected)) or not math.isclose(
            actual, expected, rel_tol=1e-9, abs_tol=1e-8,
        ):
            issues.append(label)

    same("MA20 與布林中軌不一致", raw.get("ma20"), raw.get("bollinger_mid"))
    line, signal, hist = (raw.get(k) for k in ("macd_line", "macd_signal", "macd_hist"))
    if line is not None and signal is not None:
        same("MACD 柱體無法對帳", hist, line - signal)
    previous, delta = raw.get("macd_hist_previous"), raw.get("macd_hist_change_1d")
    if hist is not None and previous is not None:
        same("MACD 單日增減無法對帳", delta, hist - previous)
    current, previous = raw.get("obv"), raw.get("obv_previous")
    if current is not None and previous is not None:
        same("OBV 單日增減無法對帳", raw.get("obv_change_1d"), current - previous)
    if previous_close is not None and latest_close is not None and latest_volume is not None:
        expected_delta = latest_volume if latest_close > previous_close else -latest_volume if latest_close < previous_close else 0
        same("OBV 增減與日K方向或成交量不一致", raw.get("obv_change_1d"), expected_delta)
    same("唐奇安上緣與突破基準不一致", raw.get("donchian_upper"), raw.get("prior_high_20d"))
    same("唐奇安下緣與突破基準不一致", raw.get("donchian_lower"), raw.get("prior_low_20d"))
    return issues
