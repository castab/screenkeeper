"""Configuration loading and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


DEFAULT_RECONCILE_INTERVAL = 30.0
DEFAULT_POWER_ON_DELAY = 15.0
DEFAULT_PUSH_INTERVAL = 15.0
DEFAULT_PROMETHEUS_JOB = "signage_controller"
DEFAULT_RESTART_DELAY = 2.0
DEFAULT_HWDEC = "auto"
DEFAULT_MPV_BINARY = "mpv"
DEFAULT_HEARTBEAT_INTERVAL = 30.0
DEFAULT_CONTROL_PLANE_TIMEOUT = 10.0
ENVIRONMENT_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LOKI_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
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
class LokiConfig:
    """Optional Loki log-shipping configuration."""

    url: str
    bearer_token_env: str
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PrometheusConfig:
    """Optional Prometheus remote-write configuration."""

    remote_write_url: str
    bearer_token_env: str
    job: str = DEFAULT_PROMETHEUS_JOB
    instance: str | None = None
    push_interval: float = DEFAULT_PUSH_INTERVAL


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    """Optional run-time telemetry configuration."""

    loki: LokiConfig | None = None
    prometheus: PrometheusConfig | None = None


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
class ApplicationConfig:
    """Controller configuration."""

    tvs: tuple[TvConfig, ...]
    reconcile_interval: float = DEFAULT_RECONCILE_INTERVAL
    power_on_delay: float = DEFAULT_POWER_ON_DELAY
    observability: ObservabilityConfig = ObservabilityConfig()
    playback: PlaybackConfig | None = None
    control_plane: ControlPlaneConfig | None = None

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
    observability = _parse_observability(raw.get("observability"))

    tvs = tuple(_parse_tv(entry, index) for index, entry in enumerate(tv_entries, start=1))
    ids = [tv.id for tv in tvs]
    duplicates = sorted({tv_id for tv_id in ids if ids.count(tv_id) > 1})
    if duplicates:
        raise ConfigurationError(f"TV IDs must be unique; duplicated IDs: {', '.join(duplicates)}")

    config_dir = path.resolve().parent
    known_tv_ids = frozenset(tv.id for tv in tvs)
    playback = _parse_playback(raw.get("playback"), config_dir, known_tv_ids)
    control_plane = _parse_control_plane(raw.get("control_plane"))

    return ApplicationConfig(
        tvs=tvs,
        reconcile_interval=reconcile_interval,
        power_on_delay=power_on_delay,
        observability=observability,
        playback=playback,
        control_plane=control_plane,
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


def _parse_observability(raw: Any) -> ObservabilityConfig:
    if raw is None:
        return ObservabilityConfig()
    if not isinstance(raw, dict):
        raise ConfigurationError("'observability' must be a mapping.")

    return ObservabilityConfig(
        loki=_parse_loki(raw.get("loki")),
        prometheus=_parse_prometheus(raw.get("prometheus")),
    )


def _parse_loki(raw: Any) -> LokiConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigurationError("'observability.loki' must be a mapping.")

    labels_raw = raw.get("labels", {})
    if not isinstance(labels_raw, dict):
        raise ConfigurationError("'observability.loki.labels' must be a mapping of strings.")
    labels: list[tuple[str, str]] = []
    reserved_labels = {"service", "instance", "level", "tv_id"}
    for name, value in labels_raw.items():
        if not isinstance(name, str) or not LOKI_LABEL_RE.fullmatch(name):
            raise ConfigurationError(
                "'observability.loki.labels' keys must be valid Loki label names."
            )
        if name in reserved_labels:
            raise ConfigurationError(
                f"'observability.loki.labels.{name}' is reserved by the controller."
            )
        if not isinstance(value, str):
            raise ConfigurationError("'observability.loki.labels' values must be strings.")
        labels.append((name, value))

    return LokiConfig(
        url=_parse_url(raw.get("url"), "observability.loki.url"),
        bearer_token_env=_parse_environment_variable(
            raw.get("bearer_token_env"), "observability.loki.bearer_token_env"
        ),
        labels=tuple(sorted(labels)),
    )


def _parse_prometheus(raw: Any) -> PrometheusConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigurationError("'observability.prometheus' must be a mapping.")

    job = raw.get("job", DEFAULT_PROMETHEUS_JOB)
    if not isinstance(job, str) or not job.strip():
        raise ConfigurationError("'observability.prometheus.job' must be a non-empty string.")
    instance = raw.get("instance")
    if instance is not None and (not isinstance(instance, str) or not instance.strip()):
        raise ConfigurationError("'observability.prometheus.instance' must be a non-empty string.")

    if "pushgateway_url" in raw:
        raise ConfigurationError(
            "'observability.prometheus.pushgateway_url' is no longer supported; "
            "use 'remote_write_url'."
        )

    return PrometheusConfig(
        remote_write_url=_parse_url(
            raw.get("remote_write_url"), "observability.prometheus.remote_write_url"
        ),
        bearer_token_env=_parse_environment_variable(
            raw.get("bearer_token_env"), "observability.prometheus.bearer_token_env"
        ),
        job=job.strip(),
        instance=instance.strip() if instance else None,
        push_interval=_parse_seconds(
            raw.get("push_interval", DEFAULT_PUSH_INTERVAL),
            "observability.prometheus.push_interval",
            allow_zero=False,
        ),
    )


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


def _parse_url(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"'{name}' must be a non-empty HTTPS URL.")
    value = value.strip()
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as err:
        raise ConfigurationError(f"'{name}' must be a valid HTTPS URL.") from err
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ConfigurationError(f"'{name}' must be a non-empty HTTPS URL.")
    return value


def _parse_environment_variable(value: Any, name: str) -> str:
    if not isinstance(value, str) or not ENVIRONMENT_VARIABLE_RE.fullmatch(value):
        raise ConfigurationError(f"'{name}' must be a valid environment variable name.")
    return value
