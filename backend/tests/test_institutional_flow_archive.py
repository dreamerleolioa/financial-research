from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_flow import (
    InstitutionalFlowRow,
    InstitutionalReport,
)
from ai_stock_sentinel.daily_radar.institutional_flow_provider import (
    OfficialInstitutionalReportError,
    OfficialTaiwanInstitutionalReportProvider,
    TWSE_T86_FALLBACK_URL,
    TWSE_T86_URL,
    normalize_tpex_institutional_report,
    normalize_twse_institutional_report,
)
from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
    _postgres_snapshot_upsert,
    archive_institutional_report,
    get_completed_institutional_snapshot,
    get_institutional_flows,
    get_market_institutional_flows,
)
from ai_stock_sentinel.daily_radar.institutional_flow_service import (
    refresh_taiwan_institutional_flows,
)
from ai_stock_sentinel.db.models import (
    TaiwanInstitutionalFlow,
    TaiwanInstitutionalReportSnapshot,
)
from ai_stock_sentinel.db.session import Base


def _db_session() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        engine,
        tables=[
            TaiwanInstitutionalReportSnapshot.__table__,
            TaiwanInstitutionalFlow.__table__,
        ],
    )
    return Session(engine), engine


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "6a7b8c9d0e1f_add_taiwan_institutional_flow_archive.py"
    )
    spec = importlib.util.spec_from_file_location(
        "taiwan_institutional_flow_archive_migration",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_sql(direction: str) -> str:
    migration = _load_migration()
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


def _twse_fields() -> list[str]:
    return [
        "證券代號",
        "證券名稱",
        "外陸資買進股數(不含外資自營商)",
        "外陸資賣出股數(不含外資自營商)",
        "外陸資買賣超股數(不含外資自營商)",
        "外資自營商買進股數",
        "外資自營商賣出股數",
        "外資自營商買賣超股數",
        "投信買進股數",
        "投信賣出股數",
        "投信買賣超股數",
        "自營商買賣超股數",
        "自營商買進股數(自行買賣)",
        "自營商賣出股數(自行買賣)",
        "自營商買賣超股數(自行買賣)",
        "自營商買進股數(避險)",
        "自營商賣出股數(避險)",
        "自營商買賣超股數(避險)",
        "三大法人買賣超股數",
    ]


def _twse_payload(*rows: list[Any], trade_date: date = date(2026, 8, 24)) -> dict[str, Any]:
    return {
        "stat": "OK",
        "date": trade_date.strftime("%Y%m%d"),
        "fields": _twse_fields(),
        "data": list(rows),
        "total": len(rows),
    }


def _twse_row(
    symbol: str = "2330",
    *,
    foreign: str = "1,000",
    trust: str = "200",
    dealer: str = "100",
    total: str = "1,300",
) -> list[Any]:
    row: list[Any] = ["0"] * 19
    row[0] = symbol
    row[1] = "台積電"
    row[4] = foreign
    row[10] = trust
    row[11] = dealer
    row[18] = total
    return row


def _tpex_payload(*rows: list[Any], trade_date: date = date(2026, 8, 24)) -> dict[str, Any]:
    fields = ["買賣超股數"] * 24
    fields[0] = "代號"
    fields[1] = "名稱"
    fields[23] = "三大法人買賣超股數合計"
    roc_date = f"{trade_date.year - 1911:03d}/{trade_date.month:02d}/{trade_date.day:02d}"
    return {
        "stat": "ok",
        "tables": [
            {
                "title": "三大法人買賣明細資訊",
                "date": roc_date,
                "fields": fields,
                "data": list(rows),
                "totalCount": len(rows),
            }
        ],
    }


def _tpex_row(symbol: str = "6488") -> list[Any]:
    row: list[Any] = ["0"] * 24
    row[0] = symbol
    row[1] = "環球晶"
    row[4] = "2,000"
    row[13] = "300"
    row[22] = "-100"
    row[23] = "2,200"
    return row


def _flow(
    symbol: str,
    *,
    market: str = "TW",
    trade_date: date = date(2026, 8, 24),
    foreign: int = 1000,
) -> InstitutionalFlowRow:
    return InstitutionalFlowRow(
        symbol=symbol,
        market=market,
        name="fixture",
        trade_date=trade_date,
        foreign_net_shares=foreign,
        investment_trust_net_shares=200,
        dealer_net_shares=100,
        total_net_shares=foreign + 300,
    )


def _report(*rows: InstitutionalFlowRow) -> InstitutionalReport:
    first = rows[0]
    return InstitutionalReport(
        market=first.market,
        trade_date=first.trade_date,
        source_provider="fixture",
        source_dataset="fixture_report",
        rows=tuple(rows),
    )


def test_institutional_flow_archive_migration_is_additive_and_reversible() -> None:
    upgrade_sql = _migration_sql("upgrade")
    downgrade_sql = _migration_sql("downgrade")

    assert "CREATE TABLE taiwan_institutional_report_snapshots" in upgrade_sql
    assert "CREATE TABLE taiwan_institutional_flows" in upgrade_sql
    assert "uq_taiwan_institutional_snapshot_market_date_dataset" in upgrade_sql
    assert "uq_taiwan_institutional_flow_symbol_date_dataset" in upgrade_sql
    assert "FOREIGN KEY(snapshot_id)" in upgrade_sql
    assert downgrade_sql.index("DROP TABLE taiwan_institutional_flows") < downgrade_sql.index(
        "DROP TABLE taiwan_institutional_report_snapshots"
    )


def test_official_institutional_report_parsers_keep_actor_flows_separate() -> None:
    twse = normalize_twse_institutional_report(
        _twse_payload(_twse_row(), _twse_row("0050")),
        expected_date=date(2026, 8, 24),
    )
    tpex = normalize_tpex_institutional_report(
        _tpex_payload(_tpex_row(), _tpex_row("00679B")),
        expected_date=date(2026, 8, 24),
    )

    assert [row.symbol for row in twse.rows] == ["2330.TW"]
    assert twse.rows[0].foreign_net_shares == 1000
    assert twse.rows[0].investment_trust_net_shares == 200
    assert twse.rows[0].dealer_net_shares == 100
    assert twse.rows[0].total_net_shares == 1300
    assert [row.symbol for row in tpex.rows] == ["6488.TWO"]
    assert tpex.rows[0].foreign_net_shares == 2000
    assert tpex.rows[0].investment_trust_net_shares == 300
    assert tpex.rows[0].dealer_net_shares == -100
    assert tpex.rows[0].total_net_shares == 2200


def test_official_institutional_report_rejects_date_or_schema_drift() -> None:
    with pytest.raises(OfficialInstitutionalReportError) as date_error:
        normalize_twse_institutional_report(
            _twse_payload(_twse_row(), trade_date=date(2026, 8, 21)),
            expected_date=date(2026, 8, 24),
        )
    assert date_error.value.code == "twse_institutional_date_mismatch"

    payload = _tpex_payload(_tpex_row())
    payload["tables"][0]["fields"][23] = "unexpected"
    with pytest.raises(OfficialInstitutionalReportError) as schema_error:
        normalize_tpex_institutional_report(
            payload,
            expected_date=date(2026, 8, 24),
        )
    assert schema_error.value.code == "tpex_institutional_schema_changed"


def test_official_institutional_report_rejects_declared_row_count_mismatch() -> None:
    twse_payload = _twse_payload(_twse_row())
    twse_payload["total"] = 2
    with pytest.raises(OfficialInstitutionalReportError) as twse_error:
        normalize_twse_institutional_report(
            twse_payload,
            expected_date=date(2026, 8, 24),
        )
    assert twse_error.value.code == "twse_institutional_row_count_mismatch"

    tpex_payload = _tpex_payload(_tpex_row())
    tpex_payload["tables"][0]["totalCount"] = 2
    with pytest.raises(OfficialInstitutionalReportError) as tpex_error:
        normalize_tpex_institutional_report(
            tpex_payload,
            expected_date=date(2026, 8, 24),
        )
    assert tpex_error.value.code == "tpex_institutional_row_count_mismatch"


def test_official_institutional_report_rejects_duplicate_or_invalid_rows() -> None:
    with pytest.raises(OfficialInstitutionalReportError) as duplicate_error:
        normalize_twse_institutional_report(
            _twse_payload(_twse_row(), _twse_row()),
            expected_date=date(2026, 8, 24),
        )
    assert duplicate_error.value.code == "institutional_report_duplicate_symbol"

    with pytest.raises(OfficialInstitutionalReportError) as row_error:
        normalize_twse_institutional_report(
            _twse_payload(_twse_row(foreign="--")),
            expected_date=date(2026, 8, 24),
        )
    assert row_error.value.code == "institutional_report_row_invalid"


def test_institutional_report_hash_is_stable_across_row_order() -> None:
    first = _flow("2330.TW")
    second = _flow("2317.TW", foreign=-500)

    assert _report(first, second).payload_hash == _report(second, first).payload_hash
    assert len(_report(first).payload_hash) == 64


class _Response:
    def __init__(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        status_code: int = 200,
        retry_after: str | None = None,
    ) -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = {"Retry-After": retry_after} if retry_after else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Mapping[str, Any]:
        return self._payload


def test_official_institutional_provider_retries_with_twse_fallback_route() -> None:
    calls: list[str] = []
    responses: list[Any] = [
        TimeoutError("temporary timeout"),
        _Response(_twse_payload(_twse_row())),
    ]
    sleeps: list[float] = []

    def request_get(url: str, **kwargs: Any) -> Any:
        calls.append(url)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    report = OfficialTaiwanInstitutionalReportProvider(
        request_get=request_get,
        max_attempts=2,
        retry_backoff_seconds=0.25,
        sleep=sleeps.append,
    ).fetch_market(market="TW", trade_date=date(2026, 8, 24))

    assert calls == [TWSE_T86_URL, TWSE_T86_FALLBACK_URL]
    assert sleeps == [0.25]
    assert [row.symbol for row in report.rows] == ["2330.TW"]


def test_official_institutional_provider_retries_semantically_incomplete_report() -> None:
    calls: list[str] = []
    responses = [
        _Response(_twse_payload()),
        _Response(_twse_payload(_twse_row())),
    ]

    def request_get(url: str, **kwargs: Any) -> _Response:
        calls.append(url)
        return responses.pop(0)

    report = OfficialTaiwanInstitutionalReportProvider(
        request_get=request_get,
        max_attempts=2,
        retry_backoff_seconds=0,
        sleep=lambda seconds: None,
    ).fetch_market(market="TW", trade_date=date(2026, 8, 24))

    assert calls == [TWSE_T86_URL, TWSE_T86_FALLBACK_URL]
    assert [row.symbol for row in report.rows] == ["2330.TW"]


def test_official_institutional_provider_preserves_final_parser_error() -> None:
    provider = OfficialTaiwanInstitutionalReportProvider(
        request_get=lambda url, **kwargs: _Response(_twse_payload()),
        max_attempts=1,
    )

    with pytest.raises(OfficialInstitutionalReportError) as exc_info:
        provider.fetch_market(market="TW", trade_date=date(2026, 8, 24))

    assert exc_info.value.code == "institutional_report_empty"


def test_official_institutional_provider_does_not_retry_permanent_http_error() -> None:
    calls = 0

    def request_get(url: str, **kwargs: Any) -> _Response:
        nonlocal calls
        calls += 1
        return _Response(status_code=400)

    provider = OfficialTaiwanInstitutionalReportProvider(
        request_get=request_get,
        max_attempts=3,
        sleep=lambda seconds: pytest.fail("permanent errors must not sleep"),
    )

    with pytest.raises(OfficialInstitutionalReportError) as exc_info:
        provider.fetch_market(market="TW", trade_date=date(2026, 8, 24))

    assert exc_info.value.code == "institutional_report_request_failed"
    assert calls == 1


def test_archive_replaces_corrected_report_without_stale_rows() -> None:
    session, engine = _db_session()
    try:
        first_snapshot = archive_institutional_report(
            session,
            _report(_flow("2330.TW"), _flow("2317.TW")),
        )
        first_snapshot_id = first_snapshot.id
        corrected_snapshot = archive_institutional_report(
            session,
            _report(_flow("2330.TW", foreign=1500)),
        )
        session.commit()

        snapshots = session.scalars(select(TaiwanInstitutionalReportSnapshot)).all()
        flows = session.scalars(select(TaiwanInstitutionalFlow)).all()
        assert len(snapshots) == 1
        assert corrected_snapshot.id == first_snapshot_id
        assert corrected_snapshot.status == "completed"
        assert corrected_snapshot.row_count == 1
        assert len(corrected_snapshot.payload_hash or "") == 64
        assert [row.symbol for row in flows] == ["2330.TW"]
        assert flows[0].foreign_net_shares == 1500
        assert flows[0].snapshot_id == corrected_snapshot.id
    finally:
        session.close()
        engine.dispose()


def test_completed_snapshot_and_flow_queries_require_completed_archive() -> None:
    session, engine = _db_session()
    try:
        archive_institutional_report(session, _report(_flow("2330.TW")))
        session.commit()

        snapshot = get_completed_institutional_snapshot(
            session,
            market="TW",
            trade_date=date(2026, 8, 24),
        )
        flows = get_institutional_flows(
            session,
            symbols=["2330.TW"],
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 24),
        )

        assert snapshot is not None
        assert snapshot.row_count == 1
        assert [row.symbol for row in flows] == ["2330.TW"]
        assert get_institutional_flows(
            session,
            symbols=[],
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 24),
        ) == []

        snapshot.status = "failed"
        snapshot.row_count = 0
        snapshot.payload_hash = None
        session.commit()
        assert get_completed_institutional_snapshot(
            session,
            market="TW",
            trade_date=date(2026, 8, 24),
        ) is None
        assert get_institutional_flows(
            session,
            symbols=["2330.TW"],
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 24),
        ) == []
    finally:
        session.close()
        engine.dispose()


def test_market_flow_query_returns_complete_report_rows_only() -> None:
    session, engine = _db_session()
    try:
        archive_institutional_report(
            session,
            _report(_flow("2330.TW"), _flow("2317.TW")),
        )
        session.commit()

        rows = get_market_institutional_flows(
            session,
            market="TW",
            trade_date=date(2026, 8, 24),
        )

        assert [row.symbol for row in rows] == ["2317.TW", "2330.TW"]
        assert get_market_institutional_flows(
            session,
            market="TWO",
            trade_date=date(2026, 8, 24),
        ) == []
    finally:
        session.close()
        engine.dispose()


def test_postgres_snapshot_write_uses_atomic_upsert() -> None:
    statement = _postgres_snapshot_upsert(
        _report(_flow("2330.TW")),
        dataset="taiwan_institutional_flow",
        fetched_at=MagicMock(),
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert (
        "ON CONFLICT ON CONSTRAINT "
        "uq_taiwan_institutional_snapshot_market_date_dataset DO UPDATE"
    ) in sql
    assert "RETURNING taiwan_institutional_report_snapshots.id" in sql


def test_archive_rejects_row_identity_mismatch_before_mutating_database() -> None:
    session, engine = _db_session()
    try:
        wrong_market_row = _flow("6488.TWO", market="TWO")
        report = InstitutionalReport(
            market="TW",
            trade_date=date(2026, 8, 24),
            source_provider="fixture",
            source_dataset="fixture_report",
            rows=(wrong_market_row,),
        )

        with pytest.raises(ValueError, match="identity mismatch"):
            archive_institutional_report(session, report)

        assert session.scalar(select(TaiwanInstitutionalReportSnapshot)) is None
    finally:
        session.close()
        engine.dispose()


def test_archive_rejects_symbol_suffix_that_does_not_match_market() -> None:
    session, engine = _db_session()
    try:
        mismatched_symbol = _flow("6488.TWO", market="TW")

        with pytest.raises(ValueError, match="symbol does not match market"):
            archive_institutional_report(session, _report(mismatched_symbol))

        assert session.scalar(select(TaiwanInstitutionalReportSnapshot)) is None
    finally:
        session.close()
        engine.dispose()


class _FixtureInstitutionalReportProvider:
    def __init__(
        self,
        *,
        fail_market: str | None = None,
        mismatched_market: str | None = None,
        missing_source_market: str | None = None,
    ) -> None:
        self.fail_market = fail_market
        self.mismatched_market = mismatched_market
        self.missing_source_market = missing_source_market
        self.calls: list[tuple[str, date]] = []

    def fetch_market(self, *, market: str, trade_date: date) -> InstitutionalReport:
        self.calls.append((market, trade_date))
        if market == self.fail_market:
            raise OfficialInstitutionalReportError("fixture_failed", market=market)
        report_market = market
        if market == self.mismatched_market:
            report_market = "TWO" if market == "TW" else "TW"
        symbol = "6488.TWO" if report_market == "TWO" else "2330.TW"
        return InstitutionalReport(
            market=report_market,
            trade_date=trade_date,
            source_provider="" if market == self.missing_source_market else "fixture",
            source_dataset=f"fixture_{market}",
            rows=(
                _flow(
                    symbol,
                    market=report_market,
                    trade_date=trade_date,
                ),
            ),
        )


def test_institutional_flow_refresh_archives_both_markets() -> None:
    session, engine = _db_session()
    provider = _FixtureInstitutionalReportProvider()
    try:
        result = refresh_taiwan_institutional_flows(
            session,
            trade_date=date(2026, 8, 24),
            provider=provider,
        )
        session.commit()

        assert result["status"] == "completed"
        assert result["markets_attempted"] == ["TW", "TWO"]
        assert result["markets_completed"] == ["TW", "TWO"]
        assert result["records_written"] == 2
        assert result["errors"] == []
        assert sorted(provider.calls) == [
            ("TW", date(2026, 8, 24)),
            ("TWO", date(2026, 8, 24)),
        ]
        assert session.query(TaiwanInstitutionalReportSnapshot).count() == 2
        assert session.query(TaiwanInstitutionalFlow).count() == 2
    finally:
        session.close()
        engine.dispose()


def test_institutional_flow_refresh_persists_partial_success_with_safe_error() -> None:
    session, engine = _db_session()
    try:
        result = refresh_taiwan_institutional_flows(
            session,
            trade_date=date(2026, 8, 24),
            provider=_FixtureInstitutionalReportProvider(fail_market="TWO"),
        )
        session.commit()

        assert result["status"] == "failed"
        assert result["markets_completed"] == ["TW"]
        assert result["records_written"] == 1
        assert result["errors"] == [
            {
                "code": "fixture_failed",
                "market": "TWO",
                "trade_date": "2026-08-24",
                "error_type": "OfficialInstitutionalReportError",
            }
        ]
        assert session.query(TaiwanInstitutionalReportSnapshot).one().market == "TW"
    finally:
        session.close()
        engine.dispose()


def test_institutional_flow_refresh_rejects_provider_identity_mismatch() -> None:
    session, engine = _db_session()
    try:
        result = refresh_taiwan_institutional_flows(
            session,
            trade_date=date(2026, 8, 24),
            provider=_FixtureInstitutionalReportProvider(mismatched_market="TW"),
        )

        assert result["status"] == "failed"
        assert result["markets_completed"] == ["TWO"]
        assert result["errors"][0]["code"] == "institutional_report_identity_mismatch"
        assert result["errors"][0]["market"] == "TW"
    finally:
        session.close()
        engine.dispose()


def test_institutional_flow_refresh_rejects_missing_provider_source() -> None:
    session, engine = _db_session()
    try:
        result = refresh_taiwan_institutional_flows(
            session,
            trade_date=date(2026, 8, 24),
            provider=_FixtureInstitutionalReportProvider(missing_source_market="TWO"),
        )

        assert result["status"] == "failed"
        assert result["markets_completed"] == ["TW"]
        assert result["errors"][0]["code"] == "institutional_report_metadata_invalid"
        assert result["errors"][0]["market"] == "TWO"
    finally:
        session.close()
        engine.dispose()
