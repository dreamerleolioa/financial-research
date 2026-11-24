from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from ai_stock_sentinel.daily_radar.jobs import reconcile_job
from ai_stock_sentinel.daily_radar.readiness import validation_inventory
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult, DailyRadarPreparedRun
from radar_calendar_support import calendar
from test_daily_radar_validation_read import add_result
from test_radar_job_postgres import pg_engine
from test_radar_jobs import db
from test_radar_window_replanning import DAY, NOW, active, setup


def later_result(db, defect=None):
    candidate, job = setup(db)
    work = active(db, job)[0]
    work.state, work.reason = 'needs_review', 'newer_validation_result_preserved'
    work.result = {'manual_repair_required': True}
    later = DAY + timedelta(days=2)
    if defect != 'missing_calendar':
        closed = (DAY - timedelta(days=3),) if defect == 'calendar_changed' else ()
        db.add(DailyRadarPreparedRun(run_date=later, market='TW', selected_symbols=[], universe=[],
            symbol_count=0, step_statuses={'validation-calendar': calendar(DAY-timedelta(days=364), later, closed).payload()}))
    add_result(db, candidate)
    row = db.scalar(select(DailyRadarForwardValidationResult))
    row.evaluation_as_of_date = later
    payload = deepcopy(row.outcome)
    payload.update(benchmark_return_pct=1, excess_return_vs_benchmark_pct=1,
        max_adverse_excursion_pct=-2, max_favorable_excursion_pct=3, entry_price=100, target_price=102)
    payload['observation_diagnostic'].update(confirmation_date='2026-06-04', waiting_end_date='2026-06-04')
    if defect == 'candidate_changed':
        payload['validation_input']['candidate_revision'] = '0' * 64
    elif defect == 'missing_input':
        payload.pop('validation_input')
    elif defect == 'missing_window':
        payload.pop('validation_window')
    elif defect == 'missing_return':
        payload.pop('forward_return_pct')
    elif defect in ('missing_lead', 'missing_risk', 'risk_string', 'event_outside_window'):
        diag = payload['observation_diagnostic']
        if defect == 'missing_lead':
            diag.pop('lead_trading_days')
        elif defect == 'missing_risk':
            diag.pop('waiting_max_adverse_excursion_pct')
        elif defect == 'risk_string':
            diag['waiting_max_adverse_excursion_pct'] = '-2'
        else:
            diag['confirmation_date'] = '2026-06-10'
    elif defect == 'malformed_diagnostic':
        payload['observation_diagnostic'] = ['invalid']
    elif defect == 'target_changed':
        row.target_date = later
    elif defect == 'benchmark_changed':
        row.benchmark_symbol = 'OTHER'
    elif defect == 'unproven_terminal':
        row.status, row.skip_reason = 'skipped', 'target_session_suspended'
    row.outcome = payload
    db.commit()
    return job, work, row


@pytest.mark.parametrize('defect', [None, 'missing_input', 'candidate_changed', 'missing_window',
    'missing_return', 'missing_lead', 'missing_risk', 'risk_string', 'event_outside_window', 'malformed_diagnostic',
    'target_changed', 'benchmark_changed', 'missing_calendar', 'calendar_changed', 'unproven_terminal'])
def test_later_result_settles_only_proven_work_and_preserves_history(db, defect):
    job, work, row = later_result(db, defect)
    original = (row.updated_at, row.target_date, row.status, deepcopy(row.outcome))
    for minutes in (51, 102):
        reconcile_job(db, job.id, now=NOW+timedelta(minutes=minutes), audit=True)
        if defect:
            assert active(db, job) == [work]
            assert work.state == 'needs_review'
            assert not job.scope['intent_complete']
        else:
            assert active(db, job) == [] and work.state == 'ready'
            assert work.result.get('manual_repair_required') is not True
            assert work.result['settled_by']['evaluation_as_of_date'] == '2026-06-10'
            assert job.scope['intent_complete'] is True
    assert validation_inventory(db, as_of_date=DAY)['signal_close']['missing_outcome_count'] == 1
    assert (row.updated_at, row.target_date, row.status, row.outcome) == original


@pytest.mark.parametrize('change,expected', [(False, 'ready'), (True, 'needs_review')])
def test_claim_rechecks_the_locked_result_without_fetching_prices(db, monkeypatch, change, expected):
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar import validation_execution

    job, work, row = later_result(db)
    def unexpected(*args, **kwargs):
        pytest.fail('an existing later result must be settled without fetching prices')
    monkeypatch.setattr(validation_execution, 'run_signal_close_validation', unexpected)
    collected = WorkHandlers(db).collect_window(SimpleNamespace(scope=work.scope, run_date=job.run_date))
    if change:
        row.outcome = {k: v for k, v in row.outcome.items() if k != 'validation_input'}
        db.commit()
    before = deepcopy(row.outcome)
    result = collected.apply(db)
    assert result['state'] == expected
    assert row.evaluation_as_of_date == DAY + timedelta(days=2) and row.outcome == before


def test_damaged_handoff_evidence_reopens_the_queue_without_changing_result(db):
    job, work, row = later_result(db)
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    assert job.scope['intent_complete'] and 'settled_by' in work.result
    row.outcome = {k: v for k, v in row.outcome.items() if k != 'validation_window'}
    db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=102), audit=True)
    assert work.state == 'needs_review' and 'settled_by' not in work.result
    assert not job.scope['intent_complete']


@pytest.mark.parametrize('defect', [None, 'date', 'sessions', 'event'])
def test_later_split_proof_can_identify_target_with_no_row_target(db, defect):
    from ai_stock_sentinel.daily_radar.jobs import create_job
    from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION

    _, _, row = later_result(db)
    job = create_job(db, run_date=DAY, lanes=['next_open'], now=NOW)
    work = active(db, job)[0]
    days = [f'2026-06-{day:02d}' for day in (2, 3, 4, 5, 8)]
    row.validation_version = RESEARCH_VALIDATION_VERSION
    row.status, row.skip_reason, row.target_date = 'skipped', 'stock_split_in_window', None
    row.outcome = {'validation_input': row.outcome['validation_input'], 'return_basis': 'next_open',
        'price_basis': 'unadjusted_price', 'expected_target_date': days[-1], 'session_dates': days,
        'split_event': {'date': days[2], 'symbol': work.scope['symbol'], 'ratio': 2,
                        'price_source': 'fixture', 'corporate_actions_checked': True}}
    payload = deepcopy(row.outcome)
    if defect == 'date':
        payload['expected_target_date'] = days[-2]
    elif defect == 'sessions':
        payload['session_dates'] = days[1:]
    elif defect == 'event':
        payload['split_event']['ratio'] = 1
    row.outcome = payload
    db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    assert work.state == ('needs_review' if defect else 'ready')
    assert row.target_date is None and row.outcome == payload


def test_planner_keeps_a_later_result_stable_until_completion(pg_engine):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.daily_radar.readiness import inventory_items
    from ai_stock_sentinel.db.models import Base

    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as planner, Session(pg_engine) as writer:
        _, work, row = later_result(planner)
        row_id = row.id
        items = list(inventory_items(planner, as_of_date=DAY, candidate_ids=[work.scope['candidate_id']],
                                     settle_later_results=True, lock_later_results=True))
        assert any(item.get('settled_by') for item in items)
        lock = select(DailyRadarForwardValidationResult.id).where(
            DailyRadarForwardValidationResult.id == row_id).with_for_update(nowait=True)
        with pytest.raises(OperationalError) as error:
            writer.scalar(lock)
        assert error.value.orig.pgcode == '55P03'
        writer.rollback()
        planner.rollback()
        assert writer.scalar(lock) == row_id


def test_new_job_cannot_certify_older_shared_work_with_ordinary_same_day_result(db):
    from ai_stock_sentinel.daily_radar.jobs import create_job, member

    older, work, row = later_result(db, 'missing_return')
    reconcile_job(db, older.id, now=NOW+timedelta(minutes=51), audit=True)
    newer = create_job(db, run_date=row.evaluation_as_of_date, lanes=['signal_close'], now=NOW)
    member(db, newer, work)
    db.commit()
    reconcile_job(db, newer.id, now=NOW+timedelta(minutes=52), audit=True)
    assert work not in active(db, newer)
    assert active(db, older) == [work] and work.state == 'needs_review'


@pytest.mark.parametrize('damage', [None, 'digest', 'missing_bars', 'wrong_sessions', 'wrong_price',
                                    'wrong_benchmark_return', 'wrong_diagnostic'])
def test_later_price_lock_verifies_archived_bars_before_settlement(db, damage):
    from ai_stock_sentinel.daily_radar.price_acceptance import PRICE_EVIDENCE, VERSION
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

    job, work, row = later_result(db)
    dates = [f'2026-06-{d:02d}' for d in (1, 2, 3, 4, 5, 8)]
    if damage == 'wrong_sessions':
        dates[-2] = '2026-06-06'
    bars = [{'date': d, 'open': 100, 'high': 103, 'low': 98, 'close': 100} for d in dates]
    for bar in bars[2:4]:
        bar.update(close=105, high=106)
    bars[-1]['close'] = 100 if damage == 'wrong_price' else 102
    benchmark = [bar | {'close': 100} for bar in bars]
    benchmark[-1]['close'] = 100 if damage == 'wrong_benchmark_return' else 101
    proof = {'version': VERSION, 'symbol': work.scope['symbol'], 'return_basis': 'signal_close',
        'signal_date': dates[0], 'stock': bars, 'benchmark': benchmark}
    proof['sha256'] = '0'*64 if damage == 'digest' else fingerprint(proof)
    if damage == 'missing_bars':
        proof.pop('stock')
    row.outcome = row.outcome | {PRICE_EVIDENCE: proof, 'max_favorable_excursion_pct': 6}
    if damage == 'wrong_diagnostic':
        diagnostic = row.outcome['observation_diagnostic'] | {'waiting_max_adverse_excursion_pct': -1}
        row.outcome = row.outcome | {'observation_diagnostic': diagnostic}
    db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    assert work.state == ('needs_review' if damage else 'ready')
    assert row.outcome[PRICE_EVIDENCE] == proof


@pytest.mark.parametrize('busy_row', ['work', 'result_during_final_audit', 'result_during_replan'])
def test_contended_settlement_keeps_job_incomplete_until_rechecked(pg_engine, busy_row):
    from sqlalchemy.orm import Session
    from ai_stock_sentinel.db.models import Base, RadarWorkItem

    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as planner, Session(pg_engine) as writer:
        planner.execute(text("SET lock_timeout = '2s'"))
        job, work, row = later_result(planner)
        if busy_row == 'work':
            writer.scalar(select(RadarWorkItem.id).where(RadarWorkItem.id == work.id).with_for_update())
        else:
            reconcile_job(planner, job.id, now=NOW+timedelta(minutes=51), audit=True)
            assert job.scope['intent_complete']
            writer.scalar(select(DailyRadarForwardValidationResult.id).where(
                DailyRadarForwardValidationResult.id == row.id).with_for_update())
        minute = 102 if busy_row == 'result_during_replan' else 52
        reconcile_job(planner, job.id, now=NOW+timedelta(minutes=minute), audit=True)
        assert not job.scope['intent_complete']
        if busy_row == 'work':
            assert active(planner, job) == [work]
        if busy_row == 'result_during_replan':
            assert work.state == 'ready' and 'settled_by' in work.result
            assert job.scope['window_planning']['complete']
            assert not job.scope['window_result_audit']['complete']
        writer.rollback()
        reconcile_job(planner, job.id, now=NOW+timedelta(minutes=minute+1), audit=True)
        assert job.scope['intent_complete'] and work.state == 'ready'


def test_rejected_epoch_rolls_back_shared_work_settlement(db, monkeypatch):
    from ai_stock_sentinel.daily_radar import planning_inventory

    job, work, _ = later_result(db)
    original = planning_inventory.inventory_input_revision
    reads = []
    def revision(*args, **kwargs):
        reads.append(1)
        return original(*args, **kwargs) if len(reads) == 1 else 'different_epoch'
    monkeypatch.setattr(planning_inventory, 'inventory_input_revision', revision)
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    assert not job.scope['window_planning']['complete']
    assert work.state == 'needs_review' and 'settled_by' not in work.result


def test_older_planner_checks_the_cutoff_of_a_concurrently_inserted_window(db, monkeypatch):
    from ai_stock_sentinel.daily_radar import jobs

    job, old, row = later_result(db, 'missing_return')
    old.work_key = '1' * 64
    db.commit()
    ensure = jobs.ensure_work
    inserted = []
    def insert_other_first(session, **kwargs):
        if kwargs['stage'] == 'window' and 'cutoff' not in kwargs['identity'] and not inserted:
            day = row.evaluation_as_of_date
            inserted.append(ensure(session, **(kwargs | {'run_date': day, 'state': 'pending',
                'scope': kwargs['scope'] | {'as_of_date': str(day)}})))
        return ensure(session, **kwargs)
    monkeypatch.setattr(jobs, 'ensure_work', insert_other_first)
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    waiting, = active(db, job)
    assert waiting.identity.get('cutoff') == str(job.run_date) and waiting.state == 'needs_review'
    assert inserted[0].state == 'pending'


@pytest.mark.parametrize('format_', ['raw_lock', 'reviewed_lock', 'historical_repair'])
@pytest.mark.parametrize('wrong_entry', [False, True])
def test_archived_next_open_metrics_use_the_first_session_open(db, format_, wrong_entry):
    from ai_stock_sentinel.daily_radar.jobs import create_job
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
    from ai_stock_sentinel.daily_radar.price_acceptance import PRICE_EVIDENCE, VERSION, research_rows
    from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION, evaluate_research_window
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

    _, _, row = later_result(db)
    job = create_job(db, run_date=DAY, lanes=['next_open'], now=NOW)
    work, = active(db, job)
    signal = row.signal_date
    candidate, = forward_validation_candidates_from_runs(db, market='TW', start_date=signal, end_date=signal)
    dates = [f'2026-06-{day:02d}' for day in (1, 2, 3, 4, 5, 8)]
    stock = research_rows([{'date': d, 'open': 98, 'high': 103, 'low': 97, 'close': 102,
                            'price_source': 'test'} for d in dates], {})
    benchmark = [bar | {'open': 99} for bar in stock]
    computed = evaluate_research_window(candidate, price_series=stock, benchmark_prices=benchmark, window_days=5,
        as_of_date=row.evaluation_as_of_date, benchmark_symbol=row.benchmark_symbol)
    assert computed['outcome']['entry_price'] == 98
    outcome = computed['outcome']
    proof = {'version': VERSION, 'symbol': candidate['symbol'], 'return_basis': 'next_open', 'signal_date': str(signal)}
    if format_ != 'raw_lock':
        archived = {'symbol': candidate['symbol'], 'signal_date': str(signal), 'session_dates': dates[1:],
                    'calculation_stock': stock[1:], 'calculation_benchmark': benchmark}
        outcome['reviewed_official_window'] = archived
        proof['reviewed_official_window_sha256'] = fingerprint(archived)
    else:
        proof.update(stock=stock, benchmark=benchmark)
    if format_ != 'historical_repair':
        outcome[PRICE_EVIDENCE] = proof | {'sha256': fingerprint(proof)}
    if wrong_entry:
        outcome['entry_price'] = 102
    row.validation_version, row.outcome = RESEARCH_VALIDATION_VERSION, outcome
    db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    assert work.state == ('needs_review' if wrong_entry else 'ready')
    assert row.outcome == outcome


@pytest.mark.parametrize('claimed', ['already_broken_out', 'already_invalidated'])
def test_immediate_observation_checks_the_original_signal_without_price_bars(db, claimed):
    job, work, row = later_result(db)
    diagnostic = {'version': row.outcome['observation_diagnostic']['version'], 'status': claimed}
    row.outcome = row.outcome | {'observation_diagnostic': diagnostic}
    db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    assert work.state == 'needs_review'


@pytest.mark.parametrize('price,status', [(105, 'already_broken_out'), (95, 'already_invalidated')])
def test_signal_alone_proves_an_immediate_observation(db, price, status):
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
    from ai_stock_sentinel.daily_radar.observation_validation import complete_window_diagnostic

    _, _, row = later_result(db)
    signal = row.signal_date
    candidate, = forward_validation_candidates_from_runs(db, market='TW', start_date=signal, end_date=signal)
    candidate['input_snapshot'] = candidate['input_snapshot'] | {'ohlcv': {'close': price}}
    assert complete_window_diagnostic(candidate, {'status': status}, [DAY])
