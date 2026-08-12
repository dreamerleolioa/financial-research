#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


EXPORT_CONTRACT_VERSION = "codex-daily-radar-raw-pool-v2"
DEFAULT_KEYCHAIN_SERVICE = "financial-research-production-db"
DEFAULT_DB_HOST = "139.162.81.85"
DEFAULT_DB_PORT = 32643
DEFAULT_DB_NAME = "zeabur"
DEFAULT_DB_USER = "codex_radar_reader"
BACKGROUND_CONTEXT_TYPES = ("weekly_major_holders", "lending", "full_margin")
INDICATOR_FIELDS = (
    "ma5",
    "ma20",
    "ma60",
    "bias20",
    "rsi14",
    "macd",
    "macd_signal",
    "macd_histogram",
    "kd_k",
    "kd_d",
    "atr14",
    "mfi14",
    "obv",
    "obv_trend",
    "support_level",
    "resistance_level",
    "volume_ratio",
    "missing_trading_days_60",
)
INSTITUTIONAL_FIELDS = (
    "foreign_net_shares",
    "investment_trust_net_shares",
    "three_party_net_shares",
    "same_day_actor",
    "same_day_net_buy",
    "same_day_concentration",
    "same_day_source_dates",
    "recent_actor",
    "cumulative_net_buy",
    "consecutive_buy_days",
    "consecutive_positive_days",
    "recent_source_dates",
)
FUNDAMENTAL_FIELDS = (
    "ttm_eps",
    "annual_cash_dividend",
    "dividend_yield",
    "pe_current",
    "pe_mean",
    "pe_std",
    "pe_percentile",
    "pe_band",
    "margin",
    "source_provider",
    "warnings",
)
BACKGROUND_PAYLOAD_FIELDS = {
    "weekly_major_holders": (
        "data_dates",
        "large_holder_400_lot_plus_ratio",
        "major_holder_people",
        "major_holder_ratio",
        "retail_100_lot_or_less_ratio",
        "thousand_lot_holder_ratio",
        "total_people",
        "total_shares",
    ),
    "lending": (
        "data_dates",
        "daily_point_count",
        "latest_daily_lending_volume",
        "lending_volume_delta",
        "lookback_trading_days",
        "period_lending_volume",
        "unit",
    ),
    "full_margin": (
        "data_dates",
        "latest_margin_balance",
        "latest_short_balance",
        "lookback_trading_days",
        "margin_balance_delta",
        "margin_balance_delta_pct",
        "short_balance_delta",
        "short_balance_delta_pct",
        "unit",
    ),
}
AVWAP_ANCHOR_FIELDS = (
    "anchor_date",
    "anchor_reason",
    "available",
    "avwap",
    "distance_to_avwap_pct",
    "estimated",
    "snapshot_close",
    "source_granularity",
)


def main() -> None:
    args = _parse_args()
    payload = export_daily_radar(
        user_id=args.user_id,
        market=args.market,
        run_date=args.run_date,
        db_host=args.db_host,
        db_port=args.db_port,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=_database_password(args.keychain_service),
        sslmode=args.sslmode,
    )
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=args.indent,
        sort_keys=True,
        default=_json_default,
    )
    if args.output is None:
        print(serialized)
        return
    output_path = _write_secure_output(args.output, serialized)
    print(
        json.dumps(
            {
                "contract_version": payload["contract_version"],
                "input_hash": payload["input_hash"],
                "output_path": str(output_path),
                "raw_universe_count": len(payload["raw_universe"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def export_daily_radar(
    *,
    user_id: int,
    market: str,
    run_date: date | None,
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_password: str,
    sslmode: str,
) -> dict[str, Any]:
    if market != "TW":
        raise RuntimeError("The AI raw-pool export supports market=TW only")
    connection = psycopg2.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_password,
        sslmode=sslmode,
        connect_timeout=10,
        application_name="codex_daily_radar_raw_universe_export",
    )
    try:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            reader_state = _reader_state(cursor)
            user_scope = _user_scope(cursor, user_id=user_id)
            source_run = _latest_completed_run(cursor, market=market, run_date=run_date)
            prepared = _prepared_run(cursor, run_date=source_run["run_date"], market=market)
            if prepared is None:
                raise RuntimeError("Completed Daily Radar run has no prepared data anchor")
            raw_rows = _raw_universe_rows(
                cursor,
                run_date=source_run["run_date"],
            )
            raw_symbols = [str(row["symbol"]) for row in raw_rows]
            avwap_by_symbol = _avwap_contexts(
                cursor,
                run_date=source_run["run_date"],
                symbols=raw_symbols,
            )
            background_by_symbol = _background_contexts(
                cursor,
                run_date=source_run["run_date"],
                symbols=raw_symbols,
            )
            positions = _active_positions(cursor, user_id=user_id)
            watchlist = _watchlist(cursor, user_id=user_id)
        connection.rollback()
    finally:
        connection.close()

    raw_universe = [
        _project_raw_universe_row(
            row,
            avwap_context=avwap_by_symbol.get(str(row["symbol"])),
            background_context=background_by_symbol.get(str(row["symbol"]), []),
        )
        for row in raw_rows
    ]
    _validate_export(source_run=source_run, prepared=prepared, raw_universe=raw_universe)

    generated_at = datetime.now(timezone.utc)
    portfolio_fingerprint = _stable_hash(
        [
            {
                "position_group_id": item["position_group_id"],
                "symbol": item["symbol"],
                "entry_price": item["entry_price"],
                "quantity": item["quantity"],
                "entry_date": item["entry_date"],
            }
            for item in positions
        ]
    )
    payload: dict[str, Any] = {
        "contract_version": EXPORT_CONTRACT_VERSION,
        "generated_at": generated_at,
        "source": {
            "database_role": reader_state["current_user"],
            "transaction_read_only": reader_state["transaction_read_only"],
            "selection": "latest_completed_run" if run_date is None else "completed_run_for_date",
            "ai_pool_semantics": "all_final_supported_tw_stock_raw_rows_for_source_date",
            "prepared_run_usage": "date_readiness_and_market_context_only",
            "excluded_result_sources": [
                "daily_radar_candidates",
                "daily_radar_prepared_runs.selected_symbols",
                "prefilter_results",
                "universe_rank_and_track_scores",
            ],
        },
        "user_scope": {
            "user_id": user_scope["id"],
            "portfolio_fingerprint": portfolio_fingerprint,
        },
        "source_run": source_run,
        "prepared_run": _project_prepared_run(prepared),
        "raw_pool": {
            "record_date": source_run["run_date"],
            "selection": "all_final_supported_tw_stock_raw_rows_for_source_date",
            "symbol_count": len(raw_universe),
        },
        "raw_universe": raw_universe,
        "portfolio": {"active_positions": positions},
        "watchlist": watchlist,
    }
    payload["input_hash"] = _stable_hash({key: value for key, value in payload.items() if key != "generated_at"})
    return payload


def _reader_state(cursor: RealDictCursor) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT current_user,
               current_setting('transaction_read_only') AS transaction_read_only
        """
    )
    row = dict(cursor.fetchone() or {})
    if row.get("current_user") != DEFAULT_DB_USER or row.get("transaction_read_only") != "on":
        raise RuntimeError("Production export requires codex_radar_reader in a read-only transaction")
    return row


def _user_scope(cursor: RealDictCursor, *, user_id: int) -> dict[str, Any]:
    cursor.execute(
        "SELECT id, is_active, deleted_at FROM users WHERE id = %s",
        (user_id,),
    )
    row = cursor.fetchone()
    if row is None or not row["is_active"] or row["deleted_at"] is not None:
        raise RuntimeError(f"Configured user_id={user_id} is not an active production user")
    return dict(row)


def _latest_completed_run(cursor: RealDictCursor, *, market: str, run_date: date | None) -> dict[str, Any]:
    query = """
        SELECT id, run_date, market, status, started_at, finished_at, created_at
        FROM daily_radar_runs
        WHERE market = %s AND status = 'completed'
    """
    params: list[Any] = [market]
    if run_date is not None:
        query += " AND run_date = %s"
        params.append(run_date)
    query += " ORDER BY run_date DESC, created_at DESC LIMIT 1"
    cursor.execute(query, params)
    row = cursor.fetchone()
    if row is None:
        suffix = f" for {run_date.isoformat()}" if run_date else ""
        raise RuntimeError(f"No completed {market} Daily Radar run{suffix}")
    return dict(row)


def _prepared_run(cursor: RealDictCursor, *, run_date: date, market: str) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT id, run_date, market, status,
               market_context, step_statuses, errors, created_at, updated_at
        FROM daily_radar_prepared_runs
        WHERE run_date = %s AND market = %s
        LIMIT 1
        """,
        (run_date, market),
    )
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _raw_universe_rows(
    cursor: RealDictCursor,
    *,
    run_date: date,
) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT symbol, record_date, technical, institutional, fundamental,
               raw_data_is_final, fetched_at
        FROM stock_raw_data
        WHERE record_date = %s
          AND raw_data_is_final = true
          AND symbol ~ '^[0-9]{4}\\.(TW|TWO)$'
          AND symbol !~ '^00'
        ORDER BY symbol ASC
        """,
        (run_date,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _avwap_contexts(
    cursor: RealDictCursor,
    *,
    run_date: date,
    symbols: list[str],
) -> dict[str, dict[str, Any]]:
    cursor.execute(
        """
        SELECT DISTINCT ON (symbol)
               symbol, data_date, dataset, adjustment_mode, source_provider,
               source_granularity, is_final, freshness, missing_reason, payload
        FROM phase1_avwap_snapshots
        WHERE symbol = ANY(%s) AND data_date <= %s
        ORDER BY symbol, data_date DESC, updated_at DESC
        """,
        (symbols, run_date),
    )
    return {str(row["symbol"]): dict(row) for row in cursor.fetchall()}


def _background_contexts(
    cursor: RealDictCursor,
    *,
    run_date: date,
    symbols: list[str],
) -> dict[str, list[dict[str, Any]]]:
    cursor.execute(
        """
        SELECT DISTINCT ON (symbol, context_type)
               symbol, context_type, source, as_of_date, freshness,
               payload, missing_reason, replay_key
        FROM shared_background_contexts
        WHERE symbol = ANY(%s)
          AND context_type = ANY(%s)
          AND (as_of_date IS NULL OR as_of_date <= %s)
          AND applicable_consumers ? %s
        ORDER BY symbol, context_type, as_of_date DESC NULLS LAST, updated_at DESC
        """,
        (symbols, list(BACKGROUND_CONTEXT_TYPES), run_date, "daily_radar"),
    )
    contexts: dict[str, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        projected = dict(row)
        symbol = str(projected.pop("symbol"))
        contexts.setdefault(symbol, []).append(projected)
    return contexts


def _active_positions(cursor: RealDictCursor, *, user_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT position_group_id, symbol, entry_price, quantity, entry_date,
               notes, created_at, updated_at
        FROM user_portfolio
        WHERE user_id = %s AND is_active = true
        ORDER BY symbol ASC, entry_date ASC
        """,
        (user_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _watchlist(cursor: RealDictCursor, *, user_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT symbol, notes, sort_order, created_at, updated_at
        FROM user_watchlist
        WHERE user_id = %s
        ORDER BY sort_order ASC, symbol ASC
        """,
        (user_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _project_raw_universe_row(
    row: Mapping[str, Any],
    *,
    avwap_context: Mapping[str, Any] | None,
    background_context: list[dict[str, Any]],
) -> dict[str, Any]:
    technical = _mapping(row.get("technical"))
    institutional = _mapping(row.get("institutional"))
    fundamental = _mapping(row.get("fundamental"))
    indicators = _mapping(technical.get("indicators"))
    return {
        "symbol": row.get("symbol"),
        "name": technical.get("name"),
        "record_date": row.get("record_date"),
        "raw_data_is_final": bool(row.get("raw_data_is_final")),
        "fetched_at": row.get("fetched_at"),
        "data_dates": {
            "technical": technical.get("data_dates"),
            "institutional": institutional.get("data_dates"),
            "fundamental": fundamental.get("data_dates"),
        },
        "ohlcv": technical.get("ohlcv"),
        "indicators": _pick_fields(indicators, INDICATOR_FIELDS),
        "price_history": _project_price_history(technical.get("price_history")),
        "institutional": _pick_fields(institutional, INSTITUTIONAL_FIELDS),
        "fundamental": _pick_fields(fundamental, FUNDAMENTAL_FIELDS),
        "background_context": _project_background_context(background_context),
        "avwap_context": _project_avwap_context(avwap_context),
    }


def _project_prepared_run(prepared: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": prepared.get("id"),
        "run_date": prepared.get("run_date"),
        "market": prepared.get("market"),
        "status": prepared.get("status"),
        "market_context": prepared.get("market_context"),
        "step_statuses": _project_step_statuses(prepared.get("step_statuses")),
        "error_count": _list_count(prepared.get("errors")),
        "created_at": prepared.get("created_at"),
        "updated_at": prepared.get("updated_at"),
    }


def _project_step_statuses(value: Any) -> dict[str, dict[str, Any]]:
    statuses = _mapping(value)
    projected: dict[str, dict[str, Any]] = {}
    for step_name, raw_details in sorted(statuses.items()):
        details = _mapping(raw_details)
        projected[str(step_name)] = {
            "status": details.get("status"),
            "updated_at": details.get("updated_at"),
            "records_written": details.get("records_written"),
            "error_count": _list_count(details.get("errors")),
            "missing_symbol_count": _list_count(details.get("missing_symbols")),
            "skipped_symbol_count": _list_count(details.get("skipped_symbols")),
        }
    return projected


def _project_price_history(value: Any) -> dict[str, Any]:
    rows = [dict(row) for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []
    usable = [row for row in rows if row.get("date") is not None and _finite_number(row.get("close"))]
    horizons: dict[str, dict[str, Any] | None] = {}
    for days in (5, 20, 60):
        if len(usable) <= days:
            horizons[f"{days}d"] = None
            continue
        start = usable[-(days + 1)]
        end = usable[-1]
        start_close = float(start["close"])
        end_close = float(end["close"])
        horizons[f"{days}d"] = {
            "start_date": start["date"],
            "start_close": start["close"],
            "end_date": end["date"],
            "end_close": end["close"],
            "return_pct": ((end_close / start_close) - 1.0) * 100.0 if start_close != 0 else None,
        }
    return {
        "point_count": len(usable),
        "first_date": usable[0]["date"] if usable else None,
        "last_date": usable[-1]["date"] if usable else None,
        "recent_closes": usable[-5:],
        "horizons": horizons,
    }


def _project_background_context(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for context in contexts:
        context_type = str(context.get("context_type") or "")
        fields = BACKGROUND_PAYLOAD_FIELDS.get(context_type, ())
        projected.append(
            {
                "context_type": context_type,
                "as_of_date": context.get("as_of_date"),
                "freshness": context.get("freshness"),
                "missing_reason": context.get("missing_reason"),
                "source": context.get("source"),
                "data": _pick_fields(_mapping(context.get("payload")), fields),
            }
        )
    return projected


def _project_avwap_context(context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if context is None:
        return None
    payload = _mapping(context.get("payload"))
    anchors = _mapping(payload.get("anchors"))
    return {
        "data_date": context.get("data_date"),
        "freshness": context.get("freshness"),
        "missing_reason": context.get("missing_reason"),
        "is_final": context.get("is_final"),
        "source_provider": context.get("source_provider"),
        "adjustment_mode": context.get("adjustment_mode"),
        "anchors": {
            str(name): _pick_fields(_mapping(anchor), AVWAP_ANCHOR_FIELDS)
            for name, anchor in sorted(anchors.items())
        },
        "data_quality": payload.get("data_quality"),
    }


def _validate_export(
    *,
    source_run: Mapping[str, Any],
    prepared: Mapping[str, Any],
    raw_universe: list[dict[str, Any]],
) -> None:
    run_date = source_run.get("run_date")
    if not isinstance(run_date, date) or run_date > date.today():
        raise RuntimeError("Daily Radar source run_date is missing or in the future")
    if prepared.get("run_date") != run_date:
        raise RuntimeError("Prepared raw-data universe date does not match the completed source run")
    if not raw_universe:
        raise RuntimeError("No final supported Taiwan stock raw rows are available for the source date")
    symbols = [str(item.get("symbol")) for item in raw_universe]
    if len(set(symbols)) != len(symbols):
        raise RuntimeError("Raw universe export contains duplicate symbols")
    if symbols != sorted(symbols):
        raise RuntimeError("Raw universe export must be ordered by symbol, not source rank")
    if any(not _is_supported_tw_stock_symbol(symbol) for symbol in symbols):
        raise RuntimeError("Raw universe export contains an unsupported Taiwan stock symbol")
    if any(item.get("record_date") != run_date for item in raw_universe):
        raise RuntimeError("Raw universe export contains a mismatched record_date")
    if any(item.get("raw_data_is_final") is not True for item in raw_universe):
        raise RuntimeError("Raw universe export contains non-final source data")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_supported_tw_stock_symbol(symbol: str) -> bool:
    normalized = str(symbol).strip().upper()
    suffix = ".TWO" if normalized.endswith(".TWO") else ".TW" if normalized.endswith(".TW") else None
    if suffix is None:
        return False
    stock_id = normalized.removesuffix(suffix)
    return len(stock_id) == 4 and stock_id.isdigit() and not stock_id.startswith("00")


def _pick_fields(source: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source.get(field) for field in fields}


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    numeric = float(value)
    return numeric == numeric and numeric not in (float("inf"), float("-inf"))


def _write_secure_output(output: Path, serialized: str) -> Path:
    resolved_parent = output.expanduser().parent.resolve()
    allowed_parents = {
        Path("/tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }
    if resolved_parent not in allowed_parents:
        allowed_display = ", ".join(sorted(str(path) for path in allowed_parents))
        raise RuntimeError(f"--output must be a direct child of one of: {allowed_display}")
    resolved = resolved_parent / output.name
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    return resolved


def _database_password(keychain_service: str) -> str:
    value = os.environ.get("CODEX_RADAR_DB_PASSWORD") or os.environ.get("PGPASSWORD")
    if value:
        return value
    try:
        completed = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                DEFAULT_DB_USER,
                "-s",
                keychain_service,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"Read-only database credential is unavailable in macOS Keychain service {keychain_service!r}"
        ) from exc
    value = completed.stdout.strip()
    if not value:
        raise RuntimeError("Read-only database credential resolved to an empty value")
    return value


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export one production date-scoped AI raw pool read-only.")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--market", default="TW")
    parser.add_argument("--run-date", type=date.fromisoformat, default=None)
    parser.add_argument("--db-host", default=os.environ.get("CODEX_RADAR_DB_HOST", DEFAULT_DB_HOST))
    parser.add_argument("--db-port", type=int, default=int(os.environ.get("CODEX_RADAR_DB_PORT", DEFAULT_DB_PORT)))
    parser.add_argument("--db-name", default=os.environ.get("CODEX_RADAR_DB_NAME", DEFAULT_DB_NAME))
    parser.add_argument("--db-user", default=os.environ.get("CODEX_RADAR_DB_USER", DEFAULT_DB_USER))
    parser.add_argument("--sslmode", default=os.environ.get("CODEX_RADAR_DB_SSLMODE", "disable"))
    parser.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    parser.add_argument("--indent", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.user_id <= 0:
        parser.error("--user-id must be positive")
    return args


if __name__ == "__main__":
    main()
