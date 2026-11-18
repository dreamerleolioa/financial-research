"""Short claims and fenced commits for bounded Radar work."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
import os

from sqlalchemy import select, update, and_, or_, func, type_coerce, DateTime
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ai_stock_sentinel.db.models import RadarJob, RadarWorkItem, RadarWorkAttempt, RadarJobWork
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

CLAIMABLE = ("pending", "waiting_provider")
DONE = ("ready", "terminal", "superseded")


def durable_enabled():
    return os.getenv("DAILY_RADAR_DURABLE_EXECUTION_ENABLED", "false").lower() == "true"


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def database_now(db, override=None):
    if override is not None:
        return aware(override)
    if db.get_bind().dialect.name == "postgresql":
        return aware(db.scalar(select(func.clock_timestamp())))
    return utcnow()


class LostLease(ValueError):
    pass


@dataclass(frozen=True)
class WorkClaim:
    work_id: int
    token: str
    attempt_id: int
    input_revision: str
    stage: str
    lane: str
    run_date: date | None
    scope: dict


def insert(db, model):
    return (pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert)(model)


def work_insert_values(*, identity, stage, lane, run_date, scope, input_revision,
                       retry_at=None, state="pending", dependencies=(), priority=50, not_before=None):
    return dict(work_key=fingerprint(identity), stage=stage, lane=lane, run_date=run_date,
                identity=deepcopy(identity), scope=deepcopy(scope), input_revision=input_revision,
                state=state, retry_at=retry_at or utcnow(), not_before=not_before or retry_at or utcnow(),
                dependencies=list(dependencies), priority=priority, attempt_count=0)


def ensure_work(db, *, identity, stage, lane, run_date, scope, input_revision,
                retry_at=None, state="pending", dependencies=(), priority=50, not_before=None):
    values = work_insert_values(identity=identity, stage=stage, lane=lane, run_date=run_date,
        scope=scope, input_revision=input_revision, retry_at=retry_at, state=state,
        dependencies=dependencies, priority=priority, not_before=not_before)
    db.execute(insert(db, RadarWorkItem).values(values).on_conflict_do_nothing(index_elements=["work_key"]))
    return db.scalar(select(RadarWorkItem).where(RadarWorkItem.work_key == values["work_key"]))


def _occupied_slot_query(job_id, slot, now):
    """The attempt owns its slot even if its work is shared across dates."""
    owner = func.coalesce(RadarWorkAttempt.scope['slot_job_id'].as_integer(),
                          RadarWorkAttempt.scope['advance_job_id'].as_integer())
    query = (select(RadarWorkItem.lease_expires_at)
             .join(RadarWorkAttempt, RadarWorkAttempt.lease_token == RadarWorkItem.lease_token)
             .where(RadarWorkItem.state == 'running', RadarWorkItem.lease_expires_at > now,
                    func.coalesce(RadarWorkAttempt.scope['advance_slot'].as_string(), 'normal') == slot,
                    or_(owner == job_id, and_(owner.is_(None),
                        select(RadarJobWork.work_id).where(RadarJobWork.work_id == RadarWorkItem.id,
                            RadarJobWork.job_id == job_id, RadarJobWork.active.is_(True)).exists()))))
    return query.order_by(RadarWorkItem.lease_expires_at).limit(1)


def unsplit_job(db):
    condition = RadarJob.scope['successors'].as_string().is_(None)
    if db.get_bind().dialect.name == 'sqlite':
        # A pre-cutover SQLite writer can restore its stale JSON scope. Only
        # this fallback needs a second lookup; PostgreSQL locks the parent.
        from sqlalchemy.orm import aliased
        child = aliased(RadarJob)
        condition &= ~select(child.id).where(
            child.scope['inherited_from'].as_integer() == RadarJob.id).correlate(RadarJob).exists()
    return condition


def occupied_slot(db, job_id, slot, now):
    return db.scalar(_occupied_slot_query(job_id, slot, now))


def observe_normal_slot(db, job_id, now=None):
    # The lease and its timestamp are from the same statement snapshot.
    observed_at = (func.statement_timestamp() if db.get_bind().dialect.name == 'postgresql'
                   else type_coerce(func.strftime('%Y-%m-%d %H:%M:%f', 'now'), DateTime()))
    deadline = _occupied_slot_query(job_id, 'normal', aware(now) if now else observed_at).scalar_subquery()
    observed, occupied = db.execute(select(observed_at, deadline)).one()
    return {'normal_slot_busy': occupied is not None, 'normal_observed_at': aware(observed).isoformat()}


def claim_work(db, *, job_id=None, work_id=None, now=None, lease_seconds=240, slot=None, owner=None):
    now = database_now(db, now)
    due = or_(and_(RadarWorkItem.state.in_(CLAIMABLE), RadarWorkItem.retry_at <= now),
              and_(RadarWorkItem.state == "running", RadarWorkItem.lease_expires_at <= now))
    query = select(RadarWorkItem).where(due, RadarWorkItem.not_before <= now)
    if owner:
        from ai_stock_sentinel.daily_radar.jobs import OWNER_LANES
        lanes = (*OWNER_LANES['validation'], 'validation') if owner == 'validation' else OWNER_LANES[owner]
        query = query.where(RadarWorkItem.lane.in_(lanes))
    if slot is not None:
        # Serialize the claim decision while separate leases fence each work item.
        db.scalar(select(RadarJob.id).where(RadarJob.id == job_id).with_for_update())
        if occupied_slot(db, job_id, slot, now) is not None:
            return None
        if slot == 'window':
            query = query.where(RadarWorkItem.stage == 'window')
    if job_id is not None:
        eligible_job = select(RadarJob.id).where(RadarJob.id == job_id,
            unsplit_job(db), RadarJob.scope['inherit_cursor'].as_integer().is_(None))
        if owner:
            eligible_job = eligible_job.where(RadarJob.scope['scheduler_owner'].as_string() == owner)
        else:
            eligible_job = eligible_job.where(RadarJob.scope['scheduler_owner'].as_string().is_(None))
        membership = select(RadarJobWork.work_id).where(RadarJobWork.job_id == job_id,
            RadarJobWork.active.is_(True), eligible_job.exists())
        query = query.where(RadarWorkItem.id.in_(membership))
    else:
        query = query.where(RadarWorkItem.id == work_id)
    row = db.scalar(query.order_by(RadarWorkItem.priority, RadarWorkItem.retry_at, RadarWorkItem.id)
        .with_for_update(skip_locked=True, of=RadarWorkItem).limit(1).execution_options(populate_existing=True))
    if row is None:
        return None
    # SQLite has no row locks. A compare-and-set prevents duplicate successful
    # claims there; PostgreSQL skips held row locks before reaching this point.
    token = uuid4().hex
    accept = update(RadarWorkItem).where(RadarWorkItem.id == row.id, due)
    if job_id is not None:
        accept = accept.where(RadarWorkItem.id.in_(membership))
    changed = db.execute(accept.values(
        state="running", lease_token=token, lease_expires_at=now + timedelta(seconds=lease_seconds),
        attempt_count=RadarWorkItem.attempt_count + 1, updated_at=now).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        return None
    db.execute(update(RadarWorkAttempt).where(RadarWorkAttempt.work_id == row.id,
        RadarWorkAttempt.state == "running").values(state="lost_lease", finished_at=now).execution_options(synchronize_session=False))
    attempt = RadarWorkAttempt(work_id=row.id, lease_token=token, input_revision=row.input_revision,
        started_at=now, state="running", scope=deepcopy(row.scope) | ({"advance_slot": slot, "advance_job_id": job_id} if slot else {}))
    db.add(attempt)
    db.flush()
    return WorkClaim(row.id, token, attempt.id, row.input_revision, row.stage, row.lane, row.run_date, deepcopy(row.scope))


def fence(db, claim, *, now=None):
    row = db.scalar(select(RadarWorkItem).where(RadarWorkItem.id == claim.work_id)
        .with_for_update().execution_options(populate_existing=True))
    now = database_now(db, now)
    if (row is None or row.state != "running" or row.lease_token != claim.token
            or row.input_revision != claim.input_revision or aware(row.lease_expires_at) <= now):
        raise LostLease("radar_work_lease_lost")
    return row


def finish_work(db, claim, *, state, result=None, reason=None, retry_at=None, now=None):
    row = fence(db, claim, now=now)
    now = database_now(db, now)
    row.state, row.result, row.reason, row.retry_at = state, deepcopy(result or {}), reason, retry_at
    row.lease_token = row.lease_expires_at = None
    row.updated_at = now
    attempt = db.get(RadarWorkAttempt, claim.attempt_id)
    attempt.state, attempt.finished_at = state, now
    attempt.result, attempt.reason = deepcopy(result or {}), reason
    db.flush()
    return row


def retry_delay(attempt_count, *, progressed=False, retry_after=None):
    base = 60 if progressed else min(3600, 60 * 2 ** min(max(0, attempt_count - 1), 6))
    return max(base, retry_after or 0)
