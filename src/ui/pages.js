import { escapeHtml, formatGb, formatStatus } from "./api.js";
import { translateText } from "./i18n.js";

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
  { group: "HỆ THỐNG", items: [["jobs", "Jobs", "≡"], ["models", "Models & Storage", "▦"], ["settings", "Settings", "⚙"]] },
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
const statusPill = (status) => {
  // Snapshot status values are data, not translation keys.  Collapse any
  // unknown-but-well-shaped value to the fixed Unknown label before it can
  // reach the translator.
  const normalized = readinessStatus(status);
  const label = READINESS_STATUS_LABELS[normalized] || READINESS_STATUS_LABELS.unknown;
  return `<span class="status-pill" data-status="${escapeHtml(normalized)}">${uiTextHtml(label)}</span>`;
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
      reason: safeUiText(record.reason, "No additional reason was published in this server snapshot."),
      nextAction: safeUiText(record.next_action, "Review the server-owned evidence before runtime work."),
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
export const mediaCapabilityEvidence = (state) => {
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
    ? `<button class="button button--compact" type="button" data-readiness-route="settings"><strong>Review detailed evidence</strong><span class="row-meta">Open server snapshot in Settings</span></button>`
    : variant === "settings"
      ? `<button class="button button--compact" type="button" data-route="media"><strong>Open Media / Node Studio</strong><span class="row-meta">View exact operation scope</span></button>`
      : "";
  const operationRows = evidence.operations.map((item) => `<article class="media-evidence-row" data-media-operation="${escapeHtml(item.id)}" data-operation-status="${escapeHtml(item.status)}"><div><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.reason)}</span></div>${statusPill(item.status, readinessStatusLabel(item.status))}<p><strong>Next action</strong> ${escapeHtml(item.nextAction)}</p></article>`).join("");
  const cleanupLabel = evidence.cleanup.processesRemaining === 0 && evidence.cleanup.tempCleaned ? "Clean" : "Needs review";
  const overwriteLabel = evidence.sourceOverwriteChecked ? (evidence.sourceOverwritten ? "Overwrite detected" : "Source preserved") : "Not checked";
  return `<section class="media-evidence card card--flat" aria-labelledby="media-evidence-title-${escapeHtml(variant)}" data-media-evidence data-media-evidence-status="${escapeHtml(evidence.status)}" data-media-evidence-outcome="${escapeHtml(evidence.outcome)}" data-media-evidence-execution="${escapeHtml(evidence.execution)}" data-media-evidence-verified="${String(evidence.evidenceVerified)}"><div class="card-title-row"><div><span class="eyebrow">MEDIA CAPABILITY EVIDENCE</span><h2 id="media-evidence-title-${escapeHtml(variant)}">Exact media operation scope</h2><p>Server snapshot only; the UI does not execute media operations.</p></div>${statusPill(evidence.status, readinessStatusLabel(evidence.status))}</div><div class="media-evidence-summary"><div><span>Outcome</span><strong>${escapeHtml(MEDIA_EVIDENCE_OUTCOME_LABELS[evidence.outcome] || "Not run")}</strong></div><div><span>Execution</span><strong>${escapeHtml(MEDIA_EVIDENCE_EXECUTION_LABELS[evidence.execution] || "Not run")}</strong></div><div><span>Cleanup</span><strong>${escapeHtml(cleanupLabel)}</strong></div><div><span>Source overwrite</span><strong>${escapeHtml(overwriteLabel)}</strong></div></div><div class="media-evidence-guidance" role="status"><div><span>Reason</span><p>${escapeHtml(evidence.reason)}</p></div><div><span>Next action</span><p>${escapeHtml(evidence.nextAction)}</p></div></div><div class="media-evidence-list" role="list">${operationRows}</div>${detail ? `<div class="media-evidence-generic" data-media-generic-status="${escapeHtml(evidence.genericStatus)}"><strong>Generic Media actions remain ${escapeHtml(readinessStatusLabel(evidence.genericStatus))}</strong><p>${escapeHtml(evidence.genericReason)}</p><p>${escapeHtml(evidence.genericNextAction)}</p></div>` : ""}<div class="media-evidence-actions">${action}</div></section>`;
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
export const jobRecoverySnapshot = (state) => {
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
    const status = safeJobStatus(item.status);
    const progress = Number.isInteger(item.progress) && item.progress >= 0 && item.progress <= 100 ? item.progress : 0;
    return [{
      id,
      actionId,
      tool: safeUiText(item.tool, "Job"),
      source: jobSource,
      sourceLabel: jobSource === "durable" ? "Durable" : "Hot",
      status,
      progress,
      resumable: item.resumable === true,
      reason: safeUiText(item.reason),
      nextAction: safeUiText(item.next_action, "Review the job state and create a new task when recovery is unavailable."),
      lifecycle: detail?.lifecycle || "",
      lifecycleNote: detail?.message || "",
      contractVersion: detail?.contractVersion || "",
      createdAt: detail?.createdAt || "",
      startedAt: detail?.startedAt || "",
      updatedAt: detail?.updatedAt || "",
      finishedAt: detail?.finishedAt || "",
      artifactsPublished: detail?.artifactsPublished === true,
      artifacts: detail?.artifacts || [],
      provenance: detail?.provenance || [],
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
    active: records.filter((item) => ["queued", "starting", "running", "cancelling"].includes(item.status)).length,
    attention: records.filter((item) => ["failed", "unavailable", "cancelled", "interrupted"].includes(item.status)).length,
    interrupted: records.filter((item) => item.status === "interrupted").length,
    recoverable: records.filter((item) => item.resumable === true).length,
    total: records.length,
  };
  const publishedCounts = productJobs.counts && typeof productJobs.counts === "object" && !Array.isArray(productJobs.counts) ? productJobs.counts : {};
  const counts = Object.fromEntries(Object.keys(derivedCounts).map((key) => [key, safeJobCount(publishedCounts[key], derivedCounts[key])]));
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
  ? modules.map((item) => `<article class="readiness-module" data-status="${escapeHtml(item.status)}" role="listitem"><div class="readiness-module__head"><div><h3>${escapeHtml(item.label)}</h3><p>${escapeHtml(item.kind)}${item.version ? ` · ${escapeHtml(item.version)}` : ""}</p></div>${statusPill(item.status, readinessStatusLabel(item.status))}</div><div class="readiness-module__guidance"><div><span>Reason</span><p>${escapeHtml(item.reason)}</p></div><div><span>Next action</span><p>${escapeHtml(item.nextAction)}</p></div></div></article>`).join("")
  : `<div class="empty-state compact"><strong>No module rows published</strong><span>The server snapshot contains no safe module projection.</span></div>`;
const readinessFitLabel = (fit) => fit === true ? "Fit" : fit === false ? "No fit" : "Unknown";
const readinessResourceDetails = (resource) => {
  const target = resource.targetGpu;
  const targetText = target
    ? `${target.vendor} · ${target.deviceClass} · ${target.model}${target.vramMb == null ? "" : ` · ${target.vramMb} MB VRAM`}`
    : "No target GPU published";
  const fitRows = [
    ...resource.physical.map((item) => ({ ...item, kind: "Physical fit", fit: item.fit })),
    ...resource.concurrent.map((item) => ({ ...item, kind: "Concurrent fit", fit: item.fit })),
  ];
  const rows = fitRows.length
    ? fitRows.map((item) => `<div class="readiness-resource-row"><span>${escapeHtml(item.kind)} · ${escapeHtml(item.id)}</span><span>${escapeHtml(item.gpu)} · ${escapeHtml(readinessFitLabel(item.fit))}</span></div>`).join("")
    : `<div class="empty-state compact"><span>No per-module resource fit was published.</span></div>`;
  const errors = resource.errors.length
    ? `<div class="readiness-resource-errors"><strong>Resource notes</strong>${resource.errors.map((item) => `<span>${escapeHtml(item.code)}${item.module ? ` · ${escapeHtml(item.module)}` : ""}</span>`).join("")}</div>`
    : "";
  const actions = resource.actions.length
    ? `<div class="readiness-guidance"><div><span>Next safe action</span><p>${escapeHtml(resource.actions[0])}</p></div></div>`
    : "";
  return `<section class="readiness-resource card card--flat" aria-labelledby="readiness-resource-title" data-resource-plan-status="${escapeHtml(resource.status)}"><div class="card-title-row"><div><span class="eyebrow">RESOURCE PREFLIGHT</span><h2 id="readiness-resource-title">Dry-run resource fit</h2></div>${statusPill(resource.status, readinessStatusLabel(resource.status))}</div><div class="readiness-resource-summary"><div><span>Mode</span><strong>${escapeHtml(resource.mode)}</strong></div><div><span>Target</span><strong>${escapeHtml(targetText)}</strong></div><div><span>Source</span><strong>Server-owned</strong></div></div><div class="readiness-resource-list">${rows}</div>${errors}${actions}<p class="small muted">Resource fit is planning evidence only; no provider, install, repair, uninstall, GPU or media operation ran.</p></section>`;
};
const readinessStorageDetails = (volumes) => `<section class="readiness-storage card card--flat" aria-labelledby="readiness-storage-title"><div class="card-title-row"><div><span class="eyebrow">STORAGE CONSTRAINTS</span><h2 id="readiness-storage-title">Allowlisted volume constraints</h2></div><span class="tag">C: / D:</span></div><div class="readiness-storage-grid">${volumes.map((volume) => {
  const value = (key) => volume.available ? formatGb(volume[`${key}Bytes`]) : "\u2014";
  return `<article class="readiness-storage-item" data-volume-id="${escapeHtml(volume.id)}" data-status="${escapeHtml(volume.status)}"><div class="readiness-module__head"><div><h3>${escapeHtml(volume.label)}</h3><p>Server-owned snapshot</p></div>${statusPill(volume.status, readinessStatusLabel(volume.status))}</div><div class="readiness-storage-values"><div><span>Total</span><strong>${escapeHtml(value("total"))}</strong></div><div><span>Free</span><strong>${escapeHtml(value("free"))}</strong></div><div><span>Used</span><strong>${escapeHtml(value("used"))}</strong></div></div><p>${escapeHtml(volume.reason)}</p><div class="readiness-guidance"><div><span>${volume.lowSpace ? "Low-space action" : "Next action"}</span><p>${escapeHtml(volume.nextAction)}</p></div></div></article>`;
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
const capability = (name, item, note, direct = {}) => `<div class="capability"><div><strong>${escapeHtml(name)}</strong><p>${escapeHtml(note)}</p>${direct.reason ? `<p>${escapeHtml(direct.reason)}</p>` : ""}${direct.action ? `<p class="capability-action"><strong>Bước tiếp theo:</strong> ${escapeHtml(direct.action)}</p>` : ""}</div>${statusPill(direct.tool_status || item.component_status || item.status || "missing")}</div>`;
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
    <div class="image-workflow-rail__intro"><div><span class="eyebrow">IMAGE FOUNDATION</span><h2>Luồng ảnh end-to-end</h2><p>Chọn template trong Hub Nodes để nối Prompt → Generate/Edit → Upscale → Preview/Save. Mỗi backend hiển thị đúng partial/unavailable nếu chưa smoke.</p></div><span class="tag">artifact · job · provenance</span></div>
    <div class="image-workflow-rail__steps">
      <article><span>01</span><strong>Tạo ảnh</strong><small>FLUX / ComfyUI</small>${statusPill(generation.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="image:nodes">Mở template</button></article>
      <article><span>02</span><strong>Chỉnh ảnh</strong><small>Qwen Image-to-Image</small>${statusPill(edit.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="image:nodes">Mở template</button></article>
      <article><span>03</span><strong>Upscale</strong><small>FFmpeg fallback / AI partial</small>${statusPill(transform.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="image:nodes">Mở template</button></article>
    </div>
  </section>`;
};
const videoWorkflowRail = (state) => {
  const transform = tool(state, "run_media_operation");
  const upscale = tool(state, "upscale_anime_video");
  return `<section class="video-workflow-rail" aria-label="Video workflow">
    <div class="video-workflow-rail__intro"><div><span class="eyebrow">VIDEO CREATIVE</span><h2>Luồng video trong Hub</h2><p>Chọn template để nối video artifact hoặc prompt → transform/generation → upscale → interpolate → encode → preview/export.</p></div><span class="tag">job · progress · provenance</span></div>
    <div class="video-workflow-rail__steps">
      <article><span>01</span><strong>Transform</strong><small>FFmpeg allowlist</small>${statusPill(transform.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="media:nodes">Mở template</button></article>
      <article><span>02</span><strong>Upscale / FPS</strong><small>FFmpeg fallback · AnimeSR</small>${statusPill(upscale.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="media:nodes">Mở template</button></article>
      <article><span>03</span><strong>Prompt Generate</strong><small>Backend video chưa có</small>${statusPill("unavailable")}<button class="button button--compact" type="button" data-workspace-tab="media:nodes">Xem contract</button></article>
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
    const previewButton = url ? `<button class="button button--compact" type="button" data-focus-key="artifact-preview-opener" data-preview-artifact="${escapeHtml(id)}" data-artifact-url="${escapeHtml(url)}" data-artifact-name="${escapeHtml(name)}" data-artifact-type="${escapeHtml(mediaType)}" data-artifact-mask="${mask}" data-artifact-meta="${escapeHtml(artifactMetadata(item))}" data-artifact-provenance="${escapeHtml(artifactProvenance(item))}">Xem</button>` : `<span class="artifact-unavailable">Preview unavailable</span>`;
    const saveLink = url ? `<a class="button button--compact" href="${escapeHtml(url)}" download="${escapeHtml(name)}">LÆ°u/Xuáº¥t</a>` : "";
    const openButton = id ? `<button class="button button--compact" type="button" data-open-artifact="${escapeHtml(id)}">Má»Ÿ</button>` : "";
    return `<div class="artifact-item">${isImage && url ? `<img class="artifact-preview${mask ? " artifact-preview--mask" : ""}" src="${escapeHtml(url)}" alt="${escapeHtml(mask ? `Mask raster: ${name}` : name)}" />` : ""}<div class="row-main"><div class="row-name">${escapeHtml(name)}</div><div class="row-meta">${escapeHtml(mediaType)} Â· ${formatGb(item.size_bytes)}${mask ? " Â· mask" : ""}</div></div>${previewButton}${saveLink}${openButton}</div>`;
  }).join("")}</div>`;
};

const provenanceList = (job) => {
  const items = Array.isArray(job) ? job : Array.isArray(job?.provenance) ? job.provenance : [];
  if (!Array.isArray(items) || !items.length) return "";
  return `<details class="job-provenance"><summary>Provenance · ${items.length} artifact</summary><div class="tag-list">${items.map((item) => `<span class="tag">${escapeHtml(safeUiIdentifier(item?.node_type, "node"))} → ${escapeHtml(safeArtifactName(typeof item?.name === "string" ? item.name : typeof item?.artifact_id === "string" ? item.artifact_id : "Artifact"))}</span>`).join("")}</div></details>`;
};

const formResult = (id) => `<div class="form-result" id="${escapeHtml(id)}" role="status" aria-live="polite"></div>`;

function renderDashboard(state) {
  const source = state && typeof state === "object" ? state : {};
  const health = source.health && typeof source.health === "object" ? source.health : {};
  const disk = health.disk && typeof health.disk === "object" ? health.disk : {};
  const gpu = health.gpu && typeof health.gpu === "object" ? health.gpu : {};
  const productization = source.productization && typeof source.productization === "object" ? source.productization : {};
  const storage = source.storage && typeof source.storage === "object" ? source.storage : (productization.storage && typeof productization.storage === "object" ? productization.storage : {});
  const readinessView = readinessSnapshot(source);
  const volumes = readinessView.volumes;
  const jobRecovery = jobRecoverySnapshot(source);
  const jobs = jobRecovery.records;
  const components = Array.isArray(source.components) ? source.components : [];
  const componentFallback = components.map((item) => item);
  const control = source.capabilities && typeof source.capabilities === "object" ? source.capabilities : {};
  const readiness = readinessView.status !== "unknown" ? readinessView.status : readinessStatus(control.status || health.status);
  const activeJobs = jobRecovery.counts.active;
  const metricStatic = (label, value, detail) => `<article class="metric-card"><span data-i18n="${escapeHtml(label)}">${uiTextHtml(label)}</span><strong>${escapeHtml(value)}</strong><small data-i18n="${escapeHtml(detail)}">${uiTextHtml(detail)}</small></article>`;
  const metricSnapshot = (label, value, detail) => `<article class="metric-card"><span data-i18n="${escapeHtml(label)}">${uiTextHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`;
  const statusRank = { error: 0, unavailable: 0, missing: 0, not_published: 0, partial: 1, not_run: 1, planned: 2, starting: 3, installed: 4, operational: 5, healthy: 5, ready: 5, clean: 5 };
  const rankOf = (status) => Object.prototype.hasOwnProperty.call(statusRank, status) ? statusRank[status] : 3;
  const productCapabilities = productization.capabilities && typeof productization.capabilities === "object" ? productization.capabilities : {};
  const modules = Array.isArray(productCapabilities.modules) ? readinessView.modules : safeReadinessModules({ ...source, components: componentFallback });
  modules.sort((left, right) => {
    const rankDifference = rankOf(left.status) - rankOf(right.status);
    if (rankDifference) return rankDifference;
    const labelDifference = textKey(left.label) < textKey(right.label) ? -1 : textKey(left.label) > textKey(right.label) ? 1 : 0;
    if (labelDifference) return labelDifference;
    return textKey(left.id) < textKey(right.id) ? -1 : textKey(left.id) > textKey(right.id) ? 1 : 0;
  });
  const attention = [];
  if (readiness !== "healthy" && readiness !== "operational") {
    attention.push({ id: "hub-api", title: "Hub API", detail: "Readiness snapshot needs review", status: readiness });
  }
  modules.filter((item) => ["error", "unavailable", "missing", "partial", "not_published", "not_run"].includes(item.status)).forEach((item) => {
    attention.push({ id: `module-${item.id}`, title: item.label, detail: item.kind, status: item.status });
  });
  jobs.filter((item) => ["failed", "unavailable", "cancelled", "interrupted"].includes(String(item?.status || ""))).forEach((item, index) => {
    const jobId = item?.id || `job-${index + 1}`;
    attention.push({ id: `job-${jobId}`, title: jobId, detail: item?.reason || item?.lifecycleNote || "Job needs review", status: item?.status || "failed" });
  });
  attention.sort((left, right) => {
    const rankDifference = rankOf(left.status) - rankOf(right.status);
    if (rankDifference) return rankDifference;
    return textKey(left.title) < textKey(right.title) ? -1 : textKey(left.title) > textKey(right.title) ? 1 : 0;
  });
  const attentionItems = attention.slice(0, 4);
  const moduleRows = modules.length
    ? modules.slice(0, 12).map((item) => `<div class="dashboard-module-row"><div class="row-main"><strong>${escapeHtml(item.label)}</strong><span class="row-meta">${escapeHtml(item.kind)}${item.version ? ` · ${escapeHtml(item.version)}` : ""}</span></div>${statusPill(item.status, readinessStatusLabel(item.status))}</div>`).join("")
    : `<p class="small muted">Chưa có module trong readiness snapshot.</p>`;
  const attentionRows = attentionItems.length
    ? attentionItems.map((item) => `<div class="dashboard-module-row"><div class="row-main"><strong>${escapeHtml(item.title)}</strong><span class="row-meta">${escapeHtml(item.detail)}</span></div>${statusPill(item.status, readinessStatusLabel(item.status))}</div>`).join("")
    : `<p class="small muted">Không có hạng mục cần chú ý.</p>`;
  const moduleEvidenceRows = modules.length
    ? modules.slice(0, 12).map((item) => `<div class="dashboard-module-row"><div class="row-main"><strong>${escapeHtml(item.label)}</strong><span class="row-meta">${escapeHtml(item.reason)}</span><span class="row-meta">${escapeHtml(item.nextAction)}</span></div>${statusPill(item.status, readinessStatusLabel(item.status))}</div>`).join("")
    : `<p class="small muted">No server-owned module evidence.</p>`;
  const quickActions = [
    ["image", "Image AI", "Compose và chỉnh sửa ảnh"],
    ["media", "Media", "Transform media trong Hub"],
    ["jobs", "Jobs", "Theo dõi queue và artifact"],
    ["models", "Models & Storage", "Kiểm tra inventory"],
  ].map(([route, label, detail]) => `<button class="button button--compact" type="button" data-route="${escapeHtml(route)}"><strong>${escapeHtml(label)}</strong><span class="row-meta">${escapeHtml(detail)}</span></button>`).join("");
  const readinessAction = `<button class="button button--compact" type="button" data-readiness-route="settings"><strong>Readiness & Module Plan</strong><span class="row-meta">Review the server snapshot</span></button>`;
  const workflowSteps = [
    ["01", "Check readiness", "Review module health and attention."],
    ["02", "Choose a route", "Open an existing Hub workspace."],
    ["03", "Run from Jobs", "Keep progress and artifacts in Hub."],
  ].map(([step, title, detail]) => `<li class="dashboard-module-row"><span class="tag">${escapeHtml(step)}</span><div class="row-main"><strong>${escapeHtml(title)}</strong><span class="row-meta">${escapeHtml(detail)}</span></div></li>`).join("");
  const gpuValue = gpu.name || "Chưa phát hiện";
  const gpuDetail = gpu.memory_free_mib != null ? `${gpu.memory_free_mib} MiB VRAM trống` : "Snapshot GPU chưa sẵn sàng";
  const diskValue = disk.free_bytes != null ? formatGb(disk.free_bytes) : "—";
  const readinessNote = readiness === "healthy" || readiness === "operational" ? "Hub API snapshot ổn định; readiness của từng module vẫn được hiển thị riêng." : "Kiểm tra các mục cần chú ý trước khi chạy workflow.";
  const planStatus = readinessView.planStatus;
  const planReason = readinessView.planReason;
  const planAction = readinessView.planAction;
  const storageStatus = readinessStatus(storage.status, "unavailable");
  const storageExecution = readinessView.execution;
  const storageVolumeCard = (volume) => {
    const volumeId = String(volume?.id || "").toLowerCase();
    const label = volumeId === "c" ? "C:" : volumeId === "d" ? "D:" : "Volume";
    const status = readinessStatus(volume?.status, "unavailable");
    const available = volume?.available === true;
    const value = (key) => available ? formatGb(volume[`${key}Bytes`]) : "\u2014";
    const low = volume?.lowSpace === true;
    const reason = volume?.reason || "Volume statistics are unavailable; no figures are shown.";
    const action = volume?.nextAction || "Verify that the volume is mounted and readable, then refresh storage.";
    return `<article class="dashboard-storage-volume" data-volume-id="${escapeHtml(volumeId || "unknown")}" data-status="${escapeHtml(status)}" data-low-space="${low}"><div class="card-title-row"><div><span class="eyebrow">SERVER-OWNED VOLUME</span><h3>${escapeHtml(label)}</h3></div>${statusPill(status, status === "available" ? "Available" : "Unavailable")}</div><div class="dashboard-storage-values"><div><span>Total</span><strong>${escapeHtml(value("total"))}</strong></div><div><span>Free</span><strong>${escapeHtml(value("free"))}</strong></div><div><span>Used</span><strong>${escapeHtml(value("used"))}</strong></div></div><p class="dashboard-storage-reason">${escapeHtml(reason)}</p>${low ? `<div class="callout callout--warning dashboard-storage-warning" role="alert"><strong>Low space</strong><span>${escapeHtml(action)}</span></div>` : `<div class="dashboard-storage-action"><strong>Next action</strong><span>${escapeHtml(action)}</span></div>`}</article>`;
  };
  const storageHtml = volumes.length ? volumes.map(storageVolumeCard).join("") : `<div class="empty-state compact"><strong>Storage projection unavailable</strong><span>C:/ and D:/ figures are not available in this snapshot.</span></div>`;
  const lowSpaceVolumes = volumes.filter((volume) => volume?.lowSpace === true);
  const storageWarning = lowSpaceVolumes.length ? `<div class="callout callout--warning dashboard-storage-warning" role="alert"><strong>Low-space warning</strong><span>${escapeHtml(lowSpaceVolumes.map((volume) => String(volume?.id || "").toLowerCase() === "c" ? "C:" : String(volume?.id || "").toLowerCase() === "d" ? "D:" : "volume").join(", "))} review storage before new writes.</span></div>` : "";
  const workflowLibraryHtml = workflowLibraryState(source.workflowLibrary);
  return `<section class="dashboard-page" aria-labelledby="dashboard-title">
    <section class="dashboard-hero">
      <div><span class="eyebrow">CONTROL PLANE</span><h1 id="dashboard-title">Dashboard</h1><p>${escapeHtml(readinessNote)}</p></div>
      ${statusPill(readiness, formatStatus(readiness))}
    </section>
    ${workflowLibraryHtml}
    <section class="dashboard-metric-grid" aria-label="Readiness metrics">
      ${metricStatic("Hub API", formatStatus(readiness), "Static readiness snapshot")}
      ${metricStatic("Module plan", formatStatus(planStatus), "Preflight is read-only; install/download is not_run")}
      ${metricSnapshot("GPU", gpuValue, gpuDetail)}
      ${metricStatic("Ổ đĩa", diskValue, "Dung lượng trống")}
      ${metricSnapshot("Jobs hoạt động", String(activeJobs), `${jobs.length} bản ghi trong queue`)}
    </section>
    <section class="dashboard-storage card" aria-labelledby="dashboard-storage-title" data-storage-status="${escapeHtml(storageStatus)}" data-execution="${escapeHtml(storageExecution)}">
      <div class="card-title-row"><div><span class="eyebrow" data-i18n="STORAGE PROJECTION">${uiTextHtml("STORAGE PROJECTION")}</span><h2 id="dashboard-storage-title" data-i18n="C:/ & D:/ dung lượng">${uiTextHtml("C:/ & D:/ dung lượng")}</h2><p class="small"><span data-i18n="Server-owned, allowlisted volume snapshot">${uiTextHtml("Server-owned, allowlisted volume snapshot")}</span> · <span data-i18n="execution:">${uiTextHtml("execution:")}</span> ${escapeHtml(storageExecution)}</p></div>${statusPill(storageStatus, formatStatus(storageStatus))}</div>
      <div class="dashboard-storage-grid">${storageHtml}</div>
      ${storageWarning}
    </section>
    ${mediaEvidencePanel(source, "compact")}
    <section class="job-recovery-card card" aria-labelledby="dashboard-recovery-title" data-recovery-source="${escapeHtml(jobRecovery.source)}" data-recovery-status="${escapeHtml(jobRecovery.status)}">
      <div class="card-title-row"><div><span class="eyebrow" data-i18n="JOB RECOVERY">${uiTextHtml("JOB RECOVERY")}</span><h2 id="dashboard-recovery-title" data-i18n="Recovery attention">${uiTextHtml("Recovery attention")}</h2><p>${escapeHtml(jobRecovery.reason)}</p></div>${statusPill(jobRecovery.status, readinessStatusLabel(jobRecovery.status))}</div>
      <div class="job-recovery-counts" aria-label="Job recovery counts">
        <div data-i18n-container="Active" data-recovery-count="active"><span>Active</span><strong>${escapeHtml(String(jobRecovery.counts.active))}</strong></div>
        <div data-i18n-container="Attention" data-recovery-count="attention"><span>Attention</span><strong>${escapeHtml(String(jobRecovery.counts.attention))}</strong></div>
        <div data-i18n-container="Interrupted" data-recovery-count="interrupted"><span>Interrupted</span><strong>${escapeHtml(String(jobRecovery.counts.interrupted))}</strong></div>
        <div data-i18n-container="Recoverable" data-recovery-count="recoverable"><span>Recoverable</span><strong>${escapeHtml(String(jobRecovery.counts.recoverable))}</strong></div>
      </div>
      <div class="job-recovery-guidance"><span data-i18n="Next action">${uiTextHtml("Next action")}</span><p>${escapeHtml(jobRecovery.nextAction)}</p></div>
      <button class="button button--compact" type="button" data-route="jobs" data-recovery-focus="${jobRecovery.counts.attention ? "attention" : "all"}" aria-controls="jobs-page" data-i18n="Open focused Jobs">${uiTextHtml("Open focused Jobs")}</button>
    </section>
    <section class="dashboard-main-grid">
      <section class="dashboard-primary card" aria-labelledby="dashboard-modules-title">
        <div class="card-title-row"><div><span class="eyebrow" data-i18n="MODULE HEALTH">${uiTextHtml("MODULE HEALTH")}</span><h2 id="dashboard-modules-title" data-i18n="Tình trạng module">${uiTextHtml("Tình trạng module")}</h2></div><span class="tag">${escapeHtml(String(modules.length))} module</span></div>
        <div class="dashboard-module-list">${moduleRows}</div>
        <details><summary data-i18n="Reason & next action">${uiTextHtml("Reason & next action")}</summary><div class="dashboard-module-list">${moduleEvidenceRows}</div></details>
        <div class="callout" data-module-plan-status="${escapeHtml(planStatus)}"><strong data-i18n="Module preflight">${uiTextHtml("Module preflight")}</strong><p>${escapeHtml(planReason)}</p><p>${escapeHtml(planAction)}</p></div>
      </section>
      <aside class="dashboard-aside">
        <section class="card" aria-labelledby="dashboard-attention-title"><div class="card-title-row"><h2 id="dashboard-attention-title">Cần chú ý</h2><span class="tag">Tối đa 4</span></div><div class="dashboard-attention-list">${attentionRows}</div></section>
        <section class="card" aria-labelledby="dashboard-quick-title"><div class="card-title-row"><h2 id="dashboard-quick-title">Điều hướng nhanh</h2></div><div class="dashboard-quick-actions">${quickActions}${readinessAction}</div></section>
        <section class="card" aria-labelledby="dashboard-workflow-title"><div class="card-title-row"><h2 id="dashboard-workflow-title">Workflow ngắn</h2></div><ol class="dashboard-module-list">${workflowSteps}</ol></section>
      </aside>
    </section>
  </section>`;
}

function renderAiri(state) {
  const item = app(state, "airi");
  const action = item.launchable ? `<button class="button button--primary" type="button" data-launch="airi">Mở AIRI</button>` : "";
  return heading("ỨNG DỤNG NGOÀI", "AIRI", "AIRI do trình cài đặt Windows quản lý và là ngoại lệ duy nhất có cửa sổ riêng.", action) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("AIRI external", `<div class="stack"><div class="split"><div><strong>${escapeHtml(item.display_name || "AIRI")}</strong><p>Hub không đọc, sao chép hoặc hiển thị API key của AIRI.</p></div>${statusPill(item.component_status || "missing")}</div><div class="callout">Mở AIRI Settings trong ứng dụng AIRI; Hub chỉ dùng allowlist để gọi launcher đã đăng ký.</div></div>`, action)}
      ${card("Ranh giới tích hợp", `<ul class="notice-list"><li>Không embed AIRI bằng hack WebView.</li><li>Không di chuyển AIRI khỏi vị trí installer-managed.</li><li>Các workflow còn lại ưu tiên thực hiện ngay trong Hub.</li></ul>`, "", "card--flat")}
    </div>`;
}

function renderVision(state) {
  const omni = component(state, "omniparser");
  const rf = component(state, "rfdetr");
  const ground = component(state, "groundingdino");
  return heading("VISION", "Vision Studio", "Tải ảnh/screenshot vào Hub, chạy parser hoặc detector, xem JSON và artifact ngay trong cửa sổ này.") + `
    <div class="capability-grid">${capability("OmniParser", omni, "Parse UI, vùng tương tác và ảnh annotation.", tool(state, "parse_screen"))}${capability("RF-DETR", rf, "Phát hiện object theo threshold.", tool(state, "detect_objects"))}${capability("Grounding DINO", ground, "Prompt → boxes, có thể dùng lại trong SAM2.", tool(state, "ground_objects"))}</div>
    <div class="workspace-grid workspace-grid--three" style="margin-top:16px">
      ${card("OmniParser", `<form data-job-form data-tool="parse_screen" class="stack">${file("Ảnh hoặc screenshot", "asset_id", "image/*")}${field("Box threshold", `<input name="box_threshold" type="number" min="0" max="1" step="0.01" value="0.05" />`)}<div class="form-actions">${button("Phân tích UI", "button--primary")}</div>${formResult("vision-omni-result")}</form>`)}
      ${card("RF-DETR", `<form data-job-form data-tool="detect_objects" class="stack">${file("Ảnh/video", "asset_id", "image/*,video/*")}${field("Detection threshold", `<input name="threshold" type="number" min="0" max="1" step="0.01" value="0.50" />`)}<div class="form-actions">${button("Phát hiện object", "button--primary")}</div>${formResult("vision-rf-result")}</form>`)}
      ${card("Grounding DINO", `<form data-job-form data-tool="ground_objects" class="stack">${file("Ảnh", "asset_id", "image/*")}${field("Prompt", `<input name="prompt" required placeholder="person . bag ." />`)}<div class="form-grid">${field("Box", `<input name="box_threshold" type="number" step="0.01" value="0.35" />`)}${field("Text", `<input name="text_threshold" type="number" step="0.01" value="0.25" />`)}</div><div class="form-actions">${button("Tạo boxes", "button--primary")}</div>${formResult("vision-ground-result")}</form>`)}
    </div>`;
}

function renderSam2(state) {
  const item = component(state, "sam2");
  const pointTool = tool(state, "segment_from_points");
  const pointStatus = pointTool.tool_status || item.component_status || "missing";
  return heading("VISION", "SAM2", "Phân vùng và theo dõi trực tiếp qua trình xử lý SAM2; SAM2 Mask Studio không còn là workflow chính.", statusPill(pointStatus, `Chọn điểm: ${formatStatus(pointStatus)}`)) + workspaceState("SAM2 direct worker", pointTool, "Chọn điểm hoặc box trên preview rồi kiểm tra mask artifact trong Jobs.") + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Đầu vào và prompt", `<form data-job-form data-tool="segment_from_points" data-tool-by-field="mode" data-tool-map='{"points":"segment_from_points","box":"segment_from_box","track":"track_video_object","text":"segment_from_text"}' class="stack">${file("Ảnh hoặc video", "asset_id", "image/*,video/*")}${field("Chế độ", `<select name="mode"><option value="points">Chọn điểm</option><option value="box">Chọn box</option><option value="text">Prompt Grounding → SAM2</option><option value="track">Theo dõi video</option></select>`)}${field("Điểm (x,y,nhãn; …)", `<input name="points_text" placeholder="320,240,1; 100,80,0" />`)}${field("Box (x1,y1,x2,y2)", `<input name="box_text" placeholder="80,60,600,500" />`)}${field("Prompt Grounding", `<input name="prompt" placeholder="person . object ." />`)}<div class="form-actions">${button("Tạo mask / track", "button--primary")}</div>${formResult("sam2-result")}</form>`)}
      ${card("Xem trước và kết quả", `<div class="preview-empty"><span>◒</span><strong>Xem trước mask sẽ xuất hiện trong Jobs</strong><p>Điểm/box đi vào trình xử lý trực tiếp. Model nạp theo yêu cầu và giải phóng khi job xong.</p></div><div class="callout">Trạng thái ở tiêu đề áp dụng riêng cho chế độ Chọn điểm. Box, prompt text và theo dõi chỉ sẵn sàng sau smoke riêng; nếu backend báo một phần/chưa khả dụng, lỗi sẽ hiện cạnh action thay vì mở GUI ngoài.</div>`, "", "card--flat")}
    </div>`;
}

function renderOcr(state) {
  const item = component(state, "paddleocr_vl");
  return heading("DOCUMENTS", "OCR", "Đọc ảnh, PDF, clipboard export hoặc tài liệu từ một workspace; kết quả text/Markdown/JSON/tables là artifact Hub.", statusPill(tool(state, "ocr_document").tool_status || item.component_status || "missing")) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Tải tài liệu", `<form data-job-form data-tool="ocr_document" class="stack">${file("Ảnh hoặc PDF", "asset_id", "image/*,application/pdf")}${field("Output", `<select name="output_format"><option value="all">Text + Markdown + JSON + Tables</option><option value="markdown">Markdown</option><option value="json">JSON</option></select>`)}<div class="form-actions">${button("Chạy OCR", "button--primary")}</div>${formResult("ocr-result")}</form>`)}
      ${card("Kết quả", `<div class="preview-empty"><span>▤</span><strong>Không mở app OCR riêng</strong><p>Chọn tệp, chạy worker, rồi mở artifact trong bảng Jobs.</p></div><div class="tag-list"><span class="tag">Image</span><span class="tag">PDF</span><span class="tag">Clipboard export</span><span class="tag">Folder batch qua hàng đợi</span></div>`, "", "card--flat")}
    </div>`;
}

function renderWhisper(state) {
  const item = component(state, "whisper");
  return heading("SPEECH", "Whisper / Subtitles", "Thêm media, chọn ngôn ngữ và xuất transcript/SRT hoặc burn subtitle bằng worker nền.", statusPill(tool(state, "transcribe_media").tool_status || item.component_status || "missing")) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Transcript queue", `<form data-job-form data-tool="transcribe_media" data-tool-by-field="workflow" data-tool-map='{"transcribe":"transcribe_media","burn":"create_subtitled_video"}' class="stack">${file("Video hoặc audio", "asset_id", "audio/*,video/*")}${field("Workflow", `<select name="workflow"><option value="transcribe">Transcript + SRT</option><option value="burn">Transcript + burn subtitle</option></select>`)}<div class="form-grid">${field("Language", `<input name="language" value="auto" placeholder="auto / vi / ja" />`)}${field("Thiết bị", `<select name="device"><option value="cpu">CPU safe</option><option value="cuda">CUDA nếu environment hỗ trợ</option></select>`)}</div>${field("Đoạn ngắn bắt đầu/kết thúc (giây, tùy chọn)", `<div class="inline-fields"><input name="start" type="number" min="0" step="0.1" value="0" /><input name="end" type="number" min="0.1" step="0.1" value="10" /></div>`)}<div class="form-actions">${button("Thêm vào hàng đợi", "button--primary")}</div>${formResult("whisper-result")}</form>`)}
      ${card("Điều khiển", `<ul class="notice-list"><li>Output không ghi đè source media.</li><li>Cancel/resume dùng Jobs và chỉ dừng process do Hub sở hữu.</li><li>Folder batch được lên kế hoạch qua nhiều job upload; không cần mở Whisper GUI.</li></ul><div class="preview-empty compact"><span>≋</span><strong>Transcript, SRT và video subtitle xuất hiện ở Jobs</strong></div>`, "", "card--flat")}
    </div>`;
}

function renderVoice(state) {
  const tts = component(state, "qwen3_tts");
  const seed = component(state, "seed_vc");
  return heading("VOICE", "Voice Studio", "Qwen3-TTS và Seed-VC chạy bằng worker nền trong Hub, không mở secondary Voice GUI.") + `
    <div class="capability-grid">${capability("Qwen3-TTS", tts, "Text to Speech, Voice Design, Voice Clone, Batch.", tool(state, "text_to_speech"))}${capability("Seed-VC", seed, "Voice Conversion với source/target audio.", tool(state, "convert_voice"))}</div>
    <div class="workspace-grid workspace-grid--three" style="margin-top:16px">
      ${card("Text to Speech", `<form data-job-form data-tool="text_to_speech" class="stack">${field("Text", `<textarea name="text" required placeholder="Nhập nội dung cần đọc…"></textarea>`)}<div class="form-grid">${field("Language", `<input name="language" value="Vietnamese" />`)}${field("Speaker", `<input name="speaker" value="Ryan" />`)}</div><div class="form-actions">${button("Tạo giọng nói", "button--primary")}</div>${formResult("tts-result")}</form>`)}
      ${card("Voice Design / Clone", `<form data-job-form data-tool="design_voice" data-tool-by-field="operation" data-tool-map='{"design":"design_voice","clone":"clone_voice"}' class="stack">${field("Operation", `<select name="operation"><option value="design">Voice Design</option><option value="clone">Voice Clone</option></select>`)}${field("Text", `<textarea name="text" required placeholder="Nội dung đầu ra…"></textarea>`)}${file("Reference audio (chỉ Voice Clone)", "reference_asset_id", "audio/*")}${field("Reference text", `<input name="reference_text" placeholder="Tùy chọn" />`)}<div class="form-actions">${button("Chạy Qwen3-TTS", "button--primary")}</div>${formResult("voice-design-result")}</form>`)}
      ${card("Voice Conversion", `<form data-job-form data-tool="convert_voice" class="stack">${file("Source audio", "source_asset_id", "audio/*")}${file("Target voice", "target_asset_id", "audio/*")}${field("Diffusion steps", `<input name="diffusion_steps" type="number" min="1" max="50" value="4" />`)}<div class="form-actions">${button("Chuyển giọng", "button--primary")}</div>${formResult("seed-result")}</form>`)}
    </div>`;
}

function renderImageQuickV5(state) {
  const comfy = state.lifecycle?.comfyui || {};
  const imageTool = tool(state, "generate_flux");
  const recipe = state.pendingQuickRecipe || {};
  const selectedModel = recipe.model === "qwen" ? "qwen" : "flux";
  const settings = recipe.settings || {};
  const numeric = (key, fallback) => Number.isFinite(Number(settings[key])) ? Number(settings[key]) : fallback;
  const recipeNotice = state.pendingRecipeName ? `<div class="callout creative-applied-recipe" role="status"><strong>Recipe đang được áp dụng: ${escapeHtml(state.pendingRecipeName)}</strong><span>Prompt, style, negative block, seed và settings đã được đưa vào Quick. Chỉnh tiếp trước khi tạo job.</span></div>` : "";
  return heading("IMAGE", "Image AI", "Quick tạo FLUX/Qwen qua ComfyUI API ẩn. Hub Nodes dùng graph editor kéo socket; ComfyUI Advanced vẫn partial cho đến khi hoàn tất Windows WebView acceptance.", `<span class="status-pill" data-status="${escapeHtml(imageTool.tool_status || comfy.status || "stopped")}">${escapeHtml(formatStatus(imageTool.tool_status || comfy.status || "stopped"))}</span>`) + workspaceState("Image Generate / Edit", imageTool, "Chọn template Hub Nodes để nối prompt → generate/edit → upscale → preview.") + imageWorkflowRail(state) + recipeNotice + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Quick", `<form data-job-form data-tool="generate_flux" data-tool-by-field="engine" data-tool-map='{"flux":"generate_flux","qwen":"generate_qwen_image"}' class="stack">${field("Model", `<select name="engine"><option value="flux" ${selectedModel === "flux" ? "selected" : ""}>FLUX.2 Klein</option><option value="qwen" ${selectedModel === "qwen" ? "selected" : ""}>Qwen Image 2512</option></select>`)}${field("Prompt", `<textarea name="prompt" required placeholder="Mô tả ảnh cần tạo…">${escapeHtml(recipe.prompt || "")}</textarea>`)}${field("Negative prompt", `<input name="negative_prompt" placeholder="Tùy chọn" value="${escapeHtml(recipe.negative_prompt || "")}" />`)}<div class="form-grid">${field("Width", `<input name="width" type="number" min="256" max="2048" step="64" value="${numeric("width", 768)}" />`)}${field("Height", `<input name="height" type="number" min="256" max="2048" step="64" value="${numeric("height", 768)}" />`)}${field("Steps", `<input name="steps" type="number" min="1" max="80" value="${numeric("steps", 20)}" />`)}${field("Seed", `<input name="seed" type="number" min="0" placeholder="random" value="${Number.isInteger(recipe.seed) ? recipe.seed : ""}" />`)}</div>${file("Input image (FLUX image-edit nếu workflow hỗ trợ)", "input_image_asset_id", "image/*")}<div class="form-actions">${button("Generate trong Hub", "button--primary")}</div>${formResult("image-result")}</form>`) }
      ${card("Chế độ làm việc", `<div class="preview-empty"><span>✦</span><strong>Không mở trình duyệt ngoài</strong><p>Chỉnh sửa & Mask giữ layer, nét brush, snapshot và provenance theo contract không phá hủy. Chọn Hub Nodes để nối typed socket bằng kéo-thả. ComfyUI Advanced là bridge partial: chỉ dùng sau checklist Windows WebView, không được coi là operational chỉ vì iframe hiển thị.</p></div><div class="form-actions"><button class="button button--primary" type="button" data-workspace-tab="image:studio">Mở Chỉnh sửa & Mask</button><button class="button" type="button" data-workspace-tab="image:nodes">Mở Hub Nodes</button><button class="button" type="button" data-workspace-tab="image:advanced">Xem ComfyUI Advanced (partial)</button></div>`, "", "card--flat")}
    </div>`;
}

const maskStrokeOverlay = (layer) => (layer?.operations || []).filter((operation) => operation.operation === "brush" && Array.isArray(operation.points)).map((operation) => {
  const points = operation.points.map((point) => `${Math.max(0, Math.min(1, Number(point.x) || 0)) * 100},${Math.max(0, Math.min(1, Number(point.y) || 0)) * 100}`).join(" ");
  const width = Math.max(1.2, Math.min(24, (Number(operation.size) || 0.06) * 100));
  return `<polyline class="mask-studio-canvas__stroke ${operation.mode === "subtract" ? "is-subtract" : ""}" points="${points}" stroke-width="${width}" />`;
}).join("");

const imageMaskRecovery = (recovery = {}) => {
  if (!recovery || !["recovery_required", "recovered_partial"].includes(recovery.status)) return "";
  const label = recovery.status === "recovered_partial" ? "Đã phục hồi một phần · chỉ đọc" : "Cần phục hồi · chỉ đọc";
  return `<section class="workspace-state creative-recovery" data-status="${escapeHtml(recovery.status)}" role="status"><div class="workspace-state__head"><div><span class="eyebrow">SAFE RECOVERY</span><h2>Image & Mask Studio cần chú ý</h2></div>${statusPill(recovery.status, label)}</div><p>${escapeHtml(recovery.reason || "Hub giữ nguyên state local chưa hợp lệ.")}</p><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(recovery.action || "Kiểm tra export/snapshot bằng công cụ quản trị trước khi tạo workspace local mới.")}</span></div></section>`;
};

function renderImageMaskStudio(state) {
  const studio = state.imageMaskStudio || {};
  const recovery = studio.recovery || {};
  const sessions = studio.sessions || [];
  const detail = state.imageMaskSession?.session || null;
  const sourceAssets = (state.creative?.assets || []).filter((asset) => String(asset.media_type || "").startsWith("image/") && asset.available !== false);
  const projects = (state.creative?.projects || []).filter((project) => project.status === "active");
  const preflight = studio.preflight?.capabilities || state.imageMaskSession?.preflight?.capabilities || [];
  const selectedLayer = detail?.layers?.find((layer) => layer.id === (state.selectedImageMaskLayerId || detail.active_layer_id)) || detail?.layers?.at(-1) || null;
  const selectedMask = selectedLayer?.kind === "mask" ? selectedLayer : null;
  const compare = state.imageMaskCompare?.compare || null;
  const sourceOption = state.pendingImageMaskSourceId || "";
  const limits = studio.limits || {};
  const capacityNotice = Number.isFinite(Number(limits.max_sessions)) || Number.isFinite(Number(limits.max_presets))
    ? `<p class="small">Giới hạn an toàn: ${escapeHtml(limits.session_count || 0)}/${escapeHtml(limits.max_sessions || "—")} phiên · ${escapeHtml(limits.preset_count || 0)}/${escapeHtml(limits.max_presets || "—")} preset. Hub từ chối record mới khi đầy để không tự xóa bản nháp.</p>`
    : "";
  const capabilityCards = preflight.map((item) => `<article class="mask-capability" data-status="${escapeHtml(item.status)}"><div class="split"><strong>${escapeHtml(item.title)}</strong>${statusPill(item.status)}</div><p>${escapeHtml(item.reason)}</p><small><strong>Bước tiếp theo:</strong> ${escapeHtml(item.action)}</small></article>`).join("");
  if (["recovery_required", "recovered_partial"].includes(recovery.status)) {
    return heading("IMAGE / CREATIVE", "Chỉnh sửa ảnh & Mask Studio", "Studio đang ở chế độ chỉ đọc để không ghi đè state local cần phục hồi.", `<button class="button" type="button" data-refresh-image-mask-studio>Làm mới Studio</button>`) + `
      <section class="mask-capability-grid" aria-label="Preflight Image & Mask Studio">${capabilityCards || `<div class="workspace-state" data-status="partial"><p>Đang tải preflight capability...</p></div>`}</section>
      ${imageMaskRecovery(recovery)}`;
  }
  const sessionList = sessions.map((item) => `<button type="button" class="mask-session-card ${item.id === detail?.id ? "is-selected" : ""}" data-image-mask-open="${escapeHtml(item.id)}" aria-pressed="${item.id === detail?.id}"><span class="eyebrow">${escapeHtml(item.dirty ? "DRAFT CHƯA LƯU" : "ĐÃ LƯU LOCAL")}</span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.layer_count)} lớp · ${escapeHtml(item.mask_count)} mask · r${escapeHtml(item.revision)}</small></button>`).join("") || `<div class="empty-state compact"><strong>Chưa có phiên chỉnh sửa</strong><span>Chọn một artifact ảnh Hub hoặc tải ảnh lên để bắt đầu stack non-destructive.</span></div>`;
  const sourcePreview = detail?.source_artifact?.url
    ? `<div class="mask-studio-canvas ${selectedMask ? "is-editable" : ""}" ${selectedMask ? `data-mask-canvas data-studio-id="${escapeHtml(detail.id)}" data-layer-id="${escapeHtml(selectedMask.id)}" tabindex="0" role="application" aria-label="Canvas mask ${escapeHtml(selectedMask.name)}. Kéo để thêm hoặc trừ mask; Escape hủy nét đang vẽ."` : ""}><img src="${escapeHtml(detail.source_artifact.url)}" alt="Ảnh nguồn của ${escapeHtml(detail.title)}" /><svg class="mask-studio-canvas__overlay" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${selectedMask ? maskStrokeOverlay(selectedMask) : ""}</svg>${selectedMask ? `<span class="mask-studio-canvas__hint">Kéo để vẽ ${escapeHtml(selectedMask.name)} · Esc để hủy nét</span>` : `<span class="mask-studio-canvas__hint">Chọn một layer mask để vẽ metadata</span>`}</div>`
    : `<div class="preview-empty"><span>◌</span><strong>Ảnh nguồn không còn khả dụng</strong><p>Studio giữ bản nháp, nhưng không hiển thị raw path. Gắn lại artifact Hub hợp lệ trong một phiên mới.</p></div>`;
  const layerStack = detail?.layers?.slice().reverse().map((layer, reverseIndex) => {
    const index = (detail.layers.length - 1) - reverseIndex;
    const selected = layer.id === selectedLayer?.id;
    return `<article class="mask-layer ${selected ? "is-selected" : ""}" data-kind="${escapeHtml(layer.kind)}"><button type="button" class="mask-layer__select" data-image-mask-layer-select="${escapeHtml(layer.id)}" aria-pressed="${selected}"><span class="mask-layer__icon">${layer.kind === "source" ? "▣" : layer.kind === "mask" ? "◌" : layer.kind === "adjustment" ? "◐" : "✦"}</span><span><strong>${escapeHtml(layer.name)}</strong><small>${escapeHtml(layer.kind)}${layer.kind === "mask" ? ` · ${escapeHtml(layer.operation_count || 0)} thao tác` : ""}</small></span></button><div class="mask-layer__actions"><button class="button button--compact" type="button" data-image-mask-layer-visible="${escapeHtml(layer.id)}" data-next-visible="${layer.visible ? "false" : "true"}" aria-label="${layer.visible ? "Ẩn" : "Hiện"} ${escapeHtml(layer.name)}">${layer.visible ? "Ẩn" : "Hiện"}</button>${layer.kind !== "source" ? `<button class="button button--compact" type="button" data-image-mask-layer-move="${escapeHtml(layer.id)}" data-direction="up" ${index >= detail.layers.length - 1 ? "disabled" : ""} aria-label="Đưa ${escapeHtml(layer.name)} lên">↑</button><button class="button button--compact" type="button" data-image-mask-layer-move="${escapeHtml(layer.id)}" data-direction="down" ${index <= 1 ? "disabled" : ""} aria-label="Đưa ${escapeHtml(layer.name)} xuống">↓</button><button class="button button--compact button--danger" type="button" data-image-mask-layer-remove="${escapeHtml(layer.id)}" aria-label="Gỡ ${escapeHtml(layer.name)}">Gỡ</button>` : ""}</div></article>`;
  }).join("") || "";
  const snapshotOptions = (detail?.snapshots || []).map((snapshot) => `<option value="${escapeHtml(snapshot.id)}">r${escapeHtml(snapshot.revision)} · ${escapeHtml(snapshot.label)}</option>`).join("");
  const comparison = compare?.before && compare?.after ? `<section class="mask-compare" aria-label="So sánh trước và sau"><div>${compare.before.preview_artifact?.url ? `<img src="${escapeHtml(compare.before.preview_artifact.url)}" alt="Trước: ${escapeHtml(compare.before.label)}" />` : `<div class="mask-compare__placeholder">Không có preview raster</div>`}<strong>Trước · ${escapeHtml(compare.before.label)}</strong></div><div>${compare.after.preview_artifact?.url ? `<img src="${escapeHtml(compare.after.preview_artifact.url)}" alt="Sau: ${escapeHtml(compare.after.label)}" />` : `<div class="mask-compare__placeholder">Không có preview raster</div>`}<strong>Sau · ${escapeHtml(compare.after.label)}</strong></div></section><details class="advanced"><summary>Khác biệt metadata/layer · ${escapeHtml(Object.keys(compare.differences || {}).length)} mục</summary><pre>${escapeHtml(JSON.stringify(compare.differences || {}, null, 2))}</pre></details>` : `<div class="callout">Tạo hoặc chỉnh ít nhất một layer để Studio lưu snapshot Trước / Sau. Preview chỉ dùng artifact nguồn/dẫn xuất có sẵn, không giả lập raster hóa mask.</div>`;
  const inspector = !detail ? "" : selectedLayer ? `<section class="mask-inspector"><div class="split"><div><span class="eyebrow">INSPECTOR</span><h2>${escapeHtml(selectedLayer.name)}</h2></div><span class="tag">${escapeHtml(selectedLayer.kind)}</span></div><form class="stack" data-image-mask-form="update-layer" data-session-id="${escapeHtml(detail.id)}" data-layer-id="${escapeHtml(selectedLayer.id)}">${field("Tên layer", `<input name="name" maxlength="120" value="${escapeHtml(selectedLayer.name)}" />`)}${field("Độ mờ", `<input name="opacity" type="number" min="0" max="1" step="0.05" value="${escapeHtml(selectedLayer.opacity)}" />`)}<label class="node-toggle"><input name="visible" type="checkbox" ${selectedLayer.visible ? "checked" : ""} /> Hiển thị layer</label>${selectedLayer.kind === "adjustment" ? `${field("Adjustment", `<select name="adjustment_kind"><option value="brightness" ${selectedLayer.adjustment?.kind === "brightness" ? "selected" : ""}>Brightness</option><option value="contrast" ${selectedLayer.adjustment?.kind === "contrast" ? "selected" : ""}>Contrast</option><option value="saturation" ${selectedLayer.adjustment?.kind === "saturation" ? "selected" : ""}>Saturation</option><option value="exposure" ${selectedLayer.adjustment?.kind === "exposure" ? "selected" : ""}>Exposure</option><option value="temperature" ${selectedLayer.adjustment?.kind === "temperature" ? "selected" : ""}>Temperature</option><option value="crop" ${selectedLayer.adjustment?.kind === "crop" ? "selected" : ""}>Crop metadata</option></select>`)}${field("Settings JSON an toàn", `<textarea name="adjustment_settings" rows="3">${escapeHtml(JSON.stringify(selectedLayer.adjustment?.settings || {}))}</textarea>`)}` : ""}<div class="form-actions">${button("Lưu layer")}</div><div class="form-result" role="status"></div></form>${selectedMask ? `<section class="mask-brush-tools"><h3>Brush & mask</h3><div class="form-grid">${field("Chế độ", `<select data-mask-brush-mode><option value="add">Thêm mask</option><option value="subtract">Trừ mask</option></select>`)}${field("Kích thước", `<input data-mask-brush-size type="number" min="0.002" max="1" step="0.01" value="0.06" />`)}${field("Cường độ", `<input data-mask-brush-strength type="number" min="0.01" max="1" step="0.05" value="1" />`)}</div><p class="small">Kéo trực tiếp trên ảnh để lưu stroke vector non-destructive. Không có pixel/source nào bị ghi đè.</p><div class="form-actions"><button class="button button--compact" type="button" data-image-mask-operation="invert">Đảo mask</button><button class="button button--compact" type="button" data-image-mask-operation="feather">Feather</button><button class="button button--compact" type="button" data-image-mask-operation="grow">Grow</button><button class="button button--compact" type="button" data-image-mask-operation="shrink">Shrink</button><button class="button button--compact" type="button" data-image-mask-export="${escapeHtml(selectedMask.id)}">Xuất manifest mask</button>${selectedMask.artifact?.url ? `<a class="button button--compact" href="${escapeHtml(selectedMask.artifact.url)}" download="${escapeHtml(selectedMask.artifact.name || "mask")}">Lưu artifact mask</a>` : ""}</div><label class="field"><span>Mức feather/grow/shrink</span><input data-mask-operation-amount type="number" min="0.001" max="1" step="0.01" value="0.08" /></label></section>` : ""}</section>` : `<div class="empty-state compact"><strong>Chọn một layer</strong><span>Inspector sẽ hiển thị metadata không phá hủy cho layer đang chọn.</span></div>`;
  return heading("IMAGE / CREATIVE", "Chỉnh sửa ảnh & Mask Studio", "Lớp, mask vector, snapshot, recipe và provenance cục bộ. Ảnh nguồn luôn immutable; SAM2/inpaint/outpaint chỉ phản ánh preflight thực tế.", `<button class="button" type="button" data-refresh-image-mask-studio>Làm mới Studio</button>`) + `
    <section class="mask-capability-grid" aria-label="Preflight Image & Mask Studio">${capabilityCards || `<div class="workspace-state" data-status="partial"><p>Đang tải preflight capability...</p></div>`}</section>
    <div class="workspace-grid workspace-grid--two mask-studio-create">
      ${card("Bắt đầu từ artifact ảnh", `<form class="stack" data-image-mask-form="create-session">${field("Tên phiên", `<input name="title" maxlength="120" placeholder="Ví dụ: Portrait masking" />`)}${field("Artifact ảnh đã có", `<select name="existing_source_artifact_id"><option value="">Chọn từ Asset Library</option>${sourceAssets.map((asset) => `<option value="${escapeHtml(asset.id)}" ${sourceOption === asset.id ? "selected" : ""}>${escapeHtml(asset.name || asset.id)}</option>`).join("")}</select>`)}${file("Hoặc tải ảnh nguồn mới", "source_artifact_id", "image/*")}${field("Project (tùy chọn)", `<select name="project_id">${projectOptions(projects, detail?.project_id || "", "Chưa liên kết project")}</select>`)}<div class="form-actions">${button("Tạo Studio không phá hủy", "button--primary")}</div><div class="form-result" role="status"></div></form><p class="small">Upload dùng Artifact Store streaming; Studio chỉ nhận opaque ID và snapshot metadata, không đọc path máy.</p>`) }
      ${card("Phiên gần đây", `${capacityNotice}<div class="mask-session-list" aria-label="Danh sách phiên Mask Studio">${sessionList}</div>`, "", "card--flat")}
    </div>
    ${detail ? `<section class="image-mask-session" data-image-mask-session data-session-id="${escapeHtml(detail.id)}"><header class="image-mask-session__header"><div><span class="eyebrow">${detail.dirty ? "DRAFT CHƯA LƯU" : "ĐÃ LƯU LOCAL"}</span><h2>${escapeHtml(detail.title)}</h2><p>r${escapeHtml(detail.revision)} · autosave ${escapeHtml(detail.autosaved_at || "—")} · ${escapeHtml(detail.last_action || "")}</p></div><div class="form-actions"><button class="button button--compact" type="button" data-image-mask-undo ${detail.history?.can_undo ? "" : "disabled"}>Hoàn tác</button><button class="button button--compact" type="button" data-image-mask-redo ${detail.history?.can_redo ? "" : "disabled"}>Làm lại</button><button class="button button--primary button--compact" type="button" data-image-mask-save>Lưu bản nháp</button></div></header><div class="image-mask-session__layout"><section class="image-mask-canvas-panel"><div class="split"><h2>Canvas layer</h2><span class="tag">${escapeHtml(detail.source_artifact?.name || detail.source_artifact_id)}</span></div>${sourcePreview}<p class="small">Canvas vẽ metadata ở tọa độ chuẩn hóa. Muốn có PNG mask/output mới, xuất/tạo bằng công cụ được ủy quyền rồi upload artifact để gắn làm layer dẫn xuất.</p></section><section class="image-mask-layer-panel"><div class="split"><h2>Stack lớp</h2><span class="tag">${escapeHtml(detail.layer_count)} lớp</span></div><div class="mask-layer-stack" aria-label="Layer stack">${layerStack}</div><form class="stack mask-add-layer" data-image-mask-form="add-layer" data-session-id="${escapeHtml(detail.id)}"><h3>Thêm layer</h3>${field("Loại", `<select name="kind"><option value="mask">Mask</option><option value="adjustment">Adjustment</option><option value="generated">Artifact dẫn xuất</option></select>`)}${field("Tên", `<input name="name" maxlength="120" placeholder="Ví dụ: Background mask" />`)}${field("Artifact mask/dẫn xuất (tùy chọn cho mask)", `<input type="file" data-asset-key="layer_artifact_id" accept="image/*" />`)}${field("Adjustment", `<select name="adjustment_kind"><option value="brightness">Brightness</option><option value="contrast">Contrast</option><option value="saturation">Saturation</option><option value="exposure">Exposure</option><option value="temperature">Temperature</option><option value="crop">Crop metadata</option></select>`)}${field("Settings JSON", `<textarea name="adjustment_settings" rows="2">{}</textarea>`)}<div class="form-actions">${button("Thêm layer")}</div><div class="form-result" role="status"></div></form></section>${inspector}</div><section class="image-mask-session__bottom"><div class="workspace-grid workspace-grid--three">${card("Snapshot & recovery", `<form class="stack" data-image-mask-form="compare" data-session-id="${escapeHtml(detail.id)}">${field("Trước", `<select name="before_snapshot_id">${snapshotOptions}</select>`)}${field("Sau", `<select name="after_snapshot_id">${snapshotOptions}</select>`)}<div class="form-actions">${button("So sánh Trước / Sau")}</div><div class="form-result" role="status"></div></form><form class="stack creative-inline-form" data-image-mask-form="restore-snapshot" data-session-id="${escapeHtml(detail.id)}">${field("Khôi phục snapshot", `<select name="snapshot_id">${snapshotOptions}</select>`)}<div class="form-actions">${button("Khôi phục an toàn")}</div><div class="form-result" role="status"></div></form>`) }${card("Recipe / preset", `<form class="stack" data-image-mask-form="capture-preset" data-session-id="${escapeHtml(detail.id)}">${field("Tên preset", `<input name="title" maxlength="120" placeholder="Ví dụ: Soft subject mask" />`)}<div class="form-actions">${button("Lưu preset")}</div><div class="form-result" role="status"></div></form><label class="field"><span>Preset đã lưu</span><select data-image-mask-preset><option value="">Chọn preset...</option>${(studio.presets || []).map((preset) => `<option value="${escapeHtml(preset.id)}">${escapeHtml(preset.title)} · ${escapeHtml(preset.layer_count)} lớp</option>`).join("")}</select></label><button class="button button--compact" type="button" data-image-mask-apply-preset>Áp dụng preset</button>`) }${card("Project & import", `<form class="stack" data-image-mask-form="link-project" data-session-id="${escapeHtml(detail.id)}">${field("Liên kết project", `<select name="project_id">${projectOptions(projects, detail.project_id || "", "Chọn project để liên kết")}</select>`)}<div class="form-actions">${button("Liên kết artifact Studio")}</div><div class="form-result" role="status"></div></form><form class="stack creative-inline-form" data-image-mask-form="import-mask" data-session-id="${escapeHtml(detail.id)}">${field("Import manifest mask", `<textarea name="manifest" rows="4" placeholder='{"contract_version":"image-mask-export.v1",…}'></textarea>`)}<div class="form-actions">${button("Validate & import")}</div><div class="form-result" role="status"></div></form>${detail.pending_project_attach ? `<p class="callout callout--warning">Liên kết project đang chờ recovery. Hãy thử lại sau khi project đích sẵn sàng.</p>` : ""}`) }</div>${comparison}</section></section>` : `<section class="empty-state mask-studio-empty"><strong>Chọn hoặc tạo một phiên để bắt đầu</strong><span>Layer, mask và history sẽ được autosave vào user-data local riêng, không làm thay đổi Creative Workspace v1 hay ảnh nguồn.</span></section>`}`;
}

function renderComfyAdvancedV5(state) {
  const comfy = state.comfyAdvanced?.comfyui || state.lifecycle?.comfyui || {};
  const workflows = state.comfyWorkflows || [];
  const candidate = String(comfy.advanced_url || "");
  const url = /^http:\/\/127\.0\.0\.1:\d+$/.test(candidate) ? candidate : "";
  const frame = url
    ? `<iframe class="comfy-advanced__frame" data-comfy-frame src="${escapeHtml(url)}" title="ComfyUI Advanced in Local AI Hub" sandbox="allow-scripts allow-same-origin allow-forms allow-downloads" referrerpolicy="no-referrer"></iframe>`
    : `<div class="preview-empty"><span>✦</span><strong>ComfyUI chưa chạy</strong><p>Bấm Khởi động để Hub mở ComfyUI thành backend ẩn rồi nhúng editor thật ở đây. Không mở Chrome hoặc Edge ngoài.</p></div>`;
  return heading("IMAGE / ADVANCED", "ComfyUI Advanced", "Bridge ComfyUI giữ trạng thái partial cho đến khi checklist Windows WebView thực tế pass. Hub không sao chép hoặc triển khai lại frontend ComfyUI.", `<span class="status-pill" data-status="partial">Partial · cần acceptance</span><button class="button" type="button" data-workspace-tab="image:quick">← Back to Hub</button>`) + `
    <section class="comfy-advanced" data-comfy-advanced>
      <div class="comfy-advanced__toolbar"><div><strong>partial · ${escapeHtml(comfy.status || "stopped")}</strong><span>${comfy.hub_owned ? " · Hub-owned hidden backend" : " · existing local backend"}</span></div><div class="form-actions"><button class="button button--primary" type="button" data-comfy-action="start">Khởi động / làm mới ComfyUI</button><button class="button" type="button" data-workspace-tab="image:quick">Back to Hub</button></div></div>
      <div class="comfy-advanced__layout">
        <aside class="comfy-bridge"><h2>Workflow bridge</h2><p>List/load/save JSON local cho node <code>ComfyUI Workflow</code>. Bridge không nhận raw path và không được commit vào Git.</p><p class="callout callout--warning">Acceptance iframe/canvas/socket/queue/shortcut/upload vẫn deferred do GPU/resource contention. Không coi bề mặt này là operational.</p><label class="field"><span>Đã lưu</span><select data-comfy-workflow-select><option value="">Chọn workflow…</option>${workflows.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}${item.local ? " · local" : ""}</option>`).join("")}</select></label><div class="form-actions"><button class="button" type="button" data-comfy-action="load">Load JSON</button></div><label class="field"><span>ID khi lưu local</span><input data-comfy-workflow-id placeholder="my_flux_mask" /></label><label class="field"><span>Bridge JSON</span><textarea data-comfy-workflow-json rows="18" placeholder='{"schema_version":1,"id":"my_flux_mask","kind":"raw_comfy_api",…}'></textarea></label><div class="form-actions"><button class="button" type="button" data-comfy-action="save">Save local bridge</button></div><small>Quick FLUX/Qwen compile qua API. Bridge raw chỉ nhận binding có schema và lưu dưới user-data local.</small></aside>
        <div class="comfy-advanced__frame-wrap">${frame}</div>
      </div>
    </section>`;
}

function renderImage(state) {
  const comfy = state.lifecycle?.comfyui || {};
  const imageTool = tool(state, "generate_flux");
  return heading("IMAGE", "Image AI", "FLUX và Qwen Image gọi ComfyUI API trực tiếp. ComfyUI chạy nền ẩn khi Hub cần, không mở Local Image Studio.", `<span class="status-pill" data-status="${escapeHtml(imageTool.tool_status || comfy.status || "stopped")}">${escapeHtml(formatStatus(imageTool.tool_status || comfy.status || "stopped"))}</span>`) + workspaceState("Image Generate / Edit", imageTool, "Chọn template Hub Nodes để nối prompt → generate/edit → upscale → preview.") + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Generate", `<form data-job-form data-tool="generate_flux" data-tool-by-field="engine" data-tool-map='{"flux":"generate_flux","qwen":"generate_qwen_image"}' class="stack">${field("Model", `<select name="engine"><option value="flux">FLUX.2 Klein</option><option value="qwen">Qwen Image 2512</option></select>`)}${field("Prompt", `<textarea name="prompt" required placeholder="Mô tả ảnh cần tạo…"></textarea>`)}${field("Negative prompt", `<input name="negative_prompt" placeholder="Tùy chọn" />`)}<div class="form-grid">${field("Width", `<input name="width" type="number" min="256" max="2048" step="64" value="768" />`)}${field("Height", `<input name="height" type="number" min="256" max="2048" step="64" value="768" />`)}${field("Steps", `<input name="steps" type="number" min="1" max="80" value="20" />`)}${field("Seed", `<input name="seed" type="number" min="0" placeholder="random" />`)}</div>${file("Input image (FLUX image-edit nếu workflow hỗ trợ)", "input_image_asset_id", "image/*")}<div class="form-actions">${button("Generate trong Hub", "button--primary")}</div>${formResult("image-result")}</form>`)}
      ${card("Preview & Advanced", `<div class="preview-empty"><span>✦</span><strong>Ảnh output sẽ hiện trong Jobs</strong><p>Lịch sử giữ prompt, seed, model qua metadata job an toàn; không lộ đường dẫn cục bộ.</p></div><details class="advanced"><summary>ComfyUI Advanced</summary><p>Advanced mở web interface ComfyUI trong trình duyệt nếu cần sửa workflow; normal workflow vẫn dùng form Hub ở bên trái.</p><button class="button" type="button" data-open-comfy>Open ComfyUI web interface</button></details>`, "", "card--flat")}
    </div>`;
}

function renderMedia(state) {
  const item = component(state, "ffmpeg");
  const genericTool = tool(state, "run_media_operation");
  const mediaEvidence = mediaCapabilityEvidence(state);
  const genericContract = {
    ...genericTool,
    tool_status: "partial",
    status: "partial",
    reason: "Generic media actions are not covered by the three exact server evidence rows.",
    action: "Use the exact evidence summary first; this UI does not claim generic media execution.",
  };
  return heading("MEDIA", "Media workspace", "Media operation state is shown from the server snapshot before any separately authorized work.", statusPill("partial", "Partial")) + mediaEvidencePanel(state, "media") + workspaceState("Generic Media action", genericContract, "Review the exact operation scope; no generic action is enabled from this snapshot.") + videoWorkflowRail(state) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Generic video and image actions", `<form data-job-form data-tool="run_media_operation" data-media-generic-action="explanatory" data-media-generic-status="partial" class="stack" aria-describedby="media-generic-guidance">${file("Primary media input", "asset_id", "audio/*,video/*,image/*")}${files("Additional inputs", "input_asset_ids", "video/*,image/*")}${file("Secondary audio or subtitle", "secondary_asset_id", "audio/*,.srt,.ass")}${field("Operation", `<select name="operation"><option value="probe">Read metadata</option><option value="trim">Trim</option><option value="concat">Concat</option><option value="resize">Resize</option><option value="crop">Crop</option><option value="rotate">Rotate</option><option value="fps">FPS</option><option value="transcode">Transcode</option><option value="extract_audio">Extract audio</option><option value="replace_audio">Replace audio</option><option value="mux">Mux audio/video</option><option value="burn_subtitle">Burn subtitle</option><option value="extract_frames">Extract frames</option><option value="image_sequence_video">Image sequence to video</option><option value="image_resize">Image resize</option><option value="image_crop">Image crop</option><option value="image_rotate">Image rotate</option><option value="image_flip">Image flip</option><option value="image_convert">Image convert</option><option value="image_compress">Image compress</option></select>`)}<div class="form-grid">${field("Start", `<input name="start" type="number" min="0" step="0.1" value="0" />`)}${field("End", `<input name="end" type="number" min="0.1" step="0.1" value="5" />`)}${field("Width", `<input name="width" type="number" min="2" value="1280" />`)}${field("Height", `<input name="height" type="number" min="-2" value="-2" />`)}${field("FPS", `<input name="fps" type="number" min="1" value="30" />`)}${field("Rotation", `<select name="degrees"><option value="90">90</option><option value="180">180</option><option value="270">270</option></select>`)}${field("Flip", `<select name="axis"><option value="horizontal">Horizontal</option><option value="vertical">Vertical</option></select>`)}${field("Image format", `<select name="format"><option value="png">PNG</option><option value="jpg">JPG</option><option value="webp">WEBP</option></select>`)}</div><div id="media-generic-guidance" class="callout callout--warning" role="status"><strong>Generic media remains Partial.</strong><span>${escapeHtml(mediaEvidence.genericReason)} ${escapeHtml(mediaEvidence.genericNextAction)}</span></div><div class="form-actions"><button class="button" type="submit" disabled data-media-generic-action="explanatory">Execution unavailable from this snapshot</button></div>${formResult("media-result")}</form>`) }
      ${card("Media safety boundary", `<ul class="notice-list"><li>Only exact server-owned evidence can be operational.</li><li>Generic actions remain explanatory and cannot submit a runtime request here.</li><li>Artifact previews use opaque Hub URLs and native metadata/range transport.</li></ul>`, "", "card--flat")}
    </div>`;
}

function renderAnime(state) {
  const item = component(state, "animesr");
  return heading("VIDEO AI", "AnimeSR", "Workflow upscale chính chạy bằng worker AnimeSR trong Hub. Anime Upscale Studio chỉ là legacy/debug fallback, không còn là action chính.", statusPill(tool(state, "upscale_anime_video").tool_status || item.component_status || "missing")) + workspaceState("AnimeSR / Interpolate", tool(state, "upscale_anime_video"), "Chuẩn bị clip ngắn; video smoke hiện deferred do resource contention.") + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Upscale queue", `<form data-job-form data-tool="upscale_anime_video" class="stack">${file("Video input", "asset_id", "video/*")}${field("Model", `<select name="model"><option value="AnimeSR_v2">AnimeSR v2</option><option value="AnimeSR_v1-PaperModel">AnimeSR v1 Paper</option></select>`)}<div class="form-grid">${field("Scale", `<select name="scale"><option value="2">2×</option><option value="3">3×</option><option value="4">4×</option></select>`)}${field("Chunk seconds", `<input name="chunk_seconds" type="number" min="10" value="120" />`)}</div><div class="check-grid"><label><input name="half" type="checkbox" checked /> Half precision</label><label><input name="use_rife" type="checkbox" /> RIFE (partial)</label><label><input name="use_realesrgan" type="checkbox" /> Real-ESRGAN (partial)</label></div><div class="form-actions">${button("Thêm AnimeSR job", "button--primary")}</div>${formResult("anime-result")}</form>`)}
      ${card("Progress & output", `<div class="preview-empty"><span>⇱</span><strong>Queue, progress, cancel và resume nằm ở Jobs</strong><p>Hub không mở Anime Upscale Studio để chạy normal workflow.</p></div><details class="advanced"><summary>Advanced / legacy</summary><p>Legacy Studio chỉ nên dùng debug khi direct worker báo limitation đã được ghi nhận.</p></details>`, "", "card--flat")}
    </div>`;
}

const creativeTabs = (state) => {
  const active = state.creativeTab || "projects";
  const tabs = [["projects", "Projects"], ["assets", "Asset Library"], ["recipes", "Prompts & Recipes"], ["compare", "Compare Board"], ["gallery", "Workflow Gallery"]];
  return `<div class="module-tabs creative-tabs" role="tablist" aria-label="Creative workspace sections">${tabs.map(([id, label]) => `<button class="tab ${active === id ? "is-selected" : ""}" type="button" role="tab" aria-selected="${active === id}" data-creative-tab="${id}">${label}</button>`).join("")}</div>`;
};

const creativeRecovery = (recovery = {}) => {
  if (!recovery || recovery.status === "clean") return "";
  return `<section class="workspace-state creative-recovery" data-status="${escapeHtml(recovery.status)}" role="status"><div class="workspace-state__head"><div><span class="eyebrow">SAFE RECOVERY</span><h2>Workspace local cần chú ý</h2></div>${statusPill(recovery.status, recovery.status === "partial_recovery" ? "Đã phục hồi một phần" : "Cần phục hồi")}</div><p>${escapeHtml(recovery.reason || "Hub giữ nguyên local metadata chưa hợp lệ.")}</p><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(recovery.action || "Export/import lại manifest đã được validate.")}</span></div></section>`;
};

const projectOptions = (projects, selected = "", label = "Chọn project…") => `<option value="">${escapeHtml(label)}</option>${projects.map((project) => `<option value="${escapeHtml(project.id)}" ${project.id === selected ? "selected" : ""}>${escapeHtml(project.title)}${project.status === "archived" ? " · archived" : ""}</option>`).join("")}`;

const recipeOptions = (recipes, selected = "", label = "Không gắn recipe") => `<option value="">${escapeHtml(label)}</option>${recipes.map((recipe) => `<option value="${escapeHtml(recipe.id)}" ${recipe.id === selected ? "selected" : ""}>${escapeHtml(recipe.title)} · v${escapeHtml(recipe.version)}</option>`).join("")}`;

const assetThumb = (asset, extra = "") => {
  const image = asset.preview_url ? `<img src="${escapeHtml(asset.preview_url)}" alt="${escapeHtml(asset.name || asset.id)}" loading="lazy" />` : `<div class="asset-contact-sheet__placeholder" aria-hidden="true">${String(asset.media_type || "artifact").startsWith("video/") ? "▶" : String(asset.media_type || "artifact").startsWith("audio/") ? "♪" : "▧"}</div>`;
  const tags = (asset.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const lineage = asset.lineage?.parent_artifact_id ? `<small>Derived from ${escapeHtml(asset.lineage.parent_artifact_id)}</small>` : asset.lineage?.derived_artifact_ids?.length ? `<small>${escapeHtml(asset.lineage.derived_artifact_ids.length)} derived asset</small>` : "";
  return `<article class="asset-contact-sheet__item ${extra}" data-asset-id="${escapeHtml(asset.id)}">${image}<div class="asset-contact-sheet__copy"><strong title="${escapeHtml(asset.name || asset.id)}">${escapeHtml(asset.name || asset.id)}</strong><span>${escapeHtml(asset.media_type || "artifact")} · ${formatGb(asset.size_bytes)}</span>${lineage}<div class="tag-list">${tags}</div></div></article>`;
};

const renderCreativeProjects = (state) => {
  const creative = state.creative || {};
  const projects = creative.projects || [];
  const recent = creative.recent_projects || [];
  const selectedId = state.selectedProjectId || state.creativeProject?.project?.id || "";
  const selected = state.creativeProject?.project || projects.find((item) => item.id === selectedId);
  const exportAction = selected ? `<button class="button" type="button" data-export-project="${escapeHtml(selected.id)}">Export manifest</button>` : "";
  return `
    <div class="creative-summary"><div><strong>${projects.filter((item) => item.status === "active").length}</strong><span>project đang mở</span></div><div><strong>${creative.assets?.length || 0}</strong><span>artifact thấy được</span></div><div><strong>${creative.recipes?.length || 0}</strong><span>recipe local</span></div></div>
    ${recent.length ? `<section class="creative-recent" aria-label="Recent projects"><strong>Gần đây</strong><div>${recent.map((project) => `<button type="button" class="chip-button" data-project-open="${escapeHtml(project.id)}">${escapeHtml(project.title)}</button>`).join("")}</div></section>` : ""}
    <div class="workspace-grid workspace-grid--two">
      ${card("Tạo creative project", `<form class="stack" data-creative-form="create-project">${field("Tên project", `<input name="title" maxlength="120" required placeholder="Ví dụ: Campaign mùa thu" />`)}${field("Mô tả", `<textarea name="description" maxlength="1000" placeholder="Mục tiêu sáng tạo, audience hoặc deliverable…"></textarea>`)}${field("Tags", `<input name="tags" placeholder="campaign, social, portrait" />`)}<div class="form-actions">${button("Tạo project", "button--primary")}</div><div class="form-result" role="status"></div></form>`) }
      ${card("Mở & phục hồi", `<div class="stack">${field("Project hiện tại", `<select data-project-select>${projectOptions(projects, selectedId)}</select>`)}<p class="small">Manifest được version hóa và chỉ giữ metadata + opaque artifact ID. Hub không copy output, model, đường dẫn máy hoặc secret vào project.</p><div class="form-actions">${exportAction}</div><details class="advanced"><summary>Import project manifest</summary><form class="stack creative-inline-form" data-creative-form="import-project">${field("Conflict", `<select name="conflict"><option value="copy">Copy an toàn</option><option value="skip">Bỏ qua ID trùng</option><option value="replace">Thay thế ID trùng</option></select>`)}${field("Manifest JSON", `<textarea name="manifest" required rows="9" placeholder='{"contract_version":"creative-project-export.v1",…}'></textarea>`)}<div class="form-actions">${button("Validate & import")}</div><div class="form-result" role="status"></div></form></details></div>`, "", "card--flat")}
    </div>
    <section class="creative-project-grid" aria-label="Project library">${projects.map((project) => `<article class="creative-project-card ${project.id === selectedId ? "is-selected" : ""}"><div class="split"><div><span class="eyebrow">${escapeHtml(project.status)}</span><h2>${escapeHtml(project.title)}</h2></div>${statusPill(project.status === "active" ? "operational" : "partial", project.status === "active" ? "Đang làm" : "Archived")}</div><p>${escapeHtml(project.description || "Chưa có mô tả.")}</p><div class="tag-list">${(project.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div><div class="creative-project-card__meta"><span>${escapeHtml(project.asset_count)} asset</span><span>${escapeHtml(project.recipe_count)} recipe</span></div><div class="form-actions"><button class="button button--compact" type="button" data-project-open="${escapeHtml(project.id)}">Mở</button>${project.status === "active" ? `<button class="button button--compact button--danger" type="button" data-project-archive="${escapeHtml(project.id)}">Archive</button>` : `<button class="button button--compact" type="button" data-project-restore="${escapeHtml(project.id)}">Khôi phục</button>`}</div></article>`).join("") || `<div class="empty-state"><strong>Chưa có project</strong><span>Tạo project đầu tiên để gom asset, recipe, workflow và compare board vào một workspace local an toàn.</span></div>`}</section>
    ${selected ? card("Project đang chọn", `<form class="stack" data-creative-form="rename-project" data-project-id="${escapeHtml(selected.id)}">${field("Tên project", `<input name="title" maxlength="120" required value="${escapeHtml(selected.title)}" />`)}${field("Mô tả", `<textarea name="description" maxlength="1000">${escapeHtml(selected.description || "")}</textarea>`)}${field("Tags", `<input name="tags" value="${escapeHtml((selected.tags || []).join(", "))}" />`)}${field("Workflow preset", `<input name="workflow_preset" value="${escapeHtml(selected.workflow_preset || "")}" placeholder="Tùy chọn, preset ID đã biết" />`)}<div class="form-actions">${button("Lưu metadata")}</div><div class="form-result" role="status"></div></form>`, "", "card--wide") : ""}`;
};

const renderCreativeAssets = (state) => {
  const creative = state.creative || {};
  const filters = state.assetFilters || {};
  const source = creative.assets || [];
  const needle = String(filters.query || "").toLowerCase();
  const tag = String(filters.tag || "").toLowerCase();
  const collections = creative.collections || [];
  const assets = source.filter((asset) => (!needle || `${asset.name || ""} ${(asset.tags || []).join(" ")}`.toLowerCase().includes(needle)) && (!tag || (asset.tags || []).includes(tag)) && (!filters.favorite || asset.favorite) && (!filters.collection || (asset.collections || []).some((item) => item.id === filters.collection)));
  const selected = state.creativeProject?.project;
  const projectAssets = state.creativeProject?.assets || [];
  return `<div class="workspace-grid workspace-grid--two">
    ${card("Tìm trong Asset Library", `<form class="stack" data-creative-form="asset-filter">${field("Tìm kiếm", `<input name="query" value="${escapeHtml(filters.query || "")}" placeholder="Tên artifact hoặc tag" />`)}${field("Tag", `<input name="tag" value="${escapeHtml(filters.tag || "")}" placeholder="Ví dụ: favorite, portrait" />`)}${field("Collection", `<select name="collection"><option value="">Tất cả collection</option>${collections.map((collection) => `<option value="${escapeHtml(collection.id)}" ${filters.collection === collection.id ? "selected" : ""}>${escapeHtml(collection.title)}</option>`).join("")}</select>`)}<label class="node-toggle"><input name="favorite" type="checkbox" ${filters.favorite ? "checked" : ""} /> Chỉ favorite</label><div class="form-actions">${button("Lọc library")}</div></form><p class="small">Contact sheet tái sử dụng artifact đã có trong Hub. Mỗi record chỉ public opaque ID và URL artifact được Hub kiểm soát.</p>`) }
    ${card("Collections", `<form class="stack" data-creative-form="create-collection">${field("Tên collection", `<input name="title" required maxlength="100" placeholder="Ví dụ: Hero candidates" />`)}${field("Tags", `<input name="tags" placeholder="portrait, final" />`)}<div class="form-actions">${button("Tạo collection")}</div><div class="form-result" role="status"></div></form><div class="creative-collection-list">${collections.map((collection) => `<span class="tag">${escapeHtml(collection.title)} · ${escapeHtml(collection.asset_count)}</span>`).join("") || "<span class=\"muted small\">Chưa có collection.</span>"}</div>`, "", "card--flat")}
  </div>
  <section class="asset-contact-sheet" aria-label="Asset contact sheet">${assets.map((asset) => `<div class="asset-library-card">${assetThumb(asset)}<div class="asset-contact-sheet__actions"><button class="button button--compact" type="button" data-asset-favorite="${escapeHtml(asset.id)}" data-next-favorite="${asset.favorite ? "false" : "true"}">${asset.favorite ? "Bỏ favorite" : "Favorite"}</button>${String(asset.media_type || "").startsWith("image/") ? `<button class="button button--compact" type="button" data-open-image-mask-studio="${escapeHtml(asset.id)}">Chỉnh sửa & Mask</button>` : ""}${selected && !projectAssets.some((item) => item.id === asset.id) ? `<button class="button button--compact" type="button" data-attach-asset="${escapeHtml(asset.id)}">Thêm vào project</button>` : ""}</div><form class="asset-tag-form" data-creative-form="asset-tags" data-asset-id="${escapeHtml(asset.id)}">${field("Tags", `<input name="tags" value="${escapeHtml((asset.tags || []).join(", "))}" aria-label="Tags for ${escapeHtml(asset.name || asset.id)}" />`)}<button class="button button--compact" type="submit">Lưu tag</button></form>${collections.length ? `<form class="asset-tag-form" data-creative-form="asset-collection" data-asset-id="${escapeHtml(asset.id)}">${field("Collection", `<select name="collection_id" aria-label="Collection for ${escapeHtml(asset.name || asset.id)}">${collections.map((collection) => `<option value="${escapeHtml(collection.id)}">${escapeHtml(collection.title)}</option>`).join("")}</select>`)}<button class="button button--compact" type="submit">Thêm collection</button></form>` : ""}</div>`).join("") || `<div class="empty-state"><strong>Không có asset phù hợp</strong><span>Upload hoặc chạy workflow hiện có để Hub đăng ký artifact, rồi quay lại đây để gắn metadata non-destructive.</span></div>`}</section>
  ${selected ? cardDynamic(`Asset của ${selected.title}`, `<div class="asset-contact-sheet asset-contact-sheet--compact">${projectAssets.map((asset) => assetThumb(asset)).join("") || `<div class="empty-state compact">Project chưa tham chiếu asset Hub nào.</div>`}</div>`, "", "card--wide") : ""}`;
};

const renderCreativeRecipes = (state) => {
  const creative = state.creative || {};
  const recipes = creative.recipes || [];
  const projects = creative.projects || [];
  const selectedId = state.selectedProjectId || state.creativeProject?.project?.id || "";
  return `<div class="workspace-grid workspace-grid--two">
    ${card("Tạo prompt & recipe", `<form class="stack" data-creative-form="create-recipe">${field("Tên recipe", `<input name="title" required maxlength="120" placeholder="Ví dụ: Cinematic portrait" />`)}${field("Prompt template", `<textarea name="prompt_template" rows="4" placeholder="A {{subject}} in a studio…"></textarea>`)}${field("Variables", `<input name="variables" placeholder="subject|Chủ thể|person|required; mood|Mood|warm" />`)}${field("Style block", `<textarea name="style_block" rows="2" placeholder="cinematic editorial lighting"></textarea>`)}${field("Negative block", `<textarea name="negative_block" rows="2" placeholder="blur, watermark"></textarea>`)}<div class="form-grid">${field("Model", `<input name="model" value="flux" />`)}${field("Seed", `<input name="seed" type="number" min="0" value="42" />`)}${field("Width", `<input name="width" type="number" min="256" value="768" />`)}${field("Height", `<input name="height" type="number" min="256" value="768" />`)}${field("Steps", `<input name="steps" type="number" min="1" value="20" />`)}</div>${field("Workflow preset", `<input name="workflow_preset" placeholder="Optional tracked preset ID" />`)}${field("Gắn project", `<select name="project_id">${projectOptions(projects, selectedId, "Không gắn project")}</select>`)}${field("Tags", `<input name="tags" placeholder="portrait, social" />`)}<div class="form-actions">${button("Lưu recipe", "button--primary")}</div><div class="form-result" role="status"></div><p class="small">Variable syntax: <code>name|Nhãn|default|required</code>. Settings được validate là JSON an toàn trước khi lưu.</p></form>`) }
    ${card("Recipe Pack", `<p class="small">Export chỉ chứa recipe/version/settings an toàn; không có model, output cá nhân, secret hoặc đường dẫn máy.</p><div class="form-actions"><button class="button" type="button" data-export-recipe-pack>Export toàn bộ recipe</button></div><details class="advanced"><summary>Import Recipe Pack</summary><form class="stack creative-inline-form" data-creative-form="import-recipe-pack">${field("Conflict", `<select name="conflict"><option value="copy">Copy an toàn</option><option value="skip">Bỏ qua ID trùng</option><option value="replace">Thay thế ID trùng</option></select>`)}${field("Pack JSON", `<textarea name="pack" required rows="9" placeholder='{"contract_version":"creative-recipe-pack.v1",…}'></textarea>`)}<div class="form-actions">${button("Validate & import")}</div><div class="form-result" role="status"></div></form></details>`, "", "card--flat")}
  </div>
  <section class="creative-recipe-grid" aria-label="Recipe library">${recipes.map((recipe) => `<article class="creative-recipe-card"><div class="split"><div><span class="eyebrow">RECIPE · v${escapeHtml(recipe.version)}</span><h2>${escapeHtml(recipe.title)}</h2></div><span class="tag">${escapeHtml(recipe.model)}</span></div><p>${escapeHtml(recipe.prompt_template || "Không có prompt template.")}</p>${recipe.style_block ? `<p class="small"><strong>Style:</strong> ${escapeHtml(recipe.style_block)}</p>` : ""}${recipe.negative_block ? `<p class="small"><strong>Negative:</strong> ${escapeHtml(recipe.negative_block)}</p>` : ""}<div class="tag-list">${(recipe.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}${(recipe.variables || []).map((variable) => `<span class="tag">${escapeHtml(variable.name)}</span>`).join("")}</div><form class="stack creative-inline-form" data-creative-form="apply-recipe" data-recipe-id="${escapeHtml(recipe.id)}">${(recipe.variables || []).map((variable) => fieldDynamic(variable.label || variable.name, `<input name="variable_${escapeHtml(variable.name)}" value="${escapeHtml(variable.default || "")}" ${variable.required ? "required" : ""} />`)).join("")}${field("Đích áp dụng", `<select name="target"><option value="quick">Image AI Quick</option><option value="nodes">Image Hub Nodes</option></select>`)}<div class="form-actions">${button("Áp dụng recipe")}</div><div class="form-result" role="status"></div></form></article>`).join("") || `<div class="empty-state"><strong>Chưa có recipe</strong><span>Tạo recipe để dùng lại prompt, style, negative, seed, model và settings trên Quick hoặc Node Studio.</span></div>`}</section>`;
};

const renderCreativeCompare = (state) => {
  const detail = state.creativeProject || {};
  const project = detail.project;
  const board = detail.compare || {};
  const assets = detail.assets || [];
  if (!project) return `<div class="empty-state"><strong>Chọn một project trước</strong><span>Compare Board chỉ làm việc với artifact opaque đã được project hiện tại tham chiếu.</span></div>`;
  const differences = Object.entries(board.differences || {});
  return `<div class="workspace-grid workspace-grid--two">
    ${card("Thêm artifact vào A/B board", `<form class="stack" data-creative-form="compare-add" data-project-id="${escapeHtml(project.id)}">${field("Artifact", `<select name="artifact_id">${assets.map((asset) => `<option value="${escapeHtml(asset.id)}">${escapeHtml(asset.name || asset.id)}</option>`).join("") || "<option value=\"\">Project chưa có artifact</option>"}</select>`)}${field("Nhãn", `<input name="label" maxlength="100" placeholder="A · portrait warm" />`)}<div class="form-actions">${button("Thêm vào compare")}</div><div class="form-result" role="status"></div></form><p class="small">Tối đa 8 artifact; thông tin diff chỉ chứa metadata/provenance an toàn của Hub.</p>`) }
    ${card("Lựa chọn & quay lại recipe", `<p>${board.selected_artifact_id ? `Đang chọn ${escapeHtml(board.selected_artifact_id)}` : "Chưa chọn kết quả ưu tiên."}</p><p class="small">Favorite có tính non-destructive; chọn recipe tại từng card để quay về workflow đã tạo kết quả.</p>`, "", "card--flat")}
  </div>
  <section class="compare-board" aria-label="Artifact compare board">${(board.items || []).map((item) => `<article class="compare-board__item ${item.artifact_id === board.selected_artifact_id ? "is-selected" : ""}">${assetThumb(item.asset || { id: item.artifact_id })}<div class="compare-board__body"><strong>${escapeHtml(item.label || item.artifact_id)}</strong>${item.recipe ? `<span class="small">Recipe: ${escapeHtml(item.recipe.title)} · v${escapeHtml(item.recipe.version)}</span>` : `<span class="small">Chưa có recipe liên kết</span>`}<div class="form-actions"><button class="button button--compact" type="button" data-compare-select="${escapeHtml(item.artifact_id)}" data-project-id="${escapeHtml(project.id)}">Chọn</button><button class="button button--compact" type="button" data-compare-favorite="${escapeHtml(item.artifact_id)}" data-project-id="${escapeHtml(project.id)}">Chọn + favorite</button>${item.recipe ? `<button class="button button--compact" type="button" data-apply-recipe-quick="${escapeHtml(item.recipe.id)}">Mở recipe</button>` : ""}</div></div></article>`).join("") || `<div class="empty-state"><strong>Compare Board trống</strong><span>Thêm tối thiểu hai artifact để xem khác biệt metadata, settings và provenance.</span></div>`}</section>
  ${differences.length ? card("Metadata / settings / provenance diff", `<div class="creative-diff-grid">${differences.map(([fieldName, values]) => `<details><summary>${escapeHtml(fieldName)} · ${values.length} artifact</summary><pre>${escapeHtml(JSON.stringify(values, null, 2))}</pre></details>`).join("")}</div>`, "", "card--wide") : `<div class="callout">Chưa có khác biệt metadata cần hiển thị. Compare Board sẽ cho diff khi có nhiều artifact với provenance/settings khác nhau.</div>`}`;
};

const renderCreativeGallery = (state) => {
  const creative = state.creative || {};
  const filters = state.galleryFilters || {};
  const needle = String(filters.query || "").toLowerCase();
  const category = String(filters.category || "").toLowerCase();
  const gallery = (creative.gallery || []).filter((item) => (!needle || `${item.title} ${item.description} ${(item.categories || []).join(" ")}`.toLowerCase().includes(needle)) && (!category || (item.categories || []).some((itemCategory) => String(itemCategory).toLowerCase() === category)));
  const categories = [...new Set((creative.gallery || []).flatMap((item) => item.categories || []))].sort();
  return `<div class="workspace-grid workspace-grid--two">
    ${card("Khám phá template", `<form class="stack" data-creative-form="gallery-filter">${field("Tìm kiếm", `<input name="query" value="${escapeHtml(filters.query || "")}" placeholder="Tên, mô tả hoặc category" />`)}${field("Category", `<select name="category"><option value="">Tất cả category</option>${categories.map((item) => `<option value="${escapeHtml(item)}" ${item === filters.category ? "selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select>`)}<div class="form-actions">${button("Lọc gallery")}</div></form>`) }
    ${card("Capability preflight", `<p class="small">Mỗi card bên dưới đọc availability từ node registry hiện tại. Template partial/unavailable vẫn có thể được xem, nhưng Hub sẽ nêu rõ reason/action thay vì hứa backend đã chạy.</p>`, "", "card--flat")}
  </div>
  <section class="creative-gallery-grid" aria-label="Workflow template gallery">${gallery.map((item) => `<article class="creative-gallery-card"><div class="creative-gallery-card__preview" aria-hidden="true"><span>⌘</span><small>${escapeHtml(item.preview?.label || "workflow")}</small></div><div class="split"><div><span class="eyebrow">${escapeHtml(item.scope)} · ${escapeHtml(item.stage)}</span><h2>${escapeHtml(item.title)}</h2></div>${statusPill(item.status)}</div><p>${escapeHtml(item.description || "Tracked workflow template.")}</p><div class="tag-list">${(item.categories || []).map((categoryName) => `<span class="tag">${escapeHtml(categoryName)}</span>`).join("")}</div><div class="workspace-state creative-gallery-card__availability" data-status="${escapeHtml(item.status)}"><p>${escapeHtml(item.availability?.reason || "Chưa có availability detail.")}</p><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(item.availability?.action || "Mở template trong Hub Nodes.")}</span></div></div><div class="form-actions"><button class="button button--compact" type="button" data-gallery-use="${escapeHtml(item.id)}" data-gallery-scope="${escapeHtml(item.scope)}">Mở trong Hub Nodes</button><span class="small">${escapeHtml(item.node_count)} node</span></div></article>`).join("") || `<div class="empty-state"><strong>Không có template phù hợp</strong><span>Thử bỏ bớt điều kiện tìm kiếm hoặc kiểm tra workflow tracked.</span></div>`}</section>`;
};

function renderCreativeWorkspace(state) {
  const creative = state.creative || {};
  if (state.creativeLoading && !creative.contract_version) return heading("CREATIVE WORKSPACE", "Projects & Recipes", "Đang tải metadata local an toàn từ Hub.") + `<div class="empty-state" role="status"><strong>Đang tải Creative Workspace…</strong><span>Không đọc hoặc hiển thị raw filesystem path.</span></div>`;
  const active = state.creativeTab || "projects";
  const content = workflowLibraryState(state.workflowLibrary) + (active === "assets" ? renderCreativeAssets(state) : active === "recipes" ? renderCreativeRecipes(state) : active === "compare" ? renderCreativeCompare(state) : active === "gallery" ? renderCreativeGallery(state) : renderCreativeProjects(state));
  return heading("CREATIVE WORKSPACE", "Projects, Assets & Recipes", "Tổ chức creative work theo project → artifact → recipe → workflow → compare. Local metadata được version hóa, an toàn và không đóng gói output/model/secrets.", `<button class="button" type="button" data-refresh-creative>Làm mới workspace</button>`) + creativeTabs(state) + creativeRecovery(creative.recovery) + content;
}

function renderJobs(state) {
  const recovery = jobRecoverySnapshot(state);
  const filter = state.jobFilter || "all";
  const filtered = recovery.records.filter((job) => filter === "all" || (filter === "active" && ["queued", "starting", "running", "cancelling"].includes(job.status)) || (filter === "attention" && ["failed", "unavailable", "cancelled", "interrupted"].includes(job.status)) || (filter === "completed" && job.status === "completed"));
  const rows = filtered.map((job) => {
    const durable = job.source === "durable";
    const active = ["queued", "starting", "running", "cancelling"].includes(job.status);
    const actions = active && !durable && job.actionId
      ? `<button class="button button--compact button--danger" type="button" data-focus-key="job-action-cancel" data-cancel-job="${escapeHtml(job.actionId)}">Cancel</button>`
      : job.resumable === true && job.actionId
        ? durable
          ? `<button class="button button--compact" type="button" data-focus-key="job-action-resume-durable" data-resume-durable-job="${escapeHtml(job.id)}">${job.status === "cancelled" ? "Resume" : "Retry"}</button>`
          : `<button class="button button--compact" type="button" data-focus-key="job-action-resume" data-resume-job="${escapeHtml(job.id)}">${job.status === "cancelled" ? "Resume" : "Retry"}</button>`
        : "";
    const timestampRows = [
      ["Created", job.createdAt],
      ["Started", job.startedAt],
      ["Updated", job.updatedAt],
      ["Finished", job.finishedAt],
    ].filter(([, value]) => value).map(([label, value]) => `<span><strong data-i18n="${escapeHtml(label)}">${uiTextHtml(label)}</strong>${escapeHtml(value)}</span>`).join("");
    const timestamps = timestampRows ? `<div class="job-timestamps">${timestampRows}</div>` : `<div class="job-timestamps"><span data-i18n="Lifecycle timestamps not published in this snapshot.">${uiTextHtml("Lifecycle timestamps not published in this snapshot.")}</span></div>`;
    const artifactSummary = job.artifacts.length
      ? `<div class="job-artifact-summary" data-artifact-state="available"><strong data-i18n="Artifact preview">${uiTextHtml("Artifact preview")}</strong><span>${escapeHtml(String(job.artifacts.length))} available</span></div>${artifactList(job.artifacts)}`
      : `<div class="job-artifact-summary" data-artifact-state="unavailable"><strong data-i18n="Artifact preview unavailable">${uiTextHtml("Artifact preview unavailable")}</strong><span data-i18n="Preview unavailable in this snapshot.">${uiTextHtml("Preview unavailable in this snapshot.")}</span></div>`;
    const recoveryReason = job.reason || "No recovery reason was published in this snapshot.";
    const lifecycle = job.lifecycle || "Not published";
    return `<article class="job-card" id="job-${escapeHtml(job.id)}" data-job-id="${escapeHtml(job.id)}" data-job-source="${escapeHtml(job.source)}" data-job-status="${escapeHtml(job.status)}" data-job-resumable="${job.resumable === true}"><div class="split"><div><div class="job-card__title"><strong>${escapeHtml(job.tool)}</strong><span class="tag job-source" data-job-source-label="${escapeHtml(job.source)}">${escapeHtml(job.sourceLabel)}</span></div><div class="row-meta">${escapeHtml(job.id)}${job.contractVersion ? ` · ${escapeHtml(job.contractVersion)}` : ""}</div></div>${statusPill(job.status, readinessStatusLabel(job.status))}</div><div class="job-lifecycle"><span data-i18n="Lifecycle">${uiTextHtml("Lifecycle")}</span><strong>${escapeHtml(lifecycle)}</strong></div><div class="progress-track" role="progressbar" aria-label="${escapeHtml(job.id)} progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${job.progress}"><div class="progress-bar" style="width:${job.progress}%"></div></div>${job.lifecycleNote ? `<p class="job-message">${escapeHtml(job.lifecycleNote)}</p>` : ""}<div class="job-recovery-detail"><div><span data-i18n="Recovery reason">${uiTextHtml("Recovery reason")}</span><p>${escapeHtml(recoveryReason)}</p></div><div><span data-i18n="Next action">${uiTextHtml("Next action")}</span><p>${escapeHtml(job.nextAction)}</p></div></div>${timestamps}${provenanceList(job.provenance)}${artifactSummary}<div class="form-actions">${actions || `<span class="job-action-note" data-i18n="No recovery action is available from this server snapshot.">${uiTextHtml("No recovery action is available from this server snapshot.")}</span>`}</div></article>`;
  }).join("");
  const filters = [["all", "All"], ["active", "Active"], ["attention", "Attention"], ["completed", "Completed"]].map(([id, label]) => `<button class="tab ${filter === id ? "is-selected" : ""}" type="button" data-focus-key="job-filter-${id}" data-job-filter="${id}" aria-pressed="${filter === id}">${label}</button>`).join("");
  const counts = recovery.counts;
  return `<section id="jobs-page" class="jobs-page" data-job-recovery-source="${escapeHtml(recovery.source)}" data-job-recovery-status="${escapeHtml(recovery.status)}">${heading("CONTROL PLANE", "Jobs", "Track server-owned queue and recovery state; actions only appear when the published record gates them.")}<div class="job-recovery-banner card"><div class="card-title-row"><div><span class="eyebrow" data-i18n="RECOVERY SNAPSHOT">${uiTextHtml("RECOVERY SNAPSHOT")}</span><h2 data-i18n="Jobs recovery">${uiTextHtml("Jobs recovery")}</h2><p>${escapeHtml(recovery.reason)}</p></div>${statusPill(recovery.status, readinessStatusLabel(recovery.status))}</div><div class="job-recovery-counts"><div><span data-i18n="Active">${uiTextHtml("Active")}</span><strong>${escapeHtml(String(counts.active))}</strong></div><div><span data-i18n="Attention">${uiTextHtml("Attention")}</span><strong>${escapeHtml(String(counts.attention))}</strong></div><div><span data-i18n="Interrupted">${uiTextHtml("Interrupted")}</span><strong>${escapeHtml(String(counts.interrupted))}</strong></div><div><span data-i18n="Recoverable">${uiTextHtml("Recoverable")}</span><strong>${escapeHtml(String(counts.recoverable))}</strong></div></div><p class="small"><span data-i18n="Source:">${uiTextHtml("Source:")}</span> ${escapeHtml(recovery.source)} · <span data-i18n="execution:">${uiTextHtml("execution:")}</span> ${escapeHtml(recovery.execution)}${recovery.dryRun ? ` · ${uiTextHtml("dry_run:")} true` : ""}</p></div><div class="module-tabs job-filters" role="group" aria-label="Job history filters">${filters}</div><div class="job-action-status" data-job-action-status role="status" aria-live="polite"></div><div class="job-list">${rows || `<div class="empty-state"><strong data-i18n="No jobs in this filter">${uiTextHtml("No jobs in this filter")}</strong><span data-i18n="Choose All to see the complete server snapshot.">${uiTextHtml("Choose All to see the complete server snapshot.")}</span></div>`}</div></section>`;
}

function renderModels(state) {
  const storage = state.storage || {};
  const models = state.models || [];
  const areas = Object.entries(storage.areas || {});
  return heading("STORAGE", "Models & Storage", "Model store canonical không nhân bản. Legacy cleanup chỉ xử lý mục đã phân loại và xác minh, không tự xoá UNKNOWN hoặc user media.", `<button class="button" type="button" data-refresh-storage>Quét lại</button>`) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Dung lượng", `<div class="row-list">${areas.map(([name, value]) => `<div class="row-item"><span>${escapeHtml(name)}</span><strong>${formatGb(value.bytes)}</strong></div>`).join("") || `<div class="empty-state compact">Chưa có số liệu storage.</div>`}</div>`)}
      ${card("Legacy cleanup", `<div class="metric-inline"><strong>${escapeHtml(storage.legacy_counts?.total || 0)}</strong><span>legacy paths đã inventory</span></div><div class="callout callout--warning">Cleanup V3 tách REAL_DIRECTORY/JUNCTION, kiểm tra reference và user data trước. Mục active hoặc unknown sẽ được giữ cùng lý do/rollback.</div>`, "", "card--flat")}
    </div>
    ${card("Model registry", models.length ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th>Engine</th><th>Size</th><th>Status</th></tr></thead><tbody>${models.map((item) => `<tr><td>${escapeHtml(item.model_name)}</td><td>${escapeHtml(item.engine)}</td><td>${formatGb(item.size?.bytes)}</td><td>${statusPill(item.installed ? "installed" : "not_installed")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state compact">Chưa có model registry.</div>`, "", "card--wide")}`;
}

function renderSettings(state) {
  const settings = state.settings || {};
  const snapshot = readinessSnapshot(state);
  return heading("SYSTEM", "Settings", "Cấu hình startup, chính sách GPU, storage và advanced integrations. Không hiển thị secrets hay local machine paths.") + `
    <section class="readiness-page" aria-labelledby="readiness-page-title" data-readiness-source="server-snapshot">
      <section class="readiness-summary card" aria-labelledby="readiness-page-title" data-readiness-status="${escapeHtml(snapshot.status)}">
        <div class="card-title-row"><div><span class="eyebrow" data-i18n="SERVER SNAPSHOT">${uiTextHtml("SERVER SNAPSHOT")}</span><h2 id="readiness-page-title" data-i18n="Readiness & Module Plan">${uiTextHtml("Readiness & Module Plan")}</h2><p data-i18n="Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.">${uiTextHtml("Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.")}</p></div><div class="readiness-summary__pills">${statusPill(snapshot.status, readinessStatusLabel(snapshot.status))}<span class="tag">${escapeHtml(snapshot.resourceSnapshot)}</span></div></div>
        <div class="readiness-summary__metrics"><div><span data-i18n="Overall readiness">${uiTextHtml("Overall readiness")}</span><strong>${escapeHtml(readinessStatusLabel(snapshot.status))}</strong></div><div><span data-i18n="Module plan">${uiTextHtml("Module plan")}</span><strong>${escapeHtml(readinessStatusLabel(snapshot.planStatus))}</strong></div><div><span data-i18n="Modules">${uiTextHtml("Modules")}</span><strong>${escapeHtml(String(snapshot.modules.length))}</strong></div><div><span data-i18n="Execution">${uiTextHtml("Execution")}</span><strong>${escapeHtml(snapshot.execution)}</strong></div></div>
        <div class="readiness-guidance"><div><span data-i18n="Reason">${uiTextHtml("Reason")}</span><p>${escapeHtml(snapshot.reason)}</p></div><div><span data-i18n="Next action">${uiTextHtml("Next action")}</span><p>${escapeHtml(snapshot.nextAction)}</p></div></div>
        <div class="readiness-safety"><span class="tag" data-i18n="dry_run:">${uiTextHtml("dry_run:")} ${snapshot.dryRun ? "true" : "false"}</span><span class="tag" data-i18n="Install / repair / uninstall: explanatory only">${uiTextHtml("Install / repair / uninstall: explanatory only")}</span></div>
      </section>
      <section class="readiness-modules card" aria-labelledby="readiness-modules-title"><div class="card-title-row"><div><span class="eyebrow" data-i18n="MODULE EVIDENCE">${uiTextHtml("MODULE EVIDENCE")}</span><h2 id="readiness-modules-title" data-i18n="Safe module projection">${uiTextHtml("Safe module projection")}</h2><p data-i18n="Rows are limited to server-projected id, provider, component, status, version, reason and next action.">${uiTextHtml("Rows are limited to server-projected id, provider, component, status, version, reason and next action.")}</p></div><span class="tag">${escapeHtml(String(snapshot.modules.length))} rows</span></div><div class="readiness-module-list" role="list">${readinessModuleDetails(snapshot.modules)}</div></section>
      <div class="workspace-grid workspace-grid--two readiness-detail-grid">${readinessResourceDetails(snapshot.resourcePlan)}${readinessStorageDetails(snapshot.volumes)}</div>
      ${mediaEvidencePanel(state, "settings")}
    </section>
    <div class="workspace-grid workspace-grid--two">
      ${card("Appearance & startup", `<div class="row-list"><div class="row-item"><span>Start maximized</span><strong>${settings.start_maximized ? "Bật" : "Tắt"}</strong></div><div class="row-item"><span>Minimum window</span><strong>${escapeHtml(settings.minimum_width || 1280)} × ${escapeHtml(settings.minimum_height || 720)}</strong></div><div class="row-item"><span>Theme</span><button class="button button--compact" type="button" data-cycle-theme>Đổi theme</button></div></div>`)}
      ${card("Workers & lifecycle", `<div class="row-list"><div class="row-item"><span>Model policy</span><strong>${escapeHtml(settings.model_load_policy || "on_demand")}</strong></div><div class="row-item"><span>Heavy GPU slots</span><strong>${escapeHtml(settings.max_heavy_gpu_jobs || 1)}</strong></div><div class="row-item"><span>ComfyUI port</span><strong>${escapeHtml(settings.comfyui_port || 8188)}</strong></div></div><div class="form-actions"><button class="button" type="button" data-close-backends>Đóng backend Hub-owned rảnh</button></div>`)}
      ${card("Storage safety", `<ul class="notice-list"><li>Không ghi đè source media.</li><li>Không duplicate model multi-GB.</li><li>Không tự xoá user media hoặc unknown legacy data.</li><li>AIRI giữ external/installer-managed.</li></ul>`, "", "card--flat")}
      ${card("Advanced legacy", `<details class="advanced"><summary>Legacy applications</summary><p>SAM2 Mask Studio, Anime Upscale Studio và Local Image Studio không nằm trong normal workflow. Giữ lại làm fallback/debug sau khi direct worker được đánh giá.</p></details>`, "", "card--flat")}
    </div>`;
}

export function renderPage(route, state) {
  const pages = { dashboard: renderDashboard, airi: renderAiri, vision: renderVision, sam2: renderSam2, ocr: renderOcr, whisper: renderWhisper, voice: renderVoice, image: renderImageQuickV5, media: renderMedia, animesr: renderAnime, projects: renderCreativeWorkspace, jobs: renderJobs, models: renderModels, settings: renderSettings };
  const pageRoute = route === "video" ? "media" : route;
  const nodeCopy = {
    image: "Compose FLUX/Qwen, SAM2 mask và image transforms trong cùng graph; preset JSON được track, workflow cá nhân autosave local.",
    sam2: "Advanced workflow: Grounding DINO → SAM2 → mask/composite/export. GPU nodes chỉ chạy khi bấm Run Graph.",
    media: "Build video creative graph: input/prompt → transform hoặc generation contract → upscale/interpolate → encode → preview/export. Encode chỉ hiện capability FFmpeg thực tế.",
    animesr: "Advanced order do bạn chọn: Load → AnimeSR → Frame Interpolation → Encode. AnimeSR/RIFE vẫn partial cho tới smoke riêng.",
  };
  const nodeScope = Object.prototype.hasOwnProperty.call(nodeCopy, pageRoute) ? pageRoute : null;
  if (pageRoute === "image" && activeTab(state, "image") === "advanced") {
    return `${imageModuleTabs(state)}${renderComfyAdvancedV5(state)}`;
  }
  if (pageRoute === "image" && activeTab(state, "image") === "studio") {
    return `${imageModuleTabs(state)}${renderImageMaskStudio(state)}`;
  }
  if (pageRoute === "image" && activeTab(state, "image") === "nodes") {
    return `${heading("ADVANCED WORKFLOW", "Image AI Hub Nodes", "Kéo socket trực tiếp, typed sockets, minimap, multi-select và live preview.")}${imageModuleTabs(state)}${nodeStudio("image", nodeCopy.image)}`;
  }
  if (pageRoute === "image") {
    return `${imageModuleTabs(state)}${renderImageQuickV5(state)}`;
  }
  if (nodeScope && activeTab(state, nodeScope) === "nodes") {
    return `${heading("ADVANCED WORKFLOW", `${nodeScope === "sam2" ? "SAM2" : nodeScope === "animesr" ? "AnimeSR" : nodeScope === "media" ? "Media" : "Image AI"} Nodes`, "Node editor chạy offline trong cửa sổ Local AI Hub.")}${moduleTabs(state, nodeScope)}${nodeStudio(nodeScope, nodeCopy[nodeScope])}`;
  }
  const page = (pages[pageRoute] || renderDashboard)(state);
  return nodeScope ? `${moduleTabs(state, nodeScope)}${page}` : page;
}
