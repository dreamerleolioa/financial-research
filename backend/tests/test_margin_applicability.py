from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


from ai_stock_sentinel.daily_radar.background_context import (
    same_day_background_context_is_reusable,
    update_background_chip_context_cache,
)
from ai_stock_sentinel.daily_radar.official_background_context import OfficialBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.raw_data import _project_margin_context
from ai_stock_sentinel.daily_radar.data_quality import margin_evidence_is_complete, missing_scoring_fields
from ai_stock_sentinel.db.models import SharedBackgroundContext
from tests.test_official_background_context import _FakeResponse, _twse_margin_payload

RUN_DATE = date(2026, 9, 9)
LISTING_URL = 'https://www.twse.com.tw/rwd/zh/company/newlisting'


def _listing(remark='創新板第一上市', listing_date='115.05.29'):
    return {'stat': 'OK', 'fields': ['公司代號', '股票上市買賣日期', '備註'],
            'data': [['7827', listing_date, remark]]}


def _provider(listing=None, *, include_margin=False, calls=None):
    def get(url, *, params, **kwargs):
        if calls is not None:
            calls.append(url)
        if url == LISTING_URL:
            if isinstance(listing, Exception):
                raise listing
            return _FakeResponse(listing if listing is not None else _listing())
        stock_id = '7827' if include_margin else '2330'
        return _FakeResponse(_twse_margin_payload(params['date'], [
            [stock_id, '測試', '0', '0', '0', '900', '1000', '0', '0', '0', '0', '40', '50', '0', '0', '']
        ]))
    return OfficialBackgroundChipContextProvider(request_get=get, lookback_trading_days=1,
                                                max_lookback_calendar_days=1)


def _fetch(provider, run_date=RUN_DATE):
    return list(provider.fetch(symbols=['7827.TW'], context_types=['full_margin'],
                               run_date=run_date, market='TW'))[0]


def test_new_first_listing_is_not_applicable_and_preserves_evidence():
    result = _fetch(_provider())
    assert result.freshness == 'fresh'
    assert result.as_of_date == RUN_DATE
    assert result.missing_reason is None
    assert result.payload['applicability'] == 'not_applicable'
    evidence = result.payload['eligibility']
    assert evidence['listing_date'] == '2026-05-29'
    assert evidence['evaluated_for'] == '2026-09-09'
    assert evidence['source_url'] == LISTING_URL
    assert evidence['reason'] == 'initial_listing_under_six_months'
    assert 'latest_margin_balance' not in result.payload
    assert same_day_background_context_is_reusable(result, run_date=RUN_DATE)
    assert not same_day_background_context_is_reusable(result, run_date=date(2026, 9, 10))


@pytest.mark.parametrize('listing', [
    _listing('櫃轉市'), _listing(''), _listing('創新板'),
    _listing(listing_date='115.09.10'), _listing(listing_date='bad'),
    {'stat': 'OK', 'fields': [], 'data': []},
    {'stat': 'OK', 'fields': ['公司代號', '股票上市買賣日期', '備註'], 'data': []},
    RuntimeError('upstream unavailable'),
])
def test_unproven_ineligibility_remains_missing(listing):
    result = _fetch(_provider(listing))
    assert result.missing_reason == 'official_no_data'
    assert not same_day_background_context_is_reusable(result, run_date=RUN_DATE)


@pytest.mark.parametrize('run_date,expected', [
    (date(2026, 11, 28), True), (date(2026, 11, 29), False), (date(2026, 11, 30), False),
])
def test_six_calendar_month_boundary_does_not_grant_eligibility(run_date, expected):
    result = _fetch(_provider(), run_date)
    assert (result.payload.get('applicability') == 'not_applicable') is expected


def test_actual_margin_rows_take_precedence_and_do_not_fetch_listing():
    calls = []
    result = _fetch(_provider(include_margin=True, calls=calls))
    assert result.payload['latest_margin_balance'] == 1000
    assert 'applicability' not in result.payload
    assert LISTING_URL not in calls


def test_not_applicable_projection_is_complete_without_fabricated_numbers():
    result = _fetch(_provider())
    projected = _project_margin_context(vars(result), technical={})
    assert projected['applicability'] == 'not_applicable'
    assert projected['eligibility'] == result.payload['eligibility']
    assert margin_evidence_is_complete(projected)
    missing = missing_scoring_fields(ohlcv={}, indicators={}, institutional_flow={}, margin=projected)
    assert not any(field.startswith('margin.') for field in missing)
    assert 'margin_delta_pct' not in projected
    assert 'margin_to_volume' not in projected
    invalid = deepcopy(projected)
    invalid['eligibility']['evaluated_for'] = '2026-11-29'
    assert not margin_evidence_is_complete(invalid)
    assert not margin_evidence_is_complete({'applicability': 'not_applicable'})


def test_refresh_persists_and_reuses_not_applicable_separately_from_missing():
    engine = create_engine('sqlite://')
    SharedBackgroundContext.__table__.create(engine)
    calls = []
    with Session(engine) as session:
        for index in range(2):
            result = update_background_chip_context_cache(
                session, run_date=RUN_DATE, market='TW', provider=_provider(calls=calls),
                symbols=['7827.TW'], context_types=['full_margin'],
                require_same_day_fresh=True, reuse_same_day_fresh=True,
            )
            session.flush()
            assert result['status'] == 'completed'
            assert result['missing_symbols'] == []
            assert result['not_applicable_symbols'] == ['7827.TW']
            assert result['records_written'] == (1 if index == 0 else 0)
        assert calls.count(LISTING_URL) == 1


def test_not_applicable_scoring_has_no_margin_bonus_and_keeps_replay_evidence():
    from tests.test_daily_radar_scoring import _joined_records_by_symbol, _market_context
    from ai_stock_sentinel.daily_radar.prefilter import prefilter_record
    from ai_stock_sentinel.daily_radar.scoring import score_daily_radar_record
    from ai_stock_sentinel.daily_radar.explanations import _input_evidence

    for symbol in ('2330.TW', '2454.TW', '3034.TW', '2303.TW'):
        record = deepcopy(_joined_records_by_symbol()[symbol])
        record['margin'] = dict(_fetch(_provider()).payload)
        record['margin']['eligibility'].update(
            symbol=symbol, listing_date='2026-04-01', evaluated_for=record['record_date'],
        )
        filtered = prefilter_record(record)
        assert 'data_gap' not in {item['code'] for item in filtered['prefilter_reasons']}
        result = score_daily_radar_record(record, market_context=_market_context(), prefilter_result=filtered)
        assert not {'institutional_margin_contained', 'bottoming_margin_easing',
                    'support_retest_margin_not_expanding'} & {r['rule_id'] for r in result['matched_rules']}
        assert result['input_snapshot']['replay_input']['record']['margin'] == record['margin']
        assert filtered['debug']['margin']['margin_to_volume'] is None
        assert any('融資融券不適用' in text for text in _input_evidence(record))


@pytest.mark.parametrize('field,value', [('symbol', '2330.TW'), ('evaluated_for', '2026-09-08')])
def test_wrong_symbol_or_date_cannot_exempt_required_scoring_fields(field, value):
    margin = deepcopy(_fetch(_provider()).payload)
    margin['eligibility'][field] = value
    assert not margin_evidence_is_complete(margin, record_date=RUN_DATE, symbol='7827.TW')
    missing = missing_scoring_fields(ohlcv={}, indicators={}, institutional_flow={}, margin=margin,
                                    record_date=RUN_DATE, symbol='7827.TW')
    assert 'margin.margin_delta_pct' in missing
    assert 'margin.margin_to_volume' in missing


def test_new_raw_row_roundtrip_retains_applicability_without_old_margin_values():
    from tests.test_daily_radar_raw_data import FakeBatchFetcher
    from ai_stock_sentinel.daily_radar.raw_data import ensure_daily_radar_raw_rows
    from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_cache_records
    from ai_stock_sentinel.db.models import StockRawData

    engine = create_engine('sqlite://')
    StockRawData.__table__.create(engine)
    context = vars(_fetch(_provider()))
    with Session(engine, autoflush=False) as session:
        rows = ensure_daily_radar_raw_rows(session, RUN_DATE, ['7827.TW'],
                                          technical_fetcher=FakeBatchFetcher(),
                                          margin_contexts_by_symbol={'7827.TW': context})
        session.flush()
        session.expire_all()
        [record] = load_daily_radar_cache_records(rows)
        assert record['margin']['applicability'] == 'not_applicable'
        assert record['data_dates']['margin'] == RUN_DATE.isoformat()
        assert 'margin_delta_pct' not in record['margin']


def test_export_keeps_inapplicability_and_does_not_count_it_as_missing():
    from tests.test_export_codex_daily_radar import exporter
    margin = dict(_fetch(_provider()).payload)
    result = exporter._analytical_completeness([
        {'symbol': '7827.TW', 'record_date': '2026-09-09', 'fundamental': {'margin': margin}}
    ])
    assert result['lanes']['margin']['missing_symbols'] == []


def test_export_script_still_starts_directly_without_pythonpath():
    import os
    from pathlib import Path
    import subprocess
    import sys

    script = Path(__file__).parents[1] / 'scripts' / 'export_codex_daily_radar.py'
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    result = subprocess.run([sys.executable, str(script), '--help'], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_refresh_endpoint_retains_not_applicable_in_response_and_step_status(monkeypatch):
    from types import SimpleNamespace
    from ai_stock_sentinel.daily_radar import router
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarRefreshStepRequest

    engine = create_engine('sqlite://')
    SharedBackgroundContext.__table__.create(engine)
    statuses = []
    monkeypatch.setattr(router, '_prepared_run_or_404',
                        lambda *args, **kwargs: SimpleNamespace(selected_symbols=['7827.TW']))
    monkeypatch.setattr(router, 'update_daily_radar_prepared_step_status',
                        lambda *args, **kwargs: statuses.append(kwargs))
    with Session(engine) as session:
        result = router._refresh_daily_radar_context_step(
            session, payload=DailyRadarRefreshStepRequest(run_date=RUN_DATE, market='TW'),
            provider=_provider(), context_type='full_margin', step='refresh-full-margin',
        )
    assert result.status == 'completed'
    assert result.model_dump()['not_applicable_symbols'] == ['7827.TW']
    assert statuses[0]['details']['not_applicable_symbols'] == ['7827.TW']
