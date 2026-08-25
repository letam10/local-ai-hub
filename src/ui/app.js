/*
  FILE NOTE
  - Mục đích: Main controller và UI lifecycle cho Local AI Hub frontend (routing, snapshot state, desktop close bridge, preview layer)
  - Liên kết trực tiếp: src/ui/index.html, src/ui/pages.js, src/ui/node_studio.js, src/ui/image_mask_studio.js, src/ui/i18n.js, src/ui/api.js
  - Vùng ảnh hưởng khi sửa: Toàn bộ giao diện frontend (navigation, render, snapshot continuity, toast, keyboard shortcuts)
*/

import {
  cancelJob,
  closeOwnedBackends,
  createCollection,
  createProject,
  createRecipe,
  addProjectAsset,
  applyRecipe,
  archiveProject,
  addImageMaskLayer,
  applyImageMaskOperation,
  applyImageMaskPreset,
  exportProject,
  exportRecipePack,
  exportImageMask,
  formatGb,
  formatStatus,
  getBootstrap,
  getCapabilities,
  getComfyAdvanced,
  getComfyBridgeWorkflow,
  getComfyBridgeWorkflows,
  getCreativeOverview,
  getImageMaskCompare,
  getImageMaskSession,
  getImageMaskStudioOverview,
  getHealth,
  getJobs,
  deleteJobHistory,
  clearTerminalJobHistory,
  getDurableJobs,
  resumeDurableJob,
  getWorkflowLibrary,
  saveWorkflowLibrary,
  deleteWorkflowLibrary,
  planWorkflowLibraryMigration,
  confirmWorkflowLibraryMigration,
  getLifecycle,
  getModels,
  getComponents,
  createComponentPlan,
  confirmComponentPlan,
  createComponentImportPlan,
  confirmComponentImport,
  createComponentBundlePlan,
  confirmComponentBundle,
  createComponentReusePlan,
  confirmComponentReuse,
  createComponentMaintenancePlan,
  confirmComponentMaintenance,
  getStorage,
  getProject,
  getSettings,
  patchSettings,
  resetSettingsSection,
  getDiagnosticsSnapshot,
  exportDiagnosticsBundle,
  repairVerifyConfig,
  repairInspectRecovery,
  repairClearRecoveryDrafts,
  createBackup,
  listBackups,
  inspectBackup,
  planRestore,
  applyRestore,
  getProjectManifest,
  getProjectMissingArtifacts,
  searchAssets,
  getArtifactStatus,
  importProject,
  importRecipePack,
  importImageMask,
  launchApplication,
  openArtifact,
  resumeJob,
  redoImageMaskSession,
  removeImageMaskLayer,
  restoreImageMaskSnapshot,
  saveImageMaskSession,
  saveComfyBridgeWorkflow,
  scanStorage,
  startComfyAdvanced,
  submitJob,
  updateAsset,
  updateImageMaskLayer,
  updateImageMaskSession,
  updateCollection,
  updateProject,
  updateProjectCompare,
  undoImageMaskSession,
  moveImageMaskLayer,
  createImageMaskSession,
  captureImageMaskPreset,
  linkImageMaskProject,
  uploadFile,
  escapeHtml,
  getUpdateSettings,
  setUpdateSchedule,
  checkComponentUpdate,
  checkAllUpdates,
  planComponentUpdate,
  confirmComponentUpdate,
  rollbackComponentUpdate,
} from "./api.js";
import { disposeNodeStudios, mountNodeStudios } from "./features/node_studio/studio.js";
import { mountImageMaskCanvases } from "./image_mask_studio.js";
import { createWorkflowLibraryAdapter } from "./workflow_library.js";
import { NAVIGATION, jobRecoverySnapshot, renderPage } from "./pages.js";
import { currentLanguage, localizeDocument, setLanguage, translateText } from "./i18n.js";
import { FEATURE_REGISTRY } from "./core/feature_registry.js";
import { confirmComponentInstall, getProductionCatalog, planComponentInstall } from "./shared/api/catalog.js";

globalThis.__localAiHubFrontendStarted = true;

const state = {
  health: {}, capabilities: {}, productization: {}, components: [], componentManager: {}, componentPlans: {}, tools: [], applications: [], jobs: [], durableJobs: [], models: [], storage: {}, settings: {}, lifecycle: {}, comfyAdvanced: {}, comfyWorkflows: [], workspaceTabs: {}, jobFilter: "all", jobQuery: "", jobTypeFilter: "all", jobSort: "newest", jobPage: 1, apiStatus: "loading", apiError: "",
  creative: {}, creativeLoading: false, creativeTab: "projects", selectedProjectId: "", creativeProject: null, assetFilters: {}, galleryFilters: {}, pendingQuickRecipe: null, pendingNodeRecipe: null, pendingGalleryPreset: null, pendingRecipeName: "",
  imageMaskStudio: {}, imageMaskLoading: false, selectedImageMaskSessionId: "", selectedImageMaskLayerId: "", imageMaskSession: null, imageMaskCompare: null, pendingImageMaskSourceId: "",
  workflowLibrary: { status: "partial", reason: "Workflow Library server-owned adapter chưa được V5-D wire.", action: "Tiếp tục local draft; xác nhận endpoint typed trong V5-D trước khi đồng bộ." },
  productionCatalog: { status: "partial", models: [], runtimes: [] }, updateCenter: { settings: { policy: "manual" }, records: [] }, modelFilters: { query: "", category: "", installed: "all" }, modelActionStatus: "", settingsActionStatus: "",
  featureRegistry: FEATURE_REGISTRY,
};
const view = document.querySelector("#module-view");
const nav = document.querySelector("#sidebar-nav");
const topStatus = document.querySelector("#top-status");
const recordFrontendEvent = async (event, route = null) => {
  const recorder = globalThis.pywebview?.api?.frontend?.record;
  if (typeof recorder !== "function") return { status: "unavailable" };
  try { return await recorder(event, route); } catch { return { status: "unavailable" }; }
};
const showFrontendBootstrapFailure = async () => {
  await recordFrontendEvent("frontend_js_bootstrap_failed");
  globalThis.__localAiHubFrontendReady = false;
  const message = "Giao diện Local AI Hub không hoàn tất khởi tạo. Hãy thử lại hoặc khôi phục phiên bản trước.";
  if (topStatus) topStatus.textContent = "Không thể khởi động giao diện";
  if (apiEndpoint) apiEndpoint.textContent = "Frontend chưa sẵn sàng";
  if (view) view.innerHTML = `<section class="empty-state startup-recovery" role="alert"><strong>Không thể khởi động giao diện Local AI Hub</strong><span>${message}</span><span>API có thể vẫn phản hồi, nhưng HTTP không được xem là bằng chứng app đã chạy.</span></section>`;
};
const diskMetric = document.querySelector("#disk-metric");
const gpuMetric = document.querySelector("#gpu-metric");
const apiEndpoint = document.querySelector("#api-endpoint");
const jobSummary = document.querySelector("#job-summary");
const toastRegion = document.querySelector("#toast-region");
const snapshotStatus = document.querySelector("#snapshot-status");
const languageSelect = document.querySelector("#language-select");
const sidebar = document.querySelector(".sidebar");
const sidebarToggle = document.querySelector("#sidebar-toggle");
const artifactPreviewLayer = document.querySelector("#artifact-preview-layer");
const mainContent = document.querySelector("#main-content");
const workflowLibraryAdapter = createWorkflowLibraryAdapter(null, {
  list: getWorkflowLibrary,
  save: ({ entry, expected_revision }) => saveWorkflowLibrary(entry, expected_revision),
  remove: ({ id, expected_revision }) => deleteWorkflowLibrary(id, expected_revision),
  plan_migration: ({ entries }) => planWorkflowLibraryMigration(entries),
  confirm_migration: ({ entries, expected_revision }) => confirmWorkflowLibraryMigration(entries, expected_revision),
});
let routeLoad = null;
let desktopCloseLayer = null;
let artifactPreviewOpener = null;
let disposeImageMaskCanvases = () => {};

const SIDEBAR_PREFERENCE_KEY = "local-ai-hub-sidebar-v1";
const SIDEBAR_PREFERENCE_VERSION = 1;
const MOBILE_NAV_MAX_WIDTH = 980;
const SNAPSHOT_STATUS_TEXT = Object.freeze({
  received: "Snapshot received.",
  deferred: "Snapshot update deferred while preserving your interaction.",
  preserved: "Snapshot applied; your interaction was preserved.",
  unavailable: "Snapshot update unavailable; the current view is preserved.",
});
const FOCUS_TOKEN_SELECTORS = Object.freeze({
  "job-filter-all": '[data-focus-key="job-filter-all"]',
  "job-filter-active": '[data-focus-key="job-filter-active"]',
  "job-filter-attention": '[data-focus-key="job-filter-attention"]',
  "job-filter-completed": '[data-focus-key="job-filter-completed"]',
  "job-action-cancel": '[data-focus-key="job-action-cancel"]',
  "job-action-resume": '[data-focus-key="job-action-resume"]',
  "job-action-resume-durable": '[data-focus-key="job-action-resume-durable"]',
  "artifact-preview-opener": '[data-focus-key="artifact-preview-opener"]',
  "artifact-preview-close": '[data-focus-key="artifact-preview-close"]',
});
const SAFE_FOCUS_TOKENS = new Set(Object.keys(FOCUS_TOKEN_SELECTORS));
const focusTokenSelector = (token) => SAFE_FOCUS_TOKENS.has(token) ? FOCUS_TOKEN_SELECTORS[token] : "";
const setSnapshotStatus = (stateKey) => {
  if (!snapshotStatus) return;
  const key = Object.prototype.hasOwnProperty.call(SNAPSHOT_STATUS_TEXT, stateKey) ? stateKey : "received";
  snapshotStatus.textContent = SNAPSHOT_STATUS_TEXT[key];
  snapshotStatus.dataset.state = key;
};

const safeStorageGet = (key) => {
  try { return window.localStorage?.getItem(key) ?? null; } catch { return null; }
};

const safeStorageSet = (key, value) => {
  try { window.localStorage?.setItem(key, value); } catch { /* Storage can be disabled or unavailable. */ }
};

const readScrollContinuity = () => ({
  windowX: Number(window.scrollX || 0),
  windowY: Number(window.scrollY || 0),
  mainTop: Number(mainContent?.scrollTop || 0),
  viewTop: Number(view?.scrollTop || 0),
});

const restoreScrollContinuity = (scroll) => {
  if (!scroll) return;
  if (mainContent) mainContent.scrollTop = scroll.mainTop;
  if (view) view.scrollTop = scroll.viewTop;
  if (typeof window.scrollTo === "function") window.scrollTo(scroll.windowX, scroll.windowY);
};

const captureFocusContinuity = () => {
  const active = document.activeElement;
  const insideView = Boolean(active && (active === mainContent || active === view || view?.contains(active)));
  const insidePreview = Boolean(active && artifactPreviewLayer?.contains(active));
  const continuity = { activeInside: insideView || insidePreview, token: "", ordinal: 0, scroll: readScrollContinuity() };
  if (!continuity.activeInside) return continuity;
  if (active === mainContent || active === view) { continuity.token = "main"; return continuity; }
  const token = active?.dataset?.focusKey;
  const selector = focusTokenSelector(token);
  if (!selector) return continuity;
  continuity.token = token;
  continuity.ordinal = [...document.querySelectorAll(selector)].indexOf(active);
  return continuity;
};

const focusMainContent = () => {
  if (mainContent && typeof mainContent.focus === "function") mainContent.focus({ preventScroll: true });
};

const restoreFocusContinuity = (continuity, explicitFocus = "") => {
  if (explicitFocus === "main") { focusMainContent(); return; }
  if (!continuity?.activeInside) return;
  if (continuity.token === "main") { focusMainContent(); return; }
  const selector = focusTokenSelector(continuity.token);
  const candidates = selector ? [...document.querySelectorAll(selector)] : [];
  const candidate = candidates[continuity.ordinal] || candidates[0];
  if (candidate && !candidate.disabled && typeof candidate.focus === "function") candidate.focus({ preventScroll: true });
  else focusMainContent();
};

const readSidebarPreference = () => {
  const raw = safeStorageGet(SIDEBAR_PREFERENCE_KEY);
  if (!raw) return false;
  try {
    const preference = JSON.parse(raw);
    return preference?.version === SIDEBAR_PREFERENCE_VERSION && preference.collapsed === true;
  } catch {
    return false;
  }
};

const sidebarState = { desktopCollapsed: readSidebarPreference(), mobileOpen: false };

const isMobileNavigation = () => {
  try {
    if (typeof window.matchMedia === "function") return window.matchMedia(`(max-width: ${MOBILE_NAV_MAX_WIDTH}px)`).matches;
  } catch { /* Use the width fallback when media-query access is unavailable. */ }
  return Number(window.innerWidth || 0) <= MOBILE_NAV_MAX_WIDTH;
};

const syncSidebarState = () => {
  if (!sidebar || !sidebarToggle) return;
  const mobile = isMobileNavigation();
  sidebarToggle.setAttribute("aria-controls", "sidebar");
  if (mobile) {
    sidebar.classList.remove("is-collapsed");
    sidebar.classList.toggle("is-open", sidebarState.mobileOpen);
    sidebarToggle.setAttribute("aria-expanded", String(sidebarState.mobileOpen));
    sidebarToggle.setAttribute("aria-label", sidebarState.mobileOpen ? "Close navigation" : "Open navigation");
    sidebarToggle.setAttribute("aria-label", translateText(sidebarToggle.getAttribute("aria-label")));
    return;
  }
  sidebarState.mobileOpen = false;
  sidebar.classList.remove("is-open");
  sidebar.classList.toggle("is-collapsed", sidebarState.desktopCollapsed);
  sidebarToggle.setAttribute("aria-expanded", String(!sidebarState.desktopCollapsed));
  sidebarToggle.setAttribute("aria-label", sidebarState.desktopCollapsed ? "Expand navigation" : "Collapse navigation");
  sidebarToggle.setAttribute("aria-label", translateText(sidebarToggle.getAttribute("aria-label")));
};

const persistSidebarPreference = () => {
  safeStorageSet(SIDEBAR_PREFERENCE_KEY, JSON.stringify({ version: SIDEBAR_PREFERENCE_VERSION, collapsed: sidebarState.desktopCollapsed }));
};

const toggleSidebar = () => {
  if (isMobileNavigation()) {
    sidebarState.mobileOpen = !sidebarState.mobileOpen;
  } else {
    sidebarState.desktopCollapsed = !sidebarState.desktopCollapsed;
    persistSidebarPreference();
  }
  syncSidebarState();
};

const closeMobileSidebar = () => {
  if (!isMobileNavigation()) return;
  sidebarState.mobileOpen = false;
  syncSidebarState();
};

const dismissDesktopClosePrompt = () => {
  desktopCloseLayer?.remove();
  desktopCloseLayer = null;
};

const desktopApi = () => globalThis.pywebview?.api || null;

const invokeDesktopChoice = async (method, button, status) => {
  const api = desktopApi();
  if (!api?.[method]) {
    status.textContent = "Desktop bridge chưa sẵn sàng; Hub vẫn được giữ mở an toàn.";
    return null;
  }
  if (button) button.disabled = true;
  try {
    const result = await api[method]();
    status.textContent = result?.message || "Đã nhận lựa chọn đóng Local AI Hub.";
    return result;
  } catch (error) {
    status.textContent = `Không thể xử lý lựa chọn: ${error.message || error}`;
    if (button) button.disabled = false;
    return null;
  }
};

const showDesktopClosePrompt = (detail = {}) => {
  dismissDesktopClosePrompt();
  const layer = document.createElement("section");
  layer.className = "desktop-close-prompt";
  layer.setAttribute("role", "dialog");
  layer.setAttribute("aria-modal", "true");
  layer.setAttribute("aria-labelledby", "desktop-close-title");
  const card = document.createElement("div");
  card.className = "desktop-close-prompt__card";
  const verified = detail.verification === "verified" && Number.isInteger(detail.active_jobs) && detail.active_jobs >= 0;
  const canCancel = verified && detail.can_cancel === true && detail.active_jobs > 0;
  const eyebrow = document.createElement("span"); eyebrow.className = "eyebrow"; eyebrow.textContent = verified && detail.active_jobs > 0 ? "JOBS ĐANG HOẠT ĐỘNG" : "KHÔNG THỂ XÁC MINH TÁC VỤ";
  const title = document.createElement("h2"); title.id = "desktop-close-title"; title.textContent = "Bạn muốn xử lý Local AI Hub thế nào?";
  const copy = document.createElement("p");
  const count = verified ? detail.active_jobs : 0;
  copy.textContent = verified && count > 0
    ? `${count} job đang chờ, chuẩn bị, chạy hoặc hủy. Hub không tự dừng worker đang hoạt động.`
    : "Hub chưa đóng vì chưa xác minh được trạng thái tác vụ.";
  const status = document.createElement("p"); status.className = `desktop-close-prompt__status ${detail.kind === "error" ? "is-error" : ""}`; status.setAttribute("role", "status"); status.textContent = detail.message || "Chọn một trong ba cách tiếp tục.";
  const actions = document.createElement("div"); actions.className = "desktop-close-prompt__actions";
  const returnButton = document.createElement("button"); returnButton.className = "button"; returnButton.type = "button"; returnButton.textContent = "Quay lại Hub";
  const cancelButton = document.createElement("button"); cancelButton.className = "button button--danger"; cancelButton.type = "button"; cancelButton.textContent = "Hủy jobs và thoát"; cancelButton.hidden = !canCancel;
  const backgroundButton = document.createElement("button"); backgroundButton.className = "button button--primary"; backgroundButton.type = "button"; backgroundButton.textContent = "Giữ chạy nền vào khay";
  const lockChoicesWhileCancelling = () => {
    returnButton.disabled = true;
    cancelButton.disabled = true;
    backgroundButton.disabled = true;
  };
  returnButton.addEventListener("click", async () => {
    const result = await invokeDesktopChoice("return_to_hub", returnButton, status);
    if (result?.status === "completed") dismissDesktopClosePrompt();
    else if (result?.status === "pending") lockChoicesWhileCancelling();
    else returnButton.disabled = false;
  });
  cancelButton.addEventListener("click", async () => {
    const result = await invokeDesktopChoice("cancel_jobs_and_exit", cancelButton, status);
    if (result?.status === "pending") lockChoicesWhileCancelling();
    else if (result?.status === "error") cancelButton.disabled = false;
  });
  backgroundButton.addEventListener("click", async () => {
    const result = await invokeDesktopChoice("keep_running_in_background", backgroundButton, status);
    if (result?.status === "completed") dismissDesktopClosePrompt();
    else if (result?.status === "pending") lockChoicesWhileCancelling();
    else backgroundButton.disabled = false;
  });
  actions.append(returnButton, cancelButton, backgroundButton);
  const note = document.createElement("small"); note.textContent = "Chạy nền chỉ ẩn cửa sổ sau khi Windows đã tạo biểu tượng khay có lệnh Khôi phục và Thoát.";
  card.append(eyebrow, title, copy, status, actions, note);
  layer.append(card);
  document.body.append(layer);
  desktopCloseLayer = layer;
  returnButton.focus();
};

window.addEventListener("local-ai-hub:close-request", (event) => showDesktopClosePrompt(event.detail || {}));

const routeId = () => {
  const value = window.location.hash.replace(/^#\/?/, "").split("/")[0];
  return NAVIGATION.flatMap((group) => group.items).some(([id]) => id === value) ? value : "dashboard";
};

const safeDisplayMessage = (value, fallback) => typeof value === "string" && value.trim() ? value : fallback;
const setJobActionStatus = (message, kind = "") => {
  const status = document.querySelector("[data-job-action-status]");
  if (!status) return;
  status.textContent = safeDisplayMessage(message, "Hub returned no displayable action message.");
  status.dataset.status = kind;
};
const showToast = (message, kind = "") => {
  const toast = document.createElement("div");
  toast.className = `toast ${kind ? `toast--${kind}` : ""}`;
  toast.textContent = safeDisplayMessage(message, "Hub returned no displayable message.");
  toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 5200);
};

const closeArtifactPreview = ({ restoreFocus = true } = {}) => {
  const opener = artifactPreviewOpener;
  artifactPreviewLayer?.replaceChildren();
  artifactPreviewOpener = null;
  if (!restoreFocus) return;
  if (opener?.isConnected && !opener.disabled && typeof opener.focus === "function") opener.focus({ preventScroll: true });
  else focusMainContent();
};

const legacyShowArtifactPreview = (button) => {
  if (!artifactPreviewLayer) return;
  artifactPreviewOpener = button;
  artifactPreviewLayer.replaceChildren();
  const dialog = document.createElement("section");
  dialog.className = "artifact-preview-dialog";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  const header = document.createElement("header");
  header.className = "artifact-preview-dialog__header";
  const title = document.createElement("strong");
  title.textContent = button.dataset.artifactName || "Artifact preview";
  const close = document.createElement("button");
  close.className = "button button--compact";
  close.type = "button";
  close.dataset.focusKey = "artifact-preview-close";
  close.dataset.closeArtifactPreview = "true";
  close.textContent = "Đóng";
  header.append(title, close);
  const body = document.createElement("div");
  body.className = "artifact-preview-dialog__body";
  const url = button.dataset.artifactUrl || "";
  const mediaType = button.dataset.artifactType || "";
  if (mediaType.startsWith("image/")) {
    const image = document.createElement("img"); image.src = url; image.alt = title.textContent; body.append(image);
  } else if (mediaType.startsWith("video/")) {
    const video = document.createElement("video"); video.src = url; video.controls = true; video.preload = "metadata"; body.append(video);
  } else if (mediaType.startsWith("audio/")) {
    const audio = document.createElement("audio"); audio.src = url; audio.controls = true; audio.preload = "metadata"; body.append(audio);
  } else {
    const note = document.createElement("p"); note.textContent = "Artifact này không có trình phát inline."; body.append(note);
  }
  const save = document.createElement("a");
  save.className = "button button--primary"; save.href = url; save.download = button.dataset.artifactName || "artifact"; save.textContent = "Lưu/Xuất artifact";
  body.append(save);
  dialog.append(header, body);
  artifactPreviewLayer.append(dialog);
  close.focus();
};

const ARTIFACT_URL_PATTERN = /^\/api\/artifacts\/artifact_[a-f0-9]{32}$/;
const safeArtifactPreviewUrl = (value) => {
  const candidate = String(value || "");
  return ARTIFACT_URL_PATTERN.test(candidate) ? candidate : "";
};
const readArtifactDatasetRecord = (value) => {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
};
const safeArtifactRecordValue = (key, value) => {
  if (value === null || value === undefined || value === "") return null;
  if (key === "media_type") return /^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(String(value)) ? String(value) : null;
  if (key === "size_bytes") return Number.isInteger(value) && value >= 0 ? value : null;
  if (key === "attempt") return Number.isInteger(value) && value >= 1 && value <= 10000 ? value : null;
  if (key === "created_at") return /^[0-9T:.+Z-]{8,80}$/.test(String(value)) ? String(value) : null;
  if (key === "sha256" || key === "job_spec_fingerprint") return /^[a-f0-9]{64}$/.test(String(value)) ? String(value) : null;
  if (key === "job_id") return /^jobv5_[a-f0-9]{32}$/.test(String(value)) ? String(value) : null;
  if (key === "adapter_id") return /^[a-z][a-z0-9_.-]{0,63}$/.test(String(value)) ? String(value) : null;
  if (key === "status") return ["queued", "starting", "running", "cancelling", "completed", "failed", "unavailable", "interrupted"].includes(String(value)) ? String(value) : null;
  return null;
};
const appendArtifactRecord = (body, title, record, keys) => {
  const entries = keys.map((key) => [key, safeArtifactRecordValue(key, record[key])]).filter(([, value]) => value !== null);
  if (!entries.length) return;
  const details = document.createElement("details");
  details.className = "artifact-preview-dialog__details";
  const summary = document.createElement("summary");
  summary.textContent = title;
  details.append(summary);
  const list = document.createElement("dl");
  entries.forEach(([key, value]) => {
    const term = document.createElement("dt"); term.textContent = key;
    const description = document.createElement("dd"); description.textContent = String(value);
    list.append(term, description);
  });
  details.append(list);
  body.append(details);
};
const showArtifactPreview = (button) => {
  if (!artifactPreviewLayer) return;
  artifactPreviewOpener = button;
  artifactPreviewLayer.replaceChildren();
  const dialog = document.createElement("section");
  dialog.className = "artifact-preview-dialog";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  const header = document.createElement("header");
  header.className = "artifact-preview-dialog__header";
  const title = document.createElement("strong");
  const rawName = String(button.dataset.artifactName || "Artifact preview").replace(/[\r\n]+/g, " ").split(/[\\/]/).pop().trim();
  title.textContent = rawName || "Artifact preview";
  const close = document.createElement("button");
  close.className = "button button--compact";
  close.type = "button";
  close.dataset.focusKey = "artifact-preview-close";
  close.dataset.closeArtifactPreview = "true";
  close.textContent = "Đóng";
  header.append(title, close);
  const body = document.createElement("div");
  body.className = "artifact-preview-dialog__body";
  const url = safeArtifactPreviewUrl(button.dataset.artifactUrl);
  const mediaTypeCandidate = String(button.dataset.artifactType || "").split(";", 1)[0].trim().toLowerCase();
  const mediaType = /^[a-z0-9!#$&^_.+-]+\/[a-z0-9!#$&^_.+-]+$/.test(mediaTypeCandidate) ? mediaTypeCandidate : "application/octet-stream";
  const isMask = button.dataset.artifactMask === "true";
  if (!url) {
    const note = document.createElement("p");
    note.textContent = "Artifact reference is unavailable; no local path is shown.";
    body.append(note);
  } else if (mediaType.startsWith("image/")) {
    const image = document.createElement("img");
    image.src = url;
    image.alt = isMask ? `Mask raster: ${title.textContent}` : title.textContent;
    if (isMask) image.className = "artifact-preview-image artifact-preview-image--mask";
    body.append(image);
  } else if (mediaType.startsWith("video/")) {
    const video = document.createElement("video");
    video.src = url;
    video.controls = true;
    video.preload = "metadata";
    body.append(video);
  } else if (mediaType.startsWith("audio/")) {
    const audio = document.createElement("audio");
    audio.src = url;
    audio.controls = true;
    audio.preload = "metadata";
    body.append(audio);
  } else {
    const note = document.createElement("p");
    note.textContent = isMask
      ? "Mask raster preview is unavailable for this artifact type; Hub keeps the mask as truthful metadata without browser rasterization."
      : "Artifact này không có trình phát inline; metadata server-owned vẫn được hiển thị bên dưới.";
    body.append(note);
  }
  const metadata = readArtifactDatasetRecord(button.dataset.artifactMeta);
  appendArtifactRecord(body, "Metadata", metadata, ["media_type", "size_bytes", "created_at", "sha256"]);
  const provenance = readArtifactDatasetRecord(button.dataset.artifactProvenance);
  appendArtifactRecord(body, "Provenance", provenance, ["job_id", "job_spec_fingerprint", "adapter_id", "attempt", "status"]);
  if (url) {
    const save = document.createElement("a");
    save.className = "button button--primary";
    save.href = url;
    save.download = title.textContent || "artifact";
    save.textContent = "Lưu/Xuất artifact";
    body.append(save);
  }
  dialog.append(header, body);
  artifactPreviewLayer.append(dialog);
  close.focus();
};

const renderNavigation = () => {
  const active = routeId();
  if (!nav) return;
  nav.setAttribute("aria-label", "Module navigation");
  nav.setAttribute("aria-label", translateText(nav.getAttribute("aria-label")));
  nav.innerHTML = NAVIGATION.map((group, index) => {
    const groupId = `nav-group-${index}`;
    return `
      <section class="nav-group-section" role="group" aria-labelledby="${groupId}">
        <h2 class="nav-group" id="${groupId}">${escapeHtml(translateText(group.group))}</h2>
        <div class="nav-group-items">
          ${group.items.map(([id, label, icon]) => {
            const current = id === active ? ' aria-current="page"' : "";
            const translatedLabel = translateText(label);
            return `<button class="nav-item ${id === active ? "is-active" : ""}" type="button" data-route="${escapeHtml(id)}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"${current}><span class="nav-icon" aria-hidden="true">${escapeHtml(icon)}</span><span class="nav-label">${escapeHtml(translatedLabel)}</span></button>`;
          }).join("")}
        </div>
      </section>`;
  }).join("");
  nav.querySelectorAll(".nav-item").forEach((item) => {
    const label = item.querySelector(".nav-label")?.textContent || "";
    item.setAttribute("aria-label", label);
    item.setAttribute("title", label);
  });
};

const renderApiState = () => {
  if (state.apiStatus === "error") return `<section class="global-state global-state--error" role="alert"><strong>API Hub chưa sẵn sàng</strong><span>${state.apiError || "Kiểm tra listener loopback rồi thử lại."}</span><button class="button button--compact" type="button" data-refresh-api>Thử lại</button></section>`;
  if (state.apiStatus === "loading") return `<section class="global-state global-state--loading" role="status"><strong>Đang tải workspace</strong><span>Đang lấy health, capability và queue snapshot…</span></section>`;
  return "";
};

const TOOL_EXECUTION_READY = new Set(["operational"]);
const applyToolActionGates = () => {
  const tools = new Map((Array.isArray(state.tools) ? state.tools : []).map((item) => [String(item?.name || ""), item]));
  view.querySelectorAll("form[data-job-form]").forEach((form) => {
    const toolId = String(form.dataset.tool || "");
    const item = tools.get(toolId) || {};
    const status = String(item.tool_status || item.status || "unavailable").toLowerCase();
    const ready = TOOL_EXECUTION_READY.has(status);
    const reason = String(item.reason || "Backend chưa có bằng chứng chạy an toàn trong snapshot hiện tại.").slice(0, 240);
    form.dataset.readinessStatus = status;
    form.querySelectorAll("button[type=submit]").forEach((button) => {
      if (button.hasAttribute("disabled") && button.dataset.readinessGate !== "true") return;
      if (!ready) {
        button.disabled = true;
        button.dataset.readinessGate = "true";
        button.setAttribute("aria-disabled", "true");
        button.title = reason;
      } else if (button.dataset.readinessGate === "true") {
        button.disabled = false;
        delete button.dataset.readinessGate;
        button.removeAttribute("aria-disabled");
        button.removeAttribute("title");
      }
    });
  });
};

const updateTopbar = () => {
  const health = state.health || {};
  const disk = health.disk || {};
  const gpu = health.gpu || {};
  const readiness = state.apiStatus === "error"
    ? "unavailable"
    : state.apiStatus === "loading"
      ? "starting"
      : state.apiStatus === "degraded"
        ? "partial"
        : (health.status || "ready");
  topStatus.textContent = `${formatStatus(readiness)} · Workflow trực tiếp`;
  diskMetric.textContent = disk.free_bytes ? `Ổ đĩa ${formatGb(disk.free_bytes)} trống` : "Ổ đĩa —";
  gpuMetric.textContent = gpu.name ? `GPU ${gpu.name}` : "GPU chưa phát hiện";
  if (apiEndpoint) apiEndpoint.textContent = `API ${window.location.host || "127.0.0.1"}`;
  const recovery = jobRecoverySnapshot(state);
  jobSummary.textContent = `Jobs: ${recovery.counts.active} active · ${recovery.counts.total} records`;
};

const render = ({ background = false, focus = "" } = {}) => {
  const continuity = captureFocusContinuity();
  if (background && continuity.token) {
    setSnapshotStatus("deferred");
    updateTopbar();
    return false;
  }
  disposeNodeStudios();
  disposeImageMaskCanvases();
  renderNavigation();
  syncSidebarState();
  view.innerHTML = `${renderApiState()}${renderPage(routeId(), state)}`;
  applyToolActionGates();
  restoreScrollContinuity(continuity.scroll);
  restoreFocusContinuity(continuity, focus);
  setSnapshotStatus(background ? (continuity.activeInside ? "preserved" : "received") : (continuity.activeInside && !focus ? "preserved" : "received"));
  updateTopbar();
  if (view.querySelector("[data-node-studio]")) {
    mountNodeStudios({
      showToast,
      recipeApplication: state.pendingNodeRecipe,
      initialPresetId: state.pendingGalleryPreset,
      onRecipeApplied: () => { state.pendingNodeRecipe = null; },
      onPresetApplied: () => { state.pendingGalleryPreset = null; },
      workflowLibrary: workflowLibraryAdapter,
    });
  }
  if (view.querySelector("[data-mask-canvas]")) {
    disposeImageMaskCanvases = mountImageMaskCanvases(view, {
      onStroke: async ({ studioId, layerId, mode, size, strength, points }) => {
        const session = state.imageMaskSession?.session;
        if (!session || session.id !== studioId) return;
        try {
          await applyImageMaskOperation(studioId, layerId, { base_revision: session.revision, operation: "brush", mode, size, strength, points });
          await refreshImageMaskStudio();
          showToast(`Đã autosave nét ${mode === "subtract" ? "trừ" : "thêm"} mask.`, "success");
        } catch (error) {
          showToast(error.message || "Không thể lưu nét mask.", "error");
        }
      },
      onCancel: () => { /* Escape only discards the unsaved pointer draft. */ },
    });
  }
  if (languageSelect) languageSelect.value = currentLanguage();
  localizeDocument(document);
  void recordFrontendEvent("route_rendered", routeId());
  return true;
};

const applyBootstrap = (payload) => {
  state.apiStatus = "ready";
  state.apiError = "";
  state.health = payload.health || {};
  state.capabilities = payload.capabilities || {};
  state.productization = payload.productization || {};
  state.storage = payload.storage || state.productization.storage || {};
  state.components = payload.components || [];
  state.applications = payload.applications || [];
  state.jobs = payload.jobs || [];
  state.durableJobs = payload.durable_jobs?.records || [];
  state.productionCatalog = payload.production_catalog || state.productionCatalog;
  state.tools = payload.tools || [];
  state.settings = payload.settings || {};
  state.lifecycle = payload.lifecycle || {};
  if (payload.workflow_library && typeof payload.workflow_library === "object") state.workflowLibrary = payload.workflow_library;
};

const confirmFrontendReady = async () => {
  const bridge = await new Promise((resolve) => {
    if (globalThis.pywebview?.api) { resolve(globalThis.pywebview.api); return; }
    // Browser/dev mode has no native pending-health authority to commit.
    if (!globalThis.pywebview) { resolve(null); return; }
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      window.removeEventListener("pywebviewready", onReady);
      resolve(value);
    };
    const onReady = () => finish(globalThis.pywebview?.api || null);
    window.addEventListener("pywebviewready", onReady, { once: true });
    setTimeout(() => finish(globalThis.pywebview?.api || null), 2000);
  });
  if (!bridge?.confirm_frontend_ready) {
    if (!globalThis.pywebview) return true;
    throw new Error("FRONTEND_BRIDGE_UNAVAILABLE");
  }
  try { await bridge.frontend?.record?.("webview_navigation_completed", null); } catch { /* bounded telemetry only */ }
  const result = await bridge.confirm_frontend_ready();
  if (!result || result.status !== "ready") throw new Error("FRONTEND_READY_REJECTED");
  return true;
};

const refreshFast = async ({ quiet = false, renderView = true } = {}) => {
  const [health, jobs, capabilities, durableJobs] = await Promise.allSettled([getHealth(), getJobs(), getCapabilities(), getDurableJobs()]);
  let failed = false;
  if (health.status === "fulfilled") state.health = health.value || {};
  else failed = true;
  if (jobs.status === "fulfilled") state.jobs = jobs.value.jobs || [];
  else failed = true;
  if (capabilities.status === "fulfilled") state.capabilities = capabilities.value || {};
  else failed = true;
  if (durableJobs.status === "fulfilled") state.durableJobs = durableJobs.value?.records || [];
  else failed = true;
  const rendered = ["dashboard", "settings", "jobs"].includes(routeId()) ? render({ background: true }) : false;
  if (failed && state.apiStatus === "ready") state.apiStatus = "degraded";
  if (failed && !quiet) showToast("API đang khởi động hoặc một snapshot nhanh chưa sẵn sàng.", "warning");
  if (failed && rendered !== false) setSnapshotStatus("unavailable");
  return rendered;
};

const refreshCreative = async ({ renderView = true } = {}) => {
  state.creativeLoading = true;
  try {
    const creative = await getCreativeOverview();
    state.creative = creative || {};
    const projects = state.creative.projects || [];
    const selected = state.selectedProjectId && projects.some((item) => item.id === state.selectedProjectId)
      ? state.selectedProjectId
      : (state.creative.recent_projects || [])[0]?.id || projects.find((item) => item.status === "active")?.id || projects[0]?.id || "";
    state.selectedProjectId = selected;
    if (selected) {
      try { state.creativeProject = await getProject(selected); }
      catch { state.creativeProject = null; state.selectedProjectId = ""; }
    } else state.creativeProject = null;
    return state.creative;
  } finally {
    state.creativeLoading = false;
    if (renderView && routeId() === "projects") render();
  }
};

const refreshImageMaskStudio = async ({ renderView = true, before = "", after = "" } = {}) => {
  state.imageMaskLoading = true;
  try {
    const [overviewResult, creativeResult] = await Promise.allSettled([getImageMaskStudioOverview(), getCreativeOverview()]);
    if (overviewResult.status !== "fulfilled") throw overviewResult.reason;
    state.imageMaskStudio = overviewResult.value || {};
    if (creativeResult.status === "fulfilled") state.creative = creativeResult.value || state.creative;
    const sessions = state.imageMaskStudio.sessions || [];
    const selected = state.selectedImageMaskSessionId && sessions.some((item) => item.id === state.selectedImageMaskSessionId)
      ? state.selectedImageMaskSessionId
      : (state.imageMaskStudio.recent_sessions || [])[0]?.id || sessions[0]?.id || "";
    state.selectedImageMaskSessionId = selected;
    if (!selected) {
      state.imageMaskSession = null;
      state.imageMaskCompare = null;
      state.selectedImageMaskLayerId = "";
      return state.imageMaskStudio;
    }
    const detail = await getImageMaskSession(selected);
    state.imageMaskSession = detail || null;
    const layers = detail?.session?.layers || [];
    if (!layers.some((layer) => layer.id === state.selectedImageMaskLayerId)) {
      state.selectedImageMaskLayerId = detail?.session?.active_layer_id || layers.at(-1)?.id || "";
    }
    try { state.imageMaskCompare = await getImageMaskCompare(selected, before, after); }
    catch { state.imageMaskCompare = null; }
    return state.imageMaskStudio;
  } finally {
    state.imageMaskLoading = false;
    if (renderView && routeId() === "image" && state.workspaceTabs.image === "studio") render();
  }
};

const loadRouteData = async ({ scan = false } = {}) => {
  const route = routeId();
  if (route === "models") {
    if (routeLoad) return routeLoad;
    routeLoad = Promise.allSettled([getModels(), scan ? scanStorage() : getStorage(), getProductionCatalog(), getUpdateSettings()]).then((results) => {
      if (results[0].status === "fulfilled") state.models = results[0].value.models || [];
      if (results[1].status === "fulfilled") state.storage = results[1].value || {};
      if (results[2].status === "fulfilled") state.productionCatalog = results[2].value || state.productionCatalog;
      if (results[3].status === "fulfilled") state.updateCenter = { ...state.updateCenter, settings: results[3].value || state.updateCenter.settings };
      render();
    }).catch(() => {}).finally(() => { routeLoad = null; });
    return routeLoad;
  }
  if (route === "components") {
    if (routeLoad) return routeLoad;
    routeLoad = Promise.allSettled([getComponents(), getProductionCatalog()]).then((results) => {
      const payload = results[0].status === "fulfilled" ? results[0].value : {};
      state.componentManager = payload || {};
      if (results[1].status === "fulfilled") state.productionCatalog = results[1].value || state.productionCatalog;
      render();
    }).catch((error) => {
      showToast(error.message || "Không thể tải Component Manager.", "error");
    }).finally(() => { routeLoad = null; });
    return routeLoad;
  }
  if (route === "image") {
    const results = await Promise.allSettled([getLifecycle(), getComfyAdvanced(), getComfyBridgeWorkflows()]);
    if (results[0].status === "fulfilled") state.lifecycle = results[0].value;
    if (results[1].status === "fulfilled") state.comfyAdvanced = results[1].value;
    if (results[2].status === "fulfilled") state.comfyWorkflows = results[2].value.workflows || [];
    try { await refreshImageMaskStudio({ renderView: false }); }
    catch (error) {
      state.imageMaskStudio = { ...state.imageMaskStudio, recovery: { status: "recovery_required", reason: error.message || "Không thể tải Image & Mask Studio.", action: "Kiểm tra API Hub rồi thử lại." } };
      if (state.workspaceTabs.image === "studio") showToast(error.message || "Không thể tải Image & Mask Studio.", "error");
    }
    render();
  }
  if (route === "diagnostics") {
    if (routeLoad) return routeLoad;
    routeLoad = getDiagnosticsSnapshot().then((res) => {
      state.diagnostics = res;
      render();
    }).catch((err) => {
      showToast(err.message || "Không thể tải Diagnostics snapshot.", "error");
    }).finally(() => { routeLoad = null; });
    return routeLoad;
  }
  if (route === "settings") {
    if (routeLoad) return routeLoad;
    routeLoad = Promise.allSettled([getSettings(), listBackups()]).then(([setRes, backRes]) => {
      if (setRes.status === "fulfilled" && setRes.value?.settings) {
        state.settings = setRes.value.settings;
        state.settings_revision = setRes.value.settings_revision;
        state.settingsRecovery = setRes.value.recovery;
      }
      if (backRes.status === "fulfilled" && backRes.value?.backups) {
        state.backups = backRes.value.backups;
      }
      render();
    }).catch(() => {}).finally(() => { routeLoad = null; });
    return routeLoad;
  }
  return undefined;
};

const initialize = async () => {
  await recordFrontendEvent("frontend_bootstrap_started");
  let bootstrapReady = false;
  try {
    applyBootstrap(await getBootstrap());
    bootstrapReady = true;
  } catch (error) {
    state.apiStatus = "error";
    state.apiError = error.message;
    showToast(`API chưa sẵn sàng: ${error.message}`, "warning");
  }
  try {
    const library = await workflowLibraryAdapter.list();
    if (library?.status) state.workflowLibrary = library;
  } catch { /* Keep the explicit partial adapter state. */ }
  render({ focus: "main" });
  await recordFrontendEvent("frontend_rendered", routeId());
  if (bootstrapReady) {
    try {
      await confirmFrontendReady();
    } catch {
      state.apiStatus = "error";
      state.apiError = "FRONTEND_READY_REJECTED";
      render({ background: true });
    }
  }
  globalThis.__localAiHubFrontendReady = bootstrapReady && state.apiStatus !== "error";
  await loadRouteData();
};

const currentTheme = () => safeStorageGet("local-ai-hub-theme") || "system";
const applyTheme = (theme) => {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = theme;
  safeStorageSet("local-ai-hub-theme", theme);
};
const cycleTheme = () => {
  const next = { system: "dark", dark: "light", light: "system" }[currentTheme()];
  applyTheme(next);
  showToast(`Giao diện: ${next === "system" ? "theo hệ thống" : next === "dark" ? "tối" : "sáng"}`);
  render();
};

const toPayload = async (form) => {
  const payload = {};
  for (const element of form.elements) {
    if (!element.name || element.disabled || element.type === "file") continue;
    payload[element.name] = element.type === "checkbox" ? element.checked : element.value;
  }
  for (const fileInput of form.querySelectorAll("input[type=file][data-asset-key]")) {
    if (!fileInput.files?.length) continue;
    const artifacts = await Promise.all([...fileInput.files].map((selected) => uploadFile(selected)));
    payload[fileInput.dataset.assetKey] = fileInput.multiple ? artifacts.map((artifact) => artifact.id) : artifacts[0].id;
  }
  if (payload.points_text) {
    payload.points = payload.points_text.split(";").map((token) => token.trim()).filter(Boolean).map((token) => {
      const [x, y, label = "1"] = token.split(",").map((part) => part.trim());
      return { x: Number(x), y: Number(y), label: Number(label) };
    }).filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
    delete payload.points_text;
  }
  if (payload.box_text) {
    const values = payload.box_text.split(",").map((part) => Number(part.trim()));
    if (values.length === 4 && values.every(Number.isFinite)) payload.box = values;
    delete payload.box_text;
  }
  return payload;
};

const toolForForm = (form, payload) => {
  let tool = form.dataset.tool;
  if (!form.dataset.toolByField || !form.dataset.toolMap) return tool;
  try { tool = JSON.parse(form.dataset.toolMap)[payload[form.dataset.toolByField]] || tool; } catch { /* allowlisted fallback */ }
  return tool;
};

const inlineResult = (form, text, kind = "") => {
  const target = form.querySelector(".form-result");
  if (!target) return;
  target.className = `form-result ${kind ? `form-result--${kind}` : ""}`;
  target.textContent = text;
};

const pointFromEvent = (event, image) => {
  const bounds = image.getBoundingClientRect();
  const x = Math.max(0, Math.min(image.naturalWidth - 1, (event.clientX - bounds.left) * image.naturalWidth / bounds.width));
  const y = Math.max(0, Math.min(image.naturalHeight - 1, (event.clientY - bounds.top) * image.naturalHeight / bounds.height));
  return { x: Math.round(x), y: Math.round(y) };
};

const renderFilePreview = (input) => {
  const preview = input.closest(".field")?.querySelector("[data-file-preview]");
  if (!preview) return;
  if (preview.dataset.objectUrl) URL.revokeObjectURL(preview.dataset.objectUrl);
  preview.replaceChildren(); delete preview.dataset.objectUrl;
  const selected = [...(input.files || [])];
  if (!selected.length) return;
  const note = document.createElement("small");
  note.textContent = selected.length > 1 ? `${selected.length} tệp đã chọn; preview tệp đầu.` : selected[0].name;
  preview.append(note);
  const file = selected[0];
  const source = URL.createObjectURL(file); preview.dataset.objectUrl = source;
  if (file.type.startsWith("image/")) {
    const image = document.createElement("img"); image.src = source; image.alt = `Preview ${file.name}`;
    const sam2Form = input.closest('form[data-tool="segment_from_points"]');
    if (sam2Form) {
      image.classList.add("sam2-selection-preview"); let start = null;
      image.addEventListener("pointerdown", (event) => { start = pointFromEvent(event, image); image.setPointerCapture?.(event.pointerId); });
      image.addEventListener("pointerup", (event) => {
        if (!start) return;
        const end = pointFromEvent(event, image);
        const points = sam2Form.querySelector('[name="points_text"]'); const box = sam2Form.querySelector('[name="box_text"]');
        if (Math.abs(end.x - start.x) < 8 && Math.abs(end.y - start.y) < 8 && points) {
          points.value = [points.value.trim(), `${end.x},${end.y},1`].filter(Boolean).join("; "); showToast(`Đã thêm điểm SAM2: ${end.x}, ${end.y}`);
        } else if (box) { box.value = `${Math.min(start.x, end.x)},${Math.min(start.y, end.y)},${Math.max(start.x, end.x)},${Math.max(start.y, end.y)}`; showToast("Đã chọn box SAM2 trên preview."); }
        start = null;
      });
    }
    preview.append(image);
  } else if (file.type.startsWith("video/") || file.type.startsWith("audio/")) {
    const media = document.createElement(file.type.startsWith("video/") ? "video" : "audio"); media.src = source; media.controls = true; media.preload = "metadata"; preview.append(media);
  }
};

const splitTags = (value) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
const numberOr = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const downloadJson = (name, value) => {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = name; link.hidden = true;
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
};

const recipeVariables = (raw) => String(raw || "").split(";").map((entry) => entry.trim()).filter(Boolean).map((entry) => {
  const [name = "", label = name, fallback = "", required = ""] = entry.split("|").map((part) => part.trim());
  return { name, label: label || name, default: fallback, required: required.toLowerCase() === "required" };
});

const handleCreativeForm = async (form) => {
  const kind = form.dataset.creativeForm;
  const values = Object.fromEntries(new FormData(form).entries());
  if (kind === "asset-filter") {
    state.assetFilters = { query: String(values.query || ""), tag: String(values.tag || "").trim().toLowerCase(), collection: String(values.collection || ""), favorite: values.favorite === "on" };
    render();
    return "Đã áp dụng bộ lọc Asset Library.";
  }
  if (kind === "gallery-filter") {
    state.galleryFilters = { query: String(values.query || ""), category: String(values.category || "") };
    render();
    return "Đã áp dụng bộ lọc template.";
  }
  let result;
  if (kind === "create-project") {
    result = await createProject({ title: values.title, description: values.description || "", tags: splitTags(values.tags) });
    state.selectedProjectId = result.project?.id || "";
  } else if (kind === "rename-project") {
    result = await updateProject(form.dataset.projectId, { title: values.title, description: values.description || "", tags: splitTags(values.tags), workflow_preset: values.workflow_preset || null });
  } else if (kind === "import-project") {
    result = await importProject({ manifest: JSON.parse(String(values.manifest || "{}")), conflict: values.conflict || "copy" });
    state.selectedProjectId = result.project?.id || state.selectedProjectId;
  } else if (kind === "asset-tags") {
    result = await updateAsset(form.dataset.assetId, { tags: splitTags(values.tags) });
  } else if (kind === "asset-collection") {
    const collection = (state.creative.collections || []).find((item) => item.id === values.collection_id);
    if (!collection) throw new Error("Chọn collection hợp lệ trước khi thêm asset.");
    result = await updateCollection(collection.id, { asset_ids: [...new Set([...(collection.asset_ids || []), form.dataset.assetId])] });
  } else if (kind === "create-collection") {
    result = await createCollection({ title: values.title, tags: splitTags(values.tags), asset_ids: [] });
  } else if (kind === "create-recipe") {
    result = await createRecipe({
      title: values.title,
      prompt_template: values.prompt_template || "",
      variables: recipeVariables(values.variables),
      style_block: values.style_block || "",
      negative_block: values.negative_block || "",
      model: values.model || "flux",
      seed: Math.max(0, Math.trunc(numberOr(values.seed, 42))),
      settings: { width: Math.max(256, Math.trunc(numberOr(values.width, 768))), height: Math.max(256, Math.trunc(numberOr(values.height, 768))), steps: Math.max(1, Math.trunc(numberOr(values.steps, 20))) },
      workflow_preset: values.workflow_preset || null,
      project_id: values.project_id || null,
      tags: splitTags(values.tags),
    });
  } else if (kind === "import-recipe-pack") {
    result = await importRecipePack({ pack: JSON.parse(String(values.pack || "{}")), conflict: values.conflict || "copy" });
  } else if (kind === "compare-add") {
    if (!values.artifact_id) throw new Error("Project chưa có artifact để đưa vào Compare Board.");
    result = await updateProjectCompare(form.dataset.projectId, { artifact_id: values.artifact_id, label: values.label || values.artifact_id });
  } else if (kind === "apply-recipe") {
    const recipeId = form.dataset.recipeId;
    const recipeValues = {};
    for (const [key, value] of Object.entries(values)) if (key.startsWith("variable_")) recipeValues[key.slice("variable_".length)] = value;
    result = await applyRecipe(recipeId, { project_id: state.selectedProjectId || undefined, values: recipeValues });
    state.pendingRecipeName = result.recipe?.title || "Recipe";
    if (values.target === "nodes") {
      state.pendingNodeRecipe = result.node_studio;
      state.workspaceTabs.image = "nodes";
    } else {
      state.pendingQuickRecipe = result.quick;
      state.workspaceTabs.image = "quick";
    }
    window.location.hash = "#/image";
    return `Đã áp dụng ${state.pendingRecipeName}.`;
  } else {
    throw new Error("Creative form không được nhận diện.");
  }
  await refreshCreative({ renderView: false });
  render();
  return result?.project?.title ? `Đã cập nhật ${result.project.title}.` : "Đã lưu Creative Workspace local.";
};

const parseSafeObject = (value, label) => {
  const parsed = JSON.parse(String(value || "{}"));
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(`${label} phải là JSON object.`);
  return parsed;
};

const activeImageMaskSession = () => state.imageMaskSession?.session || null;

const handleImageMaskForm = async (form) => {
  const kind = form.dataset.imageMaskForm;
  const session = activeImageMaskSession();
  let result;
  if (kind === "create-session") {
    const payload = await toPayload(form);
    const sourceArtifactId = payload.source_artifact_id || payload.existing_source_artifact_id;
    if (!sourceArtifactId) throw new Error("Chọn hoặc tải một artifact ảnh nguồn trước khi tạo Studio.");
    result = await createImageMaskSession({ title: payload.title || "", source_artifact_id: sourceArtifactId, project_id: payload.project_id || null });
    state.selectedImageMaskSessionId = result.session?.id || "";
    state.selectedImageMaskLayerId = result.session?.active_layer_id || "";
    state.pendingImageMaskSourceId = "";
  } else if (!session) {
    throw new Error("Chọn một phiên Image & Mask Studio trước.");
  } else if (kind === "update-layer") {
    const values = Object.fromEntries(new FormData(form).entries());
    const layerId = form.dataset.layerId;
    const payload = { base_revision: session.revision, name: values.name || "", opacity: numberOr(values.opacity, 1), visible: Boolean(form.querySelector('[name="visible"]')?.checked), active: true };
    if ((session.layers || []).find((layer) => layer.id === layerId)?.kind === "adjustment") {
      payload.adjustment = { kind: values.adjustment_kind || "brightness", settings: parseSafeObject(values.adjustment_settings, "Settings adjustment") };
    }
    result = await updateImageMaskLayer(session.id, layerId, payload);
  } else if (kind === "add-layer") {
    const values = await toPayload(form);
    const payload = { base_revision: session.revision, kind: values.kind, name: values.name || "" };
    if (values.kind === "adjustment") payload.adjustment = { kind: values.adjustment_kind || "brightness", settings: parseSafeObject(values.adjustment_settings, "Settings adjustment") };
    if (values.layer_artifact_id) payload.artifact_id = values.layer_artifact_id;
    if (values.kind === "generated") payload.parent_layer_id = state.selectedImageMaskLayerId || session.active_layer_id;
    result = await addImageMaskLayer(session.id, payload);
    state.selectedImageMaskLayerId = result.layer?.id || state.selectedImageMaskLayerId;
  } else if (kind === "compare") {
    const values = Object.fromEntries(new FormData(form).entries());
    state.imageMaskCompare = await getImageMaskCompare(session.id, values.before_snapshot_id || "", values.after_snapshot_id || "");
    render();
    return "Đã cập nhật so sánh Trước / Sau từ snapshot Studio.";
  } else if (kind === "restore-snapshot") {
    const values = Object.fromEntries(new FormData(form).entries());
    result = await restoreImageMaskSnapshot(session.id, values.snapshot_id, { base_revision: session.revision });
  } else if (kind === "capture-preset") {
    const values = Object.fromEntries(new FormData(form).entries());
    result = await captureImageMaskPreset(session.id, { title: values.title || "", base_revision: session.revision });
  } else if (kind === "import-mask") {
    const values = Object.fromEntries(new FormData(form).entries());
    result = await importImageMask(session.id, { mask: parseSafeObject(values.manifest, "Manifest mask"), base_revision: session.revision });
    state.selectedImageMaskLayerId = result.layer?.id || state.selectedImageMaskLayerId;
  } else if (kind === "link-project") {
    const values = Object.fromEntries(new FormData(form).entries());
    if (!values.project_id) throw new Error("Chọn project trước khi liên kết artifact Studio.");
    result = await linkImageMaskProject(session.id, { project_id: values.project_id, base_revision: session.revision });
  } else {
    throw new Error("Biểu mẫu Image & Mask Studio không được nhận diện.");
  }
  await refreshImageMaskStudio();
  return result?.preset?.title ? `Đã lưu preset ${result.preset.title}.` : result?.layer?.name ? `Đã cập nhật layer ${result.layer.name}.` : "Đã autosave Image & Mask Studio cục bộ.";
};

document.addEventListener("change", (event) => {
  const modelCategory = event.target.closest("[data-model-category]");
  if (modelCategory) { state.modelFilters.category = String(modelCategory.value || "").slice(0, 48); render(); return; }
  const modelInstalled = event.target.closest("[data-model-installed]");
  if (modelInstalled) { state.modelFilters.installed = ["all", "installed", "uninstalled"].includes(modelInstalled.value) ? modelInstalled.value : "all"; render(); return; }
  const jobType = event.target.closest("[data-job-type-filter]");
  if (jobType) { state.jobTypeFilter = String(jobType.value || "all").slice(0, 80); state.jobPage = 1; render(); return; }
  const jobSort = event.target.closest("[data-job-sort]");
  if (jobSort) { state.jobSort = jobSort.value === "oldest" ? "oldest" : "newest"; state.jobPage = 1; render(); return; }
  const language = event.target.closest("#language-select");
  if (language) {
    setLanguage(language.value);
    // Reload from the canonical server snapshot so a language change never
    // translates an already translated text node a second time.
    window.location.reload();
    return;
  }
  const input = event.target.closest("input[type=file][data-asset-key]");
  if (input) renderFilePreview(input);
  const projectSelect = event.target.closest("[data-project-select]");
  if (projectSelect) {
    state.selectedProjectId = projectSelect.value || "";
    refreshCreative().catch((error) => showToast(error.message, "error"));
  }
});

document.addEventListener("load", (event) => {
  const image = event.target.closest?.("[data-asset-preview]");
  if (!image) return;
  image.closest(".asset-preview-frame")?.querySelector("[data-preview-skeleton]")?.remove();
}, true);

document.addEventListener("error", (event) => {
  const image = event.target.closest?.("[data-asset-preview]");
  if (!image) return;
  const frame = image.closest(".asset-preview-frame");
  image.hidden = true;
  frame?.querySelector("[data-preview-skeleton]")?.remove();
  const fallback = frame?.querySelector("[data-preview-fallback]");
  if (fallback) fallback.hidden = false;
}, true);

document.addEventListener("input", (event) => {
  const jobSearch = event.target.closest("[data-job-search]");
  if (jobSearch) { state.jobQuery = String(jobSearch.value || "").slice(0, 120); state.jobPage = 1; render(); return; }
  const modelSearch = event.target.closest("[data-model-search]");
  if (!modelSearch) return;
  state.modelFilters.query = String(modelSearch.value || "").slice(0, 80);
  // Keep the active search control stable while the bounded catalog filters
  // on every keystroke; the old change-only listener left the table stale
  // until a second unrelated control blurred.
  render();
});

document.addEventListener("submit", async (event) => {
  const imageMaskForm = event.target.closest("form[data-image-mask-form]");
  if (imageMaskForm) {
    event.preventDefault();
    const submit = imageMaskForm.querySelector("button[type=submit]"); if (submit) submit.disabled = true;
    inlineResult(imageMaskForm, "Đang validate bản nháp Studio an toàn…");
    try { showToast(await handleImageMaskForm(imageMaskForm), "success"); }
    catch (error) {
      if (imageMaskForm.dataset.imageMaskForm === "link-project" && error?.payload?.status === "pending_project_attach") {
        try { await refreshImageMaskStudio(); } catch { /* Preserve the server error even if refresh is unavailable. */ }
      }
      inlineResult(imageMaskForm, error.message, "error");
      showToast(error.message, "error");
    }
    finally { if (submit) submit.disabled = false; }
    return;
  }
  const creativeForm = event.target.closest("form[data-creative-form]");
  if (creativeForm) {
    event.preventDefault();
    const submit = creativeForm.querySelector("button[type=submit]"); if (submit) submit.disabled = true;
    inlineResult(creativeForm, "Đang kiểm tra dữ liệu local an toàn…");
    try { showToast(await handleCreativeForm(creativeForm), "success"); }
    catch (error) { inlineResult(creativeForm, error.message, "error"); showToast(error.message, "error"); }
    finally { if (submit) submit.disabled = false; }
    return;
  }
  const form = event.target.closest("form[data-job-form]");
  if (!form) return;
  event.preventDefault();
  const submit = form.querySelector("button[type=submit]"); if (submit) submit.disabled = true;
  inlineResult(form, "Đang tải input và tạo job…");
  try {
    const payload = await toPayload(form); const tool = toolForForm(form, payload); const result = await submitJob(tool, payload);
    inlineResult(form, `Đã tạo ${result.job?.id || "job"}. Theo dõi ở Jobs.`, "success"); showToast(`Đã thêm ${tool} vào hàng đợi Hub.`); await refreshFast({ quiet: true });
  } catch (error) { inlineResult(form, error.message, "error"); showToast(error.message, "error"); }
  finally { if (submit) submit.disabled = false; }
});

document.addEventListener("click", async (event) => {
  if (event.target.closest("#sidebar-toggle")) {
    toggleSidebar();
    return;
  }
  if (event.target.closest("[data-close-artifact-preview]")) { closeArtifactPreview(); return; }
  const preview = event.target.closest("[data-preview-artifact]");
  if (preview) { showArtifactPreview(preview); return; }
  if (event.target.closest("[data-refresh-api]")) { await initialize(); return; }
  const refreshComponents = event.target.closest("[data-refresh-components]");
  if (refreshComponents) {
    refreshComponents.disabled = true;
    try { state.componentManager = await getComponents(); render(); showToast("Đã làm mới Component Manager.", "success"); }
    catch (error) { showToast(error.message || "Không thể làm mới Component Manager.", "error"); }
    finally { refreshComponents.disabled = false; }
    return;
  }
  const componentPlanButton = event.target.closest("[data-component-plan]");
  if (componentPlanButton) {
    componentPlanButton.disabled = true;
    try {
      const id = componentPlanButton.dataset.componentPlan || "";
      const type = componentPlanButton.dataset.componentType || "";
      const plan = await createComponentPlan(id, type);
      state.componentPlans[`${type}:${id}`] = plan;
      render();
      showToast("Đã tạo kế hoạch cài đặt server-owned.", "success");
    } catch (error) { showToast(error.message || "Không thể lập kế hoạch component.", "error"); }
    finally { componentPlanButton.disabled = false; }
    return;
  }
  const nativeImportButton = event.target.closest("[data-component-native-import]");
  if (nativeImportButton) {
    nativeImportButton.disabled = true;
    try {
      const bridge = globalThis.pywebview?.api;
      if (!bridge?.component_import || typeof bridge.component_import.select_source !== "function") throw new Error("Desktop bridge chưa sẵn sàng; hãy mở Hub bằng ứng dụng desktop.");
      const selected = await bridge.component_import.select_source(nativeImportButton.dataset.componentNativeImport || "");
      if (selected?.status !== "ready" || !selected.selection_id) throw new Error(selected?.code || "Không nhận được lựa chọn hợp lệ.");
      const plan = await createComponentImportPlan(selected.selection_id, "COPY_INTO_MANAGED_MODELS");
      state.componentPlans[`import:model:${plan.component?.component_id || nativeImportButton.dataset.componentNativeImport}`] = plan;
      render();
      showToast("Đã nhận lựa chọn native; hãy xem và xác nhận kế hoạch import.", "success");
    } catch (error) { showToast(error.message || "Không thể chọn model để import.", "error"); }
    finally { nativeImportButton.disabled = false; }
    return;
  }
  const componentBundleButton = event.target.closest("[data-component-bundle]");
  if (componentBundleButton) {
    componentBundleButton.disabled = true;
    try {
      const id = componentBundleButton.dataset.componentBundle || "";
      const type = componentBundleButton.dataset.componentType || "model";
      const plan = await createComponentBundlePlan(id, type);
      state.componentPlans[`bundle:${type}:${id}`] = plan;
      render();
      showToast("Đã lập gói dependency theo thứ tự server-owned; chưa có download.", "success");
    } catch (error) { showToast(error.message || "Không thể lập gói dependency.", "error"); }
    finally { componentBundleButton.disabled = false; }
    return;
  }
  const componentBundleConfirm = event.target.closest("[data-component-bundle-confirm]");
  if (componentBundleConfirm) {
    componentBundleConfirm.disabled = true;
    try {
      const result = await confirmComponentBundle(componentBundleConfirm.dataset.componentBundleConfirm || "", true);
      showToast(result.next_action || result.reason || "Gói dependency đã được xử lý.", result.status === "completed" ? "success" : "warning");
      state.componentManager = await getComponents(); render();
    } catch (error) { showToast(error.message || "Không thể xác nhận gói dependency.", "error"); }
    finally { componentBundleConfirm.disabled = false; }
    return;
  }
  const componentReuseButton = event.target.closest("[data-component-reuse]");
  if (componentReuseButton) {
    componentReuseButton.disabled = true;
    try {
      const id = componentReuseButton.dataset.componentReuse || "";
      const type = componentReuseButton.dataset.componentType || "model";
      const plan = await createComponentReusePlan(id, type);
      state.componentPlans[`reuse:${type}:${id}`] = plan;
      render();
      showToast("Đã kiểm tra bản cài sẵn; chưa sao chép hoặc tải lại dữ liệu.", "success");
    } catch (error) { showToast(error.message || "Không thể kiểm tra bản cài sẵn.", "error"); }
    finally { componentReuseButton.disabled = false; }
    return;
  }
  const componentReuseConfirm = event.target.closest("[data-component-reuse-confirm]");
  if (componentReuseConfirm) {
    componentReuseConfirm.disabled = true;
    try {
      const result = await confirmComponentReuse(componentReuseConfirm.dataset.componentReuseConfirm || "", true);
      showToast(result.next_action || result.reason || "Đã xử lý reuse bản cài sẵn.", result.status === "completed" ? "success" : "warning");
      state.componentManager = await getComponents(); render();
    } catch (error) { showToast(error.message || "Không thể xác nhận reuse.", "error"); }
    finally { componentReuseConfirm.disabled = false; }
    return;
  }
  const componentConfirm = event.target.closest("[data-component-confirm]");
  if (componentConfirm) {
    componentConfirm.disabled = true;
    try {
      const result = await confirmComponentPlan(componentConfirm.dataset.componentConfirm || "", true);
      showToast(result.next_action || result.reason || "Kế hoạch đã được xử lý theo policy.", result.status === "unavailable" ? "warning" : "success");
      state.componentManager = await getComponents(); render();
    } catch (error) { showToast(error.message || "Không thể xác nhận kế hoạch.", "error"); }
    finally { componentConfirm.disabled = false; }
    return;
  }
  const componentImportConfirm = event.target.closest("[data-component-import-confirm]");
  if (componentImportConfirm) {
    componentImportConfirm.disabled = true;
    try {
      const result = await confirmComponentImport(componentImportConfirm.dataset.componentImportConfirm || "", true);
      showToast(result.next_action || result.reason || "Kế hoạch import đã được xử lý.", result.status === "completed" ? "success" : "warning");
      state.componentManager = await getComponents(); render();
    } catch (error) { showToast(error.message || "Không thể xác nhận import.", "error"); }
    finally { componentImportConfirm.disabled = false; }
    return;
  }
  const maintenanceConfirm = event.target.closest("[data-component-maintenance-confirm]");
  if (maintenanceConfirm) {
    maintenanceConfirm.disabled = true;
    try {
      const result = await confirmComponentMaintenance(maintenanceConfirm.dataset.componentMaintenanceConfirm || "", true);
      showToast(result.next_action || result.reason || "Kế hoạch bảo trì đã được xử lý.", result.status === "completed" ? "success" : "warning");
      state.componentManager = await getComponents(); render();
    } catch (error) { showToast(error.message || "Không thể xác nhận bảo trì.", "error"); }
    finally { maintenanceConfirm.disabled = false; }
    return;
  }
  const productPlanButton = event.target.closest("[data-product-plan]");
  if (productPlanButton) {
    productPlanButton.disabled = true;
    try {
      const plan = await planComponentInstall(productPlanButton.dataset.productPlan || "");
      if (plan?.plan_id) {
        const result = await confirmComponentInstall(plan.plan_id, false);
        state.modelActionStatus = result?.reason || plan.reason || "Đã tạo kế hoạch catalog.";
        showToast(state.modelActionStatus, result?.status === "unavailable" ? "warning" : "success");
      } else {
        state.modelActionStatus = plan?.reason || "Không thể lập kế hoạch catalog.";
        showToast(state.modelActionStatus, "warning");
      }
      render();
    } catch (error) {
      state.modelActionStatus = error.message || "Không thể lập kế hoạch catalog.";
      showToast(state.modelActionStatus, "error");
      render();
    }
    finally { productPlanButton.disabled = false; }
    return;
  }
  const maintenanceButton = event.target.closest("[data-component-maintenance]");
  if (maintenanceButton) {
    maintenanceButton.disabled = true;
    try {
      const id = maintenanceButton.dataset.componentMaintenance || "";
      const type = maintenanceButton.dataset.componentType || "";
      const action = maintenanceButton.dataset.maintenanceAction || "repair";
      const plan = await createComponentMaintenancePlan(id, action);
      state.componentPlans[`${type}:${id}`] = plan;
      render();
      showToast("Đã tạo kế hoạch bảo trì server-owned.", "success");
    } catch (error) { showToast(error.message || "Không thể lập kế hoạch bảo trì.", "error"); }
    finally { maintenanceButton.disabled = false; }
    return;
  }
  if (event.target.closest("#refresh-snapshot")) {
    const button = event.target.closest("#refresh-snapshot");
    button.disabled = true;
    if (snapshotStatus) snapshotStatus.textContent = "Đang nhận snapshot mới…";
    try {
      await refreshFast({ quiet: false, renderView: true });
      if (snapshotStatus) snapshotStatus.textContent = "Đã nhận snapshot mới. Chỉ lần làm mới này đã cập nhật dữ liệu.";
      showToast("Đã làm mới snapshot theo yêu cầu.", "success");
    } catch (error) {
      if (snapshotStatus) snapshotStatus.textContent = "Không thể nhận snapshot mới; dữ liệu hiện tại vẫn được giữ.";
      showToast(error.message || "Không thể làm mới snapshot.", "error");
    } finally { button.disabled = false; }
    return;
  }
  const route = event.target.closest("[data-route], [data-readiness-route]");
  if (route) {
    const nextRoute = route.dataset.route || route.dataset.readinessRoute;
    if (route.dataset.recoveryFocus === "attention") state.jobFilter = "attention";
    if (route.dataset.recoveryFocus === "all") state.jobFilter = "all";
    closeMobileSidebar(); window.location.hash = `#/${nextRoute}`; return;
  }
  if (event.target.closest("[data-refresh-creative]")) {
    try { await refreshCreative(); showToast("Đã làm mới Creative Workspace."); }
    catch (error) { showToast(error.message, "error"); }
    return;
  }
  if (event.target.closest("[data-refresh-image-mask-studio]")) {
    try { await refreshImageMaskStudio(); showToast("Đã làm mới Image & Mask Studio."); }
    catch (error) { showToast(error.message, "error"); }
    return;
  }
  const openImageMaskStudio = event.target.closest("[data-open-image-mask-studio]");
  if (openImageMaskStudio) {
    state.pendingImageMaskSourceId = openImageMaskStudio.dataset.openImageMaskStudio || "";
    state.workspaceTabs.image = "studio";
    window.location.hash = "#/image";
    return;
  }
  const openImageMaskSession = event.target.closest("[data-image-mask-open]");
  if (openImageMaskSession) {
    state.selectedImageMaskSessionId = openImageMaskSession.dataset.imageMaskOpen || "";
    state.selectedImageMaskLayerId = "";
    try { await refreshImageMaskStudio(); }
    catch (error) { showToast(error.message, "error"); }
    return;
  }
  const imageMaskLayerSelect = event.target.closest("[data-image-mask-layer-select]");
  if (imageMaskLayerSelect) {
    state.selectedImageMaskLayerId = imageMaskLayerSelect.dataset.imageMaskLayerSelect || "";
    render();
    return;
  }
  const imageMaskSession = activeImageMaskSession();
  const imageMaskLayerVisible = event.target.closest("[data-image-mask-layer-visible]");
  if (imageMaskLayerVisible && imageMaskSession) {
    imageMaskLayerVisible.disabled = true;
    try {
      await updateImageMaskLayer(imageMaskSession.id, imageMaskLayerVisible.dataset.imageMaskLayerVisible, { base_revision: imageMaskSession.revision, visible: imageMaskLayerVisible.dataset.nextVisible === "true" });
      await refreshImageMaskStudio();
      showToast("Đã cập nhật hiển thị layer.");
    } catch (error) { showToast(error.message, "error"); imageMaskLayerVisible.disabled = false; }
    return;
  }
  const imageMaskLayerMove = event.target.closest("[data-image-mask-layer-move]");
  if (imageMaskLayerMove && imageMaskSession) {
    imageMaskLayerMove.disabled = true;
    try {
      await moveImageMaskLayer(imageMaskSession.id, imageMaskLayerMove.dataset.imageMaskLayerMove, { base_revision: imageMaskSession.revision, direction: imageMaskLayerMove.dataset.direction });
      await refreshImageMaskStudio();
      showToast("Đã sắp xếp lại stack layer.");
    } catch (error) { showToast(error.message, "error"); imageMaskLayerMove.disabled = false; }
    return;
  }
  const imageMaskLayerRemove = event.target.closest("[data-image-mask-layer-remove]");
  if (imageMaskLayerRemove && imageMaskSession) {
    imageMaskLayerRemove.disabled = true;
    try {
      await removeImageMaskLayer(imageMaskSession.id, imageMaskLayerRemove.dataset.imageMaskLayerRemove, { base_revision: imageMaskSession.revision });
      state.selectedImageMaskLayerId = "";
      await refreshImageMaskStudio();
      showToast("Đã gỡ layer khỏi Studio; artifact gốc không bị xóa.");
    } catch (error) { showToast(error.message, "error"); imageMaskLayerRemove.disabled = false; }
    return;
  }
  if (event.target.closest("[data-image-mask-undo]") && imageMaskSession) {
    try { await undoImageMaskSession(imageMaskSession.id, { base_revision: imageMaskSession.revision }); await refreshImageMaskStudio(); showToast("Đã hoàn tác Studio."); }
    catch (error) { showToast(error.message, "error"); }
    return;
  }
  if (event.target.closest("[data-image-mask-redo]") && imageMaskSession) {
    try { await redoImageMaskSession(imageMaskSession.id, { base_revision: imageMaskSession.revision }); await refreshImageMaskStudio(); showToast("Đã làm lại Studio."); }
    catch (error) { showToast(error.message, "error"); }
    return;
  }
  if (event.target.closest("[data-image-mask-save]") && imageMaskSession) {
    try { await saveImageMaskSession(imageMaskSession.id, { base_revision: imageMaskSession.revision }); await refreshImageMaskStudio(); showToast("Đã lưu bản nháp Studio cục bộ.", "success"); }
    catch (error) { showToast(error.message, "error"); }
    return;
  }
  const imageMaskOperation = event.target.closest("[data-image-mask-operation]");
  if (imageMaskOperation && imageMaskSession) {
    const selectedLayer = (imageMaskSession.layers || []).find((layer) => layer.id === state.selectedImageMaskLayerId) || {};
    if (selectedLayer.kind !== "mask") { showToast("Chọn một mask layer trước khi áp dụng thao tác.", "warning"); return; }
    imageMaskOperation.disabled = true;
    try {
      const amount = numberOr(view.querySelector("[data-mask-operation-amount]")?.value, 0.08);
      await applyImageMaskOperation(imageMaskSession.id, selectedLayer.id, { base_revision: imageMaskSession.revision, operation: imageMaskOperation.dataset.imageMaskOperation, amount });
      await refreshImageMaskStudio();
      showToast("Đã autosave thao tác mask non-destructive.", "success");
    } catch (error) { showToast(error.message, "error"); imageMaskOperation.disabled = false; }
    return;
  }
  const imageMaskExport = event.target.closest("[data-image-mask-export]");
  if (imageMaskExport && imageMaskSession) {
    imageMaskExport.disabled = true;
    try {
      const exported = await exportImageMask(imageMaskSession.id, imageMaskExport.dataset.imageMaskExport);
      downloadJson("local-ai-hub-mask-manifest.json", exported.mask);
      showToast("Đã export manifest mask an toàn; không có path hoặc pixel bí mật.", "success");
    } catch (error) { showToast(error.message, "error"); }
    finally { imageMaskExport.disabled = false; }
    return;
  }
  if (event.target.closest("[data-image-mask-apply-preset]") && imageMaskSession) {
    const presetId = view.querySelector("[data-image-mask-preset]")?.value;
    if (!presetId) { showToast("Chọn preset trước khi áp dụng.", "warning"); return; }
    try {
      await applyImageMaskPreset(imageMaskSession.id, presetId, { base_revision: imageMaskSession.revision });
      await refreshImageMaskStudio();
      showToast("Đã áp dụng preset vào stack không phá hủy.", "success");
    } catch (error) { showToast(error.message, "error"); }
    return;
  }
  const creativeTab = event.target.closest("[data-creative-tab]");
  if (creativeTab) { state.creativeTab = creativeTab.dataset.creativeTab || "projects"; render(); return; }
  const projectOpen = event.target.closest("[data-project-open]");
  if (projectOpen) {
    state.selectedProjectId = projectOpen.dataset.projectOpen || "";
    try { await refreshCreative(); } catch (error) { showToast(error.message, "error"); }
    return;
  }
  const projectArchive = event.target.closest("[data-project-archive]");
  if (projectArchive) {
    projectArchive.disabled = true;
    try { await archiveProject(projectArchive.dataset.projectArchive, true); await refreshCreative(); showToast("Đã archive project; artifact gốc không bị xóa."); }
    catch (error) { showToast(error.message, "error"); projectArchive.disabled = false; }
    return;
  }
  const projectRestore = event.target.closest("[data-project-restore]");
  if (projectRestore) {
    projectRestore.disabled = true;
    try { await archiveProject(projectRestore.dataset.projectRestore, false); state.selectedProjectId = projectRestore.dataset.projectRestore || ""; await refreshCreative(); showToast("Đã khôi phục project."); }
    catch (error) { showToast(error.message, "error"); projectRestore.disabled = false; }
    return;
  }
  const exportProjectButton = event.target.closest("[data-export-project]");
  if (exportProjectButton) {
    exportProjectButton.disabled = true;
    try {
      const result = await exportProject(exportProjectButton.dataset.exportProject);
      downloadJson("local-ai-hub-project-manifest.json", result.manifest);
      showToast("Đã export manifest project an toàn.", "success");
    } catch (error) { showToast(error.message, "error"); }
    finally { exportProjectButton.disabled = false; }
    return;
  }
  const exportPack = event.target.closest("[data-export-recipe-pack]");
  if (exportPack) {
    exportPack.disabled = true;
    try { const result = await exportRecipePack(); downloadJson("local-ai-hub-recipe-pack.json", result.pack); showToast("Đã export Recipe Pack an toàn.", "success"); }
    catch (error) { showToast(error.message, "error"); }
    finally { exportPack.disabled = false; }
    return;
  }
  const attachAsset = event.target.closest("[data-attach-asset]");
  if (attachAsset) {
    const projectId = state.selectedProjectId;
    if (!projectId) { showToast("Chọn project trước khi thêm asset.", "warning"); return; }
    attachAsset.disabled = true;
    try { await addProjectAsset(projectId, { artifact_id: attachAsset.dataset.attachAsset }); await refreshCreative(); showToast("Đã tham chiếu artifact vào project; file gốc không bị sao chép.", "success"); }
    catch (error) { showToast(error.message, "error"); attachAsset.disabled = false; }
    return;
  }
  const favoriteAsset = event.target.closest("[data-asset-favorite]");
  if (favoriteAsset) {
    favoriteAsset.disabled = true;
    try { await updateAsset(favoriteAsset.dataset.assetFavorite, { favorite: favoriteAsset.dataset.nextFavorite === "true" }); await refreshCreative(); showToast("Đã cập nhật favorite asset.", "success"); }
    catch (error) { showToast(error.message, "error"); favoriteAsset.disabled = false; }
    return;
  }
  const compareSelect = event.target.closest("[data-compare-select], [data-compare-favorite]");
  if (compareSelect) {
    compareSelect.disabled = true;
    try {
      await updateProjectCompare(compareSelect.dataset.projectId, { selected_artifact_id: compareSelect.dataset.compareSelect || compareSelect.dataset.compareFavorite, favorite_selected: Boolean(compareSelect.dataset.compareFavorite) });
      await refreshCreative(); showToast(compareSelect.dataset.compareFavorite ? "Đã chọn và favorite artifact." : "Đã chọn artifact trên Compare Board.", "success");
    } catch (error) { showToast(error.message, "error"); compareSelect.disabled = false; }
    return;
  }
  const applyQuick = event.target.closest("[data-apply-recipe-quick]");
  const applyNodes = event.target.closest("[data-apply-recipe-nodes]");
  if (applyQuick || applyNodes) {
    const button = applyQuick || applyNodes;
    button.disabled = true;
    try {
      const result = await applyRecipe(button.dataset.applyRecipeQuick || button.dataset.applyRecipeNodes, { project_id: state.selectedProjectId || undefined });
      state.pendingRecipeName = result.recipe?.title || "Recipe";
      if (applyNodes) { state.pendingNodeRecipe = result.node_studio; state.workspaceTabs.image = "nodes"; }
      else { state.pendingQuickRecipe = result.quick; state.workspaceTabs.image = "quick"; }
      window.location.hash = "#/image";
      showToast(`Đã áp dụng ${state.pendingRecipeName}; bạn có thể chỉnh trước khi tạo job.`, "success");
    } catch (error) { showToast(error.message, "error"); button.disabled = false; }
    return;
  }
  const galleryUse = event.target.closest("[data-gallery-use]");
  if (galleryUse) {
    state.pendingGalleryPreset = galleryUse.dataset.galleryUse || null;
    const scope = ["image", "media", "sam2", "animesr"].includes(galleryUse.dataset.galleryScope) ? galleryUse.dataset.galleryScope : "image";
    state.workspaceTabs[scope] = "nodes";
    window.location.hash = `#/${scope}`;
    showToast("Đang mở template trong Hub Nodes; trạng thái backend vẫn theo preflight.");
    return;
  }
  const tab = event.target.closest("[data-workspace-tab]");
  if (tab) {
    const [module, name] = tab.dataset.workspaceTab.split(":");
    state.workspaceTabs[module] = name;
    if (module === "image" && name === "studio") {
      try { await refreshImageMaskStudio(); }
      catch (error) { showToast(error.message, "error"); render(); }
    } else render();
    return;
  }
  const jobFilter = event.target.closest("[data-job-filter]");
  if (jobFilter) { state.jobFilter = jobFilter.dataset.jobFilter || "all"; state.jobPage = 1; render(); return; }
  const jobPageButton = event.target.closest("[data-job-page]");
  if (jobPageButton) {
    state.jobPage = Math.max(1, state.jobPage + (jobPageButton.dataset.jobPage === "next" ? 1 : -1));
    render();
    return;
  }
  const deleteJobButton = event.target.closest("[data-delete-job]");
  if (deleteJobButton) {
    const jobCard = deleteJobButton.closest("[data-job-id]");
    if (jobCard?.dataset.jobStatus && ["queued", "starting", "running", "cancelling"].includes(jobCard.dataset.jobStatus)) {
      showToast("Tác vụ đang chạy không thể xóa khỏi lịch sử.", "warning");
      return;
    }
    const confirmed = window.confirm("Xóa 1 tác vụ khỏi lịch sử? File đầu ra và artifact sẽ được giữ nguyên.");
    if (!confirmed) return;
    deleteJobButton.disabled = true;
    try {
      const result = await deleteJobHistory(deleteJobButton.dataset.deleteJob || "");
      if (result.status !== "deleted") throw new Error(result.message || "Không thể xóa tác vụ khỏi lịch sử.");
      await refreshFast({ quiet: true, renderView: false });
      render();
      showToast(result.message || "Đã xóa khỏi lịch sử; artifact vẫn được giữ nguyên.", "success");
    } catch (error) { showToast(error.message || "Không thể xóa tác vụ khỏi lịch sử.", "error"); deleteJobButton.disabled = false; }
    return;
  }
  const clearHistoryButton = event.target.closest("[data-clear-terminal-history]");
  if (clearHistoryButton) {
    const count = jobRecoverySnapshot(state).records.filter((job) => ["completed", "failed", "unavailable", "cancelled", "interrupted"].includes(job.status) && job.source !== "durable").length;
    const confirmed = window.confirm(`Xóa ${count} tác vụ đã kết thúc khỏi lịch sử? File đầu ra và artifact sẽ được giữ nguyên; tác vụ đang chạy không bị ảnh hưởng.`);
    if (!confirmed) return;
    clearHistoryButton.disabled = true;
    try {
      const result = await clearTerminalJobHistory(true);
      if (result.status !== "completed") throw new Error(result.message || "Không thể xóa lịch sử.");
      state.jobPage = 1;
      await refreshFast({ quiet: true, renderView: false });
      render();
      showToast(result.message || "Đã xóa lịch sử đã kết thúc.", "success");
    } catch (error) { showToast(error.message || "Không thể xóa lịch sử.", "error"); clearHistoryButton.disabled = false; }
    return;
  }
  if (event.target.closest("[data-refresh-diagnostics]")) {
    try {
      state.diagnostics = await getDiagnosticsSnapshot();
      render();
      showToast("Đã làm mới kết quả kiểm tra hệ thống.", "success");
    } catch (error) {
      showToast(error.message || "Không thể làm mới Diagnostics.", "error");
    }
    return;
  }
  if (event.target.closest("[data-export-diagnostics]")) {
    const button = event.target.closest("[data-export-diagnostics]");
    button.disabled = true;
    try {
      const result = await exportDiagnosticsBundle();
      downloadJson("local-ai-hub-diagnostics.json", result.bundle);
      showToast("Đã xuất gói chẩn đoán an toàn (đã redact secrets).", "success");
    } catch (error) {
      showToast(error.message || "Không thể xuất gói chẩn đoán.", "error");
    } finally {
      button.disabled = false;
    }
    return;
  }
  const repairButton = event.target.closest("[data-repair]");
  if (repairButton) {
    const action = repairButton.dataset.repair;
    const outputEl = document.querySelector("#repair-output");
    repairButton.disabled = true;
    try {
      if (action === "verify-config") {
        const res = await repairVerifyConfig();
        if (outputEl) outputEl.innerHTML = `<div class="callout callout--info"><strong>Kết quả kiểm tra cấu hình:</strong> ${escapeHtml(res.result?.reason || "Hoàn tất kiểm tra.")}</div>`;
      } else if (action === "inspect-recovery" || action === "clear-drafts") {
        const res = await getRecoveryDrafts();
        const drafts = Array.isArray(res.drafts) ? res.drafts : [];
        if (!drafts.length) {
          if (outputEl) outputEl.innerHTML = `<div class="callout callout--info"><strong>Trạng thái phục hồi:</strong> Không phát hiện bản nháp sót.</div>`;
          showToast("Không có bản nháp phục hồi nào tồn tại.", "info");
        } else {
          if (outputEl) {
            outputEl.innerHTML = `
              <div class="callout callout--warning">
                <strong>Danh sách bản nháp phục hồi (${drafts.length} bản nháp):</strong>
                <p class="small">Chọn các bản nháp bạn muốn dọn dẹp an toàn:</p>
                <div class="draft-selection-list">
                  ${drafts.map((d) => `
                    <label class="row-item" style="cursor:pointer;">
                      <input type="checkbox" class="draft-checkbox" value="${escapeHtml(d.scope || d.name)}" checked />
                      <span><strong>${escapeHtml(d.scope || d.name)}</strong> (${escapeHtml(String(d.size_bytes || 0))} bytes)</span>
                    </label>
                  `).join("")}
                </div>
                <div class="form-actions" style="margin-top:0.75rem;">
                  <button class="button button--danger" type="button" data-confirm-clear-drafts>Dọn dẹp bản nháp đã chọn</button>
                </div>
              </div>
            `;
          }
        }
      }
    } catch (error) {
      if (outputEl) outputEl.innerHTML = `<div class="callout callout--danger">${escapeHtml(error.message || "Lỗi thao tác bảo trì.")}</div>`;
      showToast(error.message || "Lỗi thao tác bảo trì.", "error");
    } finally {
      repairButton.disabled = false;
    }
    return;
  }
  const clearDraftsConfirmBtn = event.target.closest("[data-confirm-clear-drafts]");
  if (clearDraftsConfirmBtn) {
    const checkboxes = Array.from(document.querySelectorAll(".draft-checkbox:checked"));
    const scopes = checkboxes.map((cb) => cb.value).filter(Boolean);
    if (!scopes.length) {
      showToast("Vui lòng chọn ít nhất một bản nháp để dọn dẹp.", "warning");
      return;
    }
    const confirmed = window.confirm(`Bạn có chắc chắn muốn dọn dẹp ${scopes.length} bản nháp phục hồi đã chọn không?`);
    if (!confirmed) return;
    clearDraftsConfirmBtn.disabled = true;
    try {
      const res = await repairClearRecoveryDrafts(scopes, true);
      showToast(res.message || `Đã dọn dẹp ${scopes.length} bản nháp phục hồi.`, "success");
      const outputEl = document.querySelector("#repair-output");
      if (outputEl) outputEl.innerHTML = `<div class="callout callout--success">${escapeHtml(res.message || "Đã dọn dẹp xong.")}</div>`;
      await loadRouteData();
    } catch (error) {
      showToast(error.message || "Không thể dọn dẹp bản nháp.", "error");
    } finally {
      clearDraftsConfirmBtn.disabled = false;
    }
    return;
  }
  if (event.target.closest("[data-save-settings]")) {
    const saveButton = event.target.closest("[data-save-settings]");
    const expectedRevision = Number(saveButton.dataset.expectedRevision ?? state.settings_revision ?? 0);
    const lang = document.querySelector("#settings-lang")?.value || "vi";
    const theme = document.querySelector("#settings-theme")?.value || "system";
    const maximized = document.querySelector("#settings-maximized")?.checked ?? true;
    const minWidth = Math.max(800, Math.min(3840, Number(document.querySelector("#settings-min-width")?.value || 1280)));
    const minHeight = Math.max(600, Math.min(2160, Number(document.querySelector("#settings-min-height")?.value || 720)));
    const modelPolicy = document.querySelector("#settings-model-policy")?.value || "on_demand";
    const gpuJobs = Number(document.querySelector("#settings-gpu-jobs")?.value || 1);

    saveButton.disabled = true;
    try {
      const result = await patchSettings({
        ui: { language: lang, theme: theme },
        window: { start_maximized: Boolean(maximized), minimum_width: minWidth, minimum_height: minHeight },
        jobs: { model_load_policy: modelPolicy, max_heavy_gpu_jobs: gpuJobs },
      }, expectedRevision);

      if (result.accepted) {
        state.settings = result.settings;
        state.settings_revision = result.settings_revision;
        showToast("Đã lưu cài đặt thành công.", "success");
        render();
      } else if (result.status === "conflict") {
        showToast("Cảnh báo xung đột: Cài đặt đã bị thay đổi ở nơi khác. Đang tải lại...", "warning");
        await loadRouteData();
      }
    } catch (error) {
      showToast(error.message || "Không thể lưu cài đặt.", "error");
    } finally {
      saveButton.disabled = false;
    }
    return;
  }
  const resetSettingsBtn = event.target.closest("[data-reset-settings]");
  if (resetSettingsBtn) {
    const section = resetSettingsBtn.dataset.resetSettings;
    resetSettingsBtn.disabled = true;
    try {
      const result = await resetSettingsSection(section);
      if (result.accepted) {
        state.settings = result.settings;
        state.settings_revision = result.settings_revision;
        showToast(`Đã đặt lại cấu hình phần ${section}.`, "success");
        render();
      }
    } catch (error) {
      showToast(error.message || "Không thể đặt lại cài đặt.", "error");
    } finally {
      resetSettingsBtn.disabled = false;
    }
    return;
  }
  if (event.target.closest("[data-create-backup]")) {
    const button = event.target.closest("[data-create-backup]");
    button.disabled = true;
    try {
      const res = await createBackup();
      if (res.accepted) {
        showToast(`Đã tạo bản sao lưu: ${res.backup_id}`, "success");
        await loadRouteData();
      }
    } catch (error) {
      showToast(error.message || "Không thể tạo backup.", "error");
    } finally {
      button.disabled = false;
    }
    return;
  }
  if (event.target.closest("[data-inspect-backup]")) {
    const select = document.querySelector("#backup-select");
    const backupId = select?.value?.trim();
    const outputEl = document.querySelector("#restore-plan-output");
    const settingsStatus = document.querySelector("#settings-save-status");
    if (!backupId) {
      state.settingsActionStatus = "Vui lòng chọn một bản sao lưu để kiểm tra.";
      if (settingsStatus) settingsStatus.textContent = state.settingsActionStatus;
      showToast(state.settingsActionStatus, "warning");
      return;
    }
    try {
      const insp = await inspectBackup(backupId);
      if (!insp.valid) {
        state.settingsActionStatus = `File backup không hợp lệ: ${(insp.errors || []).join(", ")}`;
        if (settingsStatus) settingsStatus.textContent = state.settingsActionStatus;
        if (outputEl) outputEl.innerHTML = `<div class="callout callout--danger">${escapeHtml(state.settingsActionStatus)}</div>`;
        return;
      }
      const plan = await planRestore(backupId);
      if (!plan.accepted) {
        state.settingsActionStatus = plan.reason || "Không thể lập kế hoạch khôi phục.";
        if (settingsStatus) settingsStatus.textContent = state.settingsActionStatus;
        if (outputEl) outputEl.innerHTML = `<div class="callout callout--danger">${escapeHtml(state.settingsActionStatus)}</div>`;
        return;
      }
      state.settingsActionStatus = `Đã lập kế hoạch khôi phục ${plan.plan_id || ""}; chưa áp dụng thay đổi.`.trim();
      if (settingsStatus) settingsStatus.textContent = state.settingsActionStatus;
      const categories = Object.keys(plan.categories || {}).join(", ");
      const prev = plan.preview || {};
      if (outputEl) {
        outputEl.innerHTML = `
          <div class="callout callout--warning">
            <strong>Kế hoạch khôi phục (Plan ID: ${escapeHtml(plan.plan_id)}):</strong>
            <p>Phạm vi: ${escapeHtml(categories || "Tệp cấu hình")}</p>
            <p>Chi tiết: <strong>${escapeHtml(String(prev.total || 0))}</strong> tệp (Tạo mới: ${escapeHtml(String(prev.create || 0))}, Ghi đè: ${escapeHtml(String(prev.overwrite || 0))}, Bỏ qua mới hơn: ${escapeHtml(String(prev.skip_newer || 0))})</p>
            <div class="form-actions">
              <button class="button button--danger" type="button" data-apply-restore="${escapeHtml(plan.plan_id)}">Xác nhận khôi phục</button>
            </div>
          </div>
        `;
      }
    } catch (error) {
      state.settingsActionStatus = error.message || "Lỗi kiểm tra backup.";
      if (settingsStatus) settingsStatus.textContent = state.settingsActionStatus;
      if (outputEl) outputEl.innerHTML = `<div class="callout callout--danger">${escapeHtml(state.settingsActionStatus)}</div>`;
      showToast(state.settingsActionStatus, "error");
    }
    return;
  }
  const applyRestoreBtn = event.target.closest("[data-apply-restore]");
  if (applyRestoreBtn) {
    const planId = applyRestoreBtn.dataset.applyRestore;
    applyRestoreBtn.disabled = true;
    try {
      const res = await applyRestore(planId, true);
      if (res.accepted) {
        showToast("Đã khôi phục dữ liệu thành công!", "success");
        await initialize();
      } else if (res.status === "conflict") {
        showToast("Cảnh báo xung đột: Trạng thái cấu hình đã thay đổi. Vui lòng lập lại kế hoạch.", "warning");
      } else {
        showToast(res.reason || "Khôi phục thất bại.", "error");
        applyRestoreBtn.disabled = false;
      }
    } catch (error) {
      showToast(error.message || "Khôi phục thất bại.", "error");
      applyRestoreBtn.disabled = false;
    }
    return;
  }
  if (event.target.closest("#theme-toggle") || event.target.closest("[data-cycle-theme]")) { cycleTheme(); return; }
  const refreshButton = event.target.closest("[data-refresh-storage]");
  if (refreshButton) {
    refreshButton.disabled = true;
    state.modelActionStatus = "Đang quét storage theo ngân sách bounded…";
    render();
    try {
      await loadRouteData({ scan: true });
      state.modelActionStatus = "Đã cập nhật snapshot storage; tổng có thể là partial nếu vượt ngân sách quét.";
      showToast(state.modelActionStatus, "success");
      render();
    } catch (error) {
      state.modelActionStatus = error.message || "Không thể quét lại storage.";
      showToast(state.modelActionStatus, "error");
      render();
    }
    return;
  }
  const checkAllUpdatesButton = event.target.closest("[data-check-all-updates]");
  if (checkAllUpdatesButton) {
    checkAllUpdatesButton.disabled = true;
    try {
      const result = await checkAllUpdates(true);
      state.updateCenter = { ...state.updateCenter, records: result.records || [], last_checked: Date.now() };
      render();
      showToast("Đã kiểm tra metadata update; không có component nào tự cài.", "success");
    } catch (error) { showToast(error.message || "Không thể kiểm tra update.", "error"); }
    finally { checkAllUpdatesButton.disabled = false; }
    return;
  }
  const checkUpdateButton = event.target.closest("[data-check-update]");
  if (checkUpdateButton) {
    checkUpdateButton.disabled = true;
    try {
      const result = await checkComponentUpdate(checkUpdateButton.dataset.checkUpdate || "", true);
      const records = [...(state.updateCenter.records || []).filter((item) => item.component_id !== result.component_id), result];
      state.updateCenter = { ...state.updateCenter, records, last_checked: Date.now() };
      render();
      showToast(`Đã kiểm tra update cho ${result.component_id || "component"}.`, "success");
    } catch (error) { showToast(error.message || "Không thể kiểm tra update.", "error"); }
    finally { checkUpdateButton.disabled = false; }
    return;
  }
  const planUpdateButton = event.target.closest("[data-plan-update]");
  if (planUpdateButton) {
    planUpdateButton.disabled = true;
    try {
      const plan = await planComponentUpdate(planUpdateButton.dataset.planUpdate || "");
      state.updateCenter = { ...state.updateCenter, pendingPlan: plan };
      render();
      showToast(plan.status === "planned" ? "Đã lập kế hoạch update; chưa tải/cài." : (plan.code || "Update chưa khả dụng."), plan.status === "planned" ? "success" : "warning");
    } catch (error) { showToast(error.message || "Không thể lập kế hoạch update.", "error"); }
    finally { planUpdateButton.disabled = false; }
    return;
  }
  const confirmUpdateButton = event.target.closest("[data-confirm-update]");
  if (confirmUpdateButton) {
    confirmUpdateButton.disabled = true;
    try {
      const result = await confirmComponentUpdate(confirmUpdateButton.dataset.confirmUpdate || "", true);
      showToast(result.next_action || result.reason || "Update đã được xử lý.", result.status === "completed" ? "success" : "warning");
      state.updateCenter = { ...state.updateCenter, pendingPlan: null };
      render();
    } catch (error) { showToast(error.message || "Không thể xác nhận update.", "error"); }
    finally { confirmUpdateButton.disabled = false; }
    return;
  }
  const rollbackButton = event.target.closest("[data-rollback-update]");
  if (rollbackButton) {
    rollbackButton.disabled = true;
    try {
      const result = await rollbackComponentUpdate(rollbackButton.dataset.rollbackUpdate || "");
      showToast(result.next_action || result.reason || "Rollback đã được xử lý.", result.status === "completed" ? "success" : "warning");
    } catch (error) { showToast(error.message || "Không thể rollback.", "error"); }
    finally { rollbackButton.disabled = false; }
    return;
  }
  const saveUpdateScheduleButton = event.target.closest("[data-save-update-schedule]");
  if (saveUpdateScheduleButton) {
    const policy = view.querySelector("[data-update-schedule]")?.value || "manual";
    saveUpdateScheduleButton.disabled = true;
    try {
      const result = await setUpdateSchedule(policy);
      if (result.status === "saved") { state.updateCenter = { ...state.updateCenter, settings: result }; render(); showToast("Đã lưu lịch kiểm tra update; không tự cài đặt.", "success"); }
      else showToast(result.code || "Lịch update không hợp lệ.", "warning");
    } catch (error) { showToast(error.message || "Không thể lưu lịch update.", "error"); }
    finally { saveUpdateScheduleButton.disabled = false; }
    return;
  }
  const launchButton = event.target.closest("[data-launch]");
  if (launchButton) {
    launchButton.disabled = true;
    try { const result = await launchApplication(launchButton.dataset.launch); showToast(`${result.application || "AIRI"}: đang khởi chạy.`); }
    catch (error) { showToast(`Không thể mở AIRI: ${error.message}`, "error"); }
    finally { launchButton.disabled = false; }
    return;
  }
  const cancel = event.target.closest("[data-cancel-job]");
  if (cancel) {
    cancel.disabled = true;
    try {
      const result = await cancelJob(cancel.dataset.cancelJob);
      const refreshed = await refreshFast({ quiet: true });
      if (refreshed === false) render();
      const message = safeDisplayMessage(result?.message, "Cancel request sent; Jobs snapshot refreshed.");
      setJobActionStatus(message, "success"); showToast(message, "success");
    } catch (error) {
      setJobActionStatus(error.message, "error"); showToast(error.message, "error");
    } finally { cancel.disabled = false; }
    return;
  }
  const resume = event.target.closest("[data-resume-job]");
  if (resume) {
    resume.disabled = true;
    try {
      await resumeJob(resume.dataset.resumeJob);
      const refreshed = await refreshFast({ quiet: true });
      if (refreshed === false) render();
      const message = "Legacy recovery request sent; Jobs snapshot refreshed.";
      setJobActionStatus(message, "success"); showToast(message, "success");
    } catch (error) {
      setJobActionStatus(error.message, "error"); showToast(error.message, "error");
    } finally { resume.disabled = false; }
    return;
  }
  const durableResume = event.target.closest("[data-resume-durable-job]");
  if (durableResume) {
    durableResume.disabled = true;
    try {
      const result = await resumeDurableJob(durableResume.dataset.resumeDurableJob);
      const refreshed = await refreshFast({ quiet: true });
      if (refreshed === false) render();
      const message = safeDisplayMessage(result?.next_action, "Durable recovery response received; the server snapshot remains authoritative.");
      setJobActionStatus(message, result?.status === "unavailable" ? "warning" : "success"); showToast(message, "warning");
    } catch (error) {
      setJobActionStatus(error.message, "error"); showToast(error.message, "error");
    } finally { durableResume.disabled = false; }
    return;
  }
  const open = event.target.closest("[data-open-artifact]");
  if (open) { try { showToast((await openArtifact(open.dataset.openArtifact)).message || "Đã yêu cầu mở artifact."); } catch (error) { showToast(error.message, "error"); } return; }
  const comfyAction = event.target.closest("[data-comfy-action]")?.dataset.comfyAction;
  if (comfyAction === "start") {
    const button = event.target.closest("[data-comfy-action]");
    button.disabled = true;
    try {
      state.comfyAdvanced = await startComfyAdvanced();
      state.lifecycle = { ...state.lifecycle, comfyui: state.comfyAdvanced.comfyui || {} };
      const workflows = await getComfyBridgeWorkflows();
      state.comfyWorkflows = workflows.workflows || [];
      showToast("Đã yêu cầu ComfyUI backend; Advanced vẫn partial cho đến khi Windows WebView acceptance pass.", "warning");
      render();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
    return;
  }
  if (comfyAction === "load") {
    const select = view.querySelector("[data-comfy-workflow-select]");
    const id = select?.value;
    if (!id) { showToast("Chọn bridge workflow trước.", "warning"); return; }
    try {
      const result = await getComfyBridgeWorkflow(id);
      const textarea = view.querySelector("[data-comfy-workflow-json]");
      const idInput = view.querySelector("[data-comfy-workflow-id]");
      if (textarea) textarea.value = JSON.stringify(result.workflow, null, 2);
      if (idInput) idInput.value = result.workflow.id || id;
      showToast("Đã nạp bridge JSON local.");
    } catch (error) { showToast(error.message, "error"); }
    return;
  }
  if (comfyAction === "save") {
    const textarea = view.querySelector("[data-comfy-workflow-json]");
    const idInput = view.querySelector("[data-comfy-workflow-id]");
    try {
      const workflow = JSON.parse(textarea?.value || "{}");
      const id = String(idInput?.value || workflow.id || "").trim();
      if (!id) throw new Error("Nhập ID bridge workflow trước khi lưu.");
      await saveComfyBridgeWorkflow(id, workflow);
      const workflows = await getComfyBridgeWorkflows();
      state.comfyWorkflows = workflows.workflows || [];
      showToast("Đã lưu bridge workflow vào user-data local, không đưa vào Git.");
      render();
    } catch (error) { showToast(error.message, "error"); }
    return;
  }
  if (event.target.closest("[data-close-backends]")) { try { const result = await closeOwnedBackends(); showToast(result.stopped?.length ? "Đã dừng backend Hub-owned rảnh." : "Không có backend Hub-owned cần dừng."); await refreshFast({ quiet: true }); } catch (error) { showToast(error.message, "error"); } }
});

document.addEventListener("keydown", async (event) => {
  if (event.key === "Escape") {
    if (artifactPreviewLayer && !artifactPreviewLayer.matches(":empty")) {
      event.preventDefault();
      closeArtifactPreview();
      return;
    }
    const openModals = Array.from(document.querySelectorAll(".modal, .dialog, .is-open, .modal-backdrop, .toast-container"));
    if (openModals.length) {
      event.preventDefault();
      openModals.forEach((el) => {
        if (el.classList.contains("is-open")) el.classList.remove("is-open");
      });
      return;
    }
  }
  if (routeId() !== "image" || state.workspaceTabs.image !== "studio") return;
  const target = event.target;
  if (target?.matches?.("input, textarea, select, [contenteditable=true]")) return;
  const session = activeImageMaskSession();
  if (!session || !(event.ctrlKey || event.metaKey)) return;
  if (event.key.toLowerCase() === "z" && !event.shiftKey && session.history?.can_undo) {
    event.preventDefault();
    try { await undoImageMaskSession(session.id, { base_revision: session.revision }); await refreshImageMaskStudio(); showToast("Hoàn tác bằng phím tắt."); }
    catch (error) { showToast(error.message, "error"); }
  } else if ((event.key.toLowerCase() === "y" || (event.key.toLowerCase() === "z" && event.shiftKey)) && session.history?.can_redo) {
    event.preventDefault();
    try { await redoImageMaskSession(session.id, { base_revision: session.revision }); await refreshImageMaskStudio(); showToast("Làm lại bằng phím tắt."); }
    catch (error) { showToast(error.message, "error"); }
  }
});

window.addEventListener("resize", syncSidebarState);
window.addEventListener("hashchange", async () => { render({ focus: "main" }); await loadRouteData(); });
syncSidebarState();
applyTheme(currentTheme());
setLanguage(currentLanguage());
initialize().catch(() => { void showFrontendBootstrapFailure(); });
