"""HTTP client for the Screenkeeper Control API.

Implements `contracts/openapi.yaml` directly. It does not import anything from
the control application and no client is generated from it, so the two halves of
the monorepo stay independently deployable.

The error taxonomy exists to drive retry policy. "The control plane is down" is
a normal condition for a signage appliance and is retried; "your credential was
rejected" is an operator problem and must not be retried at heartbeat frequency.

Only known response fields are read, so a server that adds fields cannot break
an older appliance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from .. import __version__
from ..http_client import REQUEST_TIMEOUT, URLError, request_json
from .identity import DeviceIdentity


API_PREFIX = "/api/v1"
DEFAULT_POLL_INTERVAL = 5.0
# Bounds a hostile or misconfigured server's advertised poll cadence.
MIN_POLL_INTERVAL = 1.0
MAX_POLL_INTERVAL = 60.0

STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_EXPIRED = "expired"


class ControlPlaneError(RuntimeError):
    """Base class for every control-plane failure."""


class ControlPlaneUnavailableError(ControlPlaneError):
    """Transient: a transport failure, a timeout, 429, or a 5xx. Retry later."""


class ControlPlaneAuthError(ControlPlaneError):
    """The credential was rejected (401/403). Not a network problem.

    Never resolved by generating a new device identity — that would abandon the
    enrollment an administrator already claimed.
    """


class EnrollmentExpiredError(ControlPlaneError):
    """The pairing code lapsed before anyone claimed it. Request a new one."""


class ControlPlaneRejectedError(ControlPlaneError):
    """The server refused the request and retrying it unchanged will not help."""


class Transport(Protocol):
    """The seam tests replace to exercise the client without a network."""

    def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        token: str | None = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> tuple[int, dict[str, Any]]:
        """Send one request and return its status and decoded JSON body."""


@dataclass(frozen=True, slots=True)
class EnrollmentTicket:
    """A pending enrollment and the code a human types to claim it."""

    enrollment_id: str
    code: str
    expires_at: str | None = None
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL


@dataclass(frozen=True, slots=True)
class EnrollmentState:
    """The current state of an enrollment, as the control plane reports it."""

    status: str
    player_id: str | None = None
    player_name: str | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    location_id: str | None = None
    location_name: str | None = None

    @property
    def is_claimed(self) -> bool:
        """Report whether an administrator has claimed this appliance."""
        return self.status == STATUS_CLAIMED and bool(self.player_id)


class ControlPlaneClient:
    """Outbound-only client for the three operations in the v1 contract."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = REQUEST_TIMEOUT,
        transport: Transport = request_json,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    @property
    def host(self) -> str:
        """Return the configured host, for messages that must not include a token."""
        return urlsplit(self.base_url).netloc or self.base_url

    def create_enrollment(self, identity: DeviceIdentity, hostname: str) -> EnrollmentTicket:
        """Request a pairing code for this appliance.

        The device token travels in the body exactly once here. The server hashes
        it and never returns it; the same secret then serves as the permanent
        bearer credential, so nothing further has to be sent back to the player.
        """
        status, body = self._send(
            "POST",
            "/enrollments",
            payload={
                "installation_id": identity.installation_id,
                "device_token": identity.device_token,
                "screenkeeper_version": __version__,
                "hostname": hostname,
            },
        )
        if status not in (200, 201):
            raise self._classify(status, body, "could not request an enrollment")

        enrollment_id = _text(body.get("enrollment_id"))
        code = _text(body.get("code"))
        if enrollment_id is None or code is None:
            raise ControlPlaneRejectedError(
                f"Control plane at {self.host} returned an enrollment without an ID or code."
            )
        return EnrollmentTicket(
            enrollment_id=enrollment_id,
            code=code,
            expires_at=_text(body.get("expires_at")),
            poll_interval_seconds=_poll_interval(body.get("poll_interval_seconds")),
        )

    def poll_enrollment(self, enrollment_id: str, token: str) -> EnrollmentState:
        """Ask whether an administrator has claimed this enrollment yet."""
        status, body = self._send(
            "GET", f"/enrollments/{quote(enrollment_id, safe='')}", token=token
        )
        if status in (404, 410):
            raise EnrollmentExpiredError(
                "The pairing code expired before it was claimed."
            )
        if status != 200:
            raise self._classify(status, body, "could not check the enrollment")

        reported = _text(body.get("status")) or STATUS_PENDING
        if reported == STATUS_EXPIRED:
            raise EnrollmentExpiredError("The pairing code expired before it was claimed.")

        organization = body.get("organization")
        location = body.get("location")
        return EnrollmentState(
            status=reported,
            player_id=_text(body.get("player_id")),
            player_name=_text(body.get("player_name")),
            organization_id=_nested(organization, "id"),
            organization_name=_nested(organization, "name"),
            location_id=_nested(location, "id"),
            location_name=_nested(location, "name"),
        )

    def send_heartbeat(self, player_id: str, token: str, report: dict[str, Any]) -> None:
        """Report current observed state. Success is any 2xx."""
        status, body = self._send(
            "POST",
            f"/players/{quote(player_id, safe='')}/heartbeat",
            payload=report,
            token=token,
        )
        if not 200 <= status < 300:
            raise self._classify(status, body, "heartbeat rejected")

    def fetch_content(self, player_id: str, token: str):
        """Fetch and validate this appliance's desired content manifest."""
        from ..content.models import ContentManifest

        status, body = self._send(
            "GET", f"/players/{quote(player_id, safe='')}/content", token=token
        )
        if status != 200:
            raise self._classify(status, body, "content manifest rejected")
        return ContentManifest.from_dict(body)

    def send_content_status(self, player_id: str, token: str, report: dict[str, Any]) -> None:
        """Replace the latest content synchronization snapshot."""
        status, body = self._send(
            "PUT",
            f"/players/{quote(player_id, safe='')}/content-status",
            payload=report,
            token=token,
        )
        if not 200 <= status < 300:
            raise self._classify(status, body, "content status rejected")

    def _send(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self.base_url}{API_PREFIX}{path}"
        try:
            return self._transport(
                url, method=method, payload=payload, token=token, timeout=self.timeout
            )
        except (URLError, OSError, TimeoutError) as err:
            # Deliberately reports the host, never the URL's credentials or body.
            raise ControlPlaneUnavailableError(
                f"Control plane at {self.host} is unreachable: {err}"
            ) from err

    def _classify(self, status: int, body: dict[str, Any], context: str) -> ControlPlaneError:
        """Map an HTTP status onto the retry policy it deserves."""
        detail = _text(body.get("message")) or _text(body.get("error"))
        suffix = f": {detail}" if detail else ""
        if status in (401, 403):
            return ControlPlaneAuthError(
                f"Control plane at {self.host} rejected this player's credential "
                f"(HTTP {status}){suffix}"
            )
        if status == 429 or status >= 500:
            return ControlPlaneUnavailableError(
                f"Control plane at {self.host} is unavailable (HTTP {status}){suffix}"
            )
        return ControlPlaneRejectedError(f"{context}: HTTP {status}{suffix}")


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _nested(container: Any, key: str) -> str | None:
    return _text(container.get(key)) if isinstance(container, dict) else None


def _poll_interval(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_POLL_INTERVAL
    return min(max(float(value), MIN_POLL_INTERVAL), MAX_POLL_INTERVAL)
