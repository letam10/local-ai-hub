"""Focused static tests for V7 provenance remediation."""

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


class ReleaseProvenanceRemediationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    @staticmethod
    def oid(char: str = "a", width: int = 40) -> str:
        return char * width

    @staticmethod
    def digest(char: str = "a") -> str:
        return char * 64

    def manifest(self, *, phase: str = "tagged", tag: str = "v7.1.0", width: int = 40) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": verifier.SCHEMA_VERSION,
            "application_name": "Local AI Hub",
            "version": "7.1.0",
            "phase": phase,
            "source_commit": self.oid("a", width),
            "build_commit": self.oid("a", width),
            "source_branch": verifier.RELEASE_BRANCH,
            "intended_tag": tag,
            "tag_commit": self.oid("a", width) if phase == "tagged" else None,
            "build_input_fingerprint": self.digest("b"),
            "build_parameters": {
                "source_date_epoch": verifier.SOURCE_DATE_EPOCH,
                "zip_compression": "deflate",
                "zip_compression_level": 9,
                "zip_entry_timestamp": verifier.ZIP_ENTRY_TIMESTAMP,
                "json_framing": verifier.RAW_JSON_FRAMING,
                "selection_fingerprint": self.digest("c"),
                "installer_selection_fingerprint": self.digest("d"),
                "reproducibility": "not_claimed",
            },
            "artifacts": [{
                "id": "core_zip",
                "filename": "LocalAIHub-Core-Win64-v7.1.0.zip",
                "size_bytes": 1,
                "sha256": self.digest("e"),
                "content_fingerprint": self.digest("f"),
            }],
            "manifest_sha256": self.digest("0"),
        }
        value["manifest_sha256"] = verifier.manifest_self_hash(value)
        return value

    def test_repository_oid_width_is_real_sha1_and_64_is_not_fixture_only(self) -> None:
        schema = json.loads((ROOT / "distribution" / "release_manifest.v2.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(len(schema["properties"]["source_commit"]["oneOf"]), 2)
        self.assertEqual({item["pattern"] for item in schema["properties"]["source_commit"]["oneOf"]}, {"^[a-f0-9]{40}$", "^[a-f0-9]{64}$"})
        self.assertEqual(schema["properties"]["intended_tag"]["const"], "v7.1.0")
        self.assertEqual(verifier.repository_oid_width(ROOT), 40)
        valid = self.manifest()
        self.assertEqual(verifier.validate_release_manifest(valid, oid_width=40), {"valid": True, "codes": []})
        wide = self.manifest(width=64)
        self.assertEqual(verifier.validate_release_manifest(wide, oid_width=64), {"valid": True, "codes": []})
        self.assertIn("OID_WIDTH_MISMATCH", verifier.validate_release_manifest(wide, oid_width=40)["codes"])
        mixed = self.manifest(width=40)
        mixed["tag_commit"] = self.oid("a", 64)
        mixed["manifest_sha256"] = verifier.manifest_self_hash(mixed)
        self.assertIn("OID_WIDTH_MISMATCH", verifier.validate_release_manifest(mixed)["codes"])

    def test_duplicate_unknown_and_unsafe_values_fail_closed(self) -> None:
        with self.assertRaises(verifier.ProvenanceRefusal) as duplicate:
            verifier.strict_json_load(b'{"a":1,"a":2}')
        self.assertEqual(duplicate.exception.code, "DUPLICATE_JSON_KEY")
        invalid = self.manifest()
        invalid["client_mapping"] = {"opaque": "no-echo"}
        self.assertIn("UNKNOWN_OR_MISSING_FIELD", verifier.validate_release_manifest(invalid)["codes"])
        invalid = self.manifest()
        invalid["source_branch"] = "https://evil.invalid/release"
        result = verifier.validate_release_manifest(invalid)
        self.assertNotIn("evil.invalid", json.dumps(result))
        invalid = self.manifest()
        invalid["artifacts"][0]["filename"] = r"C:\Users\secret\artifact.zip"  # type: ignore[index]
        result = verifier.validate_release_manifest(invalid)
        self.assertIn("ARTIFACT_FILENAME_INVALID", result["codes"])
        self.assertNotIn("Users", json.dumps(result))

    def test_raw_detached_framing_requires_canonical_bytes_and_one_lf(self) -> None:
        value = self.manifest()
        canonical = verifier.canonical_json(value) + b"\n"
        path = self.root / "manifest.json"
        path.write_bytes(canonical)
        self.assertEqual(verifier.load_manifest(path, require_canonical=True), value)
        for raw in (
            json.dumps(value, indent=2).encode("utf-8") + b"\n",
            json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n",
            b" " + canonical,
            canonical + b"\n",
            verifier.canonical_json(value),
        ):
            path.write_bytes(raw)
            with self.assertRaises(verifier.ProvenanceRefusal) as refused:
                verifier.load_manifest(path, require_canonical=True)
            self.assertEqual(refused.exception.code, "NON_CANONICAL_FRAMING")
        self.assertEqual(verifier.manifest_self_hash(value), value["manifest_sha256"])

    def test_historical_phase_is_separate_and_preserves_bytes(self) -> None:
        path = self.root / "release_manifest.json"
        raw = b'{"schema_version":1,"version":"7.1.0","git_commit":"old","git_branch":"feature/v7-operational-closure","tag":"v7.0.0","tag_commit":"old-tag"}'
        path.write_bytes(raw)
        with patch.object(verifier, "_git_text", side_effect=[self.oid("b"), verifier.RELEASE_BRANCH]), patch.object(verifier, "_git_ref_exists", return_value=(True, self.oid("b"))):
            result = verifier.classify_historical_manifest(path, repo_root=self.root)
        self.assertEqual(result["status"], "LEGACY_INCONSISTENT")
        self.assertIn("VERSION_TAG_MISMATCH", result["codes"])
        self.assertIn("SOURCE_COMMIT_MISMATCH", result["codes"])
        self.assertEqual(path.read_bytes(), raw)

    def test_pre_tag_allows_unoccupied_explicit_tag_with_null_commit(self) -> None:
        tag = "v7.1.0"
        value = self.manifest(phase="pre_tag", tag=tag)
        self.assertEqual(verifier.validate_release_manifest(value, oid_width=40), {"valid": True, "codes": []})
        for future_tag in ("v7.1.1", "v7.2.0"):
            future = self.manifest(phase="pre_tag", tag=future_tag)
            self.assertIn("INTENDED_TAG_VERSION_MISMATCH", verifier.validate_release_manifest(future, oid_width=40)["codes"])
            refused = verifier.verify_pre_tag_manifest(future, ROOT, intended_tag=future_tag)
            self.assertIn("INTENDED_TAG_VERSION_MISMATCH", refused["codes"])
        git_values = [self.oid("a"), verifier.RELEASE_BRANCH, "", "", "", "", ""]
        with patch.object(verifier, "repository_oid_width", return_value=40), patch.object(verifier, "_git_text", side_effect=git_values), patch.object(verifier, "_git_ref_exists", return_value=(False, None)):
            result = verifier.source_identity_gate(self.root, intended_tag=tag, phase="pre_tag")
        self.assertTrue(result["ok"])
        occupied = [self.oid("a"), verifier.RELEASE_BRANCH, "", "", "", "", ""]
        with patch.object(verifier, "repository_oid_width", return_value=40), patch.object(verifier, "_git_text", side_effect=occupied), patch.object(verifier, "_git_ref_exists", return_value=(True, self.oid("a"))):
            refused = verifier.source_identity_gate(self.root, intended_tag=tag, phase="pre_tag")
        self.assertIn("TAG_OCCUPIED", refused["codes"])
        tagged = self.manifest(phase="tagged")
        with patch.object(verifier, "repository_oid_width", return_value=40), patch.object(verifier, "_git_text", side_effect=git_values), patch.object(verifier, "_git_ref_exists", return_value=(True, self.oid("b"))):
            mismatch = verifier.source_identity_gate(self.root, intended_tag="v7.1.0", phase="tagged")
        self.assertIn("TAG_SOURCE_MISMATCH", mismatch["codes"])
        self.assertIsNone(value["tag_commit"])
        self.assertEqual(tagged["tag_commit"], self.oid("a"))

    def test_manifest_verifiers_reject_self_hashed_stale_source_and_selection_fields(self) -> None:
        tag = "v7.1.0"
        current, current_codes = verifier.recompute_current_binding(ROOT, intended_tag=tag)
        self.assertIn("SOURCE_BRANCH_MISMATCH", current_codes)
        pre_tag = self.manifest(phase="pre_tag", tag=tag, width=current["oid_width"])
        pre_tag.update({
            "source_commit": current["source_commit"],
            "build_commit": current["build_commit"],
            "source_branch": current["source_branch"],
            "build_input_fingerprint": current["build_input_fingerprint"],
            "build_parameters": current["build_parameters"],
            "tag_commit": None,
        })
        pre_tag["manifest_sha256"] = verifier.manifest_self_hash(pre_tag)
        with patch.object(verifier, "RELEASE_BRANCH", current["source_branch"]):
            baseline_codes = verifier.verify_pre_tag_manifest(pre_tag, ROOT, intended_tag=tag)["codes"]
            self.assertTrue(baseline_codes)
            stale_source = dict(pre_tag)
            stale_source["source_commit"] = self.oid("b", current["oid_width"])
            stale_source["build_commit"] = stale_source["source_commit"]
            stale_source["manifest_sha256"] = verifier.manifest_self_hash(stale_source)
            self.assertIn("SOURCE_COMMIT_MISMATCH", verifier.verify_pre_tag_manifest(stale_source, ROOT, intended_tag=tag)["codes"])
            stale_input = dict(pre_tag)
            stale_input["build_input_fingerprint"] = self.digest("9")
            stale_input["manifest_sha256"] = verifier.manifest_self_hash(stale_input)
            self.assertIn("BUILD_INPUT_FINGERPRINT_MISMATCH", verifier.verify_pre_tag_manifest(stale_input, ROOT, intended_tag=tag)["codes"])
            stale_selection = dict(pre_tag)
            stale_selection["build_parameters"] = dict(pre_tag["build_parameters"])
            stale_selection["build_parameters"]["selection_fingerprint"] = self.digest("8")  # type: ignore[index]
            stale_selection["manifest_sha256"] = verifier.manifest_self_hash(stale_selection)
            self.assertIn("SELECTION_FINGERPRINT_MISMATCH", verifier.verify_pre_tag_manifest(stale_selection, ROOT, intended_tag=tag)["codes"])

            stale_installer = dict(pre_tag)
            stale_installer["build_parameters"] = dict(pre_tag["build_parameters"])
            stale_installer["build_parameters"]["installer_selection_fingerprint"] = self.digest("7")  # type: ignore[index]
            stale_installer["manifest_sha256"] = verifier.manifest_self_hash(stale_installer)
            stale_path = self.root / "stale-installer.json"
            stale_path.write_bytes(verifier.canonical_json(stale_installer) + b"\n")
            self.assertIn("INSTALLER_SELECTION_MISMATCH", verifier.verify_manifest_file(stale_path, repo_root=ROOT)["codes"])

    def test_checked_in_installer_spec_passes_real_audit(self) -> None:
        names = verifier.audit_installer_selection(ROOT, ROOT / "distribution" / "installer.iss")
        self.assertEqual(names, verifier.release_file_names(ROOT))

    def test_core_build_gate_refuses_before_output_or_staging(self) -> None:
        output = self.root / "dist" / "core.zip"
        manifest = self.root / "core.manifest.json"
        manifest.write_text('{"schema_version":1,"asset_name":"Core","target_size_mib":{"minimum":0,"maximum":1}}', encoding="utf-8")
        with patch.object(core, "ROOT", self.root), patch.object(core, "MANIFEST_PATH", manifest), patch.object(core, "require_source_identity", side_effect=verifier.ProvenanceRefusal("SOURCE_BRANCH_MISMATCH")), patch.object(sys, "argv", ["build_core_release.py", "--build", "--manifest", str(manifest), "--output", str(output)]):
            self.assertEqual(core.main(), 1)
        self.assertFalse(output.exists())
        self.assertFalse(output.parent.exists())
        self.assertFalse(output.with_name(output.name + ".part").exists())

    def _fixture_installer(self) -> tuple[Path, list[str]]:
        for root in verifier.RELEASE_ROOTS:
            (self.root / root).mkdir(parents=True, exist_ok=True)
        for name in verifier.RELEASE_ROOT_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode("utf-8"))
        config = self.root / "Config"
        config.mkdir(exist_ok=True)
        (config / "sample.example.json").write_text("{}", encoding="utf-8")
        spec = self.root / "installer.iss"
        lines: list[str] = []
        for rule in verifier.INSTALLER_SOURCE_RULES:
            if "\\*" in rule and "Config" not in rule:
                excludes = "__pycache__;*.pyc"
                if "distribution" in rule:
                    excludes += ";release_manifest.json"
                lines.append(rule + "; Excludes: \"" + excludes + "\"")
            else:
                lines.append(rule + "; DestDir: \"{app}\"")
        spec.write_text("\n".join(lines), encoding="utf-8")
        tracked = [*verifier.RELEASE_ROOT_FILES, "Config/sample.example.json"]
        for root in verifier.RELEASE_ROOTS:
            tracked.extend(path.relative_to(self.root).as_posix() for path in (self.root / root).rglob("*") if path.is_file())
        return spec, sorted(set(tracked))

    def test_real_parity_audit_rejects_untracked_generated_file(self) -> None:
        spec, tracked = self._fixture_installer()
        with patch.object(verifier, "tracked_files", return_value=tracked):
            self.assertEqual(verifier.audit_installer_selection(self.root, spec), verifier.release_file_names(self.root))
        generated = self.root / "src" / "generated-by-wildcard.txt"
        generated.write_text("untracked", encoding="utf-8")
        with patch.object(verifier, "tracked_files", return_value=tracked):
            with self.assertRaises(verifier.ProvenanceRefusal) as refused:
                verifier.audit_installer_selection(self.root, spec)
        self.assertEqual(refused.exception.code, "UNTRACKED_INSTALLER_SOURCE")
        self.assertNotIn("generated-by-wildcard", str(refused.exception))

    def test_normalized_zip_is_mtime_and_order_independent_and_overwrite_refuses(self) -> None:
        left = self.root / "left.txt"
        right = self.root / "right.txt"
        left.write_bytes(b"left")
        right.write_bytes(b"right")
        policy = {"target_size_mib": {"minimum": 0, "maximum": 1}}
        first = self.root / "first.zip"
        second = self.root / "second.zip"
        core.archive_payload([(right, "right.txt"), (left, "left.txt")], first, policy, False)
        os.utime(left, (1, 1))
        os.utime(right, (2, 2))
        core.archive_payload([(left, "left.txt"), (right, "right.txt")], second, policy, False)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with self.assertRaises(ValueError) as overwrite:
            core.archive_payload([(left, "left.txt")], first, policy, False)
        self.assertEqual(str(overwrite.exception), "ARTIFACT_OVERWRITE_FORBIDDEN")

    def test_artifact_rehash_and_content_fingerprint_fail_closed(self) -> None:
        path = self.root / "LocalAIHub-Setup-Win64-v7.1.0.exe"
        path.write_bytes(b"setup")
        sha = hashlib.sha256(b"setup").hexdigest()
        manifest = self.manifest()
        manifest["artifacts"][0].update({"id": "setup_exe", "filename": path.name, "size_bytes": 5, "sha256": sha, "content_fingerprint": sha})  # type: ignore[index]
        self.assertEqual(verifier.verify_artifacts(manifest, self.root), [])
        path.write_bytes(b"tampered")
        self.assertEqual(verifier.verify_artifacts(manifest, self.root), ["ARTIFACT_SIZE_MISMATCH"])

    def test_build_sidecar_is_canonical_and_legacy_manifest_is_not_written(self) -> None:
        fake = self.root
        (fake / "src").mkdir()
        (fake / "src" / "fixture.txt").write_bytes(b"fixture")
        (fake / "distribution").mkdir()
        legacy = fake / "distribution" / "release_manifest.json"
        legacy.write_bytes(b"legacy")
        spec = fake / "distribution" / "installer.iss"
        spec.write_text("\n".join(installer.INSTALLER_SOURCE_RULES if hasattr(installer, "INSTALLER_SOURCE_RULES") else verifier.INSTALLER_SOURCE_RULES), encoding="utf-8")
        oid = self.oid("a")
        with patch.object(installer, "ROOT", fake), patch.object(installer, "DIST_DIR", fake / "dist"), patch.object(installer, "ISS_PATH", spec), patch.object(installer, "require_source_identity", return_value={"ok": True, "oid_width": 40}), patch.object(installer, "release_file_names", return_value=["src/fixture.txt"]), patch.object(installer, "audit_installer_selection", return_value=["src/fixture.txt"]), patch.object(installer, "_current_head", return_value=oid):
            manifest = installer.build_release_package(output_zip=fake / "dist" / "LocalAIHub-Core-Win64-v7.1.0.zip", compile_exe=False, staging_dir=fake / "dist" / "staging")
        sidecar = fake / "dist" / "staging" / "release_manifest.v2.json"
        self.assertEqual(verifier.load_manifest(sidecar, require_canonical=True), manifest)
        self.assertEqual(legacy.read_bytes(), b"legacy")
        self.assertEqual(manifest["manifest_sha256"], verifier.manifest_self_hash(manifest))

    def test_direct_verifier_cli_is_finite_and_redacted(self) -> None:
        result = subprocess.run([sys.executable, "-B", "scripts/verify_release_provenance.py", "--historical"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["execution"], "not_run")
        self.assertIn(payload["status"], {"LEGACY_INCONSISTENT", "HISTORICAL_METADATA"})
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
