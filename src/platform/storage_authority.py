"""Fixed-root filesystem authority for Local AI Hub V8.

This module deliberately works with lexical paths first and refuses symlink,
junction and reparse-point authority. It is a control-plane primitive: callers
receive a short-lived root lease and must not cache raw workstation paths in
public state.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import stat

from src.platform.paths import HubPaths, get_paths, is_reparse_point


_MUTABLE_ROOTS = frozenset(
    {
        "config",
        "models",
        "environments",
        "runtime",
        "output",
        "cache",
        "temp",
        "logs",
        "reports",
        "backups",
    }
)


class StorageAuthorityError(ValueError):
    """Fixed-code storage-authority refusal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size_bytes: int
    mtime_ns: int
    mode: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "FileIdentity":
        return cls(
            device=int(getattr(value, "st_dev", 0) or 0),
            inode=int(getattr(value, "st_ino", 0) or 0),
            size_bytes=int(value.st_size),
            mtime_ns=int(getattr(value, "st_mtime_ns", 0) or 0),
            mode=int(value.st_mode),
        )

    def same_object(self, other: "FileIdentity") -> bool:
        return self.device == other.device and self.inode == other.inode and self.mode == other.mode

    def same_file_state(self, other: "FileIdentity") -> bool:
        return self.same_object(other) and self.size_bytes == other.size_bytes and self.mtime_ns == other.mtime_ns


@dataclass(frozen=True)
class RootLease:
    authority: "StorageAuthority"
    root_key: str
    path: Path
    identity: FileIdentity

    def assert_current(self) -> None:
        current = self.authority._directory_identity(self.path)
        if not self.identity.same_object(current):
            raise StorageAuthorityError("managed_root_identity_changed")
        self.authority._assert_existing_chain(self.path)


class StorageAuthority:
    """Own all V8 mutable roots from one server-derived ``HubPaths`` value."""

    def __init__(self, paths: HubPaths | None = None) -> None:
        self.paths = paths or get_paths()

    @staticmethod
    def _absolute(value: Path | str) -> Path:
        return Path(value).expanduser().absolute()

    @staticmethod
    def _safe_relative(value: Path | str) -> Path:
        if not isinstance(value, (str, Path)):
            raise StorageAuthorityError("invalid_relative_path")
        text = str(value)
        if not text or len(text) > 512 or "\x00" in text or ":" in text:
            raise StorageAuthorityError("invalid_relative_path")
        normalized = text.replace("\\", "/")
        candidate = Path(normalized)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise StorageAuthorityError("unsafe_relative_path")
        return candidate

    @staticmethod
    def _lstat(path: Path) -> os.stat_result | None:
        try:
            return path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StorageAuthorityError("path_unreadable") from exc

    def _assert_existing_chain(self, path: Path) -> None:
        """Reject every existing ancestor that can redirect path authority."""

        current = self._absolute(path)
        chain: list[Path] = []
        while True:
            chain.append(current)
            if current.parent == current:
                break
            current = current.parent
        for item in reversed(chain):
            info = self._lstat(item)
            if info is None:
                continue
            if stat.S_ISLNK(info.st_mode) or is_reparse_point(item):
                raise StorageAuthorityError("reparse_authority_refused")

    @staticmethod
    def _assert_lexical_child(root: Path, candidate: Path) -> None:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise StorageAuthorityError("managed_path_escape") from exc

    def _root_path(self, root_key: str) -> Path:
        if root_key not in _MUTABLE_ROOTS:
            raise StorageAuthorityError("unknown_managed_root")
        return self._absolute(self.paths.roots()[root_key])

    def _mkdir_chain(self, target: Path) -> None:
        """Create missing directories only after all existing ancestors are safe."""

        target = self._absolute(target)
        self._assert_existing_chain(target)
        missing: list[Path] = []
        current = target
        while self._lstat(current) is None:
            missing.append(current)
            if current.parent == current:
                break
            current = current.parent
        for item in reversed(missing):
            self._assert_existing_chain(item.parent)
            try:
                item.mkdir()
            except FileExistsError:
                pass
            except OSError as exc:
                raise StorageAuthorityError("managed_root_create_failed") from exc
            self._assert_existing_chain(item)
            info = self._lstat(item)
            if info is None or not stat.S_ISDIR(info.st_mode):
                raise StorageAuthorityError("managed_root_unavailable")

    def _directory_identity(self, path: Path) -> FileIdentity:
        self._assert_existing_chain(path)
        info = self._lstat(path)
        if info is None or not stat.S_ISDIR(info.st_mode) or is_reparse_point(path):
            raise StorageAuthorityError("managed_root_unavailable")
        return FileIdentity.from_stat(info)

    def lease(self, root_key: str, *, create: bool = False) -> RootLease:
        """Acquire a short-lived identity lease for one fixed mutable root."""

        root = self._root_path(root_key)
        data_root = self._absolute(self.paths.data_root)
        self._assert_lexical_child(data_root, root)
        self._assert_existing_chain(data_root)
        if create:
            self._mkdir_chain(data_root)
            self._mkdir_chain(root)
        identity = self._directory_identity(root)
        return RootLease(self, root_key, root, identity)

    def resolve_relative(
        self,
        lease: RootLease,
        relative: Path | str,
        *,
        require_exists: bool = False,
        expect_file: bool | None = None,
    ) -> Path:
        if not isinstance(lease, RootLease) or lease.authority is not self:
            raise StorageAuthorityError("invalid_root_lease")
        lease.assert_current()
        safe = self._safe_relative(relative)
        candidate = self._absolute(lease.path / safe)
        self._assert_lexical_child(lease.path, candidate)
        self._assert_existing_chain(candidate)
        if require_exists:
            info = self._lstat(candidate)
            if info is None or is_reparse_point(candidate):
                raise StorageAuthorityError("managed_leaf_unavailable")
            if expect_file is True and not stat.S_ISREG(info.st_mode):
                raise StorageAuthorityError("managed_leaf_not_file")
            if expect_file is False and not stat.S_ISDIR(info.st_mode):
                raise StorageAuthorityError("managed_leaf_not_directory")
        return candidate

    def mkdir_relative(self, lease: RootLease, relative: Path | str) -> Path:
        target = self.resolve_relative(lease, relative, require_exists=False)
        self._mkdir_chain(target)
        lease.assert_current()
        info = self._lstat(target)
        if info is None or not stat.S_ISDIR(info.st_mode) or is_reparse_point(target):
            raise StorageAuthorityError("managed_directory_unavailable")
        return target

    def file_identity(self, lease: RootLease, relative: Path | str) -> FileIdentity:
        candidate = self.resolve_relative(lease, relative, require_exists=True, expect_file=True)
        info = self._lstat(candidate)
        if info is None:
            raise StorageAuthorityError("managed_leaf_unavailable")
        return FileIdentity.from_stat(info)

    def file_identity_path(self, lease: RootLease, candidate: Path | str) -> tuple[Path, Path, FileIdentity]:
        """Validate an existing regular file and return path, relative key and identity."""

        raw = self._absolute(candidate)
        self._assert_lexical_child(lease.path, raw)
        relative = raw.relative_to(lease.path)
        path = self.resolve_relative(lease, relative, require_exists=True, expect_file=True)
        info = self._lstat(path)
        if info is None:
            raise StorageAuthorityError("managed_leaf_unavailable")
        return path, relative, FileIdentity.from_stat(info)

    @contextmanager
    def hold_file_identity(
        self,
        lease: RootLease,
        relative: Path | str,
        expected: FileIdentity,
    ):
        """Hold an exact managed leaf through one atomic publication boundary.

        On Windows this opens the leaf without following a reparse point and
        denies write/delete sharing. A foreign actor cannot replace the
        pathname after pre-commit validation and before the SQLite visibility
        commit. Other platforms retain an identity-checked no-follow read
        handle; their callers still revalidate around external boundaries.
        """

        try:
            candidate = self.resolve_relative(lease, relative, require_exists=True, expect_file=True)
            current = self.file_identity(lease, relative)
        except StorageAuthorityError:
            raise StorageAuthorityError("managed_leaf_unavailable") from None
        if not expected.same_file_state(current):
            raise StorageAuthorityError("managed_leaf_identity_changed")

        fd: int | None = None
        try:
            if os.name == "nt":
                import ctypes
                import msvcrt
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                create_file = kernel32.CreateFileW
                create_file.argtypes = [
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.HANDLE,
                ]
                create_file.restype = wintypes.HANDLE
                handle = create_file(
                    os.fspath(candidate),
                    0x80000000,  # GENERIC_READ
                    0x00000001,  # FILE_SHARE_READ; deny write and delete
                    None,
                    3,  # OPEN_EXISTING
                    0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
                    None,
                )
                invalid_handle = ctypes.c_void_p(-1).value
                if int(handle) == invalid_handle:
                    raise StorageAuthorityError("managed_leaf_unavailable")
                try:
                    fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
                except (OSError, ValueError):
                    kernel32.CloseHandle(handle)
                    raise StorageAuthorityError("managed_leaf_unavailable") from None
            else:
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(candidate, flags)

            if not expected.same_file_state(FileIdentity.from_stat(os.fstat(fd))):
                raise StorageAuthorityError("managed_leaf_identity_changed")
            lease.assert_current()
            self._assert_existing_chain(candidate)
            current_stat = self._lstat(candidate)
            if current_stat is None or not stat.S_ISREG(current_stat.st_mode) or is_reparse_point(candidate):
                raise StorageAuthorityError("managed_leaf_unavailable")
            if not expected.same_file_state(FileIdentity.from_stat(current_stat)):
                raise StorageAuthorityError("managed_leaf_identity_changed")
            yield fd
            # POSIX ``O_NOFOLLOW`` protects the object opened by ``fd`` but
            # does not stop another process from replacing the pathname with
            # ``rename(2)`` while the handle is held.  Revalidate both the
            # handle and the managed pathname after the caller's publication
            # boundary so a pathname replacement can never be reported as a
            # successful public commit.  Windows' deny-share handle still
            # provides the stronger prevention guarantee; this check is the
            # cross-platform detection/rollback boundary.
            if not expected.same_file_state(FileIdentity.from_stat(os.fstat(fd))):
                raise StorageAuthorityError("managed_leaf_identity_changed")
            lease.assert_current()
            final_stat = self._lstat(candidate)
            if (
                final_stat is None
                or not stat.S_ISREG(final_stat.st_mode)
                or is_reparse_point(candidate)
                or not expected.same_file_state(FileIdentity.from_stat(final_stat))
            ):
                raise StorageAuthorityError("managed_leaf_identity_changed")
        except StorageAuthorityError:
            raise
        except (AttributeError, ImportError, OSError, TypeError, ValueError):
            raise StorageAuthorityError("managed_leaf_unavailable") from None
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _delete_windows_identity_attested(
        self,
        lease: RootLease,
        candidate: Path,
        expected: FileIdentity,
    ) -> bool:
        """Mark exactly one opened Windows leaf for deletion.

        A pathname-based unlink after an identity check can delete a later
        replacement.  Open the already-attested regular leaf with reparse
        following disabled and DELETE sharing denied, then set deletion on
        that same handle.  A competing replacement either fails while the
        handle is held or remains at the pathname while only the original
        handle-bound object is removed.
        """

        if os.name != "nt":
            return False
        fd: int | None = None
        try:
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            create_file.restype = wintypes.HANDLE
            handle = create_file(
                os.fspath(candidate),
                0x80000000 | 0x00010000,  # GENERIC_READ | DELETE
                0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE; deny delete
                None,
                3,  # OPEN_EXISTING
                0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
                None,
            )
            invalid_handle = ctypes.c_void_p(-1).value
            if int(handle) == invalid_handle:
                return False
            try:
                fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
            except (OSError, ValueError):
                kernel32.CloseHandle(handle)
                return False
            if not expected.same_file_state(FileIdentity.from_stat(os.fstat(fd))):
                return False
            # Revalidate the root and lexical chain after handle acquisition.
            # This catches a replacement that happened before the open, while
            # the denied-delete handle protects the final deletion afterward.
            lease.assert_current()
            self._assert_existing_chain(candidate)
            current = self._lstat(candidate)
            if current is None or not stat.S_ISREG(current.st_mode) or is_reparse_point(candidate):
                return False
            if not expected.same_file_state(FileIdentity.from_stat(current)):
                return False

            class _FileDispositionInfo(ctypes.Structure):
                _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

            disposition = _FileDispositionInfo(1)
            set_file_information = kernel32.SetFileInformationByHandle
            set_file_information.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
            set_file_information.restype = wintypes.BOOL
            return bool(set_file_information(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)))
        except (AttributeError, ImportError, OSError, TypeError, ValueError):
            return False
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def unlink_if_identity(self, lease: RootLease, relative: Path | str, expected: FileIdentity) -> bool:
        """Delete only a regular Hub-owned leaf whose exact identity still matches."""

        try:
            candidate = self.resolve_relative(lease, relative, require_exists=True, expect_file=True)
            current = self.file_identity(lease, relative)
        except StorageAuthorityError:
            return False
        if not expected.same_file_state(current):
            return False
        if os.name == "nt":
            return self._delete_windows_identity_attested(lease, candidate, expected)
        try:
            candidate.unlink()
        except OSError:
            return False
        lease.assert_current()
        return True


__all__ = ["FileIdentity", "RootLease", "StorageAuthority", "StorageAuthorityError"]
