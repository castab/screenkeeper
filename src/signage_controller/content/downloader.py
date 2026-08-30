from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import aiohttp

from .models import ContentAsset


class DownloadError(RuntimeError):
    pass


class AssetDownloader(Protocol):
    async def download(self, asset: ContentAsset, destination: Path) -> None: ...


class HttpAssetDownloader:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    async def download(self, asset: ContentAsset, destination: Path) -> None:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(destination.parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{asset.sha256}.", suffix=".part", dir=destination.parent
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        byte_count = 0
        parsed_url = urlsplit(asset.download_url)
        host = parsed_url.hostname or "unknown-host"
        if parsed_url.port is not None:
            host = f"{host}:{parsed_url.port}"
        try:
            os.fchmod(descriptor, 0o600)
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(asset.download_url, allow_redirects=False) as response:
                    if not 200 <= response.status < 300:
                        raise DownloadError(f"Object storage at {host} returned HTTP {response.status}.")
                    with os.fdopen(descriptor, "wb") as handle:
                        descriptor = -1
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            handle.write(chunk)
                            digest.update(chunk)
                            byte_count += len(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
            if byte_count != asset.byte_size or digest.hexdigest() != asset.sha256:
                raise DownloadError("Downloaded object failed size or SHA-256 validation.")
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except DownloadError:
            raise
        except Exception as err:
            raise DownloadError(f"Could not download content from object storage at {host}: {type(err).__name__}.") from err
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
