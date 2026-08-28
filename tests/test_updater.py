from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from signage_controller.updater import (
    HttpError,
    InstallLayout,
    NoReleaseAvailableError,
    Release,
    UpdateError,
    activate_version,
    download_archive,
    install_release,
    normalize_version,
    prune_versions,
    resolve_release,
    restart_services,
    update_lock,
    version_key,
)


def _release_payload(tag: str = "v0.2.0", *, assets: list[dict] | None = None, **extra) -> bytes:
    payload = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "tarball_url": f"https://api.github.com/repos/castab/screenkeeper/tarball/{tag}",
        "assets": assets
        if assets is not None
        else [
            {
                "name": "signage_controller-0.2.0.tar.gz",
                "browser_download_url": "https://example.invalid/signage_controller-0.2.0.tar.gz",
            },
            {
                "name": "SHA256SUMS",
                "browser_download_url": "https://example.invalid/SHA256SUMS",
            },
        ],
    }
    payload.update(extra)
    return json.dumps(payload).encode()


def _source_tarball(version: str = "0.2.0", root: str = "signage_controller-0.2.0") -> bytes:
    """Build a minimal source archive shaped like a published sdist."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        content = f'__version__ = "{version}"\n'.encode()
        info = tarfile.TarInfo(f"{root}/src/signage_controller/__init__.py")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class FakeOpener:
    """Serves canned bytes per URL and records the fetch order."""

    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.requested.append(url)
        try:
            response = self.responses[url]
        except KeyError:  # pragma: no cover - a test wiring mistake
            raise AssertionError(f"unexpected fetch of {url}") from None
        if isinstance(response, Exception):
            raise response
        return response


class FakeRunner:
    """Records commands and fakes just enough of `venv` and the entry point."""

    def __init__(self, *, reported_version: str = "0.2.0", fail_on: str | None = None) -> None:
        self.commands: list[list[str]] = []
        self.reported_version = reported_version
        self.fail_on = fail_on

    def __call__(self, argv: Sequence[str]) -> str:
        parts = [str(part) for part in argv]
        self.commands.append(parts)
        if self.fail_on is not None and self.fail_on in " ".join(parts):
            raise UpdateError(f"Command failed: {self.fail_on}")
        if "venv" in parts:
            binaries = Path(parts[-1]) / "bin"
            binaries.mkdir(parents=True, exist_ok=True)
            for name in ("pip", "signage-controller"):
                (binaries / name).write_text("#!/bin/sh\n", encoding="utf-8")
        if parts[-1] == "--version":
            return f"signage-controller {self.reported_version}\n"
        return ""


# --- version handling --------------------------------------------------------


def test_version_key_orders_releases_numerically() -> None:
    versions = ["0.10.0", "0.2.0", "1.0.0", "0.2.1"]

    assert sorted(versions, key=version_key) == ["0.2.0", "0.2.1", "0.10.0", "1.0.0"]


def test_version_key_sorts_a_prerelease_before_its_final_release() -> None:
    assert version_key("0.2.0-rc1") < version_key("0.2.0")


def test_normalize_version_strips_the_tag_prefix() -> None:
    assert normalize_version("v0.2.0") == "0.2.0"
    assert normalize_version("0.2.0") == "0.2.0"


def test_normalize_version_rejects_a_non_version() -> None:
    with pytest.raises(UpdateError, match="Unrecognized version"):
        normalize_version("main")


# --- release resolution ------------------------------------------------------


def test_resolve_release_prefers_the_published_sdist_asset() -> None:
    url = "https://api.github.com/repos/castab/screenkeeper/releases/latest"
    opener = FakeOpener({url: _release_payload()})

    release = resolve_release("castab/screenkeeper", opener=opener)

    assert release.version == "0.2.0"
    assert release.tag == "v0.2.0"
    assert release.archive_name == "signage_controller-0.2.0.tar.gz"
    assert release.checksums_url == "https://example.invalid/SHA256SUMS"


def test_resolve_release_falls_back_to_the_source_tarball_without_assets() -> None:
    url = "https://api.github.com/repos/castab/screenkeeper/releases/latest"
    opener = FakeOpener({url: _release_payload(assets=[])})

    release = resolve_release("castab/screenkeeper", opener=opener)

    assert release.archive_url.endswith("/tarball/v0.2.0")
    # Nothing attests to a bare source tarball, so no checksum is claimed.
    assert release.checksums_url is None


def test_resolve_release_without_any_published_release_points_at_the_ref_install() -> None:
    # The repository's state today: tags are intended but none exist yet.
    url = "https://api.github.com/repos/castab/screenkeeper/releases/latest"
    opener = FakeOpener({url: HttpError(404, url, "Not Found")})

    with pytest.raises(NoReleaseAvailableError, match="install.sh --ref main"):
        resolve_release("castab/screenkeeper", opener=opener)


def test_resolve_release_for_an_unknown_tag_names_the_missing_tag() -> None:
    url = "https://api.github.com/repos/castab/screenkeeper/releases/tags/v9.9.9"
    opener = FakeOpener({url: HttpError(404, url, "Not Found")})

    with pytest.raises(UpdateError, match="no release tagged v9.9.9"):
        resolve_release("castab/screenkeeper", "9.9.9", opener=opener)


def test_resolve_release_propagates_a_non_404_failure() -> None:
    url = "https://api.github.com/repos/castab/screenkeeper/releases/latest"
    opener = FakeOpener({url: HttpError(503, url, "Service Unavailable")})

    with pytest.raises(HttpError, match="503"):
        resolve_release("castab/screenkeeper", opener=opener)


def test_resolve_release_rejects_a_draft_release() -> None:
    url = "https://api.github.com/repos/castab/screenkeeper/releases/tags/v0.2.0"
    opener = FakeOpener({url: _release_payload(draft=True)})

    with pytest.raises(UpdateError, match="is a draft"):
        resolve_release("castab/screenkeeper", "0.2.0", opener=opener)


# --- download verification ---------------------------------------------------


def _release_with_checksums() -> Release:
    return Release(
        version="0.2.0",
        tag="v0.2.0",
        archive_url="https://example.invalid/archive.tar.gz",
        archive_name="archive.tar.gz",
        checksums_url="https://example.invalid/SHA256SUMS",
    )


def test_download_archive_accepts_a_matching_published_checksum(tmp_path: Path) -> None:
    payload = _source_tarball()
    digest = hashlib.sha256(payload).hexdigest()
    opener = FakeOpener(
        {
            "https://example.invalid/archive.tar.gz": payload,
            "https://example.invalid/SHA256SUMS": f"{digest}  archive.tar.gz\n".encode(),
        }
    )

    archive = download_archive(_release_with_checksums(), tmp_path, opener=opener)

    assert archive.read_bytes() == payload


def test_download_archive_rejects_a_mismatched_checksum(tmp_path: Path) -> None:
    opener = FakeOpener(
        {
            "https://example.invalid/archive.tar.gz": _source_tarball(),
            "https://example.invalid/SHA256SUMS": b"%s  archive.tar.gz\n" % (b"0" * 64),
        }
    )

    with pytest.raises(UpdateError, match="Checksum mismatch"):
        download_archive(_release_with_checksums(), tmp_path, opener=opener)
    assert list(tmp_path.iterdir()) == []


def test_download_archive_requires_a_checksum_when_asked(tmp_path: Path) -> None:
    release = Release(
        version="0.2.0",
        tag="v0.2.0",
        archive_url="https://example.invalid/archive.tar.gz",
        archive_name="archive.tar.gz",
        checksums_url=None,
    )
    opener = FakeOpener({"https://example.invalid/archive.tar.gz": _source_tarball()})

    with pytest.raises(UpdateError, match="--require-checksum"):
        download_archive(release, tmp_path, opener=opener, require_checksum=True)


def test_download_archive_without_a_checksum_warns_but_proceeds(tmp_path: Path, caplog) -> None:
    release = Release(
        version="0.2.0",
        tag="v0.2.0",
        archive_url="https://example.invalid/archive.tar.gz",
        archive_name="archive.tar.gz",
        checksums_url=None,
    )
    opener = FakeOpener({"https://example.invalid/archive.tar.gz": _source_tarball()})

    with caplog.at_level("WARNING"):
        archive = download_archive(release, tmp_path, opener=opener)

    assert archive.exists()
    assert "could not be verified" in caplog.text


# --- installation ------------------------------------------------------------


def _install_fixture(tmp_path: Path) -> tuple[InstallLayout, Release, FakeOpener]:
    layout = InstallLayout(tmp_path / "opt")
    release = Release(
        version="0.2.0",
        tag="v0.2.0",
        archive_url="https://example.invalid/archive.tar.gz",
        archive_name="archive.tar.gz",
        checksums_url=None,
    )
    opener = FakeOpener({"https://example.invalid/archive.tar.gz": _source_tarball()})
    return layout, release, opener


def test_install_release_builds_a_version_directory_without_activating_it(tmp_path: Path) -> None:
    layout, release, opener = _install_fixture(tmp_path)
    runner = FakeRunner()

    version_dir = install_release(release, layout, opener=opener, runner=runner)

    assert version_dir == layout.version_dir("0.2.0")
    assert layout.entry_point("0.2.0").exists()
    # Activation is a separate, explicit step.
    assert not layout.current_link.exists()
    # The archive and extracted source are not kept in the installed tree.
    assert sorted(entry.name for entry in version_dir.iterdir()) == ["venv"]
    assert [entry.name for entry in layout.versions_dir.iterdir()] == ["0.2.0"]


def test_install_release_builds_the_venv_at_its_final_path(tmp_path: Path) -> None:
    # pip bakes absolute paths into console-script shebangs and pyvenv.cfg, so
    # a venv built anywhere other than its final home is broken once activated.
    layout, release, opener = _install_fixture(tmp_path)
    runner = FakeRunner()

    install_release(release, layout, opener=opener, runner=runner)

    venv_commands = [command for command in runner.commands if "venv" in command]
    assert venv_commands, "expected the venv to be created"
    assert venv_commands[0][-1] == str(layout.version_dir("0.2.0") / "venv")
    # The smoke test must exercise the entry point at that same final path.
    assert runner.commands[-1][0] == str(layout.entry_point("0.2.0"))


def test_install_release_leaves_a_running_install_untouched_on_failure(tmp_path: Path) -> None:
    layout, release, opener = _install_fixture(tmp_path)
    install_release(release, layout, opener=opener, runner=FakeRunner())
    activate_version(layout, "0.2.0")

    newer = Release(
        version="0.3.0",
        tag="v0.3.0",
        archive_url="https://example.invalid/archive.tar.gz",
        archive_name="archive.tar.gz",
        checksums_url=None,
    )
    with pytest.raises(UpdateError, match="pip install"):
        install_release(newer, layout, opener=opener, runner=FakeRunner(fail_on="pip install"))

    assert layout.current_version() == "0.2.0"
    assert layout.installed_versions() == ["0.2.0"]
    # Neither the partial build nor the source directory is left behind.
    assert [entry.name for entry in layout.versions_dir.iterdir()] == ["0.2.0"]


def test_install_release_names_the_missing_package_without_venv_support(tmp_path: Path) -> None:
    # Debian and Ubuntu ship `venv` without `ensurepip` unless python3-venv is
    # installed; the raw failure from the venv module does not say so.
    layout, release, opener = _install_fixture(tmp_path)

    with pytest.raises(UpdateError, match="python3-venv"):
        install_release(
            release, layout, opener=opener, runner=FakeRunner(fail_on="import ensurepip")
        )
    assert layout.installed_versions() == []


def test_install_release_rejects_a_build_reporting_the_wrong_version(tmp_path: Path) -> None:
    layout, release, opener = _install_fixture(tmp_path)

    with pytest.raises(UpdateError, match="does not match the release version"):
        install_release(
            release, layout, opener=opener, runner=FakeRunner(reported_version="0.1.0")
        )
    assert layout.installed_versions() == []


def test_install_release_reuses_an_existing_version_directory(tmp_path: Path) -> None:
    layout, release, opener = _install_fixture(tmp_path)
    install_release(release, layout, opener=opener, runner=FakeRunner())

    second_runner = FakeRunner()
    install_release(release, layout, opener=opener, runner=second_runner)

    assert second_runner.commands == []


def test_install_release_rebuilds_when_forced(tmp_path: Path) -> None:
    layout, release, opener = _install_fixture(tmp_path)
    install_release(release, layout, opener=opener, runner=FakeRunner())

    second_runner = FakeRunner()
    install_release(release, layout, opener=opener, runner=second_runner, force=True)

    assert any("venv" in command for command in second_runner.commands)


# --- activation and rollback -------------------------------------------------


def _fake_installed(layout: InstallLayout, version: str) -> None:
    entry_point = layout.entry_point(version)
    entry_point.parent.mkdir(parents=True, exist_ok=True)
    entry_point.write_text("#!/bin/sh\n", encoding="utf-8")


def test_activate_version_flips_the_symlink_to_a_relative_target(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")
    _fake_installed(layout, "0.2.0")

    activate_version(layout, "0.2.0")

    assert layout.current_version() == "0.2.0"
    # Relative, so the tree stays relocatable.
    assert not os.path.isabs(os.readlink(layout.current_link))


def test_activate_version_replaces_an_existing_symlink(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")
    _fake_installed(layout, "0.2.0")
    _fake_installed(layout, "0.3.0")
    activate_version(layout, "0.2.0")

    activate_version(layout, "0.3.0")

    assert layout.current_version() == "0.3.0"
    # Rolling back is the same swap in reverse; the old tree is still present.
    activate_version(layout, "0.2.0")
    assert layout.current_version() == "0.2.0"


def test_activate_version_rejects_a_version_that_is_not_installed(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")
    layout.versions_dir.mkdir(parents=True)

    with pytest.raises(UpdateError, match="is not installed"):
        activate_version(layout, "0.2.0")


def test_current_version_is_none_without_a_symlink(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")
    layout.versions_dir.mkdir(parents=True)

    assert layout.current_version() is None


# --- inventory and pruning ---------------------------------------------------


def test_installed_versions_ignores_source_and_junk_directories(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")
    layout.versions_dir.mkdir(parents=True)
    for name in (".source-0.3.0-42", "not-a-version"):
        (layout.versions_dir / name).mkdir()
    for version in ("0.2.0", "0.10.0"):
        _fake_installed(layout, version)

    assert layout.installed_versions() == ["0.2.0", "0.10.0"]


def test_installed_versions_ignores_an_interrupted_build(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")
    _fake_installed(layout, "0.2.0")
    # A version directory with no usable entry point: a build killed part-way.
    (layout.versions_dir / "0.3.0" / "venv" / "bin").mkdir(parents=True)

    assert layout.installed_versions() == ["0.2.0"]
    assert layout.is_installed("0.3.0") is False


def test_prune_versions_keeps_the_newest_and_never_the_active_one(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")
    for version in ("0.1.0", "0.2.0", "0.3.0", "0.4.0"):
        _fake_installed(layout, version)
    activate_version(layout, "0.4.0")

    removed = prune_versions(layout, keep=2)

    assert removed == ["0.1.0", "0.2.0"]
    assert layout.installed_versions() == ["0.3.0", "0.4.0"]


def test_prune_versions_never_removes_the_active_version(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")
    for version in ("0.1.0", "0.2.0", "0.3.0"):
        _fake_installed(layout, version)
    # Deliberately active on an older build, as it would be after a rollback.
    activate_version(layout, "0.1.0")

    prune_versions(layout, keep=1)

    assert "0.1.0" in layout.installed_versions()
    assert layout.current_version() == "0.1.0"


def test_prune_versions_rejects_keeping_nothing(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")

    with pytest.raises(UpdateError, match="at least 1"):
        prune_versions(layout, keep=0)


# --- layout detection and locking --------------------------------------------


def test_detect_infers_the_root_from_a_managed_venv_prefix(tmp_path: Path, monkeypatch) -> None:
    prefix = tmp_path / "opt" / "versions" / "0.2.0" / "venv"
    monkeypatch.setattr("sys.prefix", str(prefix))

    detected = InstallLayout.detect()

    assert detected is not None
    assert detected.root == tmp_path / "opt"


def test_detect_returns_none_in_a_development_checkout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.prefix", str(tmp_path / "screenkeeper" / ".venv"))

    assert InstallLayout.detect() is None


def test_from_environment_prefers_an_explicit_install_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SCREENKEEPER_INSTALL_ROOT", str(tmp_path / "elsewhere"))

    assert InstallLayout.from_environment().root == tmp_path / "elsewhere"


def test_update_lock_refuses_a_concurrent_update(tmp_path: Path) -> None:
    layout = InstallLayout(tmp_path / "opt")

    with update_lock(layout):
        with pytest.raises(UpdateError, match="already running"):
            with update_lock(layout):
                pass  # pragma: no cover - the lock must not be granted


def test_update_lock_does_not_restrict_install_root_permissions(tmp_path: Path) -> None:
    # The install tree must stay traversable for every user whose
    # /usr/local/bin/signage-controller resolves through it.
    layout = InstallLayout(tmp_path / "opt")

    with update_lock(layout):
        pass

    assert layout.root.stat().st_mode & 0o005


# --- service restarts --------------------------------------------------------


def test_restart_services_reports_units_that_failed_without_raising(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
    runner = FakeRunner(fail_on="screenkeeper-playback.service")

    failed = restart_services(("screenkeeper.service", "screenkeeper-playback.service"), runner=runner)

    assert failed == ["screenkeeper-playback.service"]


def test_restart_services_without_systemd_reports_every_unit(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    runner = FakeRunner()

    failed = restart_services(("screenkeeper.service",), runner=runner)

    assert failed == ["screenkeeper.service"]
    assert runner.commands == []
