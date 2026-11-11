"""One bounded claim/collect/accept cycle. No background work after HTTP return."""
from datetime import timedelta
from copy import deepcopy
import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from ai_stock_sentinel.daily_radar.collection_transactions import read_only_collection, CollectionMutationError
from ai_stock_sentinel.db.models import RadarWorkItem, RadarSourceSnapshot
from ai_stock_sentinel.daily_radar.work_queue import claim_work, fence, finish_work, LostLease, database_now, retry_delay, ensure_work
from ai_stock_sentinel.daily_radar.jobs import reconcile_job, prepared_generation, job_status
from ai_stock_sentinel.daily_radar.validation_preparation import reserve_validation_commit
from ai_stock_sentinel.daily_radar.readiness import recovery_action
from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers, prepared
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint


class SourceRevisionChanged(LostLease):
    """Safe rejection metadata; never persist arbitrary exception text."""
    def __init__(self, request_hash, expected, actual):
        super().__init__('radar_source_revision_changed')
        def digest(value):
            return value if isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) else None
        self.rejection = {'reason': 'radar_source_revision_changed', 'request_hash': digest(request_hash),
            'expected_snapshot_sha256': digest(expected), 'actual_snapshot_sha256': digest(actual)}


def _source_result(handlers):
    store = getattr(handlers, 'store', None)
    return {'source_observations': deepcopy(list(store.seen.values())) if store else [],
            'source_captured_count': store.captured_count if store else 0,
            'source_dependencies': deepcopy(dict(store.dependencies)) if store else {}}


def fence_sources(db, store):
    if store is None:
        return
    for key, proof in sorted(store.seen.items()):
        work_key = fingerprint({'kind': 'source_capture', 'request_hash': key})
        previous = db.scalar(select(RadarSourceSnapshot).where(RadarSourceSnapshot.request_hash == key)
            .order_by(RadarSourceSnapshot.captured_at.desc(), RadarSourceSnapshot.id.desc()).limit(1))
        if previous is None:
            raise SourceRevisionChanged(key, proof['snapshot_sha256'], None)
        ensure_work(db, identity={'kind': 'source_capture', 'request_hash': key}, stage='source-capture',
            lane='source', run_date=None, scope={'request': previous.request}, input_revision=key,
            retry_at=database_now(db))
        db.scalar(select(RadarWorkItem).where(RadarWorkItem.work_key == work_key).with_for_update(read=True))
        latest = db.scalar(select(RadarSourceSnapshot).where(RadarSourceSnapshot.request_hash == key)
            .order_by(RadarSourceSnapshot.captured_at.desc(), RadarSourceSnapshot.id.desc()).limit(1))
        if latest is None or latest.payload_hash != proof['snapshot_sha256']:
            raise SourceRevisionChanged(key, proof['snapshot_sha256'], latest.payload_hash if latest else None)


def _reason(exc):
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        return exc.detail.get('code', 'radar_domain_prerequisite_missing') if isinstance(exc.detail, dict) else 'radar_domain_prerequisite_missing'
    value = str(exc)
    # Only stable machine reasons may enter shared summaries. Never persist
    # provider bodies, URLs, tokens or private symbols from exception text.
    if value in {'research_source_dependency_waiting', 'official_trading_calendar_unavailable',
        'research_source_snapshot_integrity_invalid', 'radar_prepared_run_missing',
        'radar_collection_mutated_domain', 'market_session_identity_invalid',
        'market_bar_identity_or_coverage_invalid', 'background_context_identity_invalid',
        'market_session_evidence_missing_or_invalid', 'research_recovery_budget_exhausted',
        'forward_price_provider_returned_no_data', 'radar_source_reference_invalid',
        'radar_source_recheck_not_supported', 'calendar_evidence_conflict', 'market_session_prepared_revision_changed'}:
        return value
    return 'radar_provider_unavailable'


def advance_job(db, job_id, *, handlers=None, now=None):
    reconcile_job(db, job_id, now=now)
    claim = claim_work(db, job_id=job_id, now=now)
    db.commit()
    if claim is None:
        reconcile_job(db, job_id, now=now, audit=True)
        return job_status(db, job_id) | {'advanced_count': 0}
    try:
        # Domain acceptance owns db. Default collectors get a separate reader;
        # custom handlers using db remain subject to the same read-only guard.
        with Session(db.get_bind(), autoflush=False, close_resets_only=False) as reader:
            handlers = handlers or WorkHandlers(reader)
            with read_only_collection(db), read_only_collection(reader):
                collected = handlers.collect(claim)
        # Same relation order as reviewed historical repair. Source network has
        # already completed; domain effects and lease completion are atomic.
        reserve_validation_commit(db)
        if claim.stage == 'scoring':
            from ai_stock_sentinel.daily_radar.input_manifests import serialize_publication
            serialize_publication(db, market='TW', run_date=claim.run_date)
        fence(db, claim, now=now)
        fence_sources(db, getattr(handlers, 'store', None))
        if claim.scope.get('generation'):
            row = prepared(db, claim.run_date)
            if claim.stage not in {'lending', 'full-margin'}:
                db.refresh(row, with_for_update=True)
            if prepared_generation(row) != claim.scope['generation']:
                raise LostLease('radar_prepared_generation_changed')
        accepted = collected.apply(db)
        state = accepted.get('state', 'ready')
        result = dict(accepted.get('result') or {})
        result.update(_source_result(handlers))
        work = db.get(RadarWorkItem, claim.work_id)
        retry_at = None
        if state == 'waiting_provider':
            old_sources = {p['request_hash']: p['snapshot_sha256'] for p in (work.result or {}).get('source_observations', [])}
            progressed = any(old_sources.get(p['request_hash']) != p['snapshot_sha256']
                for p in result['source_observations'])
            retry_at = database_now(db, now) + timedelta(seconds=retry_delay(work.attempt_count, progressed=progressed))
            if accepted.get('retry_at') is not None:
                from ai_stock_sentinel.daily_radar.work_queue import aware
                retry_at = max(retry_at, aware(accepted['retry_at']))
        finish_work(db, claim, state=state, result=result, reason=accepted.get('reason'), retry_at=retry_at, now=now)
        db.commit()
    except LostLease as exc:
        db.rollback()
        # New input generation invalidates the old claim immediately; expiry
        # recovery must not resurrect it. Another owner remains untouched.
        try:
            changed = str(exc) in {'radar_prepared_generation_changed', 'radar_window_inputs_changed'}
            result = _source_result(handlers)
            if isinstance(exc, SourceRevisionChanged):
                result['source_rejection'] = deepcopy(exc.rejection)
            finish_work(db, claim, state='superseded' if changed else 'waiting_provider',
                result=result,
                reason='radar_inputs_changed' if changed else 'radar_source_lease_lost',
                retry_at=None if changed else database_now(db, now) + timedelta(minutes=1), now=now)
            db.commit()
        except LostLease:
            db.rollback()
    except Exception as exc:
        db.rollback()
        reason = _reason(exc)
        internal = isinstance(exc, (CollectionMutationError, TypeError, AttributeError, ImportError, AssertionError))
        state = 'failed_internal' if internal else 'needs_review' if recovery_action(reason) == 'needs_review' else 'waiting_provider'
        try:
            work = fence(db, claim, now=now)
            due = database_now(db, now) + timedelta(seconds=retry_delay(work.attempt_count)) if state == 'waiting_provider' else None
            explicit = getattr(exc, 'retry_at', None)
            if explicit is not None and due is not None:
                from ai_stock_sentinel.daily_radar.work_queue import aware
                due = max(due, aware(explicit))
            finish_work(db, claim, state=state, reason=reason, retry_at=due,
                result={'error_type': type(exc).__name__, **_source_result(handlers)}, now=now)
            db.commit()
        except LostLease:
            db.rollback()
    reconcile_job(db, job_id, now=now, audit=True)
    return job_status(db, job_id) | {'advanced_count': 1}
