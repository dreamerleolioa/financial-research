"""Immutable transport evidence shared across Radar requests.

A captured response is not coverage proof. Every consumer still runs its exact
schema/date/symbol/completeness checks before using prices or declaring no event.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from importlib.metadata import version
from urllib.parse import urlencode, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import RadarSourceSnapshot

PARSER_VERSION = "radar-source-capture-v1"
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def snapshots_enabled():
    return os.getenv("DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED", "false").lower() == "true"


def store_for_session(db):
    if snapshots_enabled() and isinstance(db, Session):
        return SourceSnapshotStore(db.get_bind())
    return None


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class SourceSnapshotStore:
    def __init__(self, bind, *, clock=None, ttl=timedelta(minutes=10)):
        self.bind, self.clock, self.ttl = bind, clock or (lambda: datetime.now(timezone.utc)), ttl
        self.seen = {}
        self.captured_count = 0
        self.dependencies = {}

    def capture(self, *, provider, url, params, fetch, parser_version=PARSER_VERSION):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in {
                "www.twse.com.tw", "www.tpex.org.tw", "query1.finance.yahoo.com"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.port not in (None, 443)):
            raise ValueError("research_source_snapshot_url_invalid")
        if any(any(secret in str(key).lower() for secret in ("token", "authorization", "password", "api_key"))
               for key in params):
            raise ValueError("research_source_snapshot_secret_parameter")
        request = {"provider": provider, "url": url, "params": params, "parser_version": parser_version}
        return self._capture_request(request, fetch=fetch)

    @staticmethod
    def _sdk_request(operation, params):
        if operation not in {"research-unadjusted-history", "technical-adjusted-history", "forward-adjusted-history"}:
            raise ValueError("research_source_snapshot_operation_invalid")
        return {"provider": "yfinance", "operation": operation, "params": params,
                "sdk_version": version("yfinance"), "parser_version": PARSER_VERSION}

    def lookup_sdk(self, *, operation, params):
        request = self._sdk_request(operation, params)
        if (exact := self._lookup(request)) is not None:
            return exact
        if operation not in {"research-unadjusted-history", "forward-adjusted-history"}:
            return None
        now = _utc(self.clock())
        with Session(self.bind) as db:
            rows = db.scalars(select(RadarSourceSnapshot).where(
                RadarSourceSnapshot.sdk_operation == operation, RadarSourceSnapshot.sdk_symbol == params["symbol"],
                RadarSourceSnapshot.sdk_start_date <= date.fromisoformat(params["start_date"]),
                RadarSourceSnapshot.sdk_end_date >= date.fromisoformat(params["end_date"]),
                RadarSourceSnapshot.captured_at <= now, RadarSourceSnapshot.expires_at > now,
            ).order_by(RadarSourceSnapshot.captured_at.desc(), RadarSourceSnapshot.id.desc()))
            for row in rows:
                recorded = row.request
                args = recorded.get("params") or {}
                if (recorded.get("sdk_version") == request["sdk_version"]
                        and recorded.get("parser_version") == request["parser_version"]
                        and recorded.get("operation") == operation and args.get("symbol") == params["symbol"]
                        and args.get("auto_adjust") is params["auto_adjust"] and args.get("actions") is params["actions"]
                        and args.get("start_date", "9999") <= params["start_date"]
                        and args.get("end_date", "0000") >= params["end_date"]):
                    result = self._read(row)
                    self.seen[row.request_hash] = deepcopy(result[1])
                    return result
        return None

    def capture_sdk(self, *, operation, params, fetch, reuse=True):
        return self._capture_request(self._sdk_request(operation, params), fetch=fetch, reuse=reuse)

    def capture_operation(self, *, operation, params, fetch):
        if operation not in {'background-chip-context', 'technical-refresh-payload', 'market-index-context', 'avwap-history'}:
            raise ValueError('radar_provider_operation_invalid')
        policy = {key: os.getenv(key, '') for key in ('DAILY_RADAR_TW_OHLCV_PROVIDER_MODE',
            'DAILY_RADAR_BACKGROUND_PROVIDER_MODE', 'DAILY_RADAR_LENDING_PROVIDER_MODE', 'DAILY_RADAR_AVWAP_PROVIDER_MODE')}
        request = {'provider': 'radar-provider-chain', 'operation': operation, 'params': params,
            'policy': policy, 'parser_version': PARSER_VERSION,
            'sdk_version': version('yfinance') if operation in {'technical-refresh-payload', 'market-index-context'} else None}
        return self._capture_request(request, fetch=fetch)

    def _lookup(self, request):
        request_hash, now = fingerprint(request), _utc(self.clock())
        with Session(self.bind) as db:
            cached = db.scalar(select(RadarSourceSnapshot).where(
                RadarSourceSnapshot.request_hash == request_hash,
                RadarSourceSnapshot.captured_at <= now,
                RadarSourceSnapshot.expires_at > now,
            ).order_by(RadarSourceSnapshot.captured_at.desc(), RadarSourceSnapshot.id.desc()).limit(1))
            if cached is not None:
                result = self._read(cached)
                self.seen[cached.request_hash] = deepcopy(result[1])
                return result
        return None

    def _capture_request(self, request, *, fetch, reuse=True):
        if reuse and (cached := self._lookup(request)) is not None:
            return cached
        from ai_stock_sentinel.daily_radar.work_queue import durable_enabled
        if durable_enabled() and request.get("operation") != "technical-adjusted-history":
            from ai_stock_sentinel.daily_radar.source_work import capture_source
            return capture_source(self, request=request, fetch=fetch)
        # No archive transaction remains open while calling the provider.
        payload = fetch()
        return self.record(request, payload)

    @staticmethod
    def encode_payload(payload):
        if not isinstance(payload, (dict, list)):
            raise ValueError("research_source_snapshot_payload_invalid")
        encoded = canonical_json(payload).encode()
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("research_source_snapshot_too_large")
        return encoded

    def record(self, request, payload, *, db=None):
        encoded = self.encode_payload(payload)
        captured_at = _utc(self.clock())
        def write(db):
            projection = {}
            if request.get("operation") in {"research-unadjusted-history", "forward-adjusted-history"}:
                params = request["params"]
                projection = {"sdk_operation": request["operation"], "sdk_symbol": params["symbol"],
                    "sdk_start_date": date.fromisoformat(params["start_date"]),
                    "sdk_end_date": date.fromisoformat(params["end_date"])}
            row = RadarSourceSnapshot(request_hash=fingerprint(request), request=request,
                payload=deepcopy(payload), payload_hash=sha256(encoded).hexdigest(),
                captured_at=captured_at, expires_at=captured_at + self.ttl, **projection)
            db.add(row)
            db.flush()
            return self._read(row)
        if db is None:
            with Session(self.bind, expire_on_commit=False) as owner, owner.begin():
                result = write(owner)
            self.remember(result)
        else:
            result = write(db)
        return result

    def remember(self, result):
        self.captured_count += 1
        self.seen[result[1]["request_hash"]] = deepcopy(result[1])

    @staticmethod
    def _read(row):
        if fingerprint(row.payload) != row.payload_hash or fingerprint(row.request) != row.request_hash:
            raise ValueError("research_source_snapshot_integrity_invalid")
        request = row.request
        source = {"snapshot_id": row.id, "snapshot_sha256": row.payload_hash,
            "request_hash": row.request_hash,
            "captured_at": _utc(row.captured_at).isoformat(timespec="milliseconds"),
            "expires_at": _utc(row.expires_at).isoformat(timespec="milliseconds"),
            "parser_version": request["parser_version"], "capture_state": "captured"}
        if "url" in request:
            # JSONB reorders object keys. Keep the canonical response-first URL
            # required by existing official-calendar proof validation.
            items = sorted(request["params"].items(), key=lambda item: (item[0] != "response", item[0]))
            source["source_url"] = request["url"] + "?" + urlencode(items)
        else:
            source.update(provider=request["provider"], source_operation=request["operation"],
                          sdk_version=request.get("sdk_version"))
        return deepcopy(row.payload), source
