"""Ordinary reads must not materialize raw repair evidence or truncate coverage."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import event, select

from ai_stock_sentinel.daily_radar import forward_validation as validation, jobs
from ai_stock_sentinel.daily_radar.history_repair import result_snapshot
from ai_stock_sentinel.daily_radar.readiness import classify_window, validation_inventory
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.validation_inputs import bind_validation_input
from ai_stock_sentinel.daily_radar.validation_read import read_observation_validation
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result, RadarWorkItem
from test_next_open_history_repair import db, seed, DAY
from test_radar_job_postgres import pg_engine


def seed_both(db):
    cal, _, _, _ = seed(db)
    snapshot = validation.forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
    for row in list(db.scalars(select(Result))):
        row.outcome = bind_validation_input(snapshot, {'outcome': row.outcome})['outcome']
        payload = deepcopy(row.outcome) | {'observation_diagnostic': {
            'version': 'daily-radar-observation-v1', 'status': 'confirmed',
            'first_seen_date': snapshot['record_date'], 'lead_trading_days': 3,
            'waiting_max_adverse_excursion_pct': -2}}
        db.add(Result(candidate_id=row.candidate_id, signal_date=row.signal_date,
                      window_days=row.window_days, target_date=row.target_date,
                      benchmark_symbol='TAIEX', status='validated', evaluation_as_of_date=DAY,
                      validation_version=validation.FORWARD_VALIDATION_VERSION, outcome=payload))
    db.commit()
    return cal, snapshot


def read_both(db, snapshot):
    return [row for version in (validation.FORWARD_VALIDATION_VERSION, RESEARCH_VALIDATION_VERSION)
            for row in validation.persisted_forward_validation_outcomes(
                db, [snapshot], windows=[5, 10, 20, 40, 60], as_of_date=DAY, validation_version=version)]


def test_annual_readers_project_raw_evidence_in_sql_and_keep_all_windows(db):
    _, snapshot = seed_both(db)
    before = read_both(db, snapshot)
    inventory = validation_inventory(db, as_of_date=DAY, lookback_days=365)
    assert all(inventory[basis]['expected_window_count'] == inventory[basis]['validated_count'] == 5
               for basis in ('signal_close', 'next_open'))
    public = read_observation_validation(db, market='TW', as_of_date=DAY, lookback_days=365).model_dump()
    for row in db.scalars(select(Result)):
        row.outcome = dict(row.outcome) | {
            'reviewed_official_window': {'raw_report': 'x' * 100_000},
            'historical_repair': dict(row.outcome['historical_repair']) | {
                'prior_evidence': {'historical_repair': {'prior_evidence': {'raw_report': 'x' * 100_000}}}}}
    db.commit()
    revisions = {row.id: validation.forward_validation_result_revision(row) for row in db.scalars(select(Result))}
    db.expunge_all()
    selected = []
    def capture(state):
        if state.is_select:
            selected.extend(state.statement.selected_columns)
    event.listen(db, 'do_orm_execute', capture)
    try:
        actual = read_both(db, snapshot)
        assert actual == before
        assert len(actual) == 10
        assert len(json.dumps(actual)) < 50_000
        assert validation_inventory(db, as_of_date=DAY, lookback_days=365) == inventory
        assert read_observation_validation(db, market='TW', as_of_date=DAY, lookback_days=365).model_dump() == public
        assert not any(col.compare(Result.outcome.__clause_element__()) for col in selected)
        assert not any(isinstance(row, Result) for row in db.identity_map.values())
    finally:
        event.remove(db, 'do_orm_execute', capture)
    # Repair snapshots and write preimages still see every byte of evidence.
    for row in db.scalars(select(Result)):
        assert len(result_snapshot(row)['outcome']['reviewed_official_window']['raw_report']) == 100_000
        assert row.outcome['historical_repair']['prior_evidence']['historical_repair']['prior_evidence']
        assert validation.forward_validation_result_revision(row) == revisions[row.id]


@pytest.mark.parametrize('defect', [None, 'event', 'input'])
def test_light_reader_preserves_terminal_and_input_identity_checks(db, defect):
    cal, snapshot = seed_both(db)
    row = db.scalar(select(Result).where(Result.window_days == 20, Result.validation_version == RESEARCH_VALIDATION_VERSION))
    sessions = [d for d in cal.sessions if str(d) > snapshot['record_date']][:20]
    row.status, row.skip_reason, row.target_date = 'skipped', 'stock_split_in_window', None
    row.outcome = dict(row.outcome) | {'expected_target_date': str(sessions[-1]),
        'session_dates': [str(d) for d in sessions],
        'split_event': {'date': str(sessions[1]) if defect != 'event' else str(DAY),
                        'symbol': snapshot['symbol'], 'ratio': 2, 'price_source': 'yfinance',
                        'corporate_actions_checked': True},
        'reviewed_official_window': {'raw': 'x' * 100_000},
        'historical_repair': {'repair_id': 'reviewed', 'prior_evidence': {'raw': 'x' * 100_000}}}
    if defect == 'input':
        row.outcome = dict(row.outcome) | {'validation_input': {'version': 'radar-validation-input-v1', 'candidate_revision': 'stale'}}
    db.commit()
    saved = validation.persisted_forward_validation_outcomes(db, [snapshot], windows=[20], as_of_date=DAY,
                                                           validation_version=RESEARCH_VALIDATION_VERSION)[0]
    state, reason, _ = classify_window(snapshot, saved, basis='next_open', window=20, as_of_date=DAY, calendar=cal)
    assert state == {None: 'terminal', 'event': 'retryable', 'input': 'input_unverified'}[defect]
    assert saved['outcome']['split_event'] == row.outcome['split_event']
    assert saved['outcome']['historical_repair'] == {'repair_id': 'reviewed'}
    assert 'reviewed_official_window' not in saved['outcome']


@pytest.mark.parametrize('marker', ['absent', 'null', 'empty', 'prior_only', 'prior_only_null', 'metadata'])
def test_window_exclusion_projects_evidence_and_preserves_prior_only_ownership(db, marker):
    _, snapshot = seed_both(db)
    row = db.scalar(select(Result).where(Result.window_days == 20, Result.validation_version == RESEARCH_VALIDATION_VERSION))
    row.status, row.skip_reason, row.target_date = 'skipped', 'missing_benchmark_price', None
    payload = {'reviewed_official_window': {'raw': 'x' * 100_000}}
    values = {'null': None, 'empty': {}, 'prior_only': {'prior_evidence': {'raw': 'x' * 100_000}},
              'prior_only_null': {'prior_evidence': None}, 'metadata': {'repair_id': 'reviewed', 'prior_evidence': {}}}
    if marker != 'absent':
        payload['historical_repair'] = values[marker]
    row.outcome = payload
    db.commit()
    db.expunge_all()
    selected = []
    def capture(state):
        if state.is_select:
            selected.extend(state.statement.selected_columns)
    event.listen(db, 'do_orm_execute', capture)
    pending = {f"id:{snapshot['candidate_id']}": [20]}
    try:
        actual = validation.exclude_persisted_daily_radar_windows(db, pending, benchmark_symbol='TAIEX',
            validation_version=RESEARCH_VALIDATION_VERSION, as_of_date=DAY)
        assert actual == ({} if marker in {'prior_only', 'prior_only_null', 'metadata'} else pending)
        assert not any(col.compare(Result.outcome.__clause_element__()) for col in selected)
    finally:
        event.remove(db, 'do_orm_execute', capture)


@pytest.mark.parametrize('closed', [False, True])
def test_job_status_projects_old_large_work_payloads_without_changing_state(db, closed):
    job = jobs.create_job(db, run_date=DAY, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    if closed:
        work = db.scalar(select(RadarWorkItem).where(RadarWorkItem.stage == 'market-session'))
        work.state, work.result = 'terminal', {'status': 'closed'}
        db.commit()
    baseline = jobs.job_status(db, job.id)
    assert (baseline['publication']['state'] == 'closed') == closed
    for work in db.scalars(select(RadarWorkItem)):
        work.result = dict(work.result or {}) | {'before': {'reviewed_official_window': {'raw': 'x' * 100_000}}}
    db.commit()
    job_id = job.id
    db.expunge_all()
    selected = []
    def capture(state):
        if state.is_select:
            selected.extend(state.statement.selected_columns)
    event.listen(db, 'do_orm_execute', capture)
    try:
        assert jobs.job_status(db, job_id) == baseline
        assert not any(col.compare(RadarWorkItem.result.__clause_element__()) for col in selected)
        assert not any(col.compare(RadarWorkItem.scope.__clause_element__()) for col in selected)
        assert not any(isinstance(row, RadarWorkItem) for row in db.identity_map.values())
    finally:
        event.remove(db, 'do_orm_execute', capture)
