from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_API_SPEC = REPO_ROOT / "docs/specs/backend-api-technical-spec.md"
POSITION_SPEC = REPO_ROOT / "docs/specs/ai-stock-sentinel-position-diagnosis-spec.md"


def test_compatibility_deprecation_audit_blocks_removal_until_dependencies_close() -> None:
    text = BACKEND_API_SPEC.read_text(encoding="utf-8") + "\n" + POSITION_SPEC.read_text(encoding="utf-8")

    assert "legacy/internal compatibility" in text
    assert "不可刪除" in text
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
    text = BACKEND_API_SPEC.read_text(encoding="utf-8") + "\n" + POSITION_SPEC.read_text(encoding="utf-8")

    for dependency in [
        "API consumer",
        "PositionScorer",
        "position_context",
        "stock_analysis_cache",
        "portfolio history",
        "daily_analysis_log",
        "前端",
        "Portfolio",
        "對外文件",
        "API 技術規格",
    ]:
        assert dependency.lower() in text.lower()


def test_compatibility_deprecation_audit_lists_removal_closure_steps() -> None:
    text = BACKEND_API_SPEC.read_text(encoding="utf-8") + "\n" + POSITION_SPEC.read_text(encoding="utf-8")

    for closure_step in [
        "risk_state",
        "discipline_triggers",
        "observation_conditions",
        "risk_control_reference",
        "compatibility_source",
        "legacy_recommended_action",
        "primary UI",
        "primary display",
    ]:
        assert closure_step in text
