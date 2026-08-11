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
export const getApplications = () => request("/api/applications");
export const getSettings = () => request("/api/settings");
export const getJobs = () => request("/api/jobs");
export const getDurableJobs = () => request("/api/durable-jobs");
export const resumeDurableJob = (id) => request(`/api/durable-jobs/${encodeURIComponent(id)}/resume`, { method: "POST" });
export const getLifecycle = () => request("/api/lifecycle");
export const getCapabilities = () => request("/api/capabilities");
export const getWorkflowLibrary = () => request("/api/workflow-library");
export const getWorkflowLibraryEntry = (id) => request(`/api/workflow-library/${encodeURIComponent(id)}`);
export const saveWorkflowLibrary = (workflow, expectedRevision) => request("/api/workflow-library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workflow, expected_revision: expectedRevision }) });
export const deleteWorkflowLibrary = (id, expectedRevision) => request(`/api/workflow-library/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: expectedRevision }) });
export const planWorkflowLibraryMigration = (entries) => request("/api/workflow-library/migration/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries }) });
export const confirmWorkflowLibraryMigration = (entries, expectedRevision) => request("/api/workflow-library/migration/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entries, expected_revision: expectedRevision }) });

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
