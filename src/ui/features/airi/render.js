export function createAiriRenderer(deps) {
  const { app, heading, card, escapeHtml, statusPill, statusExplanation, statusImpact } = deps;
  return function renderAiri(state) {
      const item = app(state, "airi");
      const integrations = Array.isArray(state.externalIntegrationsV2?.integrations) ? state.externalIntegrationsV2.integrations : [];
      const integration = integrations.find((entry) => entry?.integration_id === "airi") || null;
      const connectionState = String(integration?.connection_state || "");
      const connectionCopy = {
        NOT_INSTALLED: "Chưa cài đặt",
        INSTALLED_NOT_CONNECTED: "Đã cài đặt · chưa kết nối",
        CONNECTED: "Đã kết nối",
        UNSUPPORTED_API: "Chưa có API được hỗ trợ",
        AUTH_REQUIRED: "Cần xác thực trong AIRI",
      };
      const launchAvailable = integration?.launch_capability?.state === "AVAILABLE" && item.launchable;
      const action = launchAvailable ? `<button class="button button--primary" type="button" data-launch="airi">Mở AIRI</button>` : "";
      const status = connectionState === "CONNECTED" ? "operational" : connectionState === "NOT_INSTALLED" ? "unavailable" : "external_managed";
      const explanation = statusExplanation({
        name: "Kết nối AIRI",
        technicalId: "airi",
        purpose: "Hiển thị trạng thái launcher AIRI do Windows quản lý; Hub không sở hữu runtime hoặc API key của AIRI.",
        status,
        reason: integration?.reason || item.notes || "AIRI là ứng dụng ngoài và chưa công bố API được Hub hỗ trợ.",
        impact: statusImpact(status, "Tích hợp AIRI"),
        nextAction: integration?.next_action || (launchAvailable ? "Mở AIRI Settings hoặc bấm Mở AIRI; mọi cấu hình API vẫn do AIRI quản lý." : "Cài/đăng ký AIRI qua installer của AIRI rồi làm mới danh sách ứng dụng."),
      });
      return heading("ỨNG DỤNG NGOÀI", "AIRI", "AIRI là ứng dụng ngoài do trình cài đặt Windows quản lý; Hub chỉ đọc trạng thái launcher được allowlist.", action) + `
        <div class="workspace-grid workspace-grid--two">
          ${card("Ứng dụng ngoài — chưa kết nối", `<div class="stack"><div class="split"><div><strong>${escapeHtml(item.display_name || integration?.display_name || "AIRI")}</strong><p>${escapeHtml(connectionCopy[connectionState] || "Đang đọc projection tích hợp")}</p><p>Hub không đọc, sao chép hoặc hiển thị API key của AIRI.</p></div>${statusPill(status)}</div>${explanation}<div class="callout">${escapeHtml(integration?.official_channel === "none" ? "Chưa có API/IPC chính thức được đăng ký; Hub không tự quét hoặc đoán endpoint." : "Chỉ dùng kênh API/IPC chính thức đã được project đăng ký.")}</div></div>`, action)}
          ${card("Ranh giới tích hợp", `<ul class="notice-list"><li>Không embed AIRI bằng hack WebView.</li><li>Không di chuyển AIRI khỏi vị trí installer-managed.</li><li>Launcher (nếu có) luôn do allowlist phía Hub sở hữu.</li><li>Các workflow còn lại ưu tiên thực hiện ngay trong Hub.</li></ul>`, "", "card--flat")}
        </div>`;
  };
}
