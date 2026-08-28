from __future__ import annotations

import shlex

import pytest

from signage_controller import __version__
from signage_controller.cli import (
    UPDATE_COMMANDS,
    _playback_command,
    _require_managed_install,
    _versions,
    build_parser,
)
from signage_controller.config import (
    ApplicationConfig,
    ConfigurationError,
    PlaybackConfig,
    PlayerConfig,
    TvConfig,
)
from signage_controller.playback.mpv import build_mpv_argv
from signage_controller.playback.supervisor import resolve_socket_dir
from signage_controller.updater import (
    DEFAULT_KEEP_VERSIONS,
    InstallLayout,
    UpdateError,
    activate_version,
)


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


def test_parser_reports_the_package_version(capsys) -> None:
    # `upgrade` compares this against the release tag, so it must be the
    # single declared version rather than a hardcoded string.
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip().endswith(__version__)


def test_parser_accepts_upgrade_defaults() -> None:
    args = build_parser().parse_args(["upgrade"])

    assert args.command == "upgrade"
    assert args.check is False
    assert args.target_version is None
    assert args.no_restart is False
    assert args.keep == DEFAULT_KEEP_VERSIONS


def test_parser_accepts_upgrade_options() -> None:
    args = build_parser().parse_args(
        ["upgrade", "--check", "--to", "0.2.0", "--require-checksum", "--keep", "5"]
    )

    assert args.check is True
    assert args.target_version == "0.2.0"
    assert args.require_checksum is True
    assert args.keep == 5


def test_parser_accepts_rollback_and_versions() -> None:
    rollback = build_parser().parse_args(["rollback", "--to", "0.1.0"])
    assert rollback.command == "rollback"
    assert rollback.target_version == "0.1.0"

    assert build_parser().parse_args(["versions"]).command == "versions"


def test_update_commands_are_dispatched_without_loading_configuration() -> None:
    # An upgrade has to work on a host whose config.yaml is missing or broken.
    for command in ("upgrade", "rollback", "versions"):
        assert command in UPDATE_COMMANDS


def test_versions_reports_a_development_checkout_as_unmanaged(tmp_path, capsys) -> None:
    exit_code = _versions(InstallLayout(tmp_path / "absent"))

    assert exit_code == 0
    assert "not a managed installation" in capsys.readouterr().out


def test_versions_marks_the_active_version(tmp_path, capsys) -> None:
    layout = InstallLayout(tmp_path / "opt")
    for version in ("0.1.0", "0.2.0"):
        entry_point = layout.entry_point(version)
        entry_point.parent.mkdir(parents=True)
        entry_point.write_text("#!/bin/sh\n", encoding="utf-8")
    activate_version(layout, "0.1.0")

    assert _versions(layout) == 0

    output = capsys.readouterr().out
    assert "0.1.0  (active)" in output
    assert "0.2.0\n" in output


def test_require_managed_install_explains_how_to_install(tmp_path) -> None:
    with pytest.raises(UpdateError, match="scripts/install.sh"):
        _require_managed_install(InstallLayout(tmp_path / "absent"))


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
