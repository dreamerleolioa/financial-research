from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
import re

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_flow import (
    InstitutionalFlowRow,
    InstitutionalReport,
)
from ai_stock_sentinel.db.models import (
    TaiwanInstitutionalFlow,
    TaiwanInstitutionalReportSnapshot,
)


DEFAULT_INSTITUTIONAL_FLOW_DATASET = "taiwan_institutional_flow"


def archive_institutional_report(
    session: Session,
    report: InstitutionalReport,
    *,
    dataset: str = DEFAULT_INSTITUTIONAL_FLOW_DATASET,
) -> TaiwanInstitutionalReportSnapshot:
    rows = _validated_report_rows(report)
    fetched_at = datetime.now(timezone.utc)
    if _is_postgresql(session):
        snapshot_id = session.scalar(
            _postgres_snapshot_upsert(
                report,
                dataset=dataset,
                fetched_at=fetched_at,
            )
        )
        if snapshot_id is None:
            raise RuntimeError("institutional snapshot upsert returned no id")
        snapshot = session.get(TaiwanInstitutionalReportSnapshot, snapshot_id)
        if snapshot is None:
            raise RuntimeError("institutional snapshot could not be reloaded")
    else:
        snapshot = session.scalar(
            select(TaiwanInstitutionalReportSnapshot).where(
                TaiwanInstitutionalReportSnapshot.market == report.market,
                TaiwanInstitutionalReportSnapshot.trade_date == report.trade_date,
                TaiwanInstitutionalReportSnapshot.dataset == dataset,
            )
        )
        if snapshot is None:
            snapshot = TaiwanInstitutionalReportSnapshot(
                market=report.market,
                trade_date=report.trade_date,
                dataset=dataset,
            )
            session.add(snapshot)
        snapshot.source_provider = report.source_provider
        snapshot.source_dataset = report.source_dataset
        snapshot.status = "completed"
        snapshot.row_count = len(rows)
        snapshot.payload_hash = report.payload_hash
        snapshot.fetched_at = fetched_at
        session.flush()

    session.execute(
        delete(TaiwanInstitutionalFlow).where(
            TaiwanInstitutionalFlow.snapshot_id == snapshot.id
        )
    )
    session.add_all(
        [
            _flow_model(
                row,
                snapshot_id=snapshot.id,
                dataset=dataset,
            )
            for row in rows
        ]
    )
    session.flush()
    return snapshot


def get_completed_institutional_snapshot(
    session: Session,
    *,
    market: str,
    trade_date: date,
    dataset: str = DEFAULT_INSTITUTIONAL_FLOW_DATASET,
) -> TaiwanInstitutionalReportSnapshot | None:
    return session.scalar(
        select(TaiwanInstitutionalReportSnapshot).where(
            TaiwanInstitutionalReportSnapshot.market == market,
            TaiwanInstitutionalReportSnapshot.trade_date == trade_date,
            TaiwanInstitutionalReportSnapshot.dataset == dataset,
            TaiwanInstitutionalReportSnapshot.status == "completed",
        )
    )


def get_institutional_flows(
    session: Session,
    *,
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
    dataset: str = DEFAULT_INSTITUTIONAL_FLOW_DATASET,
) -> list[TaiwanInstitutionalFlow]:
    if not symbols:
        return []
    statement = (
        select(TaiwanInstitutionalFlow)
        .join(
            TaiwanInstitutionalReportSnapshot,
            TaiwanInstitutionalReportSnapshot.id
            == TaiwanInstitutionalFlow.snapshot_id,
        )
        .where(
            TaiwanInstitutionalFlow.symbol.in_(list(symbols)),
            TaiwanInstitutionalFlow.trade_date >= start_date,
            TaiwanInstitutionalFlow.trade_date <= end_date,
            TaiwanInstitutionalFlow.dataset == dataset,
            TaiwanInstitutionalReportSnapshot.status == "completed",
        )
        .order_by(
            TaiwanInstitutionalFlow.symbol.asc(),
            TaiwanInstitutionalFlow.trade_date.asc(),
        )
    )
    return list(session.scalars(statement).all())


def get_market_institutional_flows(
    session: Session,
    *,
    market: str,
    trade_date: date,
    dataset: str = DEFAULT_INSTITUTIONAL_FLOW_DATASET,
) -> list[TaiwanInstitutionalFlow]:
    statement = (
        select(TaiwanInstitutionalFlow)
        .join(
            TaiwanInstitutionalReportSnapshot,
            TaiwanInstitutionalReportSnapshot.id
            == TaiwanInstitutionalFlow.snapshot_id,
        )
        .where(
            TaiwanInstitutionalFlow.market == market,
            TaiwanInstitutionalFlow.trade_date == trade_date,
            TaiwanInstitutionalFlow.dataset == dataset,
            TaiwanInstitutionalReportSnapshot.status == "completed",
        )
        .order_by(TaiwanInstitutionalFlow.symbol.asc())
    )
    return list(session.scalars(statement).all())


def _validated_report_rows(
    report: InstitutionalReport,
) -> tuple[InstitutionalFlowRow, ...]:
    if report.market not in {"TW", "TWO"}:
        raise ValueError("unsupported institutional report market")
    if not report.rows:
        raise ValueError("completed institutional report must contain rows")
    symbols: set[str] = set()
    expected_suffix = ".TW" if report.market == "TW" else ".TWO"
    for row in report.rows:
        if row.market != report.market or row.trade_date != report.trade_date:
            raise ValueError("institutional report row identity mismatch")
        if re.fullmatch(rf"[1-9]\d{{3}}{re.escape(expected_suffix)}", row.symbol) is None:
            raise ValueError("institutional report symbol does not match market")
        if row.row_origin not in {"reported", "complete_report_zero_fill"}:
            raise ValueError("unsupported institutional flow row origin")
        if row.symbol in symbols:
            raise ValueError("duplicate institutional flow symbol")
        symbols.add(row.symbol)
    return report.rows


def _flow_model(
    row: InstitutionalFlowRow,
    *,
    snapshot_id: int,
    dataset: str,
) -> TaiwanInstitutionalFlow:
    return TaiwanInstitutionalFlow(
        snapshot_id=snapshot_id,
        symbol=row.symbol,
        market=row.market,
        name=row.name,
        trade_date=row.trade_date,
        dataset=dataset,
        foreign_net_shares=row.foreign_net_shares,
        investment_trust_net_shares=row.investment_trust_net_shares,
        dealer_net_shares=row.dealer_net_shares,
        total_net_shares=row.total_net_shares,
        row_origin=row.row_origin,
    )


def _postgres_snapshot_upsert(
    report: InstitutionalReport,
    *,
    dataset: str,
    fetched_at: datetime,
):
    statement = postgresql_insert(TaiwanInstitutionalReportSnapshot).values(
        market=report.market,
        trade_date=report.trade_date,
        dataset=dataset,
        source_provider=report.source_provider,
        source_dataset=report.source_dataset,
        status="completed",
        row_count=len(report.rows),
        payload_hash=report.payload_hash,
        fetched_at=fetched_at,
    )
    return statement.on_conflict_do_update(
        constraint="uq_taiwan_institutional_snapshot_market_date_dataset",
        set_={
            "source_provider": statement.excluded.source_provider,
            "source_dataset": statement.excluded.source_dataset,
            "status": statement.excluded.status,
            "row_count": statement.excluded.row_count,
            "payload_hash": statement.excluded.payload_hash,
            "fetched_at": statement.excluded.fetched_at,
        },
    ).returning(TaiwanInstitutionalReportSnapshot.id)


def _is_postgresql(session: Session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


__all__ = [
    "DEFAULT_INSTITUTIONAL_FLOW_DATASET",
    "archive_institutional_report",
    "get_completed_institutional_snapshot",
    "get_institutional_flows",
    "get_market_institutional_flows",
]
