from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.calibration import forward_validation as shared_forward_validation
from ai_stock_sentinel.calibration.forward_validation import (
    DEFAULT_BENCHMARK_SYMBOL,
    DEFAULT_FORWARD_WINDOWS,
    ForwardValidationAdapter,
    TERMINAL_FORWARD_VALIDATION_SKIP_REASONS,
    is_terminal_forward_validation_skip_reason,
)
from ai_stock_sentinel.calibration.repository import (
    load_benchmark_prices_from_prepared_market_context as load_cached_benchmark_prices,
    load_price_series_from_raw_data as load_shared_price_series,
)
from ai_stock_sentinel.daily_radar.calibration import calibration_candidates_from_fixture
from ai_stock_sentinel.daily_radar.repository import PUBLIC_RUN_STATUSES
from ai_stock_sentinel.daily_radar.scoring import RULE_VERSION, SCORING_VERSION
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarForwardValidationResult,
    DailyRadarRun,
)


FORWARD_VALIDATION_VERSION = "daily-radar-forward-validation-v2"
FORWARD_VALIDATION_REPORT_VERSION = "daily-radar-forward-validation-report-v2"
DEFAULT_HIT_THRESHOLD_PCT = 0.0


@dataclass(frozen=True)
class ForwardValidationEvaluation:
    report: dict[str, Any]
    outcomes: list[dict[str, Any]]


def build_forward_validation_report(
    candidates: Iterable[Mapping[str, Any]],
    *,
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_prices: Sequence[Mapping[str, Any]],
    market: str,
    sample_source: str,
    as_of_date: date | None = None,
    windows: Sequence[int] = DEFAULT_FORWARD_WINDOWS,
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
    validation_version: str = FORWARD_VALIDATION_VERSION,
    hit_threshold_pct: float = DEFAULT_HIT_THRESHOLD_PCT,
    windows_by_candidate: Mapping[str, Sequence[int]] | None = None,
    aggregation_scope: str = "evaluation_batch",
) -> ForwardValidationEvaluation:
    active_windows = _ordered_positive_values(windows)
    candidate_list = [dict(candidate) for candidate in candidates]
    evaluation = shared_forward_validation.evaluate_forward_validation(
        candidate_list,
        price_series_by_symbol=price_series_by_symbol,
        benchmark_prices=benchmark_prices,
        adapter=DAILY_RADAR_FORWARD_ADAPTER,
        as_of_date=as_of_date,
        windows=active_windows,
        benchmark_symbol=benchmark_symbol,
        validation_version=validation_version,
        hit_threshold_pct=hit_threshold_pct,
        windows_by_candidate=windows_by_candidate,
    )
    outcomes = evaluation.outcomes

    report = _build_report_from_outcomes(
        candidate_list,
        outcomes,
        market=market,
        sample_source=sample_source,
        as_of_date=as_of_date,
        active_windows=active_windows,
        benchmark_symbol=benchmark_symbol,
        validation_version=validation_version,
        hit_threshold_pct=hit_threshold_pct,
        aggregation_scope=aggregation_scope,
    )
    return ForwardValidationEvaluation(report=report, outcomes=outcomes)


def build_forward_validation_report_from_outcomes(
    candidates: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
    *,
    market: str,
    sample_source: str,
    as_of_date: date | None,
    windows: Sequence[int] = DEFAULT_FORWARD_WINDOWS,
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
    validation_version: str = FORWARD_VALIDATION_VERSION,
    hit_threshold_pct: float = DEFAULT_HIT_THRESHOLD_PCT,
    aggregation_scope: str = "persisted_fixed_date_cohort",
) -> dict[str, Any]:
    return _build_report_from_outcomes(
        [dict(candidate) for candidate in candidates],
        [dict(outcome) for outcome in outcomes],
        market=market,
        sample_source=sample_source,
        as_of_date=as_of_date,
        active_windows=_ordered_positive_values(windows),
        benchmark_symbol=benchmark_symbol,
        validation_version=validation_version,
        hit_threshold_pct=hit_threshold_pct,
        aggregation_scope=aggregation_scope,
    )


def _build_report_from_outcomes(
    candidates: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    *,
    market: str,
    sample_source: str,
    as_of_date: date | None,
    active_windows: Sequence[int],
    benchmark_symbol: str,
    validation_version: str,
    hit_threshold_pct: float,
    aggregation_scope: str,
) -> dict[str, Any]:
    outcome_rows = [dict(outcome) for outcome in outcomes]
    valid_outcomes = [outcome for outcome in outcome_rows if outcome["status"] == "validated"]
    selected_valid_outcomes = [
        outcome for outcome in valid_outcomes if _selection_status(outcome) == "selected"
    ]
    shadow_valid_outcomes = [
        outcome for outcome in valid_outcomes if _selection_status(outcome) == "shadow"
    ]
    return {
        "metadata": {
            "report_version": FORWARD_VALIDATION_REPORT_VERSION,
            "validation_version": validation_version,
            "market": market,
            "sample_source": sample_source,
            "as_of_date": as_of_date.isoformat() if as_of_date is not None else None,
            "windows": active_windows,
            "benchmark_symbol": benchmark_symbol,
            "hit_threshold_pct": hit_threshold_pct,
            "aggregation_scope": aggregation_scope,
            "positioning": "rule_quality_calibration_diagnostic_not_performance_marketing",
        },
        "sample_summary": _sample_summary(candidates, outcome_rows, active_windows),
        "selection_diagnostics": _selection_diagnostics(
            outcome_rows,
            active_windows,
            hit_threshold_pct=hit_threshold_pct,
        ),
        "shadow_prefilter_reason_outcomes": _grouped_outcomes(
            shadow_valid_outcomes,
            _prefilter_reason_codes,
        ),
        "eligibility_audit_outcomes": _grouped_outcomes(
            [row for row in shadow_valid_outcomes if _shadow_cohort(row) == "eligibility_audit"],
            _prefilter_reason_codes,
        ),
        "bucket_outcomes": _grouped_outcomes(selected_valid_outcomes, _primary_bucket),
        "secondary_bucket_outcomes": _grouped_outcomes(selected_valid_outcomes, _secondary_buckets),
        "rule_outcomes": _grouped_outcomes(selected_valid_outcomes, _matched_rule_codes),
        "risk_label_outcomes": _grouped_outcomes(selected_valid_outcomes, _risk_labels),
        "market_regime_outcomes": _grouped_outcomes(selected_valid_outcomes, _market_regime),
        "relative_strength_bucket_outcomes": _grouped_outcomes(
            selected_valid_outcomes,
            _relative_strength_bucket,
        ),
        "repeat_status_outcomes": _grouped_outcomes(selected_valid_outcomes, _repeat_status),
        "score_decile_outcomes": _grouped_outcomes(selected_valid_outcomes, _score_decile),
        "data_freshness_outcomes": _grouped_outcomes(
            selected_valid_outcomes,
            _data_freshness_status_from_outcome,
        ),
        "ablation_candidates": _ablation_candidates(selected_valid_outcomes),
        "skip_reasons": dict(
            sorted(
                Counter(
                    str(row.get("skip_reason") or "unknown")
                    for row in outcome_rows
                    if row["status"] == "skipped"
                ).items()
            )
        ),
        "version_manifest": {
            "scoring_version": SCORING_VERSION,
            "rule_version": RULE_VERSION,
            "validation_version": validation_version,
            "report_version": FORWARD_VALIDATION_REPORT_VERSION,
            "live_scoring_changed": False,
            "live_ranking_changed": False,
            "diagnostic_only": True,
        },
    }


def evaluate_forward_window(
    candidate: Mapping[str, Any],
    *,
    price_series: Sequence[Mapping[str, Any]],
    benchmark_prices: Sequence[Mapping[str, Any]],
    window_days: int,
    as_of_date: date | None,
    benchmark_symbol: str,
    validation_version: str,
    hit_threshold_pct: float,
) -> dict[str, Any]:
    return shared_forward_validation.evaluate_forward_window(
        candidate,
        price_series=price_series,
        benchmark_prices=benchmark_prices,
        adapter=DAILY_RADAR_FORWARD_ADAPTER,
        window_days=window_days,
        as_of_date=as_of_date,
        benchmark_symbol=benchmark_symbol,
        validation_version=validation_version,
        hit_threshold_pct=hit_threshold_pct,
    )


def forward_validation_candidates_from_runs(
    session: Session,
    *,
    market: str,
    start_date: date | None = None,
    end_date: date | None = None,
    statuses: tuple[str, ...] = PUBLIC_RUN_STATUSES,
) -> list[dict[str, Any]]:
    query = (
        select(DailyRadarCandidate, DailyRadarRun)
        .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
        .where(DailyRadarRun.market == market, DailyRadarRun.status.in_(statuses))
    )
    if start_date is not None:
        query = query.where(DailyRadarRun.run_date >= start_date)
    if end_date is not None:
        query = query.where(DailyRadarRun.run_date <= end_date)
    rows = session.execute(
        query.order_by(
            DailyRadarRun.run_date.asc(),
            DailyRadarRun.created_at.desc(),
            DailyRadarRun.id.desc(),
            DailyRadarCandidate.observation_score.desc(),
            DailyRadarCandidate.symbol.asc(),
        )
    ).all()
    latest_run_by_date: dict[date, int] = {}
    snapshots: list[dict[str, Any]] = []
    for candidate, run in rows:
        selected_run_id = latest_run_by_date.setdefault(run.run_date, run.id)
        if run.id == selected_run_id:
            snapshots.append(_candidate_snapshot(candidate, run))
    return snapshots


def load_forward_prices_from_fixture(
    fixture_dir: str | Path,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], str]:
    path = Path(fixture_dir) / "forward_prices.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, Mapping) else None
    prices_by_symbol = {
        str(record.get("symbol")): list(record.get("prices") or [])
        for record in records or []
        if isinstance(record, Mapping)
    }
    benchmark = payload.get("benchmark") if isinstance(payload, Mapping) else {}
    benchmark_symbol = str(_mapping(benchmark).get("symbol") or DEFAULT_BENCHMARK_SYMBOL)
    benchmark_prices = list(_mapping(benchmark).get("prices") or [])
    return prices_by_symbol, benchmark_prices, benchmark_symbol


def forward_validation_fixture_inputs(
    *,
    fixture_dir: str | Path,
    run_date: date,
    market: str,
    candidate_limit: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[dict[str, Any]], str]:
    candidates = calibration_candidates_from_fixture(
        fixture_dir=fixture_dir,
        run_date=run_date,
        market=market,
        candidate_limit=candidate_limit,
    )
    prices_by_symbol, benchmark_prices, benchmark_symbol = load_forward_prices_from_fixture(fixture_dir)
    return candidates, prices_by_symbol, benchmark_prices, benchmark_symbol


def load_price_series_from_raw_data(
    session: Session,
    *,
    symbols: Iterable[str],
    start_date: date,
    end_date: date,
) -> dict[str, list[dict[str, Any]]]:
    return load_shared_price_series(
        session,
        symbols=list(symbols),
        start_date=start_date,
        end_date=end_date,
    )


def load_benchmark_prices_from_prepared_market_context(
    session: Session,
    *,
    market: str,
    benchmark_symbol: str,
    as_of_date: date,
    required_dates: Iterable[date] = (),
) -> list[dict[str, Any]]:
    return load_cached_benchmark_prices(
        session,
        market=market,
        benchmark_symbol=benchmark_symbol,
        as_of_date=as_of_date,
        required_dates=required_dates,
    )


def validate_forward_validation_benchmark(
    candidates: Iterable[Mapping[str, Any]],
    *,
    benchmark_symbol: str,
) -> None:
    mismatched_candidate_ids = [
        int(candidate["candidate_id"])
        for candidate in candidates
        if candidate.get("candidate_id") is not None
        and candidate_forward_validation_benchmark_symbol(
            candidate.get("input_snapshot")
        )
        != benchmark_symbol
    ]
    if mismatched_candidate_ids:
        raise ValueError(
            "Daily Radar forward validation benchmark must match the benchmark "
            "used by every scoring candidate"
        )


def candidate_forward_validation_benchmark_symbol(input_snapshot: Any) -> str:
    replay_input = _mapping(_mapping(input_snapshot).get("replay_input"))
    market_context = _mapping(replay_input.get("market_context"))
    benchmark = _mapping(market_context.get("benchmark"))
    return str(benchmark.get("symbol") or DEFAULT_BENCHMARK_SYMBOL)


def upsert_forward_validation_results(
    session: Session,
    outcomes: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    outcome_rows = list(outcomes)
    candidate_ids = {
        int(outcome["candidate_id"])
        for outcome in outcome_rows
        if outcome.get("candidate_id") is not None
    }
    candidate_identity_by_id = {
        int(candidate_id): (
            run_date,
            candidate_forward_validation_benchmark_symbol(input_snapshot),
        )
        for candidate_id, run_date, input_snapshot in session.execute(
            select(
                DailyRadarCandidate.id,
                DailyRadarRun.run_date,
                DailyRadarCandidate.input_snapshot,
            )
            .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
            .where(DailyRadarCandidate.id.in_(candidate_ids))
        )
    }
    written = 0
    validated = 0
    skipped = 0
    retryable_skipped = 0
    terminal_skipped = 0
    for outcome in outcome_rows:
        candidate_id = outcome.get("candidate_id")
        if candidate_id is None:
            continue
        signal_date = _parse_date(outcome.get("signal_date"))
        if signal_date is None:
            raise ValueError(
                "Daily Radar validation outcome requires a valid signal_date"
            )
        benchmark_symbol = str(outcome.get("benchmark_symbol") or "")
        candidate_identity = candidate_identity_by_id.get(int(candidate_id))
        if candidate_identity is None:
            raise ValueError(
                f"Daily Radar validation candidate {candidate_id} does not exist"
            )
        expected_signal_date, expected_benchmark_symbol = candidate_identity
        if (
            signal_date != expected_signal_date
            or benchmark_symbol != expected_benchmark_symbol
        ):
            raise ValueError(
                "Daily Radar validation identity must match its scoring candidate"
            )
        existing = session.execute(
            select(DailyRadarForwardValidationResult).where(
                DailyRadarForwardValidationResult.candidate_id == int(candidate_id),
                DailyRadarForwardValidationResult.window_days == int(outcome["window_days"]),
                DailyRadarForwardValidationResult.validation_version == str(outcome["validation_version"]),
            )
        ).scalar_one_or_none()
        payload = dict(_mapping(outcome.get("outcome")))
        if outcome.get("status") == "skipped":
            payload = {"skip_reason": outcome.get("skip_reason")}
        if existing is None:
            existing = DailyRadarForwardValidationResult(
                candidate_id=int(candidate_id),
                window_days=int(outcome["window_days"]),
                validation_version=str(outcome["validation_version"]),
                status=str(outcome["status"]),
                signal_date=signal_date,
                target_date=_parse_date(outcome.get("target_date")),
                benchmark_symbol=benchmark_symbol,
                evaluation_as_of_date=_parse_date(outcome.get("evaluation_as_of_date")),
                outcome=payload,
                skip_reason=outcome.get("skip_reason"),
            )
        else:
            existing.status = str(outcome["status"])
            existing.signal_date = signal_date
            existing.target_date = _parse_date(outcome.get("target_date"))
            existing.benchmark_symbol = benchmark_symbol
            existing.evaluation_as_of_date = _parse_date(outcome.get("evaluation_as_of_date"))
            existing.outcome = payload
            existing.skip_reason = outcome.get("skip_reason")
        session.add(existing)
        written += 1
        if outcome.get("status") == "validated":
            validated += 1
        else:
            skipped += 1
            if is_terminal_forward_validation_skip_reason(outcome.get("skip_reason")):
                terminal_skipped += 1
            else:
                retryable_skipped += 1
    session.flush()
    return {
        "records_written": written,
        "validated_count": validated,
        "skipped_count": skipped,
        "retryable_skipped_count": retryable_skipped,
        "terminal_skipped_count": terminal_skipped,
    }


def persisted_forward_validation_outcomes(
    session: Session,
    candidates: Iterable[Mapping[str, Any]],
    *,
    windows: Sequence[int],
    as_of_date: date,
    validation_version: str = FORWARD_VALIDATION_VERSION,
) -> list[dict[str, Any]]:
    candidate_by_id = {
        int(candidate["candidate_id"]): dict(candidate)
        for candidate in candidates
        if candidate.get("candidate_id") is not None
    }
    if not candidate_by_id:
        return []
    active_windows = _ordered_positive_values(windows)
    results = session.execute(
        select(DailyRadarForwardValidationResult)
        .where(
            DailyRadarForwardValidationResult.candidate_id.in_(candidate_by_id),
            DailyRadarForwardValidationResult.window_days.in_(active_windows),
            DailyRadarForwardValidationResult.validation_version == validation_version,
        )
        .order_by(
            DailyRadarForwardValidationResult.signal_date.asc(),
            DailyRadarForwardValidationResult.candidate_id.asc(),
            DailyRadarForwardValidationResult.window_days.asc(),
        )
    ).scalars()
    outcomes: list[dict[str, Any]] = []
    for result in results:
        candidate = candidate_by_id[result.candidate_id]
        candidate_signal_date = _parse_date(candidate.get("record_date"))
        expected_benchmark = candidate_forward_validation_benchmark_symbol(
            candidate.get("input_snapshot")
        )
        if (
            result.status not in {"validated", "skipped"}
            or candidate_signal_date is None
            or result.signal_date != candidate_signal_date
            or result.benchmark_symbol != expected_benchmark
        ):
            continue
        if (
            result.status == "validated"
            and (result.target_date is None or result.target_date > as_of_date)
        ):
            continue
        if (
            result.status == "skipped"
            and (
                result.evaluation_as_of_date is None
                or result.evaluation_as_of_date > as_of_date
            )
        ):
            continue
        outcomes.append(
            {
                "candidate_id": result.candidate_id,
                "symbol": str(candidate.get("symbol") or ""),
                "signal_date": result.signal_date.isoformat(),
                "window_days": result.window_days,
                "validation_version": result.validation_version,
                "benchmark_symbol": result.benchmark_symbol,
                "evaluation_as_of_date": (
                    result.evaluation_as_of_date.isoformat()
                    if result.evaluation_as_of_date
                    else None
                ),
                "candidate_snapshot": dict(DAILY_RADAR_FORWARD_ADAPTER.candidate_snapshot(candidate)),
                "status": result.status,
                "target_date": result.target_date.isoformat() if result.target_date else None,
                "skip_reason": result.skip_reason,
                "outcome": dict(result.outcome or {}) if result.status == "validated" else {},
            }
        )
    return outcomes


def default_due_start_date(as_of_date: date, max_window: int = max(DEFAULT_FORWARD_WINDOWS)) -> date:
    return shared_forward_validation.default_due_start_date(as_of_date, max_window)


def due_windows_by_candidate(
    candidates: Iterable[Mapping[str, Any]],
    *,
    as_of_date: date,
    windows: Sequence[int],
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    benchmark_prices: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, list[int]]:
    return shared_forward_validation.due_windows_by_candidate(
        candidates,
        adapter=DAILY_RADAR_FORWARD_ADAPTER,
        as_of_date=as_of_date,
        windows=windows,
        price_series_by_symbol=price_series_by_symbol,
        benchmark_prices=benchmark_prices,
    )


def exclude_persisted_daily_radar_windows(
    session: Session,
    windows_by_candidate: Mapping[str, Sequence[int]],
    *,
    validation_version: str = FORWARD_VALIDATION_VERSION,
    benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
) -> dict[str, list[int]]:
    candidate_ids = [
        int(key.removeprefix("id:"))
        for key in windows_by_candidate
        if key.startswith("id:") and key.removeprefix("id:").isdigit()
    ]
    if not candidate_ids:
        return {
            key: list(windows)
            for key, windows in windows_by_candidate.items()
            if windows
        }
    terminal = {
        (result.candidate_id, result.window_days)
        for result, candidate, run in session.execute(
            select(
                DailyRadarForwardValidationResult,
                DailyRadarCandidate,
                DailyRadarRun,
            )
            .join(
                DailyRadarCandidate,
                DailyRadarForwardValidationResult.candidate_id
                == DailyRadarCandidate.id,
            )
            .join(DailyRadarRun, DailyRadarCandidate.run_id == DailyRadarRun.id)
            .where(
                DailyRadarForwardValidationResult.candidate_id.in_(candidate_ids),
                DailyRadarForwardValidationResult.validation_version == validation_version,
            )
        ).all()
        if (
            result.signal_date == run.run_date
            and result.benchmark_symbol == benchmark_symbol
            and result.benchmark_symbol
            == candidate_forward_validation_benchmark_symbol(
                candidate.input_snapshot
            )
            and (
                result.status == "validated"
                or (
                    result.status == "skipped"
                    and result.skip_reason
                    in TERMINAL_FORWARD_VALIDATION_SKIP_REASONS
                )
            )
        )
    }
    pending: dict[str, list[int]] = {}
    for key, windows in windows_by_candidate.items():
        if key.startswith("id:") and key.removeprefix("id:").isdigit():
            candidate_id = int(key.removeprefix("id:"))
            remaining = [
                int(window)
                for window in windows
                if (candidate_id, int(window)) not in terminal
            ]
        else:
            remaining = [int(window) for window in windows]
        if remaining:
            pending[key] = remaining
    return pending


def symbols_requiring_forward_price_refresh(
    candidates: Iterable[Mapping[str, Any]],
    *,
    windows_by_candidate: Mapping[str, Sequence[int]],
    price_series_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    benchmark_prices: Sequence[Mapping[str, Any]] | None = None,
    as_of_date: date,
) -> list[str]:
    return shared_forward_validation.symbols_requiring_forward_price_refresh(
        candidates,
        windows_by_candidate=windows_by_candidate,
        price_series_by_symbol=price_series_by_symbol,
        benchmark_prices=benchmark_prices,
        as_of_date=as_of_date,
    )


def benchmark_requires_forward_price_refresh(
    candidates: Iterable[Mapping[str, Any]],
    *,
    windows_by_candidate: Mapping[str, Sequence[int]],
    benchmark_prices: Sequence[Mapping[str, Any]],
    as_of_date: date,
) -> bool:
    return shared_forward_validation.benchmark_requires_forward_price_refresh(
        candidates,
        windows_by_candidate=windows_by_candidate,
        benchmark_prices=benchmark_prices,
        as_of_date=as_of_date,
    )


def merge_price_series(
    existing: Mapping[str, Sequence[Mapping[str, Any]]],
    fetched: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return shared_forward_validation.merge_price_series(existing, fetched)


def write_report(report: Mapping[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sample_summary(
    candidates: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    windows: Sequence[int],
) -> dict[str, Any]:
    validated_by_window = Counter(int(outcome["window_days"]) for outcome in outcomes if outcome["status"] == "validated")
    skipped_by_window = Counter(int(outcome["window_days"]) for outcome in outcomes if outcome["status"] == "skipped")
    return {
        "candidate_count": len(candidates),
        "window_count": len(windows),
        "evaluated_sample_count": len(outcomes),
        "validated_sample_count": sum(validated_by_window.values()),
        "skipped_sample_count": sum(skipped_by_window.values()),
        "validated_by_window": {str(window): validated_by_window[window] for window in windows},
        "skipped_by_window": {str(window): skipped_by_window[window] for window in windows},
    }


def _selection_diagnostics(
    outcomes: Sequence[Mapping[str, Any]],
    windows: Sequence[int],
    *,
    hit_threshold_pct: float,
) -> dict[str, dict[str, Any]]:
    diagnostics: dict[str, dict[str, Any]] = {}
    for window in windows:
        rows = [row for row in outcomes if int(row.get("window_days") or 0) == window]
        selected = [row for row in rows if _selection_status(row) == "selected"]
        comparable_shadow = [
            row
            for row in rows
            if _selection_status(row) == "shadow" and _shadow_cohort(row) == "comparable"
        ]
        eligibility_audit = [
            row
            for row in rows
            if _selection_status(row) == "shadow" and _shadow_cohort(row) == "eligibility_audit"
        ]
        selected_valid = _validated_rows(selected)
        comparable_valid = _validated_rows(comparable_shadow)
        comparable_pool = selected_valid + comparable_valid
        diagnostics[str(window)] = {
            "population_scope": "selected_plus_comparable_shadow_within_daily_universe",
            "selected_validation": _validation_summary(selected),
            "comparable_shadow_validation": _validation_summary(comparable_shadow),
            "eligibility_audit_validation": _validation_summary(eligibility_audit),
            "absolute_positive": _conditional_selection_metric(
                selected_valid,
                comparable_valid,
                hit=lambda row: _is_hit(row),
            ),
            "benchmark_outperformance": _conditional_selection_metric(
                selected_valid,
                comparable_valid,
                hit=lambda row: _is_benchmark_outperformance(row, hit_threshold_pct),
            ),
            "validated_selected_share_within_comparable_pool": _ratio(
                len(selected_valid),
                len(comparable_pool),
            ),
        }
    return diagnostics


def _selection_status(outcome: Mapping[str, Any]) -> str:
    return str(_mapping(outcome.get("candidate_snapshot")).get("selection_status") or "selected")


def _shadow_cohort(outcome: Mapping[str, Any]) -> str:
    snapshot = _mapping(outcome.get("candidate_snapshot"))
    explicit = str(snapshot.get("shadow_cohort") or "")
    if explicit in {"comparable", "eligibility_audit"}:
        return explicit
    reason_codes = set(_prefilter_reason_codes(outcome))
    if reason_codes.intersection({"low_liquidity", "min_price"}):
        return "eligibility_audit"
    return "comparable"


def _validated_rows(outcomes: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in outcomes if row.get("status") == "validated"]


def _validation_summary(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    attempted_count = len(outcomes)
    validated_count = sum(row.get("status") == "validated" for row in outcomes)
    skipped = [row for row in outcomes if row.get("status") == "skipped"]
    skip_reasons = Counter(str(row.get("skip_reason") or "unknown") for row in skipped)
    return {
        "attempted_count": attempted_count,
        "validated_count": validated_count,
        "skipped_count": len(skipped),
        "validation_rate": _ratio(validated_count, attempted_count),
        "skip_rate": _ratio(len(skipped), attempted_count),
        "skip_reasons": dict(sorted(skip_reasons.items())),
    }


def _conditional_selection_metric(
    selected: Sequence[Mapping[str, Any]],
    comparable_shadow: Sequence[Mapping[str, Any]],
    *,
    hit: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    selected_hits = sum(bool(hit(row)) for row in selected)
    shadow_hits = sum(bool(hit(row)) for row in comparable_shadow)
    observed_positive_count = selected_hits + shadow_hits
    return {
        "selected_hit_count": selected_hits,
        "comparable_shadow_hit_count": shadow_hits,
        "conditional_selected_precision": _ratio(selected_hits, len(selected)),
        "conditional_recall_within_observed_comparable_pool": _ratio(
            selected_hits,
            observed_positive_count,
        ),
        "observed_comparable_shadow_miss_share": _ratio(
            shadow_hits,
            observed_positive_count,
        ),
    }


def _is_hit(outcome: Mapping[str, Any]) -> bool:
    return _mapping(outcome.get("outcome")).get("hit_above_threshold") is True


def _is_benchmark_outperformance(outcome: Mapping[str, Any], threshold_pct: float) -> bool:
    excess_return = _float_or_none(
        _mapping(outcome.get("outcome")).get("excess_return_vs_benchmark_pct")
    )
    return excess_return is not None and excess_return > threshold_pct


def _grouped_outcomes(
    outcomes: Sequence[Mapping[str, Any]],
    dimension: Any,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[int, list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for outcome in outcomes:
        values = dimension(outcome)
        if isinstance(values, str):
            values = [values]
        for value in values:
            grouped[str(value)][int(outcome["window_days"])].append(outcome)
    return {
        group: {
            str(window): _aggregate_outcomes(rows)
            for window, rows in sorted(windows.items())
        }
        for group, windows in sorted(grouped.items())
    }


def _aggregate_outcomes(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(_mapping(outcome.get("outcome"))) for outcome in outcomes]
    forward_returns = [_float(row.get("forward_return_pct")) for row in rows]
    excess_returns = [_float(row.get("excess_return_vs_benchmark_pct")) for row in rows]
    mfe = [_float(row.get("max_favorable_excursion_pct")) for row in rows]
    mae = [_float(row.get("max_adverse_excursion_pct")) for row in rows]
    positives = [value for value in forward_returns if value > 0]
    negatives = [value for value in forward_returns if value < 0]
    known_defense = [row for row in rows if row.get("close_below_defense_reference") is not None]
    close_below_count = sum(1 for row in known_defense if row.get("close_below_defense_reference") is True)
    return {
        "sample_count": len(rows),
        "average_forward_return_pct": _average(forward_returns),
        "average_excess_return_vs_benchmark_pct": _average(excess_returns),
        "average_max_favorable_excursion_pct": _average(mfe),
        "average_max_adverse_excursion_pct": _average(mae),
        "close_below_defense_reference_count": close_below_count,
        "close_below_defense_reference_ratio": _ratio(close_below_count, len(known_defense)),
        "hit_rate_above_threshold": _ratio(sum(1 for row in rows if row.get("hit_above_threshold") is True), len(rows)),
        "profit_factor_like_ratio": _profit_factor_like_ratio(positives, negatives),
    }


def _ablation_candidates(outcomes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rule_groups = _grouped_outcomes(outcomes, _matched_rule_codes)
    for rule_id, windows in rule_groups.items():
        for window, metrics in windows.items():
            sample_count = int(metrics.get("sample_count") or 0)
            average_excess = metrics.get("average_excess_return_vs_benchmark_pct")
            if sample_count < 5 or (average_excess is not None and float(average_excess) < 0):
                rows.append(
                    {
                        "dimension": "matched_rule_code",
                        "value": rule_id,
                        "window_days": int(window),
                        "sample_count": sample_count,
                        "average_excess_return_vs_benchmark_pct": average_excess,
                        "reason": "low_sample_or_negative_excess_diagnostic_only",
                    }
                )
    return sorted(rows, key=lambda row: (row["value"], row["window_days"]))


def _candidate_dimensions(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "primary_bucket": _primary_bucket_from_candidate(candidate),
        "secondary_buckets": _secondary_buckets_from_candidate(candidate),
        "matched_rule_codes": _matched_rule_codes_from_candidate(candidate),
        "risk_labels": _risk_labels_from_candidate(candidate),
        "market_regime": _market_regime_from_candidate(candidate),
        "relative_strength_bucket": _relative_strength_bucket_from_candidate(candidate),
        "repeat_status": str(candidate.get("repeat_status") or "unknown"),
        "score_decile": _score_decile_from_candidate(candidate),
        "data_freshness_status": _data_freshness_status(candidate),
        "selection_status": str(candidate.get("selection_status") or "selected"),
        "shadow_cohort": candidate.get("shadow_cohort"),
        "prefilter_status": str(candidate.get("prefilter_status") or "accepted"),
        "prefilter_reason_codes": [
            str(reason.get("code") or "")
            for reason in candidate.get("prefilter_reasons") or []
            if isinstance(reason, Mapping) and str(reason.get("code") or "")
        ],
    }


def _primary_bucket(outcome: Mapping[str, Any]) -> str:
    return str(_mapping(outcome.get("candidate_snapshot")).get("primary_bucket") or "unknown")


def _secondary_buckets(outcome: Mapping[str, Any]) -> list[str]:
    values = _mapping(outcome.get("candidate_snapshot")).get("secondary_buckets")
    return list(values) if isinstance(values, list) and values else ["none"]


def _matched_rule_codes(outcome: Mapping[str, Any]) -> list[str]:
    values = _mapping(outcome.get("candidate_snapshot")).get("matched_rule_codes")
    return list(values) if isinstance(values, list) and values else ["none"]


def _risk_labels(outcome: Mapping[str, Any]) -> list[str]:
    values = _mapping(outcome.get("candidate_snapshot")).get("risk_labels")
    return list(values) if isinstance(values, list) and values else ["none"]


def _prefilter_reason_codes(outcome: Mapping[str, Any]) -> list[str]:
    values = _mapping(outcome.get("candidate_snapshot")).get("prefilter_reason_codes")
    return list(values) if isinstance(values, list) and values else ["none"]


def _market_regime(outcome: Mapping[str, Any]) -> str:
    return str(_mapping(outcome.get("candidate_snapshot")).get("market_regime") or "unknown")


def _relative_strength_bucket(outcome: Mapping[str, Any]) -> str:
    return str(_mapping(outcome.get("candidate_snapshot")).get("relative_strength_bucket") or "unknown")


def _repeat_status(outcome: Mapping[str, Any]) -> str:
    return str(_mapping(outcome.get("candidate_snapshot")).get("repeat_status") or "unknown")


def _score_decile(outcome: Mapping[str, Any]) -> str:
    return str(_mapping(outcome.get("candidate_snapshot")).get("score_decile") or "unknown")


def _data_freshness_status_from_outcome(outcome: Mapping[str, Any]) -> str:
    return str(_mapping(outcome.get("candidate_snapshot")).get("data_freshness_status") or "unknown")


def _primary_bucket_from_candidate(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("primary_bucket") or "unknown")


def _secondary_buckets_from_candidate(candidate: Mapping[str, Any]) -> list[str]:
    values = candidate.get("secondary_buckets")
    return [str(value) for value in values] if isinstance(values, list) and values else ["none"]


def _matched_rule_codes_from_candidate(candidate: Mapping[str, Any]) -> list[str]:
    codes: list[str] = []
    for rule in _as_list(candidate.get("matched_rules")):
        if isinstance(rule, Mapping):
            codes.append(str(rule.get("rule_id") or "unknown_rule"))
        else:
            codes.append(str(rule))
    return codes or ["none"]


def _risk_labels_from_candidate(candidate: Mapping[str, Any]) -> list[str]:
    values = candidate.get("risk_labels")
    return [str(value) for value in values] if isinstance(values, list) and values else ["none"]


def _market_regime_from_candidate(candidate: Mapping[str, Any]) -> str:
    input_snapshot = _mapping(candidate.get("input_snapshot"))
    market = _mapping(input_snapshot.get("market_context"))
    score_market = _mapping(_mapping(_mapping(candidate.get("score_breakdown")).get("market_context")).get("details"))
    return str(market.get("regime") or score_market.get("regime") or "unknown")


def _relative_strength_bucket_from_candidate(candidate: Mapping[str, Any]) -> str:
    relative_strength = _mapping(_mapping(candidate.get("score_breakdown")).get("relative_strength"))
    freshness = str(relative_strength.get("freshness") or "")
    if freshness and freshness != "fresh":
        return freshness
    value = relative_strength.get("relative_value")
    if value is None:
        return "missing"
    numeric = _float(value)
    if numeric >= 0.02:
        return "positive"
    if numeric <= -0.02:
        return "negative"
    return "neutral"


def _score_decile_from_candidate(candidate: Mapping[str, Any]) -> str:
    score = _int(candidate.get("observation_score"))
    lower = min(90, max(0, (score // 10) * 10))
    upper = lower + 9
    return f"{lower:02d}-{upper:02d}"


def _data_freshness_status(candidate: Mapping[str, Any]) -> str:
    if "data_gap" in set(_risk_labels_from_candidate(candidate)):
        return "data_gap"
    signal_date = _parse_date(candidate.get("record_date"))
    ohlcv_date = _parse_date(_mapping(candidate.get("data_dates")).get("ohlcv"))
    if signal_date is not None and ohlcv_date is not None and ohlcv_date < signal_date:
        return "stale"
    if ohlcv_date is None:
        return "unknown"
    return "fresh"


def _candidate_snapshot(candidate: DailyRadarCandidate, run: DailyRadarRun) -> dict[str, Any]:
    return {
        "candidate_id": candidate.id,
        "run_id": run.id,
        "symbol": candidate.symbol,
        "name": candidate.name,
        "record_date": run.run_date.isoformat(),
        "primary_bucket": candidate.primary_bucket,
        "secondary_buckets": list(candidate.secondary_buckets or []),
        "observation_score": candidate.observation_score,
        "bucket_scores": dict(candidate.bucket_scores or {}),
        "risk_labels": list(candidate.risk_labels or []),
        "matched_rules": list(candidate.matched_rules or []),
        "repeat_status": candidate.repeat_status,
        "score_breakdown": dict(candidate.score_breakdown or {}),
        "input_snapshot": dict(candidate.input_snapshot or {}),
        "data_dates": dict(candidate.data_dates or {}),
        "selection_status": candidate.selection_status,
        "prefilter_status": candidate.prefilter_status,
        "prefilter_reasons": list(candidate.prefilter_reasons or []),
        "shadow_cohort": candidate.shadow_cohort,
    }


def _entry_price(candidate: Mapping[str, Any], prices: Mapping[date, Mapping[str, float]], signal_date: date) -> float | None:
    snapshot_close = _float_or_none(_mapping(_mapping(candidate.get("input_snapshot")).get("ohlcv")).get("close"))
    if snapshot_close is not None and snapshot_close > 0:
        return snapshot_close
    return _close_on(prices, signal_date)


def _close_on(prices: Mapping[date, Mapping[str, float]], row_date: date) -> float | None:
    row = prices.get(row_date)
    if row is None:
        return None
    return row.get("close")


def _defense_reference(candidate: Mapping[str, Any]) -> dict[str, Any]:
    indicators = _mapping(_mapping(candidate.get("input_snapshot")).get("indicators"))
    for source in ("support_level", "ma20", "ma60"):
        value = _float_or_none(indicators.get(source))
        if value is not None and value > 0:
            return {"source": source, "value": _round(value)}
    return {"source": None, "value": None}


def _profit_factor_like_ratio(positives: Sequence[float], negatives: Sequence[float]) -> float | None:
    if not positives and not negatives:
        return None
    downside = abs(sum(negatives))
    if downside == 0:
        return None if not positives else round(float(sum(positives)), 4)
    return _round(float(sum(positives)) / downside)


def _average(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return _round(sum(values) / len(values))


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return _round(numerator / denominator)


def _ordered_positive_values(values: Sequence[int]) -> list[int]:
    return sorted({int(value) for value in values if int(value) > 0})


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _float(value: Any) -> float:
    parsed = _float_or_none(value)
    return parsed if parsed is not None else 0.0


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    return int(value)


def _round(value: float) -> float:
    return round(float(value), 4)


DAILY_RADAR_FORWARD_ADAPTER = ForwardValidationAdapter(
    candidate_snapshot=_candidate_dimensions,
    entry_price=_entry_price,
    defense_reference=_defense_reference,
    freshness_status=_data_freshness_status,
)


__all__ = [
    "DAILY_RADAR_FORWARD_ADAPTER",
    "DEFAULT_BENCHMARK_SYMBOL",
    "DEFAULT_FORWARD_WINDOWS",
    "FORWARD_VALIDATION_REPORT_VERSION",
    "FORWARD_VALIDATION_VERSION",
    "ForwardValidationEvaluation",
    "build_forward_validation_report",
    "build_forward_validation_report_from_outcomes",
    "candidate_forward_validation_benchmark_symbol",
    "default_due_start_date",
    "due_windows_by_candidate",
    "exclude_persisted_daily_radar_windows",
    "evaluate_forward_window",
    "forward_validation_candidates_from_runs",
    "forward_validation_fixture_inputs",
    "load_forward_prices_from_fixture",
    "load_benchmark_prices_from_prepared_market_context",
    "load_price_series_from_raw_data",
    "persisted_forward_validation_outcomes",
    "benchmark_requires_forward_price_refresh",
    "merge_price_series",
    "symbols_requiring_forward_price_refresh",
    "upsert_forward_validation_results",
    "validate_forward_validation_benchmark",
    "write_report",
]
