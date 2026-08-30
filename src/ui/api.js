/*
  FILE NOTE
  - Mục đích: Typed HTTP client cho Local AI Hub frontend (settings, diagnostics, backup/restore, node drafts, projects, jobs, workflows)
  - Liên kết trực tiếp: src/ui/app.js, src/ui/pages.js, src/ui/node_studio.js, src/ui/image_mask_studio.js
  - Vùng ảnh hưởng khi sửa: Toàn bộ các tương tác gọi API từ frontend tới backend
*/

export class HubApiError extends Error {
  constructor(message, payload = {}, status = 0) {
    super(message);
    this.name = "HubApiError";
    this.payload = payload;
    this.status = status;
  }
}

const request = async (path, options = {}) => {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); } catch { payload = { status: "error", error: "Phản hồi không phải JSON." }; }
  if (!response.ok) {
    const message = payload.error || payload.reason || payload.message || `HTTP ${response.status}`;
    throw new HubApiError(message, payload, response.status);
  }
  return payload;
};

export const getHealth = () => request("/health");
export const getBootstrap = () => request("/api/bootstrap");
export const getTools = () => request("/tools");
export const getDashboard = () => request("/api/dashboard");
export const getStorage = () => request("/api/storage");
export const getModels = () => request("/api/models");
export const getModules = () => request("/api/modules");
export const getModule = (id) => request(`/api/modules/${encodeURIComponent(id)}`);
export const getComponents = () => request("/api/components");
export const getComponent = (id) => request(`/api/components/${encodeURIComponent(id)}`);
export const getComponentPlan = (id) => request(`/api/components/plans/${encodeURIComponent(id)}`);
export const createComponentPlan = (componentId, componentType, variant = "default") => request("/api/components/install/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ component_id: componentId, component_type: componentType, variant }) });
export const confirmComponentPlan = (planId, confirmed = false) => request(`/api/components/plans/${encodeURIComponent(planId)}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed }) });
export const createComponentBundlePlan = (componentId, componentType = "model", variant = "default") => request("/api/components/bundles/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ component_id: String(componentId), component_type: componentType, variant }) });
export const getComponentBundlePlan = (planId) => request(`/api/components/bundles/${encodeURIComponent(planId)}`);
export const confirmComponentBundle = (planId, confirmed = false) => request(`/api/components/bundles/${encodeURIComponent(planId)}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: Boolean(confirmed) }) });
export const createComponentReusePlan = (componentId, componentType = "model") => request("/api/components/reuse/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ component_id: String(componentId), component_type: componentType }) });
export const confirmComponentReuse = (planId, confirmed = false) => request(`/api/components/reuse/${encodeURIComponent(planId)}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: Boolean(confirmed) }) });
export const applyComponentPlan = (planId, confirmed = false) => request("/api/components/install/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan_id: planId, confirmed }) });
export const createComponentImportPlan = (selectionId, mode) => request("/api/components/import/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selection_id: selectionId, mode }) });
export const confirmComponentImport = (planId, confirmed = false) => request("/api/components/import/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan_id: String(planId), confirmed: Boolean(confirmed) }) });
export const createComponentVerifyPlan = (componentId, componentType) => request("/api/components/verify/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ component_id: componentId, component_type: componentType }) });
export const createComponentMaintenancePlan = (componentId, action) => request("/api/components/maintenance/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ component_id: componentId, action }) });
export const confirmComponentMaintenance = (planId, confirmed = false) => request(`/api/components/maintenance/${encodeURIComponent(planId)}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed }) });
export const getUpdateSettings = () => request("/api/updates/settings");
export const setUpdateSchedule = (policy) => request("/api/updates/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ policy }) });
export const checkComponentUpdate = (componentId, refreshSource = false) => request("/api/updates/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ component_id: String(componentId), refresh_source: Boolean(refreshSource) }) });
export const checkAllUpdates = (refreshSource = false) => request("/api/updates/check-all", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_source: Boolean(refreshSource) }) });
export const planComponentUpdate = (componentId) => request("/api/updates/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ component_id: String(componentId) }) });
export const confirmComponentUpdate = (planId, confirmed = false) => request(`/api/updates/plans/${encodeURIComponent(planId)}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: Boolean(confirmed) }) });
export const rollbackComponentUpdate = (componentId) => request("/api/updates/rollback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ component_id: String(componentId) }) });
export const getApplications = () => request("/api/applications");
export const getExternalIntegrationsV2 = () => request("/api/external-integrations/v2");
export const getProductExperienceV2 = () => request("/api/product-experience/v2");
export const searchProductExperienceV2 = (query = "") => request(`/api/product-experience/v2/search?${new URLSearchParams({ q: String(query || "").slice(0, 80) })}`);
export const getPlatformHardeningV2 = () => request("/api/platform-hardening/v2");
export const getPlatformHardeningAreaV2 = (areaId) => request(`/api/platform-hardening/v2/${encodeURIComponent(areaId)}`);
export const getPlatformExtensibilityV2 = () => request("/api/extensibility/v2");
export const getPlatformExtensibilityAreaV2 = (areaId) => request(`/api/extensibility/v2/${encodeURIComponent(areaId)}`);
export const getSettings = () => request("/api/settings");
export const getSettingsSchema = () => request("/api/settings/schema");
export const patchSettings = (patch, expectedRevision) => request("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...patch, expected_revision: expectedRevision }) });
export const resetSettingsSection = (section) => request("/api/settings/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ section }) });
export const getJobs = () => request("/api/jobs");
export const deleteJobHistory = (id) => request(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
export const clearTerminalJobHistory = (confirmed = false) => request("/api/jobs/history/clear", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: Boolean(confirmed) }) });
export const getDurableJobs = () => request("/api/durable-jobs");
export const resumeDurableJob = (id) => request(`/api/durable-jobs/${encodeURIComponent(id)}/resume`, { method: "POST" });
export const retryDurableJob = (id) => request(`/api/durable-jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
export const getLifecycle = () => request("/api/lifecycle");
export const getCapabilities = () => request("/api/capabilities");
export const getWorkflowLibrary = () => request("/api/workflow-library");
export const getWorkflowLibraryEntry = (id) => request(`/api/workflow-library/${encodeURIComponent(id)}`);
export const saveWorkflowLibrary = (workflow, expectedRevision) => request("/api/workflow-library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workflow, expected_revision: expectedRevision }) });
export const deleteWorkflowLibrary = (id, expectedRevision) => request(`/api/workflow-library/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: expectedRevision }) });
export const setWorkflowLibraryFavorite = (id, favorite, expectedRevision) => request(`/api/workflow-library/${encodeURIComponent(id)}/favorite`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(expectedRevision === undefined ? { favorite: Boolean(favorite) } : { favorite: Boolean(favorite), expected_revision: expectedRevision }) });
export const markWorkflowLibraryOpened = (id, expectedRevision) => request(`/api/workflow-library/${encodeURIComponent(id)}/opened`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(expectedRevision === undefined ? {} : { expected_revision: expectedRevision }) });
export const planWorkflowLibraryMigration = (entries) => request("/api/workflow-library/migration/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries }) });
export const confirmWorkflowLibraryMigration = (entries, expectedRevision) => request("/api/workflow-library/migration/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries, expected_revision: expectedRevision }) });

// V6 Diagnostics Center
export const getDiagnosticsSnapshot = () => request("/api/diagnostics/snapshot");
export const getDiagnosticsSubsystem = (name) => request(`/api/diagnostics/subsystem/${encodeURIComponent(name)}`);
export const exportDiagnosticsBundle = () => request("/api/diagnostics/export");
export const repairVerifyConfig = () => request("/api/diagnostics/repair/verify-config", { method: "POST" });
export const repairInspectRecovery = () => request("/api/diagnostics/repair/inspect-recovery", { method: "POST" });
export const getRecoveryDrafts = () => request("/api/diagnostics/repair/recovery-drafts");
export const repairClearRecoveryDrafts = (scopes = [], confirmed = false) => request("/api/diagnostics/repair/clear-recovery-drafts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scopes, confirmed }) });

// V6 Backup / Restore
export const listBackups = () => request("/api/backup/list");
export const createBackup = () => request("/api/backup/create", { method: "POST" });
export const inspectBackup = (backupId) => request("/api/backup/inspect", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ backup_id: backupId }) });
export const planRestore = (backupId) => request("/api/backup/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ backup_id: backupId }) });
export const applyRestore = (planId, confirmed = false) => request("/api/backup/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plan_id: planId, confirmed }) });

// V6 Node Studio Drafts
export const getNodeDraft = (scope) => request(`/api/node-studio/drafts/${encodeURIComponent(scope)}`);
export const saveNodeDraft = (scope, graph) => request(`/api/node-studio/drafts/${encodeURIComponent(scope)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph }) });
export const clearNodeDraft = (scope) => request(`/api/node-studio/drafts/${encodeURIComponent(scope)}`, { method: "DELETE" });

export const uploadFile = async (file) => {
  if (!file) throw new Error("Chọn tệp trước khi tải lên Hub.");
  const response = await fetch("/api/uploads", {
    method: "POST",
    headers: { "X-File-Name": encodeURIComponent(file.name), "Content-Type": file.type || "application/octet-stream", Accept: "application/json" },
    body: file,
  });
  let payload = {};
  try { payload = await response.json(); } catch { payload = { error: "Upload không trả JSON." }; }
  if (!response.ok) throw new Error(payload.error || `Upload thất bại (HTTP ${response.status}).`);
  return payload.artifact;
};

export const submitJob = (tool, payload) => request(`/api/jobs/${encodeURIComponent(tool)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const cancelJob = (id) => request(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
export const resumeJob = (id) => request(`/jobs/${encodeURIComponent(id)}/resume`, { method: "POST" });
export const openArtifact = (id) => request(`/api/artifacts/${encodeURIComponent(id)}/open`, { method: "POST" });
export const launchApplication = (id) => request(`/api/applications/${encodeURIComponent(id)}/launch`, { method: "POST" });
export const closeOwnedBackends = () => request("/api/lifecycle/close", { method: "POST" });
export const scanStorage = () => request("/api/storage/scan", { method: "POST" });
export const getStorageScan = () => request("/api/storage/scan");
export const cancelStorageScan = (scanId = "") => request(`/api/storage/scan/cancel${scanId ? `?scan_id=${encodeURIComponent(scanId)}` : ""}`, { method: "POST" });
export const getComfyAdvanced = () => request("/api/comfyui/advanced");
export const startComfyAdvanced = () => request("/api/comfyui/advanced/start", { method: "POST" });
export const getComfyBridgeWorkflows = () => request("/api/comfyui/workflows");
export const getComfyBridgeWorkflow = (id) => request(`/api/comfyui/workflows/${encodeURIComponent(id)}`);
export const saveComfyBridgeWorkflow = (id, workflow) => request(`/api/comfyui/workflows/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(workflow) });

export const getNodeRegistry = (scope) => request(`/api/node-studio/registry?scope=${encodeURIComponent(scope || "")}`);
export const getNodeAvailability = (scope) => request(`/api/node-studio/availability?scope=${encodeURIComponent(scope || "")}`);
export const getNodePresets = () => request("/api/node-studio/presets");
export const getNodePreset = (id) => request(`/api/node-studio/presets/${encodeURIComponent(id)}`);
export const validateNodeGraph = (graph, requireRunnable = false) => request("/api/node-studio/validate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph, require_runnable: requireRunnable }) });
export const getDirtyNodes = (graph, changedNodeIds) => request("/api/node-studio/dirty", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph, changed_node_ids: changedNodeIds }) });
export const runNodeGraph = (graph, draft = false) => request("/api/node-studio/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph, draft }) });
export const getNodeRun = (jobId) => request(`/api/node-studio/runs/${encodeURIComponent(jobId)}`);

// Post-V8 Milestone 2: preflight and projections are deliberately separate
// from Run Graph. They return only plan/read-only metadata and never start a
// worker, provider, model, GPU or media process.
export const getWorkflowRuntimeV2 = () => request("/api/workflow-runtime/v2");
export const preflightWorkflowRuntimeV2 = (payload) => request("/api/workflow-runtime/v2/preflight", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const dispatchWorkflowRuntimeV2 = (payload) => request("/api/workflow-runtime/v2/dispatch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const getProjectWorkspaceV2 = () => request("/api/project-workspace/v2");
export const getProjectWorkspaceV2Entry = (id) => request(`/api/project-workspace/v2/${encodeURIComponent(id)}`);
export const getProjectWorkspaceV2Manifest = (id) => request(`/api/project-workspace/v2/${encodeURIComponent(id)}/manifest`);
export const attachProjectWorkflowV2 = (projectId, workflowId) => request(`/api/project-workspace/v2/${encodeURIComponent(projectId)}/workflows`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workflow_id: workflowId }) });
export const attachProjectJobV2 = (projectId, jobId) => request(`/api/project-workspace/v2/${encodeURIComponent(projectId)}/jobs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: jobId }) });
export const getArtifactLibraryV2 = (limit = 120) => request(`/api/artifact-library/v2?limit=${encodeURIComponent(Math.max(1, Math.min(240, Number(limit) || 120)))}`);
export const getArtifactLibraryV2Entry = (id) => request(`/api/artifact-library/v2/${encodeURIComponent(id)}`);
export const getMediaPipelineV2 = () => request("/api/media-pipeline/v2");
export const preflightMediaPipelineV2 = (payload) => request("/api/media-pipeline/v2/preflight", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });

// Milestone 4A creative workspace: all records are local metadata and opaque
// artifact IDs.  The client never receives filesystem paths or secret fields.
export const getCreativeOverview = () => request("/api/creative/overview");
export const getProjects = () => request("/api/projects");
export const getProject = (id) => request(`/api/projects/${encodeURIComponent(id)}`);
export const createProject = (payload) => request("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const updateProject = (id, payload) => request(`/api/projects/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const archiveProject = (id, archived = true) => request(`/api/projects/${encodeURIComponent(id)}/${archived ? "archive" : "restore"}`, { method: "POST" });
export const addProjectAsset = (projectId, payload) => request(`/api/projects/${encodeURIComponent(projectId)}/assets`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const getProjectCompare = (projectId) => request(`/api/projects/${encodeURIComponent(projectId)}/compare`);
export const updateProjectCompare = (projectId, payload) => request(`/api/projects/${encodeURIComponent(projectId)}/compare`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const exportProject = (projectId) => request(`/api/projects/${encodeURIComponent(projectId)}/export`);
export const importProject = (payload) => request("/api/projects/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const getAssets = ({ query = "", tag = "", favorite = false, collection = "", project = "" } = {}) => request(`/api/assets?${new URLSearchParams({ query, tag, favorite: String(favorite), collection, project })}`);
export const updateAsset = (id, payload) => request(`/api/assets/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const getCollections = () => request("/api/collections");
export const createCollection = (payload) => request("/api/collections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const updateCollection = (id, payload) => request(`/api/collections/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const getRecipes = () => request("/api/recipes");
export const createRecipe = (payload) => request("/api/recipes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const updateRecipe = (id, payload) => request(`/api/recipes/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const applyRecipe = (id, payload = {}) => request(`/api/recipes/${encodeURIComponent(id)}/apply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const exportRecipePack = (ids = []) => request(`/api/recipes/export-pack${ids.length ? `?${ids.map((id) => `id=${encodeURIComponent(id)}`).join("&")}` : ""}`);
export const getProjectManifest = (id) => request(`/api/projects/${encodeURIComponent(id)}/manifest`);
export const getProjectMissingArtifacts = (id) => request(`/api/projects/${encodeURIComponent(id)}/missing-artifacts`);
export const searchAssets = (params = {}) => request(`/api/assets/search?${new URLSearchParams(params)}`);
export const getArtifactStatus = (id) => request(`/api/artifacts/${encodeURIComponent(id)}/status`);
export const importRecipePack = (payload) => request("/api/recipes/import-pack", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const getWorkflowGallery = () => request("/api/workflow-gallery");

// Image & Mask Studio is a declarative local editor.  Its public API carries
// opaque artifact/session IDs only; pixels remain in the Artifact Store.
export const getImageMaskStudioOverview = (projectId = "") => request(`/api/image-mask-studio/overview${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`);
export const getImageMaskStudioPreflight = () => request("/api/image-mask-studio/preflight");
export const getImageMaskSession = (id) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(id)}`);
export const createImageMaskSession = (payload) => request("/api/image-mask-studio/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const updateImageMaskSession = (id, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const addImageMaskLayer = (id, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(id)}/layers`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const updateImageMaskLayer = (sessionId, layerId, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/layers/${encodeURIComponent(layerId)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const moveImageMaskLayer = (sessionId, layerId, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/layers/${encodeURIComponent(layerId)}/move`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const removeImageMaskLayer = (sessionId, layerId, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/layers/${encodeURIComponent(layerId)}/remove`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const applyImageMaskOperation = (sessionId, layerId, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/layers/${encodeURIComponent(layerId)}/operations`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const undoImageMaskSession = (id, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(id)}/undo`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const redoImageMaskSession = (id, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(id)}/redo`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const saveImageMaskSession = (id, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(id)}/save`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const getImageMaskCompare = (id, before = "", after = "") => request(`/api/image-mask-studio/sessions/${encodeURIComponent(id)}/compare?${new URLSearchParams({ ...(before ? { before } : {}), ...(after ? { after } : {}) })}`);
export const restoreImageMaskSnapshot = (sessionId, snapshotId, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/snapshots/${encodeURIComponent(snapshotId)}/restore`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const exportImageMask = (sessionId, layerId) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/layers/${encodeURIComponent(layerId)}/export`);
export const importImageMask = (sessionId, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/masks/import`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const captureImageMaskPreset = (sessionId, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/presets`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const applyImageMaskPreset = (sessionId, presetId, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/presets/${encodeURIComponent(presetId)}/apply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
export const linkImageMaskProject = (sessionId, payload) => request(`/api/image-mask-studio/sessions/${encodeURIComponent(sessionId)}/link-project`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });

export const formatGb = (bytes) => {
  const value = Number(bytes || 0) / (1024 ** 3);
  return `${value.toFixed(value >= 10 ? 1 : 2)} GB`;
};

export const formatStatus = (status) => ({
  healthy: "Sẵn sàng",
  operational: "Sẵn sàng",
  installed: "Đã cài",
  running: "Đang chạy",
  starting: "Đang chuẩn bị",
  queued: "Đang chờ",
  cancelling: "Đang hủy",
  cancelled: "Đã hủy",
  interrupted: "Bị gián đoạn",
  completed: "Hoàn tất",
  failed: "Thất bại",
  partial: "Một phần",
  unavailable: "Chưa khả dụng",
  not_installed: "Chưa cài",
  external_managed: "Quản lý bên ngoài",
  planned: "Đã hoạch định",
}[status] || status || "Chưa rõ");

export const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");
