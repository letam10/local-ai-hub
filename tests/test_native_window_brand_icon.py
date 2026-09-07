"""Executable startup-branding contracts; no API, provider or GPU work."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.app import main as desktop


class NativeWindowBrandIconTests(unittest.TestCase):
    def test_icon_is_passed_to_start_and_identity_precedes_window_creation(self):
        events = []
        callback = lambda: None

        # Deliberately use the real start signature, not an unrestricted mock.
        def start(func, *, gui, debug, icon):
            events.append((func, gui, debug, icon))

        with patch.object(desktop, "_canonical_icon_path", return_value="brand.ico"), patch.object(
            desktop, "_set_app_user_model_id", side_effect=lambda: events.append("identity")
        ):
            desktop._start_native_webview(SimpleNamespace(start=start), callback)
        self.assertEqual(events, ["identity", (callback, "edgechromium", False, "brand.ico")])

    def test_missing_icon_does_not_invent_a_python_or_system_icon(self):
        captured = []
        with patch.object(desktop, "_canonical_icon_path", return_value=None), patch.object(
            desktop, "_set_app_user_model_id"
        ):
            desktop._start_native_webview(
                SimpleNamespace(start=lambda *args, **kwargs: captured.append(kwargs)), lambda: None
            )
        self.assertIsNone(captured[0]["icon"])

    def test_canonical_icon_uses_install_root_not_cwd(self):
        with TemporaryDirectory() as directory:
            icon = Path(directory) / "local-ai-hub.ico"
            icon.write_bytes(b"test fixture")
            with patch.dict(desktop.os.environ, {"LOCALAIHUB_INSTALL_ROOT": directory}):
                self.assertEqual(desktop._canonical_icon_path(), str(icon))

    def test_missing_file_is_unavailable(self):
        with TemporaryDirectory() as directory, patch.dict(
            desktop.os.environ, {"LOCALAIHUB_INSTALL_ROOT": directory}
        ):
            self.assertIsNone(desktop._canonical_icon_path())


if __name__ == "__main__":
    unittest.main()
