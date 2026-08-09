const request = async (path, options = {}) => {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); } catch { payload = { status: "error", error: "Phản hồi không phải JSON." }; }
  if (!response.ok) {
    const message = payload.error || payload.reason || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
};

export const getHealth = () => request("/health");
export const getDashboard = () => request("/api/dashboard");
export const getStorage = () => request("/api/storage");
export const getModels = () => request("/api/models");
export const getApplications = () => request("/api/applications");
export const getSettings = () => request("/api/settings");
export const getJobs = () => request("/jobs");

export const launchApplication = (id) => request(`/api/applications/${encodeURIComponent(id)}/launch`, { method: "POST" });

export const formatGb = (bytes) => {
  const value = Number(bytes || 0) / (1024 ** 3);
  return `${value.toFixed(value >= 10 ? 1 : 2)} GB`;
};

export const formatStatus = (status) => ({
  operational: "Sẵn sàng",
  installed: "Đã cài",
  running: "Đang chạy",
  partial: "Một phần",
  unavailable: "Chưa khả dụng",
  not_installed: "Chưa cài",
  external_managed: "Quản lý bên ngoài",
  planned: "Đã hoạch định",
  queued: "Đang chờ",
  completed: "Hoàn tất",
  failed: "Thất bại",
}[status] || status || "Chưa rõ");

export const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");
