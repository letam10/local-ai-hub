"""Regression: a bundled runtime must import the exact selected payload."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.app.payload_bootstrap import api_server_command


class ExactPayloadBootstrapTests(unittest.TestCase):
    def test_command_inserts_exact_payload_before_module_resolution(self) -> None:
        payload = Path(r"D:\LocalAIHub\Temp\candidate-payload")
        command = api_server_command(payload)
        self.assertEqual(command[0], "-c")
        self.assertEqual(len(command), 2)
        self.assertIn("import runpy,sys", command[1])
        self.assertIn("sys.path.insert(0", command[1])
        self.assertIn(json.dumps(str(payload.absolute())), command[1])
        self.assertIn("runpy.run_module('src.services.api.api_server'", command[1])
        self.assertNotIn("-m", command)


if __name__ == "__main__":
    unittest.main()
