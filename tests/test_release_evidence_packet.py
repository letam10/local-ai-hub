"""Focused static contract tests for release-evidence-packet.v1."""

from __future__ import annotations

import copy
import json
import socket
import subprocess
import unittest
from unittest.mock import patch

from src.services.release_evidence import (
    build_release_evidence_packet,
    canonical_packet_json,
    project_json,
    project_markdown,
    validate_packet,
)
from src.services.release_evidence.packet import parse_release_evidence_packet
from src.shared.schemas.release_evidence_packet import (
    release_evidence_packet_fingerprint,
    release_evidence_packet_schema,
)


class ReleaseEvidencePacketTests(unittest.TestCase):
    @staticmethod
    def identity() -> dict[str, object]:
        return {
            "candidate_branch": "feature/candidate",
            "candidate_ref": "refs/heads/candidate",
            "base_ref": "refs/heads/base",
            "base_sha": "a" * 64,
            "head_sha": "b" * 64,
            "merge_base_sha": "c" * 64,
            "lane_or_candidate": "m16c",
            "owner": "qa",
            "changed_file_summary": {
                "added": 1,
                "modified": 0,
                "deleted": 0,
                "total": 1,
                "scope_fingerprint": "d" * 64,
            },
            "toolchain": {
                "git": "git-2",
                "python": "python-3",
                "validator": "schema-1",
                "ci": "ci-1",
            },
        }

    @staticmethod
    def commands() -> list[dict[str, object]]:
        return [
            {
                "label": "diff_check",
                "exit_status": 0,
                "duration_class": "instant",
                "expected_skips": [],
                "deterministic_summary": "clean",
            },
            {
                "label": "ci_validate",
                "exit_status": 0,
                "duration_class": "short",
                "expected_skips": [],
                "deterministic_summary": "pass",
            },
        ]

    def valid_packet(self) -> dict[str, object]:
        result = build_release_evidence_packet(self.identity(), self.commands())
        self.assertTrue(result["valid"], result["errors"])
        packet = result["packet"]
        assert isinstance(packet, dict)
        return packet

    def test_valid_packet_is_detached_canonical_and_deterministic(self) -> None:
        identity = self.identity()
        commands = self.commands()
        result = build_release_evidence_packet(identity, commands)
        self.assertTrue(result["valid"], result["errors"])
        packet = result["packet"]
        assert isinstance(packet, dict)
        self.assertEqual(result["fingerprint"], release_evidence_packet_fingerprint(packet))
        self.assertEqual(packet["verdict"]["operational"], "not_run")
        original_packet = copy.deepcopy(packet)
        packet["identity"]["owner"] = "changed"
        self.assertEqual(identity["owner"], "qa")

        reordered = build_release_evidence_packet(self.identity(), list(reversed(self.commands())))
        self.assertTrue(reordered["valid"], reordered["errors"])
        self.assertEqual(result["fingerprint"], reordered["fingerprint"])
        self.assertEqual(canonical_packet_json(original_packet), canonical_packet_json(reordered["packet"]))

    def test_schema_is_closed_and_required_fields_have_properties(self) -> None:
        schema = release_evidence_packet_schema()

        def walk(value: object) -> None:
            if not isinstance(value, dict):
                if isinstance(value, list):
                    for child in value:
                        walk(child)
                return
            required = value.get("required")
            if isinstance(required, list):
                properties = value.get("properties")
                self.assertIsInstance(properties, dict)
                self.assertTrue(set(required).issubset(properties))
            if value.get("type") == "object":
                self.assertFalse(value.get("additionalProperties", True))
            for child in value.values():
                walk(child)

        walk(schema)

    def test_unsafe_and_client_controlled_values_fail_without_reflection(self) -> None:
        unsafe_identity = self.identity()
        unsafe_identity["candidate_ref"] = "C:" + "\\private"
        rejected = build_release_evidence_packet(unsafe_identity, self.commands())
        self.assertFalse(rejected["valid"])
        self.assertNotIn("private", json.dumps(rejected))

        secret_identity = self.identity()
        secret_identity["owner"] = "api" + "_key=hidden"
        rejected_secret = build_release_evidence_packet(secret_identity, self.commands())
        self.assertFalse(rejected_secret["valid"])
        self.assertNotIn("hidden", json.dumps(rejected_secret))

        client_identity = self.identity()
        client_identity["report_mapping"] = "client"
        rejected_client = build_release_evidence_packet(client_identity, self.commands())
        self.assertFalse(rejected_client["valid"])
        self.assertNotIn("client", json.dumps(rejected_client))

    def test_invalid_counts_identity_status_command_and_skip_fail_closed(self) -> None:
        packet = self.valid_packet()

        bad_count = copy.deepcopy(packet)
        bad_count["identity"]["changed_file_summary"]["added"] = -1
        self.assertFalse(validate_packet(bad_count)["valid"])

        bad_identity = copy.deepcopy(packet)
        bad_identity["identity"]["head_sha"] = ""
        self.assertFalse(validate_packet(bad_identity)["valid"])

        bad_status = copy.deepcopy(packet)
        bad_status["verdict"]["static"] = "operational"
        self.assertFalse(validate_packet(bad_status)["valid"])

        bad_command = copy.deepcopy(packet)
        bad_command["commands"][0]["raw_command"] = "not accepted"
        self.assertFalse(validate_packet(bad_command)["valid"])

        bad_skip = copy.deepcopy(packet)
        bad_skip["commands"][0]["expected_skips"] = ["unknown_skip"]
        self.assertFalse(validate_packet(bad_skip)["valid"])

    def test_operational_status_cannot_be_inflated_without_named_evidence(self) -> None:
        verdict = {
            "static": "pass",
            "operational": "operational",
            "provenance": "server_owned_validated",
            "runtime_smoke": "completed",
        }
        rejected = build_release_evidence_packet(self.identity(), self.commands(), verdict)
        self.assertFalse(rejected["valid"])
        self.assertIn("runtime_claim_unauthorized", {item["code"] for item in rejected["errors"]})

        valid = self.valid_packet()
        self.assertEqual(valid["verdict"]["static"], "pass")
        self.assertNotEqual(valid["verdict"]["operational"], "operational")

    def test_redacted_json_and_markdown_projections_are_deterministic(self) -> None:
        packet = self.valid_packet()
        reordered = copy.deepcopy(packet)
        reordered["commands"].reverse()
        json_one = project_json(packet)
        json_two = project_json(reordered)
        self.assertTrue(json_one["ready"], json_one["errors"])
        self.assertTrue(json_two["ready"], json_two["errors"])
        self.assertEqual(json_one["content"], json_two["content"])
        markdown_one = project_markdown(packet)
        markdown_two = project_markdown(reordered)
        self.assertTrue(markdown_one["ready"], markdown_one["errors"])
        self.assertEqual(markdown_one["content"], markdown_two["content"])
        rendered = json_one["content"].decode("utf-8") + markdown_one["content"].decode("utf-8")
        for forbidden in ("raw_command", "C:\\", "api_key", "password", "secret", "Bearer "):
            self.assertNotIn(forbidden, rendered)

    def test_parser_rejects_duplicate_nonfinite_invalid_utf8_and_oversize(self) -> None:
        duplicate = parse_release_evidence_packet(b'{"contract":"release-evidence-packet.v1","contract":"bad"}')
        self.assertFalse(duplicate["valid"])
        self.assertEqual(duplicate["errors"][0]["code"], "duplicate_json_key")
        self.assertFalse(parse_release_evidence_packet(b'{"value":NaN}')["valid"])
        self.assertFalse(parse_release_evidence_packet(b"\xff")["valid"])
        self.assertFalse(parse_release_evidence_packet(b"a" * (256 * 1024 + 1))["valid"])

    def test_admission_transition_is_detached_and_fixed(self) -> None:
        packet = self.valid_packet()
        with patch("src.services.release_evidence.packet.copy.deepcopy", wraps=copy.deepcopy) as copier:
            from src.services.release_evidence import admit_release_evidence_packet

            admitted = admit_release_evidence_packet(packet, "qa-review-1")
        self.assertTrue(admitted["valid"], admitted["errors"])
        self.assertEqual(admitted["packet"]["admission"]["status"], "admitted")
        self.assertNotEqual(packet["admission"]["status"], "admitted")
        self.assertTrue(copier.called)

    def test_static_core_has_no_runtime_side_effects(self) -> None:
        packet = self.valid_packet()
        with patch.object(subprocess, "run") as run, patch.object(socket, "socket") as socket_factory, patch("builtins.open") as open_file:
            self.assertTrue(project_json(packet)["ready"])
            self.assertTrue(project_markdown(packet)["ready"])
            run.assert_not_called()
            socket_factory.assert_not_called()
            open_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
