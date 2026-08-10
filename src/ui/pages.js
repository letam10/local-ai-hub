import { escapeHtml, formatGb, formatStatus } from "./api.js";

export const NAVIGATION = [
  { group: "TỔNG QUAN", items: [["dashboard", "Dashboard", "◫"], ["airi", "AIRI", "◌"]] },
  { group: "VISION & DOCUMENT", items: [["vision", "Vision Studio", "◉"], ["sam2", "SAM2", "◒"], ["ocr", "OCR", "▤"]] },
  { group: "SPEECH & VOICE", items: [["whisper", "Whisper", "≋"], ["voice", "Voice", "♪"]] },
  { group: "IMAGE & VIDEO", items: [["image", "Image AI", "✦"], ["media", "Media", "▹"], ["video", "Video Creative", "▶"], ["animesr", "AnimeSR", "⇱"]] },
  { group: "CREATIVE WORKSPACE", items: [["projects", "Projects & Recipes", "▧"]] },
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
const capability = (name, item, note, direct = {}) => `<div class="capability"><div><strong>${escapeHtml(name)}</strong><p>${escapeHtml(note)}</p>${direct.reason ? `<p>${escapeHtml(direct.reason)}</p>` : ""}${direct.action ? `<p class="capability-action"><strong>Bước tiếp theo:</strong> ${escapeHtml(direct.action)}</p>` : ""}</div>${statusPill(direct.tool_status || item.component_status || item.status || "missing")}</div>`;
const workspaceState = (label, item = {}, fallbackAction = "Kiểm tra backend rồi thử lại trong Jobs.") => {
  const status = item.tool_status || item.status || item.component_status || "missing";
  const reason = item.reason || "Chưa có snapshot readiness cho backend này.";
  const action = item.action || fallbackAction;
  return `<section class="workspace-state" data-status="${escapeHtml(status)}" aria-live="polite"><div class="workspace-state__head"><div><span class="eyebrow">BACKEND CONTRACT</span><h2>${escapeHtml(label)}</h2></div>${statusPill(status)}</div><p>${escapeHtml(reason)}</p><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(action)}</span></div></section>`;
};
const activeTab = (state, module) => state.workspaceTabs?.[module] || "quick";
const moduleTabs = (state, module) => {
  const selected = activeTab(state, module);
  return `<div class="module-tabs" role="tablist" aria-label="${escapeHtml(module)} workspace"><button class="tab ${selected === "quick" ? "is-selected" : ""}" type="button" data-workspace-tab="${escapeHtml(module)}:quick" role="tab" aria-selected="${selected === "quick"}">Quick</button><button class="tab ${selected === "nodes" ? "is-selected" : ""}" type="button" data-workspace-tab="${escapeHtml(module)}:nodes" role="tab" aria-selected="${selected === "nodes"}">Nodes</button></div>`;
};
const imageModuleTabs = (state) => {
  const selected = activeTab(state, "image");
  return `<div class="module-tabs" role="tablist" aria-label="Image AI workspace"><button class="tab ${selected === "quick" ? "is-selected" : ""}" type="button" data-workspace-tab="image:quick" role="tab" aria-selected="${selected === "quick"}">Quick</button><button class="tab ${selected === "studio" ? "is-selected" : ""}" type="button" data-workspace-tab="image:studio" role="tab" aria-selected="${selected === "studio"}">Chỉnh sửa & Mask</button><button class="tab ${selected === "nodes" ? "is-selected" : ""}" type="button" data-workspace-tab="image:nodes" role="tab" aria-selected="${selected === "nodes"}">Hub Nodes</button><button class="tab ${selected === "advanced" ? "is-selected" : ""}" type="button" data-workspace-tab="image:advanced" role="tab" aria-selected="${selected === "advanced"}">ComfyUI Advanced</button></div>`;
};
const nodeStudio = (scope, description) => `<section class="node-studio-wrap"><div class="node-studio-intro"><div><strong>Node Studio</strong><p>${escapeHtml(description)}</p></div><span class="tag">offline · typed sockets · DAG</span></div><div data-node-studio data-scope="${escapeHtml(scope)}"></div></section>`;
const imageWorkflowRail = (state) => {
  const generation = tool(state, "generate_flux");
  const edit = tool(state, "generate_qwen_image");
  const transform = tool(state, "run_media_operation");
  return `<section class="image-workflow-rail" aria-label="Image workflow">
    <div class="image-workflow-rail__intro"><div><span class="eyebrow">IMAGE FOUNDATION</span><h2>Luồng ảnh end-to-end</h2><p>Chọn template trong Hub Nodes để nối Prompt → Generate/Edit → Upscale → Preview/Save. Mỗi backend hiển thị đúng partial/unavailable nếu chưa smoke.</p></div><span class="tag">artifact · job · provenance</span></div>
    <div class="image-workflow-rail__steps">
      <article><span>01</span><strong>Tạo ảnh</strong><small>FLUX / ComfyUI</small>${statusPill(generation.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="image:nodes">Mở template</button></article>
      <article><span>02</span><strong>Chỉnh ảnh</strong><small>Qwen Image-to-Image</small>${statusPill(edit.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="image:nodes">Mở template</button></article>
      <article><span>03</span><strong>Upscale</strong><small>FFmpeg fallback / AI partial</small>${statusPill(transform.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="image:nodes">Mở template</button></article>
    </div>
  </section>`;
};
const videoWorkflowRail = (state) => {
  const transform = tool(state, "run_media_operation");
  const upscale = tool(state, "upscale_anime_video");
  return `<section class="video-workflow-rail" aria-label="Video workflow">
    <div class="video-workflow-rail__intro"><div><span class="eyebrow">VIDEO CREATIVE</span><h2>Luồng video trong Hub</h2><p>Chọn template để nối video artifact hoặc prompt → transform/generation → upscale → interpolate → encode → preview/export.</p></div><span class="tag">job · progress · provenance</span></div>
    <div class="video-workflow-rail__steps">
      <article><span>01</span><strong>Transform</strong><small>FFmpeg allowlist</small>${statusPill(transform.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="media:nodes">Mở template</button></article>
      <article><span>02</span><strong>Upscale / FPS</strong><small>FFmpeg fallback · AnimeSR</small>${statusPill(upscale.tool_status || "partial")}<button class="button button--compact" type="button" data-workspace-tab="media:nodes">Mở template</button></article>
      <article><span>03</span><strong>Prompt Generate</strong><small>Backend video chưa có</small>${statusPill("unavailable")}<button class="button button--compact" type="button" data-workspace-tab="media:nodes">Xem contract</button></article>
    </div>
  </section>`;
};

function artifacts(value, found = []) {
  if (!value) return found;
  if (Array.isArray(value)) value.forEach((item) => artifacts(item, found));
  else if (typeof value === "object") {
    const id = value.id || value.artifact_id;
    if (id && value.url && !found.some((item) => item.id === id)) found.push({ ...value, id });
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
    return `<div class="artifact-item">${isImage ? `<img class="artifact-preview" src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}" />` : ""}<div class="row-main"><div class="row-name">${escapeHtml(item.name)}</div><div class="row-meta">${escapeHtml(item.media_type || "artifact")} · ${formatGb(item.size_bytes)}</div></div>${isMedia ? `<button class="button button--compact" type="button" data-preview-artifact="${escapeHtml(item.id)}" data-artifact-url="${escapeHtml(item.url)}" data-artifact-name="${escapeHtml(item.name)}" data-artifact-type="${escapeHtml(item.media_type || "application/octet-stream")}">Xem</button>` : ""}<a class="button button--compact" href="${escapeHtml(item.url)}" download="${escapeHtml(item.name || "artifact")}">Lưu/Xuất</a><button class="button button--compact" type="button" data-open-artifact="${escapeHtml(item.id)}">Mở</button></div>`;
  }).join("")}</div>`;
};

const provenanceList = (job) => {
  const items = job?.result?.provenance || job?.provenance || [];
  if (!Array.isArray(items) || !items.length) return "";
  return `<details class="job-provenance"><summary>Provenance · ${items.length} artifact</summary><div class="tag-list">${items.map((item) => `<span class="tag">${escapeHtml(item.node_type || "node")} → ${escapeHtml(item.name || item.artifact_id || "artifact")}</span>`).join("")}</div></details>`;
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
  return heading("VISION", "SAM2", "Phân vùng và theo dõi trực tiếp qua trình xử lý SAM2; SAM2 Mask Studio không còn là workflow chính.", statusPill(pointStatus, `Chọn điểm: ${formatStatus(pointStatus)}`)) + workspaceState("SAM2 direct worker", pointTool, "Chọn điểm hoặc box trên preview rồi kiểm tra mask artifact trong Jobs.") + `
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
  const recipe = state.pendingQuickRecipe || {};
  const selectedModel = recipe.model === "qwen" ? "qwen" : "flux";
  const settings = recipe.settings || {};
  const numeric = (key, fallback) => Number.isFinite(Number(settings[key])) ? Number(settings[key]) : fallback;
  const recipeNotice = state.pendingRecipeName ? `<div class="callout creative-applied-recipe" role="status"><strong>Recipe đang được áp dụng: ${escapeHtml(state.pendingRecipeName)}</strong><span>Prompt, style, negative block, seed và settings đã được đưa vào Quick. Chỉnh tiếp trước khi tạo job.</span></div>` : "";
  return heading("IMAGE", "Image AI", "Quick tạo FLUX/Qwen qua ComfyUI API ẩn. Hub Nodes dùng graph editor kéo socket; ComfyUI Advanced vẫn partial cho đến khi hoàn tất Windows WebView acceptance.", `<span class="status-pill" data-status="${escapeHtml(imageTool.tool_status || comfy.status || "stopped")}">${escapeHtml(formatStatus(imageTool.tool_status || comfy.status || "stopped"))}</span>`) + workspaceState("Image Generate / Edit", imageTool, "Chọn template Hub Nodes để nối prompt → generate/edit → upscale → preview.") + imageWorkflowRail(state) + recipeNotice + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Quick", `<form data-job-form data-tool="generate_flux" data-tool-by-field="engine" data-tool-map='{"flux":"generate_flux","qwen":"generate_qwen_image"}' class="stack">${field("Model", `<select name="engine"><option value="flux" ${selectedModel === "flux" ? "selected" : ""}>FLUX.2 Klein</option><option value="qwen" ${selectedModel === "qwen" ? "selected" : ""}>Qwen Image 2512</option></select>`)}${field("Prompt", `<textarea name="prompt" required placeholder="Mô tả ảnh cần tạo…">${escapeHtml(recipe.prompt || "")}</textarea>`)}${field("Negative prompt", `<input name="negative_prompt" placeholder="Tùy chọn" value="${escapeHtml(recipe.negative_prompt || "")}" />`)}<div class="form-grid">${field("Width", `<input name="width" type="number" min="256" max="2048" step="64" value="${numeric("width", 768)}" />`)}${field("Height", `<input name="height" type="number" min="256" max="2048" step="64" value="${numeric("height", 768)}" />`)}${field("Steps", `<input name="steps" type="number" min="1" max="80" value="${numeric("steps", 20)}" />`)}${field("Seed", `<input name="seed" type="number" min="0" placeholder="random" value="${Number.isInteger(recipe.seed) ? recipe.seed : ""}" />`)}</div>${file("Input image (FLUX image-edit nếu workflow hỗ trợ)", "input_image_asset_id", "image/*")}<div class="form-actions">${button("Generate trong Hub", "button--primary")}</div>${formResult("image-result")}</form>`) }
      ${card("Chế độ làm việc", `<div class="preview-empty"><span>✦</span><strong>Không mở trình duyệt ngoài</strong><p>Chỉnh sửa & Mask giữ layer, nét brush, snapshot và provenance theo contract không phá hủy. Chọn Hub Nodes để nối typed socket bằng kéo-thả. ComfyUI Advanced là bridge partial: chỉ dùng sau checklist Windows WebView, không được coi là operational chỉ vì iframe hiển thị.</p></div><div class="form-actions"><button class="button button--primary" type="button" data-workspace-tab="image:studio">Mở Chỉnh sửa & Mask</button><button class="button" type="button" data-workspace-tab="image:nodes">Mở Hub Nodes</button><button class="button" type="button" data-workspace-tab="image:advanced">Xem ComfyUI Advanced (partial)</button></div>`, "", "card--flat")}
    </div>`;
}

const maskStrokeOverlay = (layer) => (layer?.operations || []).filter((operation) => operation.operation === "brush" && Array.isArray(operation.points)).map((operation) => {
  const points = operation.points.map((point) => `${Math.max(0, Math.min(1, Number(point.x) || 0)) * 100},${Math.max(0, Math.min(1, Number(point.y) || 0)) * 100}`).join(" ");
  const width = Math.max(1.2, Math.min(24, (Number(operation.size) || 0.06) * 100));
  return `<polyline class="mask-studio-canvas__stroke ${operation.mode === "subtract" ? "is-subtract" : ""}" points="${points}" stroke-width="${width}" />`;
}).join("");

function renderImageMaskStudio(state) {
  const studio = state.imageMaskStudio || {};
  const sessions = studio.sessions || [];
  const detail = state.imageMaskSession?.session || null;
  const sourceAssets = (state.creative?.assets || []).filter((asset) => String(asset.media_type || "").startsWith("image/") && asset.available !== false);
  const projects = (state.creative?.projects || []).filter((project) => project.status === "active");
  const preflight = studio.preflight?.capabilities || state.imageMaskSession?.preflight?.capabilities || [];
  const selectedLayer = detail?.layers?.find((layer) => layer.id === (state.selectedImageMaskLayerId || detail.active_layer_id)) || detail?.layers?.at(-1) || null;
  const selectedMask = selectedLayer?.kind === "mask" ? selectedLayer : null;
  const compare = state.imageMaskCompare?.compare || null;
  const sourceOption = state.pendingImageMaskSourceId || "";
  const capabilityCards = preflight.map((item) => `<article class="mask-capability" data-status="${escapeHtml(item.status)}"><div class="split"><strong>${escapeHtml(item.title)}</strong>${statusPill(item.status)}</div><p>${escapeHtml(item.reason)}</p><small><strong>Bước tiếp theo:</strong> ${escapeHtml(item.action)}</small></article>`).join("");
  const sessionList = sessions.map((item) => `<button type="button" class="mask-session-card ${item.id === detail?.id ? "is-selected" : ""}" data-image-mask-open="${escapeHtml(item.id)}" aria-pressed="${item.id === detail?.id}"><span class="eyebrow">${escapeHtml(item.dirty ? "DRAFT CHƯA LƯU" : "ĐÃ LƯU LOCAL")}</span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.layer_count)} lớp · ${escapeHtml(item.mask_count)} mask · r${escapeHtml(item.revision)}</small></button>`).join("") || `<div class="empty-state compact"><strong>Chưa có phiên chỉnh sửa</strong><span>Chọn một artifact ảnh Hub hoặc tải ảnh lên để bắt đầu stack non-destructive.</span></div>`;
  const sourcePreview = detail?.source_artifact?.url
    ? `<div class="mask-studio-canvas ${selectedMask ? "is-editable" : ""}" ${selectedMask ? `data-mask-canvas data-studio-id="${escapeHtml(detail.id)}" data-layer-id="${escapeHtml(selectedMask.id)}" tabindex="0" role="application" aria-label="Canvas mask ${escapeHtml(selectedMask.name)}. Kéo để thêm hoặc trừ mask; Escape hủy nét đang vẽ."` : ""}><img src="${escapeHtml(detail.source_artifact.url)}" alt="Ảnh nguồn của ${escapeHtml(detail.title)}" /><svg class="mask-studio-canvas__overlay" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${selectedMask ? maskStrokeOverlay(selectedMask) : ""}</svg>${selectedMask ? `<span class="mask-studio-canvas__hint">Kéo để vẽ ${escapeHtml(selectedMask.name)} · Esc để hủy nét</span>` : `<span class="mask-studio-canvas__hint">Chọn một layer mask để vẽ metadata</span>`}</div>`
    : `<div class="preview-empty"><span>◌</span><strong>Ảnh nguồn không còn khả dụng</strong><p>Studio giữ bản nháp, nhưng không hiển thị raw path. Gắn lại artifact Hub hợp lệ trong một phiên mới.</p></div>`;
  const layerStack = detail?.layers?.slice().reverse().map((layer, reverseIndex) => {
    const index = (detail.layers.length - 1) - reverseIndex;
    const selected = layer.id === selectedLayer?.id;
    return `<article class="mask-layer ${selected ? "is-selected" : ""}" data-kind="${escapeHtml(layer.kind)}"><button type="button" class="mask-layer__select" data-image-mask-layer-select="${escapeHtml(layer.id)}" aria-pressed="${selected}"><span class="mask-layer__icon">${layer.kind === "source" ? "▣" : layer.kind === "mask" ? "◌" : layer.kind === "adjustment" ? "◐" : "✦"}</span><span><strong>${escapeHtml(layer.name)}</strong><small>${escapeHtml(layer.kind)}${layer.kind === "mask" ? ` · ${escapeHtml(layer.operation_count || 0)} thao tác` : ""}</small></span></button><div class="mask-layer__actions"><button class="button button--compact" type="button" data-image-mask-layer-visible="${escapeHtml(layer.id)}" data-next-visible="${layer.visible ? "false" : "true"}" aria-label="${layer.visible ? "Ẩn" : "Hiện"} ${escapeHtml(layer.name)}">${layer.visible ? "Ẩn" : "Hiện"}</button>${layer.kind !== "source" ? `<button class="button button--compact" type="button" data-image-mask-layer-move="${escapeHtml(layer.id)}" data-direction="up" ${index >= detail.layers.length - 1 ? "disabled" : ""} aria-label="Đưa ${escapeHtml(layer.name)} lên">↑</button><button class="button button--compact" type="button" data-image-mask-layer-move="${escapeHtml(layer.id)}" data-direction="down" ${index <= 1 ? "disabled" : ""} aria-label="Đưa ${escapeHtml(layer.name)} xuống">↓</button><button class="button button--compact button--danger" type="button" data-image-mask-layer-remove="${escapeHtml(layer.id)}" aria-label="Gỡ ${escapeHtml(layer.name)}">Gỡ</button>` : ""}</div></article>`;
  }).join("") || "";
  const snapshotOptions = (detail?.snapshots || []).map((snapshot) => `<option value="${escapeHtml(snapshot.id)}">r${escapeHtml(snapshot.revision)} · ${escapeHtml(snapshot.label)}</option>`).join("");
  const comparison = compare?.before && compare?.after ? `<section class="mask-compare" aria-label="So sánh trước và sau"><div>${compare.before.preview_artifact?.url ? `<img src="${escapeHtml(compare.before.preview_artifact.url)}" alt="Trước: ${escapeHtml(compare.before.label)}" />` : `<div class="mask-compare__placeholder">Không có preview raster</div>`}<strong>Trước · ${escapeHtml(compare.before.label)}</strong></div><div>${compare.after.preview_artifact?.url ? `<img src="${escapeHtml(compare.after.preview_artifact.url)}" alt="Sau: ${escapeHtml(compare.after.label)}" />` : `<div class="mask-compare__placeholder">Không có preview raster</div>`}<strong>Sau · ${escapeHtml(compare.after.label)}</strong></div></section><details class="advanced"><summary>Khác biệt metadata/layer · ${escapeHtml(Object.keys(compare.differences || {}).length)} mục</summary><pre>${escapeHtml(JSON.stringify(compare.differences || {}, null, 2))}</pre></details>` : `<div class="callout">Tạo hoặc chỉnh ít nhất một layer để Studio lưu snapshot Trước / Sau. Preview chỉ dùng artifact nguồn/dẫn xuất có sẵn, không giả lập raster hóa mask.</div>`;
  const inspector = !detail ? "" : selectedLayer ? `<section class="mask-inspector"><div class="split"><div><span class="eyebrow">INSPECTOR</span><h2>${escapeHtml(selectedLayer.name)}</h2></div><span class="tag">${escapeHtml(selectedLayer.kind)}</span></div><form class="stack" data-image-mask-form="update-layer" data-session-id="${escapeHtml(detail.id)}" data-layer-id="${escapeHtml(selectedLayer.id)}">${field("Tên layer", `<input name="name" maxlength="120" value="${escapeHtml(selectedLayer.name)}" />`)}${field("Độ mờ", `<input name="opacity" type="number" min="0" max="1" step="0.05" value="${escapeHtml(selectedLayer.opacity)}" />`)}<label class="node-toggle"><input name="visible" type="checkbox" ${selectedLayer.visible ? "checked" : ""} /> Hiển thị layer</label>${selectedLayer.kind === "adjustment" ? `${field("Adjustment", `<select name="adjustment_kind"><option value="brightness">Brightness</option><option value="contrast">Contrast</option><option value="saturation">Saturation</option><option value="exposure">Exposure</option><option value="temperature">Temperature</option><option value="crop">Crop metadata</option></select>`)}${field("Settings JSON an toàn", `<textarea name="adjustment_settings" rows="3">${escapeHtml(JSON.stringify(selectedLayer.adjustment?.settings || {}))}</textarea>`)}` : ""}<div class="form-actions">${button("Lưu layer")}</div><div class="form-result" role="status"></div></form>${selectedMask ? `<section class="mask-brush-tools"><h3>Brush & mask</h3><div class="form-grid">${field("Chế độ", `<select data-mask-brush-mode><option value="add">Thêm mask</option><option value="subtract">Trừ mask</option></select>`)}${field("Kích thước", `<input data-mask-brush-size type="number" min="0.002" max="1" step="0.01" value="0.06" />`)}${field("Cường độ", `<input data-mask-brush-strength type="number" min="0.01" max="1" step="0.05" value="1" />`)}</div><p class="small">Kéo trực tiếp trên ảnh để lưu stroke vector non-destructive. Không có pixel/source nào bị ghi đè.</p><div class="form-actions"><button class="button button--compact" type="button" data-image-mask-operation="invert">Đảo mask</button><button class="button button--compact" type="button" data-image-mask-operation="feather">Feather</button><button class="button button--compact" type="button" data-image-mask-operation="grow">Grow</button><button class="button button--compact" type="button" data-image-mask-operation="shrink">Shrink</button><button class="button button--compact" type="button" data-image-mask-export="${escapeHtml(selectedMask.id)}">Xuất manifest mask</button></div><label class="field"><span>Mức feather/grow/shrink</span><input data-mask-operation-amount type="number" min="0.001" max="1" step="0.01" value="0.08" /></label></section>` : ""}</section>` : `<div class="empty-state compact"><strong>Chọn một layer</strong><span>Inspector sẽ hiển thị metadata không phá hủy cho layer đang chọn.</span></div>`;
  return heading("IMAGE / CREATIVE", "Chỉnh sửa ảnh & Mask Studio", "Lớp, mask vector, snapshot, recipe và provenance cục bộ. Ảnh nguồn luôn immutable; SAM2/inpaint/outpaint chỉ phản ánh preflight thực tế.", `<button class="button" type="button" data-refresh-image-mask-studio>Làm mới Studio</button>`) + `
    <section class="mask-capability-grid" aria-label="Preflight Image & Mask Studio">${capabilityCards || `<div class="workspace-state" data-status="partial"><p>Đang tải preflight capability...</p></div>`}</section>
    <div class="workspace-grid workspace-grid--two mask-studio-create">
      ${card("Bắt đầu từ artifact ảnh", `<form class="stack" data-image-mask-form="create-session">${field("Tên phiên", `<input name="title" maxlength="120" placeholder="Ví dụ: Portrait masking" />`)}${field("Artifact ảnh đã có", `<select name="existing_source_artifact_id"><option value="">Chọn từ Asset Library</option>${sourceAssets.map((asset) => `<option value="${escapeHtml(asset.id)}" ${sourceOption === asset.id ? "selected" : ""}>${escapeHtml(asset.name || asset.id)}</option>`).join("")}</select>`)}${file("Hoặc tải ảnh nguồn mới", "source_artifact_id", "image/*")}${field("Project (tùy chọn)", `<select name="project_id">${projectOptions(projects, detail?.project_id || "", "Chưa liên kết project")}</select>`)}<div class="form-actions">${button("Tạo Studio không phá hủy", "button--primary")}</div><div class="form-result" role="status"></div></form><p class="small">Upload dùng Artifact Store streaming; Studio chỉ nhận opaque ID và snapshot metadata, không đọc path máy.</p>`) }
      ${card("Phiên gần đây", `<div class="mask-session-list" aria-label="Danh sách phiên Mask Studio">${sessionList}</div>`, "", "card--flat")}
    </div>
    ${detail ? `<section class="image-mask-session" data-image-mask-session data-session-id="${escapeHtml(detail.id)}"><header class="image-mask-session__header"><div><span class="eyebrow">${detail.dirty ? "DRAFT CHƯA LƯU" : "ĐÃ LƯU LOCAL"}</span><h2>${escapeHtml(detail.title)}</h2><p>r${escapeHtml(detail.revision)} · autosave ${escapeHtml(detail.autosaved_at || "—")} · ${escapeHtml(detail.last_action || "")}</p></div><div class="form-actions"><button class="button button--compact" type="button" data-image-mask-undo ${detail.history?.can_undo ? "" : "disabled"}>Hoàn tác</button><button class="button button--compact" type="button" data-image-mask-redo ${detail.history?.can_redo ? "" : "disabled"}>Làm lại</button><button class="button button--primary button--compact" type="button" data-image-mask-save>Lưu bản nháp</button></div></header><div class="image-mask-session__layout"><section class="image-mask-canvas-panel"><div class="split"><h2>Canvas layer</h2><span class="tag">${escapeHtml(detail.source_artifact?.name || detail.source_artifact_id)}</span></div>${sourcePreview}<p class="small">Canvas vẽ metadata ở tọa độ chuẩn hóa. Muốn có PNG mask/output mới, xuất/tạo bằng công cụ được ủy quyền rồi upload artifact để gắn làm layer dẫn xuất.</p></section><section class="image-mask-layer-panel"><div class="split"><h2>Stack lớp</h2><span class="tag">${escapeHtml(detail.layer_count)} lớp</span></div><div class="mask-layer-stack" aria-label="Layer stack">${layerStack}</div><form class="stack mask-add-layer" data-image-mask-form="add-layer" data-session-id="${escapeHtml(detail.id)}"><h3>Thêm layer</h3>${field("Loại", `<select name="kind"><option value="mask">Mask</option><option value="adjustment">Adjustment</option><option value="generated">Artifact dẫn xuất</option></select>`)}${field("Tên", `<input name="name" maxlength="120" placeholder="Ví dụ: Background mask" />`)}${field("Artifact mask/dẫn xuất (tùy chọn cho mask)", `<input type="file" data-asset-key="layer_artifact_id" accept="image/*" />`)}${field("Adjustment", `<select name="adjustment_kind"><option value="brightness">Brightness</option><option value="contrast">Contrast</option><option value="saturation">Saturation</option><option value="exposure">Exposure</option><option value="temperature">Temperature</option><option value="crop">Crop metadata</option></select>`)}${field("Settings JSON", `<textarea name="adjustment_settings" rows="2">{}</textarea>`)}<div class="form-actions">${button("Thêm layer")}</div><div class="form-result" role="status"></div></form></section>${inspector}</div><section class="image-mask-session__bottom"><div class="workspace-grid workspace-grid--three">${card("Snapshot & recovery", `<form class="stack" data-image-mask-form="compare" data-session-id="${escapeHtml(detail.id)}">${field("Trước", `<select name="before_snapshot_id">${snapshotOptions}</select>`)}${field("Sau", `<select name="after_snapshot_id">${snapshotOptions}</select>`)}<div class="form-actions">${button("So sánh Trước / Sau")}</div><div class="form-result" role="status"></div></form><form class="stack creative-inline-form" data-image-mask-form="restore-snapshot" data-session-id="${escapeHtml(detail.id)}">${field("Khôi phục snapshot", `<select name="snapshot_id">${snapshotOptions}</select>`)}<div class="form-actions">${button("Khôi phục an toàn")}</div><div class="form-result" role="status"></div></form>`) }${card("Recipe / preset", `<form class="stack" data-image-mask-form="capture-preset" data-session-id="${escapeHtml(detail.id)}">${field("Tên preset", `<input name="title" maxlength="120" placeholder="Ví dụ: Soft subject mask" />`)}<div class="form-actions">${button("Lưu preset")}</div><div class="form-result" role="status"></div></form><label class="field"><span>Preset đã lưu</span><select data-image-mask-preset><option value="">Chọn preset...</option>${(studio.presets || []).map((preset) => `<option value="${escapeHtml(preset.id)}">${escapeHtml(preset.title)} · ${escapeHtml(preset.layer_count)} lớp</option>`).join("")}</select></label><button class="button button--compact" type="button" data-image-mask-apply-preset>Áp dụng preset</button>`) }${card("Project & import", `<form class="stack" data-image-mask-form="link-project" data-session-id="${escapeHtml(detail.id)}">${field("Liên kết project", `<select name="project_id">${projectOptions(projects, detail.project_id || "", "Chọn project để liên kết")}</select>`)}<div class="form-actions">${button("Liên kết artifact Studio")}</div><div class="form-result" role="status"></div></form><form class="stack creative-inline-form" data-image-mask-form="import-mask" data-session-id="${escapeHtml(detail.id)}">${field("Import manifest mask", `<textarea name="manifest" rows="4" placeholder='{"contract_version":"image-mask-export.v1",…}'></textarea>`)}<div class="form-actions">${button("Validate & import")}</div><div class="form-result" role="status"></div></form>${detail.pending_project_attach ? `<p class="callout callout--warning">Liên kết project đang chờ recovery. Hãy thử lại sau khi project đích sẵn sàng.</p>` : ""}`) }</div>${comparison}</section></section>` : `<section class="empty-state mask-studio-empty"><strong>Chọn hoặc tạo một phiên để bắt đầu</strong><span>Layer, mask và history sẽ được autosave vào user-data local riêng, không làm thay đổi Creative Workspace v1 hay ảnh nguồn.</span></section>`}`;
}

function renderComfyAdvancedV5(state) {
  const comfy = state.comfyAdvanced?.comfyui || state.lifecycle?.comfyui || {};
  const workflows = state.comfyWorkflows || [];
  const candidate = String(comfy.advanced_url || "");
  const url = /^http:\/\/127\.0\.0\.1:\d+$/.test(candidate) ? candidate : "";
  const frame = url
    ? `<iframe class="comfy-advanced__frame" data-comfy-frame src="${escapeHtml(url)}" title="ComfyUI Advanced in Local AI Hub" sandbox="allow-scripts allow-same-origin allow-forms allow-downloads" referrerpolicy="no-referrer"></iframe>`
    : `<div class="preview-empty"><span>✦</span><strong>ComfyUI chưa chạy</strong><p>Bấm Khởi động để Hub mở ComfyUI thành backend ẩn rồi nhúng editor thật ở đây. Không mở Chrome hoặc Edge ngoài.</p></div>`;
  return heading("IMAGE / ADVANCED", "ComfyUI Advanced", "Bridge ComfyUI giữ trạng thái partial cho đến khi checklist Windows WebView thực tế pass. Hub không sao chép hoặc triển khai lại frontend ComfyUI.", `<span class="status-pill" data-status="partial">Partial · cần acceptance</span><button class="button" type="button" data-workspace-tab="image:quick">← Back to Hub</button>`) + `
    <section class="comfy-advanced" data-comfy-advanced>
      <div class="comfy-advanced__toolbar"><div><strong>partial · ${escapeHtml(comfy.status || "stopped")}</strong><span>${comfy.hub_owned ? " · Hub-owned hidden backend" : " · existing local backend"}</span></div><div class="form-actions"><button class="button button--primary" type="button" data-comfy-action="start">Khởi động / làm mới ComfyUI</button><button class="button" type="button" data-workspace-tab="image:quick">Back to Hub</button></div></div>
      <div class="comfy-advanced__layout">
        <aside class="comfy-bridge"><h2>Workflow bridge</h2><p>List/load/save JSON local cho node <code>ComfyUI Workflow</code>. Bridge không nhận raw path và không được commit vào Git.</p><p class="callout callout--warning">Acceptance iframe/canvas/socket/queue/shortcut/upload vẫn deferred do GPU/resource contention. Không coi bề mặt này là operational.</p><label class="field"><span>Đã lưu</span><select data-comfy-workflow-select><option value="">Chọn workflow…</option>${workflows.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}${item.local ? " · local" : ""}</option>`).join("")}</select></label><div class="form-actions"><button class="button" type="button" data-comfy-action="load">Load JSON</button></div><label class="field"><span>ID khi lưu local</span><input data-comfy-workflow-id placeholder="my_flux_mask" /></label><label class="field"><span>Bridge JSON</span><textarea data-comfy-workflow-json rows="18" placeholder='{"schema_version":1,"id":"my_flux_mask","kind":"raw_comfy_api",…}'></textarea></label><div class="form-actions"><button class="button" type="button" data-comfy-action="save">Save local bridge</button></div><small>Quick FLUX/Qwen compile qua API. Bridge raw chỉ nhận binding có schema và lưu dưới user-data local.</small></aside>
        <div class="comfy-advanced__frame-wrap">${frame}</div>
      </div>
    </section>`;
}

function renderImage(state) {
  const comfy = state.lifecycle?.comfyui || {};
  const imageTool = tool(state, "generate_flux");
  return heading("IMAGE", "Image AI", "FLUX và Qwen Image gọi ComfyUI API trực tiếp. ComfyUI chạy nền ẩn khi Hub cần, không mở Local Image Studio.", `<span class="status-pill" data-status="${escapeHtml(imageTool.tool_status || comfy.status || "stopped")}">${escapeHtml(formatStatus(imageTool.tool_status || comfy.status || "stopped"))}</span>`) + workspaceState("Image Generate / Edit", imageTool, "Chọn template Hub Nodes để nối prompt → generate/edit → upscale → preview.") + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Generate", `<form data-job-form data-tool="generate_flux" data-tool-by-field="engine" data-tool-map='{"flux":"generate_flux","qwen":"generate_qwen_image"}' class="stack">${field("Model", `<select name="engine"><option value="flux">FLUX.2 Klein</option><option value="qwen">Qwen Image 2512</option></select>`)}${field("Prompt", `<textarea name="prompt" required placeholder="Mô tả ảnh cần tạo…"></textarea>`)}${field("Negative prompt", `<input name="negative_prompt" placeholder="Tùy chọn" />`)}<div class="form-grid">${field("Width", `<input name="width" type="number" min="256" max="2048" step="64" value="768" />`)}${field("Height", `<input name="height" type="number" min="256" max="2048" step="64" value="768" />`)}${field("Steps", `<input name="steps" type="number" min="1" max="80" value="20" />`)}${field("Seed", `<input name="seed" type="number" min="0" placeholder="random" />`)}</div>${file("Input image (FLUX image-edit nếu workflow hỗ trợ)", "input_image_asset_id", "image/*")}<div class="form-actions">${button("Generate trong Hub", "button--primary")}</div>${formResult("image-result")}</form>`)}
      ${card("Preview & Advanced", `<div class="preview-empty"><span>✦</span><strong>Ảnh output sẽ hiện trong Jobs</strong><p>Lịch sử giữ prompt, seed, model qua metadata job an toàn; không lộ đường dẫn cục bộ.</p></div><details class="advanced"><summary>ComfyUI Advanced</summary><p>Advanced mở web interface ComfyUI trong trình duyệt nếu cần sửa workflow; normal workflow vẫn dùng form Hub ở bên trái.</p><button class="button" type="button" data-open-comfy>Open ComfyUI web interface</button></details>`, "", "card--flat")}
    </div>`;
}

function renderMedia(state) {
  const item = component(state, "ffmpeg");
  return heading("MEDIA", "Trình biên tập media", "FFmpeg/FFprobe chạy bằng danh sách lệnh cho phép ở chế độ ẩn, không có ô shell và không ghi đè media nguồn.", statusPill(tool(state, "run_media_operation").tool_status || item.component_status || "missing")) + workspaceState("Video Transform / Export", tool(state, "run_media_operation"), "Chọn artifact, operation allowlist và kiểm tra output; video smoke deferred khi GPU đang bận.") + videoWorkflowRail(state) + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Thao tác video và ảnh", `<form data-job-form data-tool="run_media_operation" class="stack">${file("Media đầu vào chính", "asset_id", "audio/*,video/*,image/*")}${files("Đầu vào bổ sung (ghép / chuỗi ảnh)", "input_asset_ids", "video/*,image/*")}${file("Audio hoặc phụ đề thứ hai", "secondary_asset_id", "audio/*,.srt,.ass")}${field("Thao tác", `<select name="operation"><option value="probe">Đọc metadata</option><option value="trim">Cắt đầu/cuối</option><option value="concat">Ghép video</option><option value="resize">Đổi kích thước video</option><option value="crop">Cắt khung video</option><option value="rotate">Xoay video</option><option value="fps">FPS</option><option value="transcode">Chuyển mã</option><option value="extract_audio">Tách audio</option><option value="replace_audio">Thay audio</option><option value="mux">Mux audio/video</option><option value="burn_subtitle">Chèn phụ đề</option><option value="extract_frames">Tách frame</option><option value="image_sequence_video">Chuỗi ảnh → video</option><option value="image_resize">Đổi kích thước ảnh</option><option value="image_crop">Cắt khung ảnh</option><option value="image_rotate">Xoay ảnh</option><option value="image_flip">Lật ảnh</option><option value="image_convert">Đổi định dạng ảnh</option><option value="image_compress">Nén ảnh</option></select>`)}<div class="form-grid">${field("Bắt đầu", `<input name="start" type="number" min="0" step="0.1" value="0" />`)}${field("Kết thúc", `<input name="end" type="number" min="0.1" step="0.1" value="5" />`)}${field("Chiều rộng", `<input name="width" type="number" min="2" value="1280" />`)}${field("Chiều cao", `<input name="height" type="number" min="-2" value="-2" />`)}${field("FPS", `<input name="fps" type="number" min="1" value="30" />`)}${field("Xoay", `<select name="degrees"><option value="90">90°</option><option value="180">180°</option><option value="270">270°</option></select>`)}${field("Lật", `<select name="axis"><option value="horizontal">Ngang</option><option value="vertical">Dọc</option></select>`)}${field("Định dạng ảnh", `<select name="format"><option value="png">PNG</option><option value="jpg">JPG</option><option value="webp">WEBP</option></select>`)}</div><div class="form-actions">${button("Chạy FFmpeg", "button--primary")}</div>${formResult("media-result")}</form>`)}
      ${card("An toàn thao tác", `<ul class="notice-list"><li>Kết quả tạo trong vùng Output/Media của Hub.</li><li>Đọc metadata là read-only; tác vụ ghi file đi qua Job Manager.</li><li>Ghép video và chuỗi ảnh chỉ nhận artifact đã tải lên Hub, rồi tạo manifest Temp ngắn hạn; không nhận raw shell/path list.</li></ul><div class="tag-list"><span class="tag">Cắt</span><span class="tag">Ghép</span><span class="tag">Crop</span><span class="tag">Xoay</span><span class="tag">Mux</span><span class="tag">Chèn phụ đề</span><span class="tag">Frames</span></div>`, "", "card--flat")}
    </div>`;
}

function renderAnime(state) {
  const item = component(state, "animesr");
  return heading("VIDEO AI", "AnimeSR", "Workflow upscale chính chạy bằng worker AnimeSR trong Hub. Anime Upscale Studio chỉ là legacy/debug fallback, không còn là action chính.", statusPill(tool(state, "upscale_anime_video").tool_status || item.component_status || "missing")) + workspaceState("AnimeSR / Interpolate", tool(state, "upscale_anime_video"), "Chuẩn bị clip ngắn; video smoke hiện deferred do resource contention.") + `
    <div class="workspace-grid workspace-grid--two">
      ${card("Upscale queue", `<form data-job-form data-tool="upscale_anime_video" class="stack">${file("Video input", "asset_id", "video/*")}${field("Model", `<select name="model"><option value="AnimeSR_v2">AnimeSR v2</option><option value="AnimeSR_v1-PaperModel">AnimeSR v1 Paper</option></select>`)}<div class="form-grid">${field("Scale", `<select name="scale"><option value="2">2×</option><option value="3">3×</option><option value="4">4×</option></select>`)}${field("Chunk seconds", `<input name="chunk_seconds" type="number" min="10" value="120" />`)}</div><div class="check-grid"><label><input name="half" type="checkbox" checked /> Half precision</label><label><input name="use_rife" type="checkbox" /> RIFE (partial)</label><label><input name="use_realesrgan" type="checkbox" /> Real-ESRGAN (partial)</label></div><div class="form-actions">${button("Thêm AnimeSR job", "button--primary")}</div>${formResult("anime-result")}</form>`)}
      ${card("Progress & output", `<div class="preview-empty"><span>⇱</span><strong>Queue, progress, cancel và resume nằm ở Jobs</strong><p>Hub không mở Anime Upscale Studio để chạy normal workflow.</p></div><details class="advanced"><summary>Advanced / legacy</summary><p>Legacy Studio chỉ nên dùng debug khi direct worker báo limitation đã được ghi nhận.</p></details>`, "", "card--flat")}
    </div>`;
}

const creativeTabs = (state) => {
  const active = state.creativeTab || "projects";
  const tabs = [["projects", "Projects"], ["assets", "Asset Library"], ["recipes", "Prompts & Recipes"], ["compare", "Compare Board"], ["gallery", "Workflow Gallery"]];
  return `<div class="module-tabs creative-tabs" role="tablist" aria-label="Creative workspace sections">${tabs.map(([id, label]) => `<button class="tab ${active === id ? "is-selected" : ""}" type="button" role="tab" aria-selected="${active === id}" data-creative-tab="${id}">${label}</button>`).join("")}</div>`;
};

const creativeRecovery = (recovery = {}) => {
  if (!recovery || recovery.status === "clean") return "";
  return `<section class="workspace-state creative-recovery" data-status="${escapeHtml(recovery.status)}" role="status"><div class="workspace-state__head"><div><span class="eyebrow">SAFE RECOVERY</span><h2>Workspace local cần chú ý</h2></div>${statusPill(recovery.status, recovery.status === "partial_recovery" ? "Đã phục hồi một phần" : "Cần phục hồi")}</div><p>${escapeHtml(recovery.reason || "Hub giữ nguyên local metadata chưa hợp lệ.")}</p><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(recovery.action || "Export/import lại manifest đã được validate.")}</span></div></section>`;
};

const projectOptions = (projects, selected = "", label = "Chọn project…") => `<option value="">${escapeHtml(label)}</option>${projects.map((project) => `<option value="${escapeHtml(project.id)}" ${project.id === selected ? "selected" : ""}>${escapeHtml(project.title)}${project.status === "archived" ? " · archived" : ""}</option>`).join("")}`;

const recipeOptions = (recipes, selected = "", label = "Không gắn recipe") => `<option value="">${escapeHtml(label)}</option>${recipes.map((recipe) => `<option value="${escapeHtml(recipe.id)}" ${recipe.id === selected ? "selected" : ""}>${escapeHtml(recipe.title)} · v${escapeHtml(recipe.version)}</option>`).join("")}`;

const assetThumb = (asset, extra = "") => {
  const image = asset.preview_url ? `<img src="${escapeHtml(asset.preview_url)}" alt="${escapeHtml(asset.name || asset.id)}" loading="lazy" />` : `<div class="asset-contact-sheet__placeholder" aria-hidden="true">${String(asset.media_type || "artifact").startsWith("video/") ? "▶" : String(asset.media_type || "artifact").startsWith("audio/") ? "♪" : "▧"}</div>`;
  const tags = (asset.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const lineage = asset.lineage?.parent_artifact_id ? `<small>Derived from ${escapeHtml(asset.lineage.parent_artifact_id)}</small>` : asset.lineage?.derived_artifact_ids?.length ? `<small>${escapeHtml(asset.lineage.derived_artifact_ids.length)} derived asset</small>` : "";
  return `<article class="asset-contact-sheet__item ${extra}" data-asset-id="${escapeHtml(asset.id)}">${image}<div class="asset-contact-sheet__copy"><strong title="${escapeHtml(asset.name || asset.id)}">${escapeHtml(asset.name || asset.id)}</strong><span>${escapeHtml(asset.media_type || "artifact")} · ${formatGb(asset.size_bytes)}</span>${lineage}<div class="tag-list">${tags}</div></div></article>`;
};

const renderCreativeProjects = (state) => {
  const creative = state.creative || {};
  const projects = creative.projects || [];
  const recent = creative.recent_projects || [];
  const selectedId = state.selectedProjectId || state.creativeProject?.project?.id || "";
  const selected = state.creativeProject?.project || projects.find((item) => item.id === selectedId);
  const exportAction = selected ? `<button class="button" type="button" data-export-project="${escapeHtml(selected.id)}">Export manifest</button>` : "";
  return `
    <div class="creative-summary"><div><strong>${projects.filter((item) => item.status === "active").length}</strong><span>project đang mở</span></div><div><strong>${creative.assets?.length || 0}</strong><span>artifact thấy được</span></div><div><strong>${creative.recipes?.length || 0}</strong><span>recipe local</span></div></div>
    ${recent.length ? `<section class="creative-recent" aria-label="Recent projects"><strong>Gần đây</strong><div>${recent.map((project) => `<button type="button" class="chip-button" data-project-open="${escapeHtml(project.id)}">${escapeHtml(project.title)}</button>`).join("")}</div></section>` : ""}
    <div class="workspace-grid workspace-grid--two">
      ${card("Tạo creative project", `<form class="stack" data-creative-form="create-project">${field("Tên project", `<input name="title" maxlength="120" required placeholder="Ví dụ: Campaign mùa thu" />`)}${field("Mô tả", `<textarea name="description" maxlength="1000" placeholder="Mục tiêu sáng tạo, audience hoặc deliverable…"></textarea>`)}${field("Tags", `<input name="tags" placeholder="campaign, social, portrait" />`)}<div class="form-actions">${button("Tạo project", "button--primary")}</div><div class="form-result" role="status"></div></form>`) }
      ${card("Mở & phục hồi", `<div class="stack">${field("Project hiện tại", `<select data-project-select>${projectOptions(projects, selectedId)}</select>`)}<p class="small">Manifest được version hóa và chỉ giữ metadata + opaque artifact ID. Hub không copy output, model, đường dẫn máy hoặc secret vào project.</p><div class="form-actions">${exportAction}</div><details class="advanced"><summary>Import project manifest</summary><form class="stack creative-inline-form" data-creative-form="import-project">${field("Conflict", `<select name="conflict"><option value="copy">Copy an toàn</option><option value="skip">Bỏ qua ID trùng</option><option value="replace">Thay thế ID trùng</option></select>`)}${field("Manifest JSON", `<textarea name="manifest" required rows="9" placeholder='{"contract_version":"creative-project-export.v1",…}'></textarea>`)}<div class="form-actions">${button("Validate & import")}</div><div class="form-result" role="status"></div></form></details></div>`, "", "card--flat")}
    </div>
    <section class="creative-project-grid" aria-label="Project library">${projects.map((project) => `<article class="creative-project-card ${project.id === selectedId ? "is-selected" : ""}"><div class="split"><div><span class="eyebrow">${escapeHtml(project.status)}</span><h2>${escapeHtml(project.title)}</h2></div>${statusPill(project.status === "active" ? "operational" : "partial", project.status === "active" ? "Đang làm" : "Archived")}</div><p>${escapeHtml(project.description || "Chưa có mô tả.")}</p><div class="tag-list">${(project.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div><div class="creative-project-card__meta"><span>${escapeHtml(project.asset_count)} asset</span><span>${escapeHtml(project.recipe_count)} recipe</span></div><div class="form-actions"><button class="button button--compact" type="button" data-project-open="${escapeHtml(project.id)}">Mở</button>${project.status === "active" ? `<button class="button button--compact button--danger" type="button" data-project-archive="${escapeHtml(project.id)}">Archive</button>` : `<button class="button button--compact" type="button" data-project-restore="${escapeHtml(project.id)}">Khôi phục</button>`}</div></article>`).join("") || `<div class="empty-state"><strong>Chưa có project</strong><span>Tạo project đầu tiên để gom asset, recipe, workflow và compare board vào một workspace local an toàn.</span></div>`}</section>
    ${selected ? card("Project đang chọn", `<form class="stack" data-creative-form="rename-project" data-project-id="${escapeHtml(selected.id)}">${field("Tên project", `<input name="title" maxlength="120" required value="${escapeHtml(selected.title)}" />`)}${field("Mô tả", `<textarea name="description" maxlength="1000">${escapeHtml(selected.description || "")}</textarea>`)}${field("Tags", `<input name="tags" value="${escapeHtml((selected.tags || []).join(", "))}" />`)}${field("Workflow preset", `<input name="workflow_preset" value="${escapeHtml(selected.workflow_preset || "")}" placeholder="Tùy chọn, preset ID đã biết" />`)}<div class="form-actions">${button("Lưu metadata")}</div><div class="form-result" role="status"></div></form>`, "", "card--wide") : ""}`;
};

const renderCreativeAssets = (state) => {
  const creative = state.creative || {};
  const filters = state.assetFilters || {};
  const source = creative.assets || [];
  const needle = String(filters.query || "").toLowerCase();
  const tag = String(filters.tag || "").toLowerCase();
  const collections = creative.collections || [];
  const assets = source.filter((asset) => (!needle || `${asset.name || ""} ${(asset.tags || []).join(" ")}`.toLowerCase().includes(needle)) && (!tag || (asset.tags || []).includes(tag)) && (!filters.favorite || asset.favorite) && (!filters.collection || (asset.collections || []).some((item) => item.id === filters.collection)));
  const selected = state.creativeProject?.project;
  const projectAssets = state.creativeProject?.assets || [];
  return `<div class="workspace-grid workspace-grid--two">
    ${card("Tìm trong Asset Library", `<form class="stack" data-creative-form="asset-filter">${field("Tìm kiếm", `<input name="query" value="${escapeHtml(filters.query || "")}" placeholder="Tên artifact hoặc tag" />`)}${field("Tag", `<input name="tag" value="${escapeHtml(filters.tag || "")}" placeholder="Ví dụ: favorite, portrait" />`)}${field("Collection", `<select name="collection"><option value="">Tất cả collection</option>${collections.map((collection) => `<option value="${escapeHtml(collection.id)}" ${filters.collection === collection.id ? "selected" : ""}>${escapeHtml(collection.title)}</option>`).join("")}</select>`)}<label class="node-toggle"><input name="favorite" type="checkbox" ${filters.favorite ? "checked" : ""} /> Chỉ favorite</label><div class="form-actions">${button("Lọc library")}</div></form><p class="small">Contact sheet tái sử dụng artifact đã có trong Hub. Mỗi record chỉ public opaque ID và URL artifact được Hub kiểm soát.</p>`) }
    ${card("Collections", `<form class="stack" data-creative-form="create-collection">${field("Tên collection", `<input name="title" required maxlength="100" placeholder="Ví dụ: Hero candidates" />`)}${field("Tags", `<input name="tags" placeholder="portrait, final" />`)}<div class="form-actions">${button("Tạo collection")}</div><div class="form-result" role="status"></div></form><div class="creative-collection-list">${collections.map((collection) => `<span class="tag">${escapeHtml(collection.title)} · ${escapeHtml(collection.asset_count)}</span>`).join("") || "<span class=\"muted small\">Chưa có collection.</span>"}</div>`, "", "card--flat")}
  </div>
  <section class="asset-contact-sheet" aria-label="Asset contact sheet">${assets.map((asset) => `<div class="asset-library-card">${assetThumb(asset)}<div class="asset-contact-sheet__actions"><button class="button button--compact" type="button" data-asset-favorite="${escapeHtml(asset.id)}" data-next-favorite="${asset.favorite ? "false" : "true"}">${asset.favorite ? "Bỏ favorite" : "Favorite"}</button>${String(asset.media_type || "").startsWith("image/") ? `<button class="button button--compact" type="button" data-open-image-mask-studio="${escapeHtml(asset.id)}">Chỉnh sửa & Mask</button>` : ""}${selected && !projectAssets.some((item) => item.id === asset.id) ? `<button class="button button--compact" type="button" data-attach-asset="${escapeHtml(asset.id)}">Thêm vào project</button>` : ""}</div><form class="asset-tag-form" data-creative-form="asset-tags" data-asset-id="${escapeHtml(asset.id)}">${field("Tags", `<input name="tags" value="${escapeHtml((asset.tags || []).join(", "))}" aria-label="Tags for ${escapeHtml(asset.name || asset.id)}" />`)}<button class="button button--compact" type="submit">Lưu tag</button></form>${collections.length ? `<form class="asset-tag-form" data-creative-form="asset-collection" data-asset-id="${escapeHtml(asset.id)}">${field("Collection", `<select name="collection_id" aria-label="Collection for ${escapeHtml(asset.name || asset.id)}">${collections.map((collection) => `<option value="${escapeHtml(collection.id)}">${escapeHtml(collection.title)}</option>`).join("")}</select>`)}<button class="button button--compact" type="submit">Thêm collection</button></form>` : ""}</div>`).join("") || `<div class="empty-state"><strong>Không có asset phù hợp</strong><span>Upload hoặc chạy workflow hiện có để Hub đăng ký artifact, rồi quay lại đây để gắn metadata non-destructive.</span></div>`}</section>
  ${selected ? card(`Asset của ${selected.title}`, `<div class="asset-contact-sheet asset-contact-sheet--compact">${projectAssets.map((asset) => assetThumb(asset)).join("") || `<div class="empty-state compact">Project chưa tham chiếu asset Hub nào.</div>`}</div>`, "", "card--wide") : ""}`;
};

const renderCreativeRecipes = (state) => {
  const creative = state.creative || {};
  const recipes = creative.recipes || [];
  const projects = creative.projects || [];
  const selectedId = state.selectedProjectId || state.creativeProject?.project?.id || "";
  return `<div class="workspace-grid workspace-grid--two">
    ${card("Tạo prompt & recipe", `<form class="stack" data-creative-form="create-recipe">${field("Tên recipe", `<input name="title" required maxlength="120" placeholder="Ví dụ: Cinematic portrait" />`)}${field("Prompt template", `<textarea name="prompt_template" rows="4" placeholder="A {{subject}} in a studio…"></textarea>`)}${field("Variables", `<input name="variables" placeholder="subject|Chủ thể|person|required; mood|Mood|warm" />`)}${field("Style block", `<textarea name="style_block" rows="2" placeholder="cinematic editorial lighting"></textarea>`)}${field("Negative block", `<textarea name="negative_block" rows="2" placeholder="blur, watermark"></textarea>`)}<div class="form-grid">${field("Model", `<input name="model" value="flux" />`)}${field("Seed", `<input name="seed" type="number" min="0" value="42" />`)}${field("Width", `<input name="width" type="number" min="256" value="768" />`)}${field("Height", `<input name="height" type="number" min="256" value="768" />`)}${field("Steps", `<input name="steps" type="number" min="1" value="20" />`)}</div>${field("Workflow preset", `<input name="workflow_preset" placeholder="Optional tracked preset ID" />`)}${field("Gắn project", `<select name="project_id">${projectOptions(projects, selectedId, "Không gắn project")}</select>`)}${field("Tags", `<input name="tags" placeholder="portrait, social" />`)}<div class="form-actions">${button("Lưu recipe", "button--primary")}</div><div class="form-result" role="status"></div><p class="small">Variable syntax: <code>name|Nhãn|default|required</code>. Settings được validate là JSON an toàn trước khi lưu.</p></form>`) }
    ${card("Recipe Pack", `<p class="small">Export chỉ chứa recipe/version/settings an toàn; không có model, output cá nhân, secret hoặc đường dẫn máy.</p><div class="form-actions"><button class="button" type="button" data-export-recipe-pack>Export toàn bộ recipe</button></div><details class="advanced"><summary>Import Recipe Pack</summary><form class="stack creative-inline-form" data-creative-form="import-recipe-pack">${field("Conflict", `<select name="conflict"><option value="copy">Copy an toàn</option><option value="skip">Bỏ qua ID trùng</option><option value="replace">Thay thế ID trùng</option></select>`)}${field("Pack JSON", `<textarea name="pack" required rows="9" placeholder='{"contract_version":"creative-recipe-pack.v1",…}'></textarea>`)}<div class="form-actions">${button("Validate & import")}</div><div class="form-result" role="status"></div></form></details>`, "", "card--flat")}
  </div>
  <section class="creative-recipe-grid" aria-label="Recipe library">${recipes.map((recipe) => `<article class="creative-recipe-card"><div class="split"><div><span class="eyebrow">RECIPE · v${escapeHtml(recipe.version)}</span><h2>${escapeHtml(recipe.title)}</h2></div><span class="tag">${escapeHtml(recipe.model)}</span></div><p>${escapeHtml(recipe.prompt_template || "Không có prompt template.")}</p>${recipe.style_block ? `<p class="small"><strong>Style:</strong> ${escapeHtml(recipe.style_block)}</p>` : ""}${recipe.negative_block ? `<p class="small"><strong>Negative:</strong> ${escapeHtml(recipe.negative_block)}</p>` : ""}<div class="tag-list">${(recipe.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}${(recipe.variables || []).map((variable) => `<span class="tag">${escapeHtml(variable.name)}</span>`).join("")}</div><form class="stack creative-inline-form" data-creative-form="apply-recipe" data-recipe-id="${escapeHtml(recipe.id)}">${(recipe.variables || []).map((variable) => field(variable.label || variable.name, `<input name="variable_${escapeHtml(variable.name)}" value="${escapeHtml(variable.default || "")}" ${variable.required ? "required" : ""} />`)).join("")}${field("Đích áp dụng", `<select name="target"><option value="quick">Image AI Quick</option><option value="nodes">Image Hub Nodes</option></select>`)}<div class="form-actions">${button("Áp dụng recipe")}</div><div class="form-result" role="status"></div></form></article>`).join("") || `<div class="empty-state"><strong>Chưa có recipe</strong><span>Tạo recipe để dùng lại prompt, style, negative, seed, model và settings trên Quick hoặc Node Studio.</span></div>`}</section>`;
};

const renderCreativeCompare = (state) => {
  const detail = state.creativeProject || {};
  const project = detail.project;
  const board = detail.compare || {};
  const assets = detail.assets || [];
  if (!project) return `<div class="empty-state"><strong>Chọn một project trước</strong><span>Compare Board chỉ làm việc với artifact opaque đã được project hiện tại tham chiếu.</span></div>`;
  const differences = Object.entries(board.differences || {});
  return `<div class="workspace-grid workspace-grid--two">
    ${card("Thêm artifact vào A/B board", `<form class="stack" data-creative-form="compare-add" data-project-id="${escapeHtml(project.id)}">${field("Artifact", `<select name="artifact_id">${assets.map((asset) => `<option value="${escapeHtml(asset.id)}">${escapeHtml(asset.name || asset.id)}</option>`).join("") || "<option value=\"\">Project chưa có artifact</option>"}</select>`)}${field("Nhãn", `<input name="label" maxlength="100" placeholder="A · portrait warm" />`)}<div class="form-actions">${button("Thêm vào compare")}</div><div class="form-result" role="status"></div></form><p class="small">Tối đa 8 artifact; thông tin diff chỉ chứa metadata/provenance an toàn của Hub.</p>`) }
    ${card("Lựa chọn & quay lại recipe", `<p>${board.selected_artifact_id ? `Đang chọn ${escapeHtml(board.selected_artifact_id)}` : "Chưa chọn kết quả ưu tiên."}</p><p class="small">Favorite có tính non-destructive; chọn recipe tại từng card để quay về workflow đã tạo kết quả.</p>`, "", "card--flat")}
  </div>
  <section class="compare-board" aria-label="Artifact compare board">${(board.items || []).map((item) => `<article class="compare-board__item ${item.artifact_id === board.selected_artifact_id ? "is-selected" : ""}">${assetThumb(item.asset || { id: item.artifact_id })}<div class="compare-board__body"><strong>${escapeHtml(item.label || item.artifact_id)}</strong>${item.recipe ? `<span class="small">Recipe: ${escapeHtml(item.recipe.title)} · v${escapeHtml(item.recipe.version)}</span>` : `<span class="small">Chưa có recipe liên kết</span>`}<div class="form-actions"><button class="button button--compact" type="button" data-compare-select="${escapeHtml(item.artifact_id)}" data-project-id="${escapeHtml(project.id)}">Chọn</button><button class="button button--compact" type="button" data-compare-favorite="${escapeHtml(item.artifact_id)}" data-project-id="${escapeHtml(project.id)}">Chọn + favorite</button>${item.recipe ? `<button class="button button--compact" type="button" data-apply-recipe-quick="${escapeHtml(item.recipe.id)}">Mở recipe</button>` : ""}</div></div></article>`).join("") || `<div class="empty-state"><strong>Compare Board trống</strong><span>Thêm tối thiểu hai artifact để xem khác biệt metadata, settings và provenance.</span></div>`}</section>
  ${differences.length ? card("Metadata / settings / provenance diff", `<div class="creative-diff-grid">${differences.map(([fieldName, values]) => `<details><summary>${escapeHtml(fieldName)} · ${values.length} artifact</summary><pre>${escapeHtml(JSON.stringify(values, null, 2))}</pre></details>`).join("")}</div>`, "", "card--wide") : `<div class="callout">Chưa có khác biệt metadata cần hiển thị. Compare Board sẽ cho diff khi có nhiều artifact với provenance/settings khác nhau.</div>`}`;
};

const renderCreativeGallery = (state) => {
  const creative = state.creative || {};
  const filters = state.galleryFilters || {};
  const needle = String(filters.query || "").toLowerCase();
  const category = String(filters.category || "").toLowerCase();
  const gallery = (creative.gallery || []).filter((item) => (!needle || `${item.title} ${item.description} ${(item.categories || []).join(" ")}`.toLowerCase().includes(needle)) && (!category || (item.categories || []).some((itemCategory) => String(itemCategory).toLowerCase() === category)));
  const categories = [...new Set((creative.gallery || []).flatMap((item) => item.categories || []))].sort();
  return `<div class="workspace-grid workspace-grid--two">
    ${card("Khám phá template", `<form class="stack" data-creative-form="gallery-filter">${field("Tìm kiếm", `<input name="query" value="${escapeHtml(filters.query || "")}" placeholder="Tên, mô tả hoặc category" />`)}${field("Category", `<select name="category"><option value="">Tất cả category</option>${categories.map((item) => `<option value="${escapeHtml(item)}" ${item === filters.category ? "selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select>`)}<div class="form-actions">${button("Lọc gallery")}</div></form>`) }
    ${card("Capability preflight", `<p class="small">Mỗi card bên dưới đọc availability từ node registry hiện tại. Template partial/unavailable vẫn có thể được xem, nhưng Hub sẽ nêu rõ reason/action thay vì hứa backend đã chạy.</p>`, "", "card--flat")}
  </div>
  <section class="creative-gallery-grid" aria-label="Workflow template gallery">${gallery.map((item) => `<article class="creative-gallery-card"><div class="creative-gallery-card__preview" aria-hidden="true"><span>⌘</span><small>${escapeHtml(item.preview?.label || "workflow")}</small></div><div class="split"><div><span class="eyebrow">${escapeHtml(item.scope)} · ${escapeHtml(item.stage)}</span><h2>${escapeHtml(item.title)}</h2></div>${statusPill(item.status)}</div><p>${escapeHtml(item.description || "Tracked workflow template.")}</p><div class="tag-list">${(item.categories || []).map((categoryName) => `<span class="tag">${escapeHtml(categoryName)}</span>`).join("")}</div><div class="workspace-state creative-gallery-card__availability" data-status="${escapeHtml(item.status)}"><p>${escapeHtml(item.availability?.reason || "Chưa có availability detail.")}</p><div class="workspace-state__action"><strong>Bước tiếp theo</strong><span>${escapeHtml(item.availability?.action || "Mở template trong Hub Nodes.")}</span></div></div><div class="form-actions"><button class="button button--compact" type="button" data-gallery-use="${escapeHtml(item.id)}" data-gallery-scope="${escapeHtml(item.scope)}">Mở trong Hub Nodes</button><span class="small">${escapeHtml(item.node_count)} node</span></div></article>`).join("") || `<div class="empty-state"><strong>Không có template phù hợp</strong><span>Thử bỏ bớt điều kiện tìm kiếm hoặc kiểm tra workflow tracked.</span></div>`}</section>`;
};

function renderCreativeWorkspace(state) {
  const creative = state.creative || {};
  if (state.creativeLoading && !creative.contract_version) return heading("CREATIVE WORKSPACE", "Projects & Recipes", "Đang tải metadata local an toàn từ Hub.") + `<div class="empty-state" role="status"><strong>Đang tải Creative Workspace…</strong><span>Không đọc hoặc hiển thị raw filesystem path.</span></div>`;
  const active = state.creativeTab || "projects";
  const content = active === "assets" ? renderCreativeAssets(state) : active === "recipes" ? renderCreativeRecipes(state) : active === "compare" ? renderCreativeCompare(state) : active === "gallery" ? renderCreativeGallery(state) : renderCreativeProjects(state);
  return heading("CREATIVE WORKSPACE", "Projects, Assets & Recipes", "Tổ chức creative work theo project → artifact → recipe → workflow → compare. Local metadata được version hóa, an toàn và không đóng gói output/model/secrets.", `<button class="button" type="button" data-refresh-creative>Làm mới workspace</button>`) + creativeTabs(state) + creativeRecovery(creative.recovery) + content;
}

function renderJobs(state) {
  const jobs = state.jobs || [];
  const filter = state.jobFilter || "all";
  const filtered = jobs.filter((job) => filter === "all" || (filter === "active" && ["queued", "starting", "running", "cancelling"].includes(job.status)) || (filter === "attention" && ["failed", "unavailable", "cancelled", "interrupted"].includes(job.status)) || (filter === "completed" && job.status === "completed"));
  const rows = filtered.map((job) => {
    const actions = ["queued", "starting", "running", "cancelling"].includes(job.status)
      ? `<button class="button button--compact button--danger" type="button" data-cancel-job="${escapeHtml(job.id)}">Hủy</button>`
      : job.resumable ? `<button class="button button--compact" type="button" data-resume-job="${escapeHtml(job.id)}">${job.status === "cancelled" ? "Tiếp tục" : "Thử lại"}</button>` : "";
    return `<article class="job-card" data-job-status="${escapeHtml(job.status)}"><div class="split"><div><strong>${escapeHtml(job.tool)}</strong><div class="row-meta">${escapeHtml(job.id)} · ${escapeHtml(job.created_at || "")}${job.contract_version ? ` · ${escapeHtml(job.contract_version)}` : ""}</div></div>${statusPill(job.status)}</div><div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.max(0, Math.min(100, Number(job.progress || 0)))}"><div class="progress-bar" style="width:${Math.max(0, Math.min(100, Number(job.progress || 0)))}%"></div></div><p class="job-message">${escapeHtml(job.message || job.error || "")}</p>${job.next_action ? `<div class="job-next-action"><strong>Bước tiếp theo</strong><span>${escapeHtml(job.next_action)}</span></div>` : ""}${provenanceList(job)}${artifactList(job.result)}<div class="form-actions">${actions}</div></article>`;
  }).join("");
  const filters = [ ["all", "Tất cả"], ["active", "Đang chạy"], ["attention", "Cần chú ý"], ["completed", "Hoàn tất"] ].map(([id, label]) => `<button class="tab ${filter === id ? "is-selected" : ""}" type="button" data-job-filter="${id}" aria-pressed="${filter === id}">${label}</button>`).join("");
  return heading("CONTROL PLANE", "Jobs", "Theo dõi queue và lịch sử job do Hub tạo; cancel/retry chỉ tác động tới process và payload do Hub sở hữu.") + `<div class="module-tabs job-filters" role="group" aria-label="Bộ lọc lịch sử job">${filters}</div><div class="job-list">${rows || `<div class="empty-state"><strong>Không có job trong bộ lọc này</strong><span>Chạy workflow từ module bất kỳ hoặc chuyển sang Tất cả.</span></div>`}</div>`;
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
  const pages = { dashboard: renderDashboard, airi: renderAiri, vision: renderVision, sam2: renderSam2, ocr: renderOcr, whisper: renderWhisper, voice: renderVoice, image: renderImageQuickV5, media: renderMedia, animesr: renderAnime, projects: renderCreativeWorkspace, jobs: renderJobs, models: renderModels, settings: renderSettings };
  const pageRoute = route === "video" ? "media" : route;
  const nodeCopy = {
    image: "Compose FLUX/Qwen, SAM2 mask và image transforms trong cùng graph; preset JSON được track, workflow cá nhân autosave local.",
    sam2: "Advanced workflow: Grounding DINO → SAM2 → mask/composite/export. GPU nodes chỉ chạy khi bấm Run Graph.",
    media: "Build video creative graph: input/prompt → transform hoặc generation contract → upscale/interpolate → encode → preview/export. Encode chỉ hiện capability FFmpeg thực tế.",
    animesr: "Advanced order do bạn chọn: Load → AnimeSR → Frame Interpolation → Encode. AnimeSR/RIFE vẫn partial cho tới smoke riêng.",
  };
  const nodeScope = Object.prototype.hasOwnProperty.call(nodeCopy, pageRoute) ? pageRoute : null;
  if (pageRoute === "image" && activeTab(state, "image") === "advanced") {
    return `${imageModuleTabs(state)}${renderComfyAdvancedV5(state)}`;
  }
  if (pageRoute === "image" && activeTab(state, "image") === "studio") {
    return `${imageModuleTabs(state)}${renderImageMaskStudio(state)}`;
  }
  if (pageRoute === "image" && activeTab(state, "image") === "nodes") {
    return `${heading("ADVANCED WORKFLOW", "Image AI Hub Nodes", "Kéo socket trực tiếp, typed sockets, minimap, multi-select và live preview.")}${imageModuleTabs(state)}${nodeStudio("image", nodeCopy.image)}`;
  }
  if (pageRoute === "image") {
    return `${imageModuleTabs(state)}${renderImageQuickV5(state)}`;
  }
  if (nodeScope && activeTab(state, nodeScope) === "nodes") {
    return `${heading("ADVANCED WORKFLOW", `${nodeScope === "sam2" ? "SAM2" : nodeScope === "animesr" ? "AnimeSR" : nodeScope === "media" ? "Media" : "Image AI"} Nodes`, "Node editor chạy offline trong cửa sổ Local AI Hub.")}${moduleTabs(state, nodeScope)}${nodeStudio(nodeScope, nodeCopy[nodeScope])}`;
  }
  const page = (pages[pageRoute] || renderDashboard)(state);
  return nodeScope ? `${moduleTabs(state, nodeScope)}${page}` : page;
}
