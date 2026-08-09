import { escapeHtml, formatGb, formatStatus } from "./api.js";

export const NAVIGATION = [
  { group: "TỔNG QUAN", items: [["dashboard", "Dashboard", "◫"], ["airi", "AIRI", "◌"]] },
  { group: "VISION & DOCUMENT", items: [["vision", "Vision Studio", "◉"], ["sam2", "SAM2", "◒"], ["ocr", "OCR", "▤"]] },
  { group: "SPEECH & VOICE", items: [["whisper", "Whisper", "≋"], ["voice", "Voice", "♪"]] },
  { group: "IMAGE & VIDEO", items: [["image", "Image AI", "✦"], ["media", "Media", "▹"], ["animesr", "AnimeSR", "⇱"]] },
  { group: "HỆ THỐNG", items: [["jobs", "Jobs", "≡"], ["models", "Models & Storage", "▦"], ["settings", "Settings", "⚙"]] },
];

const component = (state, id) => (state.components || []).find((item) => item.id === id) || {};
const tool = (state, id) => (state.tools || []).find((item) => item.name === id) || {};
const app = (state, id) => (state.applications || []).find((item) => item.id === id) || {};

const statusPill = (status, label = formatStatus(status)) => `<span class="status-pill" data-status="${escapeHtml(status || "unknown")}">${escapeHtml(label)}</span>`;
const heading = (eyebrow, title, description, actions = "") => `
  <header class="page-heading"><div class="heading-copy"><div class="eyebrow">${escapeHtml(eyebrow)}</div><h1>${escapeHtml(title)}</h1><p>${escapeHtml(description)}</p></div><div class="heading-actions">${actions}</div></header>`;
const card = (title, content, action = "", extra = "") => `<section class="card ${extra}"><div class="card-title-row"><h2>${escapeHtml(title)}</h2>${action}</div>${content}</section>`;
const field = (label, control, extra = "") => `<label class="field ${extra}"><span>${escapeHtml(label)}</span>${control}</label>`;
const file = (label, key, accept = "") => field(label, `<input type="file" data-asset-key="${escapeHtml(key)}" ${accept ? `accept="${escapeHtml(accept)}"` : ""} /><div class="file-preview" data-file-preview aria-live="polite"></div>`);
const files = (label, key, accept = "") => field(label, `<input type="file" data-asset-key="${escapeHtml(key)}" multiple ${accept ? `accept="${escapeHtml(accept)}"` : ""} /><div class="file-preview" data-file-preview aria-live="polite"></div>`);
const button = (text, extra = "") => `<button class="button ${extra}" type="submit">${escapeHtml(text)}</button>`;
const capability = (name, item, note, direct = {}) => `<div class="capability"><div><strong>${escapeHtml(name)}</strong><p>${escapeHtml(note)}</p>${direct.reason ? `<p>${escapeHtml(direct.reason)}</p>` : ""}</div>${statusPill(direct.tool_status || item.component_status || item.status || "missing")}</div>`;
const activeTab = (state, module) => state.workspaceTabs?.[module] || "quick";
const moduleTabs = (state, module) => {
  const selected = activeTab(state, module);
  return `<div class="module-tabs" role="tablist" aria-label="${escapeHtml(module)} workspace"><button class="tab ${selected === "quick" ? "is-selected" : ""}" type="button" data-workspace-tab="${escapeHtml(module)}:quick" role="tab" aria-selected="${selected === "quick"}">Quick</button><button class="tab ${selected === "nodes" ? "is-selected" : ""}" type="button" data-workspace-tab="${escapeHtml(module)}:nodes" role="tab" aria-selected="${selected === "nodes"}">Nodes</button></div>`;
};
const imageModuleTabs = (state) => {
  const selected = activeTab(state, "image");
  return `<div class="module-tabs" role="tablist" aria-label="Image AI workspace"><button class="tab ${selected === "quick" ? "is-selected" : ""}" type="button" data-workspace-tab="image:quick" role="tab" aria-selected="${selected === "quick"}">Quick</button><button class="tab ${selected === "nodes" ? "is-selected" : ""}" type="button" data-workspace-tab="image:nodes" role="tab" aria-selected="${selected === "nodes"}">Hub Nodes</button><button class="tab ${selected === "advanced" ? "is-selected" : ""}" type="button" data-workspace-tab="image:advanced" role="tab" aria-selected="${selected === "advanced"}">ComfyUI Advanced</button></div>`;
};
const nodeStudio = (scope, description) => `<section class="node-studio-wrap"><div class="node-studio-intro"><div><strong>Node Studio</strong><p>${escapeHtml(description)}</p></div><span class="tag">offline · typed sockets · DAG</span></div><div data-node-studio data-scope="${escapeHtml(scope)}"></div></section>`;

function artifacts(value, found = []) {
  if (!value) return found;
  if (Array.isArray(value)) value.forEach((item) => artifacts(item, found));
  else if (typeof value === "object") {
    if (value.id && value.url && !found.some((item) => item.id === value.id)) found.push(value);
    Object.values(value).forEach((item) => artifacts(item, found));
  }
  return found;
}

const artifactList = (value) => {
  const items = artifacts(value);
  if (!items.length) return "";
  return `<div class="artifact-list">${items.map((item) => {
    const isImage = String(item.media_type || "").startsWith("image/");
    const isMedia = /^(image|audio|video)\//.test(String(item.media_type || ""));
    return `<div class="artifact-item">${isImage ? `<img class="artifact-preview" src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}" />` : ""}<div class="row-main"><div class="row-name">${escapeHtml(item.name)}</div><div class="row-meta">${escapeHtml(item.media_type || "artifact")} · ${formatGb(item.size_bytes)}</div></div>${isMedia ? `<a class="button button--compact" href="${escapeHtml(item.url)}" target="_blank" rel="noopener">Xem</a>` : ""}<button class="button button--compact" type="button" data-open-artifact="${escapeHtml(item.id)}">Mở</button></div>`;
  }).join("")}</div>`;
};

const formResult = (id) => `<div class="form-result" id="${escapeHtml(id)}" role="status" aria-live="polite"></div>`;

function renderDashboard(state) {
  const health = state.health || {};
  const disk = health.disk || {};
  const gpu = health.gpu || {};
  const jobs = state.jobs || [];
  const active = jobs.filter((item) => ["starting", "running", "cancelling"].includes(item.status)).length;
  const components = state.components || [];
  return heading("CONTROL PLANE", "Dashboard", "Một cửa sổ điều khiển workflow AI cục bộ. AIRI là ngoại lệ duy nhất mở ứng dụng riêng.") + `
    <div class="metric-grid">
      <div class="metric-card"><span>Hub API</span><strong>${escapeHtml(formatStatus(health.status))}</strong><small>loopback · không CDN</small></div>
      <div class="metric-card"><span>GPU</span><strong>${escapeHtml(gpu.name || "Chưa phát hiện")}</strong><small>${gpu.memory_free_mib ? `${escapeHtml(gpu.memory_free_mib)} MiB VRAM trống` : "nạp model theo yêu cầu"}</small></div>
      <div class="metric-card"><span>Ổ đĩa</span><strong>${formatGb(disk.free_bytes)}</strong><small>dung lượng trống</small></div>
      <div class="metric-card"><span>Job hoạt động</span><strong>${active}</strong><small>${jobs.length} bản ghi trong hàng đợi</small></div>
    </div>
    <div class="workspace-grid workspace-grid--two" style="margin-top:16px">
      ${card("Tình trạng module", `<div class="row-list">${components.slice(0, 10).map((item) => `<div class="row-item"><div class="row-main"><div class="row-name">${escapeHtml(item.name || item.id)}</div><div class="row-meta">${escapeHtml(item.kind || "component")}</div></div>${statusPill(item.component_status || item.status)}</div>`).join("") || `<div class="empty-state compact">Chưa có component.</div>`}`)}
      ${card("Workflow trong cửa sổ Hub", `<ul class="notice-list"><li>SAM2, AnimeSR, Whisper, Voice, Vision, OCR, Image AI và FFmpeg dùng worker/API nền.</li><li>Không có console PowerShell/cmd khi khởi động từ shortcut Hub.</li><li>Trạng thái “Một phần” nghĩa là adapter đã cấu hình nhưng chưa có smoke bounded V3.</li></ul>`, "", "card--flat")}
    </div>`;
}

function renderAiri(state) {
  const item = app(state, "airi");
  const action = item.launchable ? `<button class="button button--primary" type="button" data-launch="airi">Mở AIRI</button>` : "";
  return heading("ỨNG DỤNG NGOÀI", "AIRI", "AIRI do trình cài đặt Windows quản lý và là ngoại lệ duy nhất có cửa sổ riêng.", action) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("AIRI external", `<div class="stack"><div class="split"><div><strong>${escapeHtml(item.display_name || "AIRI")}</strong><p>Hub không đọc, sao chép hoặc hiển thị API key của AIRI.</p></div>${statusPill(item.component_status || "missing")}</div><div class="callout">Mở AIRI Settings trong ứng dụng AIRI; Hub chỉ dùng allowlist để gọi launcher đã đăng ký.</div></div>`, action)}
      ${card("Ranh giới tích hợp", `<ul class="notice-list"><li>Không embed AIRI bằng hack WebView.</li><li>Không di chuyển AIRI khỏi vị trí installer-managed.</li><li>Các workflow còn lại ưu tiên thực hiện ngay trong Hub.</li></ul>`, "", "card--flat")}
    </div>`;
}

function renderVision(state) {
  const omni = component(state, "omniparser");
  const rf = component(state, "rfdetr");
  const ground = component(state, "groundingdino");
  return heading("VISION", "Vision Studio", "Tải ảnh/screenshot vào Hub, chạy parser hoặc detector, xem JSON và artifact ngay trong cửa sổ này.") + `
    <div class="capability-grid">${capability("OmniParser", omni, "Parse UI, vùng tương tác và ảnh annotation.", tool(state, "parse_screen"))}${capability("RF-DETR", rf, "Phát hiện object theo threshold.", tool(state, "detect_objects"))}${capability("Grounding DINO", ground, "Prompt → boxes, có thể dùng lại trong SAM2.", tool(state, "ground_objects"))}</div>
    <div class="workspace-grid workspace-grid--three" style="margin-top:16px">
      ${card("OmniParser", `<form data-job-form data-tool="parse_screen" class="stack">${file("Ảnh hoặc screenshot", "asset_id", "image/*")}${field("Box threshold", `<input name="box_threshold" type="number" min="0" max="1" step="0.01" value="0.05" />`)}<div class="form-actions">${button("Phân tích UI", "button--primary")}</div>${formResult("vision-omni-result")}</form>`)}
      ${card("RF-DETR", `<form data-job-form data-tool="detect_objects" class="stack">${file("Ảnh/video", "asset_id", "image/*,video/*")}${field("Detection threshold", `<input name="threshold" type="number" min="0" max="1" step="0.01" value="0.50" />`)}<div class="form-actions">${button("Phát hiện object", "button--primary")}</div>${formResult("vision-rf-result")}</form>`)}
      ${card("Grounding DINO", `<form data-job-form data-tool="ground_objects" class="stack">${file("Ảnh", "asset_id", "image/*")}${field("Prompt", `<input name="prompt" required placeholder="person . bag ." />`)}<div class="form-grid">${field("Box", `<input name="box_threshold" type="number" step="0.01" value="0.35" />`)}${field("Text", `<input name="text_threshold" type="number" step="0.01" value="0.25" />`)}</div><div class="form-actions">${button("Tạo boxes", "button--primary")}</div>${formResult("vision-ground-result")}</form>`)}
    </div>`;
}

function renderSam2(state) {
  const item = component(state, "sam2");
  const pointTool = tool(state, "segment_from_points");
  const pointStatus = pointTool.tool_status || item.component_status || "missing";
  return heading("VISION", "SAM2", "Phân vùng và theo dõi trực tiếp qua trình xử lý SAM2; SAM2 Mask Studio không còn là workflow chính.", statusPill(pointStatus, `Chọn điểm: ${formatStatus(pointStatus)}`)) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Đầu vào và prompt", `<form data-job-form data-tool="segment_from_points" data-tool-by-field="mode" data-tool-map='{"points":"segment_from_points","box":"segment_from_box","track":"track_video_object","text":"segment_from_text"}' class="stack">${file("Ảnh hoặc video", "asset_id", "image/*,video/*")}${field("Chế độ", `<select name="mode"><option value="points">Chọn điểm</option><option value="box">Chọn box</option><option value="text">Prompt Grounding → SAM2</option><option value="track">Theo dõi video</option></select>`)}${field("Điểm (x,y,nhãn; …)", `<input name="points_text" placeholder="320,240,1; 100,80,0" />`)}${field("Box (x1,y1,x2,y2)", `<input name="box_text" placeholder="80,60,600,500" />`)}${field("Prompt Grounding", `<input name="prompt" placeholder="person . object ." />`)}<div class="form-actions">${button("Tạo mask / track", "button--primary")}</div>${formResult("sam2-result")}</form>`)}
      ${card("Xem trước và kết quả", `<div class="preview-empty"><span>◒</span><strong>Xem trước mask sẽ xuất hiện trong Jobs</strong><p>Điểm/box đi vào trình xử lý trực tiếp. Model nạp theo yêu cầu và giải phóng khi job xong.</p></div><div class="callout">Trạng thái ở tiêu đề áp dụng riêng cho chế độ Chọn điểm. Box, prompt text và theo dõi chỉ sẵn sàng sau smoke riêng; nếu backend báo một phần/chưa khả dụng, lỗi sẽ hiện cạnh action thay vì mở GUI ngoài.</div>`, "", "card--flat")}
    </div>`;
}

function renderOcr(state) {
  const item = component(state, "paddleocr_vl");
  return heading("DOCUMENTS", "OCR", "Đọc ảnh, PDF, clipboard export hoặc tài liệu từ một workspace; kết quả text/Markdown/JSON/tables là artifact Hub.", statusPill(tool(state, "ocr_document").tool_status || item.component_status || "missing")) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Tải tài liệu", `<form data-job-form data-tool="ocr_document" class="stack">${file("Ảnh hoặc PDF", "asset_id", "image/*,application/pdf")}${field("Output", `<select name="output_format"><option value="all">Text + Markdown + JSON + Tables</option><option value="markdown">Markdown</option><option value="json">JSON</option></select>`)}<div class="form-actions">${button("Chạy OCR", "button--primary")}</div>${formResult("ocr-result")}</form>`)}
      ${card("Kết quả", `<div class="preview-empty"><span>▤</span><strong>Không mở app OCR riêng</strong><p>Chọn tệp, chạy worker, rồi mở artifact trong bảng Jobs.</p></div><div class="tag-list"><span class="tag">Image</span><span class="tag">PDF</span><span class="tag">Clipboard export</span><span class="tag">Folder batch qua hàng đợi</span></div>`, "", "card--flat")}
    </div>`;
}

function renderWhisper(state) {
  const item = component(state, "whisper");
  return heading("SPEECH", "Whisper / Subtitles", "Thêm media, chọn ngôn ngữ và xuất transcript/SRT hoặc burn subtitle bằng worker nền.", statusPill(tool(state, "transcribe_media").tool_status || item.component_status || "missing")) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Transcript queue", `<form data-job-form data-tool="transcribe_media" data-tool-by-field="workflow" data-tool-map='{"transcribe":"transcribe_media","burn":"create_subtitled_video"}' class="stack">${file("Video hoặc audio", "asset_id", "audio/*,video/*")}${field("Workflow", `<select name="workflow"><option value="transcribe">Transcript + SRT</option><option value="burn">Transcript + burn subtitle</option></select>`)}<div class="form-grid">${field("Language", `<input name="language" value="auto" placeholder="auto / vi / ja" />`)}${field("Thiết bị", `<select name="device"><option value="cpu">CPU safe</option><option value="cuda">CUDA nếu environment hỗ trợ</option></select>`)}</div>${field("Đoạn ngắn bắt đầu/kết thúc (giây, tùy chọn)", `<div class="inline-fields"><input name="start" type="number" min="0" step="0.1" value="0" /><input name="end" type="number" min="0.1" step="0.1" value="10" /></div>`)}<div class="form-actions">${button("Thêm vào hàng đợi", "button--primary")}</div>${formResult("whisper-result")}</form>`)}
      ${card("Điều khiển", `<ul class="notice-list"><li>Output không ghi đè source media.</li><li>Cancel/resume dùng Jobs và chỉ dừng process do Hub sở hữu.</li><li>Folder batch được lên kế hoạch qua nhiều job upload; không cần mở Whisper GUI.</li></ul><div class="preview-empty compact"><span>≋</span><strong>Transcript, SRT và video subtitle xuất hiện ở Jobs</strong></div>`, "", "card--flat")}
    </div>`;
}

function renderVoice(state) {
  const tts = component(state, "qwen3_tts");
  const seed = component(state, "seed_vc");
  return heading("VOICE", "Voice Studio", "Qwen3-TTS và Seed-VC chạy bằng worker nền trong Hub, không mở secondary Voice GUI.") + `
    <div class="capability-grid">${capability("Qwen3-TTS", tts, "Text to Speech, Voice Design, Voice Clone, Batch.", tool(state, "text_to_speech"))}${capability("Seed-VC", seed, "Voice Conversion với source/target audio.", tool(state, "convert_voice"))}</div>
    <div class="workspace-grid workspace-grid--three" style="margin-top:16px">
      ${card("Text to Speech", `<form data-job-form data-tool="text_to_speech" class="stack">${field("Text", `<textarea name="text" required placeholder="Nhập nội dung cần đọc…"></textarea>`)}<div class="form-grid">${field("Language", `<input name="language" value="Vietnamese" />`)}${field("Speaker", `<input name="speaker" value="Ryan" />`)}</div><div class="form-actions">${button("Tạo giọng nói", "button--primary")}</div>${formResult("tts-result")}</form>`)}
      ${card("Voice Design / Clone", `<form data-job-form data-tool="design_voice" data-tool-by-field="operation" data-tool-map='{"design":"design_voice","clone":"clone_voice"}' class="stack">${field("Operation", `<select name="operation"><option value="design">Voice Design</option><option value="clone">Voice Clone</option></select>`)}${field("Text", `<textarea name="text" required placeholder="Nội dung đầu ra…"></textarea>`)}${file("Reference audio (chỉ Voice Clone)", "reference_asset_id", "audio/*")}${field("Reference text", `<input name="reference_text" placeholder="Tùy chọn" />`)}<div class="form-actions">${button("Chạy Qwen3-TTS", "button--primary")}</div>${formResult("voice-design-result")}</form>`)}
      ${card("Voice Conversion", `<form data-job-form data-tool="convert_voice" class="stack">${file("Source audio", "source_asset_id", "audio/*")}${file("Target voice", "target_asset_id", "audio/*")}${field("Diffusion steps", `<input name="diffusion_steps" type="number" min="1" max="50" value="4" />`)}<div class="form-actions">${button("Chuyển giọng", "button--primary")}</div>${formResult("seed-result")}</form>`)}
    </div>`;
}

function renderImageQuickV5(state) {
  const comfy = state.lifecycle?.comfyui || {};
  const imageTool = tool(state, "generate_flux");
  return heading("IMAGE", "Image AI", "Quick tạo FLUX/Qwen qua ComfyUI API ẩn. Hub Nodes dùng graph editor kéo socket thật; Advanced hiển thị ComfyUI trong chính cửa sổ này.", `<span class="status-pill" data-status="${escapeHtml(imageTool.tool_status || comfy.status || "stopped")}">${escapeHtml(formatStatus(imageTool.tool_status || comfy.status || "stopped"))}</span>`) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Quick", `<form data-job-form data-tool="generate_flux" data-tool-by-field="engine" data-tool-map='{"flux":"generate_flux","qwen":"generate_qwen_image"}' class="stack">${field("Model", `<select name="engine"><option value="flux">FLUX.2 Klein</option><option value="qwen">Qwen Image 2512</option></select>`)}${field("Prompt", `<textarea name="prompt" required placeholder="Mô tả ảnh cần tạo…"></textarea>`)}${field("Negative prompt", `<input name="negative_prompt" placeholder="Tùy chọn" />`)}<div class="form-grid">${field("Width", `<input name="width" type="number" min="256" max="2048" step="64" value="768" />`)}${field("Height", `<input name="height" type="number" min="256" max="2048" step="64" value="768" />`)}${field("Steps", `<input name="steps" type="number" min="1" max="80" value="20" />`)}${field("Seed", `<input name="seed" type="number" min="0" placeholder="random" />`)}</div>${file("Input image (FLUX image-edit nếu workflow hỗ trợ)", "input_image_asset_id", "image/*")}<div class="form-actions">${button("Generate trong Hub", "button--primary")}</div>${formResult("image-result")}</form>`) }
      ${card("Chế độ làm việc", `<div class="preview-empty"><span>✦</span><strong>Không mở trình duyệt ngoài</strong><p>Chọn Hub Nodes để nối typed socket bằng kéo-thả. Chọn ComfyUI Advanced để sửa graph ComfyUI thật trong WebView của Local AI Hub.</p></div><div class="form-actions"><button class="button" type="button" data-workspace-tab="image:nodes">Mở Hub Nodes</button><button class="button button--primary" type="button" data-workspace-tab="image:advanced">Mở ComfyUI Advanced</button></div>`, "", "card--flat")}
    </div>`;
}

function renderComfyAdvancedV5(state) {
  const comfy = state.comfyAdvanced?.comfyui || state.lifecycle?.comfyui || {};
  const workflows = state.comfyWorkflows || [];
  const candidate = String(comfy.advanced_url || "");
  const url = /^http:\/\/127\.0\.0\.1:\d+$/.test(candidate) ? candidate : "";
  const frame = url
    ? `<iframe class="comfy-advanced__frame" data-comfy-frame src="${escapeHtml(url)}" title="ComfyUI Advanced in Local AI Hub" sandbox="allow-scripts allow-same-origin allow-forms allow-downloads" referrerpolicy="no-referrer"></iframe>`
    : `<div class="preview-empty"><span>✦</span><strong>ComfyUI chưa chạy</strong><p>Bấm Khởi động để Hub mở ComfyUI thành backend ẩn rồi nhúng editor thật ở đây. Không mở Chrome hoặc Edge ngoài.</p></div>`;
  return heading("IMAGE / ADVANCED", "ComfyUI Advanced", "ComfyUI frontend gốc được nhúng trong WebView Local AI Hub. Hub không sao chép hoặc triển khai lại frontend ComfyUI.", `<button class="button" type="button" data-workspace-tab="image:quick">← Back to Hub</button>`) + `
    <section class="comfy-advanced" data-comfy-advanced>
      <div class="comfy-advanced__toolbar"><div><strong>${escapeHtml(comfy.status || "stopped")}</strong><span>${comfy.hub_owned ? " · Hub-owned hidden backend" : " · existing local backend"}</span></div><div class="form-actions"><button class="button button--primary" type="button" data-comfy-action="start">Khởi động / làm mới ComfyUI</button><button class="button" type="button" data-workspace-tab="image:quick">Back to Hub</button></div></div>
      <div class="comfy-advanced__layout">
        <aside class="comfy-bridge"><h2>Workflow bridge</h2><p>List/load/save JSON local cho node <code>ComfyUI Workflow</code>. Bridge không nhận raw path và không được commit vào Git.</p><label class="field"><span>Đã lưu</span><select data-comfy-workflow-select><option value="">Chọn workflow…</option>${workflows.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}${item.local ? " · local" : ""}</option>`).join("")}</select></label><div class="form-actions"><button class="button" type="button" data-comfy-action="load">Load JSON</button></div><label class="field"><span>ID khi lưu local</span><input data-comfy-workflow-id placeholder="my_flux_mask" /></label><label class="field"><span>Bridge JSON</span><textarea data-comfy-workflow-json rows="18" placeholder='{"schema_version":1,"id":"my_flux_mask","kind":"raw_comfy_api",…}'></textarea></label><div class="form-actions"><button class="button" type="button" data-comfy-action="save">Save local bridge</button></div><small>Quick FLUX/Qwen compile qua API. Bridge raw chỉ nhận binding có schema và lưu dưới user-data local.</small></aside>
        <div class="comfy-advanced__frame-wrap">${frame}</div>
      </div>
    </section>`;
}

function renderImage(state) {
  const comfy = state.lifecycle?.comfyui || {};
  const imageTool = tool(state, "generate_flux");
  return heading("IMAGE", "Image AI", "FLUX và Qwen Image gọi ComfyUI API trực tiếp. ComfyUI chạy nền ẩn khi Hub cần, không mở Local Image Studio.", `<span class="status-pill" data-status="${escapeHtml(imageTool.tool_status || comfy.status || "stopped")}">${escapeHtml(formatStatus(imageTool.tool_status || comfy.status || "stopped"))}</span>`) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Generate", `<form data-job-form data-tool="generate_flux" data-tool-by-field="engine" data-tool-map='{"flux":"generate_flux","qwen":"generate_qwen_image"}' class="stack">${field("Model", `<select name="engine"><option value="flux">FLUX.2 Klein</option><option value="qwen">Qwen Image 2512</option></select>`)}${field("Prompt", `<textarea name="prompt" required placeholder="Mô tả ảnh cần tạo…"></textarea>`)}${field("Negative prompt", `<input name="negative_prompt" placeholder="Tùy chọn" />`)}<div class="form-grid">${field("Width", `<input name="width" type="number" min="256" max="2048" step="64" value="768" />`)}${field("Height", `<input name="height" type="number" min="256" max="2048" step="64" value="768" />`)}${field("Steps", `<input name="steps" type="number" min="1" max="80" value="20" />`)}${field("Seed", `<input name="seed" type="number" min="0" placeholder="random" />`)}</div>${file("Input image (FLUX image-edit nếu workflow hỗ trợ)", "input_image_asset_id", "image/*")}<div class="form-actions">${button("Generate trong Hub", "button--primary")}</div>${formResult("image-result")}</form>`)}
      ${card("Preview & Advanced", `<div class="preview-empty"><span>✦</span><strong>Ảnh output sẽ hiện trong Jobs</strong><p>Lịch sử giữ prompt, seed, model qua metadata job an toàn; không lộ đường dẫn cục bộ.</p></div><details class="advanced"><summary>ComfyUI Advanced</summary><p>Advanced mở web interface ComfyUI trong trình duyệt nếu cần sửa workflow; normal workflow vẫn dùng form Hub ở bên trái.</p><button class="button" type="button" data-open-comfy>Open ComfyUI web interface</button></details>`, "", "card--flat")}
    </div>`;
}

function renderMedia(state) {
  const item = component(state, "ffmpeg");
  return heading("MEDIA", "Trình biên tập media", "FFmpeg/FFprobe chạy bằng danh sách lệnh cho phép ở chế độ ẩn, không có ô shell và không ghi đè media nguồn.", statusPill(tool(state, "run_media_operation").tool_status || item.component_status || "missing")) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Thao tác video và ảnh", `<form data-job-form data-tool="run_media_operation" class="stack">${file("Media đầu vào chính", "asset_id", "audio/*,video/*,image/*")}${files("Đầu vào bổ sung (ghép / chuỗi ảnh)", "input_asset_ids", "video/*,image/*")}${file("Audio hoặc phụ đề thứ hai", "secondary_asset_id", "audio/*,.srt,.ass")}${field("Thao tác", `<select name="operation"><option value="probe">Đọc metadata</option><option value="trim">Cắt đầu/cuối</option><option value="concat">Ghép video</option><option value="resize">Đổi kích thước video</option><option value="crop">Cắt khung video</option><option value="rotate">Xoay video</option><option value="fps">FPS</option><option value="transcode">Chuyển mã</option><option value="extract_audio">Tách audio</option><option value="replace_audio">Thay audio</option><option value="mux">Mux audio/video</option><option value="burn_subtitle">Chèn phụ đề</option><option value="extract_frames">Tách frame</option><option value="image_sequence_video">Chuỗi ảnh → video</option><option value="image_resize">Đổi kích thước ảnh</option><option value="image_crop">Cắt khung ảnh</option><option value="image_rotate">Xoay ảnh</option><option value="image_flip">Lật ảnh</option><option value="image_convert">Đổi định dạng ảnh</option><option value="image_compress">Nén ảnh</option></select>`)}<div class="form-grid">${field("Bắt đầu", `<input name="start" type="number" min="0" step="0.1" value="0" />`)}${field("Kết thúc", `<input name="end" type="number" min="0.1" step="0.1" value="5" />`)}${field("Chiều rộng", `<input name="width" type="number" min="2" value="1280" />`)}${field("Chiều cao", `<input name="height" type="number" min="-2" value="-2" />`)}${field("FPS", `<input name="fps" type="number" min="1" value="30" />`)}${field("Xoay", `<select name="degrees"><option value="90">90°</option><option value="180">180°</option><option value="270">270°</option></select>`)}${field("Lật", `<select name="axis"><option value="horizontal">Ngang</option><option value="vertical">Dọc</option></select>`)}${field("Định dạng ảnh", `<select name="format"><option value="png">PNG</option><option value="jpg">JPG</option><option value="webp">WEBP</option></select>`)}</div><div class="form-actions">${button("Chạy FFmpeg", "button--primary")}</div>${formResult("media-result")}</form>`)}
      ${card("An toàn thao tác", `<ul class="notice-list"><li>Kết quả tạo trong vùng Output/Media của Hub.</li><li>Đọc metadata là read-only; tác vụ ghi file đi qua Job Manager.</li><li>Ghép video và chuỗi ảnh chỉ nhận artifact đã tải lên Hub, rồi tạo manifest Temp ngắn hạn; không nhận raw shell/path list.</li></ul><div class="tag-list"><span class="tag">Cắt</span><span class="tag">Ghép</span><span class="tag">Crop</span><span class="tag">Xoay</span><span class="tag">Mux</span><span class="tag">Chèn phụ đề</span><span class="tag">Frames</span></div>`, "", "card--flat")}
    </div>`;
}

function renderAnime(state) {
  const item = component(state, "animesr");
  return heading("VIDEO AI", "AnimeSR", "Workflow upscale chính chạy bằng worker AnimeSR trong Hub. Anime Upscale Studio chỉ là legacy/debug fallback, không còn là action chính.", statusPill(tool(state, "upscale_anime_video").tool_status || item.component_status || "missing")) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Upscale queue", `<form data-job-form data-tool="upscale_anime_video" class="stack">${file("Video input", "asset_id", "video/*")}${field("Model", `<select name="model"><option value="AnimeSR_v2">AnimeSR v2</option><option value="AnimeSR_v1-PaperModel">AnimeSR v1 Paper</option></select>`)}<div class="form-grid">${field("Scale", `<select name="scale"><option value="2">2×</option><option value="3">3×</option><option value="4">4×</option></select>`)}${field("Chunk seconds", `<input name="chunk_seconds" type="number" min="10" value="120" />`)}</div><div class="check-grid"><label><input name="half" type="checkbox" checked /> Half precision</label><label><input name="use_rife" type="checkbox" /> RIFE (partial)</label><label><input name="use_realesrgan" type="checkbox" /> Real-ESRGAN (partial)</label></div><div class="form-actions">${button("Thêm AnimeSR job", "button--primary")}</div>${formResult("anime-result")}</form>`)}
      ${card("Progress & output", `<div class="preview-empty"><span>⇱</span><strong>Queue, progress, cancel và resume nằm ở Jobs</strong><p>Hub không mở Anime Upscale Studio để chạy normal workflow.</p></div><details class="advanced"><summary>Advanced / legacy</summary><p>Legacy Studio chỉ nên dùng debug khi direct worker báo limitation đã được ghi nhận.</p></details>`, "", "card--flat")}
    </div>`;
}

function renderJobs(state) {
  const jobs = state.jobs || [];
  const rows = jobs.map((job) => {
    const actions = ["queued", "starting", "running", "cancelling"].includes(job.status)
      ? `<button class="button button--compact button--danger" type="button" data-cancel-job="${escapeHtml(job.id)}">Hủy</button>`
      : job.resumable ? `<button class="button button--compact" type="button" data-resume-job="${escapeHtml(job.id)}">Resume</button>` : "";
    return `<article class="job-card"><div class="split"><div><strong>${escapeHtml(job.tool)}</strong><div class="row-meta">${escapeHtml(job.id)} · ${escapeHtml(job.created_at || "")}</div></div>${statusPill(job.status)}</div><div class="progress-track"><div class="progress-bar" style="width:${Math.max(0, Math.min(100, Number(job.progress || 0)))}%"></div></div><p class="job-message">${escapeHtml(job.message || job.error || "")}</p>${artifactList(job.result)}<div class="form-actions">${actions}</div></article>`;
  }).join("");
  return heading("CONTROL PLANE", "Jobs", "Theo dõi job thực tế do Hub tạo; cancel không ảnh hưởng Python/ComfyUI/AIRI không thuộc Hub.") + `<div class="job-list">${rows || `<div class="empty-state">Chưa có job. Chạy một workflow từ module bất kỳ để bắt đầu.</div>`}</div>`;
}

function renderModels(state) {
  const storage = state.storage || {};
  const models = state.models || [];
  const areas = Object.entries(storage.areas || {});
  return heading("STORAGE", "Models & Storage", "Model store canonical không nhân bản. Legacy cleanup chỉ xử lý mục đã phân loại và xác minh, không tự xoá UNKNOWN hoặc user media.", `<button class="button" type="button" data-refresh-storage>Quét lại</button>`) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Dung lượng", `<div class="row-list">${areas.map(([name, value]) => `<div class="row-item"><span>${escapeHtml(name)}</span><strong>${formatGb(value.bytes)}</strong></div>`).join("") || `<div class="empty-state compact">Chưa có số liệu storage.</div>`}</div>`)}
      ${card("Legacy cleanup", `<div class="metric-inline"><strong>${escapeHtml(storage.legacy_counts?.total || 0)}</strong><span>legacy paths đã inventory</span></div><div class="callout callout--warning">Cleanup V3 tách REAL_DIRECTORY/JUNCTION, kiểm tra reference và user data trước. Mục active hoặc unknown sẽ được giữ cùng lý do/rollback.</div>`, "", "card--flat")}
    </div>
    ${card("Model registry", models.length ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th>Engine</th><th>Size</th><th>Status</th></tr></thead><tbody>${models.map((item) => `<tr><td>${escapeHtml(item.model_name)}</td><td>${escapeHtml(item.engine)}</td><td>${formatGb(item.size?.bytes)}</td><td>${statusPill(item.installed ? "installed" : "not_installed")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state compact">Chưa có model registry.</div>`, "", "card--wide")}`;
}

function renderSettings(state) {
  const settings = state.settings || {};
  return heading("SYSTEM", "Settings", "Cấu hình startup, chính sách GPU, storage và advanced integrations. Không hiển thị secrets hay local machine paths.") + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Appearance & startup", `<div class="row-list"><div class="row-item"><span>Start maximized</span><strong>${settings.start_maximized ? "Bật" : "Tắt"}</strong></div><div class="row-item"><span>Minimum window</span><strong>${escapeHtml(settings.minimum_width || 1280)} × ${escapeHtml(settings.minimum_height || 720)}</strong></div><div class="row-item"><span>Theme</span><button class="button button--compact" type="button" data-cycle-theme>Đổi theme</button></div></div>`)}
      ${card("Workers & lifecycle", `<div class="row-list"><div class="row-item"><span>Model policy</span><strong>${escapeHtml(settings.model_load_policy || "on_demand")}</strong></div><div class="row-item"><span>Heavy GPU slots</span><strong>${escapeHtml(settings.max_heavy_gpu_jobs || 1)}</strong></div><div class="row-item"><span>ComfyUI port</span><strong>${escapeHtml(settings.comfyui_port || 8188)}</strong></div></div><div class="form-actions"><button class="button" type="button" data-close-backends>Đóng backend Hub-owned rảnh</button></div>`)}
      ${card("Storage safety", `<ul class="notice-list"><li>Không ghi đè source media.</li><li>Không duplicate model multi-GB.</li><li>Không tự xoá user media hoặc unknown legacy data.</li><li>AIRI giữ external/installer-managed.</li></ul>`, "", "card--flat")}
      ${card("Advanced legacy", `<details class="advanced"><summary>Legacy applications</summary><p>SAM2 Mask Studio, Anime Upscale Studio và Local Image Studio không nằm trong normal workflow. Giữ lại làm fallback/debug sau khi direct worker được đánh giá.</p></details>`, "", "card--flat")}
    </div>`;
}

export function renderPage(route, state) {
  const pages = { dashboard: renderDashboard, airi: renderAiri, vision: renderVision, sam2: renderSam2, ocr: renderOcr, whisper: renderWhisper, voice: renderVoice, image: renderImageQuickV5, media: renderMedia, animesr: renderAnime, jobs: renderJobs, models: renderModels, settings: renderSettings };
  const nodeCopy = {
    image: "Compose FLUX/Qwen, SAM2 mask và image transforms trong cùng graph; preset JSON được track, workflow cá nhân autosave local.",
    sam2: "Advanced workflow: Grounding DINO → SAM2 → mask/composite/export. GPU nodes chỉ chạy khi bấm Run Graph.",
    media: "Build Trim/Crop/Audio/Interpolation/Encode graphs. Encode chỉ hiện capability FFmpeg thực tế.",
    animesr: "Advanced order do bạn chọn: Load → AnimeSR → Frame Interpolation → Encode. AnimeSR/RIFE vẫn partial cho tới smoke riêng.",
  };
  const nodeScope = Object.prototype.hasOwnProperty.call(nodeCopy, route) ? route : null;
  if (route === "image" && activeTab(state, "image") === "advanced") {
    return `${imageModuleTabs(state)}${renderComfyAdvancedV5(state)}`;
  }
  if (route === "image" && activeTab(state, "image") === "nodes") {
    return `${heading("ADVANCED WORKFLOW", "Image AI Hub Nodes", "Kéo socket trực tiếp, typed sockets, minimap, multi-select và live preview.")}${imageModuleTabs(state)}${nodeStudio("image", nodeCopy.image)}`;
  }
  if (route === "image") {
    return `${imageModuleTabs(state)}${renderImageQuickV5(state)}`;
  }
  if (nodeScope && activeTab(state, nodeScope) === "nodes") {
    return `${heading("ADVANCED WORKFLOW", `${nodeScope === "sam2" ? "SAM2" : nodeScope === "animesr" ? "AnimeSR" : nodeScope === "media" ? "Media" : "Image AI"} Nodes`, "Node editor chạy offline trong cửa sổ Local AI Hub.")}${moduleTabs(state, nodeScope)}${nodeStudio(nodeScope, nodeCopy[nodeScope])}`;
  }
  const page = (pages[route] || renderDashboard)(state);
  return nodeScope ? `${moduleTabs(state, nodeScope)}${page}` : page;
}
