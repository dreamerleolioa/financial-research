from datetime import date
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from test_radar_jobs import db
from test_daily_radar_forward_validation import _add_candidate, _add_run
from test_radar_price_acceptance import CANDIDATE, DAYS, bar, official
from test_radar_target_suspension import evaluate, proof
from ai_stock_sentinel.daily_radar.forward_validation import (
    forward_validation_result_revision, upsert_forward_validation_results,
)
from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceCollector, OfficialPriceEvidence
from ai_stock_sentinel.daily_radar.research_validation import _evaluate_locked_next_open
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
from ai_stock_sentinel.daily_radar.trading_status import (
    CONFLICT, REASON, TPEX_HALT, TWSE_HALT, collect_target_suspension, matches,
)
from ai_stock_sentinel.daily_radar.validation_execution import _collect_locked_signal_prices
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result


def test_halted_window_survives_the_other_targets_price_timeout():
    later = date(2026, 6, 5)
    sessions = [*DAYS, later]
    def collect(symbol, start, end):
        stock = {str(d): bar(d, 100) for d in (DAYS[0], later)}
        benchmark = {str(d): bar(d, 10000) for d in sessions}
        return OfficialPriceEvidence(stock, benchmark, [], target_suspension=proof() if end == DAYS[-1] else None)
    def timeout(*args, **kwargs):
        raise TimeoutError()
    rows = _collect_locked_signal_prices([CANDIDATE], None, calendar=SimpleNamespace(sessions=sessions, through=later),
        request=DailyRadarForwardValidationRunRequest(windows=[1, 2]), collect_official=collect, fetch_prices=timeout)
    assert {r['window_days']: r['skip_reason'] for r in rows} == {
        1: REASON, 2: 'validation_price_source_unavailable'}


@pytest.mark.parametrize('suffix,market_path,times,expected', [
    ('TW', TWSE_HALT, ('8:00', '8:00'), True),
    ('TW', TWSE_HALT, ('10:15', '8:00'), False),
    ('TWO', TPEX_HALT, ('8:00', '8:00'), True),
    ('TWO', TPEX_HALT, ('11:00', '8:00'), False),
])
def test_general_halt_receipts_require_full_target_session(suffix, market_path, times, expected):
    target = date(2026, 6, 4)
    symbol = '5880.' + suffix
    def capture(path, params):
        assert path == market_path
        date_fields = ['暫停交易日期', '暫停交易時間', '恢復交易日期', '恢復交易時間']
        if suffix == 'TW':
            body = {'stat': 'OK', 'params': params, 'fields': ['證券代號', *date_fields],
                    'data': [['5880', '115/06/04', times[0], '115/06/05', times[1]]]}
            host = 'www.twse.com.tw'
        else:
            body = {'stat': 'ok', 'date': '2026', 'tables': [{'date': '2026', 'totalCount': 2,
                    'fields': ['有價證券代號', '有價證券類別', *date_fields],
                    'data': [['5880', '上櫃股票', '115/06/04', times[0], '-', '-'],
                             ['5880', '上櫃股票', '-', '-', '115/06/05', times[1]]]}]}
            host = 'www.tpex.org.tw'
        return body, {'source_url': 'https://' + host + path + '?' + urlencode(params),
                      'captured_at': '2026-06-05T00:00:00+00:00', 'snapshot_sha256': fingerprint(body)}
    from ai_stock_sentinel.daily_radar import trading_status
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trading_status, '_parse_suspension', lambda *args: None)
        data = collect_target_suspension(SimpleNamespace(capture_twse_report=capture, capture_tpex_report=capture),
                                         symbol, target)
    assert (data is not None) is expected
    if expected:
        assert matches(data, symbol=symbol, target=target)
        assert not matches(data, symbol='1234.' + suffix, target=target)


def test_next_open_event_uses_required_quote_and_its_own_diagnostics():
    day = str(DAYS[-1])
    events = [{'family': 'rights', 'effective_date': day}]
    evidence = official([bar(DAYS[0], 100), bar(DAYS[-1], 90)], events)
    provider = SimpleNamespace(diagnostics=[{'symbol': CANDIDATE['symbol'], 'stage': 'twse_rights_recovery',
                                            'reason': 'earlier_failure', 'target_date': str(DAYS[0])}])
    args = dict(yahoo=[], benchmark=[], official=evidence, provider=provider, as_of_date=DAYS[-1],
                benchmark_symbol='TAIEX', calendar=SimpleNamespace(sessions=DAYS))
    timeout = _evaluate_locked_next_open(CANDIDATE, DAYS, **args, price_source_unavailable=True)
    assert timeout['skip_reason'] == 'validation_price_source_unavailable'
    gap = _evaluate_locked_next_open(CANDIDATE, DAYS, **args)
    assert gap['skip_reason'] == 'official_corporate_action_in_window'
    assert all(row['target_date'] == day for row in gap['outcome']['event_validation_diagnostics'])


@pytest.mark.parametrize('first_trade', [False, True])
def test_late_opposing_evidence_is_reported_on_revision_mismatch(db, first_trade):
    candidate = _add_candidate(db, _add_run(db, run_date=DAYS[0]), symbol=CANDIDATE['symbol'])
    model = CANDIDATE | {'candidate_id': candidate.id}
    calendar = SimpleNamespace(sessions=DAYS, through=DAYS[-1])
    halt = evaluate(proof(), []) | {'candidate_id': candidate.id}
    rows = [bar(DAYS[0], 100), bar(DAYS[-1], 110)]
    trade = _collect_locked_signal_prices([model], None, calendar=calendar,
        request=DailyRadarForwardValidationRunRequest(windows=[1]), collect_official=lambda *args: official(rows),
        fetch_prices=lambda *args, **kwargs: {model['symbol']: rows})[0]
    assert trade['status'] == 'validated'
    winner, loser = (trade, halt) if first_trade else (halt, trade)
    assert upsert_forward_validation_results(db, [winner], calendar=calendar)['records_written'] == 1
    db.commit()
    revision = forward_validation_result_revision(db.scalar(select(Result)))
    result = upsert_forward_validation_results(db, [loser], calendar=calendar,
        expected_revisions={(candidate.id, 1, winner['validation_version']): None})
    db.commit()
    assert result['records_written'] == 0
    assert result['preserved_conflicts'][0]['reason'] == CONFLICT
    assert forward_validation_result_revision(db.scalar(select(Result))) == revision


def test_collector_reads_shared_range_once_and_checks_only_missing_target(monkeypatch):
    from ai_stock_sentinel.daily_radar import trading_status
    later = date(2026, 6, 5)
    collector = OfficialPriceCollector(SimpleNamespace())
    calls = []
    def collect_range(symbol, start, end, **kwargs):
        calls.append((start, end))
        return OfficialPriceEvidence({str(later): bar(later, 100)}, {}, [])
    def suspension(provider, symbol, day):
        calls.append(day)
        raise TimeoutError()
    monkeypatch.setattr(collector, '_collect_range', collect_range)
    monkeypatch.setattr(trading_status, 'collect_target_suspension', suspension)
    results = collector.collect_targets(CANDIDATE['symbol'], DAYS[0], [DAYS[-1], later])
    assert calls == [(DAYS[0], later), DAYS[-1]]
    assert results[DAYS[-1]].suspension_error == 'TimeoutError'
    assert results[later].suspension_error is None


def test_next_open_closes_a_proven_halt_without_fetching_vendor_history():
    from ai_stock_sentinel.daily_radar.research_validation import _collect_locked_research_prices, candidate_key

    evidence = OfficialPriceEvidence({}, {}, [], target_suspension=proof())
    def optional_quote(*args, **kwargs):
        pytest.fail('vendor history cannot affect a proven suspension')
    rows = _collect_locked_research_prices([CANDIDATE], {candidate_key(CANDIDATE): [1]},
        calendar=SimpleNamespace(sessions=DAYS), request=SimpleNamespace(benchmark_symbol='TAIEX'),
        as_of_date=DAYS[-1], collector=SimpleNamespace(collect_targets=lambda *args, **kwargs: {DAYS[-1]: evidence}),
        provider=SimpleNamespace(fetch=optional_quote), durable_window=True)
    assert [row['skip_reason'] for row in rows] == [REASON]


def test_shorter_snapshot_is_still_usable_after_the_wide_request_is_limited():
    from yfinance.exceptions import YFRateLimitError
    later = date(2026, 6, 5)
    sessions = [*DAYS, later]
    bars = [bar(d, 100) for d in sessions]
    supplied = {r['date']: r for r in bars}
    evidence = OfficialPriceEvidence(supplied, supplied, [])
    def rate_limit(*args, **kwargs):
        raise YFRateLimitError()
    def warm(symbols, *, start_date, end_date):
        assert end_date == DAYS[-1]
        return {CANDIDATE['symbol']: bars[:2]}
    results = _collect_locked_signal_prices([CANDIDATE], None, calendar=SimpleNamespace(sessions=sessions, through=later),
        request=DailyRadarForwardValidationRunRequest(windows=[1, 2]), collect_official=lambda *args: evidence,
        fetch_prices=rate_limit, fetch_cached=warm)
    assert [(r['window_days'], r['status'], r['skip_reason']) for r in results] == [
        (1, 'validated', None), (2, 'skipped', 'validation_price_source_unavailable')]


def test_confirmed_halt_can_use_independent_report_when_quote_is_cooling(monkeypatch):
    from datetime import datetime, timezone
    from ai_stock_sentinel.daily_radar import trading_status, next_open_action_scope
    from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy

    def history(symbol, **kwargs):
        if symbol == CANDIDATE['symbol']:
            raise SourceDependencyBusy(['request'], datetime.now(timezone.utc))
        return [bar(day, 10000) for day in DAYS]
    monkeypatch.setattr(next_open_action_scope, '_events', lambda *args, **kwargs: [])
    receipt = proof()
    monkeypatch.setattr(trading_status, 'collect_target_suspension', lambda *args: receipt)
    collector = OfficialPriceCollector(SimpleNamespace(official_research_history=history))
    fetched = []
    rows = _collect_locked_signal_prices([CANDIDATE], None,
        calendar=SimpleNamespace(sessions=DAYS, through=DAYS[-1]),
        request=DailyRadarForwardValidationRunRequest(windows=[1]), collect_official=collector.collect,
        collect_targets=collector.collect_targets, fetch_prices=lambda *a, **k: fetched.append(1))
    assert rows[0]['skip_reason'] == REASON
    assert not fetched


def test_partial_official_price_prevents_a_terminal_halt():
    from ai_stock_sentinel.daily_radar.trading_status import proven_suspension

    day = str(DAYS[-1])
    partial = OfficialPriceEvidence({day: {'date':day, 'close':100}}, {}, [], target_suspension=proof())
    placeholder = OfficialPriceEvidence({day: {'date':day, 'close':None}}, {}, [], target_suspension=proof())
    assert not proven_suspension(partial, CANDIDATE['symbol'], DAYS[-1])
    assert proven_suspension(placeholder, CANDIDATE['symbol'], DAYS[-1])


def test_independent_event_outage_does_not_replace_a_demonstrated_mismatch():
    from ai_stock_sentinel.daily_radar.price_acceptance import refused_prices

    target = str(DAYS[-1])
    unavailable = OfficialPriceEvidence({}, {}, [], unavailable_dates=frozenset({target}))
    row = refused_prices({'target_date': target}, 'signal_price_anchor_mismatch', unavailable, sessions=DAYS)
    assert row['skip_reason'] == 'signal_price_anchor_mismatch'


def test_vendor_budget_charges_each_uncached_ticker(monkeypatch):
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider

    requests = []
    def fetch(self, symbols, *, start_date, end_date):
        requests.extend(symbols)
        return {symbol: [bar(DAYS[-1], 100)] for symbol in symbols}
    monkeypatch.setattr(YFinanceForwardPriceProvider, 'fetch', fetch)
    provider = RecoveringResearchPriceProvider(max_requests=1)
    results = provider.fetch(['2330.TW', '2317.TW'], start_date=DAYS[0], end_date=DAYS[-1])
    assert requests == list(results) == ['2330.TW']
    assert provider.budget_limit == 'request_limit'


def test_vendor_rows_after_collection_deadline_are_discarded(monkeypatch):
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider

    now = [1.0]
    def fetch(self, symbols, *, start_date, end_date):
        assert self.history_timeout() == 1.0
        now[0] += 2
        self.history_timeout()
        return {symbols[0]: [bar(DAYS[-1], 100)]}
    monkeypatch.setattr(YFinanceForwardPriceProvider, 'fetch', fetch)
    provider = RecoveringResearchPriceProvider(clock=lambda: now[0], total_timeout=1)
    assert provider.fetch(['2330.TW'], start_date=DAYS[0], end_date=DAYS[-1]) == {}
    assert provider.budget_limit == 'deadline'


@pytest.mark.parametrize("report_date", ["115/06/04", "1150604"])
def test_partial_twse_quote_reaches_halt_check_without_becoming_a_price_bar(monkeypatch, report_date):
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    from ai_stock_sentinel.daily_radar import next_open_action_scope, trading_status

    def capture(path, params):
        if 'STOCK_DAY' in path:
            return {'stat': 'OK', 'date': params['date'], 'title': '115年06月 5880 股票',
                    'fields': ['日期','開盤價','最高價','最低價','收盤價','成交股數','成交金額'],
                    'data': [[report_date, '--', '--', '--', '100', '0', '0']]}, {}
        raise TimeoutError()
    provider = RecoveringResearchPriceProvider()
    monkeypatch.setattr(provider, 'capture_twse_report', capture)
    monkeypatch.setattr(next_open_action_scope, '_events', lambda *args, **kwargs: [])
    def lookup(*args):
        pytest.fail('a partial priced row must be checked before a suspension lookup')
    monkeypatch.setattr(trading_status, 'collect_target_suspension', lookup)
    evidence = OfficialPriceCollector(provider).collect(CANDIDATE['symbol'], *DAYS)
    assert not evidence.stock
    assert evidence.target_suspension is None
    assert provider.has_partial_official_price(CANDIDATE['symbol'], DAYS[-1])


def test_narrow_cache_fills_missing_dates_without_overwriting_current_vendor_quotation():
    from ai_stock_sentinel.daily_radar.research_validation import _fill_missing_vendor_prices

    disagreement = bar(DAYS[-1], 107)
    result = _fill_missing_vendor_prices([disagreement], [bar(DAYS[0], 100), bar(DAYS[-1], 100)])
    assert result == [disagreement, bar(DAYS[0], 100)]


def test_confirmed_capital_reduction_keeps_review_even_without_adjusted_prices():
    from ai_stock_sentinel.daily_radar.price_acceptance import accept_signal_close_prices

    evidence = official([bar(day, 100) for day in DAYS],
                        [{'family':'reduction', 'effective_date':str(DAYS[-1])}])
    assert accept_signal_close_prices(CANDIDATE, DAYS, [], evidence).reason == 'official_corporate_action_in_window'


def test_non_durable_batch_keeps_ticker_that_finished_before_peers_deadline(monkeypatch):
    from threading import Event, local
    import pandas as pd
    from ai_stock_sentinel.calibration import price_provider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider

    timely = Event()
    worker = local()
    clock = [0.0]

    class Provider(RecoveringResearchPriceProvider):
        def history_timeout(self):
            remaining = super().history_timeout()
            if getattr(worker, 'finished_healthy', False):
                timely.set()
            return remaining

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol
            self._data = object()
            self._quote = SimpleNamespace(_data=self._data)
            self._get_ticker_tz = lambda *a: 'Asia/Taipei'

        def history(self, **kwargs):
            assert kwargs['timeout'] == 1
            if self.symbol == '2317.TW':
                assert timely.wait(2)
                clock[0] = 2.0
                raise TimeoutError()
            worker.finished_healthy = True
            return pd.DataFrame({"Open":[100],"High":[102],"Low":[99],"Close":[101],"Volume":[1000],
                "Dividends":[0],"Stock Splits":[0]}, index=pd.DatetimeIndex([DAYS[-1]]))

    monkeypatch.setattr(price_provider.yf, 'Ticker', Ticker)
    provider = Provider(clock=lambda: clock[0], total_timeout=1)
    outcome = provider.fetch(['2330.TW', '2317.TW'], start_date=DAYS[0], end_date=DAYS[-1])
    assert set(outcome) == {'2330.TW'}
    assert provider.diagnostics[-1]['symbols'] == ['2317.TW']
