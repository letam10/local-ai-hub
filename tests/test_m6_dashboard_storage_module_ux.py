"""M6 Dashboard, Storage and Module UX contracts.

These tests stay source-side and use synthetic state only.  They never call a
Hub endpoint, inspect the installed data root, or start a storage worker.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _node_json(script: str) -> object:
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def _render(route: str, state: dict[str, object]) -> str:
    return str(_node_json(
        f"import {{ renderPage }} from './src/ui/pages.js';"
        f"const state = {json.dumps(state, ensure_ascii=True)};"
        f"process.stdout.write(JSON.stringify(renderPage({json.dumps(route)}, state)));"
    ))


class M6DashboardStorageModuleUxTests(unittest.TestCase):
    def test_dashboard_is_four_tier_summary_volumes_attention_and_actions(self) -> None:
        html = _render(
            "dashboard",
            {
                "health": {"status": "healthy", "disk": {"free_bytes": 100}, "gpu": {"name": "RTX 4060"}},
                "components": [{"id": "sam2", "name": "SAM2", "status": "partial"}],
                "productization": {"jobs": {"records": []}},
            },
        )
        self.assertEqual(
            re.findall(r'data-dashboard-tier="([^"]+)"', html),
            ["summary", "volumes", "attention", "actions"],
        )
        self.assertIn('data-dashboard-simplified="true"', html)
        for forbidden in (
            "media-evidence",
            "dashboard-onboarding",
            "workflow-library-state",
            "job-recovery-card",
        ):
            self.assertNotIn(forbidden, html)
        self.assertEqual(len(re.findall(r'data-dashboard-metric=', html)), 4)

    def test_dashboard_renders_only_server_owned_c_and_d_volume_projection(self) -> None:
        html = _render(
            "dashboard",
            {
                "health": {"status": "healthy"},
                "storage": {
                    "volumes": [
                        {
                            "id": "c",
                            "status": "available",
                            "total_bytes": 100 * 1024**3,
                            "free_bytes": 10 * 1024**3,
                            "used_bytes": 90 * 1024**3,
                            "low_space": True,
                            "reason": "Free space is low.",
                            "next_action": "Review storage before new writes.",
                        },
                        {
                            "id": "d",
                            "status": "unavailable",
                            "total_bytes": None,
                            "free_bytes": None,
                            "used_bytes": None,
                            "reason": r"D:\\private\\reason.txt",
                            "next_action": "token=must-not-render",
                        },
                        {"id": "e", "status": "available", "total_bytes": 1, "free_bytes": 1, "used_bytes": 0},
                    ],
                },
            },
        )
        self.assertEqual(re.findall(r'data-dashboard-volume="([^"]+)"', html), ["c", "d"])
        self.assertIn('data-dashboard-volume-total="107374182400"', html)
        self.assertIn('data-dashboard-volume-used="96636764160"', html)
        self.assertIn('data-dashboard-volume-free="10737418240"', html)
        self.assertIn('data-dashboard-volume-percent="90"', html)
        self.assertIn('data-dashboard-volume-percent=""', html)
        self.assertIn('data-low-space="true"', html)
        self.assertIn('data-status="unavailable"', html)
        self.assertIn("Tổng", html)
        self.assertIn("Đã dùng", html)
        self.assertIn("Trống", html)
        self.assertIn("Free space is low.", html)
        self.assertNotIn("private", html)
        self.assertNotIn("token=must-not-render", html)
        self.assertNotIn('data-dashboard-volume="e"', html)
        self.assertNotIn("style=", html)

    def test_dashboard_storage_projection_supports_two_columns_then_one_column(self) -> None:
        css = (ROOT / "src" / "ui" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".dashboard-storage-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));", css)
        self.assertIn("@media (max-width: 1100px)", css)
        self.assertIn(".dashboard-storage-grid { grid-template-columns: 1fr; }", css)

    def test_dashboard_active_count_excludes_reconstruct_only_records(self) -> None:
        html = _render(
            "dashboard",
            {
                "health": {"status": "healthy"},
                "productization": {
                    "jobs": {
                        "records": [
                            {"id": "durable-reconstruct", "source": "durable", "tool": "transcribe_media", "status": "queued", "retry_mode": "reconstruct_only", "execution": "not_run"},
                            {"id": "job-active", "source": "hot", "tool": "ocr_document", "status": "running"},
                        ]
                    }
                },
            },
        )
        self.assertIn('data-dashboard-active-count="1"', html)
        self.assertIn('data-reconstruct-only="true"', html)
        self.assertIn("Đã tạo · chưa thực thi", html)

    def test_dashboard_only_promotes_actionable_transport_failures_to_attention(self) -> None:
        html = _render(
            "dashboard",
            {
                "health": {"status": "unavailable"},
                "productization": {
                    "capabilities": {
                        "modules": [
                            {"id": "partial-module", "label": "Partial module", "status": "partial"},
                        ],
                    },
                },
            },
        )
        self.assertIn('data-dashboard-attention-count="1"', html)
        self.assertIn("hub-api", html)
        self.assertIn("API Hub", html)
        self.assertNotIn("Partial module", html)

    def test_status_detail_has_only_purpose_reason_impact_and_next_step(self) -> None:
        value = _node_json(
            "import { statusExplanation, statusSeverity } from './src/ui/shared/rendering.js';"
            "const html = statusExplanation({name:'Storage', technicalId:'storage', status:'partial', purpose:'P', reason:'R', impact:'I', nextAction:'A'});"
            "process.stdout.write(JSON.stringify({html, partial:statusSeverity('partial'), unavailable:statusSeverity('unavailable'), setup:statusSeverity('needs_setup'), failed:statusSeverity('failed'), ready:statusSeverity('ready')}));"
        )
        self.assertIsInstance(value, dict)
        payload = value
        html = str(payload["html"])
        self.assertNotIn("Trạng thái này nghĩa là gì", html)
        self.assertEqual(len(re.findall(r'class="status-explanation__label"', html)), 4)
        self.assertEqual(payload["partial"], "neutral")
        self.assertEqual(payload["unavailable"], "neutral")
        self.assertEqual(payload["setup"], "warning")
        self.assertEqual(payload["failed"], "error")
        self.assertEqual(payload["ready"], "success")

    def test_components_route_uses_one_selected_master_detail(self) -> None:
        html = _render(
            "components",
            {
                "selectedComponentId": "beta",
                "componentDetailOpen": True,
                "componentManager": {
                    "records": [
                        {"component_id": "alpha", "component_type": "runtime", "display_name": "Alpha", "status": "PARTIAL"},
                        {"component_id": "beta", "component_type": "model", "display_name": "Beta", "status": "NOT_INSTALLED"},
                    ]
                },
                "componentPlans": {},
            },
        )
        self.assertIn('data-component-master-detail="true"', html)
        self.assertEqual(len(re.findall(r'data-component-select=', html)), 2)
        self.assertIn('data-selected-component="beta"', html)
        self.assertEqual(len(re.findall(r'data-component-detail-panel', html)), 1)
        self.assertEqual(len(re.findall(r'class="component-manager-card', html)), 1)
        self.assertIn('data-component-detail-open="true"', html)

    def test_component_selection_is_deterministic_and_refresh_safe(self) -> None:
        value = _node_json(
            "import { chooseComponentId } from './src/ui/features/components/render.js';"
            "const records=[{component_id:'beta',component_type:'model'},{component_id:'alpha',component_type:'runtime'}];"
            "process.stdout.write(JSON.stringify({keep:chooseComponentId(records,'beta'), fallback:chooseComponentId(records,'missing'), empty:chooseComponentId([], 'beta')}));"
        )
        self.assertEqual(value, {"keep": "beta", "fallback": "alpha", "empty": ""})

    def test_component_keyboard_and_narrow_detail_contract_is_declared(self) -> None:
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        renderer = (ROOT / "src" / "ui" / "features" / "components" / "render.js").read_text(encoding="utf-8")
        for marker in ("selectedComponentId", "componentDetailOpen", "ArrowUp", "ArrowDown", "Enter", "Escape"):
            self.assertIn(marker, app)
        for marker in ("data-component-close-detail", "data-component-detail-panel", "data-component-select"):
            self.assertIn(marker, renderer)

    def test_storage_scan_poller_cancels_stale_timer_on_stop_and_restart(self) -> None:
        value = _node_json(
            "import { createStorageScanPoller } from './src/ui/storage_scan_polling.js';"
            "const scheduled=[]; const cancelled=[]; let active=true;"
            "const poller=createStorageScanPoller({getSnapshot:async()=>({scan:{scan_id:'scan',status:'running'}}),isRouteActive:()=>active,onSnapshot:()=>{},schedule:(callback,delay)=>{const handle={callback,delay}; scheduled.push(handle); return handle;},cancel:(handle)=>cancelled.push(handle)});"
            "poller.start('scan-a'); poller.start('scan-b'); const afterRestart=cancelled.length; poller.stop(); active=false; const afterStop=cancelled.length;"
            "process.stdout.write(JSON.stringify({scheduled:scheduled.length,afterRestart,afterStop,firstCancelled:cancelled[0]===scheduled[0],secondCancelled:cancelled[1]===scheduled[1]}));"
        )
        self.assertEqual(
            value,
            {"scheduled": 2, "afterRestart": 1, "afterStop": 2, "firstCancelled": True, "secondCancelled": True},
        )

    def test_models_route_does_not_start_a_new_deep_scan_after_a_terminal_snapshot(self) -> None:
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("Queue DEEP_EXACT automatically after the page is visible", app)
        self.assertNotIn("const deep = await scanStorage()", app)
        self.assertIn("await loadRouteData({ scan: true })", app)

    def test_models_storage_projection_separates_disk_free_and_owned_scan(self) -> None:
        html = _render(
            "models",
            {
                "productionCatalog": {"models": []},
                "models": [],
                "storage": {
                    "status": "partial",
                    "disk": {"total_bytes": 1000, "free_bytes": 250, "used_bytes": 750, "low_space": False},
                    "scan": {
                        "status": "running",
                        "mode": "deep_exact",
                        "progress": 42,
                        "total_bytes_counted": 300,
                        "entries_scanned": 12001,
                        "files_scanned": 12000,
                        "directories_scanned": 1,
                        "reparse_entries": 2,
                        "unreadable_entries": 1,
                        "completed_roots": 3,
                        "reason": "C:/private must not be shown",
                    },
                    "areas": {"Models": {"bytes": 300, "complete": False, "entries_scanned": 12001, "files_scanned": 12000}},
                },
                "updateCenter": {},
            },
        )
        self.assertIn('data-storage-disk-free="250"', html)
        self.assertIn('data-storage-owned-total="300"', html)
        self.assertIn('data-storage-entries-scanned="12001"', html)
        self.assertIn('data-storage-completed-roots="3"', html)
        self.assertIn("Quét chính xác", html)
        self.assertNotIn("C:/private", html)

    def test_models_storage_manual_action_always_declares_deep_exact_scan(self) -> None:
        html = _render(
            "models",
            {
                "productionCatalog": {"models": []},
                "models": [],
                "storage": {"scan": {"status": "partial", "mode": "fast"}},
                "updateCenter": {},
            },
        )
        self.assertIn(">Quét chính xác</button>", html)
        self.assertNotIn(">Quét lại</button>", html)

    def test_storage_scan_metrics_have_one_dom_owner_each(self) -> None:
        html = _render(
            "models",
            {
                "productionCatalog": {"models": []},
                "models": [],
                "storage": {"scan": {"status": "partial", "mode": "fast"}},
                "updateCenter": {},
            },
        )
        root = re.search(r'<div class="storage-scan-status"[^>]*>', html)
        self.assertIsNotNone(root)
        root_tag = root.group(0)
        for attribute in (
            "data-storage-disk-free",
            "data-storage-owned-total",
            "data-storage-entries-scanned",
            "data-storage-directories-scanned",
            "data-storage-reparse-entries",
            "data-storage-unreadable-entries",
            "data-storage-completed-roots",
        ):
            self.assertNotIn(attribute, root_tag)
            self.assertEqual(html.count(attribute), 1)


if __name__ == "__main__":
    unittest.main()
