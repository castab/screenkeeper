from __future__ import annotations

from signage_controller.state_store import StateStore


def test_separate_tv_ids_use_separate_client_keys(tmp_path) -> None:
    store = StateStore(tmp_path / "runtime-state")

    store.set_client_key("left", "left-key")
    store.set_client_key("right", "right-key")

    reloaded = StateStore(tmp_path / "runtime-state")
    assert reloaded.get_client_key("left") == "left-key"
    assert reloaded.get_client_key("right") == "right-key"
    assert reloaded.get_client_key("unknown") is None
    assert (tmp_path / "runtime-state" / "state.json").stat().st_mode & 0o777 == 0o600
