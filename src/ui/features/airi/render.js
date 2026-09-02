export function createAiriRenderer(deps) {
  const { app, heading, card, escapeHtml, statusPill, statusExplanation, statusImpact } = deps;
  return function renderAiri(state) {
    const item = app(state, "airi");
    const integrations = Array.isArray(state.externalIntegrationsV2?.integrations) ? state.externalIntegrationsV2.integrations : [];
    const integration = integrations.find((entry) => entry?.integration_id === "airi") || null;
    const discoveryState = String(item.discovery_state || "unavailable");
    const launchState = String(item.launch_state || "unavailable");
    const runningState = ["running", "not_running", "unknown"].includes(String(item.running_state)) ? String(item.running_state) : "unknown";
    const integrationAllowsLaunch = !integration || integration.launch_capability?.state === "AVAILABLE";
    const launchAvailable = discoveryState === "verified" && launchState === "available" && item.launchable === true && integrationAllowsLaunch;
    const ambiguous = discoveryState === "ambiguous" || launchState === "ambiguous";
    const status = launchAvailable ? "operational" : ambiguous ? "attention" : "unavailable";
    const statusCopy = launchAvailable
      ? (runningState === "running" ? "AIRI đang chạy · có thể mở lại bằng launcher đã xác minh." : runningState === "unknown" ? "Launcher AIRI đã được xác minh; chưa xác minh được trạng thái tiến trình." : "Launcher AIRI đã được xác minh.")
      : ambiguous
        ? "Có nhiều launcher AIRI hợp lệ; Hub không tự chọn candidate."
        : "Chưa tìm thấy AIRI qua registry local hoặc Windows installer identity.";
    const action = launchAvailable
      ? `<button class="button button--primary" type="button" data-launch="airi">Mở AIRI</button>`
      : `<button class="button button--compact" type="button" data-refresh-applications="airi">Làm mới trạng thái</button>`;
    const title = launchAvailable ? "AIRI" : ambiguous ? "AIRI chưa thể chọn" : "Chưa tìm thấy AIRI";
    const explanation = statusExplanation({
      name: "AIRI",
      technicalId: "airi",
      purpose: "Mở ứng dụng AIRI bên ngoài bằng launcher Windows đã được xác minh.",
      status,
      reason: statusCopy,
      impact: statusImpact(status, "AIRI"),
      nextAction: launchAvailable ? "Bấm Mở AIRI; cấu hình và API key vẫn do AIRI quản lý." : ambiguous ? "Kiểm tra application_registry.local.json để chỉ còn một candidate hợp lệ, rồi làm mới trạng thái." : "Cài/đăng ký AIRI qua installer của AIRI rồi bấm Làm mới trạng thái.",
    });
    const help = `<details class="integration-help"><summary>Trợ giúp tích hợp AIRI</summary><ul class="notice-list"><li>Không embed AIRI bằng hack WebView.</li><li>Không di chuyển AIRI khỏi vị trí installer-managed.</li><li>Launcher chỉ được backend resolve từ allowlist; trình duyệt không gửi path hay command line.</li><li>Hub không đọc, sao chép hoặc hiển thị API key của AIRI.</li></ul></details>`;
    // V8 compatibility marker: card("Ứng dụng ngoài — chưa kết nối", ...) was
    // the former basic-state title; M2 now renders the more useful discovery
    // title and keeps the old explanation inside the help contract.
    return heading("ỨNG DỤNG NGOÀI", "AIRI", "Ứng dụng Windows installer-managed; Hub chỉ hiển thị projection và launcher đã xác minh.", action) + `
      <div class="workspace-grid workspace-grid--two">
        ${card(title, `<div class="stack"><div class="split"><div><strong>${escapeHtml(item.display_name || "AIRI")}</strong><p>${escapeHtml(statusCopy)}</p>${ambiguous ? `<p>Không mở tự động khi identity/candidate còn mơ hồ.</p>` : ""}</div>${statusPill(status)}</div>${explanation}</div>`)}
        ${help}
      </div>`;
  };
}
