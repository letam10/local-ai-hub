"""Validation and dependency analysis for typed Node Studio graphs."""

from __future__ import annotations

import copy
import re
from collections import defaultdict, deque
from typing import Any

from .registry import GRAPH_SCHEMA_VERSION, NodeDefinition, get_definition


NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
EDGE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,99}$")
ARTIFACT_ID = re.compile(r"^artifact_[a-f0-9]{32}$")


def _error(code: str, message: str, **context: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **{key: value for key, value in context.items() if value is not None}}


def _definition_ports(definition: NodeDefinition, direction: str) -> dict[str, Any]:
    ports = definition.inputs if direction == "input" else definition.outputs
    return {port.name: port for port in ports}


def _value_supplied(data: dict[str, Any], name: str) -> bool:
    value = data.get(name)
    return value not in (None, "", [], {})


def _normal_graph(graph: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(graph)
    value.setdefault("schema_version", GRAPH_SCHEMA_VERSION)
    value.setdefault("id", "untitled")
    value.setdefault("title", "Untitled workflow")
    value.setdefault("scope", "all")
    value.setdefault("nodes", [])
    value.setdefault("edges", [])
    value.setdefault("groups", [])
    return value


def validate_graph(graph: object, *, require_runnable: bool = False) -> dict[str, Any]:
    """Validate shape, typed sockets, cycles and executable input requirements.

    The regular editor uses ``require_runnable=False`` so an unfinished graph can
    still be saved.  The execution API sets it to true and returns the same
    useful, per-node errors without attempting a partial run.
    """

    if not isinstance(graph, dict):
        return {"valid": False, "errors": [_error("graph_type", "Graph JSON phải là object.")], "order": [], "graph": {}}
    value = _normal_graph(graph)
    errors: list[dict[str, Any]] = []
    if value.get("schema_version") != GRAPH_SCHEMA_VERSION:
        errors.append(_error("schema_version", f"Graph schema phải là version {GRAPH_SCHEMA_VERSION}."))
    nodes = value.get("nodes")
    edges = value.get("edges")
    if not isinstance(nodes, list):
        errors.append(_error("nodes_type", "Graph nodes phải là array."))
        nodes = []
    if not isinstance(edges, list):
        errors.append(_error("edges_type", "Graph edges phải là array."))
        edges = []

    node_map: dict[str, dict[str, Any]] = {}
    definitions: dict[str, NodeDefinition] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(_error("node_type", "Mỗi node phải là object.", node_index=index))
            continue
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id):
            errors.append(_error("node_id", "Node id không hợp lệ.", node_index=index))
            continue
        if node_id in node_map:
            errors.append(_error("duplicate_node_id", "Node id bị trùng.", node_id=node_id))
            continue
        if not isinstance(node_type, str) or not (definition := get_definition(node_type)):
            errors.append(_error("unknown_node_type", "Node type không có trong registry Hub.", node_id=node_id, node_type=node_type))
            continue
        data = node.get("data")
        if data is None:
            node["data"] = {}
        elif not isinstance(data, dict):
            errors.append(_error("node_data", "Node data phải là object.", node_id=node_id))
            node["data"] = {}
        else:
            properties = {str(item.get("name")): item for item in definition.properties}
            for key in data:
                if key not in properties:
                    errors.append(_error("unknown_node_property", "Node chỉ nhận property có trong registry Hub.", node_id=node_id, property=key))
            for name, property_definition in properties.items():
                if property_definition.get("kind") != "asset" or name not in data or data[name] in (None, ""):
                    continue
                if not isinstance(data[name], str) or not ARTIFACT_ID.fullmatch(data[name]):
                    errors.append(_error("invalid_asset_id", "Asset property phải dùng opaque artifact ID của Hub.", node_id=node_id, property=name))
        position = node.get("position")
        if position is None:
            node["position"] = {"x": 80 + (index % 4) * 260, "y": 80 + (index // 4) * 180}
        elif not isinstance(position, dict):
            errors.append(_error("node_position", "Node position phải là object.", node_id=node_id))
            node["position"] = {"x": 80, "y": 80}
        node_map[node_id] = node
        definitions[node_id] = definition

    incoming: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_map}
    indegree: dict[str, int] = {node_id: 0 for node_id in node_map}
    normalized_edges: list[dict[str, Any]] = []
    seen_edges: set[str] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(_error("edge_type", "Mỗi edge phải là object.", edge_index=index))
            continue
        edge_id = edge.get("id") or f"edge_{index + 1}"
        if not isinstance(edge_id, str) or not EDGE_ID.fullmatch(edge_id):
            errors.append(_error("edge_id", "Edge id không hợp lệ.", edge_index=index))
            continue
        if edge_id in seen_edges:
            errors.append(_error("duplicate_edge_id", "Edge id bị trùng.", edge_id=edge_id))
            continue
        seen_edges.add(edge_id)
        source = edge.get("source")
        target = edge.get("target")
        if not isinstance(source, dict) or not isinstance(target, dict):
            errors.append(_error("edge_endpoints", "Edge phải có source và target object.", edge_id=edge_id))
            continue
        source_node = source.get("node")
        source_port = source.get("port")
        target_node = target.get("node")
        target_port = target.get("port")
        if not all(isinstance(item, str) for item in (source_node, source_port, target_node, target_port)):
            errors.append(_error("edge_endpoint_type", "Endpoint node/port phải là string.", edge_id=edge_id))
            continue
        if source_node not in node_map or target_node not in node_map:
            errors.append(_error("edge_unknown_node", "Edge tham chiếu node không tồn tại.", edge_id=edge_id))
            continue
        source_definition = definitions[source_node]
        target_definition = definitions[target_node]
        source_ports = _definition_ports(source_definition, "output")
        target_ports = _definition_ports(target_definition, "input")
        output = source_ports.get(source_port)
        input_port = target_ports.get(target_port)
        if output is None:
            errors.append(_error("unknown_output_port", "Output port không tồn tại.", edge_id=edge_id, node_id=source_node, port=source_port))
            continue
        if input_port is None:
            errors.append(_error("unknown_input_port", "Input port không tồn tại.", edge_id=edge_id, node_id=target_node, port=target_port))
            continue
        if output.type != input_port.type:
            errors.append(_error("socket_type_mismatch", f"Không thể nối {output.type} vào {input_port.type}.", edge_id=edge_id, source_type=output.type, target_type=input_port.type))
            continue
        incoming[(target_node, target_port)].append(edge)
        if len(incoming[(target_node, target_port)]) > 1 and not input_port.multi:
            errors.append(_error("input_already_connected", "Input port này chỉ nhận một edge.", edge_id=edge_id, node_id=target_node, port=target_port))
            continue
        if target_node not in adjacency[source_node]:
            adjacency[source_node].add(target_node)
            indegree[target_node] += 1
        normalized_edges.append({"id": edge_id, "source": {"node": source_node, "port": source_port}, "target": {"node": target_node, "port": target_port}})

    if require_runnable:
        for node_id, node in node_map.items():
            definition = definitions[node_id]
            if definition.annotation:
                continue
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            for port in definition.inputs:
                if not port.required:
                    continue
                if incoming.get((node_id, port.name)) or _value_supplied(data, port.name):
                    continue
                errors.append(_error("missing_required_input", f"Node cần input {port.label or port.name}.", node_id=node_id, port=port.name))
            if definition.runner == "load_artifact" and not _value_supplied(data, "asset_id"):
                errors.append(_error("missing_asset", "Load node cần artifact Hub đã upload.", node_id=node_id, port="asset_id"))

    executable_ids = [node_id for node_id, definition in definitions.items() if not definition.annotation]
    queue = deque(sorted(node_id for node_id in executable_ids if indegree[node_id] == 0))
    order: list[str] = []
    local_indegree = dict(indegree)
    while queue:
        node_id = queue.popleft()
        if not definitions[node_id].annotation:
            order.append(node_id)
        for child in sorted(adjacency[node_id]):
            local_indegree[child] -= 1
            if local_indegree[child] == 0:
                queue.append(child)
    if len(order) != len(executable_ids):
        cycle_nodes = sorted(node_id for node_id in executable_ids if local_indegree[node_id] > 0)
        errors.append(_error("cycle_detected", "Graph có cycle; chỉ DAG mới được chạy.", nodes=cycle_nodes))

    value["nodes"] = list(node_map.values())
    value["edges"] = normalized_edges
    return {"valid": not errors, "errors": errors, "order": order, "graph": value}


def downstream_nodes(graph: object, changed_node_ids: object) -> dict[str, Any]:
    """Return exactly the changed nodes and their downstream dependants."""

    validation = validate_graph(graph)
    if not validation["valid"]:
        return {"valid": False, "errors": validation["errors"], "dirty_nodes": []}
    changed = {item for item in changed_node_ids if isinstance(item, str)} if isinstance(changed_node_ids, list) else set()
    node_ids = {node["id"] for node in validation["graph"]["nodes"]}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in validation["graph"]["edges"]:
        adjacency[edge["source"]["node"]].add(edge["target"]["node"])
    dirty = {item for item in changed if item in node_ids}
    pending = deque(sorted(dirty))
    while pending:
        node_id = pending.popleft()
        for child in sorted(adjacency[node_id]):
            if child not in dirty:
                dirty.add(child)
                pending.append(child)
    return {"valid": True, "errors": [], "dirty_nodes": sorted(dirty)}
