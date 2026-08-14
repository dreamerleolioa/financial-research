from __future__ import annotations

import importlib.util
import inspect
import stat
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

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


def test_export_rejects_non_tw_market_before_connecting() -> None:
    with pytest.raises(RuntimeError, match="market=TW only"):
        exporter.export_daily_radar(
            user_id=1,
            market="US",
            run_date=None,
            db_host="unused",
            db_port=5432,
            db_name="unused",
            db_user="unused",
            db_password="unused",
            sslmode="disable",
        )


def test_project_raw_universe_row_keeps_neutral_data_and_excludes_radar_scores() -> None:
    projected = exporter._project_raw_universe_row(
        {
            "symbol": "2330.TW",
            "record_date": date.today(),
            "raw_data_is_final": True,
            "technical": {
                "name": "台積電",
                "ohlcv": {"close": 1000},
                "indicators": {"ma20": 980, "rsi14": 55},
                "price_history": [{"date": "2026-08-11", "close": 1000}],
                "technical_profile": {"score_summary": {"technical_score": 67}},
            },
            "institutional": {
                "same_day_net_buy": 123,
                "same_day_rank": 1,
                "flow_label": "institutional_accumulation",
                "scores": {"same_day_institutional": 123},
            },
            "fundamental": {
                "ttm_eps": 40,
                "yield_signal": "high_yield",
            },
        },
        avwap_context={"data_date": date.today(), "payload": {"anchors": {}}},
        background_context=[],
    )

    assert projected["ohlcv"] == {"close": 1000}
    assert projected["indicators"]["ma20"] == 980
    assert projected["institutional"]["same_day_net_buy"] == 123
    assert projected["fundamental"]["ttm_eps"] == 40
    assert projected["price_history"]["point_count"] == 1
    serialized = str(projected)
    for prohibited in (
        "technical_score",
        "same_day_rank",
        "flow_label",
        "same_day_institutional",
        "yield_signal",
        "observation_score",
        "primary_bucket",
    ):
        assert prohibited not in serialized


def test_project_price_history_builds_neutral_horizons() -> None:
    history = [
        {"date": f"2026-07-{index:02d}", "close": 100 + index}
        for index in range(1, 22)
    ]

    projected = exporter._project_price_history(history)

    assert projected["point_count"] == 21
    assert len(projected["recent_closes"]) == 5
    assert projected["horizons"]["5d"]["start_close"] == 116
    assert projected["horizons"]["20d"]["start_close"] == 101
    assert projected["horizons"]["60d"] is None


def test_analytical_completeness_distinguishes_missing_evidence_from_finality() -> None:
    complete = {
        "symbol": "2330.TW",
        "ohlcv": {
            field: 1.0
            for field in ("open", "high", "low", "close", "previous_close", "volume", "avg_volume_20")
        },
        "indicators": {field: 1.0 for field in ("ma20", "atr14", "volume_ratio", "obv")},
        "price_history": {"point_count": 60},
        "institutional": {
            "foreign_net_shares": 1.0,
            "investment_trust_net_shares": 1.0,
            "three_party_net_shares": 1.0,
        },
        "data_dates": {
            "institutional": {"institutional_flow": str(date.today())},
            "fundamental": {"fundamental": str(date.today())},
        },
        "record_date": date.today(),
        "fundamental": {
            "ttm_eps": 10.0,
            "margin": {"margin_delta_pct": 1.0, "margin_to_volume": 0.2},
        },
        "avwap_context": {"anchors": {"event": {"available": True}}},
    }
    incomplete = {"symbol": "2454.TW", "raw_data_is_final": True}

    audit = exporter._analytical_completeness([complete, incomplete])

    assert audit["semantics"] == "analysis_evidence_audit_not_persistence_finality"
    assert audit["eligible_symbol_count"] == 1
    assert "avwap" not in audit["eligibility_required_lanes"]
    assert audit["missing_any_symbol_count"] == 1
    assert audit["lanes"]["technical"]["missing_symbols"] == ["2454.TW"]
    assert audit["lanes"]["fundamental"]["missing_symbols"] == ["2454.TW"]


def test_secure_output_is_restricted_to_private_temp_file() -> None:
    target = Path(tempfile.gettempdir()) / f"codex-radar-test-{uuid4().hex}.json"
    try:
        resolved = exporter._write_secure_output(target, '{"ok":true}')

        assert resolved.read_text() == '{"ok":true}'
        assert stat.S_IMODE(resolved.stat().st_mode) == 0o600
    finally:
        target.unlink(missing_ok=True)


def test_secure_output_rejects_non_temp_parent() -> None:
    with pytest.raises(RuntimeError, match="direct child"):
        exporter._write_secure_output(Path("/var/codex-radar.json"), "{}")


def test_export_source_never_queries_canonical_candidates() -> None:
    source = SCRIPT_PATH.read_text()

    assert "FROM daily_radar_candidates" not in source
    assert "JOIN daily_radar_candidates" not in source


def test_raw_pool_query_is_not_limited_to_prepared_symbols() -> None:
    source = inspect.getsource(exporter._raw_universe_rows)
    prepared_source = inspect.getsource(exporter._prepared_run)

    assert "symbols:" not in source
    assert "ANY(" not in source
    assert "raw_data_is_final = true" in source
    assert "symbol ~" in source
    assert "symbol !~ '^00'" in source
    assert "selected_symbols" not in prepared_source


def test_prepared_anchor_excludes_symbol_membership() -> None:
    projected = exporter._project_prepared_run(
        {
            "id": 7,
            "run_date": date.today(),
            "market": "TW",
            "status": "scored",
            "selected_symbols": ["2330.TW"],
            "symbol_count": 1,
            "errors": [{"symbol": "2330.TW"}],
            "step_statuses": {
                "refresh-ohlcv": {
                    "status": "completed",
                    "symbol_count": 1,
                    "selected_symbol_count": 1,
                }
            },
        }
    )

    assert "selected_symbols" not in projected
    assert "symbol_count" not in projected
    assert "errors" not in projected
    assert projected["error_count"] == 1
    assert "symbol_count" not in projected["step_statuses"]["refresh-ohlcv"]
    assert "selected_symbol_count" not in projected["step_statuses"]["refresh-ohlcv"]


def test_validate_export_accepts_a_valid_date_scoped_raw_pool() -> None:
    today = date.today()

    exporter._validate_export(
        source_run={"run_date": today},
        prepared={"run_date": today},
        raw_universe=[
            {"symbol": "1101.TW", "record_date": today, "raw_data_is_final": True},
            {"symbol": "2330.TW", "record_date": today, "raw_data_is_final": True},
        ],
    )


def test_validate_export_rejects_an_empty_raw_pool() -> None:
    today = date.today()

    with pytest.raises(RuntimeError, match="No final supported Taiwan stock raw rows"):
        exporter._validate_export(
            source_run={"run_date": today},
            prepared={"run_date": today},
            raw_universe=[],
        )


@pytest.mark.parametrize(
    ("raw_universe", "message"),
    [
        (
            [
                {"symbol": "2330.TW", "record_date": date.today(), "raw_data_is_final": True},
                {"symbol": "1101.TW", "record_date": date.today(), "raw_data_is_final": True},
            ],
            "ordered by symbol",
        ),
        (
            [
                {"symbol": "1101.TW", "record_date": date.today(), "raw_data_is_final": False},
                {"symbol": "2330.TW", "record_date": date.today(), "raw_data_is_final": True},
            ],
            "non-final",
        ),
        (
            [
                {"symbol": "1101.TW", "record_date": date(2000, 1, 1), "raw_data_is_final": True},
                {"symbol": "2330.TW", "record_date": date.today(), "raw_data_is_final": True},
            ],
            "mismatched record_date",
        ),
        (
            [
                {"symbol": "0050.TW", "record_date": date.today(), "raw_data_is_final": True},
                {"symbol": "2330.TW", "record_date": date.today(), "raw_data_is_final": True},
            ],
            "unsupported Taiwan stock symbol",
        ),
    ],
)
def test_validate_export_rejects_biased_or_invalid_raw_pool(
    raw_universe: list[dict[str, object]],
    message: str,
) -> None:
    today = date.today()

    with pytest.raises(RuntimeError, match=message):
        exporter._validate_export(
            source_run={"run_date": today},
            prepared={"run_date": today},
            raw_universe=raw_universe,
        )


def test_validate_export_rejects_future_run() -> None:
    with pytest.raises(RuntimeError, match="in the future"):
        exporter._validate_export(
            source_run={"run_date": date(2999, 1, 1)},
            prepared={"run_date": date(2999, 1, 1)},
            raw_universe=[
                {"symbol": "1101.TW", "record_date": date(2999, 1, 1), "raw_data_is_final": True}
            ],
        )
