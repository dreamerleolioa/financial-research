"""backfill tdcc weekly holders v2 payload

Revision ID: f7a8b9c0d1e2
Revises: f5c6d7e8f9a0
Create Date: 2026-06-23 00:00:00.000000

"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "f5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONTEXT_TYPE = "weekly_major_holders"
_HOLDER_LEVEL_SCHEMA_VERSION = "tdcc-holder-level-v2"
_THOUSAND_LOT_LEVELS = frozenset({15})
_LARGE_HOLDER_400_LOT_PLUS_LEVELS = frozenset({12, 13, 14, 15})
_RETAIL_100_LOT_OR_LESS_LEVELS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9})


def upgrade() -> None:
    _backfill_weekly_major_holders_payloads(op.get_bind())


def downgrade() -> None:
    # Data-only compatibility backfill. Do not remove v2 keys because newer
    # provider writes use the same payload contract.
    return None


def _backfill_weekly_major_holders_payloads(bind: sa.engine.Connection) -> None:
    contexts = sa.table(
        "shared_background_contexts",
        sa.column("id", sa.Integer),
        sa.column("context_type", sa.String),
        sa.column("payload", sa.JSON),
    )
    rows = bind.execute(
        sa.select(contexts.c.id, contexts.c.payload).where(contexts.c.context_type == _CONTEXT_TYPE)
    ).mappings()

    for row in rows:
        payload = _payload_mapping(row["payload"])
        if payload is None:
            continue
        updated_payload = _with_holder_level_v2_fields(payload)
        if updated_payload is None or updated_payload == payload:
            continue
        bind.execute(
            sa.update(contexts)
            .where(contexts.c.id == row["id"])
            .values(payload=updated_payload)
        )


def _with_holder_level_v2_fields(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    ratios = _holder_ratios(payload.get("distribution"))
    if ratios is None:
        return None

    updated = dict(payload)
    updated["thousand_lot_holder_ratio"] = ratios["thousand_lot_holder_ratio"]
    updated["large_holder_400_lot_plus_ratio"] = ratios["large_holder_400_lot_plus_ratio"]
    updated["retail_100_lot_or_less_ratio"] = ratios["retail_100_lot_or_less_ratio"]
    updated["holder_level_schema_version"] = _HOLDER_LEVEL_SCHEMA_VERSION
    return updated


def _holder_ratios(distribution: Any) -> dict[str, float | None] | None:
    if not isinstance(distribution, list):
        return None

    items: list[dict[str, float | int | None]] = []
    for item in distribution:
        if not isinstance(item, Mapping):
            return None
        level = _safe_int(item.get("level"))
        if level is None:
            return None
        items.append({"level": level, "ratio": _safe_float(item.get("ratio"))})

    ratios = {
        "thousand_lot_holder_ratio": _sum_ratio(items, _THOUSAND_LOT_LEVELS),
        "large_holder_400_lot_plus_ratio": _sum_ratio(items, _LARGE_HOLDER_400_LOT_PLUS_LEVELS),
        "retail_100_lot_or_less_ratio": _sum_ratio(items, _RETAIL_100_LOT_OR_LESS_LEVELS),
    }
    if all(value is None for value in ratios.values()):
        return None
    return ratios


def _payload_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sum_ratio(items: list[dict[str, float | int | None]], levels: frozenset[int]) -> float | None:
    ratios = [item["ratio"] for item in items if item["level"] in levels and item["ratio"] is not None]
    return round(sum(float(ratio) for ratio in ratios), 4) if ratios else None
