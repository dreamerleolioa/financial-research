from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy.orm.attributes import flag_modified

from ai_stock_sentinel.daily_radar import history_repair as tool
from test_next_open_history_repair import db
from test_radar_job_postgres import pg_engine
from test_radar_history_repair import stored, calendar


def prepared(db, *, field='hit_above_threshold', value=True):
    _, _, row = stored(db, target=date(2026, 9, 23))
    row.outcome = row.outcome | {field: value}
    flag_modified(row, 'outcome'); db.commit()
    plan = tool.build_repair_plan(db, {'results': [{'id': row.id, 'operation': 'diagnostic'}]},
        calendar=calendar(), evidence={})
    return row, tool.result_snapshot(row), plan


def rehash(plan):
    plan['sha256'] = tool.fingerprint({k: v for k, v in plan.items() if k != 'sha256'})


@pytest.mark.parametrize('field,before,after', [
    ('hit_above_threshold', True, 1), ('hit_above_threshold', True, 1.0),
    ('hit_above_threshold', False, 0), ('close_below_defense_reference', False, 0.0),
    ('forward_return_pct', 1.0, True), ('extra_return_detail', {'flags': [False, True]}, {'flags': [0, 1]}),
])
def test_diagnostic_manifest_cannot_change_immutable_json_types(db, field, before, after):
    row, original, plan = prepared(db, field=field, value=before)
    plan['results'][0]['after']['outcome'][field] = after; rehash(plan)
    with pytest.raises(ValueError, match='diagnostic_repair_changed_returns'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])
    db.rollback()
    assert tool.result_snapshot(row) == original
    assert type(row.outcome[field]) is type(before)


@pytest.mark.parametrize('before,after', [(1, 1.0), (-0.0, 0), (0, -0.0),
    (10**23, 1e23), (1e23, 10**23), (-10**23, -1e23), (-1e23, -10**23)])
def test_diagnostic_numeric_storage_equivalence_remains_reversible(db, before, after):
    row, original, plan = prepared(db, field='forward_return_pct', value=before)
    plan['results'][0]['after']['outcome']['forward_return_pct'] = after; rehash(plan)
    assert tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])['written'] == 1
    db.commit()
    assert tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])['already_applied'] == 1
    db.commit()
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit()
    assert tool._storage_fingerprint(tool.result_snapshot(row)) == tool._storage_fingerprint(original)


def test_previously_applied_type_drift_can_be_rolled_back_to_original_boolean(db):
    row, original, plan = prepared(db)
    after = plan['results'][0]['after']; after['outcome']['hit_above_threshold'] = 1; rehash(plan)
    for field in tool.MUTABLE_FIELDS:
        value = deepcopy(after[field])
        if field in ('target_date', 'evaluation_as_of_date') and value: value = date.fromisoformat(value)
        setattr(row, field, value)
    flag_modified(row, 'outcome'); db.commit(); db.refresh(row)
    assert type(row.outcome['hit_above_threshold']) is int
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit(); db.refresh(row)
    assert tool.result_snapshot(row) == original and row.outcome['hit_above_threshold'] is True


def test_forged_type_drift_rollback_cannot_match_a_different_applied_snapshot(db):
    row, _, plan = prepared(db)
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit()
    original = tool.result_snapshot(row)
    plan['results'][0]['after']['outcome']['hit_above_threshold'] = 1; rehash(plan)
    with pytest.raises(ValueError, match='repair_row_changed'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True)
    db.rollback()
    assert tool.result_snapshot(row) == original


def test_legacy_rollback_still_rejects_actual_return_changes(db):
    row, original, plan = prepared(db)
    plan['results'][0]['after']['outcome']['forward_return_pct'] = 99; rehash(plan)
    with pytest.raises(ValueError, match='diagnostic_repair_changed_returns'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True)
    db.rollback()
    assert tool.result_snapshot(row) == original
