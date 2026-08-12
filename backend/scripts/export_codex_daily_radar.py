#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


EXPORT_CONTRACT_VERSION = "codex-daily-radar-export-v1"
DEFAULT_KEYCHAIN_SERVICE = "financial-research-production-db"
DEFAULT_DB_HOST = "139.162.81.85"
DEFAULT_DB_PORT = 32643
DEFAULT_DB_NAME = "zeabur"
DEFAULT_DB_USER = "codex_radar_reader"


def main() -> None:
    args = _parse_args()
    payload = export_daily_radar(
        user_id=args.user_id,
        market=args.market,
        run_date=args.run_date,
        candidate_limit=args.candidate_limit,
        recent_run_limit=args.recent_run_limit,
        db_host=args.db_host,
        db_port=args.db_port,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=_database_password(args.keychain_service),
        sslmode=args.sslmode,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=args.indent, sort_keys=True, default=_json_default))


def export_daily_radar(
    *,
    user_id: int,
    market: str,
    run_date: date | None,
    candidate_limit: int,
    recent_run_limit: int,
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_password: str,
    sslmode: str,
) -> dict[str, Any]:
    connection = psycopg2.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_password,
        sslmode=sslmode,
        connect_timeout=10,
        application_name="codex_daily_radar_export",
    )
    try:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            reader_state = _reader_state(cursor)
            user_scope = _user_scope(cursor, user_id=user_id)
            run = _latest_completed_run(cursor, market=market, run_date=run_date)
            prepared = _prepared_run(cursor, run_date=run["run_date"], market=market)
            candidates = _candidates(cursor, run_id=run["id"], limit=candidate_limit)
            positions = _active_positions(cursor, user_id=user_id)
            watchlist = _watchlist(cursor, user_id=user_id)
            tracked_symbols = sorted(
                {
                    *(str(item["symbol"]) for item in candidates),
                    *(str(item["symbol"]) for item in positions),
                    *(str(item["symbol"]) for item in watchlist),
                }
            )
            recent_history = _recent_candidate_history(
                cursor,
                market=market,
                symbols=tracked_symbols,
                run_limit=recent_run_limit,
            )
        connection.rollback()
    finally:
        connection.close()

    _validate_export(run=run, candidates=candidates, candidate_limit=candidate_limit)
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
            "selection": "latest_completed_run" if run_date is None else "latest_completed_run_for_date",
        },
        "user_scope": {
            "user_id": user_scope["id"],
            "portfolio_fingerprint": portfolio_fingerprint,
        },
        "radar_run": run,
        "prepared_run": prepared,
        "candidates": candidates,
        "portfolio": {"active_positions": positions},
        "watchlist": watchlist,
        "recent_candidate_history": recent_history,
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
        SELECT id, run_date, market, status, started_at, finished_at,
               universe_count, prefilter_count, candidate_count, errors, created_at
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
        SELECT id, run_date, market, status, symbol_count, market_context,
               step_statuses, errors, created_at, updated_at
        FROM daily_radar_prepared_runs
        WHERE run_date = %s AND market = %s
        LIMIT 1
        """,
        (run_date, market),
    )
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _candidates(cursor: RealDictCursor, *, run_id: int, limit: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, symbol, name, primary_bucket, secondary_buckets,
               observation_score, bucket_scores, risk_labels, matched_rules,
               explanation, repeat_status, score_breakdown, input_snapshot,
               data_dates, created_at
        FROM daily_radar_candidates
        WHERE run_id = %s
        ORDER BY observation_score DESC, symbol ASC
        LIMIT %s
        """,
        (run_id, limit),
    )
    return [dict(row) for row in cursor.fetchall()]


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


def _recent_candidate_history(
    cursor: RealDictCursor,
    *,
    market: str,
    symbols: list[str],
    run_limit: int,
) -> list[dict[str, Any]]:
    if not symbols:
        return []
    cursor.execute(
        """
        WITH recent_runs AS (
            SELECT DISTINCT ON (run_date) id, run_date
            FROM daily_radar_runs
            WHERE market = %s AND status = 'completed'
            ORDER BY run_date DESC, created_at DESC
            LIMIT %s
        )
        SELECT rr.run_date, c.symbol, c.observation_score, c.primary_bucket,
               c.repeat_status, c.risk_labels
        FROM recent_runs rr
        JOIN daily_radar_candidates c ON c.run_id = rr.id
        WHERE c.symbol = ANY(%s)
        ORDER BY rr.run_date DESC, c.observation_score DESC, c.symbol ASC
        """,
        (market, run_limit, symbols),
    )
    return [dict(row) for row in cursor.fetchall()]


def _validate_export(*, run: dict[str, Any], candidates: list[dict[str, Any]], candidate_limit: int) -> None:
    run_date = run.get("run_date")
    if not isinstance(run_date, date) or run_date > date.today():
        raise RuntimeError("Daily Radar run_date is missing or in the future")
    expected = min(int(run.get("candidate_count") or 0), candidate_limit)
    if expected <= 0 or len(candidates) != expected:
        raise RuntimeError(
            f"Candidate export mismatch: expected {expected}, exported {len(candidates)}"
        )
    if len({str(item.get("symbol")) for item in candidates}) != len(candidates):
        raise RuntimeError("Candidate export contains duplicate symbols")


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
    parser = argparse.ArgumentParser(description="Export one production Daily Radar research snapshot read-only.")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--market", default="TW")
    parser.add_argument("--run-date", type=date.fromisoformat, default=None)
    parser.add_argument("--candidate-limit", type=int, default=50)
    parser.add_argument("--recent-run-limit", type=int, default=10)
    parser.add_argument("--db-host", default=os.environ.get("CODEX_RADAR_DB_HOST", DEFAULT_DB_HOST))
    parser.add_argument("--db-port", type=int, default=int(os.environ.get("CODEX_RADAR_DB_PORT", DEFAULT_DB_PORT)))
    parser.add_argument("--db-name", default=os.environ.get("CODEX_RADAR_DB_NAME", DEFAULT_DB_NAME))
    parser.add_argument("--db-user", default=os.environ.get("CODEX_RADAR_DB_USER", DEFAULT_DB_USER))
    parser.add_argument("--sslmode", default=os.environ.get("CODEX_RADAR_DB_SSLMODE", "disable"))
    parser.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args()
    if args.user_id <= 0:
        parser.error("--user-id must be positive")
    if args.candidate_limit <= 0:
        parser.error("--candidate-limit must be positive")
    if args.recent_run_limit <= 0:
        parser.error("--recent-run-limit must be positive")
    return args


if __name__ == "__main__":
    main()

