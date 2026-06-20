from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.cooldown import apply_cooldown_status
from ai_stock_sentinel.daily_radar.background_context import build_background_context_labels
from ai_stock_sentinel.daily_radar.data_loader import (
    load_daily_radar_cache_records,
    load_daily_radar_fixture_records,
)
from ai_stock_sentinel.daily_radar.explanations import generate_candidate_explanation
from ai_stock_sentinel.daily_radar.prefilter import run_stage1_prefilter
from ai_stock_sentinel.daily_radar.repository import (
    create_daily_radar_run,
    get_symbol_candidate_history,
    replace_run_candidates,
    update_daily_radar_run,
)
from ai_stock_sentinel.daily_radar.scoring import score_daily_radar_record
from ai_stock_sentinel.db.models import DailyRadarRun
from ai_stock_sentinel.db.session import _get_session_local
from ai_stock_sentinel.phase1_avwap.projection import read_phase1_avwap_contexts_for_daily_radar


DEFAULT_CANDIDATE_LIMIT = 100


def run_daily_radar(
    run_date: date,
    market: str,
    *,
    session: Session | None = None,
    fixture_dir: str | Path | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
    cache_rows: Iterable[Any] | None = None,
    market_context: Mapping[str, Any] | None = None,
    background_contexts_by_symbol: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    history_candidates: Iterable[Mapping[str, Any]] | None = None,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    allow_fixture_fallback: bool = True,
) -> DailyRadarRun:
    if session is not None:
        return _run_daily_radar_with_session(
            session,
            run_date=run_date,
            market=market,
            fixture_dir=fixture_dir,
            records=records,
            cache_rows=cache_rows,
            market_context=market_context,
            background_contexts_by_symbol=background_contexts_by_symbol,
            history_candidates=history_candidates,
            candidate_limit=candidate_limit,
            allow_fixture_fallback=allow_fixture_fallback,
        )

    with _managed_session() as managed_session:
        run = _run_daily_radar_with_session(
            managed_session,
            run_date=run_date,
            market=market,
            fixture_dir=fixture_dir,
            records=records,
            cache_rows=cache_rows,
            market_context=market_context,
            background_contexts_by_symbol=background_contexts_by_symbol,
            history_candidates=history_candidates,
            candidate_limit=candidate_limit,
            allow_fixture_fallback=allow_fixture_fallback,
        )
        managed_session.commit()
        return run


def _run_daily_radar_with_session(
    session: Session,
    *,
    run_date: date,
    market: str,
    fixture_dir: str | Path | None,
    records: Iterable[Mapping[str, Any]] | None,
    cache_rows: Iterable[Any] | None,
    market_context: Mapping[str, Any] | None,
    background_contexts_by_symbol: Mapping[str, Iterable[Mapping[str, Any]]] | None,
    history_candidates: Iterable[Mapping[str, Any]] | None,
    candidate_limit: int,
    allow_fixture_fallback: bool,
) -> DailyRadarRun:
    run = create_daily_radar_run(session, run_date=run_date, market=market)
    errors: list[dict[str, Any]] = []

    try:
        loaded_records = _load_records(
            records=records,
            cache_rows=cache_rows,
            fixture_dir=fixture_dir,
            allow_fixture_fallback=allow_fixture_fallback,
        )
        active_fixture_dir = Path(fixture_dir) if fixture_dir is not None else _default_fixture_dir()
        if market_context is not None:
            active_market_context = dict(market_context)
        elif allow_fixture_fallback:
            active_market_context = dict(_load_optional_json(active_fixture_dir / "market_context.json"))
        else:
            active_market_context = {}
        prefilter_results = run_stage1_prefilter(
            loaded_records,
            limit=candidate_limit,
            include_rejected=True,
        )
        accepted_prefilters = [
            result for result in prefilter_results if result["prefilter_status"] == "accepted"
        ]
        errors.extend(_prefilter_errors(prefilter_results))

        records_by_symbol = {str(record["symbol"]): record for record in loaded_records}
        prefilter_by_symbol = {str(result["symbol"]): result for result in accepted_prefilters}
        scored_candidates = _score_candidates(
            records_by_symbol=records_by_symbol,
            prefilter_by_symbol=prefilter_by_symbol,
            market_context=active_market_context,
            errors=errors,
        )
        scored_candidates = _with_background_contexts(
            scored_candidates,
            background_contexts_by_symbol=background_contexts_by_symbol,
        )
        scored_candidates = _with_phase1_avwap_contexts(
            session,
            scored_candidates,
            run_date=run_date,
        )
        history = list(history_candidates) if history_candidates is not None else _history_from_repository_or_fixture(
            session,
            run_date=run_date,
            market=market,
            symbols=[candidate["symbol"] for candidate in scored_candidates],
            fixture_dir=active_fixture_dir,
            allow_fixture_fallback=allow_fixture_fallback,
        )
        cooled_candidates = apply_cooldown_status(
            scored_candidates,
            history,
            run_date=run_date,
        )
        final_candidates = _with_explanations(cooled_candidates, errors)
        final_candidates.sort(key=lambda candidate: (-int(candidate["observation_score"]), str(candidate["symbol"])))

        replace_run_candidates(session, run, final_candidates)
        status = _final_status(
            universe_count=len(loaded_records),
            accepted_count=len(accepted_prefilters),
            candidate_count=len(final_candidates),
            errors=errors,
        )
        return update_daily_radar_run(
            session,
            run,
            status=status,
            universe_count=len(loaded_records),
            prefilter_count=len(accepted_prefilters),
            candidate_count=len(final_candidates),
            errors=errors,
        )
    except Exception as exc:
        errors.append({"code": "run_error", "message": str(exc)})
        return update_daily_radar_run(session, run, status="failed", errors=errors)


def _load_records(
    *,
    records: Iterable[Mapping[str, Any]] | None,
    cache_rows: Iterable[Any] | None,
    fixture_dir: str | Path | None,
    allow_fixture_fallback: bool,
) -> list[dict[str, Any]]:
    if records is not None:
        return [dict(record) for record in records]
    if cache_rows is not None:
        return load_daily_radar_cache_records(cache_rows)
    if not allow_fixture_fallback:
        return []
    return load_daily_radar_fixture_records(fixture_dir or _default_fixture_dir())


def _score_candidates(
    *,
    records_by_symbol: Mapping[str, Mapping[str, Any]],
    prefilter_by_symbol: Mapping[str, Mapping[str, Any]],
    market_context: Mapping[str, Any],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for symbol in sorted(prefilter_by_symbol):
        try:
            candidate = score_daily_radar_record(
                records_by_symbol[symbol],
                market_context=market_context,
                prefilter_result=prefilter_by_symbol[symbol],
            )
        except Exception as exc:
            errors.append({"code": "candidate_processing_error", "symbol": symbol, "message": str(exc)})
            continue
        candidates.append(candidate)
    return candidates


def _with_explanations(
    candidates: Iterable[Mapping[str, Any]],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    explained: list[dict[str, Any]] = []
    for candidate in candidates:
        symbol = str(candidate["symbol"])
        try:
            explanation = generate_candidate_explanation(candidate)
        except Exception as exc:
            errors.append({"code": "candidate_processing_error", "symbol": symbol, "message": str(exc)})
            continue
        explained.append(dict(candidate) | {"explanation": explanation["text"]})
    return explained


def _with_background_contexts(
    candidates: Iterable[Mapping[str, Any]],
    *,
    background_contexts_by_symbol: Mapping[str, Iterable[Mapping[str, Any]]] | None,
) -> list[dict[str, Any]]:
    if not background_contexts_by_symbol:
        return [dict(candidate) for candidate in candidates]

    enriched: list[dict[str, Any]] = []
    for candidate in candidates:
        symbol = str(candidate.get("symbol") or "")
        contexts = [dict(context) for context in background_contexts_by_symbol.get(symbol, [])]
        if not contexts:
            enriched.append(dict(candidate))
            continue

        next_candidate = dict(candidate)
        input_snapshot = dict(_mapping(next_candidate.get("input_snapshot")))
        input_snapshot["background_context"] = contexts
        input_snapshot["background_context_labels"] = build_background_context_labels(contexts)
        next_candidate["input_snapshot"] = input_snapshot

        data_dates = dict(_mapping(next_candidate.get("data_dates")))
        context_dates = [
            str(context.get("as_of_date"))
            for context in contexts
            if context.get("as_of_date") is not None
        ]
        if context_dates:
            data_dates["background_context"] = max(context_dates)
        next_candidate["data_dates"] = data_dates
        enriched.append(next_candidate)
    return enriched


def _with_phase1_avwap_contexts(
    session: Session,
    candidates: Iterable[Mapping[str, Any]],
    *,
    run_date: date,
) -> list[dict[str, Any]]:
    candidate_rows = [dict(candidate) for candidate in candidates]
    symbols = [str(candidate.get("symbol") or "") for candidate in candidate_rows]
    contexts = read_phase1_avwap_contexts_for_daily_radar(
        session,
        symbols=symbols,
        data_date=run_date,
    )

    enriched: list[dict[str, Any]] = []
    for candidate in candidate_rows:
        symbol = str(candidate.get("symbol") or "")
        next_candidate = dict(candidate)
        input_snapshot = dict(_mapping(next_candidate.get("input_snapshot")))
        input_snapshot["phase1_avwap_context"] = contexts.get(symbol.upper())
        next_candidate["input_snapshot"] = input_snapshot
        enriched.append(next_candidate)
    return enriched


def _prefilter_errors(prefilter_results: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for result in prefilter_results:
        status = str(result.get("prefilter_status"))
        if status == "accepted":
            continue
        errors.append(
            {
                "code": f"prefilter_{status}",
                "symbol": str(result.get("symbol")),
                "reasons": [
                    str(reason.get("code"))
                    for reason in result.get("prefilter_reasons", [])
                    if isinstance(reason, Mapping)
                ],
            }
        )
    return errors


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _history_from_repository_or_fixture(
    session: Session,
    *,
    run_date: date,
    market: str,
    symbols: Iterable[str],
    fixture_dir: Path,
    allow_fixture_fallback: bool,
) -> list[Mapping[str, Any]]:
    history = get_symbol_candidate_history(
        session,
        symbols=symbols,
        before_date=run_date,
        lookback_days=5,
        market=market,
    )
    if history:
        return history
    if not allow_fixture_fallback:
        return []
    payload = _load_optional_json(fixture_dir / "history_candidates.json")
    records = payload.get("records") if isinstance(payload, Mapping) else None
    if isinstance(records, list):
        return [record for record in records if isinstance(record, Mapping)]
    return []


def _final_status(
    *,
    universe_count: int,
    accepted_count: int,
    candidate_count: int,
    errors: list[dict[str, Any]],
) -> str:
    if candidate_count > 0:
        return "completed"
    if universe_count > 0 and accepted_count == 0 and any(error["code"] == "prefilter_stale_data" for error in errors):
        return "stale_data"
    return "failed" if errors else "completed"


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _default_fixture_dir() -> Path:
    return Path(__file__).parents[3] / "tests" / "fixtures" / "daily_radar"


@contextmanager
def _managed_session():
    session = _get_session_local()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["run_daily_radar"]
