/* Durable Job Engine V2 Jobs-page projection.
 *
 * The panel intentionally reads only opaque, server-owned job metadata. It
 * creates no worker, process, provider, model, or scheduler reservation. A
 * reconstruct-only retry is named accurately and never rendered as running.
 * Refresh happens on Jobs-page entry or an explicit click; there is no poll.
 */

const JOB_ID = /^jobv2_[a-f0-9]{32}$/;
const WORKFLOW_ID = /^[a-z][a-z0-9._-]{1,95}$/;
const STATES = new Set(["QUEUED", "WAITING_RESOURCE", "PREPARING", "NEEDS_READMISSION", "RUNNING", "PAUSED", "CANCELLING", "CANCELLED", "SUCCEEDED", "FAILED"]);
const ARTIFACT_ID = /^artifact_[a-f0-9]{32}$/;
const UNSAFE_TEXT = /(?:[a-z]:[\\/]|\\\\|(?:https?|file|data):|bearer\s|\b(?:api[_-]?key|secret|password|token)\b|\.\.[\\/])/i;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));

const safeText = (value, fallback) => {
  const candidate = typeof value === "string" ? value.trim().slice(0, 320) : "";
  return candidate && !UNSAFE_TEXT.test(candidate) ? candidate : fallback;
};

const request = async (path, options = {}) => {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); }
  catch { payload = { status: "unavailable", error: "durable_job_v2_response_invalid" }; }
  if (!response.ok) throw new Error(String(payload.error || payload.code || "durable_job_v2_unavailable"));
  return payload;
};

const safeJob = (value) => {
  if (!value || typeof value !== "object") return null;
  const jobId = String(value.job_id || "");
  const workflowId = String(value.workflow_id || "");
  const state = String(value.state || "");
  const retryMode = String(value.retry_mode || "");
  const artifacts = Array.isArray(value.artifact_refs) ? value.artifact_refs.filter((item) => typeof item === "string" && ARTIFACT_ID.test(item)).slice(0, 64) : [];
  if (!JOB_ID.test(jobId) || !WORKFLOW_ID.test(workflowId) || !STATES.has(state) || !["new", "retry_execution", "reconstruct_only"].includes(retryMode)) return null;
  const reconstructOnlyPending = retryMode === "reconstruct_only" && value.actual_execution === false && state === "QUEUED";
  return {
    job_id: jobId,
    workflow_id: workflowId,
    state,
    lifecycle: safeText(value.lifecycle, reconstructOnlyPending ? "Đã tạo · chưa thực thi" : state),
    retry_of: JOB_ID.test(String(value.retry_of || "")) ? String(value.retry_of) : null,
    retry_mode: retryMode,
    dispatchable: value.dispatchable === true,
    actual_execution: value.actual_execution === true,
    active: value.active === true,
    archived: value.archived === true,
    artifacts,
    progress: Number.isInteger(value.progress) && value.progress >= 0 && value.progress <= 100 ? value.progress : 0,
    reason: safeText(value.reason, "Bản ghi durable chưa có lý do hợp lệ."),
    next_action: safeText(value.next_action, "Xem lại server-owned job record trước khi tiếp tục."),
    can_pause: value.can_pause === true,
    can_resume: value.can_resume === true,
    reconstruct_only_pending: reconstructOnlyPending,
  };
};

const semanticState = (state) => (["RUNNING", "SUCCEEDED"].includes(state) ? "available" : ["FAILED", "CANCELLED"].includes(state) ? "unavailable" : "partial");
const statePill = (state) => `<span class="status-pill" data-status="${semanticState(state)}" data-technical-status="${escapeHtml(state)}"><code>${escapeHtml(state)}</code></span>`;
const isTerminal = (job) => ["CANCELLED", "SUCCEEDED", "FAILED"].includes(job.state);

let mounted = false;
let refreshGeneration = 0;
let refreshing = false;
let selected = new Set();
let deleteConfirmationPending = false;
let currentQuery = "";

const getPanel = () => document.querySelector("[data-durable-job-v2-panel]");
const getStatus = () => document.querySelector("[data-durable-job-v2-status]");

const setStatus = (message, state = "") => {
  const target = getStatus();
  if (!target) return;
  target.textContent = message;
  target.dataset.state = state;
};

const renderRows = (records, group) => {
  const rows = records.filter(group).map((job) => {
    const retry = isTerminal(job)
      ? `<button class="button button--compact" type="button" data-durable-v2-retry="${escapeHtml(job.job_id)}">Tạo lại tác vụ</button>`
      : "";
    const cancel = job.active
      ? `<button class="button button--compact button--danger" type="button" data-durable-v2-cancel="${escapeHtml(job.job_id)}">Hủy tác vụ</button>`
      : "";
    const archive = isTerminal(job) && !job.archived
      ? `<button class="button button--compact" type="button" data-durable-v2-archive="${escapeHtml(job.job_id)}">Lưu trữ metadata</button>`
      : "";
    const select = isTerminal(job)
      ? `<label class="checkbox-label"><input type="checkbox" data-durable-v2-select="${escapeHtml(job.job_id)}" ${selected.has(job.job_id) ? "checked" : ""} /> Chọn xóa metadata</label>`
      : "";
    const retryLineage = job.retry_of ? `<p class="small">Tạo từ: <code>${escapeHtml(job.retry_of)}</code></p>` : "";
    const execution = job.reconstruct_only_pending
      ? "Đã tạo · chưa thực thi"
      : job.state === "NEEDS_READMISSION"
        ? "Cần tái tiếp nhận · chưa thực thi"
      : job.actual_execution ? "Worker đã xác nhận thực thi" : job.dispatchable ? "Đã lập lịch · chờ worker" : "Chưa thực thi";
    const ownerControls = job.can_pause || job.can_resume
      ? `<p class="small">Pause/resume được owner hỗ trợ, nhưng không được browser gọi trực tiếp trong Phase 5.</p>`
      : "";
    return `<article class="component-plan-preview" data-durable-v2-record="${escapeHtml(job.job_id)}"><div class="split"><div><strong><code>${escapeHtml(job.workflow_id)}</code></strong><p class="small"><code>${escapeHtml(job.job_id)}</code> · ${escapeHtml(execution)}</p></div>${statePill(job.state)}</div><div class="component-manager-grid"><div><span>Lifecycle</span><strong>${escapeHtml(job.lifecycle)}</strong></div><div><span>Tiến độ</span><strong>${escapeHtml(String(job.progress))}%</strong></div><div><span>Artifact</span><strong>${escapeHtml(String(job.artifacts.length))}</strong></div><div><span>Retry mode</span><strong><code>${escapeHtml(job.retry_mode)}</code></strong></div></div><div class="progress-track" role="progressbar" aria-label="Tiến độ ${escapeHtml(job.job_id)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${escapeHtml(String(job.progress))}"><div class="progress-bar" style="width:${escapeHtml(String(job.progress))}%"></div></div>${retryLineage}<p>${escapeHtml(job.reason)}</p><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(job.next_action)}</span></div>${ownerControls}<div class="form-actions">${retry}${cancel}${archive}${select}</div></article>`;
  }).join("");
  return rows || `<div class="empty-state compact">Không có bản ghi trong nhóm này.</div>`;
};

const render = (panel, payload) => {
  const records = payload && payload.schema_version === "durable-job-engine.v2" && Array.isArray(payload.records)
    ? payload.records.map(safeJob).filter(Boolean).slice(0, 512)
    : [];
  selected = new Set([...selected].filter((jobId) => records.some((job) => job.job_id === jobId && isTerminal(job))));
  const counts = payload && typeof payload.counts === "object" ? payload.counts : {};
  const active = Number.isInteger(counts.active) ? counts.active : records.filter((job) => job.active).length;
  const reconstruct = Number.isInteger(counts.reconstruct_only_pending) ? counts.reconstruct_only_pending : records.filter((job) => job.reconstruct_only_pending).length;
  const terminal = records.filter(isTerminal).length;
  const needsReadmission = records.filter((job) => job.state === "NEEDS_READMISSION");
  panel.innerHTML = `<div class="card-title-row"><div><span class="eyebrow">DURABLE JOB ENGINE V2</span><h2>Hàng đợi bền vững</h2><p class="small">Metadata SQLite server-owned; chỉ ID opaque, không có path, worker command hay artifact bytes.</p></div><button class="button button--compact" type="button" data-durable-v2-refresh>Làm mới</button></div><div class="inventory-health__metrics"><div><span>Đang active</span><strong>${escapeHtml(String(active))}</strong></div><div><span>Đã tạo, chưa thực thi</span><strong>${escapeHtml(String(reconstruct))}</strong></div><div><span>Cần tái tiếp nhận</span><strong>${escapeHtml(String(needsReadmission.length))}</strong></div><div><span>Đã kết thúc</span><strong>${escapeHtml(String(terminal))}</strong></div></div><div class="form-actions"><label><span class="sr-only">Tìm durable job</span><input type="search" data-durable-v2-search value="${escapeHtml(currentQuery)}" placeholder="Tìm workflow hoặc mã job" maxlength="80" /></label><button class="button button--compact button--danger" type="button" data-durable-v2-delete ${selected.size ? "" : "disabled"}>${deleteConfirmationPending ? `Xác nhận xóa ${selected.size} metadata` : `Xóa ${selected.size} metadata đã chọn`}</button></div><p class="small">Xóa chỉ metadata history đã kết thúc; artifact không bị xóa. “Tạo lại tác vụ” chỉ dựng record mới và luôn hiển thị chưa thực thi.</p><details open><summary>Đang chờ / đang thực thi (${records.filter((job) => job.active).length})</summary><div class="component-manager-list">${renderRows(records, (job) => job.active)}</div></details><details open><summary>Cần tái tiếp nhận · chưa thực thi (${needsReadmission.length})</summary><div class="component-manager-list">${renderRows(records, (job) => job.state === "NEEDS_READMISSION")}</div></details><details open><summary>Đã tạo · chưa thực thi (${records.filter((job) => job.reconstruct_only_pending).length})</summary><div class="component-manager-list">${renderRows(records, (job) => job.reconstruct_only_pending)}</div></details><details><summary>Đã kết thúc (${terminal})</summary><div class="component-manager-list">${renderRows(records, isTerminal)}</div></details><p class="small" data-durable-job-v2-status>${escapeHtml(safeText(payload?.reason, "Durable Job Engine V2 chưa có snapshot hợp lệ."))}</p>`;
};

const refresh = async (query = "") => {
  const panel = getPanel();
  if (!panel || refreshing) return;
  refreshing = true;
  currentQuery = query;
  const generation = ++refreshGeneration;
  setStatus("Đang đọc Durable Job Engine V2…", "loading");
  const suffix = query ? `?query=${encodeURIComponent(query)}` : "";
  try {
    const payload = await request(`/api/durable-job-engine/v2${suffix}`);
    if (generation !== refreshGeneration || !getPanel()) return;
    render(panel, payload);
    setStatus("Đã đồng bộ metadata durable; không suy diễn worker đang chạy khi chưa có lease/reservation chính xác.", "ready");
  } catch {
    if (generation === refreshGeneration && panel.isConnected) panel.innerHTML = `<div class="callout callout--warning">Durable Job Engine V2 chưa khả dụng; không suy diễn trạng thái worker.</div>`;
    setStatus("Không đọc được Durable Job Engine V2 ở lần này.", "partial");
  } finally {
    refreshing = false;
  }
};

const queryFromPanel = () => {
  const input = getPanel()?.querySelector("[data-durable-v2-search]");
  const value = typeof input?.value === "string" ? input.value.trim().slice(0, 80) : "";
  return UNSAFE_TEXT.test(value) ? "" : value;
};

const mutate = async (path, body, success) => {
  try {
    await request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    deleteConfirmationPending = false;
    setStatus(success, "ready");
    await refresh(queryFromPanel());
  } catch {
    deleteConfirmationPending = false;
    setStatus("Thao tác durable không hoàn tất; không suy diễn thay đổi artifact/worker.", "error");
  }
};

const ensurePanel = () => {
  const page = document.querySelector("#module-view #jobs-page");
  if (!page) return null;
  const existing = page.querySelector("[data-durable-job-v2-panel]");
  if (existing) return existing;
  const panel = document.createElement("section");
  panel.className = "card";
  panel.dataset.durableJobV2Panel = "";
  panel.setAttribute("aria-live", "polite");
  panel.innerHTML = `<p class="small">Đang chuẩn bị Durable Job Engine V2…</p>`;
  const controls = page.querySelector(".job-history-controls");
  if (controls) controls.before(panel); else page.append(panel);
  return panel;
};

const onClick = (event) => {
  if (event.target.closest("[data-durable-v2-refresh]")) { refresh(queryFromPanel()); return; }
  const retry = event.target.closest("[data-durable-v2-retry]");
  if (retry) { mutate(`/api/durable-job-engine/v2/${encodeURIComponent(retry.dataset.durableV2Retry)}/retry`, { mode: "RECONSTRUCT_ONLY" }, "Đã tạo durable record mới; chưa thực thi."); return; }
  const cancel = event.target.closest("[data-durable-v2-cancel]");
  if (cancel) { mutate(`/api/durable-job-engine/v2/${encodeURIComponent(cancel.dataset.durableV2Cancel)}/cancel`, {}, "Đã gửi yêu cầu hủy theo server-owned scheduler."); return; }
  const archive = event.target.closest("[data-durable-v2-archive]");
  if (archive) { mutate(`/api/durable-job-engine/v2/${encodeURIComponent(archive.dataset.durableV2Archive)}/archive`, {}, "Đã lưu trữ metadata; artifact vẫn được giữ nguyên."); return; }
  const deletion = event.target.closest("[data-durable-v2-delete]");
  if (deletion && selected.size) {
    if (!deleteConfirmationPending) {
      deleteConfirmationPending = true;
      deletion.textContent = `Xác nhận xóa ${selected.size} metadata`;
      setStatus("Nhấn lại để xác nhận xóa metadata history đã chọn. Artifact sẽ không bị xóa.", "confirm");
      return;
    }
    mutate("/api/durable-job-engine/v2/history/delete", { job_ids: [...selected], confirmed: true }, "Đã xóa metadata history; artifact vẫn được giữ nguyên.");
  }
};

const onChange = (event) => {
  const checkbox = event.target.closest("[data-durable-v2-select]");
  if (checkbox) {
    const jobId = String(checkbox.dataset.durableV2Select || "");
    if (JOB_ID.test(jobId)) {
      if (checkbox.checked) selected.add(jobId); else selected.delete(jobId);
      deleteConfirmationPending = false;
      const button = getPanel()?.querySelector("[data-durable-v2-delete]");
      if (button) { button.disabled = selected.size === 0; button.textContent = `Xóa ${selected.size} metadata đã chọn`; }
    }
    return;
  }
  if (event.target.closest("[data-durable-v2-search]")) refresh(queryFromPanel());
};

const mount = () => {
  if (mounted) return;
  const view = document.querySelector("#module-view");
  if (!view) return;
  mounted = true;
  document.addEventListener("click", onClick);
  document.addEventListener("change", onChange);
  let jobsWasPresent = false;
  const entered = () => {
    const present = Boolean(view.querySelector("#jobs-page"));
    if (present && !jobsWasPresent && ensurePanel()) refresh();
    jobsWasPresent = present;
  };
  new MutationObserver(entered).observe(view, { childList: true });
  window.addEventListener("hashchange", entered);
  entered();
};

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
else mount();
