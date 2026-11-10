from dataclasses import replace
from datetime import timedelta

import pytest

from ai_stock_sentinel.daily_radar import next_open_history_repair as tool
from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs, persisted_forward_validation_outcomes
from ai_stock_sentinel.daily_radar.readiness import classify_window
from ai_stock_sentinel.daily_radar.trading_calendar import TradingCalendar
from test_next_open_action_scope import synthetic_case, SIGNAL, DAY
from test_next_open_history_repair import db
from test_radar_job_postgres import pg_engine


def changed_calendar(cal, removed, added):
    reports = []
    for report in cal.reports:
        days = set(report['session_dates']) - {str(removed)}
        if str(added).startswith(report['month']): days.add(str(added))
        reports.append(report | {'session_dates': sorted(days), 'snapshot_sha256': tool.fingerprint(sorted(days))})
    return TradingCalendar.parse(replace(cal, sessions=tuple(sorted((set(cal.sessions)-{removed}) | {added})),
        reports=tuple(reports)).payload())


@pytest.mark.parametrize('window', [5, 10, 20, 40, 60])
@pytest.mark.parametrize('position', [0, 1])
def test_same_target_does_not_certify_a_changed_complete_window(db, window, position):
    cal, provider, row, before, request = synthetic_case(db, window=window)
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit()
    altered = changed_calendar(cal, provider.future[position], SIGNAL+timedelta(days=5))
    candidate = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
    summary = persisted_forward_validation_outcomes(db, [candidate], windows=[window], as_of_date=DAY,
        validation_version=tool.RESEARCH_VALIDATION_VERSION)[0]
    state, reason, target = classify_window(candidate, summary, basis='next_open', window=window,
        as_of_date=DAY, calendar=altered)
    assert str(target) == str(row.target_date)
    assert state == 'invalid_window' and reason == 'calendar_window_mismatch'
    assert tool._manual(candidate, tool.result_snapshot(row), altered)[0] is True


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_unknown_window_is_not_inferred_from_target_or_entry(basis):
    from ai_stock_sentinel.daily_radar.window_identity import window_error, identity
    sessions = [SIGNAL+timedelta(days=i) for i in (1, 2, 3, 4, 7)]
    row = {'outcome': {'entry_date': str(sessions[0])}}
    assert window_error(row, signal=SIGNAL, sessions=sessions, basis=basis) == 'calendar_window_unverified'
    row['outcome']['validation_window'] = identity(SIGNAL, sessions, basis)
    assert window_error(row, signal=SIGNAL, sessions=sessions, basis=basis) is None
    altered = [sessions[0], sessions[1], SIGNAL+timedelta(days=5), *sessions[3:]]
    assert window_error(row, signal=SIGNAL, sessions=sorted(altered), basis=basis) == 'calendar_window_mismatch'


def test_legacy_reviewed_dates_survive_summary_projection_without_large_evidence(db):
    cal, provider, row, before, request = synthetic_case(db)
    ev = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=ev)
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit(); db.refresh(row)
    plan['results'][0]['after']['outcome'].pop('validation_window')
    plan['sha256'] = tool.fingerprint({k:v for k,v in plan.items() if k != 'sha256'})
    row.outcome = {k:v for k,v in row.outcome.items() if k != 'validation_window'}; db.commit()
    c = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
    saved = persisted_forward_validation_outcomes(db, [c], windows=[20], as_of_date=DAY,
        validation_version=tool.RESEARCH_VALIDATION_VERSION)[0]
    assert 'reviewed_official_window' not in saved['outcome']
    assert saved['window_session_dates'] == [str(d) for d in provider.future[:20]]
    assert classify_window(c, saved, basis='next_open', window=20, as_of_date=DAY, calendar=cal)[0] == 'validated'
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True)


def test_due_keeps_reviewed_windows_manual_and_recomputes_ordinary_changed_windows(db):
    from ai_stock_sentinel.daily_radar.forward_validation import exclude_persisted_daily_radar_windows
    cal, p, row, before, req = synthetic_case(db)
    ev = tool.capture_evidence(db.get_bind(), req, calendar=cal, provider=p)
    plan = tool.build_repair_plan(db.get_bind(), req, calendar=cal, evidence=ev)
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit(); db.refresh(row)
    altered = changed_calendar(cal, p.future[1], SIGNAL+timedelta(days=5))
    pending = {f'id:{row.candidate_id}': [20]}
    kwargs = {'validation_version': tool.RESEARCH_VALIDATION_VERSION, 'calendar': altered}
    assert exclude_persisted_daily_radar_windows(db, pending, **kwargs) == {}
    row.outcome = {k:v for k,v in row.outcome.items() if k not in ('historical_repair', 'reviewed_official_window')}; db.commit()
    assert exclude_persisted_daily_radar_windows(db, pending, **kwargs) == pending
    kwargs['calendar'] = cal
    assert exclude_persisted_daily_radar_windows(db, pending, **kwargs) == {}


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_public_calendar_check_rejects_interior_change_for_both_bases(basis):
    from ai_stock_sentinel.daily_radar.window_identity import identity
    from ai_stock_sentinel.daily_radar.trading_calendar import check_outcome_calendar
    days = [SIGNAL+timedelta(days=i) for i in (1, 2, 3, 4, 7)]
    row = {'candidate_id': 1, 'signal_date': str(SIGNAL), 'window_days': 5, 'target_date': str(days[-1]),
        'status': 'validated', 'validation_version': tool.RESEARCH_VALIDATION_VERSION if basis == 'next_open' else 'daily-radar-forward-v1',
        'outcome': {'validation_window': identity(SIGNAL, days, basis)}}
    calendar_days = [SIGNAL, days[0], days[1], SIGNAL+timedelta(days=5), *days[3:]]
    checked, invalid = check_outcome_calendar([row], calendar_days)
    assert checked[0]['status'] == 'skipped' and len(invalid) == 1


def test_due_with_no_saved_calendar_fetches_one_before_certifying_ordinary_rows(db):
    from types import SimpleNamespace
    from sqlalchemy import select
    from ai_stock_sentinel.db.models import DailyRadarPreparedRun
    from ai_stock_sentinel.daily_radar.validation_inputs import bind_validation_input
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
    from ai_stock_sentinel.daily_radar.research_validation import run_research_validation
    from test_next_open_history_repair import Prices
    cal, p, row, before, request = synthetic_case(db)
    candidate = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
    row.outcome = bind_validation_input(candidate, {'outcome': {'return_basis': 'next_open', 'price_basis': 'unadjusted_price'}})['outcome']
    row.status, row.target_date = 'validated', p.target
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {}; db.commit()
    called = []
    def fetch(**kwargs): called.append(kwargs); return cal
    result = run_research_validation(db, DailyRadarForwardValidationRunRequest(mode='due', return_basis='next_open',
        as_of_date=DAY, windows=[20]), as_of_date=DAY, price_provider=Prices(cal), calendar_provider=SimpleNamespace(fetch=fetch))
    assert len(called) == 1
    assert result['records_written'] == 1
    db.refresh(row)
    assert row.outcome['validation_window']['target_date'] == str(p.target)


def test_spx_completion_does_not_consume_an_unrelated_taiwan_calendar(db):
    from ai_stock_sentinel.db.models import DailyRadarCandidate
    from ai_stock_sentinel.daily_radar.forward_validation import exclude_persisted_daily_radar_windows
    cal, p, row, before, req = synthetic_case(db)
    candidate = db.get(DailyRadarCandidate, row.candidate_id)
    candidate.input_snapshot = dict(candidate.input_snapshot) | {'replay_input': {'market_context': {'benchmark': {'symbol': 'SPX'}}}}
    row.outcome = {}; row.benchmark_symbol = 'SPX'; row.status = 'validated'; row.target_date = p.target
    db.commit()
    assert exclude_persisted_daily_radar_windows(db, {f'id:{row.candidate_id}': [20]}, calendar=cal,
        benchmark_symbol='SPX', validation_version=tool.RESEARCH_VALIDATION_VERSION) == {}


def test_signal_close_manual_inventory_detects_same_target_interior_change(db):
    from scripts.repair_daily_radar_history import inventory
    from test_radar_history_repair import stored, calendar
    _, c, row = stored(db)
    cal = calendar()
    days = [d for d in cal.sessions if d > row.signal_date]
    altered = changed_calendar(cal, days[1], row.signal_date+timedelta(days=3))
    targets = inventory(db, altered)['results']
    assert {'id': row.id, 'operation': 'recompute'} in targets
