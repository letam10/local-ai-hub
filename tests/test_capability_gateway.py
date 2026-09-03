"""Pure contract tests for the server-owned static capability gateway."""

from __future__ import annotations

import json
import socket
import subprocess
import unittest
from unittest.mock import patch

from src.services.capability_gateway import (
    TrustedSectionLoaderRegistry,
    build_capability_gateway,
    project_section,
)
from src.shared.schemas.capability_gateway import (
    CAPABILITY_GATEWAY_SCHEMA_VERSION,
    MAX_SELECTORS_PER_KEY,
    canonical_gateway_json,
    gateway_fingerprint,
    validate_gateway_request,
    validate_gateway_response,
)


SECTIONS = ("extensions", "workflow_packages", "assets", "privacy", "recipes")


def _loader(section: str):
    def load(selectors: tuple[str, ...], include_plans: bool) -> dict[str, object]:
        del selectors
        return {
            "status": "partial",
            "reason_code": "static_metadata_only",
            "action_code": "review_runtime_evidence",
            "records": [
                {
                    "id": f"{section}-one",
                    "version": "1.0.0",
                    "status": "partial",
                    "reason_code": "static_metadata_only",
                    "action_code": "review_runtime_evidence",
                    "type_summary": ["descriptor"],
                    "counts": {"items": 1},
                }
            ],
            "counts": {"items": 1},
            "type_summaries": ["descriptor"],
            "execution": "not_run",
            "dry_run": include_plans,
        }

    return load


def _registry() -> TrustedSectionLoaderRegistry:
    return TrustedSectionLoaderRegistry({section: _loader(section) for section in SECTIONS})


class CapabilityGatewayTests(unittest.TestCase):
    def test_valid_all_sections_are_deterministic_detached_and_redacted(self) -> None:
        request = {
            "schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION,
            "sections": list(reversed(SECTIONS)),
            "selectors": {},
            "include_plans": True,
        }
        first = build_capability_gateway(request, loaders=_registry())
        second = build_capability_gateway(request, loaders=_registry())

        self.assertEqual(first, second)
        self.assertEqual(first["execution"], "not_run")
        self.assertTrue(first["dry_run"])
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["fingerprint"]["value"], gateway_fingerprint({key: value for key, value in first.items() if key != "fingerprint"}))
        self.assertEqual(validate_gateway_response(first)["valid"], True)
        encoded = canonical_gateway_json(first)
        for forbidden in ("C:\\", "\\\\server", "/etc", "file:", "data:", "sk-", "Bearer ", "secret", "command"):
            self.assertNotIn(forbidden.lower(), encoded.lower())

        first["sections"]["extensions"]["records"][0]["id"] = "mutated"
        self.assertEqual(second["sections"]["extensions"]["records"][0]["id"], "extensions-one")

    def test_default_server_owned_snapshot_keeps_versioned_records_when_one_section_is_empty(self) -> None:
        from src.services.capability_gateway.defaults import build_server_owned_gateway_snapshot

        package_source = {
            "status": "partial",
            "records": [
                {"id": "local-ai-hub.image-review", "version": "1.0.0"},
                {"id": "local-ai-hub.image-review", "version": "1.1.0"},
            ],
        }
        empty_source = {"status": "partial", "records": []}
        with (
            patch("src.services.workflow_packages.discover_managed_packages", return_value=package_source),
            patch("src.services.extension_platform.discover_extensions", return_value=empty_source),
            patch("src.services.asset_intelligence.discover_managed_asset_catalogs", return_value=empty_source),
            patch("src.services.privacy_diagnostics.policy_catalog.discover_managed_privacy_policies", return_value=empty_source),
        ):
            snapshot = build_server_owned_gateway_snapshot(include_plans=True)

        self.assertEqual(snapshot["status"], "partial")
        package_records = snapshot["sections"]["workflow_packages"]["records"]
        self.assertEqual({(item["id"], item["version"]) for item in package_records}, {
            ("workflow_packages.local-ai-hub.image-review", "1.0.0"),
            ("workflow_packages.local-ai-hub.image-review", "1.1.0"),
        })

    def test_request_rejects_client_mapping_without_reflection(self) -> None:
        unsafe = {
            "schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION,
            "sections": ["extensions"],
            "selectors": {},
            "report": {"path": "C:\\private\\report.json", "sec" + "ret": "sk-live-never-echo"},
        }
        validation = validate_gateway_request(unsafe)
        self.assertFalse(validation["valid"])
        response = build_capability_gateway(unsafe, loaders=_registry())
        encoded = canonical_gateway_json(response)
        self.assertNotIn("private", encoded.lower())
        self.assertNotIn("sk-live", encoded.lower())
        self.assertNotIn("report", encoded.lower())

        # A raw mapping cannot be promoted to the trusted registry boundary.
        response = build_capability_gateway(
            {"schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION, "sections": ["extensions"]},
            loaders={"extensions": _loader("extensions")},  # type: ignore[arg-type]
        )
        self.assertEqual(response["status"], "unavailable")
        self.assertIn("trusted_loader_registry_required", {item["code"] for item in response["errors"]})

    def test_unknown_duplicate_ambiguous_and_bounded_selectors_fail_closed(self) -> None:
        base = {"schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION, "sections": ["extensions"], "selectors": {}}
        self.assertFalse(validate_gateway_request({**base, "sections": ["extensions", "extensions"]})["valid"])
        self.assertFalse(validate_gateway_request({**base, "selectors": {"extension_ids": ["one", "one"]}})["valid"])
        self.assertFalse(validate_gateway_request({**base, "selectors": {"unknown": ["one"]}})["valid"])
        too_many = {"extension_ids": [f"item-{index}" for index in range(MAX_SELECTORS_PER_KEY + 1)]}
        self.assertFalse(validate_gateway_request({**base, "selectors": too_many})["valid"])

        def duplicate_loader(selectors: tuple[str, ...], include_plans: bool) -> dict[str, object]:
            del selectors, include_plans
            return {"records": [{"id": "duplicate", "version": "1.0.0"}, {"id": "duplicate", "version": "1.0.0"}]}

        response = build_capability_gateway(base, loaders=TrustedSectionLoaderRegistry({"extensions": duplicate_loader}))
        self.assertEqual(response["status"], "unavailable")
        self.assertEqual(response["sections"]["extensions"]["reason_code"], "section_unavailable")

    def test_status_execution_and_dry_run_never_claim_runtime(self) -> None:
        def partial_loader(selectors: tuple[str, ...], include_plans: bool) -> dict[str, object]:
            del selectors
            return {
                "status": "partial",
                "records": [{"id": "static", "version": "1.0.0", "status": "operational"}],
                "execution": "not_run",
                "dry_run": include_plans,
            }

        response = build_capability_gateway(
            {"schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION, "sections": ["extensions"], "include_plans": True},
            loaders=TrustedSectionLoaderRegistry({"extensions": partial_loader}),
        )
        self.assertEqual(response["status"], "partial")
        self.assertEqual(response["execution"], "not_run")
        self.assertTrue(response["dry_run"])
        self.assertEqual(response["sections"]["extensions"]["execution"], "not_run")

        def executed_loader(selectors: tuple[str, ...], include_plans: bool) -> dict[str, object]:
            del selectors, include_plans
            return {"status": "operational", "execution": "executed", "records": []}

        rejected = build_capability_gateway(
            {"schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION, "sections": ["extensions"]},
            loaders=TrustedSectionLoaderRegistry({"extensions": executed_loader}),
        )
        self.assertEqual(rejected["status"], "unavailable")
        self.assertEqual(rejected["execution"], "not_run")

    def test_projection_rejects_raw_sensitive_payloads(self) -> None:
        payloads = (
            {"path": "C:\\private\\model.safetensors"},
            {"sec" + "ret": "never-echo"},
            {"command": "cmd.exe /c whoami"},
            {"host": "workstation", "user": "alice", "environment": "prod"},
            {"media": "data:image/png;base64,AAAA", "model": "weights.bin"},
        )
        for payload in payloads:
            result = project_section(
                "extensions",
                {"status": "partial", "records": [{"id": "safe", "version": "1.0.0", **payload}]},
            )
            self.assertEqual(result["status"], "unavailable")
            encoded = canonical_gateway_json(result)
            self.assertNotIn("private", encoded.lower())
            self.assertNotIn("never-echo", encoded.lower())
            self.assertNotIn("whoami", encoded.lower())
            self.assertNotIn("workstation", encoded.lower())
            self.assertNotIn("base64", encoded.lower())

    def test_strict_json_parser_rejects_duplicate_and_nonfinite_values(self) -> None:
        duplicate = '{"schema_version":"capability-gateway.v1","sections":["extensions"],"sections":["assets"]}'
        self.assertFalse(validate_gateway_request(duplicate)["valid"])
        nonfinite = '{"schema_version":"capability-gateway.v1","sections":["extensions"],"include_plans":NaN}'
        self.assertFalse(validate_gateway_request(nonfinite)["valid"])

    def test_no_runtime_hooks_are_called(self) -> None:
        with patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess must not run")) as popen, patch.object(socket, "socket", side_effect=AssertionError("socket must not run")) as socket_ctor:
            result = build_capability_gateway(
                {"schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION, "sections": ["extensions"]},
                loaders=_registry(),
            )
        self.assertEqual(result["execution"], "not_run")
        popen.assert_not_called()
        socket_ctor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
