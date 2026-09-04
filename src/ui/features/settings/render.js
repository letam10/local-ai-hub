export function createSettingsRenderer(deps) {
  const { heading, card, escapeHtml, uiTextHtml, statusPill, readinessStatusLabel, readinessSnapshot, readinessModuleDetails, readinessResourceDetails, readinessStorageDetails, mediaEvidencePanel } = deps;
  return function renderSettings(state) {
  const settings = state.settings || {};
  const revision = Number(state.settings_revision ?? settings.settings_revision ?? 0);
  const recovery = state.settingsRecovery || settings.recovery || {};
  const snapshot = readinessSnapshot(state);
  const settingsDirty = state.settingsDirty === true;
  const recoveryBanner = recovery.status === "recovery_required" ? `
    <div class="callout callout--danger">
      <strong>Cảnh báo khôi phục cấu hình:</strong> ${escapeHtml(recovery.reason || "File settings bị lỗi.")}
      <p>${escapeHtml(recovery.action || "Vui lòng restore từ backup hoặc reset section.")}</p>
    </div>
  ` : "";
  const dirtyBanner = `<div class="settings-dirty-banner${settingsDirty ? " is-dirty" : ""}" data-settings-dirty="${settingsDirty}" role="status" aria-live="polite"><strong>${settingsDirty ? "Có thay đổi chưa áp dụng" : "Cài đặt đã đồng bộ"}</strong><span>${settingsDirty ? "Các giá trị chỉ có hiệu lực sau khi bạn bấm Áp dụng & lưu." : "Không có thay đổi cục bộ đang chờ."}</span></div>`;

  return heading("SYSTEM", "Settings", "Cấu hình startup, chính sách GPU, lưu trữ và sao lưu / khôi phục dữ liệu machine-local an toàn.") + `
    ${recoveryBanner}${dirtyBanner}
    <div class="settings-layout">
    <details class="advanced settings-advanced">
      <summary>Readiness & Module Plan · Chẩn đoán nâng cao</summary>
    <section class="readiness-page" aria-labelledby="readiness-page-title" data-readiness-source="server-snapshot">
      <section class="readiness-summary card" aria-labelledby="readiness-page-title" data-readiness-status="${escapeHtml(snapshot.status)}">
        <div class="card-title-row"><div><span class="eyebrow" data-i18n="SERVER SNAPSHOT">${uiTextHtml("SERVER SNAPSHOT")}</span><h2 id="readiness-page-title" data-i18n="Readiness & Module Plan">${uiTextHtml("Readiness & Module Plan")}</h2><p data-i18n="Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.">${uiTextHtml("Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.")}</p></div><div class="readiness-summary__pills">${statusPill(snapshot.status, readinessStatusLabel(snapshot.status))}<span class="tag">${escapeHtml(snapshot.resourceSnapshot)}</span></div></div>
        <div class="readiness-summary__metrics"><div><span data-i18n="Overall readiness">${uiTextHtml("Overall readiness")}</span><strong>${escapeHtml(readinessStatusLabel(snapshot.status))}</strong></div><div><span data-i18n="Module plan">${uiTextHtml("Module plan")}</span><strong>${escapeHtml(readinessStatusLabel(snapshot.planStatus))}</strong></div><div><span data-i18n="Modules">${uiTextHtml("Modules")}</span><strong>${escapeHtml(String(snapshot.modules.length))}</strong></div><div><span data-i18n="Execution">${uiTextHtml("Execution")}</span><strong>${escapeHtml(snapshot.execution)}</strong></div></div>
        <div class="readiness-guidance"><div><span data-i18n="Reason">${uiTextHtml("Reason")}</span><p>${escapeHtml(snapshot.reason)}</p></div><div><span data-i18n="Next action">${uiTextHtml("Next action")}</span><p>${escapeHtml(snapshot.nextAction)}</p></div></div>
        <div class="readiness-safety"><span class="tag" data-i18n="dry_run:">${uiTextHtml("dry_run:")} ${snapshot.dryRun ? "true" : "false"}</span><span class="tag" data-i18n="Install / repair / uninstall: explanatory only">${uiTextHtml("Install / repair / uninstall: explanatory only")}</span></div>
      </section>
      <section class="readiness-modules card" aria-labelledby="readiness-modules-title"><div class="card-title-row"><div><span class="eyebrow" data-i18n="MODULE EVIDENCE">${uiTextHtml("MODULE EVIDENCE")}</span><h2 id="readiness-modules-title" data-i18n="Safe module projection">${uiTextHtml("Safe module projection")}</h2><p data-i18n="Rows are limited to server-projected id, provider, component, status, version, reason and next action.">${uiTextHtml("Rows are limited to server-projected id, provider, component, status, version, reason and next action.")}</p></div><span class="tag">${escapeHtml(String(snapshot.modules.length))} rows</span></div><div class="readiness-module-list" role="list">${readinessModuleDetails(snapshot.modules)}</div></section>
      <div class="workspace-grid workspace-grid--two readiness-detail-grid">${readinessResourceDetails(snapshot.resourcePlan)}${readinessStorageDetails(snapshot.volumes)}</div>
      ${mediaEvidencePanel(state, "settings")}
    </section>
    </details>
    <div class="workspace-grid workspace-grid--two">
      ${card("Giao diện & Khởi động", `
        <div class="row-list">
          <div class="row-item">
            <span><span>Ngôn ngữ</span><small class="setting-effect">Áp dụng ngay sau khi bấm Áp dụng & lưu.</small></span>
            <select id="settings-lang" class="input input--select" data-setting-key="ui.language">
              <option value="vi" ${settings.language === "vi" ? "selected" : ""}>Tiếng Việt (vi)</option>
              <option value="en" ${settings.language === "en" ? "selected" : ""}>English (en)</option>
              <option value="zh" ${settings.language === "zh" ? "selected" : ""}>简体中文 (zh)</option>
              <option value="ja" ${settings.language === "ja" ? "selected" : ""}>日本語 (ja)</option>
              <option value="ko" ${settings.language === "ko" ? "selected" : ""}>한국어 (ko)</option>
            </select>
          </div>
          <div class="row-item">
            <span><span>Giao diện</span><small class="setting-effect">Chỉ áp dụng sau khi bấm Áp dụng & lưu; thay đổi chưa lưu chưa đổi giao diện.</small></span>
            <select id="settings-theme" class="input input--select" data-setting-key="ui.theme">
              <option value="system" ${settings.theme === "system" ? "selected" : ""}>Theo hệ thống (system)</option>
              <option value="dark" ${settings.theme === "dark" ? "selected" : ""}>Tối (dark)</option>
              <option value="light" ${settings.theme === "light" ? "selected" : ""}>Sáng (light)</option>
            </select>
          </div>
          <div class="row-item">
            <span><span>Khởi động tối đa hóa</span><small class="setting-effect">Có hiệu lực từ lần mở app tiếp theo.</small></span>
            <input type="checkbox" id="settings-maximized" data-setting-key="window.start_maximized" ${settings.start_maximized ? "checked" : ""} />
          </div>
          <div class="row-item">
            <span><span>Chiều rộng tối thiểu (800..3840)</span><small class="setting-effect">Có hiệu lực ở lần khởi động kế tiếp.</small></span>
            <input type="number" id="settings-min-width" min="800" max="3840" class="input input--compact" data-setting-key="window.minimum_width" value="${escapeHtml(settings.minimum_width || 1280)}" />
          </div>
          <div class="row-item">
            <span><span>Chiều cao tối thiểu (600..2160)</span><small class="setting-effect">Có hiệu lực ở lần khởi động kế tiếp.</small></span>
            <input type="number" id="settings-min-height" min="600" max="2160" class="input input--compact" data-setting-key="window.minimum_height" value="${escapeHtml(settings.minimum_height || 720)}" />
          </div>
        </div>
        <div class="form-actions">
          <button class="button button--compact" type="button" data-reset-settings="ui">Đặt lại UI</button>
          <button class="button button--compact" type="button" data-reset-settings="window">Đặt lại Cửa sổ</button>
        </div>
      `)}
      ${card("Chính sách & Tài nguyên", `
        <div class="row-list">
          <div class="row-item">
            <span><span>Chính sách nạp Model</span><small class="setting-effect">Dùng cho các tác vụ mới; không tự nạp model lúc lưu.</small></span>
            <select id="settings-model-policy" class="input input--select" data-setting-key="jobs.model_load_policy">
              <option value="on_demand" ${settings.model_load_policy === "on_demand" ? "selected" : ""}>Nạp khi cần (on_demand)</option>
              <option value="keep_loaded" ${settings.model_load_policy === "keep_loaded" ? "selected" : ""}>Giữ trong VRAM (keep_loaded)</option>
            </select>
          </div>
          <div class="row-item">
            <span><span>Số job GPU nặng đồng thời</span><small class="setting-effect">Áp dụng cho các tác vụ mới; không chạy GPU lúc lưu.</small></span>
            <input type="number" id="settings-gpu-jobs" min="1" max="4" class="input input--compact" data-setting-key="jobs.max_heavy_gpu_jobs" value="${escapeHtml(settings.max_heavy_gpu_jobs || 1)}" />
          </div>
          <div class="row-item">
            <span>ComfyUI Port</span>
            <strong>${escapeHtml(settings.comfyui_port || 8188)}</strong>
          </div>
          <div class="row-item">
            <span>Revision hiện tại</span>
            <span class="tag">rev ${revision}</span>
          </div>
        </div>
        <div class="form-actions">
          <button class="button button--compact" type="button" data-reset-settings="jobs">Đặt lại Jobs</button>
          <button class="button button--compact" type="button" data-discard-settings ${settingsDirty ? "" : "disabled"}>Hủy thay đổi</button>
          <button class="button button--accent" type="button" data-save-settings data-apply-settings data-expected-revision="${revision}" ${settingsDirty ? "" : "disabled"}>Áp dụng & lưu</button>
        </div>
        <div id="settings-save-status" role="status" aria-live="polite">${escapeHtml(String(state.settingsActionStatus || "").slice(0, 240))}</div>
      `)}
      ${card("Sao lưu & Khôi phục dữ liệu (Backup & Restore)", `
        <p class="small">Tạo bản sao lưu ZIP an toàn cho toàn bộ cấu hình, projects, recipes, workflow library và drafts (đã lọc bỏ secrets và không bao gồm models/media nặng).</p>
        <div class="form-actions">
          <button class="button button--accent" type="button" data-create-backup>Tạo bản sao lưu mới</button>
        </div>
        <div class="backup-restore-box">
          <label for="backup-select" class="small-label">Chọn bản sao lưu để khôi phục:</label>
          <div class="split">
            <select id="backup-select" class="input input--select">
              <option value="">-- Chọn bản sao lưu --</option>
              ${(state.backups || []).map(b => `<option value="${escapeHtml(b.backup_id)}">${escapeHtml(b.backup_id)} (${escapeHtml(b.created_at || "")})</option>`).join("")}
            </select>
            <button class="button" type="button" data-inspect-backup>Kiểm tra & Lập kế hoạch</button>
          </div>
          <div id="restore-plan-output" class="restore-plan-output" role="status" aria-live="polite"></div>
        </div>
      `, "", "card--wide")}
      ${card("An toàn dữ liệu", `<ul class="notice-list"><li>Không ghi đè source media.</li><li>Không duplicate model multi-GB.</li><li>Không tự xoá user media hoặc unknown legacy data.</li><li>AIRI giữ external/installer-managed.</li></ul>`, "", "card--flat")}
    </div>
    </div>`;
  };
}
