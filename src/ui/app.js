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
  getProjectWorkspaceV2,
  getArtifactLibraryV2,
  getMediaPipelineV2,
  preflightMediaPipelineV2,
  getImageMaskCompare,
  getImageMaskSession,
  getImageMaskStudioOverview,
  getHealth,
  getJobs,
  getJob,
  deleteJobHistory,
  clearTerminalJobHistory,
  getDurableJobs,
  retryDurableJob,
  getWorkflowLibrary,
  getWorkflowLibraryEntry,
  saveWorkflowLibrary,
  deleteWorkflowLibrary,
  setWorkflowLibraryFavorite,
  markWorkflowLibraryOpened,
  planWorkflowLibraryMigration,
  confirmWorkflowLibraryMigration,
  getLifecycle,
  getModels,
  getComponents,
  getApplications,
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
  getStorageScan,
  getProject,
  attachProjectWorkflowV2,
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
  closeApplication,
  launchApplication,
  openArtifact,
  resumeJob,
  redoImageMaskSession,
  removeImageMaskLayer,
  restoreImageMaskSnapshot,
  saveImageMaskSession,
  saveComfyBridgeWorkflow,
  scanStorage,
  cancelStorageScan,
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
  getExternalIntegrationsV2,
  getProductExperienceV2,
  searchProductExperienceV2,
  getPlatformHardeningV2,
  getPlatformExtensibilityV2,
} from "./api.js";
// The compatibility resumeDurableJob endpoint remains available for older
// clients; this UI deliberately uses retryDurableJob so V8 says "new record,
// not executed" truthfully.
import { disposeNodeStudios, mountNodeStudios } from "./features/node_studio/studio.js";
import { detachWorkspaceJob, ensureM3State, jobSourceArtifactId, mountM3InteractiveWorkspaces, rememberM3FileSelection, selectionToPayload, setM3UploadedArtifact, sourceArtifactIdFor, syncM3WorkspaceDom, workspaceJobMatchesSource } from "./features/vision/interactive.js";
import { ensureM4State, mountM4InteractiveWorkspaces, rememberM4FileSelection, setM4UploadedArtifact, syncM4WorkspaceDom } from "./features/ocr_whisper/interactive.js";
import { createStorageScanPoller, STORAGE_SCAN_ACTIVE_STATES } from "./storage_scan_polling.js";
import { mountImageMaskCanvases } from "./image_mask_studio.js";
import { createWorkflowLibraryAdapter } from "./workflow_library.js";
import { NAVIGATION, jobRecoverySnapshot, renderPage } from "./pages.js";
import { chooseComponentId } from "./features/components/render.js";
import { currentLanguage, localizeDocument, setLanguage, translateText } from "./i18n.js";
import { FEATURE_REGISTRY } from "./core/feature_registry.js";
import { confirmComponentInstall, getProductionCatalog, planComponentInstall } from "./shared/api/catalog.js";

globalThis.__localAiHubFrontendStarted = true;

const state = {
  health: {}, capabilities: {}, productization: {}, components: [], componentManager: {}, componentPlans: {}, selectedComponentId: "", componentDetailOpen: true, tools: [], applications: [], jobs: [], durableJobs: [], models: [], storage: {}, settings: {}, lifecycle: {}, comfyAdvanced: {}, comfyWorkflows: [], workspaceTabs: {}, jobFilter: "all", jobQuery: "", jobTypeFilter: "all", jobSort: "newest", jobPage: 1, apiStatus: "loading", apiError: "",
  creative: {}, creativeLoading: false, creativeTab: "projects", selectedProjectId: "", creativeProject: null, projectWorkspaceV2: {}, artifactLibraryV2: {}, mediaPipelineV2: {}, mediaPipelinePreflight: null, externalIntegrationsV2: {}, productExperienceV2: {}, platformHardeningV2: {}, platformExtensibilityV2: {}, onboardingDismissed: false, commandPaletteOpen: false, globalSearch: { query: "", results: [] }, assetFilters: {}, galleryFilters: {}, pendingQuickRecipe: null, pendingNodeRecipe: null, pendingGalleryPreset: null, pendingRecipeName: "",
  imageMaskStudio: {}, imageMaskLoading: false, selectedImageMaskSessionId: "", selectedImageMaskLayerId: "", imageMaskSession: null, imageMaskCompare: null, pendingImageMaskSourceId: "",
  workflowLibrary: { status: "partial", reason: "Workflow Library server-owned adapter chưa khả dụng.", action: "Tiếp tục local draft; kiểm tra endpoint typed trước khi đồng bộ." },
  storageScan: { status: "idle", progress: 0, exact: false },
  productionCatalog: { status: "partial", models: [], runtimes: [] }, updateCenter: { settings: { policy: "manual" }, records: [] }, modelFilters: { query: "", category: "", installed: "all" }, modelActionStatus: "", settingsActionStatus: "", settingsDirty: false,
  featureRegistry: FEATURE_REGISTRY,
  m3: {
    vision: { activeTool: "omniparser", thresholds: {}, selectedDetectionIndex: -1, sourceFile: null, sourceArtifact: null, localPreviewUrl: "", job: null, staleJob: null },
    sam2: { mode: "points", intent: "positive", selection: { points: [], box: null }, selectionHistory: [], selectionHistoryIndex: -1, selectedPointIndex: -1, frameIndex: 0, resultTab: "original", maskOpacity: 0.68, zoom: 1, panX: 0, panY: 0, sourceFile: null, sourceArtifact: null, localPreviewUrl: "", job: null, staleJob: null },
  },
  m4: {
    ocr: { resultTab: "text", pdfPage: 1, pdfPageCount: 0, region: null, sourceFile: null, sourceArtifact: null, localPreviewUrl: "", job: null, staleJob: null },
    whisper: { resultTab: "transcript", currentTime: 0, duration: 0, start: 0, end: 10, sourceFile: null, sourceArtifact: null, localPreviewUrl: "", job: null, staleJob: null },
  },
};
const view = document.querySelector("#module-view");
const nav = document.querySelector("#sidebar-nav");
const topStatus = document.querySelector("#top-status");
const globalSearchResults = document.querySelector("#global-search-results");
const commandPalette = document.querySelector("#command-palette");
const recordLoopbackFrontendEvent = async (event, route = null) => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2000);
  try {
    const health = state.health || {};
    const response = await fetch("/api/desktop/readiness", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        event,
        route,
        source_commit: health.build_source_commit || null,
        payload_id: health.build_payload_id || null,
      }),
    });
    const result = await response.json();
    return result && typeof result === "object" ? result : { status: "unavailable" };
  } catch { return { status: "unavailable" }; }
  finally { clearTimeout(timeout); }
};
const recordFrontendEvent = async (event, route = null) => {
  return recordLoopbackFrontendEvent(event, route);
};
const waitForPaint = () => new Promise((resolve) => {
  let settled = false;
  const finish = () => {
    if (settled) return;
    settled = true;
    clearTimeout(timeout);
    resolve();
  };
  const timeout = setTimeout(finish, 500);
  if (typeof requestAnimationFrame !== "function") {
    finish();
    return;
  }
  requestAnimationFrame(() => requestAnimationFrame(finish));
});
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
  get: ({ id }) => getWorkflowLibraryEntry(id),
  save: ({ entry, expected_revision }) => saveWorkflowLibrary(entry, expected_revision),
  remove: ({ id, expected_revision }) => deleteWorkflowLibrary(id, expected_revision),
  set_favorite: ({ id, favorite, expected_revision }) => setWorkflowLibraryFavorite(id, favorite, expected_revision),
  mark_opened: ({ id, expected_revision }) => markWorkflowLibraryOpened(id, expected_revision),
  plan_migration: ({ entries }) => planWorkflowLibraryMigration(entries),
  confirm_migration: ({ entries, expected_revision }) => confirmWorkflowLibraryMigration(entries, expected_revision),
});
let routeLoad = null;
let routeLoadKey = "";
let desktopCloseLayer = null;
let artifactPreviewOpener = null;
let disposeImageMaskCanvases = () => {};
let disposeM3Workspaces = () => {};
let disposeM4Workspaces = () => {};

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
    ? detail.owner === "external"
      ? `API do dịch vụ khác quản lý đang có ${count} job; desktop không có quyền hủy và sẽ không dừng listener external.`
      : `${count} job đang chờ, chuẩn bị, chạy hoặc hủy. Hub không tự dừng worker đang hoạt động.`
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

const isRoutableRoute = (value) => NAVIGATION.flatMap((group) => group.items).some(([id]) => id === value);
const routeId = () => {
  const value = window.location.hash.replace(/^#\/?/, "").split("/")[0];
  return isRoutableRoute(value) ? value : "dashboard";
};
let lastRenderedRoute = null;
let routeRenderGeneration = 0;

const routeIsCurrent = (route, generation) => routeId() === route && routeRenderGeneration === generation;
const beginRouteLoad = (key, factory) => {
  let promise;
  promise = Promise.resolve().then(factory).finally(() => {
    if (routeLoad === promise) {
      routeLoad = null;
      routeLoadKey = "";
    }
  });
  routeLoad = promise;
  routeLoadKey = key;
  return promise;
};

const focusComponentMaster = (id = state.selectedComponentId) => {
  const buttons = [...view.querySelectorAll("[data-component-select]")];
  const button = buttons.find((candidate) => candidate.dataset.componentSelect === String(id || "")) || buttons[0];
  if (button && typeof button.focus === "function") button.focus({ preventScroll: true });
};
const focusComponentDetail = () => {
  const close = view.querySelector("[data-component-close-detail]");
  if (close && typeof close.focus === "function") close.focus({ preventScroll: true });
};
const syncComponentSelection = () => {
  const records = Array.isArray(state.componentManager?.records) ? state.componentManager.records : [];
  state.selectedComponentId = chooseComponentId(records, state.selectedComponentId);
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

const renderGlobalSearch = () => {
  if (!globalSearchResults) return;
  const search = state.globalSearch && typeof state.globalSearch === "object" ? state.globalSearch : {};
  const results = Array.isArray(search.results) ? search.results.filter((item) => item && isRoutableRoute(item.route)) : [];
  if (!search.query) { globalSearchResults.hidden = true; globalSearchResults.replaceChildren(); return; }
  globalSearchResults.hidden = false;
  globalSearchResults.innerHTML = `<div class="global-search-results__panel"><strong>Kết quả tìm Hub</strong><span class="small">${escapeHtml(String(search.query).slice(0, 80))}</span>${results.length ? `<div class="global-search-results__rows">${results.map((item) => `<button class="button button--compact" type="button" data-route="${escapeHtml(item.route || "dashboard")}"><strong>${escapeHtml(item.label || item.id || "Workspace")}</strong><span class="row-meta">${escapeHtml(item.description || "")}</span></button>`).join("")}</div>` : `<p class="small">Không có workspace phù hợp trong catalog Hub.</p>`}</div>`;
};

const COMMANDS = Object.freeze([
  { id: "open-models", label: "Mở Models", detail: "Xem model catalog và storage", route: "models" },
  { id: "open-diagnostics", label: "Mở Diagnostics", detail: "Xem chẩn đoán sanitized", route: "diagnostics" },
  { id: "new-workflow", label: "New Workflow", detail: "Mở workspace dự án; chưa tạo dữ liệu", route: "projects" },
  { id: "check-update", label: "Check Update", detail: "Mở nơi kiểm tra update thủ công", route: "models" },
  { id: "scan-storage", label: "Scan Storage", detail: "Mở Storage; bạn tự bấm Quét chính xác", route: "models" },
  { id: "search-artifact", label: "Search Artifact", detail: "Tìm artifact trong metadata Hub", route: "" },
]);

const renderCommandPalette = () => {
  if (!commandPalette) return;
  if (state.commandPaletteOpen !== true) { commandPalette.hidden = true; commandPalette.replaceChildren(); return; }
  commandPalette.hidden = false;
  commandPalette.innerHTML = `<div class="command-palette__panel"><div class="card-title-row"><div><span class="eyebrow">COMMAND PALETTE</span><h2 id="command-palette-title">Bảng lệnh Hub</h2><p class="small">Ctrl+K · Escape để đóng. Lệnh chỉ điều hướng hoặc tìm metadata; không tự chạy thao tác nguy hiểm.</p></div><button class="button button--compact" type="button" data-command-close>Đóng</button></div><div class="command-palette__rows">${COMMANDS.map((command) => `<button class="button button--compact" type="button" data-command-action="${escapeHtml(command.id)}"${command.route ? ` data-command-route="${escapeHtml(command.route)}"` : ""}><strong>${escapeHtml(command.label)}</strong><span class="row-meta">${escapeHtml(command.detail)}</span></button>`).join("")}</div></div>`;
};

const renderApiState = () => {
  if (state.apiStatus === "error") return `<section class="global-state global-state--error" role="alert"><strong>API Hub chưa sẵn sàng</strong><span>${state.apiError || "Kiểm tra listener loopback rồi thử lại."}</span><button class="button button--compact" type="button" data-refresh-api>Thử lại</button></section>`;
  if (state.apiStatus === "loading") return `<section class="global-state global-state--loading" role="status"><strong>Đang tải workspace</strong><span>Đang lấy health, capability và queue snapshot…</span></section>`;
  return "";
};

const TOOL_EXECUTION_READY = new Set(["operational"]);
const applyToolActionGates = () => {
  const tools = new Map((Array.isArray(state.tools) ? state.tools : []).map((item) => [String(item?.name || ""), item]));
  const gate = (button, form) => {
    if (!form) {
      button.disabled = true;
      button.dataset.readinessGate = "true";
      button.setAttribute("aria-disabled", "true");
      button.title = "Chưa tìm thấy form thao tác an toàn.";
      return;
    }
    let toolId = String(form.dataset.tool || "");
    if (form.dataset.toolByField && form.dataset.toolMap) {
      const control = form.elements?.[form.dataset.toolByField];
      try { toolId = JSON.parse(form.dataset.toolMap)[control?.value] || toolId; } catch { /* keep the declared fallback */ }
    }
    const item = tools.get(toolId) || {};
    const status = String(item.tool_status || item.status || "unavailable").toLowerCase();
    const ready = TOOL_EXECUTION_READY.has(status);
    const reason = String(item.reason || "Backend chưa có bằng chứng chạy an toàn trong snapshot hiện tại.").slice(0, 240);
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
  };
  view.querySelectorAll("form[data-job-form]").forEach((form) => {
    let toolId = String(form.dataset.tool || "");
    if (form.dataset.toolByField && form.dataset.toolMap) {
      const control = form.elements?.[form.dataset.toolByField];
      try { toolId = JSON.parse(form.dataset.toolMap)[control?.value] || toolId; } catch { /* keep the declared fallback */ }
    }
    const item = tools.get(toolId) || {};
    const status = String(item.tool_status || item.status || "unavailable").toLowerCase();
    const ready = TOOL_EXECUTION_READY.has(status);
    const reason = String(item.reason || "Backend chưa có bằng chứng chạy an toàn trong snapshot hiện tại.").slice(0, 240);
    form.dataset.readinessStatus = status;
    form.querySelectorAll("button[type=submit]").forEach((button) => {
      if (button.hasAttribute("disabled") && button.dataset.readinessGate !== "true") return;
      gate(button, form);
    });
  });
  view.querySelectorAll("[data-m3-primary-action], [data-m4-primary-action]").forEach((button) => {
    gate(button, document.getElementById(button.getAttribute("form") || ""));
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
  jobSummary.textContent = `${translateText("Jobs")}: ${recovery.counts.active} ${translateText("active")} · ${recovery.counts.total} ${translateText("records")}`;
};

const render = ({ background = false, focus = "" } = {}) => {
  const activeRoute = routeId();
  const sameRoute = lastRenderedRoute === activeRoute;
  const continuity = sameRoute ? captureFocusContinuity() : { activeInside: false, token: "", ordinal: 0, scroll: null };
  if (background && continuity.token) {
    setSnapshotStatus("deferred");
    updateTopbar();
    return false;
  }
  ensureM3State(state);
  ensureM4State(state);
  stopAllM3JobPollers();
  restoreWorkspaceJobReferences();
  disposeNodeStudios();
  disposeImageMaskCanvases();
  disposeM3Workspaces();
  disposeM4Workspaces();
  renderNavigation();
  renderGlobalSearch();
  renderCommandPalette();
  syncSidebarState();
  view.innerHTML = `${renderApiState()}${renderPage(activeRoute, state)}`;
  applyToolActionGates();
  disposeM3Workspaces = mountM3InteractiveWorkspaces(view, state, {
    onStateChange: () => applyToolActionGates(),
  });
  disposeM4Workspaces = mountM4InteractiveWorkspaces(view, state, {
    onStateChange: () => applyToolActionGates(),
  });
  resumeVisibleWorkspaceJobPollers();
  if (sameRoute) restoreScrollContinuity(continuity.scroll);
  else {
    // A navigation boundary must never inherit the previous route's deep
    // scroll position. Same-route background refreshes still use the
    // continuity snapshot above.
    if (mainContent) mainContent.scrollTop = 0;
    if (view) view.scrollTop = 0;
    if (typeof window.scrollTo === "function") window.scrollTo(0, 0);
  }
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
  lastRenderedRoute = activeRoute;
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
  state.settingsDirty = false;
  // Settings are persisted by the server-owned SettingsPersistence service;
  // hydrate the current shell from that snapshot before the first render so a
  // restart does not silently fall back to stale localStorage preferences.
  if (["vi", "en", "zh", "ja", "ko"].includes(state.settings.language)) setLanguage(state.settings.language);
  if (["system", "dark", "light"].includes(state.settings.theme)) applyTheme(state.settings.theme);
  state.lifecycle = payload.lifecycle || {};
  if (payload.workflow_library && typeof payload.workflow_library === "object") state.workflowLibrary = payload.workflow_library;
};

const confirmFrontendReady = async () => {
  // Persist the identity-bound proof before asking the native bridge to clear
  // pending health.  This makes a WebView/native bridge delay observable to
  // the desktop wait loop without treating HTTP status as readiness.
  const signal = await recordLoopbackFrontendEvent("frontend_ready", routeId());
  const signalRecorded = signal?.status === "recorded" || signal?.status === "ready";
  if (globalThis.pywebview && !signalRecorded) {
    throw new Error(signal?.code || "FRONTEND_READY_SIGNAL_REJECTED");
  }
  // The desktop startup thread consumes this event and performs the native
  // health/pending-marker commit.  Never call a potentially blocking native
  // WebView bridge method from the renderer's critical path.
  return true;
};

const refreshFast = async ({ quiet = false, renderView = true } = {}) => {
  const requestedRoute = routeId();
  const requestedGeneration = routeRenderGeneration;
  const isCurrent = () => routeIsCurrent(requestedRoute, requestedGeneration);
  const [health, jobs, capabilities, durableJobs] = await Promise.allSettled([getHealth(), getJobs(), getCapabilities(), getDurableJobs()]);
  if (!isCurrent()) return false;
  let failed = false;
  if (health.status === "fulfilled") state.health = health.value || {};
  else failed = true;
  if (jobs.status === "fulfilled") state.jobs = jobs.value.jobs || [];
  else failed = true;
  if (capabilities.status === "fulfilled") state.capabilities = capabilities.value || {};
  else failed = true;
  if (durableJobs.status === "fulfilled") state.durableJobs = durableJobs.value?.records || [];
  else failed = true;
  const rendered = ["dashboard", "settings", "jobs"].includes(requestedRoute) ? render({ background: true }) : false;
  if (failed && state.apiStatus === "ready") state.apiStatus = "degraded";
  if (failed && !quiet) showToast("API đang khởi động hoặc một snapshot nhanh chưa sẵn sàng.", "warning");
  if (failed && rendered !== false) setSnapshotStatus("unavailable");
  return rendered;
};

const refreshCreative = async ({ renderView = true, isCurrent } = {}) => {
  const requestedRoute = routeId();
  const requestedGeneration = routeRenderGeneration;
  const isActive = typeof isCurrent === "function" ? isCurrent : () => routeIsCurrent(requestedRoute, requestedGeneration);
  state.creativeLoading = true;
  try {
    const [creativeResult, workspaceResult, artifactsResult] = await Promise.allSettled([
      getCreativeOverview(),
      getProjectWorkspaceV2(),
      getArtifactLibraryV2(120),
    ]);
    if (creativeResult.status !== "fulfilled") throw creativeResult.reason;
    if (!isActive()) return state.creative;
    state.creative = creativeResult.value || {};
    if (workspaceResult.status === "fulfilled") state.projectWorkspaceV2 = workspaceResult.value || {};
    if (artifactsResult.status === "fulfilled") state.artifactLibraryV2 = artifactsResult.value || {};
    const projects = state.creative.projects || [];
    const selected = state.selectedProjectId && projects.some((item) => item.id === state.selectedProjectId)
      ? state.selectedProjectId
      : (state.creative.recent_projects || [])[0]?.id || projects.find((item) => item.status === "active")?.id || projects[0]?.id || "";
    state.selectedProjectId = selected;
    if (selected) {
      try { state.creativeProject = await getProject(selected); }
      catch { state.creativeProject = null; state.selectedProjectId = ""; }
    } else state.creativeProject = null;
    if (!isActive()) return state.creative;
    return state.creative;
  } finally {
    state.creativeLoading = false;
    if (renderView && isActive() && routeId() === "projects") render();
  }
};

const refreshImageMaskStudio = async ({ renderView = true, before = "", after = "", isCurrent } = {}) => {
  const requestedRoute = routeId();
  const requestedGeneration = routeRenderGeneration;
  const isActive = typeof isCurrent === "function" ? isCurrent : () => routeIsCurrent(requestedRoute, requestedGeneration);
  state.imageMaskLoading = true;
  try {
    const [overviewResult, creativeResult] = await Promise.allSettled([getImageMaskStudioOverview(), getCreativeOverview()]);
    if (overviewResult.status !== "fulfilled") throw overviewResult.reason;
    if (!isActive()) return state.imageMaskStudio;
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
    if (!isActive()) return state.imageMaskStudio;
    state.imageMaskSession = detail || null;
    const layers = detail?.session?.layers || [];
    if (!layers.some((layer) => layer.id === state.selectedImageMaskLayerId)) {
      state.selectedImageMaskLayerId = detail?.session?.active_layer_id || layers.at(-1)?.id || "";
    }
    try { state.imageMaskCompare = await getImageMaskCompare(selected, before, after); }
    catch { state.imageMaskCompare = null; }
    if (!isActive()) return state.imageMaskStudio;
    return state.imageMaskStudio;
  } finally {
    state.imageMaskLoading = false;
    if (renderView && isActive() && routeId() === "image" && state.workspaceTabs.image === "studio") render();
  }
};

const UNSAFE_STORAGE_DISPLAY = /(?:[a-z]:[\\/]|\\\\|(?:file|data|https?):|(?:api[_-]?key|password|secret|token)\s*[:=])/i;
const safeStorageDisplay = (value, fallback = "") => {
  const candidate = typeof value === "string" ? value.trim().slice(0, 240) : "";
  return candidate && !UNSAFE_STORAGE_DISPLAY.test(candidate) ? candidate : fallback;
};
const updateStorageScanDom = (result) => {
  // Polling a storage scan must not tear down the module DOM (and must not
  // remount canvases, reset focus, or reload the whole WebView).  Update only
  // the bounded status/area projection that the Models page owns.
  if (routeId() !== "models") return;
  const scan = result?.scan && typeof result.scan === "object" ? result.scan : {};
  const status = String(scan.status || result?.status || "idle");
  const mode = String(scan.mode || result?.scan_mode || "fast");
  const progress = Math.max(0, Math.min(100, Number.isFinite(Number(scan.progress)) ? Number(scan.progress) : 0));
  const savedAt = safeStorageDisplay(scan.saved_at, "");
  const currentRoot = safeStorageDisplay(scan.current_root || scan.current_area, "");
  const areasForTotals = result?.areas && typeof result.areas === "object" ? result.areas : {};
  const aggregateCount = (key) => {
    if (Number.isSafeInteger(scan[key]) && scan[key] >= 0) return scan[key];
    return Object.values(areasForTotals).reduce((total, value) => total + (Number.isSafeInteger(value?.[key]) && value[key] >= 0 ? value[key] : 0), 0);
  };
  const diskFree = Number.isSafeInteger(result?.disk?.free_bytes) && result.disk.free_bytes >= 0 ? result.disk.free_bytes : null;
  const ownedTotal = Number.isSafeInteger(scan.owned_storage_total_bytes) && scan.owned_storage_total_bytes >= 0
    ? scan.owned_storage_total_bytes
    : Number.isSafeInteger(scan.total_bytes_counted) && scan.total_bytes_counted >= 0 ? scan.total_bytes_counted : 0;
  const entriesScanned = aggregateCount("entries_scanned");
  const filesScanned = aggregateCount("files_scanned");
  const directoriesScanned = aggregateCount("directories_scanned");
  const reparseEntries = aggregateCount("reparse_entries");
  const unreadableEntries = aggregateCount("unreadable_entries");
  const completedRoots = Number.isSafeInteger(scan.completed_roots) && scan.completed_roots >= 0
    ? scan.completed_roots
    : Object.values(areasForTotals).filter((value) => value?.complete === true).length;
  const totalRoots = Number.isSafeInteger(scan.total_roots) && scan.total_roots > 0 ? scan.total_roots : 7;
  const banner = view.querySelector("[data-storage-scan-status]");
  if (!banner) { render(); return; }
  banner.dataset.storageScanStatus = status;
  banner.dataset.storageScanMode = mode;
  banner.dataset.storageScanProgress = String(progress);
  const title = banner.querySelector(".card-title-row strong");
  if (title) {
    const area = currentRoot ? ` · ${currentRoot}` : "";
    title.textContent = scan.polling_limited === true
      ? "Quét vẫn đang chạy nền"
      : status === "running"
      ? `${mode === "deep_exact" ? "Quét chính xác · đang tính" : "Đang quét nhanh"} · ${progress}%${area}`
      : status === "cancelling" ? "Đang hủy quét …"
        : status === "completed" && scan.exact === true ? (savedAt ? `Chính xác tại ${savedAt}` : "Đã quét xong · tổng chính xác")
          : status === "partial" ? "Đã quét một phần · tổng chưa đủ"
            : status === "cancelled" ? "Đã hủy quét · tổng chưa đủ"
              : status === "unavailable" ? "Quét storage chưa khả dụng" : "Chưa có lần quét storage";
  }
  const badge = banner.querySelector(".card-title-row span");
  if (badge) badge.textContent = scan.exact === true ? (savedAt ? `Chính xác tại ${savedAt}` : "chính xác") : status === "running" ? "đang đếm" : "có giới hạn";
  const bar = banner.querySelector(".progress-bar");
  const track = banner.querySelector("[role=progressbar]");
  if (bar) bar.style.width = `${progress}%`;
  if (track) track.setAttribute("aria-valuenow", String(progress));
  const liveBytes = banner.querySelector("[data-storage-total-bytes]");
  const countedBytes = ownedTotal;
  if (liveBytes) liveBytes.textContent = formatGb(countedBytes);
  const liveBytesRaw = banner.querySelector("[data-storage-total-bytes-raw]");
  if (liveBytesRaw) liveBytesRaw.textContent = `${Number.isFinite(countedBytes) ? countedBytes.toLocaleString() : "0"} bytes`;
  const liveFiles = banner.querySelector("[data-storage-files-scanned]");
  if (liveFiles) { liveFiles.setAttribute("data-storage-files-scanned", String(filesScanned)); liveFiles.textContent = `${filesScanned} tệp đã đếm`; }
  const liveArea = banner.querySelector("[data-storage-current-area]");
  if (liveArea) liveArea.textContent = currentRoot;
  const ownedNode = banner.querySelector("[data-storage-owned-total] strong");
  if (ownedNode) {
    ownedNode.parentElement?.setAttribute("data-storage-owned-total", String(ownedTotal));
    ownedNode.textContent = formatGb(ownedTotal);
  }
  const diskNode = banner.querySelector("[data-storage-disk-free] strong");
  if (diskNode) {
    diskNode.parentElement?.setAttribute("data-storage-disk-free", diskFree === null ? "" : String(diskFree));
    diskNode.textContent = diskFree === null ? "—" : formatGb(diskFree);
  }
  const countNodes = [
    ["data-storage-entries-scanned", `${entriesScanned} mục`],
    ["data-storage-directories-scanned", `${directoriesScanned} thư mục`],
    ["data-storage-reparse-entries", `${reparseEntries} reparse bỏ qua`],
    ["data-storage-unreadable-entries", `${unreadableEntries} không đọc được`],
    ["data-storage-completed-roots", `${completedRoots}/${totalRoots} root hoàn tất`],
  ];
  countNodes.forEach(([attribute, text]) => {
    const node = banner.querySelector(`[${attribute}]`);
    if (node) node.textContent = text;
  });
  const reason = banner.querySelector("[data-storage-scan-reason]");
  if (reason) reason.textContent = safeStorageDisplay(scan.reason || result?.reason, "Số liệu scan server-owned đang được cập nhật.");
  const nextAction = banner.querySelector("[data-storage-scan-next-action]");
  if (nextAction) nextAction.textContent = safeStorageDisplay(scan.next_action || result?.next_action, "Bấm Quét chính xác để xác nhận lại tổng managed.");
  const pollingNotice = banner.querySelector("[data-storage-polling-notice]");
  if (pollingNotice) {
    pollingNotice.textContent = scan.polling_limited === true
      ? safeStorageDisplay(scan.polling_message, "Quét vẫn đang chạy nền; bấm Theo dõi tiếp để cập nhật.")
      : "";
    pollingNotice.hidden = scan.polling_limited !== true;
  }
  const savedAtNode = banner.querySelector("[data-storage-scan-saved-at]");
  if (savedAtNode) {
    savedAtNode.textContent = savedAt ? `Chính xác tại ${savedAt}; bấm Quét chính xác sau khi filesystem thay đổi.` : "";
    savedAtNode.hidden = !savedAt;
  }

  const areas = result?.areas && typeof result.areas === "object" ? result.areas : {};
  const rootCounts = result?.managed_root_counts && typeof result.managed_root_counts === "object" ? result.managed_root_counts : {};
  const areaProjection = { ...rootCounts, ...areas };
  const list = view.querySelector("[data-storage-area-list]");
  if (list) {
    Object.entries(areaProjection).forEach(([name, value]) => {
      let row = list.querySelector(`[data-storage-area="${CSS.escape(name)}"]`);
      if (!row) {
        row = document.createElement("div");
        row.className = "row-item";
        row.dataset.storageArea = name;
        row.innerHTML = "<span></span><strong data-storage-area-value></strong><small data-storage-area-count></small>";
        list.append(row);
      }
      const label = row.querySelector("span");
      const valueNode = row.querySelector("[data-storage-area-value]");
      const countNode = row.querySelector("[data-storage-area-count]");
      const bytes = Number(value?.bytes || 0);
      const partial = value?.complete === false || value?.status === "partial" || value?.status === "running";
      if (label) label.textContent = safeStorageDisplay(name, "Managed area");
      if (valueNode) valueNode.textContent = partial ? `Ít nhất ${formatGb(bytes)}` : formatGb(bytes);
      if (countNode) countNode.textContent = `${Number(value?.entries_scanned || 0)} mục · ${Number(value?.files_scanned || 0)} tệp · ${Number(value?.reparse_entries || 0)} reparse · ${Number(value?.unreadable_entries || 0)} không đọc được · ${value?.deduplicated === true ? "đã gộp trùng" : value?.complete === true ? "đã hoàn tất" : "chưa hoàn tất"}`;
    });
  }
  const action = view.querySelector("[data-storage-scan-action]");
  if (action) {
    const canCancel = (status === "running" || status === "cancelling") && mode === "deep_exact";
    const buttons = [];
    if (scan.polling_limited === true && STORAGE_SCAN_ACTIVE_STATES.includes(status)) {
      buttons.push(`<button class="button" type="button" data-resume-storage-polling="${escapeHtml(scan.scan_id || "")}">Theo dõi tiếp</button>`);
    }
    if (canCancel) {
      buttons.push(`<button class="button button--danger" type="button" data-cancel-storage-scan="${escapeHtml(scan.scan_id || "")}"${status === "cancelling" ? " disabled" : ""}>Hủy quét</button>`);
    }
    if (!buttons.length) buttons.push(`<button class="button" type="button" data-refresh-storage${status === "cancelling" ? " disabled" : ""}>Quét chính xác</button>`);
    action.innerHTML = buttons.join("");
  }
};

const storageScanPoller = createStorageScanPoller({
  getSnapshot: getStorageScan,
  isRouteActive: () => routeId() === "models",
  onSnapshot: (result) => {
    state.storage = result ? { ...(state.storage || {}), ...result, disk: result.disk || state.storage?.disk || {} } : state.storage;
    state.storageScan = result?.scan || state.storageScan;
    updateStorageScanDom(state.storage || {});
  },
  onCeiling: (result) => {
    const currentScan = state.storageScan && typeof state.storageScan === "object" ? state.storageScan : {};
    if (!STORAGE_SCAN_ACTIVE_STATES.includes(String(currentScan.status || result?.scan?.status || "running"))) return;
    const limitedScan = {
      ...currentScan,
      ...(result?.scan || {}),
      polling_limited: true,
      polling_message: "Quét vẫn đang chạy nền; bấm Theo dõi tiếp để cập nhật.",
    };
    state.storageScan = limitedScan;
    state.storage = { ...(state.storage || {}), scan: limitedScan };
    updateStorageScanDom(state.storage);
  },
  onError: () => {
    // A transient loopback error is retried by the coordinator while the
    // server-owned worker remains in running/cancelling state.
  },
});
const pollStorageScan = (scanId = "") => storageScanPoller.start(scanId);

const loadRouteData = async ({ scan = false } = {}) => {
  const route = routeId();
  const generation = routeRenderGeneration;
  const loadKey = `${generation}:${route}`;
  const isCurrent = () => routeIsCurrent(route, generation);
  if (routeLoad && routeLoadKey !== loadKey) {
    // A navigation boundary invalidates the old promise.  Its finally block
    // cannot clear a newer route load because beginRouteLoad compares identity.
    routeLoad = null;
    routeLoadKey = "";
  }
  const existingLoad = routeLoad && routeLoadKey === loadKey ? routeLoad : null;
  if (route === "dashboard") {
    if (existingLoad) return existingLoad;
    return beginRouteLoad(loadKey, () => getProductExperienceV2().then((value) => {
      if (!isCurrent()) return;
      state.productExperienceV2 = value || {};
      render();
    }).catch(() => {
      if (!isCurrent()) return;
      state.productExperienceV2 = { status: "unavailable", execution: "not_run", dry_run: true };
      render();
    }));
  }
  if (route === "models") {
    if (existingLoad) return existingLoad;
    return beginRouteLoad(loadKey, () => Promise.allSettled([getModels(), scan ? scanStorage() : getStorage(), getProductionCatalog(), getUpdateSettings()]).then(async (results) => {
      if (!isCurrent()) return;
      if (results[0].status === "fulfilled") state.models = results[0].value.models || [];
      if (results[1].status === "fulfilled") {
        const storageResult = results[1].value || {};
        state.storage = {
          ...(state.storage || {}),
          ...storageResult,
          disk: storageResult.disk || state.storage?.disk || {},
        };
        state.storageScan = storageResult.scan || state.storageScan;
      }
      if (results[2].status === "fulfilled") state.productionCatalog = results[2].value || state.productionCatalog;
      if (results[3].status === "fulfilled") state.updateCenter = { ...state.updateCenter, settings: results[3].value || state.updateCenter.settings };
      if (!isCurrent()) return;
      render();
      if (STORAGE_SCAN_ACTIVE_STATES.includes(String(state.storageScan?.status || ""))) {
        pollStorageScan(state.storageScan.scan_id || "");
      } else {
        // FAST is only a first-paint placeholder. Queue DEEP_EXACT
        // automatically after the page is visible, attaching to the
        // server-owned worker if another view already started it.
        try {
          const deep = await scanStorage();
          if (!isCurrent()) return;
          state.storage = { ...(state.storage || {}), ...(deep || {}), disk: deep?.disk || state.storage?.disk || {} };
          state.storageScan = deep?.scan || state.storageScan;
          render({ background: true });
          if (STORAGE_SCAN_ACTIVE_STATES.includes(String(state.storageScan?.status || ""))) pollStorageScan(state.storageScan.scan_id || "");
        } catch {
          // The visible FAST snapshot remains truthful; the scan route will
          // retry on the next user navigation/refresh.
        }
      }
    }).catch(() => {}));
  }
  if (route === "components") {
    if (existingLoad) return existingLoad;
    return beginRouteLoad(loadKey, () => Promise.allSettled([getComponents(), getProductionCatalog()]).then((results) => {
      if (!isCurrent()) return;
      const payload = results[0].status === "fulfilled" ? results[0].value : {};
      state.componentManager = payload || {};
      syncComponentSelection();
      if (results[1].status === "fulfilled") state.productionCatalog = results[1].value || state.productionCatalog;
      render();
    }).catch((error) => {
      if (isCurrent()) showToast(error.message || "Không thể tải Component Manager.", "error");
    }));
  }
  if (route === "airi") {
    if (existingLoad) return existingLoad;
    // The legacy bootstrap is kept for fast paint and the existing
    // server-owned launch action.  M3 integration state is loaded explicitly
    // here; the browser never probes AIRI, reads a path/key, or embeds it.
    return beginRouteLoad(loadKey, () => Promise.allSettled([getExternalIntegrationsV2(), getApplications()]).then((results) => {
      if (!isCurrent()) return;
      if (results[0].status === "fulfilled") state.externalIntegrationsV2 = results[0].value || {};
      if (results[1].status === "fulfilled") state.applications = results[1].value?.applications || state.applications;
      render();
    }).catch((error) => {
      if (isCurrent()) showToast(error.message || "Không thể tải trạng thái tích hợp AIRI.", "error");
    }));
  }
  if (route === "image") {
    if (existingLoad) return existingLoad;
    return beginRouteLoad(loadKey, async () => {
      const results = await Promise.allSettled([getLifecycle(), getComfyAdvanced(), getComfyBridgeWorkflows()]);
      if (!isCurrent()) return;
      if (results[0].status === "fulfilled") state.lifecycle = results[0].value;
      if (results[1].status === "fulfilled") state.comfyAdvanced = results[1].value;
      if (results[2].status === "fulfilled") state.comfyWorkflows = results[2].value.workflows || [];
      try { await refreshImageMaskStudio({ renderView: false, isCurrent }); }
      catch (error) {
        if (isCurrent()) {
          state.imageMaskStudio = { ...state.imageMaskStudio, recovery: { status: "recovery_required", reason: error.message || "Không thể tải Image & Mask Studio.", action: "Kiểm tra API Hub rồi thử lại." } };
          if (state.workspaceTabs.image === "studio") showToast(error.message || "Không thể tải Image & Mask Studio.", "error");
        }
      }
      if (isCurrent()) render();
    });
  }
  if (route === "media" || route === "video" || route === "animesr") {
    if (existingLoad) return existingLoad;
    return beginRouteLoad(loadKey, () => Promise.allSettled([getMediaPipelineV2(), getArtifactLibraryV2(120)]).then((results) => {
      if (!isCurrent()) return;
      if (results[0].status === "fulfilled") state.mediaPipelineV2 = results[0].value || {};
      else state.mediaPipelineV2 = { status: "unavailable", execution: "not_run", dry_run: true };
      if (results[1].status === "fulfilled") state.artifactLibraryV2 = results[1].value || state.artifactLibraryV2;
      render();
    }).catch(() => {
      if (!isCurrent()) return;
      state.mediaPipelineV2 = { status: "unavailable", execution: "not_run", dry_run: true };
      render();
    }));
  }
  if (route === "projects") {
    if (existingLoad) return existingLoad;
    // The bootstrap contains the legacy Creative overview for a fast first
    // paint, but M2 project/artifact projections are route data.  Fetch them
    // when Projects is selected rather than rendering a false unavailable
    // fallback until the user happens to press the manual refresh button.
    return beginRouteLoad(loadKey, () => refreshCreative({ renderView: false, isCurrent }).then(() => {
      if (isCurrent()) render();
    }).catch((error) => {
      if (isCurrent()) showToast(error.message || "Không thể tải Creative Workspace.", "error");
    }));
  }
  if (route === "diagnostics") {
    if (existingLoad) return existingLoad;
    return beginRouteLoad(loadKey, () => Promise.allSettled([getDiagnosticsSnapshot(), getPlatformHardeningV2(), getPlatformExtensibilityV2()]).then(([diagnosticsResult, hardeningResult, extensibilityResult]) => {
      if (!isCurrent()) return;
      const res = diagnosticsResult.status === "fulfilled" ? diagnosticsResult.value : { status: "unavailable", snapshot: {} };
      state.diagnostics = res;
      state.platformHardeningV2 = hardeningResult.status === "fulfilled" ? hardeningResult.value : { status: "unavailable", areas: [], execution: "not_run", dry_run: true };
      state.platformExtensibilityV2 = extensibilityResult.status === "fulfilled" ? extensibilityResult.value : { status: "unavailable", execution: "not_run", dry_run: true };
      render();
    }).catch((err) => {
      if (isCurrent()) showToast(err.message || "Không thể tải Diagnostics snapshot.", "error");
    }));
  }
  if (route === "settings") {
    if (existingLoad) return existingLoad;
    return beginRouteLoad(loadKey, () => Promise.allSettled([getSettings(), listBackups()]).then(([setRes, backRes]) => {
      if (!isCurrent()) return;
      if (setRes.status === "fulfilled" && setRes.value?.settings) {
        state.settings = setRes.value.settings;
        state.settings_revision = setRes.value.settings_revision;
        state.settingsRecovery = setRes.value.recovery;
        state.settingsDirty = false;
      }
      if (backRes.status === "fulfilled" && backRes.value?.backups) {
        state.backups = backRes.value.backups;
      }
      render();
    }).catch(() => {}));
  }
  return undefined;
};

const initialize = async () => {
  await recordFrontendEvent("frontend_bootstrap_started");
  let bootstrapReady = false;
  try {
    applyBootstrap(await getBootstrap());
    bootstrapReady = true;
    await recordFrontendEvent("frontend_bootstrap_completed");
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
  await waitForPaint();
  const navVisible = Boolean(nav?.querySelectorAll(".nav-item").length);
  const viewVisible = Boolean(view?.textContent?.trim());
  if (navVisible) await recordFrontendEvent("frontend_nav_visible", routeId());
  if (viewVisible) await recordFrontendEvent("frontend_view_visible", routeId());
  const visualProof = navVisible && viewVisible;
  if (!visualProof) throw new Error("FRONTEND_DOM_NOT_RENDERED");
  await recordFrontendEvent("frontend_dom_visible", routeId());
  await recordFrontendEvent("frontend_rendered", routeId());
  globalThis.__localAiHubFrontendRendered = true;
  globalThis.__localAiHubFrontendBootstrapReady = bootstrapReady;
  if (bootstrapReady) {
    try {
      await confirmFrontendReady();
    } catch {
      await recordFrontendEvent("frontend_ready_rejected");
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
  const workspace = form.closest("[data-m3-workspace], [data-m4-workspace]");
  if (workspace) {
    const isM4 = Boolean(workspace.dataset.m4Workspace);
    const workspaceKey = isM4 ? workspace.dataset.m4Workspace : workspace.dataset.m3Workspace;
    const workspaceState = isM4 ? (ensureM4State(state)[workspaceKey] || {}) : (ensureM3State(state)[workspaceKey] || {});
    const sourceInput = workspace.querySelector("input[type=file][data-asset-key]");
    let sourceArtifactId = sourceInput?.dataset?.uploadedArtifactId || workspaceState.sourceArtifact?.id || "";
    if (!sourceArtifactId) {
      const selected = sourceInput?.files?.[0] || workspaceState.sourceFile;
      if (!selected) throw new Error("Chọn tệp nguồn trước khi tạo job.");
      const artifact = await uploadFile(selected);
      if (isM4) setM4UploadedArtifact(workspace, state, artifact);
      else setM3UploadedArtifact(workspace, state, artifact);
      sourceArtifactId = artifact?.id || "";
    }
    if (!sourceArtifactId) throw new Error("Upload không trả artifact ID opaque.");
    payload.source_artifact_id = sourceArtifactId;
    if (!isM4 && workspaceKey === "sam2") Object.assign(payload, selectionToPayload(workspaceState));
    if (isM4 && workspaceKey === "ocr") {
      if (workspace.dataset.ocrRegionSupported === "true" && Array.isArray(workspaceState.region)) payload.normalized_box = workspaceState.region;
      if (workspace.dataset.ocrPageSupported === "true" && Number.isInteger(workspaceState.pdfPage) && workspaceState.pdfPage >= 1) payload.page_number = workspaceState.pdfPage;
    }
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

const M3_TERMINAL_JOB_STATUSES = new Set(["completed", "failed", "error", "cancelled", "unavailable", "interrupted"]);
const M3_JOB_POLL_INTERVAL_MS = 1000;
const M3_JOB_POLL_MAX_MS = 30 * 60 * 1000;
const m3JobPollers = new Map();
const WORKSPACE_JOB_TOOLS = Object.freeze({
  vision: new Set(["parse_screen", "detect_objects", "ground_objects"]),
  sam2: new Set(["segment_from_points", "segment_from_box", "segment_from_text", "track_video_object"]),
  ocr: new Set(["ocr_document"]),
  whisper: new Set(["transcribe_media"]),
});

const workspaceForKey = (workspaceKey) => view.querySelector(`[data-m3-workspace="${workspaceKey}"], [data-m4-workspace="${workspaceKey}"]`);
const workspaceModelForKey = (workspaceKey) => {
  if (workspaceKey === "ocr" || workspaceKey === "whisper") return ensureM4State(state)[workspaceKey];
  return ensureM3State(state)[workspaceKey];
};

const workspaceKeys = () => ["vision", "sam2", "ocr", "whisper"];
const restoreWorkspaceJobReferences = () => {
  for (const workspaceKey of workspaceKeys()) {
    const model = workspaceModelForKey(workspaceKey);
    if (model?.job?.id) continue;
    const tools = WORKSPACE_JOB_TOOLS[workspaceKey] || new Set();
    const candidates = (Array.isArray(state.jobs) ? state.jobs : [])
      .filter((job) => job && tools.has(job.tool))
      .sort((left, right) => String(right.created_at || right.updated_at || right.id || "").localeCompare(String(left.created_at || left.updated_at || left.id || "")));
    const source = sourceArtifactIdFor(model);
    if (!source || !model || !candidates.length) continue;
    const candidate = candidates.find((job) => jobSourceArtifactId(job) === source);
    if (candidate) model.job = candidate;
    else if (!model.staleJob) model.staleJob = candidates[0];
  }
};

const resumeVisibleWorkspaceJobPollers = () => {
  const route = routeId();
  if (!workspaceKeys().includes(route)) return;
  const model = workspaceModelForKey(route);
  const job = model?.job;
  if (job && !workspaceJobMatchesSource(model, job)) {
    detachWorkspaceJob(model);
    render({ focus: "main" });
    return;
  }
  if (job?.id && !M3_TERMINAL_JOB_STATUSES.has(String(job.status || ""))) {
    updateM3JobDom(route, job);
    startM3JobPoll(route, job.id);
  }
};

const stopAllM3JobPollers = () => workspaceKeys().forEach((workspaceKey) => stopM3JobPoll(workspaceKey));

const stopM3JobPoll = (workspaceKey) => {
  const entry = m3JobPollers.get(workspaceKey);
  if (!entry) return;
  if (entry.timer) clearTimeout(entry.timer);
  m3JobPollers.delete(workspaceKey);
};

const updateM3JobDom = (workspaceKey, job) => {
  const workspace = workspaceForKey(workspaceKey);
  if (!workspace || !job) return;
  const status = String(job.status || "unknown");
  const progressValue = Number(job.progress);
  const progress = Number.isFinite(progressValue) ? Math.max(0, Math.min(100, Math.round(progressValue))) : 0;
  const stateNode = workspace.querySelector("[data-workspace-job-status]");
  if (stateNode) stateNode.dataset.workspaceJobStatus = status;
  const label = workspace.querySelector("[data-workspace-job-status-label]");
  if (label) label.textContent = formatStatus(status);
  const value = workspace.querySelector("[data-workspace-job-progress-value]");
  if (value) value.textContent = `${progress}%`;
  const bar = workspace.querySelector("[data-workspace-job-progress]");
  if (bar) bar.style.width = `${progress}%`;
  const progressBar = bar?.closest("[role=progressbar]");
  if (progressBar) progressBar.setAttribute("aria-valuenow", String(progress));
  const message = job.message || job.error || job.result?.reason || "";
  const messageNode = workspace.querySelector("[data-workspace-job-message]");
  if (messageNode) messageNode.textContent = message;
};

const startM3JobPoll = (workspaceKey, jobId) => {
  stopM3JobPoll(workspaceKey);
  const startedAt = Date.now();
  const entry = { jobId, startedAt, timer: null, generation: Symbol(workspaceKey) };
  m3JobPollers.set(workspaceKey, entry);
  const poll = async () => {
    const current = m3JobPollers.get(workspaceKey);
    if (!current || current.jobId !== jobId || current.generation !== entry.generation || routeId() !== workspaceKey) {
      stopM3JobPoll(workspaceKey);
      return;
    }
    try {
      const payload = await getJob(jobId);
      const job = payload?.job && typeof payload.job === "object" ? payload.job : payload;
      if (!job || typeof job !== "object") throw new Error("JOB_SNAPSHOT_INVALID");
      const afterRead = m3JobPollers.get(workspaceKey);
      if (!afterRead || afterRead !== entry || routeId() !== workspaceKey) return;
      const model = workspaceModelForKey(workspaceKey);
      if (!workspaceJobMatchesSource(model, job)) {
        detachWorkspaceJob(model);
        stopM3JobPoll(workspaceKey);
        render({ focus: "main" });
        return;
      }
      model.job = job;
      updateM3JobDom(workspaceKey, job);
      if (M3_TERMINAL_JOB_STATUSES.has(String(job.status || ""))) {
        stopM3JobPoll(workspaceKey);
        render({ focus: "main" });
        return;
      }
    } catch (error) {
      // A transient loopback failure does not turn a live background job into
      // failed.  Keep the last truthful snapshot and retry until the bounded
      // observation window expires.
      const currentAfterError = m3JobPollers.get(workspaceKey);
      if (!currentAfterError || currentAfterError !== entry || routeId() !== workspaceKey) return;
      if (error?.status === 404 || error?.payload?.error === "job_not_found") {
        const model = workspaceModelForKey(workspaceKey);
        if (model) model.job = { id: jobId, status: "unavailable", progress: 0, message: "Snapshot job không còn khả dụng; mở Jobs để kiểm tra bản ghi canonical." };
        stopM3JobPoll(workspaceKey);
        render({ focus: "main" });
        return;
      }
      if (Date.now() - startedAt >= M3_JOB_POLL_MAX_MS) {
        const model = (workspaceKey === "ocr" || workspaceKey === "whisper" ? state.m4?.[workspaceKey] : state.m3?.[workspaceKey]);
        if (model?.job) {
          model.job = { ...model.job, message: "Không thể lấy snapshot job trong thời hạn theo dõi; job vẫn thuộc Jobs.", next_action: error?.message || "Mở Jobs để kiểm tra snapshot canonical." };
          updateM3JobDom(workspaceKey, model.job);
        }
        stopM3JobPoll(workspaceKey);
        return;
      }
    }
    const latest = m3JobPollers.get(workspaceKey);
    if (latest && latest === entry && latest.jobId === jobId && routeId() === workspaceKey) latest.timer = setTimeout(poll, M3_JOB_POLL_INTERVAL_MS);
  };
  void poll();
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
  const pickerButton = input.closest("[data-file-picker]")?.querySelector("[data-file-picker-button]");
  const selection = input.closest("[data-file-picker]")?.querySelector("[data-file-selection]");
  if (!selected.length) {
    if (pickerButton) pickerButton.textContent = "Chọn tệp";
    if (selection) selection.textContent = "Chưa chọn tệp";
    return;
  }
  const note = document.createElement("small");
  note.textContent = selected.length > 1 ? `${selected.length} tệp đã chọn; preview tệp đầu.` : selected[0].name;
  preview.append(note);
  if (pickerButton) pickerButton.textContent = "Đổi tệp";
  if (selection) selection.textContent = `${selected.length > 1 ? `${selected.length} tệp` : selected[0].name} · ${selected[0].type || "loại chưa rõ"} · ${Number.isFinite(selected[0].size) ? `${selected[0].size.toLocaleString()} bytes` : "kích thước chưa rõ"}`;
  const file = selected[0];
  const source = URL.createObjectURL(file); preview.dataset.objectUrl = source;
  if (file.type.startsWith("image/")) {
    const image = document.createElement("img"); image.src = source; image.alt = `Preview ${file.name}`;
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
  } else if (kind === "attach-workflow-v2") {
    const projectId = form.dataset.projectId || state.selectedProjectId;
    if (!projectId || !values.workflow_id) throw new Error("Chọn project và workflow hợp lệ trước khi liên kết.");
    result = await attachProjectWorkflowV2(projectId, values.workflow_id);
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

const updateSettingsDirtyUi = () => {
  const dirty = state.settingsDirty === true;
  const banner = document.querySelector("[data-settings-dirty]");
  if (banner) {
    banner.dataset.settingsDirty = String(dirty);
    banner.classList.toggle("is-dirty", dirty);
    const title = banner.querySelector("strong");
    const detail = banner.querySelector("span");
    if (title) title.textContent = dirty ? "Có thay đổi chưa áp dụng" : "Cài đặt đã đồng bộ";
    if (detail) detail.textContent = dirty ? "Các giá trị chỉ có hiệu lực sau khi bạn bấm Áp dụng & lưu." : "Không có thay đổi cục bộ đang chờ.";
  }
  const save = document.querySelector("[data-save-settings]");
  if (save) save.disabled = !dirty;
  const discard = document.querySelector("[data-discard-settings]");
  if (discard) discard.disabled = !dirty;
};

document.addEventListener("change", (event) => {
  const setting = event.target.closest("[data-setting-key]");
  if (setting) { state.settingsDirty = true; updateSettingsDirtyUi(); return; }
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
  if (input) {
    renderFilePreview(input);
    let sourceChanged = false;
    if (input.closest("[data-m3-workspace]")) sourceChanged = rememberM3FileSelection(input, state) || sourceChanged;
    if (input.closest("[data-m4-workspace]")) sourceChanged = rememberM4FileSelection(input, state) || sourceChanged;
    if (sourceChanged) render({ focus: "main" });
  }
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
  const setting = event.target.closest("[data-setting-key]");
  if (setting) { state.settingsDirty = true; updateSettingsDirtyUi(); return; }
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
  const form = event.target.closest("[data-global-search-form]");
  if (!form) return;
  event.preventDefault();
  const input = form.querySelector("[data-global-search-input]");
  const query = String(input?.value || "").slice(0, 80);
  try {
    const result = await searchProductExperienceV2(query);
    state.globalSearch = { query: result.query || query, results: result.results || [] };
  } catch (error) {
    state.globalSearch = { query, results: [] };
    showToast(error.message || "Không thể tìm catalog Hub.", "error");
  }
  renderGlobalSearch();
});

document.addEventListener("submit", async (event) => {
  const mediaPreflightForm = event.target.closest("form[data-media-preflight-form]");
  if (mediaPreflightForm) {
    event.preventDefault();
    const submit = mediaPreflightForm.querySelector("button[type=submit]"); if (submit) submit.disabled = true;
    inlineResult(mediaPreflightForm, "Đang lập kế hoạch media từ artifact opaque…");
    try {
      const values = Object.fromEntries(new FormData(mediaPreflightForm).entries());
      const operation = String(values.operation || "");
      const options = operation === "trim"
        ? { start_seconds: numberOr(values.start_seconds, 0), end_seconds: numberOr(values.end_seconds, 0) }
        : operation === "resize"
          ? { width: Math.trunc(numberOr(values.width, 0)), height: Math.trunc(numberOr(values.height, 0)) }
          : operation === "fps" || operation === "frame_interpolation"
            ? { fps: numberOr(values.fps, 0) }
            : operation === "video_upscale"
              ? { scale: Math.trunc(numberOr(values.scale, 0)) }
              : {};
      const payload = { artifact_id: String(values.artifact_id || ""), operation, options };
      if (values.backend) payload.backend = String(values.backend);
      const result = await preflightMediaPipelineV2(payload);
      state.mediaPipelinePreflight = result || null;
      const resultText = result?.status === "partial"
        ? `Đã lập preflight ${result.operation || operation} bằng ${result.backend || "backend"}. Chưa thực thi.`
        : result?.reason || result?.error || "Preflight media chưa khả dụng.";
      inlineResult(mediaPreflightForm, resultText, result?.status === "partial" ? "success" : "warning");
      showToast(resultText, result?.status === "partial" ? "success" : "warning");
      render({ focus: "main" });
    } catch (error) { inlineResult(mediaPreflightForm, error.message, "error"); showToast(error.message, "error"); }
    finally { if (submit) submit.disabled = false; }
    return;
  }
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
  const submitButtons = [...view.querySelectorAll("button[type=submit]")].filter((button) => button.form === form || button.getAttribute("form") === form.id);
  submitButtons.forEach((button) => { button.disabled = true; });
  inlineResult(form, "Đang tải input và tạo job…");
  try {
    const payload = await toPayload(form); const tool = toolForForm(form, payload); const result = await submitJob(tool, payload);
    const workspace = form.closest("[data-m3-workspace], [data-m4-workspace]");
    if (workspace) {
      const isM4 = Boolean(workspace.dataset.m4Workspace);
      const workspaceKey = isM4 ? workspace.dataset.m4Workspace : workspace.dataset.m3Workspace;
      const workspaceModel = workspaceModelForKey(workspaceKey);
      workspaceModel.staleJob = null;
      workspaceModel.job = result.job || null;
      inlineResult(form, `Đã tạo ${result.job?.id || "job"}. Đang theo dõi ngay trong workspace.`, "success");
      showToast(`Đã thêm ${tool} vào hàng đợi Hub.`);
      render({ focus: "main" });
      if (result.job?.id) startM3JobPoll(workspaceKey, result.job.id);
      await refreshFast({ quiet: true, renderView: false });
    } else {
      inlineResult(form, `Đã tạo ${result.job?.id || "job"}. Theo dõi ở Jobs.`, "success"); showToast(`Đã thêm ${tool} vào hàng đợi Hub.`); await refreshFast({ quiet: true });
    }
  } catch (error) { inlineResult(form, error.message, "error"); showToast(error.message, "error"); }
  finally { submitButtons.forEach((button) => { button.disabled = false; }); applyToolActionGates(); }
});

document.addEventListener("click", async (event) => {
  const filePickerButton = event.target.closest("[data-file-picker-button]");
  if (filePickerButton) {
    event.preventDefault();
    const input = filePickerButton.closest("[data-file-picker]")?.querySelector("input[type=file]");
    input?.click();
    return;
  }
  if (event.target.closest("[data-command-close]")) {
    state.commandPaletteOpen = false;
    renderCommandPalette();
    return;
  }
  const commandAction = event.target.closest("[data-command-action]");
  if (commandAction) {
    const commandId = commandAction.dataset.commandAction || "";
    state.commandPaletteOpen = false;
    renderCommandPalette();
    if (commandId === "search-artifact") {
      const input = document.querySelector("[data-global-search-input]");
      if (input) input.value = "artifact";
      try {
        const result = await searchProductExperienceV2("artifact");
        state.globalSearch = { query: result.query || "artifact", results: result.results || [] };
      } catch (error) { showToast(error.message || "Không thể tìm artifact metadata.", "error"); }
      renderGlobalSearch();
      return;
    }
    const route = commandAction.dataset.commandRoute;
    if (route) window.location.hash = `#/${route}`;
    return;
  }
  const skipOnboarding = event.target.closest("[data-onboarding-skip]");
  if (skipOnboarding) {
    state.onboardingDismissed = true;
    render();
    return;
  }
  const showOnboarding = event.target.closest("[data-onboarding-show]");
  if (showOnboarding) {
    state.onboardingDismissed = false;
    render();
    return;
  }
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
    try { state.componentManager = await getComponents(); syncComponentSelection(); render(); showToast("Đã làm mới Component Manager.", "success"); }
    catch (error) { showToast(error.message || "Không thể làm mới Component Manager.", "error"); }
    finally { refreshComponents.disabled = false; }
    return;
  }
  const componentSelect = event.target.closest("[data-component-select]");
  if (componentSelect) {
    state.selectedComponentId = componentSelect.dataset.componentSelect || "";
    state.componentDetailOpen = true;
    render();
    focusComponentDetail();
    return;
  }
  if (event.target.closest("[data-component-close-detail]")) {
    state.componentDetailOpen = false;
    render();
    focusComponentMaster();
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
    if (!isRoutableRoute(nextRoute)) {
      showToast("Điểm đến Hub không hợp lệ; không điều hướng.", "error");
      return;
    }
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
    const scope = ["image", "media", "video", "sam2", "animesr"].includes(galleryUse.dataset.galleryScope) ? galleryUse.dataset.galleryScope : "image";
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
  if (event.target.closest("[data-discard-settings]")) {
    state.settingsDirty = false;
    state.settingsActionStatus = "Đã hủy các thay đổi chưa áp dụng.";
    render();
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
        state.settingsDirty = false;
        state.settingsActionStatus = `Đã áp dụng & lưu cài đặt · revision ${result.settings_revision}.`;
        // The server has persisted the values atomically.  Reflect the same
        // contract in the current shell immediately; otherwise language and
        // theme appear to save successfully but stay stale until restart.
        setLanguage(lang);
        applyTheme(theme);
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
        state.settingsDirty = false;
        state.settingsActionStatus = `Đã đặt lại cấu hình phần ${section} · revision ${result.settings_revision}.`;
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
      state.modelActionStatus = "Đã bắt đầu quét storage nền; số liệu sẽ cập nhật dần và chỉ chính xác khi tiến độ đạt 100%.";
      showToast(state.modelActionStatus, "success");
      render();
    } catch (error) {
      state.modelActionStatus = error.message || "Không thể quét lại storage.";
      showToast(state.modelActionStatus, "error");
      render();
    }
    return;
  }
  const cancelStorageButton = event.target.closest("[data-cancel-storage-scan]");
  if (cancelStorageButton) {
    cancelStorageButton.disabled = true;
    try {
      const result = await cancelStorageScan(cancelStorageButton.dataset.cancelStorageScan || "");
      state.storage = result ? { ...(state.storage || {}), ...result, disk: result.disk || state.storage?.disk || {} } : state.storage;
      state.storageScan = result?.scan || state.storageScan;
      updateStorageScanDom(state.storage || {});
      showToast("Đã gửi yêu cầu hủy quét storage; worker sẽ dừng ở checkpoint gần nhất.", "warning");
      if (STORAGE_SCAN_ACTIVE_STATES.includes(String(state.storageScan?.status || ""))) pollStorageScan(state.storageScan.scan_id || "");
    } catch (error) {
      showToast(error.message || "Không thể hủy quét storage.", "error");
      cancelStorageButton.disabled = false;
    }
    return;
  }
  const resumeStoragePollingButton = event.target.closest("[data-resume-storage-polling]");
  if (resumeStoragePollingButton) {
    const scanId = resumeStoragePollingButton.dataset.resumeStoragePolling || "";
    state.storageScan = { ...(state.storageScan || {}), polling_limited: false };
    state.storage = { ...(state.storage || {}), scan: state.storageScan };
    updateStorageScanDom(state.storage);
    pollStorageScan(scanId);
    showToast("Đã tiếp tục theo dõi scan storage nền.", "success");
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
  const closeApplicationButton = event.target.closest("[data-close-application]");
  const refreshApplicationsButton = event.target.closest("[data-refresh-applications]");
  if (refreshApplicationsButton) {
    refreshApplicationsButton.disabled = true;
    try {
      const results = await Promise.allSettled([getApplications(), getExternalIntegrationsV2()]);
      if (results[0].status !== "fulfilled") throw results[0].reason;
      state.applications = results[0].value?.applications || [];
      if (results[1].status === "fulfilled") state.externalIntegrationsV2 = results[1].value || {};
      render();
      showToast("Đã làm mới trạng thái AIRI.", "success");
    } catch (error) {
      showToast(error?.message || "Không thể làm mới trạng thái AIRI.", "error");
    } finally { refreshApplicationsButton.disabled = false; }
    return;
  }
  if (launchButton) {
    launchButton.disabled = true;
    try {
      const result = await launchApplication(launchButton.dataset.launch);
      const refreshed = await Promise.allSettled([getApplications(), getExternalIntegrationsV2()]);
      if (refreshed[0].status === "fulfilled") state.applications = refreshed[0].value?.applications || state.applications;
      if (refreshed[1].status === "fulfilled") state.externalIntegrationsV2 = refreshed[1].value || state.externalIntegrationsV2;
      render();
      showToast(result.status === "already_running" ? "AIRI đã được Hub quản lý và đang chạy." : `${result.application || "AIRI"}: đang khởi chạy.`);
    }
    catch (error) { showToast(`Không thể mở AIRI: ${error.message}`, "error"); }
    finally { launchButton.disabled = false; }
    return;
  }
  if (closeApplicationButton) {
    closeApplicationButton.disabled = true;
    try {
      const result = await closeApplication(
        closeApplicationButton.dataset.closeApplication || "",
        closeApplicationButton.dataset.launchInstanceId || "",
      );
      const refreshed = await Promise.allSettled([getApplications(), getExternalIntegrationsV2()]);
      if (refreshed[0].status === "fulfilled") state.applications = refreshed[0].value?.applications || state.applications;
      if (refreshed[1].status === "fulfilled") state.externalIntegrationsV2 = refreshed[1].value || state.externalIntegrationsV2;
      render();
      showToast(result.message || "AIRI đã được đóng.", "success");
    } catch (error) {
      showToast(error?.message || "Không thể đóng AIRI.", "error");
    } finally { closeApplicationButton.disabled = false; }
    return;
  }
  const workspaceCancel = event.target.closest("[data-cancel-workspace-job]");
  if (workspaceCancel) {
    workspaceCancel.disabled = true;
    const workspace = workspaceCancel.closest("[data-m3-workspace], [data-m4-workspace]");
    const workspaceKey = workspace?.dataset.m3Workspace || workspace?.dataset.m4Workspace || "";
    try {
      const result = await cancelJob(workspaceCancel.dataset.cancelWorkspaceJob || "");
      const job = result?.job && typeof result.job === "object" ? result.job : null;
      if (workspaceKey && job) {
        workspaceModelForKey(workspaceKey).job = job;
        updateM3JobDom(workspaceKey, job);
      }
      showToast(result?.message || "Đang hủy tác vụ Hub-owned.", "success");
    } catch (error) {
      showToast(error.message || "Không thể hủy tác vụ.", "error");
    } finally { workspaceCancel.disabled = false; }
    return;
  }
  const workspaceJson = event.target.closest("[data-workspace-download-json]");
  if (workspaceJson) {
    const workspace = workspaceJson.closest("[data-m3-workspace], [data-m4-workspace]");
    const workspaceKey = workspace?.dataset.m3Workspace || workspace?.dataset.m4Workspace || workspaceJson.dataset.workspaceDownloadJson || "vision";
    const job = workspaceModelForKey(workspaceKey)?.job;
    if (!job?.result) { showToast("Chưa có result contract để tải.", "warning"); return; }
    downloadJson(`${workspaceKey}-${job.id || "result"}.json`, job.result.annotation || job.result.selection || job.result.ocr_result || job.result.transcript || job.result);
    showToast("Đã tạo JSON từ result contract opaque.", "success");
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
      const result = await retryDurableJob(durableResume.dataset.resumeDurableJob);
      const refreshed = await refreshFast({ quiet: true });
      if (refreshed === false) render();
      const message = result?.retry_contract === "new_job"
        ? "Đã tạo tác vụ mới nhưng chưa thực thi; bản ghi lỗi cũ được giữ nguyên."
        : safeDisplayMessage(result?.next_action, "Không thể tạo lại tác vụ từ snapshot hiện tại.");
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
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    state.commandPaletteOpen = state.commandPaletteOpen !== true;
    renderCommandPalette();
    return;
  }
  if (event.key === "Escape") {
    if (state.commandPaletteOpen === true) {
      event.preventDefault();
      state.commandPaletteOpen = false;
      renderCommandPalette();
      return;
    }
    if (artifactPreviewLayer && !artifactPreviewLayer.matches(":empty")) {
      event.preventDefault();
      closeArtifactPreview();
      return;
    }
    if (routeId() === "components" && view.querySelector('[data-component-detail-panel][data-component-detail-open="true"]')) {
      event.preventDefault();
      state.componentDetailOpen = false;
      render();
      focusComponentMaster();
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
  if (routeId() === "components") {
    const masterButton = event.target?.closest?.("[data-component-select]");
    if (masterButton) {
      const buttons = [...view.querySelectorAll("[data-component-select]")];
      const currentIndex = buttons.indexOf(masterButton);
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        if (!buttons.length) return;
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        const nextIndex = (currentIndex + direction + buttons.length) % buttons.length;
        state.selectedComponentId = buttons[nextIndex].dataset.componentSelect || "";
        state.componentDetailOpen = false;
        render();
        focusComponentMaster(state.selectedComponentId);
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        state.selectedComponentId = masterButton.dataset.componentSelect || "";
        state.componentDetailOpen = true;
        render();
        focusComponentDetail();
        return;
      }
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
window.addEventListener("hashchange", async () => {
  routeRenderGeneration += 1;
  routeLoad = null;
  routeLoadKey = "";
  storageScanPoller.stop();
  stopAllM3JobPollers();
  render({ focus: "main" });
  await loadRouteData();
});
syncSidebarState();
applyTheme(currentTheme());
setLanguage(currentLanguage());
initialize().catch(() => { void showFrontendBootstrapFailure(); });
