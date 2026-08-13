from __future__ import annotations

import hashlib
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


def _h40(letter: str = "a") -> str:
    return letter * 40


def _h64(letter: str = "b") -> str:
    return letter * 64


def binding_mapping() -> dict:
    return {
        "controller_head": _h40("1"),
        "controller_tree": _h40("2"),
        "controller_script_sha256": _h64("3"),
        "executor_head": _h40("4"),
        "executor_tree": _h40("5"),
        "executor_script_sha256": _h64("6"),
        "canonical_head": controller.EXPECTED_CANONICAL_HEAD,
        "canonical_tree": controller.EXPECTED_CANONICAL_TREE,
        "preservation_digest": controller.EXPECTED_MANIFEST_DIGEST,
        "consumer_bindings_digest": _h64("7"),
        "plan_fingerprint": _h64("8"),
        "target_names": list(controller.TARGETS),
    }


def available_snapshot() -> dict:
    return {
        "status": "available",
        "canonical_head": controller.EXPECTED_CANONICAL_HEAD,
        "canonical_tree": controller.EXPECTED_CANONICAL_TREE,
        "preservation_digest": controller.EXPECTED_MANIFEST_DIGEST,
        "preservation_count": executor.MANIFEST_COUNT,
        "consumer_digest": _h64("7"),
        "consumer_count": 4,
        "targets": [{"target": name, "state": "absent", "sha256": None} for name in controller.TARGETS],
    }


class ControllerTests(unittest.TestCase):
    def binding(self) -> controller.SessionBinding:
        return controller.SessionBinding.from_mapping(binding_mapping())

    def test_binding_requires_exact_identity_manifest_consumers_and_targets(self) -> None:
        binding = self.binding()
        self.assertEqual(binding.canonical_head, controller.EXPECTED_CANONICAL_HEAD)
        self.assertEqual(binding.target_names, controller.TARGETS)
        for key in ("canonical_head", "canonical_tree"):
            forged = binding_mapping()
            forged[key] = "f" * 40
            with self.assertRaises(controller.ControllerError):
                controller.SessionBinding.from_mapping(forged)
        forged = binding_mapping()
        forged["target_names"] = ["components.json"]
        with self.assertRaises(controller.ControllerError):
            controller.SessionBinding.from_mapping(forged)
        for field in ("controller_script_sha256", "executor_script_sha256", "preservation_digest", "consumer_bindings_digest", "plan_fingerprint"):
            forged = binding_mapping()
            forged[field] = "f" * 64
            if field == "preservation_digest":
                with self.assertRaises(controller.ControllerError):
                    controller.SessionBinding.from_mapping(forged)
            else:
                self.assertIsInstance(controller.SessionBinding.from_mapping(forged), controller.SessionBinding)

    def test_projection_is_always_no_write_and_blocks_active_hub_mismatch_and_plan(self) -> None:
        binding = self.binding()
        for snapshot, guard, active, expected, plan_fp in (
            (available_snapshot(), {"code": controller.EXPECTED_GUARD_CODE, "dirty": True}, False, "manager_controller_required", binding.plan_fingerprint),
            (available_snapshot(), {"code": controller.EXPECTED_GUARD_CODE, "dirty": True}, True, "active_hub", binding.plan_fingerprint),
            ({**available_snapshot(), "consumer_count": 3}, {"code": controller.EXPECTED_GUARD_CODE, "dirty": True}, False, "consumer_binding_mismatch", binding.plan_fingerprint),
            (available_snapshot(), {"code": "OTHER", "dirty": True}, False, "canonical_preservation_guard_required", binding.plan_fingerprint),
            (available_snapshot(), {"code": controller.EXPECTED_GUARD_CODE, "dirty": True}, False, "plan_binding_mismatch", _h64("9")),
        ):
            result = controller.inspect_plan_projection(canonical_state=guard, snapshot=snapshot, binding=binding, plan_fingerprint=plan_fp, active_hub=active)
            self.assertEqual(result["status"], "apply_blocked")
            self.assertEqual(result["execution"], "not_run")
            self.assertTrue(result["dry_run"])
            self.assertFalse(result["apply_allowed"])
            self.assertEqual(result["reason"], expected)
        missing_target = available_snapshot()
        missing_target["targets"] = missing_target["targets"][:-1]
        result = controller.inspect_plan_projection(canonical_state={"code": controller.EXPECTED_GUARD_CODE, "dirty": True}, snapshot=missing_target, binding=binding, plan_fingerprint=binding.plan_fingerprint, active_hub=False)
        self.assertEqual(result["reason"], "target_set_mismatch")
        self.assertNotIn("D:\\", json.dumps(result))
        self.assertNotIn("http", json.dumps(result).casefold())

    def test_collect_uses_inspect_canonical_not_snapshot_writer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controller-fixture-") as root:
            fake_root = Path(root)
            called = []

            def guard_reader() -> dict:
                called.append("inspect")
                return {"code": controller.EXPECTED_GUARD_CODE, "dirty": True, "raw_path": str(fake_root)}

            fake_snapshot = available_snapshot()
            with patch.object(executor, "inspect", return_value=fake_snapshot) as inspect_mock, patch.object(canonical_git_integrity, "preflight_canonical", side_effect=AssertionError("forbidden snapshot writer")):
                result = controller.collect_static_projection(canonical_root=fake_root, config_root=fake_root, preservation_manifest=[], consumer_hashes={"a": {}}, canonical_integrity_reader=guard_reader)
            self.assertEqual(called, ["inspect"])
            inspect_mock.assert_called_once()
            self.assertEqual(result["guard"], {"code": controller.EXPECTED_GUARD_CODE, "dirty": True})
            self.assertNotIn(str(fake_root), json.dumps(result))
            self.assertNotIn(controller.EXPECTED_MANIFEST_DIGEST, json.dumps(result))
            self.assertNotIn("consumer_digest", json.dumps(result))

    def test_parent_pipe_is_required_and_manual_child_cannot_reach_handler(self) -> None:
        binding = self.binding()
        with self.assertRaises(controller.ControllerError):
            controller.ParentSession(binding, object())
        pipe = controller.open_parent_pipe()
        session = controller.ParentSession(binding, pipe)
        self.assertEqual(session.receive({}, object())["reason"], "parent_pipe_required")
        self.assertEqual(session.receive({}, pipe)["reason"], "frame_shape_invalid")

    def test_authenticated_frames_are_bounded_and_replay_or_tamper_refuses(self) -> None:
        pipe = controller.open_parent_pipe()
        session = controller.ParentSession(self.binding(), pipe)
        endpoint = session.child_endpoint()
        first = endpoint.frame(payload={"status": "ack"})
        self.assertEqual(session.receive(first, pipe)["status"], "accepted")
        self.assertEqual(session.receive(first, pipe)["reason"], "sequence_or_session_mismatch")
        stale = endpoint.frame(seq=1, payload={"status": "ack"})
        stale["mac"] = "0" * 64
        self.assertEqual(session.receive(stale, pipe)["reason"], "frame_auth_invalid")
        second_session = controller.ParentSession(self.binding(), pipe)
        second_endpoint = second_session.child_endpoint()
        unknown = second_endpoint.frame(kind="challenge")
        self.assertEqual(second_session.receive(unknown, pipe)["reason"], "frame_kind_invalid")

    def test_expiry_nonce_transcript_and_malformed_frames_refuse(self) -> None:
        now = [100.0]
        pipe = controller.open_parent_pipe()
        session = controller.ParentSession(self.binding(), pipe, clock=lambda: now[0])
        endpoint = session.child_endpoint()
        now[0] = 200.0
        self.assertEqual(session.receive(endpoint.frame(), pipe)["reason"], "session_expired")
        session = controller.ParentSession(self.binding(), pipe)
        endpoint = session.child_endpoint()
        frame = endpoint.frame()
        frame["payload"] = {"unsafe": {"nested": True}}
        self.assertEqual(session.receive(frame, pipe)["reason"], "frame_payload_invalid")
        huge = endpoint.frame()
        huge["payload"] = {"x": "x" * 10000}
        self.assertEqual(session.receive(huge, pipe)["reason"], "frame_payload_invalid")

    def test_direct_cli_is_finite_sanitized_noop_and_writes_nothing(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "manager_machine_local_recovery_controller.py"
        with tempfile.TemporaryDirectory(prefix="controller-noop-") as root:
            before = sorted(Path(root).iterdir())
            result = subprocess.run([sys.executable, "-B", str(script)], cwd=repo_root, capture_output=True, text=True, check=False)
            after = sorted(Path(root).iterdir())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "manager_recovery_controller_noop: authenticated parent pipe required; execution not_run\n")
        self.assertEqual(before, after)
        self.assertNotIn("D:\\", result.stdout)

    def test_no_apply_resume_or_runtime_symbols_in_controller(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "shared" / "machine_local_recovery_controller.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("apply_plan", text)
        self.assertNotIn("resume_journal", text)
        self.assertNotIn("preflight_canonical", text)
        self.assertNotIn("urlopen", text)
        self.assertNotIn("Popen", text)


if __name__ == "__main__":
    unittest.main()
