from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentsFilesystemBoundaryTests(unittest.TestCase):
    def test_canonical_and_external_destructive_boundaries_are_documented(self) -> None:
        policy = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        required_phrases = (
            "`D:\\LocalAIHub` is the canonical project filesystem boundary.",
            "resolved target is outside `D:\\LocalAIHub` without explicit user approval",
            "symlink, junction, mount point, or other reparse point",
            "resolved absolute target, reason, expected file/directory count or size",
            "%TEMP%", "%LOCALAPPDATA%", "%APPDATA%", "Documents",
            "preserve Models, Environments, runtime, Output, Config local state",
            "Permission to access a path is not permission to destroy it.",
        )
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, policy)


if __name__ == "__main__":
    unittest.main()
