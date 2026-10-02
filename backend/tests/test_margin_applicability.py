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


@pytest.mark.parametrize('evidence_kind', ['initial_listing', 'credit_status'])
def test_not_applicable_scoring_has_no_margin_bonus_and_keeps_replay_evidence(evidence_kind):
    from tests.test_daily_radar_scoring import _joined_records_by_symbol, _market_context
    from ai_stock_sentinel.daily_radar.prefilter import prefilter_record
    from ai_stock_sentinel.daily_radar.scoring import score_daily_radar_record
    from ai_stock_sentinel.daily_radar.explanations import _input_evidence

    for symbol in ('2330.TW', '2454.TW', '3034.TW', '2303.TW'):
        record = deepcopy(_joined_records_by_symbol()[symbol])
        if evidence_kind == 'initial_listing':
            record['margin'] = dict(_fetch(_provider()).payload)
            record['margin']['eligibility'].update(
                symbol=symbol, listing_date='2026-04-01', evaluated_for=record['record_date'],
            )
        else:
            from ai_stock_sentinel.daily_radar.margin_applicability import credit_trading_inapplicability
            evaluated = date.fromisoformat(record['record_date'])
            report = _credit_status_report() | {
                'date': evaluated.strftime('%Y%m%d'),
                'data': [[symbol.removesuffix('.TW'), '測試', 'Y ']],
            }
            record['margin'] = credit_trading_inapplicability(
                report, symbols=[symbol], run_date=evaluated,
            )[symbol]
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


OTC_LISTING_URL = 'https://www.twse.com.tw/announcement/publicForm'


def _tw_public_offering_provider(*, listing_type='初上市', listed='115/03/30',
                                cancelled='', report_year=2026, listing_error=False,
                                include_margin=False, calls=None):
    def get(url, *, params, **kwargs):
        if calls is not None:
            calls.append(url)
        if url == LISTING_URL:
            return _FakeResponse({'stat': 'OK',
                'fields': ['公司代號', '股票上市買賣日期', '備註'],
                'data': [['7822', '115.03.30', '科技事業']]})
        if url == OTC_LISTING_URL:
            if listing_error:
                raise RuntimeError('listing unavailable')
            return _FakeResponse({'stat': 'OK', 'date': report_year,
                'fields': ['證券代號', '發行市場', '撥券日期(上市、上櫃日期)', '取消公開抽籤 '],
                'data': [['7822', listing_type, listed, cancelled]]})
        return _FakeResponse(_twse_margin_payload(params['date'], [[
            '7822' if include_margin else '2330', '測試',
            '0', '0', '0', '900', '1000', '0', '0', '0', '0', '40', '50', '0', '0', '',
        ]]))
    return OfficialBackgroundChipContextProvider(request_get=get,
        lookback_trading_days=1, max_lookback_calendar_days=1)


def test_tw_technology_listing_uses_explicit_initial_public_offering_evidence():
    evaluated = date(2026, 9, 29)
    engine = create_engine('sqlite://')
    SharedBackgroundContext.__table__.create(engine)
    calls = []
    with Session(engine) as session:
        for attempt in range(2):
            result = update_background_chip_context_cache(
                session, run_date=evaluated, market='TW',
                provider=_tw_public_offering_provider(calls=calls),
                symbols=['7822.TW'], context_types=['full_margin'],
                require_same_day_fresh=True, reuse_same_day_fresh=True,
            )
            session.flush()
            assert result['status'] == 'completed'
            assert result['not_applicable_symbols'] == ['7822.TW']
            assert result['missing_symbols'] == []
            assert result['records_written'] == (1 if attempt == 0 else 0)
    assert calls.count(OTC_LISTING_URL) == 1
    payload = list(_tw_public_offering_provider().fetch(symbols=['7822.TW'],
        context_types=['full_margin'], run_date=evaluated, market='TW'))[0]
    assert payload.source['dataset'] == 'TWSE_publicForm'
    assert payload.payload['eligibility']['listing_type'] == '初上市'
    assert payload.payload['eligibility']['source_url'] == OTC_LISTING_URL
    projected = _project_margin_context(vars(payload), technical={})
    assert margin_evidence_is_complete(projected, record_date=evaluated, symbol='7822.TW')
    assert not margin_evidence_is_complete(projected, record_date=evaluated, symbol='7822.TWO')
    assert 'margin_balance' not in projected


@pytest.mark.parametrize('kwargs', [
    {'listing_type': '初上櫃'}, {'listing_type': '上市增資'}, {'listing_type': '櫃轉市'},
    {'listed': '115/03/29'}, {'listed': '115/09/30'}, {'listed': 'bad'},
    {'cancelled': '取消'}, {'report_year': 2025}, {'listing_error': True},
])
def test_tw_public_offering_requires_matching_market_date_and_valid_evidence(kwargs):
    result = list(_tw_public_offering_provider(**kwargs).fetch(symbols=['7822.TW'],
        context_types=['full_margin'], run_date=date(2026, 9, 29), market='TW'))[0]
    assert result.missing_reason == 'official_no_data'


def test_tw_public_offering_exemption_expires_on_six_month_anniversary():
    result = list(_tw_public_offering_provider().fetch(symbols=['7822.TW'],
        context_types=['full_margin'], run_date=date(2026, 9, 30), market='TW'))[0]
    assert result.missing_reason == 'official_no_data'


CREDIT_STATUS_URL = 'https://www.twse.com.tw/exchangeReport/TWT93U'


def _credit_status_report():
    return {'stat': 'OK', 'date': '20261001', 'fields': ['代號', '名稱', '備註'],
            'notes': ['符號說明<ul><li>Y-未取得信用交易資格</li></ul>'],
            'data': [['7822', '倍利科', 'Y ']]}


def _credit_status_provider(report=None, *, include_margin=False, calls=None,
                            margin_date=None):
    base = _tw_public_offering_provider(include_margin=include_margin)
    def get(url, *, params, **kwargs):
        if calls is not None:
            calls.append((url, params))
        if url == CREDIT_STATUS_URL:
            if isinstance(report, Exception):
                raise report
            return _FakeResponse(_credit_status_report() if report is None else report)
        response = base._request_get(url, params=params, **kwargs)
        if margin_date and url not in {LISTING_URL, OTC_LISTING_URL}:
            response = _FakeResponse(_twse_margin_payload(margin_date, [[
                '2330', '測試', '0', '0', '0', '900', '1000', '0',
                '0', '0', '0', '40', '50', '0', '0', '',
            ]]))
        return response
    return OfficialBackgroundChipContextProvider(request_get=get,
        lookback_trading_days=1, max_lookback_calendar_days=1)


def test_expired_initial_listing_uses_same_day_explicit_credit_ineligibility():
    evaluated = date(2026, 10, 1)
    engine = create_engine('sqlite://')
    SharedBackgroundContext.__table__.create(engine)
    calls = []
    with Session(engine) as session:
        for attempt in range(2):
            result = update_background_chip_context_cache(
                session, run_date=evaluated, market='TW',
                provider=_credit_status_provider(calls=calls),
                symbols=['7822.TW'], context_types=['full_margin'],
                require_same_day_fresh=True, reuse_same_day_fresh=True,
            )
            session.flush()
            assert result['status'] == 'completed'
            assert result['not_applicable_symbols'] == ['7822.TW']
            assert result['missing_symbols'] == []
            assert result['records_written'] == (1 if attempt == 0 else 0)
    assert [params for url, params in calls if url == CREDIT_STATUS_URL] == [
        {'response': 'json', 'date': '20261001'}]
    payload = list(_credit_status_provider().fetch(symbols=['7822.TW'],
        context_types=['full_margin'], run_date=evaluated, market='TW'))[0]
    assert payload.source['dataset'] == 'TWSE_TWT93U'
    assert payload.payload['eligibility']['reason'] == 'official_credit_trading_ineligible'
    assert payload.payload['eligibility']['credit_status'] == 'Y'
    assert payload.payload['eligibility']['report_date'] == evaluated.isoformat()
    assert payload.freshness == 'fresh'
    assert payload.missing_reason is None
    projected = _project_margin_context(vars(payload), technical={})
    assert margin_evidence_is_complete(projected, record_date=evaluated, symbol='7822.TW')
    assert not margin_evidence_is_complete(projected, record_date=date(2026, 10, 2))
    assert not margin_evidence_is_complete(projected, symbol='7822.TWO')
    assert 'margin_balance' not in projected
    assert 'margin_delta_pct' not in projected
    assert 'margin_to_volume' not in projected
    invalid = deepcopy(projected)
    invalid['eligibility']['report_date'] = '2026-09-30'
    assert not margin_evidence_is_complete(invalid)
    invalid = deepcopy(projected)
    invalid['eligibility']['credit_status'] = 'X'
    assert not margin_evidence_is_complete(invalid)


@pytest.mark.parametrize('change', [
    {'stat': 'error'}, {'date': '20260930'}, {'date': '20261002'}, {'date': None},
    {'fields': ['代號', '備註', '備註']}, {'data': []}, {'data': [['7823', '其他', 'Y']]},
    {'data': [['7822', '倍利科', 'X']]}, {'data': [['7822', '倍利科', '']]},
    {'data': [['7822', '倍利科', 'UNKNOWN Y']]}, {'data': [['7822']]},
    {'data': [['7822', '倍利科', 'Y'], ['7822', '倍利科', '']]}, {'notes': []},
])
def test_credit_ineligibility_requires_exact_date_symbol_schema_and_status(change):
    report = _credit_status_report() | change
    result = list(_credit_status_provider(report).fetch(symbols=['7822.TW'],
        context_types=['full_margin'], run_date=date(2026, 10, 1), market='TW'))[0]
    assert result.missing_reason == 'official_no_data'


def test_credit_status_source_failure_does_not_grant_ineligibility():
    result = list(_credit_status_provider(RuntimeError('unavailable')).fetch(
        symbols=['7822.TW'], context_types=['full_margin'],
        run_date=date(2026, 10, 1), market='TW'))[0]
    assert result.missing_reason == 'official_no_data'


def test_credit_status_cannot_mask_missing_same_day_margin_market_report():
    calls = []
    result = list(_credit_status_provider(calls=calls, margin_date='20260930').fetch(
        symbols=['7822.TW'], context_types=['full_margin'],
        run_date=date(2026, 10, 1), market='TW'))[0]
    assert result.missing_reason == 'official_no_data'
    assert not any(url == CREDIT_STATUS_URL for url, _ in calls)


def test_actual_margin_precedes_credit_ineligibility():
    calls = []
    result = list(_credit_status_provider(include_margin=True, calls=calls).fetch(
        symbols=['7822.TW'], context_types=['full_margin'],
        run_date=date(2026, 10, 1), market='TW'))[0]
    assert result.payload['latest_margin_balance'] == 1000
    assert 'applicability' not in result.payload
    assert not any(url == CREDIT_STATUS_URL for url, _ in calls)


def test_tw_actual_margin_precedes_public_offering_evidence():
    calls = []
    result = list(_tw_public_offering_provider(include_margin=True, calls=calls).fetch(
        symbols=['7822.TW'], context_types=['full_margin'],
        run_date=date(2026, 9, 29), market='TW'))[0]
    assert result.payload['latest_margin_balance'] == 1000
    assert LISTING_URL not in calls
    assert OTC_LISTING_URL not in calls


def _otc_provider(*, listing_type='初上櫃', listed='115/04/22', cancelled='',
                  include_margin=False, listing_error=False, calls=None):
    from tests.test_official_background_context import _tpex_margin_payload

    def get(url, *, params, **kwargs):
        if calls is not None:
            calls.append((url, params))
        if url == OTC_LISTING_URL:
            if listing_error:
                raise RuntimeError('listing unavailable')
            return _FakeResponse({'stat': 'OK', 'date': int(params['yy']),
                'fields': ['證券代號', '發行市場', '撥券日期(上市、上櫃日期)', '取消公開抽籤 '],
                'data': [['7828', listing_type, listed, cancelled]]})
        return _FakeResponse(_tpex_margin_payload(params['date'].replace('/', ''), [[
            '7828' if include_margin else '6488', '測試',
            '900', '0', '0', '0', '1000', '0', '0', '0',
            '40', '0', '0', '0', '50', '0', '0', '0', '0',
        ]]))
    return OfficialBackgroundChipContextProvider(request_get=get,
        lookback_trading_days=1, max_lookback_calendar_days=1)


def test_otc_initial_listing_exempts_margin_with_official_evidence():
    result = list(_otc_provider().fetch(symbols=['7828.TWO'],
        context_types=['full_margin'], run_date=date(2026, 9, 15), market='TW'))[0]
    assert result.freshness == 'fresh'
    assert result.payload['applicability'] == 'not_applicable'
    assert result.payload['eligibility']['listing_date'] == '2026-04-22'
    assert result.payload['eligibility']['source_url'] == OTC_LISTING_URL
    assert result.source['market'] == 'TWO'
    assert same_day_background_context_is_reusable(result, run_date=date(2026, 9, 15))
    projected = _project_margin_context(vars(result), technical={})
    assert margin_evidence_is_complete(projected, record_date=date(2026, 9, 15), symbol='7828.TWO')
    assert 'margin_balance' not in projected


@pytest.mark.parametrize('kwargs', [
    {'listing_type': '上市轉上櫃'}, {'listing_type': '上櫃增資'},
    {'listed': '115/03/15'}, {'listed': '115/09/16'}, {'listed': 'bad'},
    {'cancelled': '取消'}, {'listing_error': True},
])
def test_otc_unproven_listing_remains_missing(kwargs):
    result = list(_otc_provider(**kwargs).fetch(symbols=['7828.TWO'],
        context_types=['full_margin'], run_date=date(2026, 9, 15), market='TW'))[0]
    assert result.missing_reason == 'official_no_data'


def test_otc_actual_margin_precedes_listing_evidence():
    calls = []
    result = list(_otc_provider(include_margin=True, calls=calls).fetch(
        symbols=['7828.TWO'], context_types=['full_margin'],
        run_date=date(2026, 9, 15), market='TW'))[0]
    assert result.payload['latest_margin_balance'] == 1000
    assert not any(url == OTC_LISTING_URL for url, _ in calls)


@pytest.mark.parametrize('evaluated,expected', [
    (date(2026, 10, 21), True), (date(2026, 10, 22), False),
])
def test_otc_six_month_boundary(evaluated, expected):
    result = list(_otc_provider().fetch(symbols=['7828.TWO'], context_types=['full_margin'],
        run_date=evaluated, market='TW'))[0]
    assert (result.payload.get('applicability') == 'not_applicable') is expected


def test_otc_early_year_checks_prior_year_and_reuses_verified_context():
    calls = []
    evaluated = date(2026, 3, 15)
    engine = create_engine('sqlite://')
    SharedBackgroundContext.__table__.create(engine)
    with Session(engine) as session:
        for attempt in range(2):
            result = update_background_chip_context_cache(
                session, run_date=evaluated, market='TW',
                provider=_otc_provider(listed='114/12/22', calls=calls),
                symbols=['7828.TWO'], context_types=['full_margin'],
                require_same_day_fresh=True, reuse_same_day_fresh=True,
            )
            session.flush()
            assert result['status'] == 'completed'
            assert result['not_applicable_symbols'] == ['7828.TWO']
            assert result['records_written'] == (1 if attempt == 0 else 0)
    assert [params['yy'] for url, params in calls if url == OTC_LISTING_URL] == ['2026', '2025']
