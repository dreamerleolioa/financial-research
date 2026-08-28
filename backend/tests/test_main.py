from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ai_stock_sentinel import main as main_module


def _graph_result(symbol: str = "2330.TW") -> dict:
    return {
        "snapshot": {
            "symbol": symbol,
            "currency": "TWD",
            "current_price": 100.0,
            "previous_close": 99.0,
            "day_open": 99.5,
            "day_high": 101.0,
            "day_low": 98.5,
            "volume": 123456,
            "recent_closes": [98.0, 99.0, 100.0],
            "fetched_at": "2026-08-28T00:00:00+00:00",
        },
        "analysis": "",
        "errors": [],
    }


def test_build_graph_deps_returns_only_market_crawler() -> None:
    crawler = main_module.build_graph_deps()

    assert crawler is not None
    assert hasattr(crawler, "fetch_basic_snapshot")


def test_main_builds_deterministic_graph_and_invokes_it(capsys) -> None:
    crawler = MagicMock()
    graph = MagicMock()
    graph.invoke.return_value = _graph_result()

    with (
        patch.object(main_module, "build_graph_deps", return_value=crawler),
        patch("ai_stock_sentinel.main.build_graph", return_value=graph) as build_graph,
        patch("sys.argv", ["main", "--symbol", "2330.TW"]),
    ):
        main_module.main()

    build_graph.assert_called_once_with(crawler=crawler)
    graph.invoke.assert_called_once()
    state = graph.invoke.call_args.args[0]
    assert state["symbol"] == "2330.TW"
    assert "news_content" not in state
    assert "skip_ai" not in state
    assert state["is_final"] is True

    output = json.loads(capsys.readouterr().out)
    assert output["snapshot"]["symbol"] == "2330.TW"


def test_main_uses_default_symbol(capsys) -> None:
    graph = MagicMock()
    graph.invoke.return_value = _graph_result()

    with (
        patch.object(main_module, "build_graph_deps", return_value=MagicMock()),
        patch("ai_stock_sentinel.main.build_graph", return_value=graph),
        patch("sys.argv", ["main"]),
    ):
        main_module.main()

    assert graph.invoke.call_args.args[0]["symbol"] == "2330.TW"
    json.loads(capsys.readouterr().out)


def test_legacy_agent_entrypoint_is_removed() -> None:
    assert not hasattr(main_module, "build_agent")
