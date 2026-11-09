"""Allowlisted provider adapters; source leases surround the actual network call."""
from datetime import date
import time

from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
from ai_stock_sentinel.calibration.research_prices import ResearchSourceError
from ai_stock_sentinel.data_sources.official_http import official_request_get


class CachedForwardPrices:
    def __init__(self, db, *, store, provider=None):
        if store is None:
            raise ValueError("radar_source_snapshots_required")
        self.db, self.source_store = db, store
        self.provider = provider or YFinanceForwardPriceProvider()

    def fetch(self, symbols, *, start_date, end_date):
        if self.db.new or self.db.dirty or self.db.deleted:
            raise ValueError("radar_network_with_pending_writes")
        self.db.rollback()
        result = {}
        for symbol in sorted(set(symbols)):
            params = {"symbol": symbol, "start_date": str(start_date), "end_date": str(end_date),
                      "auto_adjust": True, "actions": False}
            captured = self.source_store.lookup_sdk(operation="forward-adjusted-history", params=params)
            if captured is None:
                def fetch(symbol=symbol):
                    rows = self.provider.fetch([symbol], start_date=start_date, end_date=end_date).get(symbol)
                    if not rows:
                        raise RuntimeError("forward_price_provider_returned_no_data")
                    return {"rows": list(rows)}
                captured = self.source_store.capture_sdk(operation="forward-adjusted-history", params=params, fetch=fetch)
            payload, proof = captured
            result[symbol] = [dict(row) | {"source_capture": proof} for row in payload["rows"]
                             if start_date <= date.fromisoformat(row["date"]) <= end_date]
        return result


class CapturedOfficialGetter:
    def __init__(self, store, getter=None, *, clock=time.monotonic, max_seconds=75, max_requests=48):
        self.store, self.getter = store, getter or official_request_get
        self.official_transport = getter is None or getter is official_request_get
        self.clock, self.max_seconds, self.max_requests = clock, max_seconds, max_requests
        self.reset_budget()

    def reset_budget(self):
        self.deadline, self.request_count = self.clock() + self.max_seconds, 0

    def _budget(self):
        if self.clock() >= self.deadline or self.request_count >= self.max_requests:
            error = TimeoutError('research_recovery_budget_exhausted')
            error.local_budget_exhausted = True
            raise error

    def __call__(self, url, *, params=None, **kwargs):
        from urllib.parse import urlsplit
        params = params or {}
        provider = {'www.tpex.org.tw': 'tpex', 'query1.finance.yahoo.com': 'yahoo_chart_events'}.get(urlsplit(url).hostname, 'twse')
        def fetch():
            self._budget()
            self.request_count += 1
            options = dict(kwargs)
            if self.official_transport:
                options['max_attempts'] = 1
            options['timeout'] = min(float(options.get('timeout', 30)), max(0.01, self.deadline - self.clock()))
            response = self.getter(url, params=params, **options)
            if self.clock() >= self.deadline:
                error = TimeoutError('research_recovery_budget_exhausted')
                error.local_budget_exhausted = True
                raise error
            status = getattr(response, "status_code", 200)
            if status >= 400:
                raise ResearchSourceError("research_source_http_error", retryable=status in {408, 425, 429} or status >= 500,
                    http_status=status, retry_after=(getattr(response, "headers", {}) or {}).get("Retry-After"))
            return response.json() if hasattr(response, "json") else response
        return self.store.capture(provider=provider, url=url, params=params, fetch=fetch)[0]
