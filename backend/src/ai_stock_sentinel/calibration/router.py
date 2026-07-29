from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_stock_sentinel.analysis.calibration import (
    ANALYSIS_FORWARD_VALIDATION_VERSION,
    GENERAL_ANALYSIS_FORWARD_ADAPTER,
    build_general_analysis_monthly_report,
    evaluate_general_analysis_forward_validation,
    general_validation_samples,
    upsert_general_analysis_validation_results,
)
from ai_stock_sentinel.calibration.price_provider import (
    ForwardPriceProvider,
    get_forward_price_provider,
)
from ai_stock_sentinel.calibration.auth import require_calibration_internal_auth
from ai_stock_sentinel.calibration.forward_validation import (
    DEFAULT_BENCHMARK_SYMBOL,
    DEFAULT_FORWARD_WINDOWS,
    TERMINAL_FORWARD_VALIDATION_SKIP_REASONS,
    default_due_start_date,
    discover_due_windows_by_candidate,
)
from ai_stock_sentinel.calibration.forward_validation_planning import (
    prepare_due_forward_validation,
)
from ai_stock_sentinel.calibration.governance import DEFAULT_MIN_REPLAY_COVERAGE
from ai_stock_sentinel.calibration.repository import (
    load_benchmark_prices_from_prepared_market_context,
    load_price_series_from_raw_data,
)
from ai_stock_sentinel.db.models import AnalysisForwardValidationResult
from ai_stock_sentinel.db.session import get_db


router = APIRouter(tags=["calibration"])


class GeneralAnalysisForwardValidationRequest(BaseModel):
    mode: Literal["due", "range"] = "due"
    market: str = Field(default="TW", min_length=1, max_length=20)
    as_of_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    windows: list[int] = Field(default_factory=lambda: list(DEFAULT_FORWARD_WINDOWS))
    benchmark_symbol: str = Field(default=DEFAULT_BENCHMARK_SYMBOL, min_length=1, max_length=40)


class GeneralAnalysisForwardValidationResponse(BaseModel):
    status: Literal["completed"]
    mode: Literal["due", "range"]
    market: str
    as_of_date: date
    sample_count: int
    records_written: int
    validated_count: int
    skipped_count: int
    retryable_skipped_count: int
    terminal_skipped_count: int
    report: dict[str, Any]


class GeneralAnalysisMonthlyReviewRequest(BaseModel):
    market: str = Field(default="TW", min_length=1, max_length=20)
    benchmark_symbol: str = Field(default=DEFAULT_BENCHMARK_SYMBOL, min_length=1, max_length=40)
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    min_sample_count: int = Field(default=20, ge=1, le=10_000)
    min_validated_coverage: float = Field(default=0.9, ge=0, le=1)
    min_replay_coverage: float = Field(
        default=DEFAULT_MIN_REPLAY_COVERAGE,
        ge=0,
        le=1,
    )


class GeneralAnalysisMonthlyReviewResponse(BaseModel):
    status: Literal["completed"]
    market: str
    through_month: str
    report_json: dict[str, Any]
    report_markdown: str


@router.post(
    "/internal/analysis-calibration/forward-validation/run",
    response_model=GeneralAnalysisForwardValidationResponse,
    dependencies=[Depends(require_calibration_internal_auth)],
)
def run_general_analysis_forward_validation(
    payload: GeneralAnalysisForwardValidationRequest | None = None,
    db: Session = Depends(get_db),
    price_provider: ForwardPriceProvider = Depends(get_forward_price_provider),
) -> GeneralAnalysisForwardValidationResponse:
    request = payload or GeneralAnalysisForwardValidationRequest()
    as_of_date = request.as_of_date or date.today()
    start_date = request.start_date
    if request.mode == "due" and start_date is None:
        start_date = default_due_start_date(
            as_of_date,
            max(request.windows or list(DEFAULT_FORWARD_WINDOWS)),
        )
    samples = general_validation_samples(
        db,
        start_date=start_date,
        end_date=request.end_date or as_of_date,
        market=request.market,
        benchmark_symbol=request.benchmark_symbol,
    )
    symbols = {str(sample["symbol"]) for sample in samples}
    signal_dates = [
        parsed
        for sample in samples
        if (parsed := _parse_date(sample.get("record_date"))) is not None
    ]
    price_start_date = min(signal_dates, default=start_date or as_of_date)
    price_series = load_price_series_from_raw_data(
        db,
        symbols=sorted(symbols | {request.benchmark_symbol}),
        start_date=price_start_date,
        end_date=as_of_date,
    )
    benchmark_prices = price_series.get(request.benchmark_symbol, [])
    if not benchmark_prices:
        benchmark_prices = load_benchmark_prices_from_prepared_market_context(
            db,
            market=request.market,
            benchmark_symbol=request.benchmark_symbol,
            as_of_date=as_of_date,
        )
    windows_by_sample = None
    if request.mode == "due":
        windows_by_sample = discover_due_windows_by_candidate(
            samples,
            adapter=GENERAL_ANALYSIS_FORWARD_ADAPTER,
            as_of_date=as_of_date,
            windows=request.windows,
            price_series_by_symbol={
                symbol: price_series.get(symbol, [])
                for symbol in symbols
            },
            benchmark_prices=benchmark_prices,
        )
        windows_by_sample = _exclude_persisted_general_analysis_windows(
            db,
            windows_by_sample,
        )
        preparation = prepare_due_forward_validation(
            samples,
            adapter=GENERAL_ANALYSIS_FORWARD_ADAPTER,
            pending_windows_by_candidate=windows_by_sample,
            price_series_by_symbol=price_series,
            benchmark_prices=benchmark_prices,
            benchmark_symbol=request.benchmark_symbol,
            as_of_date=as_of_date,
            price_start_date=price_start_date,
            fetch_prices=price_provider.fetch,
        )
        price_series = preparation.price_series_by_symbol
        benchmark_prices = preparation.benchmark_prices
        windows_by_sample = preparation.evaluation_windows_by_candidate
    report, outcomes = evaluate_general_analysis_forward_validation(
        samples,
        price_series_by_symbol={
            symbol: price_series.get(symbol, [])
            for symbol in symbols
        },
        benchmark_prices=benchmark_prices,
        as_of_date=as_of_date,
        market=request.market,
        windows=request.windows,
        benchmark_symbol=request.benchmark_symbol,
        due_only=request.mode == "due",
        windows_by_sample=windows_by_sample,
    )
    summary = upsert_general_analysis_validation_results(db, outcomes)
    db.commit()
    return GeneralAnalysisForwardValidationResponse(
        status="completed",
        mode=request.mode,
        market=request.market,
        as_of_date=as_of_date,
        sample_count=len(samples),
        records_written=summary["records_written"],
        validated_count=summary["validated_count"],
        skipped_count=summary["skipped_count"],
        retryable_skipped_count=summary["retryable_skipped_count"],
        terminal_skipped_count=summary["terminal_skipped_count"],
        report=report,
    )


@router.post(
    "/internal/analysis-calibration/monthly",
    response_model=GeneralAnalysisMonthlyReviewResponse,
    dependencies=[Depends(require_calibration_internal_auth)],
)
def run_general_analysis_monthly_review(
    payload: GeneralAnalysisMonthlyReviewRequest,
    db: Session = Depends(get_db),
) -> GeneralAnalysisMonthlyReviewResponse:
    report, markdown = build_general_analysis_monthly_report(
        db,
        through_year=payload.year,
        through_month=payload.month,
        min_sample_count=payload.min_sample_count,
        min_validated_coverage=payload.min_validated_coverage,
        min_replay_coverage=payload.min_replay_coverage,
        market=payload.market,
        benchmark_symbol=payload.benchmark_symbol,
    )
    return GeneralAnalysisMonthlyReviewResponse(
        status="completed",
        market=payload.market,
        through_month=f"{payload.year:04d}-{payload.month:02d}",
        report_json=report,
        report_markdown=markdown,
    )


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _exclude_persisted_general_analysis_windows(
    session: Session,
    windows_by_sample: dict[str, list[int]],
) -> dict[str, list[int]]:
    sample_ids = [
        int(key.removeprefix("id:"))
        for key in windows_by_sample
        if key.startswith("id:") and key.removeprefix("id:").isdigit()
    ]
    terminal = {
        (result.sample_id, result.window_days)
        for result in session.scalars(
            select(AnalysisForwardValidationResult).where(
                AnalysisForwardValidationResult.sample_id.in_(sample_ids),
                AnalysisForwardValidationResult.validation_version
                == ANALYSIS_FORWARD_VALIDATION_VERSION,
            )
        ).all()
        if result.status == "validated"
        or (
            result.status == "skipped"
            and result.skip_reason in TERMINAL_FORWARD_VALIDATION_SKIP_REASONS
        )
    } if sample_ids else set()
    pending: dict[str, list[int]] = {}
    for key, windows in windows_by_sample.items():
        sample_id = (
            int(key.removeprefix("id:"))
            if key.startswith("id:") and key.removeprefix("id:").isdigit()
            else None
        )
        remaining = [
            int(window)
            for window in windows
            if sample_id is None or (sample_id, int(window)) not in terminal
        ]
        if remaining:
            pending[key] = remaining
    return pending


__all__ = ["router"]
