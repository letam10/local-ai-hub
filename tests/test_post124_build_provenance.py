from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import assemble_product_update, build_main_update, build_stable_launcher
from src.shared import source_provenance


REPO = Path(__file__).resolve().parents[1]
PR_HEAD = "a" * 40
PR_MERGE = "b" * 40
OTHER_SOURCE = "c" * 40
WORKFLOW_RUN = 123


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical(value))


def _write_app_artifact(root: Path, *, source: str = PR_HEAD, workflow_run: int = WORKFLOW_RUN) -> None:
    root.mkdir(parents=True, exist_ok=True)
    archive_path = root / "LocalAIHub-main-update.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        info = zipfile.ZipInfo("app/README.md", date_time=(1980, 1, 1, 0, 0, 0))
        archive.writestr(info, b"app")
    archive_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "local-ai-hub-main-update.v1", "product_id": "LocalAIHub",
        "product_version": "8.0.1", "channel": "main", "source_commit": source,
        "payload_id": f"main-{source[:12]}", "runtime_strategy": "reuse-current",
        "archive": "LocalAIHub-main-update.zip", "archive_sha256": archive_sha, "file_count": 1,
    }
    manifest_path = root / "update-manifest.json"
    _write_json(manifest_path, manifest)
    contract_path = root / "update-contract.json"
    _write_json(contract_path, {
        "schema_version": "local-ai-hub-update-contract.v1",
        "update_kind": "APP_ONLY",
        "source_commit": source,
    })
    _write_json(root / "source-attestation.json", {
        "schema_version": "local-ai-hub-source-attestation.v1", "artifact": "APP_ONLY",
        "source_commit": source, "payload_id": f"main-{source[:12]}",
        "workflow_run_id": workflow_run, "archive": "LocalAIHub-main-update.zip",
        "archive_sha256": archive_sha,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        "file_count": 1, "total_bytes": 3,
    })


def _write_launcher_artifact(root: Path, *, source: str = PR_HEAD, workflow_run: int = WORKFLOW_RUN) -> None:
    root.mkdir(parents=True, exist_ok=True)
    archive_path = root / "LocalAIHub-stable-launcher-onedir.zip"
    files = {"LocalAIHub.exe": b"exe", "_internal/support.dll": b"support"}
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(f"LocalAIHub/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            archive.writestr(info, data)
    rows = [
        {"name": name, "size": len(files[name]), "sha256": hashlib.sha256(files[name]).hexdigest()}
        for name in sorted(files)
    ]
    _write_json(root / "launcher-build.json", {
        "schema_version": "local-ai-hub-stable-launcher-build.v1", "source_commit": source,
        "workflow_run_id": workflow_run, "format": "onedir",
        "executable_sha256": next(row["sha256"] for row in rows if row["name"] == "LocalAIHub.exe"),
        "files": rows, "file_count": len(rows), "total_bytes": sum(row["size"] for row in rows),
        "tree_manifest_sha256": hashlib.sha256(_canonical(rows)).hexdigest(),
    })


class Post124BuildProvenanceTests(unittest.TestCase):
    def test_pr_head_is_explicitly_selected_over_pull_request_merge_sha(self) -> None:
        with (
            patch.object(source_provenance, "git_head", return_value=PR_HEAD),
            patch.dict(os.environ, {"GITHUB_SHA": PR_MERGE}, clear=False),
        ):
            self.assertEqual(source_provenance.resolve_source_commit(REPO, PR_HEAD), PR_HEAD)
            with self.assertRaisesRegex(source_provenance.SourceIdentityError, "SOURCE_COMMIT_MISMATCH"):
                source_provenance.resolve_source_commit(REPO, PR_MERGE)

    def test_checkout_and_expected_source_mismatch_fails_closed_before_builder_work(self) -> None:
        with patch.object(build_main_update, "resolve_source_commit", side_effect=RuntimeError("SOURCE_COMMIT_MISMATCH")):
            with self.assertRaisesRegex(RuntimeError, "SOURCE_COMMIT_MISMATCH"):
                build_main_update.build(REPO / "Temp" / "post124-provenance-no-build", expected_source_sha=OTHER_SOURCE)
        with patch.object(build_stable_launcher, "resolve_source_commit", side_effect=ValueError("SOURCE_COMMIT_MISMATCH")):
            with tempfile.TemporaryDirectory(dir=REPO / "Temp", prefix="post124-launcher-provenance-") as temporary:
                with self.assertRaisesRegex(ValueError, "SOURCE_COMMIT_MISMATCH"):
                    build_stable_launcher.build(Path(temporary), expected_source_sha=OTHER_SOURCE)

    def test_assembler_checks_expected_source_run_and_manifest_archive_integrity(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO / "Temp", prefix="post124-assembler-provenance-") as temporary:
            root = Path(temporary)
            app = root / "app"
            launcher = root / "launcher"
            _write_app_artifact(app)
            _write_launcher_artifact(launcher)
            result = assemble_product_update.assemble(
                app, launcher, root / "assembled", workflow_run_id=WORKFLOW_RUN, expected_source_sha=PR_HEAD,
            )
            self.assertEqual(result["source_commit"], PR_HEAD)
            self.assertEqual(result["workflow_run_id"], WORKFLOW_RUN)
            sums = (root / "assembled" / "SHA256SUMS.txt").read_text(encoding="ascii")
            self.assertIn(result["archive_sha256"], sums)

            cases = (
                ("app source", lambda: _write_app_artifact(root / "bad-app-source", source=OTHER_SOURCE), "bad-app-source", launcher, "app_manifest_mismatch"),
                ("app run", lambda: _write_app_artifact(root / "bad-app-run", workflow_run=WORKFLOW_RUN + 1), "bad-app-run", launcher, "app_source_attestation_mismatch"),
                ("launcher source", lambda: _write_launcher_artifact(root / "bad-launcher-source", source=OTHER_SOURCE), app, "bad-launcher-source", "launcher_source_mismatch"),
                ("launcher run", lambda: _write_launcher_artifact(root / "bad-launcher-run", workflow_run=WORKFLOW_RUN + 1), app, "bad-launcher-run", "launcher_run_mismatch"),
            )
            for label, create, app_name, launcher_name, error in cases:
                with self.subTest(case=label):
                    create()
                    bad_app = root / app_name if isinstance(app_name, str) else app_name
                    bad_launcher = root / launcher_name if isinstance(launcher_name, str) else launcher_name
                    with self.assertRaisesRegex(RuntimeError, error):
                        assemble_product_update.assemble(
                            bad_app, bad_launcher, root / f"assembled-{label.replace(' ', '-')}",
                            workflow_run_id=WORKFLOW_RUN, expected_source_sha=PR_HEAD,
                        )

            _write_app_artifact(app)
            manifest_path = app / "update-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["archive_sha256"] = "0" * 64
            _write_json(manifest_path, manifest)
            with self.assertRaisesRegex(RuntimeError, "app_manifest_mismatch"):
                assemble_product_update.assemble(
                    app, launcher, root / "assembled-archive-mismatch",
                    workflow_run_id=WORKFLOW_RUN, expected_source_sha=PR_HEAD,
                )

    def test_push_main_policy_remains_separate_from_pr_candidate_policy(self) -> None:
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("github.event.pull_request.head.sha", workflow)
        self.assertIn("validate-artifact-source:", workflow)
        self.assertIn("--expected-source-sha", workflow)
        self.assertIn("name: local-ai-hub-main-update", workflow)
        self.assertIn("name: local-ai-hub-composite-product-update", workflow)
        self.assertIn("name: pr-${{ github.run_id }}-local-ai-hub-composite-candidate", workflow)
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/main'", workflow)
        self.assertNotIn("pull_request_target", workflow)


if __name__ == "__main__":
    unittest.main()
