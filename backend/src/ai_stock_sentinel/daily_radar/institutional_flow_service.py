from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_flow import InstitutionalReport
from ai_stock_sentinel.daily_radar.institutional_flow_provider import (
    OfficialInstitutionalReportError,
    OfficialTaiwanInstitutionalReportProvider,
)
from ai_stock_sentinel.daily_radar.institutional_flow_repository import (
    archive_institutional_report,
)


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
            except OfficialInstitutionalReportError as exc:
                errors.append(
                    {
                        "code": exc.code,
                        "market": market,
                        "trade_date": trade_date.isoformat(),
                        "error_type": exc.__class__.__name__,
                    }
                )

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


__all__ = [
    "InstitutionalReportProvider",
    "refresh_taiwan_institutional_flows",
]
