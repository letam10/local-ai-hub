/* Models feature renderer.  This module owns catalog-card/table composition;
 * pages.js keeps only the compatibility wrapper for older routes. */
export function renderProductionModels({ productionCatalog, legacyModels, storage, escapeHtml, formatGb, statusPill, card, heading }) {
  const production = Array.isArray(productionCatalog?.models) ? productionCatalog.models : [];
  const legacy = Array.isArray(legacyModels) ? legacyModels : [];
  const areas = Object.entries(storage?.areas || {});
  const catalogRows = production.map((item) => {
    const status = String(item.status || "UNAVAILABLE").toLowerCase();
    const disposition = String(item.disposition || "MANUAL_IMPORT_ONLY");
    const action = disposition === "AUTO_INSTALL_READY" ? "Download & Install" : disposition === "AUTH_REQUIRED" ? "Authorize & Install" : disposition === "LICENSE_REQUIRED" ? "Review License" : disposition === "MANUAL_IMPORT_ONLY" ? "Import Model" : "Manual Review";
    return `<tr><td><strong>${escapeHtml(item.display_name || item.model_id)}</strong><br><small>${escapeHtml(item.model_id || "")}</small></td><td>${escapeHtml(item.category || "Other")}</td><td>${escapeHtml(item.size_label || (item.expected_download_size_bytes ? `Download: ${item.expected_download_size_bytes} bytes` : "Size unavailable"))}</td><td>${statusPill(status)}<br><small>${escapeHtml(action)}</small></td><td><button class="button button--compact" type="button" data-product-plan="${escapeHtml(item.model_id || "")}">${escapeHtml(action)}</button></td></tr>`;
  }).join("");
  const legacyRows = legacy.map((item) => `<tr><td>${escapeHtml(item.model_name)}</td><td>${escapeHtml(item.engine)}</td><td>${formatGb(item.size?.bytes)}</td><td>${statusPill(item.installed ? "installed" : "not_installed")}</td></tr>`).join("");
  const table = catalogRows ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th>Category</th><th>Size</th><th>Status / action</th><th></th></tr></thead><tbody>${catalogRows}</tbody></table></div>` : legacyRows ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th>Engine</th><th>Size</th><th>Status</th></tr></thead><tbody>${legacyRows}</tbody></table></div>` : `<div class="empty-state compact">Chưa có model catalog.</div>`;
  return heading("STORAGE", "Models & Storage", "Model store canonical không nhân bản. Legacy cleanup chỉ xử lý mục đã phân loại và xác minh, không tự xoá UNKNOWN hoặc user media.", `<button class="button" type="button" data-refresh-storage>Quét lại</button>`) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Dung lượng", `<div class="row-list">${areas.map(([name, value]) => `<div class="row-item"><span>${escapeHtml(name)}</span><strong>${formatGb(value.bytes)}</strong></div>`).join("") || `<div class="empty-state compact">Chưa có số liệu storage.</div>`}</div>`)}
      ${card("Legacy cleanup", `<div class="metric-inline"><strong>${escapeHtml(storage?.legacy_counts?.total || 0)}</strong><span>legacy paths đã inventory</span></div><div class="callout callout--warning">Cleanup V3 tách REAL_DIRECTORY/JUNCTION, kiểm tra reference và user data trước. Mục active hoặc unknown sẽ được giữ cùng lý do/rollback.</div>`, "", "card--flat")}
    </div>
    ${card("AI Models & Components", table, "", "card--wide")}`;
}

export const modelsFeature = Object.freeze({ id: "models", refreshPolicy: "manual", renderer: "renderProductionModels" });
