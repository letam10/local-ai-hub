const ARTIFACT_URL_RE = /^\/api\/artifacts\/artifact_[a-f0-9]{32}$/;

const safeUrl = (value) => {
  const candidate = String(value || "");
  return candidate.startsWith("blob:") || ARTIFACT_URL_RE.test(candidate) ? candidate : "";
};

const numberValue = (value, fallback) => {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 1 ? number : fallback;
};

const sourceFor = (model = {}) => {
  const artifact = model.sourceArtifact && typeof model.sourceArtifact === "object" ? model.sourceArtifact : {};
  const localUrl = safeUrl(model.localPreviewUrl);
  return {
    url: localUrl || safeUrl(artifact.url),
    mediaType: String(model.sourceFile?.type || artifact.media_type || "").split(";", 1)[0].toLowerCase(),
    name: String(model.sourceFile?.name || artifact.name || "").slice(0, 180),
  };
};

const renderMediaStage = (_source, { vision = false } = {}) => `<div class="m3-media-stage ${vision ? "m3-media-stage--vision" : "m3-media-stage--sam2"}" ${vision ? "data-vision-preview-stage" : "data-sam2-canvas"} tabindex="0" role="application" aria-label="${vision ? "Canvas xem trước Vision" : "Canvas tương tác SAM2"}">
  <div class="m3-media-layer" data-m3-media-layer>
    <img data-m3-preview-image hidden alt="Xem trước tệp nguồn" />
    <video data-m3-preview-video hidden controls preload="metadata">Trình duyệt không hỗ trợ xem video.</video>
  </div>
  ${vision ? '<svg class="vision-overlay" data-vision-overlay viewBox="0 0 1 1" preserveAspectRatio="none" aria-label="Bounding boxes"></svg>' : '<canvas class="sam2-overlay" data-sam2-overlay aria-hidden="true"></canvas>'}
  <div class="m3-media-empty" data-m3-preview-empty><strong>Chưa có tệp nguồn</strong><span>Chọn tệp để xem preview trong workspace.</span></div>
</div>`;

const renderProgress = (job, { escapeHtml, formatStatus, statusPill }) => {
  if (!job || typeof job !== "object") return `<div class="workspace-job-empty" data-workspace-job-empty><strong>Chưa có tác vụ trong workspace</strong><span>Chọn input và bấm chạy; progress, lỗi và artifact sẽ xuất hiện tại đây.</span></div>`;
  const status = String(job.status || "unknown");
  const progressNumber = Number(job.progress);
  const progress = Number.isFinite(progressNumber) ? Math.max(0, Math.min(100, Math.round(progressNumber))) : 0;
  const active = ["queued", "starting", "running", "cancelling"].includes(status);
  const result = job.result && typeof job.result === "object" ? job.result : {};
  const message = job.message || result.message || "";
  const error = job.error || result.error || result.reason || "";
  const action = job.next_action || result.next_action || "";
  return `<div class="workspace-job" data-workspace-job-id="${escapeHtml(job.id || "")}" data-workspace-job-status="${escapeHtml(status)}">
    <div class="workspace-job__head"><div><span class="eyebrow">JOB ${escapeHtml(job.tool || "Hub")}</span><strong data-workspace-job-status-label>${escapeHtml(formatStatus(status))}</strong></div><div class="workspace-job__meta"><span data-workspace-job-progress-value>${progress}%</span>${statusPill(status)}</div></div>
    <div class="progress-track" role="progressbar" aria-label="Tiến trình tác vụ" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><div class="progress-bar" data-workspace-job-progress style="width:${progress}%"></div></div>
    ${message ? `<p class="small" data-workspace-job-message>${escapeHtml(message)}</p>` : ""}
    ${error ? `<div class="callout callout--danger" data-workspace-job-error><strong>${escapeHtml(error)}</strong>${action ? `<span>${escapeHtml(action)}</span>` : ""}</div>` : action && !active ? `<p class="small" data-workspace-job-action>${escapeHtml(action)}</p>` : ""}
    <div class="workspace-job__actions">${active ? `<button class="button button--danger button--compact" type="button" data-cancel-workspace-job="${escapeHtml(job.id || "")}">Hủy tác vụ</button>` : ""}${result && (result.annotation || result.selection || result.ocr_result || result.transcript || result.whisper_transcript) ? `<button class="button button--compact" type="button" data-workspace-download-json="${escapeHtml(job.tool || "vision")}">Tải JSON kết quả</button>` : ""}</div>
  </div>`;
};

export const renderWorkspaceJob = renderProgress;

const renderDetectionList = (annotation, model, escapeHtml) => {
  const detections = Array.isArray(annotation?.detections) ? annotation.detections : [];
  if (!detections.length) return `<div class="m3-result-empty">Chưa có detection hợp lệ trong result contract.</div>`;
  return `<div class="vision-detection-list" role="list" aria-label="Danh sách detection">${detections.map((item, index) => {
    const confidence = Number.isFinite(Number(item.confidence)) ? `${Math.round(Number(item.confidence) * 100)}%` : "chưa có score";
    return `<button class="vision-detection" type="button" data-vision-detection-index="${index}" aria-pressed="${index === model.selectedDetectionIndex}"><span class="vision-detection__index">${index + 1}</span><span><strong>${escapeHtml(item.label || "object")}</strong><small>${escapeHtml(confidence)} · box chuẩn hóa</small></span></button>`;
  }).join("")}</div>`;
};

const renderVisionPanel = (definition, model, deps) => {
  const { field, button, formResult, escapeHtml } = deps;
  const thresholds = model.thresholds || {};
  const range = (name, label, fallback) => {
    const value = numberValue(thresholds[name], fallback);
    return `<div class="m3-threshold-pair"><label class="field"><span>${escapeHtml(label)}</span><input type="range" data-vision-threshold="${escapeHtml(name)}" data-vision-threshold-role="range" min="0" max="1" step="0.01" value="${value}" aria-label="${escapeHtml(label)}"></label><label class="field"><span>Giá trị</span><input name="${escapeHtml(name)}" type="number" data-vision-threshold="${escapeHtml(name)}" data-vision-threshold-role="number" min="0" max="1" step="0.01" value="${value}"></label></div>`;
  };
  const controls = definition.id === "omniparser"
    ? range("box_threshold", "Ngưỡng box", 0.05)
    : definition.id === "rfdetr"
      ? range("threshold", "Ngưỡng detection", 0.5)
      : `${field("Prompt đối tượng", `<textarea name="prompt" required maxlength="300" placeholder="person, bag, screen"></textarea>`)}${range("box_threshold", "Ngưỡng box", 0.35)}${range("text_threshold", "Ngưỡng text", 0.25)}`;
  const note = definition.id === "groundingdino" ? "Prompt ngắn được gửi đến Grounding DINO; boxes có thể được dùng tiếp trong SAM2." : definition.id === "rfdetr" ? "RF-DETR nhận ảnh nguồn dùng chung và trả detection có score nếu worker cung cấp." : "OmniParser nhận screenshot/ảnh nguồn dùng chung và trả vùng tương tác khi worker hỗ trợ.";
  return `<section class="m3-tool-panel card" data-vision-tool-panel="${escapeHtml(definition.id)}" role="tabpanel" aria-label="${escapeHtml(definition.label)}">
    <div class="m3-tool-panel__head"><div><h2>${escapeHtml(definition.label)}</h2><p>${escapeHtml(note)}</p></div><span class="status-pill" data-status="${escapeHtml(definition.status)}">${escapeHtml(definition.statusLabel)}</span></div>
    <form data-job-form data-m3-job-form data-tool="${escapeHtml(definition.tool)}" class="stack" data-workspace-form="vision" data-workspace-tool="${escapeHtml(definition.id)}">
      ${controls}
      <p class="small m3-tool-readiness">${escapeHtml(definition.reason)} ${escapeHtml(definition.action)}</p>
      <div class="form-actions">${button(definition.runLabel, "button--primary")}</div>${formResult(`vision-${definition.id}-form-result`)}
    </form>
  </section>`;
};

export function createVisionRenderer(deps) {
  const { component, tool, heading, capability, card, file, formResult, escapeHtml, artifactList, statusPill, formatStatus } = deps;
  return function renderVision(state) {
    const model = state?.m3?.vision && typeof state.m3.vision === "object" ? state.m3.vision : {};
    const selected = ["omniparser", "rfdetr", "groundingdino"].includes(model.activeTool) ? model.activeTool : "omniparser";
    const definitions = [
      { id: "omniparser", label: "OmniParser", tool: "parse_screen", runLabel: "Phân tích UI", componentId: "omniparser", note: "Parse UI và vùng tương tác." },
      { id: "rfdetr", label: "RF-DETR", tool: "detect_objects", runLabel: "Phát hiện đối tượng", componentId: "rfdetr", note: "Object detection theo threshold." },
      { id: "groundingdino", label: "Grounding DINO", tool: "ground_objects", runLabel: "Tạo bounding boxes", componentId: "groundingdino", note: "Grounding theo prompt." },
    ].map((definition) => {
      const componentState = component(state, definition.componentId);
      const toolState = tool(state, definition.tool);
      const status = String(toolState.tool_status || toolState.status || componentState.component_status || "unavailable");
      return { ...definition, status, statusLabel: formatStatus(status), reason: toolState.reason || componentState.reason || "Backend chưa có readiness snapshot.", action: toolState.action || "Kiểm tra backend rồi thử lại.", componentState };
    });
    const annotation = model.job?.result?.annotation || model.job?.result?.vision_annotation;
    const source = sourceFor(model);
    const capabilityMarkup = definitions.map((definition) => capability(definition.label, definition.componentState, definition.note, tool(state, definition.tool))).join("");
    const tabs = definitions.map((definition) => `<button class="tab ${definition.id === selected ? "is-selected" : ""}" type="button" role="tab" data-vision-tool="${escapeHtml(definition.id)}" aria-selected="${definition.id === selected}">${escapeHtml(definition.label)}<small>${escapeHtml(definition.statusLabel)}</small></button>`).join("");
    const results = renderProgress(model.job, { escapeHtml, formatStatus, statusPill });
    return heading("VISION", "Vision Studio", "Một workspace chung cho OmniParser, RF-DETR và Grounding DINO: chọn input, xem preview, theo dõi job và nhận artifact ngay tại đây.") + `
      <section class="m3-tool-workspace" data-m3-workspace="vision" data-m3-workspace-key="vision" data-active-tool="${escapeHtml(selected)}">
        <div class="m3-workspace__inputs">
          ${card("Đầu vào dùng chung", `${file("Ảnh hoặc screenshot", "source_artifact_id", "image/*")}<p class="small">Upload đi qua Artifact Store; các tab Vision dùng cùng một artifact opaque và không sao chép output ngoài Store.</p>`)}
          <section class="m3-tool-tabs" role="tablist" aria-label="Công cụ Vision">${tabs}</section>
          <div class="capability-grid m3-capability-grid">${capabilityMarkup}</div>
          ${definitions.map((definition) => renderVisionPanel(definition, model, { ...deps, formResult, escapeHtml })).join("")}
        </div>
        <div class="m3-workspace__canvas">
          ${card("Canvas / preview", `${renderMediaStage(source, { vision: true })}<p class="small m3-canvas-hint">Preview chỉ xem tại route hiện tại. Chọn detection để highlight box; click ảnh không mở file picker. Result contract: <code>vision.annotation.v1</code>.</p>`)}
        </div>
        <div class="m3-workspace__results">
          ${card("Tiến trình, kết quả và artifact", `<div data-workspace-job-result>${results}</div>`)}
          ${card("Detections", `<div data-vision-detection-result>${renderDetectionList(annotation, model, escapeHtml)}</div>`, "", "card--flat")}
          ${annotation ? card("Projection opaque", `<dl class="m3-contract-summary"><div><dt>Contract</dt><dd>${escapeHtml(annotation.schema_version || "vision.annotation.v1")}</dd></div><div><dt>Source artifact</dt><dd>${escapeHtml(annotation.source_artifact_id || "—")}</dd></div><div><dt>Detections</dt><dd>${escapeHtml(String((annotation.detections || []).length))}</dd></div></dl>${artifactList(model.job?.result || {})}`) : ""}
        </div>
      </section>`;
  };
}
