from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest

from ai_stock_sentinel.calibration import official_corporate_actions as actions, twse_rights_recovery as rights
from ai_stock_sentinel.daily_radar import next_open_official_repair as repair
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

SYMBOL = '2890.TW'
SIGNAL, BEFORE, DIV, LATE, PRIOR, EX = [date.fromisoformat(d) for d in (
    '2026-07-21', '2026-07-22', '2026-07-23', '2026-10-02', '2026-10-06', '2026-10-07')]
DAYS = (SIGNAL, BEFORE, DIV, LATE, PRIOR, EX)
ZONE = ZoneInfo('Asia/Taipei')
FACTOR = (45.40 + 35.15 * .03461290969) / (1.03461290969 * 45.40)


def history(day, price, dataset, symbol=SYMBOL):
    index = dataset == 'twse_index_history'
    path = 'indicesReport/MI_5MINS_HIST' if index else 'exchangeReport/STOCK_DAY'
    params = {'date': day.strftime('%Y%m01')}
    if not index:
        params['stockNo'] = symbol.split('.')[0]
    return dict(date=str(day), open=price, low=price - .5, close=price, high=price + .5, volume=800,
                price_source=dataset, price_basis='unadjusted_price', corporate_actions_checked=True,
                dividends=0, stock_splits=0, official_price_report=dict(source_url=f'https://www.twse.com.tw/{path}?' +
                urlencode(params), snapshot_sha256='d' * 64, captured_at='2026-11-19T12:00:00+00:00'))


def chart(day, quote, cash=None):
    start = int(datetime.combine(day, time.min, ZONE).timestamp())
    stamp = start + 9 * 3600
    record = {'meta': {'symbol': SYMBOL, 'exchangeTimezoneName': ZONE.key}, 'timestamp': [stamp],
              'indicators': {'quote': [{k: [quote[k]] for k in (*rights.OHLC, 'volume')}]}}
    if cash is not None:
        record['events'] = {'splits': {str(stamp): dict(date=stamp, numerator=1020, denominator=1000,
                                                        splitRatio='1020:1000')},
                            'dividends': {str(stamp): dict(date=stamp, amount=cash)}}
    raw = {'chart': {'error': None, 'result': [record]}}
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/2890.TW?' + urlencode(
        dict(period1=start, period2=start + 86400, interval='1d', events='div,splits'))
    return raw, dict(source_url=url, captured_at='2026-11-19T12:00:00+00:00', snapshot_sha256=fingerprint(raw))


@pytest.fixture
def scenario(monkeypatch):
    stock = {day: history(day, 45.40, 'twse_stock_day') for day in DAYS}
    index = {day: history(day, 100.0, 'twse_index_history') for day in DAYS}
    source = stock[EX]['official_price_report']
    dist = actions._distribution(DIV, '20', '1.1', 45.4)
    monkeypatch.setattr(actions, '_twse_action', lambda *args: (dist, [source]))
    witness = dict(ex_date=str(EX), previous_close=45.4, subscription_price=35.15,
                   subscription_shares_per_thousand=34.61290969, **source)
    def later(symbol, sessions, capture):
        assert EX in sessions and DIV not in sessions
        return witness, [source]
    monkeypatch.setattr(rights, '_rights_issue', later)
    vendor = {}
    for d in DAYS:
        factor = 1 / 1.02 if d < DIV else FACTOR if d in (LATE, PRIOR) else 1
        vendor[d] = stock[d] | {k: stock[d][k] * factor for k in rights.OHLC} | dict(
            price_source='yfinance', stock_splits=1.02 if d == DIV else 0, dividends=1.091692 if d == DIV else 0)
    raw = {day: chart(day, vendor[day], 1.091692 if day == DIV else None) for day in (DIV, PRIOR, EX)}
    def load(symbol, *, start_date, end_date, max_rows=None):
        data = index if symbol == 'TAIEX' else stock
        return [v for d, v in data.items() if start_date <= d <= end_date][:max_rows]
    def capture(path, params):
        assert path == '/exchangeReport/STOCK_DAY'
        raw = {'stat': 'OK', 'date': params['date'], 'title': '2890 行情',
               'fields': ['日期', '開盤價', '最高價', '最低價', '收盤價', '成交股數', '成交金額'],
               'data': [[f'{d.year-1911}/{d:%m/%d}', *(r[k] for k in rights.OHLC), 800, 800 * r['close']]
                        for d, r in stock.items() if d.strftime('%Y%m01') == params['date']]}
        return raw, source | {'source_url': 'https://www.twse.com.tw' + path + '?' + urlencode(params),
                              'snapshot_sha256': fingerprint(raw)}

    provider = SimpleNamespace(capture_twse_report=capture, official_research_history=load,
                               capture_split_day=lambda symbol, day: raw[day])
    def inputs(target):
        dates = [d for d in DAYS if d <= target]
        bars = [deepcopy(vendor[d]) for d in dates]
        marked = [r | {'ohlc_conflict_fields': list(rights.OHLC)} if d < DIV or d == LATE else r
                  for r, d in zip(bars, dates)]
        row = dict(symbol=SYMBOL, window_days=len(dates)-1, status='skipped', signal_date=str(SIGNAL),
                   outcome={'expected_target_date': str(target)})
        return row, bars, marked, [index[d] for d in dates]
    return inputs, provider, raw, vendor, SimpleNamespace(sessions=DAYS, through=EX)


@pytest.mark.parametrize('target, adjusted', [(DIV, []), (LATE, [str(LATE)])])
def test_only_reviewed_history_can_explain_distribution(scenario, target, adjusted):
    inputs, provider, _, _, calendar = scenario
    args = inputs(target)
    original = deepcopy(args)
    assert repair._classify_corporate_action(*args, as_of_date=target, provider=provider) is None
    result = repair.reviewed_distribution_basis.classify(*args, calendar=calendar, provider=provider)
    assert result['skip_reason'] == actions.STOCK_DISTRIBUTION
    assert result['target_date'] == str(target)
    proof = result['outcome'][actions.PROVENANCE_KEY]['reviewed_distribution_basis']
    precision = result['outcome'][actions.PROVENANCE_KEY]['distribution_precision']
    assert precision['provider_cash_per_share'] == proof['provider_cash_per_share'] == 1.091692
    assert precision['cash_in_distribution_units'] == 1.1
    assert abs(Decimal(proof['factor']) - Decimal(str(FACTOR))) < Decimal('1e-15')
    assert proof['adjusted_session_dates'] == adjusted
    assert args == original
    assert 'forward_return_pct' not in result['outcome']


@pytest.mark.parametrize('fault', ['cash', 'prior_open', 'ex_close', 'bad_source', 'wrong_date', 'extra_event',
                                   'partial_tail', 'raw_volume', 'marked_disagrees', 'future_outside_cutoff',
                                   'invalid_archive_price', 'benchmark_action', 'old_event_receipt', 'split_ratio'])
def test_unexplained_evidence_preserves_gap(scenario, fault):
    inputs, provider, raw, vendor, calendar = scenario
    if fault == 'cash':
        vendor[DIV]['dividends'] = 1.091693
        raw[DIV] = chart(DIV, vendor[DIV], 1.091693)
    elif fault in ('prior_open', 'ex_close'):
        day, field = (PRIOR, 'open') if fault == 'prior_open' else (EX, 'close')
        raw[day] = chart(day, vendor[day] | {field: vendor[day][field] + .01})
    elif fault == 'bad_source':
        raw[DIV][1]['snapshot_sha256'] = '0' * 64
    elif fault in ('wrong_date', 'extra_event'):
        record = raw[DIV][0]['chart']['result'][0]
        if fault == 'wrong_date':
            record['timestamp'][0] += 86400
        else:
            record['events']['capitalGains'] = {'100': {}}
        raw[DIV][1]['snapshot_sha256'] = fingerprint(raw[DIV][0])
    elif fault == 'partial_tail':
        vendor[LATE]['close'] += .01
    elif fault == 'raw_volume':
        raw[PRIOR] = chart(PRIOR, vendor[PRIOR] | {'volume': 0})
    elif fault == 'invalid_archive_price':
        vendor[DIV]['open'] = None
    elif fault == 'old_event_receipt':
        raw[DIV][1]['captured_at'] = '2026-10-06T12:00:00+00:00'
    elif fault == 'split_ratio':
        rec = raw[DIV][0]['chart']['result'][0]
        next(iter(rec['events']['splits'].values()))['splitRatio'] = '1030:1000'
        raw[DIV][1]['snapshot_sha256'] = fingerprint(raw[DIV][0])
    elif fault == 'future_outside_cutoff':
        calendar = SimpleNamespace(through=LATE, sessions=DAYS)
    args = inputs(LATE)
    if fault == 'benchmark_action':
        args[3][-1]['stock_splits'] = 1.05
    if fault == 'marked_disagrees':
        args[2][-1]['high'] += 1
    with pytest.raises(ValueError):
        repair.reviewed_distribution_basis.classify(*args, calendar=calendar, provider=provider)


def test_offline_replay_binds_event_history_and_supports_older_captures(scenario):
    from ai_stock_sentinel.daily_radar.next_open_history_repair import _Tape, PREVIOUS_CAPTURE_VERSION

    inputs, provider, _, _, calendar = scenario
    legacy, bars, marked, index = inputs(LATE)
    candidate = dict(candidate_id=11, record_date=str(SIGNAL), symbol=SYMBOL, data_dates={'ohlcv': str(SIGNAL)})
    provider.fetch = lambda *args, **kwargs: {SYMBOL: bars, 'TAIEX': index}
    tape = _Tape(provider)
    tape.fetch([SYMBOL, 'TAIEX'])
    tape.repaired[SYMBOL] = marked
    before = legacy | {'skip_reason': 'stock_split_in_window', 'outcome': {actions.PROVENANCE_KEY: {}}}
    result = repair.collect_window(candidate, before, before, calendar=calendar, provider=tape)
    repair.verify_evidence(candidate, result, calendar=calendar, prior=before['outcome'])
    proof = result['outcome'][repair.KEY]['event_replay']
    assert proof['kind'] == 'corporate'
    assert proof['distribution_verification'] == repair.reviewed_distribution_basis.VERSION
    assert not _Tape(evidence={'version': PREVIOUS_CAPTURE_VERSION,
                               'capabilities': ['fetch'], 'calls': []}).review_distribution_basis

    changed = deepcopy(result)
    changed['outcome'][actions.PROVENANCE_KEY]['reviewed_distribution_basis']['factor'] = '1'
    with pytest.raises(ValueError, match='replay_mismatch'):
        repair.verify_evidence(candidate, changed, calendar=calendar, prior=before['outcome'])
    altered = deepcopy(result)
    repeats = [c for c in altered['outcome'][repair.KEY]['event_replay']['calls']
               if c['request']['method'] == 'official_research_history' and c['request']['args'] == [SYMBOL]]
    assert len(repeats) == 2
    next(r for r in repeats[0]['value'] if r['date'] == str(SIGNAL))['open'] -= .001
    with pytest.raises(ValueError, match='history_changed'):
        repair.verify_evidence(candidate, altered, calendar=calendar, prior=before['outcome'])
    capture = next(c for c in proof['calls'] if c['request']['method'] == 'capture_split_day')
    capture['value'][1]['snapshot_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='report_hash_invalid'):
        repair.verify_evidence(candidate, result, calendar=calendar, prior=before['outcome'])


def test_chart_capture_is_reused_but_every_replay_call_is_checked():
    from ai_stock_sentinel.daily_radar.next_open_history_repair import _Tape
    calls = []
    def capture(symbol, day):
        calls.append((symbol, day))
        return {'raw': str(day)}, {'capture': len(calls)}
    tape = _Tape(SimpleNamespace(fetch=lambda *a: None, capture_split_day=capture))
    first = tape.capture_split_day(SYMBOL, DIV)
    first[0]['raw'] = 'mutated'
    second = tape.capture_split_day(SYMBOL, DIV)
    tape.capture_split_day(SYMBOL, EX)
    assert calls == [(SYMBOL, DIV), (SYMBOL, EX)]
    assert second == ({'raw': str(DIV)}, {'capture': 1})
    replay = _Tape(evidence={'capabilities': tape.capabilities, 'calls': tape.calls})
    assert replay.capture_split_day(SYMBOL, DIV) == replay.capture_split_day(SYMBOL, DIV)
    with pytest.raises(ValueError, match='replay_mismatch'):
        replay.capture_split_day(SYMBOL, DIV)


def test_receipt_cannot_supply_economic_terms_or_an_out_of_range_date(scenario, monkeypatch):
    inputs, provider, _, _, calendar = scenario
    source = {'source_url': 'https://www.twse.com.tw/', 'ex_date': str(EX), 'subscription_price': 10}
    provider.capture_twse_report = lambda *args: ({}, source)
    def parse(symbol, sessions, capture):
        capture(rights.RIGHTS_DETAIL, {})
        pytest.fail('receipt metadata reached the event parser')
    monkeypatch.setattr(rights, '_rights_issue', parse)
    with pytest.raises(ValueError, match='receipt'):
        repair.reviewed_distribution_basis.classify(*inputs(LATE), calendar=calendar, provider=provider)
    monkeypatch.setattr(rights, '_rights_issue', lambda *args: ({'ex_date': str(DIV)}, []))
    with pytest.raises(ValueError, match='session'):
        repair.reviewed_distribution_basis.classify(*inputs(LATE), calendar=calendar, provider=provider)


def test_malformed_report_is_a_window_error(scenario, monkeypatch):
    inputs, provider, _, _, calendar = scenario
    def bad(*args):
        raise TypeError("unhashable type: 'list'")
    monkeypatch.setattr(actions, '_twse_action', bad)
    with pytest.raises(ValueError, match='reviewed_distribution_invalid_input'):
        repair.reviewed_distribution_basis.classify(*inputs(LATE), calendar=calendar, provider=provider)


def test_second_distribution_report_must_confirm_the_same_terms(scenario, monkeypatch):
    inputs, provider, _, _, calendar = scenario
    original = actions._twse_action
    first, sources = original(None, None, None)
    corrected = actions._distribution(DIV, '20', '1.100001', 45.4)
    reports = iter([(first, sources), (corrected, sources)])
    monkeypatch.setattr(actions, '_twse_action', lambda *args: next(reports))
    with pytest.raises(ValueError, match='terms_changed'):
        repair.reviewed_distribution_basis.classify(*inputs(LATE), calendar=calendar, provider=provider)
