"""Reconcile vendor units for an archived TWSE distribution, only in historical review."""
from datetime import date, datetime, time
from decimal import Decimal
from math import isclose
from zoneinfo import ZoneInfo

from ai_stock_sentinel.calibration import official_corporate_actions as actions, twse_rights_recovery as rights
from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
from ai_stock_sentinel.daily_radar.next_open_action_scope import RIGHTS_FIELDS
from ai_stock_sentinel.daily_radar.research_validation import _classify_corporate_action

KEY = 'reviewed_distribution_basis'
VERSION = 'twse-post-window-distribution-basis-v1'
ZONE = ZoneInfo('Asia/Taipei')
PRICE_TOLERANCE = 0.00005


def matching_event(rows, signal, target):
    events = [r for r in rows if isinstance(r, dict) and isinstance(r.get('date'), str)
              and str(signal) < r['date'] <= str(target)
              and (rights.number(r.get('stock_splits')) or 0) > 1
              and (rights.number(r.get('dividends')) or 0) > 0]
    return events[0] if len(events) == 1 else None


def _chart(provider, symbol, day, ex_day):
    raw, source = provider.capture_split_day(symbol, day)
    if not actions.valid_split_receipt(raw, source, symbol, day):
        raise ValueError('reviewed_distribution_chart_receipt_invalid')
    try:
        chart = raw['chart']
        record, = chart['result']
        stamp, = record['timestamp']
        quote, = record['indicators']['quote']
        prices = {}
        for key in (*rights.OHLC, 'volume'):
            value, = quote[key]
            prices[key] = rights.number(value)
        if (chart['error'] is not None or record['meta']['symbol'] != symbol
                or record['meta']['exchangeTimezoneName'] != ZONE.key
                or datetime.fromtimestamp(stamp, ZONE).date() != day
                or datetime.fromisoformat(source['captured_at']) < datetime.combine(ex_day, time(13, 30), ZONE)
                or any(v is None or v <= 0 for v in prices.values())
                or not prices['low'] <= min(prices['open'], prices['close'])
                <= max(prices['open'], prices['close']) <= prices['high']):
            raise ValueError
        return stamp, record.get('events', {}), prices, source
    except (KeyError, TypeError, OSError, OverflowError, ValueError) as exc:
        raise ValueError('reviewed_distribution_chart_invalid') from exc


def _event_agrees(events, stamp, bar, share_ratio):
    try:
        split_key, split = next(iter(events['splits'].items()))
        cash_key, cash = next(iter(events['dividends'].items()))
        numerator, denominator = (Decimal(str(split[k])) for k in ('numerator', 'denominator'))
        stated = [Decimal(v) for v in split['splitRatio'].split(':')]
        return (set(events) == {'splits', 'dividends'} and len(events['splits']) == len(events['dividends']) == 1
                and split_key == cash_key == str(stamp) and cash['date'] == split['date'] == stamp
                and cash['amount'] == bar['dividends'] and stated == [numerator, denominator] and denominator > 0
                and numerator / denominator == Decimal(str(bar['stock_splits'])) == share_ratio)
    except (ArithmeticError, AttributeError, KeyError, StopIteration, TypeError, ValueError):
        return False


def _matches(row, official, factor=1):
    pairs = [(rights.number(row.get(k)), rights.number(official.get(k))) for k in rights.OHLC]
    return all(price is not None and reference is not None
               and isclose(price, reference * float(factor), rel_tol=1e-7, abs_tol=PRICE_TOLERANCE)
               for price, reference in pairs)


def classify(row, yahoo, marked, benchmark, *, calendar, provider):
    try:
        return _classify(row, yahoo, marked, benchmark, calendar=calendar, provider=provider)
    except (ArithmeticError, AttributeError, IndexError, KeyError, TypeError) as exc:
        raise ValueError('reviewed_distribution_invalid_input') from exc


def _classify(row, yahoo, marked, benchmark, *, calendar, provider):
    from ai_stock_sentinel.daily_radar.next_open_official_repair import _prices

    symbol = row['symbol']
    target = date.fromisoformat(row['outcome']['expected_target_date'])
    signal = date.fromisoformat(row['signal_date'])
    sessions = [d for d in calendar.sessions if signal < d <= target]
    later = [d for d in calendar.sessions if target < d <= calendar.through]
    bar = matching_event(yahoo, signal, target)
    if (not symbol.endswith('.TW') or not later or not sessions or bar is None
            or not all(callable(getattr(provider, name, None)) for name in
                       ('official_research_history', 'capture_twse_report', 'capture_split_day'))):
        raise ValueError('reviewed_distribution_not_applicable')

    window = {str(signal), *map(str, sessions)}
    for series in (yahoo, benchmark):
        if any(r.get('price_basis') != 'unadjusted_price' or r.get('corporate_actions_checked') is not True
               for r in series if r['date'] in window):
            raise ValueError('reviewed_distribution_unverified_price_basis')
    if any(rights.number(r.get('stock_splits')) not in (0, 1) or rights.number(r.get('dividends')) != 0
           for r in benchmark if r['date'] in window):
        raise ValueError('reviewed_distribution_unexplained_benchmark_action')

    action, _ = actions._twse_action(symbol, sessions, provider.capture_twse_report)
    if (action is None or action.distribution_terms is None or not action.cash_per_share
            or str(action.effective_date) != bar['date'] or action.cash_per_share == bar['dividends']):
        raise ValueError('reviewed_distribution_event_mismatch')
    def capture_terms(path, params):
        data, receipt = provider.capture_twse_report(path, params)
        # The rights parser flattens the detail receipt into its event. Only source
        # metadata can enter that merge; economic facts must come from the report.
        if not isinstance(receipt, dict) or set(RIGHTS_FIELDS).intersection(receipt):
            raise ValueError('reviewed_distribution_rights_receipt_invalid')
        return data, receipt

    issue, reports = rights._rights_issue(symbol, later, capture_terms)
    ex_day = date.fromisoformat(issue['ex_date'])
    if ex_day not in later:
        raise ValueError('reviewed_distribution_rights_session_invalid')
    previous = max(d for d in calendar.sessions if d < ex_day)
    close, price, per_thousand = (Decimal(str(issue[k])) for k in
                                  ('previous_close', 'subscription_price', 'subscription_shares_per_thousand'))
    ratio = per_thousand / 1000
    factor = (close + price * ratio) / ((1 + ratio) * close)
    cash = Decimal(action.distribution_terms.cash_per_share)
    observed = Decimal(str(bar['dividends']))
    if not 0 < factor < 1 or observed != (cash * factor).quantize(Decimal('.000001')):
        raise ValueError('reviewed_distribution_cash_mismatch')

    # Keep the exchange's monthly file on the tape for the two price witnesses.
    # Parsing it again during apply ties all eight prices to the archived report.
    reader = RecoveringResearchPriceProvider()
    reader.capture_twse_report = provider.capture_twse_report
    stock = reader.official_research_history(symbol, start_date=previous, end_date=ex_day)
    witness = _prices(stock, [previous, ex_day], symbol)
    if Decimal(str(witness[str(previous)]['close'])) != close:
        raise ValueError('reviewed_distribution_reference_close_mismatch')
    receipts = []
    for day, expected, other in ((previous, factor, 1), (ex_day, 1, factor)):
        _, events, quoted, receipt = _chart(provider, symbol, day, ex_day)
        reference = witness[str(day)]
        if events or not _matches(quoted, reference, expected) or _matches(quoted, reference, other):
            raise ValueError('reviewed_distribution_price_transition_unverified')
        receipts.append({'date': str(day), 'vendor': receipt, 'exchange': reference['official_price_report']})

    stamp, events, quoted, receipt = _chart(provider, symbol, action.effective_date, ex_day)
    shares = 1 + Decimal(action.distribution_terms.shares_per_thousand) / 1000
    if not _event_agrees(events, stamp, bar, shares) or not _matches(quoted, bar):
        raise ValueError('reviewed_distribution_chart_event_mismatch')

    stock = provider.official_research_history(symbol, start_date=signal, end_date=target)
    official = _prices(stock, [signal, *sessions], symbol)
    selected = [r for r in yahoo if r['date'] in official]
    checked = rights._daily_rows(selected, [signal, *sessions], 'yfinance')
    adjusted = []
    for day in sessions:
        if day <= action.effective_date:
            continue
        key = str(day)
        if _matches(checked[key], official[key]):
            if adjusted:
                raise ValueError('reviewed_distribution_noncontiguous_prices')
        elif _matches(checked[key], official[key], factor):
            adjusted.append(key)
        else:
            raise ValueError('reviewed_distribution_unexplained_price')

    def normalize(r):
        if r['date'] == bar['date']:
            return r | {'dividends': action.cash_per_share}
        if r['date'] in adjusted:
            if (any(r.get(k) != checked[r['date']][k] for k in rights.OHLC)
                    or set(r.get('ohlc_conflict_fields') or ()) - set(rights.OHLC)):
                raise ValueError('reviewed_distribution_conflicting_archive')
            return {k: v for k, v in r.items() if k != 'ohlc_conflict_fields'} | {
                k: r[k] / float(factor) for k in rights.OHLC}
        return r

    # Original inputs stay on the replay tape. The existing validator owns
    # distribution/benchmark coverage and the terminal skip (no return).
    result = _classify_corporate_action(row, [normalize(r) for r in yahoo], [normalize(r) for r in marked],
                                        benchmark, as_of_date=target, provider=provider)
    if result is None or result['skip_reason'] != actions.STOCK_DISTRIBUTION:
        raise ValueError('reviewed_distribution_window_unverified')
    proof = result['outcome'][actions.PROVENANCE_KEY]
    precision = proof['distribution_precision']
    if (precision['official_terms'] != {'cash_per_share': action.distribution_terms.cash_per_share,
                                       'shares_per_thousand': action.distribution_terms.shares_per_thousand}
            or proof['event']['previous_close'] != action.previous_close):
        raise ValueError('reviewed_distribution_terms_changed')
    precision['cash_in_distribution_units'] = precision['provider_cash_per_share']
    precision['provider_cash_per_share'] = bar['dividends']
    proof[KEY] = {
        'version': VERSION, 'rights_event': issue, 'rights_reports': reports, 'price_witnesses': receipts,
        'distribution_receipt': receipt, 'official_cash_per_share': str(cash),
        'provider_cash_per_share': bar['dividends'], 'factor': str(factor), 'adjusted_session_dates': adjusted,
    }
    return result
