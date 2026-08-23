from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.platform.paths import HubPaths
from src.services.component_installer.receipts import CatalogBindingContext, read_receipts
from src.services.operational_closure.update_executor import ComponentUpdateExecutor


class V8RuntimeUpdateCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="v8-runtime-update-")
        root = Path(self.temp.name)
        self.paths = HubPaths(app_root=root / "app", data_root=root / "data")
        self.paths.app_root.mkdir()
        self.paths.data_root.mkdir()
        self.required = ["v8/ffmpeg/ffmpeg.exe", "v8/ffmpeg/ffprobe.exe"]
        self.old = {"ffmpeg.exe": b"old-ffmpeg", "ffprobe.exe": b"old-ffprobe"}
        self.new = {"ffmpeg.exe": b"new-ffmpeg", "ffprobe.exe": b"new-ffprobe"}
        slot = self.paths.runtime_root / "v8" / "ffmpeg"
        slot.mkdir(parents=True)
        for name, payload in self.old.items():
            (slot / name).write_bytes(payload)
        legacy = self.paths.runtime_root / "tools"
        legacy.mkdir(parents=True)
        (legacy / "legacy-marker.txt").write_bytes(b"legacy-preserved")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _archive(self) -> tuple[Path, dict[str, object]]:
        archive = self.paths.temp_root / "candidate.zip"
        archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as handle:
            handle.writestr("ffmpeg-2/bin/ffmpeg.exe", self.new["ffmpeg.exe"])
            handle.writestr("ffmpeg-2/bin/ffprobe.exe", self.new["ffprobe.exe"])
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        candidate: dict[str, object] = {
            "version": "2.0",
            "revision": "runtime-r2",
            "source_identity": "catalog:runtime-r2",
            "source_verification": "verified",
            "integrity": {"verification": "verified", "size_bytes": archive.stat().st_size, "sha256": digest},
            "archive_prefix": "ffmpeg-2",
            "archive_leaves": {self.required[0]: "bin/ffmpeg.exe", self.required[1]: "bin/ffprobe.exe"},
            "estimated_disk_size": 1024 * 1024,
        }
        return archive, candidate

    def _record(self, candidate: dict[str, object]) -> dict[str, object]:
        return {
            "runtime_id": "ffmpeg",
            "revision": "runtime-r1",
            "latest_supported_revision": "runtime-r2",
            "install_strategy": "portable_archive",
            "source_identity": "catalog:runtime-r1",
            "root_class": "runtime_root",
            "required_leaves": self.required,
            "update_candidate": candidate,
        }

    def _plan(self, record: dict[str, object], candidate: dict[str, object], archive: Path) -> tuple[dict[str, object], CatalogBindingContext]:
        binding = CatalogBindingContext.for_v2(catalog_version="2026.08.22", catalog_fingerprint="a" * 64, source_identity="catalog:runtime-r1")
        plan: dict[str, object] = {
            "component_id": "ffmpeg",
            "component_type": "runtime",
            "catalog_fingerprint": binding.catalog_fingerprint,
            "installed_revision": "runtime-r1",
            "update_candidate": candidate,
            "_candidate_archive": archive,
            "_catalog_binding": binding,
            "_record": record,
            "_record_revision": record["revision"],
            "_install_strategy": record["install_strategy"],
            "_latest_supported_revision": record["latest_supported_revision"],
            "_candidate_fingerprint": hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        }
        return plan, binding

    def test_update_rollback_and_reupdate_preserve_legacy_slot(self) -> None:
        archive, candidate = self._archive()
        record = self._record(candidate)
        plan, binding = self._plan(record, candidate, archive)
        executor = ComponentUpdateExecutor(paths=self.paths)

        updated = executor.apply(plan, confirmed=True, catalog_binding=binding, current_record=record)
        self.assertEqual(updated["status"], "completed")
        slot = self.paths.runtime_root / "v8" / "ffmpeg"
        self.assertEqual((slot / "ffmpeg.exe").read_bytes(), self.new["ffmpeg.exe"])
        self.assertEqual((self.paths.runtime_root / "tools" / "legacy-marker.txt").read_bytes(), b"legacy-preserved")
        receipt = read_receipts(self.paths.config_root)["records"]["ffmpeg"]
        self.assertEqual(receipt["bundle_revision"], "runtime-r2")
        self.assertTrue(receipt["rollback_candidate"])

        rolled_back = executor.rollback("ffmpeg", catalog_binding=binding, current_record=record, plan=plan)
        self.assertEqual(rolled_back["status"], "completed")
        self.assertEqual((slot / "ffmpeg.exe").read_bytes(), self.old["ffmpeg.exe"])
        self.assertFalse(read_receipts(self.paths.config_root)["records"]["ffmpeg"]["rollback_candidate"])

        reapplied = executor.apply(plan, confirmed=True, catalog_binding=binding, current_record=record)
        self.assertEqual(reapplied["status"], "completed")
        self.assertEqual((slot / "ffprobe.exe").read_bytes(), self.new["ffprobe.exe"])

    def test_archive_identity_mismatch_refuses_without_activation(self) -> None:
        archive, candidate = self._archive()
        candidate["integrity"] = {"verification": "verified", "size_bytes": archive.stat().st_size, "sha256": "0" * 64}
        record = self._record(candidate)
        plan, binding = self._plan(record, candidate, archive)
        result = ComponentUpdateExecutor(paths=self.paths).apply(plan, confirmed=True, catalog_binding=binding, current_record=record)
        self.assertEqual(result["code"], "update_candidate_checksum_mismatch")
        self.assertEqual((self.paths.runtime_root / "v8" / "ffmpeg" / "ffmpeg.exe").read_bytes(), self.old["ffmpeg.exe"])
        self.assertFalse((self.paths.config_root / "component_install_receipts.json").exists())


if __name__ == "__main__":
    unittest.main()
