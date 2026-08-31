from __future__ import annotations

import gzip
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from ai_stock_sentinel.active_etf_holdings.provider import (
    ActiveEtfFundDescriptor,
    ActiveEtfFundSnapshot,
    ActiveEtfHoldingRow,
    ActiveEtfProviderError,
)
from ai_stock_sentinel.active_etf_holdings.schemas import (
    ActiveEtfChange,
    ActiveEtfConsensus,
    ActiveEtfCoverageFund,
    ActiveEtfDailyResponse,
    ActiveEtfDailySummary,
    ActiveEtfRefreshError,
    ActiveEtfRefreshResponse,
    ActiveEtfSourceEvidence,
)
from ai_stock_sentinel.db.models import (
    ActiveEtfFund,
    ActiveEtfHolding,
    ActiveEtfHoldingSnapshot,
    ActiveEtfSourceHolding,
    ActiveEtfSourceObservation,
)

logger = logging.getLogger(__name__)
_PERCENT_QUANTUM = Decimal("0.0001")
_MIN_SCALE_SAMPLE = 5
_LIKELY_SCALE_RESIDUAL_PCT = Decimal("0.5000")
_LIKELY_SCALE_RAW_CHANGE_PCT = Decimal("0.5000")


class ActiveEtfHoldingsProvider(Protocol):
    source_provider: str

    def fetch_registry(self) -> list[ActiveEtfFundDescriptor]: ...

    def fetch_snapshot(self, fund: ActiveEtfFundDescriptor) -> ActiveEtfFundSnapshot: ...


class ActiveEtfVerificationProvider(Protocol):
    source_provider: str

    def supports(self, fund_code: str) -> bool: ...

    def fetch_snapshot(
        self,
        fund: ActiveEtfFundDescriptor,
        *,
        expected_data_date: date | None = None,
    ) -> ActiveEtfFundSnapshot: ...


def refresh_active_etf_holdings(
    db: Session,
    *,
    provider: ActiveEtfHoldingsProvider,
    verification_provider: ActiveEtfVerificationProvider | None = None,
    fund_codes: list[str] | None = None,
    max_workers: int = 4,
) -> ActiveEtfRefreshResponse:
    registry = provider.fetch_registry()
    registry_by_code = {fund.fund_code: fund for fund in registry}
    selected_codes = sorted(registry_by_code) if fund_codes is None else fund_codes
    if not selected_codes:
        raise ActiveEtfProviderError("active_etf_requested_funds_empty")
    unknown_codes = sorted(set(selected_codes) - set(registry_by_code))
    if unknown_codes:
        raise ActiveEtfProviderError("active_etf_requested_fund_not_found")

    _sync_fund_registry(db, registry, source_provider=provider.source_provider)
    db.commit()

    created = 0
    updated = 0
    reused = 0
    errors: list[ActiveEtfRefreshError] = []
    snapshot_pairs: list[
        tuple[ActiveEtfFundSnapshot, ActiveEtfFundSnapshot | None, bool, bool]
    ] = []
    worker_count = min(max(1, max_workers), 4, len(selected_codes))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(provider.fetch_snapshot, registry_by_code[fund_code]): fund_code
            for fund_code in selected_codes
        }
        for future in as_completed(futures):
            fund_code = futures[future]
            try:
                primary_snapshot = future.result()
                verification_snapshot = None
                verification_supported = (
                    verification_provider is not None
                    and verification_provider.supports(fund_code)
                )
                verification_fetch_failed = False
                if verification_supported:
                    try:
                        assert verification_provider is not None
                        verification_snapshot = verification_provider.fetch_snapshot(
                            registry_by_code[fund_code],
                            expected_data_date=primary_snapshot.data_date,
                        )
                    except Exception:
                        verification_fetch_failed = True
                        logger.exception(
                            "Active ETF verification fetch failed fund_code=%s",
                            fund_code,
                        )
                        errors.append(
                            ActiveEtfRefreshError(
                                fund_code=fund_code,
                                code="active_etf_verification_fetch_failed",
                            )
                        )
                snapshot_pairs.append(
                    (
                        primary_snapshot,
                        verification_snapshot,
                        verification_supported,
                        verification_fetch_failed,
                    )
                )
            except ActiveEtfProviderError as exc:
                error_code = str(exc)
                if error_code == "active_etf_holdings_not_published":
                    logger.info(
                        "Active ETF holdings not published fund_code=%s",
                        fund_code,
                    )
                else:
                    logger.exception(
                        "Active ETF holding fetch failed fund_code=%s",
                        fund_code,
                    )
                    error_code = "active_etf_snapshot_fetch_failed"
                errors.append(
                    ActiveEtfRefreshError(
                        fund_code=fund_code,
                        code=error_code,
                    )
                )
            except Exception:
                logger.exception("Active ETF holding fetch failed fund_code=%s", fund_code)
                errors.append(
                    ActiveEtfRefreshError(
                        fund_code=fund_code,
                        code="active_etf_snapshot_fetch_failed",
                    )
                )

    verified = 0
    single_source = 0
    conflicted = 0
    for (
        snapshot,
        verification_snapshot,
        verification_supported,
        verification_fetch_failed,
    ) in sorted(
        snapshot_pairs,
        key=lambda item: item[0].fund.fund_code,
    ):
        try:
            verification_status, verification_details = _reconcile_snapshots(
                snapshot,
                verification_snapshot,
                verification_supported=verification_supported,
            )
            _upsert_observation(db, snapshot, source_provider=provider.source_provider)
            if verification_snapshot is not None:
                assert verification_provider is not None
                _upsert_observation(
                    db,
                    verification_snapshot,
                    source_provider=verification_provider.source_provider,
                )
            if verification_fetch_failed and _has_matching_verified_snapshot(
                db,
                snapshot,
                source_provider=provider.source_provider,
            ):
                verification_status = "verified"
                outcome = "reused"
            else:
                outcome = _upsert_snapshot(
                    db,
                    snapshot,
                    source_provider=provider.source_provider,
                    verification_status=verification_status,
                    verification_details=verification_details,
                )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Active ETF holding persistence failed fund_code=%s data_date=%s",
                snapshot.fund.fund_code,
                snapshot.data_date,
            )
            errors.append(
                ActiveEtfRefreshError(
                    fund_code=snapshot.fund.fund_code,
                    code="active_etf_snapshot_persistence_failed",
                )
            )
            continue
        if outcome == "created":
            created += 1
        elif outcome == "updated":
            updated += 1
        else:
            reused += 1
        if verification_status == "verified":
            verified += 1
        elif verification_status == "conflict":
            conflicted += 1
        else:
            single_source += 1

    return ActiveEtfRefreshResponse(
        status="partial" if errors else "completed",
        expected_funds=len(registry),
        selected_funds=len(selected_codes),
        snapshots_created=created,
        snapshots_updated=updated,
        snapshots_reused=reused,
        verified_snapshots=verified,
        single_source_snapshots=single_source,
        conflicted_snapshots=conflicted,
        errors=sorted(errors, key=lambda error: error.fund_code),
    )


def get_active_etf_daily_response(
    db: Session,
    *,
    data_date: date | None = None,
    available_date_limit: int = 60,
) -> ActiveEtfDailyResponse | None:
    available_dates = list(
        db.scalars(
            select(ActiveEtfHoldingSnapshot.data_date)
            .distinct()
            .order_by(ActiveEtfHoldingSnapshot.data_date.desc())
            .limit(available_date_limit)
        )
    )
    if not available_dates:
        return None
    selected_date = data_date or available_dates[0]
    if selected_date not in available_dates:
        return None

    funds = list(
        db.scalars(
            select(ActiveEtfFund)
            .where(ActiveEtfFund.enabled.is_(True))
            .order_by(ActiveEtfFund.fund_code)
        )
    )
    current_snapshots = list(
        db.scalars(
            select(ActiveEtfHoldingSnapshot)
            .options(
                selectinload(ActiveEtfHoldingSnapshot.fund),
                selectinload(ActiveEtfHoldingSnapshot.holdings),
            )
            .where(
                ActiveEtfHoldingSnapshot.data_date == selected_date,
                ActiveEtfHoldingSnapshot.fund_code.in_([fund.fund_code for fund in funds]),
            )
        ).unique()
    )
    current_by_fund = {snapshot.fund_code: snapshot for snapshot in current_snapshots}
    latest_dates = dict(
        db.execute(
            select(
                ActiveEtfHoldingSnapshot.fund_code,
                func.max(ActiveEtfHoldingSnapshot.data_date),
            ).group_by(ActiveEtfHoldingSnapshot.fund_code)
        ).all()
    )

    changes: list[ActiveEtfChange] = []
    coverage: list[ActiveEtfCoverageFund] = []
    for fund in funds:
        current = current_by_fund.get(fund.fund_code)
        if current is None:
            coverage.append(
                ActiveEtfCoverageFund(
                    fund_code=fund.fund_code,
                    name=fund.name,
                    source_provider=fund.source_provider,
                    source_url=fund.source_url,
                    status="missing",
                    latest_data_date=latest_dates.get(fund.fund_code),
                )
            )
            continue
        sources = _source_evidence_for_snapshot(db, current)
        verification_reason = _verification_reason(current)
        if current.verification_status == "conflict":
            coverage.append(
                ActiveEtfCoverageFund(
                    fund_code=fund.fund_code,
                    name=fund.name,
                    category=_snapshot_category(current),
                    source_provider=current.source_provider,
                    source_url=current.source_url,
                    status="source_conflict",
                    verification_status=current.verification_status,
                    source_count=current.source_count,
                    verification_reason=verification_reason,
                    sources=sources,
                    data_date=current.data_date,
                    latest_data_date=latest_dates.get(fund.fund_code),
                    fetched_at=current.fetched_at,
                )
            )
            continue
        previous = db.scalar(
            select(ActiveEtfHoldingSnapshot)
            .options(selectinload(ActiveEtfHoldingSnapshot.holdings))
            .where(
                ActiveEtfHoldingSnapshot.fund_code == fund.fund_code,
                ActiveEtfHoldingSnapshot.data_date < selected_date,
                ActiveEtfHoldingSnapshot.verification_status.in_(
                    ("verified", "single_source")
                ),
            )
            .order_by(ActiveEtfHoldingSnapshot.data_date.desc())
            .limit(1)
        )
        if previous is None:
            coverage.append(
                ActiveEtfCoverageFund(
                    fund_code=fund.fund_code,
                    name=fund.name,
                    category=_snapshot_category(current),
                    source_provider=current.source_provider,
                    source_url=current.source_url,
                    status="no_baseline",
                    verification_status=current.verification_status,
                    source_count=current.source_count,
                    verification_reason=verification_reason,
                    sources=sources,
                    data_date=current.data_date,
                    latest_data_date=latest_dates.get(fund.fund_code),
                    fetched_at=current.fetched_at,
                )
            )
            continue
        fund_changes, common_scale_ratio = _changes_for_snapshots(current, previous)
        changes.extend(fund_changes)
        coverage.append(
            ActiveEtfCoverageFund(
                fund_code=fund.fund_code,
                name=fund.name,
                category=_snapshot_category(current),
                source_provider=current.source_provider,
                source_url=current.source_url,
                status="ready",
                verification_status=current.verification_status,
                source_count=current.source_count,
                verification_reason=verification_reason,
                sources=sources,
                data_date=current.data_date,
                previous_date=previous.data_date,
                latest_data_date=latest_dates.get(fund.fund_code),
                fetched_at=current.fetched_at,
                change_count=len(fund_changes),
                common_scale_ratio=common_scale_ratio,
            )
        )

    changes.sort(key=_change_sort_key)
    action_counts = defaultdict(int)
    for change in changes:
        action_counts[change.action] += 1
    summary = ActiveEtfDailySummary(
        changed_funds=len({change.fund_code for change in changes}),
        changed_stocks=len({change.symbol for change in changes}),
        changed_rows=len(changes),
        additions=action_counts["added"],
        increases=action_counts["increased"],
        decreases=action_counts["decreased"],
        removals=action_counts["removed"],
    )
    generated_at = max(
        (snapshot.fetched_at for snapshot in current_snapshots),
        default=datetime.now(timezone.utc),
    )
    return ActiveEtfDailyResponse(
        data_date=selected_date,
        available_dates=available_dates,
        generated_at=generated_at,
        expected_funds=len(funds),
        covered_funds=sum(
            snapshot.verification_status in {"verified", "single_source"}
            for snapshot in current_snapshots
        ),
        summary=summary,
        funds=coverage,
        changes=changes,
        consensus=_build_consensus(changes),
    )


def _sync_fund_registry(
    db: Session,
    registry: list[ActiveEtfFundDescriptor],
    *,
    source_provider: str,
) -> None:
    active_codes = {fund.fund_code for fund in registry}
    existing = {
        fund.fund_code: fund for fund in db.scalars(select(ActiveEtfFund)).all()
    }
    enabled_count = sum(1 for fund in existing.values() if fund.enabled)
    if enabled_count and len(active_codes) * 5 < enabled_count * 4:
        raise ActiveEtfProviderError("active_etf_registry_coverage_dropped")
    for descriptor in registry:
        fund = existing.get(descriptor.fund_code)
        if fund is None:
            db.add(
                ActiveEtfFund(
                    fund_code=descriptor.fund_code,
                    name=descriptor.name,
                    market="TW",
                    source_provider=source_provider,
                    source_url=descriptor.source_url,
                    enabled=True,
                )
            )
            continue
        fund.name = descriptor.name
        fund.source_provider = source_provider
        fund.source_url = descriptor.source_url
        fund.enabled = True
    for fund_code, fund in existing.items():
        if fund_code not in active_codes:
            fund.enabled = False


def _upsert_snapshot(
    db: Session,
    snapshot: ActiveEtfFundSnapshot,
    *,
    source_provider: str,
    verification_status: str,
    verification_details: dict,
) -> str:
    existing = db.scalar(
        select(ActiveEtfHoldingSnapshot).where(
            ActiveEtfHoldingSnapshot.fund_code == snapshot.fund.fund_code,
            ActiveEtfHoldingSnapshot.data_date == snapshot.data_date,
        )
    )
    normalized_hash = snapshot.normalized_hash
    metadata = {
        "category": snapshot.fund.category,
        "normalized_hash": normalized_hash,
    }
    source_count = len(verification_details.get("sources", [])) or 1
    if existing is not None:
        existing_normalized_hash = (existing.source_metadata or {}).get("normalized_hash")
        if (
            existing_normalized_hash == normalized_hash
            and existing.parser_version == snapshot.parser_version
            and existing.verification_status == verification_status
            and existing.verification_details == verification_details
        ):
            existing.fetched_at = snapshot.fetched_at
            existing.source_provider = source_provider
            existing.source_url = snapshot.fund.source_url
            existing.payload_hash = snapshot.payload_hash
            existing.holding_count = len(snapshot.holdings)
            existing.skipped_instrument_count = snapshot.skipped_instrument_count
            existing.source_metadata = metadata
            existing.source_count = source_count
            return "reused"
        db.execute(
            delete(ActiveEtfHolding).where(ActiveEtfHolding.snapshot_id == existing.id)
        )
        db.flush()
        row = existing
        outcome = "updated"
    else:
        row = ActiveEtfHoldingSnapshot(
            fund_code=snapshot.fund.fund_code,
            data_date=snapshot.data_date,
            fetched_at=snapshot.fetched_at,
            source_provider=source_provider,
            source_url=snapshot.fund.source_url,
            payload_hash=snapshot.payload_hash,
            parser_version=snapshot.parser_version,
            holding_count=len(snapshot.holdings),
            skipped_instrument_count=snapshot.skipped_instrument_count,
            source_metadata=metadata,
            verification_status=verification_status,
            source_count=source_count,
            verification_details=verification_details,
        )
        db.add(row)
        db.flush()
        outcome = "created"

    row.fetched_at = snapshot.fetched_at
    row.source_provider = source_provider
    row.source_url = snapshot.fund.source_url
    row.payload_hash = snapshot.payload_hash
    row.parser_version = snapshot.parser_version
    row.holding_count = len(snapshot.holdings)
    row.skipped_instrument_count = snapshot.skipped_instrument_count
    row.source_metadata = metadata
    row.verification_status = verification_status
    row.source_count = source_count
    row.verification_details = verification_details
    db.add_all(
        [
            ActiveEtfHolding(
                snapshot_id=row.id,
                symbol=holding.symbol,
                name=holding.name,
                shares=holding.shares,
                weight_pct=holding.weight_pct,
                position_order=holding.position_order,
            )
            for holding in snapshot.holdings
        ]
    )
    return outcome


def _has_matching_verified_snapshot(
    db: Session,
    snapshot: ActiveEtfFundSnapshot,
    *,
    source_provider: str,
) -> bool:
    existing = db.scalar(
        select(ActiveEtfHoldingSnapshot).where(
            ActiveEtfHoldingSnapshot.fund_code == snapshot.fund.fund_code,
            ActiveEtfHoldingSnapshot.data_date == snapshot.data_date,
            ActiveEtfHoldingSnapshot.source_provider == source_provider,
            ActiveEtfHoldingSnapshot.verification_status == "verified",
        )
    )
    if existing is None:
        return False
    return (existing.source_metadata or {}).get("normalized_hash") == snapshot.normalized_hash


def _upsert_observation(
    db: Session,
    snapshot: ActiveEtfFundSnapshot,
    *,
    source_provider: str,
) -> None:
    raw_payload = snapshot.raw_payload
    if not raw_payload:
        raise ActiveEtfProviderError("active_etf_observation_raw_payload_missing")
    if len(raw_payload) > 5_000_000:
        raise ActiveEtfProviderError("active_etf_holdings_response_too_large")
    existing = db.scalar(
        select(ActiveEtfSourceObservation).where(
            ActiveEtfSourceObservation.fund_code == snapshot.fund.fund_code,
            ActiveEtfSourceObservation.data_date == snapshot.data_date,
            ActiveEtfSourceObservation.source_provider == source_provider,
        )
    )
    if existing is not None:
        db.execute(
            delete(ActiveEtfSourceHolding).where(
                ActiveEtfSourceHolding.observation_id == existing.id
            )
        )
        db.flush()
        row = existing
    else:
        row = ActiveEtfSourceObservation(
            fund_code=snapshot.fund.fund_code,
            data_date=snapshot.data_date,
            fetched_at=snapshot.fetched_at,
            source_provider=source_provider,
            source_url=snapshot.source_url or snapshot.fund.source_url,
            payload_hash=snapshot.payload_hash,
            normalized_hash=snapshot.normalized_hash,
            parser_version=snapshot.parser_version,
            raw_payload_gzip=b"",
            raw_size_bytes=0,
            holding_count=0,
            skipped_instrument_count=0,
        )
        db.add(row)
        db.flush()
    row.fetched_at = snapshot.fetched_at
    row.source_url = snapshot.source_url or snapshot.fund.source_url
    row.payload_hash = snapshot.payload_hash
    row.normalized_hash = snapshot.normalized_hash
    row.parser_version = snapshot.parser_version
    row.raw_payload_gzip = gzip.compress(raw_payload, compresslevel=9, mtime=0)
    row.raw_size_bytes = len(raw_payload)
    row.holding_count = len(snapshot.holdings)
    row.skipped_instrument_count = snapshot.skipped_instrument_count
    db.add_all(
        [
            ActiveEtfSourceHolding(
                observation_id=row.id,
                symbol=holding.symbol,
                name=holding.name,
                shares=holding.shares,
                weight_pct=holding.weight_pct,
                position_order=holding.position_order,
            )
            for holding in snapshot.holdings
        ]
    )


def _reconcile_snapshots(
    primary: ActiveEtfFundSnapshot,
    verification: ActiveEtfFundSnapshot | None,
    *,
    verification_supported: bool,
) -> tuple[str, dict]:
    sources = [_snapshot_evidence_dict(primary, "moneydj")]
    if verification is None:
        return (
            "single_source",
            {
                "reason": (
                    "verification_source_unavailable"
                    if verification_supported
                    else "official_source_unsupported"
                ),
                "sources": sources,
            },
        )
    sources.append(_snapshot_evidence_dict(verification, "issuer_official"))
    if primary.data_date != verification.data_date:
        return (
            "conflict",
            {
                "reason": "source_date_mismatch",
                "sources": sources,
                "primary_date": primary.data_date.isoformat(),
                "verification_date": verification.data_date.isoformat(),
            },
        )
    primary_rows = _rows_by_comparison_symbol(primary)
    verification_rows = _rows_by_comparison_symbol(verification)
    missing_from_official = sorted(primary_rows.keys() - verification_rows.keys())
    missing_from_primary = sorted(verification_rows.keys() - primary_rows.keys())
    share_mismatches = [
        {
            "symbol": symbol,
            "primary_shares": primary_rows[symbol].shares,
            "verification_shares": verification_rows[symbol].shares,
        }
        for symbol in sorted(primary_rows.keys() & verification_rows.keys())
        if primary_rows[symbol].shares != verification_rows[symbol].shares
    ]
    if missing_from_official or missing_from_primary or share_mismatches:
        return (
            "conflict",
            {
                "reason": "holding_mismatch",
                "sources": sources,
                "missing_from_official": missing_from_official[:20],
                "missing_from_primary": missing_from_primary[:20],
                "share_mismatches": share_mismatches[:20],
                "mismatch_count": (
                    len(missing_from_official)
                    + len(missing_from_primary)
                    + len(share_mismatches)
                ),
            },
        )
    return "verified", {"reason": "share_inventory_match", "sources": sources}


def _comparison_symbol(symbol: str) -> str:
    return symbol.split(".", 1)[0].strip().upper()


def _rows_by_comparison_symbol(
    snapshot: ActiveEtfFundSnapshot,
) -> dict[str, ActiveEtfHoldingRow]:
    rows: dict[str, ActiveEtfHoldingRow] = {}
    for row in snapshot.holdings:
        symbol = _comparison_symbol(row.symbol)
        if symbol in rows:
            raise ActiveEtfProviderError("active_etf_comparison_symbol_collision")
        rows[symbol] = row
    return rows


def _snapshot_evidence_dict(
    snapshot: ActiveEtfFundSnapshot,
    fallback_provider: str,
) -> dict:
    return {
        "source_provider": snapshot.source_provider or fallback_provider,
        "source_url": snapshot.source_url or snapshot.fund.source_url,
        "data_date": snapshot.data_date.isoformat(),
        "payload_hash": snapshot.payload_hash,
    }


def _changes_for_snapshots(
    current: ActiveEtfHoldingSnapshot,
    previous: ActiveEtfHoldingSnapshot,
) -> tuple[list[ActiveEtfChange], Decimal | None]:
    current_by_symbol = {holding.symbol: holding for holding in current.holdings}
    previous_by_symbol = {holding.symbol: holding for holding in previous.holdings}
    scale_ratios = [
        Decimal(current_by_symbol[symbol].shares) / Decimal(previous_by_symbol[symbol].shares)
        for symbol in current_by_symbol.keys() & previous_by_symbol.keys()
        if current_by_symbol[symbol].shares > 0 and previous_by_symbol[symbol].shares > 0
    ]
    common_scale_ratio_raw = (
        Decimal(str(median(scale_ratios))) if len(scale_ratios) >= _MIN_SCALE_SAMPLE else None
    )
    common_scale_ratio = (
        _quantize(common_scale_ratio_raw) if common_scale_ratio_raw is not None else None
    )
    verification_status = (
        "verified"
        if current.verification_status == previous.verification_status == "verified"
        else "single_source"
    )
    source_count = min(current.source_count, previous.source_count)
    changes: list[ActiveEtfChange] = []
    for symbol in sorted(current_by_symbol.keys() | previous_by_symbol.keys()):
        current_row = current_by_symbol.get(symbol)
        previous_row = previous_by_symbol.get(symbol)
        current_shares = current_row.shares if current_row is not None else 0
        previous_shares = previous_row.shares if previous_row is not None else 0
        share_delta = current_shares - previous_shares
        if share_delta == 0:
            continue
        if previous_row is None:
            action = "added"
        elif current_row is None:
            action = "removed"
        elif share_delta > 0:
            action = "increased"
        else:
            action = "decreased"
        share_delta_pct = (
            _quantize(Decimal(share_delta) / Decimal(previous_shares) * 100)
            if previous_shares > 0 and current_row is not None
            else None
        )
        relative_change = None
        likely_scale_change = False
        if (
            common_scale_ratio_raw is not None
            and common_scale_ratio_raw > 0
            and current_row is not None
            and previous_row is not None
            and previous_shares > 0
        ):
            relative_change = _quantize(
                (Decimal(current_shares) / Decimal(previous_shares) / common_scale_ratio_raw - 1)
                * 100
            )
            likely_scale_change = (
                share_delta_pct is not None
                and abs(relative_change) <= _LIKELY_SCALE_RESIDUAL_PCT
                and abs(share_delta_pct) >= _LIKELY_SCALE_RAW_CHANGE_PCT
            )
        current_weight = current_row.weight_pct if current_row is not None else Decimal(0)
        previous_weight = previous_row.weight_pct if previous_row is not None else Decimal(0)
        changes.append(
            ActiveEtfChange(
                action=action,
                fund_code=current.fund_code,
                fund_name=current.fund.name,
                symbol=symbol,
                name=(current_row or previous_row).name,
                source_provider=current.source_provider,
                source_url=current.source_url,
                verification_status=verification_status,
                source_count=source_count,
                fetched_at=current.fetched_at,
                data_date=current.data_date,
                previous_date=previous.data_date,
                current_shares=current_shares,
                previous_shares=previous_shares,
                share_delta=share_delta,
                share_delta_pct=share_delta_pct,
                current_weight_pct=current_weight,
                previous_weight_pct=previous_weight,
                weight_delta_pct_points=_quantize(current_weight - previous_weight),
                relative_share_change_pct=relative_change,
                likely_fund_scale_change=likely_scale_change,
            )
        )
    return changes, common_scale_ratio


def _build_consensus(changes: list[ActiveEtfChange]) -> list[ActiveEtfConsensus]:
    grouped: dict[str, list[ActiveEtfChange]] = defaultdict(list)
    for change in changes:
        grouped[change.symbol].append(change)
    consensus: list[ActiveEtfConsensus] = []
    for symbol, rows in grouped.items():
        fund_codes = {row.fund_code for row in rows}
        counts = defaultdict(int)
        for row in rows:
            counts[row.action] += 1
        positive = counts["added"] + counts["increased"]
        negative = counts["decreased"] + counts["removed"]
        direction = "mixed"
        if positive and not negative:
            direction = "increase"
        elif negative and not positive:
            direction = "decrease"
        consensus.append(
            ActiveEtfConsensus(
                symbol=symbol,
                name=rows[0].name,
                direction=direction,
                fund_count=len(fund_codes),
                added_count=counts["added"],
                increased_count=counts["increased"],
                decreased_count=counts["decreased"],
                removed_count=counts["removed"],
            )
        )
    return sorted(consensus, key=lambda row: (-row.fund_count, row.symbol))


def _snapshot_category(snapshot: ActiveEtfHoldingSnapshot) -> str | None:
    value = (snapshot.source_metadata or {}).get("category")
    return value if isinstance(value, str) and value else None


def _verification_reason(snapshot: ActiveEtfHoldingSnapshot) -> str | None:
    value = (snapshot.verification_details or {}).get("reason")
    return value if isinstance(value, str) and value else None


def _source_evidence_for_snapshot(
    db: Session,
    snapshot: ActiveEtfHoldingSnapshot,
) -> list[ActiveEtfSourceEvidence]:
    details_sources = (snapshot.verification_details or {}).get("sources", [])
    expected_keys = {
        (source.get("source_provider"), source.get("data_date"))
        for source in details_sources
        if isinstance(source, dict)
    }
    observations = list(
        db.scalars(
            select(ActiveEtfSourceObservation)
            .where(ActiveEtfSourceObservation.fund_code == snapshot.fund_code)
            .order_by(ActiveEtfSourceObservation.source_provider)
        )
    )
    return [
        ActiveEtfSourceEvidence(
            source_provider=observation.source_provider,
            source_url=observation.source_url,
            data_date=observation.data_date,
            fetched_at=observation.fetched_at,
            payload_hash=observation.payload_hash,
        )
        for observation in observations
        if (observation.source_provider, observation.data_date.isoformat()) in expected_keys
    ]


def _change_sort_key(change: ActiveEtfChange) -> tuple[int, int, str, str]:
    action_order = {"added": 0, "increased": 1, "decreased": 2, "removed": 3}
    return (
        action_order[change.action],
        -abs(change.share_delta),
        change.symbol,
        change.fund_code,
    )


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP)


__all__ = [
    "ActiveEtfHoldingsProvider",
    "ActiveEtfVerificationProvider",
    "get_active_etf_daily_response",
    "refresh_active_etf_holdings",
]
