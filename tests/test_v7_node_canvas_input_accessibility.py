from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "src" / "ui" / "features" / "node_studio" / "studio.js"
LITEGRAPH = ROOT / "src" / "ui" / "vendor" / "litegraph.js"
DOC = ROOT / "docs" / "operations" / "V7_NODE_CANVAS_INPUT_ACCESSIBILITY.md"


class NodeCanvasInputAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.studio = STUDIO.read_text(encoding="utf-8")
        cls.litegraph = LITEGRAPH.read_text(encoding="utf-8")
        cls.documentation = DOC.read_text(encoding="utf-8")

    def test_studio_selects_pointer_mode_per_canvas_without_global_leakage(self) -> None:
        self.assertIn('getPointerEventsMethod?.(this.canvasElement, "pointer")', self.studio)
        self.assertIn("pointerevents_method: pointereventsMethod", self.studio)
        self.assertIn("onPointerCancel = () => this.handleCanvasPointerCancel()", self.studio)
        self.assertNotIn('LiteGraph.pointerevents_method = "pointer"', self.studio)
        self.assertIn('const pointereventsMethod = canvas.pointerevents_method || "mouse"', self.studio)
        self.assertIn("pointereventsMethod);", self.studio)

    def test_vendor_listener_mapping_has_one_event_and_no_fallthrough_duplicates(self) -> None:
        self.assertIn("LiteGraph._pointerEventName = function(method, semantic)", self.litegraph)
        self.assertIn('down: "pointerdown"', self.litegraph)
        self.assertIn('cancel: "pointercancel"', self.litegraph)
        self.assertIn('lostpointercapture: "lostpointercapture"', self.litegraph)
        self.assertIn('down: "mousedown"', self.litegraph)
        self.assertIn("return oDOM.addEventListener(eventName, callback, capture === true)", self.litegraph)
        self.assertIn("return oDOM.removeEventListener(eventName, callback, capture === true)", self.litegraph)
        self.assertNotIn("switch(sEvent)", self.litegraph)
        self.assertNotIn("oDOM.addEventListener(sMethod+sEvent", self.litegraph)

    def test_pointer_capability_and_event_normalization_are_fail_closed(self) -> None:
        self.assertIn("LiteGraph.getPointerEventsMethod = function(target, requested)", self.litegraph)
        self.assertIn('return view && view.PointerEvent ? "pointer" : "mouse"', self.litegraph)
        self.assertIn("LiteGraph.normalizePointerEvent = function(event, phase)", self.litegraph)
        self.assertIn("normalized.isPrimary = event.isPrimary === undefined ? true : event.isPrimary === true", self.litegraph)
        self.assertIn("normalized.pointerId = pointerId", self.litegraph)
        self.assertIn("normalized.originalEvent = event", self.litegraph)
        self.assertIn("if (!event || (typeof event !== \"object\" && typeof event !== \"function\"))", self.litegraph)
        self.assertIn("return null;", self.litegraph)
        self.assertNotIn("JSON.stringify(event)", self.litegraph)

    def test_primary_pointer_capture_cancel_and_lost_capture_clear_all_transient_state(self) -> None:
        for marker in (
            "_active_pointer_id",
            "_capturePointer",
            "setPointerCapture",
            "releasePointerCapture",
            'this._pointerListenerAdd(canvas,"cancel"',
            'this._pointerListenerAdd(canvas,"lostpointercapture"',
            "LGraphCanvas.prototype.processPointerCancel",
            "this._pointerListenerRemove(document, \"cancel\"",
            "this._pointerListenerRemove(document, \"lostpointercapture\"",
            "this.node_dragged = null",
            "this.resizing_node = null",
            "this.dragging_rectangle = null",
            "this.connecting_node = null",
            "this.pointer_is_down = false",
            "this.onPointerCancel",
        ):
            self.assertIn(marker, self.litegraph)
        self.assertIn("if (this.pointerevents_method === \"pointer\" && this._active_pointer_id !== null)", self.litegraph)
        self.assertIn("if (!this._acceptPointerEvent(e, true))", self.litegraph)

    def test_cancel_restores_pre_change_snapshot_without_history_or_autosave(self) -> None:
        start = self.studio.index("  handleCanvasPointerCancel()")
        end = self.studio.index("  bindConnectionPickerHook()", start)
        cancel_block = self.studio[start:end]
        self.assertIn("const before = this.beforeChange", cancel_block)
        self.assertIn("const graph = JSON.parse(before)", cancel_block)
        self.assertIn("this.hydrateLiteGraph(graph)", cancel_block)
        self.assertNotIn("persist(", cancel_block)
        self.assertNotIn("afterChange", cancel_block)
        self.assertIn("cancelNativeConnection", cancel_block)
        self.assertIn("closeConnectionPicker(false)", cancel_block)

    def test_destroy_and_graph_detach_unbind_before_pointer_state_is_cleared(self) -> None:
        destroy_start = self.studio.index("  destroy()")
        destroy_end = self.studio.index("  readLocalGraph()", destroy_start)
        destroy_block = self.studio[destroy_start:destroy_end]
        self.assertIn("this.liteCanvas?.setCanvas?.(null);", destroy_block)
        self.assertIn("this.liteCanvas?.setGraph?.(null);", destroy_block)
        self.assertLess(destroy_block.index("setCanvas"), destroy_block.index("setGraph"))
        self.assertNotIn("persist(", destroy_block)
        self.assertNotIn("afterChange", destroy_block)

        clear_start = self.litegraph.index("LGraphCanvas.prototype.clear = function()")
        clear_end = self.litegraph.index("this.frame = 0;", clear_start)
        clear_prefix = self.litegraph[clear_start:clear_end]
        self.assertIn("this._removeDocumentPointerListeners();", clear_prefix)
        self.assertIn("this._clearPointerInteractionState();", clear_prefix)

        unbind_start = self.litegraph.index("LGraphCanvas.prototype.unbindEvents = function()")
        unbind_end = self.litegraph.index("LGraphCanvas.getFileExtension", unbind_start)
        unbind_block = self.litegraph[unbind_start:unbind_end]
        self.assertIn("this._removeDocumentPointerListeners();", unbind_block)
        self.assertIn("this._clearPointerInteractionState();", unbind_block)
        self.assertLess(unbind_block.index("_clearPointerInteractionState"), unbind_block.index("this._active_pointer_id = null"))

        up_start = self.litegraph.index("LGraphCanvas.prototype.processMouseUp = function(e)")
        null_start = self.litegraph.index("if (!this.graph)", up_start)
        null_end = self.litegraph.index("var window = this.getCanvasWindow();", null_start)
        null_block = self.litegraph[null_start:null_end]
        self.assertIn("this._removeDocumentPointerListeners();", null_block)
        self.assertIn("this._clearPointerInteractionState();", null_block)
        self.assertNotIn("graph.change", null_block)
        self.assertNotIn("afterChange", null_block)
        self.assertIn("return false;", null_block)

    def test_connection_picker_rebinds_using_the_canvas_method(self) -> None:
        start = self.studio.index("  bindConnectionPickerHook()")
        end = self.studio.index("  openConnectionPicker(", start)
        hook = self.studio[start:end]
        self.assertIn("pointerListenerRemove(canvas.canvas, \"up\", original, true, pointereventsMethod)", hook)
        self.assertIn("pointerListenerRemove(rootDocument, \"up\", original, true, pointereventsMethod)", hook)
        self.assertIn("pointerListenerAdd(canvas.canvas, \"up\", wrapper, true, pointereventsMethod)", hook)

    def test_existing_desktop_and_no_execution_markers_remain(self) -> None:
        for marker in (
            "multi_select = false",
            "onMouse =",
            "bindConnectionPickerHook",
            "handleSelectionShortcut",
            "isTextControl",
            "isCanvasActive",
            "ArrowUp",
            "data-node-palette",
            "data-node-inspector",
            "NODE_UI_STATE_PREFIX",
            'execution: "not_run"',
        ):
            self.assertIn(marker, self.studio)
        self.assertIn("pointerevents_method: \"mouse\"", self.litegraph)

    def test_contract_document_is_static_and_bounded(self) -> None:
        for marker in (
            "PointerEvent",
            "pointercancel",
            "lostpointercapture",
            "setPointerCapture",
            "setCanvas(null)",
            "unbindEvents",
            "idempotent",
            "graph-null",
            "primary",
            "no duplicate",
            "execution=not_run",
            "no runtime",
            "litegraph.js",
            "studio.js",
        ):
            self.assertIn(marker, self.documentation)
        self.assertNotIn("download", self.documentation.lower())
        self.assertNotIn("GPU", self.documentation)


if __name__ == "__main__":
    unittest.main()
