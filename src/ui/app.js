import {
  getApplications,
  getDashboard,
  getJobs,
  getModels,
  getSettings,
  getStorage,
  launchApplication,
  formatGb,
  formatStatus,
} from "./api.js";
import { NAVIGATION, renderPage } from "./pages.js";

const state = {
  health: {},
  components: [],
  applications: [],
  jobs: [],
  models: [],
  storage: {},
  settings: {},
};

const view = document.querySelector("#module-view");
const nav = document.querySelector("#sidebar-nav");
const topStatus = document.querySelector("#top-status");
const diskMetric = document.querySelector("#disk-metric");
const gpuMetric = document.querySelector("#gpu-metric");
const jobSummary = document.querySelector("#job-summary");
const toastRegion = document.querySelector("#toast-region");

const routeId = () => {
  const value = window.location.hash.replace(/^#\/?/, "").split("/")[0];
  return NAVIGATION.flatMap((group) => group.items).some(([id]) => id === value) ? value : "dashboard";
};

const showToast = (message) => {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4200);
};

const renderNavigation = () => {
  const active = routeId();
  nav.innerHTML = NAVIGATION.map((group) => `
    <div class="nav-group">${group.group}</div>
    ${group.items.map(([id, label, icon]) => `<button class="nav-item ${id === active ? "is-active" : ""}" type="button" data-route="${id}" aria-current="${id === active ? "page" : "false"}"><span class="nav-icon">${icon}</span><span>${label}</span></button>`).join("")}
  `).join("");
};

const updateTopbar = () => {
  const health = state.health || {};
  const disk = health.disk || {};
  const gpu = health.gpu || {};
  topStatus.textContent = health.status ? `${formatStatus(health.status)} · API loopback` : "API chưa sẵn sàng";
  diskMetric.textContent = disk.free_bytes ? `Ổ đĩa ${formatGb(disk.free_bytes)} trống` : "Ổ đĩa —";
  gpuMetric.textContent = gpu.name ? `GPU ${gpu.name}` : "GPU chưa phát hiện";
  const active = state.jobs.filter((job) => ["starting", "running"].includes(job.status)).length;
  jobSummary.textContent = `Jobs: ${active} đang chạy · ${state.jobs.length} bản ghi`;
};

const render = () => {
  renderNavigation();
  view.innerHTML = renderPage(routeId(), state);
  view.focus({ preventScroll: true });
  updateTopbar();
};

const applyResponse = (result, key, fallback) => {
  if (result.status === "fulfilled") return result.value;
  state[key] = fallback;
  return fallback;
};

const refresh = async ({ quiet = false } = {}) => {
  const results = await Promise.allSettled([
    getDashboard(),
    getJobs(),
    getModels(),
    getStorage(),
    getSettings(),
    getApplications(),
  ]);
  const dashboard = applyResponse(results[0], "dashboard", {});
  state.health = dashboard.health || {};
  state.components = dashboard.components || [];
  state.applications = dashboard.applications || [];
  state.jobs = applyResponse(results[1], "jobsResponse", { jobs: [] }).jobs || [];
  state.models = applyResponse(results[2], "modelsResponse", { models: [] }).models || [];
  state.storage = applyResponse(results[3], "storage", {});
  state.settings = applyResponse(results[4], "settingsResponse", { settings: {} }).settings || {};
  const applications = applyResponse(results[5], "applicationsResponse", { applications: [] }).applications || [];
  if (applications.length) state.applications = applications;
  render();
  if (!quiet && results.some((result) => result.status === "rejected")) showToast("Một số dữ liệu chưa đọc được; trạng thái được giữ trung thực.");
};

const currentTheme = () => localStorage.getItem("local-ai-hub-theme") || "system";
const applyTheme = (theme) => {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = theme;
  localStorage.setItem("local-ai-hub-theme", theme);
};
const cycleTheme = () => {
  const next = { system: "dark", dark: "light", light: "system" }[currentTheme()];
  applyTheme(next);
  showToast(`Giao diện: ${next === "system" ? "theo hệ thống" : next === "dark" ? "tối" : "sáng"}`);
  render();
};

document.addEventListener("click", async (event) => {
  const route = event.target.closest("[data-route]");
  if (route) {
    window.location.hash = `#/${route.dataset.route}`;
    return;
  }
  if (event.target.closest("#theme-toggle") || event.target.closest("[data-cycle-theme]")) {
    cycleTheme();
    return;
  }
  const refreshButton = event.target.closest("[data-refresh-storage]");
  if (refreshButton) {
    refreshButton.disabled = true;
    await refresh({ quiet: true });
    refreshButton.disabled = false;
    showToast("Đã đọc lại storage và registry.");
    return;
  }
  const launchButton = event.target.closest("[data-launch]");
  if (launchButton) {
    launchButton.disabled = true;
    try {
      const result = await launchApplication(launchButton.dataset.launch);
      showToast(`${result.application || "Ứng dụng"}: đang khởi chạy (PID ${result.pid || "—"}).`);
      window.setTimeout(() => refresh({ quiet: true }), 1200);
    } catch (error) {
      showToast(`Không thể mở ứng dụng: ${error.message}`);
    } finally {
      launchButton.disabled = false;
    }
  }
});

window.addEventListener("hashchange", render);
applyTheme(currentTheme());
refresh();
window.setInterval(() => refresh({ quiet: true }), 12000);
