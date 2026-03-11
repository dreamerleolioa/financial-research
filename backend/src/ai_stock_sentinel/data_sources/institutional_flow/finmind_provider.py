"""FinMindProvider：Primary 資料源，欄位最完整（含三大法人 + 融資融券）。"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ai_stock_sentinel.data_sources.finmind_token import get_token_manager
from ai_stock_sentinel.data_sources.institutional_flow.interface import (
    InstitutionalFlowData,
    InstitutionalFlowError,
)

logger = logging.getLogger(__name__)

# FinMind API endpoint
_FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

# FinMind 欄位對應表
_INST_COLUMN_MAP = {
    # FinMind 欄位名 → 本系統欄位名
    "Foreign_Investors_Buy": "foreign_buy_raw",
    "Foreign_Investors_Sell": "foreign_sell_raw",
    "Investment_Trust_Buy": "investment_trust_buy_raw",
    "Investment_Trust_Sell": "investment_trust_sell_raw",
    "Dealer_Buy": "dealer_buy_raw",
    "Dealer_Sell": "dealer_sell_raw",
}

_MARGIN_COLUMN_MAP = {
    "MarginPurchaseBuy": "margin_purchase_buy",
    "MarginPurchaseSell": "margin_purchase_sell",
    "MarginPurchaseToday": "margin_balance_today",
    "MarginPurchaseYesterday": "margin_balance_yesterday",
}


class FinMindProvider:
    """
    Primary Provider，透過 FinMind Public API 拉取三大法人與融資資料。

    API key 可選（免費方案有限流）。
    """

    name = "FinMind"

    def __init__(self, api_token: str = ""):
        # api_token 僅供測試用靜態覆蓋；正式使用時留空，由 token manager 動態取得
        self._static_token = api_token

    def _get_token(self) -> str:
        """優先用靜態 token（測試用），否則從 token manager 取得。"""
        return self._static_token or get_token_manager().token

    def fetch_daily_flow(self, symbol: str, days: int = 5) -> InstitutionalFlowData:
        try:
            return self._fetch_daily_flow_inner(symbol=symbol, days=days)
        except InstitutionalFlowError as exc:
            if exc.code == "FINMIND_TOKEN_EXPIRED" and not self._static_token:
                # token 過期：invalidate 後重試一次
                logger.warning("[FinMindProvider] token 過期（402），嘗試自動刷新後重試")
                get_token_manager().invalidate()
                return self._fetch_daily_flow_inner(symbol=symbol, days=days)
            raise

    def _fetch_daily_flow_inner(self, symbol: str, days: int) -> InstitutionalFlowData:
        try:
            import requests
        except ImportError as e:
            raise InstitutionalFlowError(
                code="MISSING_DEPENDENCY",
                message="requests 套件未安裝，請執行 pip install requests",
                provider=self.name,
            ) from e

        stock_id = _strip_suffix(symbol)
        end_date = date.today()
        start_date = end_date - timedelta(days=days * 2 + 7)  # 多抓幾天避免非交易日缺口

        warnings: list[str] = []

        # ---- 三大法人 ----
        inst_rows = self._fetch_dataset(
            requests=requests,
            dataset="TaiwanStockInstitutionalInvestorsBuySell",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

        foreign_buy: float | None = None
        investment_trust_buy: float | None = None
        dealer_buy: float | None = None
        foreign_net_cum: float | None = None
        trust_net_cum: float | None = None
        dealer_net_cum: float | None = None

        if inst_rows:
            # 取最近 days 筆（FinMind 回傳的是每日資料，需彙總）
            recent = inst_rows[-days:]
            f_buys, f_sells = [], []
            t_buys, t_sells = [], []
            d_buys, d_sells = [], []

            for row in recent:
                name_field = row.get("name", "")
                buy_val = _safe_float(row.get("buy", 0))
                sell_val = _safe_float(row.get("sell", 0))

                if "外資" in name_field or "Foreign" in name_field:
                    f_buys.append(buy_val)
                    f_sells.append(sell_val)
                elif "投信" in name_field or "Investment_Trust" in name_field:
                    t_buys.append(buy_val)
                    t_sells.append(sell_val)
                elif "自營" in name_field or "Dealer" in name_field:
                    d_buys.append(buy_val)
                    d_sells.append(sell_val)

            # FinMind buy/sell 欄位單位為「股」，除以 1000 轉換為「張」
            _S = 1000  # shares per lot (張)

            if f_buys:
                foreign_buy = sum(f_buys) / _S
                foreign_net_cum = (sum(f_buys) - sum(f_sells)) / _S
            else:
                warnings.append("FinMind: 外資欄位未找到，可能欄位名稱漂移")

            if t_buys:
                investment_trust_buy = sum(t_buys) / _S
                trust_net_cum = (sum(t_buys) - sum(t_sells)) / _S
            else:
                warnings.append("FinMind: 投信欄位未找到")

            if d_buys:
                dealer_buy = sum(d_buys) / _S
                dealer_net_cum = (sum(d_buys) - sum(d_sells)) / _S
            else:
                warnings.append("FinMind: 自營商欄位未找到")
        else:
            raise InstitutionalFlowError(
                code="FINMIND_NO_DATA",
                message=f"FinMind 三大法人資料為空（symbol={symbol}）",
                provider=self.name,
            )

        # ---- 融資 ----
        margin_delta: float | None = None
        margin_balance_delta_pct: float | None = None

        margin_rows = self._fetch_dataset(
            requests=requests,
            dataset="TaiwanStockMarginPurchaseShortSale",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

        if margin_rows:
            recent_m = margin_rows[-days:]
            deltas = []
            pct_deltas = []
            for row in recent_m:
                today_val = _safe_float(row.get("MarginPurchaseTodayBalance") or row.get("MarginPurchaseToday"))
                yesterday_val = _safe_float(row.get("MarginPurchaseYesterdayBalance") or row.get("MarginPurchaseYesterday"))
                if today_val is not None and yesterday_val is not None and yesterday_val != 0:
                    delta = today_val - yesterday_val
                    deltas.append(delta)
                    pct_deltas.append(delta / yesterday_val * 100)
            if deltas:
                # MarginPurchaseTodayBalance 單位為「股」，除以 1000 轉換為「張」
                margin_delta = sum(deltas) / 1000
                margin_balance_delta_pct = sum(pct_deltas) / len(pct_deltas)
        else:
            warnings.append("FinMind: 融資資料為空，margin_delta 設為 None")

        # ---- 彙整 ----
        three_party_net = _safe_sum(foreign_net_cum, trust_net_cum, dealer_net_cum)
        consecutive_buy_days = _calc_consecutive_buy_days(inst_rows, days)
        flow_label = _determine_flow_label(
            three_party_net=three_party_net,
            margin_balance_delta_pct=margin_balance_delta_pct,
        )

        return InstitutionalFlowData(
            symbol=symbol,
            period_days=days,
            foreign_buy=foreign_buy,
            investment_trust_buy=investment_trust_buy,
            dealer_buy=dealer_buy,
            foreign_net_cumulative=foreign_net_cum,
            trust_net_cumulative=trust_net_cum,
            dealer_net_cumulative=dealer_net_cum,
            three_party_net=three_party_net,
            consecutive_buy_days=consecutive_buy_days,
            margin_delta=margin_delta,
            margin_balance_delta_pct=margin_balance_delta_pct,
            flow_label=flow_label,
            source_provider=self.name,
            warnings=warnings,
        )

    def _fetch_dataset(self, *, requests, dataset: str, stock_id: str, start_date: date, end_date: date) -> list[dict]:
        params: dict = {
            "dataset": dataset,
            "data_id": stock_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        token = self._get_token()
        if token:
            params["token"] = token

        try:
            resp = requests.get(_FINMIND_API, params=params, timeout=15)
        except Exception as exc:
            raise InstitutionalFlowError(
                code="FINMIND_REQUEST_ERROR",
                message=f"FinMind API 請求失敗（dataset={dataset}）：{exc}",
                provider=self.name,
            ) from exc

        if resp.status_code == 402:
            raise InstitutionalFlowError(
                code="FINMIND_TOKEN_EXPIRED",
                message=f"FinMind token 過期或無效（402），dataset={dataset}",
                provider=self.name,
            )

        try:
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            raise InstitutionalFlowError(
                code="FINMIND_REQUEST_ERROR",
                message=f"FinMind API 請求失敗（dataset={dataset}）：{exc}",
                provider=self.name,
            ) from exc

        if payload.get("status") != 200:
            msg = payload.get("msg", "unknown error")
            raise InstitutionalFlowError(
                code="FINMIND_API_ERROR",
                message=f"FinMind API 回傳錯誤（dataset={dataset}）：{msg}",
                provider=self.name,
            )

        return payload.get("data", [])


# ---- 工具函式 ----

def _strip_suffix(symbol: str) -> str:
    """'2330.TW' → '2330'"""
    return symbol.split(".")[0]


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_sum(*vals: float | None) -> float | None:
    filtered = [v for v in vals if v is not None]
    return sum(filtered) if filtered else None


def _calc_consecutive_buy_days(rows: list[dict], max_days: int) -> int | None:
    """估算連續法人買超天數（簡易版，以合計三大淨買賣）。"""
    if not rows:
        return None
    # 依日期取得每日三方合計淨買賣
    from collections import defaultdict
    daily: dict[str, float] = defaultdict(float)
    for row in rows:
        dt = row.get("date", "")
        buy = _safe_float(row.get("buy", 0)) or 0
        sell = _safe_float(row.get("sell", 0)) or 0
        daily[dt] += buy - sell

    sorted_dates = sorted(daily.keys(), reverse=True)
    count = 0
    for dt in sorted_dates[:max_days]:
        if daily[dt] > 0:
            count += 1
        else:
            break
    return count


def _determine_flow_label(
    three_party_net: float | None,
    margin_balance_delta_pct: float | None,
) -> str:
    """rule-based 決定 flow_label，不呼叫 LLM。"""
    if three_party_net is None:
        return "neutral"

    # 散戶追高：融資大增 + 法人出貨
    if margin_balance_delta_pct is not None and margin_balance_delta_pct > 5 and three_party_net < 0:
        return "retail_chasing"

    # 法人出貨
    if three_party_net < -1000:
        return "distribution"

    # 法人吸籌
    if three_party_net > 1000:
        return "institutional_accumulation"

    return "neutral"
