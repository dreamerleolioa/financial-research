from datetime import date, timedelta

import pytest

from test_active_etf_holdings import etf_db_session, etf_client, _fund, _snapshot, FakeProvider
from ai_stock_sentinel.active_etf_holdings.range_service import get_active_etf_range_response
from ai_stock_sentinel.active_etf_holdings.service import refresh_active_etf_holdings, get_active_etf_daily_response


def seed(db, day, rows, fund=None, funds=None):
    snapshot = _snapshot(day, rows, fund=fund)
    refresh_active_etf_holdings(db, provider=FakeProvider({snapshot.fund.fund_code: snapshot}, funds=funds))


def test_round_trip_and_weights_do_not_treat_first_day_as_purchase(etf_db_session):
    for day, shares, weight in [(1, 100, '10'), (2, 150, '12'), (3, 100, '11')]:
        seed(etf_db_session, date(2026, 8, day), [('2330.TW', '台積電', shares, weight)])
    response = get_active_etf_range_response(etf_db_session, start_date=date(2026, 8, 1), end_date=date(2026, 8, 3), symbol='2330.TW')
    position = response.stocks[0].funds[0]
    assert position.net_share_delta == 0
    assert position.increase_days == position.decrease_days == 1
    assert position.added_days == position.removed_days == 0
    assert position.weight_delta_pct_points == 1
    assert [point.share_delta for point in response.timeline] == [None, 50, -50]
    assert response.timeline[0].previous_date is None
    assert response.timeline[2].previous_date == date(2026, 8, 2)
    assert all(point.source_url.startswith('https://www.moneydj.com/') for point in response.timeline)


def test_outside_baselines_missing_and_one_snapshot(etf_db_session):
    a, b, c = _fund(), _fund('00980A'), _fund('00981A')
    funds = [a, b, c]
    for fund, day, shares in [(a, 1, 10), (a, 2, 100), (a, 4, 120), (b, 3, 80), (c, 1, 50)]:
        seed(etf_db_session, date(2026, 8, day), [('2330.TW', '台積電', shares, '10')], fund, funds)
    response = get_active_etf_range_response(etf_db_session, start_date=date(2026, 8, 2), end_date=date(2026, 8, 4))
    positions = {item.fund_code: item for item in response.stocks[0].funds}
    assert positions[a.fund_code].net_share_delta == 20
    assert positions[b.fund_code].net_share_delta is None
    assert positions[b.fund_code].increase_days == 0
    coverage = {item.fund_code: item for item in response.funds}
    assert coverage[a.fund_code].missing_dates == [date(2026, 8, 3)]
    assert coverage[c.fund_code].snapshot_count == 0
    assert response.timeline == []


def test_added_removed_and_unchanged_holdings(etf_db_session):
    for day, rows in [(1, [('A.US', 'A', 100, '10')]), (2, [('A.US', 'A', 100, '11'), ('B.US', 'B', 20, '2')]), (3, [('A.US', 'A', 100, '12')])]:
        seed(etf_db_session, date(2026, 8, day), rows)
    response = get_active_etf_range_response(etf_db_session)
    stocks = {stock.symbol: stock for stock in response.stocks}
    assert stocks['A.US'].funds[0].net_share_delta == 0
    assert stocks['A.US'].funds[0].weight_delta_pct_points == 2
    assert stocks['B.US'].funds[0].added_days == stocks['B.US'].funds[0].removed_days == 1
    assert stocks['B.US'].funds[0].first_shares == stocks['B.US'].funds[0].last_shares == 0
    assert get_active_etf_range_response(etf_db_session, symbol='UNKNOWN').stocks == []


def test_empty_window_is_not_no_history(etf_db_session):
    assert get_active_etf_range_response(etf_db_session) is None
    seed(etf_db_session, date(2026, 8, 1), [('A.US', 'A', 100, '10')])
    response = get_active_etf_range_response(etf_db_session, start_date=date(2026, 7, 1), end_date=date(2026, 7, 2))
    assert response.stocks == response.observed_dates == []
    assert response.funds[0].snapshot_count == 0


@pytest.mark.parametrize('query', ['start_date=2026-08-30&end_date=2026-08-01', 'start_date=2024-01-01&end_date=2026-08-01', 'start_date=bad', 'symbol=%3Cscript%3E'])
def test_endpoint_validates_queries(etf_client, query):
    etf_client.post('/internal/active-etf-holdings/refresh', json={})
    assert etf_client.get('/active-etf-holdings/range?' + query).status_code == 422


def test_endpoint_serializes_and_requires_auth(etf_client):
    from ai_stock_sentinel import api
    from ai_stock_sentinel.auth.dependencies import get_current_user
    assert etf_client.get('/active-etf-holdings/range').status_code == 404
    etf_client.post('/internal/active-etf-holdings/refresh', json={})
    response = etf_client.get('/active-etf-holdings/range?symbol=2330.tw')
    assert response.status_code == 200
    assert response.json()['stocks'][0]['funds'][0]['net_share_delta'] is None
    assert response.json()['timeline'][0]['weight_pct'] == '10.0000'
    override = api.app.dependency_overrides.pop(get_current_user)
    try:
        assert etf_client.get('/active-etf-holdings/range').status_code in (401, 403)
    finally:
        api.app.dependency_overrides[get_current_user] = override


def test_daily_can_drill_into_date_older_than_menu_limit(etf_db_session):
    start = date(2026, 1, 1)
    for offset in range(62):
        seed(etf_db_session, start + timedelta(days=offset), [('A.US', 'A', offset + 1, '10')])
    response = get_active_etf_daily_response(etf_db_session, data_date=start + timedelta(days=1))
    assert response.data_date == start + timedelta(days=1)
    assert response.data_date in response.available_dates
    assert response.changes[0].share_delta == 1


def test_default_start_handles_earliest_valid_end_date(etf_client):
    etf_client.post('/internal/active-etf-holdings/refresh', json={})
    response = etf_client.get('/active-etf-holdings/range?end_date=0001-01-01')
    assert response.status_code == 200
    assert response.json()['start_date'] == '0001-01-01'
    assert response.json()['stocks'] == []
