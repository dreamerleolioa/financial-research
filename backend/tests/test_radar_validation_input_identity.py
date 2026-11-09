from datetime import date, timedelta

import pytest
from sqlalchemy import select

from ai_stock_sentinel.daily_radar import jobs
from ai_stock_sentinel.daily_radar.forward_validation import (
    FORWARD_VALIDATION_VERSION, forward_validation_candidates_from_runs,
    persisted_forward_validation_outcomes,
)
from ai_stock_sentinel.daily_radar.readiness import validation_candidate_revision, validation_inventory
from ai_stock_sentinel.daily_radar.validation_read import read_observation_validation
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
from test_radar_jobs import db
from test_radar_window_replanning import setup, active, DAY, NOW
from test_daily_radar_validation_read import add_result


def proof(candidate):
    return {'version': 'radar-validation-input-v1',
            'candidate_revision': validation_candidate_revision(candidate)}


def saved(db, *, verified=True):
    candidate, job = setup(db)
    add_result(db, candidate)
    row = db.scalar(select(DailyRadarForwardValidationResult))
    snapshot = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
    if verified:
        row.outcome = dict(row.outcome) | {'validation_input': proof(snapshot)}
    else:
        row.outcome = {k: v for k, v in row.outcome.items() if k != 'validation_input'}
    db.commit()
    jobs.reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    return candidate, job, row


def test_changed_inputs_do_not_certify_a_new_work_with_an_old_result(db):
    candidate, job, row = saved(db)
    old = active(db, job)[0].id
    candidate.input_snapshot = dict(candidate.input_snapshot) | {'ohlcv': {'close': 110}}
    db.commit()
    jobs.reconcile_job(db, job.id, now=NOW+timedelta(minutes=52), audit=True)
    current = active(db, job)[0]
    assert current.id != old
    assert current.state == 'needs_review'
    assert current.reason == 'validation_input_unverified'
    assert validation_inventory(db, as_of_date=DAY)['signal_close']['ready'] is False
    assert job.scope['intent_complete'] is False
    assert row.status == 'validated' and row.outcome['forward_return_pct'] == 2


def test_legacy_return_without_input_proof_is_not_ready_or_public_performance(db):
    _, job, row = saved(db, verified=False)
    assert active(db, job)[0].state == 'needs_review'
    report = read_observation_validation(db, market='TW', as_of_date=DAY, lookback_days=365)
    group = report.cohorts[0].windows['5']['all_selected']
    assert group.coverage_complete is False
    assert group.evaluated_observation_count == 0
    assert row.status == 'validated' and 'validation_input' not in row.outcome


def test_matching_input_proof_is_ready_and_display_changes_keep_identity(db):
    candidate, job, _ = saved(db)
    old = active(db, job)[0].id
    candidate.name = '顯示名稱修正'
    db.commit(); jobs.reconcile_job(db, job.id, now=NOW+timedelta(minutes=52), audit=True)
    assert active(db, job)[0].id == old
    assert active(db, job)[0].state == 'ready'


@pytest.mark.parametrize('version', [FORWARD_VALIDATION_VERSION, 'daily-radar-next-open-price-v2'])
def test_persisted_read_rejects_mismatched_input_proof_for_both_bases(db, version):
    candidate, _, row = saved(db)
    row.validation_version = version
    row.outcome = dict(row.outcome) | {'validation_input': {'version': 'radar-validation-input-v1', 'candidate_revision': '0'*64}}
    db.commit()
    candidates = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)
    result = persisted_forward_validation_outcomes(db, candidates, windows=[5], as_of_date=DAY, validation_version=version)[0]
    assert result['status'] == 'skipped'
    assert result['skip_reason'] == 'validation_input_unverified'
    assert row.status == 'validated'


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_both_evaluators_bind_all_five_complete_windows(basis):
    from ai_stock_sentinel.daily_radar.forward_validation import build_forward_validation_report
    from ai_stock_sentinel.daily_radar.research_validation import evaluate_research_window
    from test_daily_radar_research_validation import price
    candidate = {'candidate_id': 1, 'symbol': '2330.TW', 'record_date': '2026-06-01',
        'data_dates': {'ohlcv': '2026-06-01'}, 'input_snapshot': {'ohlcv': {'close': 100}}}
    days = [date(2026, 6, 1)+timedelta(days=i) for i in range(95)
        if (date(2026, 6, 1)+timedelta(days=i)).weekday() < 5]
    prices = [price(str(d), open_=100+i, close=100+i) for i,d in enumerate(days)]
    if basis == 'signal_close':
        rows = build_forward_validation_report([candidate], price_series_by_symbol={'2330.TW': prices},
            benchmark_prices=prices, market='TW', sample_source='unit', as_of_date=days[-1],
            windows=[5,10,20,40,60], trading_calendar=days).outcomes
    else:
        rows = [evaluate_research_window(candidate, price_series=prices, benchmark_prices=prices,
            window_days=w, as_of_date=days[-1], benchmark_symbol='TAIEX', trading_calendar=days)
            for w in (5,10,20,40,60)]
    assert {r['window_days'] for r in rows} == {5,10,20,40,60}
    assert all(r['status'] == 'validated' and r['outcome']['validation_input'] == proof(candidate) for r in rows)


def test_writer_keeps_collected_revision_and_never_stamps_current_inputs(db):
    from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
    from ai_stock_sentinel.daily_radar.validation_inputs import bind_validation_input
    candidate, _ = setup(db)
    evaluated = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
    result = bind_validation_input(evaluated, {'candidate_id': candidate.id, 'symbol': candidate.symbol,
        'signal_date': '2026-06-01', 'window_days': 5, 'validation_version': FORWARD_VALIDATION_VERSION,
        'benchmark_symbol': 'TAIEX', 'evaluation_as_of_date': str(DAY), 'target_date': str(DAY),
        'status': 'validated', 'skip_reason': None, 'outcome': {'forward_return_pct': 2}})
    candidate.input_snapshot = dict(candidate.input_snapshot) | {'ohlcv': {'close': 110}}
    db.commit()
    upsert_forward_validation_results(db, [result]); db.commit()
    stored = db.scalar(select(DailyRadarForwardValidationResult))
    assert stored.outcome['validation_input'] == proof(evaluated)
    current = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)
    assert stored.outcome['validation_input'] != proof(current[0])
    row = persisted_forward_validation_outcomes(db, current, windows=[5], as_of_date=DAY)[0]
    assert row['skip_reason'] == 'validation_input_unverified'


def test_public_read_never_materializes_unrelated_audit_snapshots(db):
    from test_daily_radar_validation_read import add_candidate
    candidate, _, _ = saved(db)
    audit = add_candidate(db, candidate.run, '2317.TW', status='shadow')
    audit.shadow_cohort = 'eligibility_audit'
    # Public reads have no contract with audit-only payloads. Materializing this
    # unrelated row would fail normalization (and scale with the entire pool).
    audit.bucket_scores = 'not a public score mapping'
    db.commit()
    report = read_observation_validation(db, market='TW', as_of_date=DAY, lookback_days=365)
    assert len(report.cohorts) == 1
