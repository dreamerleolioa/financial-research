"""Full official evidence required by reviewed next-open recomputation.

No database access. Collection calls the supplied record/replay provider; the
same frozen proof is checked again under apply/rollback locks without I/O.
"""
from copy import deepcopy
from datetime import date
from urllib.parse import parse_qs, urlsplit

from ai_stock_sentinel.calibration import twse_rights_recovery as rights, official_corporate_actions as actions
from ai_stock_sentinel.calibration.research_prices import _mark_conflicts
from ai_stock_sentinel.daily_radar.research_validation import (
    evaluate_research_window, _recover_rights_window, _classify_corporate_action,
)
from ai_stock_sentinel.daily_radar.terminal_integrity import _source
from ai_stock_sentinel.daily_radar.validation_inputs import PROOF_KEY
from ai_stock_sentinel.daily_radar import next_open_action_scope as action_scope

KEY = 'reviewed_official_window'
VERSION = 'next-open-reviewed-official-window-v1'
RIGHTS_VERIFICATION = 'full-window-recovery-or-matching-v1'


def prior_actions(outcome):
    """Never lose an action merely because an earlier repair nested its proof."""
    found = set()
    for _ in range(32):
        if not isinstance(outcome, dict):
            raise ValueError('next_open_prior_evidence_invalid')
        if any(k in outcome for k in (rights.PROVENANCE_KEY, 'reviewed_rights_recovery')):
            found.add('rights')
        if actions.PROVENANCE_KEY in outcome:
            found.add('corporate')
        if 'split_event' in outcome:
            found.add('split')
        marker = outcome.get('historical_repair')
        if not isinstance(marker, dict) or 'prior_evidence' not in marker:
            return found
        outcome = marker['prior_evidence']
    raise ValueError('next_open_prior_evidence_too_deep')


def _prices(rows, dates, symbol):
    dataset = ('twse_index_history' if symbol == 'TAIEX' else
               'twse_stock_day' if symbol.endswith('.TW') else 'tpex_trading_stock')
    result = rights._daily_rows(rows, dates, dataset)
    for day, row in result.items():
        source = row.get('official_price_report')
        if not _source(source):
            raise ValueError('next_open_official_source_invalid')
        parsed = urlsplit(source['source_url']); query = parse_qs(parsed.query)
        month = day[:7].replace('-', '') + '01'
        if symbol == 'TAIEX':
            valid = (parsed.hostname == 'www.twse.com.tw' and parsed.path == '/indicesReport/MI_5MINS_HIST'
                     and query.get('date') == [month])
        elif symbol.endswith('.TW'):
            valid = (parsed.hostname == 'www.twse.com.tw' and parsed.path == '/exchangeReport/STOCK_DAY'
                     and query.get('date') == [month] and query.get('stockNo') == [symbol.split('.')[0]])
        else:
            valid = (parsed.hostname == 'www.tpex.org.tw' and parsed.path == '/www/zh-tw/afterTrading/tradingStock'
                     and query.get('date') == [day[:7].replace('-', '/') + '/01']
                     and query.get('code') == [symbol.split('.')[0]])
        if not valid:
            raise ValueError('next_open_official_source_identity_invalid')
    return result


def _window(candidate, window, calendar):
    signal = date.fromisoformat(candidate['record_date'])
    sessions = [d for d in calendar.sessions if d > signal][:window]
    if len(sessions) != window:
        raise ValueError('next_open_official_window_not_mature')
    return signal, sessions


def _event_input(candidate, window, calendar, marked, benchmark):
    """Derive an event request from prices, never from a manifest's decision."""
    _, sessions = _window(candidate, window, calendar)
    row = evaluate_research_window(candidate, price_series=marked, benchmark_prices=benchmark,
        window_days=window, as_of_date=sessions[-1], benchmark_symbol='TAIEX',
        trading_calendar=calendar.sessions, validate_signal_session=True)
    observed = row['outcome']
    return dict(row) | {'status': 'skipped', 'skip_reason': rights.CONFLICT_REASON,
        'target_date': None, 'evaluation_as_of_date': str(calendar.through),
        'outcome': {'return_basis': 'next_open', 'price_basis': 'unadjusted_price',
            'missing_dates': observed.get('missing_dates', []),
            'expected_target_date': str(sessions[-1]), PROOF_KEY: observed[PROOF_KEY]}}


def collect_window(candidate, before, row, *, calendar, provider):
    if not callable(getattr(provider, 'official_research_history', None)):
        raise ValueError('next_open_official_provider_unavailable')
    signal, sessions = _window(candidate, before['window_days'], calendar)
    symbol, target = candidate['symbol'], sessions[-1]
    yahoo = provider.fetched.get(symbol, [])
    benchmark = provider.repaired.get('TAIEX', provider.fetched.get('TAIEX', []))
    marked = deepcopy(provider.repaired.get(symbol, yahoo))
    scope = action_scope.collect(candidate, before['outcome'], sessions, calendar.through, provider)
    removed = {f['kind'] for f in scope['excluded']} if scope else set()
    kind = (prior_actions(before['outcome']) - removed) | prior_actions(row.get('outcome') or {})
    event_replay = None
    def event(kind, stock_rows, benchmark_rows, marked_rows):
        event_row = _event_input(candidate, before['window_days'], calendar, marked_rows, benchmark_rows)
        start = provider.position()
        if kind == 'rights':
            result = _recover_rights_window(candidate, event_row, stock_rows, marked_rows, benchmark_rows,
                calendar.sessions, as_of_date=target, scoped=True, provider=provider, reviewed=True)
        else:
            result = _classify_corporate_action(event_row, stock_rows, marked_rows, benchmark_rows,
                as_of_date=target, provider=provider)
        frozen = {'kind': kind, 'row': deepcopy(event_row), 'yahoo': deepcopy(stock_rows),
            'marked': deepcopy(marked_rows), 'benchmark': deepcopy(benchmark_rows),
            'capabilities': list(provider.capabilities), 'calls': deepcopy(provider.calls[start:provider.position()])}
        if kind == 'rights':
            frozen['rights_verification'] = RIGHTS_VERIFICATION
        return result, frozen
    if 'rights' in kind:
        # Explicit scope makes prior official rights evidence mandatory even
        # when today's Yahoo rows have no conflict flags. Existing precision,
        # event, benchmark and entire price-window checks remain authoritative.
        row, event_replay = event('rights', yahoo, benchmark, marked)
        if row is None:
            raise ValueError('next_open_official_rights_unverified')
    elif 'corporate' in kind:
        row, event_replay = event('corporate', yahoo, benchmark, marked)
        if row is None:
            raise ValueError('next_open_official_action_unverified')
    elif scope and any(e['family'] == 'reduction' for e in scope['current_events']):
        # Prove the current halt before demanding prices for its closed days.
        # Only the existing reason-specific validator may exempt those dates.
        row, event_replay = event('corporate', yahoo, benchmark, marked)
        if row is None:
            raise ValueError('next_open_official_action_unverified')
    else:
        stock = provider.official_research_history(symbol, start_date=signal, end_date=target)
        index = provider.official_research_history('TAIEX', start_date=signal, end_date=target)
        stock_index = _prices(stock, [signal, *sessions], symbol)
        benchmark_index = _prices(index, [signal, *sessions], 'TAIEX')
        _mark_conflicts({r['date']: r for r in marked}, stock_index)
        benchmark = deepcopy(benchmark)
        _mark_conflicts({r['date']: r for r in benchmark}, benchmark_index)
        row = evaluate_research_window(candidate, price_series=marked, benchmark_prices=benchmark,
            window_days=before['window_days'], as_of_date=calendar.through,
            benchmark_symbol='TAIEX', trading_calendar=calendar.sessions, validate_signal_session=True)
        if row.get('skip_reason') == rights.CONFLICT_REASON or scope and scope['current_events']:
            recovered = None
            if symbol.endswith('.TW'):
                recovered, frozen = event('rights', yahoo, benchmark, marked)
                if recovered is not None:event_replay = frozen
            if recovered is None:
                recovered, frozen = event('corporate', yahoo, benchmark, marked)
                if recovered is not None:event_replay = frozen
            row = recovered or row
    row = deepcopy(row); row['evaluation_as_of_date'] = str(calendar.through)
    if 'split' in kind and (row['status'] != 'skipped' or row.get('skip_reason') not in {'stock_split_in_window', *actions.TERMINAL_REASONS}):
        raise ValueError('next_open_official_split_unverified')
    halt = set((row.get('outcome', {}).get(actions.PROVENANCE_KEY) or {}).get('halt_dates', []))
    # Halt omissions must be proved by the existing reason-specific terminal
    # contract; a missing official stock day can never invent a closure.
    stock_dates = [d for d in (signal, *sessions) if str(d) not in halt]
    stock = provider.official_research_history(symbol, start_date=signal, end_date=target)
    index = provider.official_research_history('TAIEX', start_date=signal, end_date=target)
    _prices(stock, stock_dates, symbol); _prices(index, [signal, *sessions], 'TAIEX')
    row['outcome'] = dict(row['outcome']) | {KEY: {'version': VERSION, 'symbol': symbol,
        'signal_date': str(signal), 'session_dates': [str(d) for d in sessions],
        'stock': stock, 'benchmark': index}}
    if event_replay is not None:
        row['outcome'][KEY]['event_replay'] = event_replay
    if scope is not None:
        row['outcome'][KEY][action_scope.KEY] = scope
    if row['status'] == 'validated' or row.get('skip_reason') == 'stock_split_in_window':
        if rights.PROVENANCE_KEY in row['outcome']:
            used_stock = rights._unadjusted_rows(stock, 'twse_official_rights_window')
            used_index = rights._unadjusted_rows(index, 'index_not_applicable')
        else:
            used_stock, used_index = marked, benchmark
        row['outcome'][KEY].update(
            calculation_stock=[deepcopy(r) for r in used_stock if str(signal) < r['date'] <= str(target)],
            calculation_benchmark=[deepcopy(r) for r in used_index if str(signal) <= r['date'] <= str(target)])
    if scope is not None:
        action_scope.result_matches(scope, row)
    return row


def verify_evidence(candidate, after, *, calendar, prior, require_scope=True):
    signal, sessions = _window(candidate, after['window_days'], calendar)
    payload = after['outcome']; proof = payload.get(KEY)
    if (not isinstance(proof, dict) or proof.get('version') != VERSION
            or proof.get('symbol') != candidate['symbol'] or proof.get('signal_date') != str(signal)
            or proof.get('session_dates') != [str(d) for d in sessions]):
        raise ValueError('next_open_official_evidence_invalid')
    kind = prior_actions(prior)
    if action_scope.KEY in proof:
        kind -= action_scope.verify(candidate, prior, sessions, calendar.through, proof[action_scope.KEY])
        action_scope.result_matches(proof[action_scope.KEY], after)
    elif require_scope and action_scope.excluded_actions(prior, candidate['symbol'], signal, sessions, calendar.through):
        raise ValueError('next_open_prior_action_scope_invalid')
    rights_proof = payload.get(rights.PROVENANCE_KEY) or {}
    if 'rights' in kind and (after['status'] != 'validated'
            or rights_proof.get('evidence_version') != 'twse-cash-rights-v1'
            or rights_proof.get('session_dates') != [str(d) for d in sessions]
            or (rights_proof.get('event') or {}).get('symbol') != candidate['symbol']
            or not _source(rights_proof.get('event'))
            or not rights_proof.get('window_action_sources')
            or not all(_source(s) for s in rights_proof['window_action_sources'])):
        raise ValueError('next_open_official_rights_unverified')
    if 'corporate' in kind and (after['status'] != 'skipped' or after['skip_reason'] not in actions.TERMINAL_REASONS):
        raise ValueError('next_open_official_action_unverified')
    if 'split' in kind and (after['status'] != 'skipped' or after.get('skip_reason') not in {'stock_split_in_window', *actions.TERMINAL_REASONS}):
        raise ValueError('next_open_official_split_unverified')
    event_kind = ('rights' if rights.PROVENANCE_KEY in payload else
                  'corporate' if actions.PROVENANCE_KEY in payload else None)
    if event_kind is not None:
        _verify_event(candidate, after, proof.get('event_replay'), event_kind, calendar, sessions[-1])
    halt = set((payload.get(actions.PROVENANCE_KEY) or {}).get('halt_dates', []))
    stock = _prices(proof.get('stock', []), [d for d in (signal, *sessions) if str(d) not in halt], candidate['symbol'])
    benchmark = _prices(proof.get('benchmark', []), [signal, *sessions], 'TAIEX')
    if after['status'] == 'validated' or after.get('skip_reason') == 'stock_split_in_window':
        used_stock, used_index = deepcopy(proof.get('calculation_stock', [])), deepcopy(proof.get('calculation_benchmark', []))
        for rows, expected, official in ((used_stock, sessions, stock), (used_index, [signal, *sessions], benchmark)):
            if len(rows) != len(expected) or {r.get('date') for r in rows} != {str(d) for d in expected}:
                raise ValueError('next_open_official_calculation_window_invalid')
            # Keep the existing shared tolerance. Floating-point Yahoo prices
            # can agree with official decimals without being bit-identical.
            _mark_conflicts({r['date']: r for r in rows}, official)
            if any(r.get('ohlc_conflict_fields') for r in rows):
                raise ValueError('next_open_official_calculation_prices_conflict')
        checked = evaluate_research_window(candidate,
            price_series=used_stock, benchmark_prices=used_index,
            window_days=after['window_days'], as_of_date=calendar.through,
            benchmark_symbol='TAIEX', trading_calendar=calendar.sessions, validate_signal_session=True)
        fields = ('entry_price', 'target_price', 'forward_return_pct', 'benchmark_return_pct',
                  'excess_return_vs_benchmark_pct', 'max_adverse_excursion_pct', 'max_favorable_excursion_pct')
        if after.get('skip_reason') == 'stock_split_in_window':
            if (checked.get('skip_reason') != 'stock_split_in_window'
                    or payload.get('split_event') != checked['outcome'].get('split_event')):
                raise ValueError('next_open_official_split_unverified')
            return
        if checked['status'] != 'validated' or any(payload.get(k) != checked['outcome'].get(k) for k in fields):
            raise ValueError('next_open_official_metrics_mismatch')
        if 'validation_window' not in payload:
            checked['outcome'].pop('validation_window', None)  # Legacy full session proof remains authoritative.
        if action_scope.KEY in proof and event_kind is None and {
                k: v for k, v in payload.items() if k not in (KEY, 'historical_repair')} != checked['outcome']:
            raise ValueError('next_open_official_metrics_mismatch')


def _verify_event(candidate, after, frozen, kind, calendar, target):
    from ai_stock_sentinel.daily_radar.next_open_history_repair import _Tape
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
    if not isinstance(frozen, dict) or frozen.get('kind') != kind:
        raise ValueError('next_open_official_event_replay_missing')
    row = _event_input(candidate, after['window_days'], calendar, frozen['marked'], frozen['benchmark'])
    if frozen.get('row') != row:
        raise ValueError('next_open_official_event_identity_invalid')
    for call in frozen['calls']:
        if call['request']['method'] in ('capture_twse_report','capture_tpex_report') and not call.get('failed'):
            raw, source = call['value']
            if not _source(source) or source['snapshot_sha256'] != fingerprint(raw):
                raise ValueError('next_open_official_event_report_hash_invalid')
    replay = _Tape(evidence=frozen)
    if kind == 'rights':
        mode = frozen.get('rights_verification')
        if mode not in (None, RIGHTS_VERIFICATION):
            raise ValueError('next_open_official_event_verification_invalid')
        result = _recover_rights_window(candidate, row, frozen['yahoo'], frozen['marked'], frozen['benchmark'],
            calendar.sessions, as_of_date=target, scoped=True, provider=replay,
            reviewed=mode == RIGHTS_VERIFICATION)
    else:
        result = _classify_corporate_action(row, frozen['yahoo'], frozen['marked'], frozen['benchmark'],
            as_of_date=target, provider=replay)
        if result is not None and (result['status'] != 'skipped' or result['skip_reason'] not in actions.TERMINAL_REASONS):
            raise ValueError('next_open_official_event_replay_mismatch')
    derived = {k:v for k,v in after['outcome'].items() if k not in (KEY, 'historical_repair')}
    if result is not None and 'validation_window' not in derived:
        result['outcome'].pop('validation_window', None)
    if (result is None or replay.offset != len(replay.calls)
            or any(result.get(k) != after.get(k) for k in ('status', 'skip_reason', 'target_date'))
            or result['outcome'] != derived):
        raise ValueError('next_open_official_event_replay_mismatch')
    # The price proof and calculation rows must describe the same inputs that
    # the event validator actually consumed, rather than an independent copy
    # with a separately consistent but contradictory result.
    histories = {call['request']['args'][0]: call['value'] for call in replay.calls
                 if call['request']['method'] == 'official_research_history' and not call.get('failed')}
    proof = after['outcome'][KEY]
    if proof['stock'] != histories.get(candidate['symbol']) or proof['benchmark'] != histories.get('TAIEX'):
        raise ValueError('next_open_official_event_inputs_mismatch')
    if kind == 'rights':
        signal, sessions = _window(candidate, after['window_days'], calendar)
        stock = rights._daily_rows(histories[candidate['symbol']], [signal, *sessions], 'twse_stock_day')
        index = rights._daily_rows(histories['TAIEX'], [signal, *sessions], 'twse_index_history')
        if (proof.get('calculation_stock') != rights._unadjusted_rows(
                (stock[str(d)] for d in sessions), 'twse_official_rights_window')
                or proof.get('calculation_benchmark') != rights._unadjusted_rows(index.values(), 'index_not_applicable')):
            raise ValueError('next_open_official_event_inputs_mismatch')
