"""Collect provider evidence first; return a pure transactional acceptance step."""
from copy import deepcopy
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from types import SimpleNamespace
from sqlalchemy import select

from ai_stock_sentinel.daily_radar import dependencies as providers
from ai_stock_sentinel.daily_radar.schemas import DailyRadarRefreshStepRequest, DailyRadarPreparedRunRequest, DailyRadarForwardValidationRunRequest, DailyRadarManagedRawDataRefreshRequest
from ai_stock_sentinel.daily_radar.repository import get_daily_radar_prepared_run, get_final_raw_data_rows_for_symbols, update_daily_radar_prepared_step_status
from ai_stock_sentinel.daily_radar.source_snapshots import SourceSnapshotStore, fingerprint
from ai_stock_sentinel.daily_radar.durable_providers import CachedForwardPrices, CapturedOfficialGetter
from ai_stock_sentinel.daily_radar.work_queue import LostLease


@dataclass
class Collected:
    apply: object


def prepared(db, day):
    row = get_daily_radar_prepared_run(db, run_date=day, market='TW')
    if row is None:
        raise ValueError('radar_prepared_run_missing')
    return row


def static_context(payloads):
    return SimpleNamespace(fetch=lambda **kwargs: iter(payloads))


class WorkHandlers:
    def __init__(self, db):
        self.db = db
        self.store = SourceSnapshotStore(db.get_bind())
        self.getter = CapturedOfficialGetter(self.store)

    def collect(self, claim):
        method = getattr(self, 'collect_' + claim.stage.replace('-', '_'), None)
        if method is None:
            raise ValueError('radar_work_stage_invalid')
        self.store.seen.clear()
        self.store.dependencies.clear()
        self.store.captured_count = 0
        self.getter.reset_budget()
        return method(claim)

    def collect_market_session(self, claim):
        from fastapi import HTTPException
        from ai_stock_sentinel.daily_radar.market_session import TwseMarketSessionProvider, require_prepared_open_session
        from ai_stock_sentinel.daily_radar.jobs import prepared_generation
        def revision(row):
            return (row.id, prepared_generation(row), fingerprint((row.step_statuses or {}).get('market-session'))) if row else None
        expected = revision(get_daily_radar_prepared_run(self.db, run_date=claim.run_date, market='TW'))
        self.db.rollback()
        result = TwseMarketSessionProvider(request_get=self.getter).resolve(run_date=claim.run_date, market='TW')
        if result.run_date != claim.run_date or result.market != 'TW' or result.status not in {'open', 'closed'}:
            raise ValueError('market_session_identity_invalid')
        proof = {'status': result.status, 'run_date': str(result.run_date), 'market': result.market,
                 'provider': result.provider, 'dataset': result.dataset}
        def apply(db):
            current = get_daily_radar_prepared_run(db, run_date=claim.run_date, market='TW')
            if current is not None and result.status == 'open':
                db.refresh(current, with_for_update=True)
                if revision(current) == expected:
                    current.step_statuses = dict(current.step_statuses or {}) | {'market-session': proof}
                    require_prepared_open_session(current)
                else:
                    # Preserve a concurrent, valid preparation. An invalid new
                    # revision must be recollected, not overwritten by this claim.
                    try:
                        require_prepared_open_session(current)
                    except HTTPException as exc:
                        raise ValueError('market_session_prepared_revision_changed') from exc
            return {'state': 'ready' if result.status == 'open' else 'terminal', 'result': proof}
        return Collected(apply)

    def collect_institutional(self, claim):
        from ai_stock_sentinel.daily_radar.institutional_flow_repository import get_completed_institutional_snapshot, archive_institutional_report, institutional_snapshot_is_complete
        from ai_stock_sentinel.daily_radar.institutional_flow_provider import OfficialTaiwanInstitutionalReportProvider
        from ai_stock_sentinel.daily_radar.institutional_flow_service import _validate_report_identity
        market = claim.scope['market']
        current = get_completed_institutional_snapshot(self.db, market=market, trade_date=claim.run_date)
        if institutional_snapshot_is_complete(current):
            return Collected(lambda db: {'state': 'ready', 'result': {'reused': True}})
        self.db.rollback()
        report = OfficialTaiwanInstitutionalReportProvider(request_get=self.getter, max_attempts=1).fetch_market(market=market, trade_date=claim.run_date)
        _validate_report_identity(report, market=market, trade_date=claim.run_date)
        def apply(db):
            row = archive_institutional_report(db, report)
            return {'state': 'ready', 'result': {'snapshot_id': row.id, 'payload_hash': row.payload_hash, 'row_count': row.row_count}}
        return Collected(apply)

    def collect_market_bars(self, claim):
        from ai_stock_sentinel.daily_radar.market_bar_repository import upsert_taiwan_daily_bars
        from ai_stock_sentinel.daily_radar.market_bar_provider import OfficialTaiwanMarketBarProvider
        market = claim.scope['market']
        trade_date = date.fromisoformat(claim.scope.get('trade_date', str(claim.run_date)))
        self.db.rollback()
        bars = OfficialTaiwanMarketBarProvider(request_get=self.getter, max_attempts=1).fetch_market(market=market, trade_date=trade_date)
        if not bars or any(b.market != market or b.trade_date != trade_date or not b.is_final for b in bars):
            raise ValueError('market_bar_identity_or_coverage_invalid')
        def apply(db):
            count = upsert_taiwan_daily_bars(db, bars)
            return {'state': 'ready', 'result': {'records_written': count}}
        return Collected(apply)

    def collect_prepare(self, claim):
        from ai_stock_sentinel.daily_radar.refresh_router import persist_prepared_universe
        from ai_stock_sentinel.daily_radar.market_session import require_prepared_open_session
        from ai_stock_sentinel.db.models import RadarWorkItem
        from ai_stock_sentinel.daily_radar.market_exploration import load_market_exploration, MarketExplorationReadinessError
        from ai_stock_sentinel.daily_radar.pipeline_support import _required_institutional_archive_details
        row = get_daily_radar_prepared_run(self.db, run_date=claim.run_date, market='TW')
        if row and row.selected_symbols and (row.step_statuses or {}).get('prepare-universe', {}).get('status') == 'completed':
            require_prepared_open_session(row)
            def reuse(db):
                current = prepared(db, claim.run_date)
                db.refresh(current, with_for_update=True)
                require_prepared_open_session(current)
                archive = _required_institutional_archive_details(db, run_date=claim.run_date)
                update_daily_radar_prepared_step_status(db, current, step='refresh-institutional-flows', status='completed', details=archive)
                return {'state': 'ready', 'result': {'prepared_run_id': current.id, 'reused': True}}
            return Collected(reuse)
        try:
            load_market_exploration(self.db, run_date=claim.run_date)
        except MarketExplorationReadinessError as exc:
            gaps = deepcopy(exc.missing_market_dates)
            return Collected(lambda db: {'state': 'waiting_dependency', 'reason': 'market_exploration_archive_incomplete',
                'result': {'archive_gaps': gaps}})
        session = self.db.scalar(select(RadarWorkItem).where(RadarWorkItem.run_date == claim.run_date,
            RadarWorkItem.stage == 'market-session', RadarWorkItem.state == 'ready').order_by(RadarWorkItem.id).limit(1))
        proof = deepcopy(session.result) if session else None
        if not proof or proof.get('status') != 'open':
            raise ValueError('market_session_evidence_missing_or_invalid')
        def apply(db):
            result = persist_prepared_universe(db, request=DailyRadarPreparedRunRequest(run_date=claim.run_date),
                run_date=claim.run_date, universe_provider=providers.get_daily_radar_universe_provider(db), session_proof=proof, commit=False)
            return {'state': 'ready', 'result': {'symbol_count': result.symbol_count}}
        return Collected(apply)

    def _collect_context(self, claim, context_type):
        from ai_stock_sentinel.daily_radar.pipeline_support import _refresh_daily_radar_context_step
        from ai_stock_sentinel.daily_radar.background_context import same_day_background_context_is_reusable
        from ai_stock_sentinel.db.models import SharedBackgroundContext
        symbol = claim.scope['symbol']
        current = self.db.scalar(select(SharedBackgroundContext).where(SharedBackgroundContext.symbol == symbol,
            SharedBackgroundContext.context_type == context_type).order_by(SharedBackgroundContext.as_of_date.desc()).limit(1))
        payloads = []
        if not current or not same_day_background_context_is_reusable(current, run_date=claim.run_date):
            self.db.rollback()
            from ai_stock_sentinel.daily_radar.default_background_context import DefaultBackgroundChipContextProvider
            from ai_stock_sentinel.daily_radar.official_background_context import OfficialBackgroundChipContextProvider
            from ai_stock_sentinel.daily_radar.background_context import BackgroundContextPayload
            provider = DefaultBackgroundChipContextProvider(official_provider=OfficialBackgroundChipContextProvider(request_get=self.getter))
            def fetch():
                rows = []
                for item in provider.fetch(symbols=[symbol], context_types=[context_type], run_date=claim.run_date, market='TW'):
                    value = asdict(item)
                    value['as_of_date'] = str(item.as_of_date) if item.as_of_date else None
                    rows.append(value)
                return {'contexts': rows}
            captured, _ = self.store.capture_operation(operation='background-chip-context',
                params={'symbol': symbol, 'context_type': context_type, 'run_date': str(claim.run_date)}, fetch=fetch)
            payloads = [BackgroundContextPayload(**(item | {'as_of_date': date.fromisoformat(item['as_of_date']) if item['as_of_date'] else None}))
                        for item in captured['contexts']]
            if any(p.symbol != symbol or p.context_type != context_type for p in payloads):
                raise ValueError('background_context_identity_invalid')
        def apply(db):
            result = _refresh_daily_radar_context_step(db, payload=DailyRadarRefreshStepRequest(run_date=claim.run_date),
                provider=static_context(payloads), context_type=context_type, step='refresh-' + claim.stage,
                commit=False, expected_generation=claim.scope.get('generation'), fetched_payloads=payloads)
            missing = result.missing_symbol_reasons.get(symbol)
            return {'state': 'waiting_provider' if symbol in result.missing_symbols else 'ready',
                    'reason': missing, 'result': {'target_ready': symbol not in result.missing_symbols}}
        return Collected(apply)

    def collect_lending(self, claim):
        return self._collect_context(claim, 'lending')

    def collect_full_margin(self, claim):
        return self._collect_context(claim, 'full_margin')

    def collect_ohlcv(self, claim):
        from ai_stock_sentinel.daily_radar.raw_data import prepare_daily_radar_raw_payloads
        from ai_stock_sentinel.daily_radar.refresh_router import _persist_ohlcv_refresh
        prepared(self.db, claim.run_date)
        fetcher = providers.get_daily_radar_technical_fetcher(self.db)
        collection = prepare_daily_radar_raw_payloads(self.db, claim.run_date,
            [claim.scope['symbol']], technical_fetcher=fetcher)
        self.db.rollback()
        payloads, _ = self.store.capture_operation(operation='technical-refresh-payload',
            params={'symbol': claim.scope['symbol'], 'run_date': str(claim.run_date)},
            fetch=lambda: {'payloads': collection.fetch()})
        payloads = payloads['payloads']
        def apply(db):
            latest = prepared(db, claim.run_date)
            current_snapshot = latest.id, deepcopy(latest.selected_symbols), deepcopy(latest.universe)
            result = _persist_ohlcv_refresh(db, request=DailyRadarRefreshStepRequest(run_date=claim.run_date),
                run_date=claim.run_date, snapshot=current_snapshot, technical_fetcher=fetcher, fetched_payloads=payloads, market_bars=[], commit=False)
            symbol = claim.scope['symbol']
            return {'state': 'waiting_provider' if symbol in result.missing_symbols else 'ready',
                'reason': result.missing_symbol_reasons.get(symbol),
                'result': {'target_ready': symbol not in result.missing_symbols,
                           'diagnostic': result.symbol_diagnostics.get(symbol, {})}}
        return Collected(apply)

    def collect_market_context(self, claim):
        from ai_stock_sentinel.daily_radar.refresh_router import persist_market_context
        self.db.rollback()
        from ai_stock_sentinel.daily_radar.market_context import YFinanceMarketIndexContextProvider
        provider = YFinanceMarketIndexContextProvider(official_request_getter=self.getter)
        context, _ = self.store.capture_operation(operation='market-index-context',
            params={'run_date': str(claim.run_date), 'market': 'TW'},
            fetch=lambda: dict(provider.build(run_date=claim.run_date, market='TW')))
        def apply(db):
            result = persist_market_context(db, request=DailyRadarRefreshStepRequest(run_date=claim.run_date),
                run_date=claim.run_date, market_context=context, commit=False)
            return {'state': 'ready' if result.status == 'completed' else 'waiting_provider',
                    'reason': None if result.status == 'completed' else 'market_context_incomplete'}
        return Collected(apply)

    def collect_scoring(self, claim):
        from ai_stock_sentinel.daily_radar.run_router import execute_prepared_scoring
        def apply(db):
            result = execute_prepared_scoring(db, payload=DailyRadarRefreshStepRequest(run_date=claim.run_date), commit=False)
            return {'state': 'ready' if result.status == 'completed' else 'waiting_provider',
                'result': {'run_id': result.run_id, 'status': result.status, 'candidate_count': result.candidate_count}}
        return Collected(apply)

    def collect_calendar(self, claim):
        from ai_stock_sentinel.daily_radar.trading_calendar import OfficialTradingCalendarProvider, require_calendar, save_calendar, load_calendar
        start = date.fromisoformat(claim.scope['start_date'])
        calendar = load_calendar(self.db, start_date=start, as_of_date=claim.run_date)
        if calendar is None or calendar.through < claim.run_date:
            self.db.rollback()
            calendar = require_calendar(OfficialTradingCalendarProvider(self.db), start_date=start, end_date=claim.run_date)
        def apply(db):
            save_calendar(db, calendar)
            return {'state': 'ready', 'result': {'calendar': calendar.payload()}}
        return Collected(apply)

    def collect_window(self, claim):
        from ai_stock_sentinel.daily_radar.validation_execution import run_signal_close_validation
        from ai_stock_sentinel.daily_radar.research_validation import run_research_validation, RESEARCH_TERMINAL_REASONS
        from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
        from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar, save_calendar
        from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs, upsert_forward_validation_results, persisted_forward_validation_outcomes, forward_validation_result_revision
        from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
        from ai_stock_sentinel.daily_radar.readiness import classify_window, recovery_action, validation_candidate_revision
        scope = claim.scope
        signal, asof = date.fromisoformat(scope['signal_date']), date.fromisoformat(scope['as_of_date'])
        calendar = load_calendar(self.db, start_date=signal, as_of_date=asof)
        if calendar is None or calendar.through < asof:
            raise ValueError('official_trading_calendar_unavailable')
        future = [d for d in calendar.sessions if signal < d <= asof]
        target = future[scope['window_days'] - 1] if len(future) >= scope['window_days'] else None
        calendar_revision = fingerprint([str(d) for d in calendar.sessions if signal <= d <= target]) if target else None
        if scope.get('calendar_revision') is not None and scope['calendar_revision'] != calendar_revision:
            raise LostLease('radar_window_inputs_changed')
        candidates = forward_validation_candidates_from_runs(self.db, market='TW', start_date=signal, end_date=signal)
        candidates = [c for c in candidates if c['candidate_id'] == scope['candidate_id']]
        if (len(candidates) != 1 or (scope.get('candidate_revision') is not None
                and validation_candidate_revision(candidates[0]) != scope['candidate_revision'])):
            raise LostLease('radar_window_inputs_changed')
        request = DailyRadarForwardValidationRunRequest(mode='due', as_of_date=asof, start_date=signal,
            end_date=signal, windows=[scope['window_days']], return_basis=scope['return_basis'])
        def result_row(db):
            return db.scalar(select(DailyRadarForwardValidationResult).where(
                DailyRadarForwardValidationResult.candidate_id == scope['candidate_id'],
                DailyRadarForwardValidationResult.window_days == scope['window_days'],
                DailyRadarForwardValidationResult.validation_version == scope['validation_version'])
                .execution_options(populate_existing=True))
        expected = {(scope['candidate_id'], scope['window_days'], scope['validation_version']):
                    forward_validation_result_revision(result_row(self.db))}
        provider = (CachedForwardPrices(self.db, store=self.store) if scope['return_basis'] == 'signal_close'
                    else RecoveringResearchPriceProvider(session=self.db, source_store=self.store))
        execute = run_signal_close_validation if scope['return_basis'] == 'signal_close' else run_research_validation
        bundle = execute(self.db, request, as_of_date=asof, price_provider=provider,
            calendar_provider=SimpleNamespace(fetch=lambda **kwargs: calendar), candidate_ids=[scope['candidate_id']], prepare_only=True)
        revision = [validation_candidate_revision(c) for c in bundle.candidates]
        def apply(db):
            from ai_stock_sentinel.daily_radar.input_manifests import serialize_publication
            serialize_publication(db, market='TW', run_date=signal)
            current = forward_validation_candidates_from_runs(db, market='TW', start_date=signal, end_date=signal)
            current = [c for c in current if c['candidate_id'] == scope['candidate_id']]
            latest = load_calendar(db, start_date=signal, as_of_date=asof)
            latest_revision = fingerprint([str(d) for d in latest.sessions if signal <= d <= target]) if latest and target else None
            if [validation_candidate_revision(c) for c in current] != revision or latest is None or latest.through < asof or latest_revision != calendar_revision:
                raise LostLease('radar_window_inputs_changed')
            existing = result_row(db)
            if existing is not None and existing.evaluation_as_of_date and existing.evaluation_as_of_date > asof:
                return {'state': 'needs_review', 'reason': 'newer_validation_result_preserved',
                        'result': {'manual_repair_required': True}}
            before = persisted_forward_validation_outcomes(db, current, windows=request.windows, as_of_date=asof,
                validation_version=scope['validation_version'])
            for saved in before:
                state, reason, _ = classify_window(current[0], saved, basis=scope['return_basis'], window=scope['window_days'], as_of_date=asof, calendar=calendar)
                if state in {'invalid_window', 'missing_diagnostic', 'input_unverified'}:
                    return {'state': 'needs_review', 'reason': reason, 'result': {'manual_repair_required': True}}
            kwargs = {'terminal_skip_reasons': RESEARCH_TERMINAL_REASONS} if scope['return_basis'] == 'next_open' else {}
            upsert_forward_validation_results(db, bundle.outcomes, expected_revisions=expected, **kwargs)
            accepted = result_row(db)
            if accepted is not None and accepted.evaluation_as_of_date and accepted.evaluation_as_of_date > asof:
                return {'state': 'needs_review', 'reason': 'newer_validation_result_preserved',
                        'result': {'manual_repair_required': True}}
            save_calendar(db, calendar)
            db.flush()
            saved = persisted_forward_validation_outcomes(db, current, windows=request.windows, as_of_date=asof,
                validation_version=scope['validation_version'])
            state, reason, accepted_target = classify_window(current[0], saved[0] if saved else None,
                basis=scope['return_basis'], window=scope['window_days'], as_of_date=asof, calendar=calendar)
            action = recovery_action(reason) if reason else None
            return {'state': 'ready' if state in {'validated', 'terminal'} else 'needs_review' if action in {'needs_review', 'repair_calendar', 'repair_diagnostic'} else 'waiting_provider',
                'reason': reason, 'result': {'before': before, 'after': saved, 'expected_target_date': str(accepted_target)}}
        return Collected(apply)

    def collect_avwap(self, claim):
        from ai_stock_sentinel.phase1_avwap.service import refresh_phase1_avwap_snapshots_for_symbols
        from ai_stock_sentinel.phase1_avwap.provider import ArchiveFirstDailyPriceProvider, TwseDailyPriceProvider, DailyPriceHistoryResult, InsufficientListedHistory
        from ai_stock_sentinel.phase1_avwap.calculator import DailyPriceBar
        from ai_stock_sentinel.phase1_avwap.repository import get_phase1_avwap_snapshots
        symbol = claim.scope['symbol']
        existing = get_phase1_avwap_snapshots(self.db, symbols=[symbol], data_date=claim.run_date).get(symbol)
        reusable = existing is not None and existing.freshness == 'fresh'
        provider = ArchiveFirstDailyPriceProvider(self.db,
            fallback_provider=TwseDailyPriceProvider(request_get=self.getter, month_fetch_workers=1))
        if not reusable:
            provider.prepare_fetch([symbol], start_date=claim.run_date - timedelta(days=120), end_date=claim.run_date)
        self.db.rollback()
        history, terminal_error = None, None
        if not reusable:
            def fetch():
                try:
                    result = provider.fetch_history_result(symbol, start_date=claim.run_date - timedelta(days=120), end_date=claim.run_date)
                except InsufficientListedHistory as exc:
                    exc.local_budget_exhausted = True  # No host cooldown for proven local inapplicability.
                    raise
                value = asdict(result)
                value['bars'] = [asdict(bar) | {'trade_date': str(bar.trade_date)} for bar in result.bars]
                return value
            try:
                value, _ = self.store.capture_operation(operation='avwap-history',
                    params={'symbol': symbol, 'start_date': str(claim.run_date - timedelta(days=120)), 'end_date': str(claim.run_date)}, fetch=fetch)
                history = DailyPriceHistoryResult(**(value | {'bars': [DailyPriceBar(**(bar | {'trade_date': date.fromisoformat(bar['trade_date'])})) for bar in value['bars']]}))
            except InsufficientListedHistory as exc:
                terminal_error = exc
        def history_result(*args, **kwargs):
            if terminal_error is not None:
                raise terminal_error
            if history is None:
                raise LostLease('radar_avwap_snapshot_changed')
            return history
        static = SimpleNamespace(fetch_history_result=history_result)
        def apply(db):
            result = refresh_phase1_avwap_snapshots_for_symbols(db, symbols=[symbol], data_date=claim.run_date, provider=static)
            missing = result.missing_symbol_reasons.get(symbol)
            row = prepared(db, claim.run_date)
            db.flush()
            snapshots = get_phase1_avwap_snapshots(db, symbols=row.selected_symbols, data_date=claim.run_date)
            not_applicable = [s for s, value in snapshots.items() if value.missing_reason == 'insufficient_listed_history']
            missing_symbols = [s for s in row.selected_symbols if s not in snapshots or (snapshots[s].freshness != 'fresh' and s not in not_applicable)]
            update_daily_radar_prepared_step_status(db, row, step='refresh-avwap', status='failed' if missing_symbols else 'completed',
                details={'required_for_scoring': False, 'missing_symbols': missing_symbols,
                         'not_applicable_symbols': not_applicable, 'symbol_count': len(row.selected_symbols)})
            return {'state': 'terminal' if terminal_error else 'waiting_provider' if missing else 'ready',
                    'reason': 'insufficient_listed_history' if terminal_error else missing}
        return Collected(apply)

    def collect_managed_raw(self, claim):
        from ai_stock_sentinel.daily_radar.raw_data import prepare_daily_radar_raw_payloads, ensure_daily_radar_raw_rows, current_daily_radar_raw_rows
        from ai_stock_sentinel.daily_radar.pipeline_support import _industry_classifications_by_symbol, _materialize_industry_classifications, _finish_managed_raw_data_refresh
        from ai_stock_sentinel.daily_radar.managed_raw_data import ManagedRawDataSelection
        symbol, symbols = claim.scope['symbol'], claim.scope['managed_symbols']
        fetcher = providers.get_daily_radar_technical_fetcher(self.db)
        self.db.rollback()
        industries = _industry_classifications_by_symbol([symbol])
        collection = prepare_daily_radar_raw_payloads(self.db, claim.run_date, [symbol], technical_fetcher=fetcher)
        self.db.rollback()
        captured, _ = self.store.capture_operation(operation='technical-refresh-payload',
            params={'symbol': symbol, 'run_date': str(claim.run_date)},
            fetch=lambda: {'payloads': collection.fetch()})
        payloads = captured['payloads']
        def apply(db):
            rows = ensure_daily_radar_raw_rows(db, claim.run_date, [symbol], technical_fetcher=fetcher, fetched_payloads=payloads)
            _materialize_industry_classifications(rows, resolver=industries.get)
            db.flush()
            current = current_daily_radar_raw_rows(get_final_raw_data_rows_for_symbols(db, run_date=claim.run_date, symbols=symbols), run_date=claim.run_date)
            covered = {row.symbol for row in current}
            selection = ManagedRawDataSelection(tuple(symbols), tuple(symbols), len(symbols), claim.scope.get('deferred', 0))
            _finish_managed_raw_data_refresh(db, prepared=prepared(db, claim.run_date),
                request=DailyRadarManagedRawDataRefreshRequest(run_date=claim.run_date), run_date=claim.run_date,
                selection=selection, selected_overlap_count=len(set(symbols) & set(prepared(db, claim.run_date).selected_symbols)),
                status='completed' if len(covered) == len(symbols) else 'failed', records_written=len(rows),
                missing_record_count=len(symbols) - len(covered), commit=False)
            return {'state': 'ready' if symbol in covered else 'waiting_provider',
                'reason': None if symbol in covered else 'managed_raw_data_incomplete', 'result': {'target_ready': symbol in covered}}
        return Collected(apply)

    def collect_source_recheck(self, claim):
        from ai_stock_sentinel.db.models import RadarSourceSnapshot
        from ai_stock_sentinel.calibration.price_provider import YFinanceResearchPriceProvider, YFinanceForwardPriceProvider
        from ai_stock_sentinel.calibration.research_prices import requests
        from datetime import datetime, timezone
        observations = claim.scope['observations']
        requests_to_check = []
        for observed in observations:
            row = self.db.get(RadarSourceSnapshot, observed['snapshot_id'])
            if row is None or row.request_hash != observed['request_hash']:
                raise ValueError('radar_source_reference_invalid')
            self.store._read(row)
            request = deepcopy(row.request)
            if request.get('operation') == 'technical-adjusted-history':
                continue  # Accepted computation is forensic, not a network source.
            requests_to_check.append((request, observed['snapshot_sha256']))
        self.db.rollback()
        yahoo_getter = CapturedOfficialGetter(self.store, getter=requests.get)
        changed, expires = False, []
        for request, old_hash in requests_to_check:
            operation, params = request.get('operation'), request['params']
            if operation in {'research-unadjusted-history', 'forward-adjusted-history'}:
                provider = YFinanceForwardPriceProvider() if operation == 'forward-adjusted-history' else YFinanceResearchPriceProvider()
                def fetch(params=params, provider=provider):
                    rows = provider.fetch([params['symbol']], start_date=date.fromisoformat(params['start_date']),
                        end_date=date.fromisoformat(params['end_date'])).get(params['symbol'])
                    if not rows:
                        raise RuntimeError('forward_price_provider_returned_no_data')
                    return {'rows': list(rows)}
                _, proof = self.store.capture_sdk(operation=operation, params=params, fetch=fetch)
            elif 'url' in request:
                getter = yahoo_getter if request['provider'] == 'yahoo_chart_events' else self.getter
                getter(request['url'], params=params, timeout=20)
                proof = self.store.seen[fingerprint(request)]
            else:
                raise ValueError('radar_source_recheck_not_supported')
            changed |= proof['snapshot_sha256'] != old_hash
            expires.append(datetime.fromisoformat(proof['expires_at']))
        result = {'changed': changed, 'checked_source_count': len(requests_to_check)}
        return Collected(lambda db: {'state': 'ready' if changed else 'waiting_provider',
            'reason': None if changed else 'source_revision_unchanged', 'result': result,
            'retry_at': max(expires, default=datetime.now(timezone.utc) + timedelta(hours=1))})
