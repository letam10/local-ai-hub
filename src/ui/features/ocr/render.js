export function createOcrRenderer(deps) {
  const { component, tool, heading, statusPill, card, file, field, button, formResult } = deps;
  return function renderOcr(state) {
      const item = component(state, "paddleocr_vl");
      return heading("DOCUMENTS", "OCR", "Đọc ảnh, PDF, clipboard export hoặc tài liệu từ một workspace; kết quả text/Markdown/JSON/tables là artifact Hub.", statusPill(tool(state, "ocr_document").tool_status || item.component_status || "missing")) + `
        <div class="workspace-grid workspace-grid--two">
          ${card("Tải tài liệu", `<form data-job-form data-tool="ocr_document" class="stack">${file("Ảnh hoặc PDF", "asset_id", "image/*,application/pdf")}${field("Output", `<select name="output_format"><option value="all">Text + Markdown + JSON + Tables</option><option value="markdown">Markdown</option><option value="json">JSON</option></select>`)}<div class="form-actions">${button("Chạy OCR", "button--primary")}</div>${formResult("ocr-result")}</form>`)}
          ${card("Kết quả", `<div class="preview-empty"><span>▤</span><strong>Không mở app OCR riêng</strong><p>Chọn tệp, chạy worker, rồi mở artifact trong bảng Jobs.</p></div><div class="tag-list"><span class="tag">Image</span><span class="tag">PDF</span><span class="tag">Clipboard export</span><span class="tag">Folder batch qua hàng đợi</span></div>`, "", "card--flat")}
        </div>`;
  };
}
