"""FinMindProvider：Primary 資料源，欄位最完整（含三大法人 + 融資融券）。"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ai_stock_sentinel.data_sources.finmind_client import FinMindClient, FinMindClientError
from ai_stock_sentinel.data_sources.finmind_token import get_token_manager
from ai_stock_sentinel.data_sources.institutional_flow.interface import (
    InstitutionalFlowData,
    InstitutionalFlowError,
)

logger = logging.getLogger(__name__)

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

    def __init__(self, api_token: str = "", client: FinMindClient | None = None):
        # api_token 僅供測試用靜態覆蓋；正式使用時留空，由 token manager 動態取得
        self._static_token = api_token
        self._client = client or FinMindClient(api_token=api_token)

    def fetch_daily_flow(self, symbol: str, days: int = 5) -> InstitutionalFlowData:
        try:
            return self._fetch_daily_flow_inner(symbol=symbol, days=days)
        except InstitutionalFlowError as exc:
            if exc.code == "FINMIND_TOKEN_EXPIRED" and not self._static_token and not self._client.uses_static_token:
                # token 過期：invalidate 後重試一次
                logger.warning("[FinMindProvider] token 過期（402），嘗試自動刷新後重試")
                get_token_manager().invalidate()
                return self._fetch_daily_flow_inner(symbol=symbol, days=days)
            raise

    def _fetch_daily_flow_inner(self, symbol: str, days: int) -> InstitutionalFlowData:
        stock_id = _strip_suffix(symbol)
        end_date = date.today()
        start_date = end_date - timedelta(days=days * 2 + 7)  # 多抓幾天避免非交易日缺口

        warnings: list[str] = []

        # ---- 三大法人 ----
        inst_rows = self._fetch_dataset(
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
        consecutive_buy_days: int | None = None
        consecutive_sell_days: int | None = None
        dominant_buyer: str | None = None
        dominant_seller: str | None = None

        if inst_rows:
            inst_summary = _summarize_institutional_rows(inst_rows, days)
            foreign_buy = inst_summary["foreign_buy"]
            investment_trust_buy = inst_summary["investment_trust_buy"]
            dealer_buy = inst_summary["dealer_buy"]
            foreign_net_cum = inst_summary["foreign_net_cumulative"]
            trust_net_cum = inst_summary["trust_net_cumulative"]
            dealer_net_cum = inst_summary["dealer_net_cumulative"]
            consecutive_buy_days = inst_summary["consecutive_buy_days"]
            consecutive_sell_days = inst_summary["consecutive_sell_days"]
            dominant_buyer = inst_summary["dominant_buyer"]
            dominant_seller = inst_summary["dominant_seller"]
            warnings.extend(inst_summary["warnings"])
        else:
            raise InstitutionalFlowError(
                code="FINMIND_NO_DATA",
                message=f"FinMind 三大法人資料為空（symbol={symbol}）",
                provider=self.name,
            )

        # ---- 融資 ----
        margin_delta: float | None = None
        margin_balance_delta_pct: float | None = None
        short_delta: float | None = None
        short_balance_delta_pct: float | None = None

        margin_rows = self._fetch_dataset(
            dataset="TaiwanStockMarginPurchaseShortSale",
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
        )

        if margin_rows:
            margin_summary = _summarize_margin_rows(margin_rows, days)
            margin_delta = margin_summary["margin_delta"]
            margin_balance_delta_pct = margin_summary["margin_balance_delta_pct"]
            short_delta = margin_summary["short_delta"]
            short_balance_delta_pct = margin_summary["short_balance_delta_pct"]
        else:
            warnings.append("FinMind: 融資資料為空，margin_delta 設為 None")

        securities_lending_delta: float | None = None
        securities_lending_volume: float | None = None
        try:
            lending_rows = self._fetch_dataset(
                dataset="TaiwanStockSecuritiesLending",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
            lending_summary = _summarize_lending_rows(lending_rows, days)
            securities_lending_delta = lending_summary["securities_lending_delta"]
            securities_lending_volume = lending_summary["securities_lending_volume"]
        except InstitutionalFlowError as exc:
            warnings.append(f"FinMind: 借券資料未取得（{exc.code}）")

        foreign_holding_ratio: float | None = None
        foreign_holding_ratio_delta_pct: float | None = None
        try:
            shareholding_rows = self._fetch_dataset(
                dataset="TaiwanStockShareholding",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
            holding_summary = _summarize_foreign_holding_rows(shareholding_rows)
            foreign_holding_ratio = holding_summary["foreign_holding_ratio"]
            foreign_holding_ratio_delta_pct = holding_summary["foreign_holding_ratio_delta_pct"]
        except InstitutionalFlowError as exc:
            warnings.append(f"FinMind: 外資持股資料未取得（{exc.code}）")

        major_holder_ratio: float | None = None
        major_holder_ratio_delta_pct: float | None = None
        retail_holder_ratio_delta_pct: float | None = None
        try:
            holder_rows = self._fetch_dataset(
                dataset="TaiwanStockHoldingSharesPer",
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
            holder_summary = _summarize_holder_rows(holder_rows)
            major_holder_ratio = holder_summary["major_holder_ratio"]
            major_holder_ratio_delta_pct = holder_summary["major_holder_ratio_delta_pct"]
            retail_holder_ratio_delta_pct = holder_summary["retail_holder_ratio_delta_pct"]
        except InstitutionalFlowError as exc:
            warnings.append(f"FinMind: 股東持股分級資料未取得（{exc.code}）")

        # ---- 彙整 ----
        three_party_net = _safe_sum(foreign_net_cum, trust_net_cum, dealer_net_cum)
        flow_label = _determine_flow_label(
            three_party_net=three_party_net,
            margin_balance_delta_pct=margin_balance_delta_pct,
            short_balance_delta_pct=short_balance_delta_pct,
            securities_lending_delta=securities_lending_delta,
            major_holder_ratio_delta_pct=major_holder_ratio_delta_pct,
            consecutive_buy_days=consecutive_buy_days,
            consecutive_sell_days=consecutive_sell_days,
            dominant_buyer=dominant_buyer,
            dominant_seller=dominant_seller,
        )
        flow_strength = _determine_flow_strength(
            three_party_net=three_party_net,
            consecutive_buy_days=consecutive_buy_days,
            consecutive_sell_days=consecutive_sell_days,
            margin_balance_delta_pct=margin_balance_delta_pct,
            short_balance_delta_pct=short_balance_delta_pct,
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
            consecutive_sell_days=consecutive_sell_days,
            margin_delta=margin_delta,
            margin_balance_delta_pct=margin_balance_delta_pct,
            short_delta=short_delta,
            short_balance_delta_pct=short_balance_delta_pct,
            securities_lending_delta=securities_lending_delta,
            securities_lending_volume=securities_lending_volume,
            foreign_holding_ratio=foreign_holding_ratio,
            foreign_holding_ratio_delta_pct=foreign_holding_ratio_delta_pct,
            major_holder_ratio=major_holder_ratio,
            major_holder_ratio_delta_pct=major_holder_ratio_delta_pct,
            retail_holder_ratio_delta_pct=retail_holder_ratio_delta_pct,
            dominant_buyer=dominant_buyer,
            dominant_seller=dominant_seller,
            flow_strength=flow_strength,
            flow_label=flow_label,
            source_provider=self.name,
            warnings=warnings,
        )

    def _fetch_dataset(self, *, dataset: str, stock_id: str, start_date: date, end_date: date) -> list[dict]:
        try:
            return self._client.fetch_data(
                dataset=dataset,
                data_id=stock_id,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
        except FinMindClientError as exc:
            raise _institutional_error_from_client_error(exc, provider=self.name) from exc


# ---- 工具函式 ----

def _strip_suffix(symbol: str) -> str:
    """'2330.TW' → '2330'"""
    return symbol.split(".")[0]


def _institutional_error_from_client_error(exc: FinMindClientError, *, provider: str) -> InstitutionalFlowError:
    if exc.code == "missing_dependency":
        return InstitutionalFlowError(
            code="MISSING_DEPENDENCY",
            message="requests 套件未安裝，請執行 pip install requests",
            provider=provider,
        )
    if exc.code == "quota_or_token_error":
        return InstitutionalFlowError(
            code="FINMIND_TOKEN_EXPIRED",
            message=f"FinMind token 過期、無效或 quota 已滿（402），dataset={exc.dataset}",
            provider=provider,
        )
    if exc.code == "quota_exceeded":
        return InstitutionalFlowError(
            code="FINMIND_QUOTA_EXCEEDED",
            message=f"FinMind request budget 已滿（dataset={exc.dataset}）",
            provider=provider,
        )
    if exc.code == "api_error":
        return InstitutionalFlowError(
            code="FINMIND_API_ERROR",
            message=f"FinMind API 回傳錯誤（dataset={exc.dataset}）：{exc.message}",
            provider=provider,
        )
    return InstitutionalFlowError(
        code="FINMIND_REQUEST_ERROR",
        message=f"FinMind API 請求失敗（dataset={exc.dataset}）：{exc.message}",
        provider=provider,
    )


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


def _investor_key(name_field: str) -> str | None:
    if "外資" in name_field or "Foreign" in name_field:
        return "foreign"
    if "投信" in name_field or "Investment_Trust" in name_field:
        return "trust"
    if "自營" in name_field or "Dealer" in name_field:
        return "dealer"
    return None


def _summarize_institutional_rows(rows: list[dict], days: int) -> dict:
    from collections import defaultdict

    warnings: list[str] = []
    daily: dict[str, dict[str, float]] = defaultdict(lambda: {"foreign": 0.0, "trust": 0.0, "dealer": 0.0})
    buy_totals = {"foreign": 0.0, "trust": 0.0, "dealer": 0.0}
    sell_totals = {"foreign": 0.0, "trust": 0.0, "dealer": 0.0}
    recent_dates = sorted({row.get("date", "") for row in rows if row.get("date")}, reverse=True)[:days]
    recent_date_set = set(recent_dates)

    for row in rows:
        if row.get("date") not in recent_date_set:
            continue
        key = _investor_key(str(row.get("name", "")))
        if key is None:
            continue
        buy = _safe_float(row.get("buy", 0)) or 0
        sell = _safe_float(row.get("sell", 0)) or 0
        buy_totals[key] += buy
        sell_totals[key] += sell
        daily[row.get("date", "")][key] += buy - sell

    for key, label in {"foreign": "外資", "trust": "投信", "dealer": "自營商"}.items():
        if buy_totals[key] == 0 and sell_totals[key] == 0:
            warnings.append(f"FinMind: {label}欄位未找到")

    daily_totals = [(dt, sum(values.values())) for dt, values in sorted(daily.items(), reverse=True)]
    consecutive_buy_days = _calc_consecutive_days(daily_totals, positive=True, max_days=days)
    consecutive_sell_days = _calc_consecutive_days(daily_totals, positive=False, max_days=days)
    net_by_actor = {key: (buy_totals[key] - sell_totals[key]) / 1000 for key in buy_totals}

    return {
        "foreign_buy": buy_totals["foreign"] / 1000 if buy_totals["foreign"] else None,
        "investment_trust_buy": buy_totals["trust"] / 1000 if buy_totals["trust"] else None,
        "dealer_buy": buy_totals["dealer"] / 1000 if buy_totals["dealer"] else None,
        "foreign_net_cumulative": net_by_actor["foreign"] if buy_totals["foreign"] or sell_totals["foreign"] else None,
        "trust_net_cumulative": net_by_actor["trust"] if buy_totals["trust"] or sell_totals["trust"] else None,
        "dealer_net_cumulative": net_by_actor["dealer"] if buy_totals["dealer"] or sell_totals["dealer"] else None,
        "consecutive_buy_days": consecutive_buy_days,
        "consecutive_sell_days": consecutive_sell_days,
        "dominant_buyer": _dominant_actor(net_by_actor, positive=True),
        "dominant_seller": _dominant_actor(net_by_actor, positive=False),
        "warnings": warnings,
    }


def _calc_consecutive_days(daily_totals: list[tuple[str, float]], *, positive: bool, max_days: int) -> int:
    count = 0
    for _, value in daily_totals[:max_days]:
        if (positive and value > 0) or (not positive and value < 0):
            count += 1
        else:
            break
    return count


def _dominant_actor(net_by_actor: dict[str, float], *, positive: bool) -> str:
    candidates = {key: value for key, value in net_by_actor.items() if (value > 0 if positive else value < 0)}
    if not candidates:
        return "none"
    key, value = max(candidates.items(), key=lambda item: abs(item[1]))
    total = sum(abs(v) for v in candidates.values())
    if total and abs(value) / total < 0.5:
        return "mixed"
    return key


def _summarize_margin_rows(rows: list[dict], days: int) -> dict[str, float | None]:
    recent_rows = rows[-days:]
    margin_deltas: list[float] = []
    margin_pct_deltas: list[float] = []
    short_deltas: list[float] = []
    short_pct_deltas: list[float] = []
    for row in recent_rows:
        margin_today = _safe_float(row.get("MarginPurchaseTodayBalance") or row.get("MarginPurchaseToday"))
        margin_yesterday = _safe_float(row.get("MarginPurchaseYesterdayBalance") or row.get("MarginPurchaseYesterday"))
        short_today = _safe_float(row.get("ShortSaleTodayBalance") or row.get("ShortSaleToday"))
        short_yesterday = _safe_float(row.get("ShortSaleYesterdayBalance") or row.get("ShortSaleYesterday"))
        if margin_today is not None and margin_yesterday is not None and margin_yesterday != 0:
            delta = margin_today - margin_yesterday
            margin_deltas.append(delta)
            margin_pct_deltas.append(delta / margin_yesterday * 100)
        if short_today is not None and short_yesterday is not None and short_yesterday != 0:
            delta = short_today - short_yesterday
            short_deltas.append(delta)
            short_pct_deltas.append(delta / short_yesterday * 100)
    return {
        "margin_delta": sum(margin_deltas) / 1000 if margin_deltas else None,
        "margin_balance_delta_pct": sum(margin_pct_deltas) / len(margin_pct_deltas) if margin_pct_deltas else None,
        "short_delta": sum(short_deltas) / 1000 if short_deltas else None,
        "short_balance_delta_pct": sum(short_pct_deltas) / len(short_pct_deltas) if short_pct_deltas else None,
    }


def _summarize_lending_rows(rows: list[dict], days: int) -> dict[str, float | None]:
    if not rows:
        return {"securities_lending_delta": None, "securities_lending_volume": None}
    recent_rows = rows[-days:]
    volumes = [_first_float(row, ["SecuritiesLending", "SecuritiesLendingVolume", "lend", "volume"]) for row in recent_rows]
    volumes = [value for value in volumes if value is not None]
    if not volumes:
        return {"securities_lending_delta": None, "securities_lending_volume": None}
    delta = volumes[-1] - volumes[0] if len(volumes) >= 2 else None
    return {
        "securities_lending_delta": delta / 1000 if delta is not None else None,
        "securities_lending_volume": sum(volumes) / 1000,
    }


def _summarize_foreign_holding_rows(rows: list[dict]) -> dict[str, float | None]:
    if not rows:
        return {"foreign_holding_ratio": None, "foreign_holding_ratio_delta_pct": None}
    sorted_rows = sorted(rows, key=lambda row: str(row.get("date", "")))
    latest = _first_float(sorted_rows[-1], ["ForeignInvestmentRemainingRatio", "ForeignInvestmentSharesRatio", "ratio"])
    earliest = _first_float(sorted_rows[0], ["ForeignInvestmentRemainingRatio", "ForeignInvestmentSharesRatio", "ratio"])
    return {
        "foreign_holding_ratio": latest,
        "foreign_holding_ratio_delta_pct": latest - earliest if latest is not None and earliest is not None else None,
    }


def _summarize_holder_rows(rows: list[dict]) -> dict[str, float | None]:
    if not rows:
        return {"major_holder_ratio": None, "major_holder_ratio_delta_pct": None, "retail_holder_ratio_delta_pct": None}

    from collections import defaultdict

    daily_major: dict[str, float] = defaultdict(float)
    daily_retail: dict[str, float] = defaultdict(float)
    for row in rows:
        dt = str(row.get("date", ""))
        level = str(row.get("HoldingSharesLevel") or row.get("level") or "")
        ratio = _first_float(row, ["percent", "HoldingSharesPercent", "ratio"])
        if ratio is None:
            continue
        if any(token in level for token in ["400", "600", "800", "1000", "above"]):
            daily_major[dt] += ratio
        elif any(token in level for token in ["1-999", "1", "under"]):
            daily_retail[dt] += ratio

    dates = sorted(set(daily_major) | set(daily_retail))
    if not dates:
        return {"major_holder_ratio": None, "major_holder_ratio_delta_pct": None, "retail_holder_ratio_delta_pct": None}
    first = dates[0]
    last = dates[-1]
    major_latest = daily_major.get(last) or None
    return {
        "major_holder_ratio": major_latest,
        "major_holder_ratio_delta_pct": daily_major[last] - daily_major[first] if first in daily_major and last in daily_major else None,
        "retail_holder_ratio_delta_pct": daily_retail[last] - daily_retail[first] if first in daily_retail and last in daily_retail else None,
    }


def _first_float(row: dict, keys: list[str]) -> float | None:
    for key in keys:
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _determine_flow_label(
    three_party_net: float | None,
    margin_balance_delta_pct: float | None,
    short_balance_delta_pct: float | None = None,
    securities_lending_delta: float | None = None,
    major_holder_ratio_delta_pct: float | None = None,
    consecutive_buy_days: int | None = None,
    consecutive_sell_days: int | None = None,
    dominant_buyer: str | None = None,
    dominant_seller: str | None = None,
) -> str:
    """rule-based 決定 flow_label，不呼叫 LLM。"""
    if three_party_net is None:
        return "neutral"

    # 散戶追高：融資大增 + 法人出貨
    if margin_balance_delta_pct is not None and margin_balance_delta_pct > 5 and three_party_net < 0:
        return "retail_chasing"

    if margin_balance_delta_pct is not None and margin_balance_delta_pct > 8 and major_holder_ratio_delta_pct is not None and major_holder_ratio_delta_pct < 0:
        return "retail_chasing"

    short_pressure = short_balance_delta_pct is not None and short_balance_delta_pct > 8
    lending_pressure = securities_lending_delta is not None and securities_lending_delta > 500
    institutional_selling = (consecutive_sell_days or 0) >= 3 or dominant_seller in {"foreign", "trust"}
    if three_party_net < 0 and (short_pressure or lending_pressure or institutional_selling):
        return "distribution"

    # 法人出貨
    if three_party_net < -1000:
        return "distribution"

    # 法人吸籌
    if three_party_net > 1000 or ((consecutive_buy_days or 0) >= 3 and dominant_buyer in {"foreign", "trust"}):
        return "institutional_accumulation"

    return "neutral"


def _determine_flow_strength(
    *,
    three_party_net: float | None,
    consecutive_buy_days: int | None,
    consecutive_sell_days: int | None,
    margin_balance_delta_pct: float | None,
    short_balance_delta_pct: float | None,
) -> str:
    magnitude = abs(three_party_net or 0)
    streak = max(consecutive_buy_days or 0, consecutive_sell_days or 0)
    margin_pressure = abs(margin_balance_delta_pct or 0)
    short_pressure = abs(short_balance_delta_pct or 0)
    if magnitude >= 3000 or streak >= 4 or margin_pressure >= 8 or short_pressure >= 8:
        return "strong"
    if magnitude >= 1000 or streak >= 2 or margin_pressure >= 3 or short_pressure >= 3:
        return "moderate"
    return "weak"
