"""Memory boundaries must preserve the validation and transaction contracts."""
from copy import deepcopy
from datetime import date, timedelta
import json
import logging

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration import repository
from ai_stock_sentinel.daily_radar import forward_validation as validation
from ai_stock_sentinel.daily_radar import maintenance_router
from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate, DailyRadarForwardValidationResult, DailyRadarPreparedRun,
    DailyRadarRun, StockRawData,
)
from ai_stock_sentinel.db.session import Base
from test_daily_radar_forward_validation import (
    _add_candidate, _add_run, _forward_validation_sqlite_engine, _price,
)


def engine_with_tables():
    engine = _forward_validation_sqlite_engine()
    Base.metadata.create_all(engine, tables=[model.__table__ for model in (
        DailyRadarRun, DailyRadarCandidate, DailyRadarForwardValidationResult,
        DailyRadarPreparedRun, StockRawData,
    )])
    return engine


def test_candidate_projection_preserves_full_snapshot_results_without_large_replay():
    engine = engine_with_tables()
    with Session(engine) as session:
        candidate = _add_candidate(session, _add_run(session))
        candidate.input_snapshot = dict(candidate.input_snapshot) | {
            "ohlcv": {"close": 100, "irrelevant": "x" * 100_000},
            "indicators": {"support_level": 96, "resistance_level": 102, "ma20": 98, "ma60": 95},
            "versions": {"scoring_version": "s1", "rule_version": "r1", "config_version": "c1"},
            "selection_version": "policy1",
            "replay_input": {"market_context": {"benchmark": {"symbol": "TAIEX"}},
                             "fundamental": {"large_unused_payload": "x" * 100_000}},
        }
        candidate.score_breakdown = dict(candidate.score_breakdown) | {
            "unused_scoring_evidence": "x" * 100_000,
            "market_context": {"details": {"regime": "defensive"}},
        }
        original_score = deepcopy(candidate.score_breakdown)
        original = deepcopy(candidate.input_snapshot)
        session.commit()
        session.expunge_all()
        projected = validation.forward_validation_candidates_from_runs(session, market="TW")
        assert len(json.dumps(projected)) < len(json.dumps(original)) / 20
        assert not session.identity_map  # no ORM snapshot retained by the loader
        full = deepcopy(projected)
        full[0]["input_snapshot"] = original
        full[0]["score_breakdown"] = original_score
        prices = [_price(f"2026-06-0{i}", 100 + i, 102 + i, 99 + i, 100 + i) for i in range(1, 7)]
        kwargs = dict(price_series_by_symbol={"2330.TW": prices}, benchmark_prices=prices,
                      market="TW", sample_source="unit", as_of_date=date(2026, 6, 6), windows=[2, 3, 5])
        before = validation.build_forward_validation_report(full, **kwargs)
        after = validation.build_forward_validation_report(projected, **kwargs)
        assert after.outcomes == before.outcomes
        assert after.report == before.report
        assert projected[0]["observation_origin"]["candidate_id"] == projected[0]["candidate_id"]


def test_raw_loader_streams_without_unrelated_json_and_preserves_cross_batch_conflict():
    engine = engine_with_tables()
    start = date(2026, 6, 1)
    batch_size = 128
    source_rows = []
    for index in range(batch_size + 2):
        history = [_price("2026-06-01", 100 if index < batch_size else 101, 110, 90, 100),
                   _price("2026-05-31", 99, 110, 90, 100)]
        technical = {"price_history": history, "recent_closes": [100],
                     "recent_close_dates": ["2026-06-01"]}
        source_rows.append({"raw_data_is_final": True, "technical": technical})
        with Session(engine) as session:
            session.add(StockRawData(symbol="2330.TW", record_date=start + timedelta(days=index),
                raw_data_is_final=True, technical=technical,
                fundamental={"unused": "x" * 10_000}, institutional={"unused": "y" * 10_000}))
            session.commit()
    with Session(engine) as session:
        session.add(StockRawData(symbol="2330.TW", record_date=start + timedelta(days=132),
            raw_data_is_final=False, technical={"price_history": [_price("2026-06-01", 999, 999, 999, 999)]}))
        session.add(StockRawData(symbol="2317.TW", record_date=start, raw_data_is_final=True,
            technical={"price_history": [_price("2026-06-01", 200, 210, 190, 200)]}))
        session.commit()
        options, statements = [], []
        event.listen(session, "do_orm_execute", lambda state: options.append(dict(state.execution_options)))
        event.listen(engine, "before_cursor_execute", lambda conn, cursor, statement, params, context, many:
                     statements.append(statement))
        prices = repository.load_price_series_from_raw_data(session,
            symbols=["2330.TW", "2317.TW", "2330.TW"], start_date=start, end_date=date(2026, 10, 15))
        assert any(option.get("yield_per") == batch_size for option in options)
        assert all("stock_raw_data.fundamental" not in sql and "stock_raw_data.institutional" not in sql
                   for sql in statements)
        assert prices["2330.TW"] == repository.completed_price_rows_from_raw_data(
            source_rows, start_date=start, end_date=date(2026, 10, 15))
        assert prices["2330.TW"][0]["ohlc_conflict_fields"] == ["open"]
        assert len(prices["2330.TW"]) == 1
        assert prices["2317.TW"][0]["close"] == 200
        assert not session.identity_map


def test_persisted_identity_queries_do_not_reload_full_snapshots():
    engine = engine_with_tables()
    with Session(engine) as session:
        candidate = _add_candidate(session, _add_run(session))
        candidate.input_snapshot = dict(candidate.input_snapshot) | {
            "replay_input": {"market_context": {"benchmark": {"symbol": "SPX"}}, "unused": "x" * 100_000}}
        session.commit()
        candidate_id = candidate.id
        session.expunge_all()
        selected_columns = []
        event.listen(session, "do_orm_execute", lambda state:
                     selected_columns.extend(state.statement.selected_columns) if state.is_select else None)
        outcome = {"candidate_id": candidate_id, "signal_date": "2026-06-01", "window_days": 5,
            "validation_version": validation.FORWARD_VALIDATION_VERSION, "status": "validated",
            "benchmark_symbol": "SPX", "evaluation_as_of_date": "2026-06-08", "outcome": {}}
        validation.upsert_forward_validation_results(session, [outcome])
        pending = {f"id:{candidate_id}": [5, 10]}
        assert validation.exclude_persisted_daily_radar_windows(session, pending, benchmark_symbol="SPX") == {
            f"id:{candidate_id}": [10]}
        assert validation.exclude_persisted_daily_radar_windows(session, pending, benchmark_symbol="TAIEX") == pending
        assert selected_columns
        assert not any(column.compare(DailyRadarCandidate.input_snapshot.__clause_element__())
                       for column in selected_columns)
        assert not any(isinstance(row, DailyRadarCandidate) for row in session.identity_map.values())


def test_price_refresh_batches_all_symbols_and_stops_on_failure(monkeypatch):
    from ai_stock_sentinel.calibration import forward_validation_planning as planning
    monkeypatch.setattr(planning, "FORWARD_PRICE_FETCH_BATCH_SIZE", 2)
    from test_daily_radar_forward_validation import _candidate_snapshot
    candidates = [_candidate_snapshot() | {"candidate_id": i + 1, "symbol": f"{i}.TW"} for i in range(5)]
    benchmark = [_price("2026-06-01", 100, 100, 100, 100), _price("2026-06-02", 101, 101, 101, 101)]
    calls = []
    def fetch(symbols, **kwargs):
        calls.append(list(symbols))
        return {symbol: benchmark for symbol in symbols}
    kwargs = dict(adapter=validation.DAILY_RADAR_FORWARD_ADAPTER,
        pending_windows_by_candidate={f"id:{i+1}": [1] for i in range(5)},
        price_series_by_symbol={}, benchmark_prices=benchmark, benchmark_symbol="TAIEX",
        as_of_date=date(2026, 6, 2), price_start_date=date(2026, 6, 1))
    result = planning.prepare_due_forward_validation(candidates, fetch_prices=fetch, **kwargs)
    assert calls == [["0.TW", "1.TW"], ["2.TW", "3.TW"], ["4.TW"]]
    assert result.evaluation_windows_by_candidate == kwargs["pending_windows_by_candidate"]
    calls.clear()
    def failing_fetch(symbols, **kwargs):
        if len(calls) == 1:
            raise RuntimeError("provider_failure")
        return fetch(symbols, **kwargs)
    with pytest.raises(RuntimeError, match="provider_failure"):
        planning.prepare_due_forward_validation(candidates, fetch_prices=failing_fetch, **kwargs)
    assert calls == [["0.TW", "1.TW"]]


def test_stage_metrics_record_completion_and_failure_without_masking_errors(caplog):
    from ai_stock_sentinel.calibration.runtime_metrics import ForwardValidationMetrics
    caplog.set_level(logging.INFO)
    metrics = ForwardValidationMetrics(logging.getLogger("unit.forward"), as_of_date=date(2026, 10, 5))
    with metrics.stage("candidates", candidate_count=3):
        pass
    with pytest.raises(ValueError, match="original"):
        with metrics.stage("provider"):
            raise ValueError("original")
    records = [r for r in caplog.records if hasattr(r, "validation_stage")]
    assert [(r.validation_stage, r.stage_event) for r in records] == [
        ("candidates", "started"), ("candidates", "completed"), ("provider", "started"), ("provider", "failed")]
    assert len({r.validation_run_id for r in records}) == 1
    assert all(r.peak_rss_bytes > 0 for r in records)


def test_endpoint_rolls_back_all_written_results_if_commit_fails(monkeypatch):
    engine = engine_with_tables()
    with Session(engine) as session:
        _add_candidate(session, _add_run(session))
        session.commit()
    benchmark = [_price("2026-06-01", 100, 100, 100, 100), _price("2026-06-02", 101, 101, 101, 101)]
    monkeypatch.setattr(maintenance_router, "load_price_series_from_raw_data",
        lambda *args, **kwargs: {"2330.TW": benchmark, "TAIEX": benchmark})
    class Provider:
        def fetch(self, *args, **kwargs):
            raise AssertionError("complete prices must not be fetched")
    with Session(engine) as session:
        def fail_commit():
            raise RuntimeError("commit_failed")
        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit_failed"):
            maintenance_router.run_daily_radar_forward_validation_endpoint(
                DailyRadarForwardValidationRunRequest(mode="due", as_of_date=date(2026, 6, 2), windows=[1]),
                db=session, price_provider=Provider())
        assert session.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 1
        session.rollback()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 0


def test_endpoint_batches_provider_and_commits_once_with_idempotent_results(monkeypatch, caplog):
    from ai_stock_sentinel.calibration import forward_validation_planning as planning
    monkeypatch.setattr(planning, "FORWARD_PRICE_FETCH_BATCH_SIZE", 2)
    caplog.set_level(logging.INFO)
    engine = engine_with_tables()
    with Session(engine) as session:
        run = _add_run(session)
        run.candidate_count = 5
        for index in range(5):
            _add_candidate(session, run, symbol=f"{index}.TW")
        session.commit()
    benchmark = [_price("2026-06-01", 100, 100, 100, 100), _price("2026-06-02", 101, 101, 101, 101)]
    monkeypatch.setattr(maintenance_router, "load_price_series_from_raw_data",
        lambda *args, **kwargs: {"TAIEX": benchmark})
    calls = []
    class Provider:
        def fetch(self, symbols, **kwargs):
            calls.append(list(symbols))
            return {symbol: benchmark for symbol in symbols}
    request = DailyRadarForwardValidationRunRequest(mode="due", as_of_date=date(2026, 6, 2), windows=[1])
    with Session(engine, autoflush=False) as session:
        commits = []
        event.listen(session, "after_commit", lambda session: commits.append(True))
        result = maintenance_router.run_daily_radar_forward_validation_endpoint(request, db=session, price_provider=Provider())
        assert len(commits) == 1
        assert result.candidate_count == result.validated_count == result.records_written == 5
        assert calls == [["0.TW", "1.TW"], ["2.TW", "3.TW"], ["4.TW"]]
        assert session.scalar(select(func.count()).select_from(DailyRadarForwardValidationResult)) == 5
        first_report = result.report
        calls.clear()
        result = maintenance_router.run_daily_radar_forward_validation_endpoint(request, db=session, price_provider=Provider())
        assert result.records_written == 0
        assert result.report == first_report
        assert calls == []
    provider_records = [record for record in caplog.records if getattr(record, "validation_stage", None) == "fetch_prices"]
    assert len(provider_records) == 6  # three batches, each with start/end checkpoints
    assert all("batch_symbol_count" in record.getMessage() for record in provider_records)
