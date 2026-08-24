"""Static UI asset references must resolve inside the installed payload."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UiAssetContractTests(unittest.TestCase):
    def test_index_stylesheet_references_exist(self) -> None:
        html = (ROOT / "src" / "ui" / "index.html").read_text(encoding="utf-8")
        references = re.findall(r'<link[^>]+href="([^"]+)"', html)
        local_paths = [value.removeprefix("/ui/") for value in references if value.startswith("/ui/")]
        self.assertTrue(local_paths)
        for relative in local_paths:
            self.assertTrue((ROOT / "src" / "ui" / relative).is_file(), relative)

    def test_settings_backup_loader_imports_its_api_dependency(self) -> None:
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        api = (ROOT / "src" / "ui" / "api.js").read_text(encoding="utf-8")
        self.assertIn("  listBackups,", app)
        self.assertRegex(api, r"export const listBackups\s*=")
        self.assertIn("listBackups()", app)

    def test_vietnamese_status_dictionary_does_not_fragment_unavailable(self) -> None:
        i18n = (ROOT / "src" / "ui" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('"unavailable": "chưa khả dụng"', i18n)
        self.assertIn(
            '"Media operation scope is not applied to this workspace; no execution is claimed.": '
            '"Phạm vi thao tác media không áp dụng cho workspace này; không tuyên bố thực thi."',
            i18n,
        )

    def test_component_control_plane_uses_localized_fixed_copy(self) -> None:
        control_plane = (ROOT / "src" / "ui" / "features" / "components" / "v8_control_plane.js").read_text(encoding="utf-8")
        renderer = (ROOT / "src" / "ui" / "features" / "components" / "render.js").read_text(encoding="utf-8")
        self.assertIn('import { translateText } from "../../i18n.js";', control_plane)
        self.assertIn('uiText("V8 source acceptance")', control_plane)
        self.assertIn('uiTextHtml("Component Operations")', renderer)

    def test_models_and_resource_fit_use_localized_fixed_labels(self) -> None:
        models = (ROOT / "src" / "ui" / "features" / "models" / "models.js").read_text(encoding="utf-8")
        pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8")
        rendering = (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        self.assertIn("uiTextHtml(action)", models)
        self.assertIn('uiTextHtml("Check Update")', models)
        self.assertIn("uiTextHtml });", pages)
        self.assertIn("uiTextHtml(item.kind)", rendering)
        self.assertIn("uiTextHtml(readinessFitLabel(item.fit))", rendering)

    def test_storage_partial_copy_is_explicit_and_localized(self) -> None:
        models = (ROOT / "src" / "ui" / "features" / "models" / "models.js").read_text(encoding="utf-8")
        rendering = (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        i18n = (ROOT / "src" / "ui" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('value?.complete === false || value?.status === "partial"', models)
        self.assertIn('volume.status === "partial"', rendering)
        self.assertIn('"At least": "Đã tính ít nhất"', i18n)
        self.assertIn('uiTextHtml("Model")', models)
        self.assertIn('uiTextHtml("Check")', models)

    def test_snapshot_and_workflow_rail_copy_is_localized(self) -> None:
        index = (ROOT / "src" / "ui" / "index.html").read_text(encoding="utf-8")
        rendering = (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        i18n = (ROOT / "src" / "ui" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('data-i18n="Snapshot received."', index)
        self.assertIn('uiTextHtml("Load & Transform")', rendering)
        self.assertIn('uiTextHtml("Prompt / Input")', rendering)
        self.assertIn('"Snapshot received.": "Đã nhận snapshot."', i18n)

    def test_node_titles_and_ports_use_the_fixed_localization_boundary(self) -> None:
        studio = (ROOT / "src" / "ui" / "features" / "node_studio" / "studio.js").read_text(encoding="utf-8")
        self.assertIn('this.title = nodeText(captured.title)', studio)
        self.assertIn('this.addInput(nodeText(port.label || port.name)', studio)
        self.assertIn('this.addOutput(nodeText(port.label || port.name)', studio)
        self.assertIn('nodeText(definition.title)', studio)

    def test_airi_external_state_is_explicitly_unconnected(self) -> None:
        renderer = (ROOT / "src" / "ui" / "features" / "airi" / "render.js").read_text(encoding="utf-8")
        i18n = (ROOT / "src" / "ui" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('card("Ứng dụng ngoài — chưa kết nối"', renderer)
        self.assertIn('"Ứng dụng ngoài — chưa kết nối"', i18n)


if __name__ == "__main__":
    unittest.main()
