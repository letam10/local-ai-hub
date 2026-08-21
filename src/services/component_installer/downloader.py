"""Conservative, shared HTTPS downloader used by explicit install plans."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .policy import source_fingerprint, trusted_source


class DownloadError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DownloadResult:
    staged_path: Path
    bytes_received: int
    sha256: str
    resumed: bool
    source_fingerprint: str

    def safe_projection(self) -> dict[str, object]:
        return {
            "status": "completed",
            "bytes_received": self.bytes_received,
            "sha256": self.sha256,
            "resumed": self.resumed,
            "source_fingerprint": self.source_fingerprint,
            "location_class": "component_install_staging",
        }


class _BoundedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, *, limit: int, fixture_mode: bool) -> None:
        super().__init__()
        self.limit = limit
        self.fixture_mode = fixture_mode
        self.count = 0

    def redirect_request(self, req: Request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        self.count += 1
        if self.count > self.limit or not trusted_source(urljoin(req.full_url, newurl), fixture_mode=self.fixture_mode):
            raise DownloadError("redirect_not_trusted")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


ProgressCallback = Callable[[int, int | None], None]


class TrustedDownloader:
    """Download only server-selected sources into installer-owned staging."""

    def __init__(self, *, staging_root: Path, timeout: float = 30.0, max_redirects: int = 3, max_bytes: int = 64 * 1024 * 1024 * 1024) -> None:
        self.staging_root = staging_root
        self.timeout = max(1.0, min(300.0, float(timeout)))
        self.max_redirects = max(0, min(8, int(max_redirects)))
        self.max_bytes = max(1, int(max_bytes))

    def _validate_destination(self, destination_name: str) -> Path:
        if not isinstance(destination_name, str) or not destination_name or Path(destination_name).name != destination_name or destination_name in {".", ".."}:
            raise DownloadError("unsafe_staging_name")
        # Inspect the lexical root and every existing ancestor before mkdir;
        # otherwise a junction in a parent could redirect creation outside the
        # task-owned staging tree before the post-create check runs.
        current = self.staging_root.absolute()
        while current.parent != current:
            if self._is_reparse(current):
                raise DownloadError("unsafe_staging_root")
            current = current.parent
        if self._is_reparse(current):
            raise DownloadError("unsafe_staging_root")
        self.staging_root.mkdir(parents=True, exist_ok=True)
        root = self.staging_root.resolve()
        if self._is_reparse(self.staging_root):
            raise DownloadError("unsafe_staging_root")
        lexical = root / destination_name
        if any(self._is_reparse(item) for item in (lexical.parent, lexical) if item.exists()):
            raise DownloadError("unsafe_staging_target")
        if lexical.exists():
            # A completed installer artifact is never overwritten by a later
            # plan.  The caller must create a fresh destination or inspect the
            # existing receipt first.
            raise DownloadError("staging_target_exists")
        candidate = lexical.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise DownloadError("unsafe_staging_name") from exc
        return candidate

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        try:
            if stat.S_ISLNK(path.lstat().st_mode):
                return True
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if os.name == "nt":
            try:
                import ctypes

                attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
                attrs = int(attrs) & 0xFFFFFFFF
                return attrs != 0xFFFFFFFF and bool(attrs & 0x400)
            except (AttributeError, OSError):
                return False
        return False

    def _owned_regular(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.staging_root.resolve())
            return path.is_file() and not self._is_reparse(path)
        except (OSError, ValueError):
            return False

    def _remove_owned(self, path: Path) -> None:
        if path.exists() or path.is_symlink():
            if not self._owned_regular(path):
                raise DownloadError("unsafe_staging_target")
            path.unlink()

    def _write_json(self, path: Path, value: dict[str, object]) -> None:
        if path.exists() and not self._owned_regular(path):
            raise DownloadError("unsafe_staging_target")
        temporary = path.with_suffix(path.suffix + ".tmp")
        if temporary.exists() or temporary.is_symlink():
            if not self._owned_regular(temporary):
                raise DownloadError("unsafe_staging_target")
            self._remove_owned(temporary)
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def download(
        self,
        source_url: str,
        destination_name: str,
        *,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        max_bytes: int | None = None,
        disk_free_bytes: int | None = None,
        disk_safety_bytes: int = 0,
        cancel_event: threading.Event | None = None,
        fixture_mode: bool = False,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        if not trusted_source(source_url, fixture_mode=fixture_mode):
            raise DownloadError("source_not_trusted")
        target = self._validate_destination(destination_name)
        partial = target.with_suffix(target.suffix + ".partial")
        metadata_path = partial.with_suffix(partial.suffix + ".json")
        for owned_path in (partial, metadata_path):
            if owned_path.exists() and not self._owned_regular(owned_path):
                raise DownloadError("unsafe_staging_target")
        limit = min(self.max_bytes, int(max_bytes) if max_bytes is not None else self.max_bytes)
        expected_sha = expected_sha256.lower() if isinstance(expected_sha256, str) else None
        if expected_sha is not None and (len(expected_sha) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha)):
            raise DownloadError("invalid_expected_hash")
        if expected_size is not None and (not isinstance(expected_size, int) or expected_size < 0 or expected_size > limit):
            raise DownloadError("invalid_expected_size")
        if disk_free_bytes is not None:
            if not isinstance(disk_free_bytes, int) or disk_free_bytes < 0 or not isinstance(disk_safety_bytes, int) or disk_safety_bytes < 0:
                raise DownloadError("invalid_disk_preflight")
            if expected_size is not None and disk_free_bytes < expected_size + disk_safety_bytes:
                raise DownloadError("insufficient_disk")
        identity = source_fingerprint(source_url)
        existing = 0
        resume = False
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            metadata = {}
        if partial.is_file() and isinstance(metadata, dict) and metadata.get("source_fingerprint") == identity and metadata.get("expected_size") == expected_size:
            existing = partial.stat().st_size
            resume = existing > 0
        elif partial.exists() or metadata_path.exists():
            # Only installer-owned staging files are eligible for this cleanup.
            self._remove_owned(partial)
            self._remove_owned(metadata_path)
        if existing > limit:
            self._remove_owned(partial)
            self._remove_owned(metadata_path)
            raise DownloadError("partial_size_limit")

        headers = {"Accept": "application/octet-stream", "User-Agent": "LocalAIHub-ComponentInstaller/1"}
        if resume:
            headers["Range"] = f"bytes={existing}-"
            prior_identity = metadata.get("etag") or metadata.get("last_modified")
            if isinstance(prior_identity, str) and prior_identity:
                headers["If-Range"] = prior_identity
        request = Request(source_url, headers=headers, method="GET")
        opener = build_opener(_BoundedRedirectHandler(limit=self.max_redirects, fixture_mode=fixture_mode))
        try:
            response = opener.open(request, timeout=self.timeout)
        except DownloadError:
            raise
        except HTTPError as exc:
            raise DownloadError(f"http_{exc.code}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise DownloadError("download_unavailable") from exc

        status = getattr(response, "status", getattr(response, "code", 200))
        response_etag = response.headers.get("ETag")
        response_modified = response.headers.get("Last-Modified")
        prior_etag = metadata.get("etag") if isinstance(metadata, dict) else None
        prior_modified = metadata.get("last_modified") if isinstance(metadata, dict) else None
        if resume and ((prior_etag and response_etag and prior_etag != response_etag) or (prior_modified and response_modified and prior_modified != response_modified)):
            # The URL is the same but the remote object identity changed.  Do
            # not append bytes from two different objects.
            response.close()
            self._remove_owned(partial)
            self._remove_owned(metadata_path)
            existing = 0
            resume = False
            request = Request(source_url, headers={"Accept": "application/octet-stream", "User-Agent": "LocalAIHub-ComponentInstaller/1"}, method="GET")
            try:
                response = opener.open(request, timeout=self.timeout)
            except DownloadError:
                raise
            except (HTTPError, URLError, OSError, TimeoutError) as exc:
                raise DownloadError("download_unavailable") from exc
            status = getattr(response, "status", getattr(response, "code", 200))
            response_etag = response.headers.get("ETag")
            response_modified = response.headers.get("Last-Modified")
        if resume and status != 206:
            existing = 0
            resume = False
            self._remove_owned(partial)
        total_header = response.headers.get("Content-Length")
        try:
            total = int(total_header) + existing if total_header is not None else expected_size
        except ValueError:
            total = expected_size
        if total is not None and total > limit:
            response.close()
            self._remove_owned(partial)
            self._remove_owned(metadata_path)
            raise DownloadError("download_size_limit")
        if expected_size is not None and total is not None and total != expected_size:
            response.close()
            self._remove_owned(partial)
            self._remove_owned(metadata_path)
            raise DownloadError("content_length_mismatch")

        digest = hashlib.sha256()
        if resume:
            with partial.open("rb") as previous:
                while True:
                    chunk = previous.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        received = existing
        self._write_json(metadata_path, {"schema_version": "download-partial.v1", "source_fingerprint": identity, "expected_size": expected_size, "bytes_received": received, "etag": response_etag, "last_modified": response_modified})
        try:
            with response, partial.open("ab" if resume else "wb") as output:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadError("cancelled")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > limit:
                        raise DownloadError("download_size_limit")
                    digest.update(chunk)
                    output.write(chunk)
                    if progress:
                        progress(received, total)
                    if received % (8 * 1024 * 1024) < len(chunk):
                        output.flush()
                        os.fsync(output.fileno())
                        self._write_json(metadata_path, {"schema_version": "download-partial.v1", "source_fingerprint": identity, "expected_size": expected_size, "bytes_received": received, "etag": response_etag, "last_modified": response_modified})
        except DownloadError:
            raise
        except (OSError, URLError, TimeoutError) as exc:
            raise DownloadError("download_io_failed") from exc

        if expected_size is not None and received != expected_size:
            raise DownloadError("download_size_mismatch")
        actual = digest.hexdigest()
        if expected_sha is not None and actual != expected_sha:
            self._remove_owned(partial)
            self._remove_owned(metadata_path)
            raise DownloadError("checksum_mismatch")
        if target.exists() or target.is_symlink():
            raise DownloadError("staging_target_exists")
        try:
            # A hard-link publish is no-replace on Windows and POSIX filesystems
            # alike; it avoids replacing an intervening completed artifact.
            os.link(partial, target)
            self._remove_owned(partial)
        except OSError as exc:
            raise DownloadError("staging_publish_failed") from exc
        self._remove_owned(metadata_path)
        return DownloadResult(target, received, actual, resume, identity)


__all__ = ["DownloadError", "DownloadResult", "TrustedDownloader"]
