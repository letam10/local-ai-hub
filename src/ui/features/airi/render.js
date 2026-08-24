export function createAiriRenderer(deps) {
  const { app, heading, card, escapeHtml, statusPill } = deps;
  return function renderAiri(state) {
      const item = app(state, "airi");
      const action = item.launchable ? `<button class="button button--primary" type="button" data-launch="airi">Mở AIRI</button>` : "";
      return heading("ỨNG DỤNG NGOÀI", "AIRI", "AIRI là ứng dụng ngoài do trình cài đặt Windows quản lý và chưa kết nối với Hub.", action) + `
        <div class="workspace-grid workspace-grid--two">
          ${card("Ứng dụng ngoài — chưa kết nối", `<div class="stack"><div class="split"><div><strong>${escapeHtml(item.display_name || "AIRI")}</strong><p>Hub không đọc, sao chép hoặc hiển thị API key của AIRI.</p></div>${statusPill(item.component_status || "missing")}</div><div class="callout">Mở AIRI Settings trong ứng dụng AIRI; Hub chỉ dùng allowlist để gọi launcher đã đăng ký.</div></div>`, action)}
          ${card("Ranh giới tích hợp", `<ul class="notice-list"><li>Không embed AIRI bằng hack WebView.</li><li>Không di chuyển AIRI khỏi vị trí installer-managed.</li><li>Các workflow còn lại ưu tiên thực hiện ngay trong Hub.</li></ul>`, "", "card--flat")}
        </div>`;
  };
}
