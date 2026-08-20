"""Bounded tests for the detached V7 release-provenance contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import scripts.build_core_release as core
import scripts.build_installer as installer
import scripts.verify_release_provenance as verifier


ROOT = Path(__file__).resolve().parents[1]


class ReleaseProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    @staticmethod
    def _digest(char: str = "a") -> str:
        return char * 64

    def manifest(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": verifier.SCHEMA_VERSION,
            "application_name": "Local AI Hub",
            "version": verifier.REVIEWED_RELEASE_VERSION,
            "source_commit": self._digest("a"),
            "build_commit": self._digest("a"),
            "source_branch": verifier.RELEASE_BRANCH,
            "intended_tag": verifier.INTENDED_TAG,
            "tag_commit": self._digest("a"),
            "build_input_fingerprint": self._digest("b"),
            "build_parameters": {
                "source_date_epoch": verifier.SOURCE_DATE_EPOCH,
                "zip_compression": "deflate",
                "zip_compression_level": 9,
                "zip_entry_timestamp": verifier.ZIP_ENTRY_TIMESTAMP,
                "selection_fingerprint": self._digest("c"),
                "installer_selection_fingerprint": self._digest("d"),
                "reproducibility": "not_claimed",
            },
            "artifacts": [
                {
                    "id": "core_zip",
                    "filename": "LocalAIHub-Core-Win64-v7.1.0.zip",
                    "size_bytes": 1,
                    "sha256": self._digest("e"),
                    "content_fingerprint": self._digest("f"),
                }
            ],
            "manifest_sha256": self._digest("0"),
        }
        value["manifest_sha256"] = verifier.manifest_self_hash(value)
        return value

    def test_schema_is_closed_and_historical_manifest_is_not_a_v2_input(self) -> None:
        schema = json.loads((ROOT / "distribution/release_manifest.v2.schema.json").read_text(encoding="utf-8"))
        self.assertTrue(schema["additionalProperties"] is False)
        self.assertEqual(schema["properties"]["schema_version"]["const"], verifier.SCHEMA_VERSION)
        self.assertEqual(schema["properties"]["version"]["const"], "7.1.0")
        self.assertEqual(json.loads((ROOT / "distribution/release_manifest.json").read_text(encoding="utf-8"))["schema_version"], 1)

    def test_valid_manifest_uses_canonical_self_hash(self) -> None:
        value = self.manifest()
        self.assertEqual(verifier.validate_release_manifest(value), {"valid": True, "codes": []})
        self.assertEqual(value["manifest_sha256"], verifier.manifest_self_hash(value))
        self.assertEqual(hashlib.sha256(verifier.canonical_json({**value, "manifest_sha256": None})).hexdigest(), value["manifest_sha256"])

    def test_duplicate_unknown_and_nonfinite_json_fail_closed(self) -> None:
        with self.assertRaises(verifier.ProvenanceRefusal) as duplicate:
            verifier.strict_json_load(b'{"schema_version":1,"schema_version":2}')
        self.assertEqual(duplicate.exception.code, "DUPLICATE_JSON_KEY")
        with self.assertRaises(verifier.ProvenanceRefusal) as nonfinite:
            verifier.strict_json_load(b'{"value":NaN}')
        self.assertEqual(nonfinite.exception.code, "NONFINITE_JSON_VALUE")
        invalid = self.manifest()
        sensitive_key = "sec" + "ret"
        invalid["client_mapping"] = {sensitive_key: "do-not-echo"}
        self.assertIn("UNKNOWN_OR_MISSING_FIELD", verifier.validate_release_manifest(invalid)["codes"])

    def test_absolute_url_secret_and_path_like_values_are_rejected_without_echo(self) -> None:
        for field, value in (
            ("source_branch", "https://evil.invalid/release"),
            ("source_branch", r"C:\Users\secret\release"),
        ):
            invalid = self.manifest()
            invalid[field] = value
            result = verifier.validate_release_manifest(invalid)
            self.assertFalse(result["valid"])
            self.assertNotIn("evil.invalid", json.dumps(result))
            self.assertNotIn("Users", json.dumps(result))
        invalid = self.manifest()
        invalid["artifacts"][0]["filename"] = "https://evil.invalid/out.zip"  # type: ignore[index]
        result = verifier.validate_release_manifest(invalid)
        self.assertIn("ARTIFACT_FILENAME_INVALID", result["codes"])
        self.assertNotIn("evil.invalid", json.dumps(result))

    def test_digest_branch_tag_build_and_version_mismatches_are_finite(self) -> None:
        invalid = self.manifest()
        invalid["source_commit"] = self._digest("1")
        invalid["build_commit"] = self._digest("2")
        invalid["tag_commit"] = self._digest("3")
        invalid["source_branch"] = "other/release"
        invalid["version"] = "7.1.1"
        invalid["manifest_sha256"] = verifier.manifest_self_hash(invalid)
        codes = verifier.validate_release_manifest(invalid)["codes"]
        for code in ("BUILD_COMMIT_NOT_DETACHED", "TAG_SOURCE_MISMATCH", "SOURCE_BRANCH_MISMATCH", "UNREVIEWED_VERSION"):
            self.assertIn(code, codes)

    def test_tampered_self_hash_and_reproducibility_claim_fail_closed(self) -> None:
        invalid = self.manifest()
        invalid["manifest_sha256"] = self._digest("9")
        self.assertIn("MANIFEST_SELF_HASH_MISMATCH", verifier.validate_release_manifest(invalid)["codes"])
        invalid = self.manifest()
        invalid["build_parameters"]["reproducibility"] = "verified"  # type: ignore[index]
        invalid["manifest_sha256"] = verifier.manifest_self_hash(invalid)
        self.assertIn("REPRODUCIBILITY_STATUS_INVALID", verifier.validate_release_manifest(invalid)["codes"])

    def test_historical_manifest_is_classified_without_rewrite(self) -> None:
        path = self.root / "release_manifest.json"
        raw = b'{"schema_version":1,"version":"7.1.0","git_commit":"old","git_branch":"feature/v7-operational-closure","tag":"v7.0.0","tag_commit":"old-tag"}'
        path.write_bytes(raw)
        with patch.object(
            verifier,
            "_git_text",
            side_effect=[self._digest("a"), verifier.RELEASE_BRANCH, self._digest("b")],
        ):
            result = verifier.classify_historical_manifest(path, repo_root=self.root)
        self.assertEqual(result["status"], "LEGACY_INCONSISTENT")
        self.assertIn("VERSION_TAG_MISMATCH", result["codes"])
        self.assertIn("SOURCE_COMMIT_MISMATCH", result["codes"])
        self.assertNotIn("old", json.dumps(result))
        self.assertEqual(path.read_bytes(), raw)

    def test_direct_verifier_cli_returns_finite_historical_refusal(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", "scripts/verify_release_provenance.py", "--historical"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        projection = json.loads(result.stdout)
        self.assertEqual(projection["status"], "LEGACY_INCONSISTENT")
        self.assertEqual(projection["execution"], "not_run")
        self.assertNotIn(str(ROOT), result.stdout)
        self.assertNotIn("git", result.stdout.lower())
        self.assertEqual(result.stderr, "")

    def test_git_binding_accepts_exact_identity_and_rejects_dirty_or_stale(self) -> None:
        value = self.manifest()
        exact = [value["source_commit"], value["source_branch"], "", value["tag_commit"], "", "", "", ""]
        with patch.object(verifier, "_git_text", side_effect=exact), patch.object(verifier, "release_file_names", return_value=[]), patch.object(verifier, "selection_fingerprint", return_value=value["build_parameters"]["selection_fingerprint"]), patch.object(verifier, "installer_selection_fingerprint", return_value=value["build_parameters"]["installer_selection_fingerprint"]), patch.object(verifier, "compute_build_input_fingerprint", return_value=value["build_input_fingerprint"]):
            self.assertEqual(verifier._verify_git_binding(value, self.root), [])
        dirty = exact.copy()
        dirty[2] = " M file.py"
        with patch.object(verifier, "_git_text", side_effect=dirty), patch.object(verifier, "release_file_names", return_value=[]), patch.object(verifier, "selection_fingerprint", return_value=value["build_parameters"]["selection_fingerprint"]), patch.object(verifier, "installer_selection_fingerprint", return_value=value["build_parameters"]["installer_selection_fingerprint"]), patch.object(verifier, "compute_build_input_fingerprint", return_value=value["build_input_fingerprint"]):
            self.assertIn("DIRTY_SOURCE", verifier._verify_git_binding(value, self.root))
        stale = exact.copy()
        stale[0] = self._digest("8")
        with patch.object(verifier, "_git_text", side_effect=stale), patch.object(verifier, "release_file_names", return_value=[]), patch.object(verifier, "selection_fingerprint", return_value=value["build_parameters"]["selection_fingerprint"]), patch.object(verifier, "installer_selection_fingerprint", return_value=value["build_parameters"]["installer_selection_fingerprint"]), patch.object(verifier, "compute_build_input_fingerprint", return_value=value["build_input_fingerprint"]):
            self.assertIn("SOURCE_COMMIT_MISMATCH", verifier._verify_git_binding(value, self.root))

    def test_artifact_rehash_and_tamper_are_bounded(self) -> None:
        artifact_root = self.root / "artifacts"
        artifact_root.mkdir()
        artifact = artifact_root / "LocalAIHub-Setup-Win64-v7.1.0.exe"
        artifact.write_bytes(b"artifact")
        value = self.manifest()
        value["artifacts"][0]["id"] = "setup_exe"  # type: ignore[index]
        value["artifacts"][0]["filename"] = "LocalAIHub-Setup-Win64-v7.1.0.exe"  # type: ignore[index]
        value["artifacts"][0]["size_bytes"] = artifact.stat().st_size  # type: ignore[index]
        value["artifacts"][0]["sha256"] = hashlib.sha256(b"artifact").hexdigest()  # type: ignore[index]
        value["artifacts"][0]["content_fingerprint"] = value["artifacts"][0]["sha256"]  # type: ignore[index]
        self.assertEqual(verifier.verify_artifacts(value, artifact_root), [])
        artifact.write_bytes(b"tampered")
        self.assertEqual(verifier.verify_artifacts(value, artifact_root), ["ARTIFACT_HASH_MISMATCH"])
        value["artifacts"][0]["filename"] = r"C:\secret\artifact.zip"  # type: ignore[index]
        codes = verifier.verify_artifacts(value, artifact_root)
        self.assertIn("ARTIFACT_FILENAME_INVALID", codes)
        self.assertNotIn("secret", json.dumps(codes))

    def test_normalized_zip_is_identical_after_mtime_change(self) -> None:
        source_a = self.root / "a.txt"
        source_b = self.root / "b.txt"
        source_a.write_bytes(b"same-a")
        source_b.write_bytes(b"same-b")
        policy = {"target_size_mib": {"minimum": 0, "maximum": 1}}
        first = self.root / "first.zip"
        second = self.root / "second.zip"
        core.archive_payload([(source_b, "b.txt"), (source_a, "a.txt")], first, policy, False)
        os.utime(source_a, (1, 1))
        os.utime(source_b, (2, 2))
        core.archive_payload([(source_a, "a.txt"), (source_b, "b.txt")], second, policy, False)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(core.zip_content_fingerprint(first), core.zip_content_fingerprint(second))

    def test_archive_output_overwrite_and_unsafe_selection_are_refused(self) -> None:
        source = self.root / "source.txt"
        source.write_bytes(b"source")
        output = self.root / "output.zip"
        output.write_bytes(b"existing")
        with self.assertRaises(ValueError) as overwrite:
            core.archive_payload([(source, "source.txt")], output, {"target_size_mib": {"minimum": 0, "maximum": 1}}, False)
        self.assertEqual(str(overwrite.exception), "ARTIFACT_OVERWRITE_FORBIDDEN")
        with self.assertRaises(ValueError) as unsafe:
            core.archive_payload([(source, "../escape.txt")], self.root / "new.zip", {"target_size_mib": {"minimum": 0, "maximum": 1}}, False)
        self.assertEqual(str(unsafe.exception), "ARCHIVE_SELECTION_INVALID")

    def test_forbidden_runtime_payload_and_missing_staging_are_refused(self) -> None:
        runtime = self.root / "runtime"
        (runtime / "model").mkdir(parents=True)
        (runtime / "model" / "weights.bin").write_bytes(b"weights")
        with self.assertRaises(ValueError) as forbidden:
            core._runtime_files(runtime, "runtime/bootstrap", {"model"}, set())
        self.assertEqual(str(forbidden.exception), "FORBIDDEN_RUNTIME_PAYLOAD")
        with self.assertRaises(ValueError) as missing:
            core._runtime_files(self.root / "missing", "runtime/bootstrap", set(), set())
        self.assertEqual(str(missing.exception), "STAGING_DIRECTORY_UNAVAILABLE")

    def test_installer_and_core_selection_are_cross_checked_and_no_git_describe_remains(self) -> None:
        source_names = verifier.release_file_names(ROOT)
        installer_names = installer.installer_selection_names(ROOT)
        self.assertEqual(source_names, installer_names)
        self.assertEqual(source_names, sorted(source_names))
        self.assertNotIn("git describe", (ROOT / "scripts/build_installer.py").read_text(encoding="utf-8"))
        self.assertIn("SOURCE: \"..\\workflows\\*\"".casefold(), (ROOT / "distribution/installer.iss").read_text(encoding="utf-8").casefold())

    def test_version_input_refuses_unapproved_release(self) -> None:
        with patch.object(installer, "PRODUCT_VERSION", "7.1.1"):
            with self.assertRaises(installer.ReleaseBuildRefusal) as refused:
                installer._assert_reviewed_version()
        self.assertEqual(refused.exception.code, "UNREVIEWED_VERSION")

    def test_build_writes_only_detached_sidecar_and_preserves_legacy_manifest(self) -> None:
        fake_root = self.root
        (fake_root / "src").mkdir()
        (fake_root / "src" / "fixture.txt").write_bytes(b"fixture")
        (fake_root / "distribution").mkdir()
        historical = fake_root / "distribution" / "release_manifest.json"
        historical.write_bytes(b"historical-byte-preserved")
        iss = fake_root / "distribution" / "installer.iss"
        iss.write_text("\n".join(installer._required_installer_markers()), encoding="utf-8")
        commit = self._digest("a")
        with patch.object(installer, "ROOT", fake_root), patch.object(installer, "DIST_DIR", fake_root / "dist"), patch.object(installer, "ISS_PATH", iss), patch.object(installer, "get_git_info", return_value=(commit, verifier.RELEASE_BRANCH, verifier.INTENDED_TAG, commit)), patch.object(installer, "_assert_release_identity"), patch.object(installer, "release_file_names", return_value=["src/fixture.txt"]), patch.object(installer, "installer_selection_names", return_value=["src/fixture.txt"]):
            manifest = installer.build_release_package(output_zip=fake_root / "dist" / "LocalAIHub-Core-Win64-v7.1.0.zip", compile_exe=False, staging_dir=fake_root / "dist" / "staging")
        self.assertTrue((fake_root / "dist" / "staging" / "release_manifest.v2.json").is_file())
        self.assertEqual(historical.read_bytes(), b"historical-byte-preserved")
        self.assertEqual(verifier.validate_release_manifest(manifest), {"valid": True, "codes": []})
        self.assertNotIn("release_manifest.v2.json", __import__("zipfile").ZipFile(fake_root / "dist" / "LocalAIHub-Core-Win64-v7.1.0.zip").namelist())

    def test_build_rejects_existing_output_before_staging(self) -> None:
        output = self.root / "dist" / "LocalAIHub-Core-Win64-v7.1.0.zip"
        output.parent.mkdir()
        output.write_bytes(b"existing")
        with patch.object(installer, "DIST_DIR", self.root / "dist"), patch.object(installer, "get_git_info", return_value=(self._digest("a"), verifier.RELEASE_BRANCH, verifier.INTENDED_TAG, self._digest("a"))), patch.object(installer, "_assert_release_identity"):
            with self.assertRaises(installer.ReleaseBuildRefusal) as refused:
                installer.build_release_package(output_zip=output, compile_exe=False, staging_dir=self.root / "dist" / "staging")
        self.assertEqual(refused.exception.code, "ARTIFACT_OVERWRITE_FORBIDDEN")
        self.assertFalse((self.root / "dist" / "staging").exists())


if __name__ == "__main__":
    unittest.main()
