import { renderWorkspaceJob } from "../vision/render.js";

const resultFor = (model) => model?.job?.result?.ocr_result || model?.job?.result?.ocr || null;

const blockText = (page) => (Array.isArray(page?.blocks) ? page.blocks : [])
  .map((block) => String(block?.text || "").trim())
  .filter(Boolean);

const renderText = (result, escapeHtml) => {
  const pages = Array.isArray(result?.pages) ? result.pages : [];
  const text = pages.flatMap(blockText).join("\n");
  return text ? `<pre class="m4-inline-text" data-ocr-inline-text>${escapeHtml(text)}</pre>` : `<div class="m4-result-empty">Chưa có văn bản inline trong result contract; hãy kiểm tra artifact OCR đã publish.</div>`;
};

const renderMarkdown = (result, escapeHtml) => {
  const pages = Array.isArray(result?.pages) ? result.pages : [];
  const markdown = pages.map((page) => `## Trang ${page.page_number}\n\n${blockText(page).join("\n\n")}`).filter(Boolean).join("\n\n");
  return markdown ? `<pre class="m4-inline-text" data-ocr-inline-markdown>${escapeHtml(markdown)}</pre>` : `<div class="m4-result-empty">Chưa có Markdown inline; artifact Markdown sẽ xuất hiện nếu worker hỗ trợ.</div>`;
};

const renderTables = (result, escapeHtml) => {
  const pages = Array.isArray(result?.pages) ? result.pages : [];
  const rows = pages.flatMap((page) => (Array.isArray(page?.blocks) ? page.blocks : [])
    .filter((block) => block?.block_type === "table")
    .map((block) => `<article class="m4-ocr-table"><strong>Trang ${escapeHtml(String(page.page_number))}</strong><pre>${escapeHtml(String(block.text || "(bảng trống)"))}</pre></article>`));
  return rows.length ? `<div class="m4-ocr-table-list">${rows.join("")}</div>` : `<div class="m4-result-empty">Chưa có bảng trong result contract; artifact bảng sẽ xuất hiện nếu worker trả bảng.</div>`;
};

const renderBoxes = (result, escapeHtml) => {
  const pages = Array.isArray(result?.pages) ? result.pages : [];
  const rows = pages.flatMap((page) => (Array.isArray(page?.blocks) ? page.blocks : []).map((block, index) => `<li><strong>Trang ${escapeHtml(String(page.page_number))} · ${escapeHtml(String(block.block_type || "text"))} ${index + 1}</strong><code>[${(block.normalized_box || []).map((value) => escapeHtml(String(value))).join(", ")}]</code><span>${escapeHtml(String(block.text || ""))}</span></li>`));
  return rows.length ? `<ul class="m4-ocr-box-list" data-ocr-box-list>${rows.join("")}</ul>` : `<div class="m4-result-empty">Chưa có bounding box hợp lệ trong result contract.</div>`;
};

const renderResultTabs = (model, result, deps) => {
  const { escapeHtml, artifactList } = deps;
  const tab = ["text", "markdown", "tables", "json", "boxes"].includes(model.resultTab) ? model.resultTab : "text";
  const json = result ? JSON.stringify(result, null, 2) : "";
  const panels = {
    text: renderText(result, escapeHtml),
    markdown: renderMarkdown(result, escapeHtml),
    tables: renderTables(result, escapeHtml),
    json: json ? `<pre class="m4-inline-text">${escapeHtml(json)}</pre>` : `<div class="m4-result-empty">Chưa có OCR JSON.</div>`,
    boxes: renderBoxes(result, escapeHtml),
  };
  const labels = [["text", "Văn bản"], ["markdown", "Markdown"], ["tables", "Bảng"], ["json", "JSON"], ["boxes", "Hộp"]];
  return `<section class="m4-result-view" data-ocr-result-view>
    <div class="m4-result-tabs" role="tablist" aria-label="Kết quả OCR">${labels.map(([id, label]) => `<button class="tab ${tab === id ? "is-selected" : ""}" type="button" data-m4-result-tab="${id}" role="tab" aria-selected="${tab === id}">${label}</button>`).join("")}</div>
    <div class="m4-result-panels">${labels.map(([id]) => `<div data-m4-result-panel="${id}"${tab === id ? "" : " hidden"}>${panels[id]}</div>`).join("")}</div>
    ${artifactList(model.job?.result || {})}
  </section>`;
};

export function createOcrRenderer(deps) {
  const { component, tool, heading, statusPill, card, file, field, button, formResult, escapeHtml, artifactList, formatStatus } = deps;
  return function renderOcr(state) {
    const model = state?.m4?.ocr && typeof state.m4.ocr === "object" ? state.m4.ocr : {};
    const item = component(state, "paddleocr_vl");
    const toolState = tool(state, "ocr_document");
    const status = toolState.tool_status || item.component_status || "missing";
    const result = resultFor(model);
    const job = renderWorkspaceJob(model.job, { escapeHtml, formatStatus, statusPill });
    const sourceLabel = model.sourceArtifact?.name || model.sourceFile?.name || "Chưa chọn tài liệu";
    const publishedOptions = toolState.supported_options && typeof toolState.supported_options === "object" ? toolState.supported_options : {};
    const languages = Array.isArray(publishedOptions.languages) ? publishedOptions.languages.filter((value) => ["auto", "vi", "en", "ja", "zh"].includes(value)) : [];
    const formats = Array.isArray(publishedOptions.output_formats) ? publishedOptions.output_formats.filter((value) => ["all", "text", "markdown", "json", "tables"].includes(value)) : [];
    const regionSupported = publishedOptions.normalized_region === true;
    const pageSupported = publishedOptions.page_number === true;
    const languageLabels = { auto: "Tự nhận diện", vi: "Tiếng Việt", en: "English", ja: "日本語", zh: "中文" };
    const formatLabels = { all: "Văn bản + Markdown + JSON + Bảng", text: "Văn bản", markdown: "Markdown", json: "JSON", tables: "Bảng" };
    const languageOptions = languages.length ? languages.map((language) => `<option value="${escapeHtml(language)}">${escapeHtml(languageLabels[language] || language)}</option>`).join("") : `<option value="">Chưa công bố ngôn ngữ OCR</option>`;
    const formatOptions = formats.length ? formats.map((format) => `<option value="${escapeHtml(format)}">${escapeHtml(formatLabels[format] || format)}</option>`).join("") : `<option value="">Chưa công bố định dạng OCR</option>`;
    const languageControl = `<select name="language"${languages.length ? "" : " disabled data-ocr-option-unavailable=\"language\""}>${languageOptions}</select>`;
    const formatControl = `<select name="output_format"${formats.length ? "" : " disabled data-ocr-option-unavailable=\"output_format\""}>${formatOptions}</select>`;
    return heading("DOCUMENTS", "OCR", "Workspace OCR cho ảnh/PDF: xem preview, chọn trang/vùng, theo dõi job và nhận text/Markdown/bảng/JSON trong Hub.", statusPill(status, `OCR · ${formatStatus(status)}`)) + `
      <section class="m4-tool-workspace" data-m4-workspace="ocr" data-m4-workspace-key="ocr" data-ocr-region-supported="${String(regionSupported)}" data-ocr-page-supported="${String(pageSupported)}">
        <div class="m4-workspace__inputs">
          ${card("Đầu vào và thiết lập", `${file("Ảnh hoặc PDF", "source_artifact_id", "image/*,application/pdf")}<form data-job-form data-m4-job-form data-tool="ocr_document" class="stack" data-workspace-form="ocr"><div class="m4-readiness" data-m4-readiness><strong>${escapeHtml(formatStatus(status))}</strong><span>${escapeHtml(toolState.reason || item.reason || "Chưa có readiness snapshot cho OCR.")}</span><small>${escapeHtml(toolState.action || "Kiểm tra backend rồi thử lại.")}</small></div>${field("Ngôn ngữ OCR", languageControl)}${field("Định dạng đầu ra", formatControl)}<p class="small">Các option chỉ hiển thị khi capability server-owned của PaddleOCR helper công bố; page và vùng normalized cũng được kiểm tra trước khi submit.</p><div class="form-actions">${button("Chạy OCR", "button--primary")}</div>${formResult("ocr-result")}</form>`)}
          ${card("Vùng đã chọn", regionSupported ? `<div class="m4-selection-summary" data-ocr-selection-summary>Toàn trang · click và kéo trên ảnh để giới hạn vùng OCR.</div><button class="button button--compact" type="button" data-ocr-clear-region>Xóa vùng chọn</button><p class="small">Tọa độ vùng chỉ được gửi dưới dạng normalized [0,1]; không yêu cầu nhập x,y.</p>` : `<div class="m4-selection-summary">Server helper hiện không công bố hỗ trợ vùng OCR; control đã được tắt và không submit.</div>`)}
        </div>
        <div class="m4-workspace__canvas">
          ${card("Preview ảnh / PDF", `<div class="m4-page-toolbar" role="group" aria-label="Điều khiển trang PDF"><button class="button button--compact" type="button" data-ocr-page-action="previous"${pageSupported ? "" : " disabled"}>Trang trước</button><label class="m4-page-number"><span>Trang</span><input type="number" data-ocr-page-number min="1" max="100000" step="1" value="1" aria-label="Số trang PDF"${pageSupported ? "" : " disabled"}></label><button class="button button--compact" type="button" data-ocr-page-action="next"${pageSupported ? "" : " disabled"}>Trang sau</button><strong data-ocr-page-value>Trang 1 / chưa xác định</strong></div><div class="m4-media-stage m4-media-stage--ocr" data-m4-ocr-stage tabindex="0" role="application" aria-label="Canvas preview OCR"><div class="m4-media-layer" data-m4-ocr-media-layer><img data-m4-ocr-image hidden alt="Xem trước tài liệu"><iframe data-m4-ocr-pdf hidden title="Xem trước PDF"></iframe></div><div class="m4-ocr-result-overlay" data-ocr-result-overlay aria-hidden="true"></div><div class="m4-ocr-region" data-ocr-region hidden aria-hidden="true"></div><div class="m4-media-empty" data-m4-media-empty><strong>Chưa có tài liệu nguồn</strong><span>Chọn ảnh hoặc PDF để bắt đầu.</span></div></div><p class="small">${escapeHtml(sourceLabel)} · Click/kéo trên ảnh chỉ chọn vùng; không mở file picker.</p>`)}
        </div>
        <div class="m4-workspace__results">
          ${card("Tiến trình, lỗi và artifact", `<div data-workspace-job-result>${job}</div><p class="small">Result contract: <code>ocr.result.v1</code>; artifact IDs là opaque.</p>`)}
          ${card("Kết quả OCR", renderResultTabs(model, result, { escapeHtml, artifactList }), "", "card--flat")}
          ${result ? card("Projection opaque", `<dl class="m4-contract-summary"><div><dt>Contract</dt><dd>${escapeHtml(result.schema_version || "ocr.result.v1")}</dd></div><div><dt>Trang</dt><dd>${escapeHtml(String((result.pages || []).length))}</dd></div><div><dt>Source artifact</dt><dd>${escapeHtml(result.source_artifact_id || "—")}</dd></div></dl>`) : ""}
        </div>
      </section>`;
  };
}
