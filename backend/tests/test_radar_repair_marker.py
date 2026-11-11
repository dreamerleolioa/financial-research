from copy import deepcopy
from datetime import date

import pytest

from ai_stock_sentinel.daily_radar import next_open_history_repair as tool
from ai_stock_sentinel.daily_radar.history_repair import MUTABLE_FIELDS
from test_next_open_action_scope import synthetic_case
from test_next_open_history_repair import db
from test_radar_job_postgres import pg_engine


def prepared(db, kind='rights'):
    cal, provider, row, before, request = synthetic_case(db, kind=kind)
    evidence = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    return row, before, tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=evidence)


def rehash(plan):
    plan['sha256'] = tool.fingerprint({k:v for k,v in plan.items() if k != 'sha256'})


@pytest.mark.parametrize('defect', ['missing', 'empty', 'type', 'repair_id', 'operation', 'before_sha256',
    'calendar_sha256', 'capture_sha256', 'version', 'prior_evidence'])
def test_rehashed_manifest_cannot_drop_or_detach_repair_ownership(db, defect):
    row, before, plan = prepared(db)
    payload = plan['results'][0]['after']['outcome']; marker = payload['historical_repair']
    if defect == 'missing': payload.pop('historical_repair')
    elif defect == 'empty': payload['historical_repair'] = {}
    elif defect == 'type': payload['historical_repair'] = True
    else: marker[defect] = {} if defect == 'prior_evidence' else 'wrong'
    rehash(plan)
    with pytest.raises(ValueError, match='repair_marker'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])
    db.rollback()
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('operation', ['diagnostic', 'recompute'])
def test_common_history_apply_also_requires_a_valid_ownership_marker(db, operation):
    from ai_stock_sentinel.daily_radar import history_repair as common
    from test_radar_history_repair import stored, calendar, evidence_for
    _, _, row = stored(db, target=date(2026,9,24) if operation == 'recompute' else date(2026,9,23))
    before = tool.result_snapshot(row)
    plan = common.build_repair_plan(db, {'results': [{'id': row.id, 'operation': operation}]},
        calendar=calendar(), evidence=evidence_for(row.id) if operation == 'recompute' else {})
    plan['results'][0]['after']['outcome'].pop('historical_repair'); rehash(plan)
    with pytest.raises(ValueError, match='repair_marker'):
        common.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])
    db.rollback()
    assert tool.result_snapshot(row) == before


def test_an_exact_pre_marker_v2_snapshot_retains_controlled_rollback(db):
    row, before, plan = prepared(db)
    after = plan['results'][0]['after']; after['outcome'].pop('historical_repair'); rehash(plan)
    for field in MUTABLE_FIELDS:
        value = deepcopy(after[field])
        if field in ('target_date', 'evaluation_as_of_date') and value: value = date.fromisoformat(value)
        setattr(row, field, value)
    db.commit()
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit(); db.refresh(row)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('action', ['ordinary_writer', 'forged_rollback'])
def test_valid_repair_protection_and_exact_rollback_preimage_remain_enforced(db, action):
    from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
    row, before, plan = prepared(db)
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit(); db.refresh(row)
    applied = tool.result_snapshot(row)
    changed = deepcopy(plan)
    changed['results'][0]['after']['outcome'].pop('historical_repair'); rehash(changed)
    if action == 'ordinary_writer':
        assert upsert_forward_validation_results(db, [changed['results'][0]['after']])['records_written'] == 0
    else:
        with pytest.raises(ValueError, match='repair_row_changed'):
            tool.apply_repair_plan(db, changed, expected_sha256=changed['sha256'], rollback=True)
        db.rollback()
    assert tool.result_snapshot(row) == applied
    assert tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])['already_applied'] == 1
    db.commit()
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit(); db.refresh(row)
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('replacement', [1, 1.0])
def test_archived_prior_evidence_preserves_json_boolean_type(db, replacement):
    row, before, plan = prepared(db, kind='split')
    marker = plan['results'][0]['after']['outcome']['historical_repair']
    marker['prior_evidence']['split_event']['corporate_actions_checked'] = replacement
    rehash(plan)
    with pytest.raises(ValueError, match='repair_marker'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])
    db.rollback()
    assert tool.result_snapshot(row) == before


@pytest.mark.parametrize('change', ['before_race', 'rollback_manifest'])
def test_exact_snapshot_checks_preserve_json_types(db, change):
    row, before, plan = prepared(db, kind='split')
    if change == 'before_race':
        from sqlalchemy.orm.attributes import flag_modified
        payload = deepcopy(row.outcome); payload['split_event']['corporate_actions_checked'] = 1
        row.outcome = payload; flag_modified(row, 'outcome'); db.commit(); db.refresh(row)
        assert type(row.outcome['split_event']['corporate_actions_checked']) is int
    else:
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit(); db.refresh(row)
        plan['results'][0]['after']['outcome']['historical_repair']['prior_evidence']['split_event']['corporate_actions_checked'] = 1
        rehash(plan)
    actual = tool.fingerprint(tool.result_snapshot(row))
    with pytest.raises(ValueError, match='repair_row_changed'):
        tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=change == 'rollback_manifest')
    db.rollback()
    assert tool.fingerprint(tool.result_snapshot(row)) == actual


def test_signed_zero_postimage_can_reapply_and_rollback_after_commit(db):
    import math
    cal, provider, row, before, request = synthetic_case(db)
    original = provider.fetch
    def fetch(*args, **kwargs):
        values = original(*args, **kwargs)
        for symbol, rows in values.items():
            for bar in rows:
                close = (100.1 if symbol == 'TAIEX' else 100.6) if bar['date'] == str(provider.target) else 100
                bar.update(open=100, close=close, high=close+1, low=99)
        return values
    provider.fetch = fetch
    ev = tool.capture_evidence(db.get_bind(), request, calendar=cal, provider=provider)
    plan = tool.build_repair_plan(db.get_bind(), request, calendar=cal, evidence=ev)
    value = plan['results'][0]['after']['outcome']['cost_scenarios']['0.5']['net_excess_return_pct']
    assert value == 0 and math.copysign(1, value) == -1
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256']); db.commit(); db.refresh(row)
    if db.get_bind().dialect.name == 'postgresql':
        assert math.copysign(1, row.outcome['cost_scenarios']['0.5']['net_excess_return_pct']) == 1
    assert tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'])['already_applied'] == 1
    db.commit()
    tool.apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True); db.commit(); db.refresh(row)
    assert tool.result_snapshot(row) == before


def test_storage_comparison_matches_postgres_jsonb_numeric_and_type_contract(pg_engine):
    import json
    from sqlalchemy import text
    from ai_stock_sentinel.daily_radar.history_repair import _storage_fingerprint
    scalars = [None, False, True, -0.0, 0, 0.0, 1, 1.0, -1, 1e20, 1e-20, 2**80, 'true', '1']
    with pg_engine.connect() as connection:
        for value in scalars + [[v] for v in scalars] + [{'value': v, 'nested': [v, False]} for v in scalars]:
            stored = connection.scalar(text('SELECT CAST(:value AS jsonb)'), {'value': json.dumps(value)})
            assert _storage_fingerprint(value) == _storage_fingerprint(stored)
        for left in scalars:
            for right in scalars:
                equal = connection.scalar(text('SELECT CAST(:a AS jsonb) = CAST(:b AS jsonb)'),
                    {'a': json.dumps(left), 'b': json.dumps(right)})
                assert (_storage_fingerprint(left) == _storage_fingerprint(right)) == equal
