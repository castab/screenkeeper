from __future__ import annotations

import pytest

from signage_controller.config import ConfigurationError, load_config


def test_load_config_parses_multiple_tvs(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """reconcile_interval: 45
power_on_delay: 12
tvs:
  - id: left
    name: Left Menu
    host: 192.168.50.21
    desired_input: HDMI_1
    desired_volume: 0
  - id: right
    name: Right Menu
    host: 192.168.50.22
    desired_input: HDMI_2
    desired_volume: 7
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.reconcile_interval == 45
    assert config.power_on_delay == 12
    assert [tv.id for tv in config.tvs] == ["left", "right"]
    assert config.get_tv("right").desired_volume == 7


def test_load_config_rejects_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """tvs:
  - id: same
    name: First
    host: 192.168.1.1
    desired_input: HDMI_1
    desired_volume: 0
  - id: same
    name: Second
    host: 192.168.1.2
    desired_input: HDMI_1
    desired_volume: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="unique"):
        load_config(path)


def test_load_config_parses_optional_metrics(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """metrics:
  enabled: true
  host: 0.0.0.0
  port: 9999
tvs:
  - id: left
    name: Left Menu
    host: 192.168.50.21
    desired_input: HDMI_1
    desired_volume: 0
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.metrics is not None
    assert config.metrics.enabled is True
    assert config.metrics.host == "0.0.0.0"
    assert config.metrics.port == 9999


def test_load_config_metrics_absent_by_default(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """tvs:
  - id: left
    name: Left Menu
    host: 192.168.50.21
    desired_input: HDMI_1
    desired_volume: 0
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.metrics is None


def test_load_config_metrics_block_defaults_enabled_true(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """metrics: {}
tvs:
  - id: left
    name: Left Menu
    host: 192.168.50.21
    desired_input: HDMI_1
    desired_volume: 0
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.metrics is not None
    assert config.metrics.enabled is True
    assert config.metrics.host == "127.0.0.1"
    assert config.metrics.port == 9464


def test_load_config_rejects_out_of_range_metrics_port(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """metrics:
  port: 70000
tvs:
  - id: left
    name: Left Menu
    host: 192.168.50.21
    desired_input: HDMI_1
    desired_volume: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="metrics.port"):
        load_config(path)
