from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarRun,
    Phase1AvwapSnapshot,
    User,
    UserPortfolio,
    UserWatchlist,
)
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.phase1_avwap.calculator import DailyPriceBar, build_phase1_avwap_payload
from ai_stock_sentinel.phase1_avwap.provider import normalize_finmind_daily_price_rows
from ai_stock_sentinel.phase1_avwap.projection import (
    read_phase1_avwap_contexts_for_daily_radar,
    read_phase1_current_day_observations_for_managed_universe,
    read_phase1_observation_for_analyze,
    read_phase1_position_states_for_portfolio,
)
from ai_stock_sentinel.phase1_avwap.repository import upsert_phase1_avwap_snapshot
from ai_stock_sentinel.phase1_avwap.service import (
    refresh_phase1_avwap_snapshots,
    refresh_phase1_avwap_snapshots_for_symbols,
)
from ai_stock_sentinel.phase1_avwap.universe import resolve_phase1_managed_universe


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            UserPortfolio.__table__,
            UserWatchlist.__table__,
            DailyRadarRun.__table__,
            DailyRadarCandidate.__table__,
            Phase1AvwapSnapshot.__table__,
        ],
    )
    with Session(engine) as session:
        yield session


class FakeDailyPriceProvider:
    def __init__(self, bars_by_symbol: dict[str, list[DailyPriceBar]] | None = None) -> None:
        self.bars_by_symbol = bars_by_symbol or {}
        self.calls: list[tuple[str, date, date]] = []

    def fetch_history(self, symbol: str, *, start_date: date, end_date: date) -> list[DailyPriceBar]:
        self.calls.append((symbol, start_date, end_date))
        if symbol in self.bars_by_symbol:
            return self.bars_by_symbol[symbol]
        return _bars(end_date)


def test_normalize_finmind_rows_uses_trading_money_and_marks_fallback_estimated() -> None:
    rows = normalize_finmind_daily_price_rows(
        [
            {
                "date": "2026-06-02",
                "open": 10,
                "max": 12,
                "min": 9,
                "close": 11,
                "Trading_Volume": 100,
                "Trading_money": 1100,
            },
            {
                "date": "2026-06-03",
                "open": 11,
                "max": 13,
                "min": 10,
                "close": 12,
                "Trading_Volume": 200,
            },
        ]
    )

    assert rows[0].amount == 1100
    assert rows[0].estimated_amount is False
    assert rows[1].amount == pytest.approx(((13 + 10 + 12) / 3) * 200)
    assert rows[1].estimated_amount is True


def test_build_phase1_avwap_payload_computes_daily_anchors_from_amount_over_volume() -> None:
    payload = build_phase1_avwap_payload(
        symbol="2330.TW",
        bars=_bars(date(2026, 6, 5)),
        data_date=date(2026, 6, 5),
        dataset="TaiwanStockPrice",
        adjustment_mode="unadjusted",
        holding_entry_date=date(2026, 6, 3),
        holding_avg_cost=13.5,
    )

    assert payload["source_granularity"] == "daily"
    assert payload["data_quality"]["estimated"] is False
    assert payload["anchors"]["swing_low_60d"]["anchor_date"] == "2026-06-02"
    assert payload["anchors"]["swing_low_60d"]["avwap"] == pytest.approx(14.6667)
    assert payload["anchors"]["breakout_20d"]["anchor_date"] == "2026-06-05"
    assert payload["anchors"]["breakout_20d"]["avwap"] == pytest.approx(18.0)
    assert payload["anchors"]["high_volume_60d"]["anchor_date"] == "2026-06-03"
    assert payload["anchors"]["entry"]["anchor_date"] == "2026-06-03"
    assert payload["anchors"]["entry"]["avwap"] == pytest.approx(15.2)


def test_resolve_phase1_managed_universe_merges_holdings_watchlist_and_latest_daily_radar(
    db_session: Session,
) -> None:
    _seed_user_universe(db_session)

    universe = resolve_phase1_managed_universe(db_session, user_id=1, market="TW")

    assert [item.symbol for item in universe] == ["2330.TW", "2454.TW", "2317.TW"]
    by_symbol = {item.symbol: item for item in universe}
    assert by_symbol["2330.TW"].sources == ["active_holding", "daily_radar_candidate"]
    assert by_symbol["2330.TW"].holding_entry_date == date(2026, 1, 15)
    assert by_symbol["2330.TW"].holding_avg_cost == 900.0
    assert by_symbol["2454.TW"].sources == ["watchlist"]
    assert by_symbol["2317.TW"].sources == ["daily_radar_candidate"]


def test_refresh_phase1_avwap_snapshots_reuses_fresh_rows_before_fetching(
    db_session: Session,
) -> None:
    _seed_user_universe(db_session)
    data_date = date(2026, 6, 5)
    upsert_phase1_avwap_snapshot(
        db_session,
        symbol="2454.TW",
        data_date=data_date,
        payload={"symbol": "2454.TW", "data_quality": {"estimated": False}},
        freshness="fresh",
    )
    provider = FakeDailyPriceProvider()

    result = refresh_phase1_avwap_snapshots(
        db_session,
        user_id=1,
        data_date=data_date,
        lookback_days=30,
        provider=provider,
    )

    assert result.reused_symbols == ["2454.TW"]
    assert result.fetched_symbols == ["2330.TW", "2317.TW"]
    assert result.missing_symbols == []
    assert provider.calls == [
        ("2330.TW", date(2026, 1, 15), data_date),
        ("2317.TW", data_date - timedelta(days=30), data_date),
    ]
    rows = db_session.scalars(select(Phase1AvwapSnapshot).order_by(Phase1AvwapSnapshot.symbol)).all()
    assert [row.symbol for row in rows] == ["2317.TW", "2330.TW", "2454.TW"]
    assert rows[0].dataset == "TaiwanStockPrice"
    assert rows[0].adjustment_mode == "unadjusted"
    assert rows[0].payload["anchors"]["swing_low_60d"]["avwap"] == pytest.approx(14.6667)


def test_refresh_phase1_avwap_snapshots_marks_missing_when_requested_date_row_is_absent(
    db_session: Session,
) -> None:
    _seed_user_with_active_holding(db_session)
    data_date = date(2026, 6, 5)
    provider = FakeDailyPriceProvider({"2330.TW": _bars_until(date(2026, 6, 4))})

    result = refresh_phase1_avwap_snapshots(
        db_session,
        user_id=1,
        data_date=data_date,
        provider=provider,
    )

    assert result.fetched_symbols == ["2330.TW"]
    assert result.missing_symbols == ["2330.TW"]
    snapshot = result.snapshots[0]
    assert snapshot.symbol == "2330.TW"
    assert snapshot.data_date == data_date
    assert snapshot.freshness == "missing"
    assert snapshot.missing_reason == "daily_price_row_missing_for_data_date"
    assert snapshot.payload["data_quality"]["missing_reason"] == "daily_price_row_missing_for_data_date"
    assert snapshot.payload["anchors"] == {}


def test_refresh_phase1_avwap_snapshots_for_symbols_refreshes_daily_radar_selection(
    db_session: Session,
) -> None:
    data_date = date(2026, 6, 5)
    provider = FakeDailyPriceProvider()

    result = refresh_phase1_avwap_snapshots_for_symbols(
        db_session,
        symbols=["2454.TW", "2454.tw", "2317.TW"],
        data_date=data_date,
        lookback_days=30,
        provider=provider,
    )

    assert [item.symbol for item in result.universe] == ["2454.TW", "2317.TW"]
    assert [item.sources for item in result.universe] == [["daily_radar_candidate"], ["daily_radar_candidate"]]
    assert result.reused_symbols == []
    assert result.fetched_symbols == ["2454.TW", "2317.TW"]
    assert provider.calls == [
        ("2454.TW", data_date - timedelta(days=30), data_date),
        ("2317.TW", data_date - timedelta(days=30), data_date),
    ]
    rows = db_session.scalars(select(Phase1AvwapSnapshot).order_by(Phase1AvwapSnapshot.symbol)).all()
    assert [row.symbol for row in rows] == ["2317.TW", "2454.TW"]
    assert all(row.freshness == "fresh" for row in rows)


def test_read_phase1_observation_for_analyze_returns_snapshot_payload_for_managed_symbol(
    db_session: Session,
) -> None:
    _seed_user_with_active_holding(db_session)
    data_date = date(2026, 6, 5)
    upsert_phase1_avwap_snapshot(
        db_session,
        symbol="2330.TW",
        data_date=data_date,
        payload={
            "symbol": "2330.TW",
            "data_date": data_date.isoformat(),
            "anchors": {"swing_low_60d": {"avwap": 900.25}},
            "data_quality": {"estimated": False, "missing_reason": None},
        },
        freshness="fresh",
    )

    observation = read_phase1_observation_for_analyze(
        db_session,
        user_id=1,
        symbol="2330.TW",
        data_date=data_date,
    )

    assert observation["freshness"] == "fresh"
    assert observation["missing_reason"] is None
    assert observation["anchors"]["swing_low_60d"]["avwap"] == 900.25
    assert observation["source"] == {
        "provider": "finmind",
        "dataset": "TaiwanStockPrice",
        "adjustment_mode": "unadjusted",
    }


def test_read_phase1_observation_for_analyze_reports_snapshot_missing_for_managed_symbol(
    db_session: Session,
) -> None:
    _seed_user_with_active_holding(db_session)

    observation = read_phase1_observation_for_analyze(
        db_session,
        user_id=1,
        symbol="2330.TW",
        data_date=date(2026, 6, 5),
    )

    assert observation["freshness"] == "missing"
    assert observation["missing_reason"] == "phase1_snapshot_missing"
    assert observation["data_quality"]["blocking"] is False


def test_read_phase1_observation_for_analyze_reports_out_of_universe_without_fetching(
    db_session: Session,
) -> None:
    _seed_user_with_active_holding(db_session)

    observation = read_phase1_observation_for_analyze(
        db_session,
        user_id=1,
        symbol="9999.TW",
        data_date=date(2026, 6, 5),
    )

    assert observation["freshness"] == "missing"
    assert observation["missing_reason"] == "not_in_phase1_universe"
    assert observation["anchors"] == {}


def test_read_phase1_observation_for_analyze_reports_read_failure_as_nonblocking(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_stock_sentinel.phase1_avwap.projection as projection_module

    def _raise(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(projection_module, "resolve_phase1_managed_universe", _raise)

    observation = projection_module.read_phase1_observation_for_analyze(
        db_session,
        user_id=1,
        symbol="2330.TW",
        data_date=date(2026, 6, 5),
    )

    assert observation["freshness"] == "missing"
    assert observation["missing_reason"] == "phase1_snapshot_read_failed"
    assert observation["data_quality"]["blocking"] is False


def test_read_phase1_position_states_for_portfolio_projects_snapshot_state(
    db_session: Session,
) -> None:
    data_date = date(2026, 6, 5)
    upsert_phase1_avwap_snapshot(
        db_session,
        symbol="2330.TW",
        data_date=data_date,
        payload={
            "symbol": "2330.TW",
            "data_date": data_date.isoformat(),
            "anchors": {
                "entry": {
                    "available": True,
                    "anchor_date": "2026-01-15",
                    "anchor_reason": "holding_entry_date",
                    "avwap": 900.0,
                    "distance_to_avwap_pct": 3.5,
                    "source_granularity": "daily",
                    "estimated": False,
                },
                "breakout_20d": {
                    "available": True,
                    "anchor_date": "2026-06-05",
                    "anchor_reason": "breakout_20d_high",
                    "avwap": 910.0,
                    "distance_to_avwap_pct": 2.4,
                },
            },
            "data_quality": {"estimated": False, "rows_used": 80},
        },
        freshness="fresh",
    )

    states = read_phase1_position_states_for_portfolio(
        db_session,
        symbols=["2330.TW"],
        data_date=data_date,
    )

    state = states["2330.TW"]
    assert state["state"] == "hold"
    assert state["label"] == "續抱"
    assert state["display_anchor"]["type"] == "entry"
    assert state["display_anchor"]["distance_to_avwap_pct"] == 3.5
    assert state["matched_rules"] == ["phase1_display_anchor_supported"]
    assert state["data_quality"]["blocking"] is False


def test_read_phase1_position_states_for_portfolio_reports_missing_distance_reason(
    db_session: Session,
) -> None:
    data_date = date(2026, 6, 5)
    upsert_phase1_avwap_snapshot(
        db_session,
        symbol="2330.TW",
        data_date=data_date,
        payload={
            "symbol": "2330.TW",
            "data_date": data_date.isoformat(),
            "anchors": {
                "entry": {
                    "available": True,
                    "anchor_date": "2026-01-15",
                    "anchor_reason": "holding_entry_date",
                    "avwap": 900.0,
                    "source_granularity": "daily",
                    "estimated": False,
                },
            },
            "data_quality": {"estimated": False, "rows_used": 80},
        },
        freshness="fresh",
    )

    states = read_phase1_position_states_for_portfolio(
        db_session,
        symbols=["2330.TW"],
        data_date=data_date,
    )

    state = states["2330.TW"]
    assert state["state"] == "data_unavailable"
    assert state["missing_reason"] == "phase1_distance_to_avwap_missing"
    assert state["data_quality"]["missing_reason"] == "phase1_distance_to_avwap_missing"
    assert state["data_quality"]["blocking"] is False


def test_read_phase1_position_states_for_portfolio_reports_read_failure_as_nonblocking(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_stock_sentinel.phase1_avwap.projection as projection_module

    def _raise(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(projection_module, "get_phase1_avwap_snapshots", _raise)

    states = projection_module.read_phase1_position_states_for_portfolio(
        db_session,
        symbols=["2330.TW"],
        data_date=date(2026, 6, 5),
    )

    state = states["2330.TW"]
    assert state["state"] == "data_unavailable"
    assert state["missing_reason"] == "phase1_snapshot_read_failed"
    assert state["data_quality"]["blocking"] is False


def test_read_phase1_current_day_observations_classifies_non_holding_managed_symbols(
    db_session: Session,
) -> None:
    _seed_user_universe(db_session)
    data_date = date(2026, 6, 5)
    upsert_phase1_avwap_snapshot(
        db_session,
        symbol="2330.TW",
        data_date=data_date,
        payload=_phase1_snapshot_payload(symbol="2330.TW", close=930, swing_distance=4, breakout_distance=2),
        freshness="fresh",
    )
    upsert_phase1_avwap_snapshot(
        db_session,
        symbol="2454.TW",
        data_date=data_date,
        payload=_phase1_snapshot_payload(symbol="2454.TW", close=100, swing_distance=3, breakout_distance=8),
        freshness="fresh",
    )
    upsert_phase1_avwap_snapshot(
        db_session,
        symbol="2317.TW",
        data_date=data_date,
        payload=_phase1_snapshot_payload(symbol="2317.TW", close=100, swing_distance=7, breakout_distance=2),
        freshness="fresh",
    )

    observations = read_phase1_current_day_observations_for_managed_universe(
        db_session,
        user_id=1,
        data_date=data_date,
    )

    assert sorted(observations) == ["2317.TW", "2454.TW"]
    assert observations["2454.TW"]["state"] == "pullback_watch"
    assert observations["2454.TW"]["label"] == "建倉"
    assert observations["2454.TW"]["display_anchor"]["type"] == "swing_low_60d"
    assert observations["2317.TW"]["state"] == "strong_breakout"
    assert observations["2317.TW"]["matched_rules"] == ["phase1_breakout_anchor_supported_within_5pct"]
    assert observations["2317.TW"]["data_quality"]["blocking"] is False


def test_read_phase1_current_day_observations_reports_read_failure_as_nonblocking(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_stock_sentinel.phase1_avwap.projection as projection_module

    _seed_user_universe(db_session)

    def _raise(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(projection_module, "get_phase1_avwap_snapshots", _raise)

    observations = projection_module.read_phase1_current_day_observations_for_managed_universe(
        db_session,
        user_id=1,
        data_date=date(2026, 6, 5),
    )

    assert sorted(observations) == ["2317.TW", "2454.TW"]
    watchlist_observation = observations["2454.TW"]
    assert watchlist_observation["freshness"] == "missing"
    assert watchlist_observation["missing_reason"] == "phase1_snapshot_read_failed"
    assert watchlist_observation["matched_rules"] == ["phase1_current_day_observation_unavailable"]
    assert watchlist_observation["data_quality"]["blocking"] is False


def test_read_phase1_avwap_contexts_for_daily_radar_reports_read_failure_as_nonblocking(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ai_stock_sentinel.phase1_avwap.projection as projection_module

    def _raise(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(projection_module, "get_phase1_avwap_snapshots", _raise)

    contexts = projection_module.read_phase1_avwap_contexts_for_daily_radar(
        db_session,
        symbols=["2330.TW"],
        data_date=date(2026, 6, 5),
    )

    context = contexts["2330.TW"]
    assert context["freshness"] == "missing"
    assert context["missing_reason"] == "phase1_snapshot_read_failed"
    assert context["applicable_consumers"] == ["daily_radar"]
    assert context["data_quality"]["blocking"] is False


def test_phase1_avwap_migration_creates_snapshot_table_constraints_and_indexes() -> None:
    sql = _render_migration_sql("upgrade")

    assert "CREATE TABLE phase1_avwap_snapshots" in sql
    assert "CONSTRAINT uq_phase1_avwap_symbol_date_dataset_mode UNIQUE" in sql
    assert "CONSTRAINT ck_phase1_avwap_snapshot_freshness CHECK" in sql
    assert "CREATE INDEX idx_phase1_avwap_snapshots_symbol" in sql
    assert "CREATE INDEX idx_phase1_avwap_snapshots_data_date" in sql
    assert "JSONB" in sql


def _seed_user_universe(session: Session) -> None:
    _seed_user_with_active_holding(session)
    session.add(UserWatchlist(user_id=1, symbol="2454.TW", sort_order=0))
    run = DailyRadarRun(
        run_date=date(2026, 6, 5),
        market="TW",
        status="completed",
        universe_count=3,
        prefilter_count=2,
        candidate_count=2,
        errors=[],
        created_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    _add_candidate(session, run, symbol="2317.TW", score=88)
    _add_candidate(session, run, symbol="2330.TW", score=95)
    session.flush()


def _seed_user_with_active_holding(session: Session) -> None:
    session.add(User(id=1, google_sub="user-1", email="user@example.com"))
    session.flush()
    session.add(
        UserPortfolio(
            user_id=1,
            symbol="2330.TW",
            entry_price=900,
            quantity=1000,
            entry_date=date(2026, 1, 15),
            is_active=True,
        )
    )
    session.flush()


def _add_candidate(session: Session, run: DailyRadarRun, *, symbol: str, score: int) -> None:
    session.add(
        DailyRadarCandidate(
            run_id=run.id,
            symbol=symbol,
            name=symbol,
            primary_bucket="price_volume_strengthening",
            secondary_buckets=[],
            observation_score=score,
            bucket_scores={},
            risk_labels=[],
            matched_rules=[],
            explanation="fixture",
            repeat_status="new",
            score_breakdown={},
            input_snapshot={},
            data_dates={"ohlcv": run.run_date.isoformat()},
        )
    )


def _bars(end_date: date) -> list[DailyPriceBar]:
    return [
        DailyPriceBar(date(2026, 6, 1), 10, 10, 9, 10, 100, 1000),
        DailyPriceBar(date(2026, 6, 2), 11, 12, 8, 12, 100, 1200),
        DailyPriceBar(date(2026, 6, 3), 13, 15, 13, 14, 300, 4200),
        DailyPriceBar(date(2026, 6, 4), 15, 16, 15, 16, 100, 1600),
        DailyPriceBar(end_date, 17, 20, 17, 18, 100, 1800),
    ]


def _bars_until(end_date: date) -> list[DailyPriceBar]:
    return [bar for bar in _bars(date(2026, 6, 5)) if bar.trade_date <= end_date]


def _phase1_snapshot_payload(
    *,
    symbol: str,
    close: float,
    swing_distance: float,
    breakout_distance: float,
) -> dict:
    return {
        "symbol": symbol,
        "data_date": "2026-06-05",
        "dataset": "TaiwanStockPrice",
        "adjustment_mode": "unadjusted",
        "ohlcv": {"close": close},
        "anchors": {
            "swing_low_60d": {
                "available": True,
                "anchor_date": "2026-06-01",
                "anchor_reason": "swing_low_60d",
                "avwap": 90,
                "distance_to_avwap_pct": swing_distance,
                "source_granularity": "daily",
                "estimated": False,
            },
            "breakout_20d": {
                "available": True,
                "anchor_date": "2026-06-05",
                "anchor_reason": "breakout_20d_high",
                "avwap": 98,
                "distance_to_avwap_pct": breakout_distance,
                "source_granularity": "daily",
                "estimated": False,
            },
        },
        "data_quality": {"estimated": False, "rows_used": 60},
    }


def _load_phase1_avwap_migration() -> ModuleType:
    migration_paths = sorted(
        Path(__file__).parents[1].joinpath("alembic", "versions").glob("*_add_phase1_avwap_snapshots.py")
    )
    assert len(migration_paths) == 1
    spec = importlib.util.spec_from_file_location("phase1_avwap_snapshot_migration", migration_paths[0])
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_migration_sql(direction: str) -> str:
    migration = _load_phase1_avwap_migration()
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    operations = Operations(context)
    original_op = migration.op
    migration.op = operations
    try:
        getattr(migration, direction)()
    finally:
        migration.op = original_op
    return buffer.getvalue()
