from __future__ import annotations

from pathlib import Path

import pytest

from signage_controller.config import ConfigurationError, load_config


BASE_TVS = """tvs:
  - id: dev-tv
    name: Development LG TV
    host: 192.168.1.50
    desired_input: HDMI_1
    desired_volume: 0
"""


def _write(path: Path, playback_yaml: str) -> Path:
    config_path = path / "config.yaml"
    config_path.write_text(BASE_TVS + playback_yaml, encoding="utf-8")
    return config_path


def test_playback_absent_is_none(tmp_path) -> None:
    config = load_config(_write(tmp_path, ""))

    assert config.playback is None
    with pytest.raises(ConfigurationError, match="No 'playback' section"):
        config.get_player("anything")


def test_playback_minimal_defaults(tmp_path) -> None:
    config = load_config(
        _write(
            tmp_path,
            """playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
""",
        )
    )

    assert config.playback is not None
    assert config.playback.mpv_binary == "mpv"
    assert config.playback.restart_delay == 2.0
    assert config.playback.hwdec == "auto"
    assert config.playback.fullscreen is True
    assert config.playback.loop is True
    assert config.playback.audio is False
    assert len(config.playback.players) == 1


def test_playback_full_player_fields(tmp_path) -> None:
    config = load_config(
        _write(
            tmp_path,
            """playback:
  mpv_binary: /usr/bin/mpv
  restart_delay: 5
  hwdec: vaapi
  fullscreen: false
  loop: false
  audio: true
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
      tv_id: dev-tv
      screen: 1
""",
        )
    )

    playback = config.playback
    assert playback is not None
    assert playback.mpv_binary == "/usr/bin/mpv"
    assert playback.restart_delay == 5
    assert playback.hwdec == "vaapi"
    assert playback.fullscreen is False
    assert playback.loop is False
    assert playback.audio is True

    player = config.get_player("dev-menu")
    assert player.tv_id == "dev-tv"
    assert player.screen == 1
    assert player.screen_name is None


def test_playback_many_players(tmp_path) -> None:
    config = load_config(
        _write(
            tmp_path,
            """playback:
  players:
    - id: left
      name: Left Menu
      media: ./media/left.mp4
      screen_name: DP-1
    - id: right
      name: Right Menu
      media: ./media/right.mp4
      screen_name: DP-2
""",
        )
    )

    assert [p.id for p in config.playback.players] == ["left", "right"]
    assert config.get_player("right").screen_name == "DP-2"


def test_playback_rejects_duplicate_player_ids(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="unique"):
        load_config(
            _write(
                tmp_path,
                """playback:
  players:
    - id: same
      name: First
      media: ./media/a.mp4
    - id: same
      name: Second
      media: ./media/b.mp4
""",
            )
        )


def test_playback_rejects_screen_and_screen_name_together(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="mutually exclusive"):
        load_config(
            _write(
                tmp_path,
                """playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
      screen: 0
      screen_name: DP-1
""",
            )
        )


def test_playback_rejects_negative_screen(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="non-negative integer"):
        load_config(
            _write(
                tmp_path,
                """playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
      screen: -1
""",
            )
        )


def test_playback_rejects_boolean_screen(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="non-negative integer"):
        load_config(
            _write(
                tmp_path,
                """playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
      screen: true
""",
            )
        )


def test_playback_rejects_unknown_tv_id(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="does not match a configured TV"):
        load_config(
            _write(
                tmp_path,
                """playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
      tv_id: no-such-tv
""",
            )
        )


def test_playback_tv_id_optional(tmp_path) -> None:
    config = load_config(
        _write(
            tmp_path,
            """playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
""",
        )
    )

    assert config.get_player("dev-menu").tv_id is None


def test_playback_relative_media_resolves_against_config_dir(tmp_path, monkeypatch) -> None:
    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    config_path = sub_dir / "config.yaml"
    config_path.write_text(
        BASE_TVS
        + """playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
""",
        encoding="utf-8",
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = load_config(config_path)

    assert config.get_player("dev-menu").media == (sub_dir / "media" / "menu.mp4").resolve()


def test_playback_absolute_media_kept_as_is(tmp_path) -> None:
    absolute_media = (tmp_path / "abs" / "menu.mp4").resolve()
    config = load_config(
        _write(
            tmp_path,
            f"""playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: {absolute_media.as_posix()}
""",
        )
    )

    assert config.get_player("dev-menu").media == absolute_media


def test_playback_media_need_not_exist(tmp_path) -> None:
    config = load_config(
        _write(
            tmp_path,
            """playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/does-not-exist.mp4
""",
        )
    )

    player = config.get_player("dev-menu")
    assert not player.media.exists()


def test_playback_rejects_non_mapping_section(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="mapping"):
        load_config(_write(tmp_path, "playback: not-a-mapping\n"))


def test_playback_rejects_non_list_players(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="list"):
        load_config(
            _write(
                tmp_path,
                """playback:
  players: not-a-list
""",
            )
        )


def test_playback_rejects_non_mapping_player_entry(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="Player entry 1"):
        load_config(
            _write(
                tmp_path,
                """playback:
  players:
    - just-a-string
""",
            )
        )


def test_playback_rejects_empty_name(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="'name'"):
        load_config(
            _write(
                tmp_path,
                """playback:
  players:
    - id: dev-menu
      name: ""
      media: ./media/menu.mp4
""",
            )
        )


def test_playback_rejects_empty_media(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="'media'"):
        load_config(
            _write(
                tmp_path,
                """playback:
  players:
    - id: dev-menu
      name: Development Menu
      media: ""
""",
            )
        )


def test_playback_rejects_negative_restart_delay(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="restart_delay"):
        load_config(
            _write(
                tmp_path,
                """playback:
  restart_delay: -1
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
""",
            )
        )


def test_playback_allows_zero_restart_delay(tmp_path) -> None:
    config = load_config(
        _write(
            tmp_path,
            """playback:
  restart_delay: 0
  players:
    - id: dev-menu
      name: Development Menu
      media: ./media/menu.mp4
""",
        )
    )

    assert config.playback.restart_delay == 0


def test_load_config_without_playback_matches_tv_only_behavior(tmp_path) -> None:
    config = load_config(_write(tmp_path, ""))

    assert config.reconcile_interval == 30
    assert config.power_on_delay == 15
    assert [tv.id for tv in config.tvs] == ["dev-tv"]
    assert config.playback is None
