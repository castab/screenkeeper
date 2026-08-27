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


def test_load_config_parses_optional_observability(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """observability:
  loki:
    url: https://loki.example.com/loki/api/v1/push
    bearer_token_env: LOKI_TOKEN
    labels:
      environment: production
  prometheus:
    remote_write_url: https://prometheus.example.com/api/v1/write
    bearer_token_env: PROMETHEUS_TOKEN
    job: signage_controller
    instance: signage-host-01
    push_interval: 10
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

    assert config.observability.loki is not None
    assert config.observability.loki.bearer_token_env == "LOKI_TOKEN"
    assert config.observability.loki.labels == (("environment", "production"),)
    assert config.observability.prometheus is not None
    assert config.observability.prometheus.bearer_token_env == "PROMETHEUS_TOKEN"
    assert config.observability.prometheus.instance == "signage-host-01"


def test_load_config_rejects_reserved_loki_label(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """observability:
  loki:
    url: https://loki.example.com/loki/api/v1/push
    bearer_token_env: LOKI_TOKEN
    labels:
      tv_id: not-allowed
tvs:
  - id: left
    name: Left Menu
    host: 192.168.50.21
    desired_input: HDMI_1
    desired_volume: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="reserved"):
        load_config(path)


def test_load_config_rejects_cleartext_telemetry_url(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """observability:
  loki:
    url: http://loki.example.com/loki/api/v1/push
    bearer_token_env: LOKI_TOKEN
tvs:
  - id: left
    name: Left Menu
    host: 192.168.50.21
    desired_input: HDMI_1
    desired_volume: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="HTTPS"):
        load_config(path)
