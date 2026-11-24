import pytest
from ai_stock_sentinel.daily_radar.evidence_batches import evidence_batches


def test_batches_share_evidence_and_keep_window_identities_separate():
    def gap(cid, symbol, basis, state, reason):
        return dict(
            candidate_id=cid,
            window_days=5,
            symbol=symbol,
            return_basis=basis,
            signal_date='2026-10-01',
            state=state,
            reason=reason,
        )

    gaps = [
        gap(i, '2330.TW', 'signal_close', 'missing_diagnostic', 'observation_diagnostic_incomplete')
        for i in (3, 1, 2)
    ]
    gaps += [
        gap(1, '2330.TW', 'next_open', 'retryable', 'terminal_evidence_unverified'),
        gap(4, '2615.TW', 'signal_close', 'missing_outcome', 'validation_outcome_missing'),
    ]
    batches = list(evidence_batches(reversed(gaps), limit=2))
    assert batches[0][0] == 'due'
    assert sorted((kind, tuple(g['candidate_id'] for g in batch)) for kind, batch in batches) == [
        ('diagnostic', (1, 2)),
        ('diagnostic', (3,)),
        ('due', (4,)),
        ('next_open_repair', (1,)),
    ]
    with pytest.raises(ValueError):
        list(evidence_batches(gaps, limit=0))


@pytest.fixture
def processor():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / 'scripts/process_daily_radar_backlog.py'
    spec = importlib.util.spec_from_file_location('local_backlog', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_runner_rejects_proxy_before_connecting(processor, tmp_path, monkeypatch):
    monkeypatch.setattr(processor, 'create_engine', lambda *a: pytest.fail('reached database connection'))
    with pytest.raises(FileNotFoundError):
        processor.local_engine(tmp_path, 5432, 'copy', 'test')


def test_frozen_market_price_failure_can_recover_on_next_run(processor, tmp_path, monkeypatch):
    from datetime import date

    attempts = []

    class Prices:
        def fetch(self, symbols, **kwargs):
            attempts.append(symbols)
            return {symbols[0]: [{'date': '2026-10-01', 'close': 73}]} if len(attempts) > 1 else {}

    monkeypatch.setattr(processor, 'YFinanceForwardPriceProvider', Prices)
    args = (tmp_path, 'signal_close', {'2330.TW': '2026-10-01'}, date(2026, 10, 9))
    prices = processor.MarketPrices(*args)
    assert prices.receipt('2330.TW')['error'] == prices.receipt('2330.TW')['error'] == 'no_price_rows'
    assert processor.MarketPrices(*args).receipt('2330.TW')['rows'][0]['close'] == 73
    assert len(attempts) == 2 and len(list((tmp_path / 'market-captures').glob('*.gz'))) == 2
    path = tmp_path / 'prices' / 'signal_close' / '2330.TW.json.gz'
    receipt = processor.load(path)
    receipt['start'] = '2026-10-05'
    processor.save(path, receipt)
    with pytest.raises(ValueError, match='price_capture_identity_changed'):
        prices.receipt('2330.TW')


def test_local_processor_rejects_stale_inventory_and_distinct_report_queries(processor, monkeypatch):
    from types import SimpleNamespace
    from ai_stock_sentinel.radar_gap_contract import GAP_FIELDS

    gap = {k: 'fixture' for k in GAP_FIELDS} | {'candidate_id': 1, 'window_days': 5}
    monkeypatch.setattr(processor, 'inventory_items', lambda *a, **kw: [dict(gap, gap_revision='other')])
    with pytest.raises(ValueError, match='batch_gap_changed'):
        processor.require_current(None, [gap], SimpleNamespace(through='2026-11-19'))
    url = 'https://www.twse.com.tw/prices'
    assert processor.same_report(url + '?b=2&a=1', url, {'a': 1, 'b': 2})
    assert not processor.same_report(url + '?b=2&a=1&a=9', url, {'a': 1, 'b': 2})


def test_due_batches_prioritize_cutoff_and_quarantine_targetless_next_open():
    def gap(cid, symbol, state, reason, target):
        return dict(
            candidate_id=cid,
            symbol=symbol,
            return_basis='next_open',
            signal_date='2026-09-01',
            window_days=5,
            state=state,
            reason=reason,
            expected_target_date=target,
        )

    gaps = [
        gap(1, '1111.TW', 'missing_outcome', 'validation_outcome_missing', '2026-09-08'),
        gap(2, '3333.TW', 'missing_outcome', 'validation_outcome_missing', '2026-11-19'),
        gap(3, '3333.TW', 'invalid_window', 'non_trading_signal_date', None),
    ]
    got = [
        (kind, [r['candidate_id'] for r in batch])
        for kind, batch in evidence_batches(gaps, limit=8, as_of_date='2026-11-19')
    ]
    assert got == [('due', [2]), ('due', [1]), ('needs_review', [3])]


def test_incomplete_yahoo_retry_preserves_both_captures(processor, tmp_path, monkeypatch):
    from datetime import date

    days = ['2026-10-01', '2026-10-02']
    calls = []

    class Prices:
        def fetch(self, symbols, **kwargs):
            calls.append(1)
            return {symbols[0]: [{'date': d, 'close': 73} for d in days[: len(calls)]]}

    monkeypatch.setattr(processor, 'YFinanceForwardPriceProvider', Prices)
    args = (tmp_path, 'signal_close', {'2330.TW': days[0]}, date(2026, 10, 2))
    first = processor.MarketPrices(*args, sessions=days)
    assert len(first.receipt('2330.TW')['rows']) == 1
    retry = processor.MarketPrices(*args, sessions=days, refresh=True)
    assert len(retry.receipt('2330.TW')['rows']) == len(retry.receipt('2330.TW')['rows']) == 2
    assert len(calls) == 2 and len(list((tmp_path / 'market-captures').glob('*.gz'))) == 2


def test_retry_reports_refresh_once_and_preserve_original(processor, tmp_path, monkeypatch):
    from datetime import date
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider

    url = 'https://www.twse.com.tw'
    kwargs = {'code': '2330'}
    calls = []

    def fetch(provider, origin, path, params):
        calls.append(1)
        data = {'rows': list(calls)}
        return data, {
            'source_url': url + '/history?code=2330',
            'captured_at': '2026-01-09T00:00:00+00:00',
            'snapshot_sha256': processor.fingerprint(data),
        }

    monkeypatch.setattr(RecoveringResearchPriceProvider, '_capture_report', fetch)
    monkeypatch.setattr(processor.BatchResearchPrices, 'archived_report', lambda *a: None)
    args = (tmp_path, 'next_open', {'2330.TW': '2026-01-01'}, date(2026, 1, 6))
    first = processor.BatchResearchPrices(processor.MarketPrices(*args), None)
    assert len(first._capture_report(url, '/history', kwargs)[0]['rows']) == 1
    shared = processor.MarketPrices(*args, refresh=True)
    for _ in range(2):
        fresh = processor.BatchResearchPrices(shared, None)
        assert len(fresh._capture_report(url, '/history', kwargs)[0]['rows']) == 2
    assert len(calls) == 2 and len(list((tmp_path / 'exchange-captures').glob('*.gz'))) == 2


def test_interrupted_and_preserved_windows_remain_retryable(processor, tmp_path):
    import json

    file = tmp_path / 'batches.jsonl'
    rows = [
        {'started': True, 'windows': [[1, 5, 'sig'], [2, 5, 'sig']]},
        {
            'windows': [[2, 5, 'sig']],
            'results': [
                {
                    'window': [2, 5, 'sig'],
                    'status': 'retryable',
                    'reason': 'conflicting_price_records',
                    'proposed_status': 'validated',
                }
            ],
        },
    ]
    file.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert processor.retry_keys(file, 'conflicting_price_records') == {(1, 5, 'sig'), (2, 5, 'sig')}
    assert processor.retry_keys(file, 'other') == {(1, 5, 'sig')}


def test_due_journal_uses_persisted_outcome_and_verifiable_receipt(processor, monkeypatch, tmp_path):
    from datetime import date
    from types import SimpleNamespace
    from sqlalchemy import create_engine

    gap = {'candidate_id': 9, 'window_days': 5, 'validation_version': 'v', 'return_basis': 'signal_close'}
    saved = dict(gap, state='retryable', reason='conflicting_price_records')
    proposed = dict(gap, status='validated', skip_reason=None, outcome={})
    acceptance = SimpleNamespace(
        candidate_revisions={9: 'a', 88: 'b', 101: 'c'}, result_revisions={processor.key(gap): 'old'}
    )
    monkeypatch.setattr(processor, 'prepare_batch', lambda *a: ([proposed], acceptance))
    monkeypatch.setattr(processor, 'require_current', lambda *a: None)
    monkeypatch.setattr(processor, 'reserve_validation_commit', lambda *a, **kw: None)
    monkeypatch.setattr(processor, 'accept_validation_results', lambda *a, **kw: {'records_written': 0})
    monkeypatch.setattr(processor, 'inventory_items', lambda *a, **kw: [saved])
    engine = create_engine('sqlite://')
    summary = processor.due_batch(
        engine, [gap], SimpleNamespace(through=date(2026, 11, 19), payload=lambda: {}), None, None, tmp_path
    )
    assert summary['results'][0] == {
        'window': processor.key(gap),
        'status': 'retryable',
        'reason': 'conflicting_price_records',
        'proposed_status': 'validated',
        'proposed_reason': None,
    }
    (receipt,) = (tmp_path / 'evaluations').glob('*.gz')
    assert processor.fingerprint(processor.load(receipt)) == receipt.name.split('.')[0]


def test_journal_recovers_only_interrupted_trailing_append(processor, tmp_path):
    import json

    path = tmp_path / 'run.jsonl'
    start = {'started': True, 'windows': [[1, 5, 'sig']]}
    path.write_bytes((json.dumps(start) + '\n').encode() + b'{"wind')
    with processor.open_journal(path) as out:
        assert processor.retry_keys(path, 'any') == {(1, 5, 'sig')}
        out.write(b'{"windows":[[1,5,"sig"]],"error":"resolved_elsewhere"}\n')
    assert processor.retry_keys(path, 'any') == set()
    assert next(tmp_path.glob('*.partial-*')).read_bytes() == b'{"wind'
    path.write_bytes(b'bad\n' + path.read_bytes())
    with pytest.raises(json.JSONDecodeError):
        processor.retry_keys(path, 'any')


def test_same_day_closed_evidence_and_fresh_batch_budget(processor, tmp_path, monkeypatch):
    from datetime import date
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider

    shared = processor.MarketPrices(tmp_path, 'next_open', {'2330.TW': '2026-01-01'}, date(2026, 1, 6))
    calls = []

    def fetch(provider, origin, path, params):
        calls.append(1)
        if len(calls) == 1:
            provider.budget_limit = 'request_limit'
            raise TimeoutError('research_recovery_budget_exhausted')
        return {}, {
            'source_url': origin + path + '?code=2330',
            'captured_at': '2026-01-06T15:00:00+08:00',
            'snapshot_sha256': processor.fingerprint({}),
        }

    monkeypatch.setattr(RecoveringResearchPriceProvider, '_capture_report', fetch)
    monkeypatch.setattr(processor.BatchResearchPrices, 'archived_report', lambda *a: None)
    get = lambda: processor.BatchResearchPrices(shared, None)._capture_report(
        'https://www.twse.com.tw', '/history', {'code': '2330'}
    )
    with pytest.raises(TimeoutError):
        get()
    assert get()[0] == get()[0] == {} and len(calls) == 2


def test_conflict_retry_refreshes_complete_quotes_once(processor, tmp_path, monkeypatch):
    from datetime import date

    prices = []

    class Source:
        def fetch(self, symbols, **kwargs):
            prices.append(1)
            return {symbols[0]: [{'date': '2026-01-06', 'close': len(prices)}]}

    monkeypatch.setattr(processor, 'YFinanceForwardPriceProvider', Source)
    args = (tmp_path, 'signal_close', {'2330.TW': '2026-01-06'}, date(2026, 1, 6))
    assert processor.MarketPrices(*args).receipt('2330.TW')['rows'][0]['close'] == 1
    retry = processor.MarketPrices(*args, sessions=['2026-01-06'], refresh=True, refresh_quotes=True)
    assert (
        retry.receipt('2330.TW')['rows']
        == retry.receipt('2330.TW')['rows']
        == [{'date': '2026-01-06', 'close': 2}]
    )


def test_local_adjusted_prices_keep_the_receipt_used_for_the_window(processor, tmp_path):
    from datetime import date

    symbol = '5434.TW'
    receipt = {'symbol': symbol, 'basis': 'signal_close', 'source': 'yfinance', 'start': '2026-11-03',
               'end': '2026-11-17', 'captured_at': '2026-11-19T09:00:00+00:00', 'error': None,
               'rows': [{'date': '2026-11-03', 'open': 9, 'close': 10}]}
    receipt['sha256'] = processor.fingerprint(receipt)
    processor.save(tmp_path / 'prices/signal_close' / (symbol + '.json.gz'), receipt)
    prices = processor.MarketPrices(tmp_path, 'signal_close', {symbol: receipt['start']}, date(2026, 11, 17))
    [observed] = prices.fetch([symbol], start_date=date(2026, 11, 3), end_date=date(2026, 11, 17))[symbol]
    assert observed['source_capture']['snapshot_sha256'] == receipt['sha256']
    assert observed['source_capture']['captured_at'] == receipt['captured_at']
    assert 'source_capture' not in prices.receipt(symbol)['rows'][0]


def test_local_reuses_full_exchange_report_without_relabelling_dates(processor, tmp_path, monkeypatch):
    from datetime import date, datetime, timezone
    from urllib.parse import urlencode
    from ai_stock_sentinel.calibration import twse_rights_recovery as rights

    url = rights.TWSE + rights.EX_RIGHTS
    initial = dict(response='json', startDate='20260701', endDate='20260731')
    wanted = initial | {'startDate': '20260702', 'endDate': '20260710'}
    payload = {'stat': 'OK', 'params': initial, 'fields': ['股票代號','資料日期'],
               'data': [['2330','115年07月03日'], ['2330','115年07月24日']]}
    proof = {'source_url': url + '?' + urlencode(initial), 'captured_at': datetime.now(timezone.utc).isoformat(),
             'snapshot_sha256': processor.fingerprint(payload)}
    request = {'url': url, 'params': initial}
    processor.save(tmp_path / 'exchange-reports' / (processor.fingerprint(request) + '.json.gz'),
                   dict(request=request, proof=proof, payload=payload))
    market = processor.MarketPrices(tmp_path, 'next_open', {}, date(2026, 7, 31))
    provider = processor.BatchResearchPrices(market, None)
    monkeypatch.setattr(provider, 'archived_report', lambda request: None)
    got, source = provider.capture_twse_report(rights.EX_RIGHTS, wanted)
    assert got == payload and source['source_url'] == proof['source_url']
    read = lambda obj, ref: rights.dated_market_rows(obj, wanted, '資料日期', [], path=rights.EX_RIGHTS, source=ref)
    assert [d for d,_ in read(got, source)] == [date(2026,7,3)]
    for invalid in (source | {'snapshot_sha256': '0'*64},
                    source | {'source_url': source['source_url']+'&stockNo=2330'},
                    source | {'source_url': source['source_url'].replace('TWT49U', 'TWTB8U')}):
        with pytest.raises(ValueError):
            read(got, invalid)
    with pytest.raises(ValueError):
        read(got | {'data': []}, source)
    filtered = got | {'params': initial | {'stockNo': '2330'}}
    with pytest.raises(ValueError):
        read(filtered, source | {'snapshot_sha256': processor.fingerprint(filtered)})


def test_overlapping_reports_ignore_only_later_fund_filings(processor, tmp_path):
    from datetime import date, datetime, timezone
    from urllib.parse import urlencode
    from ai_stock_sentinel.calibration import twse_rights_recovery as rights

    url = rights.TWSE + rights.EX_RIGHTS
    fields = ['股票代號', '資料日期', '除權息前收盤價', '最近一次申報資料 季別/日期', '最近一次申報每股 (單位)淨值']
    def store(end, price, quarter, nav):
        params = dict(response='json', startDate='20260701', endDate=end)
        request = dict(url=url, params=params)
        payload = {'stat': 'OK', 'params': params, 'fields': fields,
                   'data': [['2330', '115年07月03日', price, quarter, nav]]}
        proof = {'source_url': url + '?' + urlencode(params), 'captured_at': datetime.now(timezone.utc).isoformat(),
                 'snapshot_sha256': processor.fingerprint(payload)}
        processor.save(tmp_path / 'exchange-reports' / (processor.fingerprint(request) + '.json.gz'),
                       dict(request=request, proof=proof, payload=payload))

    store('20260730', '90', 'Q2', '30')
    store('20260731', '90', 'Q3', '32')
    request = dict(url=url, params=dict(response='json', startDate='20260702', endDate='20260710'))
    def match():
        return processor.MarketPrices(tmp_path, 'next_open', {}, date(2026, 7, 31)).covering_report(request)
    payload, proof = match()
    assert payload['data'][0][-2:] == ['Q2', '30']
    assert proof['snapshot_sha256'] == processor.fingerprint(payload)
    store('20260731', '91', 'Q3', '32')
    with pytest.raises(ValueError, match='conflicting_exchange_range_reports'):
        match()


def test_covering_cache_isolated_from_corruption_and_replaced_after_refresh(processor, tmp_path, monkeypatch):
    from datetime import date, datetime, timezone
    from urllib.parse import urlencode
    from ai_stock_sentinel.calibration import twse_rights_recovery as t

    url = t.TWSE + t.EX_RIGHTS
    base = dict(response='json', startDate='20260701', endDate='20260731')
    narrow = base | dict(startDate='20260702', endDate='20260707')
    folder = tmp_path / 'exchange-reports'
    folder.mkdir()
    (folder / ('0'*64 + '.json.gz')).write_bytes(b'broken')
    market = processor.MarketPrices(tmp_path, 'next_open', {}, date(2026,7,31))
    provider = processor.BatchResearchPrices(market, None)
    monkeypatch.setattr(provider, 'archived_report', lambda request: None)
    def response(params, stock):
        payload = {'stat': 'OK', 'params': params, 'fields': ['股票代號','資料日期'],
                   'data': [[stock,'115年07月03日']]}
        return payload, {'source_url': url+'?'+urlencode(params), 'captured_at': datetime.now(timezone.utc).isoformat(),
                         'snapshot_sha256': processor.fingerprint(payload)}
    answers = iter(['1808', '5434', None, '9999'])
    def capture(prov, origin, path, params):
        stock = next(answers)
        if stock is None:
            prov.budget_limit = 'request_limit'
            raise TimeoutError()
        return response(params, stock)
    monkeypatch.setattr(processor.RecoveringResearchPriceProvider, '_capture_report', capture)
    get = lambda p: provider.capture_twse_report(t.EX_RIGHTS,p)[0]['data'][0][0]
    assert get(base) == get(narrow) == '1808'
    assert any(e.get('type') == 'invalid_exchange_cache' for e in provider.report_errors)
    market.refresh = True
    market.refreshed_reports.clear()
    assert get(base) == get(narrow) == '5434'
    assert get(narrow) == '9999'


def test_bad_exact_and_overlapping_cache_recover_from_valid_archived_report(processor, tmp_path, monkeypatch):
    from datetime import date, datetime, timezone
    from urllib.parse import urlencode
    from ai_stock_sentinel.calibration import twse_rights_recovery as t
    url = t.TWSE + t.EX_RIGHTS
    base = dict(response='json', startDate='20260701', endDate='20260731')
    narrow = base | {'endDate':'20260710'}
    def cache(params):
        return tmp_path / 'exchange-reports' / (processor.fingerprint(dict(url=url, params=params)) + '.json.gz')
    for params, fields in [(base, ['股票代號','資料日期']), (base | {'endDate':'20260730'}, ['foo','bar'])]:
        raw = dict(stat='OK', params=params, fields=fields, data=[['1808','115年07月03日']])
        source = dict(source_url=url+'?'+urlencode(params), captured_at=datetime.now(timezone.utc).isoformat(),
                      snapshot_sha256=processor.fingerprint(raw))
        processor.save(cache(params),dict(request=dict(url=url,params=params),payload=raw,proof=source))
    cache(narrow).write_bytes(b'truncated')
    provider = processor.BatchResearchPrices(processor.MarketPrices(tmp_path,'next_open',{},date(2026,7,31)),None)
    monkeypatch.setattr(provider, 'archived_report', lambda request: None)
    assert provider.capture_twse_report(t.EX_RIGHTS,narrow)[0]['fields'] == ['股票代號','資料日期']
    assert len({v['file'] for v in provider.report_errors}) == 2

def test_two_backlog_batches_share_a_retryable_detail_failure(processor, tmp_path, monkeypatch):
    from datetime import date
    from ai_stock_sentinel.calibration import twse_rights_recovery as twse
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider, ResearchSourceError

    market = processor.MarketPrices(tmp_path, 'next_open', {'3706.TW': '2026-08-24'}, date(2026, 11, 23))
    attempts = []
    def unavailable(*args):
        attempts.append(1)
        raise ResearchSourceError('exchange_unavailable', retryable=True, http_status=503, retry_after='30')

    monkeypatch.setattr(RecoveringResearchPriceProvider, '_capture_report', unavailable)
    monkeypatch.setattr(processor.BatchResearchPrices, 'archived_report', lambda *args: None)
    for _ in range(2):
        provider = processor.BatchResearchPrices(market, None)
        with pytest.raises(ResearchSourceError) as exc:
            provider.capture_twse_report(twse.RIGHTS_DETAIL, {'response': 'json', 'STK_NO': '3706', 'T1': '20260909'})
        assert (exc.value.retryable, exc.value.http_status, exc.value.retry_after) == (True, 503, '30')
    assert attempts == [1]
