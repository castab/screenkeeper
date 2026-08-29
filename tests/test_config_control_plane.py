from __future__ import annotations

from pathlib import Path

import pytest

from signage_controller.config import (
    DEFAULT_CONTROL_PLANE_TIMEOUT,
    DEFAULT_HEARTBEAT_INTERVAL,
    ConfigurationError,
    load_config,
)


BASE_TVS = """
tvs:
  - id: dev-tv
    name: Development LG TV
    host: 192.168.1.50
    desired_input: HDMI_1
    desired_volume: 0
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(BASE_TVS + body, encoding="utf-8")
    return path


def test_configuration_without_a_control_plane_section_stays_valid(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, ""))

    assert config.control_plane is None
    assert len(config.tvs) == 1


def test_valid_control_plane_configuration_parses(tmp_path: Path) -> None:
    config = load_config(
        _write(
            tmp_path,
            """
control_plane:
  base_url: https://screenkeeper.example.com
  heartbeat_interval: 45
  request_timeout: 5
""",
        )
    )

    assert config.control_plane is not None
    assert config.control_plane.base_url == "https://screenkeeper.example.com"
    assert config.control_plane.heartbeat_interval == 45.0
    assert config.control_plane.request_timeout == 5.0
    assert config.control_plane.allow_insecure_http is False


def test_control_plane_intervals_default_when_omitted(tmp_path: Path) -> None:
    config = load_config(
        _write(tmp_path, "\ncontrol_plane:\n  base_url: https://screenkeeper.example.com\n")
    )

    assert config.control_plane is not None
    assert config.control_plane.heartbeat_interval == DEFAULT_HEARTBEAT_INTERVAL
    assert config.control_plane.request_timeout == DEFAULT_CONTROL_PLANE_TIMEOUT


def test_a_trailing_slash_is_stripped_so_paths_join_cleanly(tmp_path: Path) -> None:
    config = load_config(
        _write(tmp_path, "\ncontrol_plane:\n  base_url: https://screenkeeper.example.com/\n")
    )

    assert config.control_plane is not None
    assert config.control_plane.base_url == "https://screenkeeper.example.com"


def test_malformed_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="control_plane.base_url"):
        load_config(_write(tmp_path, "\ncontrol_plane:\n  base_url: not a url\n"))


def test_missing_base_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="control_plane.base_url"):
        load_config(_write(tmp_path, "\ncontrol_plane:\n  heartbeat_interval: 30\n"))


def test_insecure_http_is_rejected_by_default(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="must use HTTPS"):
        load_config(_write(tmp_path, "\ncontrol_plane:\n  base_url: http://screenkeeper.example.com\n"))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
    ],
)
def test_loopback_http_is_accepted_without_an_override(tmp_path: Path, url: str) -> None:
    config = load_config(_write(tmp_path, f"\ncontrol_plane:\n  base_url: {url}\n"))

    assert config.control_plane is not None
    assert config.control_plane.base_url == url


def test_explicitly_enabled_development_http_is_accepted(tmp_path: Path) -> None:
    config = load_config(
        _write(
            tmp_path,
            """
control_plane:
  base_url: http://screenkeeper.dev.example.com
  allow_insecure_http: true
""",
        )
    )

    assert config.control_plane is not None
    assert config.control_plane.base_url == "http://screenkeeper.dev.example.com"
    assert config.control_plane.allow_insecure_http is True


def test_a_non_http_scheme_is_rejected_even_when_insecure_http_is_allowed(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="control_plane.base_url"):
        load_config(
            _write(
                tmp_path,
                """
control_plane:
  base_url: ftp://screenkeeper.example.com
  allow_insecure_http: true
""",
            )
        )


def test_a_non_mapping_control_plane_section_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="'control_plane' must be a mapping"):
        load_config(_write(tmp_path, "\ncontrol_plane: https://screenkeeper.example.com\n"))


def test_a_zero_heartbeat_interval_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="heartbeat_interval"):
        load_config(
            _write(
                tmp_path,
                """
control_plane:
  base_url: https://screenkeeper.example.com
  heartbeat_interval: 0
""",
            )
        )


def test_a_non_boolean_allow_insecure_http_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="allow_insecure_http"):
        load_config(
            _write(
                tmp_path,
                """
control_plane:
  base_url: https://screenkeeper.example.com
  allow_insecure_http: "yes"
""",
            )
        )
