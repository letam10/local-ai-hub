"""One-time safe bootstrap for the first watchdog-capable APP_ONLY payload.

This procedure is intentionally not invoked by the desktop updater.  A human
must run it from a reviewed source checkout after the corresponding PR is
merged and a successful main artifact exists.  AppUpdateService performs the
exact artifact/contract checks, staged API+frontend preflight, previous-pointer
recording and atomic activation; this wrapper adds an exact source/artifact
commit guard and verifies that failures leave the old pointer unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from src.app.stable_shell import load_current_pointer
from src.services.app_update import AppUpdateError, AppUpdateService


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _source_commit(source_root: Path) -> str:
    try:
        value = subprocess.check_output(["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("SOURCE_COMMIT_UNAVAILABLE") from None
    if not _SHA_RE.fullmatch(value):
        raise RuntimeError("SOURCE_COMMIT_INVALID")
    return value


def run(*, source_root: Path, install_root: Path, expected_commit: str, activate: bool) -> dict[str, object]:
    if not _SHA_RE.fullmatch(expected_commit):
        raise RuntimeError("EXPECTED_COMMIT_INVALID")
    actual_source = _source_commit(source_root)
    if actual_source != expected_commit:
        raise RuntimeError("SOURCE_COMMIT_MISMATCH")
    if not activate:
        return {"status": "ready_for_explicit_activation", "source_commit": actual_source, "activation": "not_run"}
    previous_install_root = os.environ.get("LOCALAIHUB_INSTALL_ROOT")
    os.environ["LOCALAIHUB_INSTALL_ROOT"] = str(install_root.absolute())
    try:
        before = load_current_pointer(install_root)
        service = AppUpdateService()
        candidate = service._latest_candidate()
        if candidate is None or candidate.source_commit != expected_commit:
            raise RuntimeError("EXACT_MAIN_ARTIFACT_UNAVAILABLE")
        # Freeze the verified candidate selected above so a newer main push
        # cannot race the exact-commit guard between discovery and prepare().
        service._latest_candidate = lambda: candidate  # type: ignore[method-assign]
        try:
            result = service.prepare()
        except Exception as exc:
            after = load_current_pointer(install_root)
            if after != before:
                raise RuntimeError("BOOTSTRAP_FAILURE_POINTER_CHANGED") from exc
            raise
        return {
            "status": result.get("status"),
            "source_commit": expected_commit,
            "payload_id": result.get("payload_id"),
            "previous_payload": result.get("previous_payload"),
            "candidate_api_preflight": "passed",
            "candidate_frontend_preflight": "passed",
            "activation": "atomic",
        }
    finally:
        if previous_install_root is None:
            os.environ.pop("LOCALAIHUB_INSTALL_ROOT", None)
        else:
            os.environ["LOCALAIHUB_INSTALL_ROOT"] = previous_install_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-time Local AI Hub watchdog payload bootstrap")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--activate", action="store_true", help="perform the reviewed atomic activation after all preflight checks")
    args = parser.parse_args(argv)
    try:
        result = run(source_root=args.source_root.absolute(), install_root=args.install_root.absolute(), expected_commit=args.expected_commit, activate=args.activate)
    except (AppUpdateError, RuntimeError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "code": str(exc) or type(exc).__name__}, ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run"]
