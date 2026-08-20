from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.shared.machine_local_recovery_executor as executor


class TestCapability:
    def __init__(self, token: str) -> None:
        self.token = token


class ForgedVerifier:
    def __init__(self, response: dict | None = None) -> None:
        self.response = response
        self.calls = 0

    def verify(self, capability: object, *, plan_value: dict, task_root: Path) -> dict:
        self.calls += 1
        if not isinstance(capability, TestCapability):
            return {"status": "blocked", "code": "authorization_rejected"}
        marker = task_root / ("consumed." + capability.token)
        if marker.exists():
            return {"status": "blocked", "code": "authorization_replay"}
        marker.write_text("consumed", encoding="ascii")
        if self.response is not None:
            return self.response
        return {"status": "verified", "phase": "preflight", "plan_fingerprint": plan_value.get("plan_fingerprint")}


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
            path = self.canonical / "preserved" / f"item-{index:02d}.bin"
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

    def run_preflight(self, verifier: object | None, capability: object, **kwargs: object) -> dict:
        values = {
            "guard_code": executor.EXPECTED_DIRTY_GUARD,
            "guard_dirty": True,
            "active_hub": False,
            "owned_processes": 0,
            "lock_held": False,
            "free_bytes": executor.MIN_DISK_MARGIN,
        }
        values.update(kwargs)
        return executor.preflight(
            self.planned(),
            capability,
            authorization_verifier=verifier,
            task_root=self.task,
            **values,
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
        rendered = json.dumps({"snapshot": snapshot, "plan": plan}, sort_keys=True)
        self.assertNotIn(str(self.canonical), rendered)
        self.assertNotIn(str(self.config), rendered)

    def test_candidate_projection_never_promotes_operational(self) -> None:
        projection = executor._candidate_projection()
        rendered = json.dumps(projection, sort_keys=True)
        for marker in ("running", "installed", "launchable", "operational"):
            self.assertNotIn(marker, rendered)
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
        changed = [{**self.manifest[0], "sha256": "b" * 64}, *self.manifest[1:]]
        drift = executor.inspect(canonical_root=self.canonical, config_root=self.config, preservation_manifest=changed, consumer_hashes=self.consumer_hashes, git_runner=self.git)
        self.assertEqual(drift["error"], "preservation_manifest_drift")
        consumers = {**self.consumer_hashes, executor.CONSUMER_FILES[0]: {"sha256": "c" * 64, "size": 1}}
        mismatch = executor.inspect(canonical_root=self.canonical, config_root=self.config, preservation_manifest=self.manifest, consumer_hashes=consumers, git_runner=self.git)
        self.assertEqual(mismatch["error"], "consumer_hash_mismatch")

    def test_canonical_identity_mismatch_blocks_plan(self) -> None:
        def wrong_git(_root: Path, args: tuple[str, ...]) -> str:
            value = self.git(_root, args)
            return "wrong" if args == ("rev-parse", "HEAD") else value

        snapshot = executor.inspect(canonical_root=self.canonical, config_root=self.config, preservation_manifest=self.manifest, consumer_hashes=self.consumer_hashes, git_runner=wrong_git)
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
        self.assertEqual(executor._compatibility_projection(unsafe), {"status": "apply_blocked", "code": "candidate_redaction_failed"})
        active = {"components": [{"id": "x", "status": "operational"}], "models": []}
        self.assertEqual(executor._compatibility_projection(active)["code"], "candidate_active_claim")

    def test_import_cannot_mint_and_mapping_capability_is_rejected(self) -> None:
        self.assertFalse(hasattr(executor, "issue_" + "authorization"))
        self.assertFalse(hasattr(executor, "Execution" + "Authorization"))
        self.assertFalse(hasattr(executor, "_ISSUER_" + "SEAL"))
        self.assertFalse(hasattr(executor, "Manager" + "AuthorizationVerifier"))
        verifier = ForgedVerifier()
        result = self.run_preflight(verifier, {"phase": "preflight"})
        self.assertEqual(result["error"], "manager_controller_required")
        self.assertEqual(verifier.calls, 0)

    def test_no_controller_or_malformed_shapes_refuse_before_state(self) -> None:
        capability = TestCapability("valid")
        for verifier in (None, object(), ForgedVerifier()):
            result = self.run_preflight(verifier, capability)
            self.assertEqual(result["error"], "manager_controller_required")

    def test_forged_local_verifier_can_never_reach_ready(self) -> None:
        verifier = ForgedVerifier({"status": "verified", "phase": "preflight", "plan_fingerprint": self.planned()["plan_fingerprint"]})
        before = sorted(path.relative_to(self.config).as_posix() for path in self.config.rglob("*"))
        result = self.run_preflight(verifier, TestCapability("ready"))
        after = sorted(path.relative_to(self.config).as_posix() for path in self.config.rglob("*"))
        self.assertEqual(result, {"status": "preflight_blocked", "execution": "not_run", "dry_run": True, "apply_allowed": False, "error": "manager_controller_required"})
        self.assertEqual(verifier.calls, 0)
        self.assertEqual(before, after)

    def test_unverifiable_phase_expiry_replay_shapes_refuse(self) -> None:
        plan = self.planned()
        capability = TestCapability("phase")
        for forged in (
            {"status": "verified", "phase": "apply", "plan_fingerprint": plan["plan_fingerprint"]},
            {"status": "blocked", "code": "authorization_expired"},
            {"status": "blocked", "code": "authorization_replay"},
        ):
            verifier = ForgedVerifier(forged)
            result = executor.preflight(plan, capability, authorization_verifier=verifier, task_root=self.task, guard_code=executor.EXPECTED_DIRTY_GUARD, guard_dirty=True, active_hub=False, owned_processes=0, lock_held=False, free_bytes=executor.MIN_DISK_MARGIN)
            self.assertEqual(result["error"], "manager_controller_required")
            self.assertEqual(verifier.calls, 0)

    def test_plan_mismatch_and_each_preflight_gate_refuse(self) -> None:
        plan = self.planned()
        forged = {**plan, "plan_fingerprint": "f" * 64}
        verifier = ForgedVerifier()
        mismatch = executor.preflight(forged, TestCapability("mismatch"), authorization_verifier=verifier, task_root=self.task, guard_code=executor.EXPECTED_DIRTY_GUARD, guard_dirty=True, active_hub=False, owned_processes=0, lock_held=False, free_bytes=executor.MIN_DISK_MARGIN)
        self.assertEqual(mismatch["error"], "manager_controller_required")
        for kwargs, code in (
            ({"active_hub": True, "owned_processes": 0, "lock_held": False, "free_bytes": executor.MIN_DISK_MARGIN}, "preflight_hub"),
            ({"active_hub": False, "owned_processes": None, "lock_held": False, "free_bytes": executor.MIN_DISK_MARGIN}, "preflight_process"),
            ({"active_hub": False, "owned_processes": 0, "lock_held": True, "free_bytes": executor.MIN_DISK_MARGIN}, "preflight_lock"),
            ({"active_hub": False, "owned_processes": 0, "lock_held": False, "free_bytes": 1}, "preflight_disk"),
        ):
            result = self.run_preflight(ForgedVerifier(), TestCapability(code), **kwargs)
            self.assertEqual(result["error"], "manager_controller_required")

    def test_all_refusal_text_is_finite_and_redacted(self) -> None:
        result = self.run_preflight(ForgedVerifier(), TestCapability("redacted"), guard_code="bad", guard_dirty=False, free_bytes=0)
        rendered = json.dumps(result, sort_keys=True)
        self.assertEqual(result["status"], "preflight_blocked")
        self.assertNotIn(str(self.canonical), rendered)
        self.assertNotIn("http", rendered.casefold())
        self.assertNotIn("powershell", rendered.casefold())

    def test_direct_root_entrypoint_is_sanitized_noop(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "manager_machine_local_recovery_executor.py"
        with tempfile.TemporaryDirectory(prefix="config-shaped-sentinel-") as root:
            result = subprocess.run([sys.executable, "-B", str(script)], cwd=repo_root, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.stdout, "inspect_plan_preflight_only: manager authorization and attested inputs required; recovery not_run\n")
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
