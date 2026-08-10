"""Focused M8C contract, security, discovery and planning tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.services.creative_recipe_intelligence import (
    build_compatibility_report,
    build_generation_intent,
    discover_managed_recipe_catalogs,
    export_compatibility_markdown,
    export_discovery_markdown,
    export_generation_intent_markdown,
    export_lint_markdown,
    lint_recipe,
    load_managed_recipe,
    load_managed_recipe_catalog,
    plan_recipe_variants,
)
from src.services.creative_recipe_intelligence import catalog as catalog_module
from src.services.creative_recipe_intelligence.io import export_recipe_catalog, safe_import_recipe_catalog
from src.shared.schemas.creative_recipes import (
    MAX_DESCRIPTOR_BYTES,
    canonical_recipe_catalog_json,
    compatibility_report_schema,
    creative_recipe_schemas,
    generation_intent_schema,
    lint_finding_schema,
    prompt_slot_schema,
    prompt_variant_schema,
    recipe_catalog_schema,
    recipe_schema,
    style_pack_schema,
    target_card_schema,
    validate_recipe_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "creative_recipes" / "catalog" / "creative-recipe-intelligence.creative-recipe-catalog.json"


class Milestone8CreativeRecipeIntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        validated = validate_recipe_catalog(cls.catalog)
        if not validated["valid"]:
            raise AssertionError(validated["errors"])
        cls.normalized = validated["catalog"]
        cls.recipe = next(item for item in cls.normalized["recipes"] if item["id"] == "recipe_product_image")
        cls.portrait = next(item for item in cls.normalized["recipes"] if item["id"] == "recipe_portrait_cinematic")
        cls.video = next(item for item in cls.normalized["recipes"] if item["id"] == "recipe_short_video_concept")
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            Draft202012Validator = None
        cls.Draft202012Validator = Draft202012Validator

    def test_schema_objects_are_structurally_complete_and_real_draft_valid(self) -> None:
        def walk(value: object) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("required"), list):
                    self.assertTrue(set(value["required"]).issubset(set(value.get("properties", {}))))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        for schema in creative_recipe_schemas():
            walk(schema)
            if self.Draft202012Validator is not None:
                self.Draft202012Validator.check_schema(schema)

    def test_sample_catalog_and_nested_contracts_pass_real_schema(self) -> None:
        if self.Draft202012Validator is None:
            self.skipTest("jsonschema is provided by the bounded hub test environment")
        values = [
            (recipe_catalog_schema(), self.normalized),
            (recipe_schema(), self.recipe),
            (style_pack_schema(), self.normalized["style_packs"][0]),
            (prompt_slot_schema(), self.recipe["slots"][0]),
            (prompt_variant_schema(), self.recipe["variants"][0]),
            (target_card_schema(), self.normalized["targets"][0]),
        ]
        for schema, value in values:
            self.assertFalse(list(self.Draft202012Validator(schema).iter_errors(value)))
        intent = build_generation_intent(self.recipe, {"slot_subject": "wireless speaker"}, target_id="target_image_basic")
        self.assertTrue(intent["valid"])
        self.assertFalse(list(self.Draft202012Validator(generation_intent_schema()).iter_errors(intent["intent"])))
        report = build_compatibility_report(self.recipe, "target_image_basic")
        self.assertTrue(report["valid"])
        self.assertFalse(list(self.Draft202012Validator(compatibility_report_schema()).iter_errors(report["report"])))
        lint = lint_recipe(self.recipe, {"slot_subject": "wireless speaker"}, target_id="target_image_basic")
        for finding in lint["findings"]:
            self.assertFalse(list(self.Draft202012Validator(lint_finding_schema()).iter_errors(finding)))

    def test_import_rejects_duplicate_nonfinite_utf8_oversize_and_unsafe_without_echo(self) -> None:
        duplicate = safe_import_recipe_catalog('{"schema_version":"creative-recipe-catalog.v1","schema_version":"bad"}')
        self.assertFalse(duplicate["accepted"])
        self.assertEqual(duplicate["errors"][0]["code"], "duplicate_json_key")
        self.assertFalse(safe_import_recipe_catalog(b"{\"x\":NaN}")["accepted"])
        self.assertFalse(safe_import_recipe_catalog(b"\xff")["accepted"])
        self.assertFalse(safe_import_recipe_catalog(b"{" + b"a" * MAX_DESCRIPTOR_BYTES + b"}")["accepted"])
        for unsafe in (r"C:\private\prompt.txt", "https://private.invalid", "api" + "_key=secret-value", "Bearer abcdefghijk", "eyJhbGciOiJIUzI1NiJ9.segmentvalue.payloadvalue", "<script>alert(1)</script>", "powershell -c echo", "data:image/png;base64,AAAA"):
            bad = copy.deepcopy(self.catalog)
            bad["description"] = unsafe
            result = validate_recipe_catalog(bad)
            self.assertFalse(result["valid"])
            self.assertNotIn(unsafe, json.dumps(result))

    def test_unknown_fields_and_type_errors_fail_closed(self) -> None:
        bad = copy.deepcopy(self.recipe)
        bad["runner"] = "python"
        self.assertFalse(validate_recipe_catalog({**self.catalog, "recipes": [bad]})["valid"])
        bad = copy.deepcopy(self.recipe)
        bad["slots"][0]["default"] = float("nan")
        self.assertFalse(validate_recipe_catalog({**self.catalog, "recipes": [bad]})["valid"])
        bad = copy.deepcopy(self.recipe)
        bad["slots"][1]["default"] = "not-an-enum"
        self.assertFalse(validate_recipe_catalog({**self.catalog, "recipes": [bad]})["valid"])

    def test_schema_runtime_security_parity_for_required_text_and_scrubbed_errors(self) -> None:
        if self.Draft202012Validator is None:
            self.skipTest("jsonschema is provided by the bounded hub test environment")
        for unsafe in (" ", r"\Windows\System32", "/etc/passwd", "../private", "https://private.invalid"):
            bad = copy.deepcopy(self.catalog)
            bad["label"] = unsafe
            runtime = validate_recipe_catalog(bad)
            schema_errors = list(self.Draft202012Validator(recipe_catalog_schema()).iter_errors(bad))
            self.assertFalse(runtime["valid"])
            self.assertTrue(schema_errors)
            if unsafe.strip():
                self.assertNotIn(unsafe, json.dumps(runtime))
        bad = copy.deepcopy(self.catalog)
        bad["untrusted_field"] = r"C:\private\secret.txt"
        result = validate_recipe_catalog(bad)
        self.assertFalse(result["valid"])
        self.assertNotIn("secret.txt", json.dumps(result))

    def test_scrubbed_markdown_projections_only_emit_opaque_fields(self) -> None:
        lint = lint_recipe(self.recipe, {"slot_subject": "lamp"}, target_id="target_image_basic")
        intent = build_generation_intent(self.recipe, {"slot_subject": "lamp"}, target_id="target_image_basic")
        report = build_compatibility_report(self.recipe, "target_image_basic")
        self.assertTrue(intent["valid"])
        for exported in (
            export_lint_markdown(lint["findings"]),
            export_generation_intent_markdown(intent["intent"]),
            export_compatibility_markdown(report["report"]),
            export_discovery_markdown(),
        ):
            self.assertTrue(exported["ready"])
            text = exported["content"].decode("utf-8")
            self.assertNotIn("wireless", text)
            self.assertNotIn("C:\\", text)
            self.assertNotIn("<script>", text)
            self.assertIn("not_run", text)

    def test_discovery_is_fixed_root_and_duplicate_identity_is_unavailable(self) -> None:
        self.assertEqual(discover_managed_recipe_catalogs()["status"], "partial")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "one.creative-recipe-catalog.json"
            second = root / "two.creative-recipe-catalog.json"
            first.write_text(json.dumps(self.catalog), encoding="utf-8")
            second.write_text(json.dumps(self.catalog), encoding="utf-8")
            old_root = catalog_module.MANAGED_RECIPE_ROOT
            catalog_module.MANAGED_RECIPE_ROOT = root
            try:
                result = discover_managed_recipe_catalogs()
                self.assertEqual(result["status"], "unavailable")
                self.assertIn({"code": "managed_catalog_identity_ambiguous"}, result["errors"])
                self.assertNotIn(self.catalog["label"], json.dumps(result))
                loaded = load_managed_recipe_catalog(self.catalog["id"])
                self.assertFalse(loaded["found"])
            finally:
                catalog_module.MANAGED_RECIPE_ROOT = old_root

    def test_unversioned_entity_identity_is_ambiguous_but_explicit_version_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_catalog = copy.deepcopy(self.catalog)
            second_catalog = copy.deepcopy(self.catalog)
            second_catalog["version"] = "1.1.0"
            first_catalog["recipes"][0]["version"] = "1.0.0"
            second_catalog["recipes"][0]["version"] = "1.1.0"
            (root / "one.creative-recipe-catalog.json").write_text(json.dumps(first_catalog), encoding="utf-8")
            (root / "two.creative-recipe-catalog.json").write_text(json.dumps(second_catalog), encoding="utf-8")
            old_root = catalog_module.MANAGED_RECIPE_ROOT
            catalog_module.MANAGED_RECIPE_ROOT = root
            try:
                result = discover_managed_recipe_catalogs()
                self.assertIn({"code": "managed_recipe_identity_ambiguous"}, result["errors"])
                self.assertFalse(load_managed_recipe("recipe_product_image")["found"])
                self.assertTrue(load_managed_recipe("recipe_product_image", "1.0.0")["found"])
            finally:
                catalog_module.MANAGED_RECIPE_ROOT = old_root

    def test_bounded_reader_refuses_oversize_and_never_unbounded_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "one.creative-recipe-catalog.json"
            path.write_bytes(b"{" + b"a" * MAX_DESCRIPTOR_BYTES + b"}")
            with patch.object(catalog_module.os, "open", wraps=catalog_module.os.open) as opened:
                payload, error = catalog_module._read_managed_json(path, root=root)
            self.assertIsNone(payload)
            self.assertEqual(error, "managed_recipe_size")
            opened.assert_not_called()
            path.write_text(json.dumps(self.catalog), encoding="utf-8")
            with patch.object(catalog_module.os, "read", wraps=catalog_module.os.read) as reader:
                payload, error = catalog_module._read_managed_json(path, root=root)
            self.assertIsNotNone(payload)
            self.assertIsNone(error)
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(reader.call_args.args[1], MAX_DESCRIPTOR_BYTES + 1)

    def test_reader_fails_closed_when_file_changes_after_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "one.creative-recipe-catalog.json"
            path.write_text(json.dumps(self.catalog), encoding="utf-8")
            original_read = catalog_module.os.read

            def read_then_mutate(fd: int, count: int) -> bytes:
                payload = original_read(fd, count)
                path.write_text(json.dumps(self.catalog) + " ", encoding="utf-8")
                return payload

            with patch.object(catalog_module.os, "read", side_effect=read_then_mutate):
                payload, error = catalog_module._read_managed_json(path, root=root)
            self.assertIsNone(payload)
            self.assertEqual(error, "managed_recipe_refused")

    def test_composition_lint_and_intent_are_deterministic_and_dry_run(self) -> None:
        values = {"slot_subject": "wireless speaker", "slot_surface": "matte paper"}
        first = lint_recipe(self.recipe, values, target_id="target_image_basic")
        second = lint_recipe(self.recipe, values, target_id="target_image_basic")
        self.assertEqual(first, second)
        self.assertTrue(first["valid"])
        self.assertEqual(first["status"], "planned")
        self.assertIn("wireless speaker", first["prompt"])
        self.assertEqual(first["execution"], "not_run")
        intent = build_generation_intent(self.recipe, values, target_id="target_image_basic")
        self.assertTrue(intent["valid"])
        self.assertTrue(intent["intent"]["dry_run"])
        self.assertEqual(intent["intent"]["execution"], "not_run")

    def test_lint_finds_missing_slots_conflicts_unsafe_text_and_unsupported_target(self) -> None:
        missing = lint_recipe(self.portrait, {}, target_id="target_image_basic")
        self.assertFalse(missing["valid"])
        self.assertIn("missing_required_slot", {item["code"] for item in missing["findings"]})
        bad = lint_recipe(self.recipe, {"slot_surface": "wrong"}, target_id="target_video_concept", parameters={"width": 99999})
        codes = {item["code"] for item in bad["findings"]}
        self.assertIn("slot_type_mismatch", codes)
        self.assertIn("constraint_conflict", codes)
        self.assertIn("unsupported_target", codes)
        for unsafe_value in (r"C:\private\subject.png", r"C:relative\subject.png", r"\Windows\System32", "/etc/passwd", "../private"):
            unsafe = lint_recipe(self.recipe, {"slot_subject": unsafe_value}, target_id="target_image_basic")
            self.assertIn("unsafe_text", {item["code"] for item in unsafe["findings"]})
            self.assertNotIn("subject.png", json.dumps(unsafe))

    def test_unavailable_video_target_does_not_become_planned_execution(self) -> None:
        lint = lint_recipe(self.video, {"slot_subject": "paper sculpture"}, target_id="target_video_concept")
        self.assertEqual(lint["compatibility"]["status"], "unavailable")
        self.assertEqual(lint["status"], "manual_review")
        intent = build_generation_intent(self.video, {"slot_subject": "paper sculpture"}, target_id="target_video_concept")
        self.assertFalse(intent["valid"])
        self.assertIsNone(intent["intent"])
        self.assertEqual(intent["execution"], "not_run")

    def test_variant_planning_is_stable_and_style_media_is_checked(self) -> None:
        first = plan_recipe_variants(self.recipe, {"slot_subject": "lamp"}, target_id="target_image_basic")
        second = plan_recipe_variants(self.recipe, {"slot_subject": "lamp"}, target_id="target_image_basic")
        self.assertEqual(first, second)
        self.assertEqual(len(first["variants"]), 2)
        bad = copy.deepcopy(self.recipe)
        bad["style_pack_ids"] = ["style_video_storyboard"]
        lint = lint_recipe(bad, {"slot_subject": "lamp"}, target_id="target_image_basic")
        self.assertIn("style_media_mismatch", {item["code"] for item in lint["findings"]})

    def test_export_is_canonical_detached_and_never_writes_source(self) -> None:
        source = copy.deepcopy(self.catalog)
        exported = export_recipe_catalog(source)
        self.assertTrue(exported["ready"])
        self.assertEqual(exported["content"].decode("utf-8"), canonical_recipe_catalog_json(self.normalized))
        imported = safe_import_recipe_catalog(exported["content"])
        self.assertTrue(imported["accepted"])
        imported["catalog"]["label"] = "detached"
        self.assertNotEqual(imported["catalog"]["label"], source["label"])
        self.assertEqual(source, self.catalog)

    def test_cli_json_and_markdown_are_static(self) -> None:
        script = ROOT / "scripts" / "validate_creative_recipes.py"
        json_run = subprocess.run([r"D:\LocalAIHub\Environments\hub\Scripts\python.exe", "-B", str(script), "--format", "json"], cwd=ROOT, capture_output=True, text=True, check=False)
        md_run = subprocess.run([r"D:\LocalAIHub\Environments\hub\Scripts\python.exe", "-B", str(script), "--format", "markdown"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(json_run.returncode, 0, json_run.stderr)
        self.assertEqual(md_run.returncode, 0, md_run.stderr)
        report = json.loads(json_run.stdout)
        self.assertEqual(report["execution"], "not_run")
        self.assertNotIn("creative-recipe-intelligence.creative-recipe-catalog.json", json_run.stdout)
        self.assertNotIn("C:\\", md_run.stdout)


if __name__ == "__main__":
    unittest.main()
