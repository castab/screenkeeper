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
ENVIRONMENT_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LOKI_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
class ApplicationConfig:
    """Controller configuration."""

    tvs: tuple[TvConfig, ...]
    reconcile_interval: float = DEFAULT_RECONCILE_INTERVAL
    power_on_delay: float = DEFAULT_POWER_ON_DELAY
    observability: ObservabilityConfig = ObservabilityConfig()

    def get_tv(self, tv_id: str) -> TvConfig:
        """Return a TV by ID or provide the valid IDs in the error."""
        for tv in self.tvs:
            if tv.id == tv_id:
                return tv
        valid_ids = ", ".join(tv.id for tv in self.tvs)
        raise ConfigurationError(f"Unknown TV ID {tv_id!r}. Configured IDs: {valid_ids}")


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

    return ApplicationConfig(
        tvs=tvs,
        reconcile_interval=reconcile_interval,
        power_on_delay=power_on_delay,
        observability=observability,
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
