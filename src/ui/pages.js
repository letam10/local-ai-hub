import { escapeHtml, formatGb, formatStatus } from "./api.js";

export const NAVIGATION = [
  { group: "Điều khiển", items: [
    ["dashboard", "Dashboard", "⌂"],
    ["airi", "AIRI", "✦"],
  ] },
  { group: "Vision & Speech", items: [
    ["vision", "Vision Studio", "◉"],
    ["sam2", "SAM 2", "◌"],
    ["ocr", "OCR / Documents", "▤"],
    ["whisper", "Whisper / Subtitles", "≋"],
    ["voice", "Voice Studio", "◒"],
  ] },
  { group: "Image & Media", items: [
    ["image", "Image AI", "✧"],
    ["media", "Media Editor", "▸"],
    ["animesr", "AnimeSR", "▥"],
  ] },
  { group: "Quản lý", items: [
    ["jobs", "Jobs / Queue", "☷"],
    ["models", "Models & Storage", "◫"],
    ["settings", "Settings", "⚙"],
  ] },
];

const APP_IDS = {
  airi: "airi",
  sam2: "sam2-mask-studio",
  animesr: "anime-upscale-studio",
  flux: "local-image-studio",
  qwen: "qwen-image-studio",
};

const statusPill = (status, label = formatStatus(status)) =>
  `<span class="status-pill" data-status="${escapeHtml(status)}"><span class="status-dot"></span>${escapeHtml(label)}</span>`;

const heading = (eyebrow, title, description, actions = "") => `
  <div class="page-heading">
    <div class="heading-copy"><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${description}</p></div>
    <div class="heading-actions">${actions}</div>
  </div>`;

const card = (title, body, footer = "", extra = "") => `
  <section class="card ${extra}"><div class="card-title-row"><h2>${title}</h2></div>${body}${footer ? `<div class="card-footer">${footer}</div>` : ""}</section>`;

const appById = (state, id) => (state.applications || []).find((item) => item.id === id) || null;
const componentById = (state, id) => (state.components || []).find((item) => item.id === id) || null;

const launchButton = (id, label = "Mở ứng dụng") =>
  `<button class="button button--primary" type="button" data-launch="${id}">${label}</button>`;

const capabilityCard = (name, description, status, action = "") => card(
  escapeHtml(name),
  `<p class="card-description">${escapeHtml(description)}</p>`,
  `${statusPill(status)}${action ? `<span>${action}</span>` : ""}`,
);

export function renderPage(id, state) {
  const health = state.health || {};
  if (id === "dashboard") return renderDashboard(state, health);
  if (id === "airi") return renderAiri(state);
  if (id === "vision") return renderVision(state);
  if (id === "sam2") return renderSam2(state);
  if (id === "ocr") return renderOcr(state);
  if (id === "whisper") return renderWhisper(state);
  if (id === "voice") return renderVoice(state);
  if (id === "image") return renderImage(state);
  if (id === "media") return renderMedia(state);
  if (id === "animesr") return renderAnime(state);
  if (id === "jobs") return renderJobs(state);
  if (id === "models") return renderModels(state);
  if (id === "settings") return renderSettings(state);
  return renderDashboard(state, health);
}

function renderDashboard(state, health) {
  const disk = health.disk || {};
  const gpu = health.gpu || {};
  const components = state.components || [];
  const running = components.filter((item) => item.component_status === "running").length;
  const jobs = state.jobs || [];
  const active = jobs.filter((item) => ["starting", "running"].includes(item.status)).length;
  const warnings = components.filter((item) => ["missing", "error"].includes(item.component_status));
  return heading("TỔNG QUAN", "Dashboard", "Một mặt điều khiển thống nhất cho runtime, model và các ứng dụng AI cục bộ.") + `
    <div class="card-grid">
      ${card("Hub status", `<div class="stat-value">${escapeHtml(formatStatus(health.status))}</div><div class="stat-label">API loopback đang phục vụ frontend chung</div>`, statusPill(health.status || "unknown"))}
      ${card("GPU", `<div class="stat-value">${escapeHtml(gpu.name || "Chưa phát hiện")}</div><div class="stat-label">${gpu.memory_free_mib ? `${escapeHtml(gpu.memory_free_mib)} MiB VRAM trống` : "Không tải model khi khởi động"}</div>`, statusPill(gpu.available ? "operational" : "partial", gpu.available ? "Sẵn sàng" : "Theo dõi"))}
      ${card("Ổ đĩa", `<div class="stat-value">${escapeHtml(formatGb(disk.free_bytes))}</div><div class="stat-label">dung lượng trống</div>`, statusPill(disk.free_bytes && disk.free_bytes < 20 * 1024 ** 3 ? "partial" : "operational", disk.free_bytes && disk.free_bytes < 20 * 1024 ** 3 ? "Sắp đầy" : "Bình thường"))}
      ${card("Tác vụ", `<div class="stat-value">${active}</div><div class="stat-label">đang chạy · ${jobs.length} bản ghi trong hàng đợi</div>`, statusPill(active ? "running" : "operational", active ? "Đang xử lý" : "Nhàn rỗi"))}
      ${card("Runtime", `<div class="stat-value">${running}</div><div class="stat-label">dịch vụ đang lắng nghe hoặc đã đăng ký</div>`, statusPill("installed", `${components.length} đã đăng ký`))}
      ${card("Model policy", `<div class="stat-value">On demand</div><div class="stat-label">tối đa một GPU nặng cùng lúc</div>`, statusPill("operational", "An toàn mặc định"))}
    </div>
    <div class="card-grid card-grid--wide" style="margin-top:14px">
      ${card("Dịch vụ gần đây", components.length ? `<div class="row-list">${components.slice(0, 6).map((item) => `<div class="row-item"><div class="row-main"><div class="row-name">${escapeHtml(item.name || item.id)}</div><div class="row-meta">${escapeHtml(item.kind || "component")}</div></div>${statusPill(item.component_status || item.status || "unknown")}</div>`).join("")}</div>` : `<div class="empty-state">Chưa có component trong registry.</div>`)}
      ${card("Cảnh báo cần chú ý", warnings.length ? `<ul class="notice-list">${warnings.slice(0, 5).map((item) => `<li>${escapeHtml(item.name || item.id)}: ${escapeHtml(formatStatus(item.component_status))}</li>`).join("")}</ul>` : `<div class="callout">Không có lỗi component được phát hiện trong lần đọc gần nhất.</div>`, "", "card--flat")}
    </div>`;
}

function renderAiri(state) {
  const app = appById(state, APP_IDS.airi);
  return heading("ỨNG DỤNG NGOÀI", "AIRI", "AIRI vẫn do trình cài đặt Windows quản lý; Hub chỉ lưu registry an toàn và điểm khởi chạy.", app?.launchable ? launchButton(APP_IDS.airi) : "") + `
    <div class="card-grid card-grid--wide">
      ${card("Trạng thái cài đặt", `<div class="split"><div><div class="stat-value" style="font-size:20px">${escapeHtml(app?.display_name || "AIRI")}</div><div class="stat-label">${escapeHtml(app?.managed_location || "External managed")}</div></div>${statusPill(app?.component_status || "missing")}</div>`, app?.launchable ? launchButton(APP_IDS.airi) : "")}
      ${card("Tích hợp Hub", `<ul class="notice-list"><li>Launch và trạng thái process: allowlist</li><li>Provider/voice/vision: chỉ hiển thị khi registry cung cấp</li><li>MCP: giữ stdio, không đọc API key</li></ul>`, "", "card--flat")}
    </div>`;
}

function renderVision(state) {
  const items = [
    ["OmniParser", "omniparser", "Phân tích giao diện và vùng tương tác."],
    ["RF-DETR", "rfdetr", "Phát hiện đối tượng với ngưỡng do backend hỗ trợ."],
    ["Grounding DINO", "groundingdino", "Grounding theo prompt; không tải đồng thời mọi model."],
  ];
  return heading("VISION", "Vision Studio", "Chọn một engine cho mỗi tác vụ. Backend chưa xác minh sẽ được báo rõ là partial/unavailable.") + `
    <div class="module-tabs">${items.map(([name], index) => `<button class="tab ${index === 0 ? "is-selected" : ""}" type="button">${name}</button>`).join("")}</div>
    <div class="card-grid">${items.map(([name, id, description]) => { const item = componentById(state, id); return capabilityCard(name, description, item?.component_status || "missing"); }).join("")}</div>
    <div class="callout" style="margin-top:14px">Input dự kiến: kéo/thả, clipboard hoặc file. Output: preview, ảnh chú thích, JSON, boxes, labels và scores khi adapter tương ứng đã sẵn sàng.</div>`;
}

function renderSam2(state) {
  const app = appById(state, APP_IDS.sam2);
  const component = componentById(state, "sam2");
  return heading("VISION", "SAM 2", "SAM2 Mask Studio đã được quản lý trong registry; direct adapter chỉ được đánh operational sau smoke thật.", app?.launchable ? launchButton(APP_IDS.sam2, "Mở SAM2 Mask Studio") : "") + `
    <div class="card-grid card-grid--wide">
      ${capabilityCard("Backend direct", "Grounding DINO → boxes → SAM2 → mask chưa được Hub gọi trực tiếp.", "unavailable")}
      ${card("GUI hiện có", `<div class="split"><div><h3>SAM2 Mask Studio</h3><p class="card-description">${escapeHtml(app?.managed_location || "External managed")}; model nạp theo yêu cầu.</p></div>${statusPill(component?.component_status || app?.component_status || "missing")}</div>`, app?.launchable ? launchButton(APP_IDS.sam2, "Mở GUI") : "")}
    </div>
    <div class="callout callout--warning" style="margin-top:14px">Hub không giả vờ tạo mask khi adapter direct chưa xác minh. Nút trên mở workflow GUI đã đăng ký.</div>`;
}

function renderOcr(state) {
  const item = componentById(state, "paddleocr_vl");
  return heading("DOCUMENTS", "OCR / Documents", "Một giao diện cho ảnh, PDF, clipboard và thư mục; kết quả có thể là text, Markdown, JSON hoặc bảng.") + `
    <div class="card-grid card-grid--wide">
      ${capabilityCard("PaddleOCR-VL", "Adapter allowlist có sẵn; chỉ gọi khi environment và model local đã tồn tại.", item?.component_status || "missing")}
      ${card("Input & output", `<div class="tag-list"><span class="tag">Ảnh</span><span class="tag">PDF</span><span class="tag">Clipboard</span><span class="tag">Folder</span><span class="tag">Text</span><span class="tag">Markdown</span><span class="tag">JSON</span><span class="tag">Tables</span></div>`, "", "card--flat")}
    </div>`;
}

function renderWhisper(state) {
  const item = componentById(state, "whisper");
  return heading("SPEECH", "Whisper / Subtitles", "Xếp hàng nhiều video, giữ source nguyên vẹn và xuất SRT/ASS khi backend đã xác minh.") + `
    <div class="card-grid card-grid--wide">
      ${capabilityCard("Faster-Whisper", "Transcribe và translate theo chính sách on-demand; CPU fallback được giữ khi CUDA lỗi.", item?.component_status || "missing")}
      ${card("Quy trình", `<div class="tag-list"><span class="tag">Add video</span><span class="tag">Add folder</span><span class="tag">Language</span><span class="tag">Auto detect</span><span class="tag">Translate</span><span class="tag">SRT</span><span class="tag">ASS</span><span class="tag">Burn subtitle</span><span class="tag">Resume</span></div>`, "Không ghi đè media nguồn", "card--flat")}
    </div>`;
}

function renderVoice(state) {
  const tts = componentById(state, "qwen3_tts");
  const seed = componentById(state, "seed_vc");
  return heading("VOICE", "Voice Studio", "TTS, Voice Design, Voice Clone, chuyển giọng và batch dùng các model đã có; không tự tải thêm model.") + `
    <div class="card-grid">
      ${capabilityCard("Qwen3-TTS", "0.6B CustomVoice · 1.7B VoiceDesign · 1.7B Base", tts?.component_status || "missing")}
      ${capabilityCard("Seed-VC", "Voice conversion theo preset tiny đã đăng ký.", seed?.component_status || "missing")}
      ${card("Chức năng", `<div class="tag-list"><span class="tag">Text to Speech</span><span class="tag">Voice Design</span><span class="tag">Voice Clone</span><span class="tag">Voice Conversion</span><span class="tag">Voice Library</span><span class="tag">Batch</span></div>`, "Nạp model theo yêu cầu", "card--flat")}
    </div>`;
}

function renderImage(state) {
  const flux = appById(state, APP_IDS.flux);
  const qwen = appById(state, APP_IDS.qwen);
  const comfy = componentById(state, "comfyui");
  const models = state.models || [];
  const qwenInstalled = models.some((item) => String(item.engine || "").toLowerCase().includes("qwen image") && item.installed);
  return heading("IMAGE", "Image AI", "Một frontend cho FLUX, Qwen Image và ComfyUI Advanced; cả hai model image dùng chung một ComfyUI runtime.", flux?.launchable ? launchButton(APP_IDS.flux, "Mở Local Image Studio") : "") + `
    <div class="module-tabs"><button class="tab is-selected" type="button">Qwen Image</button><button class="tab" type="button">FLUX</button><button class="tab" type="button">ComfyUI Advanced</button></div>
    <div class="card-grid">
      ${capabilityCard("Qwen Image 2512", "Prompt, resolution, steps, seed và model selector. Không tự tải model.", qwenInstalled ? "installed" : (qwen?.component_status || "not_installed"), qwen?.launchable ? launchButton(APP_IDS.qwen, "Mở Studio") : "")}
      ${capabilityCard("FLUX.2 Klein", "Workflow text-to-image local, output review và model manifest được giữ nguyên.", flux?.component_status || "missing", flux?.launchable ? launchButton(APP_IDS.flux, "Mở Studio") : "")}
      ${capabilityCard("ComfyUI", "Shared runtime cho image workflows; chỉ expose thao tác đã allowlist.", comfy?.component_status || "missing")}
    </div>
    <div class="card" style="margin-top:14px"><div class="card-title-row"><h2>Tham số an toàn</h2>${statusPill("installed", "UI sẵn sàng")}</div><div class="form-grid"><div class="field"><label for="image-prompt">Prompt</label><textarea id="image-prompt" placeholder="Nhập prompt khi backend image được kết nối…" disabled></textarea></div><div class="stack"><div class="field"><label>Resolution</label><select disabled><option>Backend quyết định</option></select></div><div class="field"><label>Steps / Seed</label><input disabled placeholder="Chỉ hiện tham số backend hỗ trợ" /></div></div></div><div class="callout" style="margin-top:13px">Sinh ảnh chưa được gọi trực tiếp bởi Hub API trong smoke này. Nút mở Studio dùng ứng dụng local đã cài, không tạo bản model thứ hai.</div></div>`;
}

function renderMedia(state) {
  const ffmpeg = componentById(state, "ffmpeg");
  return heading("MEDIA", "Media Editor", "Các thao tác FFmpeg an toàn được chuẩn hoá theo preset; không có ô raw shell command.") + `
    <div class="card-grid card-grid--wide">
      ${capabilityCard("FFmpeg / FFprobe", "Probe, trim, cut, concat, resize, crop, rotate, transcode, mux và trích frame.", ffmpeg?.component_status || "missing")}
      ${card("Preset thao tác", `<div class="tag-list"><span class="tag">Probe</span><span class="tag">Trim / Cut</span><span class="tag">Concat</span><span class="tag">Resize / Crop</span><span class="tag">Extract Audio</span><span class="tag">Mux</span><span class="tag">Burn Subtitle</span><span class="tag">Image Sequence → Video</span></div>`, "Không ghi đè source", "card--flat")}
    </div><div class="callout" style="margin-top:14px">Route probe loopback đã allowlist. Các thao tác ghi file cần input/output riêng và sẽ không tự chạy khi chưa có file do người dùng chọn.</div>`;
}

function renderAnime(state) {
  const app = appById(state, APP_IDS.animesr);
  const component = componentById(state, "animesr");
  return heading("VIDEO AI", "AnimeSR", "Anime Upscale Studio được quản lý từ registry; executor Hub giữ trạng thái trung thực cho đến khi có smoke inference ngắn.", app?.launchable ? launchButton(APP_IDS.animesr, "Mở Anime Upscale Studio") : "") + `
    <div class="card-grid card-grid--wide">
      ${card("Ứng dụng", `<div class="split"><div><h3>Anime Upscale Studio</h3><p class="card-description">${escapeHtml(app?.managed_location || "External managed")}; FFmpeg và AnimeSR dùng đường dẫn canonical/junction.</p></div>${statusPill(app?.component_status || "missing")}</div>`, app?.launchable ? launchButton(APP_IDS.animesr, "Mở ứng dụng") : "")}
      ${capabilityCard("upscale_anime_video", "Input, output, model, queue, progress, cancel, resume và mở output.", component?.component_status === "installed" ? "partial" : "unavailable")}
    </div><div class="callout callout--warning" style="margin-top:14px">Hub không tạo job giả. Khi route chưa có smoke inference, hãy mở ứng dụng đã quản lý để chạy workflow được kiểm soát.</div>`;
}

function renderJobs(state) {
  const jobs = state.jobs || [];
  return heading("CONTROL PLANE", "Jobs / Queue", "Theo dõi trạng thái queued, starting, running, completed, failed và cancelled.") + `
    ${jobs.length ? `<div class="card table-wrap"><table><thead><tr><th>Job</th><th>Tool</th><th>Trạng thái</th><th>Thời điểm</th></tr></thead><tbody>${jobs.map((job) => `<tr><td>${escapeHtml(job.id)}</td><td>${escapeHtml(job.tool)}</td><td>${statusPill(job.status)}</td><td class="muted">${escapeHtml(job.created_at || "")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state">Chưa có job. Các model nặng không được tự nạp khi mở Hub.</div>`}`;
}

function renderModels(state) {
  const storage = state.storage || {};
  const areas = storage.areas || {};
  const models = state.models || [];
  const areaRows = Object.entries(areas).map(([name, value]) => `<div class="row-item"><div class="row-main"><div class="row-name">${escapeHtml(name)}</div><div class="row-meta">managed area</div></div><strong>${escapeHtml(formatGb(value.bytes))}</strong></div>`).join("");
  return heading("STORAGE", "Models & Storage", "Model store canonical, môi trường, runtime, cache, output, temp, logs và legacy paths được xem ở một nơi.", `<button class="button" type="button" data-refresh-storage>Quét lại</button>`) + `
    <div class="card-grid card-grid--wide">
      ${card("Dung lượng theo vùng", `<div class="row-list">${areaRows || `<div class="empty-state">Chưa có dữ liệu storage.</div>`}</div>`, `Trống: ${escapeHtml(formatGb(storage.disk?.free_bytes))}`)}
      ${card("Legacy paths", `<div class="stat-value" style="font-size:20px">${storage.legacy_counts?.total || 0}</div><div class="stat-label">${storage.legacy_counts?.cleanup_candidates || 0} ứng viên cleanup có điều kiện</div><div class="callout callout--warning" style="margin-top:12px">Cleanup chỉ hiện preview; không xoá UNKNOWN, USER_DATA hoặc SYSTEM_MANAGED.</div>`, "", "card--flat")}
    </div>
    <section class="card" style="margin-top:14px"><div class="card-title-row"><h2>Model registry</h2><span class="muted small">Nạp theo yêu cầu</span></div>${models.length ? `<div class="table-wrap"><table><thead><tr><th>Model</th><th>Engine</th><th>Vị trí</th><th>Kích thước</th><th>Trạng thái</th></tr></thead><tbody>${models.map((item) => `<tr><td>${escapeHtml(item.model_name)}</td><td>${escapeHtml(item.engine)}</td><td>${escapeHtml(item.location)}</td><td>${escapeHtml(formatGb(item.size?.bytes))}</td><td>${statusPill(item.installed ? "installed" : "not_installed")}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state">Chưa có model registry.</div>`}</section>`;
}

function renderSettings(state) {
  const settings = state.settings || {};
  return heading("SYSTEM", "Settings", "Thiết lập an toàn cho startup, giao diện, đường dẫn, GPU, model, API, MCP, AIRI và storage.") + `
    <div class="card-grid card-grid--wide">
      ${card("General & Appearance", `<div class="row-list"><div class="row-item"><span>Start maximized</span><strong>${settings.start_maximized ? "Bật" : "Tắt"}</strong></div><div class="row-item"><span>Minimum window</span><strong>${settings.minimum_width || 1280} × ${settings.minimum_height || 720}</strong></div><div class="row-item"><span>Theme</span><strong id="theme-label">System / local</strong></div></div>`, `<button class="button" type="button" data-cycle-theme>Đổi theme</button>`)}
      ${card("GPU & Model policy", `<div class="row-list"><div class="row-item"><span>Load policy</span><strong>${escapeHtml(settings.model_load_policy || "on_demand")}</strong></div><div class="row-item"><span>Heavy GPU slots</span><strong>${settings.max_heavy_gpu_jobs || 1}</strong></div><div class="row-item"><span>Startup</span><strong>Không tự load model nặng</strong></div></div>`, "", "card--flat")}
      ${card("API & MCP", `<div class="row-list"><div class="row-item"><span>Bind</span><strong>${escapeHtml(settings.api_bind || "127.0.0.1")}:${settings.api_port || 8765}</strong></div><div class="row-item"><span>MCP transport</span><strong>${escapeHtml(settings.mcp_transport || "stdio")}</strong></div><div class="row-item"><span>Shell access</span><strong>Không allowlist</strong></div></div>`, "", "card--flat")}
      ${card("Storage safety", `<ul class="notice-list"><li>Không overwrite source media</li><li>Không duplicate model nhiều GB</li><li>Cleanup cần preview + xác nhận</li><li>Legacy junction có rollback metadata</li></ul>`, "", "card--flat")}
    </div>`;
}
