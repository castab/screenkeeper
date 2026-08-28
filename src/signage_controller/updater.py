"""Versioned host-native installation and self-update.

The deployment layout is a set of immutable version directories with one
atomically swapped `current` symlink::

    /opt/screenkeeper/
      versions/0.1.0/venv/
      versions/0.2.0/venv/
      current -> versions/0.2.0

An upgrade never mutates the installation it is running from. It builds the
new version under its own version directory, smoke-tests it there, and only
then repoints `current`. A failed build therefore leaves the running signage
host exactly as it was, and `rollback` is one more symlink swap rather than a
reinstall.

The new version is built at its final path rather than staged and renamed
because pip bakes absolute paths into console-script shebangs and `pyvenv.cfg`:
a virtual environment built somewhere else and moved into place is broken at
exactly the moment it is activated.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__


LOGGER = logging.getLogger(__name__)

DEFAULT_REPOSITORY = "castab/screenkeeper"
DEFAULT_INSTALL_ROOT = Path("/opt/screenkeeper")
DEFAULT_KEEP_VERSIONS = 3
DEFAULT_UNITS = ("screenkeeper.service", "screenkeeper-playback.service")
GITHUB_API_BASE = "https://api.github.com"
CHECKSUM_ASSET_NAME = "SHA256SUMS"
REQUEST_TIMEOUT = 60.0
UPDATE_AVAILABLE_EXIT_CODE = 10
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?$")


class UpdateError(RuntimeError):
    """Raised when an install, upgrade, or rollback cannot proceed safely."""


class NoReleaseAvailableError(UpdateError):
    """Raised when the repository has not published a release to install."""


class HttpError(UpdateError):
    """Raised for a non-success HTTP response, preserving the status code."""

    def __init__(self, status: int, url: str, reason: str = "") -> None:
        self.status = status
        self.url = url
        detail = f" {reason}" if reason else ""
        super().__init__(f"HTTP {status}{detail} for {url}")


Opener = Callable[[str], bytes]
Runner = Callable[[Sequence[str]], str]


def version_key(value: str) -> tuple[int, int, int, int, str]:
    """Return a sortable key for a version or tag name.

    A pre-release suffix sorts before the matching final release, so
    ``0.2.0-rc1`` is older than ``0.2.0``.
    """
    match = VERSION_RE.match(value.strip())
    if match is None:
        raise UpdateError(f"Unrecognized version {value!r}; expected MAJOR.MINOR.PATCH.")
    major, minor, patch, suffix = match.groups()
    return (int(major), int(minor), int(patch), 0 if suffix else 1, suffix or "")


def normalize_version(value: str) -> str:
    """Validate a version or tag name and return it without a leading 'v'."""
    if VERSION_RE.match(value.strip()) is None:
        raise UpdateError(f"Unrecognized version {value!r}; expected MAJOR.MINOR.PATCH.")
    return value.strip().removeprefix("v")


@dataclass(frozen=True, slots=True)
class Release:
    """One published release and the archive an upgrade should install."""

    version: str
    tag: str
    archive_url: str
    archive_name: str
    checksums_url: str | None = None


@dataclass(frozen=True, slots=True)
class InstallLayout:
    """Paths of a managed, versioned installation tree."""

    root: Path

    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    @property
    def current_link(self) -> Path:
        return self.root / "current"

    def version_dir(self, version: str) -> Path:
        return self.versions_dir / version

    def entry_point(self, version: str) -> Path:
        return self.version_dir(version) / "venv" / "bin" / "signage-controller"

    def is_installed(self, version: str) -> bool:
        """Report whether a version is complete enough to activate."""
        return self.entry_point(version).exists()

    def installed_versions(self) -> list[str]:
        """Return activatable versions, oldest first.

        A directory without a usable entry point is a build that was
        interrupted, not an installed version, so it is not listed and the
        next install of that version rebuilds over it.
        """
        if not self.versions_dir.is_dir():
            return []
        versions = []
        for entry in self.versions_dir.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            try:
                version_key(entry.name)
            except UpdateError:
                continue
            if self.is_installed(entry.name):
                versions.append(entry.name)
        return sorted(versions, key=version_key)

    def current_version(self) -> str | None:
        """Return the version `current` points at, without following it to disk."""
        try:
            target = os.readlink(self.current_link)
        except OSError:
            return None
        return Path(target).name or None

    @classmethod
    def detect(cls) -> InstallLayout | None:
        """Infer the install root from the running interpreter's location."""
        prefix = Path(sys.prefix)
        if prefix.name == "venv" and prefix.parent.parent.name == "versions":
            return cls(prefix.parent.parent.parent)
        return None

    @classmethod
    def from_environment(cls) -> InstallLayout:
        """Resolve the install root from the environment, the running venv, or the default."""
        override = os.environ.get("SCREENKEEPER_INSTALL_ROOT")
        if override:
            return cls(Path(override))
        detected = cls.detect()
        return detected if detected is not None else cls(DEFAULT_INSTALL_ROOT)


def default_repository() -> str:
    """Return the release repository, overridable for forks and testing."""
    return os.environ.get("SCREENKEEPER_REPOSITORY", DEFAULT_REPOSITORY)


def default_opener(url: str) -> bytes:
    """Fetch a URL over HTTPS, mapping transport failures to UpdateError."""
    if not url.startswith("https://"):
        raise UpdateError(f"Refusing to fetch a non-HTTPS URL: {url}")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"screenkeeper/{__version__}",
            "Accept": "application/vnd.github+json, application/octet-stream",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as err:
        raise HttpError(err.code, url, err.reason or "") from err
    except urllib.error.URLError as err:
        raise UpdateError(f"Could not reach {url}: {err.reason}") from err
    except OSError as err:
        raise UpdateError(f"Could not reach {url}: {err}") from err


def default_runner(argv: Sequence[str]) -> str:
    """Run a command, returning stdout and raising UpdateError on failure."""
    LOGGER.debug("running %s", shlex.join(str(part) for part in argv))
    try:
        completed = subprocess.run(
            [str(part) for part in argv],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as err:
        raise UpdateError(f"Command not found: {argv[0]}") from err
    except subprocess.CalledProcessError as err:
        detail = (err.stderr or err.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit status {err.returncode}"
        raise UpdateError(f"Command failed: {shlex.join(str(p) for p in argv)}: {tail}") from err
    return completed.stdout


def resolve_release(
    repository: str,
    version: str | None = None,
    *,
    opener: Opener = default_opener,
) -> Release:
    """Resolve the latest published release, or the release for one version."""
    if version is None:
        url = f"{GITHUB_API_BASE}/repos/{repository}/releases/latest"
    else:
        url = f"{GITHUB_API_BASE}/repos/{repository}/releases/tags/v{normalize_version(version)}"

    try:
        payload = opener(url)
    except HttpError as err:
        if err.status != 404:
            raise
        if version is None:
            raise NoReleaseAvailableError(
                f"{repository} has not published a release yet. Until the first tag exists, "
                "install from a branch with: scripts/install.sh --ref main"
            ) from err
        raise UpdateError(
            f"{repository} has no release tagged v{normalize_version(version)}."
        ) from err

    return _parse_release(payload, repository)


def _parse_release(payload: bytes, repository: str) -> Release:
    try:
        raw: Any = json.loads(payload)
    except json.JSONDecodeError as err:
        raise UpdateError(f"Invalid release metadata from {repository}: {err}") from err
    if not isinstance(raw, dict):
        raise UpdateError(f"Invalid release metadata from {repository}: expected an object.")

    tag = raw.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        raise UpdateError(f"Release metadata from {repository} has no tag name.")
    tag = tag.strip()
    if raw.get("draft"):
        raise UpdateError(f"Release {tag} is a draft and cannot be installed.")

    archive_url: str | None = None
    archive_name: str | None = None
    checksums_url: str | None = None
    assets = raw.get("assets")
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if name == CHECKSUM_ASSET_NAME:
            checksums_url = url
        elif name.endswith(".tar.gz") and archive_url is None:
            archive_url, archive_name = url, name

    if archive_url is None or archive_name is None:
        # No published sdist: fall back to the source archive for the tag. It is
        # installable, but nothing in the release attests to its contents.
        fallback = raw.get("tarball_url")
        if not isinstance(fallback, str) or not fallback:
            raise UpdateError(f"Release {tag} publishes no installable archive.")
        archive_url, archive_name = fallback, f"{tag}.tar.gz"
        checksums_url = None

    return Release(
        version=normalize_version(tag),
        tag=tag,
        archive_url=archive_url,
        archive_name=archive_name,
        checksums_url=checksums_url,
    )


def _lookup_checksum(document: str, archive_name: str) -> str | None:
    """Return the SHA-256 recorded for one file in a `sha256sum` document."""
    for line in document.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        digest, name = fields
        if name.lstrip("*") == archive_name:
            return digest.lower()
    return None


def download_archive(
    release: Release,
    destination: Path,
    *,
    opener: Opener = default_opener,
    require_checksum: bool = False,
) -> Path:
    """Download a release archive and verify its published checksum."""
    data = opener(release.archive_url)
    digest = hashlib.sha256(data).hexdigest()

    expected: str | None = None
    if release.checksums_url is not None:
        document = opener(release.checksums_url).decode("utf-8", errors="replace")
        expected = _lookup_checksum(document, release.archive_name)

    if expected is None:
        if require_checksum:
            raise UpdateError(
                f"Release {release.tag} publishes no {CHECKSUM_ASSET_NAME} entry for "
                f"{release.archive_name}, and --require-checksum was given."
            )
        LOGGER.warning(
            "%s: no published %s entry; the download could not be verified.",
            release.archive_name,
            CHECKSUM_ASSET_NAME,
        )
    elif expected != digest:
        raise UpdateError(
            f"Checksum mismatch for {release.archive_name}: "
            f"expected {expected}, downloaded {digest}."
        )

    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / release.archive_name
    archive.write_bytes(data)
    return archive


def _extract(archive: Path, destination: Path) -> Path:
    """Extract a source archive and return its single top-level directory."""
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            if hasattr(tarfile, "data_filter"):
                # Rejects absolute paths, parent traversal, and special files.
                tar.extractall(destination, filter="data")
            else:  # pragma: no cover - only on Python 3.11.0-3.11.3
                LOGGER.warning("This Python lacks tarfile filters; extracting without them.")
                tar.extractall(destination)
    except (tarfile.TarError, OSError) as err:
        raise UpdateError(f"Could not extract {archive.name}: {err}") from err

    entries = [entry for entry in destination.iterdir() if entry.is_dir()]
    if len(entries) != 1:
        raise UpdateError(
            f"Expected exactly one top-level directory in {archive.name}, found {len(entries)}."
        )
    return entries[0]


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


@contextmanager
def update_lock(layout: InstallLayout) -> Iterator[None]:
    """Serialize updates so a timer and an operator cannot install at once.

    Unlike the runtime lock, this deliberately does not tighten directory
    permissions: the install tree must stay traversable by every user whose
    `signage-controller` resolves through it.
    """
    layout.root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(layout.root / ".update.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            raise UpdateError("Another signage-controller update is already running.") from err
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _check_venv_support(interpreter: str, *, runner: Runner = default_runner) -> None:
    """Fail early and legibly when the interpreter cannot build a venv.

    Debian and Ubuntu ship `venv` without `ensurepip` unless python3-venv is
    installed. Without this check the failure surfaces as an opaque
    "Failing command: .../bin/python3" from the venv module itself.
    """
    try:
        runner([interpreter, "-c", "import ensurepip, venv"])
    except UpdateError as err:
        raise UpdateError(
            f"{interpreter} cannot create virtual environments: "
            "install the python3-venv package on this host."
        ) from err


def install_release(
    release: Release,
    layout: InstallLayout,
    *,
    opener: Opener = default_opener,
    runner: Runner = default_runner,
    python: str | None = None,
    require_checksum: bool = False,
    force: bool = False,
) -> Path:
    """Build a new version directory without touching the active installation.

    The build happens under the new version's own directory, which the running
    version never shares, and `current` is repointed separately only after the
    new entry point reports the expected version. A failure here therefore
    removes the partial build and leaves the running installation alone.
    """
    version_dir = layout.version_dir(release.version)
    if layout.is_installed(release.version) and not force:
        LOGGER.info("Version %s is already built at %s.", release.version, version_dir)
        return version_dir
    # Also clears a directory left behind by an interrupted build.
    _remove_tree(version_dir)

    layout.versions_dir.mkdir(parents=True, exist_ok=True)
    source_root = Path(
        tempfile.mkdtemp(prefix=f".source-{release.version}-", dir=layout.versions_dir)
    )
    try:
        archive = download_archive(
            release, source_root, opener=opener, require_checksum=require_checksum
        )
        source = _extract(archive, source_root / "extracted")

        venv_dir = version_dir / "venv"
        interpreter = python or sys.executable
        _check_venv_support(interpreter, runner=runner)
        LOGGER.info("Building %s in %s", release.version, version_dir)
        runner([interpreter, "-m", "venv", str(venv_dir)])
        pip = venv_dir / "bin" / "pip"
        runner([str(pip), "install", "--upgrade", "pip"])
        runner([str(pip), "install", str(source)])

        reported = runner([str(layout.entry_point(release.version)), "--version"]).strip()
        if release.version not in reported:
            raise UpdateError(
                f"Built version reports {reported!r}, which does not match the "
                f"release version {release.version}."
            )
        os.chmod(version_dir, 0o755)
    except BaseException:
        _remove_tree(version_dir)
        raise
    finally:
        _remove_tree(source_root)
    return version_dir


def activate_version(layout: InstallLayout, version: str) -> None:
    """Point `current` at an installed version with one atomic rename."""
    if not layout.entry_point(version).exists():
        raise UpdateError(
            f"Version {version} is not installed at {layout.version_dir(version)}."
        )
    temporary = layout.root / f".current-{os.getpid()}"
    temporary.unlink(missing_ok=True)
    try:
        # Relative so the whole tree can be relocated or bind-mounted.
        os.symlink(Path("versions") / version, temporary)
        os.replace(temporary, layout.current_link)
    except OSError as err:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"Could not activate version {version}: {err}") from err


def prune_versions(layout: InstallLayout, keep: int = DEFAULT_KEEP_VERSIONS) -> list[str]:
    """Remove the oldest installed versions, never the active one."""
    if keep < 1:
        raise UpdateError("--keep must be at least 1.")
    current = layout.current_version()
    versions = layout.installed_versions()
    removable = [version for version in versions if version != current]
    excess = removable[: max(len(versions) - keep, 0)]
    for version in excess:
        _remove_tree(layout.version_dir(version))
    return excess


def restart_services(
    units: Sequence[str] = DEFAULT_UNITS,
    *,
    runner: Runner = default_runner,
) -> list[str]:
    """Restart the signage user units, returning the ones that did not restart.

    A restart failure is reported but never unwinds the upgrade: the new
    version is already active, and an operator restart is a smaller problem
    than an automatic rollback nobody asked for.
    """
    if shutil.which("systemctl") is None:
        LOGGER.info("systemctl is not available; restart the signage processes manually.")
        return list(units)

    failed = []
    for unit in units:
        try:
            runner(["systemctl", "--user", "restart", unit])
            LOGGER.info("Restarted %s", unit)
        except UpdateError as err:
            LOGGER.warning("Could not restart %s: %s", unit, err)
            failed.append(unit)
    return failed
