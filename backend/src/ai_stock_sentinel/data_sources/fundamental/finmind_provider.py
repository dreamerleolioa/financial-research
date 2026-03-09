from __future__ import annotations
import logging
import statistics
from datetime import date, timedelta

from ai_stock_sentinel.data_sources.fundamental.interface import (
    FundamentalData, FundamentalError,
)

logger = logging.getLogger(__name__)
_FINMIND_API = "https://api.finmindtrade.com/api/v4/data"


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class FinMindFundamentalProvider:
    name = "FinMindFundamental"

    def __init__(self, api_token: str = "") -> None:
        self._token = api_token

    def _fetch_dataset(self, dataset: str, stock_id: str, start_date: str, end_date: str) -> list[dict]:
        try:
            import requests
        except ImportError as e:
            raise FundamentalError("MISSING_DEPENDENCY", "requests 未安裝", self.name) from e

        params = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": start_date,
            "end_date": end_date,
            "token": self._token,
        }
        resp = requests.get(_FINMIND_API, params=params, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", [])

    def fetch(self, symbol: str, current_price: float) -> FundamentalData:
        stock_id = symbol.split(".")[0]
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=365 * 6)).isoformat()  # 6 年抓 20+ 季
        warnings: list[str] = []

        # ---- EPS ----
        fin_rows = self._fetch_dataset(
            dataset="TaiwanStockFinancialStatements",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )
        eps_rows = [r for r in fin_rows if r.get("type") == "EPS"]
        eps_rows.sort(key=lambda r: r.get("date", ""))

        if not eps_rows:
            raise FundamentalError(
                code="FINMIND_NO_EPS_DATA",
                message=f"FinMind EPS 資料為空（symbol={symbol}）",
                provider=self.name,
            )

        eps_values = [_safe_float(r.get("value")) for r in eps_rows]
        eps_values = [v for v in eps_values if v is not None]

        ttm_eps: float | None = None
        pe_current: float | None = None
        pe_mean: float | None = None
        pe_std: float | None = None
        pe_band = "unknown"
        pe_percentile: float | None = None

        if len(eps_values) >= 4:
            ttm_eps = sum(eps_values[-4:])
            if ttm_eps and ttm_eps > 0:
                pe_current = current_price / ttm_eps

                # 歷史 PE：逐季滑動（每 4 季一組）
                historical_pes: list[float] = []
                for i in range(4, len(eps_values) + 1):
                    window_eps = sum(eps_values[i - 4:i])
                    if window_eps and window_eps > 0:
                        historical_pes.append(current_price / window_eps)

                if len(historical_pes) >= 4:
                    pe_mean = statistics.mean(historical_pes)
                    pe_std = statistics.stdev(historical_pes) if len(historical_pes) >= 2 else 0.0

                    if pe_std and pe_std > 0:
                        if pe_current < pe_mean - pe_std:
                            pe_band = "cheap"
                        elif pe_current > pe_mean + pe_std:
                            pe_band = "expensive"
                        else:
                            pe_band = "fair"
                    else:
                        pe_band = "fair"

                    below = sum(1 for p in historical_pes if p <= pe_current)
                    pe_percentile = below / len(historical_pes) * 100
        else:
            warnings.append("EPS 季數不足 4 季，無法計算 TTM EPS")

        # ---- 股利 ----
        div_rows = self._fetch_dataset(
            dataset="TaiwanStockDividend",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )
        div_rows.sort(key=lambda r: r.get("date", ""), reverse=True)

        annual_cash_dividend: float | None = None
        dividend_yield: float | None = None
        yield_signal = "unknown"

        if div_rows:
            # 取最近一筆年度現金股利
            latest_cash = _safe_float(div_rows[0].get("CashEarningsDistribution"))
            if latest_cash is not None:
                annual_cash_dividend = latest_cash
                dividend_yield = annual_cash_dividend / current_price * 100
                if dividend_yield >= 5.0:
                    yield_signal = "high_yield"
                elif dividend_yield >= 3.0:
                    yield_signal = "mid_yield"
                else:
                    yield_signal = "low_yield"
        else:
            warnings.append("FinMind: 股利資料為空")

        return FundamentalData(
            symbol=symbol,
            ttm_eps=ttm_eps,
            pe_current=pe_current,
            pe_mean=pe_mean,
            pe_std=pe_std,
            pe_band=pe_band,
            pe_percentile=pe_percentile,
            annual_cash_dividend=annual_cash_dividend,
            dividend_yield=dividend_yield,
            yield_signal=yield_signal,
            source_provider=self.name,
            warnings=warnings,
        )
