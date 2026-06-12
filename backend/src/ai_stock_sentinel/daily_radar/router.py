from __future__ import annotations

import logging
from contextlib import suppress
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_universe_provider import TwseRwdInstitutionalUniverseProvider
from ai_stock_sentinel.daily_radar.market_context import (
    MarketIndexContextProvider,
    YFinanceMarketIndexContextProvider,
)
from ai_stock_sentinel.daily_radar.raw_data import (
    BatchTechnicalFetcher,
    YFinanceBatchTechnicalFetcher,
    ensure_daily_radar_raw_rows,
)
from ai_stock_sentinel.daily_radar.auth import require_daily_radar_internal_auth
from ai_stock_sentinel.daily_radar.background_context import (
    BackgroundChipContextProvider,
    update_background_chip_context_cache,
)
from ai_stock_sentinel.daily_radar.default_background_context import DefaultBackgroundChipContextProvider
from ai_stock_sentinel.daily_radar.forward_validation import (
    DEFAULT_BENCHMARK_SYMBOL,
    DEFAULT_FORWARD_WINDOWS,
    build_forward_validation_report,
    default_due_start_date,
    due_windows_by_candidate,
    forward_validation_candidates_from_runs,
    load_price_series_from_raw_data,
    upsert_forward_validation_results,
)
from ai_stock_sentinel.daily_radar.rule_governance import (
    DEFAULT_MIN_SAMPLE_COUNT,
    build_monthly_rule_review_report,
)
from ai_stock_sentinel.daily_radar.repository import (
    BACKGROUND_CONTEXT_TYPES,
    get_daily_radar_run_by_date,
    get_final_raw_data_rows_for_date,
    get_final_raw_data_rows_for_symbols,
    get_latest_daily_radar_run,
    get_shared_background_context_trace_by_symbol,
    get_symbol_candidate_history,
)
from ai_stock_sentinel.daily_radar.schemas import (
    DailyRadarCandidateResponse,
    DailyRadarRunResponse,
)
from ai_stock_sentinel.daily_radar.service import run_daily_radar
from ai_stock_sentinel.daily_radar.universe import (
    DailyRadarUniverseEntry,
    DailyRadarUniverseProvider,
    select_daily_radar_universe,
)
from ai_stock_sentinel.db.models import DailyRadarCandidate, DailyRadarRun
from ai_stock_sentinel.db.session import get_db


router = APIRouter(tags=["daily-radar"])
logger = logging.getLogger(__name__)


class DailyRadarRunRequest(BaseModel):
    run_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)


class DailyRadarRunTriggerResponse(BaseModel):
    run_id: int
    run_date: date
    market: str
    status: Literal["completed", "running", "failed", "stale_data"]
    universe_count: int
    prefilter_count: int
    candidate_count: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None


class DailyRadarChipContextUpdateRequest(BaseModel):
    run_date: date | None = None
    market: str = Field(default="TW", min_length=1, max_length=20)
    symbols: list[str] | None = None
    context_types: list[str] = Field(default_factory=lambda: list(BACKGROUND_CONTEXT_TYPES))


class DailyRadarChipContextUpdateResponse(BaseModel):
    status: Literal["completed", "failed"]
    run_date: date
    market: str
    symbol_count: int
    context_types: list[str]
    records_written: int
    errors: list[dict[str, Any]] = Field(default_factory=list)


class DailyRadarForwardValidationRunRequest(BaseModel):
    mode: Literal["due", "range"] = "due"
    market: str = Field(default="TW", min_length=1, max_length=20)
    as_of_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    windows: list[int] = Field(default_factory=lambda: list(DEFAULT_FORWARD_WINDOWS))
    benchmark_symbol: str = Field(default=DEFAULT_BENCHMARK_SYMBOL, min_length=1, max_length=40)


class DailyRadarForwardValidationRunResponse(BaseModel):
    status: Literal["completed"]
    mode: Literal["due", "range"]
    market: str
    as_of_date: date
    candidate_count: int
    records_written: int
    validated_count: int
    skipped_count: int
    report: dict[str, Any]


class DailyRadarMonthlyRuleReviewRequest(BaseModel):
    market: str = Field(default="TW", min_length=1, max_length=20)
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    validation_version: str | None = Field(default=None, min_length=1, max_length=80)
    min_sample_count: int = Field(default=DEFAULT_MIN_SAMPLE_COUNT, ge=1, le=10_000)


class DailyRadarMonthlyRuleReviewResponse(BaseModel):
    status: Literal["completed"]
    market: str
    month: str
    report_json: dict[str, Any]
    report_markdown: str


def get_daily_radar_universe_provider() -> DailyRadarUniverseProvider:
    return TwseRwdInstitutionalUniverseProvider()


def get_daily_radar_technical_fetcher() -> BatchTechnicalFetcher:
    return YFinanceBatchTechnicalFetcher()


def get_daily_radar_market_context_provider() -> MarketIndexContextProvider:
    return YFinanceMarketIndexContextProvider()


def get_daily_radar_background_chip_context_provider() -> BackgroundChipContextProvider:
    return DefaultBackgroundChipContextProvider()


@router.post(
    "/internal/daily-radar/run",
    response_model=DailyRadarRunTriggerResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def run_daily_radar_endpoint(
    payload: DailyRadarRunRequest | None = None,
    db: Session = Depends(get_db),
    universe_provider: DailyRadarUniverseProvider = Depends(get_daily_radar_universe_provider),
    technical_fetcher: BatchTechnicalFetcher = Depends(get_daily_radar_technical_fetcher),
    market_context_provider: MarketIndexContextProvider = Depends(get_daily_radar_market_context_provider),
) -> DailyRadarRunTriggerResponse:
    failure_stage = "request_initialization"
    try:
        request = payload or DailyRadarRunRequest()
        run_date = request.run_date or _backend_today()
        market = request.market
        failure_stage = "universe_selection"
        existing_technical_rows = get_final_raw_data_rows_for_date(db, run_date=run_date)
        universe = select_daily_radar_universe(
            universe_provider,
            run_date=run_date,
            market=market,
            track_limit=50,
            technical_records=existing_technical_rows,
        )
        if not universe:
            raise HTTPException(
                status_code=409,
                detail=f"Daily Radar universe is empty for {market} on {run_date.isoformat()}.",
            )

        selected_symbols = [entry.symbol for entry in universe]
        background_contexts_by_symbol = get_shared_background_context_trace_by_symbol(
            db,
            symbols=selected_symbols,
            context_types=BACKGROUND_CONTEXT_TYPES,
            reference_date=run_date,
            point_in_time=True,
        )
        institutional_payloads_by_symbol = _institutional_payloads_by_symbol(universe, run_date=run_date)
        failure_stage = "raw_data_backfill"
        cache_rows = ensure_daily_radar_raw_rows(
            db,
            run_date,
            selected_symbols,
            technical_fetcher=technical_fetcher,
            institutional_payloads_by_symbol=institutional_payloads_by_symbol,
        )
        if not cache_rows:
            cache_rows = get_final_raw_data_rows_for_symbols(db, run_date=run_date, symbols=selected_symbols)
        if not cache_rows:
            raise HTTPException(
                status_code=409,
                detail=f"No final StockRawData rows are available for selected Daily Radar symbols on {run_date.isoformat()}.",
            )
        failure_stage = "market_context"
        market_context = dict(market_context_provider.build(run_date=run_date, market=market))
        failure_stage = "daily_radar_service"
        run = run_daily_radar(
            run_date,
            market,
            session=db,
            cache_rows=cache_rows,
            market_context=market_context,
            background_contexts_by_symbol=background_contexts_by_symbol,
            allow_fixture_fallback=False,
        )
        db.commit()
        return _run_response(run)
    except HTTPException:
        raise
    except Exception as exc:
        with suppress(Exception):
            db.rollback()
        logger.exception("Daily Radar run failed before completion")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "daily_radar_run_failed",
                "message": "Daily Radar run failed before completion. Check backend logs for the root cause.",
                "stage": failure_stage,
                "error_type": exc.__class__.__name__,
            },
        ) from exc


@router.post(
    "/internal/daily-radar/chip-context/update",
    response_model=DailyRadarChipContextUpdateResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def update_daily_radar_chip_context_endpoint(
    payload: DailyRadarChipContextUpdateRequest | None = None,
    db: Session = Depends(get_db),
    provider: BackgroundChipContextProvider = Depends(get_daily_radar_background_chip_context_provider),
) -> DailyRadarChipContextUpdateResponse:
    request = payload or DailyRadarChipContextUpdateRequest()
    run_date = request.run_date or _backend_today()
    logger.info(
        "[DailyRadarChipContext] update started run_date=%s market=%s context_types=%s requested_symbol_count=%s provider=%s",
        run_date.isoformat(),
        request.market,
        list(request.context_types or BACKGROUND_CONTEXT_TYPES),
        len(request.symbols) if request.symbols is not None else "latest_run",
        provider.__class__.__name__,
    )
    result = update_background_chip_context_cache(
        db,
        run_date=run_date,
        market=request.market,
        provider=provider,
        symbols=request.symbols,
        context_types=request.context_types,
    )
    db.commit()
    logger.info(
        "[DailyRadarChipContext] update completed status=%s run_date=%s market=%s symbol_count=%s context_types=%s records_written=%s errors_count=%s",
        result["status"],
        run_date.isoformat(),
        result["market"],
        result["symbol_count"],
        list(result["context_types"]),
        result["records_written"],
        len(result["errors"]),
    )
    return DailyRadarChipContextUpdateResponse(
        status=result["status"],
        run_date=run_date,
        market=str(result["market"]),
        symbol_count=int(result["symbol_count"]),
        context_types=list(result["context_types"]),
        records_written=int(result["records_written"]),
        errors=list(result["errors"]),
    )


@router.post(
    "/internal/daily-radar/forward-validation/run",
    response_model=DailyRadarForwardValidationRunResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def run_daily_radar_forward_validation_endpoint(
    payload: DailyRadarForwardValidationRunRequest | None = None,
    db: Session = Depends(get_db),
) -> DailyRadarForwardValidationRunResponse:
    request = payload or DailyRadarForwardValidationRunRequest()
    as_of_date = request.as_of_date or _backend_today()
    start_date = request.start_date
    if request.mode == "due" and start_date is None:
        start_date = default_due_start_date(as_of_date, max(request.windows or list(DEFAULT_FORWARD_WINDOWS)))
    candidates = forward_validation_candidates_from_runs(
        db,
        market=request.market,
        start_date=start_date,
        end_date=request.end_date or as_of_date,
    )
    symbols = {str(candidate["symbol"]) for candidate in candidates}
    price_start_date = min(
        [_parse_date(candidate.get("record_date")) for candidate in candidates if _parse_date(candidate.get("record_date")) is not None],
        default=start_date or as_of_date,
    )
    price_series = load_price_series_from_raw_data(
        db,
        symbols=sorted(symbols | {request.benchmark_symbol}),
        start_date=price_start_date,
        end_date=as_of_date,
    )
    windows_by_candidate = None
    if request.mode == "due":
        windows_by_candidate = due_windows_by_candidate(
            candidates,
            as_of_date=as_of_date,
            windows=request.windows,
            price_series_by_symbol={symbol: price_series.get(symbol, []) for symbol in symbols},
            benchmark_prices=price_series.get(request.benchmark_symbol, []),
        )
    evaluation = build_forward_validation_report(
        candidates,
        price_series_by_symbol={symbol: price_series.get(symbol, []) for symbol in symbols},
        benchmark_prices=price_series.get(request.benchmark_symbol, []),
        market=request.market,
        sample_source="production_db",
        as_of_date=as_of_date,
        windows=request.windows,
        benchmark_symbol=request.benchmark_symbol,
        windows_by_candidate=windows_by_candidate,
    )
    write_summary = upsert_forward_validation_results(db, evaluation.outcomes)
    db.commit()
    return DailyRadarForwardValidationRunResponse(
        status="completed",
        mode=request.mode,
        market=request.market,
        as_of_date=as_of_date,
        candidate_count=len(candidates),
        records_written=write_summary["records_written"],
        validated_count=write_summary["validated_count"],
        skipped_count=write_summary["skipped_count"],
        report=evaluation.report,
    )


@router.post(
    "/internal/daily-radar/rule-review/monthly",
    response_model=DailyRadarMonthlyRuleReviewResponse,
    dependencies=[Depends(require_daily_radar_internal_auth)],
)
def run_daily_radar_monthly_rule_review_endpoint(
    payload: DailyRadarMonthlyRuleReviewRequest,
    db: Session = Depends(get_db),
) -> DailyRadarMonthlyRuleReviewResponse:
    report = build_monthly_rule_review_report(
        db,
        market=payload.market,
        year=payload.year,
        month=payload.month,
        validation_version=payload.validation_version,
        min_sample_count=payload.min_sample_count,
    )
    return DailyRadarMonthlyRuleReviewResponse(
        status="completed",
        market=payload.market,
        month=f"{payload.year:04d}-{payload.month:02d}",
        report_json=report.json_report,
        report_markdown=report.markdown_report,
    )


@router.get("/daily-radar/latest", response_model=DailyRadarRunResponse, response_model_exclude_none=True)
def get_latest_daily_radar_endpoint(
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> DailyRadarRunResponse:
    run = get_latest_daily_radar_run(db, market=market)
    if run is None:
        raise HTTPException(status_code=404, detail="No public Daily Radar run is available.")
    return _public_run_response(run, bucket=bucket, limit=limit)


@router.get("/daily-radar/symbol/{symbol}", response_model=list[dict[str, Any]])
def get_daily_radar_symbol_history_endpoint(
    symbol: str,
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    lookback_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    history = get_symbol_candidate_history(
        db,
        symbols=[symbol],
        before_date=_backend_today() + timedelta(days=1),
        lookback_days=lookback_days,
        market=market,
    )
    filtered = [_history_response(item) for item in history if _matches_bucket(item, bucket)]
    return filtered[:limit]


@router.get("/daily-radar/{run_date}", response_model=DailyRadarRunResponse, response_model_exclude_none=True)
def get_daily_radar_by_date_endpoint(
    run_date: date,
    market: str = Query(default="TW", min_length=1, max_length=20),
    bucket: str | None = Query(default=None, min_length=1, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> DailyRadarRunResponse:
    run = get_daily_radar_run_by_date(db, run_date=run_date, market=market)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"No public Daily Radar run is available for {run_date.isoformat()}.",
        )
    return _public_run_response(run, bucket=bucket, limit=limit)


def _backend_today() -> date:
    return date.today()


def _institutional_payloads_by_symbol(
    universe: list[DailyRadarUniverseEntry],
    *,
    run_date: date,
) -> dict[str, dict[str, Any]]:
    return {entry.symbol: _institutional_payload(entry, run_date=run_date) for entry in universe}


def _institutional_payload(entry: DailyRadarUniverseEntry, *, run_date: date) -> dict[str, Any]:
    same_day_metrics = dict(entry.track_metrics.get("same_day_institutional") or {})
    recent_metrics = dict(entry.track_metrics.get("recent_accumulation") or {})
    flat_payload: dict[str, Any] = {
        "flow_label": "institutional_accumulation",
        "flow_state": _flow_state(entry),
        "universe_primary_track": entry.primary_track,
        "institutional_universe_tracks": list(entry.tracks),
        "universe_track_metrics": {track: dict(metrics) for track, metrics in entry.track_metrics.items()},
        "same_day_rank": entry.same_day_rank,
        "recent_accumulation_rank": entry.recent_accumulation_rank,
        "scores": _score_payload(entry),
        "source_provider": "daily_radar_universe",
        "data_dates": {"institutional_flow": _latest_institutional_date(entry, run_date=run_date)},
    }
    _merge_same_day_metrics(flat_payload, same_day_metrics)
    _merge_recent_metrics(flat_payload, recent_metrics)
    return flat_payload | {"institutional_flow": dict(flat_payload)}


def _flow_state(entry: DailyRadarUniverseEntry) -> str:
    recent_metrics = entry.track_metrics.get("recent_accumulation") or {}
    recent_flow_state = recent_metrics.get("flow_state")
    if recent_flow_state is not None:
        return str(recent_flow_state)
    recent_positive_days = _int_metric(recent_metrics.get("consecutive_buy_days"))
    if recent_positive_days is not None:
        return "consistent_accumulation" if recent_positive_days >= 2 else "weak_confirmation"

    same_day_metrics = entry.track_metrics.get("same_day_institutional") or {}
    same_day_flow_state = same_day_metrics.get("flow_state")
    if same_day_flow_state is not None:
        return str(same_day_flow_state)
    if not any(track in {"same_day_institutional", "recent_accumulation"} for track in entry.tracks):
        return "technical_trigger"
    return "weak_confirmation"


def _score_payload(entry: DailyRadarUniverseEntry) -> dict[str, float]:
    scores: dict[str, float] = {}
    _add_score(scores, "same_day_institutional", entry.same_day_score)
    _add_score(scores, "recent_accumulation", entry.recent_accumulation_score)
    for track, metrics in entry.track_metrics.items():
        _add_score(scores, track, metrics.get("score"))
    return scores


def _add_score(scores: dict[str, float], key: str, value: float | None) -> None:
    if value is not None:
        scores[key] = float(value)


def _merge_same_day_metrics(payload: dict[str, Any], metrics: dict[str, Any]) -> None:
    same_day_actor = _normalized_actor(metrics.get("actor"))
    raw_same_day_net_buy = metrics.get("net_buy")
    same_day_net_buy = _float_metric(raw_same_day_net_buy)
    _add_payload_metric(payload, "same_day_actor", metrics.get("actor"))
    _add_payload_metric(payload, "same_day_net_buy", raw_same_day_net_buy)
    _add_payload_metric(payload, "same_day_concentration", metrics.get("concentration"))
    _add_payload_metric(payload, "same_day_source_dates", _source_dates(metrics))
    if same_day_net_buy is not None:
        if same_day_actor == "foreign":
            payload["foreign_net_shares"] = same_day_net_buy
        elif same_day_actor == "trust":
            payload["investment_trust_net_shares"] = same_day_net_buy


def _merge_recent_metrics(payload: dict[str, Any], metrics: dict[str, Any]) -> None:
    recent_actor = _normalized_actor(metrics.get("actor"))
    positive_days = _int_metric(metrics.get("consecutive_buy_days"))
    if positive_days is not None:
        payload["consecutive_buy_days"] = positive_days
        payload["consecutive_positive_days"] = positive_days

    cumulative_net_buy = _float_metric(metrics.get("cumulative_net_buy"))
    if cumulative_net_buy is not None:
        payload["cumulative_net_buy"] = cumulative_net_buy
        payload["net_buy_cumulative"] = cumulative_net_buy
        payload["three_party_net_shares"] = cumulative_net_buy
        if recent_actor == "foreign":
            payload["foreign_net_shares"] = cumulative_net_buy
        elif recent_actor == "trust":
            payload["investment_trust_net_shares"] = cumulative_net_buy

    _add_payload_metric(payload, "recent_actor", metrics.get("actor"))
    raw_recent_concentration = metrics.get("concentration")
    recent_concentration = _float_metric(raw_recent_concentration)
    _add_payload_metric(payload, "recent_concentration", raw_recent_concentration)
    _add_payload_metric(payload, "net_flow_to_avg_volume", recent_concentration)
    _add_payload_metric(payload, "recent_source_dates", _source_dates(metrics))


def _normalized_actor(value: Any) -> str | None:
    if value is None:
        return None
    actor = str(value).strip().lower()
    if actor in {"foreign", "trust", "institutional", "mixed"}:
        return actor
    return None


def _add_payload_metric(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


def _latest_institutional_date(entry: DailyRadarUniverseEntry, *, run_date: date) -> str:
    source_dates: list[str] = []
    for metrics in entry.track_metrics.values():
        source_dates.extend(_source_dates(metrics) or [])
    return max(source_dates) if source_dates else run_date.isoformat()


def _source_dates(metrics: dict[str, Any]) -> list[str] | None:
    raw_dates = metrics.get("source_dates")
    if not isinstance(raw_dates, (list, tuple)):
        return None
    source_dates = [str(value) for value in raw_dates if value]
    return source_dates or None


def _int_metric(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_metric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _run_response(run: DailyRadarRun) -> DailyRadarRunTriggerResponse:
    return DailyRadarRunTriggerResponse(
        run_id=run.id,
        run_date=run.run_date,
        market=run.market,
        status=run.status,
        universe_count=run.universe_count,
        prefilter_count=run.prefilter_count,
        candidate_count=run.candidate_count,
        errors=list(run.errors or []),
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _public_run_response(
    run: DailyRadarRun,
    *,
    bucket: str | None,
    limit: int,
) -> DailyRadarRunResponse:
    candidates = [_candidate_response(candidate) for candidate in _ordered_candidates(run.candidates)]
    if bucket is not None:
        candidates = [candidate for candidate in candidates if _matches_bucket(candidate.model_dump(), bucket)]
    candidates = candidates[:limit]
    return DailyRadarRunResponse(
        run_date=run.run_date,
        status=run.status,
        data_dates=_run_data_dates(candidates),
        market_context=_run_market_context(candidates),
        candidates=candidates,
    )


def _ordered_candidates(candidates: list[DailyRadarCandidate]) -> list[DailyRadarCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (-candidate.observation_score, candidate.symbol),
    )


def _candidate_response(candidate: DailyRadarCandidate) -> DailyRadarCandidateResponse:
    return DailyRadarCandidateResponse(
        symbol=candidate.symbol,
        name=candidate.name,
        primary_bucket=candidate.primary_bucket,
        secondary_buckets=list(candidate.secondary_buckets or []),
        observation_score=candidate.observation_score,
        risk_labels=list(candidate.risk_labels or []),
        repeat_status=candidate.repeat_status,
        explanation=candidate.explanation,
        scoring_version=_trace_version(candidate.score_breakdown, "scoring_version"),
        rule_version=_trace_version(candidate.score_breakdown, "rule_version"),
        bucket_scores=dict(candidate.bucket_scores or {}),
        score_breakdown=dict(candidate.score_breakdown or {}),
        input_snapshot=dict(candidate.input_snapshot or {}),
        data_dates=_date_mapping(candidate.data_dates or {}),
        matched_rules=_matched_rules(candidate.matched_rules or []),
        background_context_labels=_background_context_labels(candidate.input_snapshot),
    )


def _history_response(item: dict[str, Any]) -> dict[str, Any]:
    response = {
        "symbol": item["symbol"],
        "name": item["name"],
        "record_date": item["record_date"],
        "primary_bucket": item["primary_bucket"],
        "secondary_buckets": list(item.get("secondary_buckets") or []),
        "observation_score": item["observation_score"],
        "risk_labels": list(item.get("risk_labels") or []),
        "repeat_status": item["repeat_status"],
        "bucket_scores": dict(item.get("bucket_scores") or {}),
        "matched_rules": _matched_rules(item.get("matched_rules") or []),
        "score_breakdown": dict(item.get("score_breakdown") or {}),
        "input_snapshot": dict(item.get("input_snapshot") or {}),
        "data_dates": {key: value.isoformat() for key, value in _date_mapping(item.get("data_dates") or {}).items()},
        "background_context_labels": _background_context_labels(item.get("input_snapshot")),
    }
    scoring_version = item.get("scoring_version") or _trace_version(item.get("score_breakdown"), "scoring_version")
    rule_version = item.get("rule_version") or _trace_version(item.get("score_breakdown"), "rule_version")
    if scoring_version is not None:
        response["scoring_version"] = scoring_version
    if rule_version is not None:
        response["rule_version"] = rule_version
    return response


def _matched_rules(raw_rules: list[Any]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for rule in raw_rules:
        if isinstance(rule, dict):
            rules.append(
                {
                    "rule_id": str(rule.get("rule_id", "unknown_rule")),
                    "label": str(rule.get("label", rule.get("rule_id", "unknown_rule"))),
                    "details": dict(rule.get("details") or {}),
                }
            )
        else:
            rules.append({"rule_id": str(rule), "label": str(rule), "details": {}})
    return rules


def _background_context_labels(input_snapshot: Any) -> list[dict[str, Any]]:
    if not isinstance(input_snapshot, dict):
        return []
    labels = input_snapshot.get("background_context_labels")
    if not isinstance(labels, list):
        return []
    return [dict(label) for label in labels if isinstance(label, dict)]


def _trace_version(payload: Any, key: str) -> str | None:
    if isinstance(payload, dict) and payload.get(key) is not None:
        return str(payload[key])
    return None


def _matches_bucket(item: dict[str, Any], bucket: str | None) -> bool:
    if bucket is None:
        return True
    return item.get("primary_bucket") == bucket or bucket in set(item.get("secondary_buckets") or [])


def _run_data_dates(candidates: list[DailyRadarCandidateResponse]) -> dict[str, date]:
    data_dates: dict[str, date] = {}
    for candidate in candidates:
        for key, value in candidate.data_dates.items():
            if key not in data_dates or value > data_dates[key]:
                data_dates[key] = value
    return data_dates


def _run_market_context(candidates: list[DailyRadarCandidateResponse]) -> dict[str, Any]:
    for candidate in candidates:
        market_context = candidate.input_snapshot.get("market_context")
        if isinstance(market_context, dict) and market_context:
            return dict(market_context)
    return {}


def _date_mapping(raw_dates: dict[str, Any]) -> dict[str, date]:
    data_dates: dict[str, date] = {}
    for key, value in raw_dates.items():
        parsed = _parse_date(value)
        if parsed is not None:
            data_dates[str(key)] = parsed
    return data_dates


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


__all__ = ["router"]
