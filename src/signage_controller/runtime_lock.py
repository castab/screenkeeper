"""Process-level lock for the long-running controller command."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class ControllerAlreadyRunningError(RuntimeError):
    """Raised when another controller process owns the runtime lock."""


@contextmanager
def acquire_controller_lock(state_dir: Path) -> Iterator[None]:
    """Acquire an exclusive Linux advisory lock for one controller process."""
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    descriptor = os.open(state_dir / "controller.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            raise ControllerAlreadyRunningError("Another signage-controller run process is active.") from err
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
