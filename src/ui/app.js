import {
  cancelJob,
  closeOwnedBackends,
  formatGb,
  formatStatus,
  getApplications,
  getDashboard,
  getJobs,
  getLifecycle,
  getModels,
  getSettings,
  getStorage,
  getTools,
  launchApplication,
  openArtifact,
  resumeJob,
  submitJob,
  uploadFile,
} from "./api.js";
import { NAVIGATION, renderPage } from "./pages.js";

const state = { health: {}, components: [], tools: [], applications: [], jobs: [], models: [], storage: {}, settings: {}, lifecycle: {} };
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

const showToast = (message, kind = "") => {
  const toast = document.createElement("div");
  toast.className = `toast ${kind ? `toast--${kind}` : ""}`;
  toast.textContent = message;
  toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 5200);
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
  topStatus.textContent = health.status ? `${formatStatus(health.status)} · Workflow trực tiếp` : "API chưa sẵn sàng";
  diskMetric.textContent = disk.free_bytes ? `Ổ đĩa ${formatGb(disk.free_bytes)} trống` : "Ổ đĩa —";
  gpuMetric.textContent = gpu.name ? `GPU ${gpu.name}` : "GPU chưa phát hiện";
  const active = state.jobs.filter((job) => ["starting", "running", "cancelling"].includes(job.status)).length;
  jobSummary.textContent = `Jobs: ${active} đang chạy · ${state.jobs.length} bản ghi`;
};

const render = () => {
  renderNavigation();
  view.innerHTML = renderPage(routeId(), state);
  view.focus({ preventScroll: true });
  updateTopbar();
};

const valueOr = (result, fallback) => result.status === "fulfilled" ? result.value : fallback;

const refresh = async ({ quiet = false, renderView = true } = {}) => {
  const results = await Promise.allSettled([getDashboard(), getJobs(), getModels(), getStorage(), getSettings(), getApplications(), getLifecycle(), getTools()]);
  const dashboard = valueOr(results[0], {});
  state.health = dashboard.health || {};
  state.components = dashboard.components || [];
  state.applications = dashboard.applications || [];
  state.jobs = valueOr(results[1], { jobs: [] }).jobs || [];
  state.models = valueOr(results[2], { models: [] }).models || [];
  state.storage = valueOr(results[3], {});
  state.settings = valueOr(results[4], { settings: {} }).settings || {};
  const applications = valueOr(results[5], { applications: [] }).applications || [];
  if (applications.length) state.applications = applications;
  state.lifecycle = valueOr(results[6], {});
  state.tools = valueOr(results[7], { tools: [] }).tools || [];
  if (renderView) render(); else updateTopbar();
  if (!quiet && results.some((result) => result.status === "rejected")) showToast("Một số dữ liệu chưa đọc được; trạng thái vẫn được giữ trung thực.", "warning");
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

const toPayload = async (form) => {
  const payload = {};
  for (const element of form.elements) {
    if (!element.name || element.disabled) continue;
    if (element.type === "file") continue;
    if (element.type === "checkbox") payload[element.name] = element.checked;
    else payload[element.name] = element.value;
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
  const field = form.dataset.toolByField;
  if (!field || !form.dataset.toolMap) return tool;
  try {
    const mapping = JSON.parse(form.dataset.toolMap);
    tool = mapping[payload[field]] || tool;
  } catch { /* static fallback stays allowlisted */ }
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
  preview.replaceChildren();
  delete preview.dataset.objectUrl;
  const selected = [...(input.files || [])];
  if (!selected.length) return;
  const note = document.createElement("small");
  note.textContent = selected.length > 1 ? `${selected.length} tệp đã chọn; preview tệp đầu.` : selected[0].name;
  preview.append(note);
  const file = selected[0];
  const source = URL.createObjectURL(file);
  preview.dataset.objectUrl = source;
  if (file.type.startsWith("image/")) {
    const image = document.createElement("img");
    image.src = source;
    image.alt = `Preview ${file.name}`;
    const sam2Form = input.closest('form[data-tool="segment_from_points"]');
    if (sam2Form) {
      image.classList.add("sam2-selection-preview");
      let start = null;
      image.addEventListener("pointerdown", (event) => { start = pointFromEvent(event, image); image.setPointerCapture?.(event.pointerId); });
      image.addEventListener("pointerup", (event) => {
        if (!start) return;
        const end = pointFromEvent(event, image);
        const points = sam2Form.querySelector('[name="points_text"]');
        const box = sam2Form.querySelector('[name="box_text"]');
        if (Math.abs(end.x - start.x) < 8 && Math.abs(end.y - start.y) < 8 && points) {
          points.value = [points.value.trim(), `${end.x},${end.y},1`].filter(Boolean).join("; ");
          showToast(`Đã thêm điểm SAM2: ${end.x}, ${end.y}`);
        } else if (box) {
          box.value = `${Math.min(start.x, end.x)},${Math.min(start.y, end.y)},${Math.max(start.x, end.x)},${Math.max(start.y, end.y)}`;
          showToast("Đã chọn box SAM2 trên preview.");
        }
        start = null;
      });
    }
    preview.append(image);
  } else if (file.type.startsWith("video/") || file.type.startsWith("audio/")) {
    const media = document.createElement(file.type.startsWith("video/") ? "video" : "audio");
    media.src = source;
    media.controls = true;
    media.preload = "metadata";
    preview.append(media);
  }
};

document.addEventListener("change", (event) => {
  const input = event.target.closest("input[type=file][data-asset-key]");
  if (input) renderFilePreview(input);
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-job-form]");
  if (!form) return;
  event.preventDefault();
  const submit = form.querySelector("button[type=submit]");
  if (submit) submit.disabled = true;
  inlineResult(form, "Đang tải input và tạo job…");
  try {
    const payload = await toPayload(form);
    const tool = toolForForm(form, payload);
    const result = await submitJob(tool, payload);
    inlineResult(form, `Đã tạo ${result.job?.id || "job"}. Theo dõi ở Jobs.`, "success");
    showToast(`Đã thêm ${tool} vào hàng đợi Hub.`);
    await refresh({ quiet: true, renderView: false });
  } catch (error) {
    inlineResult(form, error.message, "error");
    showToast(error.message, "error");
  } finally {
    if (submit) submit.disabled = false;
  }
});

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
      showToast(`${result.application || "AIRI"}: đang khởi chạy.`);
    } catch (error) {
      showToast(`Không thể mở AIRI: ${error.message}`, "error");
    } finally {
      launchButton.disabled = false;
    }
    return;
  }
  const cancel = event.target.closest("[data-cancel-job]");
  if (cancel) {
    cancel.disabled = true;
    try { showToast((await cancelJob(cancel.dataset.cancelJob)).message || "Đang hủy job."); await refresh({ quiet: true }); }
    catch (error) { showToast(error.message, "error"); }
    return;
  }
  const resume = event.target.closest("[data-resume-job]");
  if (resume) {
    resume.disabled = true;
    try { showToast(`Đã tạo ${((await resumeJob(resume.dataset.resumeJob)).job || {}).id || "job tiếp tục"}.`); await refresh({ quiet: true }); }
    catch (error) { showToast(error.message, "error"); }
    return;
  }
  const open = event.target.closest("[data-open-artifact]");
  if (open) {
    try { showToast((await openArtifact(open.dataset.openArtifact)).message || "Đã yêu cầu mở artifact."); }
    catch (error) { showToast(error.message, "error"); }
    return;
  }
  if (event.target.closest("[data-open-comfy]")) {
    window.open(`http://127.0.0.1:${state.settings.comfyui_port || 8188}`, "_blank", "noopener");
    return;
  }
  if (event.target.closest("[data-close-backends]")) {
    try { const result = await closeOwnedBackends(); showToast(result.stopped?.length ? "Đã dừng backend Hub-owned rảnh." : "Không có backend Hub-owned cần dừng."); await refresh({ quiet: true }); }
    catch (error) { showToast(error.message, "error"); }
  }
});

window.addEventListener("hashchange", render);
applyTheme(currentTheme());
refresh();
window.setInterval(() => refresh({ quiet: true, renderView: routeId() === "jobs" || routeId() === "dashboard" }), 12000);
