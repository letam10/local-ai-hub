export function createDiagnosticsRenderer(deps) {
  const { heading, escapeHtml, statusPill, uiTextHtml } = deps;
  return function renderDiagnostics(state) {
  const diag = state.diagnostics?.snapshot || {};
  const subsystems = [
    ["git_integrity", "Git Integrity", diag.git_integrity],
    ["config_registry", "Config Registry", diag.config_registry],
    ["jobs_store", "Jobs Store", diag.jobs_store],
    ["artifact_store", "Artifact Store", diag.artifact_store],
    ["workflow_store", "Workflow Library", diag.workflow_store],
    ["models_inventory", "Models Inventory", diag.models_inventory],
    ["environments_inventory", "Environments", diag.environments_inventory],
    ["runtime_inventory", "Runtime Engines", diag.runtime_inventory],
    ["storage", "Storage Drives", diag.storage],
    ["gpu", "GPU Detection", diag.gpu],
    ["latest_app_errors", "Application Logs", diag.latest_app_errors],
    ["recovery_forensic", "Recovery State", diag.recovery_forensic],
  ];

  const cardsHtml = subsystems.map(([id, label, info]) => {
    const status = String(info?.status || "UNKNOWN").toUpperCase();
    const reason = info?.reason || "Đang tải dữ liệu kiểm tra...";
    const nextAction = info?.next_action || "Không có hành động bổ sung.";
    return `
      <article class="card diagnostics-card" data-subsystem="${escapeHtml(id)}" data-status="${escapeHtml(status)}">
        <div class="split">
          <strong>${uiTextHtml(label)}</strong>
          ${statusPill(status.toLowerCase(), status)}
        </div>
        <div class="diagnostics-detail">
          <p class="diagnostics-reason">${escapeHtml(reason)}</p>
          <div class="diagnostics-action"><span class="small-label">Khuyến nghị:</span> ${escapeHtml(nextAction)}</div>
        </div>
      </article>
    `;
  }).join("");

  return heading("SYSTEM", "Diagnostics Center", "Kiểm tra toàn diện 12 subsystem phần mềm, phát hiện sự cố và hỗ trợ bảo trì an toàn (read-only first).", `
    <div class="form-actions">
      <button class="button" type="button" data-refresh-diagnostics>Làm mới kiểm tra</button>
      <button class="button button--accent" type="button" data-export-diagnostics>Xuất gói chẩn đoán (Sanitized)</button>
    </div>
  `) + `
    <div class="workspace-grid workspace-grid--two diagnostics-grid">
      ${cardsHtml}
    </div>
    <section class="card repair-center-card">
      <div class="card-title-row">
        <div>
          <span class="eyebrow">BẢO TRÌ & KHẮC PHỤC</span>
          <h2>${uiTextHtml("Desktop Repair Center")}</h2>
          <p>Các hành động bảo trì chỉ tác động lên ${uiTextHtml("machine-local app state")} (fail-closed, inspect trước khi thực thi).</p>
        </div>
      </div>
      <div class="form-actions repair-actions">
        <button class="button" type="button" data-repair="verify-config">Kiểm tra cấu hình app</button>
        <button class="button" type="button" data-repair="inspect-recovery">Kiểm tra trạng thái phục hồi</button>
        <button class="button" type="button" data-repair="clear-drafts">Dọn dẹp bản nháp phục hồi cũ</button>
      </div>
      <div class="repair-output" id="repair-output" role="status" aria-live="polite"></div>
    </section>
  `;
  };
}
