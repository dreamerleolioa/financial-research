from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ai_stock_sentinel import api
from ai_stock_sentinel.analysis import router as analysis_router
from ai_stock_sentinel.analysis.application.analysis_cache import (
    fetch_and_store_raw_data,
    upsert_analysis_cache,
)
from ai_stock_sentinel.analysis.application.response_builder import indicators_with_position_risk_from_full_result
from ai_stock_sentinel.auth.dependencies import get_current_user
from ai_stock_sentinel.db.session import get_db
from ai_stock_sentinel.models import StockSnapshot

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_SNAPSHOT = StockSnapshot(
    symbol="2330.TW",
    currency="TWD",
    current_price=100.0,
    previous_close=99.0,
    day_open=99.5,
    day_high=101.0,
    day_low=98.5,
    volume=123456,
    recent_closes=[98.0, 99.0, 100.0],
    fetched_at="2026-03-03T00:00:00+00:00",
)

_CLEANED_NEWS = {
    "date": "2026-03-03",
    "title": "台積電 2 月營收年增",
    "mentioned_numbers": ["2,600", "18.2%"],
    "sentiment_label": "positive",
}


def _make_graph(final_state: dict[str, Any]):
    """Build a mock compiled graph that returns *final_state* when invoked."""
    graph = MagicMock()
    graph.invoke.return_value = final_state
    return graph


def _fake_user():
    user = MagicMock()
    user.id = 1
    user.email = "test@example.com"
    user.name = "Test User"
    user.avatar_url = None
    user.is_active = True
    user.deleted_at = None
    return user


def _fake_db():
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    db.execute.return_value.scalar.return_value = 0
    return db


@pytest.fixture(autouse=True)
def _disable_external_symbol_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_router, "_check_symbol_exists", lambda symbol: None)


def _client_with_graph(graph) -> TestClient:
    api.app.dependency_overrides[analysis_router.get_graph] = lambda: graph
    api.app.dependency_overrides[get_current_user] = _fake_user
    api.app.dependency_overrides[get_db] = _fake_db
    return TestClient(api.app)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_endpoint() -> None:
    client = TestClient(api.app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_analyze_returns_snapshot_and_analysis() -> None:
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果：看漲",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"]["symbol"] == "2330.TW"
    assert body["analysis"] == "分析結果：看漲"
    assert body["cleaned_news"] is None
    assert body["errors"] == []


def test_analyze_returns_cleaned_news_when_present() -> None:
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": _CLEANED_NEWS,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post(
        "/analyze",
        json={
            "symbol": "2330.TW",
            "news_text": "2026-03-03 台積電 2 月營收 2,600 億元，年增 18.2%",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cleaned_news"]["sentiment_label"] == "positive"
    assert body["errors"] == []


def test_analyze_raw_news_items_not_exposed_in_response() -> None:
    """raw_news_items is internal graph state and must not appear in the response."""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "raw_news_items": [{"title": "secret"}],
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert "raw_news_items" not in response.json()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_analyze_validation_error_when_symbol_empty() -> None:
    client = TestClient(api.app)
    response = client.post("/analyze", json={"symbol": ""})

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_analyze_graph_exception_returns_error_code() -> None:
    graph = MagicMock()
    graph.invoke.side_effect = RuntimeError("yfinance data source temporarily unavailable")
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] == {}
    assert body["analysis"] == ""
    assert body["errors"][0]["code"] == "ANALYZE_RUNTIME_ERROR"


def test_analyze_missing_snapshot_returns_traceable_error() -> None:
    graph = _make_graph(
        {
            "snapshot": None,
            "analysis": None,
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    error_codes = {item["code"] for item in body["errors"]}
    assert "MISSING_SNAPSHOT" in error_codes
    assert "MISSING_ANALYSIS" in error_codes


def test_analyze_graph_errors_propagated_to_response() -> None:
    """Errors accumulated during graph execution are included in response."""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [{"code": "RSS_FETCH_ERROR", "message": "timeout"}],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    error_codes = {item["code"] for item in body["errors"]}
    assert "RSS_FETCH_ERROR" in error_codes


def test_analyze_response_includes_analysis_detail() -> None:
    """AnalyzeResponse 必須包含 analysis_detail 欄位（含 summary/risks/technical_signal）。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "台積電股價穩定",
            "analysis_detail": {
                "summary": "台積電股價穩定，技術面偏多。",
                "risks": ["外資動向不確定", "匯率風險"],
                "technical_signal": "bullish",
            },
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert "analysis_detail" in body, "Missing 'analysis_detail' in response"
    detail = body["analysis_detail"]
    assert detail["summary"] == "台積電股價穩定，技術面偏多。"
    assert detail["risks"] == ["外資動向不確定", "匯率風險"]
    assert detail["technical_signal"] == "bullish"


def test_analyze_response_includes_shared_context_without_passing_it_to_graph(monkeypatch) -> None:
    """Shared context is response evidence only, not graph/LLM input."""
    import ai_stock_sentinel.analysis.router as api_module

    monkeypatch.setattr(
        api_module,
        "_read_shared_context_for_symbol",
        lambda db, *, symbol, consumer: {
            "version": "shared-context-read-v1",
            "symbol": symbol,
            "consumer": consumer,
            "contexts": [
                {
                    "context_type": "weekly_major_holders",
                    "source": {"domain": "background_context", "provider": "fixture"},
                    "as_of_date": "2026-06-07",
                    "freshness": "fresh",
                    "missing_reason": None,
                    "replay_key": "background_context:2330.TW:weekly_major_holders:2026-06-07",
                    "applicable_consumers": ["analyze"],
                    "payload": {"major_holder_ratio": 0.61},
                }
            ],
            "caveats": [
                {
                    "context_type": "weekly_major_holders",
                    "label": "大戶持股集中背景",
                    "source": {"domain": "background_context", "provider": "fixture"},
                    "as_of_date": "2026-06-07",
                    "freshness": "fresh",
                    "missing_reason": None,
                    "replay_key": "background_context:2330.TW:weekly_major_holders:2026-06-07",
                    "applicable_consumers": ["analyze"],
                }
            ],
            "data_quality": {
                "status": "fresh",
                "freshness_counts": {"fresh": 1, "stale": 0, "missing": 0, "unknown": 0},
                "missing_reasons": [],
                "blocking": False,
            },
        },
    )
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["shared_context"]["version"] == "shared-context-read-v1"
    assert body["shared_context"]["consumer"] == "analyze"
    assert body["shared_context"]["caveats"][0]["context_type"] == "weekly_major_holders"
    graph_input = graph.invoke.call_args.args[0]
    assert "shared_context" not in graph_input
    assert "background_context" not in graph_input


def test_analyze_response_includes_chip_stability_context_without_passing_it_to_graph(monkeypatch) -> None:
    """TDCC chip stability is response evidence only, not graph/LLM input or technical score."""
    import ai_stock_sentinel.analysis.router as api_module

    monkeypatch.setattr(api_module, "_read_shared_context_for_symbol", lambda *a, **kw: None)
    monkeypatch.setattr(
        api_module,
        "weekly_major_holders_projection_by_symbol",
        lambda db, *, symbols, consumer, reference_date: {
            symbols[0]: {
                "status": "fresh",
                "as_of_date": "2026-06-13",
                "previous_as_of_date": "2026-06-06",
                "thousand_lot_holder_ratio": 38.2,
                "thousand_lot_holder_ratio_delta_pp": 1.52,
                "consecutive_thousand_lot_holder_ratio_increase_count": 1,
            }
        },
    )
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["chip_stability_context"] == {
        "source": "tdcc_weekly_major_holders",
        "status": "fresh",
        "as_of_date": "2026-06-13",
        "previous_as_of_date": "2026-06-06",
        "thousand_lot_holder_ratio": 38.2,
        "thousand_lot_holder_ratio_delta_pp": 1.52,
        "state": "stable",
        "trend": "improving",
        "summary": "千張大戶持股比例增加，籌碼穩定性提升。",
        "caveats": [
            {
                "code": "weekly_chip_stability_companion_only",
                "message": "TDCC 週頻籌碼穩定性補充，不納入 technical score、Daily Radar ranking 或 portfolio risk 分數。",
            }
        ],
    }
    graph_input = graph.invoke.call_args.args[0]
    assert "chip_stability_context" not in graph_input
    assert "technical_profile" not in body


def test_analyze_response_includes_phase1_observation_without_passing_it_to_graph(monkeypatch) -> None:
    """Phase 1 AVWAP projection is response evidence only, not graph/LLM input."""
    import ai_stock_sentinel.analysis.router as api_module

    monkeypatch.setattr(api_module, "_read_shared_context_for_symbol", lambda *a, **kw: None)
    monkeypatch.setattr(
        api_module,
        "_read_phase1_observation_for_analyze",
        lambda db, *, user_id, symbol, data_date, current_price=None: {
            "symbol": symbol,
            "data_date": data_date.isoformat(),
            "freshness": "fresh",
            "missing_reason": None,
            "anchors": {"swing_low_60d": {"avwap": 900.25}},
            "data_quality": {"estimated": False, "missing_reason": None},
        },
    )
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["phase1_observation"]["symbol"] == "2330.TW"
    assert body["phase1_observation"]["freshness"] == "fresh"
    assert body["phase1_observation"]["anchors"]["swing_low_60d"]["avwap"] == 900.25
    graph_input = graph.invoke.call_args.args[0]
    assert "phase1_observation" not in graph_input
    assert "phase1_avwap" not in graph_input


def test_analyze_phase1_observation_read_failure_is_nonblocking(monkeypatch) -> None:
    import ai_stock_sentinel.phase1_avwap.projection as projection_module

    monkeypatch.setattr(analysis_router, "_read_shared_context_for_symbol", lambda *a, **kw: None)

    def _raise(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(projection_module, "resolve_phase1_managed_universe", _raise)
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["analysis"] == "分析結果"
    assert body["phase1_observation"]["freshness"] == "missing"
    assert body["phase1_observation"]["missing_reason"] == "phase1_snapshot_read_failed"
    assert body["phase1_observation"]["data_quality"]["blocking"] is False


def test_analyze_response_includes_strategy_fields() -> None:
    """AnalyzeResponse 必須包含 strategy/confidence 欄位（值可為 None）。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
            "confidence_score": 65,
            "cross_validation_note": "三維共振，信心偏高",
            "strategy_type": "mid_term",
            "entry_zone": "現價附近分批買進",
            "stop_loss": "近20日低點 - 3% 或跌破 MA60",
            "holding_period": "1-3 個月",
            "action_plan_tag": "opportunity",
            "action_plan": {
                "action": "分批佈局（首筆 20-30%）",
                "defense_line": "近20日低點 - 3% 或跌破 MA60",
                "thesis_points": ["法人籌碼偏多"],
                "invalidation_conditions": ["跌破 MA20"],
            },
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    for field in ["confidence_score", "cross_validation_note", "strategy_type",
                  "entry_zone", "stop_loss", "holding_period",
                  "risk_state", "risk_state_label", "discipline_triggers",
                  "observation_conditions", "risk_control_reference",
                  "command_language_deprecated"]:
        assert field in body, f"Missing field '{field}' in response"
    assert body["confidence_score"] == 65
    assert body["strategy_type"] == "mid_term"
    assert body["risk_state"] == "setup_observation"
    assert body["risk_state_label"] == "可觀察 setup"
    assert body["observation_conditions"] == ["法人籌碼偏多"]
    assert body["discipline_triggers"] == ["跌破 MA20"]
    assert body["risk_control_reference"]["reference_type"] == "setup_risk_control_reference"
    assert body["command_language_deprecated"]["action_plan_action"] == "分批佈局（首筆 20-30%）"


def test_analyze_response_includes_extended_technical_indicators() -> None:
    """AnalyzeResponse technical_indicators 應包含 KD、ADX、OBV 欄位。"""
    closes = [100.0 + idx * 0.5 for idx in range(40)]
    snapshot = asdict(_SNAPSHOT)
    snapshot.update({
        "current_price": closes[-1],
        "recent_closes": closes,
        "recent_highs": [price + 1.0 for price in closes],
        "recent_lows": [price - 1.0 for price in closes],
        "recent_volumes": [1000 + idx * 10 for idx in range(40)],
    })
    graph = _make_graph(
        {
            "snapshot": snapshot,
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    indicators = response.json()["technical_indicators"]
    assert indicators["ma5"] is not None
    assert indicators["ma20"] is not None
    assert indicators["ma60"] is None
    assert indicators["high_20d"] is not None
    assert indicators["low_20d"] is not None
    assert indicators["high_60d"] is None
    assert indicators["low_60d"] is None
    assert indicators["kd_k"] is not None
    assert indicators["kd_d"] is not None
    assert indicators["kd_signal"] in {"bullish_cross", "bearish_cross", "neutral"}
    assert indicators["kd_zone"] in {"oversold", "overbought", "neutral"}
    assert indicators["adx"] is not None
    assert indicators["adx_trend_strength"] in {"strong", "neutral", "weak"}
    assert indicators["adx_trend_direction"] in {"bullish", "bearish", "neutral"}
    assert indicators["obv"] is not None
    assert indicators["obv_signal"] in {
        "price_volume_confirm",
        "bearish_divergence",
        "bullish_divergence",
        "price_volume_weak",
        "neutral",
    }
    assert indicators["obv_trend_20d"] in {"rising", "falling", "flat"}
    assert indicators["obv_trend_mid_long"] is None
    assert indicators["obv_trend_mid_long_window"] is None


# ---------------------------------------------------------------------------
# NQ-5: cleaned_news_quality in API response
# ---------------------------------------------------------------------------


def test_analyze_response_has_cleaned_news_quality_field() -> None:
    """AnalyzeResponse 必須包含 cleaned_news_quality 欄位（值可為 None）。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert "cleaned_news_quality" in body, "Missing 'cleaned_news_quality' in response"


def test_analyze_response_cleaned_news_quality_contains_score_and_flags() -> None:
    """當 cleaned_news_quality 非 None 時，必須包含 quality_score 與 quality_flags。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "cleaned_news_quality": {
                "quality_score": 65,
                "quality_flags": ["DATE_UNKNOWN"],
            },
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    quality = body["cleaned_news_quality"]
    assert quality is not None
    assert quality["quality_score"] == 65
    assert quality["quality_flags"] == ["DATE_UNKNOWN"]


def test_analyze_response_cleaned_news_quality_none_when_absent() -> None:
    """graph 未回傳 cleaned_news_quality 時，欄位應為 None。"""
    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "cleaned_news": None,
            "errors": [],
        }
    )
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["cleaned_news_quality"] is None


def test_analyze_response_has_news_display_field() -> None:
    """AnalyzeResponse 必須包含 news_display 欄位（值可為 None）。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert "news_display" in response.json()


def test_analyze_response_news_display_contains_expected_fields() -> None:
    """news_display 非 None 時，應包含 title、date、source_url。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "news_display": {
            "title": "台積電 Q1 法說會",
            "date": "2026-03-05",
            "source_url": "https://example.com/news/1",
        },
        "errors": [],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    display = body["news_display"]
    assert display["title"] == "台積電 Q1 法說會"
    assert display["date"] == "2026-03-05"
    assert display["source_url"] == "https://example.com/news/1"


# ---------------------------------------------------------------------------
# Session 2: action_plan_tag & institutional_flow_label
# ---------------------------------------------------------------------------


def test_api_response_includes_action_plan_tag_field() -> None:
    """AnalyzeResponse 必須包含 action_plan_tag 欄位。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "action_plan_tag": "opportunity",
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert "action_plan_tag" in body
    assert body["action_plan_tag"] == "opportunity"


def test_api_response_action_plan_tag_none_when_absent() -> None:
    """graph 未回傳 action_plan_tag 時，欄位應為 None。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert response.json()["action_plan_tag"] is None


def test_api_response_includes_institutional_flow_label_field() -> None:
    """institutional_flow 有 flow_label 且無 error 時，institutional_flow_label 應浮出。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": {
            "flow_label": "institutional_accumulation",
            "foreign_buy": 1000.0,
        },
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert "institutional_flow_label" in body
    assert body["institutional_flow_label"] == "institutional_accumulation"


def test_api_response_institutional_flow_label_none_when_error() -> None:
    """institutional_flow 含 error 欄位時，institutional_flow_label 應為 None。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": {
            "error": "INSTITUTIONAL_FETCH_ERROR",
        },
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert response.json()["institutional_flow_label"] is None


# ---------------------------------------------------------------------------
# Session 3: sentiment_label, action_plan, data_sources
# ---------------------------------------------------------------------------


def test_api_response_includes_sentiment_label_from_cleaned_news() -> None:
    """cleaned_news 有 sentiment_label 時，AnalyzeResponse.sentiment_label 應浮出。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": _CLEANED_NEWS,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": None,
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert response.json()["sentiment_label"] == "positive"


def test_api_response_sentiment_label_is_none_when_cleaned_news_absent() -> None:
    """cleaned_news 為 None 時，sentiment_label 應為 None。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": None,
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert response.json()["sentiment_label"] is None


def test_api_response_includes_action_plan_dict() -> None:
    """action_plan 在 state 有值時，AnalyzeResponse.action_plan 應回傳 dict。"""
    _action_plan = {
        "action": "觀望",
        "target_zone": "現價附近",
        "defense_line": "890",
        "momentum_expectation": "中性",
    }
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": None,
        "action_plan": _action_plan,
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    assert body["action_plan"] == _action_plan


def test_api_response_data_sources_includes_yfinance_when_snapshot_present() -> None:
    """snapshot 有值時，data_sources 應包含 yfinance。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": None,
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert "yfinance" in response.json()["data_sources"]


def test_api_response_data_sources_includes_google_news_rss_when_raw_news_present() -> None:
    """raw_news_items 有值時，data_sources 應包含 google-news-rss。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
        "institutional_flow": None,
        "raw_news_items": [{"title": "新聞一", "url": None}],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert "google-news-rss" in response.json()["data_sources"]


# ---------------------------------------------------------------------------
# Task 5: POST /analyze/position
# ---------------------------------------------------------------------------

_POSITION_SNAPSHOT = StockSnapshot(
    symbol="2330.TW",
    currency="TWD",
    current_price=1050.0,
    previous_close=1040.0,
    day_open=1040.0,
    day_high=1060.0,
    day_low=1035.0,
    volume=5000000,
    recent_closes=[1000.0, 1010.0, 1020.0, 1030.0, 1040.0, 1050.0],
    fetched_at="2026-03-10T00:00:00+00:00",
)

_POSITION_FINAL_STATE = {
    "snapshot": asdict(_POSITION_SNAPSHOT),
    "analysis": "持倉診斷結果",
    "analysis_detail": {
        "summary": "持倉安全",
        "risks": [],
        "technical_signal": "bullish",
        "institutional_flow": "accumulation",
        "sentiment_label": "positive",
        "tech_insight": "多頭排列",
        "inst_insight": "法人買超",
        "news_insight": "無重大利空",
        "final_verdict": "建議繼續持有",
        "fundamental_insight": None,
    },
    "entry_price": 980.0,
    "profit_loss_pct": 7.14,
    "position_status": "profitable_safe",
    "position_narrative": "目前獲利已脫離成本區，持股安全緩衝充足。",
    "trailing_stop": 980.0,
    "trailing_stop_reason": "獲利超過 5%，停損位上移至成本價保本",
    "recommended_action": "Hold",
    "exit_reason": None,
    "distance_to_trailing_stop_pct": 7.14,
    "distance_to_support_pct": 9.38,
    "unrealized_pnl": None,
    "holding_days": None,
    "cleaned_news": None,
    "errors": [],
    "confidence_score": 70,
    "institutional_flow": {"flow_label": "accumulation"},
}

_DISTRIBUTION_POSITION_FINAL_STATE = {
    **_POSITION_FINAL_STATE,
    "entry_price": 800.0,
    "profit_loss_pct": 31.25,
    "position_status": "profitable_safe",
    "recommended_action": "Trim",
    "exit_reason": "法人持續出貨，建議逢高分批減碼保護獲利",
    "institutional_flow": {"flow_label": "distribution"},
}


def test_analyze_position_returns_position_analysis_block() -> None:
    """The /analyze/position endpoint must return a position_analysis object."""
    graph = _make_graph(_POSITION_FINAL_STATE)
    client = _client_with_graph(graph)

    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 980.0,
    })
    assert response.status_code == 200
    body = response.json()
    assert "position_analysis" in body
    pa = body["position_analysis"]
    assert "entry_price" in pa
    assert "profit_loss_pct" in pa
    assert "position_status" in pa
    assert "trailing_stop" in pa
    assert "recommended_action" in pa
    assert pa["recommended_action"] in ("Hold", "Trim", "Exit")
    assert pa["risk_state"] == "stable"
    assert pa["risk_state_label"] == "風險狀態穩定"
    assert pa["risk_control_reference"]["reference_type"] == "dynamic_defense_reference"
    assert "recommended_action" in pa["command_language_deprecated"]


def test_position_cache_full_result_can_seed_history_risk_language_snapshot() -> None:
    indicators = indicators_with_position_risk_from_full_result(
        {"close_price": 1100.0},
        {
            "position_analysis": {
                "risk_state": "elevated",
                "risk_state_label": "風險狀態升高",
                "discipline_triggers": ["收盤跌破風險控制參考價"],
                "observation_conditions": ["量價仍需觀察"],
                "risk_control_reference": {"reference_price": 980.0},
            },
        },
    )

    assert indicators["close_price"] == 1100.0
    assert indicators["position_risk_language"] == {
        "risk_state": "elevated",
        "risk_state_label": "風險狀態升高",
        "discipline_triggers": ["收盤跌破風險控制參考價"],
        "observation_conditions": ["量價仍需觀察"],
        "risk_control_reference": {"reference_price": 980.0},
    }


def test_analyze_position_entry_price_required() -> None:
    """entry_price is required for /analyze/position."""
    client = TestClient(api.app)
    response = client.post("/analyze/position", json={"symbol": "2330.TW"})
    assert response.status_code == 422


def test_analyze_position_optional_fields_accepted() -> None:
    """entry_date and quantity are accepted but optional."""
    graph = _make_graph(_POSITION_FINAL_STATE)
    client = _client_with_graph(graph)

    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 980.0,
        "entry_date": "2026-01-15",
        "quantity": 1000,
    })
    assert response.status_code == 200


def test_analyze_position_exit_reason_not_null_when_distribution_profit() -> None:
    """Spec §7: exit_reason must not be null when flow=distribution and profit>0."""
    graph = _make_graph(_DISTRIBUTION_POSITION_FINAL_STATE)
    client = _client_with_graph(graph)

    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 800.0,
    })
    assert response.status_code == 200
    body = response.json()
    pa = body["position_analysis"]
    if pa["recommended_action"] in ("Trim", "Exit"):
        assert pa["exit_reason"] is not None
        assert pa["risk_state"] in {"elevated", "critical"}
        assert pa["discipline_triggers"]
        assert "exit_reason" in pa["command_language_deprecated"]


def test_analyze_position_shared_context_does_not_override_rule_based_fields(monkeypatch) -> None:
    """Shared context can surface caveats but must not change deterministic position fields."""
    import ai_stock_sentinel.analysis.router as api_module

    monkeypatch.setattr(
        api_module,
        "_read_shared_context_for_symbol",
        lambda db, *, symbol, consumer: {
            "version": "shared-context-read-v1",
            "symbol": symbol,
            "consumer": consumer,
            "contexts": [
                {
                    "context_type": "lending",
                    "source": {"domain": "background_context", "provider": "fixture"},
                    "as_of_date": "2026-06-07",
                    "freshness": "fresh",
                    "missing_reason": None,
                    "replay_key": "background_context:2330.TW:lending:2026-06-07",
                    "applicable_consumers": ["position_analysis"],
                    "payload": {"short_pressure": "elevated"},
                }
            ],
            "caveats": [
                {
                    "context_type": "lending",
                    "label": "借券空方壓力背景",
                    "source": {"domain": "background_context", "provider": "fixture"},
                    "as_of_date": "2026-06-07",
                    "freshness": "fresh",
                    "missing_reason": None,
                    "replay_key": "background_context:2330.TW:lending:2026-06-07",
                    "applicable_consumers": ["position_analysis"],
                }
            ],
            "data_quality": {
                "status": "fresh",
                "freshness_counts": {"fresh": 1, "stale": 0, "missing": 0, "unknown": 0},
                "missing_reasons": [],
                "blocking": False,
            },
        },
    )
    graph = _make_graph(_POSITION_FINAL_STATE)
    client = _client_with_graph(graph)

    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 980.0,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["shared_context"]["consumer"] == "position_analysis"
    assert body["shared_context"]["caveats"][0]["context_type"] == "lending"
    pa = body["position_analysis"]
    assert pa["recommended_action"] == _POSITION_FINAL_STATE["recommended_action"]
    assert pa["trailing_stop"] == _POSITION_FINAL_STATE["trailing_stop"]
    assert pa["exit_reason"] == _POSITION_FINAL_STATE["exit_reason"]
    graph_input = graph.invoke.call_args.args[0]
    assert "shared_context" not in graph_input
    assert "background_context" not in graph_input


def test_analyze_position_cache_hit_requires_same_entry_price(monkeypatch) -> None:
    """Position cache must not reuse a previous result with a different cost basis."""
    import ai_stock_sentinel.analysis.router as api_module
    from ai_stock_sentinel.config import STRATEGY_VERSION

    cache = MagicMock()
    cache.symbol = "2330.TW"
    cache.analysis_is_final = True
    cache.strategy_version = STRATEGY_VERSION
    cache.action_tag = "opportunity"
    cache.signal_confidence = 70
    cache.recommended_action = "Hold"
    cache.final_verdict = "舊持股診斷"
    cache.full_result = {
        **_POSITION_FINAL_STATE,
        "position_analysis": {
            "entry_price": 980.0,
            "recommended_action": "Hold",
        },
        "is_final": True,
        "errors": [],
    }

    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: cache)
    monkeypatch.setattr(api_module, "_check_symbol_exists", lambda symbol: None)
    monkeypatch.setattr(api_module, "backfill_yesterday_indicators", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "load_yesterday_context", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_cache", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "fetch_and_store_raw_data", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)

    graph = _make_graph({**_POSITION_FINAL_STATE, "entry_price": 900.0})
    client = _client_with_graph(graph)
    response = client.post("/analyze/position", json={"symbol": "2330.TW", "entry_price": 900.0})

    assert response.status_code == 200
    assert graph.invoke.called
    assert response.json()["position_analysis"]["entry_price"] == 900.0


def test_analyze_position_cache_hit_reuses_same_position_request(monkeypatch) -> None:
    """A position cache hit is valid only when entry price/date/quantity match."""
    import ai_stock_sentinel.analysis.router as api_module
    from ai_stock_sentinel.config import STRATEGY_VERSION

    full_result = {
        **_POSITION_FINAL_STATE,
        "position_analysis": {
            "entry_price": 980.0,
            "recommended_action": "Hold",
        },
        "_position_request": {
            "entry_price": 980.0,
            "entry_date": "2026-01-15",
            "quantity": 1000,
        },
        "is_final": True,
        "errors": [],
    }
    cache = MagicMock()
    cache.symbol = "2330.TW"
    cache.analysis_is_final = True
    cache.strategy_version = STRATEGY_VERSION
    cache.action_tag = "opportunity"
    cache.signal_confidence = 70
    cache.recommended_action = "Hold"
    cache.final_verdict = "持股診斷"
    cache.full_result = full_result

    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: cache)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)

    graph = _make_graph({})
    client = _client_with_graph(graph)
    response = client.post("/analyze/position", json={
        "symbol": "2330.TW",
        "entry_price": 980.0,
        "entry_date": "2026-01-15",
        "quantity": 1000,
    })

    assert response.status_code == 200
    assert not graph.invoke.called
    assert response.json()["position_analysis"]["entry_price"] == 980.0


def test_shared_context_read_reports_missing_and_stale_without_blocking(monkeypatch) -> None:
    import ai_stock_sentinel.shared_context as shared_context_module

    monkeypatch.setattr(
        shared_context_module,
        "get_shared_background_context_trace_by_symbol",
        lambda db, *, symbols, consumer, **_kwargs: {
            symbols[0]: [
                {
                    "context_type": "weekly_major_holders",
                    "source": {"domain": "background_context", "provider": "fixture"},
                    "as_of_date": "2026-05-31",
                    "freshness": "stale",
                    "missing_reason": None,
                    "replay_key": "background_context:2330.TW:weekly_major_holders:2026-05-31",
                    "applicable_consumers": [consumer],
                    "payload": {"major_holder_ratio": 0.61},
                },
                {
                    "context_type": "full_margin",
                    "source": {
                        "domain": "background_context",
                        "provider": "shared_background_context_cache",
                        "status": "cache_miss",
                    },
                    "as_of_date": None,
                    "freshness": "missing",
                    "missing_reason": "context_cache_missing",
                    "replay_key": "background_context:2330.TW:full_margin:missing",
                    "applicable_consumers": [consumer],
                    "payload": {},
                },
            ]
        },
    )

    payload = shared_context_module.read_shared_context_for_symbol(
        MagicMock(),
        symbol="2330.TW",
        consumer="analyze",
    )

    assert payload["consumer"] == "analyze"
    assert payload["data_quality"]["status"] == "partial"
    assert payload["data_quality"]["blocking"] is False
    assert payload["data_quality"]["freshness_counts"]["stale"] == 1
    assert payload["data_quality"]["freshness_counts"]["missing"] == 1
    assert payload["data_quality"]["missing_reasons"] == ["context_cache_missing"]
    assert payload["caveats"][1]["missing_reason"] == "context_cache_missing"


def test_shared_context_read_failure_returns_nonblocking_caveat(monkeypatch) -> None:
    import ai_stock_sentinel.shared_context as shared_context_module

    def _raise(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(shared_context_module, "get_shared_background_context_trace_by_symbol", _raise)

    payload = shared_context_module.read_shared_context_for_symbol(
        MagicMock(),
        symbol="2330.TW",
        consumer="position_analysis",
    )

    assert payload["consumer"] == "position_analysis"
    assert payload["data_quality"]["status"] == "missing"
    assert payload["data_quality"]["blocking"] is False
    assert payload["caveats"][0]["missing_reason"] == "context_cache_read_failed"


# ---------------------------------------------------------------------------
# Full result cache persistence
# ---------------------------------------------------------------------------

def test_upsert_analysis_cache_stores_full_result() -> None:
    """upsert_analysis_cache should persist full_result when provided."""
    from unittest.mock import MagicMock

    db = MagicMock()
    data = {
        "symbol": "2330.TW",
        "signal_confidence": 55,
        "action_tag": "neutral",
        "recommended_action": "觀望",
        "indicators": {},
        "final_verdict": "分析結果",
        "is_final": False,
        "full_result": {"snapshot": {"symbol": "2330.TW"}, "analysis": "分析結果"},
    }

    upsert_analysis_cache(db, data)

    db.execute.assert_called_once()
    call_kwargs = db.execute.call_args
    params = call_kwargs[0][1]  # second positional arg is the params dict
    assert "full_result" in params
    assert params["full_result"] is not None


def _stored_technical_from_fetch_and_store(db: MagicMock) -> dict[str, Any]:
    params = db.execute.call_args[0][1]
    return json.loads(params["technical"])


def test_fetch_and_store_raw_data_adds_ohlcv_for_snapshot_technical() -> None:
    db = MagicMock()
    technical = {
        "current_price": 104.0,
        "day_open": 101.0,
        "recent_closes": [100.0, 102.0, 104.0],
        "recent_highs": [101.0, 103.0, 105.0],
        "recent_lows": [99.0, 101.0, 103.0],
        "recent_volumes": [1000, 1200, 1500],
    }

    fetch_and_store_raw_data(db, "2330.TW", technical=technical, institutional={}, fundamental={})

    stored = _stored_technical_from_fetch_and_store(db)
    assert stored["recent_closes"] == technical["recent_closes"]
    assert stored["ohlcv"] == {
        "open": 101.0,
        "high": 105.0,
        "low": 103.0,
        "close": 104.0,
        "volume": 1500.0,
    }
    assert "ohlcv" not in technical


def test_fetch_and_store_raw_data_preserves_existing_ohlcv() -> None:
    db = MagicMock()
    technical = {
        "current_price": 104.0,
        "recent_closes": [100.0, 102.0, 104.0],
        "ohlcv": {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
    }

    fetch_and_store_raw_data(db, "2330.TW", technical=technical, institutional={}, fundamental={})

    stored = _stored_technical_from_fetch_and_store(db)
    assert stored["ohlcv"] == technical["ohlcv"]


def test_fetch_and_store_raw_data_uses_volume_when_recent_volumes_missing() -> None:
    db = MagicMock()
    technical = {
        "current_price": 104.0,
        "day_open": 101.0,
        "volume": 9000,
        "recent_closes": [100.0, 102.0],
        "recent_highs": [101.0, 103.0],
        "recent_lows": [99.0, 101.0],
    }

    fetch_and_store_raw_data(db, "2330.TW", technical=technical, institutional={}, fundamental={})

    stored = _stored_technical_from_fetch_and_store(db)
    assert stored["ohlcv"] == {
        "open": 101.0,
        "high": 103.0,
        "low": 101.0,
        "close": 104.0,
        "volume": 9000.0,
    }


def test_cache_hit_returns_full_result_fields(monkeypatch) -> None:
    """When cache has full_result, /analyze should return all fields."""
    from unittest.mock import MagicMock
    import ai_stock_sentinel.analysis.router as api_module
    from ai_stock_sentinel.db.session import get_db

    full = {
        "snapshot": {"symbol": "2330.TW", "current_price": 1865.0},
        "analysis": "完整分析內容",
        "signal_confidence": 37,
        "action_plan_tag": "neutral",
        "news_display_items": [{"title": "新聞標題"}],
        "fundamental_data": {"pe_ratio": 28.1},
        "is_final": False,
        "intraday_disclaimer": None,
        "errors": [],
    }

    from ai_stock_sentinel.config import STRATEGY_VERSION

    cache = MagicMock()
    cache.symbol = "2330.TW"
    cache.is_final = True
    cache.full_result = full
    cache.signal_confidence = 37
    cache.action_tag = "neutral"
    cache.recommended_action = None
    cache.final_verdict = "完整分析內容"
    cache.indicators = {}
    cache.analysis_is_final = True
    cache.strategy_version = STRATEGY_VERSION

    monkeypatch.setattr(api_module, "get_analysis_cache", lambda db, symbol, analysis_type="general": cache)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "resolve_symbol_name", lambda symbol: "台積電" if symbol == "2330.TW" else None)

    fake_db = MagicMock()
    api.app.dependency_overrides[get_db] = lambda: fake_db

    graph = _make_graph({})  # should not be invoked
    client = _client_with_graph(graph)
    response = client.post("/analyze", json={"symbol": "2330.TW"})

    api.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["analysis"] == "完整分析內容"
    assert body["fundamental_data"] == {"pe_ratio": 28.1}
    assert body["news_display_items"] == [{"title": "新聞標題"}]
    assert body["snapshot"]["symbol"] == "2330.TW"
    assert body["symbol_name"] == "台積電"
    assert body["snapshot"]["name"] == "台積電"


def test_cache_hit_replaces_symbol_placeholder_name(monkeypatch) -> None:
    """Old cache rows may store the symbol itself as the display name."""
    from unittest.mock import MagicMock
    import ai_stock_sentinel.analysis.router as api_module

    full = {
        "snapshot": {"symbol": "2330.TW", "name": "2330.TW", "current_price": 1865.0},
        "symbol_name": "2330.TW",
        "analysis": "完整分析內容",
        "news_display_items": [{"title": "新聞標題"}],
        "is_final": True,
        "intraday_disclaimer": None,
        "errors": [],
    }
    cache = MagicMock()
    cache.is_final = True
    cache.intraday_disclaimer = None
    cache.strategy_version = "strategy-v1"
    cache.final_verdict = "完整分析內容"
    cache.signal_confidence = None
    cache.action_tag = None

    monkeypatch.setattr(api_module, "resolve_symbol_name", lambda symbol: "台積電" if symbol == "2330.TW" else None)

    response = api_module._build_response_from_cache(cache, "2330.TW", full_result=full)

    assert response.symbol_name == "台積電"
    assert response.snapshot["name"] == "台積電"


def test_analyze_cache_is_called_with_full_result(monkeypatch) -> None:
    """POST /analyze should pass full_result to upsert_analysis_cache."""
    import ai_stock_sentinel.analysis.router as api_module
    from ai_stock_sentinel.db.session import get_db

    graph = _make_graph(
        {
            "snapshot": {"symbol": "2330.TW", "current_price": 100.0},
            "analysis": "分析結果",
            "signal_confidence": 55,
            "action_plan_tag": "neutral",
            "errors": [],
        }
    )

    captured = {}

    def fake_upsert(db, data):
        captured["data"] = data

    monkeypatch.setattr(api_module, "upsert_analysis_cache", fake_upsert)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: None)

    fake_db = MagicMock()
    api.app.dependency_overrides[get_db] = lambda: fake_db
    client = _client_with_graph(graph)
    client.post("/analyze", json={"symbol": "2330.TW"})
    api.app.dependency_overrides.pop(get_db, None)

    assert "full_result" in captured.get("data", {})
    full = captured["data"]["full_result"]
    assert full.get("analysis") == "分析結果"
    assert full.get("snapshot", {}).get("symbol") == "2330.TW"


def test_analyze_general_endpoint_reads_only_general_cache(monkeypatch) -> None:
    """POST /analyze must not accidentally read the position-analysis cache."""
    import ai_stock_sentinel.analysis.router as api_module

    calls: list[tuple[str, str]] = []

    def fake_get_analysis_cache(db: object, symbol: str, analysis_type: str = "general") -> None:
        calls.append((symbol, analysis_type))
        return None

    monkeypatch.setattr(api_module, "get_analysis_cache", fake_get_analysis_cache)
    monkeypatch.setattr(api_module, "backfill_yesterday_indicators", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "load_yesterday_context", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_cache", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "fetch_and_store_raw_data", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "_read_shared_context_for_symbol", lambda *a, **kw: None)

    graph = _make_graph(
        {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "一般分析結果",
            "errors": [],
        }
    )
    client = _client_with_graph(graph)
    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert calls == [("2330.TW", "general")]


def test_analyze_skip_ai_uses_recent_raw_cache_without_symbol_check_or_rewrite(monkeypatch) -> None:
    """skip_ai quick lookup should reuse recent raw data and avoid external validation/fetch writes."""
    import ai_stock_sentinel.analysis.router as api_module

    cached_snapshot = asdict(_SNAPSHOT) | {"name": "台積電"}
    cached_institutional = {"flow_state": "foreign_buying"}
    cached_fundamental = {"pe_ratio": 28.1}
    raw_cache = MagicMock()
    raw_cache.technical = cached_snapshot
    raw_cache.institutional = cached_institutional
    raw_cache.fundamental = cached_fundamental
    raw_cache.fetched_at = "2026-06-18T09:01:00+08:00"

    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: pytest.fail("skip_ai must bypass L1 analysis cache"))
    monkeypatch.setattr(api_module, "get_recent_raw_data", lambda db, symbol, max_age_seconds=600: raw_cache)
    monkeypatch.setattr(api_module, "_check_symbol_exists", lambda symbol: pytest.fail("raw cache hit must not validate symbol externally"))
    monkeypatch.setattr(api_module, "fetch_and_store_raw_data", lambda *a, **kw: pytest.fail("raw cache hit must not rewrite raw data"))
    monkeypatch.setattr(api_module, "upsert_analysis_cache", lambda *a, **kw: pytest.fail("skip_ai must not write analysis cache"))
    monkeypatch.setattr(api_module, "backfill_yesterday_indicators", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "load_yesterday_context", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "_read_shared_context_for_symbol", lambda *a, **kw: None)

    graph = _make_graph(
        {
            "snapshot": cached_snapshot,
            "institutional_flow": cached_institutional,
            "fundamental_data": cached_fundamental,
            "analysis": "快查結果",
            "errors": [],
        }
    )
    client = _client_with_graph(graph)
    response = client.post("/analyze", json={"symbol": "2330.TW", "skip_ai": True})

    assert response.status_code == 200
    graph_input = graph.invoke.call_args.args[0]
    assert graph_input["skip_ai"] is True
    assert graph_input["snapshot"] == cached_snapshot
    assert graph_input["institutional_flow"] == cached_institutional
    assert graph_input["fundamental_data"] == cached_fundamental
    assert response.json()["snapshot"]["name"] == "台積電"
    assert response.json()["fundamental_data"] == cached_fundamental


# ---------------------------------------------------------------------------
# Backfill yesterday indicators
# ---------------------------------------------------------------------------

def test_analyze_calls_backfill_yesterday_indicators(monkeypatch) -> None:
    """POST /analyze 應在 graph.invoke 之前呼叫 backfill_yesterday_indicators。"""
    import ai_stock_sentinel.analysis.router as api_module
    from dataclasses import asdict

    called = {}

    def fake_backfill(db, symbol):
        called["symbol"] = symbol

    monkeypatch.setattr(api_module, "backfill_yesterday_indicators", fake_backfill)
    monkeypatch.setattr(api_module, "upsert_analysis_cache", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: None)

    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "errors": [],
    })
    client = _client_with_graph(graph)
    client.post("/analyze", json={"symbol": "2330.TW"})

    assert called.get("symbol") == "2330.TW"


def test_analyze_injects_prev_context(monkeypatch) -> None:
    """POST /analyze 應在 graph.invoke 前讀取昨日上下文並注入 prev_context。"""
    import ai_stock_sentinel.analysis.router as api_module
    from dataclasses import asdict

    prev_ctx = {
        "prev_action_tag": "Trim",
        "prev_confidence": 74.0,
        "prev_rsi": 73.8,
        "prev_ma_alignment": "bullish",
    }
    captured_state = {}

    original_invoke = None

    def fake_backfill(db, symbol):
        pass

    def fake_load_yesterday_context(symbol, db):
        return prev_ctx

    monkeypatch.setattr(api_module, "backfill_yesterday_indicators", fake_backfill)
    monkeypatch.setattr(api_module, "load_yesterday_context", fake_load_yesterday_context)
    monkeypatch.setattr(api_module, "upsert_analysis_cache", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: None)

    graph = MagicMock()
    graph.invoke.side_effect = lambda state: (
        captured_state.update(state) or {
            "snapshot": asdict(_SNAPSHOT),
            "analysis": "分析結果",
            "errors": [],
        }
    )
    client = _client_with_graph(graph)
    client.post("/analyze", json={"symbol": "2330.TW"})

    assert captured_state.get("prev_context") == prev_ctx


def test_analyze_position_injects_prev_context(monkeypatch) -> None:
    """POST /analyze/position 應在 graph.invoke 前讀取昨日上下文並注入 prev_context。"""
    import ai_stock_sentinel.analysis.router as api_module

    prev_ctx = {
        "prev_action_tag": "Hold",
        "prev_confidence": 61.5,
        "prev_rsi": 65.2,
        "prev_ma_alignment": "bullish",
    }
    captured_state = {}

    def fake_backfill(db, symbol):
        pass

    def fake_load_yesterday_context(symbol, db):
        return prev_ctx

    monkeypatch.setattr(api_module, "backfill_yesterday_indicators", fake_backfill)
    monkeypatch.setattr(api_module, "load_yesterday_context", fake_load_yesterday_context)
    monkeypatch.setattr(api_module, "upsert_analysis_cache", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "upsert_analysis_log", lambda *a, **kw: None)
    monkeypatch.setattr(api_module, "has_active_portfolio", lambda *a, **kw: False)
    monkeypatch.setattr(api_module, "get_analysis_cache", lambda *a, **kw: None)

    graph = MagicMock()
    graph.invoke.side_effect = lambda state: (
        captured_state.update(state) or {**_POSITION_FINAL_STATE}
    )
    client = _client_with_graph(graph)
    client.post("/analyze/position", json={"symbol": "2330.TW", "entry_price": 950.0})

    assert captured_state.get("prev_context") == prev_ctx
