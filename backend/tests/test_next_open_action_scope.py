"""Old actions outside a corrected calendar must not block a complete repair."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration.price_provider import get_research_price_provider, YFinanceResearchPriceProvider
from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.daily_radar import next_open_history_repair as tool
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, TaiwanDailyBar, DailyRadarForwardValidationResult as Result
from radar_calendar_support import calendar
import test_twse_rights_recovery as fixture_module
from test_next_open_history_repair import db, seed, Prices, DAY, SIGNAL
from test_radar_job_postgres import pg_engine
from ai_stock_sentinel.calibration import twse_rights_recovery as rights, official_corporate_actions as actions
from ai_stock_sentinel.daily_radar.validation_inputs import bind_validation_input
from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs


class EventPrices(Prices):
    """Small, real-parser exchange reports with independently generated prices."""
    official = False

    def __init__(self, cal, window, kind='rights', position='outside', current=None):
        super().__init__(cal)
        self.window, self.kind, self.fault, self.report_calls = window, kind, None, 0
        self.cash_terms, self.dividend_value = '1', 1
        self.future = [d for d in self.days if d > SIGNAL]
        self.target = self.future[window-1]
        self.old_day = {'outside': self.future[window+1], 'after_target': self.future[window], 'target': self.target}[position]
        self.stop = self.future[window] if position == 'outside' else self.future[window-2]
        self.events = {self.old_day: kind}
        if current:
            self.events[self.target] = current

    def fetch(self, *args, **kwargs):
        values = super().fetch(*args, **kwargs)
        if self.official:
            return values
        for symbol, rows in values.items():
            if symbol == 'TAIEX':
                for row in rows:
                    if row['date'] == str(self.target) and self.fault in {'benchmark_dividend', 'benchmark_split'}:
                        row['dividends' if self.fault == 'benchmark_dividend' else 'stock_splits'] = 2
                continue
            for row in rows:
                day = date.fromisoformat(row['date'])
                current = self.events.get(day)
                if current == 'split': row['stock_splits'] = 2
                if current == 'distribution': row['stock_splits'] = 1.1
                if current == 'dividend': row['dividends'] = self.dividend_value
                if self.fault == 'phantom_dividend' and day == self.target: row['dividends'] = 1
                if self.fault == 'unmatched_split' and day == self.target: row['stock_splits'] = 2
                if self.fault == 'extra_split' and day == self.target: row['stock_splits'] = 3
                if self.events.get(self.target) == 'distribution' and day < self.target and self.fault != 'unmatched_split':
                    row.update({f: row[f] / 1.1 for f in rights.OHLC})
        return values

    def official_research_history(self, symbol, **kwargs):
        self.official = True
        try:
            rows = super().official_research_history(symbol, **kwargs)
            if self.fault in {'stock_gap', 'index_gap'} and ((symbol == 'TAIEX') == (self.fault == 'index_gap')):
                rows = [r for r in rows if r['date'] != str(self.target)]
            return rows
        finally:
            self.official = False

    def previous(self, day):
        prior = max(d for d in self.days if d < day)
        return 100 + self.days.index(prior)

    def capture_twse_report(self, path, params):
        from urllib.parse import urlencode
        self.report_calls += 1
        if self.fault == 'reports': raise TimeoutError('fixture')
        code = '2330'
        if path == rights.RIGHTS_DETAIL:
            kind = self.events[date.fromisoformat(params['T1'][:4]+'-'+params['T1'][4:6]+'-'+params['T1'][6:])]
            fields = ['股票代號', '(每股配發現金股利)除息', 'A. 按普通股股東持股比例每千股無償配股',
                'B. 員工紅利轉增資', 'C. (有償) 現金增資', '每股認購金額', '按股東持股比例每千股認購']
            raw = {'stat': 'OK', 'fields': fields, 'data': [[code, f'{self.cash_terms if kind=="dividend" else 0} 元／股',
                f'{100 if kind=="distribution" else 0} 股', '0 股', f'{10000 if kind=="rights" else 0} 股',
                f'{(81 if self.fault=="wrong_terms" else 80) if kind=="rights" else 0} 元／股', f'{50 if kind=="rights" else 0} 股']]}
        elif path == actions.TWSE_REDUCTION_DETAIL:
            stop = f'{self.stop.year-1911}/{self.stop:%m/%d}'
            raw = {'stat': 'OK', 'fields': ['股票代號：', '停止買賣日期：', '每壹仟股換發新股票：', '每股退還股款：',
                '原股每股配發現金股利：', '減資並(有償)現金增資：', '每股認購金額：', '按股東持股比例每千股認購：'],
                'data': [[code, stop, '940 股', '0.6 元/股', '0 元/股', '0 股', '0 元/股', '0 股']]}
        else:
            start, end = params['startDate'], params['endDate']
            raw = {'stat': 'OK', 'strDate': start, 'endDate': end, 'data': []}
            if path == rights.EX_RIGHTS:
                raw['fields'] = ['資料日期', '股票代號', '除權息前收盤價', '權/息', '詳細資料']
            elif path == rights.REDUCTION:
                raw['fields'] = ['恢復買賣日期', '股票代號', '停止買賣前收盤價格', '減資原因', '詳細資料']
            else:
                assert path == rights.FACE_VALUE
                raw['fields'] = ['恢復買賣日期', '股票代號']
            for day, kind in sorted(self.events.items()):
                if not start <= day.strftime('%Y%m%d') <= end or self.fault == 'event_missing' and day == self.old_day:
                    continue
                if self.fault == 'wide_missing_old' and start != end and day == self.old_day: continue
                roc = f'{day.year-1911}年{day:%m月%d日}'
                if path == rights.EX_RIGHTS and kind in {'rights', 'distribution', 'dividend'}:
                    raw['data'].append([roc, code, str(self.previous(day)), '息' if kind=='dividend' else '權', f'{code},{day:%Y%m%d}'])
                if path == rights.REDUCTION and kind == 'reduction':
                    raw['data'].append([roc, code, str(self.previous(self.stop)), '退還股款', f'{code},20260601'])
                if path == rights.FACE_VALUE and kind == 'split':raw['data'].append([roc, code])
            if self.fault == 'wide_terms' and start != end and path in {rights.EX_RIGHTS, rights.REDUCTION}:
                for r in raw['data']:r[2] = str(float(r[2])+1)
            if self.fault == 'wrong_current_ref' and path == rights.EX_RIGHTS:
                for r in raw['data']:
                    if r[0] == f'{self.target.year-1911}年{self.target:%m月%d日}':r[2] = str(float(r[2])+1)
            if self.fault == 'wrong_range': raw['strDate'] = '20200101'
        return raw, {'source_url': rights.TWSE+path+'?'+urlencode(params),
            'snapshot_sha256': tool.fingerprint(raw) if self.fault != 'bad_hash' else '0'*64,
            'captured_at': '2026-11-10T00:00:00+00:00'}


class TpexEventPrices(EventPrices):
    def official_research_history(self, symbol, **kwargs):
        from urllib.parse import urlencode
        rows = super().official_research_history(symbol, **kwargs)
        if symbol.endswith('.TWO'):
            for row in rows:
                params = {'response': 'json', 'date': row['date'][:7].replace('-', '/')+'/01', 'code': '2330'}
                row.update(price_source='tpex_trading_stock', official_price_report={
                    'source_url': 'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?'+urlencode(params),
                    'snapshot_sha256': tool.fingerprint({'fixture': row['date'][:7]}),
                    'captured_at': '2026-11-10T00:00:00+00:00'})
        return rows

    def capture_tpex_report(self, path, params):
        from urllib.parse import urlencode
        self.report_calls += 1
        if self.fault == 'reports': raise TimeoutError('fixture')
        start, end = params['startDate'].replace('/', ''), params['endDate'].replace('/', '')
        if path == actions.TPEX_DISTRIBUTIONS:
            fields = ['除權息日期', '代號', '除權息前收盤價', '權/息', '現金股利', '每仟股無償配股',
                      '現金增資股數', '現金增資認購價', '按持股比例仟股認購']
        else:
            assert path == actions.TPEX_REDUCTIONS
            fields = ['恢復買賣日期', '股票代號', '最後交易日之收盤價格', '減資原因', '詳細資料']
        data = []
        for day, kind in sorted(self.events.items()):
            if not start <= day.strftime('%Y%m%d') <= end or self.fault == 'event_missing' and day == self.old_day:
                continue
            if self.fault == 'wide_missing_old' and start != end and day == self.old_day: continue
            roc = f'{day.year-1911}/{day:%m/%d}'
            if path == actions.TPEX_DISTRIBUTIONS and kind in {'distribution', 'split', 'dividend'}:
                data.append([roc, '2330', str(self.previous(day)), '除息' if kind=='dividend' else '除權',
                             self.cash_terms if kind=='dividend' else '0', '1000' if kind=='split' else '100' if kind=='distribution' else '0', '0', '0', '0'])
            if path == actions.TPEX_REDUCTIONS and kind == 'reduction':
                cells = {'股票代號/股票名稱:': '2330/測試', '恢復買賣日期:': roc,
                    '現金增資總股數:': 'NA', '現金增資認購價:': 'NA', '現金增資配股率:': 'NA',
                    '每壹仟股換發新股票:': '940 股', '每股退還股款:': '0.6 元/股',
                    '停止買賣日期:': f'{self.stop.year-1911}/{self.stop:%m/%d}'}
                html = '<table>'+''.join(f'<tr><td>{k}</td><td>{v}</td></tr>' for k,v in cells.items())+'</table>'
                data.append([roc, '2330', str(self.previous(self.stop)), '現金減資', html])
        if self.fault == 'wide_terms' and start != end and path == actions.TPEX_DISTRIBUTIONS:
            for r in data:r[5] = '200'
        if self.fault == 'wide_terms' and start != end and path == actions.TPEX_REDUCTIONS:
            for r in data:r[4] = r[4].replace('940 股', '930 股')
        if self.fault == 'wide_current_terms' and end == self.old_day.strftime('%Y%m%d') and path == actions.TPEX_DISTRIBUTIONS:
            for r in data:
                if r[0] == f'{self.target.year-1911}/{self.target:%m/%d}':r[5] = '200'
        if self.fault == 'wrong_current_ref' and path == actions.TPEX_DISTRIBUTIONS:
            for r in data:
                if r[0] == f'{self.target.year-1911}/{self.target:%m/%d}':r[2] = str(float(r[2])+1)
        raw = {'stat': 'ok', 'date': f'{start}~{end}',
               'tables': [{'fields': fields, 'data': data, 'totalCount': len(data)+(1 if self.fault=='truncated' else 0)}]}
        if self.fault == 'wrong_range': raw['date'] = '20200101~20200101'
        return raw, {'source_url': 'https://www.tpex.org.tw'+path+'?'+urlencode(params),
            'snapshot_sha256': tool.fingerprint(raw) if self.fault != 'bad_hash' else '0'*64,
            'captured_at': '2026-11-10T00:00:00+00:00'}


def synthetic_case(db, window=20, kind='rights', position='outside', current=None, listed=True):
    cal, candidate, ids, _ = seed(db)
    if not listed:
        candidate.symbol = '2330.TWO'; db.flush()
    provider = (EventPrices if listed else TpexEventPrices)(cal, window, kind, position, current)
    row = db.scalar(select(Result).where(Result.window_days == window))
    snapshot = forward_validation_candidates_from_runs(db, market='TW', end_date=DAY)[0]
    if kind == 'rights':
        event, sources = rights._rights_issue(candidate.symbol, (provider.old_day,), provider.capture_twse_report)
        payload = {'official_rights_recovery': {'event': event, 'window_action_sources': sources}}
    elif kind in {'distribution', 'reduction'}:
        action, sources = (actions._twse_action if listed else actions._tpex_action)(candidate.symbol, (provider.old_day,),
            provider.capture_twse_report if listed else provider.capture_tpex_report)
        payload = {actions.PROVENANCE_KEY: {'event': {'symbol': candidate.symbol,
            'type': 'cash_reduction' if action.halt_start else 'stock_distribution',
            'effective_date': str(action.effective_date), 'halt_start': str(action.halt_start) if action.halt_start else None,
            'share_ratio': action.share_ratio, 'cash_per_share': action.cash_per_share, 'previous_close': action.previous_close},
            'sources': sources}}
    else:
        payload = {'split_event': {'symbol': candidate.symbol, 'date': str(provider.old_day), 'ratio': 2,
            'corporate_actions_checked': True, 'price_source': 'yfinance'}}
    if kind != 'rights':
        row.status, row.target_date = 'skipped', None
        row.skip_reason = {'distribution': actions.STOCK_DISTRIBUTION, 'reduction': actions.CASH_REDUCTION_HALT,
                           'split': 'stock_split_in_window'}[kind]
    else:
        row.target_date = provider.future[window+1]
    old_sessions = [d for i,d in enumerate(provider.future[:window+2]) if i not in (1,2)]
    row.outcome = bind_validation_input(snapshot, {'outcome': {'return_basis': 'next_open', 'price_basis': 'unadjusted_price',
        'expected_target_date': str(old_sessions[-1]), 'session_dates': [str(d) for d in old_sessions], **payload}})['outcome']
    db.commit()
    request = tool.inventory(db, calendar=cal)
    request['results'] = [r for r in request['results'] if r['id'] == row.id]
    assert len(request['results']) == 1
    before = tool.result_snapshot(row)
    db.rollback()
    return cal, provider, row, before, request


@pytest.mark.parametrize('window', [5, 10, 20, 40, 60])
@pytest.mark.parametrize('kind', ['rights', 'distribution', 'reduction', 'split'])
def test_all_supported_windows_can_exclude_verified_old_actions_and_rollback(db, window, kind):
    cal, provider, row, before, request = synthetic_case(db, window, kind)
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    calls = provider.report_calls
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert plan['results'][0]['after']['status'] == 'validated'
    assert plan['results'][0]['after']['target_date'] == str(provider.target)
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit()
    assert row.outcome['historical_repair']['prior_evidence'] == before['outcome']
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit()
    assert tool.result_snapshot(row) == before
    assert provider.report_calls == calls  # plan/apply/rollback are strictly offline.


@pytest.mark.parametrize('current', ['rights', 'distribution', 'split', 'dividend'])
def test_current_action_is_verified_instead_of_inheriting_the_old_action(db, current):
    cal, provider, row, before, request = synthetic_case(db, current=current)
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    after = plan['results'][0]['after']
    if current in {'distribution', 'split'}:
        assert after['skip_reason'] == {'distribution': actions.STOCK_DISTRIBUTION, 'split': 'stock_split_in_window'}[current]
    else:
        assert after['status'] == 'validated'
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit()
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit()
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('fault', ['reports', 'wrong_range', 'bad_hash', 'event_missing', 'wrong_terms', 'stock_gap', 'index_gap', 'phantom_dividend'])
def test_outside_event_cannot_bypass_missing_or_conflicting_full_evidence(db, fault):
    cal, provider, row, before, request = synthetic_case(db)
    provider.fault = fault
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('position', ['target', 'after_target'])
def test_boundary_event_on_target_stays_inside_and_next_day_is_outside(db, position):
    cal, provider, row, before, request = synthetic_case(db, position=position)
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    payload = plan['results'][0]['after']['outcome']
    assert ('official_rights_recovery' in payload) == (position == 'target')
    assert ('prior_action_scope' in payload['reviewed_official_window']) == (position == 'after_target')


@pytest.mark.parametrize('defect', ['date', 'symbol', 'source'])
def test_unknown_prior_identity_cannot_be_discarded_as_outside(db, defect):
    cal, provider, row, _, request = synthetic_case(db)
    payload = deepcopy(row.outcome)
    event = payload['official_rights_recovery']['event']
    event[{'date': 'ex_date', 'symbol': 'symbol', 'source': 'snapshot_sha256'}[defect]] = None
    row.outcome = payload; db.commit(); before = tool.result_snapshot(row); db.rollback()
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


def test_resume_after_target_does_not_discard_a_halt_that_overlaps_the_window(db):
    from ai_stock_sentinel.daily_radar.next_open_action_scope import excluded_actions
    cal, provider, row, _, request = synthetic_case(db, kind='reduction')
    provider.stop = provider.target
    payload = deepcopy(row.outcome)
    payload['official_corporate_action']['event']['halt_start'] = str(provider.stop)
    row.outcome = payload; db.commit(); before = tool.result_snapshot(row); db.rollback()
    sessions = provider.future[:20]
    assert excluded_actions(payload, '2330.TW', SIGNAL, sessions, DAY) == []
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('defect', ['remove_scope', 'range', 'excluded', 'report_hash', 'extra_call', 'bad_shape', 'wrong_host', 'metrics'])
def test_apply_replays_scope_proof_even_when_manifest_is_rehashed(db, defect):
    cal, provider, row, before, request = synthetic_case(db)
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    changed = deepcopy(plan); payload = changed['results'][0]['after']['outcome']
    scope = payload['reviewed_official_window']['prior_action_scope']
    if defect == 'remove_scope':payload['reviewed_official_window'].pop('prior_action_scope')
    if defect == 'range':scope['session_dates'] = scope['session_dates'][:-1]
    if defect == 'excluded':scope['excluded'][0]['effective_date'] = str(provider.target)
    if defect == 'report_hash':scope['calls'][0]['value'][1]['snapshot_sha256'] = '0'*64
    if defect == 'extra_call':scope['calls'].append(deepcopy(scope['calls'][0]))
    if defect == 'bad_shape':scope['calls'][0]['value'] = None
    if defect == 'wrong_host':
        source = scope['calls'][0]['value'][1]
        source['source_url'] = source['source_url'].replace('www.twse.com.tw', 'www.tpex.org.tw')
    if defect == 'metrics':payload['cost_scenarios']['0']['net_return_pct'] += 1
    changed['sha256'] = tool.fingerprint({k:v for k,v in changed.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='next_open_prior_action_scope|next_open_official'):
        tool.apply_repair_plan(db, changed, expected_sha256=changed['sha256'])
    db.rollback()
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('window', [5, 10, 20, 40, 60])
@pytest.mark.parametrize('kind', ['distribution', 'reduction'])
def test_tpex_outside_actions_keep_full_window_and_offline_rollback(db, window, kind):
    cal, provider, row, before, request = synthetic_case(db, window, kind, listed=False)
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    calls = provider.report_calls
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert plan['results'][0]['after']['status'] == 'validated'
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit()
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit()
    assert tool.result_snapshot(row) == before
    assert provider.report_calls == calls


@pytest.mark.parametrize('fault', ['reports', 'wrong_range', 'truncated', 'bad_hash', 'event_missing'])
def test_tpex_missing_or_unverified_reports_cannot_establish_outside_scope(db, fault):
    cal, provider, row, before, request = synthetic_case(db, kind='reduction', listed=False)
    provider.fault = fault
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('unknown', [False, True])
def test_nested_prior_facts_are_preserved_and_unknown_same_kind_stays_required(db, unknown):
    cal, provider, row, _, request = synthetic_case(db)
    old = deepcopy(row.outcome)
    row.outcome = {k: old[k] for k in ('return_basis', 'price_basis', 'validation_input')} | {
        'historical_repair': {'prior_evidence': old}}
    if unknown:
        prior = deepcopy(old['official_rights_recovery'])
        prior['event']['ex_date'] = None
        row.outcome = dict(row.outcome) | {'official_rights_recovery': prior}
    db.commit(); before = tool.result_snapshot(row); db.rollback()
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    if unknown:
        with pytest.raises(ValueError, match='next_open_repair_incomplete'):
            tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    else:
        plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit()
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit()
    assert tool.result_snapshot(row) == before


def test_action_on_calendar_closed_day_remains_unresolved(db):
    cal, provider, row, before, request = synthetic_case(db)
    closed = next(d for d in (SIGNAL+timedelta(days=i) for i in range(1,15)) if d.weekday() == 5)
    assert closed not in cal.sessions
    provider.events[closed] = 'dividend'
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


def test_action_interval_classification_agrees_with_independent_day_set_oracle():
    from ai_stock_sentinel.daily_radar.next_open_action_scope import excluded_actions
    signal = date(2026,1,1)
    days = [signal+timedelta(days=i) for i in range(18)]
    # Gaps are intentional: an event on a closed day between endpoints remains
    # unresolved rather than being silently declared outside the window.
    sessions = (days[5], days[7], days[11])
    window = set(days[5:12])
    source = {'source_url': 'https://www.twse.com.tw/report', 'snapshot_sha256': 'a'*64,
              'captured_at': '2026-11-10T00:00:00+00:00'}
    for first in range(18):
        for last in range(first,18):
            event = {'type': 'cash_reduction' if first != last else 'stock_distribution',
                     'symbol': '2330.TW', 'effective_date': str(days[last]),
                     'halt_start': str(days[first]) if first != last else None,
                     'share_ratio': 0.94 if first != last else 1.1, 'cash_per_share': 0.6, 'previous_close': 100}
            prior = {'official_corporate_action': {'event': event, 'sources': [source]}}
            actual = excluded_actions(prior, '2330.TW', signal, sessions, days[-1])
            span = set(days[first:last+1])
            assert bool(actual) == span.isdisjoint(window), (first, last)


@pytest.mark.parametrize('listed', [False, True])
@pytest.mark.parametrize('observed,accepted', [(1.2345678, True), (1.23456785, True), (1.2345681, False)])
def test_new_cash_dividend_reuses_existing_bounded_decimal_precision(db, listed, observed, accepted):
    cal, provider, row, before, request = synthetic_case(db,
        kind='rights' if listed else 'distribution', current='dividend', listed=listed)
    # Seven official decimals and eight provider decimals permit at most 5.5e-8.
    provider.cash_terms, provider.dividend_value = '1.2345678', observed
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    if accepted:
        plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
        assert plan['results'][0]['after']['status'] == 'validated'
    else:
        with pytest.raises(ValueError, match='next_open_repair_incomplete'):
            tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


def test_twse_cash_rights_proof_cannot_release_a_tpex_requirement():
    from ai_stock_sentinel.daily_radar.next_open_action_scope import excluded_actions
    cal = calendar(SIGNAL, DAY)
    provider = EventPrices(cal, 20)
    event, sources = rights._rights_issue('2330.TW', (provider.old_day,), provider.capture_twse_report)
    event['symbol'] = '2330.TWO'
    prior = {'official_rights_recovery': {'event': event, 'window_action_sources': sources}}
    assert excluded_actions(prior, '2330.TWO', SIGNAL, provider.future[:20], DAY) == []


@pytest.mark.parametrize('listed', [False, True])
@pytest.mark.parametrize('current', ['distribution', 'dividend'])
def test_failed_current_action_cannot_fall_back_to_generic_split(db, listed, current):
    cal, provider, row, before, request = synthetic_case(db,
        kind='rights' if listed else 'distribution', current=current, listed=listed)
    provider.fault = 'unmatched_split'
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


def test_old_split_is_not_corroborated_by_a_cash_rights_notice(db):
    cal, provider, row, before, request = synthetic_case(db, kind='split')
    provider.events[provider.old_day] = 'rights'
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('listed,kind', [(True,'rights'), (True,'distribution'), (True,'reduction'),
                                       (False,'distribution'), (False,'reduction')])
@pytest.mark.parametrize('fault', ['wide_missing_old', 'wide_terms'])
def test_full_range_and_single_event_reports_must_agree(db, listed, kind, fault):
    cal, provider, row, before, request = synthetic_case(db, kind=kind, listed=listed)
    provider.fault = fault
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('listed', [False, True])
def test_new_dividend_reference_must_match_the_complete_official_price_window(db, listed):
    cal, provider, row, before, request = synthetic_case(db,
        kind='rights' if listed else 'distribution', current='dividend', listed=listed)
    provider.fault = 'wrong_current_ref'
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


def test_current_distribution_terms_must_agree_across_report_ranges(db):
    cal, provider, row, before, request = synthetic_case(db, kind='distribution', current='distribution', listed=False)
    provider.fault = 'wide_current_terms'
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('listed', [False, True])
@pytest.mark.parametrize('ratio,accepted', [(1.1, True), (2, False)])
def test_prior_split_needs_a_matching_official_share_distribution(db, listed, ratio, accepted):
    cal, provider, row, _, request = synthetic_case(db, kind='split', listed=listed)
    provider.events[provider.old_day] = 'distribution'
    payload = deepcopy(row.outcome); payload['split_event']['ratio'] = ratio
    row.outcome = payload; db.commit(); before = tool.result_snapshot(row); db.rollback()
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    if accepted:
        plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
        assert plan['results'][0]['after']['status'] == 'validated'
    else:
        with pytest.raises(ValueError, match='next_open_repair_incomplete'):
            tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('fault', ['phantom_dividend', 'extra_split', 'benchmark_dividend', 'benchmark_split'])
def test_face_value_split_cannot_hide_other_unexplained_window_markers(db, fault):
    cal, provider, row, before, request = synthetic_case(db, current='split')
    if fault == 'extra_split':
        provider.events.pop(provider.target)
        provider.events[provider.future[1]] = 'split'
    provider.fault = fault
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    with pytest.raises(ValueError, match='next_open_repair_incomplete'):
        tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('extra_cash', [False, True])
def test_required_scope_cannot_be_removed_when_old_and_current_results_are_both_splits(db, extra_cash):
    cal, provider, row, before, request = synthetic_case(db, kind='split', current='split')
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    changed = deepcopy(plan); proof = changed['results'][0]['after']['outcome']['reviewed_official_window']
    proof.pop('prior_action_scope')
    if extra_cash:
        proof['calculation_stock'][-1]['dividends'] = 1
    changed['sha256'] = tool.fingerprint({k:v for k,v in changed.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='next_open_prior_action_scope'):
        tool.apply_repair_plan(db, changed, expected_sha256=changed['sha256'])
    db.rollback()
    assert tool.result_snapshot(row) == before


def test_replayed_cash_rights_after_target_is_compatible_with_verified_prior_scope():
    from ai_stock_sentinel.calibration.twse_rights_recovery import PROVENANCE_KEY
    from ai_stock_sentinel.daily_radar.next_open_action_scope import result_matches

    signal, target, ex_date = "2026-10-08", "2026-10-16", "2026-10-20"
    scope = {"symbol": "1711.TW", "signal_date": signal, "session_dates": [target],
             "through": "2026-11-12", "current_events": []}
    benchmark = [{"date": day, "dividends": 0, "stock_splits": 0} for day in (signal, target)]
    result = {"status": "validated", "outcome": {
        "reviewed_official_window": {"calculation_benchmark": benchmark},
        PROVENANCE_KEY: {"event": {"symbol": "1711.TW", "ex_date": ex_date},
                         "verification_mode": "post_window_adjustment_recovered"},
    }}
    result_matches(scope, result)
    with pytest.raises(ValueError, match="event_unresolved"):
        result_matches(scope | {"current_events": [{"effective_date": target}]}, result)


def test_pre_scope_v2_manifest_can_only_rollback_an_exact_applied_snapshot(db):
    from ai_stock_sentinel.daily_radar.history_repair import MUTABLE_FIELDS
    cal, provider, row, before, request = synthetic_case(db, kind='split', current='split')
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)
    plan['results'][0]['after']['outcome']['reviewed_official_window'].pop('prior_action_scope')
    plan['sha256'] = tool.fingerprint({k:v for k,v in plan.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='next_open_prior_action_scope'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])
    db.rollback()
    for entry in plan['results']:
        applied = db.get(Result, entry['id'])
        for field in MUTABLE_FIELDS:
            value = deepcopy(entry['after'][field])
            if field in ('target_date', 'evaluation_as_of_date') and value:
                value = date.fromisoformat(value)
            setattr(applied, field, value)
    db.commit()
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True)
    db.commit(); db.refresh(row)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('outside', [False, True])
def test_calendar_correction_recomputes_rights_window_and_preserves_original(monkeypatch, outside):
    original = fixture_module.TERMS['1727.TW']
    ex_date = fixture_module.TARGET if outside else original[0]
    monkeypatch.setattr(fixture_module, 'TERMS', fixture_module.TERMS | {'1727.TW': (ex_date, *original[1:])})
    fixture = fixture_module.scenario.__wrapped__(monkeypatch)
    engine, run, prices, _, _, _ = next(fixture)
    try:
        assert run().status_code == 200
        client = run.__closure__[0].cell_contents
        old = client.app.dependency_overrides[get_research_price_provider]()
        provider = RecoveringResearchPriceProvider(official_getter=old.official_getter)
        extra = date(2026, 10, 23)
        for rows in prices.values():
            rows.append(deepcopy(rows[0]) | {'date': str(extra)})
            rows.sort(key=lambda r: r['date'])
        def yahoo(self, symbols, *, start_date, end_date):
            return {s: [deepcopy(r) | {'price_source': 'yfinance', 'price_basis': 'unadjusted_price',
                'corporate_actions_checked': True, 'dividends': 0, 'stock_splits': 0}
                for r in prices[s] if str(start_date) <= r['date'] <= str(end_date)] for s in symbols}
        monkeypatch.setattr(YFinanceResearchPriceProvider, 'fetch', yahoo)
        TaiwanDailyBar.__table__.create(engine, checkfirst=True)
        signal, cutoff = fixture_module.SIGNAL, fixture_module.TARGET
        closed = {signal + timedelta(days=i) for i in range((cutoff-signal).days+1)} - set(fixture_module.DAYS) - {extra}
        cal = calendar(signal, cutoff, closed)
        with Session(engine) as db:
            row = db.scalar(select(Result).where(Result.status == 'validated',
                Result.outcome['official_rights_recovery']['event']['symbol'].as_string() == '1727.TW'))
            before = tool.result_snapshot(row)
            db.scalar(select(DailyRadarPreparedRun).where(DailyRadarPreparedRun.run_date == cutoff)).step_statuses = {
                'validation-calendar': cal.payload()}
            db.commit()
            request = tool.inventory(db, calendar=cal)
            request['results'] = [r for r in request['results'] if r['id'] == row.id]
            assert len(request['results']) == 1
            db.rollback()
            evidence = tool.capture_evidence(engine, request, calendar=cal, provider=provider)
            plan = tool.build_repair_plan(engine, request, calendar=cal, evidence=evidence)
            assert plan['results'][0]['after']['target_date'] == '2026-11-02'
            assert tool.result_snapshot(row) == before
            tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit()
            assert row.target_date == date(2026, 11, 2)
            assert row.outcome['historical_repair']['prior_evidence'] == before['outcome']
            tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit()
            assert tool.result_snapshot(row) == before
    finally:
        fixture.close()
