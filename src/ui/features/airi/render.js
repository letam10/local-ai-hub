export function createAiriRenderer(deps) {
  const { app, heading, card, escapeHtml, statusPill, statusExplanation, statusImpact } = deps;
  return function renderAiri(state) {
      const item = app(state, "airi");
      const action = item.launchable ? `<button class="button button--primary" type="button" data-launch="airi">Mở AIRI</button>` : "";
      const status = item.component_status || "external_managed";
      const explanation = statusExplanation({
        name: "Kết nối AIRI",
        technicalId: "airi",
        purpose: "Hiển thị trạng thái launcher AIRI do Windows quản lý; Hub không sở hữu runtime hoặc API key của AIRI.",
        status,
        reason: item.notes || "AIRI là ứng dụng ngoài và chưa công bố API được Hub hỗ trợ.",
        impact: statusImpact(status, "Tích hợp AIRI"),
        nextAction: item.launchable ? "Mở AIRI Settings hoặc bấm Mở AIRI; mọi cấu hình API vẫn do AIRI quản lý." : "Cài/đăng ký AIRI qua installer của AIRI rồi làm mới danh sách ứng dụng.",
      });
      return heading("ỨNG DỤNG NGOÀI", "AIRI", "AIRI là ứng dụng ngoài do trình cài đặt Windows quản lý; Hub chỉ đọc trạng thái launcher được allowlist.", action) + `
        <div class="workspace-grid workspace-grid--two">
          ${card("Ứng dụng ngoài — chưa kết nối", `<div class="stack"><div class="split"><div><strong>${escapeHtml(item.display_name || "AIRI")}</strong><p>Hub không đọc, sao chép hoặc hiển thị API key của AIRI.</p></div>${statusPill(status)}</div>${explanation}<div class="callout">API của AIRI chỉ được coi là khả dụng khi có adapter/endpoint được project công bố; hiện tại Hub không tự quét hoặc đoán endpoint.</div></div>`, action)}
          ${card("Ranh giới tích hợp", `<ul class="notice-list"><li>Không embed AIRI bằng hack WebView.</li><li>Không di chuyển AIRI khỏi vị trí installer-managed.</li><li>Các workflow còn lại ưu tiên thực hiện ngay trong Hub.</li></ul>`, "", "card--flat")}
        </div>`;
  };
}
