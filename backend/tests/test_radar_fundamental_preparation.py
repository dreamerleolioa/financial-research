from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from test_fundamental_archive import _managed_symbol_db_session
from ai_stock_sentinel.data_sources.fundamental import router, service
from ai_stock_sentinel.data_sources.fundamental.normalizers import normalize_finmind_statement_rows
from ai_stock_sentinel.data_sources.fundamental.repository import store_fundamental_periods
from ai_stock_sentinel.data_sources.fundamental.schemas import FundamentalBackfillRequest
from ai_stock_sentinel.db.models import CompanyDividendEvent, CompanyFundamentalPeriod, FundamentalBackfillJob, DailyRadarPreparedRun, StockRawData


DAY = date(2026, 10, 15)


@pytest.fixture
def db(monkeypatch):
    session, engine = _managed_symbol_db_session()
    for table in (CompanyFundamentalPeriod.__table__, CompanyDividendEvent.__table__, FundamentalBackfillJob.__table__):
        table.create(engine, checkfirst=True)
    monkeypatch.setattr(service, "today_taipei", lambda: DAY, raising=False)
    session.add(DailyRadarPreparedRun(run_date=DAY, market="TW", selected_symbols=["2330.TW", "0050.TW"],
        universe={}, step_statuses={"prepare-universe": {"status": "completed"},
            "refresh-ohlcv": {"status": "completed"},
            "refresh-managed-raw-data": {"status": "completed"}}, status="prepared"))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def test_raw_pool_can_be_prepared_before_ai_evidence_and_includes_unselected_rows(db):
    db.add_all([
        StockRawData(symbol="2330.TW", record_date=DAY, raw_data_is_final=True),
        StockRawData(symbol="2355.TW", record_date=DAY, raw_data_is_final=True),
        StockRawData(symbol="0050.TW", record_date=DAY, raw_data_is_final=True),
        StockRawData(symbol="5009.TWO", record_date=DAY, raw_data_is_final=False),
        StockRawData(symbol="5521.TW", record_date=date(2026, 10, 14), raw_data_is_final=True),
    ])
    db.flush()
    assert service.resolve_radar_fundamental_symbols(db, run_date=DAY, stage="raw_pool") == ["2330.TW", "2355.TW"]
    assert service.resolve_radar_fundamental_symbols(db, run_date=DAY, stage="prepared") == ["2330.TW"]


def test_missing_raw_refresh_and_future_date_start_are_rejected(db, monkeypatch):
    prepared = db.scalar(select(DailyRadarPreparedRun))
    prepared.step_statuses = {"prepare-universe": {"status": "completed"}}
    db.flush()
    with pytest.raises(ValueError, match="radar_raw_pool_not_ready"):
        service.resolve_radar_fundamental_symbols(db, run_date=DAY, stage="raw_pool")
    monkeypatch.setattr(service, "today_taipei", lambda: date(2026, 10, 14))
    with pytest.raises(ValueError, match="radar_fundamental_date_closed"):
        service.resolve_radar_fundamental_symbols(db, run_date=DAY, stage="prepared")


def test_radar_request_creates_date_bound_snapshot_before_evidence(db, monkeypatch):
    calls = []
    def fetch(session, *, symbols, after_symbol, limit, **kwargs):
        calls.append(list(symbols))
        return service.FundamentalBackfillResult("ok", list(symbols), 0, None, [])
    monkeypatch.setattr(router, "backfill_fundamentals", fetch)
    response = router.backfill_fundamentals_endpoint(FundamentalBackfillRequest(
        raw_pool_date=DAY, radar_stage="prepared"), db=db)
    assert calls == [["2330.TW"]]
    assert response["raw_pool_date"] == DAY
    assert response["next_after_symbol"] is None


def test_historical_request_does_not_call_provider(db, monkeypatch):
    monkeypatch.setattr(service, "today_taipei", lambda: date(2026, 10, 16))
    monkeypatch.setattr(router, "backfill_fundamentals", lambda *a, **kw: pytest.fail("historical preparation must not fetch"))
    with pytest.raises(HTTPException) as error:
        router.backfill_fundamentals_endpoint(FundamentalBackfillRequest(
            raw_pool_date=DAY, radar_stage="prepared"), db=db)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "radar_fundamental_date_closed"


def test_delayed_workflow_reuses_historical_cache_without_fetch_or_new_job(db, monkeypatch):
    store_fundamental_periods(db, normalize_finmind_statement_rows([
        {"date": f"2025-{month}-{'31' if month in ('03', '12') else '30'}", "type": "EPS", "value": "1"}
        for month in ("03", "06", "09", "12")], symbol="2330.TW"))
    for row in db.scalars(select(CompanyFundamentalPeriod)):
        row.first_observed_at = datetime(2026, 10, 15, 10, tzinfo=timezone.utc)
    db.commit()
    monkeypatch.setattr(service, "today_taipei", lambda: date(2026, 10, 16))
    monkeypatch.setattr(router, "backfill_fundamentals", lambda *a, **kw: pytest.fail("cache is historically complete"))
    result = router.backfill_fundamentals_endpoint(FundamentalBackfillRequest(
        raw_pool_date=DAY, radar_stage="prepared"), db=db)
    assert result["cache_ready_as_of"] is True
    assert result["job_id"] is None
    assert list(db.scalars(select(FundamentalBackfillJob))) == []


def test_continuation_cannot_cross_date_or_change_frozen_symbols(db, monkeypatch):
    job = service.create_fundamental_backfill_job(db, symbols=["2355.TW"], raw_pool_date=DAY, purpose="radar_ttm_eps", radar_stage="prepared")
    db.commit()
    calls = []
    def fetch(session, *, symbols, after_symbol, limit, **kwargs):
        calls.append(list(symbols))
        return service.FundamentalBackfillResult("ok", list(symbols), 0, None, [])
    monkeypatch.setattr(router, "backfill_fundamentals", fetch)
    response = router.backfill_fundamentals_endpoint(FundamentalBackfillRequest(
        raw_pool_date=DAY, radar_stage="prepared", job_id=job.id), db=db)
    assert calls == [["2355.TW"]]  # The current prepared list contains 2330, not 2355.
    assert response["raw_pool_date"] == DAY
    pending = service.create_fundamental_backfill_job(db, symbols=["2355.TW"], raw_pool_date=DAY, purpose="radar_ttm_eps", radar_stage="prepared")
    db.commit()
    monkeypatch.setattr(service, "today_taipei", lambda: date(2026, 10, 16))
    with pytest.raises(HTTPException) as error:
        router.backfill_fundamentals_endpoint(FundamentalBackfillRequest(
            raw_pool_date=DAY, radar_stage="prepared", job_id=pending.id), db=db)
    assert error.value.detail["code"] == "radar_fundamental_date_closed"
    assert len(calls) == 1


def test_locking_read_refreshes_a_cached_cursor_and_rejects_stale_continuation(db, monkeypatch):
    job = service.create_fundamental_backfill_job(db, symbols=["2330.TW", "2355.TW"], raw_pool_date=DAY, purpose="radar_ttm_eps", radar_stage="prepared")
    db.commit()
    cached = service.get_fundamental_backfill_job(db, job_id=job.id)
    assert cached.next_after_symbol is None
    # Model another worker's persisted update without synchronizing the identity map.
    db.execute(update(FundamentalBackfillJob).where(FundamentalBackfillJob.id == job.id)
        .values(next_after_symbol="2330.TW").execution_options(synchronize_session=False))
    assert cached.next_after_symbol is None
    monkeypatch.setattr(router, "backfill_fundamentals", lambda *a, **kw: pytest.fail("stale cursor must not fetch"))
    with pytest.raises(HTTPException) as error:
        router.backfill_fundamentals_endpoint(FundamentalBackfillRequest(
            raw_pool_date=DAY, radar_stage="prepared", job_id=job.id), db=db)
    assert error.value.detail == {"code": "fundamental_backfill_cursor_mismatch", "expected_after_symbol": "2330.TW"}


def test_diagnostics_distinguish_late_observation_from_missing_history(db):
    store_fundamental_periods(db, normalize_finmind_statement_rows([
        {"date": f"2025-{month}-{'31' if month in ('03', '12') else '30'}", "type": "EPS", "value": "1"}
        for month in ("03", "06", "09", "12")], symbol="2355.TW"))
    for row in db.scalars(select(CompanyFundamentalPeriod)).all():
        row.first_observed_at = datetime(2026, 10, 16, 1, tzinfo=timezone.utc)
    db.flush()
    details = service.describe_fundamental_gaps(db, symbols=["2355.TW", "5521.TW"], as_of_date=DAY)
    by_symbol = {item["symbol"]: item for item in details}
    assert by_symbol["2355.TW"] == {"symbol": "2355.TW", "reason": "historical_observation_after_run_date",
        "as_of_date": "2026-10-15", "visible_period_count": 0, "latest_observed_date": "2026-10-16"}
    assert by_symbol["5521.TW"]["reason"] == "no_eps_history"
    # The diagnostic must never rewrite first-observed timestamps or expose future EPS as historical evidence.
    assert all(row.first_observed_at.date() == date(2026, 10, 16) for row in db.scalars(select(CompanyFundamentalPeriod)))
    assert "ttm_eps" not in by_symbol["2355.TW"]
