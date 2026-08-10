import {
  cancelJob,
  escapeHtml,
  getDirtyNodes,
  getNodePreset,
  getNodePresets,
  getNodeRegistry,
  getNodeAvailability,
  formatStatus,
  getNodeRun,
  runNodeGraph,
  uploadFile,
  validateNodeGraph,
} from "./api.js";

// LiteGraph.js is intentionally pinned and served from /ui/vendor.  This file
// is the Hub adapter: it maps mature canvas-editor state to the Hub DAG API.
const LOCAL_PREFIX = "local-ai-hub-graph-v5";
const LEGACY_PREFIX = "local-ai-hub-node-studio-v1";
const WORKFLOW_INDEX_PREFIX = "local-ai-hub-workflows-v1";
const MAX_HISTORY = 60;
const MAX_RECENT_WORKFLOWS = 12;
const PRESET_BY_SCOPE = { image: "image_create_upscale", sam2: "sam2_segment", media: "video_creative_pipeline", animesr: "animesr_pipeline" };
const TYPE_COLORS = {
  IMAGE: "#cf7cff", MASK: "#42c6a0", VIDEO: "#f17c8e", AUDIO: "#f1ad5f",
  TEXT: "#6c8cff", NUMBER: "#a9c6ff", BOOLEAN: "#e5d66a", MODEL: "#e291c7", METADATA: "#8794ad",
};
const CATEGORY_COLORS = { utility: "#6c8cff", image: "#cf7cff", vision: "#42c6a0", media: "#f1ad5f", video: "#f17c8e", annotation: "#8794ad" };

const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const keyFor = (scope) => `${LOCAL_PREFIX}:${scope}`;
const legacyKeyFor = (scope) => `${LEGACY_PREFIX}:${scope}`;
const workflowIndexKey = (scope) => `${WORKFLOW_INDEX_PREFIX}:index:${scope}`;
const workflowGraphKey = (scope, id) => `${WORKFLOW_INDEX_PREFIX}:graph:${scope}:${id}`;
const asNumber = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const uid = () => `node_${globalThis.crypto?.randomUUID?.().replaceAll("-", "") || Math.random().toString(16).slice(2)}`;
const nowIso = () => new Date().toISOString();
const safeWorkflowId = (value, fallback = "workflow") => {
  const normalized = String(value || "").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
  return normalized || fallback;
};

function readWorkflowIndex(scope) {
  try {
    const value = JSON.parse(localStorage.getItem(workflowIndexKey(scope)) || "[]");
    return Array.isArray(value) ? value.filter((item) => item && typeof item.id === "string").slice(0, MAX_RECENT_WORKFLOWS) : [];
  } catch { return []; }
}

function writeWorkflowIndex(scope, value) {
  localStorage.setItem(workflowIndexKey(scope), JSON.stringify(value.slice(0, MAX_RECENT_WORKFLOWS)));
}

// Keep Recent rendering deterministic and independently testable.  The same
// bounded catalog drives the DOM refresh after duplicate, rename and save.
export function buildRecentWorkflowOptions(index, currentId = "") {
  return (Array.isArray(index) ? index : [])
    .filter((item) => item && typeof item.id === "string")
    .slice(0, MAX_RECENT_WORKFLOWS)
    .map((item) => ({
      id: item.id,
      title: String(item.title || item.id),
      source: String(item.source || "local"),
      selected: item.id === currentId,
    }));
}

// A Recipe is metadata, not an executable backend claim.  Applying it only
// fills the corresponding editable graph properties and leaves the graph
// unsaved until the creator explicitly confirms it.
export function applyRecipeToGraph(graph, application = {}) {
  const next = clone(graph) || {};
  const settings = application.settings && typeof application.settings === "object" ? application.settings : {};
  const prompt = String(application.prompt || "");
  const negative = String(application.negative_prompt || "");
  const seed = Number(application.seed);
  for (const node of next.nodes || []) {
    node.data ||= {};
    if (node.type === "prompt_text") node.data.text = prompt;
    if (["flux_generate", "qwen_image", "image_edit"].includes(node.type)) {
      if (negative) node.data.negative_prompt = negative;
      for (const key of ["width", "height", "steps"]) if (Number.isFinite(Number(settings[key]))) node.data[key] = Number(settings[key]);
      if (Number.isFinite(seed)) node.data.seed = seed;
    }
    if (node.type === "seed" && Number.isFinite(seed)) node.data.value = seed;
    if (node.type === "resolution") {
      for (const key of ["width", "height"]) if (Number.isFinite(Number(settings[key]))) node.data[key] = Number(settings[key]);
    }
    if (node.type === "sampler_settings" && Number.isFinite(Number(settings.steps))) node.data.steps = Number(settings.steps);
  }
  return next;
}

function emptyGraph(scope) {
  return { schema_version: 1, id: `local-${scope}`, title: `Workflow ${scope}`, scope, nodes: [], edges: [], groups: [] };
}

function firstArtifact(value) {
  if (!value || typeof value !== "object") return null;
  if (value.id && value.url) return value;
  for (const child of Object.values(value)) {
    const found = firstArtifact(child);
    if (found) return found;
  }
  return null;
}

function graphFingerprint(graph) {
  return JSON.stringify(graph);
}

function propertyControl(node, property) {
  const value = node.properties?.[property.name] ?? property.default ?? "";
  const target = `${node.id}:${property.name}`;
  const label = escapeHtml(property.label || property.name);
  if (property.kind === "asset") {
    return `<label class="graph-property"><span>${label}</span><small>${escapeHtml(value || "Chưa có artifact")}</small><input type="file" data-graph-asset="${escapeHtml(target)}" accept="${escapeHtml(property.accept || "")}" /></label>`;
  }
  if (property.kind === "boolean") {
    return `<label class="graph-property graph-property--toggle"><input type="checkbox" data-graph-property="${escapeHtml(target)}" ${value ? "checked" : ""} /><span>${label}</span></label>`;
  }
  if (property.kind === "select" || property.kind === "encoder") {
    const options = property.options || [];
    return `<label class="graph-property"><span>${label}</span><select data-graph-property="${escapeHtml(target)}">${options.map((option) => `<option value="${escapeHtml(option)}" ${String(option) === String(value) ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}</select></label>`;
  }
  if (property.kind === "textarea") {
    return `<label class="graph-property"><span>${label}</span><textarea data-graph-property="${escapeHtml(target)}">${escapeHtml(value)}</textarea></label>`;
  }
  const type = property.kind === "number" ? "number" : property.kind === "color" ? "color" : "text";
  const min = property.min === undefined ? "" : ` min="${escapeHtml(property.min)}"`;
  const max = property.max === undefined ? "" : ` max="${escapeHtml(property.max)}"`;
  const step = property.step === undefined ? "" : ` step="${escapeHtml(property.step)}"`;
  return `<label class="graph-property"><span>${label}</span><input type="${type}" data-graph-property="${escapeHtml(target)}" value="${escapeHtml(value)}"${min}${max}${step} /></label>`;
}

class HubGraphEditor {
  constructor(root, { showToast, recipeApplication = null, initialPresetId = null, onRecipeApplied = () => {}, onPresetApplied = () => {} }) {
    this.root = root;
    this.scope = root.dataset.scope || "image";
    this.showToast = showToast;
    this.recipeApplication = recipeApplication;
    this.initialPresetId = initialPresetId;
    this.onRecipeApplied = onRecipeApplied;
    this.onPresetApplied = onPresetApplied;
    this.registry = new Map();
    this.availability = { counts: {}, nodes: [] };
    this.presets = [];
    this.workflowIndex = [];
    this.graphData = emptyGraph(this.scope);
    this.groups = [];
    this.history = [];
    this.future = [];
    this.dirty = new Set();
    this.nodeStates = new Map();
    this.activeJobId = null;
    this.autoPreview = localStorage.getItem(`${keyFor(this.scope)}:auto`) === "true";
    this.draft = localStorage.getItem(`${keyFor(this.scope)}:draft`) !== "false";
    this.search = "";
    this.hydrating = false;
    this.beforeChange = null;
    this.pollTimer = null;
    this.autoTimer = null;
    this.resizeObserver = null;
    this.abort = new AbortController();
    this.minimapBounds = null;
    this.validation = null;
    this.runStatus = "idle";
    this.savedFingerprint = "";
    this.unsaved = false;
    this.recovered = false;
    this.autosavedAt = null;
  }

  async initialize() {
    this.root.innerHTML = `<div class="graph-editor-loading">Đang nạp graph editor offline…</div>`;
    if (!globalThis.LiteGraph) {
      this.root.innerHTML = `<div class="callout callout--warning">Thiếu LiteGraph offline. Kiểm tra file <code>/ui/vendor/litegraph.js</code>.</div>`;
      return;
    }
    try {
      const [registryPayload, presetPayload, availabilityPayload] = await Promise.all([getNodeRegistry(this.scope), getNodePresets(), getNodeAvailability(this.scope)]);
      this.registry = new Map((registryPayload.nodes || []).map((item) => [item.type, item]));
      this.availability = availabilityPayload.availability || registryPayload.availability || { counts: {}, nodes: [] };
      this.presets = (presetPayload.presets || []).filter((item) => item.scope === this.scope);
      this.workflowIndex = readWorkflowIndex(this.scope);
      this.configureLiteGraph();
      const saved = this.readLocalGraph();
      if (this.initialPresetId) {
        await this.loadPreset(this.initialPresetId, { quiet: true, render: false });
        this.recovered = false;
        this.onPresetApplied(this.initialPresetId);
      } else if (saved) { this.graphData = saved; this.recovered = true; }
      else await this.loadPreset(PRESET_BY_SCOPE[this.scope], { quiet: true, render: false });
      if (this.recipeApplication && this.scope === "image") {
        this.graphData = applyRecipeToGraph(this.graphData, this.recipeApplication);
        this.recovered = false;
        this.unsaved = true;
        this.persist({ source: "recipe" });
        this.onRecipeApplied(this.recipeApplication);
      }
      this.savedFingerprint = graphFingerprint(this.graphData);
      this.unsaved = this.unsaved || !this.recovered;
      this.dirty = new Set(this.graphData.nodes.map((node) => node.id));
      this.renderShell();
      this.hydrateLiteGraph(this.graphData);
      this.renderWorkflowStatus();
      if (this.recovered) this.showToast("Đã khôi phục bản autosave local của workflow.");
    } catch (error) {
      this.root.innerHTML = `<div class="callout callout--warning">Không thể nạp graph editor: ${escapeHtml(error.message)}</div>`;
    }
  }

  destroy() {
    this.abort.abort();
    if (this.pollTimer) clearInterval(this.pollTimer);
    if (this.autoTimer) clearTimeout(this.autoTimer);
    this.resizeObserver?.disconnect();
    this.liteCanvas?.stopRendering?.();
    this.liteCanvas?.setGraph?.(null);
  }

  readLocalGraph() {
    for (const storageKey of [keyFor(this.scope), legacyKeyFor(this.scope)]) {
      try {
        const value = JSON.parse(localStorage.getItem(storageKey) || "null");
        if (value?.schema_version === 1 && Array.isArray(value.nodes) && Array.isArray(value.edges)) return value;
      } catch { /* ignore a malformed private WebView value */ }
    }
    return null;
  }

  isUnsaved() {
    return this.unsaved || (Boolean(this.savedFingerprint) && graphFingerprint(this.toHubGraph()) !== this.savedFingerprint);
  }

  rememberWorkflow(graph, { source = "autosave", saved = false } = {}) {
    const id = safeWorkflowId(graph.id, `local-${this.scope}`);
    const record = {
      id,
      title: String(graph.title || id).slice(0, 160),
      scope: this.scope,
      updated_at: nowIso(),
      saved_at: saved ? nowIso() : null,
      source,
    };
    localStorage.setItem(workflowGraphKey(this.scope, id), JSON.stringify(graph));
    this.workflowIndex = [record, ...this.workflowIndex.filter((item) => item.id !== id)].slice(0, MAX_RECENT_WORKFLOWS);
    writeWorkflowIndex(this.scope, this.workflowIndex);
    return record;
  }

  persist({ source = "autosave", saved = false } = {}) {
    const graph = this.toHubGraph();
    graph.id = safeWorkflowId(graph.id, `local-${this.scope}`);
    graph.title = String(graph.title || `Workflow ${this.scope}`).slice(0, 160);
    this.graphData = { ...this.graphData, id: graph.id, title: graph.title };
    localStorage.setItem(keyFor(this.scope), JSON.stringify(graph));
    localStorage.setItem(`${keyFor(this.scope)}:auto`, String(this.autoPreview));
    localStorage.setItem(`${keyFor(this.scope)}:draft`, String(this.draft));
    this.autosavedAt = nowIso();
    this.rememberWorkflow(graph, { source, saved });
    if (saved) { this.savedFingerprint = graphFingerprint(graph); this.unsaved = false; }
    this.renderWorkflowStatus();
  }

  saveLocal() {
    this.persist({ source: "saved", saved: true });
    this.showToast("Workflow đã lưu local và có thể khôi phục trong Recent.");
  }

  renameWorkflow(value) {
    const title = String(value || "").trim().slice(0, 160);
    if (!title || title === this.graphData.title) return;
    this.graphData = { ...this.graphData, title };
    this.persist({ source: "rename" });
    this.renderShellTitle();
  }

  renderShellTitle() {
    const title = this.root.querySelector("[data-graph-title]");
    if (title && title.value !== this.graphData.title) title.value = this.graphData.title || "";
    const heading = this.root.querySelector(".graph-editor__header h2");
    if (heading) heading.textContent = this.graphData.title || `Workflow ${this.scope}`;
    this.refreshRecentControls();
  }

  refreshRecentControls() {
    const recent = this.root?.querySelector("[data-graph-recent]");
    if (!recent) return;
    recent.innerHTML = `<option value="">Chọn workflow local…</option>${this.recentOptions()}`;
    recent.value = this.graphData.id || "";
  }

  renderWorkflowStatus() {
    const target = this.root?.querySelector("[data-graph-save-state]");
    if (!target) return;
    const unsaved = this.isUnsaved();
    target.dataset.state = unsaved ? "unsaved" : "saved";
    target.textContent = unsaved ? "Có thay đổi chưa lưu" : this.recovered ? "Đã khôi phục autosave" : "Đã lưu local";
    this.refreshRecentControls();
  }

  recentOptions() {
    return buildRecentWorkflowOptions(this.workflowIndex, this.graphData.id)
      .map((item) => `<option value="${escapeHtml(item.id)}" ${item.selected ? "selected" : ""}>${escapeHtml(item.title)} · ${escapeHtml(item.source)}</option>`)
      .join("");
  }

  async loadRecent(id) {
    if (!id) return;
    try {
      const stored = localStorage.getItem(workflowGraphKey(this.scope, id));
      if (!stored) throw new Error("Không tìm thấy workflow local này.");
      const result = await validateNodeGraph(JSON.parse(stored), false);
      if (!result.validation?.valid) throw new Error(result.validation?.errors?.[0]?.message || "Workflow local không còn hợp lệ.");
      this.history = [];
      this.future = [];
      this.nodeStates.clear();
      this.hydrateLiteGraph(result.validation.graph);
      this.savedFingerprint = graphFingerprint(result.validation.graph);
      this.unsaved = false;
      this.dirty = new Set(result.validation.graph.nodes.map((node) => node.id));
      this.recovered = true;
      this.persist({ source: "recent", saved: true });
      this.showToast("Đã mở workflow trong Recent.");
    } catch (error) { this.showToast(error.message, "error"); }
  }

  duplicateWorkflow() {
    const copy = clone(this.toHubGraph());
    copy.id = `${safeWorkflowId(copy.id, `local-${this.scope}`)}-copy-${Date.now().toString(36)}`.slice(0, 80);
    copy.title = `${copy.title || "Workflow"} (bản sao)`;
    this.history = [];
    this.future = [];
    this.nodeStates.clear();
    this.hydrateLiteGraph(copy);
    this.savedFingerprint = "";
    this.unsaved = true;
    this.recovered = false;
    this.dirty = new Set(copy.nodes.map((node) => node.id));
    this.persist({ source: "duplicate" });
    this.renderShellTitle();
    this.renderWorkflowStatus();
    this.showToast("Đã tạo bản sao workflow; bấm Lưu local để xác nhận tên mới.");
  }

  configureLiteGraph() {
    const { LiteGraph } = globalThis;
    LiteGraph.NODE_TEXT_SIZE = 14;
    LiteGraph.NODE_SLOT_HEIGHT = 22;
    Object.assign(LiteGraph.LGraphCanvas.link_type_colors, TYPE_COLORS);
    for (const definition of this.registry.values()) {
      const typeName = `local-ai-hub/${definition.type}`;
      if (LiteGraph.registered_node_types[typeName]) continue;
      const captured = definition;
      function HubLiteNode() {
        this.title = captured.title;
        this.hubType = captured.type;
        this.properties = Object.fromEntries((captured.properties || []).map((property) => [property.name, clone(property.default)]));
        for (const port of captured.inputs || []) {
          this.addInput(port.label || port.name, port.type, { hubPort: port.name, required: Boolean(port.required), multi: Boolean(port.multi) });
          this.inputs[this.inputs.length - 1].hubPort = port.name;
        }
        for (const port of captured.outputs || []) {
          this.addOutput(port.label || port.name, port.type, { hubPort: port.name });
          this.outputs[this.outputs.length - 1].hubPort = port.name;
        }
        this.color = CATEGORY_COLORS[captured.category] || "#8794ad";
        this.bgcolor = "#172039";
        this.shape = "round";
        this.size = [230, Math.max(82, 42 + Math.max((captured.inputs || []).length, (captured.outputs || []).length) * 22)];
      }
      HubLiteNode.title = captured.title;
      HubLiteNode.desc = captured.description;
      HubLiteNode.prototype.onDrawForeground = function drawHubNodeForeground(ctx) {
        if (this.hubStatus && this.hubStatus !== "completed") {
          ctx.save();
          ctx.fillStyle = this.hubStatus === "failed" || this.hubStatus === "error" ? "#ef7885" : "#80aaff";
          ctx.fillRect(this.size[0] - 12, 8, 5, 5);
          ctx.restore();
        }
      };
      LiteGraph.registerNodeType(typeName, HubLiteNode);
    }
  }

  renderShell() {
    this.root.innerHTML = `
      <section class="graph-editor" aria-label="Hub Nodes ${escapeHtml(this.scope)}">
        <header class="graph-editor__header">
          <div><span class="eyebrow">NODE WORKFLOW</span><h2>${escapeHtml(this.graphData.title || `Image ${this.scope}`)}</h2><p>Canvas typed socket cho người mới: nối đúng kiểu dữ liệu, kiểm tra trước khi chạy và luôn thấy trạng thái backend.</p></div>
          <div class="graph-editor__header-status" data-graph-summary><span class="status-pill" data-status="idle">Chưa chạy</span><span class="tag">${escapeHtml(this.scope)}</span></div>
        </header>
        <div class="graph-editor__workflow-bar"><label class="graph-workflow-title"><span>Tên workflow</span><input data-graph-title aria-label="Tên workflow" value="${escapeHtml(this.graphData.title || "")}" /></label><label class="graph-workflow-recent"><span>Recent</span><select data-graph-recent aria-label="Recent workflows"><option value="">Chọn workflow local…</option>${this.recentOptions()}</select></label><span class="graph-save-state" data-graph-save-state>Đã lưu local</span><button class="button button--compact" type="button" data-graph-action="duplicate">Nhân bản</button></div>
        <div class="graph-editor__toolbar">
          <div class="graph-editor__toolbar-group"><button class="button button--primary" type="button" data-graph-action="run" aria-label="Run Graph">Chạy workflow</button><button class="button" type="button" data-graph-action="validate">Kiểm tra</button><button class="button" type="button" data-graph-action="cancel" disabled>Hủy job</button><button class="button" type="button" data-graph-action="undo">Hoàn tác</button><button class="button" type="button" data-graph-action="redo">Làm lại</button></div>
          <div class="graph-editor__toolbar-group"><select data-graph-preset aria-label="Preset workflow"><option value="">Chọn template…</option>${this.presets.map((item) => `<option value="${escapeHtml(item.id)}" title="${escapeHtml(item.description || "")}">${escapeHtml(item.title)}${item.stage ? ` · ${escapeHtml(item.stage)}` : ""}</option>`).join("")}</select><button class="button" type="button" data-graph-action="save-local">Lưu local</button><button class="button" type="button" data-graph-action="export">Export JSON</button><label class="button graph-editor__import">Import JSON<input type="file" data-graph-import accept="application/json,.json" /></label></div>
        </div>
        <div class="graph-editor__options"><label><input type="checkbox" data-graph-option="auto" ${this.autoPreview ? "checked" : ""} /> Preview tự động (Auto Preview)</label><label><input type="checkbox" data-graph-option="draft" ${this.draft ? "checked" : ""} /> Draft ảnh</label><span>Bấm node để cộng dồn lựa chọn · Ctrl/Shift cũng cộng dồn · kéo nhóm để di chuyển · kéo vùng để chọn · bấm nền trống, Esc hoặc Xóa chọn để bỏ chọn</span></div>
        <div class="graph-editor__statusbar"><span data-graph-validation>Chưa kiểm tra workflow.</span><span class="graph-editor__availability">${this.availability.counts?.operational || 0} sẵn sàng · ${this.availability.counts?.partial || 0} partial · ${this.availability.counts?.unavailable || 0} unavailable</span></div>
        <div class="graph-editor__layout">
          <aside class="graph-palette"><input type="search" data-graph-search placeholder="Tìm node…" aria-label="Tìm node" /><div data-graph-palette></div></aside>
          <div class="graph-canvas-shell"><canvas class="graph-canvas" data-graph-canvas></canvas><div class="graph-canvas__actions"><button type="button" data-graph-action="fit">Fit</button><button type="button" data-graph-action="clear-selection">Bỏ chọn</button><button type="button" data-graph-action="delete">Xóa chọn</button></div><canvas class="graph-minimap" data-graph-minimap width="180" height="118" aria-label="Minimap graph"></canvas></div>
          <aside class="graph-inspector" data-graph-inspector></aside>
        </div>
      </section>`;
    this.canvasElement = this.root.querySelector("[data-graph-canvas]");
    this.minimap = this.root.querySelector("[data-graph-minimap]");
    this.paletteElement = this.root.querySelector("[data-graph-palette]");
    this.inspectorElement = this.root.querySelector("[data-graph-inspector]");
    this.liteGraph = new globalThis.LiteGraph.LGraph();
    this.liteCanvas = new globalThis.LiteGraph.LGraphCanvas(this.canvasElement, this.liteGraph, { autoresize: false });
    this.liteCanvas.allow_dragcanvas = true;
    this.liteCanvas.allow_dragnodes = true;
    this.liteCanvas.allow_reconnect_links = true;
    this.liteCanvas.allow_searchbox = true;
    // LiteGraph's true mode makes a plain click additive.  Ctrl/Shift stay
    // additive too; explicit clear paths below keep the selection reversible.
    this.liteCanvas.multi_select = true;
    this.liteCanvas.render_shadows = true;
    this.liteCanvas.render_connections_border = true;
    this.liteCanvas.links_render_mode = globalThis.LiteGraph.SPLINE_LINK;
    this.liteCanvas.onBeforeChange = () => this.captureBeforeChange();
    this.liteCanvas.onAfterChange = () => this.captureAfterChange();
    this.liteCanvas.onSelectionChange = () => { this.renderInspector(); this.drawMinimap(); };
    this.liteCanvas.onNodeMoved = () => this.drawMinimap();
    this.bindCanvasShortcuts();
    this.bindEvents();
    this.renderPalette();
    this.renderInspector();
    this.renderGraphStatus();
    this.resizeObserver = new ResizeObserver(() => this.resizeCanvas());
    this.resizeObserver.observe(this.canvasElement.parentElement);
    this.resizeCanvas();
  }

  bindEvents() {
    const { signal } = this.abort;
    this.root.addEventListener("click", (event) => {
      const action = event.target.closest("[data-graph-action]")?.dataset.graphAction;
      if (action) this.handleAction(action);
      const add = event.target.closest("[data-graph-add]")?.dataset.graphAdd;
      if (add) this.addNode(add);
    }, { signal });
    this.root.addEventListener("input", (event) => {
      if (event.target.matches("[data-graph-search]")) { this.search = event.target.value; this.renderPalette(); }
      if (event.target.matches("[data-graph-title]")) this.renameWorkflow(event.target.value);
    }, { signal });
    this.root.addEventListener("change", (event) => {
      const option = event.target.dataset.graphOption;
      if (option === "auto") { this.autoPreview = event.target.checked; this.persist(); return; }
      if (option === "draft") { this.draft = event.target.checked; this.persist(); return; }
      if (event.target.matches("[data-graph-recent]")) { this.loadRecent(event.target.value); return; }
      if (event.target.matches("[data-graph-preset]")) { this.loadPreset(event.target.value); return; }
      if (event.target.matches("[data-graph-property]")) { this.changeProperty(event.target); return; }
      if (event.target.matches("[data-graph-asset]")) { this.uploadAsset(event.target); return; }
      if (event.target.matches("[data-graph-import]")) { this.importGraph(event.target.files?.[0]); }
    }, { signal });
    this.minimap.addEventListener("pointerdown", (event) => this.recenterFromMinimap(event), { signal });
    // LiteGraph owns a capture-phase canvas key handler.  Handle the Hub
    // shortcuts from document capture first, otherwise LiteGraph prevents the
    // Escape/Delete event before this adapter can make selection state and
    // persistence consistent.
    window.addEventListener("keydown", (event) => {
      if (!this.root.isConnected || /INPUT|TEXTAREA|SELECT/.test(event.target?.tagName || "")) return;
      if (this.handleSelectionShortcut(event)) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? this.redo() : this.undo(); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") { event.preventDefault(); this.redo(); }
    }, { signal, capture: true });
    window.addEventListener("beforeunload", (event) => {
      if (!this.root.isConnected || !this.isUnsaved()) return;
      event.preventDefault();
      event.returnValue = "";
    }, { signal });
  }

  bindCanvasShortcuts() {
    // LiteGraph owns the canvas capture listener and stops Escape/Delete.
    // Wrap that exact listener instead of relying on a later DOM listener;
    // this preserves all vendor shortcuts while making Hub selection clear
    // and delete deterministic.
    const liteGraphKeyHandler = this.liteCanvas?._key_callback;
    if (!liteGraphKeyHandler) return;
    const hubAwareKeyHandler = (event) => this.handleSelectionShortcut(event) || liteGraphKeyHandler(event);
    this.canvasElement.removeEventListener("keydown", liteGraphKeyHandler, true);
    this.liteCanvas._key_callback = hubAwareKeyHandler;
    this.canvasElement.addEventListener("keydown", hubAwareKeyHandler, true);
  }

  handleSelectionShortcut(event) {
    if (/INPUT|TEXTAREA|SELECT/.test(event.target?.tagName || "")) return false;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      this.clearSelection();
      return true;
    }
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      event.stopImmediatePropagation();
      this.deleteSelected();
      return true;
    }
    return false;
  }

  resizeCanvas() {
    if (!this.liteCanvas || !this.canvasElement.isConnected) return;
    const box = this.canvasElement.parentElement.getBoundingClientRect();
    this.liteCanvas.resize(Math.max(320, Math.floor(box.width)), Math.max(340, Math.floor(box.height)));
    this.drawMinimap();
  }

  renderPalette() {
    if (!this.paletteElement) return;
    const needle = this.search.trim().toLocaleLowerCase();
    const nodes = [...this.registry.values()].filter((definition) => !needle || `${definition.title} ${definition.category} ${definition.description}`.toLocaleLowerCase().includes(needle));
    const groups = nodes.reduce((result, definition) => {
      (result[definition.category] ||= []).push(definition);
      return result;
    }, {});
    this.paletteElement.innerHTML = Object.entries(groups).map(([category, definitions]) => `<section class="graph-palette__group"><h3>${escapeHtml(category)}</h3>${definitions.map((definition) => {
      const availability = definition.availability || { status: definition.status || "operational", reason: "" };
      return `<button type="button" class="graph-palette__item" data-graph-add="${escapeHtml(definition.type)}" title="${escapeHtml(availability.reason || definition.description || "")}"><i style="--node-color:${escapeHtml(CATEGORY_COLORS[definition.category] || "#8794ad")}"></i><span><b>${escapeHtml(definition.title)}</b><small>${escapeHtml(availability.status)} · ${escapeHtml(availability.reason || definition.description || "")}</small></span></button>`;
    }).join("")}</section>`).join("") || `<p class="graph-empty">Không tìm thấy node.</p>`;
  }

  renderGraphStatus() {
    const summary = this.root.querySelector("[data-graph-summary]");
    const validation = this.root.querySelector("[data-graph-validation]");
    const nodes = this.toHubGraph().nodes || [];
    const status = this.runStatus || "idle";
    if (summary) summary.innerHTML = `<span class="status-pill" data-status="${escapeHtml(status)}">${escapeHtml(status === "idle" ? "Chưa chạy" : formatStatus(status))}</span><span class="tag">${nodes.length} node · ${this.dirty.size} cần chạy</span>`;
    if (validation) {
      const errors = this.validation?.errors || [];
      validation.textContent = errors.length ? `${errors.length} lỗi cần sửa: ${errors[0].message || errors[0].code}` : (this.validation ? "Workflow hợp lệ để lưu; bấm Chạy workflow để kiểm tra input bắt buộc." : "Chưa kiểm tra workflow.");
      validation.className = errors.length ? "graph-editor__validation graph-editor__validation--error" : "graph-editor__validation";
    }
    this.renderWorkflowStatus();
  }

  renderInspector() {
    if (!this.inspectorElement) return;
    const nodes = this.selectedNodes();
    if (!nodes.length) {
      this.inspectorElement.innerHTML = `<div class="graph-inspector__empty"><strong>Inspector / Live preview</strong><p>Chọn node để chỉnh thông số và xem output. Kết nối trực tiếp từ socket sang socket.</p><div class="graph-type-legend">${Object.entries(TYPE_COLORS).map(([type, color]) => `<span><i style="--node-color:${color}"></i>${type}</span>`).join("")}</div><p>Minimap, pan/zoom, undo/redo và layout do canvas xử lý.</p></div>`;
      return;
    }
    if (nodes.length > 1) {
      this.inspectorElement.innerHTML = `<div class="graph-inspector__empty"><strong>${nodes.length} node đang được chọn</strong><p>Kéo các node cùng lúc, dùng Delete để xóa hoặc Ctrl+Z để hoàn tác.</p></div>`;
      return;
    }
    const node = nodes[0];
    const definition = this.registry.get(node.hubType);
    const state = this.nodeStates.get(node.hubId) || {};
    const artifact = firstArtifact(state.output);
    const preview = artifact?.url ? (String(artifact.media_type || "").startsWith("image/")
      ? `<img class="graph-preview-image" src="${escapeHtml(artifact.url)}" alt="${escapeHtml(artifact.name || "Output")}" />`
      : `<button class="button button--compact" type="button" data-preview-artifact="${escapeHtml(artifact.id || "")}" data-artifact-url="${escapeHtml(artifact.url)}" data-artifact-name="${escapeHtml(artifact.name || "Output")}" data-artifact-type="${escapeHtml(artifact.media_type || "application/octet-stream")}">Mở output</button>`) : "";
    const availability = definition?.availability || { status: definition?.status || "operational", reason: "", action: "" };
    const action = state.next_action || availability.action;
    this.inspectorElement.innerHTML = `<div class="graph-inspector__head"><div><span class="tag">${escapeHtml(definition?.category || "node")}</span><h3>${escapeHtml(definition?.title || node.hubType)}</h3><p>${escapeHtml(definition?.description || "")}</p></div><div class="graph-node-state" data-status="${escapeHtml(state.status || availability.status)}"><b>${escapeHtml(state.status || availability.status)}</b><span>${escapeHtml(state.message || state.error || availability.reason || "")}</span></div></div>${action ? `<div class="graph-action-hint"><strong>Bước tiếp theo</strong><span>${escapeHtml(action)}</span></div>` : ""}${preview ? `<section class="graph-inspector__section"><strong>Live preview</strong>${preview}</section>` : ""}<section class="graph-inspector__section"><strong>Thông số</strong>${(definition?.properties || []).map((property) => propertyControl(node, property)).join("") || `<p class="graph-empty">Node này không có property.</p>`}</section>`;
  }

  selectedNodes() {
    return Object.values(this.liteCanvas?.selected_nodes || {});
  }

  captureBeforeChange() {
    if (this.hydrating || this.beforeChange) return;
    this.beforeChange = graphFingerprint(this.toHubGraph());
  }

  captureAfterChange() {
    if (this.hydrating) return;
    const next = this.toHubGraph();
    const after = graphFingerprint(next);
    if (this.beforeChange && this.beforeChange !== after) {
      this.history.push(this.beforeChange);
      if (this.history.length > MAX_HISTORY) this.history.shift();
      this.future = [];
    }
    this.beforeChange = null;
    this.graphData = next;
    this.unsaved = true;
    this.markDirty(next.nodes.map((node) => node.id));
    this.persist();
    this.renderInspector();
    this.drawMinimap();
    this.renderGraphStatus();
    this.scheduleAutoPreview();
  }

  mutate(callback) {
    this.liteGraph.beforeChange();
    callback();
    this.liteGraph.afterChange();
  }

  addNode(type) {
    const definition = this.registry.get(type);
    if (!definition) return;
    const node = globalThis.LiteGraph.createNode(`local-ai-hub/${type}`);
    if (!node) return;
    node.hubType = type;
    node.hubId = uid();
    const count = this.liteGraph._nodes.length;
    node.pos = [260 + (count % 5) * 46, 130 + (count % 7) * 34];
    this.mutate(() => this.liteGraph.add(node));
    this.liteCanvas.selectNode(node);
    this.liteCanvas.centerOnNode(node);
  }

  deleteSelected() {
    const selected = this.selectedNodes();
    if (!selected.length) return;
    this.mutate(() => selected.forEach((node) => this.liteGraph.remove(node)));
    this.liteCanvas.deselectAllNodes();
  }

  clearSelection() {
    if (!this.liteCanvas) return;
    this.liteCanvas.deselectAllNodes();
    this.renderInspector();
    this.drawMinimap();
  }

  changeProperty(element) {
    const [liteId, name] = element.dataset.graphProperty.split(":");
    const node = this.liteGraph.getNodeById(Number(liteId));
    const definition = node && this.registry.get(node.hubType);
    const property = definition?.properties?.find((item) => item.name === name);
    if (!node || !property) return;
    let value = element.type === "checkbox" ? element.checked : element.value;
    if (property.kind === "number") value = asNumber(value, property.default ?? 0);
    this.mutate(() => { node.properties[name] = value; node.setDirtyCanvas(true, true); });
  }

  async uploadAsset(input) {
    const [liteId, name] = input.dataset.graphAsset.split(":");
    const node = this.liteGraph.getNodeById(Number(liteId));
    const file = input.files?.[0];
    if (!node || !file) return;
    input.disabled = true;
    try {
      const artifact = await uploadFile(file);
      this.mutate(() => { node.properties[name] = artifact.id; node.setDirtyCanvas(true, true); });
      this.showToast(`Đã dùng artifact ${artifact.name} trong node.`);
    } catch (error) {
      this.showToast(error.message, "error");
    } finally {
      input.disabled = false;
    }
  }

  toHubGraph() {
    if (!this.liteGraph) return clone(this.graphData);
    const nodeByLiteId = new Map();
    const nodes = this.liteGraph._nodes.map((node) => {
      const data = Object.fromEntries(Object.entries(node.properties || {}).filter(([key]) => !key.startsWith("__")));
      const value = {
        id: node.hubId || `node_${node.id}`,
        type: node.hubType || String(node.type || "").split("/").pop(),
        position: { x: Math.round(asNumber(node.pos?.[0], 0)), y: Math.round(asNumber(node.pos?.[1], 0)) },
        data,
      };
      nodeByLiteId.set(node.id, { node, value });
      return value;
    });
    const edges = Object.values(this.liteGraph.links || {}).map((link) => {
      const source = nodeByLiteId.get(link.origin_id);
      const target = nodeByLiteId.get(link.target_id);
      const sourcePort = source?.node.outputs?.[link.origin_slot]?.hubPort;
      const targetPort = target?.node.inputs?.[link.target_slot]?.hubPort;
      if (!source || !target || !sourcePort || !targetPort) return null;
      return { id: `edge_${source.value.id}_${sourcePort}_${target.value.id}_${targetPort}`, source: { node: source.value.id, port: sourcePort }, target: { node: target.value.id, port: targetPort } };
    }).filter(Boolean);
    return { schema_version: 1, id: this.graphData.id || `local-${this.scope}`, title: this.graphData.title || `Workflow ${this.scope}`, scope: this.scope, nodes, edges, groups: clone(this.groups || []) };
  }

  hydrateLiteGraph(graph) {
    this.hydrating = true;
    this.liteGraph.clear();
    this.liteCanvas.clear();
    this.groups = clone(graph.groups || []);
    const byHubId = new Map();
    for (const source of graph.nodes || []) {
      const type = String(source.type || "");
      if (!this.registry.has(type)) continue;
      const node = globalThis.LiteGraph.createNode(`local-ai-hub/${type}`);
      if (!node) continue;
      node.hubType = type;
      node.hubId = String(source.id || uid());
      node.pos = [asNumber(source.position?.x, 80), asNumber(source.position?.y, 80)];
      node.properties = { ...node.properties, ...(source.data || {}) };
      this.liteGraph.add(node);
      byHubId.set(node.hubId, node);
    }
    for (const edge of graph.edges || []) {
      const source = byHubId.get(edge.source?.node);
      const target = byHubId.get(edge.target?.node);
      const sourceSlot = source?.outputs?.findIndex((port) => port.hubPort === edge.source?.port) ?? -1;
      const targetSlot = target?.inputs?.findIndex((port) => port.hubPort === edge.target?.port) ?? -1;
      if (source && target && sourceSlot >= 0 && targetSlot >= 0) source.connect(sourceSlot, target, targetSlot);
    }
    this.graphData = clone(graph);
    this.hydrating = false;
    this.liteCanvas.setDirty(true, true);
    if (this.liteGraph._nodes.length) this.fitView();
    this.renderInspector();
    this.drawMinimap();
    this.renderGraphStatus();
  }

  async loadPreset(id, { quiet = false, render = true } = {}) {
    if (!id) return;
    try {
      const result = await getNodePreset(id);
      if (!result.validation?.valid) throw new Error(result.validation?.errors?.[0]?.message || "Preset không qua schema validation.");
      this.history = [];
      this.future = [];
      this.nodeStates.clear();
      this.dirty = new Set((result.graph.nodes || []).map((node) => node.id));
      this.graphData = result.graph;
      this.savedFingerprint = "";
      this.recovered = false;
      this.unsaved = true;
      if (render && this.liteGraph) this.hydrateLiteGraph(result.graph);
      this.persist({ source: "template" });
      if (!quiet) this.showToast("Đã nạp preset workflow Hub.");
    } catch (error) {
      if (!quiet) this.showToast(error.message, "error");
    }
  }

  undo() {
    const previous = this.history.pop();
    if (!previous) return;
    this.future.push(graphFingerprint(this.toHubGraph()));
    const graph = JSON.parse(previous);
    this.hydrateLiteGraph(graph);
    this.unsaved = true;
    this.dirty = new Set(graph.nodes.map((node) => node.id));
    this.persist();
  }

  redo() {
    const next = this.future.pop();
    if (!next) return;
    this.history.push(graphFingerprint(this.toHubGraph()));
    const graph = JSON.parse(next);
    this.hydrateLiteGraph(graph);
    this.unsaved = true;
    this.dirty = new Set(graph.nodes.map((node) => node.id));
    this.persist();
  }

  markDirty(changedIds) {
    const graph = this.toHubGraph();
    const changed = new Set(changedIds);
    const pending = [...changed];
    while (pending.length) {
      const current = pending.shift();
      for (const edge of graph.edges) {
        if (edge.source.node === current && !changed.has(edge.target.node)) { changed.add(edge.target.node); pending.push(edge.target.node); }
      }
    }
    changed.forEach((id) => this.dirty.add(id));
    getDirtyNodes(graph, [...changed]).then((result) => {
      if (result.valid) result.dirty_nodes.forEach((id) => this.dirty.add(id));
    }).catch(() => {});
  }

  scheduleAutoPreview() {
    if (!this.autoPreview) return;
    const graph = this.toHubGraph();
    const hasHeavy = graph.nodes.some((node) => this.registry.get(node.type)?.heavy);
    if (hasHeavy && !this.draft) return;
    if (this.autoTimer) clearTimeout(this.autoTimer);
    this.autoTimer = setTimeout(() => this.run({ auto: true }), 450);
  }

  async run({ auto = false } = {}) {
    if (this.activeJobId) return;
    try {
      const graph = this.toHubGraph();
      const validation = await validateNodeGraph(graph, true);
      this.validation = validation.validation || null;
      this.renderGraphStatus();
      if (!validation.validation?.valid) throw new Error(validation.validation?.errors?.[0]?.message || "Graph không hợp lệ.");
      const result = await runNodeGraph(validation.validation.graph, Boolean(auto && this.draft));
      this.activeJobId = result.job?.id || null;
      this.runStatus = result.job?.status || "queued";
      this.updateToolbar();
      this.renderGraphStatus();
      if (this.activeJobId) {
        this.showToast(`Đã tạo ${this.activeJobId}.`);
        this.startPoll();
      }
    } catch (error) {
      this.showToast(error.message, "error");
    }
  }

  async cancel() {
    if (!this.activeJobId) return;
    try { await cancelJob(this.activeJobId); this.showToast("Đang hủy graph job do Hub sở hữu."); }
    catch (error) { this.showToast(error.message, "error"); }
  }

  startPoll() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    const poll = async () => {
      if (!this.activeJobId) return;
      try {
        const response = await getNodeRun(this.activeJobId);
        const run = response.run || {};
        this.runStatus = run.status || this.runStatus;
        for (const state of run.nodes || []) {
          this.nodeStates.set(state.id, state);
          const node = this.liteGraph._nodes.find((candidate) => candidate.hubId === state.id);
          if (node) node.hubStatus = state.status;
        }
        this.liteCanvas.setDirty(true, true);
        this.renderInspector();
        if (["completed", "failed", "error", "cancelled", "unavailable"].includes(run.status)) {
          clearInterval(this.pollTimer);
          this.pollTimer = null;
          this.activeJobId = null;
          if (run.status === "completed") this.dirty.clear();
          this.validation = run.status === "completed" ? { valid: true, errors: [] } : this.validation;
          this.persist();
          this.updateToolbar();
        }
        this.renderGraphStatus();
      } catch { /* the background job may still be entering its worker thread */ }
    };
    poll();
    this.pollTimer = setInterval(poll, 2000);
  }

  updateToolbar() {
    const cancel = this.root.querySelector('[data-graph-action="cancel"]');
    if (cancel) cancel.disabled = !this.activeJobId;
  }

  async validate(requireRunnable = false) {
    try {
      const response = await validateNodeGraph(this.toHubGraph(), requireRunnable);
      this.validation = response.validation || null;
      this.renderGraphStatus();
      if (this.validation?.valid) this.showToast(requireRunnable ? "Workflow sẵn sàng để chạy." : "Workflow hợp lệ để lưu.");
      else this.showToast(this.validation?.errors?.[0]?.message || "Workflow còn lỗi.", "error");
      return this.validation;
    } catch (error) {
      this.validation = { valid: false, errors: [{ message: error.message }] };
      this.renderGraphStatus();
      this.showToast(error.message, "error");
      return this.validation;
    }
  }

  exportGraph() {
    const graph = this.toHubGraph();
    const blob = new Blob([JSON.stringify(graph, null, 2)], { type: "application/json" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `${graph.id || "workflow"}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  }

  async importGraph(file) {
    if (!file) return;
    try {
      const graph = JSON.parse(await file.text());
      const result = await validateNodeGraph(graph, false);
      if (!result.validation?.valid) throw new Error(result.validation?.errors?.[0]?.message || "Workflow JSON không hợp lệ.");
      this.history = [];
      this.future = [];
      this.dirty = new Set(result.validation.graph.nodes.map((node) => node.id));
      this.savedFingerprint = "";
      this.recovered = false;
      this.unsaved = true;
      this.hydrateLiteGraph(result.validation.graph);
      this.persist({ source: "import" });
      this.showToast("Đã import workflow JSON vào Hub Nodes.");
    } catch (error) {
      this.showToast(error.message, "error");
    }
  }

  fitView() {
    const nodes = this.liteGraph._nodes;
    if (!nodes.length) return;
    const left = Math.min(...nodes.map((node) => node.pos[0]));
    const top = Math.min(...nodes.map((node) => node.pos[1]));
    const right = Math.max(...nodes.map((node) => node.pos[0] + node.size[0]));
    const bottom = Math.max(...nodes.map((node) => node.pos[1] + node.size[1]));
    const zoom = Math.max(0.35, Math.min(1.15, Math.min(this.canvasElement.width / (right - left + 160), this.canvasElement.height / (bottom - top + 140))));
    this.liteCanvas.ds.scale = zoom;
    this.liteCanvas.ds.offset[0] = this.canvasElement.width / (2 * zoom) - (left + right) / 2;
    this.liteCanvas.ds.offset[1] = this.canvasElement.height / (2 * zoom) - (top + bottom) / 2;
    this.liteCanvas.setDirty(true, true);
    this.drawMinimap();
  }

  drawMinimap() {
    if (!this.minimap || !this.liteGraph) return;
    const ctx = this.minimap.getContext("2d");
    const { width, height } = this.minimap;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "rgba(8, 13, 27, .94)";
    ctx.fillRect(0, 0, width, height);
    const nodes = this.liteGraph._nodes;
    if (!nodes.length) return;
    const left = Math.min(...nodes.map((node) => node.pos[0]));
    const top = Math.min(...nodes.map((node) => node.pos[1]));
    const right = Math.max(...nodes.map((node) => node.pos[0] + node.size[0]));
    const bottom = Math.max(...nodes.map((node) => node.pos[1] + node.size[1]));
    const scale = Math.min((width - 18) / Math.max(1, right - left), (height - 18) / Math.max(1, bottom - top));
    const offsetX = (width - (right - left) * scale) / 2 - left * scale;
    const offsetY = (height - (bottom - top) * scale) / 2 - top * scale;
    for (const node of nodes) {
      const color = CATEGORY_COLORS[this.registry.get(node.hubType)?.category] || "#8794ad";
      ctx.fillStyle = color;
      ctx.fillRect(node.pos[0] * scale + offsetX, node.pos[1] * scale + offsetY, Math.max(4, node.size[0] * scale), Math.max(3, node.size[1] * scale));
    }
    const viewLeft = -this.liteCanvas.ds.offset[0];
    const viewTop = -this.liteCanvas.ds.offset[1];
    const viewWidth = this.canvasElement.width / this.liteCanvas.ds.scale;
    const viewHeight = this.canvasElement.height / this.liteCanvas.ds.scale;
    ctx.strokeStyle = "#edf2ff";
    ctx.lineWidth = 1;
    ctx.strokeRect(viewLeft * scale + offsetX, viewTop * scale + offsetY, viewWidth * scale, viewHeight * scale);
    this.minimapBounds = { scale, offsetX, offsetY };
  }

  recenterFromMinimap(event) {
    if (!this.minimapBounds) return;
    const rect = this.minimap.getBoundingClientRect();
    const x = (event.clientX - rect.left) * this.minimap.width / rect.width;
    const y = (event.clientY - rect.top) * this.minimap.height / rect.height;
    const graphX = (x - this.minimapBounds.offsetX) / this.minimapBounds.scale;
    const graphY = (y - this.minimapBounds.offsetY) / this.minimapBounds.scale;
    this.liteCanvas.ds.offset[0] = this.canvasElement.width / (2 * this.liteCanvas.ds.scale) - graphX;
    this.liteCanvas.ds.offset[1] = this.canvasElement.height / (2 * this.liteCanvas.ds.scale) - graphY;
    this.liteCanvas.setDirty(true, true);
    this.drawMinimap();
  }

  handleAction(action) {
    if (action === "run") this.run();
    if (action === "validate") this.validate(false);
    if (action === "cancel") this.cancel();
    if (action === "undo") this.undo();
    if (action === "redo") this.redo();
    if (action === "clear-selection") this.clearSelection();
    if (action === "delete") this.deleteSelected();
    if (action === "save-local") { this.saveLocal(); return; }
    if (action === "duplicate") this.duplicateWorkflow();
    if (action === "fit") this.fitView();
    if (action === "export") this.exportGraph();
    if (action === "save-local") { this.persist(); this.showToast("Workflow đã lưu local trong WebView."); }
  }
}

let activeEditors = [];

export function disposeNodeStudios() {
  activeEditors.forEach((editor) => editor.destroy());
  activeEditors = [];
}

export function mountNodeStudios(options) {
  disposeNodeStudios();
  activeEditors = [...document.querySelectorAll("[data-node-studio]")].map((root) => new HubGraphEditor(root, options));
  activeEditors.forEach((editor) => editor.initialize());
}
