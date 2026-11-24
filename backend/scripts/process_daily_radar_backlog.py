#!/usr/bin/env python3
"""Process a local PostgreSQL copy in evidence-sharing batches. The restore must write radar-local-validation-copy.json into the new PostgreSQL
data directory after copying successfully. Its JSON fields are version
(radar-local-copy-v1), database (current_database()), oid (pg_database.oid
as text), system_identifier (pg_control_system().system_identifier as text)
as_of_date (YYYY-MM-DD), and restore_id (a UUID issued for the restore). The runner only reads this receipt."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from tempfile import NamedTemporaryFile
from threading import Lock
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import gzip
import zlib
from hashlib import sha256
import json
from contextlib import contextmanager
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from uuid import UUID
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sqlalchemy import URL, create_engine, event, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration import twse_rights_recovery as twse
from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider, ResearchSourceError
from ai_stock_sentinel.daily_radar import history_repair, next_open_history_repair
from ai_stock_sentinel.daily_radar.evidence_batches import evidence_batches
from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
from ai_stock_sentinel.daily_radar.history_repair import fingerprint
from ai_stock_sentinel.daily_radar.price_acceptance import (
    OfficialPriceCollector,
    PRICE_EVIDENCE,
    verify_price_lock,
)
from ai_stock_sentinel.daily_radar.validation_inputs import validation_candidate_revision
from ai_stock_sentinel.radar_gap_contract import validation_gap_key
from ai_stock_sentinel.daily_radar.readiness import inventory_items, read_readiness
from ai_stock_sentinel.daily_radar.research_validation import (
    run_research_validation,
    RESEARCH_TERMINAL_REASONS,
)
from ai_stock_sentinel.daily_radar.source_snapshots import PARSER_VERSION, SourceSnapshotStore
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.daily_radar.trading_calendar import load_calendar
from ai_stock_sentinel.daily_radar.validation_execution import run_signal_close_validation
from ai_stock_sentinel.daily_radar.validation_preparation import (
    accept_validation_results,
    capture_validation_acceptance,
    reserve_validation_commit,
)
from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult as Result, RadarSourceSnapshot
from ai_stock_sentinel.taiwan_symbols import is_supported_taiwan_symbol


def key(row):
    return row['candidate_id'], row['window_days'], row['validation_version']


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False, default=str).encode()
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(gzip.compress(raw, compresslevel=3))
    Path(temporary.name).replace(path)


def load(path):
    with gzip.open(path, 'rt', encoding='utf-8') as file:
        return json.load(file)


def local_engine(socket, port, database, user, cutoff=None):
    # A Cloud SQL Auth Proxy also exposes a Unix socket. Check its real local
    # postmaster lock, data directory and bootstrap timestamp before any writes.
    socket = socket.resolve(strict=True)
    lock = (socket / f'.s.PGSQL.{port}.lock').read_text().splitlines()
    pid = (Path(lock[1]) / 'postmaster.pid').read_text().splitlines()
    if lock[:4] != pid[:4] or int(pid[3]) != port:
        raise ValueError('local_postmaster_required')
    engine = create_engine(
        URL.create(
            'postgresql+psycopg2',
            username=user,
            database=database,
            query={'host': str(socket), 'port': str(port)},
        )
    )
    # The restore issues this receipt *after* successfully copying the DB into
    # a new local cluster. A sibling DB, replacement database, or another host
    # must not gain write access merely because it accepts the same socket path.
    receipt_path = Path(pid[1]) / 'radar-local-validation-copy.json'

    @event.listens_for(engine, 'checkout')
    def verify_copy(connection, record, proxy):
        receipt = json.loads(receipt_path.read_text())
        try:
            with connection.cursor() as cursor:
                cursor.execute("""SELECT current_setting('data_directory'), inet_server_addr() IS NULL,
                    current_database(), oid::text, (pg_control_system()).system_identifier::text
                    FROM pg_database WHERE datname=current_database()""")
                directory, unix, name, oid, system = cursor.fetchone()
            restore_id = str(UUID(receipt['restore_id']))
            if (
                not unix
                or Path(directory).resolve() != Path(pid[1]).resolve()
                or name != database
                or receipt
                != {
                    'version': 'radar-local-copy-v1',
                    'database': name,
                    'oid': oid,
                    'system_identifier': system,
                    'as_of_date': str(cutoff),
                    'restore_id': restore_id,
                }
            ):
                raise ValueError('isolated_radar_copy_required')
            record.info['local_copy_identity'] = fingerprint(receipt)
        finally:
            connection.rollback()

    with engine.connect():
        pass
    return engine


class MarketPrices:
    def __init__(self, folder, basis, starts, cutoff, *, sessions=(), refresh=False, refresh_quotes=False):
        self.folder, self.basis, self.starts, self.cutoff = folder, basis, starts, cutoff
        self.locks = {symbol: Lock() for symbol in starts}
        self.attempted = set()
        self.refresh_quotes = refresh_quotes
        self.refresh = refresh
        self.sessions = tuple(map(str, sessions))
        self.report_locks = [Lock() for _ in range(32)]
        self.refreshed_reports = set()
        self.failed_reports = {}
        self.coverage_lock = Lock()
        self.coverage = None
        self.covering_hits = {}

    def _index_report(self, file, entry):
        request, proof, payload = entry['request'], entry['proof'], entry['payload']
        if file.name != fingerprint(request) + '.json.gz' or proof['snapshot_sha256'] != fingerprint(payload):
            raise ValueError('exchange_report_identity_changed')
        url, q = request['url'], request['params']
        eligible = {twse.TWSE + p for p in (twse.EX_RIGHTS, twse.FACE_VALUE, twse.REDUCTION)}
        if (url not in eligible or set(q) != {'startDate','endDate','response'} or q['response'] != 'json'):
            return
        # Replace the previous revision, including when the new response cannot prove coverage.
        self.coverage[url].pop(file.name, None)
        self.covering_hits.clear()
        if payload.get('stat') != 'OK' or not same_report(proof['source_url'], url, q):
            return
        captured = datetime.fromisoformat(proof['captured_at'])
        after = datetime.combine(self.cutoff, time(13, 30), ZoneInfo('Asia/Taipei'))
        if captured.tzinfo is not None and after <= captured <= datetime.now(timezone.utc):
            path = urlsplit(url).path
            field = '資料日期' if path == twse.EX_RIGHTS else '恢復買賣日期'
            twse.dated_market_rows(payload, q, field, [], path=path, source=proof | {'requested_subrange': q})
            self.coverage[url][file.name] = (q['startDate'], q['endDate'], entry)

    def covering_report(self, request, *, errors=None):
        params, url = request['params'], request['url']
        path = urlsplit(url).path
        if url != twse.TWSE + path or path not in (twse.EX_RIGHTS, twse.FACE_VALUE, twse.REDUCTION):
            return None
        if set(params) != {'startDate', 'endDate', 'response'} or params['response'] != 'json':
            return None
        identity = (url, params['startDate'], params['endDate'])
        with self.coverage_lock:
            if identity in self.covering_hits:
                return self.covering_hits[identity]
            if self.coverage is None:
                self.coverage = defaultdict(dict)
                for file in (self.folder / 'exchange-reports').glob('*.json.gz'):
                    try:
                        self._index_report(file, load(file))
                    except (KeyError, TypeError, ValueError, OSError, EOFError, AttributeError, zlib.error):
                        if errors is not None:
                            errors.append({'type': 'invalid_exchange_cache', 'file': file.name})
            return self._select_covering_report(url, path, params, identity)

    def _select_covering_report(self, url, path, params, identity):
        matches = []
        field = '資料日期' if path == twse.EX_RIGHTS else '恢復買賣日期'
        for start, end, entry in self.coverage[url].values():
            if start <= params['startDate'] <= params['endDate'] <= end:
                proof = entry['proof'] | {'requested_subrange': dict(params)}
                rows = twse.dated_market_rows(entry['payload'], params, field, [], path=path, source=proof)
                # TWT49U also displays the latest NAV filing, which is updated
                # after the event. Preserve the full receipt, comparing every
                # event field across reports and ignoring only these filing cells.
                signature = sorted(fingerprint(twse.market_event_facts(path, row)) for _, row in rows)
                matches.append((signature, start, end, entry['payload'], proof))
        if not matches:
            return None
        if any(item[0] != matches[0][0] for item in matches):
            raise ValueError('conflicting_exchange_range_reports')
        # Prefer the latest start and then the earliest end, with the original URL and hash.
        latest = max(m[1] for m in matches)
        match = min((m for m in matches if m[1] == latest), key=lambda m: m[2])
        self.covering_hits[identity] = (match[3], match[4])
        return self.covering_hits[identity]

    def receipt(self, symbol, *, start_date=None):
        if symbol != 'TAIEX' and not is_supported_taiwan_symbol(symbol):
            raise ValueError('invalid_market_symbol')
        start = min(self.starts[symbol], str(start_date)) if start_date else self.starts[symbol]
        with self.locks[symbol]:
            return self._receipt(symbol, start)

    def _receipt(self, symbol, start):
        path = self.folder / 'prices' / self.basis / (symbol + '.json.gz')
        wider = path.parent / f'{symbol}-{start}.json.gz'
        for existing in (path, wider):
            if not existing.exists():
                continue
            item = load(existing)
            if (
                item.get('sha256') != fingerprint({k: v for k, v in item.items() if k != 'sha256'})
                or item['symbol'] != symbol
                or item['basis'] != self.basis
                or item['end'] != str(self.cutoff)
                or item['source'] != 'yfinance'
            ):
                raise ValueError('price_capture_identity_changed')
            if item['start'] <= start:
                required = {d for d in self.sessions if start <= d <= item['end']}
                incomplete = self.refresh and not required.issubset({r['date'] for r in item['rows']})
                if (
                    existing in self.attempted
                    or not item['error']
                    and not incomplete
                    and not self.refresh_quotes
                ):
                    return item
                save(self.folder / 'market-captures' / (item['sha256'] + '.json.gz'), item)
                path = existing
                start = item['start']
                break
            path = wider
        self.attempted.add(path)
        provider = (
            YFinanceForwardPriceProvider()
            if self.basis == 'signal_close'
            else RecoveringResearchPriceProvider()
        )
        reason = None
        try:
            rows = provider.fetch([symbol], start_date=date.fromisoformat(start), end_date=self.cutoff).get(
                symbol, []
            )
            if not rows:
                reason = 'no_price_rows'
        except Exception as exc:
            rows, reason = [], type(exc).__name__
        item = {
            'basis': self.basis,
            'symbol': symbol,
            'start': start,
            'end': str(self.cutoff),
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'source': 'yfinance',
            'rows': rows,
            'error': reason,
            'provider_diagnostics': getattr(provider, 'diagnostics', [])[:3],
        }
        item['sha256'] = fingerprint(item)
        save(self.folder / 'market-captures' / (item['sha256'] + '.json.gz'), item)
        save(path, item)
        return item

    def fetch(self, symbols, *, start_date, end_date):
        result = {}
        for symbol in symbols:
            item = self.receipt(symbol, start_date=start_date)
            if str(start_date) < item['start'] or str(end_date) > item['end']:
                raise ValueError('price_capture_range_mismatch')
            rows = [r for r in item['rows'] if str(start_date) <= r['date'] <= str(end_date)]
            if self.basis == 'signal_close':
                # The verification of a pre-rights adjustment needs one recorded
                # vendor response for every candle in the window.
                proof = {'provider': item['source'], 'source_operation': 'local-adjusted-history',
                         'snapshot_sha256': item['sha256'], 'captured_at': item['captured_at']}
                rows = [dict(row, source_capture=row.get('source_capture') or proof) for row in rows]
            result[symbol] = rows
        return result


def same_report(source, url, params):
    parsed = urlsplit(source)
    return parsed._replace(query='').geturl() == url and sorted(
        parse_qsl(parsed.query, keep_blank_values=True)
    ) == sorted((k, str(v)) for k, v in params.items())


class BatchResearchPrices(RecoveringResearchPriceProvider):
    def __init__(self, market_prices, engine):
        if market_prices.basis != 'next_open':
            raise ValueError('unadjusted_research_prices_required')
        super().__init__()
        self.market_prices = market_prices
        self.report_errors = []
        self.engine = engine

    def fetch(self, symbols, *, start_date, end_date):
        return self.market_prices.fetch(symbols, start_date=start_date, end_date=end_date)

    def archived_report(self, request):
        # Only use captures made after the entire requested validation cutoff.
        # The normal exchange parsers still verify symbol, dates and coverage.
        url = request['url']
        provider = {'www.twse.com.tw': 'twse', 'www.tpex.org.tw': 'tpex'}[urlsplit(url).hostname]
        identity = dict(request, provider=provider, parser_version=PARSER_VERSION)
        after = datetime.combine(self.market_prices.cutoff, time(13, 30), ZoneInfo('Asia/Taipei'))
        with Session(self.engine) as db:
            saved = db.scalar(
                select(RadarSourceSnapshot)
                .where(
                    RadarSourceSnapshot.request_hash == fingerprint(identity),
                    RadarSourceSnapshot.captured_at >= after,
                    RadarSourceSnapshot.captured_at <= datetime.now(timezone.utc),
                )
                .order_by(RadarSourceSnapshot.captured_at.desc(), RadarSourceSnapshot.id.desc())
                .limit(1)
            )
            return SourceSnapshotStore._read(saved) if saved is not None else None

    def _capture_report(self, origin, path, params):
        # The provider's proof identifies the original capture. Preserve it
        # when another stock or horizon needs the same exchange report.
        request = {'url': origin + path, 'params': params}
        digest = fingerprint(request)
        with self.market_prices.report_locks[int(digest[:4], 16) % len(self.market_prices.report_locks)]:
            return self._read_report(request, digest, origin, path, params)

    def _read_report(self, request, digest, origin, path, params):
        cache = self.market_prices.folder / 'exchange-reports' / (digest + '.json.gz')
        failure = self.market_prices.failed_reports.get(digest)
        if failure is not None:
            self.report_errors.append(dict(failure, repeated=True))
            if 'retryable' in failure:
                raise ResearchSourceError(failure['reason'], retryable=failure['retryable'], http_status=failure['http_status'],
                                          retry_after=failure['retry_after'])
            raise ValueError('exchange_report_fetch_failed')
        refresh = self.market_prices.refresh and digest not in self.market_prices.refreshed_reports
        after = datetime.combine(self.market_prices.cutoff, time(13, 30), ZoneInfo('Asia/Taipei'))
        entry = None
        if cache.exists():
            try:
                decoded = load(cache)
                if (decoded['request'] != request or decoded['proof']['snapshot_sha256'] != fingerprint(decoded['payload'])
                        or not same_report(decoded['proof']['source_url'], origin + path, params)):
                    raise ValueError('exchange_report_identity_changed')
                captured = datetime.fromisoformat(decoded['proof']['captured_at'])
                entry = decoded
            except (KeyError, TypeError, ValueError, OSError, EOFError, AttributeError, zlib.error):
                self.report_errors.append({'type': 'invalid_exchange_cache', 'file': cache.name})
                damaged = cache.read_bytes()
                archive = self.market_prices.folder / 'exchange-captures' / (sha256(damaged).hexdigest() + '.invalid')
                archive.parent.mkdir(exist_ok=True, parents=True)
                if not archive.exists():
                    archive.write_bytes(damaged)
        if entry is not None:
            if not refresh and captured.tzinfo is not None and after <= captured <= datetime.now(timezone.utc):
                return entry['payload'], entry['proof']
            save(self.market_prices.folder / 'exchange-captures' / (fingerprint(entry) + '.json.gz'), entry)
        archived = None if refresh else self.archived_report(request)
        if not refresh and archived is None:
            covering = self.market_prices.covering_report(request, errors=self.report_errors)
            if covering is not None:
                return covering
        try:
            payload, proof = archived or super()._capture_report(origin, path, params)
            captured = datetime.fromisoformat(proof['captured_at'])
            if captured.tzinfo is None or not after <= captured <= datetime.now(timezone.utc):
                raise ValueError('exchange_capture_outside_cutoff')
            self.market_prices.refreshed_reports.add(digest)
        except (RuntimeError, ValueError, OSError) as exc:
            failure = {
                'request': request,
                'type': type(exc).__name__,
                'http_status': getattr(exc, 'http_status', None),
                'budget_limit': self.budget_limit,
            }
            if isinstance(exc, (ResearchSourceError, OSError)):
                failure.update(reason=str(exc), retryable=getattr(exc, 'retryable', isinstance(exc, OSError)),
                               retry_after=getattr(exc, 'retry_after', None))
            self.report_errors.append(failure)
            if refresh and entry is None:
                covering = self.market_prices.covering_report(request, errors=self.report_errors)
                if covering is not None:
                    return covering
            if not self.budget_limit and not getattr(exc, 'local_budget_exhausted', False):
                self.market_prices.failed_reports[digest] = failure
            raise
        entry = {'request': request, 'payload': payload, 'proof': proof}
        save(self.market_prices.folder / 'exchange-captures' / (fingerprint(entry) + '.json.gz'), entry)
        with self.market_prices.coverage_lock:
            save(cache, entry)
            if self.market_prices.coverage is not None:
                self.market_prices._index_report(cache, entry)
        return payload, proof


def ids_for(db, batch):
    candidate_ids = {g['candidate_id'] for g in batch}
    result = db.execute(
        select(Result.id, Result.candidate_id, Result.window_days, Result.validation_version).where(
            Result.candidate_id.in_(candidate_ids),
            Result.validation_version == batch[0]['validation_version'],
        )
    )
    return {(r.candidate_id, r.window_days, r.validation_version): r.id for r in result}


def repair_batch(engine, kind, batch, calendar, prices, official, folder):
    with Session(engine) as db:
        require_current(db, batch, calendar)
        ids = ids_for(db, batch)
    by_id = {v: k for k, v in ids.items()}
    errors = []
    if kind != 'diagnostic':
        eligible = []
        with Session(engine) as db:
            candidates = {
                c['candidate_id']: c
                for c in forward_validation_candidates_from_runs(
                    db,
                    market='TW',
                    end_date=calendar.through,
                    candidate_ids=sorted({g['candidate_id'] for g in batch}),
                )
            }
            stored = {r.id: r for r in db.scalars(select(Result).where(Result.id.in_(ids.values())))}
            for gap in batch:
                row = stored.get(ids.get(key(gap)))
                candidate = candidates.get(gap['candidate_id'])
                if (
                    row is not None
                    and candidate is not None
                    and gap['expected_target_date']
                    and row.signal_date in calendar.sessions
                    and next_open_history_repair._manual(
                        candidate, history_repair.result_snapshot(row), calendar
                    )[0]
                ):
                    eligible.append(gap)
                else:
                    errors.append({'window': key(gap), 'reason': gap['reason']})
        batch = eligible
        if not batch:
            return {'accepted': 0, 'errors': errors}
    operation = 'diagnostic' if kind == 'diagnostic' else 'recompute'
    request = {
        'as_of_date': str(calendar.through),
        'calendar_sha256': fingerprint(calendar.payload()),
        'results': [{'id': ids[key(g)], 'operation': operation} for g in batch],
    }
    if kind == 'diagnostic':
        rows, _ = prepare_batch(engine, batch, calendar, prices, official)
        evidence = {}
        for row in rows:
            identity = ids[key(row)]
            proof = row['outcome'].get(PRICE_EVIDENCE)
            if row['status'] != 'validated' or not proof:
                errors.append(
                    {'id': identity, 'reason': row.get('skip_reason') or 'historical_price_evidence_missing'}
                )
                continue
            verify_price_lock(row, symbol=batch[0]['symbol'], basis='signal_close')
            payload = {'prices': proof['stock'], 'benchmark': proof['benchmark']}
            evidence[str(identity)] = {
                'source_status': 'captured',
                'source': {
                    'provider': 'official_signal_close_validation',
                    'price_basis': 'signal_snapshot_adjusted',
                    'captured_at': datetime.now(timezone.utc).isoformat(),
                    'vendor_capture_sha256': prices.receipt(batch[0]['symbol'])['sha256'],
                    'validation_price_evidence': proof,
                    'sha256': fingerprint(payload),
                },
                **payload,
            }
        request['results'] = [r for r in request['results'] if str(r['id']) in evidence]
        if not request['results']:
            return {
                'accepted': 0,
                'errors': [dict(e, window=by_id[e['id']]) for e in errors],
            }
        save(folder / 'captures' / (fingerprint(evidence) + '.json.gz'), evidence)
        with Session(engine) as db:
            require_current(db, batch, calendar)
            plan = history_repair.build_repair_plan(
                db, request, calendar=calendar, evidence=evidence, errors=errors
            )
        apply = history_repair.apply_repair_plan
    else:
        request.update(version=next_open_history_repair.VERSION, return_basis='next_open')
        capture = next_open_history_repair.capture_evidence(
            engine, request, calendar=calendar, provider=official
        )
        save(folder / 'captures' / (capture['sha256'] + '.json.gz'), capture)
        plan = next_open_history_repair.build_repair_plan(
            engine, request, calendar=calendar, evidence=capture, errors=errors
        )
        apply = next_open_history_repair.apply_repair_plan
    if plan['results']:
        save(folder / 'manifests' / (plan['sha256'] + '.json.gz'), plan)
        with Session(engine) as db, db.begin():
            reserve_validation_commit(db, freeze_candidates=True)
            require_current(db, batch, calendar)
            apply(db, plan, expected_sha256=plan['sha256'])
    return {
        'accepted': len(plan['results']),
        'errors': [e if 'window' in e else dict(e, window=by_id[e['id']]) for e in errors],
    }


def require_current(db, batch, calendar):
    expected = {key(g): validation_gap_key(g) for g in batch}
    actual = {
        key(g): validation_gap_key(g)
        for g in inventory_items(
            db,
            as_of_date=calendar.through,
            candidate_ids=sorted({g['candidate_id'] for g in batch}),
            include_gap_revision=True,
        )
        if key(g) in expected and g.get('gap_revision')
    }
    if actual != expected:
        raise ValueError('batch_gap_changed')


def prepare_batch(engine, batch, calendar, prices, official):
    first = batch[0]
    basis = first['return_basis']
    ids = sorted({g['candidate_id'] for g in batch})
    windows = sorted({g['window_days'] for g in batch})
    wanted = {key(g) for g in batch}
    with Session(engine) as db:
        require_current(db, batch, calendar)
        candidates = forward_validation_candidates_from_runs(
            db, market='TW', end_date=calendar.through, candidate_ids=ids
        )
        acceptance = capture_validation_acceptance(
            db, candidates, market='TW', windows=windows, validation_version=first['validation_version']
        )
        db.rollback()
        request = DailyRadarForwardValidationRunRequest(
            mode='range',
            as_of_date=calendar.through,
            start_date=min(date.fromisoformat(g['signal_date']) for g in batch),
            end_date=max(date.fromisoformat(g['signal_date']) for g in batch),
            windows=windows,
            return_basis=basis,
        )
        execute = run_signal_close_validation if basis == 'signal_close' else run_research_validation
        bundle = execute(
            db,
            request,
            as_of_date=calendar.through,
            candidate_ids=ids,
            prepare_only=True,
            calendar_provider=SimpleNamespace(fetch=lambda **kwargs: calendar),
            price_provider=prices if basis == 'signal_close' else official,
            official_price_collector=OfficialPriceCollector(official, return_basis=basis),
        )
    rows = [r for r in bundle.outcomes if key(r) in wanted]
    if len(rows) != len(wanted) or {key(r) for r in rows} != wanted:
        raise ValueError('batch_window_results_incomplete')
    if {
        c['candidate_id']: validation_candidate_revision(c) for c in bundle.candidates
    } != acceptance.candidate_revisions:
        raise ValueError('validation_inputs_changed')
    return rows, acceptance


def due_batch(engine, batch, calendar, prices, official, folder):
    rows, acceptance = prepare_batch(engine, batch, calendar, prices, official)
    wanted = {key(g) for g in batch}
    basis = batch[0]['return_basis']
    receipt = {
        'as_of_date': str(calendar.through),
        'market': 'TW',
        'calendar_sha256': fingerprint(calendar.payload()),
        'candidate_revisions': {
            str(cid): revision for cid, revision in acceptance.candidate_revisions.items()
        },
        'rows': rows,
        'preimages': [{'key': list(k), 'revision': acceptance.result_revisions[k]} for k in sorted(wanted)],
    }
    save(folder / 'evaluations' / (fingerprint(receipt) + '.json.gz'), receipt)
    options = {'terminal_skip_reasons': RESEARCH_TERMINAL_REASONS} if basis == 'next_open' else {}
    with Session(engine) as db, db.begin():
        reserve_validation_commit(db, freeze_candidates=True)
        require_current(db, batch, calendar)
        summary = accept_validation_results(db, rows, calendar=calendar, acceptance=acceptance, **options)
        persisted = {
            key(g): g
            for g in inventory_items(
                db, as_of_date=calendar.through, candidate_ids=sorted({g['candidate_id'] for g in batch})
            )
            if key(g) in wanted
        }
        if set(persisted) != wanted:
            raise ValueError('persisted_window_missing')
    return {
        'outcomes': dict(Counter(g['reason'] or g['state'] for g in persisted.values())),
        'acceptance': summary,
        'results': [
            {
                'window': key(r),
                'status': persisted[key(r)]['state'],
                'reason': persisted[key(r)]['reason'],
                'proposed_status': r['status'],
                'proposed_reason': r.get('skip_reason'),
            }
            for r in rows
        ],
    }


@contextmanager
def open_journal(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as file:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if file.tell():
            file.seek(-1, 2)
            if file.read(1) != b'\n':
                file.seek(0)
                contents = file.read()
                end = contents.rfind(b'\n') + 1
                partial = contents[end:]
                path.with_suffix('.partial-' + fingerprint(partial.hex())[:16]).write_bytes(partial)
                file.truncate(end)
        yield file


def retry_keys(journal, reason):
    latest = {}
    with journal.open() as source:
        for line in source:
            batch = json.loads(line)
            for window in batch['windows']:
                latest[tuple(window)] = 'interrupted_batch' if batch.get('started') else batch.get('error')
            for row in (*batch.get('errors', []), *batch.get('results', [])):
                latest[tuple(row['window'])] = row.get('reason')
    return {window for window, error in latest.items() if error in {reason, 'interrupted_batch'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-socket', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--run-date', type=date.fromisoformat, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--kind', choices=['diagnostic', 'next_open_repair', 'due'])
    parser.add_argument('--retry-reason', help='Only retry current gaps with this last recorded batch error')
    args = parser.parse_args()
    engine = local_engine(args.local_socket, args.port, args.database, args.user, args.run_date)
    with engine.connect() as connection:
        copy_id = connection.info['local_copy_identity']
    folder = args.output / copy_id
    journal = folder / ((args.kind or 'all') + '-batches.jsonl')
    with open_journal(journal) as journal_file:
        retry = retry_keys(journal, args.retry_reason) if args.retry_reason else None
        run_batches(engine, args, folder, journal_file, retry)


def run_batches(engine, args, folder, journal_file, retry):
    with Session(engine) as db:
        calendar = load_calendar(db, start_date=args.run_date - timedelta(days=364), as_of_date=args.run_date)
        if not calendar or calendar.through != args.run_date:
            raise ValueError('official_calendar_unavailable')
        report = read_readiness(
            db, run_date=args.run_date, scope='validation', include_all_validation_gaps=True
        )
    gaps = [g for v in report['validation'].values() for g in v['gaps']]
    starts = {}
    for g in gaps:
        k = g['return_basis'], g['symbol']
        starts[k] = min(starts.get(k, g['signal_date']), g['signal_date'])
    markets = {}
    for basis in report['validation']:
        dates = {symbol: start for (lane, symbol), start in starts.items() if lane == basis}
        if basis == 'next_open':
            for (_, symbol), value in starts.items():
                dates.setdefault(symbol, value)
        if dates:
            dates['TAIEX'] = min(dates.values())
            markets[basis] = MarketPrices(
                folder,
                basis,
                dates,
                args.run_date,
                sessions=calendar.sessions,
                refresh=bool(args.retry_reason),
                refresh_quotes=args.retry_reason
                in {
                    'conflicting_price_records',
                    'adjustment_factor_unverified',
                    'signal_price_anchor_mismatch',
                },
            )
    groups = [
        (kind, batch)
        for kind, batch in evidence_batches(
            (g for g in gaps if retry is None or key(g) in retry), limit=args.limit, as_of_date=args.run_date
        )
        if not args.kind or kind == args.kind
    ]

    def process(item):
        number, (kind, batch) = item
        first = batch[0]
        record = {
            'batch': number,
            'kind': kind,
            'symbol': first['symbol'],
            'basis': first['return_basis'],
            'windows': [key(g) for g in batch],
        }
        official = None
        try:
            market = markets[first['return_basis']]
            official = BatchResearchPrices(markets['next_open'], engine)
            if kind == 'due':
                record.update(due_batch(engine, batch, calendar, market, official, folder))
            elif kind == 'needs_review':
                record['errors'] = [{'window': key(g), 'reason': g['reason']} for g in batch]
            else:
                record.update(repair_batch(engine, kind, batch, calendar, market, official, folder))
        except Exception as exc:
            detail = str(exc)
            record['error'] = detail if re.fullmatch('[a-z0-9_]{1,100}', detail) else type(exc).__name__
        if official is not None:
            transport = [
                {
                    k: v
                    for k, v in d.items()
                    if k in ('stage', 'source_url', 'http_status', 'error_type', 'reason', 'attempt')
                }
                for d in official.diagnostics
                if d.get('error_type') or d.get('http_status')
            ]
            if transport or official.report_errors:
                record['source_errors'] = official.report_errors + transport
        return record

    def append(record):
        journal_file.write((json.dumps(record, ensure_ascii=False, default=str) + '\n').encode())
        journal_file.flush()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = []
        for number, (kind, batch) in enumerate(groups, 1):
            append({'started': True, 'batch': number, 'kind': kind, 'windows': [key(g) for g in batch]})
            futures.append(pool.submit(process, (number, (kind, batch))))
        for number, future in enumerate(as_completed(futures), 1):
            record = future.result()
            append(record)
            if number % 25 == 0 or number == len(groups):
                print(f'{number}/{len(groups)} batches recorded', flush=True)


if __name__ == '__main__':
    main()
