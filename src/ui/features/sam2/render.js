import { renderWorkspaceJob } from "../vision/render.js";

const ARTIFACT_URL_RE = /^\/api\/artifacts\/artifact_[a-f0-9]{32}$/;

const safeUrl = (value) => {
  const candidate = String(value || "");
  return candidate.startsWith("blob:") || ARTIFACT_URL_RE.test(candidate) ? candidate : "";
};

const sourceFor = (model = {}) => {
  const artifact = model.sourceArtifact && typeof model.sourceArtifact === "object" ? model.sourceArtifact : {};
  return {
    url: safeUrl(model.localPreviewUrl) || safeUrl(artifact.url),
    mediaType: String(model.sourceFile?.type || artifact.media_type || "").split(";", 1)[0].toLowerCase(),
    name: String(model.sourceFile?.name || artifact.name || "").slice(0, 180),
  };
};

const artifactFor = (artifacts, ids, fallbackIndex = 0) => {
  const safe = Array.isArray(artifacts) ? artifacts : [];
  const wanted = Array.isArray(ids) ? ids : [];
  return wanted.map((id) => safe.find((item) => item?.id === id)).find(Boolean) || (fallbackIndex >= 0 ? safe[fallbackIndex] : null) || null;
};

const artifactMedia = (artifact, emptyText, escapeHtml) => {
  const escape = typeof escapeHtml === "function" ? escapeHtml : (value) => String(value);
  const url = safeUrl(artifact?.url);
  if (!url) return `<div class="preview-empty compact"><strong>${escape(emptyText)}</strong><span>Artifact sẽ xuất hiện sau khi job hoàn tất và được publish.</span></div>`;
  const mediaType = String(artifact?.media_type || "").toLowerCase();
  if (mediaType.startsWith("video/")) return `<video class="m3-result-media" controls preload="metadata" src="${escape(url)}" aria-label="${escape(String(artifact?.name || "Video result"))}"></video>`;
  if (mediaType.startsWith("image/")) return `<img class="m3-result-media" src="${escape(url)}" alt="${escape(String(artifact?.name || "Artifact result"))}" />`;
  return `<div class="preview-empty compact"><strong>${escape(emptyText)}</strong><span>Loại artifact này chỉ có thể tải từ Artifact Store.</span></div>`;
};

const renderComposite = (source, mask, model, escapeHtml) => {
  const escape = typeof escapeHtml === "function" ? escapeHtml : (value) => String(value);
  const sourceUrl = safeUrl(source?.url);
  const maskUrl = safeUrl(mask?.url);
  const sourceType = String(source?.mediaType || "").toLowerCase();
  const maskType = String(mask?.media_type || "").toLowerCase();
  if (!sourceUrl || !maskUrl || !sourceType.startsWith("image/") || !maskType.startsWith("image/")) {
    return `<div class="preview-empty compact" data-sam2-composite-unavailable><strong>Ghép chưa khả dụng</strong><span>Cần ảnh gốc và mask image artifact đã publish; không dựng composite giả từ selection hoặc overlay thiếu.</span></div>`;
  }
  const opacity = Math.max(0, Math.min(1, Number(model?.maskOpacity ?? 0.68) || 0));
  return `<div class="sam2-composite" data-sam2-composite><img class="m3-result-media sam2-composite__original" data-sam2-original-layer src="${escape(sourceUrl)}" alt="Ảnh gốc ${escape(source.name || "")}" /><img class="m3-result-media sam2-composite__mask" data-sam2-mask-layer src="${escape(maskUrl)}" alt="Mask artifact ${escape(mask.name || "")}" style="opacity:${opacity}" /></div>`;
};

const renderResultTabs = (model, deps) => {
  const { safeJobArtifacts, escapeHtml, artifactList } = deps;
  const result = model.job?.result && typeof model.job.result === "object" ? model.job.result : {};
  const artifacts = safeJobArtifacts(result.artifacts);
  const selection = result.selection || result.sam2_selection || {};
  const mask = artifactFor(artifacts, selection.mask_artifact_ids, -1);
  const overlay = artifactFor(artifacts, selection.overlay_artifact_ids, -1);
  const source = sourceFor(model);
  const original = source.url
    ? (source.mediaType.startsWith("video/") ? `<video class="m3-result-media" controls preload="metadata" src="${escapeHtml(source.url)}" aria-label="Ảnh/video gốc"></video>` : `<img class="m3-result-media" src="${escapeHtml(source.url)}" alt="Ảnh gốc ${escapeHtml(source.name)}" />`)
    : `<div class="preview-empty compact"><strong>Chưa có ảnh gốc</strong><span>Chọn tệp để xem.</span></div>`;
  return `<section class="sam2-result-view" data-sam2-result-view>
    <div class="m3-result-tabs" role="tablist" aria-label="Kết quả SAM2"><button class="tab ${model.resultTab === "original" ? "is-selected" : ""}" type="button" data-sam2-result-tab="original" role="tab" aria-selected="${model.resultTab === "original"}">Ảnh gốc</button><button class="tab ${model.resultTab === "mask" ? "is-selected" : ""}" type="button" data-sam2-result-tab="mask" role="tab" aria-selected="${model.resultTab === "mask"}">Mask</button><button class="tab ${model.resultTab === "composite" ? "is-selected" : ""}" type="button" data-sam2-result-tab="composite" role="tab" aria-selected="${model.resultTab === "composite"}">Ghép</button></div>
    <div class="sam2-result-panels"><div data-sam2-result-panel="original">${original}</div><div data-sam2-result-panel="mask">${artifactMedia(mask, "Chưa có mask artifact", escapeHtml)}</div><div data-sam2-result-panel="composite">${renderComposite(source, mask, model, escapeHtml)}</div></div>
    <label class="field sam2-opacity-control"><span>Độ trong suốt mask <b data-sam2-opacity-value>${Math.round(Number(model.maskOpacity ?? 0.68) * 100)}%</b></span><input type="range" data-sam2-opacity min="0" max="1" step="0.01" value="${Number(model.maskOpacity ?? 0.68)}" /></label>
    ${selection.candidate_score !== undefined && selection.candidate_score !== null ? `<p class="small">Candidate score: ${escapeHtml(String(selection.candidate_score))}</p>` : ""}
    ${artifactList(result)}
  </section>`;
};

export function createSam2Renderer(deps) {
  const { component, tool, formatStatus, heading, statusPill, card, file, field, button, formResult, escapeHtml, artifactList, safeJobArtifacts } = deps;
  return function renderSam2(state) {
    const model = state?.m3?.sam2 && typeof state.m3.sam2 === "object" ? state.m3.sam2 : {};
    const mode = ["points", "box", "text", "track"].includes(model.mode) ? model.mode : "points";
    const item = component(state, "sam2");
    const pointTool = tool(state, "segment_from_points");
    const selectedTool = tool(state, mode === "box" ? "segment_from_box" : mode === "track" ? "track_video_object" : mode === "text" ? "segment_from_text" : "segment_from_points");
    const status = selectedTool.tool_status || selectedTool.status || item.component_status || "unavailable";
    const source = sourceFor(model);
    const selection = model.selection && typeof model.selection === "object" ? model.selection : { points: [], box: null };
    const result = renderWorkspaceJob(model.job, { escapeHtml, formatStatus, statusPill, staleJob: model.staleJob });
    const settingsExpanded = model.settingsExpanded === true;
    const primaryDisabled = String(status).toLowerCase() !== "operational" ? " disabled data-readiness-gate=\"true\" aria-disabled=\"true\"" : "";
    const modeButtons = [["points", "Chọn điểm"], ["box", "Chọn hộp"], ["text", "Chọn bằng mô tả"], ["track", "Theo dõi video"]].map(([id, label]) => `<button class="tab ${id === mode ? "is-selected" : ""}" type="button" data-sam2-mode="${id}" role="tab" aria-selected="${id === mode}">${label}</button>`).join("");
    const modePanels = `<section class="sam2-mode-panel" data-sam2-mode-panel="points"><div class="sam2-intent-row" role="group" aria-label="Ý nghĩa điểm"><button class="button button--compact ${model.intent !== "negative" ? "is-selected" : ""}" type="button" data-sam2-intent="positive" aria-pressed="${model.intent !== "negative"}">Thêm vùng</button><button class="button button--compact ${model.intent === "negative" ? "is-selected" : ""}" type="button" data-sam2-intent="negative" aria-pressed="${model.intent === "negative"}">Loại vùng</button></div><p class="small">Click trực tiếp trên canvas để thêm điểm xanh lá hoặc đỏ. Kéo điểm để di chuyển; Delete xóa điểm đã chọn.</p></section>
      <section class="sam2-mode-panel" data-sam2-mode-panel="box"><p class="small">Kéo trực tiếp trên canvas để tạo box. Kéo bên trong box để chỉnh lại lựa chọn; không cần nhập x,y.</p></section>
      <section class="sam2-mode-panel" data-sam2-mode-panel="text">${field("Mô tả đối tượng", `<textarea name="prompt" maxlength="300" placeholder="một người cầm túi"></textarea>`)}<p class="small">Grounding DINO sẽ tạo box đầu tiên rồi chuyển tiếp sang SAM2 nếu cả hai capability đã sẵn sàng.</p></section>
      <section class="sam2-mode-panel" data-sam2-mode-panel="track"><p class="small">Chọn thời điểm trên player trước khi bấm chạy. Việc xem hoặc seek không tự nạp model.</p><div class="sam2-frame-readout" data-sam2-frame-value>Frame precision unavailable · ${(Number(model.frameTimeSeconds) || 0).toFixed(2)}s</div><p class="small" data-sam2-frame-precision>Frame precision unavailable: cần FPS/frame count đã xác minh; thao tác track dùng thời gian thực tế của player.</p><input type="range" data-sam2-time-slider min="0" max="0" step="0.01" value="${Number(model.frameTimeSeconds) || 0}" aria-label="Thời gian video"><input type="range" data-sam2-frame-slider min="0" max="0" step="1" value="${Number.isInteger(model.frameIndex) ? model.frameIndex : 0}" aria-label="Frame video" disabled></section>`;
    return heading("VISION", "SAM2", "Workspace trực quan cho điểm, hộp, mô tả và theo dõi video; mọi selection dùng tọa độ chuẩn hóa và result đi qua Artifact Store.", statusPill(status, `SAM2 · ${formatStatus(status)}`)) + `
      <section class="m3-tool-workspace sam2-tool-workspace" data-m3-workspace="sam2" data-m3-workspace-key="sam2" data-active-mode="${escapeHtml(mode)}">
        <div class="m3-command-strip m3-workspace__inputs" data-m3-command-strip>
          <div class="m3-command-strip__source">${file("Ảnh hoặc video", "source_artifact_id", "image/*,video/*")}</div>
          <div class="m3-command-strip__tool"><strong data-m3-active-tool-label>SAM2 · ${escapeHtml(mode)}</strong><div class="m3-command-strip__tools sam2-mode-tabs" role="tablist" aria-label="Chế độ SAM2">${modeButtons}</div></div>
          <div class="m3-command-strip__readiness"><strong data-m3-readiness>${escapeHtml(formatStatus(status))}</strong><span data-m3-readiness-reason>${escapeHtml(selectedTool.reason || pointTool.reason || "SAM2 chưa có readiness snapshot.")}</span></div>
          <div class="m3-command-strip__actions"><button class="button button--primary" type="submit" form="sam2-job-form" data-m3-primary-action${primaryDisabled}>Tạo mask / theo dõi</button><button class="button button--compact" type="button" data-m3-settings-toggle="sam2" aria-controls="m3-sam2-settings" aria-expanded="${String(settingsExpanded)}">${settingsExpanded ? "Thu gọn thiết lập" : "Thiết lập"}</button></div>
        </div>
        <div class="m3-workspace__canvas">
          ${card("Canvas / player", `<div class="sam2-canvas-toolbar"><button class="button button--compact" type="button" data-sam2-action="zoom-out">Thu nhỏ</button><button class="button button--compact" type="button" data-sam2-action="zoom-in">Phóng to</button><button class="button button--compact" type="button" data-sam2-action="fit">Vừa khung</button><button class="button button--compact" type="button" data-sam2-action="clear-last">Xóa cuối</button><button class="button button--compact" type="button" data-sam2-action="clear-all">Xóa toàn bộ</button></div><div class="sam2-canvas-help">Kéo box · click điểm · Shift + kéo để pan · cuộn để zoom · Enter thêm điểm</div><div class="m3-media-stage m3-media-stage--sam2" data-sam2-canvas tabindex="0" role="application" aria-label="Canvas SAM2 tương tác"><div class="m3-media-layer" data-sam2-media-layer><img data-m3-preview-image hidden alt="Xem trước ảnh nguồn"><video data-m3-preview-video hidden controls preload="metadata">Trình duyệt không hỗ trợ video.</video></div><canvas class="sam2-overlay" data-sam2-overlay aria-hidden="true"></canvas><div class="m3-media-empty" data-m3-preview-empty><strong>Chưa có tệp nguồn</strong><span>Chọn tệp để bắt đầu.</span></div><div class="m3-media-loading" data-m3-preview-loading hidden><strong>Đang tải preview…</strong><span>Media vẫn ở trong Hub; chờ player xác nhận metadata.</span></div><div class="m3-media-error" data-m3-preview-error hidden><strong>Không thể tải preview</strong><span data-m3-preview-error-text>Kiểm tra artifact hoặc chọn tệp khác.</span></div></div><p class="small">${source.name ? `Đang xem: ${escapeHtml(source.name)}` : "Canvas không tự chạy model khi chỉ xem hoặc chọn frame."}</p>`)}
        </div>
        <div class="m3-workspace__results">
          <section class="m3-workspace__settings" id="m3-sam2-settings" data-m3-settings="sam2"${settingsExpanded ? "" : " hidden"}>
            ${card("Đầu vào và thiết lập SAM2", `<form id="sam2-job-form" data-job-form data-m3-job-form data-tool="segment_from_points" data-tool-by-field="mode" data-tool-map='{"points":"segment_from_points","box":"segment_from_box","track":"track_video_object","text":"segment_from_text"}' class="stack" data-workspace-form="sam2"><input type="hidden" name="mode" data-sam2-mode-value value="${escapeHtml(mode)}">${modePanels}<div class="sam2-selection-summary"><strong>Lựa chọn hiện tại</strong><span data-sam2-selection-count>${selection.points?.length || 0} điểm · ${selection.box ? "1 box" : "0 box"}</span></div><details class="advanced"><summary>Nâng cao / Gỡ lỗi</summary><p class="small">Tọa độ nội bộ được gửi dưới dạng normalized [0,1]; phần này chỉ để kiểm tra contract, không phải luồng nhập bắt buộc.</p><pre data-sam2-debug-selection>{}</pre></details><p class="small">${escapeHtml(selectedTool.reason || pointTool.reason || "SAM2 chưa có readiness snapshot.")} ${escapeHtml(selectedTool.action || pointTool.action || "Kiểm tra backend rồi thử lại.")}</p>${formResult("sam2-form-result")}</form>`)}
          </section>
          ${card("Tiến trình, lỗi và artifact", `<div data-workspace-job-result>${result}</div>`)}
          ${card("Kết quả SAM2", `${renderResultTabs(model, { ...deps, artifactList, safeJobArtifacts })}<p class="small">Result contract: <code>sam2.selection.v1</code>; artifact IDs là opaque và không chứa path máy.</p>`, "", "card--flat")}
          ${model.job?.result?.selection ? card("Projection opaque", `<dl class="m3-contract-summary"><div><dt>Contract</dt><dd>${escapeHtml(model.job.result.selection.schema_version || "sam2.selection.v1")}</dd></div><div><dt>Source artifact</dt><dd>${escapeHtml(model.job.result.selection.source_artifact_id || "—")}</dd></div><div><dt>Frame</dt><dd>${escapeHtml(String(model.job.result.selection.frame_index ?? 0))}</dd></div></dl>`) : ""}
        </div>
      </section>`;
  };
}
