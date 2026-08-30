"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


DEFAULT_RECONCILE_INTERVAL = 30.0
DEFAULT_POWER_ON_DELAY = 15.0
DEFAULT_METRICS_PORT = 9464
DEFAULT_OTLP_ENDPOINT = "http://127.0.0.1:4318"
DEFAULT_RESTART_DELAY = 2.0
DEFAULT_HWDEC = "auto"
DEFAULT_MPV_BINARY = "mpv"
DEFAULT_HEARTBEAT_INTERVAL = 30.0
DEFAULT_CONTROL_PLANE_TIMEOUT = 10.0
DEFAULT_CONTENT_RECONCILE_INTERVAL = 300.0
DEFAULT_CONTENT_DOWNLOAD_TIMEOUT = 3600.0
# Plain HTTP to one of these is a developer talking to a control plane on their
# own machine, where there is no network for anyone to intercept.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigurationError(ValueError):
    """Raised when the controller configuration is invalid."""


@dataclass(frozen=True, slots=True)
class TvConfig:
    """Configured desired state for one television."""

    id: str
    name: str
    host: str
    desired_input: str
    desired_volume: int


@dataclass(frozen=True, slots=True)
class PlayerConfig:
    """Configured desired state for one mpv-driven signage player."""

    id: str
    name: str
    media: Path
    tv_id: str | None = None
    screen: int | None = None
    screen_name: str | None = None


@dataclass(frozen=True, slots=True)
class PlaybackConfig:
    """Optional local mpv playback configuration, independent of TV control."""

    mpv_binary: str = DEFAULT_MPV_BINARY
    restart_delay: float = DEFAULT_RESTART_DELAY
    hwdec: str = DEFAULT_HWDEC
    fullscreen: bool = True
    loop: bool = True
    audio: bool = False
    players: tuple[PlayerConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class MetricsConfig:
    """Optional local Prometheus exposition.

    Screenkeeper never sends metrics anywhere itself. This only controls
    whether a `/metrics` endpoint is exposed for a host-level agent (e.g.
    Grafana Alloy) to pull from; it carries no remote URL or credential.
    """

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = DEFAULT_METRICS_PORT


@dataclass(frozen=True, slots=True)
class TracingConfig:
    """Optional OpenTelemetry tracing, exported locally to a host-level agent.

    Like metrics, Screenkeeper never sends spans anywhere itself: `endpoint`
    is always the local Alloy OTLP receiver, never a remote/central URL or
    credential. Unlike metrics, this is push-based (OTLP has no pull mode),
    but the push never leaves 127.0.0.1.
    """

    enabled: bool = True
    endpoint: str = DEFAULT_OTLP_ENDPOINT


@dataclass(frozen=True, slots=True)
class ControlPlaneConfig:
    """Optional Screenkeeper Control connectivity, used only by the agent.

    Its absence is the default and keeps the appliance entirely local: TV
    control and mpv playback never consult this section.
    """

    base_url: str
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL
    request_timeout: float = DEFAULT_CONTROL_PLANE_TIMEOUT
    allow_insecure_http: bool = False


@dataclass(frozen=True, slots=True)
class ContentConfig:
    """Optional remotely managed content synchronization."""

    enabled: bool = True
    reconcile_interval: float = DEFAULT_CONTENT_RECONCILE_INTERVAL
    download_timeout: float = DEFAULT_CONTENT_DOWNLOAD_TIMEOUT
    cache_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    """Controller configuration."""

    tvs: tuple[TvConfig, ...]
    reconcile_interval: float = DEFAULT_RECONCILE_INTERVAL
    power_on_delay: float = DEFAULT_POWER_ON_DELAY
    metrics: MetricsConfig | None = None
    tracing: TracingConfig | None = None
    playback: PlaybackConfig | None = None
    control_plane: ControlPlaneConfig | None = None
    content: ContentConfig | None = None

    def get_tv(self, tv_id: str) -> TvConfig:
        """Return a TV by ID or provide the valid IDs in the error."""
        for tv in self.tvs:
            if tv.id == tv_id:
                return tv
        valid_ids = ", ".join(tv.id for tv in self.tvs)
        raise ConfigurationError(f"Unknown TV ID {tv_id!r}. Configured IDs: {valid_ids}")

    def get_player(self, player_id: str) -> PlayerConfig:
        """Return a configured player by ID or provide the valid IDs in the error."""
        if self.playback is None or not self.playback.players:
            raise ConfigurationError("No 'playback' section with players is configured.")
        for player in self.playback.players:
            if player.id == player_id:
                return player
        valid_ids = ", ".join(player.id for player in self.playback.players)
        raise ConfigurationError(f"Unknown player ID {player_id!r}. Configured IDs: {valid_ids}")


def load_config(path: Path) -> ApplicationConfig:
    """Load a YAML configuration file."""
    try:
        with path.open(encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file)
    except FileNotFoundError as err:
        raise ConfigurationError(f"Configuration file not found: {path}") from err
    except OSError as err:
        raise ConfigurationError(f"Could not read configuration file {path}: {err}") from err
    except yaml.YAMLError as err:
        raise ConfigurationError(f"Invalid YAML in {path}: {err}") from err

    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration root must be a mapping.")

    tv_entries = raw.get("tvs")
    if not isinstance(tv_entries, list) or not tv_entries:
        raise ConfigurationError("Configuration must contain a non-empty 'tvs' list.")

    reconcile_interval = _parse_seconds(
        raw.get("reconcile_interval", DEFAULT_RECONCILE_INTERVAL),
        "reconcile_interval",
        allow_zero=False,
    )
    power_on_delay = _parse_seconds(
        raw.get("power_on_delay", DEFAULT_POWER_ON_DELAY),
        "power_on_delay",
        allow_zero=True,
    )
    metrics = _parse_metrics(raw.get("metrics"))
    tracing = _parse_tracing(raw.get("tracing"))

    tvs = tuple(_parse_tv(entry, index) for index, entry in enumerate(tv_entries, start=1))
    ids = [tv.id for tv in tvs]
    duplicates = sorted({tv_id for tv_id in ids if ids.count(tv_id) > 1})
    if duplicates:
        raise ConfigurationError(f"TV IDs must be unique; duplicated IDs: {', '.join(duplicates)}")

    config_dir = path.resolve().parent
    known_tv_ids = frozenset(tv.id for tv in tvs)
    playback = _parse_playback(raw.get("playback"), config_dir, known_tv_ids)
    control_plane = _parse_control_plane(raw.get("control_plane"))
    content = _parse_content(raw.get("content"))

    return ApplicationConfig(
        tvs=tvs,
        reconcile_interval=reconcile_interval,
        power_on_delay=power_on_delay,
        metrics=metrics,
        tracing=tracing,
        playback=playback,
        control_plane=control_plane,
        content=content,
    )


def _parse_seconds(value: Any, name: str, *, allow_zero: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        qualifier = "a non-negative" if allow_zero else "a positive"
        raise ConfigurationError(f"'{name}' must be {qualifier} number of seconds.")
    parsed_value = float(value)
    if parsed_value < 0 or (not allow_zero and parsed_value == 0):
        qualifier = "non-negative" if allow_zero else "greater than zero"
        raise ConfigurationError(f"'{name}' must be {qualifier}.")
    return parsed_value


def _parse_tv(raw: Any, index: int) -> TvConfig:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"TV entry {index} must be a mapping.")

    fields: dict[str, str] = {}
    for field_name in ("id", "name", "host", "desired_input"):
        value = raw.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(
                f"TV entry {index} field '{field_name}' must be a non-empty string."
            )
        fields[field_name] = value.strip()

    volume = raw.get("desired_volume")
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
        raise ConfigurationError(
            f"TV entry {index} field 'desired_volume' must be an integer from 0 to 100."
        )

    return TvConfig(desired_volume=volume, **fields)


def _parse_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"'{name}' must be a boolean.")
    return value


def _parse_playback(
    raw: Any, config_dir: Path, known_tv_ids: frozenset[str]
) -> PlaybackConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigurationError("'playback' must be a mapping.")

    mpv_binary = raw.get("mpv_binary", DEFAULT_MPV_BINARY)
    if not isinstance(mpv_binary, str) or not mpv_binary.strip():
        raise ConfigurationError("'playback.mpv_binary' must be a non-empty string.")

    restart_delay = _parse_seconds(
        raw.get("restart_delay", DEFAULT_RESTART_DELAY), "playback.restart_delay", allow_zero=True
    )

    hwdec = raw.get("hwdec", DEFAULT_HWDEC)
    if not isinstance(hwdec, str) or not hwdec.strip():
        raise ConfigurationError("'playback.hwdec' must be a non-empty string.")

    fullscreen = _parse_bool(raw.get("fullscreen", True), "playback.fullscreen")
    loop = _parse_bool(raw.get("loop", True), "playback.loop")
    audio = _parse_bool(raw.get("audio", False), "playback.audio")

    players_raw = raw.get("players", [])
    if not isinstance(players_raw, list):
        raise ConfigurationError("'playback.players' must be a list.")
    players = tuple(
        _parse_player(entry, index, config_dir, known_tv_ids)
        for index, entry in enumerate(players_raw, start=1)
    )
    ids = [player.id for player in players]
    duplicates = sorted({player_id for player_id in ids if ids.count(player_id) > 1})
    if duplicates:
        raise ConfigurationError(f"Player IDs must be unique; duplicated IDs: {', '.join(duplicates)}")

    return PlaybackConfig(
        mpv_binary=mpv_binary.strip(),
        restart_delay=restart_delay,
        hwdec=hwdec.strip(),
        fullscreen=fullscreen,
        loop=loop,
        audio=audio,
        players=players,
    )


def _parse_player(
    raw: Any, index: int, config_dir: Path, known_tv_ids: frozenset[str]
) -> PlayerConfig:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Player entry {index} must be a mapping.")

    fields: dict[str, str] = {}
    for field_name in ("id", "name"):
        value = raw.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(
                f"Player entry {index} field '{field_name}' must be a non-empty string."
            )
        fields[field_name] = value.strip()

    media_raw = raw.get("media")
    if not isinstance(media_raw, str) or not media_raw.strip():
        raise ConfigurationError(f"Player entry {index} field 'media' must be a non-empty string.")
    media_path = Path(media_raw.strip())
    media = media_path if media_path.is_absolute() else (config_dir / media_path).resolve()

    tv_id = raw.get("tv_id")
    if tv_id is not None:
        if not isinstance(tv_id, str) or not tv_id.strip():
            raise ConfigurationError(f"Player entry {index} field 'tv_id' must be a non-empty string.")
        tv_id = tv_id.strip()
        if tv_id not in known_tv_ids:
            valid_ids = ", ".join(sorted(known_tv_ids)) or "(none configured)"
            raise ConfigurationError(
                f"Player entry {index} field 'tv_id' {tv_id!r} does not match a configured TV. "
                f"Configured TV IDs: {valid_ids}"
            )

    screen = raw.get("screen")
    if screen is not None:
        if isinstance(screen, bool) or not isinstance(screen, int) or screen < 0:
            raise ConfigurationError(
                f"Player entry {index} field 'screen' must be a non-negative integer."
            )

    screen_name = raw.get("screen_name")
    if screen_name is not None:
        if not isinstance(screen_name, str) or not screen_name.strip():
            raise ConfigurationError(
                f"Player entry {index} field 'screen_name' must be a non-empty string."
            )
        screen_name = screen_name.strip()

    if screen is not None and screen_name is not None:
        raise ConfigurationError(
            f"Player entry {index}: 'screen' and 'screen_name' are mutually exclusive."
        )

    return PlayerConfig(
        id=fields["id"],
        name=fields["name"],
        media=media,
        tv_id=tv_id,
        screen=screen,
        screen_name=screen_name,
    )


def _parse_metrics(raw: Any) -> MetricsConfig | None:
    """Parse the optional local `/metrics` block.

    Absence of the whole block means metrics are off, matching the
    `playback`/`control_plane` convention elsewhere in this file. Presence of
    the block with no explicit `enabled` means the operator wants it on.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigurationError("'metrics' must be a mapping.")

    enabled = _parse_bool(raw.get("enabled", True), "metrics.enabled")

    host = raw.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host.strip():
        raise ConfigurationError("'metrics.host' must be a non-empty string.")

    port = raw.get("port", DEFAULT_METRICS_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigurationError("'metrics.port' must be an integer from 1 to 65535.")

    return MetricsConfig(enabled=enabled, host=host.strip(), port=port)


def _parse_tracing(raw: Any) -> TracingConfig | None:
    """Parse the optional OpenTelemetry tracing block.

    Same absence/presence convention as `_parse_metrics`: no `tracing:` key
    at all means tracing is off; a present block with no explicit `enabled`
    means the operator wants it on.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigurationError("'tracing' must be a mapping.")

    enabled = _parse_bool(raw.get("enabled", True), "tracing.enabled")

    endpoint = raw.get("endpoint", DEFAULT_OTLP_ENDPOINT)
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ConfigurationError("'tracing.endpoint' must be a non-empty string.")

    return TracingConfig(enabled=enabled, endpoint=endpoint.strip())


def _parse_control_plane(raw: Any) -> ControlPlaneConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigurationError("'control_plane' must be a mapping.")

    allow_insecure_http = _parse_bool(
        raw.get("allow_insecure_http", False), "control_plane.allow_insecure_http"
    )
    return ControlPlaneConfig(
        base_url=_parse_base_url(
            raw.get("base_url"),
            "control_plane.base_url",
            allow_insecure_http=allow_insecure_http,
        ),
        heartbeat_interval=_parse_seconds(
            raw.get("heartbeat_interval", DEFAULT_HEARTBEAT_INTERVAL),
            "control_plane.heartbeat_interval",
            allow_zero=False,
        ),
        request_timeout=_parse_seconds(
            raw.get("request_timeout", DEFAULT_CONTROL_PLANE_TIMEOUT),
            "control_plane.request_timeout",
            allow_zero=False,
        ),
        allow_insecure_http=allow_insecure_http,
    )


def _parse_content(raw: Any) -> ContentConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigurationError("'content' must be a mapping.")
    enabled = _parse_bool(raw.get("enabled", True), "content.enabled")
    cache_dir_raw = raw.get("cache_dir")
    cache_dir: Path | None = None
    if cache_dir_raw is not None:
        if not isinstance(cache_dir_raw, str) or not cache_dir_raw.strip():
            raise ConfigurationError("'content.cache_dir' must be an absolute path.")
        cache_dir = Path(cache_dir_raw.strip())
        if not cache_dir.is_absolute():
            raise ConfigurationError("'content.cache_dir' must be an absolute path.")
        try:
            cache_dir.relative_to(Path("/etc"))
        except ValueError:
            pass
        else:
            raise ConfigurationError("'content.cache_dir' must not be underneath /etc.")
    return ContentConfig(
        enabled=enabled,
        reconcile_interval=_parse_seconds(
            raw.get("reconcile_interval", DEFAULT_CONTENT_RECONCILE_INTERVAL),
            "content.reconcile_interval",
            allow_zero=False,
        ),
        download_timeout=_parse_seconds(
            raw.get("download_timeout", DEFAULT_CONTENT_DOWNLOAD_TIMEOUT),
            "content.download_timeout",
            allow_zero=False,
        ),
        cache_dir=cache_dir,
    )
def _parse_base_url(value: Any, name: str, *, allow_insecure_http: bool) -> str:
    """Validate a control-plane base URL, allowing HTTP only where it is safe.

    Production traffic carries a bearer token, so HTTPS is the rule. The two
    exceptions are explicit and never silent: a loopback host, where there is no
    network to intercept, and an operator who deliberately set
    `allow_insecure_http: true`.
    """
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"'{name}' must be a non-empty HTTPS URL.")
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as err:
        raise ConfigurationError(f"'{name}' must be a valid URL.") from err
    if not parsed.hostname or (port is not None and not 1 <= port <= 65535):
        raise ConfigurationError(f"'{name}' must be a valid URL with a host.")

    if parsed.scheme == "https":
        return value
    if parsed.scheme == "http":
        if parsed.hostname in LOOPBACK_HOSTS or allow_insecure_http:
            return value
        raise ConfigurationError(
            f"'{name}' must use HTTPS. Plain HTTP is permitted only for a loopback "
            "host, or when 'control_plane.allow_insecure_http' is set to true for "
            "development."
        )
    raise ConfigurationError(f"'{name}' must be an HTTPS URL.")
