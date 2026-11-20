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
