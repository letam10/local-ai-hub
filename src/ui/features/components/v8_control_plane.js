import { translateText } from "../../i18n.js";

/*
  V8 Wave 4 source-side Product UX.
  - Reads only opaque component operation/source-acceptance endpoints.
  - Never receives or renders raw workstation paths.
  - No polling: refresh happens on Components-page render or explicit user action.
*/

const OPERATION_ID = /^compop_[a-f0-9]{32}$/;
const COMPONENT_ID = /^[a-z][a-z0-9._-]{1,95}$/;
const CAPABILITY_ID = /^[a-z][a-z0-9._:-]{1,127}$/;
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
const ACCEPTANCE_STATE_LABELS = Object.freeze({
  AUTO_INSTALL_READY: "Sẵn sàng lập kế hoạch",
  AUTH_REQUIRED: "Cần cấp quyền",
  INTEGRITY_INCOMPLETE: "Thiếu kiểm tra toàn vẹn",
  LICENSE_REVIEW_REQUIRED: "Cần xem giấy phép",
  MANUAL_REVIEW_REQUIRED: "Cần xem thủ công",
  REFERENCE_EXISTING: "Dùng bản có sẵn",
  SIZE_UNKNOWN: "Chưa rõ dung lượng",
  SOURCE_UNAVAILABLE: "Nguồn chưa khả dụng",
  SOURCE_UNVERIFIED: "Nguồn chưa xác minh",
  UNSUPPORTED_SOURCE: "Nguồn không hỗ trợ",
});
const CAPABILITY_OPERATIONAL_STATES = new Set([
  "DISCOVERED", "REGISTERED", "INSTALLED", "INSTALLED_UNVERIFIED", "VERIFIED",
  "STARTABLE", "RUNNING", "OPERATIONAL", "DEGRADED", "UNAVAILABLE", "BROKEN",
]);
const CAPABILITY_INSTALL_STATES = new Set(["DISCOVERED", "REGISTERED", "INSTALLED", "INSTALLED_UNVERIFIED", "UNAVAILABLE", "BROKEN"]);
const CAPABILITY_RUNTIME_STATES = new Set(["NOT_REQUESTED", "NOT_STARTED", "STARTABLE", "RUNNING", "UNAVAILABLE", "BROKEN"]);
const CAPABILITY_VERIFICATION_STATES = new Set(["NOT_VERIFIED", "METADATA", "FILESYSTEM", "RUNTIME_IMPORT", "PROCESS_HEALTH", "BOUNDED_SMOKE", "PRODUCTION", "FAILED"]);
const CAPABILITY_SAFE_ACTIONS = new Set(["inspect", "review_dependency", "plan_install", "plan_import", "verify_filesystem", "verify_runtime", "request_bounded_smoke", "review_evidence", "repair", "review_source", "review_license"]);
const LIFECYCLE_ACTIONS = new Set(["DISCOVER", "INSPECT", "PLAN_INSTALL", "VERIFY_SOURCE", "INSTALL", "VERIFY_INSTALL", "START", "HEALTH_CHECK", "STOP", "UPDATE", "REPAIR", "UNINSTALL", "ROLLBACK"]);
const LIFECYCLE_AVAILABILITY = new Set(["read_only", "plan_available", "plan_required", "blocked"]);
const UNSAFE_GRAPH_TEXT = /(?:[a-z]:[\\/]|\\\\|(?:https?|file|data):|bearer\s|\b(?:api[_-]?key|secret|password|token)\b|\.\.[\\/])/i;

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
const getCapabilityGraph = () => request("/api/capabilities/v2");
const getComponentLifecycle = () => request("/api/component-lifecycle/v2");
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

const safeGraphText = (value, fallback) => {
  const candidate = typeof value === "string" ? value.trim().slice(0, 320) : "";
  return candidate && !UNSAFE_GRAPH_TEXT.test(candidate) ? candidate : fallback;
};

const safeBlocker = (value) => {
  if (!value || typeof value !== "object") return null;
  const capabilityId = String(value.capability_id || "");
  const code = String(value.code || "");
  const operationalState = String(value.operational_state || "");
  if (!CAPABILITY_ID.test(capabilityId) || !/^[a-z][a-z0-9_]{1,63}$/.test(code) || !CAPABILITY_OPERATIONAL_STATES.has(operationalState)) return null;
  return {
    capability_id: capabilityId,
    code,
    operational_state: operationalState,
    reason: safeGraphText(value.reason, "Capability dependency needs review."),
    next_action: safeGraphText(value.next_action, "Review the server-owned dependency graph."),
  };
};

const safeCapability = (value) => {
  if (!value || typeof value !== "object") return null;
  const capabilityId = String(value.capability_id || "");
  const provider = String(value.provider || "");
  const operationalState = String(value.operational_state || "");
  const installState = String(value.install_state || "");
  const runtimeState = String(value.runtime_state || "");
  const verificationState = String(value.verification_state || "");
  if (!CAPABILITY_ID.test(capabilityId) || !CAPABILITY_ID.test(provider) || !CAPABILITY_OPERATIONAL_STATES.has(operationalState) || !CAPABILITY_INSTALL_STATES.has(installState) || !CAPABILITY_RUNTIME_STATES.has(runtimeState) || !CAPABILITY_VERIFICATION_STATES.has(verificationState)) return null;
  const blockers = Array.isArray(value.blockers) ? value.blockers.map(safeBlocker).filter(Boolean).slice(0, 8) : [];
  const actions = Array.isArray(value.safe_actions) ? value.safe_actions.filter((item) => CAPABILITY_SAFE_ACTIONS.has(item)).slice(0, 8) : [];
  return {
    capability_id: capabilityId,
    provider,
    operational_state: operationalState,
    install_state: installState,
    runtime_state: runtimeState,
    verification_state: verificationState,
    reason: safeGraphText(value.reason, "Capability state is awaiting server-owned evidence."),
    next_action: safeGraphText(value.next_action, "Inspect the exact blocker before requesting execution."),
    blockers,
    safe_actions: actions,
  };
};

const safeLifecycleAction = (value) => {
  if (!value || typeof value !== "object") return null;
  const action = String(value.action || "");
  const availability = String(value.availability || "");
  if (!LIFECYCLE_ACTIONS.has(action) || !LIFECYCLE_AVAILABILITY.has(availability)) return null;
  return {
    action,
    availability,
    reason: safeGraphText(value.reason, "Lifecycle action is awaiting server-owned evidence."),
    next_action: safeGraphText(value.next_action, "Inspect the capability before creating a plan."),
  };
};

const safeLifecycleRecord = (value) => {
  if (!value || typeof value !== "object") return null;
  const capabilityId = String(value.capability_id || "");
  const componentId = String(value.component_id || "");
  const componentType = String(value.component_type || "");
  const operationalState = String(value.operational_state || "");
  if (!CAPABILITY_ID.test(capabilityId) || !COMPONENT_ID.test(componentId) || !FINITE_COMPONENT_TYPES.has(componentType) || !CAPABILITY_OPERATIONAL_STATES.has(operationalState) || value.lifecycle_eligible !== true) return null;
  const actions = Array.isArray(value.actions) ? value.actions.map(safeLifecycleAction).filter(Boolean) : [];
  const blockers = Array.isArray(value.blockers) ? value.blockers.map(safeBlocker).filter(Boolean).slice(0, 8) : [];
  return {
    capability_id: capabilityId,
    component_id: componentId,
    component_type: componentType,
    operational_state: operationalState,
    actions,
    blockers,
    reason: safeGraphText(value.reason, "Lifecycle state is awaiting server-owned evidence."),
    next_action: safeGraphText(value.next_action, "Inspect the exact dependency before planning."),
  };
};

const pill = (state) => {
  const code = String(state || "unknown");
  const label = ACCEPTANCE_STATE_LABELS[code] || code;
  const semantic = ["AUTO_INSTALL_READY"].includes(code) ? "available" : ["UNSUPPORTED_SOURCE"].includes(code) ? "unavailable" : "partial";
  return `<span class="status-pill" data-status="${escapeHtml(semantic)}" data-technical-status="${escapeHtml(code)}" title="${escapeHtml(code)}">${escapeHtml(label)} <code>${escapeHtml(code)}</code></span>`;
};

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
    slot.innerHTML = `<div class="split"><strong>${uiText("V8 source acceptance")}</strong>${pill(item.acceptance_state)}</div><p class="small">${uiText("Disposition:")} ${escapeHtml(item.disposition)} · ${uiText("Auto-install:")} <strong>${item.auto_install_eligible ? uiText("eligible") : uiText("disabled")}</strong></p><p class="small">${uiText("Source")} ${requirementLabel(req.source_verified)} · ${uiText("Auth")} ${requirementLabel(req.authentication_ready)} · ${uiText("License")} ${requirementLabel(req.license_ready)} · ${uiText("Integrity")} ${requirementLabel(req.integrity_ready)} · ${uiText("Size")} ${requirementLabel(req.size_ready)}</p><div class="workspace-state__action"><strong>${uiText("Bước tiếp theo")}</strong><span>${escapeHtml(fixedCopy(item.next_action) || "Giữ component ở trạng thái chưa tự động cho tới khi đủ bằng chứng.")}</span></div>`;
  }
};

const capabilityStatePill = (state) => {
  const semantic = state === "OPERATIONAL" ? "available" : state === "BROKEN" || state === "UNAVAILABLE" ? "unavailable" : "partial";
  return `<span class="status-pill" data-status="${semantic}" data-technical-status="${escapeHtml(state)}"><code>${escapeHtml(state)}</code></span>`;
};

const renderCapabilityGraph = (target, payload) => {
  const records = payload && payload.schema_version === "capability-graph.v2" && Array.isArray(payload.capabilities)
    ? payload.capabilities.map(safeCapability).filter(Boolean)
    : [];
  if (!records.length) {
    target.innerHTML = `<div class="empty-state"><strong>Capability Graph chưa có dữ liệu hợp lệ</strong><span>Giữ trạng thái chưa khả dụng cho tới khi server publish projection đã xác minh.</span></div>`;
    return;
  }
  const attention = records.filter((item) => item.blockers.length || item.operational_state !== "OPERATIONAL").slice(0, 24);
  const visible = attention.length ? attention : records.slice(0, 24);
  target.innerHTML = `<div class="component-manager-list">${visible.map((item) => {
    const blockers = item.blockers.length
      ? `<ul class="capability-graph__blockers">${item.blockers.map((blocker) => `<li><code>${escapeHtml(blocker.capability_id)}</code> · ${escapeHtml(blocker.code)} · ${escapeHtml(blocker.reason)}</li>`).join("")}</ul>`
      : `<p class="small">Không có blocker dependency được publish trong snapshot này.</p>`;
    const actions = item.safe_actions.length ? `<p class="small"><strong>Thao tác an toàn:</strong> ${item.safe_actions.map((action) => `<code>${escapeHtml(action)}</code>`).join(" ")}</p>` : "";
    return `<article class="component-plan-preview" data-capability-graph-record="${escapeHtml(item.capability_id)}"><div class="split"><div><strong><code>${escapeHtml(item.capability_id)}</code></strong><p class="small">${escapeHtml(item.provider)} · cài đặt <code>${escapeHtml(item.install_state)}</code> · runtime <code>${escapeHtml(item.runtime_state)}</code> · xác minh <code>${escapeHtml(item.verification_state)}</code></p></div>${capabilityStatePill(item.operational_state)}</div><p>${escapeHtml(item.reason)}</p><div class="workspace-state__action"><strong>Phụ thuộc chặn</strong>${blockers}</div>${actions}<div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(item.next_action)}</span></div></article>`;
  }).join("")}</div>`;
};

const lifecycleAvailabilityLabel = (value) => ({
  read_only: "Chỉ xem",
  plan_available: "Có thể lập kế hoạch",
  plan_required: "Cần lập kế hoạch trước",
  blocked: "Đang chặn an toàn",
}[value] || "Không rõ");

const renderComponentLifecycle = (target, payload) => {
  const records = payload && payload.schema_version === "component-lifecycle.v2" && Array.isArray(payload.components)
    ? payload.components.map(safeLifecycleRecord).filter(Boolean)
    : [];
  if (!records.length) {
    target.innerHTML = `<div class="empty-state"><strong>Chưa có lifecycle record hợp lệ</strong><span>Giữ trạng thái chưa khả dụng cho tới khi Capability Graph publish model hoặc runtime có thể lập kế hoạch.</span></div>`;
    return;
  }
  target.innerHTML = `<div class="component-manager-list">${records.slice(0, 24).map((item) => {
    const actions = item.actions.map((action) => `<li><code>${escapeHtml(action.action)}</code> · <strong>${escapeHtml(lifecycleAvailabilityLabel(action.availability))}</strong><br /><span class="small">${escapeHtml(action.reason)}</span></li>`).join("");
    const blockers = item.blockers.length ? `<p class="small"><strong>Phụ thuộc chặn:</strong> ${item.blockers.map((blocker) => `<code>${escapeHtml(blocker.capability_id)}</code>`).join(" · ")}</p>` : "";
    return `<article class="component-plan-preview" data-component-lifecycle-record="${escapeHtml(item.capability_id)}"><div class="split"><div><strong><code>${escapeHtml(item.capability_id)}</code></strong><p class="small">${escapeHtml(item.component_type)} · trạng thái <code>${escapeHtml(item.operational_state)}</code></p></div>${capabilityStatePill(item.operational_state)}</div><p>${escapeHtml(item.reason)}</p>${blockers}<details><summary>Hợp đồng thao tác</summary><ul class="capability-graph__blockers">${actions}</ul></details><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(item.next_action)}</span></div></article>`;
  }).join("")}</div>`;
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

const graphStatusText = (text, kind = "") => {
  const target = document.querySelector("[data-capability-graph-status]");
  if (!target) return;
  target.textContent = text;
  target.dataset.state = kind;
};

const lifecycleStatusText = (text, kind = "") => {
  const target = document.querySelector("[data-component-lifecycle-status]");
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
  graphStatusText("Đang đọc Capability Graph server-owned…", "loading");
  lifecycleStatusText("Đang đọc Component Lifecycle V2…", "loading");
  try {
    const [operationsResult, acceptanceResult, graphResult, lifecycleResult] = await Promise.allSettled([getOperations(), getSourceAcceptance(), getCapabilityGraph(), getComponentLifecycle()]);
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
    const graphTarget = document.querySelector("[data-capability-graph-list]");
    if (graphResult.status === "fulfilled" && graphTarget) {
      renderCapabilityGraph(graphTarget, graphResult.value);
      graphStatusText("Capability Graph đã đồng bộ từ metadata/evidence server-owned; không có execution.", "ready");
    } else if (graphTarget) {
      graphTarget.innerHTML = `<div class="callout callout--warning">Không đọc được Capability Graph ở lần làm mới này.</div>`;
      graphStatusText("Capability Graph chưa khả dụng; giữ nguyên trạng thái không xác minh.", "partial");
    }
    const lifecycleTarget = document.querySelector("[data-component-lifecycle-list]");
    if (lifecycleResult.status === "fulfilled" && lifecycleTarget) {
      renderComponentLifecycle(lifecycleTarget, lifecycleResult.value);
      lifecycleStatusText("Component Lifecycle V2 chỉ công bố action contract/plan; chưa thực thi component.", "ready");
    } else if (lifecycleTarget) {
      lifecycleTarget.innerHTML = `<div class="callout callout--warning">Không đọc được Component Lifecycle V2 ở lần làm mới này.</div>`;
      lifecycleStatusText("Component Lifecycle V2 chưa khả dụng; không suy diễn trạng thái chạy.", "partial");
    }
    const failures = [operationsResult, acceptanceResult, graphResult, lifecycleResult].filter((item) => item.status === "rejected").length;
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
  const graphRefreshButton = event.target.closest("[data-capability-graph-refresh]");
  if (graphRefreshButton) { refresh(); return; }
  const lifecycleRefreshButton = event.target.closest("[data-component-lifecycle-refresh]");
  if (lifecycleRefreshButton) { refresh(); return; }
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
  // Rendering the panel itself mutates #module-view.  Refresh only when the
  // Components panel enters the view, not on each descendant mutation, so the
  // observer cannot become an accidental polling loop.
  let panelWasPresent = false;
  const refreshWhenPanelEnters = () => {
    const panelPresent = Boolean(view.querySelector("[data-v8-component-control-plane]"));
    if (panelPresent && !panelWasPresent) refresh();
    panelWasPresent = panelPresent;
  };
  const observer = new MutationObserver(refreshWhenPanelEnters);
  observer.observe(view, { childList: true });
  window.addEventListener("hashchange", () => {
    panelWasPresent = Boolean(view.querySelector("[data-v8-component-control-plane]"));
    if (panelWasPresent) refresh();
  });
  refreshWhenPanelEnters();
};

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount, { once: true });
else mount();
