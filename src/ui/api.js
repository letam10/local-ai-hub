const request = async (path, options = {}) => {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); } catch { payload = { status: "error", error: "Phản hồi không phải JSON." }; }
  if (!response.ok) {
    const message = payload.error || payload.reason || payload.message || `HTTP ${response.status}`;
    throw new Error(message);
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
export const getLifecycle = () => request("/api/lifecycle");

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
export const getNodePresets = () => request("/api/node-studio/presets");
export const getNodePreset = (id) => request(`/api/node-studio/presets/${encodeURIComponent(id)}`);
export const validateNodeGraph = (graph, requireRunnable = false) => request("/api/node-studio/validate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph, require_runnable: requireRunnable }) });
export const getDirtyNodes = (graph, changedNodeIds) => request("/api/node-studio/dirty", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph, changed_node_ids: changedNodeIds }) });
export const runNodeGraph = (graph, draft = false) => request("/api/node-studio/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph, draft }) });
export const getNodeRun = (jobId) => request(`/api/node-studio/runs/${encodeURIComponent(jobId)}`);

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
