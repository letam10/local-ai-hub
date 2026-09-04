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
  preflightWorkflowRuntimeV2,
  runNodeGraph,
  uploadFile,
  validateNodeGraph,
  clearNodeDraft,
  getNodeDraft,
  saveNodeDraft,
} from "../../api.js";
import { workflowLibraryEntry } from "../../workflow_library.js";
import { currentLanguage, translateText } from "../../i18n.js";

// LiteGraph.js is intentionally pinned and served from /ui/vendor.  This file
// is the Hub adapter: it maps mature canvas-editor state to the Hub DAG API.
const LOCAL_PREFIX = "local-ai-hub-graph-v5";
const LEGACY_PREFIX = "local-ai-hub-node-studio-v1";
const WORKFLOW_INDEX_PREFIX = "local-ai-hub-workflows-v1";
const MAX_HISTORY = 60;
const MAX_RECENT_WORKFLOWS = 12;
const PRESET_BY_SCOPE = { image: "image_create_upscale", sam2: "sam2_segment", media: "video_creative_pipeline", video: "video_creative_pipeline", animesr: "animesr_pipeline" };
const TYPE_COLORS = {
  IMAGE: "#cf7cff", MASK: "#42c6a0", VIDEO: "#f17c8e", AUDIO: "#f1ad5f",
  TEXT: "#6c8cff", NUMBER: "#a9c6ff", BOOLEAN: "#e5d66a", MODEL: "#e291c7", METADATA: "#8794ad",
};
const CATEGORY_COLORS = { utility: "#6c8cff", image: "#cf7cff", vision: "#42c6a0", media: "#f1ad5f", video: "#f17c8e", annotation: "#8794ad" };
const SOCKET_TYPES = Object.freeze(["IMAGE", "MASK", "VIDEO", "AUDIO", "TEXT", "NUMBER", "BOOLEAN", "MODEL", "METADATA"]);
const socketColor = (type) => TYPE_COLORS[String(type || "").toUpperCase()] || TYPE_COLORS.METADATA;
const isMediaScope = (scope) => scope === "media" || scope === "video";
const NODE_COPY = Object.freeze({
  utility: "Tiện ích", image: "Hình ảnh", vision: "Thị giác", media: "Media", video: "Video", annotation: "Chú thích",
  "Load Image": "Tải ảnh", "Load Video": "Tải video", "Load Audio": "Tải âm thanh", "Load Subtitle": "Tải phụ đề", "Prompt / Text": "Prompt / Văn bản", Number: "Số", Boolean: "Đúng / Sai", "Point Input": "Điểm đầu vào", "Box Input": "Hộp đầu vào", "Preview Image": "Xem trước ảnh", "Preview Video": "Xem trước video", "Save Image": "Lưu ảnh", "Save Video": "Lưu video", "Export Mask": "Xuất mask", "Export Video": "Xuất video", Comment: "Ghi chú", Group: "Nhóm", Resolution: "Độ phân giải", Seed: "Seed", "Steps / Sampler": "Steps / Bộ lấy mẫu", "FLUX Generate": "Tạo ảnh FLUX", "Qwen Image Generate": "Tạo ảnh Qwen", "Image Edit / Image-to-Image": "Sửa ảnh / Ảnh sang ảnh", "AnimeSR Upscale": "Nâng cấp AnimeSR", "Real-ESRGAN": "Real-ESRGAN", "Frame Interpolation": "Nội suy khung hình", Encode: "Mã hóa", "Subtitle Burn": "Ghi phụ đề", "Extract Frames": "Tách khung hình", Rotate: "Xoay", FPS: "FPS", "Extract Audio": "Tách âm thanh", "Replace Audio": "Thay âm thanh", Resize: "Đổi kích thước", "ComfyUI Workflow": "Workflow ComfyUI", "Video Generate (backend partial)": "Tạo video (backend một phần)", "Video Transform": "Biến đổi video", "Video Upscale (AnimeSR / FFmpeg)": "Nâng cấp video (AnimeSR / FFmpeg)", "Video Grade": "Hiệu chỉnh video", "Logo / Image Overlay": "Phủ logo / ảnh", "Video Generate": "Tạo video", "Video Transform": "Biến đổi video", "Text Overlay (unavailable)": "Phủ chữ (chưa khả dụng)", "Trim / Cut": "Cắt", Concat: "Nối", Crop: "Cắt khung", Flip: "Lật", "Audio Loudness": "Độ lớn âm thanh", "Color / Levels": "Màu / mức sáng", "Image Compare A/B": "So sánh ảnh A/B", "Mask Apply": "Áp dụng mask", "Mask Composite": "Ghép mask", "Mask Preview": "Xem trước mask", "Probe Audio": "Đọc metadata âm thanh", "Probe Video": "Đọc metadata video", "Grounding DINO": "Grounding DINO", "Grounding Prompt": "Prompt Grounding", "RF-DETR Detect": "Phát hiện RF-DETR", "SAM2 Segment": "Phân vùng SAM2", "SAM2 Track": "Theo dõi SAM2", "Upscale Image (FFmpeg fallback)": "Nâng cấp ảnh (FFmpeg dự phòng)",
  "Connect": "Kết nối", socket: "cổng", "Compatible node ports": "Cổng node tương thích", "Search compatible nodes": "Tìm node tương thích", "Rejected candidates": "Ứng viên bị loại", "Close connection picker": "Đóng bộ chọn kết nối", "Only explicitly compatible typed ports are shown.": "Chỉ hiển thị các cổng typed tương thích rõ ràng.", "Auto-connected": "Đã tự kết nối", "Input is already connected; disconnect it before adding another edge.": "Đầu vào đã được kết nối; hãy ngắt kết nối trước khi thêm cạnh khác.", "Incompatible typed socket": "Cổng typed không tương thích", "Compatible socket could not be connected safely; no node was added.": "Không thể nối cổng tương thích an toàn; không thêm node.", "Typed socket mismatch": "Cổng typed không tương thích", "Input is already connected and is not multi.": "Đầu vào đã được kết nối và không hỗ trợ đa kết nối.", "Compatible typed socket.": "Cổng typed tương thích.",
  "Node Guide": "Hướng dẫn Node",
  "Node Guide · Hướng dẫn toàn diện Hub Nodes & Typed Workflow": "Hướng dẫn Node · Hướng dẫn toàn diện Hub Nodes & Typed Workflow",
  "Status truthful": "Trạng thái thực thi",
  "Execution status": "Trạng thái thực thi",
  "Capability": "Khả năng",
  "Execution state": "Trạng thái thực thi",
  "Artifact": "Artifact",
  "Safe artifact preview": "Xem trước artifact an toàn",
  "Parameters": "Thông số",
  "Validation": "Kiểm tra",
  "Dirty / downstream": "Thay đổi / phụ thuộc",
  "Cache": "Bộ nhớ đệm",
  "Progress": "Tiến độ",
  "Error": "Lỗi",
  "valid": "hợp lệ",
  "dirty": "có thay đổi",
  "clean": "sạch",
  "hit": "có trong bộ nhớ đệm",
  "miss": "không có trong bộ nhớ đệm",
  "none": "không có",
  "not_run": "chưa chạy",
  "operational": "sẵn sàng",
  "partial": "một phần",
  "unavailable": "chưa khả dụng",
  "No artifact output yet": "Chưa có artifact đầu ra",
  "Partial output": "Đầu ra một phần",
  "Preview unavailable": "Bản xem trước chưa khả dụng",
  "No artifact from failed run": "Không có artifact từ lần chạy thất bại",
  "No native preview": "Không có bản xem trước gốc",
  "Artifact": "Artifact",
  "Type": "Loại",
  "Size": "Kích thước",
  "Open preview": "Mở bản xem trước",
  "Mask": "Mask",
  "Escaped metadata only; this artifact type is not rendered as media.": "Chỉ hiển thị metadata đã thoát; loại artifact này không được dựng thành media.",
  "Preview unavailable in this node snapshot; no safe artifact was published.": "Bản xem trước chưa khả dụng trong snapshot node này; chưa có artifact an toàn được công bố.",
  "Output states": "Trạng thái output",
  "Control unavailable; server metadata is not recognized.": "Control chưa khả dụng; metadata server không được nhận dạng.",
  "Add node": "Thêm node",
  "Search nodes…": "Tìm node…",
  "Show all nodes": "Hiện tất cả node",
  "Show recommended nodes": "Hiện node được khuyến nghị",
  "Recommended · ": "Khuyến nghị · ",
  "Node actions": "Thao tác node",
  "Center node": "Đưa node vào giữa",
  "Duplicate node": "Nhân bản node",
  "Delete node": "Xóa node",
  "Auto layout": "Tự động sắp xếp",
  "No output": "Chưa có",
  "Result available": "Có kết quả",
  "Video grade": "Hiệu chỉnh video",
  "Logo overlay": "Phủ logo",
  "Encode": "Mã hóa",
  "No compatible node port matches this search.": "Không có cổng node tương thích với tìm kiếm này.",
  "Media operation scope is not applied to this workspace; no execution is claimed.": "Phạm vi thao tác media không áp dụng cho workspace này; không tuyên bố thực thi.",
  "This node is outside the exact published media operation scope.": "Node này nằm ngoài phạm vi thao tác media chính xác đã công bố.",
  "Use only the three exactly evidenced media operations.": "Chỉ sử dụng ba thao tác media có bằng chứng chính xác.",
  "Media operation evidence": "Bằng chứng thao tác media",
  "Video grade evidence": "Bằng chứng hiệu chỉnh video",
  "Logo overlay evidence": "Bằng chứng phủ logo",
  "Encode evidence": "Bằng chứng mã hóa",
  "Next action": "Hành động tiếp theo",
  "Server snapshot": "Snapshot máy chủ",
  "execution:": "thực thi:",
  "no UI execution": "không thực thi trên giao diện",
  "No completed exact evidence is available for the listed media operations.": "Chưa có bằng chứng chính xác đã hoàn tất cho các thao tác media được liệt kê.",
  "Keep the listed operations and all other media tools partial until separately evidenced.": "Giữ các thao tác được liệt kê và mọi công cụ media khác ở mức một phần cho đến khi có bằng chứng riêng.",
  "This exact operation is not verified in the server snapshot.": "Thao tác chính xác này chưa được xác minh trong snapshot máy chủ.",
  "Keep this operation partial until separately evidenced.": "Giữ thao tác này ở mức một phần cho đến khi có bằng chứng riêng.",
  "Exact media evidence is published for video grade, logo overlay and encode; opening Node Studio does not execute a worker.": "Đã công bố bằng chứng media chính xác cho hiệu chỉnh video, phủ logo và mã hóa; mở Node Studio không chạy worker.",
  "No exact media evidence is verified in this server snapshot; scoped nodes remain partial and no execution is claimed.": "Snapshot máy chủ chưa xác minh bằng chứng media chính xác; các node trong phạm vi vẫn là một phần và không tuyên bố thực thi.",
  "NODE WORKFLOW": "WORKFLOW NODE",
  "Recent": "Gần đây",
  "Recent workflows": "Workflow gần đây",
  "Run Graph": "Chạy Graph",
  "Export JSON": "Xuất JSON",
  "Import JSON": "Nhập JSON",
  "Preview indicator (manual; no auto-run)": "Chỉ báo preview (thủ công; không tự chạy)",
  "Palette": "Bảng node",
  "Inspector": "Bảng kiểm tra",
  "Canvas focus": "Tập trung canvas",
  "Palette width": "Độ rộng bảng node",
  "Inspector width": "Độ rộng bảng kiểm tra",
  "Preview size": "Kích thước preview",
  "Fit": "Vừa khung",
  "Minimap graph": "Minimap graph",
  "Open preview": "Mở bản xem trước",
  "Inspector / Live preview": "Bảng kiểm tra / Preview trực tiếp",
  "No properties": "Node này không có thuộc tính.",
  "Build video creative graph": "Tạo graph sáng tạo video", "Library": "Thư viện", "ready": "sẵn sàng", "LiteGraph workflow canvas": "Canvas workflow LiteGraph", "Run Graph": "Chạy graph",
  "No completed exact evidence is available in this server snapshot.": "Snapshot máy chủ chưa có bằng chứng chính xác đã hoàn tất.",
  "Chưa có bản xem trước an toàn trong trạng thái này.": "Chưa có bản xem trước an toàn trong trạng thái này.",
  "Bật": "Bật", "Tắt": "Tắt",
});
const NODE_STATIC_TEXT = Object.freeze([
  "NODE WORKFLOW", "Recent", "Recent workflows", "Export JSON", "Import JSON", "Preview indicator (manual; no auto-run)",
  "Palette", "Inspector", "Canvas focus", "Palette width", "Inspector width", "Preview size", "Fit", "Auto layout", "Minimap graph",
  "Inspector / Live preview", "Open preview",
]);
const localizeNodeStaticMarkup = (root) => {
  if (!root?.ownerDocument) return;
  // Keep the helper usable in the browser and in the pure Node/data-URL test
  // harness, where the global NodeFilter constructor is not exposed.
  const showText = root.ownerDocument.defaultView?.NodeFilter?.SHOW_TEXT ?? 4;
  const walker = root.ownerDocument.createTreeWalker(root, showText);
  const nodes = [];
  let current;
  while ((current = walker.nextNode())) nodes.push(current);
  nodes.forEach((textNode) => {
    const value = textNode.nodeValue || "";
    const key = value.trim();
    if (!NODE_STATIC_TEXT.includes(key)) return;
    const leading = value.slice(0, value.indexOf(key));
    const trailing = value.slice(value.indexOf(key) + key.length);
    textNode.nodeValue = `${leading}${nodeText(key)}${trailing}`;
  });
};
const NODE_UI_STATE_VERSION = 1;
const nodeText = (value) => {
  const key = String(value ?? "");
  // Pure helper tests evaluate the bounded renderer without the page module's
  // i18n imports; keep that contract deterministic while using the real
  // translators whenever the full browser module is loaded.
  const translated = typeof translateText === "function" ? translateText(key) : key;
  const language = typeof currentLanguage === "function" ? currentLanguage() : "en";
  return translated !== key ? translated : language === "vi" ? (NODE_COPY[key] || key) : key;
};
const NODE_UI_STATE_PREFIX = `${LOCAL_PREFIX}:ui:v${NODE_UI_STATE_VERSION}`;
const OPAQUE_ARTIFACT_ID = /^artifact_[a-f0-9]{32}$/;
const SAFE_ARTIFACT_URL = /^\/api\/artifacts\/artifact_[a-f0-9]{32}$/;
const SAFE_MEDIA_TYPE = /^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/;
const UNSAFE_ARTIFACT_TEXT = /(?:[a-z]:[\\/]|\\\\|(?:https?|file|data|ftp):|\b(?:bearer|api[_-]?key|password|secret|token|cookie|private[_ -]?key)\b|\b(?:cmd|powershell|bash|ffmpeg|python|callable|manifest|command)\b)/i;
const MAX_PREVIEW_ARTIFACTS = 32;
const MAX_ARTIFACT_SCAN_ITEMS = 256;
const MAX_ARTIFACT_CHILDREN = 128;
const MAX_ARTIFACT_NAME_LENGTH = 160;
const MEDIA_OPERATION_SCOPE_IDS = Object.freeze(["video_grade", "logo_overlay", "encode"]);
const MEDIA_OPERATION_SCOPE_LABELS = Object.freeze({ video_grade: "Video grade", logo_overlay: "Logo overlay", encode: "Encode" });
const MEDIA_OPERATION_SCOPE_FALLBACK = Object.freeze({ status: "unavailable", execution: "not_run", evidenceVerified: false, availableOperations: [], operationStatus: { video_grade: "partial", logo_overlay: "partial", encode: "partial" }, reason: "No completed exact media evidence is available in this server snapshot.", nextAction: "Keep these operations partial until separately evidenced." });
const SAFE_JOB_ID = /^job[A-Za-z0-9._:@-]{1,119}$/;
const SAFE_GRAPH_NODE_ID = /^[A-Za-z][A-Za-z0-9_-]{0,79}$/;
const PERSISTED_RUN_STATE_VERSION = 1;
const MAX_PERSISTED_NODE_STATES = 128;
const MAX_PERSISTED_ARTIFACTS = 32;
const RUN_STATUS_VALUES = Object.freeze(["not_run", "queued", "starting", "running", "cancelling", "completed", "failed", "error", "cancelled", "interrupted", "unavailable"]);
const ACTIVE_RUN_STATUS_VALUES = Object.freeze(["queued", "starting", "running", "cancelling"]);
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
  if (scope !== "media" && scope !== "video") return { eligible: true, reason: "" };
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
    return { compatible: false, reason: `${nodeText("Typed socket mismatch")}: ${sourceType || "unknown"} -> ${targetType || "unknown"}.` };
  }
  if (occupied && !targetPort?.multi) {
    return { compatible: false, reason: nodeText("Input is already connected and is not multi.") };
  }
  return { compatible: true, reason: nodeText("Compatible typed socket.") };
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
  try {
    if (!value || typeof value !== "object" || Array.isArray(value)) return { safe: false, reason: "Artifact metadata is unavailable." };
    const id = typeof value.id === "string" ? value.id : "";
    const url = typeof value.url === "string" ? value.url : "";
    if (!OPAQUE_ARTIFACT_ID.test(id) || url !== `/api/artifacts/${id}` || !SAFE_ARTIFACT_URL.test(url)) {
      return { safe: false, reason: "Preview requires the existing opaque Hub artifact URL." };
    }
    if (value.media_type !== undefined && typeof value.media_type !== "string") return { safe: false, reason: "Artifact metadata is unavailable." };
    if (value.name !== undefined && typeof value.name !== "string") return { safe: false, reason: "Artifact metadata is unavailable." };
    if (value.size_bytes !== undefined && value.size_bytes !== null && (!Number.isSafeInteger(value.size_bytes) || value.size_bytes < 0)) return { safe: false, reason: "Artifact metadata is unavailable." };
    if (value.mask !== undefined && typeof value.mask !== "boolean") return { safe: false, reason: "Artifact metadata is unavailable." };
    const mediaType = (value.media_type || "application/octet-stream").split(";", 1)[0].trim().toLocaleLowerCase();
    if (!SAFE_MEDIA_TYPE.test(mediaType)) return { safe: false, reason: "Artifact metadata is unavailable." };
    const rawName = value.name || "Artifact";
    if (UNSAFE_ARTIFACT_TEXT.test(rawName)) return { safe: false, reason: "Artifact metadata is unavailable." };
    const name = rawName.replace(/[\\/\r\n]+/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX_ARTIFACT_NAME_LENGTH) || "Artifact";
    const kind = mediaType.startsWith("image/") ? "image" : mediaType.startsWith("video/") ? "video" : mediaType.startsWith("audio/") ? "audio" : "metadata";
    return {
      safe: true,
      id,
      url,
      name,
      mediaType,
      sizeBytes: value.size_bytes === undefined || value.size_bytes === null ? null : value.size_bytes,
      kind,
      mask: value.mask === true || mediaType.includes("mask"),
    };
  } catch {
    return { safe: false, reason: "Artifact metadata is unavailable." };
  }
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

export function collectArtifactProjections(values) {
  const roots = Array.isArray(values) ? values : [values];
  const items = [];
  const seenIds = new Set();
  const seenObjects = new Set();
  let inspected = 0;
  let truncated = false;
  const visit = (value) => {
    if (inspected >= MAX_ARTIFACT_SCAN_ITEMS || items.length >= MAX_PREVIEW_ARTIFACTS) {
      truncated = true;
      return;
    }
    if (!value || typeof value !== "object" || seenObjects.has(value)) return;
    seenObjects.add(value);
    inspected += 1;
    try {
      if (!Array.isArray(value)) {
        const id = typeof value.id === "string" ? value.id : typeof value.artifact_id === "string" ? value.artifact_id : "";
        if (id && (Object.prototype.hasOwnProperty.call(value, "url") || Object.prototype.hasOwnProperty.call(value, "media_type"))) {
          const safe = safeArtifactProjection({
            id,
            url: value.url,
            media_type: value.media_type,
            name: value.name,
            size_bytes: value.size_bytes,
            mask: value.mask,
          });
          if (safe.safe && !seenIds.has(safe.id)) {
            seenIds.add(safe.id);
            items.push(safe);
          }
        }
      }
      if (items.length >= MAX_PREVIEW_ARTIFACTS) {
        truncated = true;
        return;
      }
      const keys = Array.isArray(value) ? value.map((_, index) => index) : Object.keys(value);
      if (keys.length > MAX_ARTIFACT_CHILDREN) truncated = true;
      for (const key of keys.slice(0, MAX_ARTIFACT_CHILDREN)) {
        visit(value[key]);
        if (items.length >= MAX_PREVIEW_ARTIFACTS) {
          truncated = true;
          return;
        }
      }
    } catch {
      return;
    }
  };
  for (const root of roots) {
    visit(root);
    if (items.length >= MAX_PREVIEW_ARTIFACTS) {
      truncated = true;
      break;
    }
  }
  return { items, truncated };
}

const ARTIFACT_PREVIEW_STATE_COPY = Object.freeze({
  completed: ["Có artifact đầu ra", "Node đã hoàn tất; chỉ artifact opaque do server công bố mới được hiển thị."],
  partial: ["Đầu ra một phần", "Node chưa hoàn tất đầy đủ; chỉ artifact an toàn đã công bố mới được xem."],
  failed: ["Tác vụ thất bại", "Node thất bại hoặc bị hủy; không có artifact an toàn từ lần chạy này."],
  unavailable: ["Chưa khả dụng", "Node đang thiếu phụ thuộc hoặc bằng chứng; không khẳng định có artifact."],
  not_run: ["Chưa có đầu ra để xem trước", "Node chưa chạy; không suy đoán rằng artifact đã được tạo."],
});

const INLINE_CONTROL_TYPES = Object.freeze([
  "text", "prompt", "textarea", "integer", "number", "slider", "color", "size", "select", "toggle", "artifact",
]);
const INLINE_CONTROL_ALIASES = Object.freeze({
  asset: "artifact",
  boolean: "toggle",
  combo: "select",
  encoder: "select",
});
const OUTPUT_STATE_COPY = Object.freeze({
  none: ["Chưa có", "Chưa có artifact an toàn được công bố cho output này."],
  running: ["Đang chạy", "Node đang xử lý; chưa khẳng định artifact đã được công bố."],
  completed: ["Có kết quả", "Output đã có kết quả từ snapshot server-owned."],
  error: ["Lỗi", "Node lỗi hoặc bị hủy; không hiển thị output giả."],
});
const OUTPUT_STATE_LABEL_KEYS = Object.freeze({ none: "No output", running: "Running", completed: "Result available", error: "Error" });
const NODE_GRID_SIZE = 40;
const NODE_LAYOUT_GAP_X = 72;
const NODE_LAYOUT_GAP_Y = 32;
const CONTEXT_MENU_LIMIT = 24;
const NODE_MIN_WIDTH = 320;
const NODE_MAX_WIDTH = 720;
const NODE_MIN_HEIGHT = 96;
const NODE_MAX_HEIGHT = 1200;
const NODE_OUTPUT_STATE_HEADER_HEIGHT = 22;
const NODE_OUTPUT_STATE_ROW_HEIGHT = 18;
const NODE_INLINE_MIN_HEIGHT = 28;
const NODE_INLINE_MULTILINE_MAX_HEIGHT = 92;
const NODE_INLINE_EDITOR_MAX_HEIGHT = 240;

const finiteOr = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;

export function normalizeNodeSize(value, fallback = { width: NODE_MIN_WIDTH, height: NODE_MIN_HEIGHT }) {
  const source = value && typeof value === "object" ? value : {};
  const fallbackSource = fallback && typeof fallback === "object" ? fallback : {};
  const rawWidth = Array.isArray(source) ? source[0] : source.width ?? source[0];
  const rawHeight = Array.isArray(source) ? source[1] : source.height ?? source[1];
  const fallbackWidth = Array.isArray(fallbackSource) ? fallbackSource[0] : fallbackSource.width ?? fallbackSource[0];
  const fallbackHeight = Array.isArray(fallbackSource) ? fallbackSource[1] : fallbackSource.height ?? fallbackSource[1];
  const width = finiteOr(rawWidth, finiteOr(fallbackWidth, NODE_MIN_WIDTH));
  const height = finiteOr(rawHeight, finiteOr(fallbackHeight, NODE_MIN_HEIGHT));
  return {
    width: Math.round(Math.max(NODE_MIN_WIDTH, Math.min(NODE_MAX_WIDTH, width))),
    height: Math.round(Math.max(NODE_MIN_HEIGHT, Math.min(NODE_MAX_HEIGHT, height))),
  };
}

/**
 * Keep the Hub authoring contract stable across the bundled LiteGraph build.
 * The upstream selection helpers leave deselected nodes in selected_nodes and
 * collapse a multi-selection before a drag starts.  Both cases make the
 * visible selection disagree with the nodes that would be moved.
 */
export function installLiteGraphSelectionCompatibility(canvas) {
  if (!canvas || canvas.__hubSelectionCompatibilityInstalled) return canvas;
  canvas.__hubSelectionCompatibilityInstalled = true;
  const nativeDeselectNode = typeof canvas.deselectNode === "function" ? canvas.deselectNode.bind(canvas) : null;
  if (nativeDeselectNode) {
    canvas.deselectNode = (node) => {
      const result = nativeDeselectNode(node);
      if (node?.id !== undefined && canvas.selected_nodes) delete canvas.selected_nodes[node.id];
      return result;
    };
  }
  const nativeProcessNodeSelected = typeof canvas.processNodeSelected === "function" ? canvas.processNodeSelected.bind(canvas) : null;
  if (nativeProcessNodeSelected) {
    canvas.processNodeSelected = (node, event) => {
      const additive = Boolean(event?.shiftKey || event?.ctrlKey || event?.metaKey || canvas.multi_select);
      const selectedCount = Object.keys(canvas.selected_nodes || {}).length;
      if (node?.is_selected && !additive && selectedCount > 1) {
        canvas.current_node = node;
        return;
      }
      return nativeProcessNodeSelected(node, event);
    };
  }
  return canvas;
}

/**
 * Resolve the server-owned UI metadata without inventing a fallback for an
 * explicitly unknown control.  ``kind`` remains the graph/schema authority;
 * ``ui`` only projects that same property into a canvas widget.
 */
export function inlineControlMetadata(property) {
  if (!property || typeof property !== "object" || Array.isArray(property)) return null;
  const ui = property.ui && typeof property.ui === "object" && !Array.isArray(property.ui) ? property.ui : {};
  const hasExplicitControl = Object.prototype.hasOwnProperty.call(ui, "control") || Object.prototype.hasOwnProperty.call(property, "control");
  const rawControl = String(ui.control ?? property.control ?? property.kind ?? "").trim().toLocaleLowerCase();
  const control = hasExplicitControl ? rawControl : (INLINE_CONTROL_ALIASES[rawControl] || rawControl);
  if (!INLINE_CONTROL_TYPES.includes(control)) return null;
  const options = Array.isArray(ui.options) ? ui.options : Array.isArray(property.options) ? property.options : [];
  const label = String(ui.label ?? property.label ?? property.name ?? "").slice(0, 160);
  const name = typeof property.name === "string" ? property.name : "";
  const multiline = ui.multiline === true || control === "prompt" || control === "textarea" || /prompt/i.test(name);
  return {
    control,
    name,
    label,
    group: String(ui.group ?? "General").slice(0, 80),
    order: finiteOr(ui.order, 0),
    multiline,
    minimum: ui.minimum ?? property.min,
    maximum: ui.maximum ?? property.max,
    step: ui.step ?? property.step,
    options: options.slice(0, 64),
    unit: String(ui.unit ?? "").slice(0, 24),
    placeholder: String(ui.placeholder ?? "").slice(0, 120),
    advanced: ui.advanced === true,
    maxLength: ui.max_length ?? property.max_length,
  };
}

export function normalizeInlineControlValue(property, rawValue) {
  const control = inlineControlMetadata(property);
  if (!control) return { accepted: false, reason: "unknown_control_type" };
  if (control.control === "toggle") {
    if (typeof rawValue === "boolean") return { accepted: true, value: rawValue };
    if (rawValue === "true" || rawValue === "1" || rawValue === 1) return { accepted: true, value: true };
    if (rawValue === "false" || rawValue === "0" || rawValue === 0) return { accepted: true, value: false };
    return { accepted: false, reason: "boolean_required" };
  }
  if (control.control === "select") {
    const selected = control.options.find((option) => String(option) === String(rawValue));
    if (selected === undefined) return { accepted: false, reason: "option_not_published" };
    return { accepted: true, value: selected };
  }
  if (control.control === "artifact") {
    const value = typeof rawValue === "string" ? rawValue.trim() : "";
    return OPAQUE_ARTIFACT_ID.test(value) ? { accepted: true, value } : { accepted: false, reason: "opaque_artifact_id_required" };
  }
  if (control.control === "color") {
    const value = typeof rawValue === "string" ? rawValue.trim() : "";
    return /^#[0-9a-f]{6,8}$/i.test(value) ? { accepted: true, value } : { accepted: false, reason: "hex_color_required" };
  }
  if (["integer", "number", "slider", "size"].includes(control.control)) {
    const value = Number(rawValue);
    if (!Number.isFinite(value)) return { accepted: false, reason: "finite_number_required" };
    let normalized = control.control === "integer" ? Math.round(value) : value;
    if (control.minimum !== undefined && control.minimum !== null && Number.isFinite(Number(control.minimum))) normalized = Math.max(Number(control.minimum), normalized);
    if (control.maximum !== undefined && control.maximum !== null && Number.isFinite(Number(control.maximum))) normalized = Math.min(Number(control.maximum), normalized);
    return { accepted: true, value: normalized };
  }
  const value = typeof rawValue === "string" ? rawValue : String(rawValue ?? "");
  if (control.maxLength !== undefined && Number.isFinite(Number(control.maxLength)) && value.length > Number(control.maxLength)) {
    return { accepted: false, reason: "text_too_long" };
  }
  return { accepted: true, value };
}

function nodeGeometry(node) {
  const pos = Array.isArray(node?.pos) || ArrayBuffer.isView(node?.pos) ? node.pos : [node?.position?.x ?? node?.x, node?.position?.y ?? node?.y];
  const size = Array.isArray(node?.size) || ArrayBuffer.isView(node?.size) ? node.size : [node?.size?.width ?? node?.width, node?.size?.height ?? node?.height];
  return {
    id: String(node?.hubId ?? node?.id ?? ""),
    x: finiteOr(pos?.[0], 0),
    y: finiteOr(pos?.[1], 0),
    width: Math.max(1, finiteOr(size?.[0], NODE_MIN_WIDTH)),
    height: Math.max(1, finiteOr(size?.[1], 100)),
  };
}

export function nodesOverlap(first, second, gap = 0) {
  const a = nodeGeometry(first);
  const b = nodeGeometry(second);
  const padding = Math.max(0, finiteOr(gap, 0));
  return a.x < b.x + b.width + padding && a.x + a.width + padding > b.x && a.y < b.y + b.height + padding && a.y + a.height + padding > b.y;
}

export function graphBounds(nodes = [], padding = 0) {
  const values = (Array.isArray(nodes) ? nodes : []).map(nodeGeometry);
  if (!values.length) return { left: 0, top: 0, right: 1, bottom: 1, width: 1, height: 1 };
  const inset = Math.max(0, finiteOr(padding, 0));
  const left = Math.min(...values.map((item) => item.x)) - inset;
  const top = Math.min(...values.map((item) => item.y)) - inset;
  const right = Math.max(...values.map((item) => item.x + item.width)) + inset;
  const bottom = Math.max(...values.map((item) => item.y + item.height)) + inset;
  return { left, top, right, bottom, width: Math.max(1, right - left), height: Math.max(1, bottom - top) };
}

export function minimapWorldBounds({ nodes = [], viewport = [0, 0], offset = [0, 0], scale = 1, padding = 24 } = {}) {
  const zoom = Math.max(0.01, finiteOr(scale, 1));
  const viewportWidth = Math.max(1, finiteOr(Array.isArray(viewport) ? viewport[0] : viewport?.width, 1));
  const viewportHeight = Math.max(1, finiteOr(Array.isArray(viewport) ? viewport[1] : viewport?.height, 1));
  const offsetX = finiteOr(Array.isArray(offset) ? offset[0] : offset?.x, 0);
  const offsetY = finiteOr(Array.isArray(offset) ? offset[1] : offset?.y, 0);
  const viewportNode = {
    id: "__current_viewport__",
    pos: [-offsetX / zoom, -offsetY / zoom],
    size: [viewportWidth / zoom, viewportHeight / zoom],
  };
  return graphBounds([...(Array.isArray(nodes) ? nodes : []), viewportNode], padding);
}

function candidateGridOffsets(radius) {
  if (radius === 0) return [[0, 0]];
  const result = [];
  for (let x = -radius; x <= radius; x += 1) {
    result.push([x, -radius], [x, radius]);
  }
  for (let y = -radius + 1; y < radius; y += 1) {
    result.push([-radius, y], [radius, y]);
  }
  return result;
}

export function findFreeGridSlot(nodes = [], requested = {}, size = {}, options = {}) {
  const grid = Math.max(1, finiteOr(options.grid, NODE_GRID_SIZE));
  const gap = Math.max(0, finiteOr(options.gap, NODE_LAYOUT_GAP_Y));
  const maxRadius = Math.max(1, Math.min(64, Math.floor(finiteOr(options.maxRadius, 32))));
  const width = Math.max(1, finiteOr(size.width ?? size[0], NODE_MIN_WIDTH));
  const height = Math.max(1, finiteOr(size.height ?? size[1], 100));
  const requestedX = finiteOr(requested.x ?? requested[0], 0);
  const requestedY = finiteOr(requested.y ?? requested[1], 0);
  const startX = options.snap === false ? requestedX : Math.round(requestedX / grid) * grid;
  const startY = options.snap === false ? requestedY : Math.round(requestedY / grid) * grid;
  const occupied = Array.isArray(nodes) ? nodes : [];
  for (let radius = 0; radius <= maxRadius; radius += 1) {
    const offsets = candidateGridOffsets(radius).sort((a, b) => (Math.abs(a[0]) + Math.abs(a[1])) - (Math.abs(b[0]) + Math.abs(b[1])) || a[1] - b[1] || a[0] - b[0]);
    for (const [offsetX, offsetY] of offsets) {
      const candidate = { x: startX + offsetX * grid, y: startY + offsetY * grid, width, height };
      if (!occupied.some((node) => nodesOverlap(candidate, node, gap))) return { x: Math.round(candidate.x), y: Math.round(candidate.y) };
    }
  }
  // The bounded search is fail-safe: keep the requested column but place the
  // node below the known graph rather than silently overlapping an existing one.
  const bounds = graphBounds(occupied, gap);
  return { x: Math.round(startX), y: Math.round(Math.max(startY, bounds.bottom)) };
}

export function collisionFreeNodePositions(nodes = [], options = {}) {
  const values = (Array.isArray(nodes) ? nodes : []).map((node, index) => ({ ...nodeGeometry(node), sourceIndex: index }));
  values.sort((a, b) => a.id.localeCompare(b.id) || a.sourceIndex - b.sourceIndex);
  const placed = [];
  const positions = [];
  for (const value of values) {
    const direct = { x: value.x, y: value.y, width: value.width, height: value.height };
    const position = placed.some((node) => nodesOverlap(direct, node, finiteOr(options.gap, 0)))
      ? findFreeGridSlot(placed, value, value, { ...options, snap: true })
      : { x: Math.round(value.x), y: Math.round(value.y) };
    const placedNode = { id: value.id, x: position.x, y: position.y, width: value.width, height: value.height };
    placed.push(placedNode);
    positions.push({ id: value.id, x: position.x, y: position.y });
  }
  return positions.sort((a, b) => a.id.localeCompare(b.id));
}

export function computeDeterministicLayout(nodes = [], edges = [], options = {}) {
  const values = (Array.isArray(nodes) ? nodes : []).map((node, index) => ({ ...nodeGeometry(node), sourceIndex: index }));
  const byId = new Map(values.map((node) => [node.id, node]));
  const incoming = new Map(values.map((node) => [node.id, []]));
  const outgoing = new Map(values.map((node) => [node.id, []]));
  for (const edge of Array.isArray(edges) ? edges : []) {
    const source = String(edge?.source?.node ?? edge?.origin_id ?? "");
    const target = String(edge?.target?.node ?? edge?.target_id ?? "");
    if (!byId.has(source) || !byId.has(target) || source === target) continue;
    if (!outgoing.get(source).includes(target)) {
      outgoing.get(source).push(target);
      incoming.get(target).push(source);
    }
  }
  for (const list of [...incoming.values(), ...outgoing.values()]) list.sort((a, b) => a.localeCompare(b));
  const indegree = new Map(values.map((node) => [node.id, incoming.get(node.id).length]));
  const layer = new Map(values.map((node) => [node.id, 0]));
  const queue = values.filter((node) => indegree.get(node.id) === 0).map((node) => node.id).sort((a, b) => a.localeCompare(b));
  const visited = new Set();
  while (queue.length) {
    const current = queue.shift();
    if (visited.has(current)) continue;
    visited.add(current);
    for (const target of outgoing.get(current)) {
      layer.set(target, Math.max(layer.get(target) || 0, (layer.get(current) || 0) + 1));
      indegree.set(target, indegree.get(target) - 1);
      if (indegree.get(target) === 0) queue.push(target);
    }
    queue.sort((a, b) => a.localeCompare(b));
  }
  // Cyclic/invalid fragments remain deterministic and visible; validation
  // still owns the cycle error and this layout never creates an overlap.
  const maxKnownLayer = Math.max(0, ...[...layer.values()]);
  values.filter((node) => !visited.has(node.id)).sort((a, b) => a.id.localeCompare(b.id)).forEach((node, index) => layer.set(node.id, maxKnownLayer + 1 + index));

  const horizontalGap = Math.max(1, finiteOr(options.horizontalGap, NODE_LAYOUT_GAP_X));
  const verticalGap = Math.max(1, finiteOr(options.verticalGap, NODE_LAYOUT_GAP_Y));
  const marginX = finiteOr(options.marginX, 80);
  const marginY = finiteOr(options.marginY, 80);
  const columns = new Map();
  for (const node of values) {
    const columnIndex = layer.get(node.id) || 0;
    if (!columns.has(columnIndex)) columns.set(columnIndex, []);
    columns.get(columnIndex).push(node);
  }
  const maxLayer = Math.max(0, ...columns.keys());
  const widths = new Map();
  for (let index = 0; index <= maxLayer; index += 1) widths.set(index, Math.max(1, ...(columns.get(index) || []).map((node) => node.width)));
  const positions = new Map();
  let x = marginX;
  for (let index = 0; index <= maxLayer; index += 1) {
    let y = marginY;
    const column = (columns.get(index) || []).sort((a, b) => a.id.localeCompare(b.id));
    for (const node of column) {
      positions.set(node.id, { id: node.id, x: Math.round(x), y: Math.round(y) });
      y += node.height + verticalGap;
    }
    x += widths.get(index) + horizontalGap;
  }
  return values.slice().sort((a, b) => a.id.localeCompare(b.id)).map((node) => positions.get(node.id)).filter(Boolean);
}

export const deterministicNodeLayout = computeDeterministicLayout;

export function minimapFingerprint({ nodes = [], edges = [], scale = 1, offset = [0, 0], selectedIds = [], viewport = [0, 0] } = {}) {
  const rawNodes = Array.isArray(nodes) ? nodes : [];
  const values = rawNodes.map(nodeGeometry).sort((a, b) => a.id.localeCompare(b.id));
  const byGraphId = new Map();
  rawNodes.forEach((node) => {
    const ids = [node?.hubId, node?.id].filter((value) => value !== undefined && value !== null).map(String);
    ids.forEach((id) => byGraphId.set(id, node));
  });
  const links = (Array.isArray(edges) ? edges : []).map((edge) => {
    const source = String(edge?.source?.node ?? edge?.origin_id ?? "");
    const target = String(edge?.target?.node ?? edge?.target_id ?? "");
    const sourceNode = byGraphId.get(source);
    const type = edge?.type || edge?.data?.type || sourceNode?.outputs?.[edge?.origin_slot]?.type || sourceNode?.type || "";
    return { id: String(edge?.id ?? `${source}:${edge?.origin_slot ?? edge?.source?.port ?? ""}->${target}:${edge?.target_slot ?? edge?.target?.port ?? ""}`), source, target, type: String(type) };
  }).sort((a, b) => a.id.localeCompare(b.id));
  const bounds = graphBounds(values);
  return JSON.stringify({
    scale: Number(finiteOr(scale, 1).toFixed(6)),
    offset: [Number(finiteOr(offset?.[0], 0).toFixed(3)), Number(finiteOr(offset?.[1], 0).toFixed(3))],
    viewport: [finiteOr(viewport?.[0], 0), finiteOr(viewport?.[1], 0)],
    bounds,
    nodes: values,
    edges: links,
    selected: (Array.isArray(selectedIds) ? selectedIds : [...(selectedIds instanceof Set ? selectedIds : [])]).map(String).sort(),
  });
}

export function createMinimapScheduler({ requestFrame, cancelFrame, fingerprint = () => "", redraw = () => {} } = {}) {
  const request = requestFrame || ((callback) => setTimeout(callback, 16));
  const cancel = cancelFrame || ((handle) => clearTimeout(handle));
  let active = false;
  let handle = null;
  let lastFingerprint = "";
  const tick = () => {
    handle = null;
    if (!active) return;
    const next = String(fingerprint() || "");
    if (next && next !== lastFingerprint) {
      lastFingerprint = next;
      redraw(next);
    }
    handle = request(tick);
  };
  return {
    start() {
      if (active) return;
      active = true;
      handle = request(tick);
    },
    stop() {
      active = false;
      if (handle !== null) cancel(handle);
      handle = null;
    },
    invalidate() { lastFingerprint = ""; },
    get active() { return active; },
    get lastFingerprint() { return lastFingerprint; },
  };
}

export function contextMenuPosition(event, containerRect = {}, menuSize = {}, padding = 8) {
  const rect = containerRect || {};
  const width = Math.max(1, finiteOr(rect.width, 0));
  const height = Math.max(1, finiteOr(rect.height, 0));
  const menuWidth = Math.max(1, finiteOr(menuSize.width, 300));
  const menuHeight = Math.max(1, finiteOr(menuSize.height, 360));
  const inset = Math.max(0, finiteOr(padding, 8));
  const clamp = (value, minimum, maximum) => Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
  return {
    left: clamp(finiteOr(event?.clientX, rect.left) - finiteOr(rect.left, 0), inset, width - menuWidth - inset),
    top: clamp(finiteOr(event?.clientY, rect.top) - finiteOr(rect.top, 0), inset, height - menuHeight - inset),
  };
}

const preferredCategoriesForScope = Object.freeze({
  image: ["image", "utility", "vision", "annotation"],
  sam2: ["vision", "image", "utility", "annotation"],
  media: ["media", "video", "utility", "annotation"],
  video: ["video", "media", "utility", "annotation"],
  animesr: ["video", "media", "utility", "annotation"],
});

export function getCanvasContextMenuCandidates(definitions, { scope = "", query = "", showAll = false, limit = CONTEXT_MENU_LIMIT } = {}) {
  const values = definitions instanceof Map ? [...definitions.values()] : Array.isArray(definitions) ? definitions : [];
  const needle = String(query || "").trim().toLocaleLowerCase();
  const categories = preferredCategoriesForScope[scope] || [];
  const ranked = values.filter((definition) => {
    if (!definition || typeof definition.type !== "string") return false;
    const haystack = `${definition.title || ""} ${definition.type} ${definition.category || ""} ${definition.description || ""}`.toLocaleLowerCase();
    return !needle || haystack.includes(needle);
  }).map((definition) => {
    const categoryRank = categories.indexOf(definition.category);
    const operational = String(definition.status || definition.availability?.status || "") === "operational";
    return { definition, recommended: categoryRank >= 0 && categoryRank < 2, _rank: (categoryRank < 0 ? 100 : categoryRank) * 10 + (operational ? 0 : 1) };
  }).sort((a, b) => a._rank - b._rank || String(a.definition.title).localeCompare(String(b.definition.title)) || a.definition.type.localeCompare(b.definition.type));
  const selected = showAll ? ranked : ranked.slice(0, Math.max(1, Math.min(120, Number(limit) || CONTEXT_MENU_LIMIT)));
  return selected.map(({ definition, recommended }) => ({ definition, recommended }));
}

function outputStatusValue(value) {
  const status = typeof value === "string" ? value : value && typeof value === "object" ? value.status ?? value.state : "";
  const normalized = String(status || "").toLocaleLowerCase();
  if (["running", "queued", "pending", "starting", "processing"].includes(normalized)) return "running";
  if (["completed", "complete", "success", "ready"].includes(normalized)) return "completed";
  if (["failed", "error", "cancelled", "canceled"].includes(normalized)) return "error";
  return "none";
}

function hasOpaqueArtifactReference(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return OPAQUE_ARTIFACT_ID.test(String(value.artifact_id || "")) || OPAQUE_ARTIFACT_ID.test(String(value.artifactId || ""));
}

export function outputSocketState(nodeState = {}, portName = "") {
  const sources = [nodeState?.outputs, nodeState?.output_states, nodeState?.outputStates];
  for (const source of sources) {
    if (!source || typeof source !== "object") continue;
    const raw = Array.isArray(source) ? source.find((item) => item?.name === portName || item?.port === portName) : source[portName];
    if (raw !== undefined) {
      const explicit = outputStatusValue(raw);
      if (explicit !== "none") return explicit;
      if (hasOpaqueArtifactReference(raw) || collectArtifactProjections(raw).items.length) return "completed";
    }
  }
  const run = outputStatusValue(nodeState?.status);
  return run === "running" || run === "error" ? run : "none";
}

export function outputSocketStateLabel(state) {
  const normalized = Object.prototype.hasOwnProperty.call(OUTPUT_STATE_LABEL_KEYS, state) ? state : "none";
  return nodeText(OUTPUT_STATE_LABEL_KEYS[normalized]);
}

function artifactPreviewState(value) {
  const status = typeof value === "string" ? value.toLocaleLowerCase() : "";
  if (status === "error" || status === "cancelled") return "failed";
  return Object.prototype.hasOwnProperty.call(ARTIFACT_PREVIEW_STATE_COPY, status) ? status : "not_run";
}

function artifactPreviewButton(artifact) {
  const metadata = JSON.stringify({ media_type: artifact.mediaType, ...(artifact.sizeBytes === null ? {} : { size_bytes: artifact.sizeBytes }) });
  return `<button class="button button--compact" type="button" data-preview-artifact="${escapeHtml(artifact.id)}" data-artifact-url="${escapeHtml(artifact.url)}" data-artifact-name="${escapeHtml(artifact.name)}" data-artifact-type="${escapeHtml(artifact.mediaType)}" data-artifact-mask="${String(artifact.mask)}" data-artifact-meta="${escapeHtml(metadata)}">${escapeHtml(nodeText("Open preview"))}</button>`;
}

function artifactPreviewItem(artifact) {
  const label = artifact.mask ? `${nodeText("Mask")} · ${artifact.mediaType}` : artifact.mediaType;
  const media = artifact.kind === "image"
    ? `<img class="graph-preview-image${artifact.mask ? " graph-preview-image--mask" : ""}" src="${escapeHtml(artifact.url)}" alt="${escapeHtml(artifact.name)}" loading="lazy" />`
    : artifact.kind === "video"
      ? `<video class="graph-preview-media" controls preload="metadata" src="${escapeHtml(artifact.url)}">Video preview unavailable in this browser.</video>`
      : artifact.kind === "audio"
        ? `<audio class="graph-preview-media" controls preload="metadata" src="${escapeHtml(artifact.url)}">Audio preview unavailable in this browser.</audio>`
        : `<div class="graph-preview-fallback"><strong>${escapeHtml(nodeText("No native preview"))}</strong><p>${escapeHtml(nodeText("Escaped metadata only; this artifact type is not rendered as media."))}</p></div>`;
  return `<article class="graph-preview-card" data-artifact-id="${escapeHtml(artifact.id)}"><div class="graph-preview-card__head"><strong>${escapeHtml(artifact.name)}</strong><span class="tag">${escapeHtml(label)}</span></div>${media}<dl class="graph-preview-meta"><div><dt>${escapeHtml(nodeText("Artifact"))}</dt><dd>${escapeHtml(artifact.id)}</dd></div><div><dt>${escapeHtml(nodeText("Type"))}</dt><dd>${escapeHtml(label)}</dd></div><div><dt>${escapeHtml(nodeText("Size"))}</dt><dd>${artifact.sizeBytes === null ? escapeHtml(nodeText("unavailable")) : `${escapeHtml(String(artifact.sizeBytes))} bytes`}</dd></div></dl><div class="graph-preview-card__actions">${artifactPreviewButton(artifact)}</div></article>`;
}

export function renderArtifactPreviewMarkup(collection, state = {}) {
  const collected = collection && Array.isArray(collection.items)
    ? collection
    : collectArtifactProjections(collection);
  const items = Array.isArray(collected.items) ? collected.items : [];
  const previewState = artifactPreviewState(state?.status);
  const [title, message] = ARTIFACT_PREVIEW_STATE_COPY[previewState].map(nodeText);
  const summary = `<div class="graph-preview-summary" data-artifact-state="${escapeHtml(previewState)}"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(message)}</span></div>`;
  if (!items.length) return `${summary}<p class="graph-empty graph-preview-empty">${escapeHtml(nodeText("Preview unavailable in this node snapshot; no safe artifact was published."))}</p>`;
  const truncation = collected.truncated ? `<p class="graph-preview-truncated">Showing the first ${MAX_PREVIEW_ARTIFACTS} safe artifacts; additional output metadata is unavailable.</p>` : "";
  return `${summary}<div class="graph-preview-list" aria-label="Node output artifacts">${items.map(artifactPreviewItem).join("")}</div>${truncation}`;
}

function graphFingerprint(graph) {
  return JSON.stringify(graph);
}

const graphNodeMap = (graph) => new Map(
  (Array.isArray(graph?.nodes) ? graph.nodes : [])
    .filter((node) => node && typeof node === "object" && typeof node.id === "string")
    .map((node) => [node.id, node]),
);

const graphEdgeKey = (edge) => {
  if (!edge || typeof edge !== "object") return "";
  const source = edge.source && typeof edge.source === "object" ? edge.source : {};
  const target = edge.target && typeof edge.target === "object" ? edge.target : {};
  return `${String(edge.id || "")}|${String(source.node || "")}:${String(source.port || "")}->${String(target.node || "")}:${String(target.port || "")}`;
};

/** Return only current node IDs changed by a graph edit, including topology endpoints. */
export function changedGraphNodeIds(before, after) {
  const previousNodes = graphNodeMap(before);
  const nextNodes = graphNodeMap(after);
  const changed = new Set();
  const nodeIds = new Set([...previousNodes.keys(), ...nextNodes.keys()]);
  for (const id of nodeIds) {
    const previous = previousNodes.get(id);
    const next = nextNodes.get(id);
    if (!previous || !next || JSON.stringify(previous) !== JSON.stringify(next)) changed.add(id);
  }
  const previousEdges = new Map((Array.isArray(before?.edges) ? before.edges : []).map((edge) => [graphEdgeKey(edge), edge]));
  const nextEdges = new Map((Array.isArray(after?.edges) ? after.edges : []).map((edge) => [graphEdgeKey(edge), edge]));
  const edgeKeys = new Set([...previousEdges.keys(), ...nextEdges.keys()]);
  for (const key of edgeKeys) {
    if (previousEdges.has(key) && nextEdges.has(key)) continue;
    const edge = nextEdges.get(key) || previousEdges.get(key);
    const source = edge?.source?.node;
    const target = edge?.target?.node;
    if (typeof source === "string") changed.add(source);
    if (typeof target === "string") changed.add(target);
  }
  return [...changed].filter((id) => nextNodes.has(id)).sort((left, right) => left.localeCompare(right));
}

/** Compute changed nodes plus only their reachable downstream dependants. */
export function downstreamDirtyNodeIds(graph, changedIds = []) {
  const nodes = graphNodeMap(graph);
  const dirty = new Set((Array.isArray(changedIds) ? changedIds : []).filter((id) => typeof id === "string" && nodes.has(id)));
  const adjacency = new Map([...nodes.keys()].map((id) => [id, new Set()]));
  for (const edge of Array.isArray(graph?.edges) ? graph.edges : []) {
    const source = edge?.source?.node;
    const target = edge?.target?.node;
    if (adjacency.has(source) && adjacency.has(target)) adjacency.get(source).add(target);
  }
  const queue = [...dirty].sort((left, right) => left.localeCompare(right));
  while (queue.length) {
    const current = queue.shift();
    for (const child of [...(adjacency.get(current) || [])].sort((left, right) => left.localeCompare(right))) {
      if (dirty.has(child)) continue;
      dirty.add(child);
      queue.push(child);
    }
  }
  return [...dirty].sort((left, right) => left.localeCompare(right));
}

const safePersistedJobId = (value) => {
  const candidate = typeof value === "string" ? value.trim() : "";
  return SAFE_JOB_ID.test(candidate) ? candidate : null;
};

const safePersistedStatus = (value) => RUN_STATUS_VALUES.includes(String(value || "")) ? String(value) : "not_run";

const safePersistedArtifact = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const id = typeof value.id === "string" ? value.id : typeof value.artifact_id === "string" ? value.artifact_id : "";
  if (!OPAQUE_ARTIFACT_ID.test(id)) return null;
  const name = typeof value.name === "string" && !UNSAFE_ARTIFACT_TEXT.test(value.name)
    ? value.name.replace(/[\\/\r\n]+/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX_ARTIFACT_NAME_LENGTH) || "Artifact"
    : "Artifact";
  const mediaType = typeof value.media_type === "string" && SAFE_MEDIA_TYPE.test(value.media_type.split(";", 1)[0].trim().toLocaleLowerCase())
    ? value.media_type.split(";", 1)[0].trim().toLocaleLowerCase()
    : "application/octet-stream";
  const size = value.size_bytes;
  return {
    id,
    name,
    media_type: mediaType,
    size_bytes: size === null || size === undefined ? null : Number.isSafeInteger(size) && size >= 0 ? size : null,
    mask: value.mask === true || value.is_mask === true || value.artifact_kind === "mask",
  };
};

/** Persist only a bounded opaque run reference and server-shaped projections. */
export function buildPersistedRunProjection(run = {}, nodeIds = []) {
  const allowedNodeIds = new Set((Array.isArray(nodeIds) ? nodeIds : []).filter((id) => typeof id === "string" && SAFE_GRAPH_NODE_ID.test(id)));
  const rawNodes = Array.isArray(run?.nodes) ? run.nodes : [];
  const nodes = rawNodes.map((state) => {
    if (!state || typeof state !== "object" || typeof state.id !== "string" || !SAFE_GRAPH_NODE_ID.test(state.id) || (allowedNodeIds.size && !allowedNodeIds.has(state.id))) return null;
    const progress = Number(state.progress);
    return {
      id: state.id,
      status: safePersistedStatus(state.status),
      progress: Number.isFinite(progress) ? Math.max(0, Math.min(100, Math.round(progress))) : 0,
      cache_hit: typeof state.cache_hit === "boolean" ? state.cache_hit : null,
    };
  }).filter(Boolean).slice(0, MAX_PERSISTED_NODE_STATES);
  const artifacts = [];
  const seen = new Set();
  for (const value of Array.isArray(run?.provenance) ? run.provenance : []) {
    const artifact = safePersistedArtifact(value);
    if (!artifact || seen.has(artifact.id)) continue;
    seen.add(artifact.id);
    artifacts.push(artifact);
    if (artifacts.length >= MAX_PERSISTED_ARTIFACTS) break;
  }
  return {
    version: PERSISTED_RUN_STATE_VERSION,
    job_id: safePersistedJobId(run?.job_id || run?.id),
    status: safePersistedStatus(run?.status),
    nodes,
    artifacts,
  };
}

export function normalizePersistedRunProjection(value, nodeIds = []) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.version !== PERSISTED_RUN_STATE_VERSION) return null;
  const jobId = value.job_id === null ? null : safePersistedJobId(value.job_id);
  if (value.job_id !== null && !jobId) return null;
  const normalized = buildPersistedRunProjection({ job_id: jobId, status: value.status, nodes: value.nodes, provenance: value.artifacts }, nodeIds);
  if (normalized.job_id !== jobId || !Array.isArray(value.nodes) || !Array.isArray(value.artifacts)) return null;
  return normalized;
}

export const persistedRunProjection = buildPersistedRunProjection;

export function propertyControl(node, property) {
  const control = inlineControlMetadata(property);
  const value = node.properties?.[property.name] ?? property.default ?? "";
  const target = `${node.id}:${property.name}`;
  if (!control) {
    return `<div class="graph-property graph-property--unavailable" data-control-state="unavailable"><span>${escapeHtml(nodeText(property.label || property.name))}</span><small>${escapeHtml(nodeText("Control unavailable; server metadata is not recognized."))}</small></div>`;
  }
  const label = escapeHtml(nodeText(control.label || property.name));
  const unit = control.unit ? ` <small class="graph-property__unit">${escapeHtml(control.unit)}</small>` : "";
  const maxLength = control.maxLength === undefined || !Number.isFinite(Number(control.maxLength)) ? "" : ` maxlength="${escapeHtml(control.maxLength)}"`;
  const min = control.minimum === undefined || control.minimum === null ? "" : ` min="${escapeHtml(control.minimum)}"`;
  const max = control.maximum === undefined || control.maximum === null ? "" : ` max="${escapeHtml(control.maximum)}"`;
  const step = control.step === undefined || control.step === null ? "" : ` step="${escapeHtml(control.step)}"`;
  if (control.control === "artifact") {
    return `<div class="graph-property graph-property--file"><span>${label}</span><div class="file-picker" data-file-picker><small data-graph-asset-value>${escapeHtml(value || "Chưa có artifact")}</small><input class="file-picker__input" type="file" data-graph-asset="${escapeHtml(target)}" tabindex="-1" aria-hidden="true" accept="${escapeHtml(property.accept || "")}" /><button class="button button--compact" type="button" data-file-picker-button>Chọn tệp</button></div></div>`;
  }
  if (control.control === "toggle") {
    return `<label class="graph-property graph-property--toggle"><input type="checkbox" role="switch" aria-label="${label}" data-graph-property="${escapeHtml(target)}" ${value ? "checked" : ""} /><span>${label} · <b>${value ? escapeHtml(nodeText("Bật")) : escapeHtml(nodeText("Tắt"))}</b></span></label>`;
  }
  if (control.control === "select") {
    return `<label class="graph-property"><span>${label}${unit}</span><select data-graph-property="${escapeHtml(target)}">${control.options.map((option) => `<option value="${escapeHtml(option)}" ${String(option) === String(value) ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}</select></label>`;
  }
  if (control.control === "textarea" || control.control === "prompt") {
    return `<label class="graph-property"><span>${label}${unit}</span><textarea data-graph-property="${escapeHtml(target)}"${maxLength}${control.placeholder ? ` placeholder="${escapeHtml(control.placeholder)}"` : ""}>${escapeHtml(value)}</textarea></label>`;
  }
  if (control.control === "slider") {
    if (control.minimum === undefined || control.maximum === undefined || !Number.isFinite(Number(control.minimum)) || !Number.isFinite(Number(control.maximum))) {
      return `<div class="graph-property graph-property--unavailable" data-control-state="unavailable"><span>${label}</span><small>${escapeHtml(nodeText("Control unavailable; server metadata is not recognized."))}</small></div>`;
    }
    const numberStep = step || " step=\"any\"";
    return `<div class="graph-property graph-property--slider" data-graph-property-group="${escapeHtml(target)}"><span>${label}${unit}</span><div class="graph-property__slider-row"><input type="range" data-graph-property="${escapeHtml(target)}" data-graph-property-role="range" value="${escapeHtml(value)}"${min}${max}${numberStep} aria-label="${label}" /><input type="number" data-graph-property="${escapeHtml(target)}" data-graph-property-role="number" value="${escapeHtml(value)}"${min}${max}${numberStep} aria-label="${label} numeric value" /><output data-graph-property-value>${escapeHtml(value)}</output></div></div>`;
  }
  if (control.control === "color") {
    return `<label class="graph-property graph-property--color"><span>${label}${unit}</span><span class="graph-property__color-row"><input type="color" data-graph-property="${escapeHtml(target)}" value="${escapeHtml(value || "#4d7dff")}" /><code>${escapeHtml(value || "#4d7dff")}</code></span></label>`;
  }
  const type = ["integer", "number", "size"].includes(control.control) ? "number" : "text";
  const numberStep = control.control === "integer" && !step ? " step=\"1\"" : step;
  return `<label class="graph-property"><span>${label}${unit}</span><input type="${type}" data-graph-property="${escapeHtml(target)}" value="${escapeHtml(value)}"${min}${max}${numberStep}${maxLength}${control.placeholder ? ` placeholder="${escapeHtml(control.placeholder)}"` : ""} /></label>`;
}

export function orderedPropertyGroups(properties = []) {
  const groups = new Map();
  (Array.isArray(properties) ? properties : []).forEach((property, index) => {
    const control = inlineControlMetadata(property);
    const name = control?.group || "General";
    const advanced = control?.advanced === true;
    const key = `${name}\u0000${advanced ? "advanced" : "basic"}`;
    if (!groups.has(key)) groups.set(key, { name, advanced, firstIndex: index, properties: [] });
    groups.get(key).properties.push({ property, index, order: control?.order ?? 0 });
  });
  return [...groups.values()]
    .sort((first, second) => first.firstIndex - second.firstIndex)
    .map((group) => ({
      name: group.name,
      advanced: group.advanced,
      properties: group.properties
        .sort((first, second) => Number(first.order) - Number(second.order) || first.index - second.index)
        .map((item) => item.property),
    }));
}

function renderPropertyGroups(node, properties) {
  const groups = orderedPropertyGroups(properties);
  return groups.map((group) => {
    const controls = group.properties.map((property) => propertyControl(node, property)).join("");
    if (group.advanced) {
      return `<details class="graph-property-group graph-property-group--advanced"><summary>${escapeHtml(nodeText(group.name))} · Advanced</summary><div class="graph-property-group__body">${controls}</div></details>`;
    }
    return `<section class="graph-property-group" data-graph-property-group-name="${escapeHtml(group.name)}"><strong>${escapeHtml(nodeText(group.name))}</strong><div class="graph-property-group__body">${controls}</div></section>`;
  }).join("");
}

function inlineWidgetRawValue(node, property, widget) {
  const drafts = node?._hubInlineDrafts;
  if (drafts && Object.prototype.hasOwnProperty.call(drafts, property.name)) return drafts[property.name];
  return widget?.value ?? node?.properties?.[property.name] ?? property.default ?? "";
}

function inlineWidgetHeight(node, property, control, widget) {
  if (!control.multiline) return NODE_INLINE_MIN_HEIGHT;
  const value = String(inlineWidgetRawValue(node, property, widget) ?? "");
  const lines = value.split(/\r?\n/).reduce((total, line) => total + Math.max(1, Math.ceil(line.length / 34)), 0);
  return Math.min(NODE_INLINE_MULTILINE_MAX_HEIGHT, NODE_INLINE_MIN_HEIGHT + Math.max(1, Math.min(4, lines)) * 14);
}

function createInlineWidget(node, property, control) {
  const widget = {
    type: "hub-inline",
    name: property.name,
    label: control.label,
    value: node.properties?.[property.name] ?? property.default ?? "",
    options: { property: property.name },
    hubControl: control.control,
    hubProperty: property.name,
    computeSize: (width) => [Math.max(NODE_MIN_WIDTH, finiteOr(width, NODE_MIN_WIDTH)), inlineWidgetHeight(node, property, control, widget)],
    draw(ctx, _owner, width, y, height) {
      const value = String(inlineWidgetRawValue(node, property, widget) ?? "");
      const singleLine = value.replace(/\s+/g, " ").trim();
      const display = (singleLine || "—").slice(0, 48) + (singleLine.length > 48 ? "…" : "");
      const widgetHeight = Math.max(Number(height) || 0, inlineWidgetHeight(node, property, control, widget));
      ctx.save();
      ctx.fillStyle = "#18223b";
      ctx.strokeStyle = "#55698f";
      ctx.beginPath();
      ctx.roundRect(14, y, width - 28, widgetHeight, [5]);
      ctx.fill();
      ctx.stroke();
      ctx.font = "11px sans-serif";
      ctx.fillStyle = "#aebddd";
      ctx.textAlign = "left";
      ctx.fillText(control.label || property.name, 22, y + 16);
      ctx.fillStyle = "#edf4ff";
      ctx.textAlign = "right";
      ctx.fillText(display, width - 22, y + 16);
      if (control.multiline) {
        ctx.fillStyle = "#80aaff";
        ctx.font = "9px sans-serif";
        ctx.fillText("Ctrl+Enter", width - 22, y + widgetHeight - 5);
      }
      ctx.restore();
    },
    mouse(event) {
      const editor = node._hubEditor;
      if (!editor || !event || !["mousedown", "pointerdown"].includes(event.type)) return Boolean(event?.type);
      editor.captureWidgetBeforeChange(node, property.name);
      editor.openInlineEditor(node, property, widget, event);
      return true;
    },
  };
  return widget;
}

function outputStateAreaHeight(node) {
  const count = Array.isArray(node?.outputs) ? node.outputs.length : 0;
  return count ? NODE_OUTPUT_STATE_HEADER_HEIGHT + count * NODE_OUTPUT_STATE_ROW_HEIGHT + 6 : 0;
}

function createSliderWidget(node, property, control) {
  const minimum = Number(control.minimum);
  const maximum = Number(control.maximum);
  const step = Number(control.step) > 0 ? Number(control.step) : (control.control === "integer" ? 1 : 0.01);
  const decimals = Math.min(6, Math.max(0, String(step).split(".")[1]?.length || 0));
  const snap = (raw) => {
    const value = Math.max(minimum, Math.min(maximum, Number(raw)));
    const snapped = minimum + Math.round((value - minimum) / step) * step;
    return Number(Math.max(minimum, Math.min(maximum, snapped)).toFixed(decimals));
  };
  const widget = {
    type: "hub-slider",
    name: property.name,
    value: snap(node.properties?.[property.name] ?? property.default ?? minimum),
    options: { property: property.name, min: minimum, max: maximum, step },
    _dragging: false,
    computeSize: () => [NODE_MIN_WIDTH, 42],
    draw(ctx, _owner, width, y, height) {
      const value = snap(this.value);
      const widgetHeight = Math.max(Number(height) || 0, 42);
      const ratio = maximum === minimum ? 0 : (value - minimum) / (maximum - minimum);
      const left = 16;
      const numberWidth = 66;
      const right = Math.max(left + 18, width - numberWidth - 18);
      const trackY = y + widgetHeight * 0.63;
      ctx.save();
      ctx.font = "11px sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
      ctx.fillStyle = "#dce7ff";
      ctx.fillText(control.label || property.name, left, y + 13);
      ctx.strokeStyle = "#55698f";
      ctx.lineWidth = 4;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(left, trackY);
      ctx.lineTo(right, trackY);
      ctx.stroke();
      ctx.strokeStyle = "#78a8ff";
      ctx.beginPath();
      ctx.moveTo(left, trackY);
      ctx.lineTo(left + (right - left) * ratio, trackY);
      ctx.stroke();
      ctx.fillStyle = "#edf4ff";
      ctx.beginPath();
      ctx.arc(left + (right - left) * ratio, trackY, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#18223b";
      ctx.strokeStyle = "#8aa8d9";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(width - numberWidth - 8, y + 4, numberWidth, widgetHeight - 8, 4);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#edf4ff";
      ctx.textAlign = "center";
      ctx.fillText(String(value), width - numberWidth / 2 - 8, y + widgetHeight * 0.68);
      ctx.restore();
    },
    mouse(event, position) {
      const editor = node._hubEditor;
      if (!editor || !["mousedown", "pointerdown", "mousemove", "pointermove", "mouseup", "pointerup"].includes(event?.type)) return false;
      const width = Number(node.size?.[0] || NODE_MIN_WIDTH);
      const numberStart = width - 82;
      const x = Number(position?.[0] || 0);
      const updateFromPointer = () => {
        const left = 16;
        const right = Math.max(left + 18, width - 84);
        const ratio = Math.max(0, Math.min(1, (x - left) / Math.max(1, right - left)));
        this.value = snap(minimum + (maximum - minimum) * ratio);
        editor.stageWidgetValue(node, property, this.value);
      };
      if (event.type.endsWith("down")) {
        editor.captureWidgetBeforeChange(node, property.name);
        if (x >= numberStart) {
          this._dragging = false;
          editor.openInlineEditor(node, property, this, event);
        } else {
          this._dragging = true;
          updateFromPointer();
        }
        return true;
      }
      if (event.type.endsWith("move") && this._dragging) {
        updateFromPointer();
        return true;
      }
      if (event.type.endsWith("up") && this._dragging) {
        updateFromPointer();
        this._dragging = false;
        editor.commitWidgetGesture(node, property.name);
        return true;
      }
      return Boolean(this._dragging);
    },
  };
  return widget;
}

function createColorWidget(node, property, control) {
  const widget = {
    type: "hub-color",
    name: property.name,
    label: control.label,
    value: node.properties?.[property.name] ?? property.default ?? "#4d7dff",
    options: { property: property.name },
    computeSize: () => [NODE_MIN_WIDTH, 26],
    draw(ctx, _owner, width, y, height) {
      const value = typeof this.value === "string" && /^#[0-9a-f]{6,8}$/i.test(this.value) ? this.value : "#4d7dff";
      const widgetHeight = Math.max(Number(height) || 0, 26);
      ctx.save();
      ctx.fillStyle = "#29344d";
      ctx.strokeStyle = "#7082a8";
      ctx.beginPath();
      ctx.roundRect(15, y, width - 30, widgetHeight, [widgetHeight * 0.4]);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = value;
      ctx.fillRect(21, y + 4, widgetHeight - 8, widgetHeight - 8);
      ctx.fillStyle = "#e9efff";
      ctx.textAlign = "left";
      ctx.fillText(this.label || this.name, 45, y + widgetHeight * 0.7);
      ctx.textAlign = "right";
      ctx.fillText(value, width - 22, y + widgetHeight * 0.7);
      ctx.restore();
    },
    mouse(event) {
      const editor = node._hubEditor;
      if (!editor || !editor.liteCanvas || !["mousedown", "pointerdown"].includes(event?.type)) return false;
      editor.captureWidgetBeforeChange(node, property.name);
      editor.openInlineEditor(node, property, this, event);
      return true;
    },
  };
  return widget;
}

class HubGraphEditor {
  constructor(root, { showToast, recipeApplication = null, initialPresetId = null, onRecipeApplied = () => {}, onPresetApplied = () => {}, workflowLibrary = null }) {
    this.root = root;
    this.scope = root.dataset.scope || "image";
    this.showToast = showToast;
    this.destroyed = false;
    this.recipeApplication = recipeApplication;
    this.initialPresetId = initialPresetId;
    this.onRecipeApplied = onRecipeApplied;
    this.onPresetApplied = onPresetApplied;
    this.workflowLibrary = workflowLibrary;
    this.workflowLibraryState = { status: "partial", reason: "Workflow Library server-owned adapter chưa khả dụng.", action: "Tiếp tục local draft; kiểm tra endpoint typed trước khi đồng bộ." };
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
    this.runProvenance = [];
    this.activeJobId = null;
    this.autoPreview = localStorage.getItem(`${keyFor(this.scope)}:auto`) === "true";
    this.draft = localStorage.getItem(`${keyFor(this.scope)}:draft`) !== "false";
    this.search = "";
    this.hydrating = false;
    this.beforeChange = null;
    this.widgetBeforeChange = null;
    this.propertyGesture = null;
    this.pollTimer = null;
    this.pollGeneration = 0;
    this.pollInFlight = false;
    this.autoTimer = null;
    this.resizeObserver = null;
    this.abort = new AbortController();
    this.minimapBounds = null;
    this.minimapFrame = null;
    this.minimapLoopActive = false;
    this.minimapDirty = false;
    this.minimapScheduler = null;
    this.minimapLastFingerprint = "";
    this.minimapDragging = null;
    this.contextMenu = null;
    this._contextMenuOutsideHandler = null;
    this.validation = null;
    this.runtimePreflightResult = null;
    this.runStatus = "idle";
    this.savedFingerprint = "";
    this.unsaved = false;
    this.recovered = false;
    this.autosavedAt = null;
    this.persistedRun = null;
    this.lastRunSnapshotFingerprint = "";
    this.panelState = this.readPanelState();
    this.connectionPicker = null;
    this.pendingConnection = null;
    this.connectionNotice = "";
    this.inlineEditor = null;
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
      this.presets = (presetPayload.presets || []).filter((item) => item.scope === this.scope || (this.scope === "video" && item.scope === "media"));
      this.workflowIndex = readWorkflowIndex(this.scope);
      this.configureLiteGraph();
      const savedState = this.readLocalState();
      if (this.initialPresetId) {
        await this.loadPreset(this.initialPresetId, { quiet: true, render: false });
        this.recovered = false;
        this.onPresetApplied(this.initialPresetId);
      } else if (savedState) {
        this.graphData = savedState.graph;
        this.persistedRun = savedState.run;
        this.recovered = true;
      }
      else await this.loadPreset(PRESET_BY_SCOPE[this.scope], { quiet: true, render: false });
      await this.refreshWorkflowLibrary();
      if (this.recipeApplication && this.scope === "image") {
        this.graphData = applyRecipeToGraph(this.graphData, this.recipeApplication);
        this.recovered = false;
        this.unsaved = true;
        this.persist({ source: "recipe" });
        this.onRecipeApplied(this.recipeApplication);
      }
      this.savedFingerprint = this.savedFingerprint || graphFingerprint(this.graphData);
      // Loading the server-owned default preset is a baseline, not a user
      // edit.  Only a recovered draft or an explicit recipe/template action
      // should surface "Có thay đổi chưa lưu" on first open.
      this.unsaved = Boolean(this.unsaved);
      this.dirty = this.unsaved ? new Set(this.graphData.nodes.map((node) => node.id)) : new Set();
      this.renderShell();
      this.hydrateLiteGraph(this.graphData);
      this.renderWorkflowStatus();
      if (this.recovered) this.showToast("Đã khôi phục bản autosave local của workflow.");
      if (this.persistedRun) await this.restorePersistedRun(this.persistedRun);
    } catch (error) {
      this.root.innerHTML = `<div class="callout callout--warning">Không thể nạp graph editor: ${escapeHtml(error.message)}</div>`;
    }
  }

  drawGraphGrid(ctx, area = this.liteCanvas?.ds?.visible_area) {
    if (!ctx || !area) return;
    const gridSize = 24;
    const majorEvery = 5;
    const scale = Math.max(0.1, Number(this.liteCanvas?.ds?.scale || 1));
    const left = Number(area[0] || 0);
    const top = Number(area[1] || 0);
    const right = left + Number(area[2] || 0);
    const bottom = top + Number(area[3] || 0);
    const firstX = Math.floor(left / gridSize) * gridSize;
    const firstY = Math.floor(top / gridSize) * gridSize;
    ctx.save();
    ctx.lineWidth = 1 / scale;
    ctx.beginPath();
    for (let x = firstX, column = Math.floor(firstX / gridSize); x <= right; x += gridSize, column += 1) {
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
    }
    for (let y = firstY, row = Math.floor(firstY / gridSize); y <= bottom; y += gridSize, row += 1) {
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
    }
    ctx.strokeStyle = "rgba(167,190,244,.12)";
    ctx.stroke();
    ctx.beginPath();
    for (let x = Math.floor(firstX / (gridSize * majorEvery)) * gridSize * majorEvery; x <= right; x += gridSize * majorEvery) {
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
    }
    for (let y = Math.floor(firstY / (gridSize * majorEvery)) * gridSize * majorEvery; y <= bottom; y += gridSize * majorEvery) {
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
    }
    ctx.strokeStyle = "rgba(167,190,244,.22)";
    ctx.stroke();
    ctx.restore();
  }

  currentMinimapFingerprint() {
    if (!this.liteGraph || !this.liteCanvas) return "";
    const nodes = this.liteGraph._nodes || [];
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const links = Object.values(this.liteGraph.links || {}).map((link) => {
      const source = nodeById.get(link.origin_id);
      return {
        id: link.id,
        origin_id: link.origin_id,
        target_id: link.target_id,
        origin_slot: link.origin_slot,
        target_slot: link.target_slot,
        type: source?.outputs?.[link.origin_slot]?.type || "",
      };
    });
    const selectedIds = Object.values(this.liteCanvas.selected_nodes || {}).map((node) => node.hubId || node.id);
    return minimapFingerprint({
      nodes,
      edges: links,
      scale: this.liteCanvas.ds.scale,
      offset: this.liteCanvas.ds.offset,
      selectedIds,
      viewport: [this.canvasElement?.width || 0, this.canvasElement?.height || 0],
    });
  }

  scheduleMinimapUpdate() {
    this.minimapDirty = true;
    this.minimapScheduler?.invalidate();
  }

  startMinimapLoop() {
    if (this.minimapScheduler?.active) return;
    const view = this.canvasElement?.ownerDocument?.defaultView || globalThis;
    this._requestMinimapFrame = view.requestAnimationFrame?.bind(view) || globalThis.requestAnimationFrame?.bind(globalThis) || ((callback) => setTimeout(callback, 16));
    this._cancelMinimapFrame = view.cancelAnimationFrame?.bind(view) || globalThis.cancelAnimationFrame?.bind(globalThis) || ((handle) => clearTimeout(handle));
    this.minimapScheduler = createMinimapScheduler({
      requestFrame: (callback) => this._requestMinimapFrame(callback),
      cancelFrame: (handle) => this._cancelMinimapFrame(handle),
      fingerprint: () => {
        if (!this.minimapDirty || !this.root?.isConnected) return "";
        return this.currentMinimapFingerprint();
      },
      redraw: (fingerprint) => {
        if (!this.root?.isConnected) return;
        this.minimapLastFingerprint = fingerprint;
        this.minimapDirty = false;
        this.drawMinimap();
      },
    });
    this.minimapLoopActive = true;
    this.minimapScheduler.start();
  }

  stopMinimapLoop() {
    this.minimapLoopActive = false;
    this.minimapScheduler?.stop();
    this.minimapScheduler = null;
    if (this.minimapFrame !== null && this._cancelMinimapFrame) this._cancelMinimapFrame(this.minimapFrame);
    this.minimapFrame = null;
    this._requestMinimapFrame = null;
    this._cancelMinimapFrame = null;
  }

  canvasGraphPosition(event) {
    if (Number.isFinite(Number(event?.canvasX)) && Number.isFinite(Number(event?.canvasY))) return { x: Number(event.canvasX), y: Number(event.canvasY) };
    const position = this.liteCanvas?.convertEventToCanvasOffset?.(event || {}) || [0, 0];
    return { x: finiteOr(position[0], 0), y: finiteOr(position[1], 0) };
  }

  contextMenuShellPosition(event, menu) {
    const shell = this.root.querySelector(".graph-canvas-shell");
    if (!shell) return { left: 8, top: 8 };
    const rect = shell.getBoundingClientRect();
    const menuRect = menu?.getBoundingClientRect?.() || {};
    return contextMenuPosition(event, rect, { width: menuRect.width || 300, height: menuRect.height || 360 }, 8);
  }

  openContextMenu(node, event) {
    if (!this.liteCanvas || !this.root.isConnected) return;
    this.closeConnectionPicker(false);
    this.closeContextMenu(false);
    if (node) this.openNodeContextMenu(node, event);
    else this.openCanvasContextMenu(event);
  }

  openCanvasContextMenu(event = {}) {
    const shell = this.root.querySelector(".graph-canvas-shell");
    if (!shell) return;
    const menu = document.createElement("div");
    menu.className = "graph-context-menu graph-context-menu--canvas";
    menu.dataset.graphContextMenu = "canvas";
    menu.setAttribute("data-graph-context-menu", "canvas");
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", nodeText("Add node"));
    menu.innerHTML = `<div class="graph-context-menu__head"><strong>${escapeHtml(nodeText("Add node"))}</strong><button type="button" class="button button--compact" data-graph-context-close aria-label="${escapeHtml(nodeText("Close add node menu"))}">Esc</button></div><input type="search" data-graph-context-search aria-label="${escapeHtml(nodeText("Search nodes"))}" placeholder="${escapeHtml(nodeText("Search nodes…"))}" /><div data-graph-context-results role="group"></div><button type="button" class="graph-context-menu__all" data-graph-context-all>${escapeHtml(nodeText("Show all nodes"))}</button>`;
    shell.appendChild(menu);
    const position = this.canvasGraphPosition(event);
    this.contextMenu = { kind: "canvas", element: menu, position, query: "", showAll: false };
    const place = this.contextMenuShellPosition(event, menu);
    menu.style.left = `${place.left}px`;
    menu.style.top = `${place.top}px`;
    const search = menu.querySelector("[data-graph-context-search]");
    search?.addEventListener("input", () => {
      this.contextMenu.query = search.value.slice(0, 120);
      this.renderContextMenu();
    });
    menu.addEventListener("click", (clickEvent) => {
      if (clickEvent.target.closest("[data-graph-context-close]")) {
        this.closeContextMenu();
        return;
      }
      if (clickEvent.target.closest("[data-graph-context-all]")) {
        this.contextMenu.showAll = !this.contextMenu.showAll;
        this.renderContextMenu();
        return;
      }
      const add = clickEvent.target.closest("[data-graph-context-add]");
      if (add) this.selectContextMenuNode(add.dataset.graphContextAdd);
      const action = clickEvent.target.closest("[data-node-context-action]");
      if (action) this.handleNodeContextAction(action.dataset.nodeContextAction);
    });
    this._contextMenuOutsideHandler = (clickEvent) => {
      if (this.contextMenu && !this.contextMenu.element.contains(clickEvent.target)) this.closeContextMenu();
    };
    document.addEventListener("pointerdown", this._contextMenuOutsideHandler, true);
    this.renderContextMenu();
    setTimeout(() => search?.focus(), 0);
  }

  openNodeContextMenu(node, event = {}) {
    const shell = this.root.querySelector(".graph-canvas-shell");
    if (!shell) return;
    const menu = document.createElement("div");
    menu.className = "graph-context-menu graph-context-menu--node";
    menu.dataset.graphContextMenu = "node";
    menu.setAttribute("data-graph-context-menu", "node");
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", nodeText("Node actions"));
    const definition = this.registry.get(node.hubType);
    menu.innerHTML = `<div class="graph-context-menu__head"><strong>${escapeHtml(nodeText(definition?.title || node.hubType))}</strong><button type="button" class="button button--compact" data-graph-context-close aria-label="${escapeHtml(nodeText("Close node menu"))}">Esc</button></div><button type="button" role="menuitem" data-node-context-action="center">${escapeHtml(nodeText("Center node"))}</button><button type="button" role="menuitem" data-node-context-action="duplicate">${escapeHtml(nodeText("Duplicate node"))}</button><button type="button" role="menuitem" data-node-context-action="delete">${escapeHtml(nodeText("Delete node"))}</button>`;
    shell.appendChild(menu);
    this.contextMenu = { kind: "node", element: menu, node, position: this.canvasGraphPosition(event) };
    const place = this.contextMenuShellPosition(event, menu);
    menu.style.left = `${place.left}px`;
    menu.style.top = `${place.top}px`;
    menu.addEventListener("click", (clickEvent) => {
      if (clickEvent.target.closest("[data-graph-context-close]")) this.closeContextMenu();
      const action = clickEvent.target.closest("[data-node-context-action]");
      if (action) this.handleNodeContextAction(action.dataset.nodeContextAction);
    });
    this._contextMenuOutsideHandler = (clickEvent) => {
      if (this.contextMenu && !this.contextMenu.element.contains(clickEvent.target)) this.closeContextMenu();
    };
    document.addEventListener("pointerdown", this._contextMenuOutsideHandler, true);
    setTimeout(() => menu.querySelector("[data-node-context-action]")?.focus(), 0);
  }

  renderContextMenu() {
    const state = this.contextMenu;
    if (!state?.element) return;
    if (state.kind === "node") return;
    const query = state.query || "";
    const candidates = getCanvasContextMenuCandidates(this.registry, { scope: this.scope, query, showAll: state.showAll });
    const groups = new Map();
    candidates.forEach((item) => {
      const category = item.definition.category || "other";
      if (!groups.has(category)) groups.set(category, []);
      groups.get(category).push(item);
    });
    const results = state.element.querySelector("[data-graph-context-results]");
    if (results) results.innerHTML = [...groups.entries()].map(([category, items]) => `<section class="graph-context-menu__group"><h3>${escapeHtml(nodeText(category))}</h3>${items.map((item) => `<button type="button" role="menuitem" data-graph-context-add="${escapeHtml(item.definition.type)}" title="${escapeHtml(item.definition.description || "")}"><span>${escapeHtml(nodeText(item.definition.title))}</span><small>${item.recommended ? escapeHtml(nodeText("Recommended · ")) : ""}${escapeHtml(nodeText(item.definition.status || item.definition.availability?.status || "partial"))}</small></button>`).join("")}</section>`).join("") || `<p class="graph-empty">Không tìm thấy node.</p>`;
    const all = state.element.querySelector("[data-graph-context-all]");
    if (all) all.textContent = nodeText(state.showAll ? "Show recommended nodes" : "Show all nodes");
  }

  selectContextMenuNode(type) {
    const state = this.contextMenu;
    if (!state || state.kind !== "canvas" || !this.registry.has(type)) return;
    const position = { ...state.position };
    this.closeContextMenu(false);
    this.addNode(type, { position, center: false });
  }

  handleNodeContextAction(action) {
    const state = this.contextMenu;
    const node = state?.kind === "node" ? state.node : null;
    if (!node) return;
    if (action === "delete") {
      this.closeContextMenu(false);
      this.liteCanvas.selectNode(node);
      this.deleteSelected();
    } else if (action === "duplicate") {
      this.closeContextMenu(false);
      this.duplicateNode(node);
    } else if (action === "center") {
      this.liteCanvas.centerOnNode(node);
      this.scheduleMinimapUpdate();
      this.closeContextMenu();
    } else if (action === "close") {
      this.closeContextMenu();
    }
  }

  handleContextMenuKey(event) {
    const menu = this.contextMenu?.element;
    if (!menu || !menu.contains(event.target)) return false;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      this.closeContextMenu();
      return true;
    }
    const items = [...menu.querySelectorAll("[data-graph-context-add], [data-node-context-action]")];
    const current = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const next = items[(Math.max(0, current) + direction + items.length) % items.length];
      next?.focus();
      return true;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const target = document.activeElement?.closest?.("[data-graph-context-add], [data-node-context-action]") || items[0];
      if (target?.dataset.graphContextAdd) this.selectContextMenuNode(target.dataset.graphContextAdd);
      else if (target?.dataset.nodeContextAction) this.handleNodeContextAction(target.dataset.nodeContextAction);
      return true;
    }
    return false;
  }

  closeContextMenu(restoreFocus = true) {
    if (this._contextMenuOutsideHandler) {
      document.removeEventListener("pointerdown", this._contextMenuOutsideHandler, true);
      this._contextMenuOutsideHandler = null;
    }
    this.contextMenu?.element?.remove();
    this.contextMenu = null;
    if (restoreFocus) this.canvasElement?.focus();
  }

  inlineEditorRawValue(state) {
    if (!state) return "";
    if (state.control.control === "toggle") return Boolean(state.input?.checked);
    if (state.control.control === "color") return String(state.exactInput?.value || "").trim();
    return state.input?.value ?? "";
  }

  positionInlineEditor() {
    const state = this.inlineEditor;
    const shell = this.root.querySelector(".graph-canvas-shell");
    if (!state?.panel || !shell || !this.canvasElement || !state.node) return;
    const canvasRect = this.canvasElement.getBoundingClientRect();
    const shellRect = shell.getBoundingClientRect();
    const scale = Math.max(0.01, Number(this.liteCanvas?.ds?.scale || 1));
    const offset = this.liteCanvas?.ds?.offset || [0, 0];
    const widgetY = Number(state.widget?.last_y ?? state.node.widgets_start_y ?? 42);
    const anchorX = canvasRect.left - shellRect.left + Number(offset[0] || 0) + (Number(state.node.pos?.[0] || 0) + 8) * scale;
    const anchorY = canvasRect.top - shellRect.top + Number(offset[1] || 0) + (Number(state.node.pos?.[1] || 0) + widgetY) * scale;
    const maxWidth = Math.max(220, Math.min(360, shell.clientWidth - 16));
    state.panel.style.width = `${maxWidth}px`;
    const panelWidth = state.panel.offsetWidth || maxWidth;
    const panelHeight = state.panel.offsetHeight || 160;
    const maxLeft = Math.max(8, shell.clientWidth - panelWidth - 8);
    const maxTop = Math.max(8, shell.clientHeight - panelHeight - 8);
    state.panel.style.left = `${Math.max(8, Math.min(maxLeft, anchorX))}px`;
    state.panel.style.top = `${Math.max(8, Math.min(maxTop, anchorY + 8))}px`;
  }

  previewInlineEditor() {
    const state = this.inlineEditor;
    if (!state) return false;
    const rawValue = this.inlineEditorRawValue(state);
    state.node._hubInlineDrafts ||= Object.create(null);
    state.node._hubInlineDrafts[state.property.name] = rawValue;
    const normalized = normalizeInlineControlValue(state.property, rawValue);
    if (normalized.accepted) {
      state.error.hidden = true;
      state.error.textContent = "";
      if (state.widget && !state.control.multiline) state.widget.value = normalized.value;
    } else {
      state.error.hidden = false;
      state.error.textContent = `Không thể cập nhật ${state.control.label || state.property.name}: ${normalized.reason}.`;
    }
    const minimum = state.node.computeSize?.() || [NODE_MIN_WIDTH, NODE_MIN_HEIGHT];
    const current = normalizeNodeSize(state.node.size, { width: NODE_MIN_WIDTH, height: NODE_MIN_HEIGHT });
    const previousSizing = state.node._hubInlineSizing === true;
    state.node._hubInlineSizing = true;
    try {
      state.node.setSize?.([current.width, Math.max(current.height, Number(minimum[1]) || NODE_MIN_HEIGHT)]);
    } finally {
      state.node._hubInlineSizing = previousSizing;
    }
    state.node.setDirtyCanvas?.(true, true);
    this.positionInlineEditor();
    return normalized.accepted;
  }

  commitInlineEditor() {
    const state = this.inlineEditor;
    if (!state) return true;
    const rawValue = this.inlineEditorRawValue(state);
    const normalized = normalizeInlineControlValue(state.property, rawValue);
    if (!normalized.accepted) {
      state.error.hidden = false;
      state.error.textContent = `Không thể cập nhật ${state.control.label || state.property.name}: ${normalized.reason}.`;
      state.input?.focus();
      return false;
    }
    delete state.node._hubInlineDrafts?.[state.property.name];
    const changed = this.stageWidgetValue(state.node, state.property, normalized.value);
    this.widgetBeforeChange = null;
    state.committed = true;
    this.closeInlineEditor(false);
    if (changed) this.commitWidgetChange(state.before);
    else this.renderInspector();
    this.scheduleMinimapUpdate();
    return true;
  }

  closeInlineEditor(restore = true) {
    const state = this.inlineEditor;
    if (!state) return true;
    if (state.outsideHandler) window.removeEventListener("pointerdown", state.outsideHandler, true);
    if (state.resizeHandler) window.removeEventListener("resize", state.resizeHandler, true);
    state.panel?.remove();
    if (restore && !state.committed) {
      delete state.node._hubInlineDrafts?.[state.property.name];
      if (state.beforeSize) state.node.setSize?.(state.beforeSize.slice());
      state.node._hubSizeChanged = state.beforeSizeChanged;
      if (state.widget) state.widget.value = state.node.properties?.[state.property.name] ?? state.property.default ?? "";
      state.node.setDirtyCanvas?.(true, true);
    }
    this.inlineEditor = null;
    this.widgetBeforeChange = null;
    this.renderInspector();
    this.scheduleMinimapUpdate();
    return true;
  }

  openInlineEditor(node, property, widget = null, _event = null) {
    const control = inlineControlMetadata(property);
    if (this.destroyed || !node || !control) return false;
    if (this.inlineEditor && !this.commitInlineEditor()) return false;
    const panel = document.createElement("div");
    panel.className = "graph-inline-editor";
    panel.setAttribute("data-graph-inline-editor", "true");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", control.label || property.name);
    panel.style.maxHeight = `${NODE_INLINE_EDITOR_MAX_HEIGHT}px`;
    const label = document.createElement("label");
    label.className = "graph-inline-editor__label";
    label.textContent = control.label || property.name;
    const field = document.createElement("div");
    field.className = "graph-inline-editor__field";
    const initial = node.properties?.[property.name] ?? property.default ?? "";
    let input = null;
    let exactInput = null;
    if (control.control === "toggle") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = initial === true;
      input.setAttribute("role", "switch");
      field.append(input);
    } else if (control.control === "select") {
      input = document.createElement("select");
      control.options.forEach((option) => {
        const item = document.createElement("option");
        item.value = String(option);
        item.textContent = String(option);
        item.selected = String(option) === String(initial);
        input.append(item);
      });
      field.append(input);
    } else if (control.control === "color") {
      const row = document.createElement("div");
      row.className = "graph-inline-editor__color-row";
      input = document.createElement("input");
      input.type = "color";
      input.value = /^#[0-9a-f]{6}$/i.test(String(initial)) ? String(initial) : "#4d7dff";
      exactInput = document.createElement("input");
      exactInput.type = "text";
      exactInput.value = String(initial || "#4d7dff");
      exactInput.maxLength = 9;
      row.append(input, exactInput);
      field.append(row);
    } else if (control.multiline) {
      input = document.createElement("textarea");
      input.rows = 4;
      input.value = String(initial);
      input.maxLength = Number.isFinite(Number(control.maxLength)) ? Number(control.maxLength) : 20000;
      if (control.placeholder) input.placeholder = control.placeholder;
      field.append(input);
    } else {
      input = document.createElement("input");
      input.type = ["integer", "number", "size"].includes(control.control) ? "number" : "text";
      input.value = String(initial);
      if (control.minimum !== undefined && control.minimum !== null) input.min = String(control.minimum);
      if (control.maximum !== undefined && control.maximum !== null) input.max = String(control.maximum);
      if (control.step !== undefined && control.step !== null) input.step = String(control.step);
      if (Number.isFinite(Number(control.maxLength))) input.maxLength = Number(control.maxLength);
      if (control.placeholder) input.placeholder = control.placeholder;
      field.append(input);
    }
    const error = document.createElement("div");
    error.className = "graph-inline-editor__error";
    error.setAttribute("role", "alert");
    error.hidden = true;
    const actions = document.createElement("div");
    actions.className = "graph-inline-editor__actions";
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "button button--compact button--primary";
    apply.textContent = control.multiline ? "Áp dụng (Ctrl+Enter)" : "Áp dụng";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "button button--compact";
    cancel.textContent = "Hủy";
    actions.append(apply, cancel);
    panel.append(label, field, error, actions);
    const shell = this.root.querySelector(".graph-canvas-shell");
    if (!shell) return false;
    shell.append(panel);
    const state = {
      node,
      property,
      control,
      widget,
      panel,
      input,
      exactInput,
      error,
      before: graphFingerprint(this.toHubGraph()),
      beforeSize: Array.isArray(node.size) || ArrayBuffer.isView(node.size) ? [...node.size] : null,
      beforeSizeChanged: node._hubSizeChanged === true,
      committed: false,
    };
    this.inlineEditor = state;
    node._hubInlineDrafts ||= Object.create(null);
    node._hubInlineDrafts[property.name] = String(initial);
    const preview = () => this.previewInlineEditor();
    input?.addEventListener("input", preview);
    input?.addEventListener("change", preview);
    if (input && exactInput) input.addEventListener("input", () => { exactInput.value = input.value; preview(); });
    exactInput?.addEventListener("input", () => {
      if (input) input.value = exactInput.value;
      preview();
    });
    apply.addEventListener("click", () => this.commitInlineEditor());
    cancel.addEventListener("click", () => this.closeInlineEditor(true));
    const handleEditorKeydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        this.closeInlineEditor(true);
      } else if (event.key === "Enter" && ((!control.multiline && !event.shiftKey) || (control.multiline && (event.ctrlKey || event.metaKey)))) {
        event.preventDefault();
        event.stopPropagation();
        this.commitInlineEditor();
      }
    };
    input?.addEventListener("keydown", handleEditorKeydown);
    exactInput?.addEventListener("keydown", handleEditorKeydown);
    state.outsideHandler = (event) => {
      if (!this.inlineEditor || panel.contains(event.target)) return;
      const committed = this.commitInlineEditor();
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!committed) input?.focus();
    };
    state.resizeHandler = () => this.positionInlineEditor();
    window.addEventListener("pointerdown", state.outsideHandler, true);
    window.addEventListener("resize", state.resizeHandler, true);
    this.positionInlineEditor();
    input?.focus();
    if (input && typeof input.select === "function" && input.type !== "checkbox") input.select();
    return true;
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
    if (!isMediaScope(this.scope) || !MEDIA_OPERATION_SCOPE_IDS.includes(definition?.type)) return null;
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
    if (!isMediaScope(this.scope)) return evidence || definition?.availability || { status: definition?.status || "operational", reason: "", action: "" };
    if (evidence) return evidence;
    return {
      status: "partial",
      reason: nodeText("This node is outside the exact published media operation scope."),
      action: nodeText("Use only the three exactly evidenced media operations."),
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
    if (!isMediaScope(this.scope)) return nodeText("Media operation scope is not applied to this workspace; no execution is claimed.");
    if (this.operationScope.evidenceVerified) return nodeText("Exact media evidence is published for video grade, logo overlay and encode; opening Node Studio does not execute a worker.");
    return nodeText("No exact media evidence is verified in this server snapshot; scoped nodes remain partial and no execution is claimed.");
  }

  operationEvidenceMarkup(definition = null) {
    const evidence = this.operationEvidenceFor(definition);
    const title = evidence ? nodeText(`${evidence.label} evidence`) : nodeText("Media operation evidence");
    const generic = isMediaScope(this.scope) && !evidence;
    const status = evidence?.status || (generic ? "partial" : isMediaScope(this.scope) ? this.operationScope.status : "unavailable");
    const reason = evidence?.reason || (generic ? nodeText("This node is outside the exact published media operation scope.") : this.operationScopeSummary());
    const nextAction = evidence?.nextAction || (generic ? nodeText("Use only the three exactly evidenced media operations.") : safeOperationScopeText(this.operationScope.nextAction, MEDIA_OPERATION_SCOPE_FALLBACK.nextAction));
    const execution = evidence?.execution || (generic ? "not_run" : this.operationScope.execution);
    return `<section class="graph-operation-evidence" data-operation-scope-status="${escapeHtml(status)}" data-operation-scope-verified="${String(evidence?.evidenceVerified === true)}"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(nodeText(reason))}</span><span><b>${escapeHtml(nodeText("Next action"))}</b> ${escapeHtml(nodeText(nextAction))}</span><small>${escapeHtml(nodeText("Server snapshot"))} · ${escapeHtml(nodeText("execution:"))} ${escapeHtml(nodeText(execution))} · ${escapeHtml(nodeText("no UI execution"))}</small></section>`;
  }

  destroy() {
    this.closeInlineEditor(true);
    this.abort.abort();
    this.destroyed = true;
    this.closeConnectionPicker(false);
    this.closeContextMenu(false);
    this.stopMinimapDrag();
    this.stopMinimapLoop();
    this.stopPoll();
    if (this.autoTimer) clearTimeout(this.autoTimer);
    this.autoTimer = null;
    this.beforeChange = null;
    this.widgetBeforeChange = null;
    this.propertyGesture = null;
    this.resizeObserver?.disconnect();
    this.liteCanvas?.stopRendering?.();
    this.liteCanvas?.setCanvas?.(null);
    this.liteCanvas?.setGraph?.(null);
  }

  readLocalState() {
    for (const storageKey of [keyFor(this.scope), legacyKeyFor(this.scope)]) {
      try {
        const value = JSON.parse(localStorage.getItem(storageKey) || "null");
        if (value?.schema_version === 1 && Array.isArray(value.nodes) && Array.isArray(value.edges)) {
          const graph = { ...value };
          delete graph.node_studio_state;
          return {
            graph,
            run: normalizePersistedRunProjection(value.node_studio_state, graph.nodes.map((node) => node?.id).filter(Boolean)),
          };
        }
      } catch { /* ignore a malformed private WebView value */ }
    }
    return null;
  }

  readLocalGraph() {
    return this.readLocalState()?.graph || null;
  }

  resetRunState(status = "idle") {
    this.stopPoll();
    this.activeJobId = null;
    this.nodeStates.clear();
    this.liteGraph?._nodes?.forEach((node) => { node.hubStatus = "not_run"; });
    this.runProvenance = [];
    this.persistedRun = null;
    this.lastRunSnapshotFingerprint = "";
    this.runStatus = status;
  }

  stopPoll() {
    this.pollGeneration += 1;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = null;
    this.pollInFlight = false;
  }

  runSnapshotFingerprint(run) {
    return JSON.stringify({
      job_id: run?.job_id || run?.id || null,
      status: run?.status || "not_run",
      nodes: Array.isArray(run?.nodes) ? run.nodes : [],
      provenance: Array.isArray(run?.provenance) ? run.provenance : [],
    });
  }

  applyRunSnapshot(run, { generation = null, syncDraft = false } = {}) {
    if (this.destroyed || !run || typeof run !== "object") return false;
    if (generation !== null && generation !== this.pollGeneration) return false;
    const jobId = safePersistedJobId(run.job_id || run.id || this.activeJobId);
    if (!jobId || (this.activeJobId && jobId !== this.activeJobId)) return false;
    const snapshotFingerprint = this.runSnapshotFingerprint(run);
    const changed = snapshotFingerprint !== this.lastRunSnapshotFingerprint;
    this.lastRunSnapshotFingerprint = snapshotFingerprint;
    this.runStatus = safePersistedStatus(run.status);
    this.activeJobId = jobId;
    this.nodeStates.clear();
    for (const state of Array.isArray(run.nodes) ? run.nodes.slice(0, MAX_PERSISTED_NODE_STATES) : []) {
      if (!state || typeof state !== "object" || typeof state.id !== "string" || !SAFE_GRAPH_NODE_ID.test(state.id)) continue;
      this.nodeStates.set(state.id, state);
      const node = this.liteGraph?._nodes?.find((candidate) => candidate.hubId === state.id);
      if (node) node.hubStatus = state.status || "not_run";
    }
    this.runProvenance = Array.isArray(run.provenance) ? run.provenance.slice(0, MAX_PERSISTED_ARTIFACTS) : [];
    this.persistedRun = buildPersistedRunProjection(run, this.toHubGraph().nodes.map((node) => node.id));
    const terminal = !ACTIVE_RUN_STATUS_VALUES.includes(this.runStatus);
    if (terminal) {
      this.activeJobId = null;
      this.stopPoll();
      if (this.runStatus === "completed") this.dirty.clear();
    }
    if (changed) {
      this.persist({ source: "run", syncDraft });
      this.renderInspector();
      this.renderGraphStatus();
    }
    return true;
  }

  markRunUnavailable(jobId = null) {
    if (this.destroyed) return;
    this.stopPoll();
    this.activeJobId = null;
    this.runStatus = "unavailable";
    this.nodeStates = new Map(this.toHubGraph().nodes.map((node) => [node.id, {
      id: node.id,
      status: "unavailable",
      progress: 0,
      message: "Job snapshot không còn khả dụng; không khẳng định kết quả đã hoàn tất.",
    }]));
    this.liteGraph?._nodes?.forEach((node) => { node.hubStatus = "unavailable"; });
    this.runProvenance = [];
    this.persistedRun = buildPersistedRunProjection({ job_id: jobId, status: "unavailable", nodes: [...this.nodeStates.values()], provenance: [] }, this.toHubGraph().nodes.map((node) => node.id));
    this.persist({ source: "run-unavailable", syncDraft: false });
    this.renderInspector();
    this.renderGraphStatus();
  }

  async restorePersistedRun(value) {
    if (this.destroyed) return;
    const projection = normalizePersistedRunProjection(value, this.toHubGraph().nodes.map((node) => node.id));
    const jobId = projection?.job_id;
    if (!jobId) return;
    this.persistedRun = projection;
    this.activeJobId = jobId;
    this.runStatus = "starting";
    this.renderGraphStatus();
    try {
      const response = await getNodeRun(jobId);
      if (this.destroyed || this.activeJobId !== jobId) return;
      if (!response?.run || typeof response.run !== "object") throw new Error("NODE_RUN_SNAPSHOT_INVALID");
      this.applyRunSnapshot(response.run);
      if (this.activeJobId) this.startPoll();
    } catch (error) {
      if (this.destroyed || this.activeJobId !== jobId) return;
      if (error?.status === 404 || error?.payload?.error === "node_run_not_found") this.markRunUnavailable(jobId);
      else {
        // A transient read failure never promotes the stored terminal label to
        // a result. Keep the opaque reference and retry through the poller.
        this.runStatus = "starting";
        this.renderGraphStatus();
        this.startPoll();
      }
    }
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

  persist({ source = "autosave", saved = false, syncDraft = true } = {}) {
    const graph = this.toHubGraph();
    graph.id = safeWorkflowId(graph.id, `local-${this.scope}`);
    graph.title = String(graph.title || `Workflow ${this.scope}`).slice(0, 160);
    const run = buildPersistedRunProjection({
      job_id: this.activeJobId || this.persistedRun?.job_id,
      status: this.runStatus,
      nodes: [...this.nodeStates.values()],
      provenance: this.runProvenance,
    }, graph.nodes.map((node) => node.id));
    this.persistedRun = run;
    const persistedGraph = { ...graph, node_studio_state: run };
    this.graphData = { ...this.graphData, id: graph.id, title: graph.title };
    localStorage.setItem(keyFor(this.scope), JSON.stringify(persistedGraph));
    localStorage.setItem(`${keyFor(this.scope)}:auto`, String(this.autoPreview));
    localStorage.setItem(`${keyFor(this.scope)}:draft`, String(this.draft));
    this.autosavedAt = nowIso();
    this.rememberWorkflow(persistedGraph, { source, saved });
    if (saved) {
      this.savedFingerprint = graphFingerprint(graph);
      this.unsaved = false;
      if (syncDraft) clearNodeDraft(this.scope).catch(() => {});
    } else if (syncDraft) {
      saveNodeDraft(this.scope, persistedGraph).catch(() => {});
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
      : { status: "partial", reason: "Workflow Library server-owned adapter chưa khả dụng.", action: "Tiếp tục local draft; kiểm tra endpoint typed trước khi đồng bộ." };
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

  libraryOptions() {
    const values = Array.isArray(this.workflowLibraryState?.workflows) ? this.workflowLibraryState.workflows : [];
    return values.slice(0, 120).map((item) => {
      const id = typeof item?.id === "string" ? item.id : "";
      if (!id) return "";
      const favorite = item.favorite === true ? "★ " : "";
      const title = String(item.title || id).slice(0, 160);
      return `<option value="${escapeHtml(id)}">${escapeHtml(`${favorite}${title} · v${Number(item.revision) || 1}`)}</option>`;
    }).join("");
  }

  currentLibraryWorkflow() {
    const id = String(this.graphData?.id || "");
    const values = Array.isArray(this.workflowLibraryState?.workflows) ? this.workflowLibraryState.workflows : [];
    return values.find((item) => item?.id === id) || null;
  }

  refreshLibraryControls() {
    const select = this.root?.querySelector("[data-graph-library]");
    if (select) {
      const selected = String(this.graphData?.id || "");
      select.innerHTML = `<option value="">Mở workflow Library…</option>${this.libraryOptions()}`;
      select.value = selected;
    }
    const favorite = this.root?.querySelector("[data-graph-action='favorite-library']");
    if (favorite) {
      const current = this.currentLibraryWorkflow();
      favorite.disabled = !current;
      favorite.setAttribute("aria-disabled", String(!current));
      favorite.textContent = current?.favorite ? "Bỏ favorite" : "Favorite";
      favorite.title = current ? "Cập nhật favorite qua Workflow Library server-owned." : "Lưu workflow vào Library trước khi đặt favorite.";
    }
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
      libraryStatus.textContent = `${nodeText("Library")}: ${nodeText(this.workflowLibraryState.status || "partial")}`;
      libraryStatus.title = this.workflowLibraryState.reason || "";
    }
    this.refreshRecentControls();
    this.refreshLibraryControls();
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
      const storedValue = JSON.parse(stored);
      const storedGraph = { ...storedValue };
      delete storedGraph.node_studio_state;
      const result = await validateNodeGraph(storedGraph, false);
      if (!result.validation?.valid) throw new Error(result.validation?.errors?.[0]?.message || "Workflow local không còn hợp lệ.");
      this.history = [];
      this.future = [];
      this.resetRunState();
      this.hydrateLiteGraph(result.validation.graph);
      this.savedFingerprint = graphFingerprint(result.validation.graph);
      this.unsaved = false;
      this.dirty = new Set();
      this.recovered = true;
      this.persistedRun = normalizePersistedRunProjection(storedValue.node_studio_state, result.validation.graph.nodes.map((node) => node.id));
      this.persist({ source: "recent", saved: true });
      this.showToast("Đã mở workflow trong Recent.");
      if (this.persistedRun) await this.restorePersistedRun(this.persistedRun);
    } catch (error) { this.showToast(error.message, "error"); }
  }

  async loadLibraryWorkflow(id) {
    if (!id) return;
    try {
      const result = this.workflowLibrary?.get ? await this.workflowLibrary.get(id) : null;
      const workflow = result?.workflow;
      if (result?.status !== "ready" || !workflow?.graph) throw new Error(result?.action || result?.reason || "Không tìm thấy workflow trong Library.");
      const validation = await validateNodeGraph(workflow.graph, false);
      if (!validation.validation?.valid) throw new Error(validation.validation?.errors?.[0]?.message || "Workflow Library không còn hợp lệ.");
      this.history = [];
      this.future = [];
      this.resetRunState();
      this.hydrateLiteGraph(validation.validation.graph);
      this.savedFingerprint = graphFingerprint(validation.validation.graph);
      this.unsaved = false;
      this.dirty = new Set(validation.validation.graph.nodes.map((node) => node.id));
      this.recovered = false;
      this.persist({ source: "library", saved: true });
      // This is an explicit user-open action.  A Library GET itself remains
      // side-effect free, while recent metadata gets the same CAS treatment
      // as a favorite mutation.
      const opened = this.workflowLibrary?.markOpened
        ? await this.workflowLibrary.markOpened(id, this.workflowLibraryRevision)
        : null;
      if (Number.isInteger(opened?.library_revision)) this.workflowLibraryRevision = opened.library_revision;
      await this.refreshWorkflowLibrary();
      this.renderWorkflowStatus();
      this.showToast("Đã mở workflow từ Workflow Library. Chưa thực thi node nào.");
    } catch (error) {
      this.showToast(error.message || "Không thể mở workflow từ Library.", "error");
      this.refreshLibraryControls();
    }
  }

  async toggleLibraryFavorite() {
    const current = this.currentLibraryWorkflow();
    if (!current) {
      this.showToast("Lưu workflow vào Library trước khi đặt favorite.", "warning");
      return;
    }
    try {
      const result = this.workflowLibrary?.setFavorite
        ? await this.workflowLibrary.setFavorite(current.id, !current.favorite, this.workflowLibraryRevision)
        : null;
      if (!result?.accepted) throw new Error(result?.action || result?.reason || "Không thể cập nhật favorite Workflow Library.");
      this.workflowLibraryRevision = Number.isInteger(result.library_revision) ? result.library_revision : this.workflowLibraryRevision;
      await this.refreshWorkflowLibrary();
      this.renderWorkflowStatus();
      this.showToast(current.favorite ? "Đã bỏ favorite workflow." : "Đã đặt favorite workflow.");
    } catch (error) {
      this.showToast(error.message || "Không thể cập nhật favorite Workflow Library.", "error");
      this.refreshLibraryControls();
    }
  }

  duplicateWorkflow() {
    const copy = clone(this.toHubGraph());
    copy.id = `${safeWorkflowId(copy.id, `local-${this.scope}`)}-copy-${Date.now().toString(36)}`.slice(0, 80);
    copy.title = `${copy.title || "Workflow"} (bản sao)`;
    this.history = [];
    this.future = [];
    this.resetRunState();
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
        this.title = nodeText(captured.title);
        this.hubType = captured.type;
        this.properties = Object.fromEntries((captured.properties || []).map((property) => [property.name, clone(property.default)]));
        this._hubInlineControls = new Map();
        this._hubInlineDrafts = Object.create(null);
        this._hubSizePersisted = false;
        this._hubSizeChanged = false;
        this.onResize = (size) => {
          if (this._hubEditor && !this._hubEditor.hydrating && !this._hubInlineSizing) {
            this._hubSizeChanged = true;
            this._hubEditor.scheduleMinimapUpdate();
          }
          return size;
        };
        for (const port of captured.inputs || []) {
          this.addInput(nodeText(port.label || port.name), port.type, { hubPort: port.name, required: Boolean(port.required), multi: Boolean(port.multi) });
          const input = this.inputs[this.inputs.length - 1];
          input.hubPort = port.name;
          input.color = socketColor(port.type);
          input.color_on = socketColor(port.type);
          input.color_off = socketColor(port.type);
        }
        for (const port of captured.outputs || []) {
          this.addOutput(nodeText(port.label || port.name), port.type, { hubPort: port.name });
          const output = this.outputs[this.outputs.length - 1];
          output.hubPort = port.name;
          output.color = socketColor(port.type);
          output.color_on = socketColor(port.type);
          output.color_off = socketColor(port.type);
        }
        this.color = CATEGORY_COLORS[captured.category] || "#8794ad";
        this.bgcolor = "#172039";
        this.shape = "round";
        this.widgets_start_y = 42 + Math.max((captured.inputs || []).length, (captured.outputs || []).length) * 22;
        this.size = [NODE_MIN_WIDTH, Math.max(NODE_MIN_HEIGHT, this.widgets_start_y)];
        for (const property of captured.properties || []) {
          const control = inlineControlMetadata(property);
          if (!control) continue;
          if (control.control === "color") {
            const widget = createColorWidget(this, property, control);
            this.addCustomWidget(widget);
            this._hubInlineControls.set(property.name, widget);
            continue;
          }
          if (control.control === "slider") {
            if (control.minimum === undefined || control.maximum === undefined || !Number.isFinite(Number(control.minimum)) || !Number.isFinite(Number(control.maximum))) continue;
            const widget = createSliderWidget(this, property, control);
            this.addCustomWidget(widget);
            widget.hubControl = control.control;
            widget.hubProperty = property.name;
            this._hubInlineControls.set(property.name, widget);
            continue;
          }
          const widget = createInlineWidget(this, property, control);
          this.addCustomWidget(widget);
          this._hubInlineControls.set(property.name, widget);
        }
        this.size = this.computeSize();
      }
      const baseComputeSize = LiteGraph.LGraphNode.prototype.computeSize;
      HubLiteNode.prototype.computeSize = function computeHubNodeSize(out) {
        const size = baseComputeSize.call(this, out);
        size[0] = Math.max(NODE_MIN_WIDTH, Math.min(NODE_MAX_WIDTH, Number(size[0]) || NODE_MIN_WIDTH));
        size[1] = Math.min(NODE_MAX_HEIGHT, Math.max(NODE_MIN_HEIGHT, Number(size[1]) || NODE_MIN_HEIGHT) + outputStateAreaHeight(this));
        return size;
      };
      HubLiteNode.title = nodeText(captured.title);
      // LiteGraph defaults unselected titles to #999 even on bright category
      // bars.  Use one high-contrast ink color for every category; selected
      // nodes still use LiteGraph's existing white selected-title color.
      HubLiteNode.title_text_color = "#ffffff";
      HubLiteNode.desc = captured.description;
      HubLiteNode.prototype.onDrawForeground = function drawHubNodeForeground(ctx) {
        const editor = this._hubEditor;
        const state = editor?.nodeStates?.get(this.hubId) || { status: this.hubStatus || "not_run" };
        if (editor?.inlineEditor?.node === this) editor.positionInlineEditor();
        const stateHeight = outputStateAreaHeight(this);
        if (!stateHeight || !this.outputs?.length) return;
        ctx.save();
        const top = this.size[1] - stateHeight;
        ctx.fillStyle = "rgba(8, 13, 27, .32)";
        ctx.fillRect(0, top, this.size[0], stateHeight);
        ctx.strokeStyle = "rgba(154, 168, 199, .32)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, top + 0.5);
        ctx.lineTo(this.size[0], top + 0.5);
        ctx.stroke();
        ctx.font = "9px sans-serif";
        ctx.textAlign = "left";
        ctx.fillStyle = "#aebddd";
        ctx.fillText(nodeText("Output states"), 10, top + 14);
        for (const [index, port] of (captured.outputs || []).entries()) {
          const outputState = outputSocketState(state, port.name);
          const color = outputState === "completed" ? "#45d19a" : outputState === "running" ? "#80aaff" : outputState === "error" ? "#ef7885" : "#8794ad";
          const y = top + NODE_OUTPUT_STATE_HEADER_HEIGHT + index * NODE_OUTPUT_STATE_ROW_HEIGHT + 5;
          const outputLabel = nodeText(port.label || port.name);
          const stateLabel = outputSocketStateLabel(outputState);
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(12, y - 3, 3, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = "#c8d4f2";
          ctx.fillText(`${outputLabel} · ${stateLabel}`, 22, y);
        }
        ctx.restore();
      };
      HubLiteNode.prototype.onWidgetChanged = function captureHubWidgetChange(name) {
        // LiteGraph reports number/slider/toggle changes before its delayed
        // property callback runs.  Capture the old graph here; commit only
        // from the callback after the shared property has actually changed.
        this._hubEditor?.captureWidgetBeforeChange(this, name);
      };
      HubLiteNode.prototype.onMouseDown = function captureHubWidgetMouseDown(_event, _position, canvas) {
        const widget = canvas?.node_widget?.[1];
        const propertyName = widget?.options?.property || widget?.hubProperty;
        if (propertyName) this._hubEditor?.captureWidgetBeforeChange(this, propertyName);
      };
      HubLiteNode.prototype.onConnectInput = function guardOccupiedInput(slot) {
        const input = this.inputs?.[slot];
        if (input?.link != null && !input.multi) {
          this._hubEditor?.showConnectionNotice(nodeText("Input is already connected; disconnect it before adding another edge."));
          return false;
        }
        return true;
      };
      HubLiteNode.prototype.onConnectOutput = function guardOutputType(slot, type) {
        const output = this.outputs?.[slot];
        if (output && type && output.type !== type) {
          this._hubEditor?.showConnectionNotice(`${nodeText("Incompatible typed socket")}: ${output.type} -> ${type}.`);
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
        <div class="graph-editor__workflow-bar"><label class="graph-workflow-title"><span>Tên workflow</span><input data-graph-title aria-label="Tên workflow" value="${escapeHtml(this.graphData.title || "")}" /></label><label class="graph-workflow-recent"><span>Recent local</span><select data-graph-recent aria-label="Recent local workflows"><option value="">Chọn workflow local…</option>${this.recentOptions()}</select></label><label class="graph-workflow-recent"><span>Workflow Library</span><select data-graph-library aria-label="Workflow Library"><option value="">Mở workflow Library…</option>${this.libraryOptions()}</select></label><span class="graph-save-state" data-graph-save-state>Đã lưu local</span><button class="button button--compact" type="button" data-graph-action="favorite-library" disabled>Favorite</button><button class="button button--compact" type="button" data-graph-action="duplicate">Nhân bản</button></div>
        <div class="graph-editor__toolbar">
          <div class="graph-editor__toolbar-group"><button class="button button--primary" type="button" data-graph-action="run" aria-label="${escapeHtml(nodeText("Run Graph"))}"${this.runButtonAttributes()}>Chạy workflow</button><button class="button" type="button" data-graph-action="validate">Kiểm tra</button><button class="button" type="button" data-graph-action="runtime-preflight">Preflight V2</button><button class="button" type="button" data-graph-action="cancel" disabled>Hủy job</button><button class="button" type="button" data-graph-action="undo">Hoàn tác</button><button class="button" type="button" data-graph-action="redo">Làm lại</button></div>
          <div class="graph-editor__toolbar-group"><select data-graph-preset aria-label="Preset workflow"><option value="">Chọn template…</option>${this.presets.map((item) => `<option value="${escapeHtml(item.id)}" title="${escapeHtml(item.description || "")}">${escapeHtml(item.title)}${item.stage ? ` · ${escapeHtml(item.stage)}` : ""}</option>`).join("")}</select><button class="button" type="button" data-graph-action="save-local">Lưu local</button><button class="button" type="button" data-graph-action="export">Export JSON</button><span class="file-picker graph-editor__import" data-file-picker><input class="file-picker__input" type="file" data-graph-import tabindex="-1" aria-hidden="true" accept="application/json,.json" /><button class="button" type="button" data-file-picker-button>Nhập JSON</button></span></div>
        </div>
        <div class="graph-editor__options"><label><input type="checkbox" data-graph-option="auto" ${this.autoPreview ? "checked" : ""} /> Preview tự động (Auto Preview)</label><label><input type="checkbox" data-graph-option="draft" ${this.draft ? "checked" : ""} /> Draft ảnh</label><span>Bấm node để cộng dồn lựa chọn · Ctrl/Shift cũng cộng dồn · kéo nhóm để di chuyển · kéo vùng để chọn · bấm nền trống, Esc hoặc Xóa chọn để bỏ chọn</span></div>
        <div class="graph-editor__statusbar"><span data-graph-validation>Chưa kiểm tra workflow.</span><span data-graph-runtime-preflight>Chưa có preflight Workflow Runtime V2.</span><span class="graph-editor__availability">${this.availability.counts?.operational || 0} ${escapeHtml(nodeText("operational"))} · ${this.availability.counts?.partial || 0} ${escapeHtml(nodeText("partial"))} · ${this.availability.counts?.unavailable || 0} ${escapeHtml(nodeText("unavailable"))}</span><span class="graph-operation-scope-status" data-graph-operation-evidence role="status">${escapeHtml(this.operationScopeSummary())}</span></div>
        <div class="graph-editor__panel-controls" role="toolbar" aria-label="Node Studio panels">
          <button class="button button--compact" type="button" data-graph-action="toggle-palette" aria-expanded="${String(this.panelState.palette !== "collapsed")}">Palette</button>
          <button class="button button--compact" type="button" data-graph-action="toggle-inspector" aria-expanded="${String(this.panelState.inspector !== "collapsed")}">Inspector</button>
          <button class="button button--compact" type="button" data-graph-action="toggle-canvas-focus" aria-pressed="${String(this.panelState.canvasFocus)}">Canvas focus</button>
          <button class="button button--compact" type="button" data-graph-action="cycle-palette-width">Palette width</button>
          <button class="button button--compact" type="button" data-graph-action="cycle-inspector-width">Inspector width</button>
          <button class="button button--compact" type="button" data-graph-action="toggle-preview">Preview size</button>
          <button class="button button--compact" type="button" data-graph-action="toggle-guide" aria-expanded="${String(this.panelState.guide)}">${escapeHtml(nodeText("Node Guide"))}</button>
        </div>
        <details class="graph-guide" data-graph-guide ${this.panelState.guide ? "open" : ""}>
          <summary>${escapeHtml(nodeText("Node Guide · Hướng dẫn toàn diện Hub Nodes & Typed Workflow"))}</summary>
          <div class="graph-guide__content">
            <div class="graph-guide__section">
              <strong>1. Khái niệm Typed Sockets & Dữ liệu</strong>
              <p>Mỗi cổng (socket) trên node được quy định kiểu dữ liệu nghiêm ngặt: <code>IMAGE</code> (tím), <code>MASK</code> (xanh lục), <code>VIDEO</code> (hồng đỏ), <code>AUDIO</code> (cam), <code>TEXT</code> (lam), <code>NUMBER</code> (xanh nhạt), <code>BOOLEAN</code> (vàng), <code>MODEL</code> (hồng tím), <code>METADATA</code> (xám). Socket đầu vào không hỗ trợ đa kết nối (non-multi) sẽ từ chối kết nối thứ hai để tránh xung đột.</p>
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
                ${this.presets.filter((item) => item.scope === this.scope || (this.scope === "video" && item.scope === "media")).map((item) => `<button class="button button--compact" type="button" data-graph-guide-preset="${escapeHtml(item.id)}" title="${escapeHtml(item.description || "")}">Mở template: ${escapeHtml(item.title || item.id)}</button>`).join("") || "<span>Chưa có template mẫu cho workspace này.</span>"}
              </div>
            </div>
          </div>
        </details>
        <div class="graph-editor__layout">
          <aside class="graph-palette"><input type="search" data-graph-search placeholder="Tìm node…" aria-label="Tìm node" /><div data-graph-palette></div></aside>
           <div class="graph-canvas-shell"><canvas class="graph-canvas" data-graph-canvas></canvas><div class="graph-canvas__actions"><button type="button" data-graph-action="fit">Fit</button><button type="button" data-graph-action="auto-layout">Auto layout</button><button type="button" data-graph-action="clear-selection">Bỏ chọn</button><button type="button" data-graph-action="delete">Xóa chọn</button></div><canvas class="graph-minimap" data-graph-minimap width="180" height="118" aria-label="Minimap graph"></canvas></div>
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
    if (autoPreviewLabel?.lastChild) autoPreviewLabel.lastChild.textContent = ` ${nodeText("Preview indicator (manual; no auto-run)")}`;
    this.canvasElement = this.root.querySelector("[data-graph-canvas]");
    this.canvasElement.tabIndex = 0;
    this.canvasElement.setAttribute("role", "application");
    this.canvasElement.setAttribute("aria-label", nodeText("LiteGraph workflow canvas"));
    this.minimap = this.root.querySelector("[data-graph-minimap]");
    this.paletteElement = this.root.querySelector("[data-graph-palette]");
    this.inspectorElement = this.root.querySelector("[data-graph-inspector]");
    this.editorElement = this.root.querySelector(".graph-editor");
    this.applyPanelState();
    this.liteGraph = new globalThis.LiteGraph.LGraph();
    const pointereventsMethod = globalThis.LiteGraph.getPointerEventsMethod?.(this.canvasElement, "pointer") || "mouse";
    this.liteCanvas = new globalThis.LiteGraph.LGraphCanvas(this.canvasElement, this.liteGraph, { autoresize: false, pointerevents_method: pointereventsMethod });
    installLiteGraphSelectionCompatibility(this.liteCanvas);
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
    this.liteCanvas.render_canvas_border = false;
    this.liteCanvas.onDrawBackground = (ctx, area) => this.drawGraphGrid(ctx, area);
    this.liteCanvas.onBeforeChange = () => this.captureBeforeChange();
    this.liteCanvas.onAfterChange = () => { this.captureAfterChange(); this.scheduleMinimapUpdate(); };
    this.liteCanvas.onSelectionChange = () => { this.renderInspector(); this.scheduleMinimapUpdate(); };
    this.liteCanvas.onNodeMoved = () => this.scheduleMinimapUpdate();
    this.liteCanvas.onMouse = (event) => this.handleCanvasMouse(event);
    this.liteCanvas.onPointerCancel = () => this.handleCanvasPointerCancel();
    this.liteCanvas.onDrawForeground = () => {
      if (this.liteCanvas.node_dragged || this.liteCanvas.resizing_node || this.liteCanvas.dragging_canvas || this.liteCanvas.selected_group || this.liteCanvas.connecting_node) this.scheduleMinimapUpdate();
      if (this.inlineEditor) this.positionInlineEditor();
    };
    this.liteCanvas.ds.onredraw = () => this.scheduleMinimapUpdate();
    // LiteGraph's default processContextMenu appends a document-level menu.
    // Keep LiteGraph as the only editor while routing both targets through the
    // Hub-owned, container-bounded menu below.
    this.liteCanvas.processContextMenu = (node, event) => {
      event?.preventDefault?.();
      this.openContextMenu(node, event);
    };
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
    this.startMinimapLoop();
    localizeNodeStaticMarkup(this.root);
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
      if (event.target.matches("[data-graph-property][data-graph-property-role]")) this.stageProperty(event.target);
    }, { signal });
    this.root.addEventListener("change", (event) => {
      const option = event.target.dataset.graphOption;
      if (option === "auto") { this.autoPreview = event.target.checked; this.persist(); return; }
      if (option === "draft") { this.draft = event.target.checked; this.persist(); return; }
      if (event.target.matches("[data-graph-recent]")) { this.loadRecent(event.target.value); return; }
      if (event.target.matches("[data-graph-library]")) { this.loadLibraryWorkflow(event.target.value); return; }
      if (event.target.matches("[data-graph-preset]")) { this.loadPreset(event.target.value); return; }
      if (event.target.matches("[data-graph-property][data-graph-property-role]")) { this.stageProperty(event.target); this.commitProperty(event.target); return; }
      if (event.target.matches("[data-graph-property]")) { this.changeProperty(event.target); return; }
      if (event.target.matches("[data-graph-asset]")) { this.uploadAsset(event.target); return; }
      if (event.target.matches("[data-graph-import]")) { this.importGraph(event.target.files?.[0]); }
    }, { signal });
    this.minimap.addEventListener("pointerdown", (event) => this.startMinimapDrag(event), { signal });
    // LiteGraph owns a capture-phase canvas key handler.  Handle the Hub
    // shortcuts from document capture first, otherwise LiteGraph prevents the
    // Escape/Delete event before this adapter can make selection state and
    // persistence consistent.
    window.addEventListener("keydown", (event) => {
      if (!this.root.isConnected) return;
      if (this.contextMenu && this.handleContextMenuKey(event)) return;
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
    if (before !== after) this.commitGraphChange(before, next);
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

  handleCanvasPointerCancel() {
    this.closeInlineEditor(true);
    this.closeConnectionPicker(false);
    this.cancelNativeConnection();
    const before = this.beforeChange;
    this.beforeChange = null;
    if (!before || !this.liteGraph) return;
    try {
      const graph = JSON.parse(before);
      if (graph && typeof graph === "object" && Array.isArray(graph.nodes) && Array.isArray(graph.edges)) {
        this.hydrateLiteGraph(graph);
      }
    } catch {
      // A malformed pre-change snapshot must not echo or create a second edit.
    }
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
    const pointereventsMethod = canvas.pointerevents_method || "mouse";
    LiteGraph.pointerListenerRemove(canvas.canvas, "up", original, true, pointereventsMethod);
    LiteGraph.pointerListenerRemove(rootDocument, "up", original, true, pointereventsMethod);
    LiteGraph.pointerListenerAdd(canvas.canvas, "up", wrapper, true, pointereventsMethod);
    canvas._mouseup_callback = wrapper;
  }

  positionConnectionPicker(picker, pending) {
    const shell = this.root.querySelector(".graph-canvas-shell");
    if (!shell || !picker || !pending) return;
    const canvasRect = this.canvasElement?.getBoundingClientRect?.() || shell.getBoundingClientRect();
    const shellRect = shell.getBoundingClientRect();
    const scale = Math.max(0.01, Number(this.liteCanvas?.ds?.scale || 1));
    const offset = this.liteCanvas?.ds?.offset || [0, 0];
    const anchorX = canvasRect.left - shellRect.left + Number(offset[0] || 0) + Number(pending.position?.x || 0) * scale + 12;
    const anchorY = canvasRect.top - shellRect.top + Number(offset[1] || 0) + Number(pending.position?.y || 0) * scale + 12;
    const panelWidth = Math.min(420, Math.max(240, shell.clientWidth - 16));
    picker.style.width = `${panelWidth}px`;
    const measuredWidth = picker.offsetWidth || panelWidth;
    const measuredHeight = picker.offsetHeight || 240;
    const maxLeft = Math.max(8, shell.clientWidth - measuredWidth - 8);
    const maxTop = Math.max(8, shell.clientHeight - measuredHeight - 8);
    picker.style.left = `${Math.max(8, Math.min(maxLeft, anchorX))}px`;
    picker.style.top = `${Math.max(8, Math.min(maxTop, anchorY))}px`;
  }

  openConnectionPicker(pending) {
    this.closeConnectionPicker(false);
    this.pendingConnection = pending;
    const candidates = getConnectionPortCandidates(this.registry, { direction: pending.direction, type: pending.type });
    const automatic = chooseConnectionCandidate(candidates.compatible);
    if (automatic) {
      if (this.connectPickerCandidate(automatic)) this.showToast(`${nodeText("Auto-connected")} ${nodeText(automatic.definition.title)} · ${automatic.port.label || automatic.port.name}.`);
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
    picker.innerHTML = `<div class="graph-connection-picker__head"><strong id="graph-picker-title-${escapeHtml(this.scope)}">${escapeHtml(nodeText("Connect"))} ${escapeHtml(pending.type)} ${escapeHtml(nodeText("socket"))}</strong><button class="button button--compact" type="button" data-graph-picker-close aria-label="${escapeHtml(nodeText("Close connection picker"))}">Esc</button></div><input type="search" data-graph-picker-search aria-label="${escapeHtml(nodeText("Search compatible nodes"))}" placeholder="${escapeHtml(nodeText("Search compatible nodes"))}" value="${escapeHtml(this.panelState.pickerSearch)}" /><div data-graph-picker-results role="listbox" aria-label="${escapeHtml(nodeText("Compatible node ports"))}"></div><div data-graph-picker-rejected class="graph-empty" role="status"></div>`;
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
    this.positionConnectionPicker(picker, pending);
    const view = picker.ownerDocument?.defaultView || globalThis;
    view.requestAnimationFrame?.(() => this.positionConnectionPicker(picker, pending));
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
    const groups = new Map();
    matches.forEach((item) => {
      const category = item.candidate.definition.category || item.candidate.port.type || "other";
      if (!groups.has(category)) groups.set(category, []);
      groups.get(category).push(item);
    });
    const results = state.element.querySelector("[data-graph-picker-results]");
    if (results) {
      results.innerHTML = matches.length
        ? [...groups.entries()].map(([category, items]) => `<section class="graph-connection-picker__group"><h3>${escapeHtml(nodeText(category))}</h3>${items.map(({ candidate, index }) => `<button class="graph-connection-picker__candidate" type="button" role="option" aria-selected="false" data-graph-picker-candidate="${index}" title="${escapeHtml(candidate.definition.description || "")}"><span class="graph-connection-picker__candidate-main"><strong>${escapeHtml(candidate.definition.title)}</strong><span>${escapeHtml(candidate.port.label || candidate.port.name)} · ${escapeHtml(candidate.port.type || "")}</span><small>${escapeHtml(candidate.definition.description || "")}</small></span><span class="graph-connection-picker__candidate-status">${escapeHtml(nodeText(candidate.definition.availability?.status || candidate.definition.status || "operational"))}</span></button>`).join("")}</section>`).join("")
        : `<p class="graph-empty">${escapeHtml(nodeText("No compatible node port matches this search."))}</p>`;
    }
    const rejected = state.element.querySelector("[data-graph-picker-rejected]");
    if (rejected) {
      const reasons = state.candidates.rejected.slice(0, 12).map((item) => `<li><strong>${escapeHtml(item.definition.title)}</strong> · ${escapeHtml(item.port.label || item.port.name)}: ${escapeHtml(item.reason)}</li>`).join("");
      rejected.innerHTML = reasons
        ? `<details class="graph-connection-picker__rejected"><summary>${escapeHtml(nodeText("Rejected candidates"))}</summary><ul>${reasons}</ul></details>`
        : `<span>${escapeHtml(nodeText("Only explicitly compatible typed ports are shown."))}</span>`;
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
    const free = findFreeGridSlot(this.liteGraph._nodes, pending.position, node, { grid: NODE_GRID_SIZE, gap: NODE_LAYOUT_GAP_Y });
    node.pos = [free.x, free.y];
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
      this.showConnectionNotice(nodeText("Compatible socket could not be connected safely; no node was added."));
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
    const state = this.connectionPicker;
    if (!state) return false;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      this.closeConnectionPicker();
      return true;
    }
    const items = [...state.element.querySelectorAll("[data-graph-picker-candidate]")];
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Home" || event.key === "End") {
      if (!items.length) return true;
      event.preventDefault();
      event.stopImmediatePropagation();
      const current = items.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (Math.max(0, current) + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
      return true;
    }
    if (event.key === "Enter") {
      const target = document.activeElement?.closest?.("[data-graph-picker-candidate]") || items[0];
      if (!target) return true;
      event.preventDefault();
      event.stopImmediatePropagation();
      const index = Number(target.dataset.graphPickerCandidate);
      const candidate = state.candidates.compatible[index];
      if (candidate) this.connectPickerCandidate(candidate);
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
    this.scheduleMinimapUpdate();
  }

  renderPalette() {
    if (!this.paletteElement) return;
    const needle = this.search.trim().toLocaleLowerCase();
    const nodes = [...this.registry.values()].filter((definition) => !needle || `${definition.title} ${definition.category} ${definition.description}`.toLocaleLowerCase().includes(needle));
    const groups = nodes.reduce((result, definition) => {
      (result[definition.category] ||= []).push(definition);
      return result;
    }, {});
    this.paletteElement.innerHTML = Object.entries(groups).map(([category, definitions]) => `<section class="graph-palette__group"><h3>${escapeHtml(nodeText(category))}</h3>${definitions.map((definition) => {
      const evidence = this.operationEvidenceFor(definition);
      const availability = this.operationAvailabilityFor(definition);
      const scoped = Boolean(evidence);
      return `<button type="button" class="graph-palette__item" data-graph-add="${escapeHtml(definition.type)}" data-operation-status="${escapeHtml(availability.status || "partial")}"${scoped ? ` data-operation-scope="${escapeHtml(evidence.id)}"` : ""} title="${escapeHtml(availability.reason || definition.description || "")}"><i style="--node-color:${escapeHtml(CATEGORY_COLORS[definition.category] || "#8794ad")}"></i><span><b>${escapeHtml(nodeText(definition.title))}</b><small>${escapeHtml(nodeText(availability.status))} · ${escapeHtml(availability.reason || definition.description || "")}</small></span></button>`;
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
    const runtimePreflight = this.root.querySelector("[data-graph-runtime-preflight]");
    if (runtimePreflight) {
      const plan = this.runtimePreflightResult || {};
      const state = String(plan.workflow_state || "");
      const blockers = Array.isArray(plan.capability_plan?.blockers) ? plan.capability_plan.blockers.length : 0;
      const resourceBlockers = Array.isArray(plan.resource_plan?.blockers) ? plan.resource_plan.blockers.length : 0;
      const artifactBlockers = Array.isArray(plan.artifact_plan?.blockers) ? plan.artifact_plan.blockers.length : 0;
      runtimePreflight.textContent = !this.runtimePreflightResult
        ? "Chưa có preflight Workflow Runtime V2."
        : plan.status === "invalid"
          ? `Preflight V2 không hợp lệ: ${plan.error || "graph"}.`
          : `Preflight V2 · ${state || "DRAFT"} · ${blockers + resourceBlockers + artifactBlockers} blocker · chưa thực thi.`;
      runtimePreflight.dataset.runtimePreflight = String(plan.status || "not_run");
    }
    this.renderWorkflowStatus();
    this.updateToolbar();
  }

  renderInspector() {
    if (!this.inspectorElement) return;
    const nodes = this.selectedNodes();
    if (!nodes.length) {
      this.inspectorElement.innerHTML = `<div class="graph-inspector__empty"><strong>Inspector / Live preview</strong><p>Chọn node để chỉnh thông số và xem output. Kết nối trực tiếp từ socket sang socket.</p><div class="graph-type-legend">${Object.entries(TYPE_COLORS).map(([type, color]) => `<span><i style="--node-color:${color}"></i>${type}</span>`).join("")}</div><p>Minimap, pan/zoom, undo/redo và layout do canvas xử lý.</p></div>`;
      localizeNodeStaticMarkup(this.inspectorElement);
      return;
    }
    if (nodes.length > 1) {
      this.inspectorElement.innerHTML = `<div class="graph-inspector__empty"><strong>${nodes.length} node đang được chọn</strong><p>Kéo các node cùng lúc, dùng Delete để xóa hoặc Ctrl+Z để hoàn tác.</p></div>`;
      localizeNodeStaticMarkup(this.inspectorElement);
      return;
    }
    const node = nodes[0];
    const definition = this.registry.get(node.hubType);
    const state = this.nodeStates.get(node.hubId) || {};
    const provenance = this.runProvenance.filter((item) => item && item.node_id === node.hubId);
    const artifactCollection = collectArtifactProjections([state.output, provenance]);
    const preview = this.renderArtifactPreview(artifactCollection, state);
    const operationEvidence = this.operationEvidenceFor(definition);
    const availability = this.operationAvailabilityFor(definition);
    const action = isMediaScope(this.scope) && !operationEvidence ? availability.action : state.next_action || availability.action;
    const displayStatus = isMediaScope(this.scope) && !operationEvidence ? availability.status : state.status || availability.status;
    const displayMessage = isMediaScope(this.scope) && !operationEvidence ? availability.reason : state.message || state.error || availability.reason || "";
    const validation = this.validation ? (this.validation.errors?.length ? `${this.validation.errors.length} error(s)` : "valid") : "not_run";
    const dirty = this.dirty.has(node.hubId) ? "dirty" : "clean";
    const cache = state.cache_hit === true ? "hit" : state.cache_hit === false ? "miss" : "not_run";
    const progress = state.progress === undefined || state.progress === null ? "not_run" : Number.isFinite(Number(state.progress)) ? `${Math.max(0, Math.min(100, Number(state.progress)))}%` : "unavailable";
    const error = state.error ? String(state.error).slice(0, 240) : "none";
    const statusRows = [["Validation", validation], ["Dirty / downstream", dirty], ["Cache", cache], ["Progress", progress], ["Error", error]]
      .map(([label, value]) => `<div><dt>${escapeHtml(nodeText(label))}</dt><dd>${escapeHtml(nodeText(value))}</dd></div>`).join("");
    const outputStates = (definition?.outputs || []).map((port) => {
      const outputState = outputSocketState(state, port.name);
      return `<li class="graph-output-state" data-output-state="${escapeHtml(outputState)}"><span class="graph-output-state__dot" aria-hidden="true"></span><span>${escapeHtml(nodeText(port.label || port.name))}</span><strong>${escapeHtml(outputSocketStateLabel(outputState))}</strong></li>`;
    }).join("");
    const outputMarkup = outputStates ? `<section class="graph-inspector__section"><strong>${escapeHtml(nodeText("Output states"))}</strong><ul class="graph-output-states">${outputStates}</ul></section>` : "";
    const capability = `<div class="graph-inspector__capability" data-status="${escapeHtml(displayStatus)}"><div class="graph-inspector__capability-head"><strong>${escapeHtml(nodeText(displayStatus))}</strong><span class="status-pill" data-status="${escapeHtml(displayStatus)}">${escapeHtml(nodeText(displayStatus))}</span></div><p>${escapeHtml(nodeText(displayMessage || "Snapshot chưa công bố thêm giải thích."))}</p></div>`;
    const nextAction = action || "Chưa có hành động tiếp theo trong snapshot này.";
    const parameterMarkup = renderPropertyGroups(node, definition?.properties || []) || `<p class="graph-empty">${escapeHtml(nodeText("Node này không có property."))}</p>`;
    this.inspectorElement.innerHTML = `<div class="graph-inspector__head"><div><span class="tag">${escapeHtml(nodeText(definition?.category || "node"))}</span><h3>${escapeHtml(nodeText(definition?.title || node.hubType))}</h3><p>${escapeHtml(nodeText(definition?.description || ""))}</p></div></div><section class="graph-inspector__section"><strong>${escapeHtml(nodeText("Capability"))}</strong>${capability}</section><section class="graph-inspector__section"><strong>${escapeHtml(nodeText("Bước tiếp theo"))}</strong><div class="graph-action-hint"><span>${escapeHtml(nodeText(nextAction))}</span></div></section><section class="graph-inspector__section"><strong>${escapeHtml(nodeText("Execution status"))}</strong><dl class="graph-status-list">${statusRows}</dl></section>${outputMarkup}${preview ? `<section class="graph-inspector__section"><strong>${escapeHtml(nodeText("Artifact"))}</strong>${preview}</section>` : ""}<section class="graph-inspector__section"><strong>${escapeHtml(nodeText("Parameters"))}</strong><div class="graph-property-groups">${parameterMarkup}</div></section>`;
    if (isMediaScope(this.scope)) {
      this.inspectorElement.querySelector(".graph-inspector__head")?.insertAdjacentHTML("afterend", this.operationEvidenceMarkup(operationEvidence ? definition : null));
      const inspectorState = this.inspectorElement.querySelector(".graph-node-state");
      inspectorState?.setAttribute("data-operation-scope-status", operationEvidence?.status || this.operationScope.status);
      inspectorState?.setAttribute("data-operation-scope-verified", String(operationEvidence?.evidenceVerified || this.operationScope.evidenceVerified === true));
    }
    localizeNodeStaticMarkup(this.inspectorElement);
  }

  renderArtifactPreview(collection, state = {}) {
    return renderArtifactPreviewMarkup(collection, state);
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
    const before = this.beforeChange;
    this.beforeChange = null;
    if (before && before !== graphFingerprint(next)) this.commitGraphChange(before, next);
  }

  mutate(callback) {
    this.liteGraph.beforeChange();
    callback();
    this.liteGraph.afterChange();
  }

  addNode(type, { position = null, center = true } = {}) {
    const definition = this.registry.get(type);
    if (!definition) return;
    const node = globalThis.LiteGraph.createNode(`local-ai-hub/${type}`);
    if (!node) return;
    node.hubType = type;
    node.hubId = uid();
    node._hubEditor = this;
    const requested = position || this.liteCanvas.convertCanvasToOffset([this.canvasElement.width * 0.5, this.canvasElement.height * 0.5]);
    const free = findFreeGridSlot(this.liteGraph._nodes, requested, node, { grid: NODE_GRID_SIZE, gap: NODE_LAYOUT_GAP_Y });
    node.pos = [free.x, free.y];
    this.mutate(() => this.liteGraph.add(node));
    this.liteCanvas.selectNode(node);
    if (center) this.liteCanvas.centerOnNode(node);
    this.scheduleMinimapUpdate();
  }

  duplicateNode(source) {
    const definition = this.registry.get(source?.hubType);
    if (!definition) return;
    const node = globalThis.LiteGraph.createNode(`local-ai-hub/${definition.type}`);
    if (!node) return;
    node.hubType = definition.type;
    node.hubId = uid();
    node._hubEditor = this;
    node.properties = { ...node.properties, ...(source.properties || {}) };
    this.syncNodeWidgetsFromProperties(node);
    if (source._hubSizePersisted || source._hubSizeChanged) {
      const requested = normalizeNodeSize(source.size, node.computeSize?.());
      const minimum = node.computeSize?.() || [NODE_MIN_WIDTH, NODE_MIN_HEIGHT];
      node.setSize?.([Math.max(requested.width, Number(minimum[0]) || NODE_MIN_WIDTH), Math.max(requested.height, Number(minimum[1]) || NODE_MIN_HEIGHT)]);
      node._hubSizePersisted = true;
    }
    const free = findFreeGridSlot(this.liteGraph._nodes, { x: Number(source.pos?.[0] || 0) + NODE_GRID_SIZE, y: Number(source.pos?.[1] || 0) + NODE_GRID_SIZE }, node, { grid: NODE_GRID_SIZE, gap: NODE_LAYOUT_GAP_Y });
    node.pos = [free.x, free.y];
    this.mutate(() => this.liteGraph.add(node));
    this.liteCanvas.selectNode(node);
    this.scheduleMinimapUpdate();
  }

  reflowCollisionFreeNodes() {
    if (!this.liteGraph?._nodes?.length) return;
    const values = this.liteGraph._nodes.map((node) => ({ id: node.hubId || String(node.id), pos: node.pos, size: node.size }));
    const positions = new Map(collisionFreeNodePositions(values, { gap: 0, grid: NODE_GRID_SIZE, maxRadius: 64 }).map((item) => [item.id, item]));
    this.liteGraph._nodes.forEach((node) => {
      const position = positions.get(node.hubId || String(node.id));
      if (!position) return;
      node.pos = [position.x, position.y];
      node.setDirtyCanvas?.(true, true);
    });
  }

  syncNodeWidgetsFromProperties(node) {
    for (const widget of node?.widgets || []) {
      const name = widget?.options?.property || widget?.hubProperty;
      if (name && Object.prototype.hasOwnProperty.call(node.properties || {}, name)) {
        widget.value = node.properties[name];
        this.syncPropertyPeers(`${node.id}:${name}`, node.properties[name]);
      }
    }
  }

  syncPropertyPeers(target, value) {
    for (const element of this.root?.querySelectorAll?.("[data-graph-property]") || []) {
      if (element.dataset.graphProperty !== target) continue;
      if (element.type === "checkbox") element.checked = value === true;
      else element.value = String(value ?? "");
    }
    for (const output of this.root?.querySelectorAll?.("[data-graph-property-value]") || []) {
      const group = output.closest("[data-graph-property-group]");
      if (group?.dataset.graphPropertyGroup === target) output.textContent = String(value ?? "");
    }
  }

  autoLayout() {
    if (!this.liteGraph?._nodes?.length) return;
    const graph = this.toHubGraph();
    const nodes = this.liteGraph._nodes.map((node) => ({ id: node.hubId || String(node.id), pos: node.pos, size: node.size }));
    const positions = new Map(computeDeterministicLayout(nodes, graph.edges, { horizontalGap: NODE_LAYOUT_GAP_X, verticalGap: NODE_LAYOUT_GAP_Y, marginX: 80, marginY: 80 }).map((item) => [item.id, item]));
    this.mutate(() => {
      this.liteGraph._nodes.forEach((node) => {
        const position = positions.get(node.hubId || String(node.id));
        if (!position) return;
        node.pos = [position.x, position.y];
        node.setDirtyCanvas?.(true, true);
      });
    });
    this.fitView();
    this.scheduleMinimapUpdate();
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
    this.scheduleMinimapUpdate();
  }

  deleteSelected() {
    const selected = this.selectedNodes();
    if (!selected.length) return;
    this.mutate(() => selected.forEach((node) => this.liteGraph.remove(node)));
    this.liteCanvas.deselectAllNodes();
    this.renderGraphStatus();
  }

  clearSelection() {
    if (!this.liteCanvas) return;
    this.liteCanvas.deselectAllNodes();
    this.renderInspector();
    this.scheduleMinimapUpdate();
  }

  propertyDescriptor(element) {
    const token = String(element?.dataset?.graphProperty || "");
    const separator = token.indexOf(":");
    if (separator < 1) return null;
    const liteId = token.slice(0, separator);
    const name = token.slice(separator + 1);
    const node = this.liteGraph?.getNodeById?.(Number(liteId));
    const definition = node && this.registry.get(node.hubType);
    const property = definition?.properties?.find((item) => item.name === name);
    return node && property ? { node, property, name, target: token } : null;
  }

  stageProperty(element) {
    const descriptor = this.propertyDescriptor(element);
    if (!descriptor) return false;
    const { node, property, name, target } = descriptor;
    if (!this.propertyGesture || this.propertyGesture.target !== target) {
      this.propertyGesture = { target, before: graphFingerprint(this.toHubGraph()) };
    }
    const rawValue = element.type === "checkbox" ? element.checked : element.value;
    const normalized = normalizeInlineControlValue(property, rawValue);
    if (!normalized.accepted) {
      this.showToast(`Không thể cập nhật ${property.label || name}: ${normalized.reason}.`, "warning");
      return false;
    }
    node.setProperty(name, normalized.value);
    this.syncNodeWidgetsFromProperties(node);
    this.syncPropertyPeers(target, normalized.value);
    node.setDirtyCanvas?.(true, true);
    return true;
  }

  commitProperty(element) {
    const descriptor = this.propertyDescriptor(element);
    if (!descriptor) return false;
    const before = this.propertyGesture?.target === descriptor.target
      ? this.propertyGesture.before
      : graphFingerprint(this.toHubGraph());
    this.propertyGesture = null;
    return this.commitGraphChange(before);
  }

  changeProperty(element) {
    this.stageProperty(element);
    this.commitProperty(element);
  }

  captureWidgetBeforeChange(node, name) {
    if (this.hydrating || !node || !name || this.widgetBeforeChange) return;
    this.widgetBeforeChange = graphFingerprint(this.toHubGraph());
  }

  changeWidgetValue(node, property, rawValue) {
    if (!this.widgetBeforeChange) this.captureWidgetBeforeChange(node, property.name);
    const changed = this.stageWidgetValue(node, property, rawValue);
    this.commitWidgetGesture(node, property.name);
    return changed;
  }

  stageWidgetValue(node, property, rawValue) {
    if (this.hydrating || !node || !property) return false;
    if (!this.widgetBeforeChange) this.captureWidgetBeforeChange(node, property.name);
    const normalized = normalizeInlineControlValue(property, rawValue);
    if (!normalized.accepted) {
      this.showToast(`Không thể cập nhật ${property.label || property.name}: ${normalized.reason}.`, "warning");
      return false;
    }
    if (node.properties?.[property.name] === normalized.value) {
      this.syncNodeWidgetsFromProperties(node);
      return false;
    }
    node.setProperty(property.name, normalized.value);
    this.syncNodeWidgetsFromProperties(node);
    node.setDirtyCanvas?.(true, true);
    return true;
  }

  commitWidgetGesture(_node, _name) {
    const before = this.widgetBeforeChange;
    this.widgetBeforeChange = null;
    if (!before) return false;
    return this.commitWidgetChange(before);
  }

  handleWidgetChange(node, name) {
    if (this.hydrating || !node || !name) return;
    this.captureWidgetBeforeChange(node, name);
  }

  handleWidgetCallback(node, property, rawValue) {
    if (this.hydrating || !node || !property) return;
    const normalized = normalizeInlineControlValue(property, rawValue);
    const before = this.widgetBeforeChange || graphFingerprint(this.toHubGraph());
    this.widgetBeforeChange = null;
    if (!normalized.accepted) {
      let previous;
      try {
        const snapshot = JSON.parse(before);
        previous = snapshot.nodes?.find((item) => item.id === node.hubId)?.data?.[property.name];
      } catch {
        previous = property.default;
      }
      node.setProperty(property.name, previous ?? property.default ?? "");
      this.syncNodeWidgetsFromProperties(node);
      this.showToast(`Không thể cập nhật ${property.label || property.name}: ${normalized.reason}.`, "warning");
      return;
    }
    node.setProperty(property.name, normalized.value);
    this.syncNodeWidgetsFromProperties(node);
    node.setDirtyCanvas?.(true, true);
    this.commitWidgetChange(before);
  }

  commitWidgetChange(before) {
    return this.commitGraphChange(before);
  }

  commitGraphChange(before, after = null) {
    const next = after || this.toHubGraph();
    const beforeValue = typeof before === "string" ? before : graphFingerprint(before);
    if (beforeValue === graphFingerprint(next)) return false;
    let previous;
    try { previous = JSON.parse(beforeValue); } catch { previous = this.graphData; }
    const changedIds = changedGraphNodeIds(previous, next);
    this.history.push(beforeValue);
    if (this.history.length > MAX_HISTORY) this.history.shift();
    this.future = [];
    this.graphData = next;
    this.runtimePreflightResult = null;
    this.unsaved = true;
    // A graph edit invalidates the displayed run projection without cancelling
    // the server-owned job. The job remains visible in Jobs if it is still live.
    if (this.activeJobId || this.persistedRun?.job_id) this.resetRunState();
    this.markDirty(changedIds);
    this.persist();
    this.renderInspector();
    this.scheduleMinimapUpdate();
    this.renderGraphStatus();
    return true;
  }

  async uploadAsset(input) {
    const [liteId, name] = input.dataset.graphAsset.split(":");
    const node = this.liteGraph.getNodeById(Number(liteId));
    const file = input.files?.[0];
    if (!node || !file) return;
    input.disabled = true;
    try {
      const artifact = await uploadFile(file);
      this.mutate(() => { node.setProperty(name, artifact.id); node.setDirtyCanvas(true, true); });
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
      if (node._hubSizePersisted || node._hubSizeChanged) {
        value.size = normalizeNodeSize(node.size, node.computeSize?.());
      }
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
    this.runtimePreflightResult = null;
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
      node._hubSizePersisted = Boolean(source.size);
      node._hubSizeChanged = false;
      node.pos = [asNumber(source.position?.x, 80), asNumber(source.position?.y, 80)];
      node.properties = { ...node.properties, ...(source.data || {}) };
      this.syncNodeWidgetsFromProperties(node);
      if (source.size) {
        const requested = normalizeNodeSize(source.size, node.computeSize?.());
        const minimum = node.computeSize?.() || [NODE_MIN_WIDTH, NODE_MIN_HEIGHT];
        node.setSize?.([Math.max(requested.width, Number(minimum[0]) || NODE_MIN_WIDTH), Math.max(requested.height, Number(minimum[1]) || NODE_MIN_HEIGHT)]);
      }
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
    this.reflowCollisionFreeNodes();
    const hydrated = this.toHubGraph();
    this.graphData = { ...clone(graph), nodes: hydrated.nodes, edges: hydrated.edges, groups: clone(this.groups || []) };
    this.hydrating = false;
    this.liteCanvas.setDirty(true, true);
    if (this.liteGraph._nodes.length) this.fitView();
    this.renderInspector();
    this.scheduleMinimapUpdate();
    this.renderGraphStatus();
  }

  async loadPreset(id, { quiet = false, render = true } = {}) {
    if (!id) return;
    try {
      const result = await getNodePreset(id);
      if (!result.validation?.valid) throw new Error(result.validation?.errors?.[0]?.message || "Preset không qua schema validation.");
      this.history = [];
      this.future = [];
      this.resetRunState();
      this.dirty = new Set((result.graph.nodes || []).map((node) => node.id));
      this.graphData = { ...result.graph, scope: this.scope };
      this.savedFingerprint = quiet ? graphFingerprint(result.graph) : "";
      this.recovered = false;
      this.unsaved = !quiet;
      if (render && this.liteGraph) this.hydrateLiteGraph(result.graph);
      this.persist({ source: "template", saved: quiet });
      if (!quiet) this.showToast("Đã nạp preset workflow Hub.");
    } catch (error) {
      if (!quiet) this.showToast(error.message, "error");
    }
  }

  undo() {
    const previous = this.history.pop();
    if (!previous) return;
    const current = this.toHubGraph();
    this.future.push(graphFingerprint(current));
    const graph = JSON.parse(previous);
    const changedIds = changedGraphNodeIds(current, graph);
    this.resetRunState();
    this.hydrateLiteGraph(graph);
    this.unsaved = true;
    this.markDirty(changedIds);
    this.persist();
  }

  redo() {
    const next = this.future.pop();
    if (!next) return;
    const current = this.toHubGraph();
    this.history.push(graphFingerprint(current));
    const graph = JSON.parse(next);
    const changedIds = changedGraphNodeIds(current, graph);
    this.resetRunState();
    this.hydrateLiteGraph(graph);
    this.unsaved = true;
    this.markDirty(changedIds);
    this.persist();
  }

  markDirty(changedIds) {
    const graph = this.toHubGraph();
    const currentIds = new Set(graph.nodes.map((node) => node.id));
    this.dirty.forEach((id) => { if (!currentIds.has(id)) this.dirty.delete(id); });
    const changed = downstreamDirtyNodeIds(graph, Array.isArray(changedIds) ? changedIds : [...(changedIds || [])]);
    changed.forEach((id) => this.dirty.add(id));
    getDirtyNodes(graph, changed).then((result) => {
      if (result.valid) result.dirty_nodes.forEach((id) => { if (currentIds.has(id)) this.dirty.add(id); });
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
      this.lastRunSnapshotFingerprint = "";
      this.persist({ source: "run", syncDraft: false });
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
    const jobId = safePersistedJobId(this.activeJobId);
    if (!jobId || this.destroyed) return;
    this.stopPoll();
    const generation = this.pollGeneration;
    const schedule = () => {
      if (generation !== this.pollGeneration || this.activeJobId !== jobId || this.destroyed) return;
      this.pollTimer = setTimeout(poll, 2000);
    };
    const poll = async () => {
      if (generation !== this.pollGeneration || this.activeJobId !== jobId || this.destroyed) return;
      this.pollTimer = null;
      if (this.pollInFlight) return;
      this.pollInFlight = true;
      try {
        const response = await getNodeRun(jobId);
        if (generation !== this.pollGeneration || this.activeJobId !== jobId || this.destroyed) return;
        const run = response?.run;
        if (!run || typeof run !== "object") throw new Error("NODE_RUN_SNAPSHOT_INVALID");
        if (!this.applyRunSnapshot(run, { generation })) throw new Error("NODE_RUN_SNAPSHOT_STALE");
        if (this.activeJobId === jobId && generation === this.pollGeneration) schedule();
      } catch (error) {
        if (generation !== this.pollGeneration || this.activeJobId !== jobId || this.destroyed) return;
        if (error?.status === 404 || error?.payload?.error === "node_run_not_found") this.markRunUnavailable(jobId);
        else schedule();
      } finally {
        if (generation === this.pollGeneration) this.pollInFlight = false;
      }
    };
    void poll();
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

  async runtimePreflight() {
    try {
      this.runtimePreflightResult = await preflightWorkflowRuntimeV2({ graph: this.toHubGraph() });
      this.renderGraphStatus();
      const state = this.runtimePreflightResult?.workflow_state || "DRAFT";
      const status = this.runtimePreflightResult?.status || "unavailable";
      this.showToast(status === "completed" ? `Đã tạo preflight V2 · ${state}. Chưa có node nào được thực thi.` : (this.runtimePreflightResult?.error || "Preflight V2 chưa khả dụng."), status === "completed" ? "success" : "warning");
      return this.runtimePreflightResult;
    } catch (error) {
      this.runtimePreflightResult = { status: "unavailable", error: error.message || "workflow_runtime_v2_unavailable" };
      this.renderGraphStatus();
      this.showToast(error.message || "Không thể tạo Workflow Runtime V2 preflight.", "error");
      return this.runtimePreflightResult;
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
      this.resetRunState();
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
    const bounds = graphBounds(nodes, 32);
    const usableWidth = Math.max(240, this.canvasElement.width - 210);
    const usableHeight = Math.max(240, this.canvasElement.height - 150);
    const zoom = Math.max(0.35, Math.min(1.15, Math.min(usableWidth / Math.max(1, bounds.width), usableHeight / Math.max(1, bounds.height))));
    this.liteCanvas.ds.scale = zoom;
    this.liteCanvas.ds.offset[0] = usableWidth / (2 * zoom) - (bounds.left + bounds.right) / 2;
    this.liteCanvas.ds.offset[1] = usableHeight / (2 * zoom) - (bounds.top + bounds.bottom) / 2;
    this.liteCanvas.setDirty(true, true);
    this.scheduleMinimapUpdate();
  }

  drawMinimap() {
    if (!this.minimap || !this.liteGraph) return;
    const ctx = this.minimap.getContext("2d");
    if (!ctx) return;
    const { width, height } = this.minimap;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "rgba(8, 13, 27, .94)";
    ctx.fillRect(0, 0, width, height);
    const nodes = this.liteGraph._nodes;
    if (!nodes.length) {
      this.minimapBounds = null;
      return;
    }
    const canvasScale = Math.max(0.01, Number(this.liteCanvas.ds.scale || 1));
    const bounds = minimapWorldBounds({ nodes, viewport: [this.canvasElement?.width || 1, this.canvasElement?.height || 1], offset: this.liteCanvas.ds.offset, scale: canvasScale, padding: 24 });
    const minimapScale = Math.min((width - 18) / bounds.width, (height - 18) / bounds.height);
    const offsetX = (width - bounds.width * minimapScale) / 2 - bounds.left * minimapScale;
    const offsetY = (height - bounds.height * minimapScale) / 2 - bounds.top * minimapScale;
    const selected = new Set(Object.values(this.liteCanvas.selected_nodes || {}).map((node) => String(node.hubId || node.id)));
    const nodeById = new Map(nodes.map((node) => [node.id, node]));

    // Typed edges are drawn before nodes so the selected node remains legible.
    for (const link of Object.values(this.liteGraph.links || {})) {
      const source = nodeById.get(link.origin_id);
      const target = nodeById.get(link.target_id);
      if (!source || !target) continue;
      const sourcePort = source.outputs?.[link.origin_slot];
      const type = sourcePort?.type || "METADATA";
      const startX = (Number(source.pos[0]) + Number(source.size[0])) * minimapScale + offsetX;
      const startY = (Number(source.pos[1]) + Number(source.size[1]) * 0.5) * minimapScale + offsetY;
      const endX = Number(target.pos[0]) * minimapScale + offsetX;
      const endY = (Number(target.pos[1]) + Number(target.size[1]) * 0.5) * minimapScale + offsetY;
      ctx.strokeStyle = socketColor(type);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(startX, startY);
      ctx.lineTo(endX, endY);
      ctx.stroke();
    }
    for (const node of nodes) {
      const color = CATEGORY_COLORS[this.registry.get(node.hubType)?.category] || "#8794ad";
      ctx.fillStyle = color;
      ctx.fillRect(node.pos[0] * minimapScale + offsetX, node.pos[1] * minimapScale + offsetY, Math.max(4, node.size[0] * minimapScale), Math.max(3, node.size[1] * minimapScale));
      if (selected.has(String(node.hubId || node.id))) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.strokeRect(node.pos[0] * minimapScale + offsetX - 1, node.pos[1] * minimapScale + offsetY - 1, Math.max(4, node.size[0] * minimapScale) + 2, Math.max(3, node.size[1] * minimapScale) + 2);
      }
    }
    const viewLeft = -Number(this.liteCanvas.ds.offset?.[0] || 0) / canvasScale;
    const viewTop = -Number(this.liteCanvas.ds.offset?.[1] || 0) / canvasScale;
    const viewWidth = (this.canvasElement?.width || 1) / canvasScale;
    const viewHeight = (this.canvasElement?.height || 1) / canvasScale;
    ctx.strokeStyle = "#edf2ff";
    ctx.lineWidth = 1;
    ctx.strokeRect(viewLeft * minimapScale + offsetX, viewTop * minimapScale + offsetY, viewWidth * minimapScale, viewHeight * minimapScale);
    this.minimapBounds = { scale: minimapScale, offsetX, offsetY, graphBounds: bounds };
  }

  recenterFromMinimap(event) {
    if (!this.minimapBounds || !this.minimap) return;
    const rect = this.minimap.getBoundingClientRect();
    const x = (event.clientX - rect.left) * this.minimap.width / Math.max(1, rect.width);
    const y = (event.clientY - rect.top) * this.minimap.height / Math.max(1, rect.height);
    const graphX = (x - this.minimapBounds.offsetX) / this.minimapBounds.scale;
    const graphY = (y - this.minimapBounds.offsetY) / this.minimapBounds.scale;
    this.liteCanvas.ds.offset[0] = this.canvasElement.width / (2 * this.liteCanvas.ds.scale) - graphX;
    this.liteCanvas.ds.offset[1] = this.canvasElement.height / (2 * this.liteCanvas.ds.scale) - graphY;
    this.liteCanvas.setDirty(true, true);
    this.scheduleMinimapUpdate();
  }

  startMinimapDrag(event) {
    if (!this.minimap || event?.button !== undefined && event.button !== 0) return;
    event?.preventDefault?.();
    this.minimapDragging = { pointerId: event?.pointerId ?? "mouse" };
    this.recenterFromMinimap(event);
    this.minimap.setPointerCapture?.(event.pointerId);
    const move = (moveEvent) => {
      if (!this.minimapDragging || (moveEvent.pointerId ?? "mouse") !== this.minimapDragging.pointerId) return;
      this.recenterFromMinimap(moveEvent);
    };
    const end = (endEvent) => {
      if (!this.minimapDragging || (endEvent.pointerId ?? "mouse") !== this.minimapDragging.pointerId) return;
      this.stopMinimapDrag();
    };
    this._minimapDragMove = move;
    this._minimapDragEnd = end;
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", end, true);
    window.addEventListener("pointercancel", end, true);
  }

  stopMinimapDrag() {
    if (this._minimapDragMove) window.removeEventListener("pointermove", this._minimapDragMove, true);
    if (this._minimapDragEnd) {
      window.removeEventListener("pointerup", this._minimapDragEnd, true);
      window.removeEventListener("pointercancel", this._minimapDragEnd, true);
    }
    this._minimapDragMove = null;
    this._minimapDragEnd = null;
    this.minimapDragging = null;
  }

  handleAction(action) {
    if (action === "run") {
      const eligibility = this.runEligibility();
      if (!eligibility.eligible) { this.showToast(eligibility.reason, "warning"); this.updateToolbar(); return; }
      this.run();
    }
    if (action === "validate") this.validate(false);
    if (action === "runtime-preflight") this.runtimePreflight();
    if (action === "cancel") this.cancel();
    if (action === "undo") this.undo();
    if (action === "redo") this.redo();
    if (action === "clear-selection") this.clearSelection();
    if (action === "delete") this.deleteSelected();
    if (action === "auto-layout") this.autoLayout();
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
    if (action === "favorite-library") { this.toggleLibraryFavorite(); return; }
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
