import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan, apply_repair_plan
from ai_stock_sentinel.daily_radar.readiness import validation_inventory
from ai_stock_sentinel.daily_radar.validation_inputs import bind_validation_input
from ai_stock_sentinel.db.models import Base
from scripts.repair_daily_radar_history import inventory
from test_radar_history_repair import stored, evidence_for, calendar
from test_radar_job_postgres import pg_engine


@pytest.fixture(params=['sqlite', 'postgres'])
def db(request):
    engine = create_engine('sqlite://') if request.param == 'sqlite' else request.getfixturevalue('pg_engine')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    if request.param == 'sqlite':
        engine.dispose()


def unverified(db, state):
    _, candidate, row = stored(db)
    payload = dict(row.outcome) | {'observation_diagnostic': {
        'version': 'daily-radar-observation-v1', 'status': 'unconfirmed',
        'first_seen_date': str(row.signal_date)}}
    payload.pop('validation_input', None)
    if state == 'mismatch':
        payload['validation_input'] = {'version': 'radar-validation-input-v1', 'candidate_revision': '0'*64}
    row.outcome = payload
    db.commit()
    return candidate, row


@pytest.mark.parametrize('state', ['legacy', 'mismatch'])
def test_inventory_selects_same_target_unverified_returns_for_full_recompute(db, state):
    _, row = unverified(db, state)
    assert {'id': row.id, 'operation': 'recompute'} in inventory(db, calendar())['results']


@pytest.mark.parametrize('state', ['legacy', 'mismatch'])
def test_reviewed_same_target_recompute_can_repair_input_proof(db, state):
    _, row = unverified(db, state)
    request = {'results': [{'id': row.id, 'operation': 'recompute'}]}
    plan = build_repair_plan(db, request, calendar=calendar(), evidence=evidence_for(row.id))
    apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit()
    report = validation_inventory(db, as_of_date=calendar().through)['signal_close']
    assert report['validated_count'] == 1 and report['input_unverified_count'] == 0
    assert row.outcome['forward_return_pct'] == 0


def test_manifest_rejects_revision_drift_outside_the_old_candidate_snapshot(db):
    from datetime import date
    _, candidate, row = stored(db, target=date(2026,9,24))
    plan = build_repair_plan(db, {'results': [{'id': row.id, 'operation': 'recompute'}]},
        calendar=calendar(), evidence=evidence_for(row.id))
    candidate.observation_score += 1
    db.commit()
    with pytest.raises(ValueError, match='repair_validation_inputs_changed'):
        apply_repair_plan(db, plan, expected_sha256=plan['sha256'])
    assert row.target_date == date(2026,9,24)
    assert 'historical_repair' not in row.outcome


def test_matching_same_target_return_is_not_recomputed_without_a_gap(db):
    _, _, row = stored(db)
    current = forward_validation_candidates_from_runs(db, market='TW', end_date=calendar().through)[0]
    row.outcome = bind_validation_input(current, {'outcome': row.outcome})['outcome']
    db.commit()
    with pytest.raises(ValueError, match='recompute_requires_historical_mismatch'):
        build_repair_plan(db, {'results': [{'id': row.id, 'operation': 'recompute'}]},
            calendar=calendar(), evidence=evidence_for(row.id))
