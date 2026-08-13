from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.shared.machine_local_recovery_executor as executor
from scripts.manager_machine_local_recovery_executor import main


class ExecutorFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="machine-recovery-fixture-")
        root = Path(self.temp.name)
        self.canonical = root / "canonical"
        self.config = root / "config"
        self.task = root / "task"
        for path in (self.canonical, self.config, self.task):
            path.mkdir()
        for index in range(executor.MANIFEST_COUNT):
            path = self.canonical / "preserved" / (f"item-{index:02d}.bin")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"preserved-{index}".encode("ascii"))
        for relative in executor.CONSUMER_FILES:
            path = self.canonical / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("consumer:" + relative).encode("ascii"))
        self.manifest = []
        for path in sorted((self.canonical / "preserved").glob("*.bin")):
            self.manifest.append({
                "relative_path": path.relative_to(self.canonical).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            })
        self.consumer_hashes = {}
        for relative in executor.CONSUMER_FILES:
            path = self.canonical / relative
            self.consumer_hashes[relative] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, _root: Path, args: tuple[str, ...]) -> str:
        return {
            ("rev-parse", "HEAD"): executor.EXPECTED_CANONICAL_HEAD,
            ("rev-parse", "HEAD^{tree}"): executor.EXPECTED_CANONICAL_TREE,
            ("status", "--porcelain"): " M PLAN.md",
        }[args]

    def snapshot(self) -> dict:
        return executor.inspect(
            canonical_root=self.canonical,
            config_root=self.config,
            preservation_manifest=self.manifest,
            consumer_hashes=self.consumer_hashes,
            git_runner=self.git,
        )

    def planned(self) -> dict:
        return executor.plan(
            self.snapshot(),
            executor_head="executor-head",
            executor_tree="executor-tree",
            executor_script_sha256="a" * 64,
        )

    def authorization(self) -> executor.ExecutionAuthorization:
        return executor.issue_authorization(
            self.planned(),
            manager_issuer=executor._ISSUER_SEAL,
        )


class MachineRecoveryExecutorTests(ExecutorFixture):
    def test_inspect_and_plan_are_sanitized_and_static(self) -> None:
        snapshot = self.snapshot()
        self.assertEqual(snapshot["status"], "available")
        self.assertTrue(snapshot["dirty"])
        plan = self.planned()
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["execution"], "not_run")
        self.assertFalse(plan["apply_allowed"])
        self.assertEqual(len(plan["targets"]), 4)
        self.assertEqual([row["decision"] for row in plan["targets"]], ["manual_review", "auto_create", "manual_review", "auto_create"])
        rendered = json.dumps({"snapshot": snapshot, "plan": plan}, sort_keys=True)
        self.assertNotIn(str(self.canonical), rendered)
        self.assertNotIn(str(self.config), rendered)

    def test_candidate_projection_never_promotes_operational(self) -> None:
        projection = executor._candidate_projection()
        rendered = json.dumps(projection, sort_keys=True)
        self.assertNotIn("running", rendered)
        self.assertNotIn("installed", rendered)
        self.assertNotIn("launchable", rendered)
        self.assertNotIn("operational", rendered)
        self.assertEqual(executor._compatibility_projection(projection)["status"], "compatible_static")

    def test_fixed_target_present_is_manual_review(self) -> None:
        (self.config / "components.json").write_text('{"components":[{"id":"local_ai_api"}]}', encoding="utf-8")
        (self.config / "hub_config.json").write_text('{"unexpected":true}', encoding="utf-8")
        states = {row["target"]: row for row in self.snapshot()["targets"]}
        self.assertEqual(states["components.json"]["state"], "valid")
        self.assertEqual(states["hub_config.json"]["state"], "valid")
        target_rows = {row["target"]: row for row in self.planned()["targets"]}
        self.assertEqual(target_rows["components.json"]["decision"], "manual_review")
        self.assertEqual(target_rows["hub_config.json"]["decision"], "manual_review")

    def test_manifest_drift_and_consumer_mismatch_fail_closed(self) -> None:
        changed = list(self.manifest)
        changed[0] = {**changed[0], "sha256": "b" * 64}
        drift = executor.inspect(
            canonical_root=self.canonical,
            config_root=self.config,
            preservation_manifest=changed,
            consumer_hashes=self.consumer_hashes,
            git_runner=self.git,
        )
        self.assertEqual(drift["error"], "preservation_manifest_drift")
        consumers = {**self.consumer_hashes, executor.CONSUMER_FILES[0]: {"sha256": "c" * 64, "size": 1}}
        mismatch = executor.inspect(
            canonical_root=self.canonical,
            config_root=self.config,
            preservation_manifest=self.manifest,
            consumer_hashes=consumers,
            git_runner=self.git,
        )
        self.assertEqual(mismatch["error"], "consumer_hash_mismatch")

    def test_canonical_identity_mismatch_blocks_plan(self) -> None:
        def wrong_git(_root: Path, args: tuple[str, ...]) -> str:
            values = self.git(_root, args)
            return "wrong" if args == ("rev-parse", "HEAD") else values

        snapshot = executor.inspect(
            canonical_root=self.canonical,
            config_root=self.config,
            preservation_manifest=self.manifest,
            consumer_hashes=self.consumer_hashes,
            git_runner=wrong_git,
        )
        self.assertEqual(executor.plan(snapshot, executor_head="h", executor_tree="t", executor_script_sha256="d" * 64)["status"], "apply_blocked")

    def test_reparse_target_is_rejected_before_target_read(self) -> None:
        original = executor._safe_child

        def reject_target(root: Path, relative: str) -> Path:
            if relative == "components.json":
                raise executor.RecoveryError("target_reparse")
            return original(root, relative)

        before = sorted(path.relative_to(self.config).as_posix() for path in self.config.rglob("*"))
        with patch.object(executor, "_safe_child", side_effect=reject_target):
            snapshot = self.snapshot()
        after = sorted(path.relative_to(self.config).as_posix() for path in self.config.rglob("*"))
        self.assertEqual(snapshot["error"], "target_reparse")
        self.assertEqual(before, after)
        self.assertFalse((self.config / executor.JOURNAL_NAME).exists())

    def test_compatibility_rejects_unsafe_and_active_claims_without_echo(self) -> None:
        unsafe = {"components": [{"id": "x", "note": "https://private.invalid"}], "models": []}
        blocked = executor._compatibility_projection(unsafe)
        self.assertEqual(blocked, {"status": "apply_blocked", "code": "candidate_redaction_failed"})
        active = {"components": [{"id": "x", "status": "operational"}], "models": []}
        self.assertEqual(executor._compatibility_projection(active)["code"], "candidate_active_claim")
        self.assertNotIn("private.invalid", json.dumps(blocked))

    def test_authorization_requires_private_issuer(self) -> None:
        with self.assertRaises(executor.RecoveryError) as raised:
            executor.issue_authorization(self.planned(), manager_issuer=object())
        self.assertEqual(raised.exception.code, "manager_issuer_required")

    def test_mutated_public_capability_is_rejected(self) -> None:
        capability = self.authorization()
        capability.public["canonical_head"] = "forged"
        result = executor.preflight(self.planned(), capability, task_root=self.task, guard_code=executor.EXPECTED_DIRTY_GUARD, guard_dirty=True, active_hub=False, owned_processes=0, lock_held=False, free_bytes=executor.MIN_DISK_MARGIN)
        self.assertEqual(result["error"], "authorization_binding_mismatch")

    def test_authorization_expiry_and_replay_are_bounded(self) -> None:
        capability = self.authorization()
        with patch.object(executor.time, "time", return_value=capability.public["expires_at"]):
            expired = executor.preflight(self.planned(), capability, task_root=self.task, guard_code=executor.EXPECTED_DIRTY_GUARD, guard_dirty=True, active_hub=False, owned_processes=0, lock_held=False, free_bytes=executor.MIN_DISK_MARGIN)
        self.assertEqual(expired["error"], "authorization_expired")
        capability = self.authorization()
        first = executor.preflight(self.planned(), capability, task_root=self.task, guard_code="OTHER", guard_dirty=True, active_hub=False, owned_processes=0, lock_held=False, free_bytes=executor.MIN_DISK_MARGIN)
        second = executor.preflight(self.planned(), capability, task_root=self.task, guard_code="OTHER", guard_dirty=True, active_hub=False, owned_processes=0, lock_held=False, free_bytes=executor.MIN_DISK_MARGIN)
        self.assertEqual(first["error"], "preflight_guard")
        self.assertEqual(second["error"], "authorization_replay")

    def test_plan_mismatch_and_each_preflight_gate_refuse(self) -> None:
        plan = self.planned()
        capability = executor.issue_authorization(plan, manager_issuer=executor._ISSUER_SEAL)
        forged = {**plan, "plan_fingerprint": "f" * 64}
        mismatch = executor.preflight(forged, capability, task_root=self.task, guard_code=executor.EXPECTED_DIRTY_GUARD, guard_dirty=True, active_hub=False, owned_processes=0, lock_held=False, free_bytes=executor.MIN_DISK_MARGIN)
        self.assertEqual(mismatch["error"], "preflight_plan")
        for kwargs, code in (
            ({"active_hub": True, "owned_processes": 0, "lock_held": False, "free_bytes": executor.MIN_DISK_MARGIN}, "preflight_hub"),
            ({"active_hub": False, "owned_processes": None, "lock_held": False, "free_bytes": executor.MIN_DISK_MARGIN}, "preflight_process"),
            ({"active_hub": False, "owned_processes": 0, "lock_held": True, "free_bytes": executor.MIN_DISK_MARGIN}, "preflight_lock"),
            ({"active_hub": False, "owned_processes": 0, "lock_held": False, "free_bytes": 1}, "preflight_disk"),
        ):
            capability = self.authorization()
            result = executor.preflight(plan, capability, task_root=self.task, guard_code=executor.EXPECTED_DIRTY_GUARD, guard_dirty=True, **kwargs)
            self.assertEqual(result["error"], code)

    def test_all_refusal_text_is_finite_and_redacted(self) -> None:
        plan = self.planned()
        capability = self.authorization()
        result = executor.preflight(plan, capability, task_root=self.task, guard_code="bad", guard_dirty=False, active_hub=False, owned_processes=0, lock_held=False, free_bytes=0)
        rendered = json.dumps(result, sort_keys=True)
        self.assertEqual(result["status"], "preflight_blocked")
        self.assertNotIn(str(self.canonical), rendered)
        self.assertNotIn("http", rendered.casefold())
        self.assertNotIn("powershell", rendered.casefold())

    def test_default_entrypoint_is_no_write_and_no_apply(self) -> None:
        with tempfile.TemporaryDirectory(prefix="config-shaped-sentinel-") as root:
            sentinel = Path(root) / "components.json"
            before = sorted(path.name for path in Path(root).iterdir())
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 0)
            after = sorted(path.name for path in Path(root).iterdir())
        self.assertEqual(before, after)
        self.assertFalse(sentinel.exists())
        self.assertNotIn("apply", output.getvalue().casefold())
        self.assertFalse(hasattr(executor, "apply_plan"))
        self.assertFalse(hasattr(executor, "resume_journal"))


if __name__ == "__main__":
    unittest.main()
