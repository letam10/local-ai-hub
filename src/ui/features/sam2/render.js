export function createSam2Renderer(deps) {
  const { component, tool, formatStatus, heading, statusPill, workspaceState, card, file, field, button, formResult } = deps;
  return function renderSam2(state) {
      const item = component(state, "sam2");
      const pointTool = tool(state, "segment_from_points");
      const pointStatus = pointTool.tool_status || item.component_status || "missing";
      return heading("VISION", "SAM2", "Phân vùng và theo dõi trực tiếp qua trình xử lý SAM2; SAM2 Mask Studio không còn là workflow chính.", statusPill(pointStatus, `Chọn điểm: ${formatStatus(pointStatus)}`)) + workspaceState("SAM2 direct worker", pointTool, "Chọn điểm hoặc box trên preview rồi kiểm tra mask artifact trong Jobs.") + `
        <div class="workspace-grid workspace-grid--two">
          ${card("Đầu vào và prompt", `<form data-job-form data-tool="segment_from_points" data-tool-by-field="mode" data-tool-map='{"points":"segment_from_points","box":"segment_from_box","track":"track_video_object","text":"segment_from_text"}' class="stack">${file("Ảnh hoặc video", "asset_id", "image/*,video/*")}${field("Chế độ", `<select name="mode"><option value="points">Chọn điểm</option><option value="box">Chọn box</option><option value="text">Prompt Grounding → SAM2</option><option value="track">Theo dõi video</option></select>`)}${field("Điểm (x,y,nhãn; …)", `<input name="points_text" placeholder="320,240,1; 100,80,0" />`)}${field("Box (x1,y1,x2,y2)", `<input name="box_text" placeholder="80,60,600,500" />`)}${field("Prompt Grounding", `<input name="prompt" placeholder="person . object ." />`)}<div class="form-actions">${button("Tạo mask / track", "button--primary")}</div>${formResult("sam2-result")}</form>`)}
          ${card("Xem trước và kết quả", `<div class="preview-empty"><span>◒</span><strong>Xem trước mask sẽ xuất hiện trong Jobs</strong><p>Điểm/box đi vào trình xử lý trực tiếp. Model nạp theo yêu cầu và giải phóng khi job xong.</p></div><div class="callout">Trạng thái ở tiêu đề áp dụng riêng cho chế độ Chọn điểm. Box, prompt text và theo dõi chỉ sẵn sàng sau smoke riêng; nếu backend báo một phần/chưa khả dụng, lỗi sẽ hiện cạnh action thay vì mở GUI ngoài.</div>`, "", "card--flat")}
        </div>`;
  };
}
