"""Explicit next-open repair: collect privately, replay offline, apply a reviewed hash.

Ordinary validation retains ownership/provenance protections. This separate path
recomputes the whole window using the same evaluator and archives prior evidence.
It never commits a caller's transaction or fetches during plan/apply/rollback.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import math
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.collection_transactions import read_only_collection
from ai_stock_sentinel.daily_radar.constants import DAILY_RADAR_VALIDATION_WINDOWS
from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
from ai_stock_sentinel.daily_radar.history_repair import (
    MAX_RESULTS, MUTABLE_FIELDS, _apply_result_manifest, candidate_snapshot, fingerprint, result_snapshot,
)
from ai_stock_sentinel.daily_radar.readiness import classify_window
from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION, run_research_validation
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.daily_radar.trading_calendar import CALENDAR_STEP, TradingCalendar, load_calendar, months
from ai_stock_sentinel.daily_radar.validation_inputs import validation_candidate_revision
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun, DailyRadarPreparedRun
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result

LEGACY_VERSION = 'daily-radar-next-open-repair-v1'
VERSION = 'daily-radar-next-open-repair-v2'
CAPTURE_VERSION = 'daily-radar-next-open-capture-v2'
METHODS = ('fetch', 'repair', 'official_research_history', 'capture_twse_report', 'capture_tpex_report')
MAX_BYTES = 64 * 1024 * 1024


def _json(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _calendar_context(session, calendar):
    calendar = TradingCalendar.parse(calendar.payload())
    prepared = session.scalar(select(DailyRadarPreparedRun).where(
        DailyRadarPreparedRun.market == 'TW', DailyRadarPreparedRun.run_date == calendar.through)
        .execution_options(populate_existing=True))
    current = load_calendar(session, start_date=calendar.start, as_of_date=calendar.through)
    if (prepared is None or (prepared.step_statuses or {}).get(CALENDAR_STEP) != calendar.payload()
            or current is None or current.payload() != calendar.payload()):
        raise ValueError('next_open_calendar_phase_required')
    return {'id': prepared.id, 'run_date': str(prepared.run_date),
            'before': {CALENDAR_STEP: calendar.payload()}, 'after': {CALENDAR_STEP: calendar.payload()}}


def _manual(candidate, row, calendar):
    state, reason, target = classify_window(candidate, row, basis='next_open', window=row['window_days'],
                                          as_of_date=calendar.through, calendar=calendar)
    owned = any(k in row['outcome'] for k in (
        'historical_repair', 'official_rights_recovery', 'reviewed_rights_recovery', 'official_corporate_action'))
    eligible = target is not None and (state in {'input_unverified', 'invalid_window'}
                                      or owned and state not in {'validated', 'terminal', 'immature'})
    return eligible, reason


def inventory(session, *, calendar):
    _calendar_context(session, calendar)
    candidates = {c['candidate_id']: c for c in forward_validation_candidates_from_runs(session,
        market='TW', start_date=calendar.start, end_date=calendar.through, comparable_pool_only=True)}
    items = []
    rows = session.scalars(select(Result).where(Result.candidate_id.in_(candidates),
        Result.validation_version == RESEARCH_VALIDATION_VERSION, Result.benchmark_symbol == 'TAIEX',
        Result.window_days.in_(DAILY_RADAR_VALIDATION_WINDOWS)).order_by(Result.id))
    for row in rows:
        if row.signal_date != date.fromisoformat(candidates[row.candidate_id]['record_date']):
            continue
        if row.evaluation_as_of_date and row.evaluation_as_of_date > calendar.through:
            continue
        eligible, reason = _manual(candidates[row.candidate_id], result_snapshot(row), calendar)
        if eligible:
            items.append({'id': row.id, 'operation': 'recompute', 'reason': reason})
    return {'version': VERSION, 'return_basis': 'next_open', 'as_of_date': str(calendar.through),
            'calendar_sha256': fingerprint(calendar.payload()), 'results': items, 'count': len(items)}


def _context(session, request, calendar):
    prepared = _calendar_context(session, calendar)
    items = request.get('results', [])
    ids = [r.get('id') for r in items]
    if (request.get('version') != VERSION or request.get('return_basis') != 'next_open'
            or request.get('as_of_date') != str(calendar.through)
            or request.get('calendar_sha256') != fingerprint(calendar.payload())
            or not 0 < len(ids) <= MAX_RESULTS or any(type(i) is not int for i in ids)
            or len(set(ids)) != len(ids) or any(r.get('operation') != 'recompute' for r in items)):
        raise ValueError('next_open_repair_allowlist_invalid')
    rows = {r.id: r for r in session.scalars(select(Result).where(Result.id.in_(ids))
                                           .execution_options(populate_existing=True))}
    candidates = {c['candidate_id']: c for c in forward_validation_candidates_from_runs(session,
        market='TW', start_date=calendar.start, end_date=calendar.through,
        candidate_ids=[r.candidate_id for r in rows.values()], comparable_pool_only=True)}
    entries = []
    for rid in ids:
        row = rows.get(rid)
        candidate = candidates.get(row.candidate_id) if row else None
        if (candidate is None or row.validation_version != RESEARCH_VALIDATION_VERSION
                or row.benchmark_symbol != 'TAIEX' or row.window_days not in DAILY_RADAR_VALIDATION_WINDOWS
                or str(row.signal_date) != candidate['record_date'] or row.signal_date not in calendar.sessions
                or row.evaluation_as_of_date and row.evaluation_as_of_date > calendar.through):
            raise ValueError('next_open_repair_scope_invalid')
        before = result_snapshot(row)
        if not _manual(candidate, before, calendar)[0]:
            raise ValueError('next_open_repair_requires_manual_gap')
        c = session.get(DailyRadarCandidate, row.candidate_id)
        entries.append({'id': rid, 'operation': 'recompute', 'before': before,
            'candidate_sha256': fingerprint(candidate_snapshot(c, session.get(DailyRadarRun, c.run_id))),
            'validation_candidate_revision': validation_candidate_revision(candidate),
            'observation_origin': candidate.get('observation_origin')})
    return {'prepared': prepared, 'results': entries}


class _Tape:
    """Sequence replay preserves repeated calls and the provider's capabilities."""
    def __init__(self, provider=None, *, evidence=None):
        self.provider = provider
        self.capabilities = (list(evidence['capabilities']) if evidence is not None else
                             [m for m in METHODS if callable(getattr(provider, m, None))])
        if 'fetch' not in self.capabilities or any(m not in METHODS for m in self.capabilities):
            raise ValueError('next_open_capture_capabilities_invalid')
        self.calls = [] if evidence is None else deepcopy(evidence['calls'])
        self.offset = 0
        self.diagnostics = []
        self.fetched, self.repaired = {}, {}

    def _remember(self, name, value):
        if name in ('fetch', 'repair') and isinstance(value, dict):
            (self.fetched if name == 'fetch' else self.repaired).update(deepcopy(value))
        return value

    def position(self):
        return len(self.calls) if self.provider is not None else self.offset

    def __getattr__(self, name):
        if name not in self.capabilities:
            raise AttributeError(name)
        def invoke(*args, **kwargs):
            signature = _json({'method': name, 'args': args, 'kwargs': kwargs})
            if self.provider is None:
                if self.offset >= len(self.calls) or self.calls[self.offset]['request'] != signature:
                    raise ValueError('next_open_capture_replay_mismatch')
                call = self.calls[self.offset]; self.offset += 1
                if call.get('failed'):
                    raise RuntimeError('next_open_captured_provider_failure')
                return self._remember(name, deepcopy(call['value']))
            if len(self.calls) >= 4096:
                raise ValueError('next_open_capture_too_many_calls')
            try:
                value = getattr(self.provider, name)(*args, **kwargs)
            except Exception:
                self.calls.append({'request': signature, 'failed': True})
                raise RuntimeError('next_open_captured_provider_failure') from None
            self.calls.append({'request': signature, 'value': _json(value)})
            return self._remember(name, value)
        return invoke


def _evaluate(reader, context, calendar, tape):
    start = min(date.fromisoformat(r['before']['signal_date']) for r in context['results'])
    wanted = {m.strftime('%Y-%m') for m in months(start, calendar.through)}
    # Retain a following publication witness, if present, for closed month ends.
    reports = tuple(r for r in calendar.reports if r['month'] >= min(wanted))
    scoped = TradingCalendar.parse(TradingCalendar(start, calendar.through,
        tuple(d for d in calendar.sessions if d >= start), reports).payload())
    req = DailyRadarForwardValidationRunRequest(mode='range', return_basis='next_open', market='TW',
        start_date=start, end_date=calendar.through, as_of_date=calendar.through, benchmark_symbol='TAIEX',
        windows=sorted({r['before']['window_days'] for r in context['results']}))
    bundle = run_research_validation(reader, req, as_of_date=calendar.through, price_provider=tape,
        calendar_provider=SimpleNamespace(fetch=lambda **kwargs: scoped), prepare_only=True,
        candidate_ids={r['before']['candidate_id'] for r in context['results']})
    from ai_stock_sentinel.daily_radar.next_open_official_repair import collect_window
    candidates = {c['candidate_id']: c for c in bundle.candidates}
    outcomes = {(r['candidate_id'], r['window_days']): r for r in bundle.outcomes}
    reader.rollback()  # Core can return signal-level skips without entering its I/O phase.
    verified = []
    for entry in context['results']:
        before = entry['before']; row = outcomes.get((before['candidate_id'], before['window_days']))
        if row is None:
            continue
        try:
            row = collect_window(candidates[before['candidate_id']], before, row, calendar=calendar, provider=tape)
        except (ValueError, RuntimeError, OSError) as exc:
            codes = {'next_open_official_provider_unavailable', 'next_open_official_rights_unverified',
                'next_open_official_action_unverified', 'next_open_official_source_invalid',
                'next_open_official_split_unverified',
                'next_open_official_source_identity_invalid', 'twse_rights_incomplete_price_window',
                'twse_rights_invalid_price_row', 'next_open_captured_provider_failure'}
            code = str(exc) if str(exc) in codes else 'next_open_official_evidence_unavailable'
            row = dict(row) | {'status': 'skipped', 'skip_reason': 'next_open_official_evidence_incomplete',
                'outcome': dict(row['outcome']) | {'official_repair_error': {'code': code}}}
        verified.append(row)
    bundle.outcomes = verified
    return bundle


def capture_evidence(bind, request, *, calendar, provider=None):
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    if provider is None:
        provider = RecoveringResearchPriceProvider(session=None)
    if getattr(provider, 'session', None) is not None or getattr(provider, 'source_store', None) is not None:
        raise ValueError('next_open_capture_requires_detached_provider')
    tape = _Tape(provider)
    # Private reader: rollback during collection cannot discard caller writes,
    # including writes already flushed and absent from Session.dirty/new.
    with Session(bind, autoflush=False) as reader, read_only_collection(reader):
        context = _context(reader, request, calendar)
        archive = {}
        previous_archive = None
        if isinstance(provider, RecoveringResearchPriceProvider):
            from ai_stock_sentinel.daily_radar.market_bar_repository import get_taiwan_daily_bars
            candidates = [reader.get(DailyRadarCandidate, r['before']['candidate_id']) for r in context['results']]
            start = min(date.fromisoformat(r['before']['signal_date']) for r in context['results'])
            for bar in get_taiwan_daily_bars(reader, symbols=sorted({c.symbol for c in candidates}),
                    start_date=start, end_date=calendar.through, final_only=True):
                archive.setdefault(bar.symbol, {})[str(bar.trade_date)] = {'date': str(bar.trade_date),
                    **{k: float(getattr(bar, k)) for k in ('open', 'high', 'low', 'close')},
                    'price_source': bar.source_dataset}
            previous_archive, provider.archived_prices = provider.archived_prices, archive
        try:
            bundle = _evaluate(reader, context, calendar, tape)
        finally:
            if isinstance(provider, RecoveringResearchPriceProvider):
                provider.archived_prices = previous_archive
        if _context(reader, request, calendar) != context:
            raise ValueError('next_open_capture_inputs_changed')
    artifact = {'version': CAPTURE_VERSION, 'validation_version': RESEARCH_VALIDATION_VERSION,
        'captured_at': datetime.now(timezone.utc).isoformat(), 'context': context,
        'calendar_sha256': fingerprint(calendar.payload()), 'capabilities': tape.capabilities,
        'calls': tape.calls, 'archive_prices': archive, 'preview': _json(bundle.outcomes)}
    artifact['sha256'] = fingerprint(artifact)
    return artifact


def _verify_after(candidate, after, calendar, *, prior=None, require_official=True):
    state, _, _ = classify_window(candidate, after, basis='next_open', window=after['window_days'],
                                  as_of_date=calendar.through, calendar=calendar)
    if state not in {'validated', 'terminal'} or after['status'] not in {'validated', 'skipped'}:
        raise ValueError('next_open_repair_incomplete')
    if require_official and after['status'] == 'validated' and after.get('skip_reason') is not None:
        raise ValueError('next_open_repair_incomplete')
    if after['evaluation_as_of_date'] != str(calendar.through):
        raise ValueError('next_open_repair_cutoff_invalid')
    if require_official:
        from ai_stock_sentinel.daily_radar.next_open_official_repair import verify_evidence
        verify_evidence(candidate, after, calendar=calendar, prior=prior or {})
    if state == 'validated':
        outcome = after['outcome']
        days = [d for d in calendar.sessions if d > date.fromisoformat(candidate['record_date'])]
        fields = ('entry_price', 'target_price', 'forward_return_pct', 'benchmark_return_pct',
                  'excess_return_vs_benchmark_pct', 'max_adverse_excursion_pct', 'max_favorable_excursion_pct')
        if (outcome.get('entry_date') != str(days[0]) or outcome.get('target_date') != after['target_date']
                or any(type(outcome.get(k)) not in (int, float) or not math.isfinite(outcome[k]) for k in fields)
                or outcome['entry_price'] <= 0 or outcome['target_price'] <= 0):
            raise ValueError('next_open_repair_metrics_invalid')


def build_repair_plan(bind, request, *, calendar, evidence):
    if (evidence.get('version') != CAPTURE_VERSION
            or evidence.get('validation_version') != RESEARCH_VALIDATION_VERSION
            or evidence.get('calendar_sha256') != fingerprint(calendar.payload())
            or evidence.get('sha256') != fingerprint({k:v for k,v in evidence.items() if k != 'sha256'})):
        raise ValueError('next_open_capture_hash')
    tape = _Tape(evidence=evidence)
    with Session(bind, autoflush=False) as reader, read_only_collection(reader):
        context = _context(reader, request, calendar)
        if context != evidence['context']:
            raise ValueError('next_open_capture_inputs_changed')
        bundle = _evaluate(reader, context, calendar, tape)
        if tape.offset != len(tape.calls) or _context(reader, request, calendar) != context:
            raise ValueError('next_open_capture_inputs_changed')
    outcomes = {(r['candidate_id'], r['window_days']): _json(r) for r in bundle.outcomes}
    candidates = {c['candidate_id']: c for c in bundle.candidates}
    repair_id = fingerprint({'context': context, 'capture_sha256': evidence['sha256']})
    plan = {'version': VERSION, 'repair_id': repair_id, 'calendar': calendar.payload(),
            'capture_sha256': evidence['sha256'], 'results': [], 'runs': [], 'prepared': context['prepared']}
    for entry in context['results']:
        before = entry['before']; row = outcomes.get((before['candidate_id'], before['window_days']))
        if row is None:
            raise ValueError('next_open_repair_incomplete')
        after = before | {k: row[k] for k in MUTABLE_FIELDS}
        _verify_after(candidates[before['candidate_id']], after, calendar, prior=before['outcome'])
        after['outcome'] = after['outcome'] | {'historical_repair': {
            'version': VERSION, 'repair_id': repair_id, 'operation': 'recompute',
            'calendar_sha256': fingerprint(calendar.payload()), 'capture_sha256': evidence['sha256'],
            'before_sha256': fingerprint(before), 'prior_evidence': deepcopy(before['outcome'])}}
        plan['results'].append(entry | {'after': after})
    plan['sha256'] = fingerprint(plan)
    return plan


def apply_repair_plan(session, plan, *, expected_sha256, rollback=False):
    legacy = plan.get('version') == LEGACY_VERSION
    if legacy and not rollback:
        raise ValueError('next_open_legacy_manifest_apply_disabled')
    calendar = TradingCalendar.parse(plan['calendar'])
    if (not plan.get('results') or plan.get('runs') != []
            or plan['prepared']['before'] != {CALENDAR_STEP: calendar.payload()}
            or plan['prepared']['after'] != plan['prepared']['before']):
        raise ValueError('next_open_repair_manifest_scope_invalid')
    priors = {(entry['before']['candidate_id'], entry['before']['window_days']): entry['before']['outcome']
              for entry in plan['results']}
    return _apply_result_manifest(session, plan, expected_sha256=expected_sha256, rollback=rollback,
        manifest_version=LEGACY_VERSION if legacy else VERSION, result_version=RESEARCH_VALIDATION_VERSION,
        verify_after=lambda candidate, after: _verify_after(candidate, after, calendar,
            prior=priors[(after['candidate_id'], after['window_days'])], require_official=not legacy),
        verify_context=lambda: _calendar_context(session, calendar))
