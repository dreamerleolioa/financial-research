"""Tests for main.py CLI entry point.

Verifies that the CLI uses build_graph() (LangGraph flow) instead of
the legacy StockCrawlerAgent / build_agent() path.
"""
from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from ai_stock_sentinel import main as main_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_graph_result(symbol: str = "2330.TW") -> dict:
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
            "fetched_at": "2026-03-03T00:00:00+00:00",
        },
        "analysis": "看漲",
        "cleaned_news": None,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# main() uses build_graph(), not build_agent()
# ---------------------------------------------------------------------------


def test_main_uses_build_graph_not_build_agent(capsys):
    """main() must invoke build_graph(), never build_agent()."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = _make_graph_result()

    fake_stdin = StringIO("")
    with (
        patch.object(main_module, "build_graph_deps", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("ai_stock_sentinel.main.build_graph", return_value=mock_graph) as mock_build_graph,
        patch("sys.argv", ["main", "--symbol", "2330.TW"]),
        patch("sys.stdin", fake_stdin),
        patch.object(fake_stdin, "isatty", return_value=True),
    ):
        main_module.main()

    mock_build_graph.assert_called_once()
    mock_graph.invoke.assert_called_once()


def test_main_does_not_call_build_agent(capsys):
    """build_agent() must not exist or not be called by main()."""
    # Simply verify build_agent is removed from the module.
    assert not hasattr(main_module, "build_agent"), (
        "build_agent() should be removed from main.py"
    )


# ---------------------------------------------------------------------------
# CLI behaviour: --symbol
# ---------------------------------------------------------------------------


def test_main_passes_symbol_to_graph(capsys):
    """The symbol from CLI args must be passed as initial state to graph.invoke()."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = _make_graph_result(symbol="0050.TW")

    fake_stdin = StringIO("")
    with (
        patch.object(main_module, "build_graph_deps", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("ai_stock_sentinel.main.build_graph", return_value=mock_graph),
        patch("sys.argv", ["main", "--symbol", "0050.TW"]),
        patch("sys.stdin", fake_stdin),
        patch.object(fake_stdin, "isatty", return_value=True),
    ):
        main_module.main()

    call_args = mock_graph.invoke.call_args[0][0]
    assert call_args["symbol"] == "0050.TW"


def test_main_default_symbol_is_2330tw(capsys):
    """When --symbol is omitted, 2330.TW is used by default."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = _make_graph_result()

    fake_stdin = StringIO("")
    with (
        patch.object(main_module, "build_graph_deps", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("ai_stock_sentinel.main.build_graph", return_value=mock_graph),
        patch("sys.argv", ["main"]),
        patch("sys.stdin", fake_stdin),
        patch.object(fake_stdin, "isatty", return_value=True),
    ):
        main_module.main()

    call_args = mock_graph.invoke.call_args[0][0]
    assert call_args["symbol"] == "2330.TW"


# ---------------------------------------------------------------------------
# CLI behaviour: --news-text
# ---------------------------------------------------------------------------


def test_main_passes_news_text_to_graph(capsys):
    """--news-text must be forwarded as news_content in initial state."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = _make_graph_result()

    # --news-text short-circuits stdin reading, no need to patch stdin
    with (
        patch.object(main_module, "build_graph_deps", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("ai_stock_sentinel.main.build_graph", return_value=mock_graph),
        patch("sys.argv", ["main", "--news-text", "台積電利多"]),
    ):
        main_module.main()

    call_args = mock_graph.invoke.call_args[0][0]
    assert call_args["news_content"] == "台積電利多"


# ---------------------------------------------------------------------------
# CLI behaviour: --news-file
# ---------------------------------------------------------------------------


def test_main_passes_news_file_content_to_graph(tmp_path, capsys):
    """--news-file must read the file and forward content as news_content."""
    news_file = tmp_path / "news.txt"
    news_file.write_text("新聞內容來自檔案", encoding="utf-8")

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = _make_graph_result()

    # --news-file short-circuits stdin reading, no need to patch stdin
    with (
        patch.object(main_module, "build_graph_deps", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("ai_stock_sentinel.main.build_graph", return_value=mock_graph),
        patch("sys.argv", ["main", "--news-file", str(news_file)]),
    ):
        main_module.main()

    call_args = mock_graph.invoke.call_args[0][0]
    assert call_args["news_content"] == "新聞內容來自檔案"


# ---------------------------------------------------------------------------
# CLI behaviour: stdin
# ---------------------------------------------------------------------------


def test_main_reads_news_from_stdin(capsys):
    """When stdin is a pipe (not a tty), its content becomes news_content."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = _make_graph_result()

    fake_stdin = StringIO("stdin 新聞")

    with (
        patch.object(main_module, "build_graph_deps", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("ai_stock_sentinel.main.build_graph", return_value=mock_graph),
        patch("sys.argv", ["main"]),
        patch("sys.stdin", fake_stdin),
        patch.object(fake_stdin, "isatty", return_value=False),
    ):
        main_module.main()

    call_args = mock_graph.invoke.call_args[0][0]
    assert call_args["news_content"] == "stdin 新聞"


def test_main_empty_stdin_pipe_gives_no_news_content(capsys):
    """An empty stdin pipe (e.g. `echo -n | main`) must produce news_content=None."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = _make_graph_result()

    fake_stdin = StringIO("")

    with (
        patch.object(main_module, "build_graph_deps", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("ai_stock_sentinel.main.build_graph", return_value=mock_graph),
        patch("sys.argv", ["main"]),
        patch("sys.stdin", fake_stdin),
        patch.object(fake_stdin, "isatty", return_value=False),
    ):
        main_module.main()

    call_args = mock_graph.invoke.call_args[0][0]
    assert call_args["news_content"] is None


# ---------------------------------------------------------------------------
# CLI behaviour: output is JSON
# ---------------------------------------------------------------------------


def test_main_prints_json_output(capsys):
    """main() must print the graph result as JSON to stdout."""
    mock_graph = MagicMock()
    result = _make_graph_result()
    mock_graph.invoke.return_value = result

    fake_stdin = StringIO("")
    with (
        patch.object(main_module, "build_graph_deps", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("ai_stock_sentinel.main.build_graph", return_value=mock_graph),
        patch("sys.argv", ["main", "--symbol", "2330.TW"]),
        patch("sys.stdin", fake_stdin),
        patch.object(fake_stdin, "isatty", return_value=True),
    ):
        main_module.main()

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["snapshot"]["symbol"] == "2330.TW"
    assert parsed["analysis"] == "看漲"


# ---------------------------------------------------------------------------
# Initial state structure
# ---------------------------------------------------------------------------


def test_main_initial_state_has_required_keys(capsys):
    """Graph must be invoked with all required GraphState keys."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = _make_graph_result()

    fake_stdin = StringIO("")
    with (
        patch.object(main_module, "build_graph_deps", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())),
        patch("ai_stock_sentinel.main.build_graph", return_value=mock_graph),
        patch("sys.argv", ["main"]),
        patch("sys.stdin", fake_stdin),
        patch.object(fake_stdin, "isatty", return_value=True),
    ):
        main_module.main()

    state = mock_graph.invoke.call_args[0][0]
    required_keys = {
        "symbol", "news_content", "snapshot", "analysis",
        "cleaned_news", "raw_news_items", "data_sufficient",
        "retry_count", "errors", "requires_news_refresh",
        "requires_fundamental_update",
        "technical_context", "institutional_context", "institutional_flow",
        "strategy_type", "entry_zone", "stop_loss", "holding_period",
        "confidence_score", "cross_validation_note",
    }
    assert required_keys.issubset(state.keys())
