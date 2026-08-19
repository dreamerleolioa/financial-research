from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
import re
from typing import Any
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.data_sources.fundamental.finmind_provider import FinMindFundamentalProvider
from ai_stock_sentinel.data_sources.fundamental.normalizers import (
    normalize_finmind_dividend_rows,
    normalize_finmind_statement_rows,
    normalize_official_statement_rows,
    normalize_tpex_ex_dividend_payload,
    normalize_twse_dividend_rows,
)
from ai_stock_sentinel.data_sources.fundamental.official_provider import (
    dividend_history_is_sufficient,
    fundamental_period_history_is_sufficient,
)
from ai_stock_sentinel.data_sources.fundamental.repository import (
    load_latest_dividend_events,
    load_latest_dividend_events_for_symbols,
    load_latest_fundamental_periods,
    load_latest_fundamental_periods_for_symbols,
    store_dividend_events,
    store_fundamental_periods,
)
from ai_stock_sentinel.data_sources.official_http import official_request_get
from ai_stock_sentinel.db.models import (
    DailyRadarPreparedRun,
    FundamentalBackfillJob,
    StockRawData,
    UserPortfolio,
    UserWatchlist,
)


OFFICIAL_STATEMENT_SCHEMAS = ("basi", "bd", "ci", "fh", "ins", "mim")
TWSE_STATEMENT_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_{schema}"
TPEX_STATEMENT_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_{schema}"
TWSE_DIVIDEND_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap45_L"
TPEX_EX_DIVIDEND_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"

RequestGet = Callable[..., Any]


@dataclass(frozen=True)
class FundamentalRefreshResult:
    status: str
    datasets_succeeded: int
    datasets_skipped: int
    datasets_failed: int
    records_written: int
    skipped_datasets: list[str]
    errors: list[str]


@dataclass(frozen=True)
class FundamentalBackfillResult:
    status: str
    symbols_processed: list[str]
    records_written: int
    next_after_symbol: str | None
    errors: list[str]


def refresh_official_fundamentals(
    session: Session,
    *,
    request_get: RequestGet | None = None,
    max_workers: int = 4,
) -> FundamentalRefreshResult:
    request_get = request_get or official_request_get
    datasets: list[tuple[str, str, str | None, str | None]] = []
    for schema in OFFICIAL_STATEMENT_SCHEMAS:
        datasets.append((f"TWSE_{schema}", TWSE_STATEMENT_URL.format(schema=schema), "TW", schema))
        datasets.append((f"TPEX_{schema}", TPEX_STATEMENT_URL.format(schema=schema), "TWO", schema))
    datasets.extend(
        [
            ("TWSE_dividend", TWSE_DIVIDEND_URL, None, None),
            ("TPEX_ex_dividend", TPEX_EX_DIVIDEND_URL, None, None),
        ]
    )

    errors: list[str] = []
    skipped_datasets: list[str] = []
    records_written = 0
    succeeded = 0
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 4))) as executor:
        futures = {
            executor.submit(_request_json, request_get, url): (name, market, schema)
            for name, url, market, schema in datasets
        }
        for future in as_completed(futures):
            name, market, schema = futures[future]
            try:
                payload = future.result()
                if market is not None and _is_official_statement_placeholder(
                    payload,
                    market=market,
                ):
                    skipped_datasets.append(name)
                    continue
                with session.begin_nested():
                    dataset_records = _store_official_dataset(
                        session,
                        name=name,
                        market=market,
                        schema=schema,
                        payload=payload,
                    )
                records_written += dataset_records
                succeeded += 1
            except Exception as exc:
                errors.append(f"{name}: refresh failed: {exc}")

    errors.sort()
    skipped_datasets.sort()
    failed = len(datasets) - succeeded - len(skipped_datasets)
    return FundamentalRefreshResult(
        status="ok" if failed == 0 else "partial",
        datasets_succeeded=succeeded,
        datasets_skipped=len(skipped_datasets),
        datasets_failed=failed,
        records_written=records_written,
        skipped_datasets=skipped_datasets,
        errors=errors,
    )


def backfill_fundamentals(
    session: Session,
    *,
    symbols: Sequence[str],
    after_symbol: str | None = None,
    limit: int = 10,
    provider: FinMindFundamentalProvider | None = None,
) -> FundamentalBackfillResult:
    bounded_limit = max(1, min(limit, 10))
    normalized_symbols = sorted(
        {
            symbol.strip().upper()
            for symbol in symbols
            if re.fullmatch(r"[1-9]\d{3}\.(?:TW|TWO)", symbol.strip().upper())
        }
    )
    if after_symbol:
        normalized_symbols = [symbol for symbol in normalized_symbols if symbol > after_symbol.upper()]
    selected = normalized_symbols[:bounded_limit]
    fallback = provider or FinMindFundamentalProvider()
    errors: list[str] = []
    records_written = 0
    for symbol in selected:
        periods = load_latest_fundamental_periods(session, symbol=symbol)
        if not fundamental_period_history_is_sufficient(periods):
            try:
                statement_rows = fallback.fetch_statement_rows(symbol)
                with session.begin_nested():
                    dataset_records = store_fundamental_periods(
                        session,
                        normalize_finmind_statement_rows(statement_rows, symbol=symbol),
                    )
                records_written += dataset_records
            except Exception as exc:
                errors.append(f"{symbol}: statement backfill failed: {exc}")
        dividends = load_latest_dividend_events(session, symbol=symbol)
        if not dividend_history_is_sufficient(dividends):
            try:
                dividend_rows = fallback.fetch_dividend_rows(symbol)
                with session.begin_nested():
                    dataset_records = store_dividend_events(
                        session,
                        normalize_finmind_dividend_rows(dividend_rows, symbol=symbol),
                    )
                records_written += dataset_records
            except Exception as exc:
                errors.append(f"{symbol}: dividend backfill failed: {exc}")

    has_more = len(normalized_symbols) > len(selected)
    return FundamentalBackfillResult(
        status="ok" if not errors else "partial",
        symbols_processed=selected,
        records_written=records_written,
        next_after_symbol=selected[-1] if selected and has_more and not errors else None,
        errors=errors,
    )


def resolve_latest_fundamental_raw_pool_date(session: Session) -> date | None:
    prepared_runs = session.scalars(
        select(DailyRadarPreparedRun).order_by(
            DailyRadarPreparedRun.run_date.desc(),
            DailyRadarPreparedRun.id.desc(),
        )
    ).all()
    for prepared in prepared_runs:
        ai_evidence = dict(prepared.step_statuses or {}).get("refresh-ai-evidence") or {}
        if ai_evidence.get("status") == "completed":
            return prepared.run_date
    return None


def fundamental_raw_pool_date_is_completed(
    session: Session,
    *,
    record_date: date,
) -> bool:
    prepared_runs = session.scalars(
        select(DailyRadarPreparedRun).where(
            DailyRadarPreparedRun.run_date == record_date,
        )
    ).all()
    return any(
        (dict(prepared.step_statuses or {}).get("refresh-ai-evidence") or {}).get(
            "status"
        )
        == "completed"
        for prepared in prepared_runs
    )


def resolve_fundamental_raw_pool_symbols(
    session: Session,
    *,
    record_date: date,
) -> list[str]:
    supported_raw_symbol = or_(
        StockRawData.symbol.like("%.TW"),
        StockRawData.symbol.like("%.TWO"),
    )
    return list(
        session.scalars(
            select(StockRawData.symbol).where(
                StockRawData.record_date == record_date,
                StockRawData.raw_data_is_final.is_(True),
                supported_raw_symbol,
            )
        ).all()
    )


def resolve_managed_fundamental_symbols(
    session: Session,
    *,
    raw_pool_date: date | None = None,
) -> list[str]:
    symbols = set(
        session.scalars(
            select(UserPortfolio.symbol).where(UserPortfolio.is_active.is_(True))
        ).all()
    )
    symbols.update(session.scalars(select(UserWatchlist.symbol)).all())
    prepared = session.scalars(
        select(DailyRadarPreparedRun).order_by(
            DailyRadarPreparedRun.run_date.desc(),
            DailyRadarPreparedRun.id.desc(),
        ).limit(1)
    ).first()
    if prepared is not None:
        symbols.update(str(symbol) for symbol in prepared.selected_symbols)
    effective_raw_pool_date = (
        raw_pool_date
        if raw_pool_date is not None
        else resolve_latest_fundamental_raw_pool_date(session)
    )
    if effective_raw_pool_date is not None:
        symbols.update(
            resolve_fundamental_raw_pool_symbols(
                session,
                record_date=effective_raw_pool_date,
            )
        )
    return sorted(
        symbol.strip().upper()
        for symbol in symbols
        if re.fullmatch(r"[1-9]\d{3}\.(?:TW|TWO)", str(symbol).strip().upper())
    )


def resolve_pending_fundamental_backfill_symbols(
    session: Session,
    *,
    symbols: Sequence[str],
) -> list[str]:
    pending: list[str] = []
    normalized_symbols = sorted(
        {
            str(symbol).strip().upper()
            for symbol in symbols
            if re.fullmatch(r"[1-9]\d{3}\.(?:TW|TWO)", str(symbol).strip().upper())
        }
    )
    periods_by_symbol = load_latest_fundamental_periods_for_symbols(
        session,
        symbols=normalized_symbols,
    )
    dividends_by_symbol = load_latest_dividend_events_for_symbols(
        session,
        symbols=normalized_symbols,
    )
    for symbol in normalized_symbols:
        periods = periods_by_symbol.get(symbol, [])
        dividends = dividends_by_symbol.get(symbol, [])
        if (
            not fundamental_period_history_is_sufficient(periods)
            or not dividend_history_is_sufficient(dividends)
        ):
            pending.append(symbol)
    return pending


def create_fundamental_backfill_job(
    session: Session,
    *,
    symbols: Sequence[str],
    raw_pool_date: date | None,
) -> FundamentalBackfillJob:
    job = FundamentalBackfillJob(
        id=str(uuid.uuid4()),
        raw_pool_date=raw_pool_date,
        symbols=list(symbols),
        next_after_symbol=None,
        status="running",
    )
    session.add(job)
    session.flush()
    return job


def get_fundamental_backfill_job(
    session: Session,
    *,
    job_id: str,
    for_update: bool = False,
) -> FundamentalBackfillJob | None:
    statement = select(FundamentalBackfillJob).where(FundamentalBackfillJob.id == job_id)
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _request_json(request_get: RequestGet, url: str) -> Any:
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            response = request_get(url, timeout=45)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error) if last_error is not None else "request failed")


def _store_official_dataset(
    session: Session,
    *,
    name: str,
    market: str | None,
    schema: str | None,
    payload: Any,
) -> int:
    if market is not None and schema is not None:
        if not _is_row_sequence(payload):
            raise ValueError("response is not a row list")
        periods = normalize_official_statement_rows(
            payload,
            market=market,
            industry_schema=schema,
            source_dataset=name,
        )
        if not periods:
            raise ValueError("normalized dataset is empty")
        return store_fundamental_periods(
            session,
            periods,
        )
    if name == "TWSE_dividend":
        if not _is_row_sequence(payload):
            raise ValueError("response is not a row list")
        events = normalize_twse_dividend_rows(payload)
        if not events:
            raise ValueError("normalized dataset is empty")
        return store_dividend_events(session, events)
    if not isinstance(payload, Mapping):
        raise ValueError("response is not an object")
    return store_dividend_events(session, normalize_tpex_ex_dividend_payload(payload))


def _is_row_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_official_statement_placeholder(payload: Any, *, market: str) -> bool:
    if market not in {"TW", "TWO"} or not _is_row_sequence(payload) or not payload:
        return False

    for row in payload:
        if not isinstance(row, Mapping):
            return False
        contract = next(
            (
                (required_fields, report_date_field)
                for required_fields, report_date_field in (
                    ({"出表日期", "年度", "季別", "公司代號"}, "出表日期"),
                    ({"Date", "Year", "Season", "SecuritiesCompanyCode"}, "Date"),
                )
                if required_fields.issubset(row)
            ),
            None,
        )
        if contract is None:
            return False
        _, report_date_field = contract
        if not _is_known_placeholder_report_date(row.get(report_date_field)):
            return False
        if any(
            _has_text(value)
            for field, value in row.items()
            if field != report_date_field
        ):
            return False
    return True


def _has_text(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _is_known_placeholder_report_date(value: Any) -> bool:
    text = str(value or "").strip()
    try:
        if re.fullmatch(r"\d{7}", text):
            date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7]))
            return True
        if re.fullmatch(r"\d{8}", text):
            year = int(text[:4])
            if year <= 1911:
                return False
            date(year, int(text[4:6]), int(text[6:8]))
            return True
    except ValueError:
        return False
    return False


__all__ = [
    "FundamentalBackfillResult",
    "FundamentalRefreshResult",
    "backfill_fundamentals",
    "create_fundamental_backfill_job",
    "fundamental_raw_pool_date_is_completed",
    "get_fundamental_backfill_job",
    "refresh_official_fundamentals",
    "resolve_fundamental_raw_pool_symbols",
    "resolve_latest_fundamental_raw_pool_date",
    "resolve_managed_fundamental_symbols",
    "resolve_pending_fundamental_backfill_symbols",
]
