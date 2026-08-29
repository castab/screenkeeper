"""Shared outbound HTTP primitives.

Every outbound request this project makes carries a bearer token, so redirects
are refused globally: a 30x to another host would otherwise hand the credential
to whoever answered. `observability.py` and the control-plane client both build
on the opener here.

`updater.py` deliberately keeps its own opener. It fetches GitHub release
archives rather than posting credentials, and it has its own header and
HTTPS-only rules.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


REQUEST_TIMEOUT = 10.0


class NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so bearer tokens and request bodies stay at the configured host."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(NoRedirectHandler())


def open_url(request: Request, timeout: float):
    """Send one request through the no-redirect opener."""
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = REQUEST_TIMEOUT,
) -> tuple[int, dict[str, Any]]:
    """Send a JSON request and return `(status, body)` without raising on 4xx/5xx.

    A non-success status is data here, not an exception: the caller decides
    whether 401 means "stop and tell the operator" or 503 means "retry later".
    Only transport failures raise, as `URLError`/`OSError`/`TimeoutError`.

    A response that is empty or not a JSON object yields `{}`, so a server that
    answers `204` or sends an unexpected body cannot break the caller.
    """
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with open_url(request, timeout) as response:
            return response.status, _decode(response.read())
    except HTTPError as err:
        # An HTTPError is a response, not a transport failure. Read its body so
        # the caller can surface a server-supplied error message.
        with err:
            return err.code, _decode(err.read())


def _decode(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


__all__ = [
    "REQUEST_TIMEOUT",
    "NoRedirectHandler",
    "URLError",
    "open_url",
    "request_json",
]
