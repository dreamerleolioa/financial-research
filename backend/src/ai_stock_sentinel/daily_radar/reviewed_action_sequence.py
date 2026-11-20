"""Replay split and dividend units for combinations of TWSE events in a reviewed window."""
from datetime import date
from decimal import Decimal
from math import isclose

import numpy as np

from ai_stock_sentinel.calibration import official_corporate_actions as actions, twse_rights_recovery as rights
from ai_stock_sentinel.daily_radar.source_work import SourceDependencyBusy
from ai_stock_sentinel.daily_radar.work_queue import LostLease
from ai_stock_sentinel.daily_radar import next_open_action_scope as scope, reviewed_distribution_basis as basis

KEY = 'reviewed_action_sequence'
VERSION = 'twse-reviewed-action-sequence-v1'
FIELDS = ('(每股配發現金股利)除息', 'A. 按普通股股東持股比例每千股無償配股', 'B. 員工紅利轉增資',
          'C. (有償) 現金增資', '每股認購金額', '按股東持股比例每千股認購')


def _terms(symbol, event, provider):
    day = event['effective_date']
    code = symbol.split('.')[0]
    raw, receipt = provider.capture_twse_report(rights.RIGHTS_DETAIL,
        {'response': 'json', 'STK_NO': code, 'T1': day.replace('-', '')})
    rows = rights._report_rows(raw, ['股票代號', *FIELDS])
    if len(rows) != 1 or str(rows[0]['股票代號']).strip() != code:
        raise ValueError('reviewed_sequence_detail_identity')
    values = [actions._decimal_text(rows[0][k], unit) for k, unit in
              zip(FIELDS, ('元／股', '股', '股', '股', '元／股', '股'), strict=True)]
    cash, free, bonus, issued, price, shares = map(Decimal, values)
    expected_notice = '權息' if cash and (free or issued) else '權' if free or issued else '息'
    if (bonus or bool(issued) != bool(price) or bool(issued) != bool(shares) or not (cash or free or issued)
            or event['notice'] != expected_notice):
        raise ValueError('reviewed_sequence_terms_unsupported')
    if 'cash_terms' in event and cash != Decimal(event['cash_terms']):
        raise ValueError('reviewed_sequence_conflicting_cash_detail')
    kind = ('mixed' if free and issued and cash else 'rights' if issued and not free and not cash
            else 'split' if free and not issued else 'cash' if cash and not free and not issued else None)
    return event | {'kind': kind, 'cash': values[0], 'free': values[1], 'issued': values[3],
                    'price': values[4], 'shares': values[5], 'receipt': receipt}


def _vendor_event(symbol, day, cutoff, provider, bar, event):
    stamp, entries, quote, receipt = basis._chart(provider, symbol, day, cutoff)
    cash, free = Decimal(event['cash']), Decimal(event['free'])
    expected = ({'dividends'} if cash else set()) | ({'splits'} if free else set())
    if set(entries) != expected or not basis._matches(quote, bar):
        raise ValueError('reviewed_sequence_chart_event')
    for family in expected:
        if set(entries[family]) != {str(stamp)} or entries[family][str(stamp)]['date'] != stamp:
            raise ValueError('reviewed_sequence_chart_event')
    vendor_cash = Decimal(str(bar['dividends']))
    if cash and Decimal(str(entries['dividends'][str(stamp)]['amount'])) != vendor_cash:
        raise ValueError('reviewed_sequence_chart_cash')
    share_ratio = Decimal(1)
    split_units = {}
    if free:
        split = entries['splits'][str(stamp)]
        num, den = (Decimal(str(split[k])) for k in ('numerator', 'denominator'))
        if num <= den or den <= 0 or [Decimal(v) for v in split['splitRatio'].split(':')] != [num, den]:
            raise ValueError('reviewed_sequence_chart_ratio')
        share_ratio = num / den
        split_units = {'numerator':str(num), 'denominator':str(den)}
        radius = actions._rounding_radius(event['free'], actions.MAX_OFFICIAL_SHARE_ROUNDING) / 1000
        matches, _ = actions._distribution_match(float(share_ratio), 1 + free / 1000, radius,
                                                 actions.MAX_PROVIDER_RATIO_ROUNDING)
        if not matches or share_ratio != Decimal(str(bar['stock_splits'])):
            raise ValueError('reviewed_sequence_split_mismatch')
    if (not cash and vendor_cash or not free and rights.number(bar.get('stock_splits')) not in (0, 1)):
        raise ValueError('reviewed_sequence_extra_marker')
    return event | {'vendor_cash': str(vendor_cash), 'vendor_ratio': str(share_ratio), 'chart': receipt, **split_units}


def _binary32_interval(value, *, price=False):
    """Endpoints that round to this observed binary32 number."""
    observed = np.float32(value)
    if not np.isfinite(observed) or observed <= 0 or price and float(observed) != float(value):
        raise ValueError('reviewed_sequence_vendor_precision')
    if not price and Decimal(np.format_float_positional(observed, unique=True, trim='-')) != Decimal(value):
        raise ValueError('reviewed_sequence_vendor_precision')
    previous = np.nextafter(observed, np.float32(0))
    following = np.nextafter(observed, np.float32(np.inf))
    center = Decimal.from_float(float(observed))
    return ((Decimal.from_float(float(previous)) + center) / 2,
            (Decimal.from_float(float(following)) + center) / 2)


def _mixed_ratio(split, dates, stock, vendor):
    # All observed OHLC prices and the chart's per-thousand numerator must
    # admit ONE ratio. Checking each price against a different rounded ratio
    # would conceal mutually inconsistent quotations.
    if Decimal(split['denominator']) != 1000:
        raise ValueError('reviewed_sequence_vendor_precision')
    lower, upper = (v / 1000 for v in _binary32_interval(split['numerator']))
    radius = actions._rounding_radius(split['free'], actions.MAX_OFFICIAL_SHARE_ROUNDING)
    free = Decimal(split['free'])
    lower, upper = max(lower, 1+(free-radius)/1000), min(upper, 1+(free+radius)/1000)
    for d in dates:
        if d >= split['effective_date']:
            break
        for key in rights.OHLC:
            low, high = _binary32_interval(vendor[d][key], price=True)
            raw = Decimal(str(stock[d][key]))
            lower, upper = max(lower, raw/high), min(upper, raw/low)
    if lower == upper and radius == 0:
        for d in dates:
            if d < split['effective_date'] and any(float(np.float32(float(Decimal(str(stock[d][k])) / lower)))
                                                  != vendor[d][k] for k in rights.OHLC):
                raise ValueError('reviewed_sequence_inconsistent_ratio')
    elif lower >= upper:
        raise ValueError('reviewed_sequence_inconsistent_ratio')
    return {'lower':str(lower), 'upper':str(upper)}


def _official_bars(provider, symbol, days):
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    from ai_stock_sentinel.daily_radar.next_open_official_repair import _prices
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint
    from ai_stock_sentinel.daily_radar.terminal_integrity import _source
    reader = RecoveringResearchPriceProvider()
    def capture(path, params):
        raw, source = provider.capture_twse_report(path, params)
        if not _source(source) or fingerprint(raw) != source['snapshot_sha256']:
            raise ValueError('reviewed_sequence_official_report_digest')
        return raw, source
    reader.capture_twse_report = capture
    bounds = {'start_date':days[0], 'end_date':days[-1]}
    archived = _prices(provider.official_research_history(symbol, **bounds), days, symbol)
    parsed = _prices(reader.official_research_history(symbol, **bounds), days, symbol)
    if archived != parsed:
        raise ValueError('reviewed_sequence_official_history_changed')
    return parsed


def classify(row, yahoo, marked, benchmark, *, calendar, provider):
    try:
        return _classify(row, yahoo, marked, benchmark, calendar, provider)
    except (KeyError, TypeError, ArithmeticError, AttributeError, IndexError) as exc:
        raise ValueError('reviewed_sequence_invalid_evidence') from exc


def _classify(row, yahoo, marked, benchmark, calendar, provider):
    symbol = row['symbol']
    signal = date.fromisoformat(row['signal_date'])
    target = date.fromisoformat(row['outcome']['expected_target_date'])
    days = [d for d in calendar.sessions if signal <= d <= target]
    if (not symbol.endswith('.TW') or not all(callable(getattr(provider, k, None)) for k in
            ('capture_twse_report', 'capture_split_day', 'official_research_history'))):
        return None
    if (len(days) != row['window_days'] + 1 or days[0] != signal or days[-1] != target or target > calendar.through):
        raise ValueError('reviewed_sequence_sessions')
    try:
        inventory = sorted(scope._events(symbol, signal, target, provider), key=lambda e: e['effective_date'])
    except (LostLease, SourceDependencyBusy):
        raise
    except (OSError, RuntimeError, ValueError):
        # With no positive evidence for this sequence the existing single-event
        # validators still own their respective decisions and error reports.
        return None
    if len({e['effective_date'] for e in inventory}) != len(inventory):
        raise ValueError('reviewed_sequence_duplicate_event')
    if not 1 <= len(inventory) <= 2 or any(e['family'] != 'rights' for e in inventory):
        return None
    events = [_terms(symbol, e, provider) for e in inventory]
    kinds = tuple(e['kind'] for e in events)
    if kinds not in {('mixed',), ('cash', 'split'), ('split', 'rights')}:
        return None
    dates = [str(d) for d in days]
    by_day = {e['effective_date']: e for e in events}
    if len(by_day) != len(events) or set(by_day) - set(dates):
        raise ValueError('reviewed_sequence_event_session')
    split = next(e for e in events if Decimal(e['free']))
    split_day = split['effective_date']
    if split_day == str(signal):
        return None
    def window(series, dataset):
        return rights._daily_rows([r for r in series if r['date'] in dates], days, dataset)
    stock = _official_bars(provider, symbol, days)
    index = _official_bars(provider, 'TAIEX', days)
    vendor = window(yahoo, 'yfinance')
    for series in (yahoo, benchmark):
        for r in series:
            key = r['date']
            if key in dates and (r.get('price_basis') != 'unadjusted_price' or r.get('corporate_actions_checked') is not True):
                raise ValueError('reviewed_sequence_unverified_quote')
            if str(signal) <= key <= str(target) and key not in dates and (rights.number(r.get('volume')) != 0
                    or rights.number(r.get('stock_splits')) not in (0, 1) or rights.number(r.get('dividends')) != 0):
                raise ValueError('reviewed_sequence_off_session')
    for key, quote in window(benchmark,'yfinance').items():
        if (not basis._matches(quote,index[key]) or rights.number(quote.get('stock_splits')) not in (0, 1)
                or rights.number(quote.get('dividends')) != 0):
            raise ValueError('reviewed_sequence_benchmark_conflict')
    for key, quote in vendor.items():
        if (rights.number(quote.get('volume')) or 0) <= 0 or (key not in by_day and (
                rights.number(quote.get('stock_splits')) not in (0, 1) or rights.number(quote.get('dividends')) != 0)):
            raise ValueError('reviewed_sequence_extra_marker')
    for r in marked:
        if r['date'] in vendor and (not basis._matches(r, vendor[r['date']]) or
                set(r.get('ohlc_conflict_fields') or ()) - set(rights.OHLC)):
            raise ValueError('reviewed_sequence_conflicting_archive')

    proved = []
    for event in events:
        d = date.fromisoformat(event['effective_date'])
        earlier = [s for s in calendar.sessions if s < d]
        if not earlier:
            raise ValueError('reviewed_sequence_reference_close_missing')
        previous = earlier[-1]
        previous_row = stock.get(str(previous))
        if previous_row is None:
            previous_row = _official_bars(provider,symbol,[previous])[str(previous)]
        if Decimal(str(previous_row['close'])) != Decimal(str(event['previous_close'])):
            raise ValueError('reviewed_sequence_reference_close_mismatch')
        proved.append(_vendor_event(symbol, d, target, provider, vendor[str(d)],event) |
                      {'previous_quote': previous_row['official_price_report']})
    split = next(e for e in proved if Decimal(e['free']))
    ratio = Decimal(split['vendor_ratio'])
    rights_day = None
    factor = Decimal(1)
    if kinds == ('split','rights'):
        issue = proved[-1]
        rights_day = issue['effective_date']
        subs = Decimal(issue['shares']) / 1000
        close = Decimal(str(issue['previous_close']))
        factor = (close + Decimal(issue['price'])*subs) / ((1+subs)*close)
        if not 0 < factor < 1:
            raise ValueError('reviewed_sequence_rights_factor')
    for event in proved:
        expected_cash = Decimal(event['cash']) * factor if rights_day else Decimal(event['cash']) / ratio
        if Decimal(event['vendor_cash']) != expected_cash.quantize(Decimal('.000001')):
            raise ValueError('reviewed_sequence_dividend_units')

    ratio_interval = _mixed_ratio(split, dates, stock, vendor) if kinds == ('mixed',) else None
    split_dates, rights_dates = [], []
    for d in dates:
        raw = stock[d]
        if d < split_day:
            if not ratio_interval and not basis._matches(vendor[d],raw,1/ratio):
                raise ValueError('reviewed_sequence_split_price')
            if d != str(signal):split_dates.append(d)
        elif rights_day and split_day < d < rights_day and basis._matches(vendor[d],raw,factor) and not basis._matches(vendor[d],raw):
            rights_dates.append(d)
        elif not basis._matches(vendor[d],raw) or rights_dates and d < rights_day:
            raise ValueError('reviewed_sequence_unexplained_price')
    if (not split_dates and dates[1] != split_day) or rights_day and (not rights_dates or rights_dates[-1] != dates[dates.index(rights_day)-1]):
        raise ValueError('reviewed_sequence_missing_transition')
    for bar in marked:
        day = bar['date']
        if day in stock:
            changed = {k for k in rights.OHLC if not isclose(vendor[day][k], stock[day][k],
                                                           rel_tol=1e-7, abs_tol=basis.PRICE_TOLERANCE)}
            if not set(bar.get('ohlc_conflict_fields') or ()).issubset(changed):
                raise ValueError('reviewed_sequence_conflicting_archive')
    sources = [e['receipt'] for e in proved] + [r['official_price_report'] for r in [*stock.values(), *index.values()]]
    event = {'type':'stock_distribution','symbol':symbol,'effective_date':split_day,'previous_close':split['previous_close'],
             'cash_per_share':float(split['cash']),'share_ratio':float(1+Decimal(split['free'])/1000),'halt_start':None}
    proof = {'evidence_version':actions.EVIDENCE_VERSION, 'event':event, 'session_dates':dates[1:],
             'halt_dates':[], 'yahoo_adjusted_dates':split_dates,
             'sources':list({s['source_url']:s for s in sources}.values()),
             KEY:{'version':VERSION,'events':proved,'rights_adjusted_dates':rights_dates,'rights_factor':str(factor),'share_ratio_interval':ratio_interval}}
    if not split_dates:
        proof[KEY]['signal_reference'] = {'date':dates[0], 'official':stock[dates[0]], 'vendor':vendor[dates[0]]}
    return row | {'status':'skipped','skip_reason':actions.STOCK_DISTRIBUTION,'target_date':str(target),
                  'outcome':row['outcome'] | {'missing_dates':[],actions.PROVENANCE_KEY:proof}}


def has_first_session_reference(proof, signal, days):
    from ai_stock_sentinel.daily_radar.terminal_integrity import _source
    try:
        sequence = proof[KEY]
        split, = [e for e in sequence['events'] if Decimal(e['free'])]
        anchor = sequence['signal_reference']
        raw, vendor = anchor['official'], anchor['vendor']
        return (sequence['version'] == VERSION and split['effective_date'] == days[0] == proof['event']['effective_date']
                and raw['date'] == vendor['date'] == anchor['date'] == str(signal)
                and raw['price_source'] == 'twse_stock_day' and _source(raw['official_price_report'])
                and vendor['price_source'] == 'yfinance' and vendor['corporate_actions_checked'] is True
                and raw['close'] == split['previous_close'] == proof['event']['previous_close']
                and Decimal(split['vendor_ratio']) > 1
                and (basis._matches(vendor,raw,1/Decimal(split['vendor_ratio'])) if not sequence['share_ratio_interval']
                     else sequence['share_ratio_interval']==_mixed_ratio(split,[str(signal)],{str(signal):raw},{str(signal):vendor})))
    except (TypeError, ValueError, KeyError, IndexError, ArithmeticError):
        return False
