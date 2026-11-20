"""Group a fixed readiness inventory without combining return bases or candidate identities."""

from collections import defaultdict

from ai_stock_sentinel.daily_radar.history_repair import MAX_RESULTS


def evidence_batches(gaps, *, limit, as_of_date=None):
    if not 1 <= limit <= MAX_RESULTS:
        raise ValueError('repair_batch_size_invalid')
    groups = defaultdict(list)
    for gap in gaps:
        if gap.get('reason') in {'non_trading_signal_date', 'official_calendar_unavailable'}:
            kind = 'needs_review'
        elif gap['state'] == 'missing_diagnostic' and gap['return_basis'] == 'signal_close':
            kind = 'diagnostic'
        elif gap['return_basis'] == 'next_open' and (
            gap['state'] in {'input_unverified', 'invalid_window'}
            or gap['reason'] in {'terminal_evidence_unverified', 'conflicting_price_records'}
        ):
            kind = 'next_open_repair'
        elif gap['state'] in {'missing_outcome', 'retryable'}:
            kind = 'due'
        else:
            kind = 'needs_review'
        historical = as_of_date is None or gap.get('expected_target_date') != str(as_of_date)
        groups[historical, kind, gap['return_basis'], gap['symbol']].append(gap)
    order = {'due': 0, 'diagnostic': 1, 'next_open_repair': 2, 'needs_review': 3}
    for group in sorted(groups, key=lambda k: (k[0], order[k[1]], k[2], k[3])):
        kind = group[1]
        members = groups[group]
        members.sort(key=lambda g: (g['signal_date'], g['candidate_id'], g['window_days']))
        for offset in range(0, len(members), limit):
            yield kind, members[offset : offset + limit]
