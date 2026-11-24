"""Bounded foreground work, with a separate lease and commit for each claim."""
from datetime import datetime, timedelta
from copy import deepcopy
from time import monotonic
import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from ai_stock_sentinel.daily_radar.collection_transactions import read_only_collection, CollectionMutationError
from ai_stock_sentinel.db.models import RadarJob, RadarWorkItem, RadarSourceSnapshot
from ai_stock_sentinel.daily_radar.work_queue import aware, claim_work, fence, finish_work, LostLease, database_now, retry_delay, ensure_work, occupied_slot, observe_normal_slot
from ai_stock_sentinel.daily_radar.jobs import reconcile_job, prepared_generation, job_status, OWNER_LANES, job_successors
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
    if value in {'research_source_dependency_waiting', 'validation_price_source_unavailable',
        'official_trading_calendar_unavailable',
        'research_source_snapshot_integrity_invalid', 'radar_prepared_run_missing',
        'radar_collection_mutated_domain', 'market_session_identity_invalid',
        'market_bar_identity_or_coverage_invalid', 'background_context_identity_invalid',
        'market_session_evidence_missing_or_invalid', 'research_recovery_budget_exhausted',
        'forward_price_provider_returned_no_data', 'radar_source_reference_invalid',
        'radar_source_recheck_not_supported', 'calendar_evidence_conflict', 'market_session_prepared_revision_changed'}:
        return value
    return 'radar_provider_unavailable'


MAX_VALIDATION_WINDOWS = 8
BATCH_ADMISSION_SECONDS = 45


def advance_job(db, job_id, *, handlers=None, now=None, window_only=False, owner=None, max_windows=1):
    if not 1 <= max_windows <= MAX_VALIDATION_WINDOWS or (max_windows > 1 and owner != 'validation'):
        raise ValueError('radar_window_batch_scope_invalid')
    deadline = monotonic() + BATCH_ADMISSION_SECONDS
    job = db.get(RadarJob, job_id)
    if job is None:
        raise ValueError('radar_job_not_found')
    if (job_successors(db, job) or job.scope.get('scheduler_owner') != owner
            or (owner and set(job.lanes).difference(OWNER_LANES[owner])) or (owner == 'radar' and window_only)):
        raise ValueError('radar_job_owner_mismatch')
    if job.scope.get('inherit_cursor') is not None:
        if not window_only:
            reconcile_job(db, job_id, now=now)
        observation = observe_normal_slot(db, job_id, now)
        return job_status(db, job_id, include_observation=True) | observation | {
            'advanced_count': 0, 'handoff_advanced': not window_only, 'blocked_by_handoff': window_only,
            'advance_slot_busy': not window_only and observation['normal_slot_busy']}
    if not window_only:
        reconcile_job(db, job_id, now=now)
    slot = 'window' if window_only else 'normal'
    claim = claim_work(db, job_id=job_id, now=now, slot=slot, owner=owner)
    db.commit()
    if claim is None:
        busy = occupied_slot(db, job_id, slot, database_now(db, now)) is not None
        if not window_only and not busy:
            reconcile_job(db, job_id, now=now, audit=True)
        status = job_status(db, job_id, include_observation=True) | {'advanced_count': 0, 'advance_slot_busy': busy}
        return status | observe_normal_slot(db, job_id, now)
    advanced = settled = 0
    while claim is not None:
        state = _execute_claim(db, claim, handlers, now)
        advanced += 1
        settled += int(claim.stage == 'window' and state in {'ready', 'terminal'})
        if (advanced >= max_windows or claim.stage != 'window' or state in (None, 'failed_internal', 'superseded')
                or monotonic() >= deadline):
            break
        claim = claim_work(db, job_id=job_id, now=now, slot=slot, owner=owner, windows_only=True)
        if claim is not None and monotonic() >= deadline:
            db.rollback()
            break
        db.commit()
    if not window_only and job.scope.get('inherit_cursor') is not None:
        reconcile_job(db, job_id, now=now)
        observation = observe_normal_slot(db, job_id, now)
        return job_status(db, job_id, include_observation=True) | observation | {
            'advanced_count': advanced, 'settled_windows': settled,
            'handoff_advanced': True, 'advance_slot_busy': observation['normal_slot_busy']}
    if not window_only:
        reconcile_job(db, job_id, now=now, audit=True)
    status = job_status(db, job_id, include_observation=True) | {'advanced_count': advanced, 'settled_windows': settled}
    return status | observe_normal_slot(db, job_id, now)


def _execute_claim(db, claim, handlers, now):
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
            explicit = accepted.get('retry_at')
            if explicit is not None:
                retry_at = max(retry_at, aware(explicit))
            # A recoverable window may diagnose a failed required input without
            # propagating its exception. Keep the source cooldown in that case.
            cooldowns = (aware(datetime.fromisoformat(value)) for value in result['source_dependencies'].values())
            retry_at = max(retry_at, max(cooldowns, default=retry_at))
        finish_work(db, claim, state=state, result=result, reason=accepted.get('reason'), retry_at=retry_at, now=now)
        db.commit()
        return state
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
        return None
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
                due = max(due, aware(explicit))
            source_result = _source_result(handlers)
            if due is not None:
                cooldowns = (datetime.fromisoformat(value) for value in source_result['source_dependencies'].values())
                due = max(due, max(cooldowns, default=due))
            finish_work(db, claim, state=state, reason=reason, retry_at=due,
                result={'error_type': type(exc).__name__, **source_result}, now=now)
            db.commit()
        except LostLease:
            db.rollback()
            return None
        return state
