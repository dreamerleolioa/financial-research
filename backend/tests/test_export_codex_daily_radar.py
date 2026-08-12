from __future__ import annotations

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "export_codex_daily_radar.py"
SPEC = importlib.util.spec_from_file_location("export_codex_daily_radar", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def test_stable_hash_is_order_independent_and_preserves_decimal_text() -> None:
    first = {"price": Decimal("123.40"), "symbol": "2330.TW"}
    second = {"symbol": "2330.TW", "price": Decimal("123.40")}

    assert exporter._stable_hash(first) == exporter._stable_hash(second)
    assert exporter._json_default(Decimal("123.40")) == "123.40"


def test_validate_export_accepts_a_complete_limited_candidate_pool() -> None:
    exporter._validate_export(
        run={"run_date": date.today(), "candidate_count": 3},
        candidates=[{"symbol": "1"}, {"symbol": "2"}],
        candidate_limit=2,
    )


def test_validate_export_rejects_count_mismatch() -> None:
    with pytest.raises(RuntimeError, match="Candidate export mismatch"):
        exporter._validate_export(
            run={"run_date": date.today(), "candidate_count": 3},
            candidates=[{"symbol": "1"}, {"symbol": "2"}],
            candidate_limit=3,
        )


def test_validate_export_rejects_duplicate_symbols() -> None:
    with pytest.raises(RuntimeError, match="duplicate symbols"):
        exporter._validate_export(
            run={"run_date": date.today(), "candidate_count": 2},
            candidates=[{"symbol": "1"}, {"symbol": "1"}],
            candidate_limit=2,
        )


def test_validate_export_rejects_future_run() -> None:
    with pytest.raises(RuntimeError, match="in the future"):
        exporter._validate_export(
            run={"run_date": date(2999, 1, 1), "candidate_count": 1},
            candidates=[{"symbol": "1"}],
            candidate_limit=1,
        )

