"""Read and verify evidence that can retire an older durable validation window."""
from datetime import date

from sqlalchemy import and_, case, func, or_, select

from ai_stock_sentinel.calibration.forward_validation import evaluate_forward_window, number, parse_date
from ai_stock_sentinel.daily_radar.forward_validation import (
    DAILY_RADAR_FORWARD_ADAPTER, FORWARD_VALIDATION_VERSION, _summary_outcome, candidate_forward_validation_benchmark_symbol,
)
from ai_stock_sentinel.daily_radar.price_acceptance import PRICE_EVIDENCE, verify_price_lock
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION, evaluate_research_window
from ai_stock_sentinel.daily_radar.validation_inputs import PROOF_KEY, validation_inputs_match
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result


RETURN_FIELDS = ('entry_price', 'target_price', 'forward_return_pct', 'benchmark_return_pct',
                 'excess_return_vs_benchmark_pct', 'max_adverse_excursion_pct', 'max_favorable_excursion_pct')


def _check_archived_returns(candidate, row, proof, basis):
    payload = row['outcome']
    reviewed = payload.get('reviewed_official_window') or {}
    stock = proof.get('stock', reviewed.get('calculation_stock'))
    benchmark = proof.get('benchmark', reviewed.get('calculation_benchmark'))
    if not isinstance(stock, list) or not isinstance(benchmark, list):
        raise ValueError('missing_locked_calculation_prices')
    sessions = [date.fromisoformat(bar['date']) for bar in benchmark]
    if reviewed:
        days = reviewed.get('session_dates')
        if (reviewed.get('symbol') != candidate['symbol'] or reviewed.get('signal_date') != row['signal_date']
                or sorted(str(day) for day in sessions) != [row['signal_date'], *days]
                or sorted(bar['date'] for bar in stock) != days):
            raise ValueError('reviewed_calculation_window_mismatch')
    args = dict(price_series=stock, benchmark_prices=benchmark, window_days=row['window_days'],
        as_of_date=date.fromisoformat(row['evaluation_as_of_date']), benchmark_symbol=row['benchmark_symbol'],
        trading_calendar=sessions)
    if basis == 'next_open':
        computed = evaluate_research_window(candidate, **args, validate_signal_session=True)
    else:
        computed = evaluate_forward_window(candidate, **args, adapter=DAILY_RADAR_FORWARD_ADAPTER,
            validation_version=row['validation_version'], hit_threshold_pct=0)
        from ai_stock_sentinel.daily_radar.observation_validation import evaluate_observation
        from ai_stock_sentinel.daily_radar.readiness import _needs_diagnostic
        if _needs_diagnostic(candidate):
            diagnostic = evaluate_observation(candidate, price_series=stock, benchmark_prices=benchmark,
                window_days=row['window_days'], as_of_date=args['as_of_date'])
            saved = payload.get('observation_diagnostic') or {}
            if any(saved.get(key) != diagnostic[key] for key in ('status', 'confirmation_date', 'invalidation_date',
                    'lead_trading_days', 'waiting_end_date', 'waiting_max_adverse_excursion_pct')):
                raise ValueError('locked_observation_diagnostic_mismatch')
    if (computed['status'] != 'validated' or computed['target_date'] != row['target_date']
            or any(payload.get(key) != computed['outcome'].get(key) for key in RETURN_FIELDS)):
        raise ValueError('locked_return_metrics_mismatch')


def later_results(db, candidates, *, versions, windows, cutoff, lock=False):
    by_id = {candidate['candidate_id']: candidate for candidate in candidates}
    if not by_id:
        return {}
    # Verification of recorded price locks needs their original bars and reports.
    # Keep the compact projection for the other rows and discard full proofs after verification.
    dialect = db.get_bind().dialect.name
    has_reviewed = (Result.outcome.op('?')('reviewed_official_window') if dialect == 'postgresql' else
                    func.json_type(Result.outcome, '$.reviewed_official_window').is_not(None))
    archived = or_(Result.outcome[PRICE_EVIDENCE]['sha256'].as_string().is_not(None),
                   and_(Result.status == 'validated', Result.validation_version == RESEARCH_VALIDATION_VERSION, has_reviewed))
    payload = case((archived, Result.outcome), else_=_summary_outcome(Result.outcome, dialect))
    conditions = (Result.candidate_id.in_(by_id), Result.validation_version.in_(versions),
                  Result.window_days.in_(windows), Result.evaluation_as_of_date > cutoff)
    query = select(Result.candidate_id, Result.window_days, Result.validation_version, Result.signal_date,
        Result.evaluation_as_of_date, Result.target_date, Result.benchmark_symbol, Result.status, Result.skip_reason,
        payload.label('outcome'), Result.outcome['reviewed_official_window']['session_dates'].label('window_session_dates')
    ).where(*conditions).order_by(Result.candidate_id, Result.window_days, Result.validation_version, Result.id)
    if lock:
        query = query.with_for_update(read=True, skip_locked=True)
    rows = {}
    for record in db.execute(query.execution_options(yield_per=32)).mappings():
        row = dict(record)
        for field in ('signal_date', 'evaluation_as_of_date', 'target_date'):
            row[field] = str(row[field]) if row[field] is not None else None
        payload = row['outcome'] if isinstance(row['outcome'], dict) else {}
        proof = payload.get(PRICE_EVIDENCE)
        row['price_lock_symbol'] = None
        basis = 'next_open' if row['validation_version'] == RESEARCH_VALIDATION_VERSION else 'signal_close'
        try:
            if PRICE_EVIDENCE in payload or any(k in payload for k in ('vendor_capture_sha256', 'reviewed_official_window_sha256')):
                if not isinstance(proof, dict) or not proof.get('symbol'):
                    raise ValueError('missing_validation_price_lock')
                verify_price_lock(row, symbol=proof['symbol'], basis=basis)
                row['price_lock_symbol'] = proof['symbol']
                if isinstance(proof.get('stock'), list):
                    row['price_lock_days'] = [bar['date'] for bar in proof['stock']]
            if row['status'] == 'validated' and (proof or 'reviewed_official_window' in payload):
                _check_archived_returns(by_id[row['candidate_id']], row, proof or {}, basis)
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, IndexError):
            payload = {}  # Keep a visible later row, but no evidence to authorize completion.
        row['outcome'] = {k: v for k, v in payload.items() if k not in (
            PRICE_EVIDENCE, 'reviewed_official_window', 'historical_repair')}
        # Some terminal policies inspect historical repair metadata (including suspension corrections).
        if isinstance(payload.get('historical_repair'), dict):
            row['outcome']['historical_repair'] = {k: v for k, v in payload['historical_repair'].items()
                                                  if k != 'prior_evidence'}
        rows[row['candidate_id'], row['window_days'], row['validation_version']] = row
    if lock:
        # Another writer can hold a row or install a later result during the scan.
        # Treat it as unverified this pass until its committed proof can be locked.
        for cid, days, version, asof in db.execute(select(Result.candidate_id, Result.window_days,
                Result.validation_version, Result.evaluation_as_of_date).where(*conditions)):
            rows.setdefault((cid, days, version), {'evaluation_as_of_date': str(asof), 'unavailable': True})
    return rows


def later_window_evidence(candidate, row, *, basis, window, calendar, target, later_calendar):
    """Certify the same mature window for queue settlement only."""
    from ai_stock_sentinel.daily_radar.readiness import _needs_diagnostic, classify_window
    if row.get('unavailable'):
        return None
    cutoff = parse_date(row['evaluation_as_of_date'])
    signal = date.fromisoformat(candidate['record_date'])
    version = RESEARCH_VALIDATION_VERSION if basis == 'next_open' else FORWARD_VALIDATION_VERSION
    payload = row['outcome']
    recorded_target = row['target_date']
    if recorded_target is None and row['status'] == 'skipped':
        recorded_target = payload.get('expected_target_date')
    if (not target or not later_calendar or later_calendar.through < cutoff
            or row['signal_date'] != str(signal) or recorded_target != str(target)
            or row['price_lock_symbol'] not in (None, candidate['symbol'])
            or row['candidate_id'] != candidate['candidate_id'] or row['window_days'] != window
            or row['validation_version'] != version or payload.get('return_basis', basis) != basis
            or any(payload.get(key, str(target)) != str(target) for key in ('target_date', 'expected_target_date'))
            or row['benchmark_symbol'] != candidate_forward_validation_benchmark_symbol(candidate.get('input_snapshot'))
            or row['status'] not in {'validated', 'skipped'}
            or not payload.get(PROOF_KEY) or not validation_inputs_match(candidate, row)):
        return None
    old_days = [d for d in calendar.sessions if signal <= d <= target]
    new_days = [d for d in later_calendar.sessions if signal <= d <= target]
    if old_days != new_days:
        return None
    dates = [str(day) for day in new_days]
    if row.get('price_lock_days', dates) != dates or row.get('window_session_dates') not in (None, dates[1:]):
        return None
    try:
        state, reason, later_target = classify_window(candidate, row, basis=basis, window=window,
                                                     as_of_date=cutoff, calendar=later_calendar)
    except (AttributeError, KeyError, TypeError, ValueError):
        # A malformed stored diagnostic or event is insufficient evidence for this window.
        return None
    if state not in {'validated', 'terminal'} or later_target != target:
        return None
    if state == 'validated':
        if basis == 'signal_close' and _needs_diagnostic(candidate):
            from ai_stock_sentinel.daily_radar.observation_validation import complete_window_diagnostic
            if not complete_window_diagnostic(candidate, payload['observation_diagnostic'], new_days[1:]):
                return None
        if (any(number(payload.get(key)) is None for key in RETURN_FIELDS)
                or any((number(payload.get(key)) or 0) <= 0 for key in ('entry_price', 'target_price'))):
            return None
    return {'evaluation_as_of_date': str(cutoff), 'state': state, 'reason': reason}
