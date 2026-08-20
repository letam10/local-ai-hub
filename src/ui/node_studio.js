/*
  FILE NOTE
  - Mục đích: Node Studio canvas editor (Hub DAG visual workflow editor, LiteGraph adapter, Smart Connection Picker, palettes, inspector, presets)
  - Liên kết trực tiếp: src/ui/app.js, src/ui/pages.js, src/ui/api.js, src/ui/workflow_library.js, src/ui/vendor/litegraph.js
  - Vùng ảnh hưởng khi sửa: Toàn bộ chức năng Hub Nodes (Image AI studio, Media studio, SAM2 studio, AnimeSR studio)
*/

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
  clearNodeDraft,
  getNodeDraft,
  saveNodeDraft,
} from "./api.js";
import { workflowLibraryEntry } from "./workflow_library.js";

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
const NODE_UI_STATE_VERSION = 1;
const NODE_UI_STATE_PREFIX = `${LOCAL_PREFIX}:ui:v${NODE_UI_STATE_VERSION}`;
const OPAQUE_ARTIFACT_ID = /^artifact_[a-f0-9]{32}$/;
const SAFE_ARTIFACT_URL = /^\/api\/artifacts\/artifact_[a-f0-9]{32}$/;
const MEDIA_OPERATION_SCOPE_IDS = Object.freeze(["video_grade", "logo_overlay", "encode"]);
const MEDIA_OPERATION_SCOPE_LABELS = Object.freeze({ video_grade: "Video grade", logo_overlay: "Logo overlay", encode: "Encode" });
const MEDIA_OPERATION_SCOPE_FALLBACK = Object.freeze({ status: "unavailable", execution: "not_run", evidenceVerified: false, availableOperations: [], operationStatus: { video_grade: "partial", logo_overlay: "partial", encode: "partial" }, reason: "No completed exact media evidence is available in this server snapshot.", nextAction: "Keep these operations partial until separately evidenced." });
const unsafeOperationScopeText = /(?:[a-z]:[\\/]|\\\\|(?:file|data|https?):|(?:api[_-]?key|password|secret|token)\s*[:=]|\b(?:cmd|powershell|bash|ffmpeg|python|callable|manifest|command)\b)/i;
const safeOperationScopeText = (value, fallback = "") => {
  if (typeof value !== "string") return fallback;
  const candidate = value.trim().slice(0, 240);
  return candidate && !unsafeOperationScopeText.test(candidate) ? candidate : fallback;
};
export function normalizeOperationScope(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.subject !== "media_overlay_cpu_acceptance" || value.schema_version !== "runtime-operation-scope.v1" || !Array.isArray(value.operations) || value.operations.length !== MEDIA_OPERATION_SCOPE_IDS.length || value.operations.some((item, index) => item !== MEDIA_OPERATION_SCOPE_IDS[index]) || !Array.isArray(value.available_operations) || !value.operation_status || typeof value.operation_status !== "object" || Array.isArray(value.operation_status) || Object.keys(value.operation_status).sort().join("|") !== MEDIA_OPERATION_SCOPE_IDS.slice().sort().join("|") || MEDIA_OPERATION_SCOPE_IDS.some((id) => !["partial", "operational"].includes(value.operation_status[id])) || typeof value.evidence_verified !== "boolean") return { ...MEDIA_OPERATION_SCOPE_FALLBACK, operationStatus: { ...MEDIA_OPERATION_SCOPE_FALLBACK.operationStatus } };
  const reason = safeOperationScopeText(value.reason);
  const nextAction = safeOperationScopeText(value.next_action);
  if (!reason || !nextAction) return { ...MEDIA_OPERATION_SCOPE_FALLBACK, operationStatus: { ...MEDIA_OPERATION_SCOPE_FALLBACK.operationStatus } };
  const completed = value.evidence_verified === true && value.status === "operational" && value.execution === "completed" && value.available_operations.length === MEDIA_OPERATION_SCOPE_IDS.length && value.available_operations.every((item, index) => item === MEDIA_OPERATION_SCOPE_IDS[index]) && MEDIA_OPERATION_SCOPE_IDS.every((id) => value.operation_status[id] === "operational");
  const unavailable = value.evidence_verified === false && value.status === "unavailable" && value.execution === "not_run" && value.available_operations.length === 0 && MEDIA_OPERATION_SCOPE_IDS.every((id) => value.operation_status[id] === "partial");
  if (!completed && !unavailable) return { ...MEDIA_OPERATION_SCOPE_FALLBACK, operationStatus: { ...MEDIA_OPERATION_SCOPE_FALLBACK.operationStatus } };
  return { status: completed ? "operational" : "unavailable", execution: completed ? "completed" : "not_run", evidenceVerified: completed, availableOperations: completed ? MEDIA_OPERATION_SCOPE_IDS.slice() : [], operationStatus: { ...value.operation_status }, reason, nextAction };
}
const MEDIA_RUN_GATED_REASON = "Run Graph is unavailable for Media until every node is one of the three exactly evidenced operations.";
const MEDIA_RUN_EMPTY_REASON = "Add an exactly evidenced media operation before running this graph.";
const isVerifiedMediaOperationScope = (value) => Boolean(value && value.status === "operational" && value.execution === "completed" && value.evidenceVerified === true && Array.isArray(value.availableOperations) && value.availableOperations.length === MEDIA_OPERATION_SCOPE_IDS.length && MEDIA_OPERATION_SCOPE_IDS.every((id, index) => value.availableOperations[index] === id) && value.operationStatus && Object.keys(value.operationStatus).sort().join("|") === MEDIA_OPERATION_SCOPE_IDS.slice().sort().join("|") && MEDIA_OPERATION_SCOPE_IDS.every((id) => value.operationStatus[id] === "operational"));
export function mediaGraphRunEligibility(scope, graph, operationScope) {
  if (scope !== "media") return { eligible: true, reason: "" };
  if (!isVerifiedMediaOperationScope(operationScope)) return { eligible: false, reason: MEDIA_RUN_GATED_REASON };
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  if (!nodes.length) return { eligible: false, reason: MEDIA_RUN_EMPTY_REASON };
  const exact = nodes.every((node) => MEDIA_OPERATION_SCOPE_IDS.includes(node?.type) && operationScope.availableOperations.includes(node.type) && operationScope.operationStatus[node.type] === "operational");
  return exact ? { eligible: true, reason: "" } : { eligible: false, reason: MEDIA_RUN_GATED_REASON };
}
const PANEL_STATE_DEFAULTS = Object.freeze({
  version: NODE_UI_STATE_VERSION,
  palette: "open",
  inspector: "open",
  canvasFocus: false,
  preview: "compact",
  paletteWidth: "default",
  inspectorWidth: "default",
  guide: false,
  pickerSearch: "",
});

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

export function normalizeNodePanelState(value) {
  const source = value && typeof value === "object" && value.version === NODE_UI_STATE_VERSION ? value : {};
  return {
    ...PANEL_STATE_DEFAULTS,
    palette: ["open", "collapsed"].includes(source.palette) ? source.palette : PANEL_STATE_DEFAULTS.palette,
    inspector: ["open", "collapsed"].includes(source.inspector) ? source.inspector : PANEL_STATE_DEFAULTS.inspector,
    canvasFocus: source.canvasFocus === true,
    preview: ["compact", "expanded"].includes(source.preview) ? source.preview : PANEL_STATE_DEFAULTS.preview,
    paletteWidth: ["default", "narrow", "wide"].includes(source.paletteWidth) ? source.paletteWidth : PANEL_STATE_DEFAULTS.paletteWidth,
    inspectorWidth: ["default", "narrow", "wide"].includes(source.inspectorWidth) ? source.inspectorWidth : PANEL_STATE_DEFAULTS.inspectorWidth,
    guide: source.guide === true,
    pickerSearch: typeof source.pickerSearch === "string" ? source.pickerSearch.slice(0, 120) : "",
  };
}

export function connectionPortDecision(sourcePort, targetPort, occupied = false) {
  const sourceType = String(sourcePort?.type || "");
  const targetType = String(targetPort?.type || "");
  if (!sourceType || !targetType || sourceType !== targetType) {
    return { compatible: false, reason: `Typed socket mismatch: ${sourceType || "unknown"} -> ${targetType || "unknown"}.` };
  }
  if (occupied && !targetPort?.multi) {
    return { compatible: false, reason: "Input is already connected and is not multi." };
  }
  return { compatible: true, reason: "Compatible typed socket." };
}

export function getConnectionPortCandidates(definitions, { direction = "input", type = "", occupied = [] } = {}) {
  const items = definitions instanceof Map ? [...definitions.values()] : Array.isArray(definitions) ? definitions : [];
  const occupiedSet = occupied instanceof Set ? occupied : new Set(Array.isArray(occupied) ? occupied : []);
  const compatible = [];
  const rejected = [];
  for (const definition of items) {
    if (!definition || typeof definition.type !== "string") continue;
    const ports = direction === "output" ? definition.outputs || [] : definition.inputs || [];
    for (const port of ports) {
      const decision = connectionPortDecision(
        direction === "output" ? port : { type },
        direction === "output" ? { type } : port,
        occupiedSet.has(`${definition.type}:${port.name}`),
      );
      const candidate = { definition, port, direction, key: `${definition.type}:${port.name}` };
      (decision.compatible ? compatible : rejected).push({ ...candidate, reason: decision.reason });
    }
  }
  return { compatible, rejected };
}

export function chooseConnectionCandidate(candidates) {
  const values = Array.isArray(candidates) ? candidates : candidates?.compatible;
  return values?.length === 1 ? values[0] : null;
}

export function safeArtifactProjection(value) {
  if (!value || typeof value !== "object") return { safe: false, reason: "Artifact metadata is unavailable." };
  const id = String(value.id || "");
  const url = String(value.url || "");
  const mediaType = String(value.media_type || "application/octet-stream").split(";", 1)[0].trim().toLocaleLowerCase();
  if (!OPAQUE_ARTIFACT_ID.test(id) || url !== `/api/artifacts/${id}` || !SAFE_ARTIFACT_URL.test(url)) {
    return { safe: false, reason: "Preview requires the existing opaque Hub artifact URL." };
  }
  const kind = mediaType.startsWith("image/") ? "image" : mediaType.startsWith("video/") ? "video" : mediaType.startsWith("audio/") ? "audio" : "metadata";
  return {
    safe: true,
    id,
    url,
    name: String(value.name || "Artifact").replace(/[\\/\r\n]+/g, " ").slice(0, 160),
    mediaType,
    sizeBytes: Number.isFinite(Number(value.size_bytes)) ? Number(value.size_bytes) : null,
    kind,
    mask: Boolean(value.mask || mediaType.includes("mask")),
  };
}

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
    const options = property.options || (property.kind === "encoder" ? ["auto"] : []);
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
  constructor(root, { showToast, recipeApplication = null, initialPresetId = null, onRecipeApplied = () => {}, onPresetApplied = () => {}, workflowLibrary = null }) {
    this.root = root;
    this.scope = root.dataset.scope || "image";
    this.showToast = showToast;
    this.recipeApplication = recipeApplication;
    this.initialPresetId = initialPresetId;
    this.onRecipeApplied = onRecipeApplied;
    this.onPresetApplied = onPresetApplied;
    this.workflowLibrary = workflowLibrary;
    this.workflowLibraryState = { status: "partial", reason: "Workflow Library server-owned adapter chưa được V5-D wire.", action: "Tiếp tục local draft; xác nhận endpoint typed trước khi đồng bộ." };
    this.workflowLibraryRevision = null;
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
    this.panelState = this.readPanelState();
    this.connectionPicker = null;
    this.pendingConnection = null;
    this.connectionNotice = "";
    this.encoderCapabilities = null;
    this.operationScope = { ...MEDIA_OPERATION_SCOPE_FALLBACK, operationStatus: { ...MEDIA_OPERATION_SCOPE_FALLBACK.operationStatus } };
  }

  panelStateKey() { return `${NODE_UI_STATE_PREFIX}:${this.scope}`; }

  readPanelState() {
    try { return normalizeNodePanelState(JSON.parse(localStorage.getItem(this.panelStateKey()) || "null")); }
    catch { return normalizeNodePanelState(null); }
  }

  writePanelState() {
    try { localStorage.setItem(this.panelStateKey(), JSON.stringify(this.panelState)); }
    catch { /* WebView storage may be unavailable; graph work remains local-only. */ }
  }

  isTextControl(target) {
    return Boolean(target?.matches?.("input,textarea,select,[contenteditable='true']"));
  }

  isCanvasActive() {
    return Boolean(this.canvasElement && (document.activeElement === this.canvasElement || this.canvasElement.matches(":focus")));
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
      this.operationScope = normalizeOperationScope(registryPayload.operation_scope);
      this.applyEncoderCapabilities(registryPayload.encoder_capabilities);
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
      await this.refreshWorkflowLibrary();
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

  async refreshWorkflowLibrary() {
    if (!this.workflowLibrary?.list) return this.workflowLibraryState;
    try {
      const result = await this.workflowLibrary.list();
      if (result && typeof result === "object") {
        this.workflowLibraryState = result;
        this.workflowLibraryRevision = Number.isInteger(result.library_revision) ? result.library_revision : null;
      }
    } catch {
      this.workflowLibraryState = { status: "partial", reason: "Workflow Library bridge không phản hồi; local draft vẫn được giữ.", action: "Kiểm tra bridge server-owned rồi thử lại bằng thao tác user-mediated." };
    }
    return this.workflowLibraryState;
  }

  applyEncoderCapabilities(snapshot) {
    this.encoderCapabilities = snapshot && typeof snapshot === "object" ? snapshot : { status: "not_run", execution: "not_run", available: false, encoders: [] };
    const definition = this.registry.get("encode");
    const property = definition?.properties?.find((item) => item.name === "codec" && item.kind === "encoder");
    if (!property) return;
    const ids = this.encoderCapabilities.status === "completed" && this.encoderCapabilities.available && Array.isArray(this.encoderCapabilities.encoders)
      ? this.encoderCapabilities.encoders.map((item) => item?.id).filter((item) => typeof item === "string" && /^[a-z0-9_]{1,64}$/.test(item))
      : [];
    property.options = ["auto", ...new Set(ids)];
  }

  operationEvidenceFor(definition) {
    if (this.scope !== "media" || !MEDIA_OPERATION_SCOPE_IDS.includes(definition?.type)) return null;
    const id = definition.type;
    const status = this.operationScope.operationStatus[id] || "partial";
    return {
      id,
      label: MEDIA_OPERATION_SCOPE_LABELS[id],
      status,
      reason: safeOperationScopeText(this.operationScope.reason, MEDIA_OPERATION_SCOPE_FALLBACK.reason),
      nextAction: safeOperationScopeText(this.operationScope.nextAction, MEDIA_OPERATION_SCOPE_FALLBACK.nextAction),
      action: safeOperationScopeText(this.operationScope.nextAction, MEDIA_OPERATION_SCOPE_FALLBACK.nextAction),
      evidenceVerified: this.operationScope.evidenceVerified === true,
      execution: this.operationScope.execution,
    };
  }

  operationAvailabilityFor(definition) {
    const evidence = this.operationEvidenceFor(definition);
    if (this.scope !== "media") return evidence || definition?.availability || { status: definition?.status || "operational", reason: "", action: "" };
    if (evidence) return evidence;
    return {
      status: "partial",
      reason: "This node is outside the exact published media operation scope.",
      action: "Use only Video grade, Logo overlay, or Encode when their server evidence is operational.",
      evidenceVerified: false,
      execution: "not_run",
    };
  }

  runEligibility(graph = null) {
    return mediaGraphRunEligibility(this.scope, graph === null ? this.toHubGraph() : graph, this.operationScope);
  }

  runButtonAttributes() {
    const eligibility = this.runEligibility(this.graphData);
    return eligibility.eligible ? "" : ` disabled aria-disabled="true" data-run-gated="true" title="${escapeHtml(eligibility.reason)}"`;
  }

  operationScopeSummary() {
    if (this.scope !== "media") return "Media operation scope is not applied to this workspace; no execution is claimed.";
    if (this.operationScope.evidenceVerified) return "Exact media evidence is published for video grade, logo overlay and encode; opening Node Studio does not execute a worker.";
    return "No exact media evidence is verified in this server snapshot; scoped nodes remain partial and no execution is claimed.";
  }

  operationEvidenceMarkup(definition = null) {
    const evidence = this.operationEvidenceFor(definition);
    const title = evidence ? `${evidence.label} evidence` : "Media operation evidence";
    const generic = this.scope === "media" && !evidence;
    const status = evidence?.status || (generic ? "partial" : this.scope === "media" ? this.operationScope.status : "unavailable");
    const reason = evidence?.reason || (generic ? "This node is outside the exact published media operation scope." : this.operationScopeSummary());
    const nextAction = evidence?.nextAction || (generic ? "Use only the three exactly evidenced media operations." : safeOperationScopeText(this.operationScope.nextAction, MEDIA_OPERATION_SCOPE_FALLBACK.nextAction));
    const execution = evidence?.execution || (generic ? "not_run" : this.operationScope.execution);
    return `<section class="graph-operation-evidence" data-operation-scope-status="${escapeHtml(status)}" data-operation-scope-verified="${String(evidence?.evidenceVerified === true)}"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(reason)}</span><span><b>Next action</b> ${escapeHtml(nextAction)}</span><small>Server snapshot · execution: ${escapeHtml(execution)} · no UI execution</small></section>`;
  }

  destroy() {
    this.abort.abort();
    this.closeConnectionPicker(false);
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
    if (saved) {
      this.savedFingerprint = graphFingerprint(graph);
      this.unsaved = false;
      clearNodeDraft(this.scope).catch(() => {});
    } else {
      saveNodeDraft(this.scope, graph).catch(() => {});
    }
    this.renderWorkflowStatus();
  }

  saveLocal() {
    this.persist({ source: "saved", saved: true });
    this.showToast("Workflow đã lưu local và có thể khôi phục trong Recent.");
  }

  async saveToLibrary() {
    const entry = workflowLibraryEntry(this.toHubGraph(), this.scope);
    const result = this.workflowLibrary?.save
      ? await this.workflowLibrary.save(entry, this.workflowLibraryRevision)
      : { status: "partial", reason: "Workflow Library server-owned adapter chưa được V5-D wire.", action: "Tiếp tục local draft; xác nhận endpoint typed trước khi đồng bộ." };
    this.workflowLibraryState = result || this.workflowLibraryState;
    if (Number.isInteger(result?.library_revision)) this.workflowLibraryRevision = result.library_revision;
    this.renderWorkflowStatus();
    if (result?.status === "ready" || result?.accepted) this.showToast("Đã ghi workflow vào Workflow Library.");
    else this.showToast(result?.action || result?.reason || "Workflow Library vẫn partial; local draft không bị mất.", "warning");
    return result;
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
    const libraryStatus = this.root.querySelector("[data-workflow-library-status]");
    if (libraryStatus) {
      libraryStatus.dataset.workflowLibraryStatus = this.workflowLibraryState.status || "partial";
      libraryStatus.textContent = "Library: " + (this.workflowLibraryState.status || "partial");
      libraryStatus.title = this.workflowLibraryState.reason || "";
    }
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
      HubLiteNode.prototype.onConnectInput = function guardOccupiedInput(slot) {
        const input = this.inputs?.[slot];
        if (input?.link != null && !input.multi) {
          this._hubEditor?.showConnectionNotice("Input is already connected; disconnect it before adding another edge.");
          return false;
        }
        return true;
      };
      HubLiteNode.prototype.onConnectOutput = function guardOutputType(slot, type) {
        const output = this.outputs?.[slot];
        if (output && type && output.type !== type) {
          this._hubEditor?.showConnectionNotice(`Incompatible typed socket: ${output.type} -> ${type}.`);
          return false;
        }
        return true;
      };
      LiteGraph.registerNodeType(typeName, HubLiteNode);
    }
  }

  renderShell() {
    this.root.innerHTML = `
      <section class="graph-editor" aria-label="Hub Nodes ${escapeHtml(this.scope)}" data-node-palette="${escapeHtml(this.panelState.palette)}" data-node-inspector="${escapeHtml(this.panelState.inspector)}" data-node-canvas-focus="${String(this.panelState.canvasFocus)}" data-node-preview="${escapeHtml(this.panelState.preview)}" data-node-palette-width="${escapeHtml(this.panelState.paletteWidth)}" data-node-inspector-width="${escapeHtml(this.panelState.inspectorWidth)}" data-operation-scope-status="${escapeHtml(this.operationScope.status)}" data-operation-scope-execution="${escapeHtml(this.operationScope.execution)}" data-operation-scope-verified="${String(this.operationScope.evidenceVerified)}">
        <header class="graph-editor__header">
          <div><span class="eyebrow">NODE WORKFLOW</span><h2>${escapeHtml(this.graphData.title || `Image ${this.scope}`)}</h2><p>Canvas typed socket cho người mới: nối đúng kiểu dữ liệu, kiểm tra trước khi chạy và luôn thấy trạng thái backend.</p></div>
          <div class="graph-editor__header-status" data-graph-summary><span class="status-pill" data-status="idle">Chưa chạy</span><span class="tag">${escapeHtml(this.scope)}</span></div>
        </header>
        <div class="graph-editor__workflow-bar"><label class="graph-workflow-title"><span>Tên workflow</span><input data-graph-title aria-label="Tên workflow" value="${escapeHtml(this.graphData.title || "")}" /></label><label class="graph-workflow-recent"><span>Recent</span><select data-graph-recent aria-label="Recent workflows"><option value="">Chọn workflow local…</option>${this.recentOptions()}</select></label><span class="graph-save-state" data-graph-save-state>Đã lưu local</span><button class="button button--compact" type="button" data-graph-action="duplicate">Nhân bản</button></div>
        <div class="graph-editor__toolbar">
          <div class="graph-editor__toolbar-group"><button class="button button--primary" type="button" data-graph-action="run" aria-label="Run Graph"${this.runButtonAttributes()}>Chạy workflow</button><button class="button" type="button" data-graph-action="validate">Kiểm tra</button><button class="button" type="button" data-graph-action="cancel" disabled>Hủy job</button><button class="button" type="button" data-graph-action="undo">Hoàn tác</button><button class="button" type="button" data-graph-action="redo">Làm lại</button></div>
          <div class="graph-editor__toolbar-group"><select data-graph-preset aria-label="Preset workflow"><option value="">Chọn template…</option>${this.presets.map((item) => `<option value="${escapeHtml(item.id)}" title="${escapeHtml(item.description || "")}">${escapeHtml(item.title)}${item.stage ? ` · ${escapeHtml(item.stage)}` : ""}</option>`).join("")}</select><button class="button" type="button" data-graph-action="save-local">Lưu local</button><button class="button" type="button" data-graph-action="export">Export JSON</button><label class="button graph-editor__import">Import JSON<input type="file" data-graph-import accept="application/json,.json" /></label></div>
        </div>
        <div class="graph-editor__options"><label><input type="checkbox" data-graph-option="auto" ${this.autoPreview ? "checked" : ""} /> Preview tự động (Auto Preview)</label><label><input type="checkbox" data-graph-option="draft" ${this.draft ? "checked" : ""} /> Draft ảnh</label><span>Bấm node để cộng dồn lựa chọn · Ctrl/Shift cũng cộng dồn · kéo nhóm để di chuyển · kéo vùng để chọn · bấm nền trống, Esc hoặc Xóa chọn để bỏ chọn</span></div>
        <div class="graph-editor__statusbar"><span data-graph-validation>Chưa kiểm tra workflow.</span><span class="graph-editor__availability">${this.availability.counts?.operational || 0} sẵn sàng · ${this.availability.counts?.partial || 0} partial · ${this.availability.counts?.unavailable || 0} unavailable</span><span class="graph-operation-scope-status" data-graph-operation-evidence role="status">${escapeHtml(this.operationScopeSummary())}</span></div>
        <div class="graph-editor__panel-controls" role="toolbar" aria-label="Node Studio panels">
          <button class="button button--compact" type="button" data-graph-action="toggle-palette" aria-expanded="${String(this.panelState.palette !== "collapsed")}">Palette</button>
          <button class="button button--compact" type="button" data-graph-action="toggle-inspector" aria-expanded="${String(this.panelState.inspector !== "collapsed")}">Inspector</button>
          <button class="button button--compact" type="button" data-graph-action="toggle-canvas-focus" aria-pressed="${String(this.panelState.canvasFocus)}">Canvas focus</button>
          <button class="button button--compact" type="button" data-graph-action="cycle-palette-width">Palette width</button>
          <button class="button button--compact" type="button" data-graph-action="cycle-inspector-width">Inspector width</button>
          <button class="button button--compact" type="button" data-graph-action="toggle-preview">Preview size</button>
          <button class="button button--compact" type="button" data-graph-action="toggle-guide" aria-expanded="${String(this.panelState.guide)}">Node Guide</button>
        </div>
        <details class="graph-guide" data-graph-guide ${this.panelState.guide ? "open" : ""}>
          <summary>Node Guide · Hướng dẫn toàn diện Hub Nodes & Typed Workflow</summary>
          <div class="graph-guide__content">
            <div class="graph-guide__section">
              <strong>1. Khái niệm Typed Sockets & Dữ liệu</strong>
              <p>Mỗi cổng (socket) trên node được quy định kiểu dữ liệu nghiêm ngặt: <code>IMAGE</code> (tím), <code>MASK</code> (xanh lục), <code>VIDEO</code> (hồng đỏ), <code>AUDIO</code> (cam), <code>TEXT</code> (lam), <code>NUMBER</code> (xanh nhạt), <code>BOOLEAN</code> (vàng), <code>METADATA</code> (xám). Socket đầu vào không hỗ trợ đa kết nối (non-multi) sẽ từ chối kết nối thứ hai để tránh xung đột.</p>
            </div>
            <div class="graph-guide__section">
              <strong>2. Thao tác Canvas & Phím tắt</strong>
              <p>• <b>Pan canvas:</b> Giữ chuột giữa (Middle click) và kéo để di chuyển khung nhìn tự do.<br/>
              • <b>Chọn node:</b> Click chuột trái để chọn 1 node duy nhất (tự bỏ chọn các node khác); giữ <code>Ctrl</code> hoặc <code>Shift</code> để chọn thêm.<br/>
              • <b>Quét vùng (Marquee):</b> Kéo chuột trái trên vùng canvas trống để chọn hàng loạt node.<br/>
              • <b>Di chuyển nhóm:</b> Khi nhiều node đang được chọn, kéo 1 node sẽ di chuyển toàn bộ nhóm cùng lúc.<br/>
              • <b>Dịch chuyển chính xác (Nudge):</b> Phím mũi tên (<code>↑ ↓ ← →</code>) dịch chuyển 5px; kết hợp <code>Shift + Mũi tên</code> dịch chuyển 25px.<br/>
              • <b>Xóa / Hủy:</b> Phím <code>Delete</code> hoặc <code>Backspace</code> để xóa node đã chọn; phím <code>Escape</code> để bỏ chọn hoặc đóng hộp thoại.<br/>
              • <b>Chọn tất cả:</b> Phím <code>Ctrl+A</code> / <code>Cmd+A</code> để chọn toàn bộ node trên canvas.<br/>
              • <b>Hoàn tác / Làm lại:</b> <code>Ctrl+Z</code> (Undo) và <code>Ctrl+Y</code> hoặc <code>Ctrl+Shift+Z</code> (Redo) hỗ trợ tối đa 60 bước.</p>
            </div>
            <div class="graph-guide__section">
              <strong>3. Smart Node Picker (Kéo socket tạo node tự động)</strong>
              <p>Kéo dây từ bất kỳ socket đầu ra hoặc đầu vào nào và thả vào vùng canvas trống. <b>Smart Node Picker</b> sẽ mở ra danh sách các node tương thích kiểu dữ liệu. Bạn chỉ cần chọn node mong muốn, hệ thống sẽ tự động tạo node mới và nối dây chính xác.</p>
            </div>
            <div class="graph-guide__section">
              <strong>4. Trạng thái Backend & Bằng chứng trung thực</strong>
              <p>Mọi node hiển thị trạng thái trung thực (<code>operational</code>, <code>partial</code>, <code>unavailable</code>) kèm lý do và bước xử lý tiếp theo. Việc mở Hub Nodes hoàn toàn tĩnh, không tự ý kích hoạt GPU hay chạy ngầm tác vụ AI nặng.</p>
            </div>
            <div class="graph-guide__section">
              <strong>5. Quản lý Workflow & Bản quyền Output</strong>
              <p>Dữ liệu đồ thị workflow được lưu trữ local offline. Mọi file kết quả (Artifacts) đều sử dụng mã định danh bảo mật (<code>artifact_*</code>) và hỗ trợ xem trước video/ảnh/âm thanh an toàn qua chuẩn HTTP streaming.</p>
            </div>
            <div class="graph-guide__templates">
              <strong>Mở template workflow mẫu trong Hub Nodes:</strong>
              <div class="graph-guide__template-buttons">
                ${this.presets.filter((item) => item.scope === this.scope).map((item) => `<button class="button button--compact" type="button" data-graph-guide-preset="${escapeHtml(item.id)}" title="${escapeHtml(item.description || "")}">Mở template: ${escapeHtml(item.title || item.id)}</button>`).join("") || "<span>Chưa có template mẫu cho workspace này.</span>"}
              </div>
            </div>
          </div>
        </details>
        <div class="graph-editor__layout">
          <aside class="graph-palette"><input type="search" data-graph-search placeholder="Tìm node…" aria-label="Tìm node" /><div data-graph-palette></div></aside>
          <div class="graph-canvas-shell"><canvas class="graph-canvas" data-graph-canvas></canvas><div class="graph-canvas__actions"><button type="button" data-graph-action="fit">Fit</button><button type="button" data-graph-action="clear-selection">Bỏ chọn</button><button type="button" data-graph-action="delete">Xóa chọn</button></div><canvas class="graph-minimap" data-graph-minimap width="180" height="118" aria-label="Minimap graph"></canvas></div>
          <aside class="graph-inspector" data-graph-inspector></aside>
        </div>
      </section>`;
    const libraryBar = this.root.querySelector(".graph-editor__workflow-bar");
    if (libraryBar) {
      const libraryStatus = document.createElement("span");
      libraryStatus.className = "graph-library-state";
      libraryStatus.dataset.workflowLibraryStatus = this.workflowLibraryState.status || "partial";
      libraryStatus.setAttribute("role", "status");
      libraryStatus.textContent = "Library: " + (this.workflowLibraryState.status || "partial");
      libraryBar.append(libraryStatus);
    }
    const toolbarGroups = this.root.querySelectorAll(".graph-editor__toolbar-group");
    const libraryTools = toolbarGroups[toolbarGroups.length - 1];
    if (libraryTools) {
      const libraryButton = document.createElement("button");
      libraryButton.className = "button";
      libraryButton.type = "button";
      libraryButton.dataset.graphAction = "save-library";
      libraryButton.textContent = "Lưu Workflow Library";
      libraryButton.title = "Ghi record server-owned sau khi V5-D bridge được xác nhận";
      libraryTools.append(libraryButton);
    }
    const autoPreviewLabel = this.root.querySelector('[data-graph-option="auto"]')?.parentElement;
    if (autoPreviewLabel?.lastChild) autoPreviewLabel.lastChild.textContent = " Preview indicator (manual; no auto-run)";
    this.canvasElement = this.root.querySelector("[data-graph-canvas]");
    this.canvasElement.tabIndex = 0;
    this.canvasElement.setAttribute("role", "application");
    this.canvasElement.setAttribute("aria-label", "LiteGraph workflow canvas");
    this.minimap = this.root.querySelector("[data-graph-minimap]");
    this.paletteElement = this.root.querySelector("[data-graph-palette]");
    this.inspectorElement = this.root.querySelector("[data-graph-inspector]");
    this.editorElement = this.root.querySelector(".graph-editor");
    this.applyPanelState();
    this.liteGraph = new globalThis.LiteGraph.LGraph();
    this.liteCanvas = new globalThis.LiteGraph.LGraphCanvas(this.canvasElement, this.liteGraph, { autoresize: false });
    this.liteCanvas.allow_dragcanvas = true;
    this.liteCanvas.allow_dragnodes = true;
    this.liteCanvas.allow_reconnect_links = true;
    this.liteCanvas.allow_searchbox = true;
    // Plain click selects one node. LiteGraph keeps Ctrl/Shift additive when
    // multi_select is false, matching the Hub authoring contract.
    this.liteCanvas.multi_select = false;
    this.liteCanvas.render_shadows = true;
    this.liteCanvas.render_connections_border = true;
    this.liteCanvas.links_render_mode = globalThis.LiteGraph.SPLINE_LINK;
    this.liteCanvas.onBeforeChange = () => this.captureBeforeChange();
    this.liteCanvas.onAfterChange = () => this.captureAfterChange();
    this.liteCanvas.onSelectionChange = () => { this.renderInspector(); this.drawMinimap(); };
    this.liteCanvas.onNodeMoved = () => this.drawMinimap();
    this.liteCanvas.onMouse = (event) => this.handleCanvasMouse(event);
    this.liteGraph.onNodeConnectionChange = () => this.captureConnectionChange();
    this.bindConnectionPickerHook();
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
      const guidePreset = event.target.closest("[data-graph-guide-preset]")?.dataset.graphGuidePreset;
      if (guidePreset) this.loadPreset(guidePreset);
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
      if (!this.root.isConnected) return;
      if (this.connectionPicker && this.handlePickerKey(event)) return;
      if (this.isTextControl(event.target)) return;
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

  handleCanvasMouse(event) {
    if (!this.liteGraph || event.which !== 1 || this.connectionPicker || this.liteCanvas.connecting_node) return false;
    const now = globalThis.LiteGraph?.getTime?.() || Date.now();
    if (now - Number(this.liteCanvas.last_mouseclick || 0) < 300) return false;
    const node = this.liteGraph.getNodeOnPos(event.canvasX, event.canvasY, this.liteCanvas.visible_nodes);
    const group = this.liteGraph.getGroupOnPos?.(event.canvasX, event.canvasY);
    if (node || group) return false;
    this.liteCanvas.dragging_rectangle = new Float32Array([event.canvasX, event.canvasY, 0, 0]);
    if (!event.shiftKey && !event.ctrlKey && !event.metaKey) this.liteCanvas.deselectAllNodes();
    return true;
  }

  captureConnectionChange() {
    if (this.hydrating || !this.liteGraph) return;
    const next = this.toHubGraph();
    const after = graphFingerprint(next);
    const before = this.beforeChange || graphFingerprint(this.graphData);
    if (before !== after) {
      this.history.push(before);
      if (this.history.length > MAX_HISTORY) this.history.shift();
      this.future = [];
      this.graphData = next;
      this.unsaved = true;
      this.markDirty(next.nodes.map((node) => node.id));
      this.persist();
      this.renderInspector();
      this.renderGraphStatus();
    }
    this.beforeChange = null;
  }

  showConnectionNotice(message) {
    this.connectionNotice = String(message || "").slice(0, 240);
    if (this.connectionNotice) this.showToast(this.connectionNotice, "warning");
  }

  connectionDescriptor(event) {
    const canvas = this.liteCanvas;
    if (!canvas?.connecting_node || (!canvas.connecting_output && !canvas.connecting_input)) return null;
    canvas.adjustMouseEvent(event);
    const node = this.liteGraph.getNodeOnPos(event.canvasX, event.canvasY, canvas.visible_nodes);
    if (node) return null;
    if (canvas.connecting_output) {
      return {
        direction: "input",
        type: String(canvas.connecting_output.type || ""),
        node: canvas.connecting_node,
        slot: canvas.connecting_slot,
        port: canvas.connecting_output,
        position: { x: event.canvasX, y: event.canvasY },
      };
    }
    return {
      direction: "output",
      type: String(canvas.connecting_input.type || ""),
      node: canvas.connecting_node,
      slot: canvas.connecting_slot,
      port: canvas.connecting_input,
      position: { x: event.canvasX, y: event.canvasY },
    };
  }

  cancelNativeConnection() {
    if (!this.liteCanvas) return;
    this.liteCanvas.connecting_output = null;
    this.liteCanvas.connecting_input = null;
    this.liteCanvas.connecting_pos = null;
    this.liteCanvas.connecting_node = null;
    this.liteCanvas.connecting_slot = -1;
    this.liteCanvas._highlight_input = null;
    this.liteCanvas._highlight_output = null;
  }

  bindConnectionPickerHook() {
    const canvas = this.liteCanvas;
    const LiteGraph = globalThis.LiteGraph;
    const original = canvas?._mouseup_callback;
    if (!canvas || !original || !LiteGraph?.pointerListenerRemove || !LiteGraph?.pointerListenerAdd) return;
    const rootDocument = canvas.getCanvasWindow?.()?.document || document;
    const wrapper = (event) => {
      const pending = this.connectionDescriptor(event);
      if (!pending) return original(event);
      this.cancelNativeConnection();
      const result = original(event);
      this.openConnectionPicker(pending);
      return result;
    };
    LiteGraph.pointerListenerRemove(canvas.canvas, "up", original, true);
    LiteGraph.pointerListenerRemove(rootDocument, "up", original, true);
    LiteGraph.pointerListenerAdd(canvas.canvas, "up", wrapper, true);
    canvas._mouseup_callback = wrapper;
  }

  openConnectionPicker(pending) {
    this.closeConnectionPicker(false);
    this.pendingConnection = pending;
    const candidates = getConnectionPortCandidates(this.registry, { direction: pending.direction, type: pending.type });
    const automatic = chooseConnectionCandidate(candidates.compatible);
    if (automatic) {
      if (this.connectPickerCandidate(automatic)) this.showToast(`Auto-connected ${automatic.definition.title} · ${automatic.port.label || automatic.port.name}.`);
      return;
    }
    const shell = this.root.querySelector(".graph-canvas-shell");
    if (!shell) return;
    const picker = document.createElement("div");
    picker.className = "graph-connection-picker callout";
    picker.dataset.graphConnectionPicker = "true";
    picker.setAttribute("role", "dialog");
    picker.setAttribute("aria-modal", "true");
    picker.setAttribute("aria-labelledby", `graph-picker-title-${this.scope}`);
    picker.style.cssText = "position:absolute;z-index:5;width:min(340px,calc(100% - 16px));max-height:72%;overflow:auto;padding:10px;background:var(--panel);box-shadow:var(--shadow);";
    const scale = Number(this.liteCanvas.ds.scale || 1);
    const left = Number(this.liteCanvas.ds.offset?.[0] || 0) + pending.position.x * scale;
    const top = Number(this.liteCanvas.ds.offset?.[1] || 0) + pending.position.y * scale;
    picker.style.left = `${Math.max(8, Math.min(Math.max(8, shell.clientWidth - 350), left))}px`;
    picker.style.top = `${Math.max(8, Math.min(Math.max(8, shell.clientHeight - 300), top))}px`;
    picker.innerHTML = `<div class="graph-connection-picker__head"><strong id="graph-picker-title-${escapeHtml(this.scope)}">Connect ${escapeHtml(pending.type)} socket</strong><button class="button button--compact" type="button" data-graph-picker-close aria-label="Close connection picker">Esc</button></div><input type="search" data-graph-picker-search aria-label="Search compatible nodes" placeholder="Search compatible nodes" value="${escapeHtml(this.panelState.pickerSearch)}" /><div data-graph-picker-results role="listbox" aria-label="Compatible node ports"></div><div data-graph-picker-rejected class="graph-empty" role="status"></div>`;
    shell.appendChild(picker);
    this.connectionPicker = { element: picker, candidates, pending };
    const input = picker.querySelector("[data-graph-picker-search]");
    input?.addEventListener("input", () => {
      this.panelState.pickerSearch = input.value.slice(0, 120);
      this.writePanelState();
      this.renderConnectionPicker();
    });
    picker.addEventListener("click", (event) => {
      const candidateButton = event.target.closest("[data-graph-picker-candidate]");
      if (candidateButton) {
        const index = Number(candidateButton.dataset.graphPickerCandidate);
        const candidate = this.connectionPicker?.candidates.compatible[index];
        if (candidate) this.connectPickerCandidate(candidate);
      }
      if (event.target.closest("[data-graph-picker-close]")) this.closeConnectionPicker();
    });
    this._pickerOutsideHandler = (event) => {
      if (this.connectionPicker && !this.connectionPicker.element.contains(event.target)) this.closeConnectionPicker();
    };
    document.addEventListener("pointerdown", this._pickerOutsideHandler, true);
    this.renderConnectionPicker();
    setTimeout(() => input?.focus(), 0);
  }

  renderConnectionPicker() {
    const state = this.connectionPicker;
    if (!state) return;
    const query = String(state.element.querySelector("[data-graph-picker-search]")?.value || "").trim().toLocaleLowerCase();
    const matches = state.candidates.compatible.map((candidate, index) => ({ candidate, index })).filter(({ candidate }) => {
      const haystack = `${candidate.definition.title} ${candidate.definition.type} ${candidate.port.label || candidate.port.name} ${candidate.definition.description}`.toLocaleLowerCase();
      return !query || haystack.includes(query);
    });
    const results = state.element.querySelector("[data-graph-picker-results]");
    if (results) results.innerHTML = matches.length ? matches.map(({ candidate, index }) => `<button class="button button--compact" type="button" role="option" data-graph-picker-candidate="${index}" title="${escapeHtml(candidate.definition.description || "")}">${escapeHtml(candidate.definition.title)} · ${escapeHtml(candidate.port.label || candidate.port.name)} <small>${escapeHtml(candidate.definition.availability?.status || candidate.definition.status || "operational")}</small></button>`).join("") : `<p class="graph-empty">No compatible node port matches this search.</p>`;
    const rejected = state.element.querySelector("[data-graph-picker-rejected]");
    if (rejected) {
      const reasons = state.candidates.rejected.slice(0, 4).map((item) => `${item.definition.title} · ${item.port.label || item.port.name}: ${item.reason}`);
      rejected.textContent = reasons.length ? `Rejected candidates: ${reasons.join("; ")}` : "Only explicitly compatible typed ports are shown.";
    }
  }

  connectPickerCandidate(candidate) {
    const pending = this.pendingConnection;
    if (!pending || !candidate) return false;
    const definition = this.registry.get(candidate.definition.type);
    if (!definition) return false;
    const node = globalThis.LiteGraph.createNode(`local-ai-hub/${definition.type}`);
    if (!node) return false;
    node.hubType = definition.type;
    node.hubId = uid();
    node._hubEditor = this;
    node.pos = [pending.position.x, pending.position.y];
    const targetSlot = pending.direction === "input" ? node.inputs?.findIndex((port) => port.hubPort === candidate.port.name) : node.outputs?.findIndex((port) => port.hubPort === candidate.port.name);
    if (targetSlot === undefined || targetSlot < 0) return false;
    let connected = false;
    this.mutate(() => {
      this.liteGraph.add(node);
      connected = pending.direction === "input"
        ? pending.node.connect(pending.slot, node, targetSlot)
        : node.connect(targetSlot, pending.node, pending.slot);
      if (!connected) {
        this.liteGraph.remove(node);
      }
    });
    if (!connected) {
      this.showConnectionNotice("Compatible socket could not be connected safely; no node was added.");
      return false;
    }
    this.closeConnectionPicker(false);
    this.liteCanvas.selectNode(node);
    this.renderInspector();
    return true;
  }

  closeConnectionPicker(restoreFocus = true) {
    if (this._pickerOutsideHandler) {
      document.removeEventListener("pointerdown", this._pickerOutsideHandler, true);
      this._pickerOutsideHandler = null;
    }
    this.connectionPicker?.element.remove();
    this.connectionPicker = null;
    this.pendingConnection = null;
    if (restoreFocus) this.canvasElement?.focus();
  }

  applyPanelState() {
    if (!this.editorElement) return;
    const attributes = {
      "data-node-palette": this.panelState.palette,
      "data-node-inspector": this.panelState.inspector,
      "data-node-canvas-focus": String(this.panelState.canvasFocus),
      "data-node-preview": this.panelState.preview,
      "data-node-palette-width": this.panelState.paletteWidth,
      "data-node-inspector-width": this.panelState.inspectorWidth,
    };
    Object.entries(attributes).forEach(([name, value]) => this.editorElement.setAttribute(name, value));
    const guide = this.root.querySelector("[data-graph-guide]");
    if (guide) guide.open = this.panelState.guide;
    const expanded = this.root.querySelector('[data-graph-action="toggle-guide"]');
    if (expanded) expanded.setAttribute("aria-expanded", String(this.panelState.guide));
    const palette = this.root.querySelector('[data-graph-action="toggle-palette"]');
    if (palette) palette.setAttribute("aria-expanded", String(this.panelState.palette !== "collapsed"));
    const inspector = this.root.querySelector('[data-graph-action="toggle-inspector"]');
    if (inspector) inspector.setAttribute("aria-expanded", String(this.panelState.inspector !== "collapsed"));
    const focus = this.root.querySelector('[data-graph-action="toggle-canvas-focus"]');
    if (focus) focus.setAttribute("aria-pressed", String(this.panelState.canvasFocus));
    this.writePanelState();
  }

  updatePanelState(changes) {
    this.panelState = normalizeNodePanelState({ ...this.panelState, ...changes, version: NODE_UI_STATE_VERSION });
    this.applyPanelState();
    this.resizeCanvas();
  }

  handlePickerKey(event) {
    if (!this.connectionPicker) return false;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      this.closeConnectionPicker();
      return true;
    }
    return false;
  }

  handleSelectionShortcut(event) {
    if (this.isTextControl(event.target)) return false;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (this.connectionPicker) this.closeConnectionPicker();
      else this.clearSelection();
      return true;
    }
    if (!this.isCanvasActive()) return false;
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      event.stopImmediatePropagation();
      this.deleteSelected();
      return true;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
      event.preventDefault();
      event.stopImmediatePropagation();
      this.selectAllNodes();
      return true;
    }
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) {
      const step = event.shiftKey ? 25 : 5;
      const delta = { ArrowUp: [0, -step], ArrowDown: [0, step], ArrowLeft: [-step, 0], ArrowRight: [step, 0] }[event.key];
      this.moveSelected(delta[0], delta[1]);
      event.preventDefault();
      event.stopImmediatePropagation();
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
      const evidence = this.operationEvidenceFor(definition);
      const availability = this.operationAvailabilityFor(definition);
      const scoped = Boolean(evidence);
      return `<button type="button" class="graph-palette__item" data-graph-add="${escapeHtml(definition.type)}" data-operation-status="${escapeHtml(availability.status || "partial")}"${scoped ? ` data-operation-scope="${escapeHtml(evidence.id)}"` : ""} title="${escapeHtml(availability.reason || definition.description || "")}"><i style="--node-color:${escapeHtml(CATEGORY_COLORS[definition.category] || "#8794ad")}"></i><span><b>${escapeHtml(definition.title)}</b><small>${escapeHtml(availability.status)} · ${escapeHtml(availability.reason || definition.description || "")}</small></span></button>`;
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
    this.updateToolbar();
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
    const preview = this.renderArtifactPreview(artifact);
    const operationEvidence = this.operationEvidenceFor(definition);
    const availability = this.operationAvailabilityFor(definition);
    const action = this.scope === "media" && !operationEvidence ? availability.action : state.next_action || availability.action;
    const displayStatus = this.scope === "media" && !operationEvidence ? availability.status : state.status || availability.status;
    const displayMessage = this.scope === "media" && !operationEvidence ? availability.reason : state.message || state.error || availability.reason || "";
    const validation = this.validation ? (this.validation.errors?.length ? `${this.validation.errors.length} error(s)` : "valid") : "not_run";
    const dirty = this.dirty.has(node.hubId) ? "dirty" : "clean";
    const cache = state.cache_hit === true ? "hit" : state.cache_hit === false ? "miss" : "not_run";
    const progress = state.progress === undefined || state.progress === null ? "not_run" : Number.isFinite(Number(state.progress)) ? `${Math.max(0, Math.min(100, Number(state.progress)))}%` : "unavailable";
    const error = state.error ? String(state.error).slice(0, 240) : "none";
    const statusRows = [["Validation", validation], ["Dirty / downstream", dirty], ["Cache", cache], ["Progress", progress], ["Error", error]]
      .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
    this.inspectorElement.innerHTML = `<div class="graph-inspector__head"><div><span class="tag">${escapeHtml(definition?.category || "node")}</span><h3>${escapeHtml(definition?.title || node.hubType)}</h3><p>${escapeHtml(definition?.description || "")}</p></div><div class="graph-node-state" data-status="${escapeHtml(displayStatus)}"><b>${escapeHtml(displayStatus)}</b><span>${escapeHtml(displayMessage)}</span></div></div>${action ? `<div class="graph-action-hint"><strong>Bước tiếp theo</strong><span>${escapeHtml(action)}</span></div>` : ""}<section class="graph-inspector__section"><strong>Status truthful</strong><dl class="graph-status-list">${statusRows}</dl></section>${preview ? `<section class="graph-inspector__section"><strong>Safe artifact preview</strong>${preview}</section>` : ""}<section class="graph-inspector__section"><strong>Thông số</strong>${(definition?.properties || []).map((property) => propertyControl(node, property)).join("") || `<p class="graph-empty">Node này không có property.</p>`}</section>`;
    if (this.scope === "media") {
      this.inspectorElement.querySelector(".graph-inspector__head")?.insertAdjacentHTML("afterend", this.operationEvidenceMarkup(operationEvidence ? definition : null));
      const inspectorState = this.inspectorElement.querySelector(".graph-node-state");
      inspectorState?.setAttribute("data-operation-scope-status", operationEvidence?.status || this.operationScope.status);
      inspectorState?.setAttribute("data-operation-scope-verified", String(operationEvidence?.evidenceVerified || this.operationScope.evidenceVerified === true));
    }
  }

  renderArtifactPreview(artifact) {
    if (!artifact) return `<p class="graph-empty">No artifact output yet; execution has not been claimed.</p>`;
    const safe = safeArtifactProjection(artifact);
    if (!safe.safe) return `<div class="graph-preview-fallback"><strong>Preview unavailable</strong><p>${escapeHtml(safe.reason)}</p><p>Only escaped, opaque Hub artifact metadata is displayed.</p></div>`;
    const metadata = `<dl class="graph-preview-meta"><div><dt>Artifact</dt><dd>${escapeHtml(safe.name)}</dd></div><div><dt>Type</dt><dd>${escapeHtml(safe.mediaType)}</dd></div>${safe.sizeBytes === null ? "" : `<div><dt>Size</dt><dd>${escapeHtml(String(safe.sizeBytes))} bytes</dd></div>`}</dl>`;
    const open = `<button class="button button--compact" type="button" data-preview-artifact="${escapeHtml(safe.id)}" data-artifact-url="${escapeHtml(safe.url)}" data-artifact-name="${escapeHtml(safe.name)}" data-artifact-type="${escapeHtml(safe.mediaType)}" data-artifact-mask="${String(safe.mask)}">Open preview</button>`;
    if (safe.kind === "image") return `${metadata}<img class="graph-preview-image" src="${escapeHtml(safe.url)}" alt="${escapeHtml(safe.name)}" loading="lazy" />${open}`;
    if (safe.kind === "video") return `${metadata}<video class="graph-preview-media" controls preload="metadata" src="${escapeHtml(safe.url)}">Video preview unavailable in this browser.</video>${open}`;
    if (safe.kind === "audio") return `${metadata}<audio class="graph-preview-media" controls preload="metadata" src="${escapeHtml(safe.url)}">Audio preview unavailable in this browser.</audio>${open}`;
    return `${metadata}<div class="graph-preview-fallback"><strong>No native preview</strong><p>Escaped metadata only; this artifact type is not rendered as media.</p></div>`;
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
    node._hubEditor = this;
    const count = this.liteGraph._nodes.length;
    node.pos = [260 + (count % 5) * 46, 130 + (count % 7) * 34];
    this.mutate(() => this.liteGraph.add(node));
    this.liteCanvas.selectNode(node);
    this.liteCanvas.centerOnNode(node);
  }

  selectAllNodes() {
    if (!this.liteCanvas) return;
    this.liteCanvas.deselectAllNodes();
    this.liteCanvas.selectNodes(this.liteGraph?._nodes || [], false);
    this.renderInspector();
  }

  moveSelected(dx, dy) {
    const selected = this.selectedNodes();
    if (!selected.length) return;
    this.mutate(() => {
      selected.forEach((node) => {
        node.pos[0] = Math.round(Number(node.pos?.[0] || 0) + dx);
        node.pos[1] = Math.round(Number(node.pos?.[1] || 0) + dy);
        node.setDirtyCanvas?.(true, true);
      });
    });
    this.drawMinimap();
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
      node._hubEditor = this;
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
    // V5-C keeps graph edits and drags declarative. Preview/run is always an
    // explicit user action; this method remains as a compatibility hook for
    // older saved preferences but never schedules a workload.
    return;
  }

  async run({ auto = false } = {}) {
    if (this.activeJobId) return;
    const eligibility = this.runEligibility();
    if (!eligibility.eligible) {
      this.showToast(eligibility.reason, "warning");
      this.updateToolbar();
      return false;
    }
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
      return true;
    } catch (error) {
      this.showToast(error.message, "error");
      return false;
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
    const run = this.root.querySelector('[data-graph-action="run"]');
    const eligibility = this.runEligibility();
    if (run) {
      run.disabled = Boolean(this.activeJobId) || !eligibility.eligible;
      run.setAttribute("aria-disabled", String(!eligibility.eligible));
      run.dataset.runGated = String(!eligibility.eligible);
      if (eligibility.eligible) run.removeAttribute("title");
      else run.setAttribute("title", eligibility.reason);
    }
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
    if (action === "run") {
      const eligibility = this.runEligibility();
      if (!eligibility.eligible) { this.showToast(eligibility.reason, "warning"); this.updateToolbar(); return; }
      this.run();
    }
    if (action === "validate") this.validate(false);
    if (action === "cancel") this.cancel();
    if (action === "undo") this.undo();
    if (action === "redo") this.redo();
    if (action === "clear-selection") this.clearSelection();
    if (action === "delete") this.deleteSelected();
    if (action === "toggle-palette") this.updatePanelState({ palette: this.panelState.palette === "collapsed" ? "open" : "collapsed" });
    if (action === "toggle-inspector") this.updatePanelState({ inspector: this.panelState.inspector === "collapsed" ? "open" : "collapsed" });
    if (action === "toggle-canvas-focus") this.updatePanelState({ canvasFocus: !this.panelState.canvasFocus });
    if (action === "toggle-preview") this.updatePanelState({ preview: this.panelState.preview === "expanded" ? "compact" : "expanded" });
    if (action === "toggle-guide") this.updatePanelState({ guide: !this.panelState.guide });
    if (action === "cycle-palette-width") this.updatePanelState({ paletteWidth: this.nextPanelWidth(this.panelState.paletteWidth) });
    if (action === "cycle-inspector-width") this.updatePanelState({ inspectorWidth: this.nextPanelWidth(this.panelState.inspectorWidth) });
    if (action === "save-local") { this.saveLocal(); return; }
    if (action === "duplicate") this.duplicateWorkflow();
    if (action === "fit") this.fitView();
    if (action === "export") this.exportGraph();
    if (action === "save-library") { this.saveToLibrary(); return; }
    if (action === "save-local") { this.persist(); this.showToast("Workflow đã lưu local trong WebView."); }
  }

  nextPanelWidth(value) {
    return { default: "wide", wide: "narrow", narrow: "default" }[value] || "default";
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
