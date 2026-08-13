from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.shared import canonical_git_integrity
from src.shared import machine_local_recovery_controller as controller
from src.shared import machine_local_recovery_executor as executor


def h40(char: str) -> str:
    return char * 40


def h64(char: str) -> str:
    return char * 64


def measured_binding(plan: str | None = None) -> controller._MeasuredBinding:
    return controller._MeasuredBinding(
        controller_head=controller.EXPECTED_CANONICAL_HEAD,
        controller_tree=controller.EXPECTED_CANONICAL_TREE,
        controller_module_sha256=h64("1"),
        controller_script_sha256=h64("2"),
        executor_head=controller.EXPECTED_CANONICAL_HEAD,
        executor_tree=controller.EXPECTED_CANONICAL_TREE,
        executor_module_sha256=h64("3"),
        executor_script_sha256=h64("4"),
        canonical_head=controller.EXPECTED_CANONICAL_HEAD,
        canonical_tree=controller.EXPECTED_CANONICAL_TREE,
        preservation_digest=controller.EXPECTED_MANIFEST_DIGEST,
        consumer_bindings_digest=h64("5"),
        plan_fingerprint=plan or h64("6"),
    )


def snapshot(binding: controller._MeasuredBinding | None = None) -> dict:
    binding = binding or measured_binding()
    return {
        "status": "available",
        "canonical_head": controller.EXPECTED_CANONICAL_HEAD,
        "canonical_tree": controller.EXPECTED_CANONICAL_TREE,
        "preservation_digest": controller.EXPECTED_MANIFEST_DIGEST,
        "preservation_count": executor.MANIFEST_COUNT,
        "consumer_digest": binding.consumer_bindings_digest,
        "consumer_count": len(controller.CONSUMER_FILES),
        "targets": [{"target": name, "state": "absent", "sha256": None} for name in controller.TARGETS],
    }


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="controller-fixture-")
        self.root = Path(self.temp.name)
        self.plan_fingerprint = h64("6")
        for relative in controller.CONSUMER_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-consumer")
        self.plan_patch = patch.object(executor, "plan", return_value={"status": "planned", "plan_fingerprint": self.plan_fingerprint})
        self.plan_patch.start()
        self.addCleanup(self.plan_patch.stop)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fake_guard(self) -> dict:
        return {"operation_code": controller.EXPECTED_GUARD_CODE, "dirty": True, "raw_path": str(self.root)}

    def fake_hub(self) -> dict:
        return {"known": True, "active": False}

    def fake_inspect(self) -> dict:
        return snapshot(measured_binding(self.plan_fingerprint))

    def test_no_public_in_process_pipe_constructor_exists(self) -> None:
        for name in ("ParentSession", "ChildPipeEndpoint", "ParentPipe", "open_parent_pipe", "child_endpoint"):
            self.assertFalse(hasattr(controller, name))

    def test_actual_spawned_child_pipe_handshake_is_parent_owned(self) -> None:
        binding = measured_binding(self.plan_fingerprint)
        with patch.object(controller, "_measure_binding", return_value=binding), patch.object(executor, "inspect", return_value=self.fake_inspect()):
            result = controller.run_parent_preflight(private_root=self.root, canonical_root=self.root, config_root=self.root, preservation_manifest=[], plan_fingerprint=self.plan_fingerprint, hub_probe=self.fake_hub, canonical_integrity_reader=self.fake_guard)
        self.assertEqual(result["status"], "apply_blocked")
        self.assertEqual(result["execution"], "not_run")
        self.assertFalse(result["apply_allowed"])
        self.assertNotIn("accepted", json.dumps(result))

    def test_actual_child_mac_timeout_and_disconnect_refusals(self) -> None:
        binding = measured_binding(self.plan_fingerprint)
        self.assertIn(controller._run_child_session(binding, timeout=0.3, fault="tamper"), {"child_timeout", "child_protocol_refused"})
        self.assertEqual(controller._run_child_session(binding, timeout=0.01, fault="timeout"), "child_timeout")
        self.assertEqual(controller._run_child_session(binding, timeout=0.3, fault="disconnect"), "child_disconnected")
        with self.assertRaises(controller.ControllerError):
            controller._validate_frame({})

    def test_arbitrary_import_only_caller_cannot_construct_or_send_session(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "shared" / "machine_local_recovery_controller.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("class ParentSession", text)
        self.assertNotIn("class ChildPipeEndpoint", text)
        self.assertNotIn("socket", text.casefold())
        self.assertNotIn("public socket", text.casefold())

    def test_binding_is_measured_and_wrong_identity_fails_before_child(self) -> None:
        with patch.object(controller, "_git_identity", return_value=("f" * 40, controller.EXPECTED_CANONICAL_TREE)):
            with self.assertRaises(controller.ControllerError) as raised:
                controller._measure_binding(self.root, [], self.plan_fingerprint)
        self.assertEqual(raised.exception.code, "controller_identity_mismatch")
        with patch.object(controller, "_git_identity", return_value=(controller.EXPECTED_CONTROLLER_HEAD, controller.EXPECTED_CONTROLLER_TREE)), patch.object(controller, "_measure_manifest", return_value=controller.EXPECTED_MANIFEST_DIGEST), patch.object(controller, "_hash_file", return_value=h64("a")):
            binding = controller._measure_binding(self.root, [], self.plan_fingerprint)
        self.assertEqual(binding.controller_script_sha256, h64("a"))
        self.assertEqual(binding.executor_tree, controller.EXPECTED_CONTROLLER_TREE)

    def test_operation_code_preserves_dirty_guard_truth(self) -> None:
        binding = measured_binding(self.plan_fingerprint)
        with patch.object(controller, "_measure_binding", return_value=binding), patch.object(executor, "inspect", return_value=self.fake_inspect()):
            wrong = controller.run_parent_preflight(private_root=self.root, canonical_root=self.root, config_root=self.root, preservation_manifest=[], plan_fingerprint=self.plan_fingerprint, hub_probe=self.fake_hub, canonical_integrity_reader=lambda: {"code": controller.EXPECTED_GUARD_CODE, "dirty": True})
            right = controller.run_parent_preflight(private_root=self.root, canonical_root=self.root, config_root=self.root, preservation_manifest=[], plan_fingerprint=self.plan_fingerprint, hub_probe=self.fake_hub, canonical_integrity_reader=self.fake_guard)
        self.assertEqual(wrong["reason"], "canonical_preservation_guard_required")
        self.assertEqual(right["reason"], "manager_controller_required")

    def test_actual_source_consumer_mismatch_and_active_hub_are_blockers(self) -> None:
        binding = measured_binding(self.plan_fingerprint)
        mismatch = {**self.fake_inspect(), "consumer_digest": h64("9")}
        with patch.object(controller, "_measure_binding", return_value=binding), patch.object(executor, "inspect", return_value=mismatch):
            source_result = controller.run_parent_preflight(private_root=self.root, canonical_root=self.root, config_root=self.root, preservation_manifest=[], plan_fingerprint=self.plan_fingerprint, hub_probe=self.fake_hub, canonical_integrity_reader=self.fake_guard)
        with patch.object(controller, "_measure_binding", return_value=binding), patch.object(executor, "inspect", return_value=self.fake_inspect()):
            active_result = controller.run_parent_preflight(private_root=self.root, canonical_root=self.root, config_root=self.root, preservation_manifest=[], plan_fingerprint=self.plan_fingerprint, hub_probe=lambda: {"known": True, "active": True}, canonical_integrity_reader=self.fake_guard)
        self.assertEqual(source_result["reason"], "source_consumer_mismatch")
        self.assertEqual(active_result["reason"], "active_hub")

    def test_plan_fingerprint_mismatch_is_a_current_blocker(self) -> None:
        binding = measured_binding(self.plan_fingerprint)
        with patch.object(controller, "_measure_binding", return_value=binding), patch.object(executor, "inspect", return_value=self.fake_inspect()), patch.object(executor, "plan", return_value={"status": "planned", "plan_fingerprint": h64("9")}):
            result = controller.run_parent_preflight(private_root=self.root, canonical_root=self.root, config_root=self.root, preservation_manifest=[], plan_fingerprint=self.plan_fingerprint, hub_probe=self.fake_hub, canonical_integrity_reader=self.fake_guard)
        self.assertEqual(result["reason"], "plan_binding_mismatch")

    def test_hub_probe_missing_or_unknown_is_not_false(self) -> None:
        binding = measured_binding(self.plan_fingerprint)
        with patch.object(controller, "_measure_binding", return_value=binding), patch.object(executor, "inspect", return_value=self.fake_inspect()):
            missing = controller.run_parent_preflight(private_root=self.root, canonical_root=self.root, config_root=self.root, preservation_manifest=[], plan_fingerprint=self.plan_fingerprint, hub_probe=None, canonical_integrity_reader=self.fake_guard)
            unknown = controller.run_parent_preflight(private_root=self.root, canonical_root=self.root, config_root=self.root, preservation_manifest=[], plan_fingerprint=self.plan_fingerprint, hub_probe=lambda: {"known": False, "active": False}, canonical_integrity_reader=self.fake_guard)
        self.assertEqual(missing["reason"], "active_hub_probe_required")
        self.assertEqual(unknown["reason"], "active_hub_probe_unknown")

    def test_projection_has_no_raw_paths_hashes_or_content(self) -> None:
        binding = measured_binding(self.plan_fingerprint)
        with patch.object(controller, "_measure_binding", return_value=binding), patch.object(executor, "inspect", return_value=self.fake_inspect()):
            result = controller.run_parent_preflight(private_root=self.root, canonical_root=self.root, config_root=self.root, preservation_manifest=[], plan_fingerprint=self.plan_fingerprint, hub_probe=self.fake_hub, canonical_integrity_reader=self.fake_guard)
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn(controller.EXPECTED_MANIFEST_DIGEST, rendered)
        self.assertNotIn("http", rendered.casefold())
        self.assertEqual(set(result), {"status", "execution", "dry_run", "apply_allowed", "reason", "next_action"})

    def test_no_forensic_snapshot_writer_or_machine_runtime_symbols(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "shared" / "machine_local_recovery_controller.py").read_text(encoding="utf-8")
        for marker in ("preflight_canonical", "apply_plan", "resume_journal", "urlopen", "Popen", "ffmpeg", "ollama"):
            self.assertNotIn(marker, source)
        with patch.object(canonical_git_integrity, "preflight_canonical", side_effect=AssertionError("writer forbidden")):
            self.assertEqual(self.fake_guard()["operation_code"], controller.EXPECTED_GUARD_CODE)

    def test_direct_cli_is_fixed_noop_and_no_input(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "manager_machine_local_recovery_controller.py"
        result = subprocess.run([sys.executable, "-B", str(script), "--unexpected", "payload"], cwd=repo_root, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "manager_recovery_controller_noop: authenticated parent pipe required; execution not_run\n")


if __name__ == "__main__":
    unittest.main()
