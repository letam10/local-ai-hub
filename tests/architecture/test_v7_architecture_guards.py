from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from src.modules.registry import discover_manifests
from src.platform.paths import get_paths
from src.services.bootstrap_core import bootstrap_core
from src.services.model_manager import ModelManager
from src.services.runtime_manager import RuntimeManager
from src.services.module_manager import compose_registry
from src.shared.version import PRODUCT_VERSION
from src.shared.schemas.module_manifest import MODULE_MANIFEST_SCHEMA
from src.services.model_manager.catalog import MODEL_CATALOG_SCHEMA
from src.services.runtime_manager.catalog import RUNTIME_CATALOG_SCHEMA


ROOT = Path(__file__).resolve().parents[2]


class V7ArchitectureGuards(unittest.TestCase):
    def test_required_metadata_files_exist(self) -> None:
        for relative in (
            "architecture/module_ownership.yaml",
            "architecture/dependency_rules.yaml",
            "docs/architecture/ARCHITECTURE.md",
            "docs/architecture/CHANGE_IMPACT_MAP.md",
            "docs/architecture/V7_MIGRATION_PLAN.md",
            "docs/development/ADDING_A_NEW_AI_MODULE.md",
            "docs/operations/CLEAN_CLONE_BOOTSTRAP.md",
            "docs/operations/MODEL_INSTALLATION.md",
            "docs/operations/RUNTIME_INSTALLATION.md",
            "Config/model_catalog.example.json",
            "Config/runtime_catalog.example.json",
            "scripts/bootstrap_core.ps1",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_product_version_has_one_current_source(self) -> None:
        release = json.loads((ROOT / "distribution" / "release_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(release["version"], PRODUCT_VERSION)
        components = json.loads((ROOT / "Config" / "components.example.json").read_text(encoding="utf-8"))
        versions = {item["version"] for item in components["components"] if item["id"] in {"local_ai_api", "local_ai_mcp"}}
        self.assertEqual(versions, {PRODUCT_VERSION})
        extensions = json.loads((ROOT / "Config" / "extensions.example.json").read_text(encoding="utf-8"))
        self.assertEqual(extensions["hub_version"], PRODUCT_VERSION)

    def test_module_manifests_are_allowlisted_and_unique(self) -> None:
        result = discover_manifests(ROOT / "src" / "modules")
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["records"]), 10)
        self.assertEqual(len({item["id"] for item in result["records"].values()}), 10)
        for item in result["records"].values():
            self.assertEqual(item["schema_version"], MODULE_MANIFEST_SCHEMA)
            self.assertNotIn("D:\\", json.dumps(item))

    def test_catalogs_validate(self) -> None:
        model_catalog = json.loads((ROOT / "Config" / "model_catalog.example.json").read_text(encoding="utf-8"))
        runtime_catalog = json.loads((ROOT / "Config" / "runtime_catalog.example.json").read_text(encoding="utf-8"))
        self.assertEqual(model_catalog["schema_version"], MODEL_CATALOG_SCHEMA)
        self.assertEqual(runtime_catalog["schema_version"], RUNTIME_CATALOG_SCHEMA)
        ModelManager(paths=get_paths(app_root=ROOT, data_root=ROOT), catalog_path=ROOT / "Config" / "model_catalog.example.json")
        RuntimeManager(paths=get_paths(app_root=ROOT, data_root=ROOT), catalog_path=ROOT / "Config" / "runtime_catalog.example.json")

    def test_import_direction_has_only_documented_legacy_exception(self) -> None:
        violations: list[str] = []
        for path in (ROOT / "src" / "shared").rglob("*.py"):
            if path.name == "adapter_common.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(("src.services", "src.modules", "src.ui")):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(("src.services", "src.modules", "src.ui")):
                            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_no_model_weights_are_tracked(self) -> None:
        output = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
        forbidden = (".safetensors", ".ckpt", ".pt", ".pth", ".onnx", ".gguf")
        self.assertEqual([line for line in output.splitlines() if line.lower().endswith(forbidden)], [])

    def test_clean_clone_model_free_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            result = bootstrap_core(paths=get_paths(app_root=ROOT, data_root=data_root))
            self.assertEqual(result["status"], "ready")
            self.assertFalse(result["optional_ai"]["models_downloaded"])
            self.assertFalse(list((data_root / "Models").rglob("*.safetensors")))
            self.assertTrue((data_root / "Config" / "components.json").is_file())
            second = bootstrap_core(paths=get_paths(app_root=ROOT, data_root=data_root))
            self.assertTrue(all(item["state"] == "preserved_existing" for item in second["config"]))

    def test_model_manager_fake_install_is_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            paths = get_paths(app_root=ROOT, data_root=data_root)
            manager = ModelManager(paths=paths, catalog_path=ROOT / "Config" / "model_catalog.example.json")
            fixture = data_root / "fixture"
            (fixture / "Video" / "AnimeSR").mkdir(parents=True)
            (fixture / "Video" / "AnimeSR" / "animesr_v2.pth").write_bytes(b"")
            result = manager.install_fixture("animesr-v2", fixture)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(manager.inspect("animesr-v2")["status"], "INSTALLED_UNVERIFIED")

    def test_module_composition_does_not_promote_file_presence(self) -> None:
        result = compose_registry(
            {"sam2": {"id": "sam2", "display_name": "SAM 2", "runtime": {"required": "sam2"}, "models": {"required": ["sam2.1-hiera-small"]}}},
            runtime_statuses={"sam2": {"status": "INSTALLED_UNVERIFIED"}},
            model_statuses={"sam2.1-hiera-small": {"model_id": "sam2.1-hiera-small", "status": "INSTALLED_UNVERIFIED"}},
        )
        self.assertEqual(result["records"][0]["status"], "INSTALLED_UNVERIFIED")
        self.assertNotEqual(result["records"][0]["status"], "OPERATIONAL")


if __name__ == "__main__":
    unittest.main()
