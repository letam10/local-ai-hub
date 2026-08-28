"""Post-V8 immutable-projection cache tests (no execution state cached)."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.services.api.context import build_default_context
from src.services.projection_cache import BoundedProjectionCache


class _Catalog:
    def snapshot(self):  # type: ignore[no-untyped-def]
        return {"models": [], "runtimes": []}


class _Lifecycle:
    def __init__(self) -> None:
        self.catalog = _Catalog()


class ProjectionCacheTests(unittest.TestCase):
    def test_fingerprint_change_or_uncacheable_call_rebuilds(self) -> None:
        cache = BoundedProjectionCache()
        calls: list[str] = []

        def build():  # type: ignore[no-untyped-def]
            calls.append("built")
            return {"serial": len(calls)}

        first = cache.get_or_build("graph", "a" * 64, build)
        second = cache.get_or_build("graph", "a" * 64, build)
        changed = cache.get_or_build("graph", "b" * 64, build)
        uncached = cache.get_or_build("graph", "b" * 64, build, cacheable=False)
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertNotEqual(changed, uncached)
        self.assertEqual(len(calls), 3)

    def _context(self, *, running: bool = False):
        status = "running" if running else "partial"
        return build_default_context({
            "project_manager": SimpleNamespace(get_artifact_status=lambda artifact_id: None),
            "component_statuses": lambda: [{"id": "whisper", "component_status": status}],
            "tool_catalog": lambda components: [],
        })

    def test_static_graph_and_model_projections_reuse_matching_fingerprint(self) -> None:
        with patch("src.services.productization.ComponentLifecycle", return_value=_Lifecycle()), \
             patch("src.services.capability_graph.build_component_capability_graph", wraps=__import__("src.services.capability_graph", fromlist=["build_component_capability_graph"]).build_component_capability_graph) as graph_builder, \
             patch("src.services.model_manager_v2.ModelManagerV2", wraps=__import__("src.services.model_manager_v2", fromlist=["ModelManagerV2"]).ModelManagerV2) as manager:
            context = self._context()
            context.call("capability_graph_snapshot")
            context.call("capability_graph_snapshot")
            context.call("model_manager_v2_snapshot")
            context.call("model_manager_v2_snapshot")
        self.assertEqual(graph_builder.call_count, 1)
        self.assertEqual(manager.call_count, 1)

    def test_live_execution_state_is_not_cached(self) -> None:
        with patch("src.services.productization.ComponentLifecycle", return_value=_Lifecycle()), \
             patch("src.services.capability_graph.build_component_capability_graph", wraps=__import__("src.services.capability_graph", fromlist=["build_component_capability_graph"]).build_component_capability_graph) as graph_builder:
            context = self._context(running=True)
            context.call("capability_graph_snapshot")
            context.call("capability_graph_snapshot")
        self.assertEqual(graph_builder.call_count, 2)


if __name__ == "__main__":
    unittest.main()
