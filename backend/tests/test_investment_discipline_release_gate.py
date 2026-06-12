from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_DOC = REPO_ROOT / "docs/plans/2026-06-12-investment-discipline-release-gate.md"
COMPATIBILITY_AUDIT_DOC = REPO_ROOT / "docs/plans/2026-06-12-compatibility-deprecation-audit.md"
COMMAND_DOC = REPO_ROOT / "docs/plans/2026-06-11-investment-discipline-execution-commands.md"
WORKFLOW = REPO_ROOT / ".github/workflows/investment-discipline-release-gate.yml"


def test_release_gate_checklist_covers_required_boundaries() -> None:
    text = GATE_DOC.read_text(encoding="utf-8")

    for phrase in [
        "Scoring signal registry",
        "Rule tier and scoring impact",
        "Validation evidence",
        "Copy guard",
        "Portfolio risk data gaps",
        "Cloud reporting boundary",
        "Validation metrics must not be presented as public win-rate claims",
        "Portfolio risk diagnostics must not produce portfolio actions",
        "context_only",
        "deprecated",
        "version bump",
    ]:
        assert phrase in text


def test_release_gate_commands_cover_automated_checks() -> None:
    text = GATE_DOC.read_text(encoding="utf-8") + "\n" + COMMAND_DOC.read_text(encoding="utf-8")

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
    gate_text = GATE_DOC.read_text(encoding="utf-8")
    audit_text = COMPATIBILITY_AUDIT_DOC.read_text(encoding="utf-8")

    assert "2026-06-12-compatibility-deprecation-audit.md" in gate_text
    assert "Current removal decision: **no-go**" in gate_text
    assert "Status: **No-go for removal**" in audit_text
    assert "historical cache" in audit_text
    assert "external-client migration guidance" in audit_text
