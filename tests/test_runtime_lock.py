from __future__ import annotations

import pytest

from signage_controller.runtime_lock import (
    ControllerAlreadyRunningError,
    acquire_controller_lock,
)


def test_controller_lock_rejects_second_process(tmp_path) -> None:
    with acquire_controller_lock(tmp_path):
        with pytest.raises(ControllerAlreadyRunningError, match="active"):
            with acquire_controller_lock(tmp_path):
                pass


def test_separate_lock_names_do_not_block_each_other(tmp_path) -> None:
    # TV control and mpv playback are independent runtimes and must be able to
    # run concurrently against one state directory.
    with acquire_controller_lock(tmp_path, name="controller.lock"):
        with acquire_controller_lock(tmp_path, name="playback.lock", command="playback run"):
            pass


def test_lock_error_names_the_conflicting_command(tmp_path) -> None:
    with acquire_controller_lock(tmp_path, name="playback.lock", command="playback run"):
        with pytest.raises(ControllerAlreadyRunningError, match="playback run"):
            with acquire_controller_lock(tmp_path, name="playback.lock", command="playback run"):
                pass
