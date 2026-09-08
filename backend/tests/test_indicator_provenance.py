from datetime import date, timedelta

import pytest

from ai_stock_sentinel.technical.profile import build_technical_profile_from_snapshot


def source():
    dates = [(date(2026, 6, 1) + timedelta(days=n)).isoformat() for n in range(65)]
    closes = [2400 + n / 10 for n in range(65)]
    return dict(recent_closes=closes, recent_highs=[c + 1 for c in closes],
                recent_lows=[c - 1 for c in closes], recent_volumes=[1000.] * 65,
                recent_close_dates=dates, recent_high_dates=dates,
                recent_low_dates=dates, recent_volume_dates=dates,
                current_price=2460, fetched_at='2026-08-05T02:00:00+00:00')


def test_delayed_history_uses_one_breakout_window():
    s = source()
    s['recent_highs'][-1] = 2450
    result = build_technical_profile_from_snapshot(s, is_final=False)['technical_indicators']
    assert result['prior_high_20d'] == 2450
    assert result['donchian_upper'] == result['prior_high_20d']
    assert result['donchian_position'] == 'breakout_up'


def test_context_does_not_pretend_latest_quote_was_inserted_into_history():
    result = build_technical_profile_from_snapshot(source(), is_final=False)['technical_indicators']
    context = result['input_context']
    assert context['indicator_mode'] == 'completed_daily'
    assert context['indicator_close'] == 2406.4
    assert context['history_completed_through'] == '2026-08-04'
    assert context['indicator_close_confirmed'] is True
    assert result['ma20'] == pytest.approx(result['bollinger_mid'])
    assert result['kd_previous_k'] is not None
    assert result['dmi_plus'] is not None


def test_missing_highs_does_not_create_updated_hlc_indicators():
    s = source()
    s['recent_highs'] = []
    result = build_technical_profile_from_snapshot(s, is_final=False)['technical_indicators']
    assert result['atr'] is None
    assert result['kd_k'] is None
    assert result['donchian_upper'] is None
    assert result['input_context']['hlc_status'] == 'unavailable'


def test_unrounded_consistency_checks_detect_bad_outputs():
    from ai_stock_sentinel.technical.consistency import indicator_consistency_issues
    raw = build_technical_profile_from_snapshot(source(), is_final=False)['technical_indicators']
    assert raw['input_context']['consistency_issues'] == []
    assert indicator_consistency_issues({**raw, 'macd_hist': raw['macd_hist'] + .001})
    assert indicator_consistency_issues({**raw, 'ma20': raw['ma20'] + 1.75})
    assert indicator_consistency_issues({**raw, 'donchian_upper': raw['donchian_upper'] + 10})


def test_storage_does_not_fill_missing_high_low_with_close():
    from ai_stock_sentinel.analysis.application.analysis_cache import normalize_raw_technical_for_storage
    raw = normalize_raw_technical_for_storage({'current_price': 110, 'recent_closes': [100, 105]})
    assert raw['ohlcv']['close'] == 105
    assert raw['ohlcv']['open'] is None
    assert raw['ohlcv']['high'] is None
    assert raw['ohlcv']['low'] is None


def test_persistence_uses_the_same_date_alignment_guard():
    from ai_stock_sentinel.analysis.application.response_builder import extract_indicators
    s = source()
    s['recent_high_dates'] = ['2020-01-01'] * len(s['recent_closes'])
    raw = extract_indicators({'snapshot': s}, is_final=False)
    assert raw['input_context']['hlc_status'] == 'unavailable'
    for key in ('kd_k', 'kd_signal', 'adx', 'atr', 'mfi', 'donchian_upper'):
        assert raw[key] is None


def test_old_donchian_profile_is_recomputed_as_a_whole():
    from ai_stock_sentinel.analysis.application.response_builder import _hydrate_cached_technical_payload
    from ai_stock_sentinel.analysis.schemas import AnalyzeResponse
    from ai_stock_sentinel.technical.profile import TECHNICAL_LAYER_VERSION
    response = AnalyzeResponse(technical_indicators={'donchian_upper': 1}, technical_profile={
        'version': 'technical-layer-v4', 'secondary_evidence': {'donchian': {'state': 'range_mid'}},
    })
    _hydrate_cached_technical_payload(response, snapshot=source(), is_final=False)
    assert response.technical_profile['version'] == TECHNICAL_LAYER_VERSION
    assert response.technical_indicators.donchian_upper == response.technical_profile['display_only']['donchian_upper']
    assert response.technical_profile['secondary_evidence']['donchian']['state'] != 'range_mid'


def test_history_volume_fallback_keeps_its_own_date_and_full_day_status():
    s = source()
    s['volume_source'] = 'history_fallback'
    raw = build_technical_profile_from_snapshot(s, is_final=False)['technical_indicators']
    assert raw['input_context']['volume_data_date'] == '2026-08-04'
    assert raw['input_context']['volume_state'] == 'full_day'


def test_storage_does_not_mix_new_quote_with_previous_daily_bar():
    from ai_stock_sentinel.analysis.application.analysis_cache import normalize_raw_technical_for_storage
    s = source()
    s['day_open'] = 2460
    s['recent_highs'] = s['recent_highs'][:-1]
    result = normalize_raw_technical_for_storage(s)['ohlcv']
    assert result['close'] == 2406.4
    assert result['open'] is None
    assert result['high'] is None


@pytest.mark.parametrize('intraday', [False, True])
def test_comparison_evidence_uses_each_indicators_actual_sequence(intraday):
    from ai_stock_sentinel.technical.metrics import macd
    s = source()
    if intraday:
        s['fetched_at'] = '2026-08-04T02:00:00+00:00'
    raw = build_technical_profile_from_snapshot(s, is_final=False)['technical_indicators']
    completed = s['recent_closes'][:-1] if intraday else s['recent_closes']
    hist = macd(completed)['macd_hist']
    previous = macd(completed[:-3])['macd_hist']
    assert raw['macd_trend_hist'] == hist
    assert raw['macd_hist_3d_previous'] == previous
    assert raw['macd_hist_change_3d'] == pytest.approx(hist - previous)
    assert raw['macd_hist_slope_pct_3d'] == round((hist - previous) / completed[-1] * 100, 4)
    assert raw['macd_trend_price'] == completed[-1]
    assert raw['macd_trend_previous_date'] == s['recent_close_dates'][len(completed) - 4]
    assert raw['obv_window_previous_date'] == s['recent_close_dates'][-6]
    assert raw['obv_window_previous_close'] == s['recent_closes'][-6]
    assert raw['obv_window_close'] == s['recent_closes'][-1]
    assert raw['obv_window_change'] == 5000
    assert raw['obv'] - raw['obv_window_previous'] == raw['obv_window_change']
    assert raw['obv_window_price_change_pct'] == pytest.approx((s['recent_closes'][-1] / s['recent_closes'][-6] - 1) * 100)
