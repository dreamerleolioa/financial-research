"""Durable Radar intent and dependency planning. No provider I/O here."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, update, or_
from ai_stock_sentinel.db.models import RadarJob, RadarWorkItem, RadarJobWork, RadarWorkAttempt, DailyRadarPreparedRun
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
from ai_stock_sentinel.daily_radar.work_queue import ensure_work, insert, database_now, aware

POLICY = 'radar-durable-v1'
LANES = ('publication', 'signal_close', 'next_open', 'auxiliary')
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


def create_job(db, *, run_date, lanes=LANES, now=None):
    if not isinstance(run_date, date) or not lanes or set(lanes) - set(LANES):
        raise ValueError('radar_job_scope_invalid')
    now, lanes = database_now(db, now), sorted(set(lanes))
    key = fingerprint({'policy': POLICY, 'market': 'TW', 'run_date': str(run_date), 'lanes': lanes})
    db.execute(insert(db, RadarJob).values(job_key=key, run_date=run_date, market='TW', lanes=lanes,
        scope={}, state='pending', next_attempt_at=now).on_conflict_do_nothing(index_elements=['job_key']))
    job = db.scalar(select(RadarJob).where(RadarJob.job_key == key))
    reconcile_job(db, job.id, now=now)
    return job


def member(db, job, work, *, required=True):
    db.execute(insert(db, RadarJobWork).values(job_id=job.id, work_id=work.id, required=required, active=True)
        .on_conflict_do_update(index_elements=['job_id', 'work_id'], set_={'active': True, 'required': required}))
    return work


def unit(db, job, stage, *, generation=None, scope=None, deps=(), lane='publication', required=True, now=None):
    scope = dict(scope or {})
    identity = {'policy': POLICY, 'date': str(job.run_date), 'stage': stage, 'generation': generation, **scope}
    work = ensure_work(db, identity=identity, stage=stage, lane=lane, run_date=job.run_date,
        scope=scope | {'generation': generation}, input_revision=fingerprint(identity),
        dependencies=[w.work_key for w in deps], priority=100 if lane == 'auxiliary' else list(TIMES).index(stage),
        retry_at=now, not_before=stage_time(job.run_date, stage))
    return member(db, job, work, required=required)


def plan_windows(db, job, scope, *, now):
    from ai_stock_sentinel.daily_radar.observation_validation import canonical_radar_run_ids
    from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar
    from ai_stock_sentinel.daily_radar.readiness import inventory_items
    from ai_stock_sentinel.db.models import DailyRadarRun
    start = job.run_date - timedelta(days=364)
    calendar = load_calendar(db, start_date=start, as_of_date=job.run_date)
    if calendar is None or calendar.through < job.run_date:
        scope.pop('window_plan', None)
        return
    runs = list(db.scalars(select(DailyRadarRun.id).where(DailyRadarRun.id.in_(canonical_radar_run_ids(
        market='TW', start_date=start, end_date=job.run_date))).order_by(DailyRadarRun.id)))
    plan = fingerprint({'runs': runs, 'calendar': calendar.payload(), 'lanes': job.lanes})
    if scope.get('window_plan') == plan:
        return
    db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
        RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(RadarWorkItem.stage == 'window'))).values(active=False))
    for item in inventory_items(db, as_of_date=job.run_date):
        if item['return_basis'] not in job.lanes or item['state'] == 'immature':
            continue
        identity = {k: item[k] for k in ('candidate_id', 'candidate_revision', 'calendar_revision', 'return_basis', 'validation_version', 'window_days', 'expected_target_date')}
        identity['policy'] = POLICY
        previous = db.scalar(select(RadarWorkItem).where(RadarWorkItem.work_key == fingerprint(identity)))
        if previous and date.fromisoformat(previous.scope['as_of_date']) > job.run_date:
            identity['cutoff'] = str(job.run_date)
        manual = item['state'] in {'invalid_window', 'missing_diagnostic'}
        done = item['state'] in {'validated', 'terminal'}
        work = ensure_work(db, identity=identity, stage='window', lane=item['return_basis'], run_date=job.run_date,
            scope=dict(item) | {'as_of_date': str(job.run_date)}, input_revision=fingerprint(identity),
            state='ready' if done else 'needs_review' if manual else 'pending', retry_at=now,
            priority=20, not_before=stage_time(job.run_date, 'window'))
        if work.state != 'running':
            if done:
                work.state, work.reason, work.retry_at = 'ready', None, None
            elif manual:
                work.state, work.reason, work.retry_at = 'needs_review', item['reason'], None
            elif work.state in {'ready', 'terminal'}:
                work.state, work.reason, work.retry_at = 'pending', item['reason'], now
        member(db, job, work)
    scope['window_plan'] = plan


def reconcile_job(db, job_id, *, now=None, audit=False):
    now = database_now(db, now)
    job = db.scalar(select(RadarJob).where(RadarJob.id == job_id).with_for_update().execution_options(populate_existing=True))
    if job is None:
        raise ValueError('radar_job_not_found')
    scope = deepcopy(job.scope or {})
    if job.last_advanced_at and now - aware(job.last_advanced_at) >= timedelta(minutes=50):
        scope.pop('window_plan', None)  # Observe reviewed/manual repairs on bounded revisits.
    session = unit(db, job, 'market-session', now=now)
    closed = session.state == 'terminal' and (session.result or {}).get('status') == 'closed'
    if closed:
        db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
            RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(
                RadarWorkItem.lane.in_(('publication', 'auxiliary')), RadarWorkItem.stage != 'market-session')))
            .values(active=False))
    if not closed and set(job.lanes) & {'publication', 'auxiliary'}:
        archives = [unit(db, job, stage, scope={'market': market}, deps=[session], now=now)
                    for stage in ('institutional', 'market-bars') for market in ('TW', 'TWO')]
        prep = unit(db, job, 'prepare', deps=archives, now=now)
        gaps = (prep.result or {}).get('archive_gaps') or {}
        gap_units = []
        for trade_date, markets in sorted(gaps.items()):
            day = date.fromisoformat(trade_date)
            if not job.run_date - timedelta(days=240) <= day <= job.run_date or set(markets) - {'TW', 'TWO'}:
                raise ValueError('radar_archive_dependency_scope_invalid')
            for market in markets:
                gap_units.append(unit(db, job, 'market-bars', scope={'market': market, 'trade_date': trade_date}, deps=[session], now=now))
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
                required = [unit(db, job, stage, generation=gen, scope={'symbol': symbol}, deps=[prep], now=now)
                    for stage in ('lending', 'full-margin', 'ohlcv') for symbol in prepared.selected_symbols]
                required.append(unit(db, job, 'market-context', generation=gen, deps=[prep], now=now))
                unit(db, job, 'scoring', generation=gen, deps=required, now=now)
            if 'auxiliary' in job.lanes:
                from ai_stock_sentinel.daily_radar.managed_raw_data import select_managed_raw_data_symbols
                if 'managed_symbols' not in scope:
                    selection = select_managed_raw_data_symbols(db, run_date=job.run_date)
                    scope['managed_symbols'], scope['managed_deferred'] = list(selection.symbols), selection.deferred_recent_symbol_count
                for symbol in prepared.selected_symbols:
                    unit(db, job, 'avwap', generation=gen, scope={'symbol': symbol}, deps=[prep], lane='auxiliary', required=False, now=now)
                for symbol in scope['managed_symbols']:
                    unit(db, job, 'managed-raw', generation=gen, scope={'symbol': symbol,
                        'managed_symbols': scope['managed_symbols'], 'deferred': scope['managed_deferred']},
                        deps=[prep], lane='auxiliary', required=False, now=now)
    if set(job.lanes) & {'signal_close', 'next_open'}:
        from ai_stock_sentinel.daily_radar.trading_calendar import VERSION as CALENDAR_VERSION
        calendar_work = unit(db, job, 'calendar', scope={'start_date': str(job.run_date - timedelta(days=364)),
            'calendar_version': CALENDAR_VERSION}, lane='validation', now=now)
        db.execute(update(RadarJobWork).where(RadarJobWork.job_id == job.id,
            RadarJobWork.work_id.in_(select(RadarWorkItem.id).where(RadarWorkItem.stage == 'calendar',
                RadarWorkItem.id != calendar_work.id))).values(active=False))
        plan_windows(db, job, scope, now=now)
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
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True))).all())
    keys = {w.work_key: w for w, _ in members}
    for work, _ in members:
        if work.state in {'pending', 'waiting_dependency'} and work.dependencies:
            ready = all(key in keys and keys[key].state == 'ready' for key in work.dependencies)
            work.state = 'pending' if ready else 'waiting_dependency'
            if ready and work.retry_at is None:
                work.retry_at = now
    required_done = all(w.state in {'ready', 'terminal'} for w, required in members if required)
    if audit and required_done:
        from ai_stock_sentinel.daily_radar.readiness import read_readiness
        report = read_readiness(db, run_date=job.run_date)
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
            for work, _ in members:
                if work.stage == 'scoring' and work.state == 'ready':
                    manifest = report['publication'].get('input_manifest') or {}
                    if not report['publication']['output_complete'] or not manifest.get('matches_current_inputs'):
                        work.state, work.retry_at = 'pending', now
                if work.stage in {'ohlcv', 'lending', 'full-margin', 'market-context'} and work.state == 'ready':
                    step = report['scoring']['steps'].get('refresh-' + work.stage) or {}
                    actual_gap = any((b.get('step') == 'refresh-' + work.stage
                        or (work.stage == 'ohlcv' and b.get('reason') == 'daily_radar_raw_data_incomplete'))
                        and (not b.get('missing_symbols') or work.scope.get('symbol') in b['missing_symbols'])
                        for b in report['scoring']['blockers'])
                    if step.get('status') != 'completed' or actual_gap:
                        work.state, work.retry_at = 'pending', now
    elif not required_done:
        scope['audit_ready'] = False
        scope['intent_complete'] = False
    job.scope = scope
    counts = Counter(w.state for w, _ in members)
    job.state = ('closed' if closed and scope.get('intent_complete') else 'ready' if scope.get('intent_complete')
                 else 'needs_review' if counts['needs_review'] else 'pending')
    due = [max(aware(w.retry_at), aware(w.not_before)) for w, _ in members if w.state in {'pending', 'waiting_provider'} and w.retry_at]
    due += [aware(w.lease_expires_at) for w, _ in members if w.state == 'running']
    job.next_attempt_at = min(due) if due else now + timedelta(hours=1)
    job.last_advanced_at = now
    db.commit()
    return job


def job_status(db, job_id):
    job = db.get(RadarJob, job_id)
    if job is None:
        raise ValueError('radar_job_not_found')
    members = db.execute(select(RadarWorkItem, RadarJobWork.required).join(RadarJobWork).where(
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True))).all()
    lanes = {}
    for work, required in members:
        lane = lanes.setdefault(work.lane, {'states': Counter(), 'reasons': Counter(), 'required_count': 0})
        lane['states'][work.state] += 1
        lane['required_count'] += int(required)
        if work.reason:
            lane['reasons'][work.reason] += 1
    closed = any(w.stage == 'market-session' and w.state == 'terminal' and (w.result or {}).get('status') == 'closed' for w, _ in members)
    scope = job.scope or {}
    now = database_now(db)
    runnable = any((w.state in {'pending', 'waiting_provider'} and w.retry_at is not None
        and aware(w.retry_at) <= now and aware(w.not_before) <= now)
        or (w.state == 'running' and aware(w.lease_expires_at) <= now) for w, _ in members)
    return {'job_id': job.id, 'requested_run_date': str(job.run_date), 'effective_run_date': str(job.run_date),
        'run_date': str(job.run_date), 'state': job.state, 'data_ready': bool(scope.get('audit_ready')),
        'intent_complete': bool(scope.get('intent_complete')),
        'runnable_now': runnable,
        'next_attempt_at': job.next_attempt_at.isoformat() if job.next_attempt_at else None,
        'publication': {'state': 'closed' if closed else (scope.get('audit') or {}).get('publication', {}).get('state', 'pending')},
        'lanes': lanes, 'last_audit': scope.get('audit'), 'work_count': len(members)}


def due_jobs(db, *, now=None, limit=20):
    now = database_now(db, now)
    rows = db.scalars(select(RadarJob).where(RadarJob.next_attempt_at <= now,
        or_(RadarJob.state.not_in(('ready', 'closed')), RadarJob.run_date >= now.date() - timedelta(days=7)))
        .order_by(RadarJob.next_attempt_at, RadarJob.id).limit(limit))
    return [{'job_id': row.id, 'run_date': str(row.run_date)} for row in rows]
