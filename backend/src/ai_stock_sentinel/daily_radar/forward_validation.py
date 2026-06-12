from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.calibration import calibration_candidates_from_fixture
from ai_stock_sentinel.daily_radar.repository import PUBLIC_RUN_STATUSES
from ai_stock_sentinel.daily_radar.scoring import RULE_VERSION, SCORING_VERSION
from ai_stock_sentinel.db.models import (
    DailyRadarCandidate,
    DailyRadarForwardValidationResult,
    DailyRadarRun,
    StockRawData,
)


FORWARD_VALIDATION_VERSION = "daily-radar-forward-validation-v1"
FORWARD_VALIDATION_REPORT_VERSION = "daily-radar-forward-validation-report-v1"
DEFAULT_FORWARD_WINDOWS = (5, 10, 20)
DEFAULT_BENCHMARK_SYMBOL = "TAIEX"
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
) -> ForwardValidationEvaluation:
    active_windows = _ordered_positive_values(windows)
    candidate_list = [dict(candidate) for candidate in candidates]
    outcomes: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()

    for candidate in candidate_list:
        candidate_windows = active_windows
        if windows_by_candidate is not None:
            candidate_windows = _ordered_positive_values(windows_by_candidate.get(_candidate_key(candidate), []))
        for window_days in candidate_windows:
            outcome = evaluate_forward_window(
                candidate,
                price_series=price_series_by_symbol.get(str(candidate.get("symbol"))) or [],
                benchmark_prices=benchmark_prices,
                window_days=window_days,
                as_of_date=as_of_date,
                benchmark_symbol=benchmark_symbol,
                validation_version=validation_version,
                hit_threshold_pct=hit_threshold_pct,
            )
            outcomes.append(outcome)
            if outcome["status"] == "skipped":
                skipped[str(outcome["skip_reason"])] += 1

    valid_outcomes = [outcome for outcome in outcomes if outcome["status"] == "validated"]
    report = {
        "metadata": {
            "report_version": FORWARD_VALIDATION_REPORT_VERSION,
            "validation_version": validation_version,
            "market": market,
            "sample_source": sample_source,
            "as_of_date": as_of_date.isoformat() if as_of_date is not None else None,
            "windows": active_windows,
            "benchmark_symbol": benchmark_symbol,
            "hit_threshold_pct": hit_threshold_pct,
            "positioning": "rule_quality_calibration_diagnostic_not_performance_marketing",
        },
        "sample_summary": _sample_summary(candidate_list, outcomes, active_windows),
        "bucket_outcomes": _grouped_outcomes(valid_outcomes, _primary_bucket),
        "secondary_bucket_outcomes": _grouped_outcomes(valid_outcomes, _secondary_buckets),
        "rule_outcomes": _grouped_outcomes(valid_outcomes, _matched_rule_codes),
        "risk_label_outcomes": _grouped_outcomes(valid_outcomes, _risk_labels),
        "market_regime_outcomes": _grouped_outcomes(valid_outcomes, _market_regime),
        "relative_strength_bucket_outcomes": _grouped_outcomes(valid_outcomes, _relative_strength_bucket),
        "repeat_status_outcomes": _grouped_outcomes(valid_outcomes, _repeat_status),
        "score_decile_outcomes": _grouped_outcomes(valid_outcomes, _score_decile),
        "data_freshness_outcomes": _grouped_outcomes(valid_outcomes, _data_freshness_status_from_outcome),
        "ablation_candidates": _ablation_candidates(valid_outcomes),
        "skip_reasons": dict(sorted(skipped.items())),
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
    return ForwardValidationEvaluation(report=report, outcomes=outcomes)


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
    signal_date = _parse_date(candidate.get("record_date"))
    symbol = str(candidate.get("symbol") or "")
    base = {
        "candidate_id": candidate.get("candidate_id"),
        "symbol": symbol,
        "signal_date": signal_date.isoformat() if signal_date is not None else None,
        "window_days": int(window_days),
        "validation_version": validation_version,
        "benchmark_symbol": benchmark_symbol,
        "candidate_snapshot": _candidate_dimensions(candidate),
    }
    if signal_date is None:
        return _skip(base, "signal_date_missing")
    if as_of_date is not None and signal_date > as_of_date:
        return _skip(base, "future_signal_date")
    if _data_freshness_status(candidate) == "stale":
        return _skip(base, "stale_candidate_price")

    candidate_prices = _price_by_date(price_series)
    benchmark_by_date = _price_by_date(benchmark_prices)
    entry_price = _entry_price(candidate, candidate_prices, signal_date)
    if entry_price is None:
        return _skip(base, "missing_candidate_entry_price")

    future_rows = _future_rows(candidate_prices, signal_date, as_of_date)
    if len(future_rows) < window_days:
        return _skip(base, "missing_future_price")
    window_rows = future_rows[:window_days]
    target_date, target_price = window_rows[-1][0], window_rows[-1][1]["close"]

    benchmark_entry = _close_on(benchmark_by_date, signal_date)
    benchmark_future_rows = _future_rows(benchmark_by_date, signal_date, as_of_date)
    if benchmark_entry is None or len(benchmark_future_rows) < window_days:
        return _skip(base, "missing_benchmark")
    benchmark_target = benchmark_future_rows[window_days - 1][1]["close"]

    if entry_price <= 0 or target_price <= 0 or benchmark_entry <= 0 or benchmark_target <= 0:
        return _skip(base, "invalid_price")

    highs = [row["high"] for _row_date, row in window_rows]
    lows = [row["low"] for _row_date, row in window_rows]
    forward_return_pct = _pct_return(entry_price, target_price)
    benchmark_return_pct = _pct_return(benchmark_entry, benchmark_target)
    defense_reference = _defense_reference(candidate)
    outcome = {
        "forward_return_pct": forward_return_pct,
        "benchmark_return_pct": benchmark_return_pct,
        "excess_return_vs_benchmark_pct": _round(forward_return_pct - benchmark_return_pct),
        "max_favorable_excursion_pct": _pct_return(entry_price, max(highs)),
        "max_adverse_excursion_pct": _pct_return(entry_price, min(lows)),
        "close_below_defense_reference": (
            target_price < defense_reference["value"] if defense_reference["value"] is not None else None
        ),
        "defense_reference": defense_reference,
        "hit_above_threshold": forward_return_pct > hit_threshold_pct,
        "entry_price": _round(entry_price),
        "target_price": _round(target_price),
        "target_date": target_date.isoformat(),
    }
    return base | {
        "status": "validated",
        "target_date": target_date.isoformat(),
        "skip_reason": None,
        "outcome": outcome,
    }


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
            DailyRadarCandidate.observation_score.desc(),
            DailyRadarCandidate.symbol.asc(),
        )
    ).all()
    return [_candidate_snapshot(candidate, run) for candidate, run in rows]


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
    ordered_symbols = sorted({str(symbol) for symbol in symbols if str(symbol)})
    if not ordered_symbols:
        return {}
    rows = session.scalars(
        select(StockRawData)
        .where(
            StockRawData.symbol.in_(ordered_symbols),
            StockRawData.record_date >= start_date,
            StockRawData.record_date <= end_date,
            StockRawData.raw_data_is_final.is_(True),
        )
        .order_by(StockRawData.symbol.asc(), StockRawData.record_date.asc())
    ).all()
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        parsed = _price_row_from_raw_data(row)
        if parsed is not None:
            by_symbol[row.symbol].append(parsed)
    return dict(by_symbol)


def upsert_forward_validation_results(
    session: Session,
    outcomes: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    written = 0
    validated = 0
    skipped = 0
    for outcome in outcomes:
        candidate_id = outcome.get("candidate_id")
        if candidate_id is None:
            continue
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
                signal_date=_parse_date(outcome.get("signal_date")) or date.min,
                target_date=_parse_date(outcome.get("target_date")),
                benchmark_symbol=str(outcome.get("benchmark_symbol") or ""),
                outcome=payload,
                skip_reason=outcome.get("skip_reason"),
            )
        else:
            existing.status = str(outcome["status"])
            existing.signal_date = _parse_date(outcome.get("signal_date")) or date.min
            existing.target_date = _parse_date(outcome.get("target_date"))
            existing.benchmark_symbol = str(outcome.get("benchmark_symbol") or "")
            existing.outcome = payload
            existing.skip_reason = outcome.get("skip_reason")
        session.add(existing)
        written += 1
        if outcome.get("status") == "validated":
            validated += 1
        else:
            skipped += 1
    session.flush()
    return {"records_written": written, "validated_count": validated, "skipped_count": skipped}


def default_due_start_date(as_of_date: date, max_window: int = max(DEFAULT_FORWARD_WINDOWS)) -> date:
    return as_of_date - timedelta(days=max_window * 3)


def due_windows_by_candidate(
    candidates: Iterable[Mapping[str, Any]],
    *,
    as_of_date: date,
    windows: Sequence[int],
) -> dict[str, list[int]]:
    active_windows = _ordered_positive_values(windows)
    due_by_candidate: dict[str, list[int]] = {}
    for candidate in candidates:
        signal_date = _parse_date(candidate.get("record_date"))
        key = _candidate_key(candidate)
        if signal_date is None or signal_date > as_of_date:
            due_by_candidate[key] = active_windows
            continue
        due_windows = [window for window in active_windows if _business_days_after(signal_date, as_of_date) >= window]
        if due_windows:
            due_by_candidate[key] = due_windows
    return due_by_candidate


def write_report(report: Mapping[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _candidate_key(candidate: Mapping[str, Any]) -> str:
    candidate_id = candidate.get("candidate_id")
    if candidate_id is not None:
        return f"id:{candidate_id}"
    return f"{candidate.get('symbol') or ''}:{candidate.get('record_date') or ''}"


def _business_days_after(start_date: date, end_date: date) -> int:
    if end_date <= start_date:
        return 0
    count = 0
    current = start_date + timedelta(days=1)
    while current <= end_date:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


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
    }


def _price_row_from_raw_data(row: StockRawData) -> dict[str, Any] | None:
    technical = _mapping(row.technical)
    ohlcv = _mapping(technical.get("ohlcv") or technical)
    close = _float_or_none(ohlcv.get("close"))
    if close is None or close <= 0:
        return None
    return {
        "date": row.record_date.isoformat(),
        "open": _float_or_default(ohlcv.get("open"), close),
        "high": _float_or_default(ohlcv.get("high"), close),
        "low": _float_or_default(ohlcv.get("low"), close),
        "close": close,
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


def _future_rows(
    prices: Mapping[date, Mapping[str, float]],
    signal_date: date,
    as_of_date: date | None,
) -> list[tuple[date, Mapping[str, float]]]:
    return [
        (row_date, row)
        for row_date, row in sorted(prices.items())
        if row_date > signal_date and (as_of_date is None or row_date <= as_of_date)
    ]


def _price_by_date(price_series: Sequence[Mapping[str, Any]]) -> dict[date, dict[str, float]]:
    prices: dict[date, dict[str, float]] = {}
    for row in price_series:
        row_date = _parse_date(row.get("date"))
        close = _float_or_none(row.get("close"))
        if row_date is None or close is None or close <= 0:
            continue
        prices[row_date] = {
            "open": _float_or_default(row.get("open"), close),
            "high": _float_or_default(row.get("high"), close),
            "low": _float_or_default(row.get("low"), close),
            "close": close,
        }
    return prices


def _defense_reference(candidate: Mapping[str, Any]) -> dict[str, Any]:
    indicators = _mapping(_mapping(candidate.get("input_snapshot")).get("indicators"))
    for source in ("support_level", "ma20", "ma60"):
        value = _float_or_none(indicators.get(source))
        if value is not None and value > 0:
            return {"source": source, "value": _round(value)}
    return {"source": None, "value": None}


def _skip(base: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return dict(base) | {
        "status": "skipped",
        "target_date": None,
        "skip_reason": reason,
        "outcome": {},
    }


def _pct_return(start: float, end: float) -> float:
    return _round(((end / start) - 1) * 100)


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


def _float_or_default(value: Any, default: float) -> float:
    parsed = _float_or_none(value)
    return parsed if parsed is not None else default


def _int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    return int(value)


def _round(value: float) -> float:
    return round(float(value), 4)


__all__ = [
    "DEFAULT_BENCHMARK_SYMBOL",
    "DEFAULT_FORWARD_WINDOWS",
    "FORWARD_VALIDATION_REPORT_VERSION",
    "FORWARD_VALIDATION_VERSION",
    "ForwardValidationEvaluation",
    "build_forward_validation_report",
    "default_due_start_date",
    "due_windows_by_candidate",
    "evaluate_forward_window",
    "forward_validation_candidates_from_runs",
    "forward_validation_fixture_inputs",
    "load_forward_prices_from_fixture",
    "load_price_series_from_raw_data",
    "upsert_forward_validation_results",
    "write_report",
]
