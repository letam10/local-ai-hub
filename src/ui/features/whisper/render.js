export function createWhisperRenderer(deps) {
  const { component, tool, heading, statusPill, card, file, field, button, formResult } = deps;
  return function renderWhisper(state) {
      const item = component(state, "whisper");
      return heading("SPEECH", "Whisper / Subtitles", "Thêm media, chọn ngôn ngữ và xuất transcript/SRT hoặc burn subtitle bằng worker nền.", statusPill(tool(state, "transcribe_media").tool_status || item.component_status || "missing")) + `
        <div class="workspace-grid workspace-grid--two">
          ${card("Transcript queue", `<form data-job-form data-tool="transcribe_media" data-tool-by-field="workflow" data-tool-map='{"transcribe":"transcribe_media","burn":"create_subtitled_video"}' class="stack">${file("Video hoặc audio", "asset_id", "audio/*,video/*")}${field("Workflow", `<select name="workflow"><option value="transcribe">Transcript + SRT</option><option value="burn">Transcript + burn subtitle</option></select>`)}<div class="form-grid">${field("Language", `<input name="language" value="auto" placeholder="auto / vi / ja" />`)}${field("Thiết bị", `<select name="device"><option value="cpu">CPU safe</option><option value="cuda">CUDA nếu environment hỗ trợ</option></select>`)}</div>${field("Đoạn ngắn bắt đầu/kết thúc (giây, tùy chọn)", `<div class="inline-fields"><input name="start" type="number" min="0" step="0.1" value="0" /><input name="end" type="number" min="0.1" step="0.1" value="10" /></div>`)}<div class="form-actions">${button("Thêm vào hàng đợi", "button--primary")}</div>${formResult("whisper-result")}</form>`)}
          ${card("Điều khiển", `<ul class="notice-list"><li>Output không ghi đè source media.</li><li>Cancel/resume dùng Jobs và chỉ dừng process do Hub sở hữu.</li><li>Folder batch được lên kế hoạch qua nhiều job upload; không cần mở Whisper GUI.</li></ul><div class="preview-empty compact"><span>≋</span><strong>Transcript, SRT và video subtitle xuất hiện ở Jobs</strong></div>`, "", "card--flat")}
        </div>`;
  };
}
