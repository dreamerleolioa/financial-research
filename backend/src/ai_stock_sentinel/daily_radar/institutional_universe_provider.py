from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from ai_stock_sentinel.daily_radar.universe import InstitutionalLeaderRow
from ai_stock_sentinel.data_sources.finmind_token import get_token_manager

FINMIND_INSTITUTIONAL_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"
FINMIND_MARKET_DATA_URL = "https://api.finmindtrade.com/api/v4/data"

_FORBIDDEN_SYMBOL_PARAMS = frozenset({"data_id", "stock_id", "symbol"})
_REQUIRED_ACTORS = frozenset({"foreign", "trust"})
_ACTOR_ORDER = ("foreign", "trust")
_VOLUME_FIELDS = (
    "Trading_Volume",
    "trading_volume",
    "volume",
    "Volume",
    "capacity",
    "Trading_Volume_Thousand_Share",
)
_NET_VALUE_FIELDS = (
    "net_buy_value",
    "NetBuyValue",
    "net_buy_amount",
    "NetBuyAmount",
)
_BUY_VALUE_FIELDS = (
    "buy_value",
    "BuyValue",
    "buy_amount",
    "BuyAmount",
)
_SELL_VALUE_FIELDS = (
    "sell_value",
    "SellValue",
    "sell_amount",
    "SellAmount",
)
_NET_SHARE_FIELDS = (
    "net_buy",
    "NetBuy",
    "net_buy_volume",
    "NetBuyVolume",
    "buy_sell",
    "BuySell",
)
_BUY_SHARE_FIELDS = ("buy", "Buy", "buy_volume", "BuyVolume")
_SELL_SHARE_FIELDS = ("sell", "Sell", "sell_volume", "SellVolume")

RequestGetter = Callable[..., Any]


class FinMindMarketInstitutionalUniverseProvider:
    name = "FinMindMarketInstitutionalUniverseProvider"

    def __init__(
        self,
        *,
        api_token: str = "",
        request_get: RequestGetter | None = None,
        timeout: int = 15,
        recent_market_days: int = 5,
        recent_calendar_window_days: int = 10,
    ) -> None:
        self._static_token = api_token
        self._request_get = request_get
        self._timeout = timeout
        self._recent_market_days = recent_market_days
        self._recent_calendar_window_days = recent_calendar_window_days

    def same_day_institutional_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]:
        rows = self._fetch_market_rows(start_date=run_date, end_date=run_date)
        return _rank_same_day(rows, market=market, actor_limit=limit)

    def recent_accumulation_leaders(
        self,
        *,
        run_date: date,
        market: str,
        limit: int,
    ) -> Sequence[InstitutionalLeaderRow]:
        start_date = run_date - timedelta(days=self._recent_calendar_window_days)
        rows = self._fetch_market_rows(start_date=start_date, end_date=run_date)
        ranked = _rank_recent_accumulation(
            rows,
            market=market,
            market_days=self._recent_market_days,
        )
        return ranked[:limit]

    def _fetch_market_rows(self, *, start_date: date, end_date: date) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "dataset": FINMIND_INSTITUTIONAL_DATASET,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        token = self._get_token()
        if token:
            params["token"] = token
        _assert_all_market_params(params)

        request_get = self._request_get or _import_requests_get()
        response = request_get(FINMIND_MARKET_DATA_URL, params=params, timeout=self._timeout)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else response
        if isinstance(payload, Mapping) and payload.get("status") not in (None, 200):
            message = payload.get("msg", "unknown FinMind error")
            raise RuntimeError(f"FinMind institutional universe request failed: {message}")
        data = payload.get("data", []) if isinstance(payload, Mapping) else payload
        return [dict(row) for row in data if isinstance(row, Mapping)]

    def _get_token(self) -> str:
        return self._static_token or get_token_manager().token


def _import_requests_get() -> RequestGetter:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests package is required for FinMind universe requests") from exc
    return requests.get


def _assert_all_market_params(params: Mapping[str, object]) -> None:
    forbidden = _FORBIDDEN_SYMBOL_PARAMS.intersection(params)
    if forbidden:
        forbidden_list = ", ".join(sorted(forbidden))
        raise ValueError(f"FinMind market universe requests must not include symbol params: {forbidden_list}")


def _rank_same_day(
    rows: Sequence[Mapping[str, Any]],
    *,
    market: str,
    actor_limit: int = 50,
) -> list[InstitutionalLeaderRow]:
    if actor_limit <= 0:
        return []

    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    volumes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    source_dates: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in rows:
        actor = _actor_key(row)
        if actor not in _REQUIRED_ACTORS:
            continue
        stock_id = _stock_id(row)
        if not stock_id:
            continue
        totals[actor][stock_id] += _net_amount(row)
        volume = _volume(row)
        if volume is not None:
            volumes[actor][stock_id] = max(volumes[actor][stock_id], volume)
        row_date = str(row.get("date", ""))
        if row_date:
            source_dates[actor][stock_id].add(row_date)

    actor_scored: list[tuple[str, str, float, float, float, tuple[str, ...]]] = []
    for actor in _ACTOR_ORDER:
        scored = []
        for stock_id, net_amount in totals.get(actor, {}).items():
            if net_amount <= 0:
                continue
            concentration = net_amount / volumes[actor][stock_id] if volumes[actor][stock_id] > 0 else 0.0
            score = net_amount + concentration
            scored.append(
                (
                    actor,
                    stock_id,
                    net_amount,
                    concentration,
                    score,
                    tuple(sorted(source_dates[actor][stock_id])),
                )
            )
        actor_scored.extend(sorted(scored, key=lambda item: (-item[2], -item[3], item[1]))[:actor_limit])

    best_by_stock: dict[str, tuple[str, str, float, float, float, tuple[str, ...]]] = {}
    for item in actor_scored:
        stock_id = item[1]
        existing = best_by_stock.get(stock_id)
        if existing is None or _same_day_sort_key(item) < _same_day_sort_key(existing):
            best_by_stock[stock_id] = item

    ranked = sorted(best_by_stock.values(), key=_same_day_sort_key)
    return [
        InstitutionalLeaderRow(
            symbol=_format_symbol(stock_id, market),
            rank=index,
            score=score,
            actor=actor,
            net_buy=net_amount,
            concentration=concentration,
            source_dates=dates,
            flow_state="same_day_net_buy",
            bucket_hints=("same_day_institutional",),
        )
        for index, (actor, stock_id, net_amount, concentration, score, dates) in enumerate(ranked, start=1)
    ]


def _same_day_sort_key(item: tuple[str, str, float, float, float, tuple[str, ...]]) -> tuple[float, float, int, str]:
    actor, stock_id, net_amount, concentration, _, _ = item
    return (-net_amount, -concentration, _actor_sort_index(actor), stock_id)


def _actor_sort_index(actor: str) -> int:
    try:
        return _ACTOR_ORDER.index(actor)
    except ValueError:
        return len(_ACTOR_ORDER)


def _rank_recent_accumulation(
    rows: Sequence[Mapping[str, Any]],
    *,
    market: str,
    market_days: int,
) -> list[InstitutionalLeaderRow]:
    latest_dates = sorted({str(row.get("date", "")) for row in rows if row.get("date")}, reverse=True)[:market_days]
    latest_date_set = set(latest_dates)
    if not latest_date_set:
        return []

    daily_net: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    daily_volume: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        row_date = str(row.get("date", ""))
        if row_date not in latest_date_set:
            continue
        actor = _actor_key(row)
        if actor not in _REQUIRED_ACTORS:
            continue
        stock_id = _stock_id(row)
        if not stock_id:
            continue
        daily_net[stock_id][row_date] += _net_amount(row)
        volume = _volume(row)
        if volume is not None:
            daily_volume[stock_id][row_date] = max(daily_volume[stock_id].get(row_date, 0.0), volume)

    scored: list[tuple[str, tuple[float, float, float], float, int, float | None, tuple[str, ...]]] = []
    for stock_id, nets_by_date in daily_net.items():
        source_dates = tuple(sorted(nets_by_date))
        ordered_nets = [nets_by_date.get(row_date, 0.0) for row_date in sorted(latest_date_set)]
        cumulative_net = sum(ordered_nets)
        if cumulative_net <= 0:
            continue
        max_consecutive = _max_consecutive_positive_days(ordered_nets)
        if max_consecutive <= 0:
            continue
        volume_total = sum(daily_volume.get(stock_id, {}).values())
        if volume_total > 0:
            concentration = cumulative_net / volume_total
            secondary_score = concentration
            score = max_consecutive * 1_000_000.0 + concentration
        else:
            concentration = None
            secondary_score = cumulative_net
            score = max_consecutive * 1_000_000.0 + cumulative_net
        scored.append(
            (
                stock_id,
                (float(max_consecutive), secondary_score, cumulative_net),
                score,
                max_consecutive,
                concentration,
                source_dates,
            )
        )

    scored.sort(key=lambda item: (-item[1][0], -item[1][1], -item[1][2], item[0]))
    return [
        InstitutionalLeaderRow(
            symbol=_format_symbol(stock_id, market),
            rank=index,
            score=score,
            actor="institutional",
            cumulative_net_buy=metrics[2],
            concentration=concentration,
            consecutive_buy_days=max_consecutive,
            source_dates=source_dates,
            flow_state="consistent_accumulation" if max_consecutive >= 2 else "weak_confirmation",
            bucket_hints=("recent_accumulation",),
        )
        for index, (stock_id, metrics, score, max_consecutive, concentration, source_dates) in enumerate(
            scored,
            start=1,
        )
    ]


def _actor_key(row: Mapping[str, Any]) -> str | None:
    actor_name = str(row.get("name") or row.get("investor") or row.get("institutional_investor") or "")
    if "外資" in actor_name or "Foreign" in actor_name:
        return "foreign"
    if "投信" in actor_name or "Investment_Trust" in actor_name or "Investment Trust" in actor_name:
        return "trust"
    if "自營" in actor_name or "Dealer" in actor_name:
        return "dealer"
    return None


def _stock_id(row: Mapping[str, Any]) -> str:
    return str(row.get("stock_id") or row.get("StockID") or row.get("stock_no") or "").strip()


def _net_amount(row: Mapping[str, Any]) -> float:
    net = _first_float(row, _NET_VALUE_FIELDS)
    if net is not None:
        return net

    buy_value = _first_float(row, _BUY_VALUE_FIELDS)
    sell_value = _first_float(row, _SELL_VALUE_FIELDS)
    if buy_value is not None or sell_value is not None:
        return (buy_value or 0.0) - (sell_value or 0.0)

    net = _first_float(row, _NET_SHARE_FIELDS)
    if net is not None:
        return net

    buy = _first_float(row, _BUY_SHARE_FIELDS) or 0.0
    sell = _first_float(row, _SELL_SHARE_FIELDS) or 0.0
    return buy - sell


def _volume(row: Mapping[str, Any]) -> float | None:
    return _first_float(row, _VOLUME_FIELDS)


def _first_float(row: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _max_consecutive_positive_days(values: Sequence[float]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value > 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _format_symbol(stock_id: str, market: str) -> str:
    if "." in stock_id:
        return stock_id
    if market.upper() == "TW":
        return f"{stock_id}.TW"
    return stock_id


__all__ = [
    "FINMIND_INSTITUTIONAL_DATASET",
    "FINMIND_MARKET_DATA_URL",
    "FinMindMarketInstitutionalUniverseProvider",
]
