"""Recover legacy signal-close reviews that recorded an outstanding official price request."""
from calendar import monthrange
from datetime import date

from sqlalchemy import select

from ai_stock_sentinel.calibration import official_corporate_actions as actions
from ai_stock_sentinel.calibration import twse_rights_recovery as twse
from ai_stock_sentinel.calibration.research_prices import INDEX_URL, STOCK_URL, TPEX, TPEX_STOCK_PATH
from ai_stock_sentinel.db.models import RadarSourceSnapshot, RadarWorkItem
from ai_stock_sentinel.daily_radar.rights_terms import read_terms, supported_cash_issue_window
from ai_stock_sentinel.daily_radar.source_snapshots import PARSER_VERSION, SourceSnapshotStore, fingerprint
from ai_stock_sentinel.daily_radar.source_work import provider_gate_identity
from ai_stock_sentinel.daily_radar.work_queue import aware


def _recorded_cash_issue(db, result, symbol, signal, target):
    proofs = {p['request_hash']: p for p in result.get('source_observations', [])}

    def capture(provider, url, params):
        key = fingerprint({'provider': provider, 'url': url, 'params': params, 'parser_version': PARSER_VERSION})
        proof = proofs.get(key) or {}
        snapshot = db.get(RadarSourceSnapshot, proof['snapshot_id']) if proof.get('snapshot_id') else None
        if snapshot is None or snapshot.request_hash != key or snapshot.payload_hash != proof.get('snapshot_sha256'):
            raise ValueError('rights_report_not_in_review')
        return SourceSnapshotStore._read(snapshot)

    def twse_report(path, params):
        return capture('twse', twse.TWSE + path, params)

    def tpex_report(path, params):
        return capture('tpex', TPEX + path, params)

    def terms_for(event):
        day = date.fromisoformat(event['effective_date'])
        if symbol.endswith('.TW'):
            return read_terms(symbol, day, {'權/息': event['notice']}, None, twse_report)
        # The original collector requested each exchange report by window-clipped month.
        period = (max(signal, day.replace(day=1)), min(target, day.replace(day=monthrange(day.year, day.month)[1])))
        rows, receipt = actions._tpex_events(tpex_report, actions.TPEX_DISTRIBUTIONS, period,
            symbol.split('.')[0], '除權息日期', '代號', ['權/息'])
        matching = [r for d, r in rows if d == day and r['權/息'] == event['notice']]
        if len(matching) == 1:
            return read_terms(symbol, day, matching[0], receipt, None)

    try:
        return any(supported_cash_issue_window(
            row['outcome'].get('official_event_review', {}).get('events'), str(signal), str(target), terms_for,
        ) for row in result.get('after', []))
    except (ValueError, KeyError, TypeError):
        return False


def resume_parked_price_wait(db, window, now):
    scope = window.scope
    result = window.result or {}
    waits = result.get('source_dependencies') or {}
    if not waits or scope.get('rights_price_wait_replayed'):
        return
    if not all(scope.get(k) for k in ('signal_date', 'expected_target_date', 'symbol')):
        return
    signal, target = map(date.fromisoformat, (scope['signal_date'], scope['expected_target_date']))
    symbol = scope['symbol']
    first_month, last_month = signal.strftime('%Y%m'), target.strftime('%Y%m')
    captures = db.scalars(select(RadarWorkItem).where(
        RadarWorkItem.stage == 'source-capture', RadarWorkItem.work_key.in_(waits)))
    retry_times = []
    gates = set()
    for capture in captures:
        request = capture.scope['request']
        url, params = request.get('url'), request['params']
        month = params.get('date', '').replace('/', '')[:6]
        if not first_month <= month <= last_month:
            continue
        if (url == INDEX_URL or (url == STOCK_URL and symbol == f"{params.get('stockNo')}.TW")
                or (url == TPEX + TPEX_STOCK_PATH and symbol == f"{params.get('code')}.TWO")):
            # The broker checks the exact snapshot before reserving either row.
            # Its expiry and the capture's fetch deadline can differ.
            cached = SourceSnapshotStore.current_snapshot(db, request, now)
            if cached is not None:
                retry_times.append(now)
                continue
            deadline = capture.lease_expires_at if capture.state == "running" else capture.retry_at
            retry_times.append(aware(deadline) if deadline else now)
            # Older captures can predate the persisted gate dependency.
            gates.add(fingerprint(provider_gate_identity(request)))
    if not retry_times or not _recorded_cash_issue(db, result, symbol, signal, target):
        return
    # A cache miss is still subject to a live provider-wide cooldown.
    if gates:
        for gate in db.scalars(select(RadarWorkItem).where(RadarWorkItem.work_key.in_(gates),
                RadarWorkItem.stage == 'provider-gate', RadarWorkItem.state.in_(('running', 'waiting_provider')))):
            deadline = gate.lease_expires_at if gate.state == 'running' else gate.retry_at
            if deadline is not None:
                retry_times.append(aware(deadline))
    locked = db.scalar(select(RadarWorkItem).where(
        RadarWorkItem.id == window.id, RadarWorkItem.state == 'needs_review', RadarWorkItem.reason == window.reason,
        RadarWorkItem.attempt_count == window.attempt_count,
        RadarWorkItem.scope['rights_price_wait_replayed'].as_boolean().is_(None),
    ).with_for_update(skip_locked=True).execution_options(populate_existing=True))
    if locked is not None:
        locked.scope = locked.scope | {'rights_price_wait_replayed': True}
        locked.state, locked.reason = 'waiting_provider', 'validation_price_source_unavailable'
        locked.retry_at = max(now, *retry_times)
