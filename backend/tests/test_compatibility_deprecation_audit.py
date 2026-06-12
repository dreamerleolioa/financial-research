from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DOC = REPO_ROOT / "docs/plans/2026-06-12-compatibility-deprecation-audit.md"


def test_compatibility_deprecation_audit_blocks_removal_until_dependencies_close() -> None:
    text = AUDIT_DOC.read_text(encoding="utf-8")

    assert "Status: **No-go for removal**" in text
    for field in [
        "recommended_action",
        "trailing_stop",
        "trailing_stop_reason",
        "exit_reason",
        "command_language_deprecated",
        "entry_zone",
        "stop_loss",
        "action_plan.action",
    ]:
        assert field in text


def test_compatibility_deprecation_audit_covers_frontend_reports_clients_and_cache() -> None:
    text = AUDIT_DOC.read_text(encoding="utf-8")

    for dependency in [
        "Backend API schema",
        "Position scorer",
        "LLM analysis context",
        "Historical cache",
        "Portfolio history",
        "Daily analysis log",
        "Frontend Analyze",
        "Frontend Portfolio",
        "Specs / external clients",
        "Reports",
    ]:
        assert dependency in text


def test_compatibility_deprecation_audit_lists_removal_closure_steps() -> None:
    text = AUDIT_DOC.read_text(encoding="utf-8")

    for closure_step in [
        "Add replacement risk-language fields to history endpoints",
        "Backfill or version historical cache rows",
        "Stop writing new legacy values",
        "Migrate `LangChainAnalyzer` position context",
        "Update frontend Portfolio historical display",
        "Update API specs",
        "Run a production data audit",
        "external-client migration guidance",
    ]:
        assert closure_step in text
