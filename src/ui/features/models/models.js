/* Models feature renderer. This module owns the storage summary and the single
 * Model Manager V2 shell; pages.js keeps the compatibility wrapper. */
const inventoryCount = (value, fallback = 0) => Number.isInteger(value) && value >= 0 ? value : fallback;
const UNSAFE_STORAGE_TEXT = /(?:[a-z]:[\\/]|\\\\|(?:file|data|https?):|(?:api[_-]?key|password|secret|token)\s*[:=])/i;
const safeStorageText = (value, fallback = "") => {
  const candidate = typeof value === "string" ? value.trim().slice(0, 240) : "";
  return candidate && !UNSAFE_STORAGE_TEXT.test(candidate) ? candidate : fallback;
};
const storageCount = (value, fallback = 0) => Number.isSafeInteger(value) && value >= 0 ? value : fallback;

const MODEL_MANAGER_CATEGORIES = Object.freeze(["Image", "Video", "Audio", "Vision", "LLM", "Utility"]);

const normalizedModelFilters = (filters = {}) => ({
  query: String(filters.query || "").trim().slice(0, 80),
  category: MODEL_MANAGER_CATEGORIES.includes(String(filters.category || "")) ? String(filters.category) : "",
  installed: ["all", "installed", "uninstalled"].includes(String(filters.installed || "all")) ? String(filters.installed || "all") : "all",
});

const modelFilterControls = (filters, { escapeHtml, uiTextHtml }) => {
  const current = normalizedModelFilters(filters);
  return `<div class="model-manager-v2-filters" data-model-filters role="search" aria-label="Lọc model catalog">
    <label class="field"><span>${uiTextHtml("Model")}</span><input type="search" data-model-search value="${escapeHtml(current.query)}" placeholder="Tên, ID hoặc module" autocomplete="off" /></label>
    <label class="field"><span>${uiTextHtml("Category")}</span><select data-model-category><option value="">Tất cả danh mục</option>${MODEL_MANAGER_CATEGORIES.map((item) => `<option value="${escapeHtml(item)}"${current.category === item ? " selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select></label>
    <label class="field"><span>Trạng thái cài đặt</span><select data-model-installed><option value="all"${current.installed === "all" ? " selected" : ""}>Tất cả</option><option value="installed"${current.installed === "installed" ? " selected" : ""}>Đã cài đặt</option><option value="uninstalled"${current.installed === "uninstalled" ? " selected" : ""}>Chưa cài đặt</option></select></label>
    <span class="row-meta" data-model-count data-model-v2-count>Đang đọc số model…</span>
  </div>${stateActionStatus(filters, escapeHtml)}`;
};

export function renderProductionModels({ productionCatalog, storage, updateCenter = {}, filters = {}, escapeHtml, formatGb, statusPill, card, heading, uiTextHtml }) {
  const production = Array.isArray(productionCatalog?.models) ? productionCatalog.models : [];
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
  const modelV2Panel = `<section class="card" data-model-manager-v2 aria-live="polite"><div class="card-title-row"><div><span class="eyebrow">MODEL MANAGER V2</span><h2>${uiTextHtml("Trạng thái, tương thích & chống trùng lặp")}</h2><p class="small">${uiTextHtml("Chỉ metadata/evidence giới hạn. Lập kế hoạch không tự tải, nạp, chạy hoặc xóa model.")}</p></div><button class="button button--compact" type="button" data-model-manager-v2-refresh>${uiTextHtml("Làm mới Model Manager")}</button></div>${modelFilterControls(filters, { escapeHtml, uiTextHtml })}<div data-model-manager-v2-status class="small">${uiTextHtml("Đang đọc Model Manager V2…")}</div><div data-model-manager-v2-list></div></section>`;
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
  const disk = storage?.disk && typeof storage.disk === "object" ? storage.disk : {};
  const areaTotal = areas.reduce((total, [, value]) => total + storageCount(value?.bytes), 0);
  const areaCount = (key) => areas.reduce((total, [, value]) => total + storageCount(value?.[key]), 0);
  const scanValue = (keys, fallback = 0) => {
    for (const key of keys) {
      if (Number.isSafeInteger(scan?.[key]) && scan[key] >= 0) return scan[key];
      if (Number.isSafeInteger(storage?.[key]) && storage[key] >= 0) return storage[key];
    }
    return fallback;
  };
  const diskFree = Number.isSafeInteger(disk.free_bytes) && disk.free_bytes >= 0 ? disk.free_bytes : null;
  const ownedTotal = scanValue(["owned_storage_total_bytes", "total_bytes_counted"], areaTotal);
  const entriesScanned = scanValue(["entries_scanned"], areaCount("entries_scanned"));
  const filesScanned = scanValue(["files_scanned"], areaCount("files_scanned"));
  const directoriesScanned = scanValue(["directories_scanned"], areaCount("directories_scanned"));
  const reparseEntries = scanValue(["reparse_entries"], areaCount("reparse_entries"));
  const unreadableEntries = scanValue(["unreadable_entries"], areaCount("unreadable_entries"));
  const completedRoots = scanValue(["completed_roots"], areas.filter(([, value]) => value?.complete === true).length);
  const totalRoots = scanValue(["total_roots"], areas.length);
  const currentRoot = safeStorageText(scan.current_root || scan.current_area, "");
  const scanReason = safeStorageText(scan.reason || storage?.reason, "Số liệu scan server-owned đang được cập nhật.");
  const scanNextAction = safeStorageText(scan.next_action || storage?.next_action, "Bấm Quét chính xác để xác nhận lại tổng managed sau khi filesystem thay đổi.");
  const scanStatus = String(scan.status || storage?.status || "idle");
  const scanProgress = Math.max(0, Math.min(100, Number.isFinite(Number(scan.progress)) ? Number(scan.progress) : scanStatus === "completed" ? 100 : 0));
  const scanMode = String(scan.mode || storage?.scan_mode || "fast");
  const scanRunning = scanStatus === "running" || scanStatus === "cancelling";
  const scanCanCancel = scanRunning && scanMode === "deep_exact";
  const scanPollingLimited = scan.polling_limited === true;
  const scanSavedAt = safeStorageText(scan.saved_at, "");
  const scanLabel = scanPollingLimited
    ? "Quét vẫn đang chạy nền"
    : scanStatus === "running"
    ? `${scanMode === "deep_exact" ? "Quét chính xác · đang tính" : "Đang quét nhanh"} · ${scanProgress}%${currentRoot ? ` · ${escapeHtml(currentRoot)}` : ""}`
    : scanStatus === "cancelling"
      ? "Đang hủy quét …"
    : scanStatus === "completed" && scan.exact === true
      ? (scanSavedAt ? `Chính xác tại ${escapeHtml(scanSavedAt)}` : "Đã quét xong · tổng chính xác")
      : scanStatus === "partial"
        ? "Đã quét trong giới hạn · tổng tối thiểu"
        : scanStatus === "unavailable"
          ? "Quét storage chưa khả dụng"
          : "Chưa có lần quét storage";
  const countedBytes = ownedTotal;
  const countedFiles = filesScanned;
  const scanActions = [];
  if (scanPollingLimited && scanRunning) scanActions.push(`<button class="button" type="button" data-resume-storage-polling="${escapeHtml(scan.scan_id || "")}">Theo dõi tiếp</button>`);
  if (scanCanCancel) scanActions.push(`<button class="button button--danger" type="button" data-cancel-storage-scan="${escapeHtml(scan.scan_id || "")}">Hủy quét</button>`);
  if (!scanActions.length) scanActions.push(`<button class="button" type="button" data-refresh-storage ${scanStatus === "cancelling" ? "disabled" : ""}>Quét chính xác</button>`);
  const scanAction = scanActions.join("");
  const exactLabel = scan.exact === true
    ? (scanSavedAt ? `Chính xác tại ${escapeHtml(scanSavedAt)}` : "Tổng managed chính xác")
    : scanMode === "deep_exact" ? "Đang tính — chưa xác nhận tổng" : "Ước tính nhanh — chưa quét toàn bộ";
  const pollingMessage = safeStorageText(scan.polling_message, "Quét vẫn đang chạy nền; bấm Theo dõi tiếp để cập nhật.");
  const scanBanner = `<div class="storage-scan-status" data-storage-scan-status="${escapeHtml(scanStatus)}" data-storage-scan-mode="${escapeHtml(scanMode)}" data-storage-scan-progress="${escapeHtml(String(scanProgress))}" role="status"><div class="card-title-row"><strong>${scanLabel}</strong><span>${exactLabel}</span></div><div class="storage-scan-live"><div data-storage-owned-total="${escapeHtml(String(countedBytes))}"><strong>${escapeHtml(formatGb(countedBytes))}</strong><span>${uiTextHtml("Tổng managed đã đếm")}</span></div><div data-storage-disk-free="${escapeHtml(diskFree === null ? "" : String(diskFree))}"><strong>${escapeHtml(diskFree === null ? "—" : formatGb(diskFree))}</strong><span>${uiTextHtml("Dung lượng trống trên đĩa")}</span></div><span data-storage-total-bytes>${escapeHtml(formatGb(countedBytes))}</span><span data-storage-total-bytes-raw>${escapeHtml(String(countedBytes))} bytes</span><span data-storage-files-scanned="${escapeHtml(String(countedFiles))}">${escapeHtml(String(countedFiles))} tệp đã đếm</span><span data-storage-current-area data-storage-current-root>${escapeHtml(currentRoot)}</span></div><div class="storage-scan-counts"><span data-storage-entries-scanned="${escapeHtml(String(entriesScanned))}">${escapeHtml(String(entriesScanned))} mục</span><span data-storage-directories-scanned="${escapeHtml(String(directoriesScanned))}">${escapeHtml(String(directoriesScanned))} thư mục</span><span data-storage-reparse-entries="${escapeHtml(String(reparseEntries))}">${escapeHtml(String(reparseEntries))} reparse bỏ qua</span><span data-storage-unreadable-entries="${escapeHtml(String(unreadableEntries))}">${escapeHtml(String(unreadableEntries))} không đọc được</span><span data-storage-completed-roots="${escapeHtml(String(completedRoots))}">${escapeHtml(String(completedRoots))}/${escapeHtml(String(totalRoots))} root hoàn tất</span></div><div class="progress-track" role="progressbar" aria-label="Tiến độ quét storage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${scanProgress}"><div class="progress-bar" style="width:${scanProgress}%"></div></div><small data-storage-polling-notice${scanPollingLimited ? "" : " hidden"}>${scanPollingLimited ? escapeHtml(pollingMessage) : ""}</small><small data-storage-scan-saved-at${scanSavedAt ? "" : " hidden"}>${scanSavedAt ? `Chính xác tại ${escapeHtml(scanSavedAt)}; bấm Quét chính xác sau khi filesystem thay đổi.` : ""}</small><small data-storage-scan-reason>${escapeHtml(scanReason)}</small><small data-storage-scan-next-action>${escapeHtml(scanNextAction)}</small></div>`;
  const areaRows = areas.map(([name, value]) => {
    const displayName = safeStorageText(name, "Managed area");
    const reparse = Number(value?.reparse_entries || 0);
    const unreadable = Number(value?.unreadable_entries || 0);
    const entries = Number(value?.entries_scanned || 0);
    const files = Number(value?.files_scanned || 0);
    const stateLabel = value?.deduplicated === true ? "đã gộp trùng" : value?.complete === true ? "đã hoàn tất" : "chưa hoàn tất";
    const detail = `${entries} mục · ${files} tệp · ${reparse} reparse · ${unreadable} không đọc được · ${stateLabel}`;
    return `<div class="row-item" data-storage-area="${escapeHtml(displayName)}"><span>${escapeHtml(displayName)}</span><strong data-storage-area-value>${storageAreaValue(value)}</strong><small data-storage-area-count>${escapeHtml(detail)}</small></div>`;
  }).join("");
  return heading("STORAGE", "Models & Storage", "Model store canonical không nhân bản. Legacy cleanup chỉ xử lý mục đã phân loại và xác minh, không tự xoá UNKNOWN hoặc user media.", `<span data-storage-scan-action>${scanAction}</span>`) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Dung lượng", scanBanner + `<div class="row-list" data-storage-area-list>${areaRows || `<div class="empty-state compact">Đang chờ snapshot storage.</div>`}</div>`)}
      ${card("Legacy cleanup", `<div class="metric-inline"><strong>${escapeHtml(storage?.legacy_counts?.total || 0)}</strong><span>${uiTextHtml("legacy paths inventoried")}</span></div><div class="callout callout--warning">${uiTextHtml("Cleanup V3 separates REAL_DIRECTORY/JUNCTION, checks references and user data first. Active or unknown items are retained with reason/rollback.")}</div>`, "", "card--flat")}
    </div>
    ${inventoryCard}${modelV2Panel}${updateCard}`;
}

function stateActionStatus(filters, escapeHtml) {
  const message = typeof filters.actionStatus === "string" ? filters.actionStatus.trim().slice(0, 240) : "";
  return message ? `<div class="form-result" data-model-action-status role="status" aria-live="polite">${escapeHtml(message)}</div>` : `<div class="form-result" data-model-action-status role="status" aria-live="polite"></div>`;
}

export const modelsFeature = Object.freeze({ id: "models", refreshPolicy: "manual", renderer: "renderProductionModels" });
