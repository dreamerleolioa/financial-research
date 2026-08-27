from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from ai_stock_sentinel.data_sources.fundamental.mops_provider import (
    MOPS_HISTORICAL_EPS_URL,
    MopsHistoricalEpsProvider,
)
from ai_stock_sentinel.data_sources.fundamental.normalizers import (
    normalize_mops_historical_eps_payload,
)


class _Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.raise_calls = 0

    def raise_for_status(self) -> None:
        self.raise_calls += 1

    def json(self) -> Any:
        return self._payload


def _payload(
    *,
    stock_id: str,
    label: str,
    report_type: str,
    q1_eps: str,
    q2_eps: str,
) -> dict[str, Any]:
    return {
        "xaxisList": ["2025Q4", "2026Q1", "2026Q2"],
        "graphData": [
            {
                "label": label,
                "data": [
                    [0, "0.50", report_type],
                    [1, q1_eps, report_type],
                    [2, q2_eps, report_type],
                ],
            }
        ],
        "showNameList": [f"{stock_id} {label} (上市金融保險業)"],
    }


@pytest.mark.parametrize(
    ("symbol", "label", "report_type", "q1_eps", "q2_eps"),
    [
        ("2801.TW", "彰銀", "C", "0.44", "0.51"),
        ("2851.TW", "中再保", "A", "2.30", "5.61"),
        ("2884.TW", "玉山金", "C", "0.62", "0.70"),
    ],
)
def test_mops_historical_eps_normalizer_covers_financial_schemas(
    symbol: str,
    label: str,
    report_type: str,
    q1_eps: str,
    q2_eps: str,
) -> None:
    periods = normalize_mops_historical_eps_payload(
        _payload(
            stock_id=symbol.split(".", 1)[0],
            label=label,
            report_type=report_type,
            q1_eps=q1_eps,
            q2_eps=q2_eps,
        ),
        symbol=symbol,
    )

    assert [(period.fiscal_year, period.fiscal_quarter) for period in periods] == [
        (2025, 4),
        (2026, 1),
        (2026, 2),
    ]
    assert periods[1].quarter_eps == Decimal(q1_eps)
    assert periods[2].quarter_eps == Decimal(q2_eps)
    assert all(period.cumulative_eps is None for period in periods)
    assert all(period.source_provider == "mops_historical" for period in periods)
    assert all(period.availability_quality == "historical_unknown" for period in periods)
    assert periods[1].raw_payload["report_type"] == report_type


def test_mops_historical_eps_provider_posts_bounded_official_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(
        _payload(
            stock_id="2801",
            label="彰銀",
            report_type="C",
            q1_eps="0.44",
            q2_eps="0.51",
        )
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    def request_post(url: str, **kwargs: Any) -> _Response:
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(
        "ai_stock_sentinel.data_sources.fundamental.mops_provider.today_taipei",
        lambda: date(2026, 8, 27),
    )
    periods = MopsHistoricalEpsProvider(request_post=request_post).fetch_periods("2801.TW")

    assert len(periods) == 3
    assert response.raise_calls == 1
    assert calls[0][0] == MOPS_HISTORICAL_EPS_URL
    assert calls[0][1]["timeout"] == 5
    assert calls[0][1]["max_attempts"] == 1
    assert calls[0][1]["data"]["companyId"] == "2801"
    assert calls[0][1]["data"]["compareItem"] == "EPS"
    assert calls[0][1]["data"]["quarter"] == "true"
    assert calls[0][1]["data"]["ys"] == "20263"


def test_mops_historical_eps_normalizer_rejects_symbol_mismatch() -> None:
    with pytest.raises(ValueError, match="symbol mismatch"):
        normalize_mops_historical_eps_payload(
            _payload(
                stock_id="2884",
                label="玉山金",
                report_type="C",
                q1_eps="0.62",
                q2_eps="0.70",
            ),
            symbol="2801.TW",
        )


def test_mops_historical_eps_normalizer_rejects_non_finite_values() -> None:
    payload = _payload(
        stock_id="2801",
        label="彰銀",
        report_type="C",
        q1_eps="NaN",
        q2_eps="0.51",
    )

    with pytest.raises(ValueError, match="non-finite EPS"):
        normalize_mops_historical_eps_payload(payload, symbol="2801.TW")


def test_mops_historical_eps_normalizer_rejects_unknown_missing_token() -> None:
    axes = [
        f"{year}Q{quarter}"
        for year in (2024, 2025)
        for quarter in range(1, 5)
    ] + ["2026Q1"]
    payload = {
        "xaxisList": axes,
        "graphData": [
            {
                "label": "彰銀",
                "data": [
                    *[
                        [index, "0.50", "C"]
                        for index in range(len(axes) - 1)
                    ],
                    [len(axes) - 1, "N/A", "C"],
                ],
            }
        ],
        "showNameList": ["2801 彰銀 (上市金融保險業)"],
    }

    with pytest.raises(ValueError, match="invalid EPS token"):
        normalize_mops_historical_eps_payload(payload, symbol="2801.TW")


@pytest.mark.parametrize("missing_token", [None, "", "-", "--"])
def test_mops_historical_eps_normalizer_skips_known_missing_tokens(
    missing_token: object,
) -> None:
    payload = _payload(
        stock_id="2801",
        label="彰銀",
        report_type="C",
        q1_eps="0.44",
        q2_eps="0.51",
    )
    payload["graphData"][0]["data"][1][1] = missing_token

    periods = normalize_mops_historical_eps_payload(payload, symbol="2801.TW")

    assert [(period.fiscal_year, period.fiscal_quarter) for period in periods] == [
        (2025, 4),
        (2026, 2),
    ]
