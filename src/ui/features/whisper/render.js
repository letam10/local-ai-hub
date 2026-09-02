import { renderWorkspaceJob } from "../vision/render.js";

const resultFor = (model) => model?.job?.result?.transcript || model?.job?.result?.whisper_transcript || null;

const formatSrtTime = (milliseconds) => {
  const value = Math.max(0, Math.round(Number(milliseconds) || 0));
  const hours = Math.floor(value / 3_600_000);
  const minutes = Math.floor((value % 3_600_000) / 60_000);
  const seconds = Math.floor((value % 60_000) / 1000);
  const millis = value % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")},${String(millis).padStart(3, "0")}`;
};

const transcriptSegments = (result) => Array.isArray(result?.segments) ? result.segments : [];

const renderTranscript = (result, escapeHtml) => {
  const segments = transcriptSegments(result);
  if (!segments.length) return `<div class="m4-result-empty">Chưa có transcript inline trong result contract; hãy kiểm tra artifact JSON đã publish.</div>`;
  return `<ol class="m4-transcript-list" data-whisper-transcript>${segments.map((segment) => `<li><button class="m4-transcript-time" type="button" data-whisper-seek="${escapeHtml(String(segment.start_ms))}">${escapeHtml(formatSrtTime(segment.start_ms))}</button><span>${escapeHtml(segment.text)}</span></li>`).join("")}</ol>`;
};

const renderSrt = (result, escapeHtml) => {
  const segments = transcriptSegments(result);
  if (!segments.length) return `<div class="m4-result-empty">Chưa có dữ liệu SRT inline.</div>`;
  const srt = segments.map((segment, index) => `${index + 1}\n${formatSrtTime(segment.start_ms)} --> ${formatSrtTime(segment.end_ms)}\n${segment.text}\n`).join("\n");
  return `<pre class="m4-inline-text" data-whisper-inline-srt>${escapeHtml(srt)}</pre>`;
};

const renderWhisperResults = (model, result, deps) => {
  const { escapeHtml, artifactList } = deps;
  const tab = ["transcript", "srt", "json"].includes(model.resultTab) ? model.resultTab : "transcript";
  const json = result ? JSON.stringify(result, null, 2) : "";
  const labels = [["transcript", "Transcript"], ["srt", "SRT"], ["json", "JSON"]];
  const panels = {
    transcript: renderTranscript(result, escapeHtml),
    srt: renderSrt(result, escapeHtml),
    json: json ? `<pre class="m4-inline-text">${escapeHtml(json)}</pre>` : `<div class="m4-result-empty">Chưa có transcript JSON.</div>`,
  };
  return `<section class="m4-result-view" data-whisper-result-view>
    <div class="m4-result-tabs" role="tablist" aria-label="Kết quả Whisper">${labels.map(([id, label]) => `<button class="tab ${tab === id ? "is-selected" : ""}" type="button" data-m4-result-tab="${id}" role="tab" aria-selected="${tab === id}">${label}</button>`).join("")}</div>
    <div class="m4-result-panels">${labels.map(([id]) => `<div data-m4-result-panel="${id}"${tab === id ? "" : " hidden"}>${panels[id]}</div>`).join("")}</div>
    ${artifactList(model.job?.result || {})}
  </section>`;
};

export function createWhisperRenderer(deps) {
  const { component, tool, heading, statusPill, card, file, field, button, formResult, escapeHtml, artifactList, formatStatus } = deps;
  return function renderWhisper(state) {
    const model = state?.m4?.whisper && typeof state.m4.whisper === "object" ? state.m4.whisper : {};
    const item = component(state, "whisper");
    const toolState = tool(state, "transcribe_media");
    const status = toolState.tool_status || item.component_status || "missing";
    const result = resultFor(model);
    const job = renderWorkspaceJob(model.job, { escapeHtml, formatStatus, statusPill });
    const gpu = state.health?.gpu?.name || "Chưa có snapshot GPU";
    const sourceLabel = model.sourceArtifact?.name || model.sourceFile?.name || "Chưa chọn media";
    return heading("SPEECH", "Whisper / Subtitles", "Workspace transcript cho audio/video: nghe, chọn đoạn thời gian, xem timestamp inline và nhận transcript/SRT/JSON trong Hub.", statusPill(status, `Whisper · ${formatStatus(status)}`)) + `
      <section class="m4-tool-workspace" data-m4-workspace="whisper" data-m4-workspace-key="whisper">
        <div class="m4-workspace__inputs">
          ${card("Đầu vào và thiết lập", `${file("Video hoặc audio", "source_artifact_id", "audio/*,video/*")}<form data-job-form data-m4-job-form data-tool="transcribe_media" data-tool-by-field="workflow" data-tool-map='{"transcribe":"transcribe_media","burn":"create_subtitled_video"}' class="stack" data-workspace-form="whisper"><div class="m4-readiness" data-m4-readiness><strong>${escapeHtml(formatStatus(status))}</strong><span>${escapeHtml(toolState.reason || item.reason || "Chưa có readiness snapshot cho Whisper.")}</span><small>${escapeHtml(toolState.action || "Kiểm tra backend rồi thử lại.")}</small></div>${field("Quy trình", `<select name="workflow"><option value="transcribe">Transcript + SRT</option><option value="burn">Transcript + ghi phụ đề vào video</option></select>`)}${field("Ngôn ngữ nguồn", `<input name="language" value="auto" maxlength="32" placeholder="auto / vi / ja" />`)}${field("Thiết bị chạy", `<select name="device"><option value="cuda">NVIDIA GPU (CUDA)</option><option value="cpu">CPU</option></select>`)}<div class="form-actions">${button("Thêm vào hàng đợi", "button--primary")}</div>${formResult("whisper-result")}</form>`)}
          ${card("Preflight tài nguyên", `<div class="m4-resource-grid"><div><span>GPU snapshot</span><strong>${escapeHtml(gpu)}</strong></div><div><span>Thực thi</span><strong>Chỉ khi capability operational</strong></div></div><p class="small">Thiết bị chỉ là lựa chọn của workflow; backend vẫn là authority. Không tự tải model và không fallback âm thầm.</p>`)}
        </div>
        <div class="m4-workspace__canvas">
          ${card("Player và timeline", `<div class="m4-media-stage m4-media-stage--whisper" data-m4-whisper-stage><div class="m4-media-layer" data-m4-whisper-media-layer><audio data-m4-whisper-audio hidden controls preload="metadata">Trình duyệt không hỗ trợ audio.</audio><video data-m4-whisper-video hidden controls preload="metadata">Trình duyệt không hỗ trợ video.</video></div><div class="m4-media-empty" data-m4-media-empty><strong>Chưa có media nguồn</strong><span>Chọn audio hoặc video để bắt đầu.</span></div></div><div class="m4-timeline" data-whisper-timeline><div class="m4-timeline-track"><span data-whisper-timeline-progress></span></div><input type="range" data-whisper-current min="0" max="86400" step="0.01" value="0" aria-label="Vị trí phát"><div class="m4-timeline-readout"><span data-whisper-current-time>0:00</span><span>/</span><span data-whisper-duration>--:--</span></div></div><div class="m4-range-controls"><label class="field"><span>Bắt đầu</span><input type="range" data-whisper-start-range min="0" max="86400" step="0.01" value="0" aria-label="Mốc bắt đầu"><input type="number" data-whisper-start-number name="start" min="0" max="86400" step="0.1" value="0" aria-label="Thời gian bắt đầu"></label><label class="field"><span>Kết thúc</span><input type="range" data-whisper-end-range min="0" max="86400" step="0.01" value="10" aria-label="Mốc kết thúc"><input type="number" data-whisper-end-number name="end" min="0" max="86400" step="0.1" value="10" aria-label="Thời gian kết thúc"></label></div><p class="small" data-whisper-range-note>Đoạn chọn: 0:00 – 0:10 · ${escapeHtml(sourceLabel)}</p>`)}
        </div>
        <div class="m4-workspace__results">
          ${card("Tiến trình, lỗi và artifact", `<div data-workspace-job-result>${job}</div><p class="small">Result contract: <code>whisper.transcript.v1</code>; artifact IDs là opaque.</p>`)}
          ${card("Kết quả Whisper", renderWhisperResults(model, result, { escapeHtml, artifactList }), "", "card--flat")}
          ${result ? card("Projection opaque", `<dl class="m4-contract-summary"><div><dt>Contract</dt><dd>${escapeHtml(result.schema_version || "whisper.transcript.v1")}</dd></div><div><dt>Segments</dt><dd>${escapeHtml(String((result.segments || []).length))}</dd></div><div><dt>Source artifact</dt><dd>${escapeHtml(result.source_artifact_id || "—")}</dd></div></dl>`) : ""}
        </div>
      </section>`;
  };
}
