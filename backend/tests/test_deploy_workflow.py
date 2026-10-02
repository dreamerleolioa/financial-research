from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


def test_pages_artifact_name_is_unique_per_run_attempt() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    artifact_name = "github-pages-${{ github.run_attempt }}"
    upload_step = workflow.split("uses: actions/upload-pages-artifact@v3", maxsplit=1)[1].split(
        "- name: Deploy to GitHub Pages", maxsplit=1
    )[0]
    deploy_step = workflow.split("uses: actions/deploy-pages@v4", maxsplit=1)[1]

    assert f"name: {artifact_name}" in upload_step
    assert f"artifact_name: {artifact_name}" in deploy_step


def test_pages_deployment_requires_frontend_verification_on_main_push() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    frontend_job = workflow.split("  test-frontend:\n", 1)[1].split("\n  deploy-frontend:", 1)[0]
    deploy_job = workflow.split("  deploy-frontend:\n", 1)[1]
    assert "needs: [test-backend, test-frontend]" in deploy_job
    for check in ["pnpm lint", "pnpm test:e2e", "pnpm build", "playwright install --with-deps chromium"]:
        assert check in frontend_job
    assert "if:" not in frontend_job
