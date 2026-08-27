/* Read-only Resource Scheduler V2 dashboard card.
 * No polling, GPU probe, worker dispatch, or browser-controlled reservation is
 * available here. The panel refreshes only on dashboard entry or user click.
 */

const SAFE_ID = /^[a-z][a-z0-9._:-]{1,95}$/;
const SAFE_STATE = new Set(["QUEUED", "WAITING_RESOURCE", "PREPARING", "RUNNING", "PAUSED", "CANCELLING", "CANCELLED", "SUCCEEDED", "FAILED"]);
const SAFE_TEXT = /(?:[a-z]:[\\/]|\\\\|(?:https?|file|data):|bearer\s|\b(?:api[_-]?key|secret|password|token)\b|\.\.[\\/])/i;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));
const safeNumber = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
const safeText = (value, fallback) => {
  const candidate = typeof value === "string" ? value.trim().slice(0, 320) : "";
  return candidate && !SAFE_TEXT.test(candidate) ? candidate : fallback;
};

const request = async () => {
  const response = await fetch("/api/resource-scheduler/v2", { headers: { Accept: "application/json" } });
  let payload = {};
  try { payload = await response.json(); }
  catch { payload = { status: "unavailable" }; }
  if (!response.ok) throw new Error("resource_scheduler_unavailable");
  return payload;
};

const safeGpu = (value) => {
  if (!value || typeof value !== "object") return null;
  const gpuId = String(value.gpu_id || "");
  if (!SAFE_ID.test(gpuId)) return null;
  return {
    gpu_id: gpuId,
    vram_total_mb: safeNumber(value.vram_total_mb),
    vram_reserved_mb: safeNumber(value.vram_reserved_mb),
    vram_available_for_reservation_mb: safeNumber(value.vram_available_for_reservation_mb),
    vram_used_by_processes_mb: value.vram_used_by_processes_mb === null ? null : safeNumber(value.vram_used_by_processes_mb),
  };
};

const safeJob = (value) => {
  if (!value || typeof value !== "object") return null;
  const jobId = String(value.job_id || "");
  const workerId = String(value.worker_id || "");
  const state = String(value.state || "");
  if (!/^job(?:v5_[a-f0-9]{32}|_[0-9]{8}_[0-9]{6}_[a-f0-9]{8})$/.test(jobId) || !SAFE_ID.test(workerId) || !SAFE_STATE.has(state)) return null;
  return {
    job_id: jobId,
    worker_id: workerId,
    profile_id: SAFE_ID.test(String(value.profile_id || "")) ? String(value.profile_id) : "unknown",
    state,
    reason_code: /^[a-z0-9_]{1,64}$/.test(String(value.reason_code || "")) ? String(value.reason_code) : "unknown",
    next_action: safeText(value.next_action, "Xem scheduler snapshot do máy chủ sở hữu."),
  };
};

const statePill = (state) => {
  const semantic = ["RUNNING", "SUCCEEDED"].includes(state) ? "available" : ["FAILED", "CANCELLED"].includes(state) ? "unavailable" : "partial";
  return `<span class="status-pill" data-status="${semantic}" data-technical-status="${escapeHtml(state)}"><code>${escapeHtml(state)}</code></span>`;
};

const render = (card, payload) => {
  const inventory = payload && typeof payload.inventory === "object" ? payload.inventory : {};
  const gpus = Array.isArray(inventory.gpus) ? inventory.gpus.map(safeGpu).filter(Boolean) : [];
  const jobs = Array.isArray(payload?.jobs) ? payload.jobs.map(safeJob).filter(Boolean).slice(0, 12) : [];
  const queue = payload && typeof payload.queue === "object" ? payload.queue : {};
  const status = String(payload?.status || "unavailable");
  const totalReserved = gpus.reduce((sum, gpu) => sum + (gpu.vram_reserved_mb || 0), 0);
  const gpuRows = gpus.length ? gpus.map((gpu) => `<div class="row-item"><span><code>${escapeHtml(gpu.gpu_id)}</code><small>${gpu.vram_used_by_processes_mb === null ? "Tiến trình GPU: chưa probe" : `Tiến trình GPU: ${escapeHtml(String(gpu.vram_used_by_processes_mb))} MiB`}</small></span><strong>${escapeHtml(String(gpu.vram_reserved_mb ?? 0))}/${escapeHtml(String(gpu.vram_total_mb ?? "—"))} MiB reserved</strong></div>`).join("") : `<div class="empty-state compact">Chưa có inventory GPU server-owned.</div>`;
  const jobRows = jobs.length ? jobs.map((job) => `<div class="row-item" data-resource-scheduler-job="${escapeHtml(job.job_id)}"><span><code>${escapeHtml(job.profile_id)}</code><small>${escapeHtml(job.reason_code)} · ${escapeHtml(job.next_action)}</small></span>${statePill(job.state)}</div>`).join("") : `<div class="empty-state compact">Chưa có job nào được bind vào Resource Scheduler.</div>`;
  card.innerHTML = `<div class="card-title-row"><div><span class="eyebrow">RESOURCE SCHEDULER V2</span><h2>Hàng đợi & tài nguyên</h2><p class="small">Inventory server-owned; không probe GPU hay chạy workload từ dashboard.</p></div><button class="button button--compact" type="button" data-resource-scheduler-refresh>Làm mới scheduler</button></div><div class="inventory-health__metrics"><div><span>Trạng thái</span><strong>${escapeHtml(status)}</strong></div><div><span>VRAM đã reserve</span><strong>${escapeHtml(String(totalReserved))} MiB</strong></div><div><span>Queue chờ</span><strong>${escapeHtml(String(safeNumber(queue.waiting) ?? 0))}</strong></div><div><span>Heavy GPU tối đa</span><strong>${escapeHtml(String(safeNumber(payload?.policy?.max_heavy_gpu_jobs) ?? 1))}</strong></div></div><div class="workspace-state__action"><strong>Lần start tiếp theo</strong><span>${queue.estimated_next_start === null ? "Chưa dự đoán; phụ thuộc reservation/worker thực tế." : escapeHtml(String(queue.estimated_next_start))}</span></div><div class="row-list">${gpuRows}</div><details><summary>Hàng đợi scheduler</summary><div class="row-list">${jobRows}</div></details><p class="small" data-resource-scheduler-status>${escapeHtml(safeText(payload?.reason, "Scheduler chưa có snapshot hợp lệ."))}</p>`;
};

let refreshGeneration = 0;
let refreshing = false;

const refresh = async () => {
  const card = document.querySelector("[data-resource-scheduler-card]");
  if (!card || refreshing) return;
  refreshing = true;
  const current = ++refreshGeneration;
  try {
    const payload = await request();
    if (current !== refreshGeneration || !document.querySelector("[data-resource-scheduler-card]")) return;
    render(card, payload);
  } catch {
    if (current === refreshGeneration && card.isConnected) card.innerHTML = `<div class="callout callout--warning">Resource Scheduler V2 chưa khả dụng; không suy diễn GPU/job đang chạy.</div>`;
  } finally {
    refreshing = false;
  }
};

const ensureCard = () => {
  const dashboard = document.querySelector("#module-view .dashboard-page");
  if (!dashboard) return null;
  const existing = dashboard.querySelector("[data-resource-scheduler-card]");
  if (existing) return existing;
  const card = document.createElement("section");
  card.className = "card";
  card.dataset.resourceSchedulerCard = "";
  card.setAttribute("aria-live", "polite");
  card.innerHTML = `<p class="small">Đang đọc Resource Scheduler V2…</p>`;
  const recovery = dashboard.querySelector(".job-recovery-card");
  if (recovery) recovery.before(card); else dashboard.append(card);
  return card;
};

const refreshIfDashboardEntered = () => {
  const card = ensureCard();
  if (card) refresh();
};

const mount = () => {
  const view = document.querySelector("#module-view");
  if (!view) return;
  document.addEventListener("click", (event) => { if (event.target.closest("[data-resource-scheduler-refresh]")) refresh(); });
  let dashboardWasPresent = false;
  const onMutation = () => {
    const present = Boolean(view.querySelector(".dashboard-page"));
    if (present && !dashboardWasPresent) refreshIfDashboardEntered();
    dashboardWasPresent = present;
  };
  new MutationObserver(onMutation).observe(view, { childList: true });
  window.addEventListener("hashchange", () => { dashboardWasPresent = Boolean(view.querySelector(".dashboard-page")); if (dashboardWasPresent) refreshIfDashboardEntered(); });
  onMutation();
};

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
else mount();
