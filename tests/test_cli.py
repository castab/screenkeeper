from __future__ import annotations

import shlex

import pytest

from signage_controller.cli import _playback_command, build_parser
from signage_controller.config import (
    ApplicationConfig,
    ConfigurationError,
    PlaybackConfig,
    PlayerConfig,
    TvConfig,
)
from signage_controller.playback.mpv import build_mpv_argv
from signage_controller.playback.supervisor import resolve_socket_dir


def test_parser_accepts_playback_check() -> None:
    args = build_parser().parse_args(["playback", "check"])

    assert args.command == "playback"
    assert args.playback_command == "check"


def test_parser_accepts_playback_command_with_player_id() -> None:
    args = build_parser().parse_args(["playback", "command", "dev-menu"])

    assert args.command == "playback"
    assert args.playback_command == "command"
    assert args.player_id == "dev-menu"


def test_parser_accepts_playback_start_with_player_id() -> None:
    args = build_parser().parse_args(["playback", "start", "dev-menu"])

    assert args.command == "playback"
    assert args.playback_command == "start"
    assert args.player_id == "dev-menu"


def test_parser_accepts_playback_run() -> None:
    args = build_parser().parse_args(["playback", "run"])

    assert args.command == "playback"
    assert args.playback_command == "run"


def test_parser_keeps_existing_tv_commands_unchanged() -> None:
    for command in ("pair", "status", "inputs", "apply"):
        args = build_parser().parse_args([command, "dev-tv"])
        assert args.command == command
        assert args.tv_id == "dev-tv"

    run_args = build_parser().parse_args(["run"])
    assert run_args.command == "run"


def _config_with_player(media) -> tuple[ApplicationConfig, PlayerConfig, PlaybackConfig]:
    player = PlayerConfig(id="dev-menu", name="Development Menu", media=media)
    playback = PlaybackConfig(players=(player,))
    config = ApplicationConfig(
        tvs=(
            TvConfig(
                id="dev-tv",
                name="Development LG TV",
                host="192.168.1.50",
                desired_input="HDMI_1",
                desired_volume=0,
            ),
        ),
        playback=playback,
    )
    return config, player, playback


def test_playback_command_output_round_trips_to_build_mpv_argv(tmp_path, capsys) -> None:
    config, player, playback = _config_with_player(tmp_path / "menu.mp4")

    exit_code = _playback_command(config, "dev-menu")

    assert exit_code == 0
    printed_argv = shlex.split(capsys.readouterr().out.strip())
    socket_path = resolve_socket_dir() / "dev-menu.sock"
    assert printed_argv == build_mpv_argv(player, playback, socket_path)


def test_playback_command_quotes_media_paths_with_spaces(tmp_path, capsys) -> None:
    config, player, playback = _config_with_player(tmp_path / "my media" / "menu.mp4")

    assert _playback_command(config, "dev-menu") == 0

    printed = capsys.readouterr().out.strip()
    # Shell-quoted for human readability, and still recovers the exact path.
    assert shlex.split(printed)[-1] == str(player.media)


def test_playback_command_without_playback_section_returns_error(capsys) -> None:
    config = ApplicationConfig(
        tvs=(
            TvConfig(
                id="dev-tv",
                name="Development LG TV",
                host="192.168.1.50",
                desired_input="HDMI_1",
                desired_volume=0,
            ),
        )
    )

    assert _playback_command(config, "dev-menu") == 1
    assert capsys.readouterr().out == ""


def test_playback_command_unknown_player_id_raises_configuration_error(tmp_path) -> None:
    config, _, _ = _config_with_player(tmp_path / "menu.mp4")

    with pytest.raises(ConfigurationError, match="Unknown player ID"):
        _playback_command(config, "no-such-player")
