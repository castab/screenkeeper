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
