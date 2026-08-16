"""Contract tests for ComfyUI output publication through Hub artifacts."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.api import jobs


class ComfyUIArtifactContractTests(unittest.TestCase):
    def _record(self) -> dict[str, str]:
        return {"id": "job_image", "tool": "generate_flux"}

    def test_comfy_outputs_are_published_as_job_bound_opaque_artifacts(self) -> None:
        seen: list[Path] = []

        def register(paths, *, provenance):
            seen.extend(paths)
            return [{"id": "artifact_" + "a" * 32, "provenance": provenance}]

        with patch.object(jobs.artifact_store, "register_worker_outputs", side_effect=register):
            safe, error = jobs._publish_result({
                "status": "completed",
                "operation": "generate_image",
                "outputs": [r"D:\LocalAIHub\Output\Image\flux\image.png"],
            }, self._record())

        self.assertIsNone(error)
        self.assertEqual(seen, [Path(r"D:\LocalAIHub\Output\Image\flux\image.png")])
        self.assertEqual(safe["artifacts"][0]["id"], "artifact_" + "a" * 32)
        self.assertNotIn("D:\\LocalAIHub", str(safe))

    def test_output_and_files_aliases_remain_supported_and_bounded(self) -> None:
        self.assertEqual(
            jobs._output_candidates({"output": "one.png", "files": ["two.png"], "outputs": ["three.png"]}),
            [Path("one.png"), Path("two.png"), Path("three.png")],
        )
        safe, error = jobs._publish_result({"status": "completed", "outputs": [str(index) for index in range(65)]}, self._record())
        self.assertEqual(error, "output_count")
        self.assertEqual(safe["status"], "failed")

    def test_cancelled_comfy_result_never_attempts_artifact_publication(self) -> None:
        with patch.object(jobs.artifact_store, "register_worker_outputs", side_effect=AssertionError("cancelled output must not publish")):
            safe, error = jobs._publish_result({"status": "cancelled", "outputs": ["ignored.png"]}, self._record())
        self.assertIsNone(error)
        self.assertEqual(safe, {"status": "cancelled"})


if __name__ == "__main__":
    unittest.main()
