import { escapeHtml, formatGb, formatStatus } from "../api.js";
import { translateText } from "../i18n.js";

// Page templates contain a mixture of fixed UI copy and server-owned snapshot
// values.  Translate only values supplied by the template itself; all values
// read from `state` continue through escapeHtml() unchanged.
const uiText = (value) => translateText(String(value ?? ""));
const uiTextHtml = (value) => escapeHtml(uiText(value));
// Generic render helpers must not infer that their arguments are static.  A
// card title, field label, or metric detail can come from the server snapshot
// or a user-authored creative record.  Those values are escaped only; fixed
// copy opts into uiTextHtml at the call site.
const dynamicTextHtml = (value) => escapeHtml(String(value ?? ""));

export const NAVIGATION = [
  { group: "TỔNG QUAN", items: [["dashboard", "Dashboard", "◫"], ["airi", "AIRI", "◌"]] },
  { group: "VISION & DOCUMENT", items: [["vision", "Vision Studio", "◉"], ["sam2", "SAM2", "◒"], ["ocr", "OCR", "▤"]] },
  { group: "SPEECH & VOICE", items: [["whisper", "Whisper", "≋"], ["voice", "Voice", "♪"]] },
  { group: "IMAGE & VIDEO", items: [["image", "Image AI", "✦"], ["media", "Media", "▹"], ["video", "Video Creative", "▶"], ["animesr", "AnimeSR", "⇱"]] },
  { group: "CREATIVE WORKSPACE", items: [["projects", "Projects & Recipes", "▧"]] },
  { group: "HỆ THỐNG", items: [["jobs", "Jobs", "≡"], ["components", "Components / AI Setup", "⬡"], ["models", "Models & Storage", "▦"], ["diagnostics", "Diagnostics", "⛨"], ["settings", "Settings", "⚙"]] },
];

const component = (state, id) => (state.components || []).find((item) => item.id === id) || {};
const tool = (state, id) => (state.tools || []).find((item) => item.name === id) || {};
const app = (state, id) => (state.applications || []).find((item) => item.id === id) || {};

const opaqueArtifactId = (value) => {
  const candidate = String(value || "");
  return /^artifact_[a-f0-9]{32}$/.test(candidate) ? candidate : "";
};
const opaqueArtifactUrl = (value) => {
  const candidate = String(value || "");
  return /^\/api\/artifacts\/artifact_[a-f0-9]{32}$/.test(candidate) ? candidate : "";
};
const safeArtifactName = (value) => {
  const candidate = String(value || "Artifact").replace(/[\r\n]+/g, " ").split(/[\\/]/).pop().trim().slice(0, 180);
  return /(?:api[_-]?key|password|secret|token)\s*[:=]/i.test(candidate) ? "Artifact" : candidate || "Artifact";
};
const artifactMetadata = (item) => {
  const mediaType = String(item?.media_type || "application/octet-stream").split(";", 1)[0].trim().toLowerCase();
  const size = Number(item?.size_bytes);
  const created = String(item?.created_at || "").trim();
  const hash = String(item?.sha256 || "").trim().toLowerCase();
  return JSON.stringify({
    media_type: /^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(mediaType) ? mediaType : "application/octet-stream",
    size_bytes: Number.isInteger(size) && size >= 0 ? size : null,
    created_at: /^[0-9T:.+Z-]{8,80}$/.test(created) ? created : null,
    sha256: /^[a-f0-9]{64}$/.test(hash) ? hash : null,
  });
};
const artifactProvenance = (item) => {
  const source = item?.provenance && typeof item.provenance === "object" ? item.provenance : {};
  const jobId = String(source.job_id || "");
  const fingerprint = String(source.job_spec_fingerprint || "");
  const adapter = String(source.adapter_id || "");
  const attempt = Number(source.attempt);
  const status = String(source.status || "");
  return JSON.stringify({
    job_id: /^jobv5_[a-f0-9]{32}$/.test(jobId) ? jobId : null,
    job_spec_fingerprint: /^[a-f0-9]{64}$/.test(fingerprint) ? fingerprint : null,
    adapter_id: /^[a-z][a-z0-9_.-]{0,63}$/.test(adapter) ? adapter : null,
    attempt: Number.isInteger(attempt) && attempt >= 1 && attempt <= 10000 ? attempt : null,
    status: ["queued", "starting", "running", "cancelling", "completed", "failed", "unavailable", "interrupted"].includes(status) ? status : null,
  });
};
const isMaskArtifact = (item, name) => item?.mask === true || item?.is_mask === true || item?.artifact_kind === "mask" || /(?:^|[._ -])mask(?:[._ -]|$)/i.test(name);

const READINESS_STATUS_LABELS = Object.freeze({
  operational: "Operational",
  healthy: "Healthy",
  ready: "Ready",
  clean: "Clean",
  partial: "Partial",
  unavailable: "Unavailable",
  not_published: "Not published",
  not_run: "Not run",
  error: "Error",
  available: "Available",
  installed: "Installed",
  not_installed: "Not installed",
  planned: "Planned",
  missing: "Missing",
  unknown: "Unknown",
  queued: "Queued",
  starting: "Starting",
  running: "Running",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  failed: "Failed",
  interrupted: "Interrupted",
  completed: "Completed",
  blocked: "Blocked",
  degraded: "Degraded",
  needs_setup: "Needs setup",
  waiting: "Waiting",
  not_applicable: "Not applicable",
  unsupported: "Unsupported",
  recovery_required: "Recovery required",
  stale_session: "Stale session",
  incompatible: "Incompatible",
  attention: "Needs attention",
  external_managed: "External app",
});
const UI_STATUS_RE = /^[a-z][a-z0-9_-]{0,39}$/;
const uiStatus = (value, fallback = "unknown") => {
  const candidate = typeof value === "string" ? value.trim().toLowerCase() : "";
  return UI_STATUS_RE.test(candidate) ? candidate : fallback;
};
const readinessStatus = (value, fallback = "unknown") => {
  const candidate = uiStatus(value, fallback);
  return Object.prototype.hasOwnProperty.call(READINESS_STATUS_LABELS, candidate) ? candidate : fallback;
};
const readinessStatusLabel = (value) => {
  const normalized = readinessStatus(value);
  return READINESS_STATUS_LABELS[normalized] || formatStatus(normalized);
};
const STATUS_SEVERITY = Object.freeze({
  operational: "success", healthy: "success", ready: "success", clean: "success", available: "success", installed: "success", running: "success", completed: "success",
  partial: "warning", degraded: "warning", needs_setup: "warning", unavailable: "warning", missing: "warning", not_installed: "warning", attention: "warning", cancelling: "warning",
  error: "error", failed: "error", blocked: "error", incompatible: "error", recovery_required: "error", stale_session: "warning",
  not_run: "neutral", planned: "neutral", not_published: "neutral", waiting: "neutral", starting: "neutral", queued: "neutral", cancelled: "neutral", interrupted: "neutral", unknown: "neutral",
  not_applicable: "muted", unsupported: "muted", external_managed: "muted",
});
const statusSeverity = (status) => STATUS_SEVERITY[readinessStatus(status)] || "neutral";
const STATUS_MEANINGS = Object.freeze({
  success: ["Đang hoạt động", "Không phát hiện lỗi trong snapshot hiện tại.", "Bạn không cần làm gì thêm lúc này."],
  warning: ["Chưa sẵn sàng đầy đủ", "Chức năng đang thiếu setup, bằng chứng hoặc phụ thuộc cần kiểm tra.", "Mở phần thiết lập được nêu ở Bước tiếp theo; không tự chạy workload."],
  error: ["Đang lỗi hoặc bị chặn", "Một phụ thuộc bắt buộc đã lỗi, không hợp lệ hoặc bị từ chối.", "Sửa nguyên nhân được nêu, rồi làm mới trạng thái trước khi thử lại."],
  neutral: ["Chưa chạy / chưa công bố", "Đây là trạng thái thông tin; chưa có lần chạy hoặc bằng chứng công bố, không tự khẳng định lỗi.", "Chờ bằng chứng hoặc thực hiện Bước tiếp theo nếu bạn cần chức năng này."],
  muted: ["Không áp dụng", "Chức năng này do ứng dụng ngoài quản lý hoặc chưa được Hub hỗ trợ trong contract hiện tại.", "Không cần sửa trong Hub; xem hướng dẫn tích hợp nếu muốn mở rộng."],
});
const statusImpact = (status, purpose = "chức năng này") => {
  const severity = statusSeverity(status);
  if (severity === "success") return `${purpose} có thể được sử dụng theo bằng chứng hiện tại.`;
  if (severity === "error") return `${purpose} bị chặn; các thao tác phụ thuộc có thể không chạy.`;
  if (severity === "warning") return `${purpose} chưa nên được coi là operational; thao tác nặng sẽ bị giữ an toàn.`;
  if (severity === "muted") return `${purpose} không thuộc phạm vi thực thi của Hub trong trạng thái này.`;
  return `${purpose} chưa được xác nhận đã chạy hoặc đã phát hành.`;
};
const statusPill = (status, labelOverride = "") => {
  // Snapshot status values are data, not translation keys.  Collapse any
  // unknown-but-well-shaped value to the fixed Unknown label before it can
  // reach the translator.
  const normalized = readinessStatus(status);
  const label = labelOverride || READINESS_STATUS_LABELS[normalized] || READINESS_STATUS_LABELS.unknown;
  return `<span class="status-pill" data-status="${escapeHtml(normalized)}" data-severity="${escapeHtml(statusSeverity(normalized))}" title="${escapeHtml(statusMeaning(normalized).summary)}">${uiTextHtml(label)}</span>`;
};
const statusMeaning = (status) => {
  const severity = statusSeverity(status);
  const copy = STATUS_MEANINGS[severity] || STATUS_MEANINGS.neutral;
  return { severity, summary: copy[0], reasonFallback: copy[1], actionFallback: copy[2] };
};
const statusExplanation = ({ name, technicalId = "", purpose = "Chức năng server-owned", status = "unknown", reason = "", impact = "", nextAction = "", compact = false } = {}) => {
  const normalized = readinessStatus(status);
  const meaning = statusMeaning(normalized);
  // Names often contain user/server-owned identifiers; translate only fixed
  // explanatory copy, never rewrite a dynamic label such as a component name.
  const safeName = safeUiText(name, "Mục trạng thái");
  const safeId = safeUiIdentifier(technicalId, "");
  const safePurpose = uiText(safeUiText(purpose, "Chức năng server-owned"));
  const safeReason = uiText(safeUiText(reason, meaning.reasonFallback));
  const safeImpact = uiText(safeUiText(impact, statusImpact(normalized, safePurpose)));
  const safeAction = uiText(safeUiText(nextAction, meaning.actionFallback));
  return `<details class="status-explanation${compact ? " status-explanation--compact" : ""}" data-status-explanation data-status="${escapeHtml(normalized)}" data-severity="${escapeHtml(meaning.severity)}"><summary><span class="status-explanation__title"><strong>${escapeHtml(safeName)}</strong>${safeId ? `<code>${escapeHtml(safeId)}</code>` : ""}</span>${statusPill(normalized)}</summary><div class="status-explanation__body"><div><span class="status-explanation__label">${uiText("Dùng để làm gì")}</span><p>${escapeHtml(safePurpose)}</p></div><div><span class="status-explanation__label">${uiText("Trạng thái này nghĩa là gì")}</span><p>${escapeHtml(uiText(meaning.summary))}</p></div><div><span class="status-explanation__label">${uiText("Tại sao")}</span><p>${escapeHtml(safeReason)}</p></div><div><span class="status-explanation__label">${uiText("Ảnh hưởng")}</span><p>${escapeHtml(safeImpact)}</p></div><div><span class="status-explanation__label">${uiText("Bước tiếp theo")}</span><p>${escapeHtml(safeAction)}</p></div></div></details>`;
};
const unsafeUiText = /(?:[a-z]:[\\/]|\\\\|(?:^|\s)\/(?:etc|tmp|var|home)(?:[\\/]|$)|(?:file|data):|(?:api[_-]?key|password|secret|token)\s*[:=])/i;
const safeUiText = (value, fallback = "") => {
  if (typeof value !== "string") return fallback;
  const candidate = value.trim().slice(0, 240);
  return candidate && !unsafeUiText.test(candidate) ? candidate : fallback;
};
const safeUiIdentifier = (value, fallback = "unknown") => {
  const candidate = typeof value === "string" ? value.trim() : "";
  return /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,119}$/.test(candidate) ? candidate : fallback;
};
const safeJobId = (value, fallback = "") => {
  const candidate = safeUiText(value, "");
  return /^[A-Za-z0-9][A-Za-z0-9._:@_-]{0,119}$/.test(candidate) ? candidate : fallback;
};
const safeJobStatus = (value, fallback = "unavailable") => {
  const candidate = uiStatus(value, fallback);
  return Object.prototype.hasOwnProperty.call(READINESS_STATUS_LABELS, candidate) ? candidate : fallback;
};
const safeJobTimestamp = (value) => {
  const candidate = safeUiText(value, "");
  return /^[0-9T:.+Z-]{8,80}$/.test(candidate) ? candidate : "";
};
const JOB_HUMAN_TITLES = Object.freeze({
  segment_from_points: "Phân đoạn đối tượng từ điểm",
  segment_from_box: "Phân đoạn đối tượng từ khung",
  transcribe_media: "Chuyển âm thanh thành văn bản",
  run_media_operation: "Xử lý media",
  upscale_anime_video: "Nâng cấp video AnimeSR",
  generate_flux: "Tạo ảnh FLUX",
  generate_qwen_image: "Tạo/chỉnh ảnh Qwen",
  text_to_speech: "Tạo giọng nói",
  design_voice: "Thiết kế giọng nói",
  clone_voice: "Nhân bản giọng nói",
  convert_voice: "Chuyển đổi giọng nói",
});
const humanJobTitle = (value) => {
  const candidate = typeof value === "string" ? value.trim() : "";
  if (!candidate) return "Tác vụ Hub";
  return JOB_HUMAN_TITLES[candidate] || candidate.replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
};
const safeJobCount = (value, fallback = 0) => Number.isInteger(value) && value >= 0 && value <= 500 ? value : fallback;
const safeArtifactMediaType = (value) => {
  const candidate = typeof value === "string" ? value.split(";", 1)[0].trim().toLowerCase() : "";
  return /^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(candidate) ? candidate : "application/octet-stream";
};
const safeJobArtifacts = (value) => {
  const records = Array.isArray(value) ? value : [];
  return records.slice(0, 64).flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const rawId = typeof item.id === "string" ? item.id : typeof item.artifact_id === "string" ? item.artifact_id : "";
    const id = opaqueArtifactId(rawId);
    const url = opaqueArtifactUrl(item.url);
    if (!id && !url) return [];
    const nameValue = typeof item.name === "string" ? item.name : "";
    const record = {
      id: id || url.split("/").pop() || "",
      url,
      name: safeArtifactName(nameValue),
      media_type: safeArtifactMediaType(item.media_type),
      size_bytes: Number.isInteger(item.size_bytes) && item.size_bytes >= 0 ? item.size_bytes : null,
      created_at: typeof item.created_at === "string" ? item.created_at : "",
      sha256: typeof item.sha256 === "string" ? item.sha256 : "",
      provenance: item.provenance && typeof item.provenance === "object" && !Array.isArray(item.provenance) ? item.provenance : {},
    };
    if (item.mask === true || item.is_mask === true || item.artifact_kind === "mask") record.is_mask = true;
    return [record];
  });
};
const safeJobProvenance = (value) => {
  const records = Array.isArray(value) ? value : [];
  return records.slice(0, 64).flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const artifactId = typeof item.artifact_id === "string" ? opaqueArtifactId(item.artifact_id) : "";
    const nodeType = safeUiIdentifier(item.node_type, "node");
    const nameValue = typeof item.name === "string" ? item.name : artifactId;
    return [{ artifact_id: artifactId, node_type: nodeType, name: safeArtifactName(nameValue) }];
  });
};
const safeHotJobDetail = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const detail = value;
  const result = detail.result && typeof detail.result === "object" && !Array.isArray(detail.result) ? detail.result : {};
  const rawArtifacts = Array.isArray(result.artifacts) ? result.artifacts : Array.isArray(detail.artifacts) ? detail.artifacts : [];
  const rawProvenance = Array.isArray(result.provenance) ? result.provenance : Array.isArray(detail.provenance) ? detail.provenance : [];
  const lifecycleValue = typeof detail.lifecycle === "string" ? detail.lifecycle : typeof detail.lifecycle_status === "string" ? detail.lifecycle_status : "";
  return {
    lifecycle: safeUiText(lifecycleValue),
    message: safeUiText(detail.message),
    error: safeUiText(detail.error),
    reason: safeUiText(detail.reason),
    contractVersion: safeUiText(detail.contract_version),
    createdAt: safeJobTimestamp(detail.created_at),
    startedAt: safeJobTimestamp(detail.started_at),
    updatedAt: safeJobTimestamp(detail.updated_at),
    finishedAt: safeJobTimestamp(detail.finished_at),
    artifactsPublished: Array.isArray(result.artifacts) || Array.isArray(detail.artifacts),
    artifacts: safeJobArtifacts(rawArtifacts),
    provenance: safeJobProvenance(rawProvenance),
  };
};
const safeReadinessModules = (state) => {
  const productization = state?.productization && typeof state.productization === "object" ? state.productization : {};
  const capabilities = productization.capabilities && typeof productization.capabilities === "object" ? productization.capabilities : {};
  const fromProductSurface = Array.isArray(capabilities.modules);
  const values = fromProductSurface ? capabilities.modules : (Array.isArray(state?.components) ? state.components : []);
  return values.slice(0, 64).map((item, index) => {
    const record = item && typeof item === "object" && !Array.isArray(item) ? item : {};
    const id = safeUiIdentifier(record.id, `module-${index + 1}`);
    return {
      id,
      label: fromProductSurface ? safeUiIdentifier(record.component, id) : safeUiText(record.name || record.component, id),
      kind: fromProductSurface ? safeUiIdentifier(record.provider, "server-owned") : safeUiText(record.kind || record.provider, "component"),
      version: safeUiText(record.version),
      status: readinessStatus(record.status || record.component_status, "missing"),
      purpose: safeUiText(record.purpose, "Kiểm tra phụ thuộc backend/model trước khi workflow sử dụng."),
      reason: safeUiText(record.reason, "Snapshot chưa công bố thêm nguyên nhân an toàn."),
      impact: safeUiText(record.impact, ""),
      nextAction: safeUiText(record.next_action, "Xem bằng chứng server-owned trước khi yêu cầu runtime."),
    };
  });
};
const safeStorageVolumes = (state) => {
  const productization = state?.productization && typeof state.productization === "object" ? state.productization : {};
  const storage = state?.storage && typeof state.storage === "object" ? state.storage : (productization.storage && typeof productization.storage === "object" ? productization.storage : {});
  const raw = Array.isArray(storage.volumes) ? storage.volumes : [];
  const byId = new Map(raw.filter((item) => item && typeof item === "object" && !Array.isArray(item)).map((item) => [String(item.id || "").toLowerCase(), item]));
  return ["c", "d"].map((id) => {
    const item = byId.get(id) || {};
    const status = readinessStatus(item.status, "unavailable");
    const numeric = ["total_bytes", "free_bytes", "used_bytes"].every((key) => Number.isInteger(item[key]) && item[key] >= 0);
    const available = status === "available" && numeric;
    return {
      id,
      label: id === "c" ? "C:" : "D:",
      status,
      available,
      totalBytes: available ? item.total_bytes : null,
      freeBytes: available ? item.free_bytes : null,
      usedBytes: available ? item.used_bytes : null,
      lowSpace: item.low_space === true,
      reason: safeUiText(item.reason, "Volume statistics are unavailable; no figures are shown."),
      nextAction: safeUiText(item.next_action, "Verify that the volume is mounted and readable, then refresh storage."),
    };
  });
};
const safeResourceGpu = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return {
    vendor: safeUiIdentifier(value.vendor, "unknown"),
    deviceClass: safeUiIdentifier(value.device_class, "unknown"),
    model: safeUiText(value.model, "unknown"),
    vramMb: Number.isInteger(value.vram_mb) && value.vram_mb >= 0 ? value.vram_mb : null,
  };
};
const safeResourceFits = (value, fitKey) => {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 64).flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    return [{
      id: safeUiIdentifier(item.id),
      status: readinessStatus(item.status),
      gpu: safeUiIdentifier(item.gpu, "unassigned"),
      fit: typeof item[fitKey] === "boolean" ? item[fitKey] : null,
    }];
  });
};
const safeResourceErrors = (value) => {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 32).flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const code = safeUiIdentifier(item.code, "resource_error");
    const module = safeUiIdentifier(item.module, "");
    return [{ code, module }];
  });
};
const safeResourceActions = (value) => Array.isArray(value)
  ? value.slice(0, 32).map((item) => safeUiText(item)).filter(Boolean)
  : [];
const safeResourcePlan = (state) => {
  const capabilities = state?.capabilities && typeof state.capabilities === "object" ? state.capabilities : {};
  const manager = capabilities.module_manager && typeof capabilities.module_manager === "object" ? capabilities.module_manager : {};
  const value = manager.resource_plan && typeof manager.resource_plan === "object" && !Array.isArray(manager.resource_plan) ? manager.resource_plan : {};
  return {
    available: Object.keys(value).length > 0,
    status: readinessStatus(value.status, "not_run"),
    mode: value.mode === "parallel" || value.mode === "serial" ? value.mode : "not_published",
    targetGpu: safeResourceGpu(value.target_gpu),
    physical: safeResourceFits(value.physical, "physical_fit"),
    concurrent: safeResourceFits(value.concurrent, "concurrent_fit"),
    errors: safeResourceErrors(value.errors),
    actions: safeResourceActions(value.actions),
  };
};
const readinessSnapshot = (state) => {
  const productization = state?.productization && typeof state.productization === "object" ? state.productization : {};
  const surface = productization.capabilities && typeof productization.capabilities === "object" ? productization.capabilities : {};
  const readiness = productization.readiness && typeof productization.readiness === "object" ? productization.readiness : {};
  const control = state?.capabilities && typeof state.capabilities === "object" ? state.capabilities : {};
  const storage = state?.storage && typeof state.storage === "object" ? state.storage : {};
  const executionValue = productization.execution || storage.execution || control.execution;
  return {
    status: readinessStatus(readiness.status || productization.status),
    reason: safeUiText(readiness.reason, "Readiness is derived from server-owned static capability evidence."),
    nextAction: safeUiText(readiness.next_action, "Review the module plan before requesting runtime work."),
    planStatus: readinessStatus(surface.module_plan_status, "not_run"),
    planReason: safeUiText(surface.reason, "Module preflight is server-owned static metadata."),
    planAction: safeUiText(surface.next_action, "Review the plan before any separately authorized operation."),
    modules: safeReadinessModules(state),
    resourcePlan: safeResourcePlan(state),
    volumes: safeStorageVolumes(state),
    execution: ["not_run", "dry_run"].includes(executionValue) ? executionValue : "not_published",
    dryRun: productization.dry_run === true,
    resourceSnapshot: Object.keys(control).length > 0 ? "current server capability snapshot" : "not published",
  };
};
const MEDIA_EVIDENCE_OPERATIONS = Object.freeze(["video_grade", "logo_overlay", "encode"]);
const MEDIA_EVIDENCE_LABELS = Object.freeze({ video_grade: "Video grade", logo_overlay: "Logo overlay", encode: "Encode" });
const MEDIA_EVIDENCE_OUTCOME_LABELS = Object.freeze({ completed: "Completed", error: "Error", blocked: "Blocked", not_run: "Not run" });
const MEDIA_EVIDENCE_EXECUTION_LABELS = Object.freeze({ completed: "Completed", attempted: "Attempted", not_run: "Not run" });
const unsafeMediaEvidenceText = /(?:[a-z]:[\\/]|\\\\|(?:^|\s)\/(?:etc|tmp|var|home)(?:[\\/]|$)|(?:file|data|https?):|(?:api[_-]?key|password|secret|token)\s*[:=]|\b(?:cmd|powershell|bash|ffmpeg|python|callable|manifest|command)\b)/i;
const isMediaEvidenceRecord = (value) => Boolean(value && typeof value === "object" && !Array.isArray(value));
const exactMediaOperationList = (value) => Array.isArray(value) && value.length === MEDIA_EVIDENCE_OPERATIONS.length && value.every((item, index) => item === MEDIA_EVIDENCE_OPERATIONS[index]);
const safeMediaEvidenceText = (value) => {
  if (typeof value !== "string") return null;
  const candidate = value.trim().slice(0, 240);
  return candidate && !unsafeMediaEvidenceText.test(candidate) ? candidate : null;
};
const mediaEvidenceFallback = () => ({
  status: "unavailable",
  outcome: "not_run",
  execution: "not_run",
  evidenceVerified: false,
  artifactPublished: false,
  invocationCount: 0,
  failureClass: "",
  cleanup: { processesRemaining: 0, tempCleaned: true },
  sourceOverwriteChecked: false,
  sourceOverwritten: null,
  availableOperations: [],
  reason: "No safe server-owned media evidence is available in this snapshot.",
  nextAction: "Keep media operations partial until exact server evidence is published.",
  operations: MEDIA_EVIDENCE_OPERATIONS.map((id) => ({ id, label: MEDIA_EVIDENCE_LABELS[id], status: "partial", reason: "This exact operation is not verified in the server snapshot.", nextAction: "Keep this operation partial until separately evidenced." })),
  genericStatus: "partial",
  genericReason: "Generic media actions are not covered by the three exact evidence rows.",
  genericNextAction: "Use only the published evidence rows; no generic media execution is claimed.",
});
const normalizeRuntimeMediaEvidence = (value) => {
  if (!isMediaEvidenceRecord(value) || value.schema_version !== "runtime-evidence-projection.v1" || value.subject !== "media_overlay_cpu_acceptance" || !exactMediaOperationList(value.operations)) return null;
  const status = ["operational", "partial", "unavailable"].includes(value.status) ? value.status : "";
  const outcome = Object.prototype.hasOwnProperty.call(MEDIA_EVIDENCE_OUTCOME_LABELS, value.outcome) ? value.outcome : "";
  const execution = Object.prototype.hasOwnProperty.call(MEDIA_EVIDENCE_EXECUTION_LABELS, value.execution) ? value.execution : "";
  const cleanup = value.cleanup;
  if (!status || !outcome || !execution || !isMediaEvidenceRecord(cleanup) || Object.keys(cleanup).some((key) => !["processes_remaining", "temp_cleaned"].includes(key)) || !Number.isInteger(cleanup.processes_remaining) || cleanup.processes_remaining < 0 || typeof cleanup.temp_cleaned !== "boolean") return null;
  if (typeof value.source_overwrite_checked !== "boolean" || (value.source_overwrite_checked && typeof value.source_overwritten !== "boolean") || (!value.source_overwrite_checked && value.source_overwritten !== null)) return null;
  if (!Number.isInteger(value.invocation_count) || ![0, 1].includes(value.invocation_count) || typeof value.artifact_published !== "boolean") return null;
  if (value.failure_class !== null && (typeof value.failure_class !== "string" || !/^[a-z][a-z0-9_-]{0,39}$/.test(value.failure_class))) return null;
  const reason = safeMediaEvidenceText(value.reason);
  const nextAction = safeMediaEvidenceText(value.next_action);
  if (!reason || !nextAction) return null;
  const exactCompleted = status === "operational" && outcome === "completed" && execution === "completed" && value.invocation_count === 1 && value.failure_class === null && cleanup.processes_remaining === 0 && cleanup.temp_cleaned === true && value.artifact_published === true && value.source_overwrite_checked === true && value.source_overwritten === false;
  const boundedError = ["partial", "unavailable"].includes(status) && outcome === "error" && execution === "attempted" && value.invocation_count === 1 && typeof value.failure_class === "string" && value.artifact_published === false;
  const notRun = ["partial", "unavailable"].includes(status) && ["blocked", "not_run"].includes(outcome) && execution === "not_run" && value.invocation_count === 0 && value.failure_class === null && value.artifact_published === false;
  if (!exactCompleted && !boundedError && !notRun) return null;
  return { status: exactCompleted ? "operational" : "unavailable", outcome, execution, evidenceVerified: exactCompleted, artifactPublished: value.artifact_published, invocationCount: value.invocation_count, failureClass: value.failure_class || "", cleanup: { processesRemaining: cleanup.processes_remaining, tempCleaned: cleanup.temp_cleaned }, sourceOverwriteChecked: value.source_overwrite_checked, sourceOverwritten: value.source_overwritten, reason, nextAction };
};
const normalizeMediaOperationScope = (value) => {
  if (!isMediaEvidenceRecord(value) || value.subject !== "media_overlay_cpu_acceptance" || value.schema_version !== "runtime-operation-scope.v1" || !exactMediaOperationList(value.operations) || !Array.isArray(value.available_operations) || !isMediaEvidenceRecord(value.operation_status) || Object.keys(value.operation_status).sort().join("|") !== MEDIA_EVIDENCE_OPERATIONS.slice().sort().join("|") || !MEDIA_EVIDENCE_OPERATIONS.every((id) => ["partial", "operational"].includes(value.operation_status[id]))) return null;
  const reason = safeMediaEvidenceText(value.reason);
  const nextAction = safeMediaEvidenceText(value.next_action);
  if (!reason || !nextAction || typeof value.evidence_verified !== "boolean") return null;
  const exactCompleted = value.evidence_verified === true && value.status === "operational" && value.execution === "completed" && exactMediaOperationList(value.available_operations) && MEDIA_EVIDENCE_OPERATIONS.every((id) => value.operation_status[id] === "operational");
  const safeUnavailable = value.evidence_verified === false && value.status === "unavailable" && value.execution === "not_run" && value.available_operations.length === 0 && MEDIA_EVIDENCE_OPERATIONS.every((id) => value.operation_status[id] === "partial");
  return exactCompleted || safeUnavailable ? { evidenceVerified: exactCompleted, availableOperations: exactCompleted ? MEDIA_EVIDENCE_OPERATIONS.slice() : [], operationStatus: { ...value.operation_status }, reason, nextAction } : null;
};
const mediaCapabilityEvidence = (state) => {
  const source = state && typeof state === "object" ? state : {};
  const productization = source.productization && typeof source.productization === "object" ? source.productization : {};
  const productCapabilities = productization.capabilities && typeof productization.capabilities === "object" && !Array.isArray(productization.capabilities) ? productization.capabilities : null;
  const capabilitySnapshot = productCapabilities || (source.capabilities && typeof source.capabilities === "object" && !Array.isArray(source.capabilities) ? source.capabilities : {});
  const runtimeEvidence = normalizeRuntimeMediaEvidence(capabilitySnapshot.runtime_evidence);
  const operationScope = normalizeMediaOperationScope(capabilitySnapshot.media_operation_scope);
  if (!runtimeEvidence || !operationScope || runtimeEvidence.evidenceVerified !== operationScope.evidenceVerified) return mediaEvidenceFallback();
  const verified = runtimeEvidence.evidenceVerified && operationScope.evidenceVerified;
  const genericReason = "Generic media actions are not covered by the three exact evidence rows.";
  const genericNextAction = "Use only the published evidence rows; no generic media execution is claimed.";
  return {
    ...runtimeEvidence,
    evidenceVerified: verified,
    availableOperations: verified ? MEDIA_EVIDENCE_OPERATIONS.slice() : [],
    reason: verified ? operationScope.reason : runtimeEvidence.reason,
    nextAction: verified ? operationScope.nextAction : runtimeEvidence.nextAction,
    operations: MEDIA_EVIDENCE_OPERATIONS.map((id) => ({
      id,
      label: MEDIA_EVIDENCE_LABELS[id],
      status: verified ? operationScope.operationStatus[id] : "partial",
      reason: verified ? operationScope.reason : runtimeEvidence.reason,
      nextAction: verified ? operationScope.nextAction : runtimeEvidence.nextAction,
    })),
    genericStatus: "partial",
    genericReason,
    genericNextAction,
  };
};
const mediaEvidencePanel = (state, variant = "compact") => {
  const evidence = mediaCapabilityEvidence(state);
  const detail = variant !== "compact";
  const action = variant === "compact"
    ? `<button class="button button--compact" type="button" data-readiness-route="settings"><strong>${uiTextHtml("Review detailed evidence")}</strong> <span class="row-meta">${uiTextHtml("Open server snapshot in Settings")}</span></button>`
    : variant === "settings"
      ? `<button class="button button--compact" type="button" data-route="media"><strong>Open Media / Node Studio</strong><span class="row-meta">View exact operation scope</span></button>`
      : "";
  const operationRows = evidence.operations.map((item) => `<article class="media-evidence-row" data-media-operation="${escapeHtml(item.id)}" data-operation-status="${escapeHtml(item.status)}"><div><strong>${uiTextHtml(item.label)}</strong><span>${escapeHtml(item.reason)}</span></div>${statusPill(item.status, readinessStatusLabel(item.status))}<p><strong>${uiTextHtml("Next action")}</strong> ${escapeHtml(item.nextAction)}</p></article>`).join("");
  const cleanupLabel = evidence.cleanup.processesRemaining === 0 && evidence.cleanup.tempCleaned ? "Clean" : "Needs review";
  const overwriteLabel = evidence.sourceOverwriteChecked ? (evidence.sourceOverwritten ? "Overwrite detected" : "Source preserved") : "Not checked";
  return `<section class="media-evidence card card--flat" aria-labelledby="media-evidence-title-${escapeHtml(variant)}" data-media-evidence data-media-evidence-status="${escapeHtml(evidence.status)}" data-media-evidence-outcome="${escapeHtml(evidence.outcome)}" data-media-evidence-execution="${escapeHtml(evidence.execution)}" data-media-evidence-verified="${String(evidence.evidenceVerified)}"><div class="card-title-row"><div><span class="eyebrow">${uiTextHtml("MEDIA CAPABILITY EVIDENCE")}</span><h2 id="media-evidence-title-${escapeHtml(variant)}">${uiTextHtml("Exact media operation scope")}</h2><p>${uiTextHtml("Server snapshot only; the UI does not execute media operations.")}</p></div>${statusPill(evidence.status, readinessStatusLabel(evidence.status))}</div><div class="media-evidence-summary"><div><span>${uiTextHtml("Outcome")}</span><strong>${uiTextHtml(MEDIA_EVIDENCE_OUTCOME_LABELS[evidence.outcome] || "Not run")}</strong></div><div><span>${uiTextHtml("Execution")}</span><strong>${uiTextHtml(MEDIA_EVIDENCE_EXECUTION_LABELS[evidence.execution] || "Not run")}</strong></div><div><span>${uiTextHtml("Cleanup")}</span><strong>${uiTextHtml(cleanupLabel)}</strong></div><div><span>${uiTextHtml("Source overwrite")}</span><strong>${uiTextHtml(overwriteLabel)}</strong></div></div><div class="media-evidence-guidance" role="status"><div><span>${uiTextHtml("Reason")}</span><p>${escapeHtml(evidence.reason)}</p></div><div><span>${uiTextHtml("Next action")}</span><p>${escapeHtml(evidence.nextAction)}</p></div></div><div class="media-evidence-list" role="list">${operationRows}</div>${detail ? `<div class="media-evidence-generic" data-media-generic-status="${escapeHtml(evidence.genericStatus)}"><strong>${uiTextHtml("Generic Media actions remain Partial")}</strong><p>${uiTextHtml(evidence.genericReason)}</p><p>${uiTextHtml(evidence.genericNextAction)}</p></div>` : ""}<div class="media-evidence-actions">${action}</div></section>`;
};
const JOB_STATUS_RANK = Object.freeze({
  failed: 0,
  unavailable: 0,
  interrupted: 0,
  cancelled: 0,
  queued: 1,
  starting: 1,
  running: 1,
  cancelling: 1,
  completed: 2,
});
const textKey = (value) => String(value ?? "").trim().toLowerCase();
const jobRecoverySnapshot = (state) => {
  const source = state && typeof state === "object" ? state : {};
  const productization = source.productization && typeof source.productization === "object" ? source.productization : {};
  const productJobs = productization.jobs && typeof productization.jobs === "object" && !Array.isArray(productization.jobs) ? productization.jobs : {};
  const hasCanonical = Array.isArray(productJobs.records);
  const hotRecords = Array.isArray(source.jobs) ? source.jobs : [];
  const durableRecords = Array.isArray(source.durableJobs) ? source.durableJobs : [];
  const fallbackRecords = [
    ...hotRecords.map((item) => item && typeof item === "object" && !Array.isArray(item) ? { ...item, source: item.source === "durable" ? "durable" : "hot" } : {}),
    ...durableRecords.map((item) => item && typeof item === "object" && !Array.isArray(item) ? { ...item, source: "durable" } : {}),
  ];
  const rawRecords = hasCanonical ? productJobs.records : fallbackRecords;
  const hotDetails = new Map();
  hotRecords.forEach((item) => {
    const id = safeJobId(item?.id);
    const detail = safeHotJobDetail(item);
    const displayId = safeUiText(item?.id, "");
    if (id && detail) hotDetails.set(id, detail);
    if (displayId && detail) hotDetails.set(displayId, detail);
  });
  const candidates = rawRecords.slice(0, 500).flatMap((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const id = safeUiText(item.id, `job-${index + 1}`) || `job-${index + 1}`;
    const actionId = safeJobId(item.id);
    const jobSource = item.source === "durable" ? "durable" : "hot";
    const detail = jobSource === "hot" ? hotDetails.get(actionId) || hotDetails.get(id) : null;
    const durableArtifacts = jobSource === "durable" ? safeJobArtifacts(item.artifacts) : [];
    const durableProvenance = jobSource === "durable" ? safeJobProvenance(item.provenance) : [];
    const status = safeJobStatus(item.status);
    const reconstructOnlyPending = jobSource === "durable" && status === "queued" && (
      item.reconstruct_only_pending === true
      || item.retry_mode === "reconstruct_only"
      || item.execution === "not_run"
    );
    const active = ["queued", "starting", "running", "cancelling"].includes(status) && !reconstructOnlyPending;
    const durableLifecycle = jobSource === "durable" && item.lifecycle && typeof item.lifecycle === "object" && !Array.isArray(item.lifecycle)
      ? safeUiText(item.lifecycle.state || item.lifecycle_status, "")
      : safeUiText(item.lifecycle_status, "");
    const progress = Number.isInteger(item.progress) && item.progress >= 0 && item.progress <= 100 ? item.progress : 0;
    return [{
      id,
      actionId,
      tool: safeUiText(item.tool, "Job"),
      title: humanJobTitle(item.tool),
      jobType: safeUiIdentifier(item.tool, "hub_job"),
      source: jobSource,
      sourceLabel: jobSource === "durable" ? "Durable" : "Hot",
      status,
      active,
      reconstructOnlyPending,
      progress,
      resumable: item.resumable === true,
      retryMode: jobSource === "durable" ? "reconstruct_only" : "same_session",
      execution: jobSource === "durable" ? "not_run" : "",
      dryRun: jobSource === "durable" && item.dry_run === true,
      actualRetryExecution: jobSource === "durable" ? false : null,
      errorCode: safeUiIdentifier(item.error_code || (item.result && item.result.failure_code), ""),
      reason: safeUiText(item.reason || item.error || detail?.error || detail?.reason || detail?.message, "Chưa có nguyên nhân an toàn trong snapshot này."),
      nextAction: safeUiText(item.next_action, "Kiểm tra backend liên quan rồi tạo lại tác vụ nếu cần."),
      lifecycle: detail?.lifecycle || durableLifecycle,
      lifecycleNote: detail?.message || "",
      contractVersion: detail?.contractVersion || "",
      createdAt: detail?.createdAt || "",
      startedAt: detail?.startedAt || "",
      updatedAt: detail?.updatedAt || "",
      finishedAt: detail?.finishedAt || "",
      artifactsPublished: detail?.artifactsPublished === true || durableArtifacts.length > 0,
      artifacts: detail?.artifacts || durableArtifacts,
      provenance: detail?.provenance || durableProvenance,
    }];
  });
  const candidateKey = (item) => `${textKey(item.source)}|${textKey(item.id)}|${textKey(item.status)}|${textKey(item.tool)}`;
  const orderedCandidates = [...candidates].sort((left, right) => {
    const leftKey = candidateKey(left);
    const rightKey = candidateKey(right);
    return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
  });
  const seen = new Set();
  const records = orderedCandidates.filter((item) => {
    const key = `${item.source}|${item.actionId || item.id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((left, right) => {
    const leftRank = Object.prototype.hasOwnProperty.call(JOB_STATUS_RANK, left.status) ? JOB_STATUS_RANK[left.status] : 3;
    const rightRank = Object.prototype.hasOwnProperty.call(JOB_STATUS_RANK, right.status) ? JOB_STATUS_RANK[right.status] : 3;
    if (leftRank !== rightRank) return leftRank - rightRank;
    const leftKey = `${textKey(left.id)}|${textKey(left.source)}`;
    const rightKey = `${textKey(right.id)}|${textKey(right.source)}`;
    return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
  });
  const derivedCounts = {
    active: records.filter((item) => item.active === true).length,
    attention: records.filter((item) => ["failed", "unavailable", "cancelled", "interrupted"].includes(item.status)).length,
    interrupted: records.filter((item) => item.status === "interrupted").length,
    recoverable: records.filter((item) => item.resumable === true).length,
    total: records.length,
  };
  const publishedCounts = productJobs.counts && typeof productJobs.counts === "object" && !Array.isArray(productJobs.counts) ? productJobs.counts : {};
  const counts = Object.fromEntries(Object.keys(derivedCounts).map((key) => [key, key === "active" ? derivedCounts.active : safeJobCount(publishedCounts[key], derivedCounts[key])]));
  const fallbackStatus = counts.attention > 0 ? "partial" : "ready";
  return {
    source: hasCanonical ? "productization.jobs" : "legacy job snapshot fallback",
    status: readinessStatus(productJobs.status, fallbackStatus),
    execution: ["not_run", "dry_run"].includes(productJobs.execution) ? productJobs.execution : "not_published",
    dryRun: productJobs.dry_run === true,
    counts,
    records,
    reason: safeUiText(productJobs.reason, counts.attention > 0 ? "The server recovery snapshot has jobs that need review." : "No recovery attention is published in this snapshot."),
    nextAction: safeUiText(productJobs.next_action, "Open Jobs to review the server-owned recovery state."),
  };
};
const readinessModuleDetails = (modules) => modules.length
  ? modules.map((item) => `<article class="readiness-module" data-status="${escapeHtml(item.status)}" role="listitem">${statusExplanation({ name: item.label, technicalId: item.id, purpose: item.purpose || `${item.kind || "Server-owned"} capability`, status: item.status, reason: item.reason, impact: item.impact, nextAction: item.nextAction })}</article>`).join("")
  : `<div class="empty-state compact"><strong>Chưa có dòng module</strong><span>Snapshot server chưa công bố projection module an toàn; không có lỗi runtime nào được suy đoán.</span></div>`;
const readinessFitLabel = (fit) => fit === true ? "Fit" : fit === false ? "No fit" : "Unknown";
const readinessGpuLabel = (value) => {
  const candidate = String(value || "");
  if (!candidate || candidate === "unassigned" || candidate === "unknown") return "Chưa cần gán tài nguyên riêng";
  return candidate;
};
const readinessFitExplanation = (kind, fit) => {
  if (kind === "Physical fit") return fit === true ? "GPU đáp ứng yêu cầu VRAM đã khai báo." : fit === false ? "Không có GPU phù hợp với yêu cầu vật lý." : "Chưa có đủ snapshot phần cứng để kết luận.";
  return fit === true ? "Có thể xếp cùng chế độ đã chọn trong snapshot preflight." : fit === false ? "Không đủ tài nguyên để chạy song song; cân nhắc chế độ tuần tự." : "Chưa có đủ dữ liệu để kết luận khả năng chạy đồng thời.";
};
const RESOURCE_ERROR_COPY = Object.freeze({
  hardware_shape: "Snapshot phần cứng không đúng cấu trúc an toàn.",
  hardware_gpu_invalid: "Thông tin GPU trong snapshot không hợp lệ.",
  module_request_invalid: "Yêu cầu tài nguyên của module không hợp lệ.",
  resource_hint_invalid: "Gợi ý CPU/RAM/đĩa của module không hợp lệ.",
  mode_invalid: "Chế độ preflight không được hỗ trợ.",
});
const readinessResourceDetails = (resource) => {
  const target = resource.targetGpu;
  const targetText = target
    ? `${target.vendor} · ${target.deviceClass} · ${target.model}${target.vramMb == null ? "" : ` · ${target.vramMb} MB VRAM`}`
    : "Chưa công bố GPU mục tiêu";
  const modeText = resource.mode === "parallel" ? "Song song — các module có thể dùng chung snapshot tài nguyên nếu còn đủ VRAM." : resource.mode === "serial" ? "Tuần tự — mỗi module được đánh giá lần lượt, không cộng dồn tải." : "Chưa công bố chế độ.";
  const fitRows = [
    ...resource.physical.map((item) => ({ ...item, kind: "Physical fit", fit: item.fit })),
    ...resource.concurrent.map((item) => ({ ...item, kind: "Concurrent fit", fit: item.fit })),
  ];
  const rows = fitRows.length
    ? fitRows.map((item) => `<div class="readiness-resource-row"><span><strong>${uiTextHtml(item.kind)}</strong> · ${escapeHtml(item.id)}<small>${escapeHtml(readinessFitExplanation(item.kind, item.fit))}</small></span><span>${escapeHtml(readinessGpuLabel(item.gpu))} · ${uiTextHtml(readinessFitLabel(item.fit))}</span></div>`).join("")
    : `<div class="empty-state compact"><span>${uiTextHtml("No per-module resource fit was published.")}</span></div>`;
  const errors = resource.errors.length
    ? `<div class="readiness-resource-errors"><strong>${uiTextHtml("Resource notes")}</strong>${resource.errors.map((item) => `<span>${escapeHtml(RESOURCE_ERROR_COPY[item.code] || "Không thể đánh giá một mục tài nguyên.")} <code>${escapeHtml(item.code)}</code>${item.module ? ` · ${escapeHtml(item.module)}` : ""}</span>`).join("")}</div>`
    : "";
  const actions = resource.actions.length
    ? `<div class="readiness-guidance"><div><span>${uiTextHtml("Next safe action")}</span><p>${escapeHtml(resource.actions[0])}</p></div></div>`
    : "";
  return `<section class="readiness-resource card card--flat" aria-labelledby="readiness-resource-title" data-resource-plan-status="${escapeHtml(resource.status)}"><div class="card-title-row"><div><span class="eyebrow">${uiTextHtml("RESOURCE PREFLIGHT")}</span><h2 id="readiness-resource-title">${uiTextHtml("Dry-run resource fit")}</h2><p class="small">${uiTextHtml("Đây là đánh giá lập kế hoạch, không phải chạy provider/model/GPU.")}</p></div>${statusPill(resource.status, readinessStatusLabel(resource.status))}</div><div class="readiness-resource-summary"><div><span>${uiTextHtml("Mode")}</span><strong>${escapeHtml(resource.mode)}</strong><small>${escapeHtml(modeText)}</small></div><div><span>${uiTextHtml("Target")}</span><strong>${escapeHtml(targetText)}</strong><small>${uiTextHtml("GPU mục tiêu và VRAM được lấy từ snapshot server-owned.")}</small></div><div><span>${uiTextHtml("Source")}</span><strong>${uiTextHtml("Server-owned")}</strong><small>${uiTextHtml("Không truy cập provider hoặc tải model.")}</small></div></div><div class="readiness-resource-list">${rows}</div>${errors}${actions}<p class="small muted">${uiTextHtml("Resource fit is planning evidence only; no provider, install, repair, uninstall, GPU or media operation ran.")}</p></section>`;
};
const readinessStorageDetails = (volumes) => `<section class="readiness-storage card card--flat" aria-labelledby="readiness-storage-title"><div class="card-title-row"><div><span class="eyebrow">STORAGE CONSTRAINTS</span><h2 id="readiness-storage-title">Allowlisted volume constraints</h2></div><span class="tag">C: / D:</span></div><div class="readiness-storage-grid">${volumes.map((volume) => {
  const value = (key) => {
    if (!volume.available) return "\u2014";
    const size = escapeHtml(formatGb(volume[`${key}Bytes`]));
    return volume.status === "partial" ? `${uiTextHtml("At least")} ${size} ${uiTextHtml("— not fully scanned")}` : size;
  };
  return `<article class="readiness-storage-item" data-volume-id="${escapeHtml(volume.id)}" data-status="${escapeHtml(volume.status)}"><div class="readiness-module__head"><div><h3>${escapeHtml(volume.label)}</h3><p>${uiTextHtml("Server-owned snapshot")}</p></div>${statusPill(volume.status, readinessStatusLabel(volume.status))}</div><div class="readiness-storage-values"><div><span>${uiTextHtml("Total")}</span><strong>${value("total")}</strong></div><div><span>${uiTextHtml("Free")}</span><strong>${value("free")}</strong></div><div><span>${uiTextHtml("Used")}</span><strong>${value("used")}</strong></div></div><p>${escapeHtml(volume.reason)}</p><div class="readiness-guidance"><div><span>${uiTextHtml(volume.lowSpace ? "Low-space action" : "Next action")}</span><p>${escapeHtml(volume.nextAction)}</p></div></div></article>`;
}).join("")}</div></section>`;
const heading = (eyebrow, title, description, actions = "") => `
  <header class="page-heading"><div class="heading-copy"><div class="eyebrow" data-i18n="${escapeHtml(eyebrow)}">${uiTextHtml(eyebrow)}</div><h1 data-i18n="${escapeHtml(title)}">${uiTextHtml(title)}</h1><p data-i18n="${escapeHtml(description)}">${uiTextHtml(description)}</p></div><div class="heading-actions">${actions}</div></header>`;
const card = (title, content, action = "", extra = "") => `<section class="card ${extra}"><div class="card-title-row"><h2>${uiTextHtml(title)}</h2>${action}</div>${content}</section>`;
const cardDynamic = (title, content, action = "", extra = "") => `<section class="card ${extra}"><div class="card-title-row"><h2>${dynamicTextHtml(title)}</h2>${action}</div>${content}</section>`;
const field = (label, control, extra = "") => `<label class="field ${extra}"><span>${uiTextHtml(label)}</span>${control}</label>`;
const fieldDynamic = (label, control, extra = "") => `<label class="field ${extra}"><span>${dynamicTextHtml(label)}</span>${control}</label>`;
const file = (label, key, accept = "") => field(label, `<input type="file" data-asset-key="${escapeHtml(key)}" ${accept ? `accept="${escapeHtml(accept)}"` : ""} /><div class="file-preview" data-file-preview aria-live="polite"></div>`);
const files = (label, key, accept = "") => field(label, `<input type="file" data-asset-key="${escapeHtml(key)}" multiple ${accept ? `accept="${escapeHtml(accept)}"` : ""} /><div class="file-preview" data-file-preview aria-live="polite"></div>`);
const button = (text, extra = "") => `<button class="button ${extra}" type="submit">${uiTextHtml(text)}</button>`;
const capability = (name, item, note, direct = {}) => `<div class="capability"><div><strong>${uiTextHtml(name)}</strong><p>${uiTextHtml(note)}</p>${direct.reason ? `<p>${escapeHtml(direct.reason)}</p>` : ""}${direct.action ? `<p class="capability-action"><strong>Bước tiếp theo:</strong> ${escapeHtml(direct.action)}</p>` : ""}</div>${statusPill(direct.tool_status || item.component_status || item.status || "missing")}</div>`;
const workspaceState = (label, item = {}, fallbackAction = "Kiểm tra backend rồi thử lại trong Jobs.") => {
  const status = item.tool_status || item.status || item.component_status || "missing";
  const reason = item.reason || "Chưa có snapshot readiness cho backend này.";
  const action = item.action || fallbackAction;
  return `<section class="workspace-state" data-status="${escapeHtml(status)}" aria-live="polite"><div class="workspace-state__head"><div><span class="eyebrow" data-i18n="BACKEND CONTRACT">${uiTextHtml("BACKEND CONTRACT")}</span><h2>${dynamicTextHtml(label)}</h2></div>${statusPill(status)}</div><p>${escapeHtml(reason)}</p><div class="workspace-state__action"><strong data-i18n="Bước tiếp theo">${uiTextHtml("Bước tiếp theo")}</strong><span>${escapeHtml(action)}</span></div></section>`;
};
const workflowLibraryState = (item = {}) => {
  const status = String(item.status || "partial");
  const reason = String(item.reason || "Workflow Library server-owned adapter chưa được V5-D wire.");
  const action = String(item.action || "Tiếp tục local draft; xác nhận endpoint typed trước khi đồng bộ.");
  return '<section class="workflow-library-state card card--flat" data-status="' + escapeHtml(status) + '" aria-labelledby="workflow-library-title"><div class="card-title-row"><div><span class="eyebrow" data-i18n="WORKFLOW LIBRARY">' + uiTextHtml("WORKFLOW LIBRARY") + '</span><h2 id="workflow-library-title" data-i18n="Workflow bền vững, local-first">' + uiTextHtml("Workflow bền vững, local-first") + '</h2></div>' + statusPill(status) + '</div><p>' + escapeHtml(reason) + '</p><div class="workspace-state__action"><strong data-i18n="Bước tiếp theo">' + uiTextHtml("Bước tiếp theo") + '</strong><span>' + escapeHtml(action) + '</span></div><small class="small" data-i18n="Graph chỉ là declarative metadata; không tự chạy node hoặc GPU khi chỉnh sửa.">' + uiTextHtml("Graph chỉ là declarative metadata; không tự chạy node hoặc GPU khi chỉnh sửa.") + '</small></section>';
};
const activeTab = (state, module) => state.workspaceTabs?.[module] || "quick";
const moduleTabs = (state, module) => {
  const selected = activeTab(state, module);
  return `<div class="module-tabs" role="tablist" aria-label="${escapeHtml(module)} workspace"><button class="tab ${selected === "quick" ? "is-selected" : ""}" type="button" data-workspace-tab="${escapeHtml(module)}:quick" role="tab" aria-selected="${selected === "quick"}" data-i18n="Quick">${uiTextHtml("Quick")}</button><button class="tab ${selected === "nodes" ? "is-selected" : ""}" type="button" data-workspace-tab="${escapeHtml(module)}:nodes" role="tab" aria-selected="${selected === "nodes"}" data-i18n="Nodes">${uiTextHtml("Nodes")}</button></div>`;
};
const imageModuleTabs = (state) => {
  const selected = activeTab(state, "image");
  return `<div class="module-tabs" role="tablist" aria-label="Image AI workspace"><button class="tab ${selected === "quick" ? "is-selected" : ""}" type="button" data-workspace-tab="image:quick" role="tab" aria-selected="${selected === "quick"}" data-i18n="Quick">${uiTextHtml("Quick")}</button><button class="tab ${selected === "studio" ? "is-selected" : ""}" type="button" data-workspace-tab="image:studio" role="tab" aria-selected="${selected === "studio"}" data-i18n="Chỉnh sửa & Mask">${uiTextHtml("Chỉnh sửa & Mask")}</button><button class="tab ${selected === "nodes" ? "is-selected" : ""}" type="button" data-workspace-tab="image:nodes" role="tab" aria-selected="${selected === "nodes"}" data-i18n="Hub Nodes">${uiTextHtml("Hub Nodes")}</button><button class="tab ${selected === "advanced" ? "is-selected" : ""}" type="button" data-workspace-tab="image:advanced" role="tab" aria-selected="${selected === "advanced"}" data-i18n="ComfyUI Advanced">${uiTextHtml("ComfyUI Advanced")}</button></div>`;
};
const nodeStudio = (scope, description) => `<section class="node-studio-wrap"><div class="node-studio-intro"><div><strong>Node Studio</strong><p>${escapeHtml(description)}</p></div><span class="tag">offline · typed sockets · DAG</span></div><div data-node-studio data-scope="${escapeHtml(scope)}"></div></section>`;
const imageWorkflowRail = (state) => {
  const generation = tool(state, "generate_flux");
  const edit = tool(state, "generate_qwen_image");
  const transform = tool(state, "run_media_operation");
  return `<section class="image-workflow-rail" aria-label="Image workflow">
    <div class="image-workflow-rail__intro"><div><span class="eyebrow">${uiTextHtml("IMAGE WORKFLOW")}</span><h2>Luồng xử lý ảnh chuẩn</h2><p>Chuỗi node chuẩn: <b>${uiTextHtml("Prompt / Input")}</b> → <b>${uiTextHtml("Generate / Edit / Upscale / Mask")}</b> → <b>${uiTextHtml("Preview")}</b> → <b>${uiTextHtml("Save")}</b>. Trạng thái backend luôn phản ánh trung thực.</p></div><span class="tag">image · mask · DAG</span></div>
    <div class="image-workflow-rail__steps">
      <article><span>01</span><strong>${uiTextHtml("Prompt / Input")}</strong><small>Text prompt hoặc ảnh đầu vào</small>${statusPill("operational")}<button class="button button--compact" type="button" data-workspace-tab="image:nodes">Mở Hub Nodes</button></article>
      <article><span>02</span><strong>${uiTextHtml("Generate / Edit / Mask")}</strong><small>FLUX / Qwen / SAM2 / Upscale</small>${statusPill(generation.tool_status || edit.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="image:nodes">Mở template</button></article>
      <article><span>03</span><strong>${uiTextHtml("Preview & Save")}</strong><small>Xem preview an toàn và xuất artifact</small>${statusPill("operational")}<button class="button button--compact" type="button" data-workspace-tab="image:nodes">Xem canvas</button></article>
    </div>
  </section>`;
};
const videoWorkflowRail = (state, scope = "media") => {
  const transform = tool(state, "run_media_operation");
  const upscale = tool(state, "upscale_anime_video");
  return `<section class="video-workflow-rail" aria-label="Video workflow">
    <div class="video-workflow-rail__intro"><div><span class="eyebrow">${uiTextHtml("VIDEO WORKFLOW")}</span><h2>Luồng xử lý video chuẩn</h2><p>Chuỗi pipeline chuẩn: <b>${uiTextHtml("Load Video")}</b> → <b>${uiTextHtml("Transform")}</b> → <b>${uiTextHtml("Upscale")}</b> → <b>RIFE</b> → <b>${uiTextHtml("Grade")}</b> → <b>${uiTextHtml("Subtitle / Logo")}</b> → <b>${uiTextHtml("Audio")}</b> → <b>${uiTextHtml("Encode")}</b> → <b>${uiTextHtml("Preview")}</b> → <b>${uiTextHtml("Save")}</b>.</p></div><span class="tag">video · streaming · DAG</span></div>
    <div class="video-workflow-rail__steps">
      <article><span>01</span><strong>${uiTextHtml("Load & Transform")}</strong><small>Đầu vào video và tiền xử lý FFmpeg</small>${statusPill(transform.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="${scope}:nodes">Mở template</button></article>
      <article><span>02</span><strong>${uiTextHtml("Upscale & RIFE & Grade")}</strong><small>AnimeSR · Nội suy FPS · ${uiTextHtml("Color grade")}</small>${statusPill(upscale.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="${scope}:nodes">Mở template</button></article>
      <article><span>03</span><strong>${uiTextHtml("Subtitle, Encode & Save")}</strong><small>Chèn phụ đề / Logo · ${uiTextHtml("Encode")} · Xuất file</small>${statusPill(transform.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="${scope}:nodes">Xem pipeline</button></article>
    </div>
  </section>`;
};
const visionWorkflowRail = (state) => {
  const parse = tool(state, "parse_screen");
  const detect = tool(state, "detect_objects");
  const ocr = tool(state, "ocr_document");
  return `<section class="image-workflow-rail" aria-label="Vision workflow" style="background:linear-gradient(135deg, rgba(66,198,160,.1), transparent 48%), var(--panel)">
    <div class="image-workflow-rail__intro"><div><span class="eyebrow">${uiTextHtml("VISION WORKFLOW")}</span><h2>Luồng phân tích thị giác chuẩn</h2><p>Chuỗi thao tác chuẩn: <b>${uiTextHtml("Load")}</b> → <b>${uiTextHtml("Detect / Ground / Segment / OCR")}</b> → <b>${uiTextHtml("Preview / Export")}</b>. Kết quả JSON và mask bounding box xem trực tiếp.</p></div><span class="tag">vision · bbox · OCR</span></div>
    <div class="image-workflow-rail__steps">
      <article><span>01</span><strong>${uiTextHtml("Load Input")}</strong><small>Tải ảnh, tài liệu hoặc screenshot</small>${statusPill("operational")}<button class="button button--compact" type="button" data-route="vision">Mở Vision Studio</button></article>
      <article><span>02</span><strong>${uiTextHtml("Detect / Segment / OCR")}</strong><small>OmniParser · RF-DETR · DINO · PaddleOCR</small>${statusPill(parse.tool_status || detect.tool_status || ocr.tool_status || "partial")}<button class="button button--compact" type="button" data-route="vision">Xem modules</button></article>
      <article><span>03</span><strong>${uiTextHtml("Preview & Export")}</strong><small>Xem JSON annotation và trích xuất text</small>${statusPill("operational")}<button class="button button--compact" type="button" data-route="jobs">Xem Jobs</button></article>
    </div>
  </section>`;
};

function artifacts(value, found = []) {
  if (!value) return found;
  if (Array.isArray(value)) value.forEach((item) => artifacts(item, found));
  else if (typeof value === "object") {
    const id = value.id || value.artifact_id;
    if (id && value.url && !found.some((item) => item.id === id)) found.push({ ...value, id });
    Object.values(value).forEach((item) => artifacts(item, found));
  }
  return found;
}

const legacyArtifactList = (value) => {
  const items = artifacts(value);
  if (!items.length) return "";
  return `<div class="artifact-list">${items.map((item) => {
    const isImage = String(item.media_type || "").startsWith("image/");
    const isMedia = /^(image|audio|video)\//.test(String(item.media_type || ""));
    return `<div class="artifact-item">${isImage ? `<img class="artifact-preview" src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}" />` : ""}<div class="row-main"><div class="row-name">${escapeHtml(item.name)}</div><div class="row-meta">${escapeHtml(item.media_type || "artifact")} · ${formatGb(item.size_bytes)}</div></div>${isMedia ? `<button class="button button--compact" type="button" data-focus-key="artifact-preview-opener" data-preview-artifact="${escapeHtml(item.id)}" data-artifact-url="${escapeHtml(item.url)}" data-artifact-name="${escapeHtml(item.name)}" data-artifact-type="${escapeHtml(item.media_type || "application/octet-stream")}">Xem</button>` : ""}<a class="button button--compact" href="${escapeHtml(item.url)}" download="${escapeHtml(item.name || "artifact")}">Lưu/Xuất</a><button class="button button--compact" type="button" data-open-artifact="${escapeHtml(item.id)}">Mở</button></div>`;
  }).join("")}</div>`;
};

const artifactList = (value) => {
  const items = safeJobArtifacts(Array.isArray(value) ? value : value?.artifacts);
  if (!items.length) return "";
  return `<div class="artifact-list">${items.map((item) => {
    const name = safeArtifactName(item.name);
    const url = opaqueArtifactUrl(item.url);
    const id = opaqueArtifactId(item.id) || url.split("/").pop() || "";
    const mediaType = String(item.media_type || "application/octet-stream").split(";", 1)[0].trim().toLowerCase();
    const isImage = mediaType.startsWith("image/");
    const mask = isMaskArtifact(item, name);
    const previewButton = url ? `<button class="button button--compact" type="button" data-focus-key="artifact-preview-opener" data-preview-artifact="${escapeHtml(id)}" data-artifact-url="${escapeHtml(url)}" data-artifact-name="${escapeHtml(name)}" data-artifact-type="${escapeHtml(mediaType)}" data-artifact-mask="${mask}" data-artifact-meta="${escapeHtml(artifactMetadata(item))}" data-artifact-provenance="${escapeHtml(artifactProvenance(item))}" aria-label="Artifact preview">Xem</button>` : `<span class="artifact-unavailable">Preview unavailable</span>`;
    const saveLink = url ? `<a class="button button--compact" href="${escapeHtml(url)}" download="${escapeHtml(name)}">Lưu/Xuất</a>` : "";
    const openButton = id ? `<button class="button button--compact" type="button" data-open-artifact="${escapeHtml(id)}">Mở</button>` : "";
    return `<div class="artifact-item">${isImage && url ? `<img class="artifact-preview${mask ? " artifact-preview--mask" : ""}" src="${escapeHtml(url)}" alt="${escapeHtml(mask ? `Mask raster: ${name}` : name)}" />` : ""}<div class="row-main"><div class="row-name">${escapeHtml(name)}</div><div class="row-meta">${escapeHtml(mediaType)} · ${formatGb(item.size_bytes)}${mask ? " · mask" : ""}</div></div>${previewButton}${saveLink}${openButton}</div>`;
  }).join("")}</div>`;
};

const provenanceList = (job) => {
  const items = Array.isArray(job) ? job : Array.isArray(job?.provenance) ? job.provenance : [];
  if (!Array.isArray(items) || !items.length) return "";
  return `<details class="job-provenance"><summary>Provenance · ${items.length} artifact</summary><div class="tag-list">${items.map((item) => `<span class="tag">${escapeHtml(safeUiIdentifier(item?.node_type, "node"))} → ${escapeHtml(safeArtifactName(typeof item?.name === "string" ? item.name : typeof item?.artifact_id === "string" ? item.artifact_id : "Artifact"))}</span>`).join("")}</div></details>`;
};

const formResult = (id) => `<div class="form-result" id="${escapeHtml(id)}" role="status" aria-live="polite"></div>`;

export { escapeHtml, formatGb, formatStatus, translateText, uiText, uiTextHtml, dynamicTextHtml, component, tool, app, opaqueArtifactId, opaqueArtifactUrl, safeArtifactName, artifactMetadata, artifactProvenance, isMaskArtifact, READINESS_STATUS_LABELS, STATUS_SEVERITY, UI_STATUS_RE, uiStatus, readinessStatus, readinessStatusLabel, statusSeverity, statusMeaning, statusImpact, statusPill, statusExplanation, unsafeUiText, safeUiText, safeUiIdentifier, safeJobId, safeJobStatus, safeJobTimestamp, safeJobCount, humanJobTitle, safeArtifactMediaType, safeJobArtifacts, safeJobProvenance, safeHotJobDetail, safeReadinessModules, safeStorageVolumes, safeResourceGpu, safeResourceFits, safeResourceErrors, safeResourceActions, safeResourcePlan, readinessSnapshot, MEDIA_EVIDENCE_OPERATIONS, MEDIA_EVIDENCE_LABELS, MEDIA_EVIDENCE_OUTCOME_LABELS, MEDIA_EVIDENCE_EXECUTION_LABELS, unsafeMediaEvidenceText, isMediaEvidenceRecord, exactMediaOperationList, safeMediaEvidenceText, mediaEvidenceFallback, normalizeRuntimeMediaEvidence, normalizeMediaOperationScope, mediaCapabilityEvidence, mediaEvidencePanel, JOB_STATUS_RANK, textKey, jobRecoverySnapshot, readinessModuleDetails, readinessFitLabel, readinessResourceDetails, readinessStorageDetails, heading, card, cardDynamic, field, fieldDynamic, file, files, button, capability, workspaceState, workflowLibraryState, activeTab, moduleTabs, imageModuleTabs, nodeStudio, imageWorkflowRail, videoWorkflowRail, visionWorkflowRail, artifacts, legacyArtifactList, artifactList, provenanceList, formResult };
