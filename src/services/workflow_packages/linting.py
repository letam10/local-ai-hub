"""Static, deterministic linting for already non-executable descriptors."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .io import validated_package_result


def _finding(code: str, severity: str, blueprint: str, entity_id: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "blueprint": blueprint, "entity_id": entity_id}


def _blueprints(package: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [("workflow", package["workflow"]), *[(item["id"], item) for item in package["subgraphs"]]]


def _reachability_findings(name: str, blueprint: dict[str, Any]) -> list[dict[str, str]]:
    node_map = {item["id"]: item for item in blueprint["nodes"]}
    adjacency: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {node_id: 0 for node_id in node_map}
    for edge in blueprint["edges"]:
        source = edge["from"]["node"]
        target = edge["to"]["node"]
        if target not in adjacency[source]:
            adjacency[source].add(target)
            indegree[target] += 1
    roots = sorted(node_id for node_id, node in node_map.items() if node["kind"] == "input")
    if not roots:
        roots = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    reachable: set[str] = set(roots)
    queue = deque(roots)
    while queue:
        node_id = queue.popleft()
        for child in sorted(adjacency[node_id]):
            if child not in reachable:
                reachable.add(child)
                queue.append(child)
    findings = [_finding("unreachable_node", "warning", name, node_id) for node_id in sorted(set(node_map) - reachable)]
    if not any(node["kind"] == "output" for node in node_map.values()):
        findings.append(_finding("missing_output_node", "warning", name, "blueprint"))
    return findings


def lint_workflow_package(value: object) -> dict[str, Any]:
    """Return advisory static findings without reading, writing, or running graphs."""

    validation = validated_package_result(value)
    if not validation["valid"]:
        return {
            "valid": False,
            "status": "invalid",
            "findings": [{"code": item["code"], "severity": "error", "location": item["location"]} for item in validation["errors"]],
        }
    package = validation["package"]
    assert isinstance(package, dict)
    findings: list[dict[str, str]] = []
    used_operations: set[str] = set()
    referenced_subgraphs: set[str] = set()
    for name, blueprint in _blueprints(package):
        findings.extend(_reachability_findings(name, blueprint))
        for node in blueprint["nodes"]:
            if node["kind"] == "operation":
                used_operations.add(node["operation"])
            elif node["kind"] == "subgraph":
                referenced_subgraphs.add(node["ref"])
    for operation in sorted(set(package["compatibility"]["required_node_types"]) - used_operations):
        findings.append(_finding("unused_declared_operation", "warning", "package", operation))
    for subgraph in sorted({item["id"] for item in package["subgraphs"]} - referenced_subgraphs):
        findings.append(_finding("unreferenced_subgraph", "warning", "package", subgraph))
    findings.sort(key=lambda item: (item.get("severity", ""), item.get("code", ""), item.get("blueprint", ""), item.get("entity_id", "")))
    return {
        "valid": True,
        "status": "clean" if not findings else "advisory",
        "fingerprint": validation["fingerprint"],
        "findings": findings,
        "execution": "not_run",
    }
