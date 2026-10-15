"""Bounded repair of price-only research; never mix adjusted technical data."""
from datetime import date, datetime, time, timedelta
import math
from time import monotonic
from zoneinfo import ZoneInfo

from curl_cffi import requests

from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider
from ai_stock_sentinel.daily_radar.market_bar_repository import get_taiwan_daily_bars
from ai_stock_sentinel.data_sources.official_http import official_request_get
from ai_stock_sentinel.phase1_avwap.provider import normalize_twse_stock_day_payload
from ai_stock_sentinel.taiwan_symbols import is_supported_taiwan_symbol


TAIPEI = ZoneInfo("Asia/Taipei")
INDEX_URL = "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
STOCK_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"


def _number(value):
    try:
        value = float(str(value).replace(",", ""))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _complete(row):
    return all((_number(row.get(f)) or 0) > 0 for f in ("open", "high", "low", "close"))


def _json(response):
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    return response.json() if hasattr(response, "json") else response


class RecoveringResearchPriceProvider(YFinanceResearchPriceProvider):
    def __init__(self, session=None, *, official_getter=None, actions_getter=None,
                 total_timeout=60, max_requests=64, clock=monotonic):
        self.session = session
        self.official_getter = official_getter or official_request_get
        self.actions_getter = actions_getter or requests.get
        self.total_timeout = total_timeout
        self.max_requests = max_requests
        self.clock = clock
        self.deadline = None
        self.request_count = 0
        self.cache = {}
        self.diagnostics = []

    def fetch(self, symbols, *, start_date, end_date):
        supported = [s for s in symbols if s in {"TAIEX", "SPX"} or is_supported_taiwan_symbol(s)]
        if not supported:
            return {}
        try:
            return super().fetch(supported, start_date=start_date, end_date=end_date)
        except Exception as exc:
            self.diagnostics.append({"stage": "yfinance", "error_type": type(exc).__name__, "symbols": supported})
            return {}

    def _request(self, getter, url, *, params):
        if self.deadline is None:
            self.deadline = self.clock() + self.total_timeout
        remaining = self.deadline - self.clock()
        if remaining <= 0 or self.request_count >= self.max_requests:
            raise TimeoutError("research_recovery_budget_exhausted")
        self.request_count += 1
        kwargs = {"params": params, "timeout": min(8, remaining)}
        if getter is self.official_getter and getter is official_request_get:
            kwargs["max_attempts"] = 1
        if getter is requests.get:
            kwargs["impersonate"] = "chrome"
        return _json(getter(url, **kwargs))

    def _official_month(self, symbol, month):
        key = ("prices", symbol, month)
        if key in self.cache:
            return self.cache[key]
        # Cache failed requests too: many candidate windows share a month.
        self.cache[key] = []
        payload = self._request(self.official_getter, INDEX_URL if symbol == "TAIEX" else STOCK_URL,
            params={"response": "json", "date": month.strftime("%Y%m%d"),
                    **({} if symbol == "TAIEX" else {"stockNo": symbol.split(".")[0]})})
        if (not isinstance(payload, dict) or payload.get("stat") != "OK"
                or str(payload.get("date", ""))[:6] != month.strftime("%Y%m")):
            raise ValueError("official_research_date_or_status_mismatch")
        if symbol == "TAIEX":
            fields = payload.get("fields", [])
            names = ("日期", "開盤指數", "最高指數", "最低指數", "收盤指數")
            indexes = [fields.index(name) for name in names]
            rows = []
            for raw in payload.get("data", []):
                day_text = str(raw[indexes[0]])
                y, m, d = map(int, day_text.split("/"))
                rows.append({"date": date(y + 1911, m, d).isoformat(),
                             **{f: _number(raw[i]) for f, i in zip(("open", "high", "low", "close"), indexes[1:])},
                             "price_source": "twse_index_history"})
        else:
            if symbol.split(".")[0] not in str(payload.get("title", "")).split():
                raise ValueError("official_research_symbol_mismatch")
            rows = [{"date": str(bar.trade_date), "open": bar.open, "high": bar.high,
                     "low": bar.low, "close": bar.close, "price_source": "twse_stock_day"}
                    for bar in normalize_twse_stock_day_payload(payload)]
        days = [date.fromisoformat(row["date"]) for row in rows]
        if len(set(days)) != len(days) or any((day.year, day.month) != (month.year, month.month) for day in days):
            raise ValueError("official_research_row_dates_invalid")
        self.cache[key] = rows
        return rows

    def _actions(self, symbol, start, end):
        key = ("actions", symbol, start, end)
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None
        query_start = start - timedelta(days=7)
        payload = self._request(self.actions_getter,
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"period1": int(datetime.combine(query_start, time.min, TAIPEI).timestamp()),
                    "period2": int(datetime.combine(end + timedelta(days=1), time.min, TAIPEI).timestamp()),
                    "interval": "1d", "events": "div,splits"})
        chart = payload.get("chart", {})
        if chart.get("error") or len(chart.get("result") or []) != 1:
            raise ValueError("research_corporate_action_response_invalid")
        data = chart["result"][0]
        meta = data.get("meta", {})
        observed = datetime.fromtimestamp(meta.get("regularMarketTime", 0), TAIPEI)
        covered_dates = [datetime.fromtimestamp(stamp, TAIPEI).date() for stamp in data.get("timestamp", [])]
        if (meta.get("symbol") != symbol or meta.get("exchangeTimezoneName") != "Asia/Taipei"
                or not covered_dates or min(covered_dates) > start or max(covered_dates) < end
                or observed < datetime.combine(end, time(13, 30), TAIPEI)):
            raise ValueError("research_corporate_action_coverage_unverified")
        events = data.get("events", {})
        by_date = {}
        for kind in ("dividends", "splits"):
            for event in events.get(kind, {}).values():
                day = datetime.fromtimestamp(event["date"], TAIPEI).date().isoformat()
                if kind == "dividends":
                    amount = _number(event.get("amount"))
                else:
                    numerator, denominator = _number(event.get("numerator")), _number(event.get("denominator"))
                    amount = numerator / denominator if numerator and numerator > 0 and denominator and denominator > 0 else None
                if amount is None or amount < 0:
                    raise ValueError("research_corporate_action_value_invalid")
                by_date.setdefault(day, {})[kind] = amount
        result = {"events": by_date, "start_date": str(start), "end_date": str(end),
                  "covered_dates": sorted({str(day) for day in covered_dates}),
                  "query_start_date": str(query_start),
                  "observed_at": observed.isoformat()}
        self.cache[key] = result
        return result

    def repair(self, prices_by_symbol, *, required_dates_by_symbol, as_of_date):
        result = {s: [dict(row) for row in rows] for s, rows in prices_by_symbol.items()}
        for symbol, requested in required_dates_by_symbol.items():
            if symbol != "TAIEX" and not is_supported_taiwan_symbol(symbol):
                continue
            required = sorted({d for d in requested if d <= as_of_date})
            original = {str(r["date"])[:10]: r for r in result.get(symbol, [])}
            missing = [d for d in required if not _complete(original.get(str(d), {}))
                       or original[str(d)].get("corporate_actions_checked") is not True]
            if not missing:
                continue
            stage = "official_prices"
            try:
                official = {}
                if self.session is not None and symbol != "TAIEX":
                    for row in get_taiwan_daily_bars(self.session, symbols=[symbol],
                            start_date=min(required), end_date=max(required), final_only=True):
                        official[str(row.trade_date)] = {"date": str(row.trade_date),
                            **{f: float(getattr(row, f)) for f in ("open", "high", "low", "close")},
                            "price_source": row.source_dataset}
                unresolved = [d for d in missing if str(d) not in official]
                if symbol.endswith(".TWO") and unresolved:
                    # Reuse market-level official reports, cached across symbols.
                    from ai_stock_sentinel.daily_radar.market_bar_provider import OfficialTaiwanMarketBarProvider
                    for day in unresolved:
                        key = ("otc", day)
                        if key not in self.cache:
                            self.cache[key] = []
                            def getter(url, **kwargs):
                                return self._request(self.official_getter, url, params=kwargs["params"])
                            self.cache[key] = OfficialTaiwanMarketBarProvider(request_get=getter, max_attempts=1).fetch_market(market="TWO", trade_date=day)
                        for bar in self.cache[key]:
                            if bar.symbol == symbol:
                                official[str(day)] = {"date": str(day), **{f: float(getattr(bar, f)) for f in ("open", "high", "low", "close")}, "price_source": bar.source_dataset}
                else:
                    months = {d.replace(day=1) for d in unresolved}
                    if symbol == "TAIEX":
                        month = min(required).replace(day=1)
                        while month <= max(required):
                            months.add(month)
                            month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
                    for month in sorted(months):
                        for row in self._official_month(symbol, month):
                            if min(required) <= date.fromisoformat(row["date"]) <= max(required):
                                official[row["date"]] = row
                    if symbol == "TAIEX":
                        missing = sorted(set(missing) | {date.fromisoformat(day) for day in official if day not in original})
                conflicts = {}
                for day in original.keys() & official.keys():
                    fields = [f for f in ("open", "high", "low", "close")
                              if _number(original[day].get(f)) is not None and _number(official[day].get(f)) is not None
                              and not math.isclose(float(original[day][f]), float(official[day][f]), rel_tol=1e-7, abs_tol=.005)]
                    if fields:
                        conflicts[day] = fields
                if conflicts:
                    for day, fields in conflicts.items():
                        original[day]["ohlc_conflict_fields"] = sorted(set(original[day].get("ohlc_conflict_fields", [])) | set(fields))
                    raise ValueError("research_official_price_conflict")
                stage = "corporate_actions"
                actions = self._actions(symbol, min(required), max(required)) if symbol != "TAIEX" else None
                if symbol != "TAIEX" and actions is None:
                    raise ValueError("research_corporate_action_coverage_unverified")
                if actions and any(str(day) not in actions["covered_dates"]
                                   for day in missing if _complete(official.get(str(day), {}))):
                    raise ValueError("research_corporate_action_coverage_unverified")
                for day in missing:
                    row = official.get(str(day))
                    if row is None or not _complete(row):
                        continue
                    event = actions["events"].get(str(day), {}) if actions else {}
                    original[str(day)] = dict(row) | {"price_basis": "unadjusted_price",
                        "corporate_actions_checked": True, "dividends": event.get("dividends", 0),
                        "stock_splits": event.get("splits", 0),
                        "corporate_action_source": "yahoo_chart_events" if actions else "index_not_applicable",
                        "corporate_action_coverage": actions,
                        "source_date": str(day)}
                self.diagnostics.append({"symbol": symbol, "stage": "recovery", "recovered_dates":
                    [str(d) for d in missing if _complete(original.get(str(d), {})) and original[str(d)].get("corporate_actions_checked") is True]})
            except Exception as exc:
                self.diagnostics.append({"symbol": symbol, "stage": stage, "error_type": type(exc).__name__,
                                         "reason": str(exc) if str(exc).startswith(("research_", "official_research_")) else type(exc).__name__,
                                         "missing_dates": [str(d) for d in missing]})
            result[symbol] = [original[d] for d in sorted(original)]
        return result
