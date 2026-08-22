"""V8 Wave 6 read-only release provenance preparation tests."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.v8_release_provenance import (
    ReleasePolicyError,
    load_release_policy,
    release_policy_snapshot,
    validate_candidate,
)


class V8Wave6ReleaseProvenanceTests(unittest.TestCase):
    def test_tracked_policy_is_v8_but_identity_is_not_user_approved(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        policy = load_release_policy(repo / "architecture" / "v8_release_policy.json")
        snapshot = release_policy_snapshot(repo)
        self.assertEqual(policy["generation"], "V8")
        self.assertEqual(policy["release_branch"], "feature/local-ai-hub-v8")
        self.assertEqual(policy["approval"]["identity"], "required")
        self.assertIsNone(policy["version_policy"]["candidate_version"])
        self.assertIsNone(policy["tag_policy"]["candidate_tag"])
        self.assertFalse(snapshot["identity_approved"])
        self.assertFalse(snapshot["activation_ready"])
        self.assertIn("V8_RELEASE_IDENTITY_APPROVAL_REQUIRED", snapshot["blockers"])

    def test_candidate_dry_run_accepts_matching_unoccupied_v8_identity_without_writing(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "architecture").mkdir(parents=True)
            shutil.copy2(repo / "architecture" / "v8_release_policy.json", root / "architecture" / "v8_release_policy.json")
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            result = validate_candidate("8.0.0", "v8.0.0", root)
        self.assertTrue(result["tag_available"])
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["writes_performed"])
        self.assertEqual(result["candidate_tag"], "v8.0.0")

    def test_candidate_version_and_tag_must_match(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with self.assertRaises(ReleasePolicyError) as caught:
            validate_candidate("8.0.0", "v8.0.1", repo)
        self.assertEqual(caught.exception.code, "RELEASE_IDENTITY_MISMATCH")

    def test_policy_rejects_unreviewed_extra_fields(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        policy = load_release_policy(repo / "architecture" / "v8_release_policy.json")
        policy["automatic_release"] = True
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaises(ReleasePolicyError) as caught:
                load_release_policy(path)
        self.assertEqual(caught.exception.code, "RELEASE_POLICY_ROOT_INVALID")

    def test_historical_v7_evidence_is_explicitly_immutable(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        policy = load_release_policy(repo / "architecture" / "v8_release_policy.json")
        self.assertTrue(policy["activation"]["historical_v7_release_evidence_immutable"])
        self.assertEqual(policy["approval"]["version_change"], "user_approved_only")
        self.assertEqual(policy["approval"]["tag_creation"], "user_approved_only")
        self.assertEqual(policy["approval"]["main_merge"], "user_approved_only")


if __name__ == "__main__":
    unittest.main()
