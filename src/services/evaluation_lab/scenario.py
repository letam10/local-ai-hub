"""Human A/B rubric planning with no benchmark or workflow execution."""

from __future__ import annotations

import copy
import json
from typing import Any

from src.shared.schemas.workflow_package import MAX_PACKAGE_BYTES, validate_evaluation_scenario
from src.services.workflow_packages.io import _DuplicateJsonKey, _duplicate_key_guard, _non_finite_number, validated_package_result


def _failure(code: str, reason: str, action: str) -> dict[str, Any]:
    return {
        "accepted": False,
        "status": "unavailable",
        "reason": reason,
        "action": action,
        "errors": [{"code": code}],
        "scenario": None,
    }


def safe_import_evaluation_scenario(payload: object) -> dict[str, Any]:
    """Accept only bounded UTF-8 JSON; no file or execution handle is accepted."""

    if isinstance(payload, str):
        try:
            raw = payload.encode("utf-8", errors="strict")
        except UnicodeError:
            return _failure("utf8", "Scenario text is not valid UTF-8.", "Export a UTF-8 JSON evaluation scenario and try again.")
    elif isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
    else:
        return _failure("payload_type", "Scenario import accepts only UTF-8 JSON text or bytes.", "Provide a static JSON scenario, not an object or file handle.")
    if not raw or len(raw) > MAX_PACKAGE_BYTES:
        return _failure("payload_size", "Scenario payload is empty or exceeds the static size bound.", "Keep the descriptor below the documented size limit.")
    try:
        text = raw.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            return _failure("bom", "Scenario JSON must not include a byte-order marker.", "Re-export the descriptor as plain UTF-8 JSON.")
        value = json.loads(text, object_pairs_hook=_duplicate_key_guard, parse_constant=_non_finite_number)
    except _DuplicateJsonKey:
        return _failure("duplicate_json_key", "Scenario JSON contains duplicate object keys.", "Use unique keys in every scenario object.")
    except UnicodeDecodeError:
        return _failure("utf8", "Scenario text is not valid UTF-8.", "Export a UTF-8 JSON evaluation scenario and try again.")
    except ValueError:
        return _failure("json_parse", "Scenario payload is not a supported static JSON document.", "Export a valid evaluation-scenario.v1 JSON descriptor.")
    validation = validate_evaluation_scenario(value)
    if not validation["valid"]:
        return {
            "accepted": False,
            "status": "unavailable",
            "reason": "Scenario failed static validation and was not imported.",
            "action": "Correct the reported contract codes, then validate again.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in validation["errors"]],
            "scenario": None,
        }
    return {
        "accepted": True,
        "status": "planned",
        "reason": "Scenario passed static validation; no score, benchmark, or workflow was executed.",
        "action": "Use it only to organize a human A/B review.",
        "errors": [],
        "scenario": copy.deepcopy(validation["scenario"]),
        "fingerprint": validation["fingerprint"],
        "execution": "not_run",
    }


def _validated_scenario(value: object) -> dict[str, Any]:
    candidate: object = value
    if isinstance(value, dict) and value.get("accepted") is True and isinstance(value.get("scenario"), dict):
        candidate = value["scenario"]
    elif isinstance(value, dict) and value.get("valid") is True and isinstance(value.get("scenario"), dict):
        candidate = value["scenario"]
    return validate_evaluation_scenario(candidate)


def build_human_ab_plan(scenario_value: object, package_value: object = None) -> dict[str, Any]:
    """Build a rubric-only plan; callers cannot use it to run an A/B benchmark."""

    scenario_validation = _validated_scenario(scenario_value)
    if not scenario_validation["valid"]:
        return {
            "valid": False,
            "status": "unavailable",
            "reason": "Evaluation scenario failed static validation.",
            "action": "Correct the scenario contract errors before creating a human review plan.",
            "errors": [{"code": item["code"], "location": item["location"]} for item in scenario_validation["errors"]],
            "execution": "not_run",
        }
    scenario = scenario_validation["scenario"]
    assert isinstance(scenario, dict)
    status = "partial"
    reason = "Scenario is ready for a human-only rubric review; package linkage was not supplied."
    action = "Supply a server-owned validated package result to verify the static package reference."
    package_fingerprint = None
    if package_value is not None:
        package_validation = validated_package_result(package_value)
        if not package_validation["valid"]:
            return {
                "valid": False,
                "status": "unavailable",
                "reason": "Package linkage failed static validation.",
                "action": "Pass a server-owned validated workflow package result.",
                "errors": [{"code": item["code"], "location": item["location"]} for item in package_validation["errors"]],
                "execution": "not_run",
            }
        package = package_validation["package"]
        assert isinstance(package, dict)
        if package["id"] != scenario["package"]["id"] or package["version"] != scenario["package"]["version"]:
            return {
                "valid": False,
                "status": "unavailable",
                "reason": "Scenario package reference does not match the supplied validated package.",
                "action": "Use the exact package ID and version declared by the human review scenario.",
                "errors": [{"code": "scenario_package_mismatch"}],
                "execution": "not_run",
            }
        status = "planned"
        reason = "Scenario and package linkage passed static validation; human review remains required."
        action = "Assign the rubric to a human reviewer; do not infer runtime quality from this plan."
        package_fingerprint = package_validation["fingerprint"]
    return {
        "valid": True,
        "status": status,
        "reason": reason,
        "action": action,
        "scenario": {
            "id": scenario["id"],
            "fingerprint": scenario_validation["fingerprint"],
            "package": copy.deepcopy(scenario["package"]),
            "candidate_ids": [item["id"] for item in sorted(scenario["candidates"], key=lambda item: item["id"])],
            "rubric": [{"id": item["id"], "weight": item["weight"]} for item in sorted(scenario["rubric"], key=lambda item: item["id"])],
        },
        "package_fingerprint": package_fingerprint,
        "human_review_required": True,
        "execution": "not_run",
    }
