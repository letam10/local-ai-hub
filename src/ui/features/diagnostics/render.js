export function createDiagnosticsRenderer(deps) {
  const { heading, escapeHtml, statusPill, statusExplanation, statusImpact, uiText, uiTextHtml } = deps;
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

  const cardHtml = ([id, label, info]) => {
    const status = String(info?.status || "UNKNOWN").toUpperCase();
    const displayStatus = status.toLowerCase() === "needs_attention" ? "attention" : status.toLowerCase();
    const reason = info?.reason || "Đang tải dữ liệu kiểm tra...";
    const nextAction = info?.next_action || "Không có hành động bổ sung.";
    const inspected = info?.inspected || "Đang xác định phạm vi kiểm tra...";
    const evidence = info?.evidence_summary || reason;
    const checkedAt = info?.checked_at || "Chưa có thời điểm kiểm tra";
    const purpose = {
      git_integrity: "Xác nhận source/repository không bị thay đổi ngoài contract.",
      config_registry: "Đọc cấu hình server-owned và các registry đã allowlist.",
      jobs_store: "Theo dõi hàng đợi, lịch sử và khả năng phục hồi tác vụ.",
      artifact_store: "Xác nhận artifact/output được quản lý an toàn.",
      workflow_store: "Kiểm tra Workflow Library và revision metadata.",
      models_inventory: "Kiểm tra inventory model quan sát được, không tự cài model.",
      environments_inventory: "Kiểm tra runtime/environment đã được khai báo.",
      runtime_inventory: "Kiểm tra engine/runtime được phép sử dụng.",
      storage: "Kiểm tra volume và dung lượng cho các thao tác ghi an toàn.",
      gpu: "Đọc snapshot GPU; không tự chạy inference.",
      latest_app_errors: "Tóm tắt lỗi ứng dụng đã được redact.",
      recovery_forensic: "Kiểm tra dấu vết phục hồi và bản nháp cần xử lý.",
    }[id] || "Kiểm tra một subsystem server-owned.";
    const translatedLabel = typeof uiText === "function" ? uiText(label) : label;
    return `<article class="diagnostics-card" data-subsystem="${escapeHtml(id)}" data-status="${escapeHtml(status)}"><div class="diagnostics-card__disclosure">${statusExplanation({ name: translatedLabel, technicalId: id, purpose, status: displayStatus, reason, impact: info?.impact || statusImpact(displayStatus, label), nextAction, compact: true })}</div><div class="diagnostics-card__evidence"><div><span>Đã kiểm tra</span><p>${escapeHtml(inspected)}</p></div><div><span>Bằng chứng</span><p>${escapeHtml(evidence)}</p></div><small>Kiểm tra lần cuối: ${escapeHtml(checkedAt)}</small></div></article>`;
  };
  // Use two independent vertical stacks.  A CSS grid with each card as a
  // direct child couples row heights, so expanding one card would create a
  // large empty area next to unrelated cards in the other column.
  const midpoint = Math.ceil(subsystems.length / 2);
  const columnsHtml = [subsystems.slice(0, midpoint), subsystems.slice(midpoint)]
    .map((column, index) => `<div class="diagnostics-column" data-diagnostics-column="${index + 1}">${column.map(cardHtml).join("")}</div>`)
    .join("");

  return heading("SYSTEM", "Diagnostics Center", "Kiểm tra toàn diện 12 subsystem phần mềm, phát hiện sự cố và hỗ trợ bảo trì an toàn (read-only first).", `
    <div class="form-actions">
      <button class="button" type="button" data-refresh-diagnostics>Làm mới kiểm tra</button>
      <button class="button button--accent" type="button" data-export-diagnostics>Xuất gói chẩn đoán (Sanitized)</button>
    </div>
  `) + `
    <div class="diagnostics-grid" data-diagnostics-grid>${columnsHtml}</div>
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
