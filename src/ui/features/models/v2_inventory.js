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

const safeText = (value, fallback, maximum = 320) => {
  const candidate = typeof value === "string" ? value.trim().slice(0, maximum) : "";
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
    family: safeText(value.family, "unknown", 96),
    provider: safeText(value.provider, "unknown", 96),
    category,
    modules: Array.isArray(value.modules) ? value.modules.filter((item) => MODEL_ID.test(String(item))).slice(0, 16) : [],
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

const MODEL_STATE_COPY = Object.freeze({
  DISCOVERED: { label: "Đã quan sát · chờ xác minh", semantic: "partial" },
  NOT_INSTALLED: { label: "Chưa cài đặt", semantic: "unavailable" },
  INSTALLED_UNVERIFIED: { label: "Đã quan sát · chưa xác minh", semantic: "partial" },
  INSTALLED_VERIFIED: { label: "Đã cài · đã xác minh", semantic: "available" },
  OPERATIONAL: { label: "Đang hoạt động", semantic: "available" },
  PARTIAL: { label: "Cài đặt một phần", semantic: "partial" },
  UNAVAILABLE: { label: "Bị chặn · chưa khả dụng", semantic: "unavailable" },
  BROKEN: { label: "Bị chặn · cần sửa", semantic: "unavailable" },
  UPDATE_AVAILABLE: { label: "Có bản cập nhật", semantic: "partial" },
});

const stateCopy = (state) => MODEL_STATE_COPY[state] || { label: "Chưa rõ trạng thái", semantic: "partial" };
const statePill = (state) => {
  const copy = stateCopy(state);
  return `<span class="status-pill" data-status="${copy.semantic}" data-technical-status="${escapeHtml(state)}" aria-label="${escapeHtml(copy.label)}">${escapeHtml(copy.label)}</span>`;
};

const actionLabel = (action) => ({
  PLAN_INSTALL: "Lập kế hoạch cài",
  VERIFY_CHECKSUM: "Lập kế hoạch xác minh",
  REGISTER_EXISTING: "Lập kế hoạch dùng bản có sẵn",
  SAFE_REMOVE: "Lập kế hoạch gỡ an toàn",
}[action] || action);

const plannedActions = new Set(["PLAN_INSTALL", "VERIFY_CHECKSUM", "REGISTER_EXISTING", "SAFE_REMOVE"]);

let selectedModelId = "";
let lastPayload = null;

const readModelFilters = () => {
  if (typeof document === "undefined") return {};
  const controls = document.querySelector("[data-model-filters]");
  return {
    query: controls?.querySelector("[data-model-search]")?.value || "",
    category: controls?.querySelector("[data-model-category]")?.value || "",
    installed: controls?.querySelector("[data-model-installed]")?.value || "all",
  };
};

export const filterModelRecords = (records, filters = {}) => {
  if (!Array.isArray(records)) return [];
  const query = String(filters.query || "").trim().toLocaleLowerCase().slice(0, 80);
  const category = MODEL_CATEGORY.has(String(filters.category || "")) ? String(filters.category) : "";
  const installed = ["all", "installed", "uninstalled"].includes(String(filters.installed || "all")) ? String(filters.installed || "all") : "all";
  return records.filter((item) => {
    if (!item || typeof item !== "object") return false;
    const modules = Array.isArray(item.modules) ? item.modules : [];
    const haystack = [item.display_name, item.model_id, item.family, item.provider, item.category, item.format, item.precision, item.runtime_id, ...modules]
      .filter(Boolean).join(" ").toLocaleLowerCase();
    const isInstalled = item.installed === true;
    return (!query || haystack.includes(query)) && (!category || item.category === category)
      && (installed === "all" || (installed === "installed" && isInstalled) || (installed === "uninstalled" && !isInstalled));
  });
};

const updateFilterCount = (visible, total) => {
  if (typeof document === "undefined") return;
  const target = document.querySelector("[data-model-v2-count]");
  if (!target) return;
  const shown = Math.min(visible, 32);
  target.textContent = `Hiển thị ${shown}/${total} model${visible > 32 ? " · giới hạn danh sách 32" : ""}`;
};

const render = (target, payload, filters = readModelFilters()) => {
  lastPayload = payload;
  const allRecords = payload && payload.schema_version === "model-manager.v2" && Array.isArray(payload.records)
    ? payload.records.map(safeModel).filter(Boolean)
    : [];
  const records = filterModelRecords(allRecords, filters);
  updateFilterCount(records.length, allRecords.length);
  if (!allRecords.length) {
    target.innerHTML = `<div class="empty-state"><strong>Chưa có Model Manager V2 record hợp lệ</strong><span>Giữ trạng thái chưa khả dụng cho tới khi server publish catalog metadata hợp lệ.</span></div>`;
    return;
  }
  if (!records.length) {
    target.innerHTML = `<div class="empty-state compact" data-model-v2-filter-empty><strong>Không có model khớp bộ lọc</strong><span>Đổi từ khóa, danh mục hoặc trạng thái cài đặt để xem lại snapshot server-owned.</span></div>`;
    return;
  }
  const selected = records.find((item) => item.model_id === selectedModelId) || records[0];
  selectedModelId = selected.model_id;
  const master = records.slice(0, 32).map((item) => `<button class="model-manager-v2-master-row${item.model_id === selected.model_id ? " is-selected" : ""}" type="button" data-model-v2-select="${escapeHtml(item.model_id)}" aria-controls="model-manager-v2-detail" aria-current="${item.model_id === selected.model_id ? "true" : "false"}"><span><strong>${escapeHtml(item.display_name)}</strong><small>${escapeHtml(item.category)} · ${escapeHtml(item.model_id)}</small></span>${statePill(item.state)}</button>`).join("");
  const buttons = selected.actions.filter((action) => plannedActions.has(action)).map((action) => `<button class="button button--compact" type="button" data-model-v2-plan="${escapeHtml(selected.model_id)}" data-model-v2-action="${escapeHtml(action)}">${escapeHtml(actionLabel(action))}</button>`).join("");
  const actionArea = buttons || `<span class="small">Chưa có thao tác an toàn cho bản ghi này; giữ nguyên trạng thái chưa khả dụng.</span>`;
  const technical = `<details class="advanced model-manager-v2-technical"><summary>Chi tiết kỹ thuật</summary><p class="small">Mã trạng thái: <code>${escapeHtml(selected.state)}</code> · checksum: <code>${escapeHtml(selected.checksum_state)}</code> · duplicate: <code>${escapeHtml(selected.duplicate_state)}</code> · moved: <code>${escapeHtml(selected.moved_state)}</code> · runtime: <code>${escapeHtml(selected.runtime_id)}</code> (<code>${escapeHtml(selected.runtime_status)}</code>).</p></details>`;
  target.innerHTML = `<div class="model-manager-v2-master-detail"><nav class="model-manager-v2-master" aria-label="Danh sách model" role="list">${master || `<div class="empty-state compact">Chưa có model hợp lệ.</div>`}</nav><section class="model-manager-v2-detail" id="model-manager-v2-detail" aria-live="polite" data-model-v2-record="${escapeHtml(selected.model_id)}"><div class="split"><div><strong>${escapeHtml(selected.display_name)}</strong><p class="small">${escapeHtml(selected.category)} · ${escapeHtml(selected.format)} · ${escapeHtml(selected.precision)}</p></div>${statePill(selected.state)}</div><div class="component-manager-grid"><div><span>Đã cài</span><strong>${selected.installed ? "Có" : "Chưa"}</strong></div><div><span>Đã xác minh</span><strong>${selected.verified ? "Có" : "Chưa"}</strong></div><div><span>Dung lượng</span><strong>${escapeHtml(formatBytes(selected.size_bytes))}</strong></div><div><span>VRAM ước tính</span><strong>${selected.vram_estimate_mb === null ? "Chưa rõ" : `${escapeHtml(String(selected.vram_estimate_mb))} MiB`}</strong></div></div><p>${escapeHtml(selected.reason)}</p><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(selected.next_action)}</span></div><div class="form-actions" data-model-v2-actions>${actionArea}</div>${technical}</section></div>`;
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
    if (target) render(target, payload, readModelFilters());
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
  const selectButton = event.target.closest("[data-model-v2-select]");
  if (selectButton && lastPayload) {
    selectedModelId = String(selectButton.dataset.modelV2Select || "");
    const target = document.querySelector("[data-model-manager-v2-list]");
    if (target) render(target, lastPayload, readModelFilters());
    return;
  }
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

if (typeof document !== "undefined") {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
  else mount();
}
