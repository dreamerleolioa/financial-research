"""Advance due jobs with one general request and one window request in flight."""
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from multiprocessing import get_context
from time import monotonic

from daily_radar_http import post, request_deadline


def _advance(write, url, token, seconds, deadline):
    timeout = min(seconds, deadline - monotonic())
    if timeout <= 0:
        raise TimeoutError('radar_driver_budget_exhausted')
    if write is post:
        with request_deadline(timeout):
            return write(url, token, {}, timeout=timeout)
    return write(url, token, {}, timeout=timeout)


def advance_windows(jobs, *, endpoint, token, write, limit, deadline, seconds, clock, record, submitted, owner=None):
    pool = (ProcessPoolExecutor(max_workers=2, mp_context=get_context('spawn'))
            if write is post else ThreadPoolExecutor(max_workers=2))
    due = deque(jobs)
    unvisited = {job['job_id'] for job in jobs}
    running = {}
    paused = set()
    transferring = set()
    busy_normal = {}
    latest = {}
    lease_observations = {}
    sent = 0
    probe_at = None
    timed_out = False

    def dispatch(job, slot):
        nonlocal sent
        path = f"/jobs/{job['job_id']}/advance"
        if slot == 'window':
            path += '?window_only=true'
        if owner:
            path += ('&' if slot == 'window' else '?') + 'owner=' + owner
        sent += 1
        submitted(job, slot)
        worker_deadline = monotonic() + max(0, deadline - clock())
        future = pool.submit(_advance, write, endpoint + path, token, seconds, worker_deadline)
        running[future] = (job, slot)

    def enqueue(job):
        if job not in due:
            due.append(job)

    try:
        while due or running or busy_normal:
            slots = {slot: job for job, slot in running.values()}
            can_start = deadline - clock() >= seconds and sent < limit
            if can_start and due and 'normal' not in slots:
                job = next((candidate for candidate in due if candidate['job_id'] in unvisited), due[0])
                due.remove(job)
                dispatch(job, 'normal')
                slots['normal'] = job
                unvisited.discard(job['job_id'])
                probe_at = clock() + .25
            normal = slots.get('normal')
            delay = None
            if can_start and 'window' not in slots and (normal is not None or busy_normal):
                # Each date first gets enough budget for a general advance.
                eligible = [job for job in due if job['job_id'] not in paused | transferring]
                if normal is not None and normal['job_id'] not in paused | transferring:
                    eligible.append(normal)
                eligible.extend(job for key, job in busy_normal.items() if key not in paused | transferring)
                if eligible and limit - sent > len(unvisited):
                    delay = max(0, probe_at - clock()) if normal is not None else 0
                    if not delay:
                        dispatch(eligible[0], 'window')
                        delay = None
            if not running:
                break
            remaining = deadline - clock()
            if remaining <= 0:
                timed_out = True
                for job, slot in running.values():
                    record(job, slot, None, TimeoutError('radar_driver_budget_exhausted'))
                    enqueue(job)
                break
            done, _ = wait(running, timeout=remaining if delay is None else min(remaining, delay),
                           return_when=FIRST_COMPLETED)
            for future in done:
                job, slot = running.pop(future)
                key = job['job_id']
                try:
                    status = future.result()
                    if any(status.get(k) != job['run_date'] for k in ('requested_run_date', 'effective_run_date')):
                        raise ValueError('radar_job_date_mismatch')
                except Exception as exc:
                    record(job, slot, None, exc)
                    if slot == 'window':
                        paused.add(key)
                    continue
                record(job, slot, status, None)
                previous = latest.get(key)
                if previous is None or status.get('observed_at', '') >= previous.get('observed_at', ''):
                    latest[key] = status
                observed = status.get('normal_observed_at', status.get('observed_at', ''))
                occupied = status.get('normal_slot_busy', status.get('advance_slot_busy') if slot == 'normal' else None)
                # SQLite may timestamp two reads within one millisecond. Let
                # a release win a tie so a delayed busy response cannot end work.
                observation = (observed, not occupied)
                previous_lease = lease_observations.get(key)
                if occupied is not None and (previous_lease is None or observation > previous_lease):
                    lease_observations[key] = observation
                    if occupied:
                        busy_normal[key] = job
                        if job in due:
                            due.remove(job)
                    elif not occupied:
                        busy_normal.pop(key, None)
                if slot == 'normal' and status.get('handoff_advanced'):
                    if status.get('handoff_pending'):
                        transferring.add(key)
                    else:
                        transferring.discard(key)
                effective = latest[key]
                if status.get('advanced_count') or status.get('handoff_advanced'):
                    if job in due:
                        due.remove(job)
                    if slot == 'normal':
                        paused.discard(key)
                elif slot == 'window' and (status.get('blocked_by_handoff') or status.get('handoff_pending')):
                    # This empty result only describes the transfer page. A
                    # general request may have finished the handoff since then.
                    probe_at = clock() + .25
                elif slot == 'window':
                    paused.add(key)
                normal_active = any(other_slot == 'normal' and other == job
                                    for other, other_slot in running.values())
                if key in transferring and not normal_active:
                    # Paging copies queue membership and can run beside a live lease.
                    enqueue(job)
                elif (key not in busy_normal and not normal_active
                        and (effective.get('advanced_count') or effective.get('runnable_now'))):
                    enqueue(job)
    finally:
        pool.shutdown(wait=not timed_out, cancel_futures=timed_out)
    remaining_jobs = list(due) + list(busy_normal.values())
    return remaining_jobs, timed_out or bool(remaining_jobs and deadline - clock() < seconds)
