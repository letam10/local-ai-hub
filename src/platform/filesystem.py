"""Reusable containment/reparse helpers for future managers and adapters."""

from __future__ import annotations

from pathlib import Path


class UnsafePathError(ValueError):
    pass


def assert_relative(root: Path, candidate: Path) -> Path:
    """Return a resolved candidate only when it remains below a resolved root."""

    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError("path_outside_root") from exc
    return candidate


def assert_no_reparse_ancestors(root: Path, candidate: Path) -> Path:
    """Reject symlink/reparse components before using a machine-local path."""

    root = root.resolve()
    lexical = candidate
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError("lexical_path_outside_root") from exc
    current = lexical
    while True:
        if current.exists() and current.is_symlink():
            raise UnsafePathError("reparse_component")
        if current == root:
            break
        if current.parent == current:
            raise UnsafePathError("root_not_reached")
        current = current.parent
    return assert_relative(root, lexical)


def safe_location_class(root_name: str, relative: Path | str) -> str:
    """Produce a path-free internal/public destination class."""

    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise UnsafePathError("unsafe_relative_location")
    return root_name


__all__ = ["UnsafePathError", "assert_no_reparse_ancestors", "assert_relative", "safe_location_class"]
