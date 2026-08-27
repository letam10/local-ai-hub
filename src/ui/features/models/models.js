/* Models feature renderer.  This module owns catalog-card/table composition;
 * pages.js keeps only the compatibility wrapper for older routes. */
const MODEL_STATUS_INSTALLED = new Set(["INSTALLED", "INSTALLED_UNVERIFIED", "OPERATIONAL"]);

const MODEL_READINESS_COPY = Object.freeze({
  INSTALLED: {
    label: "Installed · runtime not verified",
    reason: "Required catalog leaves match, but this inventory view has not produced bounded runtime evidence.",
    action: "Review the linked runtime and run a bounded verification before use.",
  },
  INSTALLED_UNVERIFIED: {
    label: "Observed locally · not verified",
    reason: "A local model record was observed, but catalog leaves or runtime smoke are not fully verified.",
    action: "Use the existing resource; review the catalog binding and verify it before running.",
  },
  PARTIAL: {
    label: "Partial installation",
    reason: "Only some required catalog leaves were observed, so the model is incomplete.",
    action: "Review the missing leaves and create an explicit import or installation plan.",
  },
  NOT_INSTALLED: {
    label: "Not installed",
    reason: "No required catalog leaf was observed. This means not installed, not that the source is unavailable.",
    action: "Import an existing model or review the server-owned plan, license and source before installing.",
  },
  UNAVAILABLE: {
    label: "Unavailable",
    reason: "The catalog cannot currently provide a usable model source or prerequisite.",
    action: "Keep installation disabled until the missing source or prerequisite is explicitly resolved.",
  },
  OPERATIONAL: {
    label: "Operational evidence",
    reason: "Only a matching bounded runtime evidence record permits the operational label.",
    action: "Use only within the scope of the published runtime evidence.",
  },
});

const inventoryCount = (value, fallback = 0) => Number.isInteger(value) && value >= 0 ? value : fallback;

const modelSizeLabel = (item, { escapeHtml, formatGb, uiTextHtml }) => {
  const raw = typeof item?.size_label === "string" ? item.size_label.trim() : "";
  const rawBytes = raw.match(/^(?:download:\s*)?(\d+)\s+bytes$/i);
  const expectedBytes = Number.isSafeInteger(item?.expected_download_size_bytes) && item.expected_download_size_bytes >= 0
    ? item.expected_download_size_bytes
    : null;
  const bytes = rawBytes ? Number(rawBytes[1]) : expectedBytes;
  if (Number.isSafeInteger(bytes) && bytes >= 0) {
    const prefix = rawBytes && /^download:/i.test(raw) ? `${uiTextHtml("Download")}: ` : "";
    return `${escapeHtml(prefix)}${escapeHtml(formatGb(bytes))}<small class="row-meta">${escapeHtml(String(bytes))} bytes</small>`;
  }
  return escapeHtml(raw || "Size unavailable");
};

const modelReadinessCopy = (item) => {
  const status = String(item?.status || "UNKNOWN").toUpperCase();
  return MODEL_READINESS_COPY[status] || {
    label: "Unknown readiness",
    reason: "The server snapshot does not contain enough evidence to classify this model.",
    action: "Review the bounded server-owned evidence before requesting runtime work.",
  };
};

function filterCatalogModels(models, filters = {}) {
  const query = String(filters.query || "").trim().toLocaleLowerCase().slice(0, 80);
  const category = String(filters.category || "").trim();
  const installed = String(filters.installed || "all");
  return models.filter((item) => {
    const haystack = [item.display_name, item.model_id, item.category, item.provider, ...(item.modules || [])]
      .filter(Boolean).join(" ").toLocaleLowerCase();
    const isInstalled = MODEL_STATUS_INSTALLED.has(String(item.status || "").toUpperCase()) || item.installed === true;
    return (!query || haystack.includes(query)) && (!category || String(item.category || "Other") === category)
      && (installed === "all" || (installed === "installed" && isInstalled) || (installed === "uninstalled" && !isInstalled));
  });
}

export function renderProductionModels({ productionCatalog, legacyModels, storage, updateCenter = {}, filters = {}, escapeHtml, formatGb, statusPill, card, heading, uiTextHtml }) {
  const production = Array.isArray(productionCatalog?.models) ? productionCatalog.models : [];
  const visibleProduction = filterCatalogModels(production, filters);
  const categories = [...new Set(production.map((item) => String(item.category || "Other")))].sort((a, b) => a.localeCompare(b));
  const legacy = Array.isArray(legacyModels) ? legacyModels : [];
  const areaValues = storage?.areas && typeof storage.areas === "object" ? storage.areas : {};
  const managedRootCounts = storage?.managed_root_counts && typeof storage.managed_root_counts === "object" ? storage.managed_root_counts : {};
  const areaNames = [...new Set([...Object.keys(managedRootCounts), ...Object.keys(areaValues)])];
  const areas = areaNames.map((name) => [name, { ...(managedRootCounts[name] || {}), ...(areaValues[name] || {}) }]);
  const inventory = productionCatalog?.inventory && typeof productionCatalog.inventory === "object" ? productionCatalog.inventory : {};
  const inventoryStatus = typeof inventory.status === "string" && inventory.status ? inventory.status : "unknown";
  const inventoryRecords = inventoryCount(inventory.registry_records, production.length);
  const inventoryObserved = inventoryCount(inventory.observed_count, production.filter((item) => item.observed_local === true).length);
  const inventoryVerified = inventoryCount(inventory.verified_installed, production.filter((item) => String(item.status || "").toUpperCase() === "INSTALLED").length);
  const inventoryUnverified = inventoryCount(inventory.installed_unverified_count, production.filter((item) => String(item.status || "").toUpperCase() === "INSTALLED_UNVERIFIED").length);
  const inventoryOperational = inventoryCount(inventory.operational_count, production.filter((item) => String(item.status || "").toUpperCase() === "OPERATIONAL" && item.operational === true).length);
  const inventoryPartial = inventoryCount(inventory.partial_count, production.filter((item) => String(item.status || "").toUpperCase() === "PARTIAL").length);
  const inventoryUnknown = inventoryCount(inventory.unknown_count, production.filter((item) => ["UNKNOWN", "NOT_PUBLISHED"].includes(String(item.status || "").toUpperCase())).length);
  const inventoryNotInstalled = inventoryCount(inventory.not_installed_count, production.filter((item) => String(item.status || "").toUpperCase() === "NOT_INSTALLED").length);
  const inventoryUnavailable = Number.isInteger(inventory.unavailable_count) && Number.isInteger(inventory.not_installed_count)
    ? inventoryCount(inventory.unavailable_count)
    : production.filter((item) => String(item.status || "").toUpperCase() === "UNAVAILABLE").length;
  const inventoryNeedsVerification = inventoryUnverified + inventoryPartial + inventoryUnknown;
  const inventorySummary = `${uiTextHtml("Registry readable")} · ${escapeHtml(String(inventoryRecords))} ${uiTextHtml("model records")} · ${escapeHtml(String(inventoryVerified))} ${uiTextHtml("verified installed")} · ${escapeHtml(String(inventoryOperational))} ${uiTextHtml("operational evidence")} · ${escapeHtml(String(inventoryNeedsVerification))} ${uiTextHtml("needs verification")} · ${escapeHtml(String(inventoryNotInstalled))} ${uiTextHtml("not installed")} · ${escapeHtml(String(inventoryUnavailable))} ${uiTextHtml("unavailable")}`;
  const inventoryCard = `<section class="inventory-health card card--flat" data-inventory-status="${escapeHtml(inventoryStatus)}"><div class="card-title-row"><div><span class="eyebrow">INVENTORY</span><h2>${uiTextHtml("Model inventory")}</h2><p class="small">${uiTextHtml("Healthy chỉ xác nhận registry đọc được; không đồng nghĩa mọi model đã cài hoặc operational.")}</p></div>${statusPill(inventoryStatus, inventoryStatus === "healthy" ? uiTextHtml("Registry readable") : "")}</div><p class="inventory-health__summary">${inventorySummary}</p><div class="inventory-health__metrics"><div><span>${uiTextHtml("Registry records")}</span><strong>${escapeHtml(String(inventoryRecords))}</strong></div><div><span>${uiTextHtml("Observed locally")}</span><strong>${escapeHtml(String(inventoryObserved))}</strong></div><div><span>${uiTextHtml("Verified installed")}</span><strong>${escapeHtml(String(inventoryVerified))}</strong></div><div><span>${uiTextHtml("Operational evidence")}</span><strong>${escapeHtml(String(inventoryOperational))}</strong></div><div><span>${uiTextHtml("Needs verification")}</span><strong>${escapeHtml(String(inventoryNeedsVerification))}</strong></div><div><span>${uiTextHtml("Not installed")}</span><strong>${escapeHtml(String(inventoryNotInstalled))}</strong></div><div><span>${uiTextHtml("Unavailable")}</span><strong>${escapeHtml(String(inventoryUnavailable))}</strong></div></div><p class="small">${uiTextHtml("Component readiness, license, source and runtime smoke are shown per row below; no model was loaded by this inventory view.")}</p></section>`;
  const modelV2Panel = `<section class="card" data-model-manager-v2 aria-live="polite"><div class="card-title-row"><div><span class="eyebrow">MODEL MANAGER V2</span><h2>${uiTextHtml("Trạng thái, tương thích & chống trùng lặp")}</h2><p class="small">${uiTextHtml("Chỉ metadata/evidence giới hạn. Lập kế hoạch không tự tải, nạp, chạy hoặc xóa model.")}</p></div><button class="button button--compact" type="button" data-model-manager-v2-refresh>${uiTextHtml("Làm mới Model Manager")}</button></div><div data-model-manager-v2-status class="small">${uiTextHtml("Đang đọc Model Manager V2…")}</div><div data-model-manager-v2-list></div></section>`;
  const catalogRows = visibleProduction.map((item) => {
    const rawStatus = String(item.status || "UNAVAILABLE").toUpperCase();
    const status = rawStatus.toLowerCase();
    const disposition = String(item.disposition || "MANUAL_IMPORT_ONLY");
    const action = disposition === "AUTO_INSTALL_READY" ? "Download & Install" : disposition === "AUTH_REQUIRED" ? "Authorize & Install" : disposition === "LICENSE_REQUIRED" ? "Review License" : disposition === "MANUAL_IMPORT_ONLY" ? "Import Model" : "Manual Review";
    const sourceStatus = String(item.source_availability?.status || "UNKNOWN").toUpperCase();
    const readiness = modelReadinessCopy(item);
    const sourceLabel = sourceStatus === "UNKNOWN" ? "Source not verified" : sourceStatus === "AVAILABLE" ? "Source available" : "Source requires review";
    return `<tr data-model-readiness="${escapeHtml(rawStatus.toLowerCase())}"><td><strong>${escapeHtml(item.display_name || item.model_id)}</strong><br><small>${escapeHtml(item.model_id || "")}</small></td><td>${escapeHtml(item.category || "Other")}</td><td>${modelSizeLabel(item, { escapeHtml, formatGb, uiTextHtml })}</td><td>${statusPill(status)}<br><small>${uiTextHtml(readiness.label)}</small><br><small>${uiTextHtml("Source")}: ${uiTextHtml(sourceLabel)}</small><br><small>${escapeHtml(readiness.reason)}</small><br><small><strong>${uiTextHtml("Next action")}:</strong> ${escapeHtml(readiness.action)}</small><br><small>${uiTextHtml(action)}</small></td><td><button class="button button--compact" type="button" data-product-plan="${escapeHtml(item.model_id || "")}">${uiTextHtml(action)}</button><button class="button button--compact" type="button" data-check-update="${escapeHtml(item.model_id || "")}">${uiTextHtml("Check Update")}</button></td></tr>`;
  }).join("");
  const legacyRows = legacy.map((item) => `<tr><td>${escapeHtml(item.model_name)}</td><td>${escapeHtml(item.engine)}</td><td>${formatGb(item.size?.bytes)}</td><td>${statusPill(item.installed ? "installed" : "not_installed")}</td></tr>`).join("");
  const table = catalogRows ? `<div class="table-wrap"><table><thead><tr><th>${uiTextHtml("Model")}</th><th>${uiTextHtml("Category")}</th><th>${uiTextHtml("Size")}</th><th>${uiTextHtml("Status / action")}</th><th></th></tr></thead><tbody>${catalogRows}</tbody></table></div>` : legacyRows ? `<div class="table-wrap"><table><thead><tr><th>${uiTextHtml("Model")}</th><th>${uiTextHtml("Engine")}</th><th>${uiTextHtml("Size")}</th><th>${uiTextHtml("Status")}</th></tr></thead><tbody>${legacyRows}</tbody></table></div>` : `<div class="empty-state compact">Chưa có model catalog.</div>`;
  const controls = `<div class="model-catalog-controls" data-model-filters role="search" aria-label="Lọc model catalog">
    <label class="field"><span>Tìm model</span><input type="search" data-model-search value="${escapeHtml(filters.query || "")}" placeholder="Tên, ID hoặc module" autocomplete="off" /></label>
    <label class="field"><span>Danh mục</span><select data-model-category><option value="">Tất cả danh mục</option>${categories.map((item) => `<option value="${escapeHtml(item)}"${filters.category === item ? " selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select></label>
    <label class="field"><span>Trạng thái cài đặt</span><select data-model-installed><option value="all"${(filters.installed || "all") === "all" ? " selected" : ""}>Tất cả</option><option value="installed"${filters.installed === "installed" ? " selected" : ""}>Đã cài đặt</option><option value="uninstalled"${filters.installed === "uninstalled" ? " selected" : ""}>Chưa cài đặt</option></select></label>
    <span class="row-meta" data-model-count>Hiển thị ${visibleProduction.length}/${production.length} model</span>
  </div>${stateActionStatus(filters, escapeHtml)}`;
  const updateRows = Array.isArray(updateCenter.records) ? updateCenter.records : [];
  const updateRowHtml = updateRows.slice(0, 8).map((item) => {
    const id = escapeHtml(item.component_id || "component");
    const actions = `<button class="button button--compact" type="button" data-check-update="${id}">${uiTextHtml("Check")}</button>${item.status === "UPDATE_AVAILABLE" ? `<button class="button button--compact button--accent" type="button" data-plan-update="${id}">${uiTextHtml("Plan Update")}</button>` : ""}${item.rollback_available ? `<button class="button button--compact" type="button" data-rollback-update="${id}">${uiTextHtml("Roll Back")}</button>` : ""}`;
    return `<div class="row-item"><span>${id} · ${escapeHtml((item.changed_parts || []).join(", ") || uiTextHtml("no changed parts"))}</span><span>${escapeHtml(item.status || "CHECK_FAILED")} ${actions}</span></div>`;
  }).join("");
  const updateCardBody = `<div class="form-actions"><button class="button button--compact" type="button" data-check-all-updates>${uiTextHtml("Check All Updates")}</button><span class="small">Lịch hiện tại: ${escapeHtml(updateCenter.settings?.policy || "manual")}; không tự cài.</span></div>${updateRows.length ? `<div class="row-list">${updateRowHtml}</div>` : `<p class="small">${uiTextHtml("No update report yet. Check on demand; no 24/7 polling.")}</p>`}`;
  const updateCard = card("Update Center", updateCardBody, "", "card--wide");
  const storageAreaValue = (value) => {
    const size = escapeHtml(formatGb(value?.bytes));
    const partial = value?.complete === false || value?.status === "partial";
    return partial ? `${uiTextHtml("At least")} ${size} ${uiTextHtml("— not fully scanned")}` : size;
  };
  const scan = storage?.scan && typeof storage.scan === "object" ? storage.scan : {};
  const scanStatus = String(scan.status || storage?.status || "idle");
  const scanProgress = Math.max(0, Math.min(100, Number.isFinite(Number(scan.progress)) ? Number(scan.progress) : scanStatus === "completed" ? 100 : 0));
  const scanMode = String(scan.mode || storage?.scan_mode || "fast");
  const scanRunning = scanStatus === "running" || scanStatus === "cancelling";
  const scanCanCancel = scanRunning && scanMode === "deep_exact";
  const scanPollingLimited = scan.polling_limited === true;
  const scanSavedAt = typeof scan.saved_at === "string" && scan.saved_at.trim() ? scan.saved_at.trim() : "";
  const scanLabel = scanPollingLimited
    ? "Quét vẫn đang chạy nền"
    : scanStatus === "running"
    ? `${scanMode === "deep_exact" ? "Đang tính chính xác" : "Đang quét nhanh"} · ${scanProgress}%${scan.current_area ? ` · ${escapeHtml(scan.current_area)}` : ""}`
    : scanStatus === "cancelling"
      ? "Đang hủy quét …"
    : scanStatus === "completed" && scan.exact === true
      ? (scanSavedAt ? `Chính xác tại ${escapeHtml(scanSavedAt)}` : "Đã quét xong · tổng chính xác")
      : scanStatus === "partial"
        ? "Đã quét trong giới hạn · tổng tối thiểu"
        : scanStatus === "unavailable"
          ? "Quét storage chưa khả dụng"
          : "Chưa có lần quét storage";
  const countedBytes = Number(scan.total_bytes_counted ?? scan.bytes_counted ?? 0);
  const countedFiles = Number(scan.files_scanned ?? 0);
  const scanActions = [];
  if (scanPollingLimited && scanRunning) scanActions.push(`<button class="button" type="button" data-resume-storage-polling="${escapeHtml(scan.scan_id || "")}">Theo dõi tiếp</button>`);
  if (scanCanCancel) scanActions.push(`<button class="button button--danger" type="button" data-cancel-storage-scan="${escapeHtml(scan.scan_id || "")}">Hủy quét</button>`);
  if (!scanActions.length) scanActions.push(`<button class="button" type="button" data-refresh-storage ${scanStatus === "cancelling" ? "disabled" : ""}>Quét lại</button>`);
  const scanAction = scanActions.join("");
  const exactLabel = scan.exact === true
    ? (scanSavedAt ? `Chính xác tại ${escapeHtml(scanSavedAt)}` : "Tổng managed chính xác")
    : scanMode === "deep_exact" ? "Đang tính — chưa xác nhận tổng" : "Ước tính nhanh — chưa quét toàn bộ";
  const scanBanner = `<div class="storage-scan-status" data-storage-scan-status="${escapeHtml(scanStatus)}" data-storage-scan-mode="${escapeHtml(scanMode)}" data-storage-scan-progress="${escapeHtml(String(scanProgress))}" role="status"><div class="card-title-row"><strong>${scanLabel}</strong><span>${exactLabel}</span></div><div class="storage-scan-live"><strong data-storage-total-bytes>${escapeHtml(formatGb(countedBytes))}</strong><span data-storage-total-bytes-raw>${escapeHtml(String(countedBytes))} bytes</span><span data-storage-files-scanned>${escapeHtml(String(countedFiles))} tệp đã đếm</span><span data-storage-current-area>${escapeHtml(scan.current_area || "")}</span></div><div class="progress-track" role="progressbar" aria-label="Tiến độ quét storage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${scanProgress}"><div class="progress-bar" style="width:${scanProgress}%"></div></div><small data-storage-polling-notice${scanPollingLimited ? "" : " hidden"}>${scanPollingLimited ? escapeHtml(scan.polling_message || "Quét vẫn đang chạy nền; bấm Theo dõi tiếp để cập nhật.") : ""}</small><small data-storage-scan-saved-at${scanSavedAt ? "" : " hidden"}>${scanSavedAt ? `Chính xác tại ${escapeHtml(scanSavedAt)}; bấm Quét lại sau khi filesystem thay đổi.` : ""}</small><small data-storage-scan-reason>${escapeHtml(scan.reason || storage?.reason || "Số liệu nhanh chỉ là ước tính; bấm Quét lại để tính toàn bộ.")}</small></div>`;
  const areaRows = areas.map(([name, value]) => {
    const reparse = Number(value?.reparse_entries || 0);
    const unreadable = Number(value?.unreadable_entries || 0);
    const entries = Number(value?.entries_scanned || 0);
    const files = Number(value?.files_scanned || 0);
    const stateLabel = value?.deduplicated === true ? "đã gộp trùng" : value?.complete === true ? "đã hoàn tất" : "chưa hoàn tất";
    const detail = `${entries} mục · ${files} tệp · ${reparse} reparse · ${unreadable} không đọc được · ${stateLabel}`;
    return `<div class="row-item" data-storage-area="${escapeHtml(name)}"><span>${escapeHtml(name)}</span><strong data-storage-area-value>${storageAreaValue(value)}</strong><small data-storage-area-count>${escapeHtml(detail)}</small></div>`;
  }).join("");
  return heading("STORAGE", "Models & Storage", "Model store canonical không nhân bản. Legacy cleanup chỉ xử lý mục đã phân loại và xác minh, không tự xoá UNKNOWN hoặc user media.", `<span data-storage-scan-action>${scanAction}</span>`) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Dung lượng", scanBanner + `<div class="row-list" data-storage-area-list>${areaRows || `<div class="empty-state compact">Đang chờ snapshot storage.</div>`}</div>`)}
      ${card("Legacy cleanup", `<div class="metric-inline"><strong>${escapeHtml(storage?.legacy_counts?.total || 0)}</strong><span>${uiTextHtml("legacy paths inventoried")}</span></div><div class="callout callout--warning">${uiTextHtml("Cleanup V3 separates REAL_DIRECTORY/JUNCTION, checks references and user data first. Active or unknown items are retained with reason/rollback.")}</div>`, "", "card--flat")}
    </div>
    ${inventoryCard}${modelV2Panel}${card("AI Models & Components", controls + table, "", "card--wide")}${updateCard}`;
}

function stateActionStatus(filters, escapeHtml) {
  const message = typeof filters.actionStatus === "string" ? filters.actionStatus.trim().slice(0, 240) : "";
  return message ? `<div class="form-result" data-model-action-status role="status" aria-live="polite">${escapeHtml(message)}</div>` : `<div class="form-result" data-model-action-status role="status" aria-live="polite"></div>`;
}

export const modelsFeature = Object.freeze({ id: "models", refreshPolicy: "manual", renderer: "renderProductionModels" });
