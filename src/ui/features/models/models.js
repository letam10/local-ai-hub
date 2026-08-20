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

export function renderProductionModels({ productionCatalog, legacyModels, storage, filters = {}, escapeHtml, formatGb, statusPill, card, heading }) {
  const production = Array.isArray(productionCatalog?.models) ? productionCatalog.models : [];
  const visibleProduction = filterCatalogModels(production, filters);
  const categories = [...new Set(production.map((item) => String(item.category || "Other")))].sort((a, b) => a.localeCompare(b));
  const legacy = Array.isArray(legacyModels) ? legacyModels : [];
  const areas = Object.entries(storage?.areas || {});
  const catalogRows = visibleProduction.map((item) => {
    const status = String(item.status || "UNAVAILABLE").toLowerCase();
    const disposition = String(item.disposition || "MANUAL_IMPORT_ONLY");
    const action = disposition === "AUTO_INSTALL_READY" ? "Download & Install" : disposition === "AUTH_REQUIRED" ? "Authorize & Install" : disposition === "LICENSE_REQUIRED" ? "Review License" : disposition === "MANUAL_IMPORT_ONLY" ? "Import Model" : "Manual Review";
    return `<tr><td><strong>${escapeHtml(item.display_name || item.model_id)}</strong><br><small>${escapeHtml(item.model_id || "")}</small></td><td>${escapeHtml(item.category || "Other")}</td><td>${escapeHtml(item.size_label || (item.expected_download_size_bytes ? `Download: ${item.expected_download_size_bytes} bytes` : "Size unavailable"))}</td><td>${statusPill(status)}<br><small>${escapeHtml(action)}</small></td><td><button class="button button--compact" type="button" data-product-plan="${escapeHtml(item.model_id || "")}">${escapeHtml(action)}</button></td></tr>`;
  }).join("");
  const legacyRows = legacy.map((item) => `<tr><td>${escapeHtml(item.model_name)}</td><td>${escapeHtml(item.engine)}</td><td>${formatGb(item.size?.bytes)}</td><td>${statusPill(item.installed ? "installed" : "not_installed")}</td></tr>`).join("");
  const table = catalogRows ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th>Category</th><th>Size</th><th>Status / action</th><th></th></tr></thead><tbody>${catalogRows}</tbody></table></div>` : legacyRows ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th>Engine</th><th>Size</th><th>Status</th></tr></thead><tbody>${legacyRows}</tbody></table></div>` : `<div class="empty-state compact">Chưa có model catalog.</div>`;
  const controls = `<div class="model-catalog-controls" data-model-filters role="search" aria-label="Lọc model catalog">
    <label class="field"><span>Tìm model</span><input type="search" data-model-search value="${escapeHtml(filters.query || "")}" placeholder="Tên, ID hoặc module" autocomplete="off" /></label>
    <label class="field"><span>Danh mục</span><select data-model-category><option value="">Tất cả danh mục</option>${categories.map((item) => `<option value="${escapeHtml(item)}"${filters.category === item ? " selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select></label>
    <label class="field"><span>Trạng thái cài đặt</span><select data-model-installed><option value="all"${(filters.installed || "all") === "all" ? " selected" : ""}>Tất cả</option><option value="installed"${filters.installed === "installed" ? " selected" : ""}>Đã cài đặt</option><option value="uninstalled"${filters.installed === "uninstalled" ? " selected" : ""}>Chưa cài đặt</option></select></label>
    <span class="row-meta" data-model-count>Hiển thị ${visibleProduction.length}/${production.length} model</span>
  </div>`;
  return heading("STORAGE", "Models & Storage", "Model store canonical không nhân bản. Legacy cleanup chỉ xử lý mục đã phân loại và xác minh, không tự xoá UNKNOWN hoặc user media.", `<button class="button" type="button" data-refresh-storage>Quét lại</button>`) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Dung lượng", `<div class="row-list">${areas.map(([name, value]) => `<div class="row-item"><span>${escapeHtml(name)}</span><strong>${formatGb(value.bytes)}</strong></div>`).join("") || `<div class="empty-state compact">Chưa có số liệu storage.</div>`}</div>`)}
      ${card("Legacy cleanup", `<div class="metric-inline"><strong>${escapeHtml(storage?.legacy_counts?.total || 0)}</strong><span>legacy paths đã inventory</span></div><div class="callout callout--warning">Cleanup V3 tách REAL_DIRECTORY/JUNCTION, kiểm tra reference và user data trước. Mục active hoặc unknown sẽ được giữ cùng lý do/rollback.</div>`, "", "card--flat")}
    </div>
    ${card("AI Models & Components", controls + table, "", "card--wide")}`;
}

export const modelsFeature = Object.freeze({ id: "models", refreshPolicy: "manual", renderer: "renderProductionModels" });
