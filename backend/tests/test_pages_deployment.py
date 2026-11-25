import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/deploy_pages.py"
ARTIFACT_ID = 15000746750
SHA = "c" * 40
PAGES = "https://api.github.com/repos/owner/repo/pages/deployments"
OIDC = "https://issuer.example/token?api-version=2"
PAGE_URL = "https://owner.github.io/repo/"


@pytest.fixture
def deploy(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("deploy_pages", SCRIPT)
    pages = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pages)
    output = tmp_path / "output"
    for key, value in {
        "GITHUB_API_URL": "https://api.github.com", "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "3",
        "GITHUB_SHA": SHA, "GITHUB_TOKEN": "workflow-token", "GITHUB_OUTPUT": str(output),
        "PAGES_ARTIFACT_ID": str(ARTIFACT_ID), "ACTIONS_ID_TOKEN_REQUEST_URL": OIDC,
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runner-token",
    }.items():
        monkeypatch.setenv(key, value)
    calls = []
    now = [0]
    monkeypatch.setattr(pages.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(pages.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    def run(statuses=(), *, create=None):
        statuses = iter(statuses)
        def request(method, url, token, payload=None, *, timeout):
            calls.append((method, url))
            if (method, url) == ("GET", OIDC):
                assert token == "runner-token"
                return {"value": "oidc-token"}
            assert token == "workflow-token"
            if (method, url) == ("POST", PAGES):
                assert payload == {"artifact_id": ARTIFACT_ID, "pages_build_version": SHA, "oidc_token": "oidc-token"}
                if isinstance(create, Exception):
                    raise create
                return create if create is not None else {"id": 987, "page_url": PAGE_URL}
            if (method, url) == ("POST", PAGES + "/987/cancel"):
                return None
            assert (method, url) == ("GET", PAGES + "/987")
            status = next(statuses)
            if isinstance(status, BaseException):
                raise status
            return {"status": status}
        monkeypatch.setattr(pages, "request_json", request)
        pages.main()
    return run, calls, now, output


def test_publish_uses_receipt_even_when_artifact_listing_is_unavailable(deploy):
    run, calls, _, output = deploy
    run([HTTPError(PAGES, 404, "not visible yet", {}, None), ConnectionResetError(), "deployment_queued",
         "deployment_attempt_error", "succeed"])
    assert calls.count(("POST", PAGES)) == 1
    assert output.read_text() == f"page_url={PAGE_URL}\n"


@pytest.mark.parametrize("status", ["deployment_failed", "deployment_content_failed", "deployment_cancelled", "deployment_lost"])
def test_terminal_failure_is_reported_without_creating_another_deployment(deploy, status):
    run, calls, _, output = deploy
    with pytest.raises(RuntimeError, match=status):
        run([status])
    assert calls.count(("POST", PAGES)) == 1
    assert not output.exists()


def test_timeout_cancels_this_deployment(deploy):
    run, calls, now, output = deploy
    with pytest.raises(TimeoutError):
        run(["deployment_in_progress"] * 200)
    assert now[0] == 600
    assert calls[-1] == ("POST", PAGES + "/987/cancel")
    assert not output.exists()


def test_interruption_cancels_the_deployment(deploy):
    run, calls, _, output = deploy
    with pytest.raises(KeyboardInterrupt):
        run([KeyboardInterrupt()])
    assert calls[-1] == ("POST", PAGES + "/987/cancel")
    assert not output.exists()


@pytest.mark.parametrize("error", [TimeoutError(), HTTPError(PAGES, 502, "bad gateway", {}, None)])
def test_ambiguous_creation_error_is_not_submitted_twice(deploy, error):
    run, calls, _, output = deploy
    with pytest.raises(RuntimeError, match=rf"{ARTIFACT_ID}.*123.*attempt 3.*may still publish"):
        run(create=error)
    assert calls.count(("POST", PAGES)) == 1
    assert not output.exists()


@pytest.mark.parametrize("artifact_id", ["", "0", "-1", "github-pages-3"])
def test_invalid_upload_id_stops_before_requesting_credentials(deploy, monkeypatch, artifact_id):
    run, calls, _, _ = deploy
    monkeypatch.setenv("PAGES_ARTIFACT_ID", artifact_id)
    with pytest.raises(ValueError, match="artifact ID"):
        run()
    assert calls == []


@pytest.mark.parametrize("status_url", [PAGES + "/987/status", PAGES.removesuffix("deployments") + "deployment/status/987"])
def test_status_url_receipt_supports_a_bare_pages_hostname(deploy, status_url):
    run, calls, _, output = deploy
    run(["succeed"], create={"status_url": status_url, "page_url": "owner.github.io/repo/"})
    assert calls[-1] == ("GET", PAGES + "/987")
    assert output.read_text() == f"page_url={PAGE_URL}\n"


def test_foreign_status_url_is_rejected_without_forwarding_credentials(deploy):
    run, calls, _, output = deploy
    with pytest.raises(ValueError, match="deployment ID"):
        run(create={"status_url": "https://other.example/987", "page_url": PAGE_URL})
    assert calls == [("GET", OIDC), ("POST", PAGES)]
    assert not output.exists()


def test_status_read_respects_a_provider_retry_after(deploy):
    run, _, now, _ = deploy
    run([HTTPError(PAGES, 429, "rate limit", {"Retry-After": "45"}, None), "succeed"])
    assert now[0] == 45


def test_authenticated_transport_sends_json_without_following_redirects(monkeypatch):
    spec = importlib.util.spec_from_file_location("deploy_pages", SCRIPT)
    pages = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pages)
    body = {"artifact_id": ARTIFACT_ID, "oidc_token": "example-token"}

    class Opener:
        def open(self, request, *, timeout):
            assert request.full_url == PAGES and request.get_method() == "POST"
            assert request.get_header("Authorization") == "Bearer example"
            assert request.get_header("Content-type") == "application/json"
            assert json.loads(request.data) == body and timeout == 20
            response = io.BytesIO(b'{"id": 987}')
            response.status = 200
            return response

    def opener(handler):
        assert handler().redirect_request(None, None, 307, None, {}, "https://other.example/") is None
        return Opener()

    monkeypatch.setattr(pages, "build_opener", opener)
    assert pages.request_json("POST", PAGES, "example", body, timeout=20) == {"id": 987}
