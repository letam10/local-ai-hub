from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts import canonical_git_guard as cli
from src.shared import canonical_git_integrity as guard


class CanonicalGitIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.canonical = root / "canonical"
        self.canonical_git = self.canonical / ".git"
        self.canonical_git.mkdir(parents=True)
        self.target = root / "disposable"
        self.target.mkdir()
        self.common = root / "common.git"
        self.admin = self.common / "worktrees" / self.target.name
        self.admin.mkdir(parents=True)
        (self.target / ".git").write_text(f"gitdir: {self.admin}\n", encoding="utf-8")
        self.snapshot = self.canonical / "Reports" / "canonical_git_integrity.local.json"
        self.snapshot.parent.mkdir()
        self._reset_git_outputs()
        self.root_patch = patch.object(guard, "CANONICAL_ROOT", self.canonical)
        self.snapshot_patch = patch.object(guard, "FORENSIC_SNAPSHOT_PATH", self.snapshot)
        self.root_patch.start()
        self.snapshot_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.snapshot_patch.stop)
        self.real_run_git = guard._run_git
        self.runner_patch = patch.object(guard, "_run_git", side_effect=self._run_git)
        self.runner_patch.start()
        self.addCleanup(self.runner_patch.stop)

    @staticmethod
    def _ok(value: str = "", *, returncode: int = 0) -> guard._GitResult:
        return guard._GitResult(returncode, value.encode("utf-8"), b"")

    def _reset_git_outputs(self) -> None:
        self.canonical_outputs = {
            ("rev-parse", "--show-toplevel"): self._ok(str(self.canonical) + "\n"),
            ("rev-parse", "--git-dir"): self._ok(".git\n"),
            ("rev-parse", "--git-common-dir"): self._ok(".git\n"),
            ("rev-parse", "--verify", "HEAD^{commit}"): self._ok("a" * 40 + "\n"),
            ("symbolic-ref", "--quiet", "--short", "HEAD"): self._ok("feature/local-ai-hub-v6\n"),
            ("config", "--get-all", "remote.origin.url"): self._ok("https://github.com/letam10/local-ai-hub.git\n"),
            ("status", "--porcelain=v1", "--untracked-files=all"): self._ok(),
        }
        self.target_outputs = {
            ("rev-parse", "--show-toplevel"): self._ok(str(self.target) + "\n"),
            ("rev-parse", "--git-dir"): self._ok(str(self.admin) + "\n"),
            ("rev-parse", "--git-common-dir"): self._ok(str(self.common) + "\n"),
            ("rev-parse", "--verify", "HEAD^{commit}"): self._ok("b" * 40 + "\n"),
            ("symbolic-ref", "--quiet", "--short", "HEAD"): self._ok("feature/local-ai-hub-v6-lah2-canonical-integrity-p0\n"),
            ("merge-base", "--is-ancestor", "a" * 40, "HEAD"): self._ok(),
            ("status", "--porcelain=v1", "--untracked-files=all"): self._ok(),
        }

    def _run_git(self, root: Path, args: tuple[str, ...]) -> guard._GitResult:
        if Path(root).resolve() == self.canonical.resolve():
            return self.canonical_outputs.get(args, guard._GitResult(1))
        if Path(root).resolve() == self.target.resolve():
            return self.target_outputs.get(args, guard._GitResult(1))
        return guard._GitResult(1)

    def _run_cli_subprocess(
        self,
        operation: str,
        *,
        dirty: bool = False,
        writer_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        code = r'''
import runpy
import sys
from pathlib import Path
from src.shared import canonical_git_integrity as guard

root = Path(sys.argv[1])
(root / "Reports").mkdir(parents=True, exist_ok=True)
dirty = sys.argv[3] == "dirty"
writer_failure = sys.argv[4] == "writer-failure"

def ok(value=""):
    return guard._GitResult(0, (value + "\n").encode("utf-8"), b"")

outputs = {
    ("rev-parse", "--show-toplevel"): ok(str(root)),
    ("rev-parse", "--git-dir"): ok(".git"),
    ("rev-parse", "--git-common-dir"): ok(".git"),
    ("rev-parse", "--verify", "HEAD^{commit}"): ok("a" * 40),
    ("symbolic-ref", "--quiet", "--short", "HEAD"): ok("feature/local-ai-hub-v6"),
    ("config", "--get-all", "remote.origin.url"): ok("https://github.com/letam10/local-ai-hub.git"),
    ("config", "--get-all", "remote.origin.pushurl"): guard._GitResult(1),
    ("status", "--porcelain=v1", "--untracked-files=all"): ok("hidden-marker.txt" if dirty else ""),
}

def fake_run(_root, args):
    return outputs.get(args, guard._GitResult(1))

guard.CANONICAL_ROOT = root
guard.FORENSIC_SNAPSHOT_PATH = root / "Reports" / "canonical_git_integrity.local.json"
guard._run_git = fake_run
if writer_failure:
    guard.write_forensic_snapshot = lambda _event: False
sys.argv = ["scripts/canonical_git_guard.py", sys.argv[2]]
runpy.run_path(str(Path.cwd() / "scripts" / "canonical_git_guard.py"), run_name="__main__")
'''
        repo_root = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                code,
                str(self.canonical),
                operation,
                "dirty" if dirty else "clean",
                "writer-failure" if writer_failure else "normal",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def _lease(self, *, target: Path | None = None, common: Path | None = None, base: str = "a" * 40) -> guard.OwnedTemporaryWorktree | None:
        return guard.issue_owned_temporary_worktree(
            target or self.target,
            branch="feature/local-ai-hub-v6-lah2-canonical-integrity-p0",
            base=base,
            common_git_dir=common or self.common,
        )

    def test_canonical_happy_projection_is_sanitized(self) -> None:
        result = guard.inspect_canonical()
        encoded = json.dumps(result, sort_keys=True)
        self.assertEqual(result["operation_code"], guard.CODE_OK)
        self.assertTrue(result["ok"])
        self.assertFalse(result["dirty"])
        self.assertEqual(result["head_state"], "valid")
        self.assertEqual(result["origin_state"], "allowlisted")
        self.assertNotIn(str(self.canonical), encoded)
        self.assertNotIn("github.com", encoded)
        self.assertNotIn("feature/local-ai-hub-v6", encoded)

    def test_canonical_missing_root_and_git_shape_fail_closed(self) -> None:
        shutil.rmtree(self.canonical)
        missing = guard.inspect_canonical()
        self.assertEqual(missing["operation_code"], guard.CODE_ROOT_MISSING)

        self.canonical.mkdir()
        self.canonical_git.write_text("not a directory", encoding="utf-8")
        invalid_git = guard.inspect_canonical()
        self.assertEqual(invalid_git["operation_code"], guard.CODE_GIT_INVALID)

    def test_canonical_linked_root_and_broken_git_query_refuse_without_echo(self) -> None:
        with patch.object(guard, "_has_link_or_reparse_component", return_value=True):
            linked = guard.inspect_canonical()
        self.assertEqual(linked["operation_code"], guard.CODE_ROOT_LINKED)

        self._reset_git_outputs()
        self.canonical_outputs[("rev-parse", "--show-toplevel")] = self._ok("not-the-canonical-root\n")
        mismatch = guard.inspect_canonical()
        self.assertEqual(mismatch["operation_code"], guard.CODE_TOP_LEVEL_MISMATCH)
        self.assertNotIn("not-the-canonical-root", json.dumps(mismatch))

        self._reset_git_outputs()
        self.canonical_outputs[("rev-parse", "--show-toplevel")] = guard._GitResult(1, b"private command output", b"private stderr")
        failed = guard.inspect_canonical()
        self.assertEqual(failed["operation_code"], guard.CODE_GIT_QUERY_FAILED)
        self.assertNotIn("private", json.dumps(failed))

    def test_canonical_head_branch_origin_and_ambiguity_refuse(self) -> None:
        cases = (
            (("rev-parse", "--verify", "HEAD^{commit}"), self._ok("not-a-head\n"), guard.CODE_HEAD_INVALID),
            (("symbolic-ref", "--quiet", "--short", "HEAD"), self._ok("main\n"), guard.CODE_BRANCH_INVALID),
            (("config", "--get-all", "remote.origin.url"), self._ok("https://secret@github.com/letam10/local-ai-hub.git\n"), guard.CODE_ORIGIN_CREDENTIALS),
            (("config", "--get-all", "remote.origin.url"), self._ok("https://github.com/other/project.git\n"), guard.CODE_ORIGIN_UNALLOWLISTED),
            (("config", "--get-all", "remote.origin.url"), self._ok("https://github.com/letam10/local-ai-hub.git\nhttps://github.com/letam10/local-ai-hub.git\n"), guard.CODE_METADATA_AMBIGUOUS),
            (("config", "--get-all", "remote.origin.pushurl"), self._ok("https://secret@github.com/letam10/local-ai-hub.git\n"), guard.CODE_ORIGIN_CREDENTIALS),
            (("config", "--get-all", "remote.origin.pushurl"), self._ok("https://github.com/other/project.git\n"), guard.CODE_ORIGIN_UNALLOWLISTED),
            (("config", "--get-all", "remote.origin.pushurl"), self._ok("https://github.com/letam10/local-ai-hub.git\nhttps://github.com/letam10/local-ai-hub.git\n"), guard.CODE_METADATA_AMBIGUOUS),
        )
        for command, value, expected in cases:
            with self.subTest(expected=expected):
                self._reset_git_outputs()
                self.canonical_outputs[command] = value
                result = guard.inspect_canonical()
                self.assertEqual(result["operation_code"], expected)
                self.assertNotIn("secret", json.dumps(result))
                self.assertNotIn("other/project", json.dumps(result))

        self._reset_git_outputs()
        self.canonical_outputs[("config", "--get-all", "remote.origin.pushurl")] = guard._GitResult(
            1,
            b"",
            b"private pushurl failure",
        )
        failed = guard.inspect_canonical()
        self.assertEqual(failed["operation_code"], guard.CODE_GIT_QUERY_FAILED)
        self.assertNotIn("private pushurl failure", json.dumps(failed))

    def test_pushurl_mismatch_is_a_fixed_refusal_without_echo(self) -> None:
        self._reset_git_outputs()
        self.canonical_outputs[("config", "--get-all", "remote.origin.pushurl")] = self._ok(
            "https://github.com/letam10/local-ai-hub.git\n"
        )
        with patch.object(
            guard,
            "_normalize_origin",
            side_effect=[("fetch-origin", guard.CODE_OK), ("push-origin", guard.CODE_OK)],
        ):
            result = guard.inspect_canonical()
        self.assertEqual(result["operation_code"], guard.CODE_ORIGIN_PUSH_MISMATCH)
        self.assertNotIn("fetch-origin", json.dumps(result))
        self.assertNotIn("push-origin", json.dumps(result))

    def test_canonical_dirty_is_preservation_required_and_untracked_is_not_echoed(self) -> None:
        marker = "?? secret-canonical-marker.txt\n"
        self.canonical_outputs[("status", "--porcelain=v1", "--untracked-files=all")] = self._ok(marker)
        result = guard.inspect_canonical()
        self.assertEqual(result["operation_code"], guard.CODE_WORKTREE_DIRTY)
        self.assertTrue(result["dirty"])
        self.assertEqual(result["worktree_state"], "dirty")
        self.assertNotIn("secret-canonical-marker", json.dumps(result))

    def test_bounded_git_output_has_fixed_refusal_without_echo(self) -> None:
        self.canonical_outputs[("rev-parse", "--show-toplevel")] = guard._GitResult(
            0,
            b"x" * (guard.MAX_GIT_OUTPUT_BYTES + 1),
            b"",
            guard.CODE_GIT_OUTPUT_OVERSIZE,
        )
        result = guard.inspect_canonical()
        self.assertEqual(result["operation_code"], guard.CODE_GIT_OUTPUT_OVERSIZE)
        self.assertNotIn("x" * 100, json.dumps(result))

    def test_read_only_git_runner_rejects_unallowlisted_calls_and_bounds_output(self) -> None:
        with patch.object(guard.shutil, "which", return_value="git"), patch.object(guard.subprocess, "run") as run:
            refused = self.real_run_git(self.canonical, ("reset", "--hard"))
            self.assertEqual(refused.code, guard.CODE_GIT_QUERY_FAILED)
            run.assert_not_called()
            run.return_value = guard._GitResult(0, b"x" * (guard.MAX_GIT_OUTPUT_BYTES + 1), b"")
            oversized = self.real_run_git(self.canonical, ("status", "--porcelain=v1", "--untracked-files=all"))
            self.assertEqual(oversized.code, guard.CODE_GIT_OUTPUT_OVERSIZE)
            self.assertNotIn("x" * 100, json.dumps(oversized.__dict__, default=str))

    def test_direct_root_cli_help_bootstrap_is_runnable(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-B", "scripts/canonical_git_guard.py", "--help"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, cli.EXIT_OK)
        self.assertIn("usage:", result.stdout.lower())
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_cli_inspect_and_preflight_subprocesses_are_sanitized(self) -> None:
        for operation in ("inspect", "preflight"):
            with self.subTest(operation=operation):
                result = self._run_cli_subprocess(operation)
                self.assertEqual(result.returncode, cli.EXIT_OK)
                projection = json.loads(result.stdout)
                self.assertEqual(projection["operation_code"], guard.CODE_OK)
                self.assertTrue(projection["ok"])
                self.assertNotIn(str(self.canonical), result.stdout)
                self.assertNotIn("github.com", result.stdout)
                if operation == "preflight":
                    self.assertTrue(self.snapshot.exists())
                    snapshot = json.loads(self.snapshot.read_text(encoding="utf-8"))
                    self.assertEqual(snapshot["canonical_path"], guard.CANONICAL_POLICY_PATH)
                    self.assertNotIn(str(self.canonical), json.dumps(snapshot))

    def test_cli_dirty_inspect_and_preflight_use_finite_refusal_exit(self) -> None:
        for operation in ("inspect", "preflight"):
            with self.subTest(operation=operation):
                result = self._run_cli_subprocess(operation, dirty=True)
                self.assertEqual(result.returncode, cli.EXIT_REFUSAL)
                projection = json.loads(result.stdout)
                self.assertEqual(projection["operation_code"], guard.CODE_WORKTREE_DIRTY)
                self.assertFalse(projection["ok"])
                self.assertTrue(projection["dirty"])
                self.assertNotIn("hidden-marker", result.stdout)
                if operation == "preflight":
                    snapshot = json.loads(self.snapshot.read_text(encoding="utf-8"))
                    self.assertEqual(snapshot["events"][-1]["outcome"], "preservation_required")

        failed = self._run_cli_subprocess("preflight", writer_failure=True)
        self.assertEqual(failed.returncode, cli.EXIT_REFUSAL)
        projection = json.loads(failed.stdout)
        self.assertEqual(projection["operation_code"], guard.CODE_SNAPSHOT_WRITE_FAILED)
        self.assertFalse(projection["ok"])
        self.assertNotIn(str(self.canonical), failed.stdout)

    def test_forensic_writer_is_atomic_bounded_rolling_and_sanitized(self) -> None:
        event = guard._event_from_result(guard.inspect_canonical())
        self.assertTrue(guard.write_forensic_snapshot(event))
        first = guard.read_forensic_snapshot()
        self.assertIsNotNone(first)
        self.assertEqual(len(first["events"]), 1)
        self.assertEqual(first["canonical_path"], guard.CANONICAL_POLICY_PATH)
        self.assertEqual(first["events"][0]["event_type"], "preflight")
        self.assertEqual(first["events"][0]["operation"], "canonical_preflight")
        self.assertEqual(first["events"][0]["target_kind"], "canonical")
        for _ in range(40):
            self.assertTrue(guard.write_forensic_snapshot(event))
        value = guard.read_forensic_snapshot()
        self.assertIsNotNone(value)
        self.assertLessEqual(len(value["events"]), guard.MAX_FORENSIC_EVENTS)
        self.assertNotIn(str(self.canonical), json.dumps(value))

    def test_forensic_malformed_oversized_unknown_and_writer_failure_preserve_bytes(self) -> None:
        event = guard._event_from_result(guard.inspect_canonical())
        self.assertTrue(guard.write_forensic_snapshot(event))
        original = self.snapshot.read_bytes()
        masked_key = "sec" + "ret"
        masked_bytes = ("{\"" + masked_key + "\":\"C:\\\\private\"}").encode("utf-8")
        self.snapshot.write_bytes(masked_bytes)
        self.assertIsNone(guard.read_forensic_snapshot())
        self.assertFalse(guard.write_forensic_snapshot(event))
        self.assertEqual(self.snapshot.read_bytes(), masked_bytes)

        self.snapshot.write_bytes(b"x" * (guard.MAX_FORENSIC_BYTES + 1))
        self.assertFalse(guard.write_forensic_snapshot(event))
        self.assertEqual(self.snapshot.read_bytes(), b"x" * (guard.MAX_FORENSIC_BYTES + 1))

        self.snapshot.write_bytes(original)
        hostile = dict(event)
        hostile["command"] = "git reset --hard"
        self.assertFalse(guard.write_forensic_snapshot(hostile))
        self.assertEqual(self.snapshot.read_bytes(), original)

        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            self.assertFalse(guard.write_forensic_snapshot(event))
        self.assertEqual(self.snapshot.read_bytes(), original)
        self.assertEqual(list(self.snapshot.parent.glob(f".{self.snapshot.name}.*.tmp")), [])

    def test_forensic_rejects_path_url_secret_fields_without_echo(self) -> None:
        event = guard._event_from_result(guard.inspect_canonical())
        self.assertTrue(guard.write_forensic_snapshot(event))
        original = self.snapshot.read_bytes()
        for key, value in {
            "path": str(self.canonical / "private.txt"),
            "url": "https://example.invalid/private",
            "sec" + "ret": "token-value",
        }.items():
            hostile = dict(event)
            hostile[key] = value
            self.assertFalse(guard.write_forensic_snapshot(hostile))
            self.assertEqual(self.snapshot.read_bytes(), original)
            self.assertNotIn(value, json.dumps(guard.read_forensic_snapshot()))

    def test_forensic_concurrent_writes_remain_bounded_and_valid(self) -> None:
        event = guard._event_from_result(guard.inspect_canonical())
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _index: guard.write_forensic_snapshot(event), range(40)))
        self.assertTrue(all(results))
        value = guard.read_forensic_snapshot()
        self.assertIsNotNone(value)
        self.assertLessEqual(len(value["events"]), guard.MAX_FORENSIC_EVENTS)
        self.assertTrue(all(guard._valid_event(item) for item in value["events"]))
        self.assertEqual(list(self.snapshot.parent.glob(f".{self.snapshot.name}.*.tmp")), [])

    def test_forensic_reader_is_side_effect_free(self) -> None:
        self.assertIsNone(guard.read_forensic_snapshot())
        event = guard._event_from_result(guard.inspect_canonical())
        self.assertTrue(guard.write_forensic_snapshot(event))
        before = self.snapshot.read_bytes()
        with patch.object(guard, "_run_git", side_effect=AssertionError("reader must not query Git")):
            value = guard.read_forensic_snapshot()
        self.assertIsNotNone(value)
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_forensic_linked_snapshot_refuses_without_mutating_bytes(self) -> None:
        event = guard._event_from_result(guard.inspect_canonical())
        self.assertTrue(guard.write_forensic_snapshot(event))
        before = self.snapshot.read_bytes()

        def linked(path: Path) -> bool:
            return Path(path).resolve() == self.snapshot.resolve()

        with patch.object(guard, "_is_link_or_reparse", side_effect=linked):
            self.assertIsNone(guard.read_forensic_snapshot())
            self.assertFalse(guard.write_forensic_snapshot(event))
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_preflight_refuses_writer_failure_without_claiming_success(self) -> None:
        with patch.object(guard, "write_forensic_snapshot", return_value=False):
            result = guard.preflight_canonical()
        self.assertEqual(result["operation_code"], guard.CODE_SNAPSHOT_WRITE_FAILED)
        self.assertFalse(result["ok"])

    def test_cleanup_decision_is_audited_and_writer_failure_refuses(self) -> None:
        lease = self._lease()
        result = guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))
        self.assertEqual(result["operation_code"], guard.CODE_OK)
        snapshot = guard.read_forensic_snapshot()
        self.assertIsNotNone(snapshot)
        event = snapshot["events"][-1]
        self.assertEqual(event["event_type"], "cleanup_decision")
        self.assertEqual(event["operation"], "owned_worktree_cleanup")
        self.assertEqual(event["target_kind"], "disposable_worktree")
        self.assertNotIn(str(self.target), json.dumps(snapshot))

        with patch.object(guard, "write_forensic_snapshot", return_value=False):
            refused = guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))
        self.assertEqual(refused["operation_code"], guard.CODE_SNAPSHOT_WRITE_FAILED)
        self.assertFalse(refused["action_available"])

    def test_valid_manager_lease_allows_only_decision_with_zero_owned_processes(self) -> None:
        lease = self._lease()
        self.assertIsNotNone(lease)
        result = guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))
        self.assertEqual(result["operation_code"], guard.CODE_OK)
        self.assertTrue(result["action_available"])
        self.assertEqual(result["action"], "ALLOW_CONTROLLED_CLEANUP")
        self.assertNotIn(str(self.target), json.dumps(result))

    def test_unissued_lease_and_probe_uncertainty_refuse(self) -> None:
        direct = guard.OwnedTemporaryWorktree(self.target, "feature/local-ai-hub-v6-lah2-canonical-integrity-p0", "a" * 40, self.common)
        self.assertEqual(
            guard.decide_owned_temporary_worktree_cleanup(direct, lambda: guard.OwnedProcessProbe(True, 0))["operation_code"],
            guard.CODE_LEASE_INVALID,
        )
        lease = self._lease()
        self.assertEqual(
            guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(False, 0))["operation_code"],
            guard.CODE_PROCESS_UNKNOWN,
        )
        self.assertEqual(
            guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 1))["operation_code"],
            guard.CODE_PROCESS_PRESENT,
        )

    def test_protected_common_git_dir_is_refused_at_lease_and_decision(self) -> None:
        candidates = (
            self.canonical / ".git",
            self.canonical / ".git" / "worktrees",
            self.canonical / "nested-common",
            self.canonical.parent,
        )
        for candidate in candidates:
            with self.subTest(candidate=str(candidate)):
                self.assertIsNone(self._lease(common=candidate))

        lease = self._lease()
        self.assertIsNotNone(lease)
        protected = replace(lease, common_git_dir=self.canonical / ".git")
        result = guard.decide_owned_temporary_worktree_cleanup(protected, lambda: guard.OwnedProcessProbe(True, 0))
        self.assertEqual(result["operation_code"], guard.CODE_COMMON_DIR_PROTECTED)
        self.assertFalse(result["action_available"])
        self.assertNotIn(str(self.canonical), json.dumps(result))

        separate = self._lease(common=self.common)
        self.assertIsNotNone(separate)

    def test_target_dirty_untracked_missing_and_path_relations_refuse(self) -> None:
        lease = self._lease()
        self.target_outputs[("status", "--porcelain=v1", "--untracked-files=all")] = self._ok("?? untracked-secret.txt\n")
        dirty = guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))
        self.assertEqual(dirty["operation_code"], guard.CODE_TARGET_DIRTY)
        self.assertTrue(dirty["dirty"])
        self.assertNotIn("untracked-secret", json.dumps(dirty))

        shutil.rmtree(self.target)
        missing = guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))
        self.assertEqual(missing["operation_code"], guard.CODE_TARGET_MISSING)

        inside = self.canonical / "disposable"
        inside.mkdir()
        inside_lease = self._lease(target=inside)
        relation = guard.decide_owned_temporary_worktree_cleanup(inside_lease, lambda: guard.OwnedProcessProbe(True, 0))
        self.assertEqual(relation["operation_code"], guard.CODE_TARGET_RELATION_UNSAFE)

    def test_target_gitlink_identity_and_ancestry_mismatches_refuse(self) -> None:
        lease = self._lease()
        self.target_outputs[("symbolic-ref", "--quiet", "--short", "HEAD")] = self._ok("feature/other\n")
        self.assertEqual(
            guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))["operation_code"],
            guard.CODE_TARGET_BRANCH_MISMATCH,
        )

        self._reset_git_outputs()
        self.target_outputs[("merge-base", "--is-ancestor", "a" * 40, "HEAD")] = guard._GitResult(1)
        self.assertEqual(
            guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))["operation_code"],
            guard.CODE_TARGET_BASE_NOT_ANCESTOR,
        )

        self._reset_git_outputs()
        (self.target / ".git").write_text("gitdir: outside\n", encoding="utf-8")
        self.assertEqual(
            guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))["operation_code"],
            guard.CODE_TARGET_COMMON_DIR_MISMATCH,
        )

        self._reset_git_outputs()
        self.target_outputs[("rev-parse", "--git-dir")] = self._ok(str(self.common / "worktrees" / "different") + "\n")
        (self.target / ".git").write_text(f"gitdir: {self.admin}\n", encoding="utf-8")
        self.assertEqual(
            guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))["operation_code"],
            guard.CODE_TARGET_GITLINK_INVALID,
        )

        self._reset_git_outputs()
        with patch.object(guard, "_has_link_or_reparse_component", side_effect=lambda path: Path(path).resolve() == self.target.resolve()):
            self.assertEqual(
                guard.decide_owned_temporary_worktree_cleanup(lease, lambda: guard.OwnedProcessProbe(True, 0))["operation_code"],
                guard.CODE_TARGET_LINKED,
            )

    def test_canonical_dirty_blocks_cleanup_before_process_probe(self) -> None:
        self.canonical_outputs[("status", "--porcelain=v1", "--untracked-files=all")] = self._ok(" M preserved.txt\n")
        lease = self._lease()
        called = []

        def probe() -> guard.OwnedProcessProbe:
            called.append(True)
            return guard.OwnedProcessProbe(True, 0)

        result = guard.decide_owned_temporary_worktree_cleanup(lease, probe)
        self.assertEqual(result["operation_code"], guard.CODE_WORKTREE_DIRTY)
        self.assertFalse(called)

    def test_dirty_canonical_assignment_is_observation_only_and_preserved(self) -> None:
        self.canonical_outputs[("status", "--porcelain=v1", "--untracked-files=all")] = self._ok(" M preserved.txt\n")
        result = guard.decide_canonical_assignment()
        self.assertEqual(result["operation_code"], guard.CODE_WORKTREE_DIRTY)
        self.assertEqual(result["action"], "OBSERVE_ONLY")
        self.assertFalse(result["action_available"])
        self.assertEqual(result["target_state"], "preserved_dirty")
        self.assertTrue(result["dirty"])
        self.assertNotIn("preserved.txt", json.dumps(result))
        snapshot = guard.read_forensic_snapshot()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["events"][-1]["event_type"], "assignment_decision")
        self.assertEqual(snapshot["events"][-1]["outcome"], "preservation_required")


if __name__ == "__main__":
    unittest.main()
