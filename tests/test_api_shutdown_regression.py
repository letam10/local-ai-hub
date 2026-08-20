from __future__ import annotations

import unittest
from unittest.mock import patch

from src.services.api import api_server


class _InterruptingServer:
    def __init__(self, _address, _handler) -> None:
        self.closed = False

    def serve_forever(self, *, poll_interval: float) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


class ApiShutdownRegressionTests(unittest.TestCase):
    def test_keyboard_shutdown_flushes_without_missing_comfy_symbol(self) -> None:
        server = _InterruptingServer(None, None)
        with (
            patch.object(api_server, "hub_config", return_value={"bind_host": "127.0.0.1", "api_port": 8765}),
            patch.object(api_server, "HubHTTPServer", return_value=server),
            patch.object(api_server, "reconcile_durable_jobs"),
            patch.object(api_server, "reconcile_startup"),
            patch.object(api_server, "_shutdown_owned_idle") as shutdown_idle,
            patch.object(api_server, "flush_jobs") as flush_jobs,
        ):
            self.assertEqual(api_server.main(), 0)

        shutdown_idle.assert_called_once_with()
        flush_jobs.assert_called_once_with()
        self.assertTrue(server.closed)


if __name__ == "__main__":
    unittest.main()
