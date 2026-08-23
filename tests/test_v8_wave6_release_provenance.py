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
    def test_tracked_policy_is_v8_with_prepared_identity_approval(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        policy = load_release_policy(repo / "architecture" / "v8_release_policy.json")
        snapshot = release_policy_snapshot(repo)
        self.assertEqual(policy["generation"], "V8")
        self.assertEqual(policy["release_branch"], "feature/local-ai-hub-v8")
        self.assertEqual(policy["approval"]["identity"], "approved")
        self.assertEqual(policy["version_policy"]["candidate_version"], "8.0.1")
        self.assertIsNone(policy["tag_policy"]["candidate_tag"])
        self.assertTrue(snapshot["identity_approved"])
        self.assertTrue(snapshot["technical_ready"])
        self.assertTrue(snapshot["merge_ready"])
        self.assertFalse(snapshot["release_ready"])
        self.assertIsNone(snapshot["tag_exists"])
        self.assertEqual(snapshot["blockers"], [])

    def test_candidate_dry_run_accepts_matching_unoccupied_v8_0_1_identity_without_writing(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "architecture").mkdir(parents=True)
            shutil.copy2(repo / "architecture" / "v8_release_policy.json", root / "architecture" / "v8_release_policy.json")
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            result = validate_candidate("8.0.1", "v8.0.1", root)
        self.assertTrue(result["tag_available"])
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["writes_performed"])
        self.assertEqual(result["candidate_tag"], "v8.0.1")

    def test_candidate_version_and_tag_must_match(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with self.assertRaises(ReleasePolicyError) as caught:
            validate_candidate("8.0.1", "v8.0.2", repo)
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

    def test_temporary_checkout_branch_does_not_override_exact_commit_binding(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "architecture").mkdir(parents=True)
            policy = load_release_policy(repo / "architecture" / "v8_release_policy.json")
            (root / "architecture" / "v8_release_policy.json").write_text(json.dumps(policy), encoding="utf-8")
            (root / "source.txt").write_text("approved source\n", encoding="utf-8")
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "test"], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-m", "approved source"], check=True, capture_output=True)
            commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            subprocess.run(["git", "-C", str(root), "checkout", "-b", "fix/v8.0.1-stable-product-shell"], check=True, capture_output=True)
            snapshot = release_policy_snapshot(
                root,
                phase="pre_tag",
                expected_commit=commit,
                requested_version="8.0.1",
                requested_tag="v8.0.1",
            )
        self.assertEqual(snapshot["release_branch"], "feature/local-ai-hub-v8")
        self.assertEqual(snapshot["release_commit"], commit)
        self.assertTrue(snapshot["identity_approved"])
        self.assertTrue(snapshot["release_ready"])
        self.assertEqual(snapshot["blockers"], [])

    def _repo_with_policy(self) -> tuple[Path, str]:
        repo = Path(tempfile.mkdtemp(prefix="lah-v8-phase-"))
        (repo / "architecture").mkdir(parents=True)
        source = Path(__file__).resolve().parents[1]
        shutil.copy2(source / "architecture" / "v8_release_policy.json", repo / "architecture" / "v8_release_policy.json")
        (repo / "marker.txt").write_text("approved source\n", encoding="utf-8")
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "approved source"], check=True, capture_output=True)
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        return repo, commit

    def test_integration_ignores_existing_candidate_tag(self) -> None:
        repo, _commit = self._repo_with_policy()
        subprocess.run(["git", "-C", str(repo), "tag", "v8.0.1"], check=True)
        snapshot = release_policy_snapshot(repo)
        self.assertEqual(snapshot["phase"], "integration")
        self.assertTrue(snapshot["technical_ready"])
        self.assertTrue(snapshot["merge_ready"])
        self.assertIsNone(snapshot["tag_exists"])
        self.assertEqual(snapshot["blockers"], [])

    def test_integration_ignores_unrelated_tags_and_history_ahead_of_tag(self) -> None:
        repo, _commit = self._repo_with_policy()
        subprocess.run(["git", "-C", str(repo), "tag", "v8.0.2"], check=True)
        (repo / "later.txt").write_text("later\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "later.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "later"], check=True, capture_output=True)
        snapshot = release_policy_snapshot(repo)
        self.assertTrue(snapshot["merge_ready"])
        self.assertEqual(snapshot["blockers"], [])

    def test_pre_tag_requires_explicit_absent_requested_tag(self) -> None:
        repo, commit = self._repo_with_policy()
        snapshot = release_policy_snapshot(
            repo,
            phase="pre_tag",
            expected_commit=commit,
            requested_version="8.0.1",
            requested_tag="v8.0.1",
        )
        self.assertTrue(snapshot["tag_is_unoccupied"])
        self.assertTrue(snapshot["release_ready"])
        self.assertEqual(snapshot["blockers"], [])

    def test_pre_tag_existing_requested_tag_blocks(self) -> None:
        repo, commit = self._repo_with_policy()
        subprocess.run(["git", "-C", str(repo), "tag", "v8.0.1"], check=True)
        snapshot = release_policy_snapshot(
            repo,
            phase="pre_tag",
            expected_commit=commit,
            requested_version="8.0.1",
            requested_tag="v8.0.1",
        )
        self.assertFalse(snapshot["release_ready"])
        self.assertIn("RELEASE_TAG_ALREADY_EXISTS", snapshot["blockers"])

    def test_wrong_expected_source_commit_is_blocked(self) -> None:
        repo, commit = self._repo_with_policy()
        wrong = "0" * 40 if commit != "0" * 40 else "1" * 40
        snapshot = release_policy_snapshot(
            repo,
            phase="pre_tag",
            expected_commit=wrong,
            requested_version="8.0.1",
            requested_tag="v8.0.1",
        )
        self.assertFalse(snapshot["source_commit_matches"])
        self.assertIn("RELEASE_SOURCE_COMMIT_MISMATCH", snapshot["blockers"])
        self.assertFalse(snapshot["release_ready"])

    def test_post_tag_exact_target_passes(self) -> None:
        repo, commit = self._repo_with_policy()
        subprocess.run(["git", "-C", str(repo), "tag", "v8.0.1", commit], check=True)
        snapshot = release_policy_snapshot(
            repo,
            phase="post_tag",
            expected_commit=commit,
            requested_version="8.0.1",
            requested_tag="v8.0.1",
        )
        self.assertTrue(snapshot["tag_exists"])
        self.assertTrue(snapshot["tag_verified"])
        self.assertTrue(snapshot["tagged_release_ready"])
        self.assertEqual(snapshot["blockers"], [])

    def test_post_tag_missing_and_wrong_target_block(self) -> None:
        repo, commit = self._repo_with_policy()
        missing = release_policy_snapshot(
            repo,
            phase="post_tag",
            expected_commit=commit,
            requested_version="8.0.1",
            requested_tag="v8.0.1",
        )
        self.assertIn("RELEASE_TAG_MISSING", missing["blockers"])
        (repo / "wrong.txt").write_text("wrong\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "wrong.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "wrong target"], check=True, capture_output=True)
        wrong = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(repo), "tag", "v8.0.1", wrong], check=True)
        mismatch = release_policy_snapshot(
            repo,
            phase="post_tag",
            expected_commit=commit,
            requested_version="8.0.1",
            requested_tag="v8.0.1",
        )
        self.assertIn("RELEASE_TAG_TARGET_MISMATCH", mismatch["blockers"])

    def test_policy_allows_candidate_version_without_planned_tag(self) -> None:
        repo, _commit = self._repo_with_policy()
        policy = load_release_policy(repo / "architecture" / "v8_release_policy.json")
        self.assertEqual(policy["version_policy"]["candidate_version"], "8.0.1")
        self.assertIsNone(policy["tag_policy"]["candidate_tag"])

    def test_pre_and_post_require_explicit_identity_and_commit(self) -> None:
        repo, _commit = self._repo_with_policy()
        with self.assertRaises(ReleasePolicyError) as caught:
            release_policy_snapshot(repo, phase="pre_tag")
        self.assertEqual(caught.exception.code, "RELEASE_IDENTITY_EXPLICIT_REQUIRED")


if __name__ == "__main__":
    unittest.main()
