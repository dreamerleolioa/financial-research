"""Immutable transport evidence shared across Radar requests.

A captured response is not coverage proof. Every consumer still runs its exact
schema/date/symbol/completeness checks before using prices or declaring no event.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
        if operation not in {"research-unadjusted-history", "technical-adjusted-history"}:
            raise ValueError("research_source_snapshot_operation_invalid")
        return {"provider": "yfinance", "operation": operation, "params": params,
                "sdk_version": version("yfinance"), "parser_version": PARSER_VERSION}

    def lookup_sdk(self, *, operation, params):
        return self._lookup(self._sdk_request(operation, params))

    def capture_sdk(self, *, operation, params, fetch, reuse=True):
        return self._capture_request(self._sdk_request(operation, params), fetch=fetch, reuse=reuse)

    def _lookup(self, request):
        request_hash, now = fingerprint(request), _utc(self.clock())
        with Session(self.bind) as db:
            cached = db.scalar(select(RadarSourceSnapshot).where(
                RadarSourceSnapshot.request_hash == request_hash,
                RadarSourceSnapshot.captured_at <= now,
                RadarSourceSnapshot.expires_at > now,
            ).order_by(RadarSourceSnapshot.captured_at.desc(), RadarSourceSnapshot.id.desc()).limit(1))
            if cached is not None:
                return self._read(cached)
        return None

    def _capture_request(self, request, *, fetch, reuse=True):
        if reuse and (cached := self._lookup(request)) is not None:
            return cached
        # No archive transaction remains open while calling the provider.
        payload = fetch()
        if not isinstance(payload, dict):
            raise ValueError("research_source_snapshot_payload_invalid")
        encoded = canonical_json(payload).encode()
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("research_source_snapshot_too_large")
        captured_at = _utc(self.clock())
        with Session(self.bind, expire_on_commit=False) as db, db.begin():
            row = RadarSourceSnapshot(request_hash=fingerprint(request), request=request,
                payload=deepcopy(payload), payload_hash=sha256(encoded).hexdigest(),
                captured_at=captured_at, expires_at=captured_at + self.ttl)
            db.add(row)
            db.flush()
            result = self._read(row)
        return result

    @staticmethod
    def _read(row):
        if fingerprint(row.payload) != row.payload_hash or fingerprint(row.request) != row.request_hash:
            raise ValueError("research_source_snapshot_integrity_invalid")
        request = row.request
        source = {"snapshot_id": row.id, "snapshot_sha256": row.payload_hash,
            "captured_at": _utc(row.captured_at).isoformat(timespec="milliseconds"),
            "parser_version": request["parser_version"], "capture_state": "captured"}
        if "url" in request:
            # JSONB reorders object keys. Keep the canonical response-first URL
            # required by existing official-calendar proof validation.
            items = sorted(request["params"].items(), key=lambda item: (item[0] != "response", item[0]))
            source["source_url"] = request["url"] + "?" + urlencode(items)
        else:
            source.update(provider=request["provider"], source_operation=request["operation"],
                          sdk_version=request["sdk_version"])
        return deepcopy(row.payload), source
