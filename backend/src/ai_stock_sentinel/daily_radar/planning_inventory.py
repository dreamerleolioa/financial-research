"""Compact input epochs and keyset pages for the complete Radar inventory."""
from datetime import date, datetime
from hashlib import sha256

from sqlalchemy import Text, cast, func, literal, select
from sqlalchemy.dialects.postgresql import aggregate_order_by

from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun
from ai_stock_sentinel.daily_radar.observation_validation import canonical_radar_run_ids
from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_snapshot_columns
from ai_stock_sentinel.daily_radar.source_snapshots import canonical_json, fingerprint
from ai_stock_sentinel.taiwan_symbols import taiwan_stock_symbol_predicate


def _digest(db, query, columns, order):
    if db.get_bind().dialect.name == 'postgresql':
        entry = func.md5(cast(func.jsonb_build_array(*columns), Text))
        rows = query.with_only_columns(order.label('ordering'), entry.label('digest')).subquery()
        # Return a scalar, not every candidate's potentially large JSON snapshot.
        return db.scalar(select(func.md5(func.string_agg(rows.c.digest,
            aggregate_order_by(literal(''), rows.c.ordering)))))
    digest = sha256()
    for row in db.execute(query.order_by(order).execution_options(yield_per=128)):
        values = [v.isoformat() if isinstance(v, (date, datetime)) else v for v in row]
        digest.update(canonical_json(values).encode())
        digest.update(b'\n')
    return digest.hexdigest()


def inventory_input_revision(db, *, as_of_date):
    """Include canonical origin history; exclude display names and result writes."""
    runs = canonical_radar_run_ids(market='TW', end_date=as_of_date)
    columns = [c for c in forward_validation_snapshot_columns() if c.key != 'name']
    run_columns = [DailyRadarRun.id, DailyRadarRun.run_date, DailyRadarRun.status, DailyRadarRun.candidate_count]
    candidates = select(*columns).join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id).where(
        DailyRadarRun.id.in_(runs), taiwan_stock_symbol_predicate(DailyRadarCandidate.symbol))
    run_query = select(*run_columns).where(DailyRadarRun.id.in_(runs))
    return fingerprint({'candidates': _digest(db, candidates, columns, DailyRadarCandidate.id),
                        'runs': _digest(db, run_query, run_columns, DailyRadarRun.id)})


def candidate_page(db, *, start_date, as_of_date, after, limit):
    query = select(DailyRadarCandidate.id).where(
        DailyRadarCandidate.run_id.in_(canonical_radar_run_ids(
            market='TW', start_date=start_date, end_date=as_of_date)),
        taiwan_stock_symbol_predicate(DailyRadarCandidate.symbol))
    if after is not None:
        query = query.where(DailyRadarCandidate.id > after)
    return list(db.scalars(query.order_by(DailyRadarCandidate.id).limit(limit + 1)))
