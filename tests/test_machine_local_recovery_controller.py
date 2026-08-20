from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from src.shared import machine_local_recovery_controller as controller


class Explosive:
    def __getattribute__(self, _name: str) -> object:
        raise AssertionError("preflight dereferenced hostile input")


class ForgedBroker:
    def __call__(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("forged broker was invoked")


class ControllerTests(unittest.TestCase):
    def test_preflight_refuses_before_any_input_access(self) -> None:
        result = controller.run_parent_preflight(
            private_root=Explosive(),
            canonical_root=Explosive(),
            config_root=Explosive(),
            preservation_manifest=Explosive(),
            plan_fingerprint=Explosive(),
            hub_probe=Explosive(),
            canonical_integrity_reader=Explosive(),
            git_runner=Explosive(),
        )
        self.assertEqual(result, {
            "status": "preflight_blocked",
            "execution": "not_run",
            "dry_run": True,
            "apply_allowed": False,
            "reason": "manager_broker_required",
            "next_action": "manager_broker_required",
        })

    def test_forged_callback_pipe_and_authority_cannot_reach_accepted(self) -> None:
        forged = ForgedBroker()
        result = controller.run_parent_preflight(
            object(), object(), forged, forged, forged, forged, forged, forged
        )
        self.assertNotIn("accepted", json.dumps(result))
        self.assertNotIn("ready", json.dumps(result))
        self.assertEqual(result["reason"], "manager_broker_required")

    def test_no_transport_or_authority_constructors_exist(self) -> None:
        for name in (
            "ParentSession",
            "ChildPipeEndpoint",
            "ParentPipe",
            "open_parent_pipe",
            "child_endpoint",
            "_MeasuredBinding",
            "_run_child_session",
            "_child_entry",
        ):
            self.assertFalse(hasattr(controller, name), name)

    def test_projection_is_fixed_and_redacted(self) -> None:
        result = controller.run_parent_preflight(
            private_root=Path("C:/private"),
            canonical_root=Path("D:/canonical"),
            config_root=Path("D:/Config"),
            preservation_manifest=[],
            plan_fingerprint="f" * 64,
            hub_probe=lambda: {"known": True, "active": True},
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("C:/", rendered)
        self.assertNotIn("D:/", rendered)
        self.assertNotIn("http", rendered.casefold())
        self.assertNotIn("[object Object]", rendered)
        self.assertEqual(set(result), {"status", "execution", "dry_run", "apply_allowed", "reason", "next_action"})

    def test_fixed_truth_contract(self) -> None:
        self.assertEqual(controller.EXPECTED_GUARD_CODE, "CANONICAL_PRESERVATION_REQUIRED")
        self.assertEqual(controller.EXPECTED_EXECUTOR_PROVENANCE_HEAD, "de12ff153750756ab9e49375750ea3398293943a")
        self.assertEqual(controller.EXPECTED_CANONICAL_HEAD, "ca998106fe2319da6b41fe1c73c6df834d65b2c8")
        self.assertEqual(len(controller.TARGETS), 4)

    def test_source_has_no_machine_execution_or_snapshot_writer(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "shared" / "machine_local_recovery_controller.py").read_text(encoding="utf-8")
        for marker in (
            "preflight_canonical",
            "apply_plan",
            "resume_journal",
            "urlopen",
            "Popen",
            "socket",
            "ffmpeg",
            "ollama",
            "multiprocessing",
            "accepted",
            "preflight_ready",
        ):
            self.assertNotIn(marker, source.casefold())

    def test_direct_cli_is_fixed_noop_and_no_input(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "manager_machine_local_recovery_controller.py"
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--unexpected", "payload"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            "manager_recovery_controller_noop: manager broker required; execution not_run\n",
        )

    def test_docs_describe_external_broker_boundary(self) -> None:
        docs = (Path(__file__).resolve().parents[1] / "docs" / "P0_P1_MACHINE_LOCAL_RECOVERY_CONTROLLER.md").read_text(encoding="utf-8")
        self.assertIn("manager-owned external broker", docs)
        self.assertIn("DPAPI", docs)
        self.assertIn("not delivered", docs)


if __name__ == "__main__":
    unittest.main()
