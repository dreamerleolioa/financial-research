from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK_DOC = REPO_ROOT / "docs/development-execution-playbook.md"
BACKEND_API_SPEC = REPO_ROOT / "docs/specs/backend-api-technical-spec.md"
DAILY_RADAR_SPEC = REPO_ROOT / "docs/specs/daily-stock-radar-spec.md"
POSITION_SPEC = REPO_ROOT / "docs/specs/ai-stock-sentinel-position-diagnosis-spec.md"
WORKFLOW = REPO_ROOT / ".github/workflows/investment-discipline-release-gate.yml"


def test_release_gate_checklist_covers_required_boundaries() -> None:
    text = "\n".join([
        PLAYBOOK_DOC.read_text(encoding="utf-8"),
        BACKEND_API_SPEC.read_text(encoding="utf-8"),
        DAILY_RADAR_SPEC.read_text(encoding="utf-8"),
    ])

    for phrase in [
        "Determinism Gate",
        "Shared Context Gate",
        "Copy Guard Gate",
        "Release Gate",
        "portfolio risk data-gap",
        "production DB / cloud internal API path",
        "不是勝率或交易建議",
        "不得轉成 portfolio action",
        "context_only",
        "deprecated",
        "STRATEGY_VERSION",
    ]:
        assert phrase in text


def test_release_gate_commands_cover_automated_checks() -> None:
    text = PLAYBOOK_DOC.read_text(encoding="utf-8") + "\n" + WORKFLOW.read_text(encoding="utf-8")

    for command_or_test in [
        "tests/test_daily_radar_rule_governance.py",
        "tests/test_daily_radar_forward_validation.py",
        "tests/test_risk_language_copy_guard.py",
        "tests/test_portfolio_risk_summary.py",
        "tests/test_portfolio_router.py",
        "tests/test_portfolio_history.py",
        "tests/test_investment_discipline_release_gate.py",
        "tests/test_compatibility_deprecation_audit.py",
        "pnpm build",
    ]:
        assert command_or_test in text


def test_release_gate_workflow_runs_backend_and_frontend_gates() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Investment Discipline Release Gate" in workflow
    assert "pull_request:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "uv run pytest -q" in workflow
    assert "tests/test_daily_radar_rule_governance.py" in workflow
    assert "tests/test_daily_radar_forward_validation.py" in workflow
    assert "tests/test_risk_language_copy_guard.py" in workflow
    assert "tests/test_portfolio_risk_summary.py" in workflow
    assert "tests/test_portfolio_router.py" in workflow
    assert "tests/test_portfolio_history.py" in workflow
    assert "tests/test_investment_discipline_release_gate.py" in workflow
    assert "tests/test_compatibility_deprecation_audit.py" in workflow
    assert "pnpm build" in workflow
    assert "/internal/daily-radar" not in workflow


def test_release_gate_tracks_compatibility_deprecation_audit() -> None:
    gate_text = PLAYBOOK_DOC.read_text(encoding="utf-8")
    audit_text = "\n".join([
        BACKEND_API_SPEC.read_text(encoding="utf-8"),
        POSITION_SPEC.read_text(encoding="utf-8"),
    ])

    assert "tests/test_compatibility_deprecation_audit.py" in gate_text
    assert "legacy/internal compatibility" in audit_text
    assert "不可刪除" in audit_text
    assert "Historical cache" not in gate_text
    assert "portfolio history" in audit_text.lower()
    assert "primary UI" in audit_text or "primary display" in audit_text
