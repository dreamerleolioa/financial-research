from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyRadarRun, DailyRadarCandidate, RadarInputManifest, RadarRunInput
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.daily_radar.input_manifests import save_manifest, link_run_input, reusable_public_run
from test_daily_radar_validation_read import add_run


def test_live_scoring_freezes_inputs_replays_and_versions_execution_options(monkeypatch):
    import json
    from pathlib import Path
    from copy import deepcopy
    from ai_stock_sentinel.db.models import DailyRadarCandidate, Phase1AvwapSnapshot
    from ai_stock_sentinel.daily_radar.service import run_daily_radar
    from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_fixture_records
    root = Path(__file__).parent / 'fixtures/daily_radar'
    records = load_daily_radar_fixture_records(root)
    before = deepcopy(records)
    context = json.loads((root / 'market_context.json').read_text())
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        DailyRadarRun, DailyRadarCandidate, Phase1AvwapSnapshot, RadarInputManifest, RadarRunInput)])
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED', 'true')
    with Session(engine) as db:
        kwargs = dict(session=db, records=records, market_context=context, allow_fixture_fallback=False)
        first = run_daily_radar(date(2026, 5, 29), 'TW', **kwargs)
        db.commit()
        assert first.status == 'completed'
        assert run_daily_radar(date(2026, 5, 29), 'TW', **kwargs).id == first.id
        db.commit()
        second = run_daily_radar(date(2026, 5, 29), 'TW', **kwargs, candidate_limit=1)
        db.commit()
        assert second.id != first.id
        assert second.candidate_count == 1
        assert records == before
        links = list(db.scalars(select(RadarRunInput)))
        assert len(links) == 2
    engine.dispose()


def test_run_input_replay_is_idempotent_but_never_resurrects_old_revision():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (DailyRadarRun, DailyRadarCandidate, RadarInputManifest, RadarRunInput)])
    with Session(engine) as db:
        first = save_manifest(db, run_date=date(2026, 6, 1), market='TW', payload={'price': 100})
        assert save_manifest(db, run_date=date(2026, 6, 1), market='TW', payload={'price': 100}).id == first.id
        old = add_run(db, count=0)
        link_run_input(db, run_id=old.id, manifest=first)
        db.commit()
        assert reusable_public_run(db, first).id == old.id
        corrected = save_manifest(db, run_date=date(2026, 6, 1), market='TW', payload={'price': 101})
        latest = add_run(db, count=0)
        link_run_input(db, run_id=latest.id, manifest=corrected)
        db.commit()
        assert reusable_public_run(db, first) is None
        assert db.get(RadarInputManifest, first.id).payload['inputs'] == {'price': 100}
        assert reusable_public_run(db, corrected).id == latest.id
    engine.dispose()


def test_readiness_detects_current_inputs_changed_after_publication(monkeypatch):
    from copy import deepcopy
    from ai_stock_sentinel.db.models import (DailyRadarPreparedRun, StockRawData, DailyRadarCandidate,
        DailyRadarForwardValidationResult, SharedBackgroundContext, Phase1AvwapSnapshot)
    from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_cache_records
    from ai_stock_sentinel.daily_radar.input_manifests import freeze_execution_inputs, prepared_input_context
    from ai_stock_sentinel.daily_radar.readiness import read_readiness
    from ai_stock_sentinel.daily_radar.repository import get_shared_background_context_trace_by_symbol
    from test_daily_radar_api import _technical_payload, _market_context, _completed_segmented_step_statuses
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (DailyRadarRun, DailyRadarPreparedRun,
        StockRawData, DailyRadarCandidate, DailyRadarForwardValidationResult, SharedBackgroundContext,
        Phase1AvwapSnapshot, RadarInputManifest, RadarRunInput)])
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED', 'true')
    day = date(2026, 6, 1)
    with Session(engine) as db:
        raw = StockRawData(symbol='2330.TW', record_date=day, raw_data_is_final=True,
            technical=_technical_payload('2330.TW', day), institutional={}, fundamental={})
        prepared = DailyRadarPreparedRun(run_date=day, market='TW', status='prepared', selected_symbols=['2330.TW'],
            symbol_count=1, universe=[], market_context=_market_context(), step_statuses=_completed_segmented_step_statuses(day))
        db.add_all([raw, prepared]); db.flush()
        manifest, _ = freeze_execution_inputs(db, run_date=day, market='TW', records=load_daily_radar_cache_records([raw]),
            market_context=prepared.market_context, background_contexts=get_shared_background_context_trace_by_symbol(
                db, symbols=['2330.TW'], reference_date=day, point_in_time=True),
            input_context=prepared_input_context(prepared, [raw]))
        run = add_run(db, day)
        link_run_input(db, run_id=run.id, manifest=manifest)
        db.commit()
        first = read_readiness(db, run_date=day)
        assert first['scoring']['ready'] is True
        assert first['publication']['input_manifest']['matches_current_inputs'] is True
        changed = deepcopy(raw.technical)
        changed['ohlcv']['open'] += 1
        raw.technical = changed
        db.commit()
        second = read_readiness(db, run_date=day)
        assert second['scoring']['ready'] is True
        assert second['publication']['state'] == 'published'
        assert second['publication']['input_manifest']['matches_current_inputs'] is False
        assert second['data_ready'] is False
    engine.dispose()


@pytest.mark.parametrize('selection', ['selected', 'shadow'])
def test_incomplete_output_gets_a_new_revision_and_preserves_old_run(monkeypatch, selection):
    import json
    from pathlib import Path
    from sqlalchemy import delete, func
    from ai_stock_sentinel.db.models import DailyRadarCandidate, Phase1AvwapSnapshot
    from ai_stock_sentinel.daily_radar.service import run_daily_radar
    from ai_stock_sentinel.daily_radar.data_loader import load_daily_radar_fixture_records
    root = Path(__file__).parent / 'fixtures/daily_radar'
    records, context = load_daily_radar_fixture_records(root), json.loads((root / 'market_context.json').read_text())
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        DailyRadarRun, DailyRadarCandidate, Phase1AvwapSnapshot, RadarInputManifest, RadarRunInput)])
    monkeypatch.setenv('DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED', 'true')
    with Session(engine) as db:
        kwargs = dict(session=db, records=records, market_context=context, allow_fixture_fallback=False)
        first = run_daily_radar(date(2026, 5, 29), 'TW', **kwargs)
        db.commit()
        original_total = db.scalar(select(func.count()).select_from(DailyRadarCandidate).where(DailyRadarCandidate.run_id == first.id))
        victim = db.scalar(select(DailyRadarCandidate.id).where(DailyRadarCandidate.run_id == first.id,
            DailyRadarCandidate.selection_status == selection).limit(1))
        assert victim is not None
        db.execute(delete(DailyRadarCandidate).where(DailyRadarCandidate.id == victim)); db.commit()
        rebuilt = run_daily_radar(date(2026, 5, 29), 'TW', **kwargs)
        db.commit()
        assert rebuilt.id != first.id
        assert db.scalar(select(func.count()).select_from(DailyRadarCandidate).where(DailyRadarCandidate.run_id == first.id)) == original_total - 1
        assert db.scalar(select(func.count()).select_from(DailyRadarCandidate).where(DailyRadarCandidate.run_id == rebuilt.id)) == original_total
    engine.dispose()
