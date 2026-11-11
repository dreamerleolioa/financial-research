"""Record/replay proof that prior actions no longer intersect a repaired window."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from math import isclose
from urllib.parse import parse_qs, urlsplit

from ai_stock_sentinel.calibration import official_corporate_actions as actions, twse_rights_recovery as rights
from ai_stock_sentinel.calibration.forward_validation import number
from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
from ai_stock_sentinel.daily_radar.terminal_integrity import _source

KEY = 'prior_action_scope'
VERSION = 'next-open-prior-action-scope-v1'
RIGHTS_FIELDS = ('type', 'symbol', 'ex_date', 'previous_close', 'subscription_price',
                'subscription_shares_per_thousand', 'cash_dividend_per_share', 'free_shares_per_thousand')
CORPORATE_FIELDS = ('type', 'symbol', 'effective_date', 'share_ratio', 'cash_per_share', 'previous_close', 'halt_start')


def _fact(kind, proof, symbol):
    if not isinstance(proof, dict):
        return None
    event = proof if kind == 'split' else proof.get('event')
    if not isinstance(event, dict) or event.get('symbol') != symbol:
        return None
    try:
        day = date.fromisoformat(event[{'rights': 'ex_date', 'corporate': 'effective_date', 'split': 'date'}[kind]])
        start = date.fromisoformat(event['halt_start']) if event.get('halt_start') else day
        if start > day:
            return None
        if kind == 'rights':
            if not symbol.endswith('.TW') or not _source(event) or any(k not in event for k in RIGHTS_FIELDS):
                return None
            frozen = {k: event[k] for k in RIGHTS_FIELDS}
        elif kind == 'corporate':
            if (not proof.get('sources') or not all(_source(s) for s in proof['sources'])
                    or any(k not in event for k in CORPORATE_FIELDS)
                    or event['type'] not in {'stock_distribution', 'cash_reduction'}
                    or event['type'] == 'cash_reduction' and not event['halt_start']):
                return None
            frozen = {k: event[k] for k in CORPORATE_FIELDS}
        else:
            ratio = number(event.get('ratio'))
            if (ratio is None or ratio <= 0 or ratio == 1 or event.get('corporate_actions_checked') is not True
                    or not event.get('price_source')):
                return None
            frozen = {k: event[k] for k in ('symbol', 'date', 'ratio', 'corporate_actions_checked', 'price_source')}
        return {'kind': kind, 'symbol': symbol, 'start_date': str(start), 'effective_date': str(day), 'event': frozen}
    except (ValueError, TypeError, KeyError):
        return None


def excluded_actions(prior, symbol, signal, sessions, through):
    """Unknown dates retain the old strict requirement; halts use an interval."""
    facts, unknown = {}, set()
    for _ in range(32):
        if not isinstance(prior, dict):
            raise ValueError('next_open_prior_evidence_invalid')
        keys = [(rights.PROVENANCE_KEY, 'rights'), (actions.PROVENANCE_KEY, 'corporate'), ('split_event', 'split')]
        if rights.PROVENANCE_KEY not in prior:
            keys.append(('reviewed_rights_recovery', 'rights'))
        for key, kind in keys:
            if key not in prior:
                continue
            fact = _fact(kind, prior[key], symbol)
            if fact is None or not str(signal) <= fact['start_date'] <= fact['effective_date'] <= str(through):
                unknown.add(kind)
            else:
                facts.setdefault(kind, {})[fingerprint(fact)] = fact
        marker = prior.get('historical_repair')
        if not isinstance(marker, dict) or 'prior_evidence' not in marker:
            break
        prior = marker['prior_evidence']
    else:
        raise ValueError('next_open_prior_evidence_too_deep')
    return sorted((fact for kind, values in facts.items() if kind not in unknown
                   and all(f['effective_date'] < str(sessions[0]) or f['start_date'] > str(sessions[-1])
                           for f in values.values()) for fact in values.values()), key=fingerprint)


def _events(symbol, start, end, provider):
    code = symbol.split('.')[0]
    days = (start, end)
    events = []
    if symbol.endswith('.TW'):
        params = {'response': 'json', 'startDate': start.strftime('%Y%m%d'), 'endDate': end.strftime('%Y%m%d')}
        for path, field, family, fields in (
                (rights.EX_RIGHTS, '資料日期', 'rights', ['除權息前收盤價', '權/息', '詳細資料']),
                (rights.REDUCTION, '恢復買賣日期', 'reduction', []),
                (rights.FACE_VALUE, '恢復買賣日期', 'face_value', [])):
            raw, _ = provider.capture_twse_report(path, params)
            rows = rights._dated_rows(raw, params, field, fields, allow_empty_status=path == rights.REDUCTION)
            for day, row in rows:
                if str(row['股票代號']).strip() != code:
                    continue
                first, notice = day, row.get('權/息')
                if family == 'reduction':
                    action, _ = actions._twse_reduction(code, day, row, provider.capture_twse_report)
                    if action is None:
                        raise ValueError('next_open_prior_action_scope_unverified')
                    first = action.halt_start
                item = {'family': family, 'start_date': str(first), 'effective_date': str(day), 'notice': notice}
                if family == 'rights':
                    if str(row['詳細資料']).strip() != f'{code},{day:%Y%m%d}':
                        raise ValueError('next_open_prior_action_scope_unverified')
                    item['previous_close'] = actions._numeric(row['除權息前收盤價'])
                    if item['previous_close'] <= 0:
                        raise ValueError('next_open_prior_action_scope_unverified')
                elif family == 'reduction':
                    item['action'] = _action_event(symbol, action)
                else:
                    item['face_value_record'] = deepcopy(row)
                if family == 'rights' and notice == '息':
                    if row['詳細資料'] != f'{code},{day:%Y%m%d}':
                        raise ValueError('next_open_prior_action_scope_unverified')
                    raw, _ = provider.capture_twse_report(rights.RIGHTS_DETAIL,
                        {'response': 'json', 'STK_NO': code, 'T1': day.strftime('%Y%m%d')})
                    cash = '(每股配發現金股利)除息'
                    others = {'A. 按普通股股東持股比例每千股無償配股': '股', 'B. 員工紅利轉增資': '股',
                              'C. (有償) 現金增資': '股', '每股認購金額': '元／股', '按股東持股比例每千股認購': '股'}
                    rows_detail = rights._report_rows(raw, ['股票代號', cash, *others])
                    if (len(rows_detail) != 1 or str(rows_detail[0]['股票代號']).strip() != code
                            or any(rights._amount(rows_detail[0][k], unit) for k, unit in others.items())):
                        raise ValueError('next_open_prior_action_scope_unverified')
                    item['cash_dividend'] = rights._amount(rows_detail[0][cash], '元／股')
                    item['cash_terms'] = actions._decimal_text(rows_detail[0][cash], '元／股')
                events.append(item)
    elif symbol.endswith('.TWO'):
        for path, field, code_field, family, fields in (
                (actions.TPEX_DISTRIBUTIONS, '除權息日期', '代號', 'rights',
                 ['除權息前收盤價', '權/息', '現金股利', '每仟股無償配股', '現金增資股數', '現金增資認購價', '按持股比例仟股認購']),
                (actions.TPEX_REDUCTIONS, '恢復買賣日期', '股票代號', 'reduction', ['最後交易日之收盤價格', '減資原因', '詳細資料'])):
            rows, _ = actions._tpex_events(provider.capture_tpex_report, path, days, code, field, code_field, fields)
            for day, row in rows:
                first, notice = day, row.get('權/息')
                if family == 'reduction':
                    action = actions._tpex_reduction(symbol, day, row)
                    if action is None:
                        raise ValueError('next_open_prior_action_scope_unverified')
                    first = action.halt_start
                item = {'family': family, 'start_date': str(first), 'effective_date': str(day), 'notice': notice}
                if family == 'reduction':
                    item['action'] = _action_event(symbol, action)
                else:
                    item['previous_close'] = actions._numeric(row['除權息前收盤價'])
                    if item['previous_close'] <= 0:
                        raise ValueError('next_open_prior_action_scope_unverified')
                    cash_issue = ('現金增資股數', '現金增資認購價', '按持股比例仟股認購')
                    if (notice in {'除權', '除權息'} and actions._numeric(row['每仟股無償配股']) > 0
                            and not any(actions._numeric(row[k]) for k in cash_issue)):
                        item['action'] = _action_event(symbol, actions._distribution(day, row['每仟股無償配股'],
                            row['現金股利'], row['除權息前收盤價']))
                if family == 'rights' and notice == '除息':
                    if any(actions._numeric(row[k]) for k in ('每仟股無償配股', '現金增資股數', '現金增資認購價', '按持股比例仟股認購')):
                        raise ValueError('next_open_prior_action_scope_unverified')
                    item['cash_dividend'] = actions._numeric(row['現金股利'])
                    item['cash_terms'] = actions._decimal_text(row['現金股利'])
                events.append(item)
    else:
        raise ValueError('next_open_prior_action_scope_unverified')
    return sorted(events, key=fingerprint)


def _action_event(symbol, action):
    return {'type': 'cash_reduction' if action.halt_start else 'stock_distribution', 'symbol': symbol,
        'effective_date': str(action.effective_date), 'share_ratio': action.share_ratio,
        'cash_per_share': action.cash_per_share, 'previous_close': action.previous_close,
        'halt_start': str(action.halt_start) if action.halt_start else None}


def _inventory_agrees(item, event):
    if item['effective_date'] != event['effective_date'] or item['start_date'] != (event['halt_start'] or event['effective_date']):
        return False
    if 'action' in item:
        return item['action'] == event
    return (event['type'] == 'stock_distribution' and item['family'] == 'rights'
            and item['notice'] in {'權', '權息'} and item['previous_close'] == event['previous_close'])


def _confirm(fact, events, provider):
    symbol, day, kind = fact['symbol'], date.fromisoformat(fact['effective_date']), fact['kind']
    inventory = [e for e in events if e['effective_date'] == str(day)]
    if kind == 'rights':
        event, _ = rights._rights_issue(symbol, (day,), provider.capture_twse_report)
        matched = ({k: event[k] for k in RIGHTS_FIELDS} == fact['event'] and len(inventory) == 1
                   and inventory[0]['family'] == 'rights' and inventory[0]['notice'] == '權'
                   and inventory[0]['previous_close'] == event['previous_close'])
    elif kind == 'corporate':
        listed = symbol.endswith('.TW')
        action, _ = (actions._twse_action if listed else actions._tpex_action)(symbol, (day,),
            provider.capture_twse_report if listed else provider.capture_tpex_report)
        matched = (action is not None and fact['event'] == _action_event(symbol, action) and len(inventory) == 1
                   and _inventory_agrees(inventory[0], fact['event']))
    else:
        matched = False
        if len(inventory) == 1 and inventory[0]['family'] == 'face_value':
            single = _events(symbol, day, day, provider)
            matched = single == inventory
        elif len(inventory) == 1 and inventory[0]['family'] == 'rights':
            listed = symbol.endswith('.TW')
            action, _ = (actions._twse_action if listed else actions._tpex_action)(symbol, (day,),
                provider.capture_twse_report if listed else provider.capture_tpex_report)
            if action is not None and action.distribution_terms is not None:
                terms = action.distribution_terms
                ratio = 1 + Decimal(terms.shares_per_thousand) / 1000
                radius = actions._rounding_radius(terms.shares_per_thousand, actions.MAX_OFFICIAL_SHARE_ROUNDING) / 1000
                matched = (_inventory_agrees(inventory[0], _action_event(symbol, action))
                    and actions._distribution_match(fact['event']['ratio'], ratio, radius, actions.MAX_PROVIDER_RATIO_ROUNDING)[0])
    if not matched:
        raise ValueError('next_open_prior_action_scope_unverified')


def _prove(candidate, prior, sessions, through, provider):
    signal = date.fromisoformat(candidate['record_date'])
    excluded = excluded_actions(prior, candidate['symbol'], signal, sessions, through)
    if not excluded:
        raise ValueError('next_open_prior_action_scope_invalid')
    start = min(sessions[0], *(date.fromisoformat(f['start_date']) for f in excluded))
    end = max(sessions[-1], *(date.fromisoformat(f['effective_date']) for f in excluded))
    events = _events(candidate['symbol'], start, end, provider)
    for fact in excluded:
        _confirm(fact, events, provider)
    return {'version': VERSION, 'symbol': candidate['symbol'], 'prior_sha256': fingerprint(prior),
            'signal_date': str(signal), 'session_dates': [str(d) for d in sessions], 'through': str(through),
            'excluded': excluded, 'current_events': [e for e in events
                if e['start_date'] <= str(sessions[-1]) and e['effective_date'] >= str(sessions[0])]}


def _check_calls(calls):
    if not isinstance(calls, list):
        raise ValueError('next_open_prior_action_scope_invalid')
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get('request'), dict):
            raise ValueError('next_open_prior_action_scope_invalid')
        request = call['request']
        if request['method'] not in {'capture_twse_report', 'capture_tpex_report'} or call.get('failed'):
            raise ValueError('next_open_prior_action_scope_unverified')
        if (not isinstance(request.get('args'), list) or len(request['args']) != 2
                or not isinstance(request['args'][0], str) or not isinstance(request['args'][1], dict)
                or not isinstance(call.get('value'), (list, tuple)) or len(call['value']) != 2):
            raise ValueError('next_open_prior_action_scope_invalid')
        raw, source = call['value']
        if not isinstance(source, dict):
            raise ValueError('next_open_prior_action_scope_unverified')
        path, params = request['args']
        parsed = urlsplit(source.get('source_url', '')); query = parse_qs(parsed.query)
        host = 'www.twse.com.tw' if request['method'] == 'capture_twse_report' else 'www.tpex.org.tw'
        if (not _source(source) or source['snapshot_sha256'] != fingerprint(raw) or parsed.path != path
                or parsed.hostname != host
                or any(query.get(k) != [str(v)] for k, v in params.items())):
            raise ValueError('next_open_prior_action_scope_unverified')


def collect(candidate, prior, sessions, through, provider):
    if not excluded_actions(prior, candidate['symbol'], date.fromisoformat(candidate['record_date']), sessions, through):
        return None
    offset = provider.position()
    try:
        data = _prove(candidate, prior, sessions, through, provider)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError('next_open_prior_action_scope_unverified') from exc
    calls = deepcopy(provider.calls[offset:provider.position()]); _check_calls(calls)
    return data | {'capabilities': list(provider.capabilities), 'calls': calls}


def verify(candidate, prior, sessions, through, proof):
    from ai_stock_sentinel.daily_radar.next_open_history_repair import _Tape
    if not isinstance(proof, dict) or proof.get('version') != VERSION:
        raise ValueError('next_open_prior_action_scope_invalid')
    try:
        _check_calls(proof['calls'])
        replay = _Tape(evidence=proof)
        expected = _prove(candidate, prior, sessions, through, replay)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError('next_open_prior_action_scope_invalid') from exc
    if replay.offset != len(replay.calls) or proof != expected | {'capabilities': replay.capabilities, 'calls': replay.calls}:
        raise ValueError('next_open_prior_action_scope_invalid')
    return {f['kind'] for f in expected['excluded']}


def _benchmark_markers_match(scope, price_proof):
    expected = {scope['signal_date'], *scope['session_dates']}
    inputs = []
    if 'calculation_benchmark' in price_proof:
        inputs.append(price_proof['calculation_benchmark'])
    replay = price_proof.get('event_replay') or {}
    if 'benchmark' in replay:
        inputs.append(replay['benchmark'])
    if not inputs:
        return False
    for rows in inputs:
        active = [r for r in rows if r.get('date') in expected]
        if len(active) != len(expected) or {r['date'] for r in active} != expected:
            return False
        if any(number(r.get('dividends')) != 0 or number(r.get('stock_splits')) not in (0, 1) for r in active):
            return False
    return True


def _split_markers_match(scope, price_proof, event):
    rows = price_proof.get('calculation_stock') or []
    expected = set(scope['session_dates'])
    if len(rows) != len(expected) or {r.get('date') for r in rows} != expected:
        return False
    for row in rows:
        split = number(row.get('stock_splits'))
        if number(row.get('dividends')) != 0 or split is None:
            return False
        if row['date'] == event['date']:
            if split != number(event['ratio']):
                return False
        elif split not in (0, 1):
            return False
    return True


def result_matches(scope, row):
    """Fresh in-window actions must be resolved by the current result's proof."""
    events, payload = scope['current_events'], row['outcome']
    price_proof = payload['reviewed_official_window']
    if not _benchmark_markers_match(scope, price_proof):
        raise ValueError('next_open_prior_action_scope_event_unresolved')
    proof = payload.get(rights.PROVENANCE_KEY) or payload.get(actions.PROVENANCE_KEY)
    event = proof.get('event') if isinstance(proof, dict) else payload.get('split_event')
    if isinstance(event, dict):
        day = event.get('ex_date') or event.get('effective_date') or event.get('date')
        matched = len(events) == 1 and event.get('symbol') == scope['symbol'] and events[0]['effective_date'] == day
        if rights.PROVENANCE_KEY in payload:
            if (matched and events[0]['family'] == 'rights' and events[0]['notice'] == '權'
                    and events[0]['previous_close'] == event.get('previous_close')):
                return
        elif actions.PROVENANCE_KEY in payload:
            if matched and _inventory_agrees(events[0], event):
                return
        else:
            # Rights/distribution rows have an applicable validator above. Its
            # rejection must not be replaced by a generic Yahoo split marker.
            if matched and events[0]['family'] == 'face_value' and _split_markers_match(scope, price_proof, event):
                return
    elif row['status'] == 'validated' and all('cash_dividend' in e for e in events):
        stock = {r['date']: r for r in payload['reviewed_official_window']['calculation_stock']}
        official = {r['date']: r for r in payload['reviewed_official_window']['stock']}
        observed = {day for day, r in stock.items() if number(r.get('dividends')) not in (None, 0)}
        expected = {e['effective_date'] for e in events if e['cash_dividend'] != 0}
        references_match = True
        for event in events:
            previous = [d for d in official if d < event['effective_date']]
            close = number(official[max(previous)].get('close')) if previous else None
            references_match &= close is not None and isclose(close, event['previous_close'], rel_tol=0, abs_tol=0.005)
        if references_match and observed == expected and all(e['effective_date'] in stock and number(stock[e['effective_date']].get('dividends')) is not None
               and actions._distribution_match(stock[e['effective_date']]['dividends'], Decimal(e['cash_terms']),
                   actions._rounding_radius(e['cash_terms'], actions.MAX_CASH_ROUNDING), actions.MAX_CASH_ROUNDING)[0]
               for e in events):
            return
    raise ValueError('next_open_prior_action_scope_event_unresolved')
