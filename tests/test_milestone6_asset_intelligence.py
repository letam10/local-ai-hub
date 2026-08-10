from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.services.asset_intelligence import (
    build_asset_qa_markdown,
    build_asset_qa_report,
    build_exact_duplicate_groups,
    diff_asset_catalogs,
    evaluate_smart_collection,
    export_asset_catalog,
    export_dataset_manifest,
    plan_asset_catalog_migration,
    plan_asset_retention,
    preflight_provenance_lineage,
    provider_capability_cards,
    safe_import_asset_catalog,
    safe_import_asset_record,
)
from src.services.asset_intelligence import catalog as catalog_module
from src.shared.schemas.asset_intelligence import (
    MAX_DESCRIPTOR_BYTES,
    MAX_QUERY_NODES,
    asset_catalog_schema,
    asset_record_schema,
    canonical_asset_catalog_json,
    provenance_lineage_schema,
    smart_collection_schema,
    validate_asset_catalog,
    validate_asset_record,
    validate_provenance_lineage,
    validate_smart_collection,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "asset_catalog" / "samples"
HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None


def sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def codes(result: dict) -> set[str]:
    return {item["code"] for item in result.get("errors", [])}


class Milestone6AssetIntelligenceTests(unittest.TestCase):
    def catalog(self) -> dict:
        return sample("portrait-assets.asset-catalog.json")

    def lineage(self) -> dict:
        return sample("portrait-lineage.provenance-lineage.json")

    def collection(self) -> dict:
        return sample("portrait-duplicates.smart-collection.json")

    def test_samples_validate_canonicalize_and_return_detached_exports(self) -> None:
        catalog = self.catalog()
        lineage = self.lineage()
        collection = self.collection()
        original_catalog = copy.deepcopy(catalog)

        self.assertTrue(validate_asset_catalog(catalog)["valid"])
        self.assertTrue(validate_provenance_lineage(lineage)["valid"])
        self.assertTrue(validate_smart_collection(collection)["valid"])

        reordered = copy.deepcopy(catalog)
        reordered["assets"].reverse()
        self.assertEqual(canonical_asset_catalog_json(catalog), canonical_asset_catalog_json(reordered))
        self.assertEqual(validate_asset_catalog(catalog)["fingerprint"], validate_asset_catalog(reordered)["fingerprint"])

        exported = export_asset_catalog(catalog)
        self.assertTrue(exported["ready"])
        self.assertEqual(exported["execution"], "not_run")
        self.assertEqual(json.loads(exported["content"])["id"], catalog["id"])
        exported["content"] = "mutated"
        self.assertEqual(catalog, original_catalog)

    def test_published_schemas_have_complete_object_required_lists(self) -> None:
        def check(value: object) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("required"), list):
                    self.assertTrue(
                        set(value["required"]).issubset(set(value.get("properties", {}))),
                        f"required field missing from properties: {value}",
                    )
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        for schema in (asset_record_schema(), asset_catalog_schema(), provenance_lineage_schema(), smart_collection_schema()):
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            check(schema)

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema is optional; no dependency is installed by this milestone")
    def test_draft202012_schema_runtime_parity_for_representative_contract_values(self) -> None:
        from jsonschema import Draft202012Validator

        catalog = self.catalog()
        lineage = self.lineage()
        collection = self.collection()
        record = copy.deepcopy(catalog["assets"][0])
        fixtures = (
            (asset_record_schema(), record, validate_asset_record),
            (asset_catalog_schema(), catalog, validate_asset_catalog),
            (provenance_lineage_schema(), lineage, validate_provenance_lineage),
            (smart_collection_schema(), collection, validate_smart_collection),
        )
        for schema, value, runtime in fixtures:
            validator = Draft202012Validator(schema)
            self.assertTrue(validator.is_valid(value), list(validator.iter_errors(value)))
            self.assertTrue(runtime(value)["valid"])

        raw_root = "\\" + "Windows\\System32"
        bad_record = copy.deepcopy(record)
        bad_record["label"] = raw_root
        bad_catalog = copy.deepcopy(catalog)
        bad_catalog["label"] = "api" + "_key=" + "small-secret-42"
        bad_lineage = copy.deepcopy(lineage)
        bad_lineage["label"] = "powershell" + " -NoProfile"
        bad_collection = copy.deepcopy(collection)
        bad_collection["query"] = {"field": "asset.kind", "operator": "gte", "value": "image"}
        rejected = (
            (asset_record_schema(), bad_record, validate_asset_record),
            (asset_catalog_schema(), bad_catalog, validate_asset_catalog),
            (provenance_lineage_schema(), bad_lineage, validate_provenance_lineage),
            (smart_collection_schema(), bad_collection, validate_smart_collection),
        )
        for schema, value, runtime in rejected:
            self.assertFalse(Draft202012Validator(schema).is_valid(value))
            self.assertFalse(runtime(value)["valid"])

    def test_scrubber_rejects_extended_paths_secrets_commands_and_embedded_payloads(self) -> None:
        base = self.catalog()["assets"][0]
        values = (
            "C:" + "relative\\asset.png",
            "\\" + "Windows\\System32",
            "api" + "_key=" + "small-secret-42",
            "password" + "=" + "small-secret-42",
            "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
            "powershell" + " -NoProfile",
            "data:" + "image/png;base64," + "a" * 16,
            "folder" + "/asset.png",
        )
        for value in values:
            candidate = copy.deepcopy(base)
            candidate["label"] = value
            result = validate_asset_record(candidate)
            self.assertFalse(result["valid"], value)
            self.assertTrue(codes(result) & {"raw_path", "secret_detected", "command_detected", "unsafe_uri", "embedded_binary"}, value)

        bad = copy.deepcopy(base)
        raw_root = "\\" + "Windows\\System32"
        bad["label"] = raw_root
        imported = safe_import_asset_record(json.dumps(bad))
        self.assertFalse(imported["accepted"])
        self.assertNotIn(raw_root, json.dumps(imported, sort_keys=True))

    def test_closed_contract_bounds_and_runtime_only_identity_rules_reject(self) -> None:
        record = self.catalog()["assets"][0]
        missing = copy.deepcopy(record)
        del missing["label"]
        self.assertIn("required_field", codes(validate_asset_record(missing)))

        unexpected = copy.deepcopy(record)
        unexpected["extra"] = "not allowed"
        self.assertIn("unknown_field", codes(validate_asset_record(unexpected)))

        poisoned = copy.deepcopy(record)
        poisoned["__proto__"] = {"value": "not allowed"}
        self.assertTrue(codes(validate_asset_record(poisoned)) & {"unknown_field", "forbidden_field"})

        invalid_kind = copy.deepcopy(record)
        invalid_kind["asset"]["kind"] = "binary"
        self.assertIn("asset_kind", codes(validate_asset_record(invalid_kind)))

        wrong_status = copy.deepcopy(record)
        wrong_status["fingerprints"]["perceptual"][0]["status"] = "available"
        self.assertIn("fingerprint_status", codes(validate_asset_record(wrong_status)))

        too_many_fingerprints = copy.deepcopy(record)
        fingerprint = copy.deepcopy(too_many_fingerprints["fingerprints"]["perceptual"][0])
        too_many_fingerprints["fingerprints"]["perceptual"] = [copy.deepcopy(fingerprint) for _ in range(9)]
        self.assertIn("fingerprint_limit", codes(validate_asset_record(too_many_fingerprints)))

        duplicate_catalog = self.catalog()
        duplicate_catalog["assets"][1]["id"] = duplicate_catalog["assets"][0]["id"]
        self.assertIn("duplicate_asset_id", codes(validate_asset_catalog(duplicate_catalog)))

        duplicate_fingerprint = copy.deepcopy(record)
        duplicate_fingerprint["fingerprints"]["perceptual"].append(copy.deepcopy(duplicate_fingerprint["fingerprints"]["perceptual"][0]))
        self.assertIn("duplicate_fingerprint", codes(validate_asset_record(duplicate_fingerprint)))

        lineage = self.lineage()
        lineage["retention_bound_days"] = 179
        self.assertIn("retention_bound", codes(validate_provenance_lineage(lineage)))

    def test_typed_smart_collection_rules_and_global_node_bound_are_fail_closed(self) -> None:
        invalid = self.collection()
        invalid["query"] = {"field": "asset.kind", "operator": "gte", "value": "image"}
        self.assertIn("query_operator", codes(validate_smart_collection(invalid)))

        invalid_bool = self.collection()
        invalid_bool["query"] = {"field": "asset.bytes", "operator": "gte", "value": True}
        self.assertIn("integer_bound", codes(validate_smart_collection(invalid_bool)))

        boolean_as_text = self.collection()
        boolean_as_text["query"] = {"field": "exact_duplicate", "operator": "eq", "value": "true"}
        self.assertIn("query_value", codes(validate_smart_collection(boolean_as_text)))

        duplicate_in = self.collection()
        duplicate_in["query"] = {"field": "asset.kind", "operator": "in", "value": ["image", "image"]}
        self.assertIn("duplicate_query_value", codes(validate_smart_collection(duplicate_in)))

        too_many_terms = self.collection()
        too_many_terms["query"] = {"all": [{"field": "asset.kind", "operator": "eq", "value": "image"} for _ in range(17)]}
        self.assertIn("query_term_limit", codes(validate_smart_collection(too_many_terms)))

        too_deep = self.collection()
        expression: dict = {"field": "asset.kind", "operator": "eq", "value": "image"}
        for _ in range(5):
            expression = {"all": [expression]}
        too_deep["query"] = expression
        self.assertIn("query_depth", codes(validate_smart_collection(too_deep)))

        def flat_tree(last_group_size: int) -> dict:
            leaf = {"field": "asset.kind", "operator": "eq", "value": "image"}
            return {
                "all": [
                    {"all": [copy.deepcopy(leaf) for _ in range(size)]}
                    for size in (16, 16, 16, last_group_size)
                ]
            }

        at_limit = self.collection()
        at_limit["query"] = flat_tree(11)  # root + four groups + 59 leaves = 64 nodes
        self.assertEqual(MAX_QUERY_NODES, 64)
        self.assertTrue(validate_smart_collection(at_limit)["valid"])

        oversized = self.collection()
        oversized["query"] = flat_tree(12)
        self.assertIn("query_node_limit", codes(validate_smart_collection(oversized)))

        selection = evaluate_smart_collection(self.collection(), self.catalog())
        self.assertTrue(selection["valid"])
        self.assertEqual(selection["asset_ids"], ["portrait-source", "portrait-source-copy"])
        self.assertEqual(selection["execution"], "not_run")

    def test_lineage_integrity_cycle_and_catalog_retention_preflight(self) -> None:
        lineage = self.lineage()
        catalog = self.catalog()

        wrong_relation = copy.deepcopy(lineage)
        wrong_relation["edges"][0]["relation"] = "package_to_run"
        self.assertIn("lineage_relation_type", codes(validate_provenance_lineage(wrong_relation)))

        duplicate_node = copy.deepcopy(lineage)
        duplicate_node["nodes"][1]["id"] = duplicate_node["nodes"][0]["id"]
        self.assertIn("duplicate_lineage_node", codes(validate_provenance_lineage(duplicate_node)))

        duplicate_edge = copy.deepcopy(lineage)
        duplicate_edge["edges"][1]["id"] = duplicate_edge["edges"][0]["id"]
        self.assertIn("duplicate_lineage_edge", codes(validate_provenance_lineage(duplicate_edge)))

        unknown_node = copy.deepcopy(lineage)
        unknown_node["edges"][0]["to"] = "missing-node"
        self.assertIn("lineage_unknown_node", codes(validate_provenance_lineage(unknown_node)))

        cycle = copy.deepcopy(lineage)
        cycle["edges"].append(
            {"id": "derivative-back-to-recipe", "from": "derivative-asset", "to": "portrait-recipe-node", "relation": "source_to_recipe"}
        )
        self.assertIn("lineage_cycle", codes(validate_provenance_lineage(cycle)))

        matching = preflight_provenance_lineage(lineage, catalog)
        self.assertEqual(matching["status"], "partial")
        self.assertEqual(matching["retention_mismatches"], [])

        mismatch = copy.deepcopy(lineage)
        mismatch["nodes"][0]["retention_days"] = 181
        preflight = preflight_provenance_lineage(mismatch, catalog)
        self.assertTrue(preflight["valid"])
        self.assertEqual(preflight["status"], "unavailable")
        self.assertEqual(preflight["retention_mismatches"], ["portrait-source"])
        self.assertEqual(preflight["execution"], "not_run")

    def test_declared_sha_groups_and_collection_evaluation_are_deterministic(self) -> None:
        catalog = self.catalog()
        original = copy.deepcopy(catalog)
        groups = build_exact_duplicate_groups(catalog)
        self.assertTrue(groups["valid"])
        self.assertEqual(groups["groups"][0]["match"], "declared_sha256_match")
        self.assertEqual(groups["groups"][0]["asset_ids"], ["portrait-source", "portrait-source-copy"])
        self.assertIn("metadata", groups["reason"])
        self.assertEqual(catalog, original)

    def test_diff_and_migration_use_canonical_inputs_and_manual_review_for_restrictive_changes(self) -> None:
        before = self.catalog()
        reordered = copy.deepcopy(before)
        reordered["assets"].reverse()
        unchanged = diff_asset_catalogs(before, reordered)
        self.assertTrue(unchanged["valid"])
        self.assertEqual(unchanged["status"], "unchanged")
        self.assertEqual(plan_asset_catalog_migration(before, reordered)["status"], "not_required")

        after = copy.deepcopy(before)
        after["assets"][0]["asset"]["bytes"] += 1
        before_snapshot = copy.deepcopy(before)
        after_snapshot = copy.deepcopy(after)
        changed = diff_asset_catalogs(before, after)
        plan = plan_asset_catalog_migration(before, after)
        self.assertEqual(changed["status"], "changed")
        self.assertEqual(plan["status"], "manual_review")
        self.assertTrue(plan["dry_run"])
        self.assertEqual(before, before_snapshot)
        self.assertEqual(after, after_snapshot)
        self.assertEqual(plan, plan_asset_catalog_migration(before, after))

    def test_import_is_bounded_rejects_duplicate_keys_and_returns_detached_data(self) -> None:
        catalog = self.catalog()
        original = copy.deepcopy(catalog)
        imported = safe_import_asset_catalog(json.dumps(catalog))
        self.assertTrue(imported["accepted"])
        assert isinstance(imported["catalog"], dict)
        imported["catalog"]["assets"][0]["label"] = "Changed detached value"
        self.assertEqual(catalog, original)

        duplicate_keys = b'{"schema_version":"asset-record.v1","schema_version":"asset-record.v1"}'
        self.assertEqual(safe_import_asset_record(duplicate_keys)["errors"][0]["code"], "duplicate_json_key")
        self.assertEqual(safe_import_asset_record(b'{"value":NaN}')["errors"][0]["code"], "json_parse")
        self.assertEqual(safe_import_asset_record(b"\xef\xbb\xbf{}")["errors"][0]["code"], "bom")
        self.assertEqual(safe_import_asset_record(b"\xff")["errors"][0]["code"], "utf8")
        self.assertEqual(safe_import_asset_record({"not": "json text"})["errors"][0]["code"], "payload_type")

        oversized = b"{" + (b" " * MAX_DESCRIPTOR_BYTES)
        self.assertEqual(safe_import_asset_catalog(oversized)["errors"][0]["code"], "payload_size")

    def test_managed_discovery_rejects_ambiguous_identities_and_checks_size_before_read(self) -> None:
        catalog = self.catalog()
        lineage = self.lineage()
        collection = self.collection()
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "asset_catalog"
            root.mkdir()
            (root / "one.asset-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
            duplicate_catalog = copy.deepcopy(catalog)
            duplicate_catalog["label"] = "Second managed descriptor"
            (root / "two.asset-catalog.json").write_text(json.dumps(duplicate_catalog), encoding="utf-8")
            (root / "one.provenance-lineage.json").write_text(json.dumps(lineage), encoding="utf-8")
            (root / "two.provenance-lineage.json").write_text(json.dumps(lineage), encoding="utf-8")
            (root / "one.smart-collection.json").write_text(json.dumps(collection), encoding="utf-8")
            (root / "two.smart-collection.json").write_text(json.dumps(collection), encoding="utf-8")

            with patch.object(catalog_module, "MANAGED_ASSET_ROOT", root):
                discovery = catalog_module.discover_managed_asset_catalogs()
                loaded = catalog_module.load_managed_asset_catalog(catalog["id"])
            self.assertEqual(discovery["catalogs"], [])
            self.assertEqual(discovery["lineages"], [])
            self.assertEqual(discovery["collections"], [])
            self.assertEqual(
                {item["code"] for item in discovery["errors"]},
                {"managed_catalog_identity_ambiguous", "managed_lineage_identity_ambiguous", "managed_collection_identity_ambiguous"},
            )
            self.assertFalse(loaded["found"])
            self.assertNotIn(str(root), json.dumps(discovery, sort_keys=True))

            oversized_path = root / "oversized.asset-catalog.json"
            oversized_path.write_bytes(b"{" + (b" " * MAX_DESCRIPTOR_BYTES))
            with patch.object(catalog_module, "MANAGED_ASSET_ROOT", root), patch.object(Path, "read_bytes", side_effect=AssertionError("must not read oversized descriptor")):
                self.assertEqual(catalog_module._read_managed_json(oversized_path), (None, "managed_descriptor_size"))

            outside = Path(temporary) / "outside.asset-catalog.json"
            outside.write_text(json.dumps(catalog), encoding="utf-8")
            with patch.object(catalog_module, "MANAGED_ASSET_ROOT", root):
                self.assertEqual(catalog_module._read_managed_json(outside), (None, "managed_descriptor_refused"))
            symlink = root / "linked.asset-catalog.json"
            try:
                symlink.symlink_to(outside)
            except OSError:
                pass  # Windows may deny symlink creation for an unprivileged test process.
            else:
                with patch.object(catalog_module, "MANAGED_ASSET_ROOT", root):
                    self.assertEqual(catalog_module._read_managed_json(symlink), (None, "managed_descriptor_refused"))

    def test_static_reports_retention_plan_and_provider_cards_do_not_claim_runtime_work(self) -> None:
        catalog = self.catalog()
        report = build_asset_qa_report(catalog)
        markdown = build_asset_qa_markdown(catalog)
        manifest = export_dataset_manifest(catalog, self.collection())
        retention = plan_asset_retention(catalog, maximum_retention_days=90)
        cards = provider_capability_cards()

        self.assertTrue(report["valid"])
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["execution"], "not_run")
        self.assertIn("not_run", markdown)
        self.assertTrue(manifest["ready"])
        self.assertEqual(manifest["execution"], "not_run")
        self.assertTrue(retention["dry_run"])
        self.assertEqual(retention["status"], "manual_review")
        self.assertEqual({card["id"] for card in cards["cards"]}, {"caption", "tag", "embedding"})
        self.assertTrue(all(card["execution"] == "not_run" for card in cards["cards"]))

        unchanged_retention = plan_asset_retention(catalog)
        self.assertEqual(unchanged_retention["status"], "planned")
        self.assertEqual(unchanged_retention, plan_asset_retention(catalog))
        self.assertEqual(plan_asset_retention(catalog, maximum_retention_days=True)["status"], "unavailable")

        safe_label = "Safe [metadata] | <not-projected>"
        catalog_with_label = copy.deepcopy(catalog)
        catalog_with_label["label"] = safe_label
        catalog_with_label["assets"][0]["label"] = safe_label
        projection = json.dumps(build_asset_qa_report(catalog_with_label), sort_keys=True)
        self.assertNotIn(safe_label, projection)
        self.assertNotIn(safe_label, build_asset_qa_markdown(catalog_with_label))

    def test_pure_contract_calls_do_not_run_subprocesses(self) -> None:
        catalog = self.catalog()
        lineage = self.lineage()
        collection = self.collection()
        with patch.object(subprocess, "run") as run:
            self.assertTrue(validate_asset_catalog(catalog)["valid"])
            evaluate_smart_collection(collection, catalog)
            preflight_provenance_lineage(lineage, catalog)
            build_asset_qa_report(catalog)
            export_dataset_manifest(catalog, collection)
            plan_asset_retention(catalog)
            provider_capability_cards()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
