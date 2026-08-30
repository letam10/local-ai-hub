/* Post-V8 Model Manager V2 panel.
 *
 * Reads only a bounded, path-free server projection.  POST buttons create an
 * existing V8 plan at most; no browser action confirms, downloads, imports,
 * loads, runs, removes, or opens a model path.
 */

const MODEL_ID = /^[a-z][a-z0-9._-]{1,95}$/;
const MODEL_STATE = new Set(["DISCOVERED", "NOT_INSTALLED", "INSTALLED_UNVERIFIED", "INSTALLED_VERIFIED", "OPERATIONAL", "PARTIAL", "UNAVAILABLE", "BROKEN", "UPDATE_AVAILABLE"]);
const MODEL_CATEGORY = new Set(["Image", "Video", "Audio", "Vision", "LLM", "Utility"]);
const MODEL_ACTION = new Set(["PLAN_INSTALL", "IMPORT_EXISTING", "REGISTER_EXISTING", "VERIFY_CHECKSUM", "CHECK_DUPLICATES", "CHECK_MOVED", "REPAIR_REGISTRY", "SAFE_REMOVE", "UPDATE_METADATA", "REVIEW_LICENSE", "CHECK_COMPATIBILITY"]);
const SAFE_TEXT = /(?:[a-z]:[\\/]|\\\\|(?:https?|file|data):|bearer\s|\b(?:api[_-]?key|secret|password|token)\b|\.\.[\\/])/i;

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));

const safeText = (value, fallback) => {
  const candidate = typeof value === "string" ? value.trim().slice(0, 320) : "";
  return candidate && !SAFE_TEXT.test(candidate) ? candidate : fallback;
};

const safeNumber = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
const formatBytes = (value) => {
  const bytes = safeNumber(value);
  if (bytes === null) return "Chưa rõ";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / (1024 ** 2)).toFixed(1)} MiB`;
  return `${(bytes / (1024 ** 3)).toFixed(2)} GiB`;
};

const request = async (path, options = {}) => {
  const response = await fetch(path, { ...options, headers: { Accept: "application/json", ...(options.headers || {}) } });
  let payload = {};
  try { payload = await response.json(); }
  catch { payload = { status: "error", error: "Phản hồi Model Manager V2 không phải JSON." }; }
  if (!response.ok) throw new Error(payload.error || payload.code || "model_manager_v2_unavailable");
  return payload;
};

const safeModel = (value) => {
  if (!value || typeof value !== "object") return null;
  const modelId = String(value.model_id || "");
  const state = String(value.state || "");
  const category = String(value.category || "");
  if (!MODEL_ID.test(modelId) || !MODEL_STATE.has(state) || !MODEL_CATEGORY.has(category)) return null;
  const duplicate = value.duplicate_analysis && typeof value.duplicate_analysis === "object" ? value.duplicate_analysis : {};
  const moved = value.moved_analysis && typeof value.moved_analysis === "object" ? value.moved_analysis : {};
  const compatibility = value.runtime_compatibility && typeof value.runtime_compatibility === "object" ? value.runtime_compatibility : {};
  const actions = Array.isArray(value.available_actions) ? value.available_actions.filter((item) => MODEL_ACTION.has(item)).slice(0, 16) : [];
  return {
    model_id: modelId,
    display_name: safeText(value.display_name, modelId),
    category,
    state,
    installed: value.installed === true,
    verified: value.verified === true,
    format: /^[a-z0-9, ]{1,64}$/i.test(String(value.format || "")) ? String(value.format) : "unknown",
    precision: /^[A-Za-z0-9._-]{1,32}$/.test(String(value.precision || "")) ? String(value.precision) : "unknown",
    size_bytes: safeNumber(value.size_bytes),
    vram_estimate_mb: safeNumber(value.vram_estimate_mb),
    checksum_state: /^[a-z_]{1,32}$/i.test(String(value.checksum?.state || "")) ? String(value.checksum.state) : "not_declared",
    duplicate_state: /^[a-z_]{1,32}$/i.test(String(duplicate.state || "")) ? String(duplicate.state) : "not_scanned",
    moved_state: /^[a-z_]{1,32}$/i.test(String(moved.state || "")) ? String(moved.state) : "not_scanned",
    runtime_id: MODEL_ID.test(String(compatibility.runtime_id || "")) ? String(compatibility.runtime_id) : "—",
    runtime_status: /^[a-z_]{1,64}$/i.test(String(compatibility.status || "")) ? String(compatibility.status) : "unknown",
    reason: safeText(value.reason, "Chưa có lý do Model Manager V2 hợp lệ."),
    next_action: safeText(value.next_action, "Xem capability và plan do máy chủ sở hữu."),
    actions,
  };
};

const statePill = (state) => {
  const semantic = state === "OPERATIONAL" ? "available" : ["UNAVAILABLE", "BROKEN"].includes(state) ? "unavailable" : "partial";
  return `<span class="status-pill" data-status="${semantic}" data-technical-status="${escapeHtml(state)}"><code>${escapeHtml(state)}</code></span>`;
};

const actionLabel = (action) => ({
  PLAN_INSTALL: "Lập kế hoạch cài",
  VERIFY_CHECKSUM: "Lập kế hoạch xác minh",
  REGISTER_EXISTING: "Lập kế hoạch dùng bản có sẵn",
  SAFE_REMOVE: "Lập kế hoạch gỡ an toàn",
}[action] || action);

const plannedActions = new Set(["PLAN_INSTALL", "VERIFY_CHECKSUM", "REGISTER_EXISTING", "SAFE_REMOVE"]);

const render = (target, payload) => {
  const records = payload && payload.schema_version === "model-manager.v2" && Array.isArray(payload.records)
    ? payload.records.map(safeModel).filter(Boolean)
    : [];
  if (!records.length) {
    target.innerHTML = `<div class="empty-state"><strong>Chưa có Model Manager V2 record hợp lệ</strong><span>Giữ trạng thái chưa khả dụng cho tới khi server publish catalog metadata hợp lệ.</span></div>`;
    return;
  }
  target.innerHTML = `<div class="component-manager-list">${records.slice(0, 32).map((item) => {
    const buttons = item.actions.filter((action) => plannedActions.has(action)).map((action) => `<button class="button button--compact" type="button" data-model-v2-plan="${escapeHtml(item.model_id)}" data-model-v2-action="${escapeHtml(action)}">${escapeHtml(actionLabel(action))}</button>`).join("");
    return `<article class="component-plan-preview" data-model-v2-record="${escapeHtml(item.model_id)}"><div class="split"><div><strong>${escapeHtml(item.display_name)}</strong><p class="small"><code>${escapeHtml(item.model_id)}</code> · ${escapeHtml(item.category)} · ${escapeHtml(item.format)} · ${escapeHtml(item.precision)}</p></div>${statePill(item.state)}</div><div class="component-manager-grid"><div><span>Đã cài</span><strong>${item.installed ? "Có" : "Chưa"}</strong></div><div><span>Đã xác minh</span><strong>${item.verified ? "Có" : "Chưa"}</strong></div><div><span>Dung lượng</span><strong>${escapeHtml(formatBytes(item.size_bytes))}</strong></div><div><span>VRAM ước tính</span><strong>${item.vram_estimate_mb === null ? "Chưa rõ" : `${escapeHtml(String(item.vram_estimate_mb))} MiB`}</strong></div></div><p class="small">Checksum: <code>${escapeHtml(item.checksum_state)}</code> · duplicate: <code>${escapeHtml(item.duplicate_state)}</code> · moved: <code>${escapeHtml(item.moved_state)}</code> · runtime: <code>${escapeHtml(item.runtime_id)}</code> (<code>${escapeHtml(item.runtime_status)}</code>)</p><p>${escapeHtml(item.reason)}</p><div class="form-actions">${buttons}</div><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(item.next_action)}</span></div></article>`;
  }).join("")}</div>`;
};

let mounted = false;
let refreshGeneration = 0;
let refreshing = false;

const setStatus = (text, state = "") => {
  const target = document.querySelector("[data-model-manager-v2-status]");
  if (!target) return;
  target.textContent = text;
  target.dataset.state = state;
};

const refresh = async () => {
  const panel = document.querySelector("[data-model-manager-v2]");
  if (!panel || refreshing) return;
  refreshing = true;
  const current = ++refreshGeneration;
  setStatus("Đang đọc Model Manager V2 server-owned…", "loading");
  try {
    const payload = await request("/api/model-manager/v2");
    if (current !== refreshGeneration || !document.querySelector("[data-model-manager-v2]")) return;
    const target = document.querySelector("[data-model-manager-v2-list]");
    if (target) render(target, payload);
    setStatus("Model Manager V2 đã đồng bộ metadata giới hạn; không tải hoặc nạp model.", "ready");
  } catch {
    const target = document.querySelector("[data-model-manager-v2-list]");
    if (target) target.innerHTML = `<div class="callout callout--warning">Không đọc được Model Manager V2 ở lần làm mới này.</div>`;
    setStatus("Model Manager V2 chưa khả dụng; không suy diễn model đã chạy.", "partial");
  } finally {
    refreshing = false;
  }
};

const createPlan = async (button) => {
  const modelId = String(button.dataset.modelV2Plan || "");
  const action = String(button.dataset.modelV2Action || "");
  if (!MODEL_ID.test(modelId) || !plannedActions.has(action)) {
    setStatus("Model ID hoặc action không hợp lệ; không gửi request.", "error");
    return;
  }
  button.disabled = true;
  try {
    const payload = await request(`/api/model-manager/v2/${encodeURIComponent(modelId)}/plans`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    const operationId = /^[a-z0-9_]+$/i.test(String(payload?.plan?.operation_id || "")) ? ` · ${payload.plan.operation_id}` : "";
    setStatus(`Đã tạo plan V8, chưa thực thi${operationId}. Xem Components để xác nhận riêng nếu được phép.`, "ready");
  } catch {
    setStatus("Không thể tạo plan Model Manager V2; không có model nào được thay đổi.", "error");
  } finally {
    button.disabled = false;
  }
};

const onClick = (event) => {
  if (event.target.closest("[data-model-manager-v2-refresh]")) { refresh(); return; }
  const planButton = event.target.closest("[data-model-v2-plan]");
  if (planButton) createPlan(planButton);
};

const mount = () => {
  if (mounted) return;
  const view = document.querySelector("#module-view");
  if (!view) return;
  mounted = true;
  document.addEventListener("click", onClick);
  let panelWasPresent = false;
  const refreshWhenPanelEnters = () => {
    const panelPresent = Boolean(view.querySelector("[data-model-manager-v2]"));
    if (panelPresent && !panelWasPresent) refresh();
    panelWasPresent = panelPresent;
  };
  new MutationObserver(refreshWhenPanelEnters).observe(view, { childList: true });
  window.addEventListener("hashchange", () => { panelWasPresent = Boolean(view.querySelector("[data-model-manager-v2]")); if (panelWasPresent) refresh(); });
  refreshWhenPanelEnters();
};

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
else mount();
