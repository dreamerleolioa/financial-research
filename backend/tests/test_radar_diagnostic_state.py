"""Only diagnostic metadata may differ in a forward diagnostic manifest."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from sqlalchemy.orm.attributes import flag_modified

from ai_stock_sentinel.daily_radar import history_repair as tool
from test_radar_diagnostic_types import prepared, rehash
from test_next_open_history_repair import db
from test_radar_job_postgres import pg_engine

FIELDS = ('candidate_id', 'window_days', 'validation_version', 'signal_date', 'target_date',
    'benchmark_symbol', 'evaluation_as_of_date', 'status', 'skip_reason')
MUTABLE_SCALARS = ('target_date', 'evaluation_as_of_date', 'status', 'skip_reason')


def replacement(snapshot, field):
    value = snapshot[field]
    if field in ('candidate_id', 'window_days'): return value + 1
    if field in ('signal_date', 'target_date', 'evaluation_as_of_date'):
        return str(date.fromisoformat(value) + timedelta(days=1))
    return {'validation_version': 'wrong-version', 'benchmark_symbol': 'SPX',
        'status': 'skipped', 'skip_reason': 'missing_future_price'}[field]


@pytest.mark.parametrize('field', FIELDS + ('extra_snapshot_field', 'nested_diagnostic_name', 'candidate_id_boolean'))
def test_forward_diagnostic_freezes_every_non_metadata_result_field(db, field):
    assert set(FIELDS) == set(tool.RESULT_FIELDS) - {'outcome'}
    row, original, plan = prepared(db, field='extra_return_detail', value={'observation_diagnostic': {'hit': True}})
    after = plan['results'][0]['after']
    if field == 'candidate_id_boolean': after['candidate_id'] = bool(original['candidate_id'])
    elif field == 'extra_snapshot_field': after['unrecognized_result_field'] = 'cannot-be-stored'
    elif field == 'nested_diagnostic_name': after['outcome']['extra_return_detail']['observation_diagnostic']['hit'] = False
    else: after[field] = replacement(original, field)
    rehash(plan)
    with pytest.raises(ValueError, match='repair_immutable_identity_changed|diagnostic_repair_changed_returns'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])
    db.commit(); db.refresh(row)
    assert tool._storage_fingerprint(tool.result_snapshot(row)) == tool._storage_fingerprint(original)


def install_legacy_postimage(db, row, snapshot):
    for field in tool.MUTABLE_FIELDS:
        value = deepcopy(snapshot[field])
        if field in ('target_date', 'evaluation_as_of_date') and value: value = date.fromisoformat(value)
        setattr(row, field, value)
    flag_modified(row, 'outcome'); db.commit(); db.refresh(row)


@pytest.mark.parametrize('field', MUTABLE_SCALARS)
def test_exact_legacy_scalar_drift_remains_recoverable_but_cannot_apply_forward(db, field):
    row, original, plan = prepared(db)
    after = plan['results'][0]['after']; after[field] = replacement(original, field); rehash(plan)
    install_legacy_postimage(db, row, after)
    applied = tool._storage_fingerprint(tool.result_snapshot(row))
    with pytest.raises(ValueError, match='diagnostic_repair_changed_returns'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])
    db.commit(); db.refresh(row)
    assert tool._storage_fingerprint(tool.result_snapshot(row)) == applied
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit(); db.refresh(row)
    assert tool.result_snapshot(row) == original


@pytest.mark.parametrize('field', MUTABLE_SCALARS)
def test_legacy_scalar_rollback_still_requires_exact_typed_postimage(db, field):
    row, _, plan = prepared(db)
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit(); db.refresh(row)
    applied = tool.result_snapshot(row)
    plan['results'][0]['after'][field] = replacement(applied, field); rehash(plan)
    with pytest.raises(ValueError, match='repair_row_changed'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True)
    db.commit(); db.refresh(row)
    assert tool.result_snapshot(row) == applied


def test_normal_diagnostic_changes_only_the_two_allowed_metadata_keys(db):
    row, original, plan = prepared(db)
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit(); db.refresh(row)
    applied = tool.result_snapshot(row)
    assert {k: v for k, v in applied.items() if k != 'outcome'} == {k: v for k, v in original.items() if k != 'outcome'}
    assert {k: v for k, v in applied['outcome'].items() if k not in ('observation_diagnostic', 'historical_repair')} == {
        k: v for k, v in original['outcome'].items() if k not in ('observation_diagnostic', 'historical_repair')}
    assert tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])['already_applied'] == 1
    db.commit()
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit(); db.refresh(row)
    assert tool.result_snapshot(row) == original
