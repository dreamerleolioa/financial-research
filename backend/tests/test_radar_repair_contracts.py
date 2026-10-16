"""Regression cases for Radar's required evidence and isolated EPS work."""
from datetime import date, datetime, timezone
from http.client import IncompleteRead
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from test_daily_radar_api import (
    FakeBatchTechnicalFetcher, _api_client, _clear_daily_radar_api_overrides,
    _persist_raw_data, _technical_payload, daily_radar_db_session,
)
from test_radar_fundamental_preparation import db
from ai_stock_sentinel.daily_radar.pipeline_support import _ai_evidence_missing_by_lane
from ai_stock_sentinel.daily_radar.repository import upsert_shared_background_context
from ai_stock_sentinel.db.models import DailyRadarPreparedRun, StockRawData, CompanyFundamentalPeriod
from ai_stock_sentinel.data_sources.fundamental import router, service, repository
from ai_stock_sentinel.data_sources.fundamental.normalizers import normalize_finmind_statement_rows
from ai_stock_sentinel.data_sources.fundamental.schemas import FundamentalBackfillRequest
from fastapi import HTTPException

DAY = date(2026, 10, 15)


def _script(filename):
    spec = importlib.util.spec_from_file_location(filename, Path(__file__).parents[2] / '.github/scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('source_date', [None, '2026-10-14', '2026-10-16', 'invalid'])
def test_stale_or_undated_numeric_margin_is_not_ready(source_date):
    row = SimpleNamespace(symbol='2330.TW', record_date=DAY,
        technical=FakeBatchTechnicalFetcher().fetch(['2330.TW'], run_date=DAY)['2330.TW'],
        institutional={}, fundamental={'margin': {'margin_delta_pct': 1, 'margin_to_volume': .1},
            'data_dates': {'margin': source_date}})
    assert _ai_evidence_missing_by_lane([row])['margin'] == ['2330.TW']


def test_lending_outage_cannot_fail_complete_required_ai_evidence(monkeypatch, daily_radar_db_session):
    session = daily_radar_db_session
    day = date(2026, 6, 1)
    session.add(DailyRadarPreparedRun(run_date=day, market='TW', selected_symbols=['2330.TW'], universe=[], symbol_count=1))
    _persist_raw_data(session, symbol='2454.TW', record_date=day, technical=_technical_payload('2454.TW', day))
    upsert_shared_background_context(session, symbol='2454.TW', context_type='full_margin',
        applicable_consumers=('daily_radar',), source={'provider': 'fixture'}, as_of_date=day,
        freshness='fresh', payload={'latest_margin_balance': 1000, 'margin_balance_delta_pct': 1}, replay_key='margin')
    session.commit()
    class Background:
        def fetch(self, **kwargs):
            pytest.fail('Same-day full margin is cached; AI evidence must not request lending')
    client = _api_client(monkeypatch, session, background_context_provider=Background())
    try:
        body = client.post('/internal/daily-radar/refresh-ai-evidence', json={'run_date': str(day)},
            headers={'Authorization': 'Bearer test-token'}).json()
        assert body['evidence_ready'] is True, body
    finally:
        client.close()
        _clear_daily_radar_api_overrides()


def test_radar_ttm_stops_after_four_quarters_without_dividend_or_statement_fallback(db, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 15, 3, tzinfo=timezone.utc)
    monkeypatch.setattr(repository, 'datetime', FrozenDatetime)
    class Historical:
        def fetch_periods(self, symbol):
            return normalize_finmind_statement_rows([
                {'date': d, 'type': 'EPS', 'value': '1'}
                for d in ['2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30']
            ], symbol=symbol)
    class Fallback:
        def fetch_statement_rows(self, symbol): pytest.fail('TTM already complete')
        def fetch_dividend_rows(self, symbol): pytest.fail('Dividends are not required by Radar')
    result = service.backfill_fundamentals(db, symbols=['2330.TW'], radar_as_of_date=DAY,
        historical_provider=Historical(), provider=Fallback())
    assert result.status == 'ok'
    assert result.provider_attempts == {'mops_historical': 1, 'finmind_statement': 0, 'finmind_dividend': 0}


@pytest.mark.parametrize('stage', ['prepared', 'raw_pool'])
def test_radar_job_contains_only_reported_eps_gaps_and_ignores_old_maintenance(db, monkeypatch, stage):
    symbols = [f'{number}.TW' for number in range(1100, 1165)]
    prepared = db.scalar(select(DailyRadarPreparedRun))
    prepared.selected_symbols = symbols + ['9999.TW']
    if stage == 'raw_pool':
        db.add_all([StockRawData(symbol=symbol, record_date=DAY, raw_data_is_final=True)
            for symbol in symbols + ['9999.TW']])
    for symbol in symbols:
        repository.store_fundamental_periods(db, normalize_finmind_statement_rows([
            {'date': day, 'type': 'EPS', 'value': 1}
            for day in ['2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30']
        ], symbol=symbol))
    for period in db.scalars(select(CompanyFundamentalPeriod)):
        period.first_observed_at = datetime(2026, 10, 15, 3, tzinfo=timezone.utc)
    old = service.create_fundamental_backfill_job(db, symbols=symbols, raw_pool_date=date(2026, 10, 14))
    old_id = old.id
    db.commit()
    calls = []
    def fetch(session, *, symbols, after_symbol, limit, **kwargs):
        calls.append(list(symbols))
        if kwargs.get('before_persist'):
            kwargs['before_persist']()
        return service.FundamentalBackfillResult('partial', list(symbols), 0, None, [],
            data_gaps=[{'symbol': '9999.TW', 'reason': 'no_eps_history', 'period_count': 0}])
    monkeypatch.setattr(router, 'backfill_fundamentals', fetch)
    result = router.backfill_fundamentals_endpoint(FundamentalBackfillRequest(raw_pool_date=DAY, radar_stage=stage), db=db)
    assert calls == [['9999.TW']]
    assert result['job_id'] != old_id
    assert result['purpose'] == 'radar_ttm_eps'
    assert result['cache_ready_as_of'] is False
    assert service.get_fundamental_backfill_job(db, job_id=old_id).next_after_symbol is None


def test_truncated_first_validation_retains_diagnostics_and_runs_second(tmp_path):
    calls = []
    def request(endpoint, token, payload):
        calls.append(payload['return_basis'])
        if payload['return_basis'] == 'signal_close':
            raise IncompleteRead(b'{', 20)
        return {'status': 'completed', 'as_of_date': str(DAY), 'retryable_skipped_count': 0,
            'report': {'remaining_retryable_window_count': 0}}
    assert _script('run_daily_radar_validation.py').run(endpoint='https://example.invalid', token='private',
        run_date=str(DAY), output_dir=tmp_path, request=request, sleep=lambda _: None) == 1
    assert calls == ['signal_close', 'signal_close', 'next_open']
    assert 'IncompleteRead' in (tmp_path / 'signal_close-attempt-1.json').read_text()
    assert (tmp_path / 'next_open-attempt-1.json').exists()


@pytest.mark.parametrize('values,dates,ready', [
    ([0, 0, 0, 0], ['2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30'], True),
    ([-1, -2, 0, 1], ['2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30'], True),
    ([1, 1, 1, 1], ['2025-06-30', '2025-12-31', '2026-03-31', '2026-06-30'], False),
])
def test_radar_eps_requires_contiguous_quarters_without_positive_eps_requirement(db, monkeypatch, values, dates, ready):
    periods = normalize_finmind_statement_rows([
        {'date': day, 'type': 'EPS', 'value': value} for day, value in zip(dates, values)
    ], symbol='2330.TW')
    repository.store_fundamental_periods(db, periods)
    for row in db.scalars(select(CompanyFundamentalPeriod)):
        row.first_observed_at = datetime(2026, 10, 15, 3, tzinfo=timezone.utc)
    class Empty:
        def fetch_statement_rows(self, symbol): return []
        def fetch_dividend_rows(self, symbol): pytest.fail('Radar must never fetch dividends')
    result = service.backfill_fundamentals(db, symbols=['2330.TW'], provider=Empty(), radar_as_of_date=DAY)
    assert (result.status == 'ok') is ready
    assert result.errors == []
    assert bool(result.data_gaps) is (not ready)


@pytest.mark.parametrize('value', ['NaN', 'Infinity', 'invalid'])
def test_invalid_fallback_eps_is_a_provider_error_not_a_valid_coverage_gap(db, value):
    class Broken:
        def fetch_statement_rows(self, symbol):
            return [{'date': '2026-06-30', 'type': 'EPS', 'value': value}]
        def fetch_dividend_rows(self, symbol): pytest.fail('Radar must never fetch dividends')
    result = service.backfill_fundamentals(db, symbols=['2330.TW'], provider=Broken(), radar_as_of_date=DAY)
    assert result.errors and not result.data_gaps
    assert db.scalar(select(CompanyFundamentalPeriod.id)) is None


def test_radar_and_maintenance_jobs_cannot_be_resumed_under_other_contracts(db, monkeypatch):
    maintenance = service.create_fundamental_backfill_job(db, symbols=['2330.TW'], raw_pool_date=DAY)
    radar = service.create_fundamental_backfill_job(db, symbols=['2330.TW'], raw_pool_date=DAY,
        purpose='radar_ttm_eps', radar_stage='prepared')
    maintenance_id, radar_id = maintenance.id, radar.id
    db.commit()
    monkeypatch.setattr(router, 'backfill_fundamentals', lambda *a, **kw: pytest.fail('Wrong contract must not fetch'))
    for request in [
        FundamentalBackfillRequest(job_id=maintenance_id, raw_pool_date=DAY, radar_stage='prepared'),
        FundamentalBackfillRequest(job_id=radar_id),
        FundamentalBackfillRequest(job_id=radar_id, raw_pool_date=DAY, radar_stage='raw_pool'),
    ]:
        with pytest.raises(HTTPException) as error:
            router.backfill_fundamentals_endpoint(request, db=db)
        assert error.value.detail['code'] == 'fundamental_backfill_purpose_mismatch'
        db.rollback()


@pytest.mark.parametrize('stage', ['prepared', 'raw_pool'])
def test_final_page_never_attests_coverage_from_cursor_alone(db, monkeypatch, stage):
    db.add(StockRawData(symbol='2330.TW', record_date=DAY, raw_data_is_final=True))
    db.commit()
    def fetch(session, **kwargs):
        kwargs['before_persist']()
        return service.FundamentalBackfillResult('ok', ['2330.TW'], 0, None, [])
    monkeypatch.setattr(router, 'backfill_fundamentals', fetch)
    result = router.backfill_fundamentals_endpoint(FundamentalBackfillRequest(raw_pool_date=DAY, radar_stage=stage), db=db)
    assert result['next_after_symbol'] is None
    assert result['remaining_gap_count'] == 1
    assert result['cache_ready_as_of'] is False


def test_finalizer_reserves_time_for_evidence_when_eps_request_exhausts_its_budget(tmp_path):
    elapsed = [0]
    requests = []
    def request(endpoint, token, payload, timeout):
        step = endpoint.rsplit('/', 1)[-1]
        requests.append((step, timeout))
        if step == 'backfill':
            elapsed[0] = 1501
            return {}
        if step == 'refresh-ai-evidence':
            from test_radar_evidence_workflow_execution import evidence
            return evidence()
        return {'status': 'completed', 'step': step, 'run_date': str(DAY), 'missing_record_count': 0, 'missing_symbols': []}
    code = _script('prepare_daily_radar_evidence.py').run(base_url='https://example.invalid', token='private',
        run_date=str(DAY), mode='finalize', output_dir=tmp_path, request=request, clock=lambda: elapsed[0])
    assert code == 1
    assert requests[-1] == ('refresh-ai-evidence', 299)


def test_runner_does_not_turn_an_exhausted_snapshot_with_gaps_into_success(tmp_path):
    def request(endpoint, token, payload, timeout):
        return {'status': 'ok', 'purpose': 'radar_ttm_eps', 'radar_stage': 'prepared', 'raw_pool_date': str(DAY),
            'job_id': 'job', 'next_after_symbol': None, 'errors': [], 'data_gaps': [],
            'cache_ready_as_of': False, 'remaining_gap_count': 1}
    assert _script('prepare_daily_radar_evidence.py').run(base_url='https://example.invalid', token='private',
        run_date=str(DAY), mode='prewarm', output_dir=tmp_path, request=request) == 1
    import json
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert summary['pagination_completed'] is True and summary['backfill_completed'] is False
    assert 'job_id' not in summary['continuation']


def test_continuation_accepts_full_pool_cache_attestation_after_other_work_fills_gaps(tmp_path):
    calls = []
    def request(endpoint, token, payload, timeout):
        calls.append(payload)
        return {'status': 'ok', 'purpose': 'radar_ttm_eps', 'radar_stage': 'prepared', 'raw_pool_date': str(DAY),
            'job_id': 'job' if len(calls) == 1 else None, 'next_after_symbol': '2330.TW' if len(calls) == 1 else None,
            'errors': [], 'data_gaps': [], 'cache_ready_as_of': len(calls) == 2,
            'remaining_gap_count': 1 if len(calls) == 1 else 0}
    assert _script('prepare_daily_radar_evidence.py').run(base_url='https://example.invalid', token='private',
        run_date=str(DAY), mode='prewarm', output_dir=tmp_path, request=request) == 0
    assert len(calls) == 2 and calls[1]['job_id'] == 'job'


def test_crossing_midnight_during_fetch_does_not_persist_eps_or_advance_cursor(db, monkeypatch):
    def fetch(session, **kwargs):
        monkeypatch.setattr(service, 'today_taipei', lambda: date(2026, 10, 16))
        kwargs['before_persist']()
        pytest.fail('Closed historical date must not reach persistence')
    monkeypatch.setattr(router, 'backfill_fundamentals', fetch)
    with pytest.raises(HTTPException) as error:
        router.backfill_fundamentals_endpoint(FundamentalBackfillRequest(raw_pool_date=DAY, radar_stage='prepared'), db=db)
    assert error.value.detail['code'] == 'radar_fundamental_date_closed'
    db.rollback()
    job = service.get_oldest_running_fundamental_backfill_job(db, purpose='radar_ttm_eps', raw_pool_date=DAY, radar_stage='prepared')
    assert job.next_after_symbol is None and job.status == 'running'
    assert db.scalar(select(CompanyFundamentalPeriod.id)) is None


def test_validation_redacts_escaped_token_in_artifacts_and_console(tmp_path, capsys):
    token = 'secret"\\\nvalue'
    def request(endpoint, token, payload):
        return {'status': 'completed', 'as_of_date': str(DAY), 'retryable_skipped_count': 0,
            'echo': token, 'report': {'remaining_retryable_window_count': 0, 'current_run_gaps': [token]}}
    assert _script('run_daily_radar_validation.py').run(endpoint='https://example.invalid', token=token,
        run_date=str(DAY), output_dir=tmp_path, request=request) == 0
    import json
    for path in tmp_path.glob('*.json'):
        assert json.loads(path.read_text())['echo'] == '[redacted]'
    assert 'secret' not in capsys.readouterr().out


def test_request_deadline_rejects_response_that_swallowed_alarm():
    import time
    from daily_radar_http import request_deadline
    with pytest.raises(TimeoutError):
        with request_deadline(.01):
            try:
                time.sleep(.05)
            except TimeoutError:
                time.sleep(.02)
