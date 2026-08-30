"""Post-V8 server-owned Capability Graph V2.

The graph is a read-only projection over existing component, tool, model and
runtime snapshots.  It deliberately contains no executor, downloader,
filesystem path, provider call, or process control hook.
"""

from .graph import (
    CAPABILITY_GRAPH_SCHEMA_VERSION,
    CapabilityGraph,
    CapabilityGraphError,
    build_capability_graph,
    build_component_capability_graph,
    validate_capability_descriptor,
)

__all__ = [
    "CAPABILITY_GRAPH_SCHEMA_VERSION",
    "CapabilityGraph",
    "CapabilityGraphError",
    "build_capability_graph",
    "build_component_capability_graph",
    "validate_capability_descriptor",
]
