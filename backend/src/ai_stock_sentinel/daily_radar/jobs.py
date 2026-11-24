"""Durable Radar intent and dependency planning. No provider I/O here."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import and_, func, select, update, or_, text, tuple_
from ai_stock_sentinel.db.models import RadarJob, RadarWorkItem, RadarJobWork, RadarWorkAttempt, DailyRadarPreparedRun
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
from ai_stock_sentinel.daily_radar.work_queue import ensure_work, insert, database_now, aware, work_insert_values, unsplit_job

POLICY = 'radar-durable-v1'
LANES = ('publication', 'signal_close', 'next_open', 'auxiliary')
OWNER_LANES = {'radar': ('publication', 'auxiliary'), 'validation': ('signal_close', 'next_open')}
TIMES = {'market-session': (17, 30), 'institutional': (17, 30), 'market-bars': (17, 45),
 'prepare': (18, 0), 'avwap': (19, 0), 'lending': (20, 0), 'full-margin': (21, 30),
 'ohlcv': (22, 30), 'managed-raw': (22, 30), 'market-context': (23, 30),
 'calendar': (23, 50), 'window': (23, 50), 'scoring': (0, 30)}


def stage_time(run_date, stage):
    day = run_date + timedelta(days=1) if stage == 'scoring' else run_date
    return datetime.combine(day, time(*TIMES[stage]), ZoneInfo('Asia/Taipei')).astimezone(timezone.utc)


def prepared_generation(prepared):
    return fingerprint({'id': prepared.id, 'symbols': prepared.selected_symbols,
        'prepare': (prepared.step_statuses or {}).get('prepare-universe')}) if prepared else None


def create_job(db, *, run_date, lanes=LANES, now=None, recheck_publication=False, owner=None):
    if not isinstance(run_date, date) or not lanes or set(lanes) - set(LANES):
        raise ValueError('radar_job_scope_invalid')
    now, lanes = database_now(db, now), sorted(set(lanes))
    if owner and (owner not in OWNER_LANES or set(lanes).difference(OWNER_LANES[owner])):
        raise ValueError('radar_job_owner_mismatch')
    key = fingerprint({'policy': POLICY, 'market': 'TW', 'run_date': str(run_date), 'lanes': lanes})
    lock_date(db, run_date)
    same_day = db.scalars(select(RadarJob).where(RadarJob.market == 'TW', RadarJob.run_date == run_date)
                          .order_by(RadarJob.id)).all()
    adopted = [job for job in same_day if job.scope.get('scheduler_owner')]
    if not owner and any(set(job.lanes).intersection(lanes) for job in adopted):
        raise ValueError('radar_job_owner_mismatch')
    if owner:
        matches = [job for job in adopted if job.lanes == lanes]
        existing = next((job for job in matches if job.scope.get('dated_registration')), None)
        if existing is None and matches:
            existing = matches[0]
        if existing:
            key = existing.job_key
        else:
            for previous in same_day:
                if set(previous.lanes).intersection(OWNER_LANES[owner]) == set(lanes) and set(previous.lanes) != set(lanes):
                    children = _split_due_job(db, previous.id, now=now)
                    key = db.get(RadarJob, children[owner]).job_key
                    break
    db.execute(insert(db, RadarJob).values(job_key=key, run_date=run_date, market='TW', lanes=lanes,
        scope={'scheduler_owner': owner} if owner else {}, state='pending', next_attempt_at=now)
        .on_conflict_do_nothing(index_elements=['job_key']))
    job = db.scalar(select(RadarJob).where(RadarJob.job_key == key).with_for_update().execution_options(populate_existing=True))
    if not owner and job.scope.get('scheduler_owner'):
        raise ValueError('radar_job_owner_mismatch')
    if owner and not job.scope.get('dated_registration'):
        scope = {**job.scope, 'scheduler_owner': owner, 'dated_registration': True}
        # A predecessor owns its interval even when provider retries are still pending.
        # An adopted legacy job keeps its original complete-inventory responsibility.
        if owner == 'validation' and all(prior.id != job.id for prior in same_day) and not scope.get('inherited_from') and not scope.get('window_planning'):
            scope['due_window_planner'] = True
            previous = db.scalar(select(RadarJob.run_date).where(RadarJob.market == job.market,
                RadarJob.run_date < run_date, RadarJob.lanes == lanes, unsplit_job(db),
                RadarJob.scope['scheduler_owner'].as_string() == owner,
                RadarJob.scope['dated_registration'].as_boolean().is_(True))
                .order_by(RadarJob.run_date.desc()).limit(1))
            if previous is not None:
                scope['window_due_after'] = previous.isoformat()
        job.scope = scope
        db.flush()
    recheck_publication = recheck_publication and 'publication' in lanes
    reconcile_job(db, job.id, now=now, audit=recheck_publication, recheck_publication=recheck_publication)
    return job


def _upsert_members(db, job_id, work_ids, required):
    statement = insert(db, RadarJobWork).values([
        {'job_id': job_id, 'work_id': work_id, 'required': required, 'active': True}
        for work_id in work_ids
    ])
    db.execute(statement.on_conflict_do_update(index_elements=['job_id', 'work_id'],
        set_={'active': True, 'required': statement.excluded.required},
        where=or_(RadarJobWork.active.is_(False), RadarJobWork.required != statement.excluded.required)))


def member(db, job, work, *, required=True):
    _upsert_members(db, job.id, [work.id], required)
    return work


def _units(db, job, stages, *, generation=None, deps=(), lane='publication', required=True, now=None):
    """Reuse existing work and only write missing work or changed job memberships."""
    run_date = job.run_date
    dependencies = [work.work_key for work in deps]
    planned = []
    for stage, scope in stages:
        identity = {'policy': POLICY, 'date': str(run_date), 'stage': stage, 'generation': generation, **scope}
        planned.append(work_insert_values(identity=identity, stage=stage, lane=lane, run_date=run_date,
            scope=scope | {'generation': generation}, input_revision=fingerprint(identity),
            dependencies=dependencies, priority=100 if lane == 'auxiliary' else list(TIMES).index(stage),
            retry_at=now, not_before=stage_time(run_date, stage)))
    if not planned:
        return []

    query = select(RadarWorkItem, RadarJobWork.active, RadarJobWork.required).outerjoin(RadarJobWork,
        and_(RadarJobWork.work_id == RadarWorkItem.id, RadarJobWork.job_id == job.id))

    def load(keys):
        return {work.work_key: (work, active, is_required)
                for work, active, is_required in db.execute(query.where(RadarWorkItem.work_key.in_(list(keys))))}

    unique = {item['work_key']: item for item in planned}
    existing = load(unique)
    missing = {key: item for key, item in unique.items() if key not in existing}
    if missing:
        db.execute(insert(db, RadarWorkItem).values(list(missing.values()))
            .on_conflict_do_nothing(index_elements=['work_key']))
        existing.update(load(missing))

    changed_members = [work.id for work, active, is_required in existing.values()
                       if active is not True or is_required != required]
    if changed_members:
        _upsert_members(db, job.id, changed_members, required)
    return [existing[item['work_key']][0] for item in planned]


def unit(db, job, stage, *, generation=None, scope=None, deps=(), lane='publication', required=True, now=None):
    return _units(db, job, [(stage, dict(scope or {}))], generation=generation, deps=deps,
                  lane=lane, required=required, now=now)[0]


WINDOW_PLAN_CANDIDATES = 50


class _WindowWorkBusy(Exception):
    """Another transaction owns work needed to reconcile this page."""


class _WindowInputsChanged(Exception):
    """The page's candidate inputs or calendar changed before acceptance."""


def plan_windows(db, job, scope, *, now, calendar_work=None):
    page_scope = dict(scope)
    try:
        with db.begin_nested():
            finished_result_audit = _plan_window_page(db, job, page_scope, now=now, calendar_work=calendar_work)
    except _WindowWorkBusy:
        # Roll back the page, including any membership retirement at the start
        # of a replan. Keep its cursor for a retry after the work lock is free.
        if 'window_result_audit' in page_scope and page_scope.get('window_plan') == scope.get('window_plan'):
            scope.setdefault('window_result_audit', {'state': 'planning', 'complete': False})
            return
        scope.pop('window_result_audit', None)
        progress = scope.get('window_planning') or {}
        scope.pop('window_plan', None)
        scope['window_planning'] = (progress if progress.get('state') == 'planning' else
                                    {'state': 'planning', 'revision': None, 'complete': False})
    except _WindowInputsChanged:
        scope.pop('window_result_audit', None)
        db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
            RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(RadarWorkItem.stage == 'window'))).values(active=False))
        scope.pop('window_plan', None)
        scope['window_planning'] = {'state': 'planning', 'revision': None, 'cursor': None,
                                    'complete': False, 'processed_candidates': 0}
    else:
        scope.clear()
        scope.update(page_scope)
        return finished_result_audit


def _plan_window_page(db, job, scope, *, now, calendar_work):
    from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar
    from ai_stock_sentinel.daily_radar.readiness import inventory_items
    from ai_stock_sentinel.daily_radar.planning_inventory import inventory_input_revision, candidate_page, maturity_signal_dates, due_in_interval
    from ai_stock_sentinel.daily_radar.trading_status import CONFLICT
    start = job.run_date - timedelta(days=364)
    calendar = load_calendar(db, start_date=start, as_of_date=job.run_date)
    if calendar is None or calendar.through < job.run_date:
        scope.pop('window_result_audit', None)
        scope.pop('window_plan', None)
        scope['window_planning'] = {'state': 'waiting_calendar', 'complete': False}
        db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
            RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(RadarWorkItem.stage == 'window'))).values(active=False))
        if calendar_work is not None and calendar_work.state == 'ready':
            db.execute(update(RadarWorkItem).where(RadarWorkItem.id == calendar_work.id,
                RadarWorkItem.state == 'ready', RadarWorkItem.lease_token.is_(None)).values(
                    state='pending', reason='official_calendar_unavailable', retry_at=now)
                    .execution_options(synchronize_session='fetch'))
        return
    inputs = inventory_input_revision(db, as_of_date=job.run_date)
    daily = scope.get('due_window_planner') is True
    since = date.fromisoformat(scope['window_due_after']) if daily and scope.get('window_due_after') else None
    yesterday = job.run_date - timedelta(days=1)
    current_dates = maturity_signal_dates(calendar, after=yesterday, through=job.run_date) if daily else ()
    catchup_dates = maturity_signal_dates(calendar, after=since, through=yesterday) if since else ()
    second_pass = 'catchup' if since else 'history'
    plan = fingerprint({'version': 'radar-dated-window-plan-v3' if daily else 'radar-window-plan-v4',
        **({'due_after': str(since)} if daily else {}), 'run_date': str(job.run_date),
        'inputs': inputs,
        'calendar': calendar.payload(), 'lanes': scope.get('inherited_plan_lanes', job.lanes)})
    completed_plan = scope.get('window_planning') or {}
    rescan = bool(completed_plan.get('complete') and scope.get('window_plan') == completed_plan.get('revision') == plan)
    progress_key = 'window_result_audit' if rescan else 'window_planning'
    if rescan:
        last = scope.get('window_inventory_audited_at')
        if progress_key not in scope and last and now - aware(datetime.fromisoformat(last)) < timedelta(minutes=50):
            return
    else:
        scope.pop('window_result_audit', None)
    progress = deepcopy(scope.get(progress_key) or {})
    if progress.get('revision') != plan or progress.get('complete') or progress.get('state') != 'planning':
        if not rescan:
            db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
                RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(RadarWorkItem.stage == 'window'))).values(active=False))
            scope.pop('window_plan', None)
        progress = {'state': 'planning', 'revision': plan, 'cursor': None, 'complete': False,
                    'processed_candidates': 0, 'accounted_windows': 0, 'started_at': now.isoformat()}
        if daily:
            progress['pass'] = 'current' if current_dates else second_pass
    scope[progress_key] = progress
    def load_page():
        dates = {'current': current_dates, 'catchup': catchup_dates}.get(progress.get('pass'))
        return candidate_page(db, start_date=start, as_of_date=job.run_date,
            after=progress['cursor'], limit=WINDOW_PLAN_CANDIDATES, include_dates=dates)

    ids = load_page()
    if not ids and progress.get('pass') == 'current':
        progress.update({'pass': second_pass, 'cursor': None})
        ids = load_page()
    page = ids[:WINDOW_PLAN_CANDIDATES]
    completed = defaultdict(list)
    audited_windows = set()
    retained_keys = set()
    for item in inventory_items(db, as_of_date=job.run_date, candidate_ids=page,
                                settle_later_results=True, lock_later_results=True):
        if item['return_basis'] not in job.lanes or item['state'] == 'immature':
            continue
        if daily:
            current_window = due_in_interval(item, yesterday, job.run_date)
            if (progress['pass'] == 'current' and not current_window
                    or progress['pass'] == 'catchup' and not due_in_interval(item, since, yesterday)
                    or progress['pass'] == 'history' and current_window):
                continue
        progress['accounted_windows'] = progress.get('accounted_windows', 0) + 1
        if item['reason'] == 'validation_result_locked':
            raise _WindowWorkBusy
        identity = {k: item[k] for k in ('candidate_id', 'candidate_revision', 'calendar_revision', 'return_basis', 'validation_version', 'window_days', 'expected_target_date')}
        identity['policy'] = POLICY
        if rescan:
            audited_windows.add((item['candidate_id'], item['return_basis'], item['window_days']))
        if item['state'] in {'validated', 'terminal'}:
            # Saved outcomes remain the authority. Do not copy the complete
            # historical inventory into the active queue on every date.
            keys = [fingerprint(identity), fingerprint(identity | {'cutoff': str(job.run_date)})]
            if rescan:
                retained_keys.update(keys)
            proof = item.get('settled_by')
            receipt = (proof['state'], proof['reason']) if proof else None
            cutoff = proof['evaluation_as_of_date'] if proof else item.get('result_as_of_date')
            completed[(cutoff, receipt)].extend(keys)
            continue
        previous = db.scalar(select(RadarWorkItem).where(RadarWorkItem.work_key == fingerprint(identity)))
        # Legacy conflicts/events need one collection to retain concrete evidence.
        # The evaluator parks an event that the existing rules cannot resolve.
        collect_review_evidence = item['reason'] in {'conflicting_price_records', 'official_corporate_action_in_window'}
        manual = (item['state'] in {'invalid_window', 'missing_diagnostic', 'input_unverified'}
                  or (item['next_action'] == 'needs_review'
                      and not collect_review_evidence))
        def create_window(key):
            return ensure_work(db, identity=key, stage='window', lane=item['return_basis'], run_date=job.run_date,
                scope=dict(item) | {'as_of_date': str(job.run_date)}, input_revision=fingerprint(key),
                state='needs_review' if manual else 'pending', retry_at=now,
                priority=19 if daily and item['expected_target_date'] == str(job.run_date) else 20,
                not_before=stage_time(job.run_date, 'window'))
        work = previous or create_window(identity)
        if date.fromisoformat(work.scope['as_of_date']) > job.run_date:
            work = create_window(identity | {'cutoff': str(job.run_date)})
        if item['reason'] == 'newer_validation_result_preserved':
            locked_work = db.scalar(select(RadarWorkItem).where(RadarWorkItem.id == work.id)
                .with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if locked_work is None:
                raise _WindowWorkBusy
        if work.state != 'running' and work.reason != CONFLICT:
            if 'settled_by' in (work.result or {}):
                work.result = {'manual_repair_required': True} if manual else {}
            if (not manual and item['reason'] == 'official_corporate_action_in_window' and work.state == 'needs_review'
                    and not (work.result or {}).get('source_observations')):
                work.state, work.reason, work.retry_at = 'pending', item['reason'], now
            if manual and (work.reason != 'source_revision_changed' or item['reason'] == 'newer_validation_result_preserved'):
                work.state, work.reason, work.retry_at = 'needs_review', item['reason'], None
            elif work.state in {'ready', 'terminal', 'superseded'}:
                work.state, work.reason, work.retry_at = 'pending', item['reason'], now
        member(db, job, work)
        if rescan:
            retained_keys.add(work.work_key)
    if audited_windows:
        _retire_replaced_windows(db, job, audited_windows, retained_keys)
    if completed:
        # An input replan temporarily retires all memberships. Reattach an
        # unresolved contradiction when the same fixed window is still present.
        candidates = db.execute(select(RadarWorkItem.id, RadarWorkItem.identity).where(
            RadarWorkItem.state == 'needs_review', RadarWorkItem.reason == CONFLICT,
            RadarWorkItem.run_date <= job.run_date,
            RadarWorkItem.identity["candidate_id"].as_integer().in_(page))).all()
        fixed_keys = {key for keys in completed.values() for key in keys}
        conflicts = [row.id for row in candidates if
                     fingerprint({k: v for k, v in row.identity.items() if k != 'cutoff'}) in fixed_keys]
        if conflicts:
            _upsert_members(db, job.id, conflicts, True)
        memberships = select(RadarJobWork.work_id).where(RadarJobWork.job_id == job.id)
        # A page may be reconciling a row owned by another date. Hold the
        # work lock through the membership decision or retain it for the next pass.
        matching = select(RadarWorkItem.id).where(RadarWorkItem.work_key.in_(fixed_keys),
            RadarWorkItem.id.in_(memberships)).order_by(RadarWorkItem.id)
        member_ids = set(db.scalars(matching))
        locked = db.execute(matching.add_columns(RadarWorkItem.state).with_for_update(skip_locked=True)).all()
        if len(locked) < len(member_ids):
            raise _WindowWorkBusy
        idle_ids = {wid for wid, state in locked if state != 'running'}
        retain = member_ids - idle_ids
        if retain:
            db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
                RadarJobWork.work_id.in_(retain)).values(active=True, required=True))
        for (cutoff, receipt), keys in completed.items():
            finish = update(RadarWorkItem).where(RadarWorkItem.work_key.in_(keys),
                RadarWorkItem.reason.is_distinct_from(CONFLICT), RadarWorkItem.id.in_(idle_ids))
            if receipt:
                state, reason = receipt
                finish = finish.where(RadarWorkItem.run_date <= job.run_date).values(
                    result={'settled_by': {'evaluation_as_of_date': cutoff, 'state': state, 'reason': reason}})
            else:
                # Passing today's ordinary inventory cannot certify shared work
                # from before the result. Its original job must verify the later proof.
                finish = finish.where(RadarWorkItem.run_date >= date.fromisoformat(cutoff)
                                      if cutoff else RadarWorkItem.run_date == job.run_date)
            db.execute(finish.values(state='ready', reason=None, retry_at=None))
        db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
            RadarJobWork.active.is_(True), RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(
                RadarWorkItem.id.in_(idle_ids), RadarWorkItem.reason.is_distinct_from(CONFLICT))))
            .values(active=False))
    latest_calendar = load_calendar(db, start_date=start, as_of_date=job.run_date)
    if (inventory_input_revision(db, as_of_date=job.run_date) != inputs or latest_calendar is None
            or latest_calendar.payload() != calendar.payload()):
        raise _WindowInputsChanged
    if page:
        progress['cursor'] = page[-1]
    progress['processed_candidates'] += len(page)
    progress['complete'] = len(ids) <= WINDOW_PLAN_CANDIDATES
    if progress['complete'] and progress.get('pass') == 'current' and (not since or catchup_dates):
        progress.update({'pass': second_pass, 'cursor': None, 'complete': False})
    progress['state'] = 'complete' if progress['complete'] else 'planning'
    if progress['complete']:
        if rescan:
            scope.pop('window_result_audit', None)
            scope['window_inventory_audited_at'] = progress.get('started_at', now.isoformat())
            return True
        scope['window_plan'] = plan
        # A slow initial plan can already have early results that need verification.
        scope['window_inventory_audited_at'] = progress.get('started_at', now.isoformat())


def _retire_replaced_windows(db, job, audited_windows, retained_keys):
    from ai_stock_sentinel.daily_radar.trading_status import CONFLICT
    # A shared window can acquire a newer cutoff. Replace only displaced
    # memberships in this page/pass; other dates still own their work.
    coordinates = tuple_(RadarWorkItem.identity['candidate_id'].as_integer(), RadarWorkItem.lane,
                         RadarWorkItem.identity['window_days'].as_integer())
    displaced = select(RadarWorkItem.id).join(RadarJobWork).where(
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True), RadarWorkItem.stage == 'window',
        coordinates.in_(sorted(audited_windows)), RadarWorkItem.work_key.not_in(retained_keys),
        RadarWorkItem.reason.is_distinct_from(CONFLICT))
    ids_to_retire = set(db.scalars(displaced))
    if ids_to_retire:
        locked = db.execute(select(RadarWorkItem.id, RadarWorkItem.state, RadarWorkItem.reason)
            .where(RadarWorkItem.id.in_(ids_to_retire)).order_by(RadarWorkItem.id)
            .with_for_update(skip_locked=True)).all()
        if len(locked) != len(ids_to_retire) or any(row.state == 'running' for row in locked):
            raise _WindowWorkBusy
        idle = [row.id for row in locked if row.reason != CONFLICT]
        db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
            RadarJobWork.work_id.in_(idle)).values(active=False))


def planning_remaining(job, scope):
    progress = scope.get('window_planning') or {}
    return bool(set(job.lanes) & {'signal_close', 'next_open'} and (
        'window_result_audit' in scope or progress.get('state') == 'planning' and not progress.get('complete')))


def invalidate_prerequisites(db, members, *, steps, blockers, now):
    """Requeue actual gaps with a CAS; never revoke another worker's lease."""
    affected = []
    for work, _ in sorted(members, key=lambda item: item[0].id):
        stage = work.stage
        if stage not in {'ohlcv', 'lending', 'full-margin', 'market-context', 'prepare', 'market-session', 'institutional'}:
            continue
        gap = any((b.get('step') == 'refresh-' + stage
            or (stage == 'ohlcv' and b.get('reason') == 'daily_radar_raw_data_incomplete')
            or (stage == 'market-context' and b.get('reason') == 'market_context_missing')
            or (stage == 'prepare' and b.get('reason') in {'prepared_run_missing', 'market_session_evidence_missing_or_invalid'})
            or (stage == 'prepare' and b.get('step') == 'refresh-institutional-flows')
            or (stage == 'institutional' and b.get('reason') == 'daily_radar_institutional_archive_incomplete'
                and (not b.get('missing_markets') or work.scope.get('market') in b['missing_markets']))
            or (stage == 'market-session' and b.get('reason') == 'market_session_evidence_missing_or_invalid'))
            and (not b.get('missing_symbols') or work.scope.get('symbol') in b['missing_symbols']) for b in blockers)
        if stage in {'ohlcv', 'lending', 'full-margin', 'market-context'}:
            step = steps.get('refresh-' + stage) or {}
            missing = step.get('missing_symbols') or []
            gap = gap or (step.get('status') != 'completed' and (
                not missing or work.scope.get('symbol') in missing))
        if not gap:
            continue
        if work.state != 'ready':
            affected.append(work.work_key)
            continue
        changed = db.scalar(update(RadarWorkItem).where(RadarWorkItem.id == work.id,
            RadarWorkItem.state == 'ready', RadarWorkItem.lease_token.is_(None)).values(
                state='pending', reason='actual_data_gap', retry_at=now).returning(RadarWorkItem.id)
                .execution_options(synchronize_session='fetch'))
        if changed is not None:
            affected.append(work.work_key)
        else:
            db.refresh(work)
            if work.state != 'ready':
                affected.append(work.work_key)
    if not affected:
        return
    for work, _ in sorted(members, key=lambda item: item[0].id):
        if work.stage != 'scoring' or work.state in {'running', 'superseded'}:
            continue
        db.execute(update(RadarWorkItem).where(RadarWorkItem.id == work.id,
            RadarWorkItem.state == work.state, RadarWorkItem.lease_token.is_(None)).values(
                state='waiting_dependency', reason='prerequisite_data_invalidated', retry_at=None,
                dependencies=list(dict.fromkeys([*work.dependencies, *affected])))
                .execution_options(synchronize_session='fetch'))


def attach_shared_dependencies(db, job, members):
    """An intent owns the retained dependency closure, including shared repairs."""
    known = {w.work_key: (w, required) for w, required in members}
    while True:
        needed = {}
        for work, required in known.values():
            for key in work.dependencies:
                if key not in known or (required and not known[key][1]):
                    needed[key] = needed.get(key, False) or required
        if not needed:
            break
        rows = db.scalars(select(RadarWorkItem).where(RadarWorkItem.work_key.in_(needed))
            .order_by(RadarWorkItem.id).execution_options(populate_existing=True)).all()
        if not rows:
            break
        for work in rows:
            required = needed[work.work_key]
            member(db, job, work, required=required)
            known[work.work_key] = (work, required)
    return sorted(known.values(), key=lambda item: item[0].id)


def sync_dependencies(db, members):
    keys = {w.work_key: w for w, _ in members}
    for work, _ in sorted(members, key=lambda item: item[0].id):
        values = {}
        if work.state != 'running' and any(key not in keys for key in work.dependencies):
            values = {'state': 'needs_review', 'reason': 'radar_dependency_missing', 'retry_at': None}
        elif work.state == 'needs_review' and work.reason == 'radar_dependency_missing':
            values = {'state': 'pending' if dependencies_ready(work, keys) else 'waiting_dependency',
                      'reason': None, 'retry_at': None}
        elif work.state in {'pending', 'waiting_dependency', 'waiting_provider'} and work.dependencies:
            ready = dependencies_ready(work, keys)
            if not ready:
                values['state'] = 'waiting_dependency'
            elif work.state != 'waiting_provider':
                values['state'] = 'pending'
        elif work.stage == 'scoring' and work.state in {'ready', 'terminal'} and work.dependencies:
            if not dependencies_ready(work, keys):
                values = {'state': 'waiting_dependency', 'reason': 'prerequisite_data_invalidated', 'retry_at': None}
        if values and any(getattr(work, k) != v for k, v in values.items()):
            changed = db.scalar(update(RadarWorkItem).where(RadarWorkItem.id == work.id,
                RadarWorkItem.state == work.state, RadarWorkItem.lease_token.is_(None)).values(**values)
                .returning(RadarWorkItem.id).execution_options(synchronize_session='fetch'))
            if changed is None:
                db.refresh(work)
    return keys


def dependencies_ready(work, keys, visiting=frozenset()):
    if work.work_key in visiting:
        return False
    return all(key in keys and keys[key].state == 'ready' and dependencies_ready(
        keys[key], keys, visiting | {work.work_key}) for key in work.dependencies)


def lock_date(db, day):
    if db.get_bind().dialect.name == 'postgresql':
        # Lanes share publication units. Serialize only reconciliation, before
        # job/work row locks; provider collection/acceptance never takes this key.
        key = int(fingerprint({'kind': 'radar-reconcile', 'market': 'TW', 'run_date': str(day)})[:15], 16)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
    elif db.get_bind().dialect.name == 'sqlite':
        db.execute(text('UPDATE radar_jobs SET id = id WHERE 0'))


def job_successors(db, job):
    successors = job.scope.get('successors')
    if successors or db.get_bind().dialect.name != 'sqlite' or job.scope.get('scheduler_owner'):
        return successors
    keys = [fingerprint({'split_job': job.job_key, 'owner': owner}) for owner in OWNER_LANES]
    children = db.scalars(select(RadarJob).where(RadarJob.job_key.in_(keys))).all()
    return {child.scope['scheduler_owner']: child.id for child in children}


def reconcile_job(db, job_id, *, now=None, audit=False, recheck_publication=False):
    now = database_now(db, now)
    day = db.scalar(select(RadarJob.run_date).where(RadarJob.id == job_id))
    if day is None:
        raise ValueError('radar_job_not_found')
    lock_date(db, day)
    job = db.scalar(select(RadarJob).where(RadarJob.id == job_id).with_for_update().execution_options(populate_existing=True))
    if job is None:
        raise ValueError('radar_job_not_found')
    successors = job_successors(db, job)
    if successors:
        job.scope = {**job.scope, 'successors': successors}
        job.state, job.next_attempt_at = 'split', None
        db.commit()
        return job
    scope = deepcopy(job.scope or {})
    if scope.get('inherit_cursor') is not None:
        if recheck_publication:
            job.scope = {**scope, 'recheck_after_inherit': True}
        _inherit_page(db, job)
        if job.scope['inherit_cursor'] is not None:
            db.commit()
            return job
        scope = deepcopy(job.scope)
        if scope.pop('recheck_after_inherit', False):
            audit = recheck_publication = True
    superseded = db.scalar(select(RadarWorkItem.id).join(RadarJobWork).where(
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True), RadarWorkItem.stage == 'window',
        RadarWorkItem.state == 'superseded').limit(1))
    progress = scope.get('window_planning') or {}
    if not progress or superseded is not None:
        scope.pop('window_result_audit', None)
        scope.pop('window_plan', None)
        scope.pop('window_planning', None)
    radar = bool(set(job.lanes).intersection(OWNER_LANES['radar']))
    session = unit(db, job, 'market-session', now=now) if radar else None
    if not radar:
        # Pre-split validation intents could still own the publication session probe.
        db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True),
            RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(RadarWorkItem.stage == 'market-session')))
            .values(active=False))
    closed = bool(session and session.state == 'terminal' and (session.result or {}).get('status') == 'closed')
    if closed:
        db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
            RadarJobWork.active.is_(True),
            RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(
                RadarWorkItem.lane.in_(('publication', 'auxiliary')), RadarWorkItem.stage != 'market-session')))
            .values(active=False))
    if not closed and radar:
        archives = _units(db, job, ((stage, {'market': market})
            for stage in ('institutional', 'market-bars') for market in ('TW', 'TWO')), deps=[session], now=now)
        prep = unit(db, job, 'prepare', deps=archives, now=now)
        gaps = (prep.result or {}).get('archive_gaps') or {}
        gap_stages = []
        for trade_date, markets in sorted(gaps.items()):
            day = date.fromisoformat(trade_date)
            if not job.run_date - timedelta(days=240) <= day <= job.run_date or set(markets) - {'TW', 'TWO'}:
                raise ValueError('radar_archive_dependency_scope_invalid')
            gap_stages.extend(('market-bars', {'market': market, 'trade_date': trade_date}) for market in markets)
        gap_units = _units(db, job, gap_stages, deps=[session], now=now)
        if gap_units and prep.state != 'running':
            prep.dependencies = list(dict.fromkeys([w.work_key for w in archives + gap_units]))
        prepared = db.scalar(select(DailyRadarPreparedRun).where(
            DailyRadarPreparedRun.run_date == job.run_date, DailyRadarPreparedRun.market == 'TW'))
        gen = prepared_generation(prepared)
        if gen:
            if scope.get('generation') != gen:
                old = db.scalars(select(RadarWorkItem).join(RadarJobWork).where(RadarJobWork.job_id == job.id,
                    RadarJobWork.active.is_(True), RadarWorkItem.stage.in_(
                        ('ohlcv', 'lending', 'full-margin', 'avwap', 'market-context', 'managed-raw', 'scoring')))).all()
                for work in old:
                    work.state, work.lease_token, work.lease_expires_at, work.retry_at = 'superseded', None, None, None
                if old:
                    db.execute(update(RadarWorkAttempt).where(RadarWorkAttempt.work_id.in_([w.id for w in old]),
                        RadarWorkAttempt.state == 'running').values(state='superseded', finished_at=now,
                            reason='radar_prepared_generation_changed'))
                    db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
                        RadarJobWork.work_id.in_([w.id for w in old])).values(active=False))
                scope['generation'] = gen
                scope.pop('managed_symbols', None)
            if 'publication' in job.lanes:
                stages = [(stage, {'symbol': symbol}) for stage in ('lending', 'full-margin', 'ohlcv')
                          for symbol in prepared.selected_symbols]
                stages.append(('market-context', {}))
                required = _units(db, job, stages, generation=gen, deps=[prep], now=now)
                unit(db, job, 'scoring', generation=gen, deps=required, now=now)
            if 'auxiliary' in job.lanes:
                from ai_stock_sentinel.daily_radar.managed_raw_data import select_managed_raw_data_symbols
                if 'managed_symbols' not in scope:
                    selection = select_managed_raw_data_symbols(db, run_date=job.run_date)
                    scope['managed_symbols'], scope['managed_deferred'] = list(selection.symbols), selection.deferred_recent_symbol_count
                stages = [('avwap', {'symbol': symbol}) for symbol in prepared.selected_symbols]
                stages.extend(('managed-raw', {'symbol': symbol, 'managed_symbols': scope['managed_symbols'],
                    'deferred': scope['managed_deferred']}) for symbol in scope['managed_symbols'])
                _units(db, job, stages, generation=gen, deps=[prep], lane='auxiliary', required=False, now=now)
    if set(job.lanes) & {'signal_close', 'next_open'}:
        from ai_stock_sentinel.daily_radar.trading_calendar import VERSION as CALENDAR_VERSION
        calendar_work = unit(db, job, 'calendar', scope={'start_date': str(job.run_date - timedelta(days=364)),
            'calendar_version': CALENDAR_VERSION}, lane='validation', now=now)
        db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
            RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(RadarWorkItem.stage == 'calendar',
                RadarWorkItem.id != calendar_work.id))).values(active=False))
        finished_result_audit = plan_windows(db, job, scope, now=now, calendar_work=calendar_work)
        if finished_result_audit and not radar:
            audit = True
    db.flush()
    review_windows = db.scalars(select(RadarWorkItem).join(RadarJobWork).where(
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True), RadarWorkItem.stage == 'window',
        RadarWorkItem.state == 'needs_review')).all()
    for window in review_windows:
        observations = (window.result or {}).get('source_observations') or []
        if not observations or window.reason not in {'conflicting_price_records', 'research_basis_mismatch', 'price_basis_mismatch'}:
            continue
        identity = {'policy': POLICY, 'stage': 'source-recheck', 'window': window.work_key,
            'sources': sorted((p['request_hash'], p['snapshot_sha256']) for p in observations)}
        check = ensure_work(db, identity=identity, stage='source-recheck', lane=window.lane,
            run_date=window.run_date, scope={'window_id': window.id, 'observations': observations},
            input_revision=fingerprint(identity), retry_at=now, priority=21)
        member(db, job, check, required=False)
        if check.state == 'ready' and (check.result or {}).get('changed'):
            window.state, window.reason, window.retry_at = 'pending', 'source_revision_changed', now
    members = list(db.execute(select(RadarWorkItem, RadarJobWork.required).join(RadarJobWork).where(
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True)).execution_options(populate_existing=True)).all())
    members = attach_shared_dependencies(db, job, members)
    sync_dependencies(db, members)
    for work, _ in members:
        if work.state == 'pending' and work.retry_at is None:
            work.retry_at = now
    progress = scope.get('window_planning') or {}
    plan_complete = (not set(job.lanes) & {'signal_close', 'next_open'} or (
        progress.get('complete') and scope.get('window_plan') == progress.get('revision')
        and not planning_remaining(job, scope)))
    required_done = bool(plan_complete and all(w.state in {'ready', 'terminal'} for w, required in members if required))
    audit_completed_score = not recheck_publication and any(
        w.stage == 'scoring' and w.state == 'ready' for w, _ in members)
    if audit and required_done and not radar:
        from ai_stock_sentinel.daily_radar.readiness import read_readiness
        after = scope.get('window_due_after')
        report = read_readiness(db, run_date=job.run_date, scope='validation', settle_later_results=True, **({'due_after': after} if after else {}))
        lanes = [report['validation'][lane] for lane in job.lanes]
        ready = (all(lane['calendar_ready'] and lane['due_interval']['unresolved_count'] == 0 for lane in lanes)
                 if after else all(lane['ready'] for lane in lanes))
        scope.update(audit={'observed_at': now.isoformat(), 'data_ready': None, 'validation': report['validation']},
                     intent_complete=ready, audit_ready=None)
        if ready:
            scope['window_inventory_audited_at'] = now.isoformat()
        else:
            scope['window_result_audit'] = {'state': 'planning', 'complete': False}
    elif audit and required_done:
        from ai_stock_sentinel.daily_radar.readiness import read_readiness
        report = read_readiness(db, run_date=job.run_date,
            audit_completed_score=audit_completed_score, settle_later_results=True, **(
            {'scope': 'publication'} if not set(job.lanes) & {'signal_close', 'next_open'} else {}))
        scope['audit'] = {'observed_at': now.isoformat(), 'data_ready': report['data_ready'],
            'publication': report['publication'], 'validation': report['validation']}
        # Closed dates have no publication; validation readiness remains independent.
        scope['audit_ready'] = report['data_ready']
        publication = report['publication']
        published = closed or (publication['status'] == 'completed' and publication['output_complete']
            and report['scoring']['ready'] and bool((publication.get('input_manifest') or {}).get('matches_current_inputs')))
        scope['intent_complete'] = (('publication' not in job.lanes or published)
            and all(report['validation'][lane]['ready'] for lane in ('signal_close', 'next_open') if lane in job.lanes))
        if not scope['intent_complete']:
            scope.pop('window_plan', None)
            invalidate_prerequisites(db, members, steps=report['scoring']['steps'],
                blockers=report['scoring']['blockers'], now=now)
            for work, _ in members:
                if work.stage == 'scoring' and work.state == 'ready':
                    manifest = report['publication'].get('input_manifest') or {}
                    if not report['publication']['output_complete'] or not manifest.get('matches_current_inputs'):
                        work.state, work.retry_at = 'pending', now
    elif not required_done:
        scope['audit_ready'] = False
        scope['intent_complete'] = False
    # Downstream failure must not suppress prerequisite repair. This cheaper
    # audit is independent of validation/other lanes reaching completion.
    if audit and not required_done and any(w.stage == 'scoring'
            and w.state in {'ready', 'pending', 'waiting_dependency', 'waiting_provider', 'needs_review', 'failed_internal'}
            for w, _ in members):
        from ai_stock_sentinel.daily_radar.readiness import scoring_prerequisite_inputs
        _, steps, blockers, _, _ = scoring_prerequisite_inputs(db, run_date=job.run_date)
        if audit_completed_score and any(b.get('step') == 'refresh-full-margin' for b in blockers):
            from ai_stock_sentinel.daily_radar.readiness import read_readiness
            report = read_readiness(db, run_date=job.run_date, scope='publication', audit_completed_score=True)
            steps, blockers = report['scoring']['steps'], report['scoring']['blockers']
        invalidate_prerequisites(db, members, steps=steps, blockers=blockers, now=now)
    sync_dependencies(db, members)
    job.scope = scope
    counts = Counter(w.state for w, _ in members)
    job.state = ('closed' if closed and scope.get('intent_complete') else 'ready' if scope.get('intent_complete')
                 else 'needs_review' if counts['needs_review'] else 'pending')
    due = [max(aware(w.retry_at), aware(w.not_before)) for w, _ in members if w.state in {'pending', 'waiting_provider'} and w.retry_at]
    due += [aware(w.lease_expires_at) for w, _ in members if w.state == 'running']
    if planning_remaining(job, scope):
        due.append(now)
    elif audit and required_done and not scope.get('intent_complete') and any(
            lane in job.lanes and report_lane.get('reasons', {}).get('validation_result_locked')
            for lane, report_lane in scope['audit']['validation'].items()):
        due.append(now)
    job.next_attempt_at = min(due) if due else now + timedelta(hours=1)
    job.last_advanced_at = now
    db.commit()
    return job


def job_status(db, job_id, *, include_observation=False):
    job = db.get(RadarJob, job_id)
    if job is None:
        raise ValueError('radar_job_not_found')
    now = database_now(db)
    members = db.execute(select(
        RadarWorkItem.lane, RadarWorkItem.stage, RadarWorkItem.state, RadarWorkItem.reason,
        RadarWorkItem.retry_at, RadarWorkItem.not_before, RadarWorkItem.lease_expires_at,
        RadarWorkItem.result['status'].as_string().label('market_status'), RadarJobWork.required,
    ).join(RadarJobWork).where(
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True))).all()
    lanes = {}
    for work in members:
        lane = lanes.setdefault(work.lane, {'states': Counter(), 'reasons': Counter(), 'required_count': 0})
        lane['states'][work.state] += 1
        lane['required_count'] += int(work.required)
        if work.reason:
            lane['reasons'][work.reason] += 1
    closed = any(w.stage == 'market-session' and w.state == 'terminal' and w.market_status == 'closed' for w in members)
    scope = job.scope or {}
    runnable = any((w.state in {'pending', 'waiting_provider'} and w.retry_at is not None
        and aware(w.retry_at) <= now and aware(w.not_before) <= now)
        or (w.state == 'running' and aware(w.lease_expires_at) <= now) for w in members)
    successors = job_successors(db, job)
    retired = bool(successors)
    runnable = not retired and (runnable or planning_remaining(job, scope) or scope.get('inherit_cursor') is not None)
    return {'job_id': job.id, 'requested_run_date': str(job.run_date), 'effective_run_date': str(job.run_date),
        'run_date': str(job.run_date), 'state': 'split' if retired else job.state, 'data_ready': not retired and bool(scope.get('audit_ready')),
        'intent_complete': not retired and bool(scope.get('intent_complete')),
        **({'successors': successors} if retired else {}),
        'handoff_pending': scope.get('inherit_cursor') is not None,
        'planning': {k: (scope.get('window_planning') or {}).get(k)
            for k in ('state', 'complete', 'processed_candidates')},
        'result_audit': {k: (scope.get('window_result_audit') or {}).get(k)
            for k in ('state', 'processed_candidates')} if 'window_result_audit' in scope else None,
        'runnable_now': runnable,
        **({'observed_at': now.isoformat()} if include_observation else {}),
        'next_attempt_at': job.next_attempt_at.isoformat() if job.next_attempt_at else None,
        'publication': {'state': 'closed' if closed else (scope.get('audit') or {}).get('publication', {}).get('state', 'pending')},
        'lanes': lanes, 'last_audit': scope.get('audit'), 'work_count': len(members)}


def _due_query(db, now):
    return select(RadarJob).where(RadarJob.next_attempt_at <= now, unsplit_job(db),
        or_(RadarJob.state.not_in(('ready', 'closed')), RadarJob.run_date >= now.date() - timedelta(days=7)))


def due_jobs(db, *, now=None, limit=20):
    rows = db.scalars(_due_query(db, database_now(db, now)).where(RadarJob.scope['scheduler_owner'].as_string().is_(None)).order_by(RadarJob.next_attempt_at, RadarJob.id).limit(limit))
    return [{'job_id': row.id, 'run_date': str(row.run_date)} for row in rows]


INHERIT_PAGE_SIZE = 1000


def _inherit_page(db, child):
    scope = dict(child.scope)
    lanes = (*child.lanes, 'validation') if scope['scheduler_owner'] == 'validation' else OWNER_LANES['radar']
    rows = db.execute(select(RadarJobWork.work_id, RadarJobWork.required, RadarJobWork.active)
        .where(RadarJobWork.job_id == scope['inherited_from'], RadarJobWork.work_id > scope['inherit_cursor'])
        .order_by(RadarJobWork.work_id).limit(INHERIT_PAGE_SIZE + 1)).all()
    page = rows[:INHERIT_PAGE_SIZE]
    active = {item.work_id: item.required for item in page if item.active}
    if active:
        owned = db.scalars(select(RadarWorkItem.id).where(
            RadarWorkItem.id.in_(active), RadarWorkItem.lane.in_(lanes))).all()
        for required in (True, False):
            ids = [work_id for work_id in owned if active[work_id] == required]
            if ids:
                _upsert_members(db, child.id, ids, required)
    scope['inherit_cursor'] = page[-1].work_id if len(rows) > len(page) else None
    child.scope = scope


def _split_due_job(db, job_id, *, now):
    # Lock the same parent row as the planner and slot claim. A previous claim
    # can finish under its original lease because the work row never moves.
    parent = db.scalar(update(RadarJob).where(RadarJob.id == job_id, unsplit_job(db))
                       .values(state='splitting').returning(RadarJob).execution_options(populate_existing=True))
    if parent is None:
        return job_successors(db, db.get(RadarJob, job_id, populate_existing=True))
    successors = {}
    for owner, allowed in OWNER_LANES.items():
        lanes = sorted(set(parent.lanes).intersection(allowed))
        fields = (('generation', 'managed_symbols', 'managed_deferred') if owner == 'radar' else
                  ('window_plan', 'window_planning', 'window_result_audit', 'window_inventory_audited_at'))
        scope = {key: parent.scope[key] for key in fields if key in parent.scope}
        scope.update(scheduler_owner=owner, inherited_from=parent.id, inherit_cursor=0)
        if owner == 'validation':
            scope['inherited_plan_lanes'] = parent.lanes
        child = RadarJob(job_key=fingerprint({'split_job': parent.job_key, 'owner': owner}),
                         market=parent.market, run_date=parent.run_date, lanes=lanes, scope=scope,
                         state='pending', next_attempt_at=parent.next_attempt_at)
        db.add(child)
        db.flush()
        _inherit_page(db, child)
        if child.scope['inherit_cursor'] is not None:
            child.next_attempt_at = now
        successors[owner] = child.id
    # Preserve who issued each attempt while transferring its occupied slot.
    # The original lease token continues to fence collection and acceptance.
    running = db.execute(select(RadarWorkAttempt, RadarWorkItem.lane).select_from(RadarWorkItem)
        .join(RadarWorkAttempt, RadarWorkAttempt.lease_token == RadarWorkItem.lease_token)
        .where(RadarWorkItem.state == 'running', RadarWorkAttempt.state == 'running',
               RadarWorkAttempt.scope['advance_job_id'].as_integer() == parent.id)
        .with_for_update(of=RadarWorkAttempt))
    for attempt, lane in running:
        owner = 'radar' if lane in OWNER_LANES['radar'] else 'validation'
        attempt.scope = {**attempt.scope, 'slot_job_id': successors[owner]}
    parent.state, parent.next_attempt_at = 'split', None
    parent.scope = {**parent.scope, 'successors': successors, 'intent_complete': False, 'audit_ready': None}
    db.flush()
    return successors


def resume_jobs(db, *, owner, now=None, limit=20):
    allowed = OWNER_LANES[owner]
    now = database_now(db, now)
    # Job intents have at most four lanes. Filter before the limit on both
    # PostgreSQL and SQLite so an older backlog cannot hide the other owner's dates.
    query = _due_query(db, now).where(or_(*(RadarJob.lanes[i].as_string().in_(allowed)
                                       for i in range(len(LANES)))))
    query = query.with_only_columns(RadarJob.id, RadarJob.lanes, RadarJob.run_date)
    rows = []
    if owner == 'validation' and limit > 1:
        newest = db.execute(query.where(RadarJob.scope['dated_registration'].as_boolean().is_(True))
                            .order_by(RadarJob.run_date.desc(), RadarJob.next_attempt_at, RadarJob.id).limit(1)).first()
        if newest:
            rows.append(newest)
            query = query.where(RadarJob.id != newest.id)
        other_dates = query.where(RadarJob.run_date != newest.run_date) if newest else query
        oldest = db.execute(other_dates.where(RadarJob.state.not_in(('ready', 'closed')))
                            .order_by(RadarJob.run_date, RadarJob.next_attempt_at, RadarJob.id).limit(1)).first()
        if oldest:
            rows.append(oldest)
            query = query.where(RadarJob.id != oldest.id)
        if len(rows) < limit:
            # Give each remaining due date a representative before filling
            # the list with legacy jobs for dates already admitted.
            ranked = query.where(RadarJob.run_date.not_in([row.run_date for row in rows])).add_columns(
                RadarJob.next_attempt_at, func.row_number().over(partition_by=RadarJob.run_date,
                    order_by=(RadarJob.next_attempt_at, RadarJob.id)).label('date_rank')).subquery()
            representatives = db.execute(select(ranked.c.id, ranked.c.lanes, ranked.c.run_date)
                .where(ranked.c.date_rank == 1).order_by(ranked.c.next_attempt_at, ranked.c.id)
                .limit(limit - len(rows))).all()
            rows.extend(representatives)
            query = query.where(RadarJob.id.not_in([row.id for row in representatives]))
    rows.extend(db.execute(query.order_by(RadarJob.next_attempt_at, RadarJob.id).limit(limit-len(rows))).all())
    result = []
    for job_id, lanes, run_date in rows:
        lock_date(db, run_date)
        if set(lanes).difference(allowed):
            job_id = _split_due_job(db, job_id, now=now)[owner]
            db.commit()
        else:
            row = db.scalar(select(RadarJob).where(RadarJob.id == job_id).with_for_update()
                            .execution_options(populate_existing=True))
            if not row.scope.get('scheduler_owner'):
                row.scope = {**row.scope, 'scheduler_owner': owner}
            db.commit()
        result.append({'job_id': job_id, 'run_date': str(run_date)})
    return result
