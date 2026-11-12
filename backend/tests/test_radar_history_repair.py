"""Contracts for bounded repairs; all databases/providers in this file are local."""
from datetime import date, timedelta

import pytest

from ai_stock_sentinel.calibration.forward_validation import evaluate_forward_window
from ai_stock_sentinel.daily_radar.forward_validation import DAILY_RADAR_FORWARD_ADAPTER


def price(day, close=100):
    return {"date": day.isoformat(), "open": close, "high": close+1, "low": close-1, "close": close}


def candidate():
    return {"candidate_id": 1, "symbol": "2330.TW", "record_date": "2026-09-16",
            "data_dates": {"ohlcv": "2026-09-16"}, "input_snapshot": {
                "ohlcv": {"close": 100}, "indicators": {"support_level": 90, "resistance_level": 110}}}


@pytest.mark.parametrize("window", [5, 10, 20, 40, 60])
def test_missing_official_session_never_advances_target(window):
    signal = date(2026, 9, 16)
    days = [signal + timedelta(days=i) for i in range(130)
            if (signal + timedelta(days=i)).weekday() < 5]
    days = days[:window+2]
    # Exhaust every location in the required window, including its target.
    for gap in range(1, window+1):
        result = evaluate_forward_window(candidate(), price_series=[price(d) for d in days],
            benchmark_prices=[price(d, 1000) for i, d in enumerate(days) if i != gap],
            adapter=DAILY_RADAR_FORWARD_ADAPTER, window_days=window, as_of_date=days[-1],
            benchmark_symbol="TAIEX", validation_version="test", hit_threshold_pct=0,
            trading_calendar=days)
        assert result["status"] == "skipped"
        assert result["skip_reason"] == "missing_benchmark"
        assert result["outcome"]["expected_target_date"] == days[window].isoformat()
        assert result["outcome"]["missing_dates"] == [days[gap].isoformat()]

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.db.models import DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult, DailyRadarPreparedRun
from ai_stock_sentinel.daily_radar.trading_calendar import TradingCalendar, CALENDAR_STEP, SESSION_STEP, VERSION


@compiles(JSONB, "sqlite")
def json_sqlite(*args, **kwargs):
    return "JSON"


from tests.radar_calendar_support import calendar


def ready_steps():
    return {CALENDAR_STEP:calendar().payload(),SESSION_STEP:{"status":"open","run_date":"2026-09-24",
            "market":"TW","provider":"twse","dataset":"MI_5MINS_HIST"}}

@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[m.__table__ for m in
        (DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult, DailyRadarPreparedRun)])
    with Session(engine) as session:
        yield session
    engine.dispose()


def stored(db, *, signal=date(2026, 9, 16), target=date(2026, 9, 23)):
    run = DailyRadarRun(run_date=signal, market="TW", status="completed", candidate_count=1, errors=[])
    db.add(run); db.flush()
    c = DailyRadarCandidate(run_id=run.id, symbol="2330.TW", name="台積電", primary_bucket="support_retest",
        observation_score=90, explanation="觀察", repeat_status="new", input_snapshot=candidate()["input_snapshot"],
        data_dates={"ohlcv": str(signal)}, selection_status="selected")
    db.add(c);db.flush()
    row = DailyRadarForwardValidationResult(candidate_id=c.id, signal_date=signal, target_date=target,
        window_days=5, validation_version="daily-radar-forward-validation-v2", status="validated",
        benchmark_symbol="TAIEX", outcome={"forward_return_pct": 17, "target_date":str(target)},
        evaluation_as_of_date=date(2026,9,24))
    db.add(row)
    db.add(DailyRadarPreparedRun(run_date=date(2026,9,24), market="TW", selected_symbols=[], universe=[], step_statuses=ready_steps()))
    from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
    from ai_stock_sentinel.daily_radar.validation_inputs import bind_validation_input
    snapshot = forward_validation_candidates_from_runs(db, market='TW', candidate_ids=[c.id])[0]
    row.outcome = bind_validation_input(snapshot, {'outcome': row.outcome})['outcome']
    from ai_stock_sentinel.daily_radar.window_identity import identity
    days = [d for d in calendar().sessions if d > signal][:5]
    if len(days) == 5 and days[-1] == target:
        row.outcome = row.outcome | {'validation_window': identity(signal, days, 'signal_close')}
    db.commit()
    return run,c,row


def test_diagnostic_repair_preserves_returns_and_is_reversible(db):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan, apply_repair_plan
    _,c,row=stored(db)
    original=dict(row.outcome)
    plan=build_repair_plan(db, {"results":[{"id":row.id,"operation":"diagnostic"}],"invalid_run_ids":[]},
                           calendar=calendar(), evidence={})
    assert row.outcome==original  # dry-run performs no mutation
    assert plan["results"][0]["after"]["outcome"]["forward_return_pct"]==17
    assert plan["results"][0]["after"]["outcome"]["observation_diagnostic"]["status"]=="insufficient_data"
    apply_repair_plan(db,plan,expected_sha256=plan["sha256"]);db.commit()
    assert row.outcome["forward_return_pct"]==17
    assert apply_repair_plan(db,plan,expected_sha256=plan["sha256"])["already_applied"]==1
    apply_repair_plan(db,plan,expected_sha256=plan["sha256"],rollback=True);db.commit()
    assert row.outcome==original


@pytest.mark.parametrize('dataset', ['MI_INDEX', 'MI_5MINS_HIST'])
def test_result_repair_preserves_verified_open_session_source(db, dataset):
    from copy import deepcopy
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan, apply_repair_plan
    _, _, row = stored(db)
    prepared = db.scalar(select(DailyRadarPreparedRun))
    steps = deepcopy(prepared.step_statuses)
    steps[SESSION_STEP]['dataset'] = dataset
    prepared.step_statuses = steps
    db.commit()
    original = deepcopy(row.outcome)
    request = {'results': [{'id': row.id, 'operation': 'diagnostic'}]}
    plan = build_repair_plan(db, request, calendar=calendar(), evidence=evidence_for(row.id))
    assert plan['prepared']['before'] == plan['prepared']['after']
    assert plan['prepared']['after'][SESSION_STEP] == steps[SESSION_STEP]
    apply_repair_plan(db, plan, expected_sha256=plan['sha256'])
    db.commit()
    assert prepared.step_statuses == steps
    apply_repair_plan(db, plan, expected_sha256=plan['sha256'], rollback=True)
    db.commit()
    assert prepared.step_statuses == steps and row.outcome == original


@pytest.mark.parametrize('invalid', [
    {'dataset': 'unknown'}, {'provider': 'unknown'}, {'status': 'closed'},
    {'run_date': '2026-09-23'}, {'market': 'US'}, {'missing_calendar': True},
])
def test_result_repair_still_requires_verified_calendar_and_open_session(db, invalid):
    from copy import deepcopy
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan
    _, _, row = stored(db)
    prepared = db.scalar(select(DailyRadarPreparedRun))
    steps = deepcopy(prepared.step_statuses)
    steps[SESSION_STEP]['dataset'] = 'MI_INDEX'
    if invalid.get('missing_calendar'):
        steps.pop(CALENDAR_STEP)
    else:
        steps[SESSION_STEP].update(invalid)
    prepared.step_statuses = steps
    db.commit()
    with pytest.raises(ValueError, match='repair_calendar_phase_required'):
        build_repair_plan(db, {'results': [{'id': row.id, 'operation': 'diagnostic'}]},
            calendar=calendar(), evidence=evidence_for(row.id))
    assert prepared.step_statuses == steps


@pytest.mark.parametrize('malformed', [['unverified'], 'open', True])
def test_metadata_plan_can_repair_malformed_open_session_proof(db, malformed):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan
    stored(db)
    prepared = db.scalar(select(DailyRadarPreparedRun))
    prepared.step_statuses = dict(prepared.step_statuses) | {SESSION_STEP: malformed}
    db.commit()
    plan = build_repair_plan(db, {'results': [], 'invalid_run_ids': []}, calendar=calendar(), evidence={})
    assert plan['results'] == []
    assert plan['prepared']['before'][SESSION_STEP] == malformed
    assert plan['prepared']['after'][SESSION_STEP] == ready_steps()[SESSION_STEP]
    assert prepared.step_statuses[SESSION_STEP] == malformed


@pytest.mark.parametrize('kind', ['list', 'text', 'boolean', 'malformed_report'])
def test_metadata_plan_can_recover_malformed_calendar_but_result_plan_still_requires_phase(db, kind):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan
    _, _, row = stored(db)
    prepared = db.scalar(select(DailyRadarPreparedRun))
    malformed = {'list': [], 'text': 'unverified', 'boolean': True,
        'malformed_report': calendar().payload() | {'reports': [True]}}[kind]
    prepared.step_statuses = dict(prepared.step_statuses) | {CALENDAR_STEP: malformed}
    db.commit()
    metadata = build_repair_plan(db, {'results': [], 'invalid_run_ids': []}, calendar=calendar(), evidence={})
    assert metadata['results'] == []
    assert metadata['prepared']['before'][CALENDAR_STEP] == malformed
    assert metadata['prepared']['after'][CALENDAR_STEP] == calendar().payload()
    with pytest.raises(ValueError, match='repair_calendar_phase_required'):
        build_repair_plan(db, {'results': [{'id': row.id, 'operation': 'diagnostic'}]},
            calendar=calendar(), evidence=evidence_for(row.id))
    assert prepared.step_statuses[CALENDAR_STEP] == malformed


def test_repair_cas_rejects_concurrent_changes_before_writing(db):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan, apply_repair_plan
    _,_,row=stored(db)
    plan=build_repair_plan(db,{"results":[{"id":row.id,"operation":"diagnostic"}]},calendar=calendar(),evidence={})
    row.outcome={"forward_return_pct":99};db.commit()
    with pytest.raises(ValueError, match="changed"):
        apply_repair_plan(db,plan,expected_sha256=plan["sha256"])
    assert db.scalar(select(DailyRadarPreparedRun)).step_statuses==ready_steps()
    assert row.outcome=={"forward_return_pct":99}


def test_invalidated_day_never_resurrects_another_revision(db):
    from ai_stock_sentinel.daily_radar.repository import get_daily_radar_run_by_date
    from ai_stock_sentinel.daily_radar.observation_validation import canonical_radar_run_ids
    for status in ["completed","invalidated","completed"]:
        db.add(DailyRadarRun(run_date=date(2026,6,19),market="TW",status=status,errors=[]))
    db.commit()
    assert get_daily_radar_run_by_date(db,run_date=date(2026,6,19),market="TW") is None
    assert list(db.scalars(canonical_radar_run_ids(market="TW")))==[]


def test_calendar_rejects_unproven_month():
    payload=calendar().payload();payload["reports"]=[]
    with pytest.raises(ValueError,match="month_gap"):
        TradingCalendar.parse(payload)


def evidence_for(row_id, *, missing_stock=(), missing_benchmark=(), anchor=100, proof=None):
    from ai_stock_sentinel.daily_radar.history_repair import fingerprint
    days=(proof or calendar()).sessions
    prices=[price(d,anchor) for d in days if d not in missing_stock]
    benchmark=[price(d,1000) for d in days if d not in missing_benchmark]
    return {str(row_id):{"prices":prices,"benchmark":benchmark,"source":{
        "provider":"test_capture","price_basis":"signal_snapshot_adjusted","captured_at":"2026-11-06T00:00:00Z",
        "sha256":fingerprint({"prices":prices,"benchmark":benchmark})}}}


def test_recompute_corrects_all_price_metrics_and_target(db):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan,apply_repair_plan
    _,_,row=stored(db,target=date(2026,9,24))
    plan=build_repair_plan(db,{"results":[{"id":row.id,"operation":"recompute"}]},
                           calendar=calendar(),evidence=evidence_for(row.id))
    after=plan["results"][0]["after"]
    assert after["target_date"]=="2026-09-23"
    assert after["outcome"]["forward_return_pct"]==0
    assert after["outcome"]["max_adverse_excursion_pct"]==-1
    assert after["outcome"]["max_favorable_excursion_pct"]==1
    apply_repair_plan(db,plan,expected_sha256=plan["sha256"]);db.commit()
    assert row.target_date==date(2026,9,23)


@pytest.mark.parametrize("missing",["stock","benchmark","basis"])
def test_repair_never_validates_missing_or_inconsistent_evidence(db,missing):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan
    _,_,row=stored(db,target=date(2026,9,24))
    ev=evidence_for(row.id,missing_stock=(date(2026,9,22),) if missing=="stock" else (),
        missing_benchmark=(date(2026,9,22),) if missing=="benchmark" else (),anchor=90 if missing=="basis" else 100)
    plan=build_repair_plan(db,{"results":[{"id":row.id,"operation":"recompute"}]},calendar=calendar(),evidence=ev)
    after=plan["results"][0]["after"]
    assert after["status"]=="skipped"
    assert after["outcome"]["expected_target_date"]=="2026-09-23"
    assert after["skip_reason"] in ("missing_future_price","missing_benchmark","historical_price_basis_mismatch")


def test_insufficient_diagnostic_can_be_enriched_later(db):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan,apply_repair_plan
    _,_,row=stored(db)
    request={"results":[{"id":row.id,"operation":"diagnostic"}]}
    plan=build_repair_plan(db,request,calendar=calendar(),evidence={})
    apply_repair_plan(db,plan,expected_sha256=plan["sha256"]);db.commit()
    later=build_repair_plan(db,request,calendar=calendar(),evidence=evidence_for(row.id))
    diagnostic=later["results"][0]["after"]["outcome"]["observation_diagnostic"]
    assert diagnostic["status"]=="unconfirmed"
    assert later["results"][0]["after"]["outcome"]["forward_return_pct"]==17


@pytest.mark.parametrize('reviewed', [False, True])
@pytest.mark.parametrize('window', [5, 10, 20, 40, 60])
def test_cli_inventory_includes_current_incomplete_diagnostics_without_changing_returns(db, reviewed, window):
    import importlib.util
    from pathlib import Path
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan
    from ai_stock_sentinel.daily_radar.window_identity import identity
    _, _, row = stored(db)
    proof = calendar(row.signal_date, date(2026, 12, 31))
    days = [d for d in proof.sessions if d > row.signal_date][:window]
    row.window_days = window
    row.target_date = days[-1]
    row.evaluation_as_of_date = proof.through
    row.outcome = dict(row.outcome) | {'target_date': str(days[-1]),
        'validation_window': identity(row.signal_date, days, 'signal_close')}
    steps = ready_steps()
    steps[CALENDAR_STEP] = proof.payload()
    steps[SESSION_STEP] = steps[SESSION_STEP] | {'run_date': str(proof.through)}
    db.add(DailyRadarPreparedRun(run_date=proof.through, market='TW', selected_symbols=[], universe=[],
        step_statuses=steps))
    row.outcome = dict(row.outcome) | {'observation_diagnostic': {
        'version': 'daily-radar-observation-v1', 'status': 'insufficient_data',
        'missing_reason': 'candidate_history_gap_or_invalid_ohlc',
        'first_seen_date': str(row.signal_date), 'origin_scope': 'available_public_history'}}
    if reviewed:
        row.outcome = dict(row.outcome) | {'historical_repair': {'fixture': 'reviewed'}}
    db.commit()
    before = dict(row.outcome)
    path = Path(__file__).parents[1] / 'scripts' / 'repair_daily_radar_history.py'
    spec = importlib.util.spec_from_file_location('radar_incomplete_cli', path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    request = cli.inventory(db, proof)
    assert request['results'] == [{'id': row.id, 'operation': 'diagnostic'}]
    plan = build_repair_plan(db, request, calendar=proof, evidence=evidence_for(row.id, proof=proof))
    after = plan['results'][0]['after']['outcome']
    assert after['observation_diagnostic']['status'] == 'unconfirmed'
    assert {k: v for k, v in after.items() if k not in ('observation_diagnostic', 'historical_repair')} == {
        k: v for k, v in before.items() if k not in ('observation_diagnostic', 'historical_repair')}
    assert row.outcome == before


def test_manifest_tampering_is_rejected(db):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan,apply_repair_plan
    _,_,row=stored(db)
    plan=build_repair_plan(db,{"results":[{"id":row.id,"operation":"diagnostic"}]},calendar=calendar(),evidence={})
    plan["results"][0]["after"]["outcome"]["forward_return_pct"]=99
    with pytest.raises(ValueError,match="hash_mismatch"):
        apply_repair_plan(db,plan,expected_sha256=plan["sha256"])


def test_planner_refreshes_internal_calendar_gap_even_with_enough_later_rows():
    from ai_stock_sentinel.calibration.forward_validation_planning import prepare_due_forward_validation
    days=calendar().sessions
    stock=[price(d) for d in days]
    index=[price(d,1000) for d in days if d!=date(2026,9,22)]
    calls=[]
    def fetch(symbols,**kwargs):
        calls.append(symbols)
        return {"TAIEX":[price(d,1000) for d in days]}
    prepare_due_forward_validation([candidate()],adapter=DAILY_RADAR_FORWARD_ADAPTER,
        pending_windows_by_candidate={"id:1":[5]},price_series_by_symbol={"2330.TW":stock},
        benchmark_prices=index,benchmark_symbol="TAIEX",as_of_date=days[-1],price_start_date=days[0],
        fetch_prices=fetch,trading_calendar=days)
    assert calls==[["TAIEX"]]


def test_dry_run_is_select_only_and_late_conflict_keeps_entire_batch_unchanged(db):
    from sqlalchemy import event
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan,apply_repair_plan
    run,c,row=stored(db)
    other=DailyRadarCandidate(run_id=run.id,symbol="2317.TW",name="鴻海",primary_bucket="support_retest",
        observation_score=80,explanation="觀察",repeat_status="new",input_snapshot=c.input_snapshot,
        data_dates=c.data_dates,selection_status="selected")
    db.add(other);db.flush()
    second=DailyRadarForwardValidationResult(candidate_id=other.id,signal_date=row.signal_date,target_date=row.target_date,
        window_days=5,validation_version=row.validation_version,status="validated",benchmark_symbol="TAIEX",outcome={})
    db.add(second);db.commit()
    statements=[]
    event.listen(db.get_bind(),"before_cursor_execute",lambda conn,cursor,sql,p,ctx,many:statements.append(sql))
    plan=build_repair_plan(db,{"results":[{"id":r.id,"operation":"diagnostic"} for r in (row,second)]},calendar=calendar(),evidence={})
    assert not any(sql.lstrip().upper().startswith(("INSERT","UPDATE","DELETE")) for sql in statements)
    second.outcome={"concurrent":"update"};db.commit()
    with pytest.raises(ValueError,match="changed"):
        apply_repair_plan(db,plan,expected_sha256=plan["sha256"])
    assert "historical_repair" not in row.outcome
    assert db.scalar(select(DailyRadarPreparedRun)).step_statuses==ready_steps()


def test_cli_inventory_rebuilds_first_observation_after_closed_day_exclusion(db):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).parents[1]/"scripts"/"repair_daily_radar_history.py"
    spec=importlib.util.spec_from_file_location("radar_repair_cli",path)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    _,_,row=stored(db)
    old=DailyRadarRun(run_date=date(2026,6,19),market="TW",status="completed",candidate_count=1,errors=[])
    db.add(old);db.flush()
    db.add(DailyRadarCandidate(run_id=old.id,symbol="2330.TW",name="舊信號",primary_bucket="support_retest",
        observation_score=90,explanation="觀察",repeat_status="new",input_snapshot=candidate()["input_snapshot"],
        data_dates={"ohlcv":"2026-06-18"},selection_status="selected"))
    db.commit()
    proof=calendar(date(2026,6,1),date(2026,9,24),closed=(date(2026,6,19),))
    result=cli.inventory(db,proof)
    assert result["invalid_run_ids"]==[old.id]
    assert result["results"]==[{"id":row.id,"operation":"diagnostic"}]


def test_capture_keeps_transport_failure_distinct_from_missing_prices():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).parents[1]/"scripts"/"repair_daily_radar_history.py"
    spec=importlib.util.spec_from_file_location("radar_capture_cli",path)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    class Broken:
        def fetch(self,*args,**kwargs):
            raise TimeoutError("do not store credential-bearing transport text")
    result=cli.capture_prices({"as_of_date":"2026-09-24","results":[{"id":1}]},
        {1:("2330.TW",date(2026,9,16))},provider=Broken())
    assert result["evidence"]["1"]["source_status"]=="provider_failed"
    assert result["failures"][0]["error_type"]=="TimeoutError"
    assert "credential-bearing" not in str(result)


def test_radar_lifecycle_enrichment_preserves_official_target_and_index_gap():
    from ai_stock_sentinel.daily_radar.forward_validation import build_forward_validation_report
    days=calendar().sessions
    result=build_forward_validation_report([candidate()],price_series_by_symbol={"2330.TW":[price(d) for d in days]},
        benchmark_prices=[price(d,1000) for d in days if d!=date(2026,9,22)],
        market="TW",sample_source="test",as_of_date=days[-1],windows=[5],trading_calendar=days)
    row=result.outcomes[0]
    assert row["outcome"]["expected_target_date"]=="2026-09-23"
    assert row["outcome"]["missing_dates"]==["2026-09-22"]


def test_repair_cli_starts_without_pythonpath_or_database_credentials(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    script = Path(__file__).parents[1] / "scripts" / "repair_daily_radar_history.py"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "DATABASE_URL")}
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "--apply-sha256" in result.stdout


def test_calendar_refresh_preserves_wider_saved_coverage(db):
    from ai_stock_sentinel.daily_radar.trading_calendar import save_calendar,load_calendar
    stored(db)
    prepared=db.scalar(select(DailyRadarPreparedRun))
    wide=calendar(date(2026,9,1),date(2026,9,24))
    prepared.step_statuses={CALENDAR_STEP:wide.payload()};db.commit()
    save_calendar(db,calendar())
    db.commit()
    loaded=load_calendar(db,start_date=date(2026,9,1),as_of_date=date(2026,9,24))
    assert loaded is not None
    assert loaded.payload()==wide.payload()


def test_calendar_refresh_rejects_conflicting_session_evidence(db):
    from ai_stock_sentinel.daily_radar.trading_calendar import save_calendar
    stored(db)
    prepared=db.scalar(select(DailyRadarPreparedRun))
    prepared.step_statuses={CALENDAR_STEP:calendar().payload()};db.commit()
    with pytest.raises(ValueError,match="calendar_evidence_conflict"):
        save_calendar(db,calendar(closed=(date(2026,9,22),)))


def test_result_plans_require_shared_calendar_phase_first(db):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan
    _,_,row=stored(db)
    prepared=db.scalar(select(DailyRadarPreparedRun))
    prepared.step_statuses={};db.commit()
    with pytest.raises(ValueError,match="repair_calendar_phase_required"):
        build_repair_plan(db,{"results":[{"id":row.id,"operation":"diagnostic"}]},
                          calendar=calendar(),evidence={})


def test_shared_calendar_phase_is_reversible_after_dependent_result_batch(db):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan,apply_repair_plan
    _,_,row=stored(db)
    prepared=db.scalar(select(DailyRadarPreparedRun))
    prepared.step_statuses={};db.commit()
    phase=build_repair_plan(db,{"results":[]},calendar=calendar(),evidence={})
    assert apply_repair_plan(db,phase,expected_sha256=phase["sha256"])["metadata_written"]==1
    db.commit()
    assert apply_repair_plan(db,phase,expected_sha256=phase["sha256"])["metadata_written"]==0
    batch=build_repair_plan(db,{"results":[{"id":row.id,"operation":"diagnostic"}]},
                            calendar=calendar(),evidence={})
    assert batch["prepared"]["before"]==batch["prepared"]["after"]
    apply_repair_plan(db,batch,expected_sha256=batch["sha256"]);db.commit()
    with pytest.raises(ValueError,match="dependent_manifest_still_applied"):
        apply_repair_plan(db,phase,expected_sha256=phase["sha256"],rollback=True)
    apply_repair_plan(db,batch,expected_sha256=batch["sha256"],rollback=True);db.commit()
    apply_repair_plan(db,phase,expected_sha256=phase["sha256"],rollback=True);db.commit()
    assert prepared.step_statuses=={}


def test_generic_writer_cannot_overwrite_repair_with_stale_identity_map(db):
    from sqlalchemy.orm.attributes import set_committed_value
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan,apply_repair_plan,result_snapshot
    from ai_stock_sentinel.daily_radar.forward_validation import upsert_forward_validation_results
    _,_,row=stored(db)
    original=result_snapshot(row)
    plan=build_repair_plan(db,{"results":[{"id":row.id,"operation":"diagnostic"}]},calendar=calendar(),evidence={})
    apply_repair_plan(db,plan,expected_sha256=plan["sha256"]);db.commit()
    repaired=dict(row.outcome)
    set_committed_value(row,"outcome",original["outcome"])
    stale=original|{"status":"skipped","skip_reason":"missing_future_price","target_date":None,"outcome":{}}
    assert upsert_forward_validation_results(db,[stale])["records_written"]==0
    db.commit();db.refresh(row)
    assert row.outcome==repaired


def test_report_uses_official_sessions_when_benchmark_has_an_internal_gap():
    from ai_stock_sentinel.daily_radar.forward_validation import build_forward_validation_report
    c=candidate();c["observation_origin"]={"candidate_id":1,"first_seen_date":"2026-09-16","scope":"available_public_history"}
    days=calendar().sessions[:-1]
    report=build_forward_validation_report([c],price_series_by_symbol={"2330.TW":[price(d) for d in days]},
        benchmark_prices=[price(d,1000) for d in days if d!=date(2026,9,22)],market="TW",sample_source="test",
        as_of_date=days[-1],windows=[5],trading_calendar=days).report
    stats=report["observation_diagnostics"]["cohorts"][0]["windows"]["5"]["all_selected"]
    assert stats["immature_observation_count"]==0
    assert stats["skipped_validation_count"]==1
    assert stats["coverage_complete"] is False


def test_promoted_first_observation_is_included_and_repaired(db):
    import importlib.util
    from pathlib import Path
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan
    _,_,row=stored(db)
    row.outcome=dict(row.outcome)|{"observation_diagnostic":{"version":"daily-radar-observation-v1",
        "status":"not_first_observation","first_seen_date":"2026-06-19","origin_scope":"available_public_history"}}
    db.commit()
    path=Path(__file__).parents[1]/"scripts"/"repair_daily_radar_history.py"
    spec=importlib.util.spec_from_file_location("radar_promoted_cli",path)
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    request=cli.inventory(db,calendar())
    assert request["results"]==[{"id":row.id,"operation":"diagnostic"}]
    plan=build_repair_plan(db,request,calendar=calendar(),evidence=evidence_for(row.id))
    assert plan["results"][0]["after"]["outcome"]["observation_diagnostic"]["status"]=="unconfirmed"


def test_apply_rechecks_first_observation_history(db):
    from ai_stock_sentinel.daily_radar.history_repair import build_repair_plan,apply_repair_plan
    _,c,row=stored(db)
    older=DailyRadarRun(run_date=date(2026,9,14),market="TW",status="completed",candidate_count=1,errors=[])
    db.add(older);db.flush()
    db.add(DailyRadarCandidate(run_id=older.id,symbol=c.symbol,name=c.name,primary_bucket=c.primary_bucket,
        observation_score=90,explanation="舊信號",repeat_status="new",input_snapshot=c.input_snapshot,
        data_dates={"ohlcv":"2026-09-14"},selection_status="selected"));db.commit()
    plan=build_repair_plan(db,{"results":[{"id":row.id,"operation":"diagnostic"}]},calendar=calendar(),evidence={})
    assert plan["results"][0]["after"]["outcome"]["observation_diagnostic"]["status"]=="not_first_observation"
    replacement=DailyRadarRun(run_date=date(2026,9,14),market="TW",status="completed",candidate_count=0,errors=[])
    db.add(replacement);db.commit()
    with pytest.raises(ValueError,match="observation_origin_changed"):
        apply_repair_plan(db,plan,expected_sha256=plan["sha256"])


def test_persisted_report_does_not_count_a_shifted_window_as_validated(db):
    from ai_stock_sentinel.daily_radar.history_repair import result_snapshot
    from ai_stock_sentinel.daily_radar.forward_validation import build_forward_validation_report_from_outcomes
    _,_,row=stored(db,target=date(2026,9,24))
    c=candidate();c["candidate_id"]=row.candidate_id
    report=build_forward_validation_report_from_outcomes([c],[result_snapshot(row)],market="TW",
        sample_source="test",as_of_date=date(2026,9,24),windows=[5],trading_calendar=calendar().sessions)
    assert report["skip_reasons"]=={"historical_window_calendar_mismatch":1}
    assert len(report["calendar_invalid_windows"])==1


def test_apply_serializes_origin_check_against_new_public_revisions(tmp_path,monkeypatch):
    import threading
    from ai_stock_sentinel.daily_radar import history_repair
    engine=create_engine("sqlite:///"+str(tmp_path/"serialized.db"),
                         connect_args={"timeout":2,"check_same_thread":False})
    Base.metadata.create_all(engine,tables=[m.__table__ for m in
        (DailyRadarRun,DailyRadarCandidate,DailyRadarForwardValidationResult,DailyRadarPreparedRun)])
    with Session(engine) as session:
        _,c,row=stored(session)
        older=DailyRadarRun(run_date=date(2026,9,14),market="TW",status="completed",candidate_count=1,errors=[])
        session.add(older);session.flush()
        session.add(DailyRadarCandidate(run_id=older.id,symbol=c.symbol,name=c.name,primary_bucket=c.primary_bucket,
            observation_score=90,explanation="first",repeat_status="new",input_snapshot=c.input_snapshot,
            data_dates={"ohlcv":"2026-09-14"},selection_status="selected"));session.commit()
        plan=history_repair.build_repair_plan(session,{"results":[{"id":row.id,"operation":"diagnostic"}]},
                                             calendar=calendar(),evidence={})
    checked=threading.Event();attempted=threading.Event();finished=threading.Event()
    errors=[];committed_during_check=[]
    real_origins=history_repair.load_observation_origins
    def origins(*args,**kwargs):
        snapshot=real_origins(*args,**kwargs)
        checked.set();assert attempted.wait(3)
        committed_during_check.append(finished.wait(.2))
        return snapshot
    monkeypatch.setattr(history_repair,"load_observation_origins",origins)
    def publish():
        try:
            assert checked.wait(3)
            with Session(engine) as session:
                session.add(DailyRadarRun(run_date=date(2026,9,14),market="TW",status="completed",candidate_count=0,errors=[]))
                attempted.set();session.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()
    worker=threading.Thread(target=publish);worker.start()
    try:
        with Session(engine) as session,session.begin():
            history_repair.apply_repair_plan(session,plan,expected_sha256=plan["sha256"])
        assert finished.wait(3)
        assert not errors
        assert committed_during_check==[False]
    finally:
        worker.join(timeout=3);engine.dispose()
