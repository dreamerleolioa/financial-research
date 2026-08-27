from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from ai_stock_sentinel.clock import today_taipei
from ai_stock_sentinel.data_sources.fundamental.normalizers import (
    NormalizedFundamentalPeriod,
    normalize_mops_historical_eps_payload,
)
from ai_stock_sentinel.data_sources.official_http import official_request_post


MOPS_HISTORICAL_EPS_URL = "https://mopsfin.twse.com.tw/compare/data"
RequestPost = Callable[..., Any]


class MopsHistoricalEpsProvider:
    name = "MopsHistoricalEps"

    def __init__(
        self,
        *,
        request_post: RequestPost | None = None,
        request_timeout_seconds: int = 5,
    ) -> None:
        self._request_post = request_post or official_request_post
        self._request_timeout_seconds = max(1, request_timeout_seconds)

    def fetch_periods(self, symbol: str) -> list[NormalizedFundamentalPeriod]:
        normalized_symbol = symbol.strip().upper()
        if re.fullmatch(r"[1-9]\d{3}\.(?:TW|TWO)", normalized_symbol) is None:
            raise ValueError("invalid Taiwan stock symbol")

        today = today_taipei()
        response = self._request_post(
            MOPS_HISTORICAL_EPS_URL,
            max_attempts=1,
            timeout=self._request_timeout_seconds,
            data={
                "compareItem": "EPS",
                "quarter": "true",
                "ylabel": "元",
                "ys": f"{today.year}{((today.month - 1) // 3) + 1}",
                "revenue": "true",
                "bcodeAvg": "false",
                "companyAvg": "false",
                "qnumber": "1",
                "companyId": normalized_symbol.split(".", 1)[0],
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("MOPS historical EPS response is not an object")
        periods = normalize_mops_historical_eps_payload(
            payload,
            symbol=normalized_symbol,
        )
        if not periods:
            raise ValueError("MOPS historical EPS response contains no EPS periods")
        return periods


__all__ = ["MOPS_HISTORICAL_EPS_URL", "MopsHistoricalEpsProvider"]
