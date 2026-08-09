import {
  cancelJob,
  closeOwnedBackends,
  formatGb,
  formatStatus,
  getBootstrap,
  getComfyAdvanced,
  getComfyBridgeWorkflow,
  getComfyBridgeWorkflows,
  getHealth,
  getJobs,
  getLifecycle,
  getModels,
  getStorage,
  launchApplication,
  openArtifact,
  resumeJob,
  saveComfyBridgeWorkflow,
  scanStorage,
  startComfyAdvanced,
  submitJob,
  uploadFile,
} from "./api.js";
import { disposeNodeStudios, mountNodeStudios } from "./node_studio.js";
import { NAVIGATION, renderPage } from "./pages.js";

const state = {
  health: {}, components: [], tools: [], applications: [], jobs: [], models: [], storage: {}, settings: {}, lifecycle: {}, comfyAdvanced: {}, comfyWorkflows: [], workspaceTabs: {}, jobFilter: "all", apiStatus: "loading", apiError: "",
};
const view = document.querySelector("#module-view");
const nav = document.querySelector("#sidebar-nav");
const topStatus = document.querySelector("#top-status");
const diskMetric = document.querySelector("#disk-metric");
const gpuMetric = document.querySelector("#gpu-metric");
const jobSummary = document.querySelector("#job-summary");
const toastRegion = document.querySelector("#toast-region");
const sidebar = document.querySelector(".sidebar");
const sidebarToggle = document.querySelector("#sidebar-toggle");
const artifactPreviewLayer = document.querySelector("#artifact-preview-layer");
let routeLoad = null;

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

const closeArtifactPreview = () => artifactPreviewLayer?.replaceChildren();

const showArtifactPreview = (button) => {
  if (!artifactPreviewLayer) return;
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
    const audio = document.createElement("audio"); audio.src = url; audio.controls = true; body.append(audio);
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

const renderNavigation = () => {
  const active = routeId();
  nav.innerHTML = NAVIGATION.map((group) => `
    <div class="nav-group">${group.group}</div>
    ${group.items.map(([id, label, icon]) => `<button class="nav-item ${id === active ? "is-active" : ""}" type="button" data-route="${id}" aria-current="${id === active ? "page" : "false"}"><span class="nav-icon">${icon}</span><span>${label}</span></button>`).join("")}
  `).join("");
};

const renderApiState = () => {
  if (state.apiStatus === "error") return `<section class="global-state global-state--error" role="alert"><strong>API Hub chưa sẵn sàng</strong><span>${state.apiError || "Kiểm tra listener loopback rồi thử lại."}</span><button class="button button--compact" type="button" data-refresh-api>Thử lại</button></section>`;
  if (state.apiStatus === "loading") return `<section class="global-state global-state--loading" role="status"><strong>Đang tải workspace</strong><span>Đang lấy health, capability và queue snapshot…</span></section>`;
  return "";
};

const updateTopbar = () => {
  const health = state.health || {};
  const disk = health.disk || {};
  const gpu = health.gpu || {};
  topStatus.textContent = health.status ? `${formatStatus(health.status)} · Workflow trực tiếp` : "Đang khởi động API…";
  diskMetric.textContent = disk.free_bytes ? `Ổ đĩa ${formatGb(disk.free_bytes)} trống` : "Ổ đĩa —";
  gpuMetric.textContent = gpu.name ? `GPU ${gpu.name}` : "GPU chưa phát hiện";
  const active = state.jobs.filter((job) => ["starting", "running", "cancelling"].includes(job.status)).length;
  jobSummary.textContent = `Jobs: ${active} đang chạy · ${state.jobs.length} bản ghi`;
};

const render = () => {
  disposeNodeStudios();
  renderNavigation();
  view.innerHTML = `${renderApiState()}${renderPage(routeId(), state)}`;
  view.focus({ preventScroll: true });
  updateTopbar();
  if (view.querySelector("[data-node-studio]")) mountNodeStudios({ showToast });
};

const applyBootstrap = (payload) => {
  state.apiStatus = "ready";
  state.apiError = "";
  state.health = payload.health || {};
  state.components = payload.components || [];
  state.applications = payload.applications || [];
  state.jobs = payload.jobs || [];
  state.tools = payload.tools || [];
  state.settings = payload.settings || {};
  state.lifecycle = payload.lifecycle || {};
};

const refreshFast = async ({ quiet = false } = {}) => {
  const [health, jobs] = await Promise.allSettled([getHealth(), getJobs()]);
  let failed = false;
  if (health.status === "fulfilled") state.health = health.value || {};
  else failed = true;
  if (jobs.status === "fulfilled") state.jobs = jobs.value.jobs || [];
  else failed = true;
  if (["dashboard", "jobs"].includes(routeId())) render(); else updateTopbar();
  if (failed && state.apiStatus === "ready") state.apiStatus = "degraded";
  if (failed && !quiet) showToast("API đang khởi động hoặc một snapshot nhanh chưa sẵn sàng.", "warning");
};

const loadRouteData = async ({ scan = false } = {}) => {
  const route = routeId();
  if (route === "models") {
    if (routeLoad) return routeLoad;
    routeLoad = Promise.allSettled([getModels(), scan ? scanStorage() : getStorage()]).then((results) => {
      if (results[0].status === "fulfilled") state.models = results[0].value.models || [];
      if (results[1].status === "fulfilled") state.storage = results[1].value || {};
      render();
    }).catch(() => {}).finally(() => { routeLoad = null; });
    return routeLoad;
  }
  if (route === "image") {
    const results = await Promise.allSettled([getLifecycle(), getComfyAdvanced(), getComfyBridgeWorkflows()]);
    if (results[0].status === "fulfilled") state.lifecycle = results[0].value;
    if (results[1].status === "fulfilled") state.comfyAdvanced = results[1].value;
    if (results[2].status === "fulfilled") state.comfyWorkflows = results[2].value.workflows || [];
    render();
  }
  return undefined;
};

const initialize = async () => {
  try {
    applyBootstrap(await getBootstrap());
  } catch (error) {
    state.apiStatus = "error";
    state.apiError = error.message;
    showToast(`API chưa sẵn sàng: ${error.message}`, "warning");
  }
  render();
  await loadRouteData();
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

document.addEventListener("change", (event) => {
  const input = event.target.closest("input[type=file][data-asset-key]");
  if (input) renderFilePreview(input);
});

document.addEventListener("submit", async (event) => {
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
    const open = !sidebar?.classList.contains("is-open");
    sidebar?.classList.toggle("is-open", open);
    sidebarToggle?.setAttribute("aria-expanded", String(open));
    return;
  }
  if (event.target.closest("[data-close-artifact-preview]")) { closeArtifactPreview(); return; }
  const preview = event.target.closest("[data-preview-artifact]");
  if (preview) { showArtifactPreview(preview); return; }
  if (event.target.closest("[data-refresh-api]")) { await initialize(); return; }
  const route = event.target.closest("[data-route]");
  if (route) { sidebar?.classList.remove("is-open"); sidebarToggle?.setAttribute("aria-expanded", "false"); window.location.hash = `#/${route.dataset.route}`; return; }
  const tab = event.target.closest("[data-workspace-tab]");
  if (tab) { const [module, name] = tab.dataset.workspaceTab.split(":"); state.workspaceTabs[module] = name; render(); return; }
  const jobFilter = event.target.closest("[data-job-filter]");
  if (jobFilter) { state.jobFilter = jobFilter.dataset.jobFilter || "all"; render(); return; }
  if (event.target.closest("#theme-toggle") || event.target.closest("[data-cycle-theme]")) { cycleTheme(); return; }
  const refreshButton = event.target.closest("[data-refresh-storage]");
  if (refreshButton) { refreshButton.disabled = true; await loadRouteData({ scan: true }); refreshButton.disabled = false; showToast("Đã quét lại storage theo yêu cầu."); return; }
  const launchButton = event.target.closest("[data-launch]");
  if (launchButton) {
    launchButton.disabled = true;
    try { const result = await launchApplication(launchButton.dataset.launch); showToast(`${result.application || "AIRI"}: đang khởi chạy.`); }
    catch (error) { showToast(`Không thể mở AIRI: ${error.message}`, "error"); }
    finally { launchButton.disabled = false; }
    return;
  }
  const cancel = event.target.closest("[data-cancel-job]");
  if (cancel) { cancel.disabled = true; try { showToast((await cancelJob(cancel.dataset.cancelJob)).message || "Đang hủy job."); await refreshFast({ quiet: true }); } catch (error) { showToast(error.message, "error"); } return; }
  const resume = event.target.closest("[data-resume-job]");
  if (resume) { resume.disabled = true; try { showToast(`Đã tạo ${((await resumeJob(resume.dataset.resumeJob)).job || {}).id || "job tiếp tục"}.`); await refreshFast({ quiet: true }); } catch (error) { showToast(error.message, "error"); } return; }
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
      showToast("ComfyUI đang chạy trong backend ẩn; editor sẽ hiện trong cửa sổ Hub.");
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

window.addEventListener("hashchange", async () => { render(); await loadRouteData(); });
applyTheme(currentTheme());
initialize();
window.setInterval(() => refreshFast({ quiet: true }), 2500);
