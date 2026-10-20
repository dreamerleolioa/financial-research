import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ai_stock_sentinel.auth.manage_allowlist import main
from ai_stock_sentinel.db.session import Base
from ai_stock_sentinel.user_models.user import AllowedGoogleAccount, User


@pytest.fixture
def membership_database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'members.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("EXPECTED_DATABASE_NAME", raising=False)
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=[User.__table__, AllowedGoogleAccount.__table__])
    with Session(engine) as db:
        db.add(User(google_sub="known-sub", email="owner@gmail.com"))
        db.commit()
    yield engine
    engine.dispose()


def test_operator_plan_then_grant_and_revoke_without_redeploy(membership_database, capsys):
    args = ["grant", "--email", "OWNER@gmail.com"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "plan_only"
    with Session(membership_database) as db:
        assert db.scalar(select(AllowedGoogleAccount)) is None
    assert main(args + ["--apply"]) == 0
    with Session(membership_database) as db:
        assert db.get(AllowedGoogleAccount, "known-sub").email == "owner@gmail.com"
    assert main(args + ["--apply"]) == 0
    assert main(["revoke", "--email", "owner@gmail.com", "--apply"]) == 0
    with Session(membership_database) as db:
        assert db.get(AllowedGoogleAccount, "known-sub") is None
        assert db.scalar(select(User)) is not None


def test_unknown_email_requires_explicit_confirmed_subject(membership_database, capsys):
    assert main(["grant", "--email", "new@gmail.com", "--apply"]) == 1
    assert "--google-sub" in capsys.readouterr().err
    assert main(["grant", "--email", "new@gmail.com", "--google-sub", "new-sub", "--apply"]) == 0


def test_operator_does_not_accept_conflicting_account_identity(membership_database):
    assert main(["grant", "--email", "owner@gmail.com", "--google-sub", "other-sub", "--apply"]) == 1
    with Session(membership_database) as db:
        assert db.scalar(select(AllowedGoogleAccount)) is None


def test_operator_module_entrypoint_uses_explicit_source_path(membership_database):
    backend = Path(__file__).parents[1]
    result = subprocess.run([sys.executable, "-m", "ai_stock_sentinel.auth.manage_allowlist", "grant",
                             "--email", "owner@gmail.com"], cwd=backend,
                            env={**os.environ, "PYTHONPATH": str(backend / "src")},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mode"] == "plan_only"
    with Session(membership_database) as db:
        assert db.scalar(select(AllowedGoogleAccount)) is None
