"""M14A cross-lane static gate contract tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_REF = "origin/feature/local-ai-hub-v4-static-contract-integration"
INTEGRATION_HEAD = "9cbca638c2df766734a6782ace828f5f7ea44f6c"
TEST_BRANCH = "test/local-ai-hub-v4-cross-lane-contracts"
TEST_RELATIVE_PATH = "tests/test_milestone10b_cross_lane_contracts.py"
TIMEOUT_SECONDS = 10

VALIDATOR_SCRIPTS = (
    "scripts/validate_extensions.py",
    "scripts/validate_workflow_packages.py",
    "scripts/validate_asset_intelligence.py",
    "scripts/validate_privacy_diagnostics.py",
    "scripts/validate_creative_recipes.py",
)

TRUTHFUL_STATUSES = {
    "changed",
    "clean",
    "invalid",
    "manual_review",
    "not_required",
    "not_run",
    "operational",
    "partial",
    "planned",
    "unavailable",
    "unchanged",
}

CLIENT_SENTINELS = {
    "mapping": "CROSS_LANE_CLIENT_MAPPING_PAYLOAD",
    "raw_path": "CROSS_LANE_RAW_PATH_PAYLOAD",
    "secret": "CROSS_LANE_SECRET_PAYLOAD",
    "host": "CROSS_LANE_HOST_PAYLOAD",
    "user": "CROSS_LANE_USER_PAYLOAD",
    "environment": "CROSS_LANE_ENVIRONMENT_PAYLOAD",
    "command": "CROSS_LANE_COMMAND_PAYLOAD",
}

FORBIDDEN_KEYS = {"client_mapping", "command", "environment", "host", "mapping", "path", "raw_path", "secret", "user"}
FINGERPRINT_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")


def _validator_command(script: str) -> list[str]:
    return [sys.executable, "-B", script, "--format", "json"]


def _run_validator(script: str) -> tuple[dict[str, Any], str, str]:
    environment = os.environ.copy()
    for key, value in CLIENT_SENTINELS.items():
        environment[f"M14A_{key.upper()}_SENTINEL"] = value
    try:
        result = subprocess.run(
            _validator_command(script),
            cwd=ROOT,
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise AssertionError(f"bounded validator timeout: {script}") from error
    if result.returncode != 0:
        raise AssertionError(f"validator failed: {script}\n{result.stderr}")
    if result.stderr:
        raise AssertionError(f"validator wrote stderr: {script}\n{result.stderr}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"validator did not emit JSON: {script}") from error
    if not isinstance(payload, dict):
        raise AssertionError(f"validator JSON root is not an object: {script}")
    return payload, result.stdout, result.stderr


def _run_all_validators() -> dict[str, tuple[dict[str, Any], str, str]]:
    return {script: _run_validator(script) for script in VALIDATOR_SCRIPTS}


def _git_read_only(arguments: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise AssertionError(f"bounded git metadata timeout: {' '.join(arguments)}") from error
    if result.returncode != 0:
        raise AssertionError(f"git metadata command failed: {' '.join(arguments)}\n{result.stderr}")
    return result.stdout


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _collect_values(value: Any, key: str) -> list[Any]:
    return [node[key] for node in _iter_dicts(value) if key in node]


def _assert_static_execution(test_case: unittest.TestCase, script: str, payload: dict[str, Any]) -> None:
    test_case.assertEqual(payload.get("status"), "partial", script)
    if script == "scripts/validate_extensions.py":
        test_case.assertNotIn("execution", payload, script)
        test_case.assertIs(payload["resource_plan"]["dry_run"], True, script)
    else:
        test_case.assertEqual(payload.get("execution"), "not_run", script)


def _status_paths(status_output: str) -> set[str]:
    paths: set[str] = set()
    for line in status_output.splitlines():
        if len(line) >= 4:
            paths.add(line[3:])
    return paths


class CrossLaneStaticGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.status_before = _git_read_only(["status", "--short", "--untracked-files=all"])
        cls.first_run = _run_all_validators()
        cls.status_after_first = _git_read_only(["status", "--short", "--untracked-files=all"])
        cls.second_run = _run_all_validators()
        cls.status_after_second = _git_read_only(["status", "--short", "--untracked-files=all"])

    def test_server_owned_projection_never_echoes_client_mapping(self) -> None:
        forbidden_values = set(CLIENT_SENTINELS.values()) | {str(ROOT), str(Path.home())}
        for script, (payload, stdout, stderr) in self.first_run.items():
            projection = f"{stdout}\n{stderr}\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
            for value in forbidden_values:
                self.assertNotIn(value, projection, script)
            for node in _iter_dicts(payload):
                for key in FORBIDDEN_KEYS:
                    self.assertNotIn(key, node, f"{script} reflected forbidden key {key}")

    def test_cross_lane_fingerprints_and_statuses_are_deterministic(self) -> None:
        fingerprints_seen = 0
        for script in VALIDATOR_SCRIPTS:
            first_payload = self.first_run[script][0]
            second_payload = self.second_run[script][0]
            self.assertEqual(_canonical_json(first_payload), _canonical_json(second_payload), script)
            _assert_static_execution(self, script, first_payload)
            for node in _iter_dicts(first_payload):
                if "status" in node:
                    self.assertIn(node["status"], TRUTHFUL_STATUSES, script)
                if "execution" in node:
                    self.assertEqual(node["execution"], "not_run", script)
                if "dry_run" in node:
                    self.assertIs(node["dry_run"], True, script)
            first_fingerprints = _collect_values(first_payload, "fingerprint")
            second_fingerprints = _collect_values(second_payload, "fingerprint")
            self.assertEqual(first_fingerprints, second_fingerprints, script)
            for fingerprint in first_fingerprints:
                self.assertIsInstance(fingerprint, str, script)
                self.assertRegex(fingerprint, FINGERPRINT_PATTERN, script)
                fingerprints_seen += 1
        self.assertGreater(fingerprints_seen, 0)

    def test_dry_run_never_writes_or_launches(self) -> None:
        self.assertEqual(self.status_before, self.status_after_first)
        self.assertEqual(self.status_before, self.status_after_second)
        for script in VALIDATOR_SCRIPTS:
            self.assertEqual(_validator_command(script)[0], sys.executable)
            self.assertEqual(_validator_command(script)[1:], ["-B", script, "--format", "json"])
            payload = self.first_run[script][0]
            _assert_static_execution(self, script, payload)
            for node in _iter_dicts(payload):
                if "execution" in node:
                    self.assertEqual(node["execution"], "not_run", script)
                if "dry_run" in node:
                    self.assertIs(node["dry_run"], True, script)

    def test_release_gate_scope_isolated(self) -> None:
        self.assertEqual(_git_read_only(["branch", "--show-current"]).strip(), TEST_BRANCH)
        self.assertEqual(_git_read_only(["rev-parse", INTEGRATION_REF]).strip(), INTEGRATION_HEAD)
        changed_paths = {
            line.strip()
            for line in _git_read_only(["diff", "--name-only", f"{INTEGRATION_REF}...HEAD"]).splitlines()
            if line.strip()
        }
        dirty_paths = _status_paths(self.status_before)
        expected = {TEST_RELATIVE_PATH}
        self.assertTrue(changed_paths <= expected)
        self.assertTrue(dirty_paths <= expected)
        self.assertEqual(changed_paths | dirty_paths, expected)


if __name__ == "__main__":
    unittest.main()
