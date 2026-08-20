"""Bounded V5-B capability registry and Module Manager contract tests."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from src.services.api import core
from src.services.api.core import capability_control_plane
from src.services.module_manager import build_capability_registry, build_module_plan, plan_module_resources
from src.shared.schemas.module_manager import (
    CAPABILITY_REGISTRY_SCHEMA_VERSION,
    canonical_module_manager_json,
    capability_registry_schema,
    module_manager_schema,
    validate_capability_registry,
    validate_module_plan,
)


def _sources() -> dict[str, dict[str, object]]:
    return {
        key: {
            "status": "partial",
            "reason": "Static metadata only.",
            "action": "Review separately authorized evidence.",
            "records": [{"id": f"{key}-one", "version": "1.0.0", "status": "partial"}],
        }
        for key in ("extensions", "workflow_packages", "assets", "privacy", "capability_gateway")
    } | {
        "release_evidence": {
            "status": "not_published",
            "reason": "No packet was published for this snapshot.",
            "action": "Publish a manager-admitted packet before release claims.",
        }
    }


def _record(identifier: str, *, status: str = "available", dependencies: list[dict[str, object]] | None = None, observed: str = "observed", evidence: str = "static") -> dict[str, object]:
    return {
        "id": identifier,
        "provider": "fixture-provider",
        "component": identifier,
        "tool": None,
        "workflow": "fixture-workflow",
        "version": "1.0.0",
        "dependencies": dependencies or [],
        "observed": {"state": observed, "fingerprint": "a" * 64, "source": "fixture-provider"},
        "evidence": {"state": evidence, "fingerprint": "a" * 64},
        "resource_hints": {"gpu": {"required": True, "vendor": "nvidia", "device_class": "discrete", "vram_mb": 6000, "target": "RTX 4060"}},
        "status": status,
        "reason": "Static fixture evidence is bounded.",
        "next_action": "Review the static fixture before runtime work.",
        "source": "server_owned",
        "license": "fixture-license",
        "model_requirements": [],
    }


class V5CapabilityControlPlaneTests(unittest.TestCase):
    def test_registry_schema_parity_is_deterministic_and_redacted(self) -> None:
        first = build_capability_registry(sources=_sources())
        second = build_capability_registry(sources=_sources())
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], CAPABILITY_REGISTRY_SCHEMA_VERSION)
        self.assertEqual(first["execution"], "not_run")
        self.assertTrue(first["dry_run"])
        self.assertTrue(validate_capability_registry(first)["valid"])
        encoded = canonical_module_manager_json(first)
        for forbidden in ("C:\\private", "file:", "data:", "Bearer ", "sk-live", "command.exe"):
            self.assertNotIn(forbidden.lower(), encoded.lower())

    def test_untrusted_input_is_rejected_without_echo(self) -> None:
        marker = "sec" + "ret-marker"
        unsafe = _record("unsafe")
        unsafe["command"] = "powershell -enc never-run"
        unsafe["reason"] = f"C:\\private\\{marker}"
        registry = build_capability_registry(records=[unsafe])
        self.assertEqual(registry["records"], [])
        encoded = canonical_module_manager_json(registry)
        self.assertNotIn(marker, encoded)
        self.assertNotIn("powershell", encoded.lower())

    def test_stale_evidence_never_remains_operational(self) -> None:
        stale = _record("stale", status="operational", observed="stale", evidence="stale")
        changed = _record("changed", status="operational")
        changed["evidence"] = {"state": "static", "fingerprint": "b" * 64}
        registry = build_capability_registry(records=[stale, changed])
        statuses = {item["id"]: item["status"] for item in registry["records"]}
        self.assertEqual(statuses["stale"], "partial")
        self.assertEqual(statuses["changed"], "partial")

    def test_dependency_missing_and_cycle_are_reported(self) -> None:
        missing = _record("missing-root", dependencies=[{"id": "absent", "version": "1.0.0", "optional": False}])
        cycle_a = _record("cycle-a", dependencies=[{"id": "cycle-b", "version": "1.0.0", "optional": False}])
        cycle_b = _record("cycle-b", dependencies=[{"id": "cycle-a", "version": "1.0.0", "optional": False}])
        plan = build_module_plan([missing, cycle_a, cycle_b])
        self.assertEqual(plan["execution"], "not_run")
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["status"], "error")
        codes = {item["code"] for item in plan["errors"]}
        self.assertEqual(codes, {"missing_dependency", "dependency_cycle"})

    def test_physical_gpu_fit_is_distinct_from_concurrent_allocation(self) -> None:
        requests = [
            {"id": "one", "resource_hints": {"gpu": {"required": True, "vendor": "nvidia", "device_class": "discrete", "vram_mb": 6000}}},
            {"id": "two", "resource_hints": {"gpu": {"required": True, "vendor": "nvidia", "device_class": "discrete", "vram_mb": 6000}}},
        ]
        hardware = {"gpus": [{"id": "gpu-1", "vendor": "nvidia", "device_class": "discrete", "model": "RTX 4060", "vram_mb": 8192}]}
        parallel = plan_module_resources(requests, hardware=hardware, mode="parallel")
        serial = plan_module_resources(requests, hardware=hardware, mode="serial")
        self.assertEqual([item["status"] for item in parallel["physical"]], ["available", "available"])
        self.assertEqual(parallel["concurrent"][1]["status"], "partial")
        self.assertEqual([item["status"] for item in serial["concurrent"]], ["available", "available"])
        oversize = plan_module_resources([{"id": "huge", "resource_hints": {"gpu": {"required": True, "vendor": "nvidia", "device_class": "discrete", "vram_mb": 9000}}}], hardware=hardware)
        self.assertEqual(oversize["status"], "unavailable")

    def test_not_published_and_manifest_policy_remain_truthful(self) -> None:
        plan = build_module_plan([_record("unpublished")])
        self.assertEqual(plan["modules"][0]["status"], "not_published")
        unsafe_manifest = plan_module_resources(
            [{"id": "unsafe", "source_manifest": {"url": "http://example.invalid/pkg", "sha256": "a" * 64}}],
            hardware=None,
        )
        self.assertEqual(unsafe_manifest["status"], "unavailable")
        self.assertEqual(unsafe_manifest["errors"][0]["code"], "source_url_policy")
        missing_hash = plan_module_resources(
            [{"id": "missing-hash", "source_manifest": {"url": "https://example.invalid/pkg"}}],
            hardware=None,
        )
        self.assertEqual(missing_hash["errors"][0]["code"], "source_sha256_required")

    def test_control_plane_is_server_owned_dry_run_projection(self) -> None:
        snapshot = capability_control_plane(sources=_sources(), hardware=None, mode="serial")
        self.assertEqual(snapshot["schema_version"], "capability-control-plane.v1")
        self.assertEqual(snapshot["execution"], "not_run")
        self.assertTrue(snapshot["dry_run"])
        self.assertEqual(snapshot["module_manager"]["execution"], "not_run")
        self.assertEqual(snapshot["media_operation_scope"]["operations"], ["video_grade", "logo_overlay", "encode"])
        self.assertFalse(snapshot["media_operation_scope"]["evidence_verified"])
        detached = copy.deepcopy(snapshot)
        detached["registry"]["records"].clear()
        self.assertTrue(snapshot["registry"]["records"])

    def test_runtime_evidence_is_read_only_server_owned_and_preserves_control_plane_dry_run(self) -> None:
        safe_failure = {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": "media_overlay_cpu_acceptance",
            "status": "unavailable",
            "outcome": "error",
            "execution": "attempted",
            "failure_class": "unknown",
            "invocation_count": 1,
            "cleanup": {"processes_remaining": 0, "temp_cleaned": True},
            "artifact_published": False,
            "source_overwrite_checked": False,
            "source_overwritten": None,
            "reason": "The last bounded media acceptance stopped before a publishable output.",
            "next_action": "Keep media operations partial; request a new exact-source approval before any future attempt.",
        }
        with patch.object(core, "runtime_evidence_projection", return_value=safe_failure) as reader:
            snapshot = capability_control_plane(sources=_sources(), hardware=None, mode="serial")
        reader.assert_called_once_with()
        self.assertEqual(snapshot["execution"], "not_run")
        self.assertTrue(snapshot["dry_run"])
        self.assertEqual(snapshot["runtime_evidence"]["failure_class"], "unknown")
        self.assertEqual(snapshot["runtime_evidence"]["artifact_published"], False)
        self.assertNotIn("source", snapshot["runtime_evidence"])
        with patch.object(core, "runtime_evidence_passed", return_value=False):
            failed = core._tool_readiness("run_media_operation", {"ffmpeg": {"component_status": "installed"}})
        self.assertEqual(failed["tool_status"], "partial")
        with patch.object(core, "runtime_evidence_passed", return_value=True):
            completed = core._tool_readiness("run_media_operation", {"ffmpeg": {"component_status": "installed"}})
        self.assertEqual(completed["tool_status"], "partial")

    def test_completed_scoped_evidence_never_promotes_generic_or_unrelated_tools(self) -> None:
        from src.services import tool_smoke

        completed = tool_smoke._runtime_evidence_record({
            "status": "completed",
            "execution": "completed",
            "artifacts": {"encoded": {"id": "artifact_" + "a" * 32, "size_bytes": 10, "sha256": "a" * 64}},
            "processes_remaining": 0,
            "temp_cleaned": True,
            "source_overwritten": False,
        })
        scope = tool_smoke.runtime_evidence_operation_scope(completed)
        self.assertTrue(scope["evidence_verified"])
        self.assertEqual(scope["operations"], ["video_grade", "logo_overlay", "encode"])
        with (
            patch.object(core, "runtime_evidence_operation_scope", return_value=scope),
            patch.object(core, "runtime_evidence_passed", side_effect=AssertionError("generic readiness must not verify broad evidence")),
        ):
            generic = core._tool_readiness("run_media_operation", {
                "ffmpeg": {"component_status": "installed"},
            })
            unrelated = core._tool_readiness("upscale_anime_video", {
                "animesr": {"component_status": "installed"},
            })
            catalog = core.tool_catalog([
                {"id": "ffmpeg", "component_status": "installed"},
                {"id": "animesr", "component_status": "installed"},
            ])
        self.assertEqual(generic["tool_status"], "partial")
        self.assertEqual(generic["operation_scope"]["available_operations"], ["video_grade", "logo_overlay", "encode"])
        self.assertEqual(unrelated["tool_status"], "partial")
        catalog_by_name = {item["name"]: item for item in catalog}
        self.assertEqual(catalog_by_name["run_media_operation"]["tool_status"], "partial")
        self.assertEqual(catalog_by_name["upscale_anime_video"]["tool_status"], "partial")

    def test_module_plan_schema_validation(self) -> None:
        plan = build_module_plan([_record("schema-check")])
        self.assertTrue(validate_module_plan(plan)["valid"])

    def test_closed_schemas_cover_emitted_projection_keys(self) -> None:
        projections = (
            (build_capability_registry(sources=_sources()), capability_registry_schema()),
            (build_capability_registry(records=[{"id": "rejected"}]), capability_registry_schema()),
            (build_module_plan([_record("schema-check")]), module_manager_schema()),
            (
                build_module_plan([_record("missing", dependencies=[{"id": "absent", "version": "1.0.0", "optional": False}])]),
                module_manager_schema(),
            ),
        )
        for projection, schema in projections:
            self.assertFalse(schema["additionalProperties"])
            declared = set(schema["properties"])
            self.assertEqual(set(projection) - declared, set())
            self.assertTrue(set(schema["required"]).issubset(projection))


if __name__ == "__main__":
    unittest.main()
