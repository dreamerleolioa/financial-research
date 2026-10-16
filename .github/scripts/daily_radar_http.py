"""Bounded HTTPS requests shared by the Radar workflow runners."""
from contextlib import contextmanager
import json
import signal
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "evidence_redirect_rejected", headers, fp)


def post(endpoint, token, payload, timeout=300):
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("evidence_endpoint_requires_https")
    request = Request(endpoint, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        return _read_json(response)


def _read_json(response):
    body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024:
        raise ValueError("evidence_response_too_large")
    return json.loads(body)


def diagnostic_json(value, token, *, indent=2):
    """Redact string values/keys before encoding, including JSON-escaped secrets."""
    def redact(item):
        if isinstance(item, str):
            return item.replace(token, "[redacted]") if token else item
        if isinstance(item, list):
            return [redact(part) for part in item]
        if isinstance(item, dict):
            return {redact(key): redact(part) for key, part in item.items()}
        return item
    return json.dumps(redact(value), ensure_ascii=False, indent=indent)


@contextmanager
def request_deadline(seconds):
    """Bound the complete request, including DNS, headers and body, in this POSIX CLI."""
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    def expired(signum, frame):
        raise TimeoutError("evidence_http_wall_clock_timeout")
    signal.signal(signal.SIGALRM, expired)
    limit = min(seconds, previous_timer[0]) if previous_timer[0] > 0 else seconds
    signal.setitimer(signal.ITIMER_REAL, limit)
    try:
        yield
        if time.monotonic() - started > limit:
            raise TimeoutError("evidence_http_wall_clock_timeout")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            remaining = max(0.000001, previous_timer[0] - (time.monotonic() - started))
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])
