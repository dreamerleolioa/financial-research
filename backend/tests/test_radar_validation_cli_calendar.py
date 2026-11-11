"""CLI writes need the same independent session proof as the internal API."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts import daily_radar_forward_validation as cli
from ai_stock_sentinel.daily_radar.forward_validation import FORWARD_VALIDATION_VERSION
from ai_stock_sentinel.daily_radar.history_repair import result_snapshot
from ai_stock_sentinel.daily_radar.window_identity import identity
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, DailyRadarForwardValidationResult as Result
from test_next_open_history_repair import db, Prices, SIGNAL, DAY
from test_radar_job_postgres import pg_engine
from test_radar_public_window_eligibility import seed_basis
from test_daily_radar_validation_read import add_run, add_candidate
from radar_calendar_support import calendar, TradingCalendar

WINDOWS = [5, 10, 20, 40, 60]


def invoke(db, monkeypatch, tmp_path, cal, *, write=True, missing_symbol=None,
           market='TW', benchmark='TAIEX', as_of_date=DAY, end_date=SIGNAL):
    monkeypatch.setattr(cli, '_get_session_local', lambda: lambda: Session(db.get_bind()))
    prices = Prices(cal)
    missing_day = [d for d in cal.sessions if d > SIGNAL][1]

    def load(session, **kwargs):
        rows = prices.fetch(kwargs['symbols'], start_date=kwargs['start_date'], end_date=kwargs['end_date'])
        if missing_symbol in rows:
            rows[missing_symbol] = [r for r in rows[missing_symbol] if r['date'] != str(missing_day)]
        return rows

    monkeypatch.setattr(cli, 'load_price_series_from_raw_data', load)
    output = tmp_path/'report.json'
    argv = ['daily_radar_forward_validation.py', '--source', 'db', '--market', market,
        '--benchmark-symbol', benchmark, '--start-date', str(SIGNAL), '--end-date', str(end_date),
        '--as-of-date', str(as_of_date), '--windows', ','.join(map(str, WINDOWS)), '--output', str(output)]
    monkeypatch.setattr('sys.argv', argv+(['--write-results'] if write else []))
    cli.main()
    return output


def saved(session):
    return {r.id: result_snapshot(r) for r in session.scalars(select(Result))}


@pytest.mark.parametrize('missing_symbol,reason', [('TAIEX', 'missing_benchmark'), ('2330.TW', 'missing_future_price')])
def test_price_gap_cannot_shift_any_cli_written_window(db, monkeypatch, tmp_path, missing_symbol, reason):
    cal = seed_basis(db, 'signal_close')
    future = [d for d in cal.sessions if d > SIGNAL]
    invoke(db, monkeypatch, tmp_path, cal, missing_symbol=missing_symbol)
    db.expire_all()
    rows = list(db.scalars(select(Result).where(Result.validation_version == FORWARD_VALIDATION_VERSION)))
    assert {r.window_days for r in rows} == set(WINDOWS)
    assert all(r.status == 'skipped' and r.skip_reason == reason and r.target_date is None for r in rows)
    assert all(r.outcome['expected_target_date'] == str(future[r.window_days-1]) for r in rows)


def test_full_cli_windows_follow_official_closures_and_special_opening(db, monkeypatch, tmp_path):
    cal = seed_basis(db, 'signal_close')
    payload = calendar(cal.start, DAY, {date(2026, 6, 2)}).payload()
    saturday = '2026-06-06'
    payload['sessions'] = sorted(payload['sessions']+[saturday])
    report = next(r for r in payload['reports'] if r['month'] == '2026-06')
    report['session_dates'] = sorted(report['session_dates']+[saturday])
    proof = TradingCalendar.parse(payload)
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {'validation-calendar': proof.payload()}
    db.commit()
    invoke(db, monkeypatch, tmp_path, proof)
    db.expire_all()
    for row in db.scalars(select(Result).where(Result.validation_version == FORWARD_VALIDATION_VERSION)):
        expected = [d for d in proof.sessions if d > SIGNAL][:row.window_days]
        assert row.status == 'validated' and row.target_date == expected[-1]
        assert row.outcome['validation_window'] == identity(SIGNAL, expected, 'signal_close')


@pytest.mark.parametrize('maturing_window', WINDOWS)
@pytest.mark.parametrize('before_target', [False, True])
def test_cli_maturity_requires_the_exact_number_of_official_sessions(db, monkeypatch, tmp_path,
                                                                   maturing_window, before_target):
    cal = seed_basis(db, 'signal_close')
    future = [d for d in cal.sessions if d > SIGNAL]
    mature_count = maturing_window-int(before_target)
    cutoff = future[mature_count-1]
    proof = calendar(cal.start, cutoff)
    for row in db.scalars(select(Result)): db.delete(row)
    prepared = db.scalar(select(DailyRadarPreparedRun))
    prepared.run_date = cutoff
    prepared.step_statuses = {'validation-calendar': proof.payload()}
    db.commit()
    invoke(db, monkeypatch, tmp_path, proof, as_of_date=cutoff)
    db.expire_all()
    rows = list(db.scalars(select(Result)))
    assert {r.window_days for r in rows} == set(WINDOWS)
    for row in rows:
        if row.window_days <= mature_count:
            assert row.status == 'validated' and row.target_date == future[row.window_days-1]
        else:
            assert row.status == 'skipped' and row.skip_reason == 'window_not_mature'
            assert row.target_date is None and 'forward_return_pct' not in row.outcome


@pytest.mark.parametrize('fault', ['absent', 'stale', 'signal_uncovered', 'invalid', 'conflicting'])
def test_unavailable_cli_calendar_preserves_results_and_existing_report(db, monkeypatch, tmp_path, fault):
    cal = seed_basis(db, 'signal_close')
    prepared = db.scalar(select(DailyRadarPreparedRun))
    if fault == 'absent': prepared.step_statuses = {}
    elif fault == 'stale': prepared.step_statuses = {'validation-calendar': calendar(cal.start, DAY-timedelta(days=1)).payload()}
    elif fault == 'signal_uncovered': prepared.step_statuses = {'validation-calendar': calendar(SIGNAL+timedelta(days=1), DAY).payload()}
    elif fault == 'invalid': prepared.step_statuses = {'validation-calendar': cal.payload() | {'sessions': []}}
    else:
        db.add(DailyRadarPreparedRun(run_date=DAY-timedelta(days=1), market='TW', symbol_count=0,
            selected_symbols=[], universe=[], step_statuses={'validation-calendar':
                calendar(cal.start, DAY-timedelta(days=1), {SIGNAL+timedelta(days=1)}).payload()}))
    db.commit()
    before = saved(db)
    db.rollback()
    output = tmp_path/'report.json'
    output.write_text('previous report')
    with pytest.raises(ValueError, match='official_trading_calendar_unavailable'):
        invoke(db, monkeypatch, tmp_path, cal)
    assert output.read_text() == 'previous report'
    assert saved(db) == before


@pytest.mark.parametrize('write', [False, True])
def test_closed_signal_cannot_be_validated_by_cli_write(db, monkeypatch, tmp_path, write):
    cal = seed_basis(db, 'signal_close')
    closed = calendar(cal.start, DAY, {SIGNAL})
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {'validation-calendar': closed.payload()}
    db.commit()
    before = saved(db)
    db.rollback()
    invoke(db, monkeypatch, tmp_path, cal, write=write)
    db.expire_all()
    if write:
        rows = list(db.scalars(select(Result).where(Result.validation_version == FORWARD_VALIDATION_VERSION)))
        assert len(rows) == len(WINDOWS) and all(r.skip_reason == 'non_trading_signal_date' for r in rows)
        assert all(r.status == 'skipped' and r.target_date is None for r in rows)
    else:
        assert saved(db) == before


def test_read_only_cli_retains_preview_without_calendar_or_writes(db, monkeypatch, tmp_path):
    cal = seed_basis(db, 'signal_close')
    db.scalar(select(DailyRadarPreparedRun)).step_statuses = {}
    db.commit()
    before = saved(db)
    db.rollback()
    output = invoke(db, monkeypatch, tmp_path, cal, write=False, missing_symbol='TAIEX')
    assert output.is_file() and saved(db) == before


@pytest.mark.parametrize('write', [False, True])
def test_mixed_past_and_future_candidates_require_calendar_for_every_write(db, monkeypatch, tmp_path, write):
    cal = seed_basis(db, 'signal_close')
    future = DAY+timedelta(days=1)
    candidate = add_candidate(db, add_run(db, future), symbol='2317.TW')
    candidate.data_dates = {'ohlcv': str(future)}
    candidate.input_snapshot = candidate.input_snapshot | {'ohlcv': {'close': 100}}
    db.commit()
    before = saved(db)
    db.rollback()
    if write:
        with pytest.raises(ValueError, match='official_trading_calendar_unavailable'):
            invoke(db, monkeypatch, tmp_path, cal, end_date=future)
        assert not (tmp_path/'report.json').exists()
    else:
        assert invoke(db, monkeypatch, tmp_path, cal, write=False, end_date=future).is_file()
    assert saved(db) == before


@pytest.mark.parametrize('change', ['conflict', 'remove_checkpoint'])
def test_calendar_changed_during_cli_computation_rolls_back_whole_batch(db, monkeypatch, tmp_path, change):
    cal = seed_basis(db, 'signal_close')
    before = saved(db)
    db.rollback()
    evaluate = cli.build_forward_validation_report

    def changed(*args, **kwargs):
        result = evaluate(*args, **kwargs)
        with Session(db.get_bind()) as other:
            prepared = other.scalar(select(DailyRadarPreparedRun))
            if change == 'remove_checkpoint': other.delete(prepared)
            else:
                prepared.step_statuses = {'validation-calendar':
                    calendar(cal.start, DAY, {SIGNAL+timedelta(days=1)}).payload()}
            other.commit()
        return result

    monkeypatch.setattr(cli, 'build_forward_validation_report', changed)
    reason = 'calendar_checkpoint_unavailable' if change == 'remove_checkpoint' else 'calendar_evidence_conflict'
    with pytest.raises(ValueError, match=reason):
        invoke(db, monkeypatch, tmp_path, cal)
    assert not (tmp_path/'report.json').exists()
    assert saved(db) == before


@pytest.mark.parametrize('market,benchmark', [('US', 'SPX'), ('TW', 'SPX')])
def test_cli_write_rejects_unsupported_official_calendar_scope(db, monkeypatch, tmp_path, market, benchmark):
    cal = seed_basis(db, 'signal_close')
    before = saved(db)
    db.rollback()
    with pytest.raises(ValueError, match='official_trading_calendar_scope_unsupported'):
        invoke(db, monkeypatch, tmp_path, cal, market=market, benchmark=benchmark)
    assert not (tmp_path/'report.json').exists() and saved(db) == before
