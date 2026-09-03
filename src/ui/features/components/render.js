const COMPONENT_ID = /^[a-z][a-z0-9._-]{1,95}$/;

/* Keep selection derived from the immutable server snapshot.  A missing
 * selection falls back to the first component in a stable id/type order. */
export function chooseComponentId(records, selectedId = "") {
  const candidates = (Array.isArray(records) ? records : [])
    .filter((item) => item && typeof item === "object" && !Array.isArray(item))
    .map((item) => ({
      id: String(item.component_id || ""),
      type: String(item.component_type || ""),
    }))
    .filter((item) => COMPONENT_ID.test(item.id));
  const selected = String(selectedId || "");
  if (selected && candidates.some((item) => item.id === selected)) return selected;
  const ordered = [...candidates].sort((left, right) => {
    const idOrder = left.id.localeCompare(right.id);
    return idOrder || left.type.localeCompare(right.type);
  });
  return ordered[0]?.id || "";
}

export function createComponentsRenderer(deps) {
  const { heading, escapeHtml, statusPill, statusExplanation, statusImpact, readinessStatusLabel, uiTextHtml, uiText } = deps;
  const componentStatusCopy = Object.freeze({
    PARTIAL: { label: "Partial", reason: "Cài đặt component chưa hoàn chỉnh.", action: "Kiểm tra component và lập kế hoạch sửa/import có kiểm soát." },
    INSTALLED_UNVERIFIED: { label: "Đã cài · chưa xác minh", reason: "Resource cục bộ đã được quan sát nhưng leaf catalog hoặc smoke bounded chưa được xác minh.", action: "Chạy kiểm tra bounded theo kế hoạch; không tự tải lại resource đang có." },
    OPERATIONAL: { label: "Đang hoạt động", reason: "Leaf bắt buộc và bằng chứng bounded tương ứng đã khớp.", action: "Có thể dùng theo phạm vi bằng chứng hiện tại." },
    LICENSE_REVIEW_REQUIRED: { label: "Cần xem giấy phép", reason: "Giấy phép upstream chưa được xem xét/cho phép trong catalog.", action: "Xem hợp đồng license trước khi bật bất kỳ thao tác cài đặt nào." },
    AUTH_REQUIRED: { label: "Cần cấp quyền", reason: "Nguồn yêu cầu xác thực provider; Hub chưa có quyền hợp lệ.", action: "Hoàn tất luồng cấp quyền riêng rồi mới lập kế hoạch cài đặt." },
    SOURCE_UNAVAILABLE: { label: "Nguồn chưa khả dụng", reason: "Catalog chưa có nguồn chính thức đủ tin cậy.", action: "Giữ cài đặt tắt cho tới khi nguồn được catalog xác nhận." },
    SOURCE_UNVERIFIED: { label: "Nguồn chưa xác minh", reason: "Danh tính nguồn hoặc revision HTTPS chưa được xác minh.", action: "Xác minh source identity trước khi bật cài đặt tự động." },
    INTEGRITY_INCOMPLETE: { label: "Thiếu kiểm tra toàn vẹn", reason: "Catalog chưa ghim đủ kích thước hoặc SHA-256 của leaf.", action: "Bổ sung integrity metadata rồi mới cân nhắc cài đặt." },
    SIZE_UNKNOWN: { label: "Chưa rõ dung lượng", reason: "Chưa có ước tính tải xuống/dung lượng đĩa đáng tin cậy.", action: "Ghi nhận ước tính bounded trước khi lập gói." },
    REFERENCE_EXISTING: { label: "Dùng bản có sẵn", reason: "Runtime do nguồn khác quản lý; Hub không thay thế hay tải lại.", action: "Kiểm tra bản cài hiện có và giữ nguyên nguồn quản lý." },
    UNSUPPORTED_SOURCE: { label: "Nguồn không hỗ trợ", reason: "Catalog không cho phép cài tự động từ nguồn này.", action: "Thực hiện review thủ công theo tài liệu chính thức." },
  });

  const componentCopy = (item, fallbackReason, fallbackAction) => {
    const code = String(item?.status || "").toUpperCase();
    const mapped = componentStatusCopy[code];
    const rawReason = typeof item?.reason === "string" ? item.reason : "";
    const rawAction = typeof item?.next_action === "string" ? item.next_action : "";
    const reasonMap = {
      "No catalog leaf is present under the managed Models root.": "Chưa tìm thấy leaf catalog dưới root Models managed.",
      "Some catalog leaves are present but the managed installation is incomplete.": "Chỉ một phần leaf catalog hiện diện; cài đặt managed chưa hoàn chỉnh.",
      "Required runtime leaves are present; import/package and bounded smoke evidence are still required.": "Leaf runtime đã có; vẫn cần xác minh import/package và bounded smoke.",
      "Review the tracked license contract before any install action is enabled.": "Xem hợp đồng license đã theo dõi trước khi bật thao tác cài đặt.",
      "Authorize the official provider before installation.": "Cấp quyền cho provider chính thức trước khi cài đặt.",
    };
    return {
      reason: reasonMap[rawReason] || mapped?.reason || rawReason || fallbackReason,
      action: mapped?.action || rawAction || fallbackAction,
      label: mapped?.label || readinessStatusLabel(code || "unknown"),
    };
  };

  const fixedCopy = (value, fallback) => {
    const candidate = typeof value === "string" ? value : "";
    const keys = new Set([
      "Components are discovered from bounded catalogs; no startup installation or inference occurred.",
      "No fixed catalog leaf is present under the managed root.",
      "Review an explicit component import or installation plan.",
      "Review the tracked license contract before any install action is enabled.",
    ]);
    return keys.has(candidate) ? uiText(candidate) : candidate || uiText(fallback);
  };

  const safeId = (value) => COMPONENT_ID.test(String(value || "")) ? String(value) : "";

  return function renderComponents(state) {
    const source = state && typeof state === "object" ? state : {};
    const manager = source.componentManager && typeof source.componentManager === "object" ? source.componentManager : {};
    const records = Array.isArray(manager.records) ? manager.records : [];
    const plans = source.componentPlans && typeof source.componentPlans === "object" ? source.componentPlans : {};
    const entries = records.map((item) => {
      const id = safeId(item.component_id);
      const type = item.component_type === "runtime" ? "runtime" : item.component_type === "model" ? "model" : "";
      if (!id || !type) return null;
      const key = `${type}:${id}`;
      const status = String(item.status || "unavailable").toLowerCase();
      const copy = componentCopy(item, "Snapshot server chưa có thêm lý do.", "Xem kế hoạch do máy chủ sở hữu.");
      const pillStatus = ["license_review_required", "auth_required", "source_unavailable", "source_unverified", "integrity_incomplete", "size_unknown", "manual_review_required"].includes(status)
        ? "partial"
        : status === "unsupported_source" ? "unavailable" : status;
      return { item, id, type, key, status, copy, pillStatus };
    }).filter(Boolean).sort((left, right) => left.id.localeCompare(right.id) || left.type.localeCompare(right.type));
    const selectedId = chooseComponentId(records, source.selectedComponentId);
    const selectedEntry = entries.find((entry) => entry.id === selectedId) || entries[0] || null;
    const detailOpen = source.componentDetailOpen !== false && Boolean(selectedEntry);

    const masterRows = entries.map((entry, index) => {
      const selected = selectedEntry?.key === entry.key;
      const item = entry.item;
      return `<button class="component-master-row${selected ? " is-selected" : ""}" type="button" data-component-select="${escapeHtml(entry.id)}" data-component-type="${escapeHtml(entry.type)}" data-component-index="${index}" aria-controls="component-detail-panel" aria-expanded="${selected && detailOpen}"${selected ? " aria-current=\"true\"" : ""}><span class="component-master-row__copy"><strong>${escapeHtml(item.display_name || entry.id)}</strong><small>${escapeHtml(entry.type.toUpperCase())} · ${escapeHtml(entry.id)}</small></span>${statusPill(entry.pillStatus, entry.copy.label)}</button>`;
    }).join("");

    const renderSelectedCard = (entry) => {
      const { item, id, type, key, status, copy, pillStatus } = entry;
      const plan = plans[key];
      const bundle = plans[`bundle:${key}`];
      const reuse = plans[`reuse:${key}`];
      const imported = plans[`import:${key}`];
      const purpose = type === "runtime"
        ? "Cung cấp runtime được quản lý để workflow có thể khởi chạy an toàn."
        : "Cung cấp model/component được workflow yêu cầu; Hub chỉ xác nhận metadata, không tự cài khi mở trang.";
      const impact = statusImpact(status, item.display_name || id);
      const explanation = statusExplanation({ name: item.display_name || id, technicalId: id, purpose, status, reason: fixedCopy(copy.reason, "Snapshot server chưa có thêm lý do."), impact, nextAction: fixedCopy(copy.action, "Xem kế hoạch server-owned."), compact: true });
      const planButton = item.plan_available === false ? "" : `<button class="button button--compact" type="button" data-component-plan="${escapeHtml(id)}" data-component-type="${escapeHtml(type)}" data-i18n="Lập kế hoạch">${uiTextHtml("Lập kế hoạch")}</button><button class="button button--compact button--accent" type="button" data-component-bundle="${escapeHtml(id)}" data-component-type="${escapeHtml(type)}" data-i18n="Lập gói phụ thuộc">${uiTextHtml("Lập gói phụ thuộc")}</button>${type === "model" ? `<button class="button button--compact" type="button" data-component-native-import="${escapeHtml(id)}" data-i18n="Import từ máy này">${uiTextHtml("Import từ máy này")}</button>` : ""}`;
      const confirmButton = plan?.action ? `<button class="button button--compact button--accent" type="button" data-component-maintenance-confirm="${escapeHtml(plan.plan_id || "")}" data-i18n="Xác nhận bảo trì">${uiTextHtml("Xác nhận bảo trì")}</button>` : plan?.plan_id ? `<button class="button button--compact button--accent" type="button" data-component-confirm="${escapeHtml(plan.plan_id)}" data-i18n="Xác nhận kế hoạch">${uiTextHtml("Xác nhận kế hoạch")}</button>` : "";
      const operationHint = (value) => value?.operation_id ? `<p class="small"><strong>V8 operation:</strong> <code>${escapeHtml(value.operation_id)}</code> · ${escapeHtml(value.operation_state || "planned")}</p>` : "";
      const planDetail = plan ? `<div class="component-plan-preview" data-component-plan-preview="${escapeHtml(key)}"><div class="split"><strong><span data-i18n="Kế hoạch">${uiTextHtml("Kế hoạch")}</span> ${escapeHtml(plan.plan_id || "")}</strong>${statusPill(plan.status || "planned")}</div>${operationHint(plan)}<p>${escapeHtml(plan.reason || uiText("Kế hoạch do server quản lý; chưa thực thi."))}</p>${confirmButton}</div>` : "";
      const bundleDetail = bundle ? `<div class="component-plan-preview" data-component-bundle-preview="${escapeHtml(key)}"><div class="split"><strong><span data-i18n="Gói phụ thuộc">${uiTextHtml("Gói phụ thuộc")}</span> ${escapeHtml(bundle.plan_id || "")}</strong>${statusPill(bundle.status || "planned")}</div>${operationHint(bundle)}<p>${escapeHtml(`Các bước: ${Array.isArray(bundle.steps) ? bundle.steps.length : 0}; tải dự kiến: ${Number(bundle.download_bytes || 0)} bytes; dung lượng: ${Number(bundle.estimated_disk_bytes || 0)} bytes.`)}</p>${bundle.plan_id ? `<button class="button button--compact button--accent" type="button" data-component-bundle-confirm="${escapeHtml(bundle.plan_id)}" data-i18n="Xác nhận gói">${uiTextHtml("Xác nhận gói")}</button>` : ""}</div>` : "";
      const reuseDetail = reuse ? `<div class="component-plan-preview" data-component-reuse-preview="${escapeHtml(key)}"><div class="split"><strong><span data-i18n="Reuse bản cài sẵn">${uiTextHtml("Reuse bản cài sẵn")}</span> ${escapeHtml(reuse.plan_id || "")}</strong>${statusPill(reuse.status || "planned")}</div>${operationHint(reuse)}<p>${escapeHtml(reuse.next_action || "Chỉ ghi receipt sau khi mọi leaf khớp; không copy và không tải lại.")}</p>${reuse.plan_id ? `<button class="button button--compact" type="button" data-component-reuse-confirm="${escapeHtml(reuse.plan_id)}" data-i18n="Xác nhận đăng ký">${uiTextHtml("Xác nhận đăng ký")}</button>` : ""}</div>` : "";
      const importDetail = imported ? `<div class="component-plan-preview" data-component-import-preview="${escapeHtml(key)}"><div class="split"><strong><span data-i18n="Kế hoạch import">${uiTextHtml("Kế hoạch import")}</span> ${escapeHtml(imported.plan_id || "")}</strong>${statusPill(imported.status || "planned")}</div>${operationHint(imported)}<p>${escapeHtml(imported.next_action || "Xem xác nhận import server-owned.")}</p>${imported.plan_id ? `<button class="button button--compact button--accent" type="button" data-component-import-confirm="${escapeHtml(imported.plan_id)}" data-i18n="Xác nhận import">${uiTextHtml("Xác nhận import")}</button>` : ""}</div>` : "";
      const maintenanceButtons = `<button class="button button--compact" type="button" data-component-reuse="${escapeHtml(id)}" data-component-type="${escapeHtml(type)}" data-i18n="Kiểm tra bản cài sẵn">${uiTextHtml("Kiểm tra bản cài sẵn")}</button><button class="button button--compact" type="button" data-component-maintenance="${escapeHtml(id)}" data-component-type="${escapeHtml(type)}" data-maintenance-action="repair" data-i18n="Lập kế hoạch sửa">${uiTextHtml("Lập kế hoạch sửa")}</button><button class="button button--compact" type="button" data-component-maintenance="${escapeHtml(id)}" data-component-type="${escapeHtml(type)}" data-maintenance-action="update" data-i18n="Kiểm tra cập nhật">${uiTextHtml("Kiểm tra cập nhật")}</button><button class="button button--compact" type="button" data-component-maintenance="${escapeHtml(id)}" data-component-type="${escapeHtml(type)}" data-maintenance-action="uninstall" data-i18n="Gỡ component">${uiTextHtml("Gỡ component")}</button>`;
      const sourceSlot = `<div class="component-plan-preview" data-v8-source-state="${escapeHtml(id)}" data-v8-source-type="${escapeHtml(type)}"><p class="small">Đang tải V8 source acceptance…</p></div>`;
      const stateLabel = (value) => value && value !== "—" ? componentCopy({ status: value }, readinessStatusLabel(value), "Xem bằng chứng server-owned.").label : "—";
      const stateCode = (value) => value && value !== "—" ? `<code>${escapeHtml(String(value).toUpperCase())}</code>` : "";
      return `<article class="component-manager-card card" data-component-id="${escapeHtml(id)}" data-component-type="${escapeHtml(type)}"><div class="card-title-row"><div><span class="eyebrow">${escapeHtml(type.toUpperCase())}</span><h2>${escapeHtml(item.display_name || id)}</h2><p class="small">ID kỹ thuật · ${escapeHtml(id)}</p></div>${statusPill(pillStatus, copy.label)}</div>${explanation}<div class="component-manager-grid"><div><span data-i18n="Module state">${uiTextHtml("Module state")}</span><strong>${uiTextHtml(stateLabel(status))} ${stateCode(status)}</strong></div><div><span data-i18n="Runtime state">${uiTextHtml("Runtime state")}</span><strong>${uiTextHtml(stateLabel(item.runtime_status))} ${stateCode(item.runtime_status)}</strong></div><div><span data-i18n="Model state">${uiTextHtml("Model state")}</span><strong>${uiTextHtml(stateLabel(item.model_status))} ${stateCode(item.model_status)}</strong></div><div><span data-i18n="Execution">${uiTextHtml("Execution")}</span><strong>${uiTextHtml(stateLabel(item.execution || "not_run"))} ${stateCode(item.execution || "not_run")}</strong></div></div>${sourceSlot}<div class="form-actions">${planButton}${maintenanceButtons}</div>${planDetail}${bundleDetail}${reuseDetail}${importDetail}</article>`;
    };

    const selectedCard = selectedEntry ? renderSelectedCard(selectedEntry) : `<div class="empty-state"><strong>${uiTextHtml("Chưa có component catalog")}</strong><span>${uiTextHtml("Catalog sẽ hiển thị khi Core bootstrap đọc được metadata tracked.")}</span></div>`;
    const detailPanel = `<section class="component-detail-panel" id="component-detail-panel" data-component-detail-panel data-component-detail-open="${detailOpen}" data-selected-component="${escapeHtml(selectedId)}" aria-labelledby="component-detail-title"${detailOpen ? "" : " hidden"}><div class="component-detail-panel__head"><div><span class="eyebrow">COMPONENT DETAIL</span><h2 id="component-detail-title">Chi tiết component đang chọn</h2></div><button class="button button--compact" type="button" data-component-close-detail aria-controls="component-detail-panel">Đóng</button></div>${selectedCard}</section>`;
    const dependencyGraph = manager.dependency_graph && typeof manager.dependency_graph === "object" ? Object.keys(manager.dependency_graph).length : 0;
    const v8Panel = `<section class="card" data-v8-component-control-plane aria-live="polite"><div class="card-title-row"><div><span class="eyebrow">${uiTextHtml("V8 CONTROL PLANE")}</span><h2>${uiTextHtml("Component Operations")}</h2><p class="small">${uiTextHtml("Opaque operation IDs, explicit confirmation và source acceptance. Không có raw filesystem path.")}</p></div><button class="button button--compact" type="button" data-v8-component-refresh>${uiTextHtml("Làm mới V8")}</button></div><div data-v8-component-status class="small">${uiTextHtml("Đang tải operation journal…")}</div><div data-v8-component-operations></div></section>`;
    const graphPanel = `<section class="card" data-capability-graph aria-live="polite"><div class="card-title-row"><div><span class="eyebrow">${uiTextHtml("CAPABILITY GRAPH V2")}</span><h2>${uiTextHtml("Phụ thuộc & xác minh")}</h2><p class="small">${uiTextHtml("Mỗi capability tách riêng trạng thái cài đặt, runtime, xác minh và vận hành. Phụ thuộc chặn luôn nêu đúng dependency, không suy diễn từ một nhãn chung.")}</p></div><button class="button button--compact" type="button" data-capability-graph-refresh>${uiTextHtml("Làm mới Capability Graph")}</button></div><div data-capability-graph-status class="small">${uiTextHtml("Đang đọc Capability Graph server-owned…")}</div><div data-capability-graph-list></div></section>`;
    const lifecyclePanel = `<section class="card" data-component-lifecycle aria-live="polite"><div class="card-title-row"><div><span class="eyebrow">${uiTextHtml("COMPONENT LIFECYCLE V2")}</span><h2>${uiTextHtml("Hợp đồng thao tác")}</h2><p class="small">${uiTextHtml("Một hợp đồng chung cho model/runtime. Chỉ action có plan server-owned mới có thể tạo V8 operation; không có thao tác tự chạy tại đây.")}</p></div><button class="button button--compact" type="button" data-component-lifecycle-refresh>${uiTextHtml("Làm mới lifecycle")}</button></div><div data-component-lifecycle-status class="small">${uiTextHtml("Đang đọc Component Lifecycle V2…")}</div><div data-component-lifecycle-list></div></section>`;
    return heading("MODULE MANAGER", "Components / AI Setup", "Quản lý runtime và model bằng kế hoạch server-owned. Không tự tải/cài khi mở Hub; chỉ xác nhận đúng kế hoạch đã xem.", `<button class="button" type="button" data-refresh-components data-i18n="Làm mới">${uiTextHtml("Làm mới")}</button>`) + `<div class="callout callout--warning">${escapeHtml(fixedCopy(manager.reason, "AI components là tuỳ chọn; trạng thái thiếu/partial được giữ trung thực."))} ${dependencyGraph ? `<span data-i18n="Dependency graph">${uiTextHtml("Dependency graph")}</span>: ${dependencyGraph} module.` : ""}</div>${v8Panel}${graphPanel}${lifecyclePanel}<div class="component-manager-list component-manager-master-detail" data-component-master-detail="true"><nav class="component-manager-master" aria-label="Danh sách component" role="list">${masterRows || `<div class="empty-state compact">${uiTextHtml("Chưa có component catalog")}</div>`}</nav>${detailPanel}</div>`;
  };
}
