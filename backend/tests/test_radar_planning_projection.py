from sqlalchemy import select, text, event
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import Base, DailyRadarCandidate
from ai_stock_sentinel.daily_radar.planning_inventory import inventory_input_revision
from test_radar_bounded_planning import seed, DAY
from test_radar_job_postgres import pg_engine
from test_radar_job_postgres import _migration
from test_radar_readiness import storage


def test_postgres_projection_tracks_raw_sql_updates_without_reading_snapshots(pg_engine):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        _, candidates = seed(db, 6)
        cid = candidates[0].id
        first = inventory_input_revision(db, as_of_date=DAY)
        db.execute(text("UPDATE daily_radar_candidates SET name='display only', "
                        "input_snapshot=input_snapshot || jsonb_build_object('unused_history', repeat('x',100000)) WHERE id=:id"), {'id': cid})
        db.commit()
        queries = []
        def record(c, cursor, statement, *args): queries.append(statement)
        event.listen(pg_engine, 'before_cursor_execute', record)
        try:
            assert inventory_input_revision(db, as_of_date=DAY) == first
        finally:
            event.remove(pg_engine, 'before_cursor_execute', record)
        assert not any('input_snapshot' in sql or 'score_breakdown' in sql for sql in queries)
        db.execute(text("UPDATE daily_radar_candidates SET input_snapshot="
                        "jsonb_set(input_snapshot, '{ohlcv,close}', '101') WHERE id=:id"), {'id': cid})
        db.commit()
        assert inventory_input_revision(db, as_of_date=DAY) != first
        # A caller cannot forge the stored digest instead of changing inputs.
        db.execute(text("UPDATE daily_radar_candidates SET validation_input_hash='forged' WHERE id=:id"), {'id': cid})
        db.commit()
        assert len(db.scalar(text('SELECT validation_input_hash FROM daily_radar_candidates WHERE id=:id'), {'id': cid})) == 32


def test_publication_readiness_does_not_scan_validation(storage, monkeypatch):
    from ai_stock_sentinel.daily_radar import readiness
    db, _ = storage
    def forbidden(*args, **kwargs):
        raise AssertionError('publication checked historical validation')
    monkeypatch.setattr(readiness, 'validation_inventory', forbidden)
    report = readiness.read_readiness(db, run_date=DAY, scope='publication')
    assert report['scope'] == 'publication'
    assert report['data_ready'] is None
    assert report['validation'] == {}


def test_projection_migration_backfills_and_rolls_back_without_changing_history(pg_engine):
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        seed(db, 6)
    with pg_engine.begin() as conn:
        original = conn.execute(text("SELECT to_jsonb(c) - 'validation_input_hash' - 'validation_projection' "
                                     "FROM daily_radar_candidates c ORDER BY id")).scalars().all()
        conn.execute(text('DROP TRIGGER radar_validation_projection_v1 ON daily_radar_candidates'))
        conn.execute(text('DROP FUNCTION radar_validation_projection_v1()'))
        conn.execute(text('ALTER TABLE daily_radar_candidates DROP COLUMN validation_input_hash, DROP COLUMN validation_projection'))
        filename = 'ef6a7b8c9d0e_add_radar_validation_projection.py'
        _migration(conn, filename)
        assert conn.scalar(text('SELECT count(*) FROM daily_radar_candidates WHERE length(validation_input_hash)=32 AND validation_projection IS NOT NULL')) == 6
        conn.execute(text("UPDATE daily_radar_candidates SET validation_input_hash='forged', validation_projection='{}'"))
        assert conn.scalar(text("SELECT count(*) FROM daily_radar_candidates WHERE validation_input_hash='forged' OR validation_projection='{}'")) == 0
        _migration(conn, filename, 'downgrade')
        assert conn.execute(text('SELECT to_jsonb(c) FROM daily_radar_candidates c ORDER BY id')).scalars().all() == original


def test_compact_projection_matches_original_candidate_contract(pg_engine):
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_snapshot_columns
    from ai_stock_sentinel.db.models import DailyRadarRun
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        _, candidates = seed(db, 6)
        candidates[0].input_snapshot = {'ohlcv': {'close': 321.5}, 'indicators': {
            'support_level': 300, 'resistance_level': 340, 'ma20': 315, 'ma60': 311},
            'versions': {'scoring_version':'v42'}, 'selection_version': 's43',
            'market_context': {'regime':'bull'}, 'replay_input': {'market_context': {'benchmark': {'symbol':'TAIEX'}}}}
        candidates[0].score_breakdown = {'relative_strength': {'freshness':'fresh','relative_value':1.25},
                                        'market_context': {'details': {'regime':'bull'}}}
        db.commit()
        def rows(compact):
            return db.execute(select(*forward_validation_snapshot_columns(compact=compact))
                .join(DailyRadarRun, DailyRadarRun.id == DailyRadarCandidate.run_id)
                .order_by(DailyRadarCandidate.id)).all()
        assert rows(True) == rows(False)


def test_scoring_history_manifest_is_stable_across_physical_row_orders(pg_engine):
    from ai_stock_sentinel.daily_radar.repository import get_symbol_candidate_history
    Base.metadata.create_all(pg_engine)
    with Session(pg_engine) as db:
        _, candidates = seed(db, 6)
        symbols = [c.symbol for c in candidates]
        for setting in ('enable_indexscan', 'enable_indexonlyscan', 'enable_bitmapscan'):
            db.execute(text(f'SET {setting}=off'))
        def history():
            return get_symbol_candidate_history(db, symbols=symbols, before_date=DAY,
                lookback_days=None, market='TW', summary_only=True)
        before = history()
        assert len(before) == 6
        db.execute(text('CREATE INDEX test_history_order ON daily_radar_candidates(symbol DESC)'))
        db.execute(text('CLUSTER daily_radar_candidates USING test_history_order'))
        db.execute(text('ANALYZE daily_radar_candidates'))
        db.commit()
        assert history() == before
