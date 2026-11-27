from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sqlalchemy import select

from ai_stock_sentinel.db.models import DailyRadarPreparedRun, RadarJobWork, RadarWorkItem
from ai_stock_sentinel.daily_radar.jobs import create_job, reconcile_job
from ai_stock_sentinel.daily_radar.readiness import inventory_items
from ai_stock_sentinel.daily_radar.price_acceptance import OfficialPriceCollector, OfficialPriceEvidence
from radar_calendar_support import calendar
from test_radar_jobs import db
from test_next_open_history_repair import db as replay_db
from test_radar_job_postgres import pg_engine
from test_daily_radar_validation_read import add_run, add_candidate, add_result


DAY = date(2026, 6, 8)
NOW = datetime(2026, 6, 9, 2, tzinfo=timezone.utc)


def setup(db, *, diagnostic_gap=False, lane="signal_close"):
    candidate = add_candidate(db, add_run(db))
    candidate.input_snapshot = dict(candidate.input_snapshot) | {
        'ohlcv': {'close': 100}, 'indicators': {'support_level': 96, 'resistance_level': 104}}
    candidate.data_dates = {'ohlcv': '2026-06-01'}
    if diagnostic_gap: add_result(db, candidate, diagnostic=False)
    db.add(DailyRadarPreparedRun(run_date=DAY, market='TW', selected_symbols=[], universe=[],
        symbol_count=0, step_statuses={'validation-calendar': calendar(DAY-timedelta(days=364), DAY).payload()}))
    db.commit()
    job = create_job(db, run_date=DAY, lanes=[lane], now=NOW)
    for w in db.scalars(select(RadarWorkItem)):
        if w.stage != 'window': w.state = 'ready'
    db.commit()
    return candidate, job


def active(db, job):
    return db.scalars(select(RadarWorkItem).join(RadarJobWork).where(
        RadarJobWork.job_id == job.id, RadarJobWork.active.is_(True), RadarWorkItem.stage == 'window')).all()


def official_prices(monkeypatch, rows):
    indexed = {row['date']: dict(row) | {'price_source': 'official-test'} for row in rows}
    monkeypatch.setattr(OfficialPriceCollector, 'collect',
        lambda self, symbol, start, end: OfficialPriceEvidence(indexed, indexed, []))


def test_candidate_input_change_replaces_old_active_window_without_idle_wait(db):
    candidate, job = setup(db)
    old = active(db, job)[0]
    old.state = 'superseded'
    candidate.input_snapshot = dict(candidate.input_snapshot) | {'ohlcv': {'close': 101}}
    db.commit()
    for n in range(1, 5): reconcile_job(db, job.id, now=NOW+timedelta(minutes=30*n), audit=True)
    current = next(i for i in inventory_items(db, as_of_date=DAY)
        if i['return_basis'] == 'signal_close' and i['window_days'] == 5)
    rows = active(db, job)
    assert len(rows) == 1 and rows[0].id != old.id
    assert rows[0].scope['candidate_revision'] == current['candidate_revision']
    assert rows[0].state != 'superseded'


def test_periodic_result_audit_is_independent_of_recent_activity(db):
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    candidate, job = setup(db, diagnostic_gap=True)
    work = active(db, job)[0]
    assert work.state == 'needs_review'
    row = db.scalar(select(DailyRadarForwardValidationResult))
    row.outcome = dict(row.outcome) | {'observation_diagnostic': {
        'version': 'daily-radar-observation-v1', 'status': 'confirmed'}}
    db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=30), audit=True)
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=60), audit=True)
    assert active(db, job) == []
    assert work.state == 'ready'
    assert job.scope['intent_complete'] is True


def test_display_name_change_does_not_create_a_new_validation_identity(db):
    candidate, job = setup(db)
    original_id = active(db, job)[0].id
    candidate.name = '補齊顯示名稱'; db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=51), audit=True)
    assert [w.id for w in active(db, job)] == [original_id]


def test_current_identity_superseded_work_is_rearmed_after_replan(db):
    _, job = setup(db)
    work = active(db, job)[0]; work.state = 'superseded'; db.commit()
    reconcile_job(db, job.id, now=NOW+timedelta(minutes=1), audit=True)
    assert active(db, job)[0].state == 'pending'


def test_collection_checks_planned_candidate_revision_before_fetch(db, monkeypatch):
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_queue import LostLease
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    candidate, job = setup(db)
    scope = dict(active(db, job)[0].scope)
    candidate.input_snapshot = dict(candidate.input_snapshot) | {'ohlcv': {'close': 101}}
    db.commit()
    def unexpected(*a, **k): raise AssertionError('stale work fetched prices')
    monkeypatch.setattr(YFinanceForwardPriceProvider, 'fetch', unexpected)
    with pytest.raises(LostLease, match='radar_window_inputs_changed'):
        WorkHandlers(db).collect_window(SimpleNamespace(scope=scope, run_date=DAY))


@pytest.mark.parametrize('change', ['name', 'price'])
def test_collection_acceptance_uses_semantic_candidate_revision(db, monkeypatch, change):
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from ai_stock_sentinel.daily_radar.work_queue import LostLease
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    from test_daily_radar_forward_validation import _price
    candidate, job = setup(db)
    scope = dict(active(db, job)[0].scope)
    rows = [_price(str(date(2026, 6, d)), 100, 110, 90, 100) for d in (1, 2, 3, 4, 5, 8)]
    official_prices(monkeypatch, rows)
    monkeypatch.setattr(YFinanceForwardPriceProvider, 'fetch', lambda self, symbols, **kw: {s: rows for s in symbols})
    bundle = WorkHandlers(db).collect_window(SimpleNamespace(scope=scope, run_date=DAY))
    if change == 'name': candidate.name = '只修正顯示名稱'
    else: candidate.input_snapshot = dict(candidate.input_snapshot) | {'ohlcv': {'close': 101}}
    db.commit()
    if change == 'name':
        assert bundle.apply(db)['state'] == 'ready'
        db.commit()
        assert db.scalar(select(DailyRadarForwardValidationResult)) is not None
    else:
        with pytest.raises(LostLease, match='radar_window_inputs_changed'): bundle.apply(db)
        db.rollback()
        assert db.scalar(select(DailyRadarForwardValidationResult)) is None


@pytest.mark.parametrize('basis', ['signal_close', 'next_open'])
def test_shadow_then_selected_has_range_independent_revision(db, monkeypatch, basis):
    from ai_stock_sentinel.calibration.price_provider import YFinanceForwardPriceProvider
    from ai_stock_sentinel.calibration.research_prices import RecoveringResearchPriceProvider
    from ai_stock_sentinel.daily_radar.work_handlers import WorkHandlers
    from test_daily_radar_forward_validation import _price
    asof = date(2026, 6, 9)
    shadow = add_candidate(db, add_run(db), status='shadow')
    shadow.shadow_cohort = 'comparable'
    later = add_candidate(db, add_run(db, date(2026, 6, 2)))
    for candidate in (shadow, later):
        candidate.input_snapshot = dict(candidate.input_snapshot) | {
            'ohlcv': {'close': 100}, 'indicators': {'support_level': 96, 'resistance_level': 104}}
        candidate.data_dates = {'ohlcv': str(candidate.run.run_date)}
    db.add(DailyRadarPreparedRun(run_date=asof, market='TW', selected_symbols=[], universe=[], symbol_count=0,
        step_statuses={'validation-calendar': calendar(asof-timedelta(days=364), asof).payload()}))
    db.commit()
    job = create_job(db, run_date=asof, lanes=[basis], now=NOW+timedelta(days=1))
    scope = next(w.scope for w in active(db, job) if w.scope['candidate_id'] == shadow.id)
    rows = [dict(_price(str(date(2026, 6, d)), 100, 110, 90, 100), price_basis='unadjusted_price',
        price_source='fixture', corporate_actions_checked=True, dividends=0, stock_splits=0) for d in (1,2,3,4,5,8,9)]
    official_prices(monkeypatch, rows)
    monkeypatch.setattr(YFinanceForwardPriceProvider, 'fetch', lambda self, symbols, **kw: {s: rows for s in symbols})
    monkeypatch.setattr(RecoveringResearchPriceProvider, 'fetch', lambda self, symbols, **kw: {s: rows for s in symbols})
    monkeypatch.setattr(RecoveringResearchPriceProvider, 'repair', lambda self, series, **kw: series)
    bundle = WorkHandlers(db).collect_window(SimpleNamespace(scope=scope, run_date=asof))
    assert bundle.apply(db)['state'] == 'ready'


@pytest.mark.parametrize('status', ['selected', 'shadow'])
def test_only_selected_origin_changes_affect_semantic_revision(status):
    from ai_stock_sentinel.daily_radar.readiness import validation_candidate_revision
    candidate = {'selection_status': status, 'observation_origin': None}
    changed = candidate | {'observation_origin': {'candidate_id': 9, 'first_seen_date': '2026-06-02'}}
    assert (validation_candidate_revision(candidate) == validation_candidate_revision(changed)) == (status == 'shadow')


def test_replanning_keeps_unresolved_conflict_on_an_already_saved_window(db, monkeypatch):
    from ai_stock_sentinel.daily_radar import readiness
    from ai_stock_sentinel.daily_radar.trading_status import CONFLICT

    _, job = setup(db)
    work = active(db, job)[0]
    work.state, work.reason = 'needs_review', CONFLICT
    saved = dict(work.scope) | {'state': 'validated', 'reason': None}
    original = dict(job.scope)
    original.pop('window_plan', None)
    original.pop('window_planning', None)
    job.scope = original
    db.commit()
    monkeypatch.setattr(readiness, 'inventory_items', lambda *a, **k: iter([saved]))
    reconcile_job(db, job.id, now=NOW)
    assert [(row.id, row.state, row.reason) for row in active(db, job)] == [(work.id, 'needs_review', CONFLICT)]


def test_another_job_attaches_unresolved_existing_window_conflict(db, monkeypatch):
    from ai_stock_sentinel.daily_radar import readiness
    from ai_stock_sentinel.daily_radar.trading_status import CONFLICT
    from ai_stock_sentinel.daily_radar.source_snapshots import fingerprint

    _, job = setup(db)
    work = active(db, job)[0]
    work.state, work.reason = 'needs_review', CONFLICT
    work.identity = dict(work.identity) | {'cutoff':'2026-06-07'}
    work.work_key = fingerprint(work.identity)
    work.run_date = date(2026, 6, 7)
    saved = dict(work.scope) | {'state': 'validated', 'reason': None}
    db.commit()
    monkeypatch.setattr(readiness, 'inventory_items', lambda *a, **k: iter([saved]))
    second = create_job(db, run_date=DAY, lanes=['signal_close', 'next_open'], now=NOW)
    assert second.id != job.id
    assert [(row.id, row.state) for row in active(db, second)] == [(work.id, 'needs_review')]

@pytest.mark.parametrize(("lane", "source", "params", "paid", "expect_retry", "capture_state"), [
    ("signal_close", "STOCK_DAY", {"date": "20260601", "stockNo": "2330"}, True, True, "waiting_provider"),
    ("signal_close", "MI_5MINS_HIST", {"date": "20260601"}, True, True, "waiting_provider"),
    ("signal_close", "STOCK_DAY", {"date": "20260601", "stockNo": "2890"}, True, False, "waiting_provider"),
    ("signal_close", "STOCK_DAY", {"date": "20260501", "stockNo": "2330"}, True, False, "waiting_provider"),
    ("signal_close", "TWTAWU", {"date": "20260601", "stockNo": "2330"}, True, False, "waiting_provider"),
    ("signal_close", "STOCK_DAY", {"date": "20260601", "stockNo": "2330"}, False, False, "waiting_provider"),
    ("next_open", "STOCK_DAY", {"date": "20260601", "stockNo": "2330"}, True, False, "waiting_provider"),
    ("signal_close", "STOCK_DAY", {"date": "20260601", "stockNo": "2330"}, True, True, "ready"),
    ("signal_close", "STOCK_DAY", {"date": "20260601", "stockNo": "2330"}, True, True, "expired"),
    ("signal_close", "STOCK_DAY", {"date": "20260601", "stockNo": "2330"}, True, True, "legacy"),
])
def test_reconcile_parked_action_with_price_wait_once(replay_db, lane, source, params, paid, expect_retry, capture_state):
    from ai_stock_sentinel.daily_radar.work_queue import aware, claim_work, ensure_work, finish_work

    db = replay_db
    _, job = setup(db, lane=lane)
    window, = active(db, job)
    section = "indicesReport" if source == "MI_5MINS_HIST" else "exchangeReport"
    request = {"provider": "twse", "parser_version": "radar-source-capture-v1",
               "url": f"https://www.twse.com.tw/{section}/{source}", "params": params}
    captured = ensure_work(db, identity={"request": request}, stage="source-capture", lane="source",
                          run_date=None, scope={"request": request}, input_revision="a" * 64, retry_at=NOW)
    reason = "official_corporate_action_in_window"
    cooldown = NOW + timedelta(minutes=2)
    window.state, window.reason, window.retry_at = "needs_review", reason, None
    captured.state = {"expired": "ready", "legacy": "pending"}.get(capture_state, capture_state)
    captured.retry_at = cooldown + timedelta(minutes=3)
    expected_retry = NOW if capture_state == "ready" else captured.retry_at
    from ai_stock_sentinel.daily_radar.source_snapshots import PARSER_VERSION, SourceSnapshotStore
    if capture_state in {"ready", "expired", "legacy"}:
        gate = ensure_work(db, identity={"kind": "provider_gate", "provider": "twse"}, stage="provider-gate",
            lane="source", run_date=None, scope={"provider": "twse"}, input_revision="b" * 64, retry_at=NOW)
        gate.state, gate.retry_at = "waiting_provider", NOW + timedelta(minutes=10)
        expected_retry = NOW if capture_state == "ready" else gate.retry_at
        if capture_state != "legacy":
            captured.dependencies = [gate.work_key]
            captured_at = NOW if capture_state == "ready" else NOW - timedelta(hours=1)
            SourceSnapshotStore(db.get_bind(), clock=lambda: captured_at).record(request, {"stat": "OK"}, db=db)
    from ai_stock_sentinel.calibration.twse_rights_recovery import TWSE, RIGHTS_DETAIL
    from test_signal_mixed_distribution import CASH, FREE, BONUS, ISSUE

    event_day = date(2026, 6, 4)
    detail = {"fields": ["股票代號", CASH, FREE, BONUS, ISSUE, "每股認購金額", "按股東持股比例每千股認購"],
              "stat": "OK", "data": [["2330", "0 元／股", "0 股" if paid else "100 股", "0 股",
                                      "1000 股" if paid else "0 股", "35 元／股" if paid else "0 元／股",
                                      "34 股" if paid else "0 股"]]}
    _, proof = SourceSnapshotStore(db.get_bind()).record(
        {"provider": "twse", "url": TWSE + RIGHTS_DETAIL, "parser_version": PARSER_VERSION,
         "params": {"response": "json", "STK_NO": "2330", "T1": event_day.strftime("%Y%m%d")}}, detail, db=db)
    unrelated = ensure_work(db, identity={"unrelated": True}, stage="source-capture", lane="source",
        run_date=None, scope={"request": {"url": "https://www.twse.com.tw/rwd/zh/afterTrading/TWTAWU",
                                         "params": {"stockNo": "2330"}}}, input_revision="c" * 64, retry_at=NOW)
    review = {"events": [{"family": "rights", "notice": "權", "effective_date": str(event_day)}]}
    window.result = {
        "after": [{"outcome": {"official_event_review": review}}],
        "source_dependencies": {captured.work_key: cooldown.isoformat(),
                                unrelated.work_key: (cooldown + timedelta(hours=1)).isoformat()},
        "source_observations": [proof],
    }
    if capture_state == "legacy":
        window.result = window.result | {"source_dependencies": window.result["source_dependencies"] | {gate.work_key: cooldown.isoformat()}}
    db.commit()

    if expect_retry and db.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text
        from sqlalchemy.orm import Session
        with Session(db.get_bind()) as owner:
            owner.scalar(select(RadarWorkItem.id).where(RadarWorkItem.id == window.id).with_for_update())
            db.execute(text("SET LOCAL lock_timeout = '1s'"))
            reconcile_job(db, job.id, now=NOW)
            db.refresh(window)
            assert window.state == "needs_review"
    reconcile_job(db, job.id, now=NOW)
    db.refresh(window)
    if expect_retry:
        assert window.state == "waiting_provider" and aware(window.retry_at) == expected_retry
        if expected_retry > NOW:
            assert claim_work(db, work_id=window.id, now=NOW) is None
        claim = claim_work(db, work_id=window.id, now=expected_retry)
        assert claim is not None
        # Even an inconclusive replay of the same work must stay parked.
        finish_work(db, claim, state="needs_review", reason=reason, result=window.result, now=expected_retry)
        db.commit()
        reconcile_job(db, job.id, now=expected_retry)
        db.refresh(window)
    assert window.state == "needs_review" and window.retry_at is None


@pytest.mark.parametrize("paid", [True, False])
def test_archived_otc_paid_terms_are_bound_to_the_reported_stock_and_day(db, paid):
    from ai_stock_sentinel.calibration.official_corporate_actions import TPEX_DISTRIBUTIONS
    from ai_stock_sentinel.calibration.research_prices import TPEX
    from ai_stock_sentinel.daily_radar.rights_price_replay import _recorded_cash_issue
    from ai_stock_sentinel.daily_radar.source_snapshots import PARSER_VERSION, SourceSnapshotStore

    signal, day, target = map(date.fromisoformat, ("2026-06-01", "2026-06-04", "2026-06-08"))
    fields = ["除權息日期", "代號", "權/息", "現金股利", "每仟股無償配股", "現金增資股數", "現金增資認購價", "按持股比例仟股認購"]
    row = ["115/06/04", "8042", "除權", "0", "0" if paid else "100", "1000" if paid else "0",
           "35" if paid else "0", "34" if paid else "0"]
    report = {"stat": "OK", "date": "20260601~20260608", "tables": [{"fields": fields, "data": [row], "totalCount": 1}]}
    _, proof = SourceSnapshotStore(db.get_bind()).record(
        {"provider": "tpex", "url": TPEX + TPEX_DISTRIBUTIONS, "parser_version": PARSER_VERSION,
         "params": {"response": "json", "startDate": "2026/06/01", "endDate": "2026/06/08"}}, report, db=db)
    event = {"family": "rights", "notice": "除權", "effective_date": str(day)}
    result = {"after": [{"outcome": {"official_event_review": {"events": [event]}}}], "source_observations": [proof]}
    assert _recorded_cash_issue(db, result, "8042.TWO", signal, target) is paid
    assert not _recorded_cash_issue(db, result, "8041.TWO", signal, target)
    event["effective_date"] = str(target)
    assert not _recorded_cash_issue(db, result, "8042.TWO", signal, target)


@pytest.mark.parametrize('lane', ['signal_close', 'next_open'])
@pytest.mark.parametrize('ordinary_dividend', [False, True])
def test_parked_twse_mixed_issue_is_reopened_once(replay_db, lane, ordinary_dividend):
    db = replay_db
    _, job = setup(db, lane=lane)
    window, = active(db, job)
    day = window.scope['expected_target_date']
    reason = 'official_corporate_action_in_window'
    window.state, window.reason, window.retry_at = 'needs_review', reason, None
    events = [{'effective_date': day, 'family': 'rights', 'notice': '權息'}]
    if ordinary_dividend:
        events.append({'effective_date': str(date.fromisoformat(day) - timedelta(days=3)),
                       'family': 'rights', 'notice': '息', 'cash_dividend': 2.})
    window.result = {'source_observations': [{'request_hash': 'a' * 64, 'snapshot_sha256': 'b' * 64}],
        'after': [{'outcome': {'official_event_review': {'events': events}}}]}
    db.commit()
    reconcile_job(db, job.id, now=NOW)
    assert window.state == 'pending' and window.scope.get('mixed_issue_policy')
    window.state, window.reason, window.retry_at = 'needs_review', reason, None
    db.commit()
    reconcile_job(db, job.id, now=NOW)
    assert window.state == 'needs_review'
