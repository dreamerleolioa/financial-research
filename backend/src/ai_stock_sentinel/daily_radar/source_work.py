"""Share source request ownership/backoff without holding DB connections over HTTP."""
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import RadarWorkItem
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
from ai_stock_sentinel.daily_radar.work_queue import (
    aware, claim_work, database_now, ensure_work, fence, finish_work, retry_delay,
)

SOURCE_LEASE_SECONDS = 120


class SourceDependencyBusy(ValueError):
    def __init__(self, keys, retry_at):
        super().__init__("research_source_dependency_waiting")
        self.keys, self.retry_at = keys, retry_at
        self.retryable = True


def _provider_group(request):
    if request.get('operation') in {'background-chip-context', 'avwap-history'}:
        return 'provider-chain-finmind'
    host = urlsplit(request["url"]).hostname if "url" in request else "query1.finance.yahoo.com"
    return "yahoo" if host.endswith(".yahoo.com") else "twse" if host.endswith(".twse.com.tw") else "tpex"


def _retry_after(exc, now):
    headers = getattr(exc, "headers", None) or {}
    value = getattr(exc, "retry_after", None) or headers.get("Retry-After")
    if value is None:
        return 0
    try:
        seconds = int(value)
    except (ValueError, TypeError):
        try:
            seconds = int((parsedate_to_datetime(str(value)).astimezone(timezone.utc) - now).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0
    return min(86400, max(0, seconds))


def _reserve(store, request):
    request_hash = fingerprint(request)
    with Session(store.bind, expire_on_commit=False) as db, db.begin():
        now = database_now(db)
        gate = ensure_work(db, identity={"kind": "provider_gate", "provider": _provider_group(request)},
            stage="provider-gate", lane="source", run_date=None,
            scope={"provider": _provider_group(request)}, input_revision=fingerprint({"provider": _provider_group(request)}), retry_at=now)
        source = ensure_work(db, identity={"kind": "source_capture", "request_hash": request_hash},
            stage="source-capture", lane="source", run_date=None, scope={"request": request},
            input_revision=request_hash, retry_at=now, dependencies=[gate.work_key])
        keys = [gate.work_key, source.work_key]
        # Always provider gate -> source request, including finish/failure.
        locked = []
        for row in (gate, source):
            current = db.scalar(select(RadarWorkItem).where(RadarWorkItem.id == row.id)
                .with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if current is None:
                return None, keys, now + timedelta(seconds=10)
            locked.append(current)
        for row in locked:
            if row.state == "ready" and row.retry_at is not None and aware(row.retry_at) <= now:
                row.state = "pending"
            if (row.state == "running" and aware(row.lease_expires_at) > now
                    or row.state not in {"pending", "waiting_provider", "running"}
                    or row.retry_at is not None and aware(row.retry_at) > now):
                retry_at = row.lease_expires_at if row.state == "running" else row.retry_at
                return None, keys, aware(retry_at) if retry_at else now + timedelta(minutes=1)
        db.flush()
        gate_claim = claim_work(db, work_id=gate.id, lease_seconds=SOURCE_LEASE_SECONDS)
        source_claim = claim_work(db, work_id=source.id, lease_seconds=SOURCE_LEASE_SECONDS)
        if gate_claim is None or source_claim is None:
            raise ValueError("research_source_claim_failed")
        return (gate_claim, source_claim), keys, None


def capture_source(store, *, request, fetch):
    reservation, keys, retry_at = _reserve(store, request)
    if reservation is None:
        # The winning request may have committed between our cache lookup and
        # reservation. Consume that evidence rather than wait for its TTL.
        if (cached := store._lookup(request)) is not None:
            return cached
        store.dependencies.update({key: retry_at.isoformat() for key in keys})
        raise SourceDependencyBusy(keys, retry_at)
    gate_claim, source_claim = reservation
    try:
        payload = fetch()
        store.encode_payload(payload)
    except Exception as exc:
        with Session(store.bind) as db, db.begin():
            # A late failing response also cannot reset another owner's gate.
            fence(db, gate_claim)
            row = fence(db, source_claim)
            now = database_now(db)
            delay = retry_delay(row.attempt_count, retry_after=_retry_after(exc, now))
            retry_at = now + timedelta(seconds=delay)
            local_failure = (getattr(exc, "local_budget_exhausted", False)
                or isinstance(exc, RuntimeError) and str(exc) == "forward_price_provider_returned_no_data")
            finish_work(db, gate_claim, state="ready" if local_failure else "waiting_provider",
                reason=type(exc).__name__, retry_at=now if local_failure else retry_at)
            finish_work(db, source_claim, state="waiting_provider", reason=type(exc).__name__, retry_at=retry_at)
        store.dependencies.update({key: retry_at.isoformat() for key in keys})
        raise
    with Session(store.bind) as db, db.begin():
        fence(db, gate_claim)
        fence(db, source_claim)
        result = store.record(request, payload, db=db)
        now = database_now(db)
        finish_work(db, gate_claim, state="ready", result={"provider": _provider_group(request)}, retry_at=now)
        finish_work(db, source_claim, state="ready", result={"source": result[1]}, retry_at=now + store.ttl)
    store.remember(result)
    return result
