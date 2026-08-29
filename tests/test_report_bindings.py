from __future__ import annotations

import json
from pathlib import Path

from signage_controller import __version__
from signage_controller.config import (
    ApplicationConfig,
    LokiConfig,
    ObservabilityConfig,
    PlaybackConfig,
    PlayerConfig,
    PrometheusConfig,
    TvConfig,
)
from signage_controller.control_plane.report import (
    build_agent_info,
    build_configured_bindings,
    build_heartbeat,
    utc_now,
)
from signage_controller.inventory.drm import Connector, Gpu, Inventory
from signage_controller.inventory.edid import EdidInfo


TV = TvConfig(
    id="dev-tv",
    name="Development LG TV",
    host="192.168.50.21",
    desired_input="HDMI_1",
    desired_volume=0,
)
PLAYER = PlayerConfig(
    id="dev-menu",
    name="Development Menu",
    media=Path("/srv/screenkeeper/media/secret-menu-v3.mp4"),
    tv_id="dev-tv",
    screen_name="DP-1",
)


def _config(**overrides) -> ApplicationConfig:
    fields = {
        "tvs": (TV,),
        "playback": PlaybackConfig(players=(PLAYER,)),
    }
    fields.update(overrides)
    return ApplicationConfig(**fields)


def test_configured_tv_is_reported_with_its_desired_state() -> None:
    bindings = build_configured_bindings(_config())

    assert bindings["tvs"] == [
        {
            "id": "dev-tv",
            "name": "Development LG TV",
            "host": "192.168.50.21",
            "desired_input": "HDMI_1",
            "desired_volume": 0,
            "driver": "lg-webos",
        }
    ]


def test_configured_tv_never_carries_a_client_key() -> None:
    """Pairing keys live in the state store and must never reach the network."""
    reported = build_configured_bindings(_config())["tvs"][0]

    assert "client_key" not in reported
    assert not any("key" in field for field in reported)


def test_playback_player_reports_its_tv_and_screen_mapping() -> None:
    bindings = build_configured_bindings(_config())

    assert bindings["playback_players"] == [
        {
            "id": "dev-menu",
            "name": "Development Menu",
            "tv_id": "dev-tv",
            "screen": None,
            "screen_name": "DP-1",
        }
    ]


def test_a_screen_index_is_reported_when_configured() -> None:
    player = PlayerConfig(id="left", name="Left", media=Path("/media/a.mp4"), screen=0)
    bindings = build_configured_bindings(_config(playback=PlaybackConfig(players=(player,))))

    assert bindings["playback_players"][0]["screen"] == 0
    assert bindings["playback_players"][0]["screen_name"] is None
    assert bindings["playback_players"][0]["tv_id"] is None


def test_no_local_media_path_leaks() -> None:
    serialized = json.dumps(build_configured_bindings(_config()))

    assert "secret-menu-v3" not in serialized
    assert "/srv/screenkeeper" not in serialized
    assert "media" not in serialized


def test_absent_playback_section_reports_an_empty_player_list() -> None:
    bindings = build_configured_bindings(_config(playback=None))

    assert bindings["playback_players"] == []
    assert len(bindings["tvs"]) == 1


def test_observability_credentials_never_reach_the_report() -> None:
    """Telemetry config names environment variables; neither name nor value belongs here."""
    config = _config(
        observability=ObservabilityConfig(
            loki=LokiConfig(url="https://loki.example.com/push", bearer_token_env="LOKI_TOKEN"),
            prometheus=PrometheusConfig(
                remote_write_url="https://prom.example.com/write",
                bearer_token_env="PROMETHEUS_TOKEN",
            ),
        )
    )

    serialized = json.dumps(build_heartbeat(config, Inventory()))

    assert "LOKI_TOKEN" not in serialized
    assert "PROMETHEUS_TOKEN" not in serialized
    assert "loki.example.com" not in serialized


def test_agent_info_reports_this_installation() -> None:
    agent = build_agent_info()

    assert agent["screenkeeper_version"] == __version__
    assert agent["os"]
    assert agent["hostname"]
    assert set(agent) == {
        "screenkeeper_version",
        "hostname",
        "os",
        "kernel",
        "architecture",
    }


def test_heartbeat_keeps_observed_and_configured_separate() -> None:
    inventory = Inventory(
        gpus=(Gpu(card="card0", vendor_id="0x8086"),),
        connectors=(
            Connector(name="DP-1", status="connected", edid=EdidInfo(sha256="abc123")),
        ),
    )

    report = build_heartbeat(_config(), inventory)

    assert set(report) == {"reported_at", "agent", "inventory", "configured_bindings"}
    # Observed: the kernel says something is plugged into DP-1.
    assert report["inventory"]["connectors"][0]["edid"]["sha256"] == "abc123"
    # Configured: an operator said dev-menu drives DP-1 alongside dev-tv.
    assert report["configured_bindings"]["playback_players"][0]["screen_name"] == "DP-1"


def test_reported_at_is_a_utc_instant() -> None:
    report = build_heartbeat(_config(), Inventory())

    assert report["reported_at"].endswith("Z")
    assert utc_now().endswith("Z")


def test_an_explicit_timestamp_is_honoured() -> None:
    report = build_heartbeat(_config(), Inventory(), reported_at="2026-08-29T04:12:00Z")

    assert report["reported_at"] == "2026-08-29T04:12:00Z"


def test_an_empty_inventory_is_a_valid_heartbeat() -> None:
    report = build_heartbeat(_config(), Inventory())

    assert report["inventory"] == {"gpus": [], "connectors": []}
