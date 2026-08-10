"""Deterministic recipe linting, composition and static compatibility planning."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from src.shared.schemas.creative_recipes import (
    CAPABILITIES,
    MAX_FINDINGS,
    MAX_VARIANT_PLANS,
    MEDIA_KINDS,
    MODEL_FAMILIES,
    RECIPE_ID_RE,
    SLOT_TYPES,
    TARGET_ID_RE,
    canonical_generation_intent_json,
    validate_compatibility_report,
    validate_generation_intent,
    validate_lint_finding,
    validate_recipe,
    validate_target_card,
)

from .catalog import load_catalog_index


_PLACEHOLDER_RE = re.compile(r"\{\{([a-z0-9_-]+)\}\}")
_UNSAFE_VALUE_RE = re.compile(r"(?:[A-Za-z]:[\\/]|[A-Za-z]:[A-Za-z0-9_.-]|\\\\|\\[A-Za-z]|/(?:etc|usr|var|tmp|home)(?:/|$)|\.\.[\\/]|https?://|file:|data:|javascript:|api[_-]?key\s*[:=]|secret\s*[:=]|token\s*[:=]|password\s*[:=]|credential\s*[:=]|bearer\s+[A-Za-z0-9._-]{8,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|[A-Za-z0-9+/]{96,}={0,2}|<\s*/?\s*(?:script|iframe|img|svg|a)\b|cmd\.exe|powershell|bash\s+-c|python\s+-c|`)", re.IGNORECASE)

_REASONS = {
    "missing_required_slot": "A required prompt slot has no safe value or default.",
    "unknown_slot_value": "Input contains a slot key that is not declared by the recipe.",
    "slot_type_mismatch": "A slot value does not match its declared type, enum or bounds.",
    "variant_unknown": "The requested prompt variant is not declared by the recipe.",
    "style_pack_missing": "A referenced style pack is not uniquely available in the managed catalog.",
    "style_media_mismatch": "A referenced style pack does not support the recipe media kind.",
    "unsupported_target": "The requested static target is missing, ambiguous or has another media kind.",
    "unsupported_capability": "The target does not declare every capability required by the recipe.",
    "unsupported_model_family": "The target and recipe have no compatible declared model family.",
    "constraint_conflict": "Requested parameters conflict with the recipe's declared bounds.",
    "text_limit": "Composed prompt text exceeds the closed static bound.",
    "unsafe_text": "Prompt text contains a path, URL, secret, command or payload marker.",
    "unresolved_placeholder": "Prompt composition left an undeclared placeholder.",
    "target_unverified": "Target metadata is static only and has not been runtime verified.",
}

_ACTIONS = {
    "missing_required_slot": "Supply the missing slot through a future UI/API DTO before planning.",
    "unknown_slot_value": "Remove undeclared keys and use only the recipe slot IDs.",
    "slot_type_mismatch": "Use the slot's declared type, enum and numeric bounds.",
    "variant_unknown": "Select a declared variant or omit the variant override.",
    "style_pack_missing": "Load a unique managed style pack or remove the reference.",
    "style_media_mismatch": "Choose a style pack that supports the recipe media kind.",
    "unsupported_target": "Choose a declared target card matching the recipe media kind.",
    "unsupported_capability": "Choose a target with the required capability allowlist.",
    "unsupported_model_family": "Choose a target with a compatible declared model family.",
    "constraint_conflict": "Adjust parameters within the recipe's static constraints.",
    "text_limit": "Shorten the prompt or split it into a separately reviewed recipe.",
    "unsafe_text": "Remove path/secret/command/payload text before planning.",
    "unresolved_placeholder": "Provide a declared slot value for every placeholder.",
    "target_unverified": "Keep the intent planned and request a separately authorized bounded smoke.",
}


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _failure(code: str, *, reason: str | None = None, action: str | None = None) -> dict[str, Any]:
    return {"valid": False, "status": "unavailable", "reason": reason or _REASONS.get(code, "Static recipe planning input failed closed validation."), "action": action or _ACTIONS.get(code, "Use only validated closed recipe metadata."), "errors": [{"code": code}], "findings": [], "intent": None, "execution": "not_run"}


def _finding(code: str, subject_kind: str, subject_id: str, *, severity: str = "error", status: str = "unavailable", evidence_count: int = 1) -> dict[str, Any]:
    safe_subject = subject_id if isinstance(subject_id, str) and len(subject_id) <= 80 and not _UNSAFE_VALUE_RE.search(subject_id) else "unknown"
    safe_kind = subject_kind if subject_kind in {"catalog", "recipe", "slot", "variant", "style_pack", "target", "compatibility"} else "recipe"
    finding_id = f"finding_{code}_{safe_kind}_{safe_subject}".replace("-", "_")
    if len(finding_id) > 64 or not re.fullmatch(r"finding_[a-z0-9_-]{1,55}", finding_id):
        finding_id = f"finding_{code}_{_digest(safe_subject)[:20]}"
    value = {"schema_version": "creative-recipe-lint-finding.v1", "id": finding_id, "code": code, "severity": severity, "status": status, "subject_kind": safe_kind, "subject_id": safe_subject, "reason_code": code, "reason": _REASONS.get(code, "Static recipe lint requires review."), "action_code": _action_code(code), "action": _ACTIONS.get(code, "Review the closed recipe contract."), "evidence_count": max(0, min(100000, int(evidence_count)))}
    validation = validate_lint_finding(value)
    return validation["finding"] if validation.get("valid") else value


def _action_code(code: str) -> str:
    mapping = {
        "missing_required_slot": "supply_slot_value",
        "unknown_slot_value": "remove_unknown_slot",
        "slot_type_mismatch": "correct_slot_type",
        "variant_unknown": "select_declared_variant",
        "style_pack_missing": "resolve_style_pack",
        "style_media_mismatch": "choose_media_style",
        "unsupported_target": "select_target",
        "unsupported_capability": "select_capability_target",
        "unsupported_model_family": "select_model_target",
        "constraint_conflict": "adjust_parameters",
        "text_limit": "shorten_prompt",
        "unsafe_text": "scrub_prompt",
        "unresolved_placeholder": "supply_slot_value",
        "target_unverified": "review_runtime_evidence",
    }
    return mapping.get(code, "review_recipe")


def _summary(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {"findings": len(findings), "errors": sum(1 for item in findings if item.get("severity") == "error"), "warnings": sum(1 for item in findings if item.get("severity") == "warning"), "info": sum(1 for item in findings if item.get("severity") == "info")}


def _catalog_maps(catalog: object | None) -> tuple[dict[str, tuple[dict[str, Any], str]], dict[str, tuple[dict[str, Any], str]], dict[str, tuple[dict[str, Any], str]], list[dict[str, str]]]:
    if catalog is None:
        index = load_catalog_index()
        return copy.deepcopy(index["recipes"]), copy.deepcopy(index["style_packs"]), copy.deepcopy(index["targets"]), copy.deepcopy(index.get("errors", []))
    if isinstance(catalog, Mapping) and isinstance(catalog.get("catalog"), dict):
        catalog = catalog["catalog"]
    if isinstance(catalog, Mapping) and all(key in catalog for key in ("recipes", "style_packs", "targets")) and all(isinstance(catalog.get(key), dict) for key in ("recipes", "style_packs", "targets")):
        # Accept only a detached server-owned index shape.  Revalidate every
        # entity and recompute its fingerprint instead of trusting a caller's
        # tuple or digest fields.
        from src.shared.schemas.creative_recipes import validate_style_pack

        supplied_errors = catalog.get("errors")
        if isinstance(supplied_errors, list) and supplied_errors:
            return {}, {}, {}, [{"code": item.get("code", "catalog_index_invalid")} for item in supplied_errors if isinstance(item, Mapping)][:16] or [{"code": "catalog_index_invalid"}]

        def _index_entities(raw: object, validator: Any, identity_prefix: str) -> tuple[dict[str, tuple[dict[str, Any], str]], list[dict[str, str]]]:
            if not isinstance(raw, Mapping):
                return {}, [{"code": "catalog_index_invalid"}]
            result: dict[str, tuple[dict[str, Any], str]] = {}
            errors: list[dict[str, str]] = []
            for identity, pair in raw.items():
                if not isinstance(identity, str) or not isinstance(pair, (tuple, list)) or len(pair) != 2:
                    errors.append({"code": "catalog_index_invalid"})
                    continue
                checked = validator(pair[0])
                if not checked.get("valid") or not isinstance(checked.get(identity_prefix), dict):
                    errors.append({"code": "catalog_index_invalid"})
                    continue
                entity = checked[identity_prefix]
                expected = f"{entity['id']}@{entity['version']}"
                if identity != expected or identity in result:
                    errors.append({"code": "catalog_index_ambiguous"})
                    continue
                result[identity] = (copy.deepcopy(entity), checked["fingerprint"])
            return result, errors

        recipes, recipe_errors = _index_entities(catalog.get("recipes"), validate_recipe, "recipe")
        styles, style_errors = _index_entities(catalog.get("style_packs"), validate_style_pack, "style_pack")
        targets, target_errors = _index_entities(catalog.get("targets"), validate_target_card, "target")
        index_errors = recipe_errors + style_errors + target_errors
        if index_errors:
            return {}, {}, {}, index_errors[:16]
        return recipes, styles, targets, []
    if isinstance(catalog, Mapping) and all(key in catalog for key in ("recipes", "style_packs", "targets")):
        from src.shared.schemas.creative_recipes import validate_recipe_catalog

        validation = validate_recipe_catalog(catalog)
        if not validation.get("valid"):
            return {}, {}, {}, [{"code": item.get("code", "catalog_invalid")} for item in validation.get("errors", [])]
        normalized = validation["catalog"]
        fingerprint = validation["fingerprint"]
        recipes = {f"{item['id']}@{item['version']}": (item, fingerprint) for item in normalized["recipes"]}
        styles = {f"{item['id']}@{item['version']}": (item, fingerprint) for item in normalized["style_packs"]}
        targets = {f"{item['id']}@{item['version']}": (item, fingerprint) for item in normalized["targets"]}
        return recipes, styles, targets, []
    return {}, {}, {}, [{"code": "catalog_required"}]


def _entity_by_id(values: dict[str, tuple[dict[str, Any], str]], entity_id: str) -> tuple[dict[str, Any], str] | None:
    matches = [value for identity, value in values.items() if identity.startswith(f"{entity_id}@")]
    return matches[0] if len(matches) == 1 else None


def _slot_value_valid(slot: dict[str, Any], value: object) -> bool:
    slot_type = slot["type"]
    if slot_type in {"text", "style", "aspect_ratio", "enum"}:
        if not isinstance(value, str) or _UNSAFE_VALUE_RE.search(value) or len(value) > 800:
            return False
        if slot_type == "aspect_ratio" and not re.fullmatch(r"[1-9][0-9]{0,1}:[1-9][0-9]{0,1}", value):
            return False
        if slot_type == "enum" and value not in slot.get("enum", []):
            return False
        return True
    if slot_type == "boolean":
        return isinstance(value, bool)
    if slot_type == "seed":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 4294967295
    if slot_type == "duration":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value != float("inf") and 0 <= value <= 3600
    if slot_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool) and (slot.get("minimum") is None or value >= slot["minimum"]) and (slot.get("maximum") is None or value <= slot["maximum"])
    if slot_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value != float("inf") and value != float("-inf") and (slot.get("minimum") is None or value >= slot["minimum"]) and (slot.get("maximum") is None or value <= slot["maximum"])
    return False


def _parameter_findings(recipe: dict[str, Any], parameters: object, subject_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if parameters is None:
        raw: dict[str, Any] = {}
    elif isinstance(parameters, Mapping):
        raw = dict(parameters)
    else:
        return {}, [_finding("constraint_conflict", "recipe", subject_id)]
    allowed = {"width", "height", "steps", "seed", "fps", "duration_seconds", "frames", "aspect_ratio"}
    findings: list[dict[str, Any]] = []
    if any(key not in allowed for key in raw):
        findings.append(_finding("constraint_conflict", "recipe", subject_id))
    params: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed:
            continue
        if key in {"width", "height", "steps", "seed", "frames"} and (not isinstance(value, int) or isinstance(value, bool)):
            findings.append(_finding("constraint_conflict", "recipe", subject_id))
            continue
        if key in {"fps", "duration_seconds"} and (not isinstance(value, (int, float)) or isinstance(value, bool) or value != value or value in (float("inf"), float("-inf"))):
            findings.append(_finding("constraint_conflict", "recipe", subject_id))
            continue
        if key == "aspect_ratio" and (not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,1}:[1-9][0-9]{0,1}", value)):
            findings.append(_finding("constraint_conflict", "recipe", subject_id))
            continue
        params[key] = value
    constraints = recipe["constraints"]
    for key, low, high in (("width", constraints.get("min_width"), constraints.get("max_width")), ("height", constraints.get("min_height"), constraints.get("max_height")), ("frames", None, constraints.get("max_frames")), ("duration_seconds", None, constraints.get("max_duration_seconds")), ("fps", None, constraints.get("max_fps"))):
        if key in params and low is not None and params[key] < low or key in params and high is not None and params[key] > high:
            findings.append(_finding("constraint_conflict", "recipe", subject_id))
    if "aspect_ratio" in params and constraints.get("aspect_ratios") and params["aspect_ratio"] not in constraints["aspect_ratios"]:
        findings.append(_finding("constraint_conflict", "recipe", subject_id))
    return params, findings


def _compatibility(recipe: dict[str, Any], target_id: object, targets: dict[str, tuple[dict[str, Any], str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    target_text = target_id if isinstance(target_id, str) else "target_invalid"
    target = _entity_by_id(targets, target_text) if isinstance(target_id, str) else None
    checks: list[dict[str, Any]] = []
    if target is None:
        finding = _finding("unsupported_target", "target", target_text)
        findings.append(finding)
        checks.append({"code": "target_declared", "status": "unavailable", "reason_code": "unsupported_target", "action_code": _action_code("unsupported_target")})
        report_status = "unavailable"
        missing: list[str] = []
    else:
        card = target[0]
        if card["media_kind"] != recipe["media_kind"]:
            findings.append(_finding("unsupported_target", "target", card["id"]))
            checks.append({"code": "media_kind", "status": "unavailable", "reason_code": "unsupported_target", "action_code": _action_code("unsupported_target")})
        else:
            checks.append({"code": "media_kind", "status": "partial", "reason_code": "target_unverified", "action_code": _action_code("target_unverified")})
        declared_targets = recipe["compatibility"]["target_ids"]
        if declared_targets and card["id"] not in declared_targets:
            findings.append(_finding("unsupported_target", "target", card["id"]))
            checks.append({"code": "target_declared", "status": "unavailable", "reason_code": "unsupported_target", "action_code": _action_code("unsupported_target")})
        else:
            checks.append({"code": "target_declared", "status": "partial", "reason_code": "target_unverified", "action_code": _action_code("target_unverified")})
        required_caps = set(recipe["compatibility"]["required_capabilities"])
        missing = sorted(required_caps - set(card["capabilities"]))
        if missing:
            findings.append(_finding("unsupported_capability", "target", card["id"], evidence_count=len(missing)))
            checks.append({"code": "capabilities", "status": "unavailable", "reason_code": "unsupported_capability", "action_code": _action_code("unsupported_capability")})
        else:
            checks.append({"code": "capabilities", "status": "partial", "reason_code": "target_unverified", "action_code": _action_code("target_unverified")})
        required_models = set(recipe["compatibility"]["model_families"])
        if required_models and not required_models.intersection(card["model_families"]):
            findings.append(_finding("unsupported_model_family", "target", card["id"]))
            checks.append({"code": "model_family", "status": "unavailable", "reason_code": "unsupported_model_family", "action_code": _action_code("unsupported_model_family")})
        else:
            checks.append({"code": "model_family", "status": "partial", "reason_code": "target_unverified", "action_code": _action_code("target_unverified")})
        if any(item["severity"] == "error" for item in findings):
            report_status = "unavailable"
        elif card["status"] == "unavailable":
            findings.append(_finding("target_unverified", "target", card["id"], severity="warning", status="manual_review"))
            report_status = "unavailable"
        else:
            report_status = "partial"
    reason_code = "compatibility_ready" if not findings else findings[0]["code"]
    action_code = "review_compatibility" if not findings else findings[0]["action_code"]
    report = {"schema_version": "creative-compatibility-report.v1", "id": f"report_{_digest({'recipe': recipe['id'], 'version': recipe['version'], 'target': target_text})[:48]}", "recipe_id": recipe["id"], "recipe_version": recipe["version"], "target_id": target_text if isinstance(target_text, str) and TARGET_ID_RE.fullmatch(target_text) else "target_invalid", "status": report_status, "reason_code": reason_code, "reason": "Static target compatibility was checked without probing a model or runtime." if not findings else _REASONS.get(reason_code, "Static compatibility requires review."), "action_code": action_code, "action": "Keep execution not_run and request a separately authorized bounded smoke." if not findings else _ACTIONS.get(reason_code, "Review the static compatibility checks."), "missing_capabilities": missing, "checks": checks, "execution": "not_run"}
    validation = validate_compatibility_report(report)
    if not validation.get("valid"):
        return {"valid": False, "status": "unavailable", "reason": "Static compatibility report failed its closed invariant.", "action": "Keep the target and recipe contracts within the published bounds.", "errors": [{"code": item.get("code", "report_invalid")} for item in validation.get("errors", [])], "report": None, "execution": "not_run"}, findings
    return {"valid": True, "status": report_status, "reason": report["reason"], "action": report["action"], "errors": [], "report": validation["report"], "fingerprint": validation["fingerprint"], "execution": "not_run"}, findings


def build_compatibility_report(recipe: object, target_id: object, *, catalog: object | None = None) -> dict[str, Any]:
    recipe_validation = validate_recipe(recipe)
    if not recipe_validation.get("valid"):
        return {"valid": False, "status": "unavailable", "reason": "Recipe failed closed validation before compatibility planning.", "action": "Use a validated recipe descriptor.", "errors": [{"code": item.get("code", "recipe_invalid")} for item in recipe_validation.get("errors", [])], "report": None, "execution": "not_run"}
    recipes, styles, targets, catalog_errors = _catalog_maps(catalog)
    if catalog_errors:
        return {"valid": False, "status": "unavailable", "reason": "Managed catalog is unavailable or ambiguous.", "action": "Fix the fixed-root catalog before compatibility planning.", "errors": catalog_errors[:8], "report": None, "execution": "not_run"}
    report, _ = _compatibility(recipe_validation["recipe"], target_id, targets)
    return report


def lint_recipe(recipe: object, values: object | None = None, *, catalog: object | None = None, target_id: object | None = None, variant_id: object | None = None, parameters: object | None = None) -> dict[str, Any]:
    recipe_validation = validate_recipe(recipe)
    if not recipe_validation.get("valid"):
        return _failure("recipe_invalid")
    normalized = recipe_validation["recipe"]
    recipes, styles, targets, catalog_errors = _catalog_maps(catalog)
    if catalog_errors:
        return _failure("catalog_required")
    findings: list[dict[str, Any]] = []
    raw_values = dict(values) if isinstance(values, Mapping) else {}
    variant: dict[str, Any] | None = None
    if variant_id is not None:
        for item in normalized["variants"]:
            if item["id"] == variant_id:
                variant = item
                break
        if variant is None:
            findings.append(_finding("variant_unknown", "variant", variant_id if isinstance(variant_id, str) else normalized["id"]))
    effective_values: dict[str, Any] = {}
    if variant is not None:
        effective_values.update({item["slot_id"]: item["value"] for item in variant["slot_values"]})
    effective_values.update(raw_values)
    slot_ids = {slot["id"] for slot in normalized["slots"]}
    if any(key not in slot_ids for key in effective_values):
        findings.append(_finding("unknown_slot_value", "recipe", normalized["id"]))
    resolved_values: list[dict[str, Any]] = []
    substitutions: dict[str, str] = {}
    for slot in normalized["slots"]:
        slot_id = slot["id"]
        present = slot_id in effective_values
        value = effective_values.get(slot_id, slot.get("default"))
        if not present and "default" not in slot and slot["required"]:
            findings.append(_finding("missing_required_slot", "slot", slot_id))
            continue
        if value is None and slot["required"]:
            findings.append(_finding("missing_required_slot", "slot", slot_id))
            continue
        if value is not None and isinstance(value, str) and _UNSAFE_VALUE_RE.search(value):
            findings.append(_finding("unsafe_text", "slot", slot_id))
            continue
        if value is not None and isinstance(value, str) and len(value) > 800:
            findings.append(_finding("text_limit", "slot", slot_id))
            continue
        if value is not None and not _slot_value_valid(slot, value):
            findings.append(_finding("slot_type_mismatch", "slot", slot_id))
            continue
        if value is not None:
            resolved_values.append({"slot_id": slot_id, "value": copy.deepcopy(value)})
            substitutions[slot_id] = str(value).lower() if isinstance(value, bool) else str(value)
    prompt_template = variant["prompt_template"] if variant is not None and variant.get("prompt_template") else normalized["prompt_template"]
    negative_template = variant["negative_prompt"] if variant is not None and variant.get("negative_prompt") else normalized["negative_prompt"]
    prompt = prompt_template
    for key, replacement in substitutions.items():
        prompt = prompt.replace("{{" + key + "}}", replacement)
    unresolved = _PLACEHOLDER_RE.findall(prompt)
    if unresolved:
        for placeholder in sorted(set(unresolved)):
            findings.append(_finding("unresolved_placeholder", "slot", f"slot_{placeholder}" if RECIPE_ID_RE.fullmatch(f"recipe_{placeholder}") else normalized["id"]))
    style_ids = list(normalized["style_pack_ids"])
    if variant is not None:
        style_ids.extend(variant["style_pack_ids"])
    style_ids = sorted(set(style_ids))
    for style_id in style_ids:
        style = _entity_by_id(styles, style_id)
        if style is None:
            findings.append(_finding("style_pack_missing", "style_pack", style_id))
            continue
        if normalized["media_kind"] not in style[0]["media_kinds"]:
            findings.append(_finding("style_media_mismatch", "style_pack", style_id))
            continue
        if style[0]["positive_prompt"]:
            prompt = "\n".join(part for part in (prompt.strip(), style[0]["positive_prompt"].strip()) if part)
        if style[0]["negative_prompt"]:
            negative_template = "\n".join(part for part in (negative_template.strip(), style[0]["negative_prompt"].strip()) if part)
    if len(prompt) > 4000 or len(negative_template) > 2500:
        findings.append(_finding("text_limit", "recipe", normalized["id"]))
    if _UNSAFE_VALUE_RE.search(prompt) or _UNSAFE_VALUE_RE.search(negative_template):
        findings.append(_finding("unsafe_text", "recipe", normalized["id"]))
    params, parameter_findings = _parameter_findings(normalized, parameters if parameters is not None else normalized["constraints"]["default_parameters"], normalized["id"])
    findings.extend(parameter_findings)
    compatibility: dict[str, Any] | None = None
    if target_id is not None:
        compatibility, compatibility_findings = _compatibility(normalized, target_id, targets)
        findings.extend(compatibility_findings)
    findings = sorted(findings, key=lambda item: item["id"])[:MAX_FINDINGS]
    summary = _summary(findings)
    if summary["errors"]:
        status = "unavailable"
    elif findings:
        status = "manual_review"
    else:
        status = "planned"
    return {"valid": summary["errors"] == 0, "status": status, "reason": "Prompt composition is deterministic and static; no model or job was launched." if not findings else "Prompt lint found bounded issues requiring review.", "action": "Use the detached generation intent only after static issues are resolved and any runtime smoke is separately authorized." if not findings else "Resolve the listed fixed lint findings before creating a generation intent.", "recipe_id": normalized["id"], "recipe_version": normalized["version"], "variant_id": variant["id"] if variant else None, "prompt": prompt if summary["errors"] == 0 else None, "negative_prompt": negative_template if summary["errors"] == 0 else None, "slot_values": sorted(resolved_values, key=lambda item: item["slot_id"]), "style_pack_ids": style_ids, "parameters": params, "findings": findings, "summary": summary, "compatibility": compatibility, "execution": "not_run", "source_fingerprint": recipe_validation["fingerprint"]}


def build_generation_intent(recipe: object, values: object | None = None, *, catalog: object | None = None, target_id: object | None = None, variant_id: object | None = None, parameters: object | None = None) -> dict[str, Any]:
    lint = lint_recipe(recipe, values, catalog=catalog, target_id=target_id, variant_id=variant_id, parameters=parameters)
    compatibility_unavailable = isinstance(lint.get("compatibility"), dict) and lint["compatibility"].get("status") == "unavailable"
    if not lint.get("valid") or not lint.get("prompt") or compatibility_unavailable or not isinstance(target_id, str) or TARGET_ID_RE.fullmatch(target_id) is None:
        return {"valid": False, "status": lint.get("status", "unavailable"), "reason": lint.get("reason", "Static lint failed."), "action": lint.get("action", "Resolve lint findings."), "errors": [{"code": item["code"]} for item in lint.get("findings", []) if item.get("severity") == "error"] or [{"code": "intent_not_ready"}], "intent": None, "execution": "not_run"}
    intent_id = f"intent_{_digest({'recipe': lint['recipe_id'], 'version': lint['recipe_version'], 'variant': lint['variant_id'], 'target': target_id, 'values': lint['slot_values'], 'parameters': lint['parameters']})[:48]}"
    intent = {"schema_version": "creative-generation-intent.v1", "id": intent_id, "recipe_id": lint["recipe_id"], "recipe_version": lint["recipe_version"], "variant_id": lint["variant_id"], "target_id": target_id, "media_kind": validate_recipe(recipe)["recipe"]["media_kind"], "prompt": lint["prompt"], "negative_prompt": lint["negative_prompt"] or "", "slot_values": lint["slot_values"], "style_pack_ids": lint["style_pack_ids"], "parameters": lint["parameters"], "status": "planned", "reason_code": "static_plan_ready", "reason": "Static composition passed; no model or job was launched.", "action_code": "review_runtime_evidence", "action": "Keep this dry-run intent detached until a separately authorized runtime smoke.", "dry_run": True, "execution": "not_run", "source_fingerprint": lint["source_fingerprint"]}
    validation = validate_generation_intent(intent)
    if not validation.get("valid"):
        return {"valid": False, "status": "unavailable", "reason": "Generation intent failed its closed invariant.", "action": "Keep the intent within the published planning-only schema.", "errors": [{"code": item.get("code", "intent_invalid")} for item in validation.get("errors", [])], "intent": None, "execution": "not_run"}
    return {"valid": True, "status": "planned", "reason": "Static generation intent is ready for human review; execution was not performed.", "action": "Pass the detached DTO to a separately authorized runtime owner.", "errors": [], "intent": validation["intent"], "fingerprint": validation["fingerprint"], "execution": "not_run"}


def plan_recipe_variants(recipe: object, values: object | None = None, *, catalog: object | None = None, target_id: object | None = None, parameters: object | None = None) -> dict[str, Any]:
    validation = validate_recipe(recipe)
    if not validation.get("valid"):
        return {"valid": False, "status": "unavailable", "reason": "Recipe failed closed validation.", "action": "Use a validated recipe.", "errors": [{"code": item.get("code", "recipe_invalid")} for item in validation.get("errors", [])], "variants": [], "execution": "not_run"}
    recipe_value = validation["recipe"]
    variant_ids: list[str | None] = [None] + [item["id"] for item in recipe_value["variants"]]
    truncated = len(variant_ids) > MAX_VARIANT_PLANS
    if len(variant_ids) > MAX_VARIANT_PLANS:
        variant_ids = variant_ids[:MAX_VARIANT_PLANS]
    plans: list[dict[str, Any]] = []
    for variant_id in variant_ids:
        lint = lint_recipe(recipe_value, values, catalog=catalog, target_id=target_id, variant_id=variant_id, parameters=parameters)
        intent = build_generation_intent(recipe_value, values, catalog=catalog, target_id=target_id, variant_id=variant_id, parameters=parameters) if lint.get("valid") else None
        plans.append({"variant_id": variant_id, "status": lint["status"], "finding_codes": sorted({item["code"] for item in lint["findings"]}), "intent": intent["intent"] if intent and intent.get("valid") else None})
    all_planned = all(item["status"] == "planned" for item in plans) and not truncated
    return {"valid": all_planned, "status": "planned" if all_planned else "manual_review", "reason": "Variant planning is deterministic and dry-run only.", "action": "Review each detached variant plan before any separately authorized execution.", "errors": [{"code": "variant_plan_limit"}] if truncated else [], "variants": plans, "execution": "not_run"}


compose_recipe = lint_recipe
preflight_recipe = lint_recipe
plan_generation_intent = build_generation_intent
lint_prompt_recipe = lint_recipe
compose_prompt_recipe = lint_recipe
preflight_prompt_recipe = lint_recipe
plan_variants = plan_recipe_variants


__all__ = ["lint_recipe", "lint_prompt_recipe", "compose_recipe", "compose_prompt_recipe", "preflight_recipe", "preflight_prompt_recipe", "build_generation_intent", "plan_generation_intent", "build_compatibility_report", "plan_recipe_variants", "plan_variants"]
