"""TWSE roster fallback through the same provider used by durable and legacy Radar."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from ai_stock_sentinel.daily_radar.background_context import build_background_context_labels, same_day_background_context_is_reusable
from ai_stock_sentinel.daily_radar.explanations import _input_evidence
from ai_stock_sentinel.daily_radar.data_quality import margin_evidence_is_complete
from ai_stock_sentinel.daily_radar.default_background_context import DefaultBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.finmind_background_context import FinMindBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.official_background_context import OfficialBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.raw_data import _project_margin_context
from tests.test_official_background_context import _FakeResponse, _complete_twse_margin_payload

DAY = date(2026, 11, 17)
ROSTER = 'https://www.twse.com.tw/exchangeReport/TWT92U'
CREDIT = 'https://www.twse.com.tw/exchangeReport/TWT93U'
BALANCES = 'https://www.twse.com.tw/exchangeReport/MI_MARGN'
LISTED = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'
ROSTER_NOTE = ('本表所列有價證券為所有得為融資融券交易之上市有價證券，不含未取得融資融券資格者，'
               '或有「有價證券得為融資融券標準」第四條及第五條規定情事而暫停融資融券交易者。')


@pytest.fixture(autouse=True)
def after_report_day(monkeypatch):
    from ai_stock_sentinel.daily_radar import twse_margin_roster
    monkeypatch.setattr(twse_margin_roster, 'current_time', lambda: datetime(2026, 11, 19, tzinfo=timezone.utc))


def reports(day=DAY):
    compact = day.strftime('%Y%m%d')
    title_date = f'{day.year - 1911}年{day.month:02}月{day.day:02}日'
    balances = _complete_twse_margin_payload(compact, [['2254', '巨鎧精密-創', *['0'] * 13, '']])
    return {
        ROSTER: {'stat': 'OK', 'date': compact, 'title': f'{title_date} 平盤下得融(借)券賣出之證券名單',
                 'fields': ['證券代號', '證券名稱', '暫停融券賣出', '暫停借券賣出',
                            '前一交易日收盤價跌停本日禁止平盤下融券、借券賣出'],
                 'notes': [ROSTER_NOTE], 'total': 1, 'data': [['2254', '巨鎧精密-創', '*', '', '']]},
        CREDIT: {'stat': 'OK', 'date': compact, 'fields': ['代號', '名稱', '備註'], 'total': 1,
                 'notes': ['Y-未取得信用交易資格'], 'data': [['2330', '台積電', '']]},
        LISTED: {'stat': 'OK', 'date': compact, 'tables': [{
            'title': f'{title_date} 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))',
            'fields': ['證券代號', '證券名稱', '成交股數', '開盤價', '最高價', '最低價', '收盤價'],
            'data': [['2258', '鴻華先進-創', '824,782', '28.05', '29.65', '27.8', '27.95'],
                     ['2254', '巨鎧精密-創', '500', '10', '10', '10', '10']]}]},
        BALANCES: balances,
    }


def fetch(data, *, symbols=('2258.TW',), day=DAY, mode='finmind_only', calls=None):
    def get(url, *, params, **kwargs):
        if calls is not None:
            calls[url] += 1
        value = data.get(url, {'stat': '查無資料'})
        if isinstance(value, Exception):
            raise value
        return _FakeResponse(value)

    official = OfficialBackgroundChipContextProvider(request_get=get, lookback_trading_days=1,
                                                     max_lookback_calendar_days=1)
    finmind = FinMindBackgroundChipContextProvider(api_token='fixture', request_get=lambda *a, **kw: _FakeResponse(
        {'data': [], 'status': 200, 'msg': 'success'}))
    provider = DefaultBackgroundChipContextProvider(official_provider=official, finmind_provider=finmind,
                                                   provider_mode=mode)
    return list(provider.fetch(symbols=list(symbols), context_types=['full_margin'], run_date=day, market='TW'))


@pytest.mark.parametrize('mode', ['finmind_only', 'official_only'])
def test_absent_listed_stock_is_exempt_only_for_the_report_day(mode):
    calls = Counter()
    absent, eligible = fetch(reports(), symbols=('2258.TW', '2254.TW'), mode=mode, calls=calls)
    assert absent.missing_reason is None
    assert absent.source['dataset'] == 'TWSE_TWT92U'
    proof = absent.payload['eligibility']
    assert (proof['symbol'], proof['report_date'], proof['source_url']) == ('2258.TW', DAY.isoformat(), ROSTER)
    projected = _project_margin_context(vars(absent), technical={})
    assert margin_evidence_is_complete(projected, record_date=DAY, symbol='2258.TW')
    assert not margin_evidence_is_complete(projected, record_date=DAY, symbol='2254.TW')
    assert not same_day_background_context_is_reusable(absent, run_date=DAY + timedelta(days=1))
    label = build_background_context_labels([vars(absent)])[0]['label']
    assert '當日可融資融券名單' in label
    assert any('當日可融資融券名單' in line for line in _input_evidence({'margin': projected}))
    assert 'latest_margin_balance' not in absent.payload
    assert 'applicability' not in eligible.payload
    if mode == 'official_only':
        assert eligible.payload['latest_margin_balance'] == 0
    else:
        assert eligible.missing_reason == 'finmind_no_data'
    assert all(calls[url] == 1 for url in (ROSTER, LISTED, BALANCES))


@pytest.mark.parametrize('source', [ROSTER, LISTED, BALANCES, CREDIT])
@pytest.mark.parametrize('defect', ['unavailable', 'stale', 'duplicate', 'malformed_row'])
def test_unusable_reports_keep_provider_gap(source, defect):
    data = reports()
    table = data[source].get('tables', [data[source]])[-1]
    if defect == 'unavailable':
        data[source] = TimeoutError()
    elif defect == 'stale':
        data[source]['date'] = '20261116'
    elif defect == 'duplicate':
        table['data'].append(deepcopy(table['data'][0]))
        if 'total' in data[source]:
            data[source]['total'] += 1
    else:
        table['data'][0] = ['']
    assert fetch(data)[0].missing_reason == 'finmind_no_data'


@pytest.mark.parametrize('case', ['wrong_count', 'empty_roster', 'unknown_scope', 'filtered_balances',
                                  'balance_row', 'missing_outstanding_balance', 'not_listed', 'credit_row', 'wrong_market'])
def test_ambiguous_roster_evidence_keeps_source_gap(case):
    data = reports()
    symbol = '2258.TW'
    if case == 'wrong_count':
        data[ROSTER]['total'] = 2
    elif case == 'empty_roster':
        data[ROSTER].update(data=[], total=0)
    elif case == 'unknown_scope':
        data[ROSTER]['notes'] = ['本表為部分標的']
    elif case == 'filtered_balances':
        data[BALANCES]['tables'][1]['title'] = '融資融券彙總 (汽車工業)'
    elif case == 'missing_outstanding_balance':
        data[BALANCES]['tables'][0]['data'][0][-1] = '12'
    elif case == 'balance_row':
        data[BALANCES]['tables'][1]['data'].append(['2258', '鴻華先進-創', *['0'] * 13, ''])
    elif case == 'not_listed':
        data[LISTED]['tables'][0]['data'].pop(0)
    elif case == 'credit_row':
        data[CREDIT]['data'].append(['2258', '鴻華先進-創', 'X'])
        data[CREDIT]['total'] += 1
    else:
        symbol = '2258.TWO'
    assert fetch(data, symbols=(symbol,))[0].missing_reason == 'finmind_no_data'


def test_next_day_roster_membership_requires_balances_again():
    tomorrow = DAY + timedelta(days=1)
    data = reports(tomorrow)
    data[ROSTER]['data'].append(['2258', '鴻華先進-創', '', '', ''])
    data[ROSTER]['total'] += 1
    assert fetch(data, day=tomorrow)[0].missing_reason == 'finmind_no_data'


def test_roster_evidence_survives_scoring_and_never_triggers_margin_rewards():
    from ai_stock_sentinel.daily_radar.prefilter import prefilter_record
    from ai_stock_sentinel.daily_radar.scoring import score_daily_radar_record
    from tests.test_daily_radar_scoring import _joined_records_by_symbol, _market_context

    record = deepcopy(_joined_records_by_symbol()['2330.TW'])
    day = date.fromisoformat(record['record_date'])
    item = fetch(reports(day), day=day)[0]
    record.update(symbol=item.symbol, margin=_project_margin_context(vars(item), technical={}))
    filtered = prefilter_record(record)
    assert 'data_gap' not in {r['code'] for r in filtered['prefilter_reasons']}
    result = score_daily_radar_record(record, market_context=_market_context(), prefilter_result=filtered)
    assert not {'institutional_margin_contained', 'bottoming_margin_easing',
                'support_retest_margin_not_expanding'} & {r['rule_id'] for r in result['matched_rules']}
    assert result['input_snapshot']['replay_input']['record']['margin'] == record['margin']


@pytest.mark.parametrize(('field', 'value'), [('checks', {}), ('report_date', '2026-11-16'),
    ('evaluated_for', '2026-11-16'), ('symbol', '2258.TWO'), ('reason', 'finmind_no_data'),
    ('roster_count', True), ('roster_count', 0)])
def test_incomplete_persisted_roster_proof_cannot_pass_scoring(field, value):
    item = fetch(reports())[0]
    projected = _project_margin_context(vars(item), technical={})
    projected['eligibility'][field] = value
    assert not margin_evidence_is_complete(projected, record_date=DAY, symbol='2258.TW')


def test_current_roster_can_explain_disappearance_after_a_day_with_balances():
    data = reports()
    yesterday = DAY - timedelta(days=1)
    def get(url, *, params, **kwargs):
        if url == BALANCES and params['date'] == yesterday.strftime('%Y%m%d'):
            previous = reports(yesterday)[BALANCES]
            previous['tables'][1]['data'][0][0] = '2258'
            return _FakeResponse(previous)
        return _FakeResponse(data.get(url, {'stat': '查無資料'}))
    provider = OfficialBackgroundChipContextProvider(request_get=get, lookback_trading_days=2,
                                                     max_lookback_calendar_days=2)
    [result] = provider.fetch(symbols=['2258.TW'], context_types=['full_margin'], run_date=DAY, market='TW')
    assert result.as_of_date == DAY and result.source['dataset'] == 'TWSE_TWT92U'
    assert 'latest_margin_balance' not in result.payload


@pytest.mark.parametrize('evidence', ['credit', 'listing'])
@pytest.mark.parametrize('conflict', ['zero_balance_row', 'unavailable', 'valid_absence'])
def test_finmind_empty_checks_actual_official_balances_before_explicit_exemption(evidence, conflict):
    data = reports()
    if evidence == 'credit':
        data[CREDIT]['data'][0] = ['2258', '測試', 'Y']
    else:
        data['https://www.twse.com.tw/rwd/zh/company/newlisting'] = {
            'stat': 'OK', 'fields': ['公司代號', '股票上市買賣日期', '備註'],
            'data': [['2258', '115.10.01', '第一上市']]}
    if conflict == 'zero_balance_row':
        data[BALANCES]['tables'][1]['data'].append(['2258', '測試', *['0'] * 13, ''])
    elif conflict == 'unavailable':
        data[BALANCES] = TimeoutError()
    result = fetch(data)[0]
    if conflict == 'valid_absence':
        assert result.payload['applicability'] == 'not_applicable'
    else:
        assert result.missing_reason == 'finmind_no_data' and not result.payload


def test_intraday_roster_absence_must_be_rechecked_after_official_reports_update(monkeypatch):
    from ai_stock_sentinel.daily_radar import twse_margin_roster
    from zoneinfo import ZoneInfo

    def at(value):
        return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Taipei'))
    clock = at('2026-11-17T21:30')
    monkeypatch.setattr(twse_margin_roster, 'current_time', lambda: clock)
    data = reports()
    early = fetch(data)[0]
    assert same_day_background_context_is_reusable(early, run_date=DAY)
    clock = at('2026-11-17T22:35')
    assert not same_day_background_context_is_reusable(early, run_date=DAY)
    data[ROSTER]['data'].append(['2258', '鴻華先進-創', '', '', ''])
    data[ROSTER]['total'] += 1
    assert fetch(data)[0].missing_reason == 'finmind_no_data'
    clock = at('2026-11-18T00:30')
    settled = fetch(reports())[0]
    clock = at('2026-12-01T10:00')
    assert same_day_background_context_is_reusable(settled, run_date=DAY)


def explicit_reports(evidence):
    data = reports()
    if evidence == 'credit':
        data[CREDIT]['data'][0] = ['2258', '測試', 'Y']
    else:
        data['https://www.twse.com.tw/rwd/zh/company/newlisting'] = {
            'stat': 'OK', 'fields': ['公司代號', '股票上市買賣日期', '備註'],
            'data': [['2258', '115.10.01', '第一上市']]}
    return data


@pytest.mark.parametrize('mode', ['official_only', 'finmind_only'])
@pytest.mark.parametrize('evidence', ['credit', 'listing'])
def test_explicit_exemption_needs_reconciled_balance_totals(evidence, mode):
    data = explicit_reports(evidence)
    data[BALANCES]['tables'][0]['data'][0][-1] = '1'
    item = fetch(data, mode=mode)[0]
    assert item.missing_reason and 'applicability' not in item.payload


@pytest.mark.parametrize('evidence', ['credit', 'listing'])
def test_all_tw_proofs_expire_and_old_snapshots_stay_readable(evidence, monkeypatch):
    from ai_stock_sentinel.daily_radar import twse_margin_roster
    from ai_stock_sentinel.daily_radar.margin_applicability import margin_is_not_applicable
    clock = datetime.fromisoformat('2026-11-18T00:07:00+08:00')
    monkeypatch.setattr(twse_margin_roster, 'current_time', lambda: clock)
    data = explicit_reports(evidence)
    early = fetch(data)[0]
    assert same_day_background_context_is_reusable(early, run_date=DAY)
    clock += timedelta(minutes=3)
    assert not same_day_background_context_is_reusable(early, run_date=DAY)
    settled = fetch(data)[0]
    assert same_day_background_context_is_reusable(settled, run_date=DAY)
    for invalid in (None, 'bad', '2026-11-18T00:10', '2026-11-18T01:00:00+08:00'):
        settled.payload['eligibility']['checked_at'] = invalid
        assert not same_day_background_context_is_reusable(settled, run_date=DAY)
    del settled.payload['eligibility']['checked_at']
    assert margin_is_not_applicable(settled.payload, run_date=DAY, symbol='2258.TW')
    assert not same_day_background_context_is_reusable(settled, run_date=DAY)
    data[BALANCES]['tables'][1]['data'].append(['2258', '測試', *['0'] * 13, ''])
    assert fetch(data)[0].missing_reason == 'finmind_no_data'
