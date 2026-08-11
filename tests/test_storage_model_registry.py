from __future__ import annotations

import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _response(payload: dict[str, object]) -> _Response:
    return _Response(json.dumps(payload).encode("utf-8"))


class StorageModelRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        from src.services.storage_manager import overview

        self.overview = overview
        overview._model_cache = None

    def tearDown(self) -> None:
        self.overview._model_cache = None

    def test_ollama_rows_use_matching_tag_metadata_not_the_store_total(self) -> None:
        registry = {
            "models": [
                {"id": "one", "engine": "ollama", "model_name": "one:latest", "local_path": "C:/managed/store"},
                {"id": "two", "engine": "ollama", "model_name": "two:latest", "local_path": "C:/managed/store"},
            ]
        }
        payload = {"models": [{"name": "one:latest", "size": 101}, {"name": "two:latest", "size": 202}]}
        with patch.object(self.overview, "load_json", return_value=registry), patch.object(
            self.overview, "urlopen", return_value=_response(payload)
        ), patch.object(self.overview, "_directory_size", side_effect=AssertionError("Ollama store must not be scanned")):
            records = self.overview.model_summary(force=True)

        by_name = {record["model_name"]: record for record in records}
        self.assertEqual(by_name["one:latest"]["size"]["bytes"], 101)
        self.assertEqual(by_name["two:latest"]["size"]["bytes"], 202)
        self.assertTrue(all(record["installed"] for record in records))
        self.assertTrue(all(record["size_source"] == "ollama_api_tags" for record in records))

    def test_unavailable_ollama_metadata_never_falls_back_to_a_guessed_store_size(self) -> None:
        registry = {"models": [{"id": "one", "engine": "ollama", "model_name": "one:latest", "file_size": 999}]}
        with patch.object(self.overview, "load_json", return_value=registry), patch.object(
            self.overview, "urlopen", side_effect=OSError("offline")
        ):
            record = self.overview.model_summary(force=True)[0]

        self.assertFalse(record["installed"])
        self.assertEqual(record["size"]["bytes"], 0)
        self.assertEqual(record["size_source"], "ollama_api_tags_unavailable")

    def test_model_cache_can_be_invalidated_after_an_external_ollama_removal(self) -> None:
        self.overview._model_cache = (1.0, [{"id": "stale"}])
        self.overview.invalidate_model_cache()
        self.assertIsNone(self.overview._model_cache)

    def test_registry_generator_replaces_stale_ollama_rows_only_after_metadata_is_available(self) -> None:
        spec = importlib.util.spec_from_file_location("refresh_managed_registry_test", ROOT / "scripts" / "refresh_managed_registry.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        existing = [
            {"id": "local-model", "engine": "AnimeSR", "model_name": "AnimeSR v2"},
            {"id": "stale-ollama", "engine": "ollama", "model_name": "removed:latest"},
        ]
        payload = {"models": [{"name": "kept:latest", "digest": "a" * 64, "size": 303}]}
        with patch.object(module, "urlopen", return_value=_response(payload)):
            records = module.reconcile_ollama_model_records(existing)

        self.assertEqual([record["model_name"] for record in records], ["AnimeSR v2", "kept:latest"])
        self.assertEqual(records[-1]["metadata_size_bytes"], 303)
        self.assertIsNone(records[-1]["file_size"])

    def test_registry_generator_retains_existing_ollama_rows_when_the_service_is_unavailable(self) -> None:
        spec = importlib.util.spec_from_file_location("refresh_managed_registry_offline_test", ROOT / "scripts" / "refresh_managed_registry.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        existing = [{"id": "stale-ollama", "engine": "ollama", "model_name": "kept:latest"}]
        with patch.object(module, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(module.reconcile_ollama_model_records(existing), existing)


if __name__ == "__main__":
    unittest.main()
