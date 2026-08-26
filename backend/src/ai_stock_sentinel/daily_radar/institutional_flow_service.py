from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_flow import InstitutionalReport
from ai_stock_sentinel.daily_radar.institutional_flow_provider import (
    OfficialInstitutionalReportError,
    OfficialTaiwanInstitutionalReportProvider,
)
from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
    InstitutionalArchiveIntegrityError,
    archive_institutional_report,
    get_complete_institutional_archive_window,
    get_completed_institutional_snapshot,
)
from ai_stock_sentinel.daily_radar.market_session import (
    MarketSessionProvider,
    MarketSessionProviderError,
    TwseMarketSessionProvider,
)


INSTITUTIONAL_BACKFILL_MAX_CALENDAR_DAYS = 11


class InstitutionalReportProvider(Protocol):
    def fetch_market(self, *, market: str, trade_date: date) -> InstitutionalReport:
        ...


def refresh_taiwan_institutional_flows(
    session: Session,
    *,
    trade_date: date,
    provider: InstitutionalReportProvider | None = None,
    max_workers: int = 2,
) -> dict[str, Any]:
    active_provider = provider or OfficialTaiwanInstitutionalReportProvider()
    markets = ("TW", "TWO")
    reports_by_market: dict[str, InstitutionalReport] = {}
    completed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(markets)))) as executor:
        futures = {
            executor.submit(
                active_provider.fetch_market,
                market=market,
                trade_date=trade_date,
            ): market
            for market in markets
        }
        for future in as_completed(futures):
            market = futures[future]
            try:
                report = future.result()
                _validate_report_identity(
                    report,
                    market=market,
                    trade_date=trade_date,
                )
                reports_by_market[market] = report
            except OfficialInstitutionalReportError as exc:
                errors.append(
                    {
                        "code": exc.code,
                        "market": market,
                        "trade_date": trade_date.isoformat(),
                        "error_type": exc.__class__.__name__,
                    }
                )

    for market in markets:
        report = reports_by_market.get(market)
        if report is None:
            continue
        snapshot = archive_institutional_report(session, report)
        completed.append(
            {
                "market": market,
                "row_count": snapshot.row_count,
                "source_provider": snapshot.source_provider,
                "source_dataset": snapshot.source_dataset,
                "payload_hash": snapshot.payload_hash,
            }
        )
        session.flush()

    completed.sort(key=lambda item: item["market"])
    errors.sort(key=lambda item: (item["market"], item["code"]))
    return {
        "status": "completed" if len(completed) == len(markets) and not errors else "failed",
        "trade_date": trade_date.isoformat(),
        "markets_attempted": list(markets),
        "markets_completed": [item["market"] for item in completed],
        "records_written": sum(int(item["row_count"]) for item in completed),
        "snapshots": completed,
        "errors": errors,
    }


def backfill_taiwan_institutional_flows(
    session: Session,
    *,
    start_date: date,
    end_date: date,
    as_of_date: date,
    provider: InstitutionalReportProvider | None = None,
    market_session_provider: MarketSessionProvider | None = None,
    max_workers: int = 2,
) -> dict[str, Any]:
    _validate_backfill_range(
        start_date=start_date,
        end_date=end_date,
        as_of_date=as_of_date,
    )
    active_market_session_provider = (
        market_session_provider or TwseMarketSessionProvider()
    )
    dates_requested = _inclusive_dates(start_date, end_date)
    dates_attempted: list[str] = []
    dates_completed: list[str] = []
    dates_reused: list[str] = []
    dates_repaired: list[str] = []
    skipped_dates: list[str] = []
    snapshots: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    records_written = 0

    for trade_date in dates_requested:
        date_value = trade_date.isoformat()
        if trade_date.weekday() >= 5:
            skipped_dates.append(date_value)
            continue

        existing_snapshot_count = 0
        for market in ("TW", "TWO"):
            if get_completed_institutional_snapshot(
                session,
                market=market,
                trade_date=trade_date,
            ) is not None:
                existing_snapshot_count += 1
        archive_needs_repair = 0 < existing_snapshot_count < 2
        try:
            existing = get_complete_institutional_archive_window(
                session,
                start_date=trade_date,
                end_date=trade_date,
            )
        except InstitutionalArchiveIntegrityError:
            archive_needs_repair = True
            existing = {}
        session.commit()
        if trade_date in existing:
            dates_reused.append(date_value)
            continue

        try:
            market_session = active_market_session_provider.resolve(
                run_date=trade_date,
                market="TW",
            )
        except MarketSessionProviderError as exc:
            errors.append(
                {
                    "code": exc.code,
                    "trade_date": date_value,
                    "error_type": exc.__class__.__name__,
                }
            )
            continue
        except Exception as exc:
            errors.append(
                {
                    "code": "institutional_backfill_market_session_failed",
                    "trade_date": date_value,
                    "error_type": exc.__class__.__name__,
                }
            )
            continue
        if market_session.status == "closed":
            if archive_needs_repair:
                errors.append(
                    {
                        "code": "institutional_backfill_archive_session_conflict",
                        "trade_date": date_value,
                    }
                )
                continue
            skipped_dates.append(date_value)
            continue

        dates_attempted.append(date_value)
        try:
            result = refresh_taiwan_institutional_flows(
                session,
                trade_date=trade_date,
                provider=provider,
                max_workers=max_workers,
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            errors.append(
                {
                    "code": "institutional_backfill_date_failed",
                    "trade_date": date_value,
                    "error_type": exc.__class__.__name__,
                }
            )
            continue

        records_written += int(result["records_written"])
        snapshots.extend(
            dict(snapshot) | {"trade_date": date_value}
            for snapshot in result["snapshots"]
        )
        errors.extend(result["errors"])
        if result["status"] == "completed":
            dates_completed.append(date_value)
            if archive_needs_repair:
                dates_repaired.append(date_value)

    errors.sort(
        key=lambda item: (
            str(item.get("trade_date") or ""),
            str(item.get("market") or ""),
            str(item.get("code") or ""),
        )
    )
    snapshots.sort(key=lambda item: (item["trade_date"], item["market"]))
    return {
        "status": "completed" if not errors else "failed",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "records_written": records_written,
        "dates_requested": [value.isoformat() for value in dates_requested],
        "dates_attempted": dates_attempted,
        "dates_completed": dates_completed,
        "dates_reused": dates_reused,
        "dates_repaired": dates_repaired,
        "skipped_dates": sorted(skipped_dates),
        "snapshots": snapshots,
        "errors": errors,
    }


def _validate_report_identity(
    report: InstitutionalReport,
    *,
    market: str,
    trade_date: date,
) -> None:
    if report.market != market or report.trade_date != trade_date:
        raise OfficialInstitutionalReportError(
            "institutional_report_identity_mismatch",
            market=market,
        )
    if not report.source_provider.strip() or not report.source_dataset.strip():
        raise OfficialInstitutionalReportError(
            "institutional_report_metadata_invalid",
            market=market,
        )


def _validate_backfill_range(
    *,
    start_date: date,
    end_date: date,
    as_of_date: date,
) -> None:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if end_date > as_of_date:
        raise ValueError("institutional backfill cannot include future dates")
    if (end_date - start_date).days >= INSTITUTIONAL_BACKFILL_MAX_CALENDAR_DAYS:
        raise ValueError(
            "institutional backfill range cannot exceed "
            f"{INSTITUTIONAL_BACKFILL_MAX_CALENDAR_DAYS} calendar days"
        )


def _inclusive_dates(start_date: date, end_date: date) -> list[date]:
    return [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]


__all__ = [
    "INSTITUTIONAL_BACKFILL_MAX_CALENDAR_DAYS",
    "InstitutionalReportProvider",
    "backfill_taiwan_institutional_flows",
    "refresh_taiwan_institutional_flows",
]
