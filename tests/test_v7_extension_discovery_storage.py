from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

import src.services.extension_platform.config as config_module
import src.services.extension_platform.generator as generator_module
from src.services.extension_platform.config import ExtensionStorageError, load_extension_config
from src.services.extension_platform.discovery import discover_extensions
from src.services.extension_platform.generator import generate_extension_scaffold
from src.shared.schemas.extension_manifest import MANIFEST_SCHEMA_VERSION


def _fixture_root() -> TemporaryDirectory[str]:
    return TemporaryDirectory(dir=Path.cwd().parent)


def _manifest(extension_id: str = "storage-catalog") -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "id": extension_id,
        "version": "0.1.0",
        "display_name": "Storage Catalog",
        "description": "Static metadata only.",
        "author": {"name": "Test author"},
        "license": "MIT",
        "source": "https://example.invalid/extension",
        "capabilities": ["capability_pack", "metadata_catalog", "resource_planning"],
        "compatibility": {"hub": {"min_version": "4.0.0"}, "platforms": ["windows"]},
        "required_components": [],
        "required_models": [],
        "permissions": ["read_extension_metadata", "plan_resources", "render_compatibility_report"],
        "entrypoints": [
            {"kind": "capability_pack", "path": "capability-pack.json"},
            {"kind": "documentation", "path": "README.md"},
        ],
        "resource_profile": {
            "cpu": {"class": "light", "threads": 1},
            "gpu": {"required": False, "vendor": "none", "device_class": "none"},
            "vram_gb": 0,
            "ram_gb": 0.25,
            "disk_gb": 0.01,
            "exclusive_resource_groups": [],
        },
        "availability": {
            "status": "planned",
            "reason": "Static fixture requires review.",
            "action": "Review the static fixture.",
        },
    }


def _write_extension(root: Path, manifest: dict | None = None) -> Path:
    value = manifest or _manifest()
    extension_dir = root / "extensions" / value["id"]
    extension_dir.mkdir(parents=True)
    (extension_dir / "extension.json").write_text(json.dumps(value), encoding="utf-8")
    (extension_dir / "capability-pack.json").write_text(
        json.dumps({
            "schema_version": "capability-pack.v1",
            "id": f"{value['id']}-pack",
            "display_name": "Storage pack",
            "description": "Static metadata only.",
            "capabilities": ["capability_pack", "metadata_catalog", "resource_planning"],
            "model_card_ids": [],
            "runtime_card_ids": [],
        }),
        encoding="utf-8",
    )
    (extension_dir / "README.md").write_text("Static fixture.\n", encoding="utf-8")
    return extension_dir


class ExtensionDiscoveryStorageTests(TestCase):
    def _symlink(self, target: Path, link: Path, *, directory: bool = False) -> None:
        try:
            os.symlink(target, link, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"reparse fixture unavailable: {type(exc).__name__}")

    def test_strict_config_parser_rejects_duplicate_nonfinite_invalid_utf8_and_unhashable_values(self) -> None:
        payloads = [
            b'{"schema_version":"extensions-config.v1","schema_version":"extensions-config.v1"}',
            b'{"schema_version":"extensions-config.v1","hub_version":NaN}',
            b"\xff\xfe\xfd",
            json.dumps({"schema_version": "extensions-config.v1", "hub_version": "4.0.0", "platform": "windows", "enabled_extensions": [{}]}).encode("utf-8"),
        ]
        for payload in payloads:
            with self.subTest(payload=payload[:24]):
                with _fixture_root() as temporary:
                    root = Path(temporary)
                    (root / "Config").mkdir()
                    (root / "Config" / "extensions.local.json").write_bytes(payload)
                    result = load_extension_config(root)
                    self.assertEqual(result["source"], "defaults")
                    self.assertNotIn("extensions.local.json", json.dumps(result))

    def test_config_reparse_root_is_unavailable_without_reading_outside(self) -> None:
        with _fixture_root() as temporary, _fixture_root() as outside_temporary:
            root = Path(temporary)
            outside = Path(outside_temporary)
            (outside / "extensions.local.json").write_text('{"marker":"outside"}', encoding="utf-8")
            self._symlink(outside, root / "Config", directory=True)
            result = load_extension_config(root)
            self.assertEqual(result["source"], "defaults")
            self.assertNotIn("outside", json.dumps(result))

    def test_duplicate_and_nonfinite_manifest_fail_closed_without_echo(self) -> None:
        with _fixture_root() as temporary:
            root = Path(temporary)
            extension_dir = root / "extensions" / "storage-catalog"
            extension_dir.mkdir(parents=True)
            (extension_dir / "extension.json").write_bytes(
                b'{"schema_version":"extension-manifest.v1","id":"storage-catalog","id":"D:\\\\secret-marker"}'
            )
            result = discover_extensions(root)
            record = result["extensions"][0]
            self.assertEqual(record["status"], "unavailable")
            self.assertIn("invalid_manifest", {item["code"] for item in result["issues"]})
            self.assertNotIn("secret-marker", json.dumps(result))

    def test_schema_valid_but_hostile_text_is_redacted_from_static_projection(self) -> None:
        with _fixture_root() as temporary:
            root = Path(temporary)
            value = _manifest()
            value["display_name"] = r"D:\private\extension-marker"
            value["description"] = "token extension-marker"
            extension_dir = _write_extension(root, value)
            pack = json.loads((extension_dir / "capability-pack.json").read_text(encoding="utf-8"))
            pack["description"] = "https://hostile.invalid/extension-marker"
            (extension_dir / "capability-pack.json").write_text(json.dumps(pack), encoding="utf-8")
            result = discover_extensions(root)
            serialized = json.dumps(result)
            self.assertNotIn("extension-marker", serialized)
            self.assertEqual(result["extensions"][0]["display_name"], "Managed extension")

    def test_managed_root_reparse_is_refused_with_fixed_issue(self) -> None:
        with _fixture_root() as temporary, _fixture_root() as outside_temporary:
            root = Path(temporary)
            outside = Path(outside_temporary)
            _write_extension(outside)
            root.mkdir(exist_ok=True)
            self._symlink(outside / "extensions", root / "extensions", directory=True)
            result = discover_extensions(root)
            self.assertEqual(result["extensions"], [])
            self.assertEqual(result["issues"][0]["code"], "managed_root_reparse")
            self.assertNotIn("storage-catalog", json.dumps(result))

    def test_descriptor_directory_and_reparse_are_unavailable(self) -> None:
        with _fixture_root() as temporary, _fixture_root() as outside_temporary:
            root = Path(temporary)
            extension_dir = _write_extension(root)
            (extension_dir / "README.md").unlink()
            (extension_dir / "README.md").mkdir()
            record = discover_extensions(root)["extensions"][0]
            self.assertEqual(record["status"], "unavailable")
            self.assertEqual(record["descriptors"]["documentation"], 0)

            outside = Path(outside_temporary) / "README.md"
            outside.write_text("outside marker", encoding="utf-8")
            (extension_dir / "README.md").rmdir()
            self._symlink(outside, extension_dir / "README.md")
            result = discover_extensions(root)
            self.assertEqual(result["extensions"][0]["status"], "unavailable")
            self.assertNotIn("outside marker", json.dumps(result))

    def test_bounded_managed_directory_refuses_unbounded_entries(self) -> None:
        with _fixture_root() as temporary:
            root = Path(temporary)
            extensions = root / "extensions"
            extensions.mkdir()
            for index in range(257):
                (extensions / f"entry-{index:03d}.json").write_text("{}", encoding="utf-8")
            result = discover_extensions(root)
            self.assertEqual(result["extensions"], [])
            self.assertEqual(result["issues"][0]["code"], "managed_root_oversized")

    def test_same_size_replacement_is_rejected_during_guarded_read(self) -> None:
        with _fixture_root() as temporary:
            root = Path(temporary)
            path = root / "extensions.local.json"
            path.write_bytes(b"123456")
            original = config_module.guard_is_current
            calls = 0

            def replace_after_first_read(guard: config_module.StorageGuard) -> bool:
                nonlocal calls
                calls += 1
                if calls == 2:
                    replacement = path.with_suffix(".replacement")
                    replacement.write_bytes(b"abcdef")
                    os.replace(replacement, path)
                return original(guard)

            with mock.patch.object(config_module, "guard_is_current", side_effect=replace_after_first_read):
                with self.assertRaises(ExtensionStorageError):
                    config_module.guarded_read_bytes(root, path, maximum=64)

    def test_generator_write_failure_has_no_partial_scaffold_or_temp_residue(self) -> None:
        with _fixture_root() as temporary:
            root = Path(temporary)
            original = generator_module._write_staged_file
            calls = 0

            def fail_second(root_path: Path, stage: Path, name: str, contents: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ExtensionStorageError("synthetic_write_failure")
                original(root_path, stage, name, contents)

            with mock.patch.object(generator_module, "_write_staged_file", side_effect=fail_second):
                with self.assertRaisesRegex(ValueError, "extension_scaffold_unavailable"):
                    generate_extension_scaffold("write-failure", root=root)
            self.assertEqual(list((root / "extensions").iterdir()), [])

    def test_generator_reparse_parent_is_refused_without_outside_write(self) -> None:
        with _fixture_root() as temporary, _fixture_root() as outside_temporary:
            root = Path(temporary)
            outside = Path(outside_temporary)
            (outside / "sentinel.txt").write_text("preserve", encoding="utf-8")
            (root / "extensions").parent.mkdir(exist_ok=True)
            self._symlink(outside, root / "extensions", directory=True)
            with self.assertRaises(ValueError):
                generate_extension_scaffold("reparse-parent", root=root)
            self.assertEqual((outside / "sentinel.txt").read_text(encoding="utf-8"), "preserve")

    def test_generator_duplicate_is_no_overwrite_and_result_is_path_free(self) -> None:
        with _fixture_root() as temporary:
            root = Path(temporary)
            first = generate_extension_scaffold("no-overwrite", root=root)
            self.assertEqual(first["status"], "created")
            self.assertNotIn(str(root), json.dumps(first))
            before = {(path.name, path.read_bytes()) for path in (root / "extensions" / "no-overwrite").iterdir()}
            with self.assertRaises(FileExistsError):
                generate_extension_scaffold("no-overwrite", root=root)
            after = {(path.name, path.read_bytes()) for path in (root / "extensions" / "no-overwrite").iterdir()}
            self.assertEqual(after, before)


if __name__ == "__main__":
    import unittest

    unittest.main()
