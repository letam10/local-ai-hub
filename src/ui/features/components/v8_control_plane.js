import { translateText } from "../../i18n.js";

/*
  V8 Wave 4 source-side Product UX.
  - Reads only opaque component operation/source-acceptance endpoints.
  - Never receives or renders raw workstation paths.
  - No polling: refresh happens on Components-page render or explicit user action.
*/

const OPERATION_ID = /^compop_[a-f0-9]{32}$/;
const COMPONENT_ID = /^[a-z][a-z0-9._-]{1,95}$/;
const FINITE_COMPONENT_TYPES = new Set(["model", "runtime"]);
const FINITE_OPERATION_STATES = new Set(["planned", "executing", "verifying", "committed", "failed", "blocked", "cancelled"]);
const FINITE_OPERATION_ACTIONS = new Set(["install", "verify", "reuse", "import", "bundle", "repair", "update", "uninstall"]);
const FINITE_ACCEPTANCE_STATES = new Set([
  "AUTO_INSTALL_READY",
  "AUTH_REQUIRED",
  "INTEGRITY_INCOMPLETE",
  "LICENSE_REVIEW_REQUIRED",
  "MANUAL_REVIEW_REQUIRED",
  "REFERENCE_EXISTING",
  "SIZE_UNKNOWN",
  "SOURCE_UNAVAILABLE",
  "SOURCE_UNVERIFIED",
  "UNSUPPORTED_SOURCE",
]);

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "'": "&#39;",
  '"': "&quot;",
}[char]));

const uiText = (value) => translateText(value);
const fixedCopy = (value) => {
  const candidate = typeof value === "string" ? value : "";
  const keys = new Set([
    "Review an explicit component import or installation plan.",
    "Review the tracked license contract before any install action is enabled.",
  ]);
  return keys.has(candidate) ? uiText(candidate) : candidate;
};

const request = async (path, options = {}) => {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); }
  catch { payload = { status: "error", error: "Phản hồi V8 không phải JSON." }; }
  if (!response.ok) {
    throw new Error(payload.error || payload.code || payload.reason || `HTTP ${response.status}`);
  }
  return payload;
};

const getOperations = () => request("/api/components/operations?limit=12");
const getSourceAcceptance = () => request("/api/components/source-acceptance");
const confirmOperation = (operationId) => request(`/api/components/operations/${encodeURIComponent(operationId)}/confirm`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ confirmed: true }),
});
const cancelOperation = (operationId) => request(`/api/components/operations/${encodeURIComponent(operationId)}/cancel`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: "{}",
});

const safeOperation = (value) => {
  if (!value || typeof value !== "object") return null;
  const operationId = String(value.operation_id || "");
  const componentId = String(value.component_id || "");
  const componentType = String(value.component_type || "");
  const action = String(value.action || "");
  const state = String(value.state || "");
  if (
    !OPERATION_ID.test(operationId)
    || !COMPONENT_ID.test(componentId)
    || !FINITE_COMPONENT_TYPES.has(componentType)
    || !FINITE_OPERATION_ACTIONS.has(action)
    || !FINITE_OPERATION_STATES.has(state)
  ) return null;
  return {
    operation_id: operationId,
    component_id: componentId,
    component_type: componentType,
    action,
    state,
    result_code: typeof value.result_code === "string" ? value.result_code.slice(0, 80) : "",
    updated_at: typeof value.updated_at === "string" ? value.updated_at.slice(0, 80) : "",
  };
};

const safeAcceptance = (value) => {
  if (!value || typeof value !== "object") return null;
  const componentId = String(value.component_id || "");
  const componentType = String(value.component_type || "");
  const acceptanceState = String(value.acceptance_state || "");
  if (!COMPONENT_ID.test(componentId) || !FINITE_COMPONENT_TYPES.has(componentType) || !FINITE_ACCEPTANCE_STATES.has(acceptanceState)) return null;
  const requirements = value.requirements && typeof value.requirements === "object" ? value.requirements : {};
  return {
    component_id: componentId,
    component_type: componentType,
    acceptance_state: acceptanceState,
    disposition: String(value.disposition || "unknown").slice(0, 64),
    auto_install_eligible: value.auto_install_eligible === true,
    source_identity: typeof value.source_identity === "string" ? value.source_identity.slice(0, 256) : "",
    next_action: typeof value.next_action === "string" ? value.next_action.slice(0, 700) : "",
    requirements: {
      catalog_auto_install_ready: requirements.catalog_auto_install_ready === true,
      source_present: requirements.source_present === true,
      source_verified: requirements.source_verified === true,
      authentication_ready: requirements.authentication_ready === true,
      license_ready: requirements.license_ready === true,
      integrity_ready: requirements.integrity_ready === true,
      size_ready: requirements.size_ready === true,
    },
  };
};

const pill = (state) => `<span class="status-pill status-pill--${escapeHtml(String(state).toLowerCase().replace(/[^a-z0-9_-]+/g, "-"))}">${escapeHtml(state)}</span>`;

const renderOperations = (target, operations) => {
  const safe = operations.map(safeOperation).filter(Boolean);
  if (!safe.length) {
    target.innerHTML = `<div class="empty-state"><strong>Chưa có V8 operation</strong><span>Kế hoạch mới sẽ xuất hiện ở đây bằng opaque operation ID.</span></div>`;
    return;
  }
  target.innerHTML = `<div class="component-manager-list">${safe.map((item) => {
    const actions = item.state === "planned" ? `<div class="form-actions"><button class="button button--compact button--accent" type="button" data-v8-operation-confirm="${escapeHtml(item.operation_id)}">Xác nhận operation</button><button class="button button--compact" type="button" data-v8-operation-cancel="${escapeHtml(item.operation_id)}">Hủy operation</button></div>` : "";
    const result = item.result_code ? `<p class="small">${uiText("Result:")} <strong>${escapeHtml(item.result_code)}</strong></p>` : "";
    return `<article class="component-plan-preview"><div class="split"><div><strong>${escapeHtml(item.component_id)}</strong><p class="small">${escapeHtml(item.component_type)} · ${escapeHtml(item.action)} · <code>${escapeHtml(item.operation_id)}</code></p></div>${pill(item.state)}</div>${result}${actions}</article>`;
  }).join("")}</div>`;
};

const requirementLabel = (ready) => ready ? "✓" : "—";

const renderSourceAcceptance = (records) => {
  const byId = new Map(records.map(safeAcceptance).filter(Boolean).map((item) => [item.component_id, item]));
  for (const slot of document.querySelectorAll("[data-v8-source-state]")) {
    const componentId = String(slot.dataset.v8SourceState || "");
    const item = byId.get(componentId);
    if (!item) {
      slot.innerHTML = `<p class="small">V8 source acceptance chưa có record hợp lệ cho component này.</p>`;
      continue;
    }
    const req = item.requirements;
    slot.innerHTML = `<div class="split"><strong>${uiText("V8 source acceptance")}</strong>${pill(item.acceptance_state)}</div><p class="small">${uiText("Disposition:")} ${escapeHtml(item.disposition)} · ${uiText("Auto-install:")} <strong>${item.auto_install_eligible ? uiText("eligible") : uiText("disabled")}</strong></p><p class="small">${uiText("Source")} ${requirementLabel(req.source_verified)} · ${uiText("Auth")} ${requirementLabel(req.authentication_ready)} · ${uiText("License")} ${requirementLabel(req.license_ready)} · ${uiText("Integrity")} ${requirementLabel(req.integrity_ready)} · ${uiText("Size")} ${requirementLabel(req.size_ready)}</p><div class="workspace-state__action"><strong>${uiText("Bước tiếp theo")}</strong><span>${escapeHtml(fixedCopy(item.next_action) || "Giữ component ở trạng thái non-automatic cho tới khi đủ evidence.")}</span></div>`;
  }
};

let generation = 0;
let refreshing = false;
let mounted = false;

const statusText = (text, kind = "") => {
  const target = document.querySelector("[data-v8-component-status]");
  if (!target) return;
  target.textContent = text;
  target.dataset.state = kind;
};

const refresh = async () => {
  const panel = document.querySelector("[data-v8-component-control-plane]");
  if (!panel || refreshing) return;
  refreshing = true;
  const current = ++generation;
  statusText("Đang đọc V8 operation journal và source acceptance…", "loading");
  try {
    const [operationsResult, acceptanceResult] = await Promise.allSettled([getOperations(), getSourceAcceptance()]);
    if (current !== generation || !document.querySelector("[data-v8-component-control-plane]")) return;
    const operationTarget = document.querySelector("[data-v8-component-operations]");
    if (operationsResult.status === "fulfilled" && operationTarget) {
      renderOperations(operationTarget, Array.isArray(operationsResult.value?.operations) ? operationsResult.value.operations : []);
    } else if (operationTarget) {
      operationTarget.innerHTML = `<div class="callout callout--warning">Không đọc được V8 operation journal ở lần làm mới này.</div>`;
    }
    if (acceptanceResult.status === "fulfilled") {
      renderSourceAcceptance(Array.isArray(acceptanceResult.value?.records) ? acceptanceResult.value.records : []);
    }
    const failures = [operationsResult, acceptanceResult].filter((item) => item.status === "rejected").length;
    statusText(failures ? `V8 control plane tải một phần (${failures} nguồn lỗi); dữ liệu hiện có được giữ an toàn.` : uiText("V8 control plane đã đồng bộ từ server-owned metadata; không có download tự động."), failures ? "partial" : "ready");
  } finally {
    refreshing = false;
  }
};

const actOnOperation = async (button, action) => {
  const operationId = action === "confirm" ? button.dataset.v8OperationConfirm : button.dataset.v8OperationCancel;
  if (!OPERATION_ID.test(String(operationId || ""))) {
    statusText("Operation ID không hợp lệ; không gửi request.", "error");
    return;
  }
  if (action === "confirm") {
    if (typeof globalThis.confirm !== "function") {
      statusText("Confirmation UI không khả dụng; operation không được gửi.", "error");
      return;
    }
    if (globalThis.confirm("Xác nhận thực thi đúng V8 operation đã hiển thị? Hành động vẫn tuân theo server-owned plan.") !== true) return;
  }
  button.disabled = true;
  try {
    const result = action === "confirm" ? await confirmOperation(operationId) : await cancelOperation(operationId);
    statusText(result?.next_action || result?.code || (action === "confirm" ? "Operation đã được xử lý." : "Operation đã được hủy."), result?.status || "ready");
    await refresh();
  } catch (error) {
    statusText(error?.message || "Không thể xử lý V8 operation.", "error");
  } finally {
    button.disabled = false;
  }
};

const onClick = (event) => {
  const refreshButton = event.target.closest("[data-v8-component-refresh]");
  if (refreshButton) { refresh(); return; }
  const confirmButton = event.target.closest("[data-v8-operation-confirm]");
  if (confirmButton) { actOnOperation(confirmButton, "confirm"); return; }
  const cancelButton = event.target.closest("[data-v8-operation-cancel]");
  if (cancelButton) actOnOperation(cancelButton, "cancel");
};

const mount = () => {
  if (mounted) return;
  const view = document.querySelector("#module-view");
  if (!view) return;
  mounted = true;
  document.addEventListener("click", onClick);
  const observer = new MutationObserver(() => { if (view.querySelector("[data-v8-component-control-plane]")) refresh(); });
  observer.observe(view, { childList: true });
  window.addEventListener("hashchange", () => { if (view.querySelector("[data-v8-component-control-plane]")) refresh(); });
  if (view.querySelector("[data-v8-component-control-plane]")) refresh();
};

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
else mount();
