/* Models feature renderer.  This module owns catalog-card/table composition;
 * pages.js keeps only the compatibility wrapper for older routes. */
const MODEL_STATUS_INSTALLED = new Set(["INSTALLED", "INSTALLED_UNVERIFIED", "OPERATIONAL"]);

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
  const areas = Object.entries(storage?.areas || {});
  const catalogRows = visibleProduction.map((item) => {
    const status = String(item.status || "UNAVAILABLE").toLowerCase();
    const disposition = String(item.disposition || "MANUAL_IMPORT_ONLY");
    const action = disposition === "AUTO_INSTALL_READY" ? "Download & Install" : disposition === "AUTH_REQUIRED" ? "Authorize & Install" : disposition === "LICENSE_REQUIRED" ? "Review License" : disposition === "MANUAL_IMPORT_ONLY" ? "Import Model" : "Manual Review";
    const sourceStatus = String(item.source_availability?.status || "UNKNOWN");
    return `<tr><td><strong>${escapeHtml(item.display_name || item.model_id)}</strong><br><small>${escapeHtml(item.model_id || "")}</small></td><td>${escapeHtml(item.category || "Other")}</td><td>${escapeHtml(item.size_label || (item.expected_download_size_bytes ? `Download: ${item.expected_download_size_bytes} bytes` : "Size unavailable"))}</td><td>${statusPill(status)}<br><small>Nguồn: ${escapeHtml(sourceStatus)}</small><br><small>${uiTextHtml(action)}</small></td><td><button class="button button--compact" type="button" data-product-plan="${escapeHtml(item.model_id || "")}">${uiTextHtml(action)}</button><button class="button button--compact" type="button" data-check-update="${escapeHtml(item.model_id || "")}">${uiTextHtml("Check Update")}</button></td></tr>`;
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
  const scanLabel = scanStatus === "running"
    ? `${scanMode === "deep_exact" ? "Đang tính chính xác" : "Đang quét nhanh"} · ${scanProgress}%${scan.current_area ? ` · ${escapeHtml(scan.current_area)}` : ""}`
    : scanStatus === "cancelling"
      ? "Đang hủy quét …"
    : scanStatus === "completed" && scan.exact === true
      ? "Đã quét xong · tổng chính xác"
      : scanStatus === "partial"
        ? "Đã quét trong giới hạn · tổng tối thiểu"
        : scanStatus === "unavailable"
          ? "Quét storage chưa khả dụng"
          : "Chưa có lần quét storage";
  const countedBytes = Number(scan.total_bytes_counted ?? scan.bytes_counted ?? 0);
  const countedFiles = Number(scan.files_scanned ?? 0);
  const scanAction = scanCanCancel
    ? `<button class="button button--danger" type="button" data-cancel-storage-scan="${escapeHtml(scan.scan_id || "")}">Hủy quét</button>`
    : `<button class="button" type="button" data-refresh-storage ${scanStatus === "cancelling" ? "disabled" : ""}>Quét lại</button>`;
  const scanBanner = `<div class="storage-scan-status" data-storage-scan-status="${escapeHtml(scanStatus)}" data-storage-scan-mode="${escapeHtml(scanMode)}" data-storage-scan-progress="${escapeHtml(String(scanProgress))}" role="status"><div class="card-title-row"><strong>${scanLabel}</strong><span>${scan.exact === true ? "chính xác" : "đang đếm"}</span></div><div class="storage-scan-live"><strong data-storage-total-bytes>${escapeHtml(formatGb(countedBytes))}</strong><span data-storage-total-bytes-raw>${escapeHtml(String(countedBytes))} bytes</span><span data-storage-files-scanned>${escapeHtml(String(countedFiles))} tệp đã đếm</span><span data-storage-current-area>${escapeHtml(scan.current_area || "")}</span></div><div class="progress-track" role="progressbar" aria-label="Tiến độ quét storage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${scanProgress}"><div class="progress-bar" style="width:${scanProgress}%"></div></div><small data-storage-scan-reason>${escapeHtml(scan.reason || storage?.reason || "Số liệu được đọc theo ngân sách giới hạn; không quét vô hạn.")}</small></div>`;
  return heading("STORAGE", "Models & Storage", "Model store canonical không nhân bản. Legacy cleanup chỉ xử lý mục đã phân loại và xác minh, không tự xoá UNKNOWN hoặc user media.", `<span data-storage-scan-action>${scanAction}</span>`) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Dung lượng", scanBanner + `<div class="row-list" data-storage-area-list>${areas.map(([name, value]) => `<div class="row-item" data-storage-area="${escapeHtml(name)}"><span>${escapeHtml(name)}</span><strong data-storage-area-value>${storageAreaValue(value)}</strong><small data-storage-area-count>${escapeHtml(String(value?.files_scanned ?? value?.entries_scanned ?? 0))} tệp</small></div>`).join("") || `<div class="empty-state compact">Đang chờ snapshot storage.</div>`}</div>`)}
      ${card("Legacy cleanup", `<div class="metric-inline"><strong>${escapeHtml(storage?.legacy_counts?.total || 0)}</strong><span>${uiTextHtml("legacy paths inventoried")}</span></div><div class="callout callout--warning">${uiTextHtml("Cleanup V3 separates REAL_DIRECTORY/JUNCTION, checks references and user data first. Active or unknown items are retained with reason/rollback.")}</div>`, "", "card--flat")}
    </div>
    ${card("AI Models & Components", controls + table, "", "card--wide")}${updateCard}`;
}

function stateActionStatus(filters, escapeHtml) {
  const message = typeof filters.actionStatus === "string" ? filters.actionStatus.trim().slice(0, 240) : "";
  return message ? `<div class="form-result" data-model-action-status role="status" aria-live="polite">${escapeHtml(message)}</div>` : `<div class="form-result" data-model-action-status role="status" aria-live="polite"></div>`;
}

export const modelsFeature = Object.freeze({ id: "models", refreshPolicy: "manual", renderer: "renderProductionModels" });
