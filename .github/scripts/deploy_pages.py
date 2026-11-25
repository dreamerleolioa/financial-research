"""Publish the Pages artifact returned by this job's upload and wait for its deployment."""

import json
import os
import re
import signal
import sys
import time
from http.client import HTTPException, IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    """Keep each bearer token on the endpoint for which it was issued."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(method, url, token, payload=None, *, timeout):
    if urlsplit(url).scheme != "https":
        raise ValueError("GitHub credentials require HTTPS")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    request = Request(url, data=body, headers=headers, method=method)
    with build_opener(NoRedirect).open(request, timeout=timeout) as response:
        return None if response.status == 204 else json.load(response)


def wait_for_pages(url, token):
    deadline = time.monotonic() + 600
    last_status = "created"
    terminal = {"succeed", "deployment_failed", "deployment_content_failed", "deployment_cancelled", "deployment_lost"}
    while (remaining := deadline - time.monotonic()) > 0:
        delay = 5
        try:
            result = request_json("GET", url, token, timeout=min(20, remaining))
            status = result["status"]
            if status in terminal:
                return status
        except HTTPError as error:
            retry_after = error.headers.get("Retry-After", "")
            quota_exhausted = error.headers.get("X-RateLimit-Remaining") == "0"
            if (error.code not in (404, 408, 429) and error.code < 500
                    and not (error.code == 403 and (retry_after or quota_exhausted))):
                raise
            if retry_after.isdigit():
                delay = max(delay, int(retry_after))
            elif quota_exhausted and (reset := error.headers.get("X-RateLimit-Reset", "")).isdigit():
                delay = max(delay, int(reset) - time.time())
            elif error.code == 429:
                delay = 60
            status = f"HTTP {error.code}"
        except (URLError, TimeoutError, ConnectionError, IncompleteRead):
            status = "status temporarily unavailable"
        if status != last_status:
            print(f"Pages deployment status: {status}", flush=True)
            last_status = status
        time.sleep(min(delay, max(0, deadline - time.monotonic())))
    raise TimeoutError(f"Pages deployment timed out after 10 minutes (last status: {last_status})")


def main():
    artifact_id = os.environ.get("PAGES_ARTIFACT_ID", "")
    if not re.fullmatch(r"[1-9][0-9]*", artifact_id):
        raise ValueError("Missing or invalid Pages upload artifact ID")

    url = f"{os.environ['GITHUB_API_URL']}/repos/{os.environ['GITHUB_REPOSITORY']}/pages/deployments"
    token = os.environ["GITHUB_TOKEN"]
    identity = request_json("GET", os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"],
                            os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"], timeout=20)
    payload = {"artifact_id": int(artifact_id), "pages_build_version": os.environ["GITHUB_SHA"],
               "oidc_token": identity["value"]}
    # A transport error can arrive after GitHub accepts this POST. Submit it only once.
    receipt = f"Pages artifact {artifact_id} from run {os.environ['GITHUB_RUN_ID']} attempt {os.environ['GITHUB_RUN_ATTEMPT']}"
    uncertain = f"{receipt} may still publish. Check this attempt before retrying."
    print(f"Submitting {receipt}", flush=True)
    try:
        created = request_json("POST", url, token, payload, timeout=30)
    except (OSError, HTTPException, ValueError) as error:
        if isinstance(error, HTTPError) and error.code < 500 and error.code != 408:
            raise
        raise RuntimeError(uncertain) from error
    if not isinstance(created, dict):
        raise ValueError(uncertain)
    deployment_id = str(created.get("id") or "")
    if not deployment_id:
        # Some API responses identify the deployment only through its status URL.
        status_url = created.get("status_url") or ""
        for prefix in (url + "/", url.removesuffix("deployments") + "deployment/status/"):
            if status_url.startswith(prefix):
                deployment_id = status_url.removeprefix(prefix).removesuffix("/status")
                break
    if not re.fullmatch(r"[A-Za-z0-9_-]+", deployment_id):
        raise ValueError(f"GitHub returned no usable Pages deployment ID. {uncertain}")
    deployment_url = f"{url}/{deployment_id}"
    print(f"Submitted Pages artifact {artifact_id} as deployment {deployment_id}", flush=True)

    try:
        status = wait_for_pages(deployment_url, token)
    except (Exception, KeyboardInterrupt):
        try:
            request_json("POST", deployment_url + "/cancel", token, timeout=5)
        except Exception as error:
            print(f"Could not confirm Pages cancellation ({type(error).__name__})", file=sys.stderr)
        raise
    if status != "succeed":
        raise RuntimeError(f"Pages deployment {deployment_id} ended with {status}")

    page_url = created["page_url"]
    if not page_url or any(c.isspace() for c in page_url):
        raise ValueError("GitHub returned an invalid Pages URL")
    if "://" not in page_url:
        page_url = "https://" + page_url
    if urlsplit(page_url).scheme not in ("https", "http") or not urlsplit(page_url).netloc:
        raise ValueError("GitHub returned an invalid Pages URL")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"page_url={page_url}\n")


if __name__ == "__main__":
    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        main()
    except HTTPError as error:
        sys.exit(f"GitHub returned HTTP {error.code} during Pages deployment")
