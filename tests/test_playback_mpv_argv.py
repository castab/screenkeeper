from __future__ import annotations

from pathlib import Path

from signage_controller.config import PlaybackConfig, PlayerConfig
from signage_controller.playback.mpv import build_mpv_argv


def _player(**overrides) -> PlayerConfig:
    fields = dict(id="dev-menu", name="Development Menu", media=Path("/media/menu.mp4"))
    fields.update(overrides)
    return PlayerConfig(**fields)


def _playback(**overrides) -> PlaybackConfig:
    fields: dict = {}
    fields.update(overrides)
    return PlaybackConfig(**fields)


SOCKET = Path("/run/screenkeeper/mpv/dev-menu.sock")


def test_default_argv_contains_expected_flags() -> None:
    argv = build_mpv_argv(_player(), _playback(), SOCKET)

    assert argv[0] == "mpv"
    assert "--no-config" in argv
    assert "--fullscreen" in argv
    assert "--loop-file=inf" in argv
    assert "--no-osc" in argv
    assert "--osd-level=0" in argv
    assert "--input-default-bindings=no" in argv
    assert "--input-terminal=no" in argv
    assert "--audio=no" in argv
    assert "--hwdec=auto" in argv
    assert f"--input-ipc-server={SOCKET}" in argv
    assert argv[-2:] == ["--", str(_player().media)]


def test_fullscreen_false_omits_flag() -> None:
    argv = build_mpv_argv(_player(), _playback(fullscreen=False), SOCKET)

    assert "--fullscreen" not in argv


def test_loop_false_omits_loop_file_flag() -> None:
    argv = build_mpv_argv(_player(), _playback(loop=False), SOCKET)

    assert "--loop-file=inf" not in argv


def test_audio_true_uses_auto_not_no() -> None:
    argv = build_mpv_argv(_player(), _playback(audio=True), SOCKET)

    assert "--audio=auto" in argv
    assert "--audio=no" not in argv


def test_hwdec_is_passed_through_verbatim() -> None:
    argv = build_mpv_argv(_player(), _playback(hwdec="vaapi"), SOCKET)

    assert "--hwdec=vaapi" in argv
    assert "--hwdec=auto" not in argv


def test_numeric_screen_sets_screen_and_fs_screen() -> None:
    argv = build_mpv_argv(_player(screen=2), _playback(), SOCKET)

    assert "--screen=2" in argv
    assert "--fs-screen=2" in argv
    assert not any(flag.startswith("--screen-name") for flag in argv)
    assert not any(flag.startswith("--fs-screen-name") for flag in argv)


def test_named_screen_sets_screen_name_and_fs_screen_name() -> None:
    argv = build_mpv_argv(_player(screen_name="DP-1"), _playback(), SOCKET)

    assert "--screen-name=DP-1" in argv
    assert "--fs-screen-name=DP-1" in argv
    assert not any(flag.startswith("--screen=") for flag in argv)
    assert not any(flag.startswith("--fs-screen=") for flag in argv)


def test_no_screen_selector_omits_all_screen_flags() -> None:
    argv = build_mpv_argv(_player(), _playback(), SOCKET)

    screen_prefixes = ("--screen=", "--fs-screen=", "--screen-name=", "--fs-screen-name=")
    assert not any(flag.startswith(screen_prefixes) for flag in argv)


def test_ipc_socket_flag_present_exactly_once() -> None:
    argv = build_mpv_argv(_player(), _playback(), SOCKET)

    matches = [flag for flag in argv if flag.startswith("--input-ipc-server=")]
    assert matches == [f"--input-ipc-server={SOCKET}"]


def test_custom_mpv_binary_is_argv_zero() -> None:
    argv = build_mpv_argv(_player(), _playback(mpv_binary="/custom/path/mpv"), SOCKET)

    assert argv[0] == "/custom/path/mpv"


def test_media_path_with_spaces_stays_one_argv_element() -> None:
    media = Path("/tmp/my media/menu.mp4")
    argv = build_mpv_argv(_player(media=media), _playback(), SOCKET)

    assert argv[-1] == str(media)
    assert argv[-2] == "--"


def test_media_path_with_leading_dash_is_never_parsed_as_an_option() -> None:
    media = Path("/tmp/-weird.mp4")
    argv = build_mpv_argv(_player(media=media), _playback(), SOCKET)

    assert argv[-1] == str(media)
    assert argv[-2] == "--"
