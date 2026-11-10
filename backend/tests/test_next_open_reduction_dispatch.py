from datetime import timedelta

import pytest

from ai_stock_sentinel.calibration import official_corporate_actions as actions
from ai_stock_sentinel.daily_radar import next_open_history_repair as tool
from test_next_open_action_scope import synthetic_case, SIGNAL, DAY
from test_next_open_history_repair import db
from test_radar_job_postgres import pg_engine


def reduction_case(db, window=20, listed=True, marker=True):
    cal, provider, row, before, request = synthetic_case(db, window=window,
        kind='rights' if listed else 'distribution', current='reduction', listed=listed)
    provider.stop = provider.future[window-3]
    provider.missing = {d for d in cal.sessions if provider.stop <= d < provider.target}
    original = provider.fetch
    symbol = '2330.TW' if listed else '2330.TWO'
    def fetch(*args, **kwargs):
        if provider.official: return original(*args, **kwargs)
        missing = provider.missing; provider.missing = set()
        try: values = original(*args, **kwargs)
        finally: provider.missing = missing
        for bar in values.get(symbol, []):
            if bar['date'] in {str(d) for d in missing}: bar['volume'] = 1 if getattr(provider, 'bad_halt', False) else 0
            if marker and bar['date'] == str(provider.target): bar['stock_splits'] = 0.94
        return values
    provider.fetch = fetch
    if listed:
        def unused(*args, **kwargs): raise AssertionError('TW-only fixture must not query TPEX')
        provider.capture_tpex_report = unused  # Same capabilities as the real shared provider.
    return cal, provider, row, before, request


@pytest.mark.parametrize('window', [5, 10, 20, 40, 60])
@pytest.mark.parametrize('listed', [False, True])
@pytest.mark.parametrize('marker', [False, True])
def test_current_reduction_is_verified_before_requiring_halt_day_prices(db, window, listed, marker):
    cal, provider, row, before, request = reduction_case(db, window, listed, marker)
    symbol = '2330.TW' if listed else '2330.TWO'
    prices = provider.fetch([symbol, 'TAIEX'], start_date=SIGNAL, end_date=DAY)
    direct = actions.prove_window(symbol, SIGNAL, window, provider.target, yahoo_rows=prices[symbol],
        benchmark_rows=prices['TAIEX'], marked_rows=prices[symbol], provider=provider)
    assert direct is not None and direct[0] == actions.CASH_REDUCTION_HALT
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    calls = provider.report_calls
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    after = plan['results'][0]['after']
    assert after['status'] == 'skipped' and after['skip_reason'] == actions.CASH_REDUCTION_HALT
    expected = {str(SIGNAL), *(str(d) for d in provider.future[:window])}
    proof = after['outcome']['reviewed_official_window']
    assert len(proof['session_dates']) == window
    assert {r['date'] for r in proof['stock']} == expected - {str(d) for d in provider.missing}
    assert {r['date'] for r in proof['benchmark']} == expected
    assert tool.result_snapshot(row) == before
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit()
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit(); db.refresh(row)
    assert tool.result_snapshot(row) == before and provider.report_calls == calls


@pytest.mark.parametrize('listed', [False, True])
@pytest.mark.parametrize('fault', ['stock_gap', 'index_gap', 'halt_volume', 'halt_date', 'bad_hash', 'extra_current', 'benchmark_dividend'])
def test_reduction_dispatch_never_waives_unexplained_gaps_or_actions(db, listed, fault):
    cal, provider, row, before, request = reduction_case(db, listed=listed)
    if fault == 'halt_volume': provider.bad_halt = True
    elif fault == 'halt_date': provider.stop = SIGNAL+timedelta(days=5)
    elif fault == 'extra_current': provider.events[provider.future[0]] = 'dividend'
    else: provider.fault = fault
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before
