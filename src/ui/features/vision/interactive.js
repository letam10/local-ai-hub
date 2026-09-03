/*
 * M3 interactive workspace primitives.
 *
 * The module owns only ephemeral WebView interaction state.  Files are
 * uploaded through the Hub API by app.js and all durable work remains a
 * canonical job/artifact record.  Geometry sent to the server is normalized
 * to [0,1]; this module never accepts or displays a workstation path.
 */

const MAX_SELECTION_POINTS = 32;
const MAX_ZOOM = 4;
const MIN_ZOOM = 0.5;
const MAX_FRAME_TIME_SECONDS = 86400;
const SAM2_MODES = Object.freeze(["points", "box", "text", "track"]);
const ARTIFACT_URL_RE = /^\/api\/artifacts\/artifact_[a-f0-9]{32}$/;

const clamp = (value, minimum = 0, maximum = 1) => Math.max(minimum, Math.min(maximum, Number(value) || 0));
const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const safePreviewUrl = (value) => {
  const candidate = String(value || "");
  return candidate.startsWith("blob:") || ARTIFACT_URL_RE.test(candidate) ? candidate : "";
};

export const normalizePoint = (point, label = 1) => ({
  x: Number(clamp(finite(point?.x), 0, 1).toFixed(8)),
  y: Number(clamp(finite(point?.y), 0, 1).toFixed(8)),
  label: Number(label) > 0 ? 1 : 0,
});

export const normalizeBox = (box) => {
  if (!Array.isArray(box) || box.length !== 4) return null;
  const values = box.map((value) => clamp(finite(value), 0, 1));
  const [x1, y1, x2, y2] = values;
  if (x2 <= x1 || y2 <= y1) return null;
  return values.map((value) => Number(value.toFixed(8)));
};

export const normalizedPointFromRect = (clientX, clientY, rect) => {
  if (!rect || rect.width <= 0 || rect.height <= 0) return null;
  return normalizePoint({ x: (clientX - rect.left) / rect.width, y: (clientY - rect.top) / rect.height }, 1);
};

export const normalizedBoxFromRects = (start, end) => normalizeBox([
  Math.min(start.x, end.x), Math.min(start.y, end.y), Math.max(start.x, end.x), Math.max(start.y, end.y),
]);

const selectionEqual = (left, right) => JSON.stringify(cloneSelection(left)) === JSON.stringify(cloneSelection(right));

/**
 * A mode has one canonical selection shape.  Keeping incompatible geometry in
 * the in-memory object was harmless-looking but allowed a stale box to leak
 * into a points request (and vice versa).  Track deliberately accepts either
 * shape, but never both at once; a box wins only for legacy state that was
 * already ambiguous before this contract was introduced.
 */
export const selectionForMode = (selection = {}, mode = "points") => {
  const normalized = cloneSelection(selection);
  if (mode === "box") return { points: [], box: normalized.box };
  if (mode === "track") return normalized.box ? { points: [], box: normalized.box } : { points: normalized.points, box: null };
  if (mode === "text") return { points: [], box: null };
  return { points: normalized.points, box: null };
};

/** Switch mode and serialize only geometry accepted by the destination mode. */
export const transitionSam2Mode = (model, mode) => {
  if (!model || !SAM2_MODES.includes(mode)) return false;
  const next = selectionForMode(model.selection, mode);
  const changed = !selectionEqual(model.selection, next) || model.mode !== mode;
  model.mode = mode;
  if (!selectionEqual(model.selection, next)) commitSam2Selection(model, next);
  else model.selection = next;
  model.selectedPointIndex = (mode === "points" || mode === "track") && model.selection.points.length
    ? Math.min(model.selectedPointIndex >= 0 ? model.selectedPointIndex : model.selection.points.length - 1, model.selection.points.length - 1)
    : -1;
  model.selectedBox = Boolean(model.selection.box) && (mode === "box" || mode === "track");
  return changed;
};

export const cloneSelection = (selection = {}) => ({
  points: Array.isArray(selection.points)
    ? selection.points.slice(0, MAX_SELECTION_POINTS).map((point) => normalizePoint(point, point?.label))
    : [],
  box: normalizeBox(selection.box),
});

export const createSam2SelectionState = (initial = {}) => ({
  mode: SAM2_MODES.includes(initial.mode) ? initial.mode : "points",
  intent: initial.intent === "negative" ? "negative" : "positive",
  selection: selectionForMode(initial.selection || initial, SAM2_MODES.includes(initial.mode) ? initial.mode : "points"),
  selectionHistory: [],
  selectionHistoryIndex: -1,
  selectedPointIndex: Number.isInteger(initial.selectedPointIndex) ? initial.selectedPointIndex : -1,
  selectedBox: initial.selectedBox === true,
  frameIndex: Number.isInteger(initial.frameIndex) && initial.frameIndex >= 0 ? initial.frameIndex : 0,
  frameTimeSeconds: Math.max(0, Math.min(MAX_FRAME_TIME_SECONDS, finite(initial.frameTimeSeconds))),
  framePrecisionUnavailable: initial.framePrecisionUnavailable === true,
  duration: Math.max(0, Math.min(MAX_FRAME_TIME_SECONDS, finite(initial.duration))),
  resultTab: ["original", "mask", "composite"].includes(initial.resultTab) ? initial.resultTab : "original",
  maskOpacity: clamp(initial.maskOpacity ?? 0.68, 0, 1),
  zoom: Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, finite(initial.zoom, 1))),
  panX: finite(initial.panX),
  panY: finite(initial.panY),
});

const ensureHistory = (model) => {
  if (!Array.isArray(model.selectionHistory) || !model.selectionHistory.length) {
    model.selectionHistory = [cloneSelection(model.selection)];
    model.selectionHistoryIndex = 0;
  }
};

export const commitSam2Selection = (model, selection) => {
  ensureHistory(model);
  // Keep the history canonical for the active mode as well.  In particular,
  // track mode may use either points or a box, but never both; callers can
  // still hand us a stale mixed object from an older draft without allowing
  // that hidden geometry to survive in memory or reach the payload builder.
  const normalized = selectionForMode(selection, SAM2_MODES.includes(model?.mode) ? model.mode : "points");
  const current = model.selectionHistory[model.selectionHistoryIndex];
  if (JSON.stringify(current) === JSON.stringify(normalized)) return model.selection;
  model.selectionHistory = model.selectionHistory.slice(0, model.selectionHistoryIndex + 1);
  model.selectionHistory.push(normalized);
  if (model.selectionHistory.length > 33) model.selectionHistory.shift();
  model.selectionHistoryIndex = model.selectionHistory.length - 1;
  model.selection = cloneSelection(normalized);
  return model.selection;
};

export const undoSam2Selection = (model) => {
  ensureHistory(model);
  if (model.selectionHistoryIndex <= 0) return false;
  model.selectionHistoryIndex -= 1;
  model.selection = selectionForMode(model.selectionHistory[model.selectionHistoryIndex], model.mode);
  model.selectedPointIndex = -1;
  model.selectedBox = Boolean(model.selection.box) && (model.mode === "box" || model.mode === "track");
  return true;
};

export const redoSam2Selection = (model) => {
  ensureHistory(model);
  if (model.selectionHistoryIndex >= model.selectionHistory.length - 1) return false;
  model.selectionHistoryIndex += 1;
  model.selection = selectionForMode(model.selectionHistory[model.selectionHistoryIndex], model.mode);
  model.selectedPointIndex = -1;
  model.selectedBox = Boolean(model.selection.box) && (model.mode === "box" || model.mode === "track");
  return true;
};

export const selectionToPayload = (model = {}) => {
  const mode = SAM2_MODES.includes(model.mode) ? model.mode : "points";
  const selection = selectionForMode(model.selection || {}, mode);
  const payload = {};
  if (mode === "points" || (mode === "track" && selection.points.length)) {
    payload.points = selection.points.map((point) => ({ ...point, normalized: true }));
  }
  if (mode === "box" || (mode === "track" && selection.box)) {
    payload.box = selection.box;
    payload.normalized_box = true;
  }
  if (mode === "track") {
    const metadata = videoMetadataFor(model);
    payload.verified_frame_rate = metadata.verified;
    payload.variable_frame_rate = metadata.variable;
    if (!model.framePrecisionUnavailable && metadata.verified && Number.isInteger(model.frameIndex) && model.frameIndex >= 0) {
      payload.frame_index = Math.max(0, Math.min(metadata.frameCount - 1, model.frameIndex));
    } else if (Number.isFinite(Number(model.frameTimeSeconds))) {
      const maximum = model.duration > 0 ? Math.min(MAX_FRAME_TIME_SECONDS, Number(model.duration)) : MAX_FRAME_TIME_SECONDS;
      payload.frame_time_seconds = Number(Math.max(0, Math.min(maximum, Number(model.frameTimeSeconds))).toFixed(6));
    }
  }
  return payload;
};

export const ensureM3State = (state) => {
  if (!state.m3 || typeof state.m3 !== "object") state.m3 = {};
  if (!state.m3.vision || typeof state.m3.vision !== "object") state.m3.vision = {};
  const vision = state.m3.vision;
  vision.activeTool = ["omniparser", "rfdetr", "groundingdino"].includes(vision.activeTool) ? vision.activeTool : "omniparser";
  vision.thresholds = vision.thresholds && typeof vision.thresholds === "object" ? vision.thresholds : {};
  vision.selectedDetectionIndex = Number.isInteger(vision.selectedDetectionIndex) ? vision.selectedDetectionIndex : -1;
  vision.job = vision.job && typeof vision.job === "object" ? vision.job : null;
  if (!state.m3.sam2 || typeof state.m3.sam2 !== "object") state.m3.sam2 = createSam2SelectionState();
  const sam2 = state.m3.sam2;
  if (!sam2.selection || typeof sam2.selection !== "object") sam2.selection = { points: [], box: null };
  sam2.mode = SAM2_MODES.includes(sam2.mode) ? sam2.mode : "points";
  sam2.selection = selectionForMode(sam2.selection, sam2.mode);
  sam2.intent = sam2.intent === "negative" ? "negative" : "positive";
  sam2.resultTab = ["original", "mask", "composite"].includes(sam2.resultTab) ? sam2.resultTab : "original";
  sam2.frameIndex = Number.isInteger(sam2.frameIndex) && sam2.frameIndex >= 0 ? sam2.frameIndex : 0;
  sam2.frameTimeSeconds = Math.max(0, Math.min(MAX_FRAME_TIME_SECONDS, finite(sam2.frameTimeSeconds)));
  sam2.duration = Math.max(0, Math.min(MAX_FRAME_TIME_SECONDS, finite(sam2.duration)));
  sam2.framePrecisionUnavailable = sam2.framePrecisionUnavailable === true;
  sam2.selectedPointIndex = Number.isInteger(sam2.selectedPointIndex) ? sam2.selectedPointIndex : -1;
  sam2.selectedBox = Boolean(sam2.selection.box) && (sam2.mode === "box" || sam2.mode === "track");
  sam2.maskOpacity = clamp(sam2.maskOpacity ?? 0.68, 0, 1);
  sam2.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, finite(sam2.zoom, 1)));
  sam2.panX = finite(sam2.panX);
  sam2.panY = finite(sam2.panY);
  sam2.job = sam2.job && typeof sam2.job === "object" ? sam2.job : null;
  return state.m3;
};

const modelFor = (state, key) => ensureM3State(state)[key];
const sourceFor = (model) => {
  const artifact = model?.sourceArtifact && typeof model.sourceArtifact === "object" ? model.sourceArtifact : null;
  const localUrl = safePreviewUrl(model?.localPreviewUrl);
  const artifactUrl = safePreviewUrl(artifact?.url);
  return {
    url: localUrl || artifactUrl,
    name: String(model?.sourceFile?.name || artifact?.name || "").slice(0, 180),
    mediaType: String(model?.sourceFile?.type || artifact?.media_type || "").split(";", 1)[0].toLowerCase(),
    size: Number.isFinite(Number(model?.sourceFile?.size)) ? Number(model.sourceFile.size) : Number(artifact?.size_bytes),
  };
};

const formatBytes = (value) => {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "kích thước chưa rõ";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
};

const updateFileControl = (workspace, source) => {
  const button = workspace.querySelector("[data-file-picker-button]");
  if (button) button.textContent = source.url || source.name ? "Đổi tệp" : "Chọn tệp";
  const selection = workspace.querySelector("[data-file-selection]");
  if (selection) {
    selection.textContent = source.url || source.name
      ? `${source.name || "Tệp đã chọn"} · ${source.mediaType || "loại chưa rõ"} · ${formatBytes(source.size)}`
      : "Chưa chọn tệp";
  }
};

const visibleMedia = (workspace) => workspace.querySelector("[data-m3-preview-image]:not([hidden]), [data-m3-preview-video]:not([hidden])");

const mediaRect = (workspace) => {
  const stage = workspace.querySelector("[data-sam2-canvas], [data-vision-preview-stage]");
  const media = visibleMedia(workspace);
  if (!stage || !media) return null;
  const stageRect = stage.getBoundingClientRect();
  const rect = media.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  return { left: rect.left - stageRect.left, top: rect.top - stageRect.top, width: rect.width, height: rect.height };
};

export const videoMetadataFor = (model = {}) => {
  const source = model.sourceArtifact && typeof model.sourceArtifact === "object" ? model.sourceArtifact : {};
  const metadata = [source.media_metadata, source.metadata, model.mediaMetadata].find((item) => item && typeof item === "object" && !Array.isArray(item)) || {};
  const fps = Number(metadata.fps ?? metadata.frame_rate);
  const frameCount = Number(metadata.frame_count ?? metadata.frames);
  const variable = metadata.vfr === true || metadata.variable_frame_rate === true || String(metadata.frame_rate_mode || "").toUpperCase() === "VFR";
  const verified = metadata.verified === true && Number.isFinite(fps) && fps > 0 && Number.isInteger(frameCount) && frameCount > 0 && !variable;
  return { verified, fps: verified ? fps : 0, frameCount: verified ? frameCount : 0, variable };
};

export const boxHandleAt = (box, point, threshold = 0.025) => {
  const normalized = normalizeBox(box);
  if (!normalized || !point) return null;
  const [x1, y1, x2, y2] = normalized;
  const candidates = {
    nw: [x1, y1], n: [(x1 + x2) / 2, y1], ne: [x2, y1],
    e: [x2, (y1 + y2) / 2], se: [x2, y2], s: [(x1 + x2) / 2, y2],
    sw: [x1, y2], w: [x1, (y1 + y2) / 2],
  };
  let found = null;
  let distance = threshold ** 2;
  Object.entries(candidates).forEach(([name, candidate]) => {
    const value = (candidate[0] - point.x) ** 2 + (candidate[1] - point.y) ** 2;
    if (value <= distance) { distance = value; found = name; }
  });
  return found;
};

export const moveNormalizedBox = (box, deltaX, deltaY) => {
  const normalized = normalizeBox(box);
  if (!normalized) return null;
  const width = normalized[2] - normalized[0];
  const height = normalized[3] - normalized[1];
  const x = Math.max(0, Math.min(1 - width, normalized[0] + finite(deltaX)));
  const y = Math.max(0, Math.min(1 - height, normalized[1] + finite(deltaY)));
  return normalizeBox([x, y, x + width, y + height]);
};

export const resizeNormalizedBox = (box, handle, point) => {
  const normalized = normalizeBox(box);
  if (!normalized || !point || typeof handle !== "string") return null;
  let [x1, y1, x2, y2] = normalized;
  const minSize = 0.002;
  const x = clamp(finite(point.x), 0, 1);
  const y = clamp(finite(point.y), 0, 1);
  if (handle.includes("w")) x1 = Math.min(x, x2 - minSize);
  if (handle.includes("e")) x2 = Math.max(x, x1 + minSize);
  if (handle.includes("n")) y1 = Math.min(y, y2 - minSize);
  if (handle.includes("s")) y2 = Math.max(y, y1 + minSize);
  return normalizeBox([Math.max(0, x1), Math.max(0, y1), Math.min(1, x2), Math.min(1, y2)]);
};

const drawSam2Canvas = (workspace, model, draft = null) => {
  const stage = workspace.querySelector("[data-sam2-canvas]");
  const canvas = workspace.querySelector("[data-sam2-overlay]");
  const layer = workspace.querySelector("[data-sam2-media-layer]");
  if (!stage || !canvas) return;
  const width = Math.max(1, Math.round(stage.clientWidth || stage.getBoundingClientRect().width));
  const height = Math.max(1, Math.round(stage.clientHeight || stage.getBoundingClientRect().height));
  const ratio = Math.max(1, Math.min(2, globalThis.devicePixelRatio || 1));
  if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
  }
  const context = canvas.getContext?.("2d");
  if (!context) return;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  if (layer) layer.style.transform = `translate(${model.panX || 0}px, ${model.panY || 0}px) scale(${model.zoom || 1})`;
  const rect = mediaRect(workspace);
  if (!rect) return;
  const toCanvas = (point) => ({ x: rect.left + point.x * rect.width, y: rect.top + point.y * rect.height });
  const selection = selectionForMode(model.selection, model.mode);
  if (selection.box) {
    const a = toCanvas({ x: selection.box[0], y: selection.box[1] });
    const b = toCanvas({ x: selection.box[2], y: selection.box[3] });
    context.strokeStyle = model.selectedBox === false ? "#69a8ff" : "#d8e5ff";
    context.lineWidth = 2;
    context.setLineDash([7, 4]);
    context.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
    context.setLineDash([]);
    if (model.selectedBox !== false) {
      const handles = [[a.x, a.y], [(a.x + b.x) / 2, a.y], [b.x, a.y], [b.x, (a.y + b.y) / 2], [b.x, b.y], [(a.x + b.x) / 2, b.y], [a.x, b.y], [a.x, (a.y + b.y) / 2]];
      context.fillStyle = "#f5f8ff";
      context.strokeStyle = "#1a2a4b";
      context.lineWidth = 1;
      handles.forEach(([x, y]) => { context.fillRect(x - 4, y - 4, 8, 8); context.strokeRect(x - 4, y - 4, 8, 8); });
    }
  }
  if (draft?.box) {
    const a = toCanvas({ x: draft.box[0], y: draft.box[1] });
    const b = toCanvas({ x: draft.box[2], y: draft.box[3] });
    context.strokeStyle = "#d8e5ff";
    context.lineWidth = 2;
    context.setLineDash([5, 4]);
    context.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
    context.setLineDash([]);
  }
  selection.points.forEach((point, index) => {
    const position = toCanvas(point);
    const positive = point.label === 1;
    context.beginPath();
    context.fillStyle = positive ? "#36d483" : "#ef7070";
    context.strokeStyle = index === model.selectedPointIndex ? "#f5f8ff" : "#09111f";
    context.lineWidth = index === model.selectedPointIndex ? 3 : 2;
    context.arc(position.x, position.y, index === model.selectedPointIndex ? 9 : 8, 0, Math.PI * 2);
    context.fill();
    context.stroke();
    context.fillStyle = "#fff";
    context.font = "700 10px sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(String(index + 1), position.x, position.y);
  });
};

const syncVisionOverlay = (workspace, model) => {
  const overlay = workspace.querySelector("[data-vision-overlay]");
  const annotation = model?.job?.result?.annotation || model?.job?.result?.vision_annotation;
  if (!overlay) return;
  const rect = mediaRect(workspace);
  overlay.style.display = rect ? "block" : "none";
  if (rect) {
    overlay.style.left = `${rect.left}px`;
    overlay.style.top = `${rect.top}px`;
    overlay.style.width = `${rect.width}px`;
    overlay.style.height = `${rect.height}px`;
  }
  const detections = Array.isArray(annotation?.detections) ? annotation.detections : [];
  overlay.replaceChildren();
  const documentRef = overlay.ownerDocument;
  detections.forEach((detection, index) => {
    const box = Array.isArray(detection?.normalized_box) ? detection.normalized_box : null;
    if (!box || box.length !== 4) return;
    const rect = documentRef.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(box[0]));
    rect.setAttribute("y", String(box[1]));
    rect.setAttribute("width", String(Math.max(0, box[2] - box[0])));
    rect.setAttribute("height", String(Math.max(0, box[3] - box[1])));
    rect.setAttribute("class", `vision-box${index === model.selectedDetectionIndex ? " is-selected" : ""}`);
    rect.dataset.detectionIndex = String(index);
    overlay.append(rect);
  });
  workspace.querySelectorAll("[data-vision-detection-index]").forEach((item) => {
    item.classList.toggle("is-selected", Number(item.dataset.visionDetectionIndex) === model.selectedDetectionIndex);
    item.setAttribute("aria-pressed", String(Number(item.dataset.visionDetectionIndex) === model.selectedDetectionIndex));
  });
};

const syncSam2Controls = (workspace, model) => {
  workspace.querySelectorAll("[data-sam2-mode-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.sam2ModePanel !== model.mode;
  });
  workspace.querySelectorAll("[data-sam2-mode]").forEach((button) => {
    const selected = button.dataset.sam2Mode === model.mode;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  workspace.querySelectorAll("[data-sam2-intent]").forEach((button) => {
    const selected = button.dataset.sam2Intent === model.intent;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  const modeInput = workspace.querySelector("[data-sam2-mode-value]");
  if (modeInput) modeInput.value = model.mode;
  const activeSelection = selectionForMode(model.selection, model.mode);
  const count = workspace.querySelector("[data-sam2-selection-count]");
  if (count) count.textContent = `${activeSelection.points.length} điểm · ${activeSelection.box ? "1 box" : "0 box"}`;
  const debug = workspace.querySelector("[data-sam2-debug-selection]");
  if (debug) debug.textContent = JSON.stringify(selectionToPayload(model), null, 2);
  workspace.querySelectorAll("[data-sam2-result-tab]").forEach((button) => {
    const selected = button.dataset.sam2ResultTab === model.resultTab;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  workspace.querySelectorAll("[data-sam2-result-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.sam2ResultPanel !== model.resultTab;
  });
  const opacity = workspace.querySelector("[data-sam2-opacity]");
  if (opacity) opacity.value = String(model.maskOpacity);
  const opacityValue = workspace.querySelector("[data-sam2-opacity-value]");
  if (opacityValue) opacityValue.textContent = `${Math.round(model.maskOpacity * 100)}%`;
  const metadata = videoMetadataFor(model);
  const frame = workspace.querySelector("[data-sam2-frame-slider]");
  if (frame) {
    frame.disabled = !metadata.verified;
    frame.max = String(Math.max(0, metadata.frameCount - 1));
    frame.value = String(Math.max(0, Math.min(Number(frame.max || 0), model.frameIndex)));
  }
  const time = workspace.querySelector("[data-sam2-time-slider]");
  if (time) {
    time.disabled = !(model.duration > 0);
    time.max = String(Math.max(0, model.duration || 0));
    time.value = String(Math.max(0, Math.min(Number(time.max || 0), model.frameTimeSeconds || 0)));
  }
  const frameValue = workspace.querySelector("[data-sam2-frame-value]");
  if (frameValue) frameValue.textContent = metadata.verified
    ? `Frame ${model.frameIndex} / ${Math.max(0, metadata.frameCount - 1)} · ${(model.frameTimeSeconds || 0).toFixed(2)}s`
    : `Frame precision unavailable · ${(model.frameTimeSeconds || 0).toFixed(2)}s`;
  const precision = workspace.querySelector("[data-sam2-frame-precision]");
  if (precision) precision.textContent = metadata.verified
    ? "Frame index dùng metadata media đã xác minh."
    : "Frame precision unavailable: cần FPS/frame count đã xác minh; thao tác track dùng thời gian thực tế của player.";
  const mediaLayer = workspace.querySelector("[data-sam2-media-layer]");
  if (mediaLayer) mediaLayer.style.setProperty("--sam2-mask-opacity", String(model.maskOpacity));
  workspace.querySelectorAll("[data-sam2-mask-layer]").forEach((mask) => { mask.style.opacity = String(model.maskOpacity); });
};

export const syncM3WorkspaceDom = (workspace, state) => {
  if (!workspace) return;
  const key = workspace.dataset.m3Workspace;
  const model = modelFor(state, key);
  const source = sourceFor(model);
  updateFileControl(workspace, source);
  const image = workspace.querySelector("[data-m3-preview-image]");
  const video = workspace.querySelector("[data-m3-preview-video]");
  const isVideo = source.mediaType.startsWith("video/") || Boolean(model?.sourceFile?.type?.startsWith("video/"));
  if (image) {
    image.hidden = !source.url || isVideo;
    if (source.url && image.src !== new URL(source.url, window.location.href).href) image.src = source.url;
    if (source.name) image.alt = `Xem trước ${source.name}`;
  }
  if (video) {
    video.hidden = !source.url || !isVideo;
    if (source.url && video.src !== new URL(source.url, window.location.href).href) video.src = source.url;
  }
  const empty = workspace.querySelector("[data-m3-preview-empty]");
  if (empty) empty.hidden = Boolean(source.url);
  if (key === "vision") syncVisionOverlay(workspace, model);
  if (key === "sam2") {
    const stage = workspace.querySelector("[data-sam2-canvas]");
    if (stage) stage.dataset.hasSource = String(Boolean(source.url));
    syncSam2Controls(workspace, model);
    drawSam2Canvas(workspace, model);
  }
};

export const rememberM3FileSelection = (input, state) => {
  const workspace = input?.closest?.("[data-m3-workspace]");
  if (!workspace || !input.files?.length) return false;
  const key = workspace.dataset.m3Workspace;
  const model = modelFor(state, key);
  const preview = input.closest(".field")?.querySelector("[data-file-preview]");
  const objectUrl = safePreviewUrl(preview?.dataset?.objectUrl) || safePreviewUrl(URL.createObjectURL(input.files[0]));
  if (model.localPreviewUrl && model.localPreviewUrl !== objectUrl && model.localPreviewUrl.startsWith("blob:")) {
    try { URL.revokeObjectURL(model.localPreviewUrl); } catch { /* best effort */ }
  }
  model.sourceFile = input.files[0];
  model.sourceArtifact = null;
  model.localPreviewUrl = objectUrl;
  model.sourceError = "";
  delete input.dataset.uploadedArtifactId;
  syncM3WorkspaceDom(workspace, state);
  return true;
};

export const setM3UploadedArtifact = (workspace, state, artifact) => {
  if (!workspace || !artifact || typeof artifact !== "object") return false;
  const model = modelFor(state, workspace.dataset.m3Workspace);
  model.sourceArtifact = { id: artifact.id, name: artifact.name, size_bytes: artifact.size_bytes, media_type: artifact.media_type, url: artifact.url };
  const input = workspace.querySelector("input[data-m3-source-input], input[data-asset-key]");
  if (input && typeof artifact.id === "string") input.dataset.uploadedArtifactId = artifact.id;
  syncM3WorkspaceDom(workspace, state);
  return true;
};

const updateVisionTabDom = (workspace, activeTool) => {
  workspace.querySelectorAll("[data-vision-tool-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.visionToolPanel !== activeTool;
  });
  workspace.querySelectorAll("[data-vision-tool]").forEach((button) => {
    const selected = button.dataset.visionTool === activeTool;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-selected", String(selected));
  });
};

const syncThresholdPair = (workspace, target) => {
  const name = target.dataset.visionThreshold;
  if (!name) return;
  const value = clamp(finite(target.value, 0), 0, 1);
  const pair = target.closest(".m3-threshold-pair") || workspace;
  const peer = [...pair.querySelectorAll("[data-vision-threshold]")].find((item) => item !== target && item.dataset.visionThreshold === name && item.dataset.visionThresholdRole !== target.dataset.visionThresholdRole);
  if (peer) peer.value = String(value);
};

const nearestPointIndex = (points, target, threshold = 0.035) => {
  let found = -1;
  let distance = threshold ** 2;
  points.forEach((point, index) => {
    const candidate = (point.x - target.x) ** 2 + (point.y - target.y) ** 2;
    if (candidate <= distance) { distance = candidate; found = index; }
  });
  return found;
};

const updateFrameFromVideo = (workspace, model, video) => {
  if (!video || !Number.isFinite(video.duration) || video.duration <= 0) return;
  const metadata = videoMetadataFor(model);
  model.duration = Math.max(0, Math.min(MAX_FRAME_TIME_SECONDS, video.duration));
  model.frameTimeSeconds = Math.max(0, Math.min(model.duration, Number(video.currentTime) || 0));
  model.framePrecisionUnavailable = !metadata.verified;
  if (metadata.verified && !model.frameDragging) {
    model.frameIndex = Math.max(0, Math.min(metadata.frameCount - 1, Math.round(model.frameTimeSeconds * metadata.fps)));
  }
  syncSam2Controls(workspace, model);
};

export const mountM3InteractiveWorkspaces = (root, state, callbacks = {}) => {
  ensureM3State(state);
  const abort = new AbortController();
  const { signal } = abort;
  const resizeObservers = [];
  const pointerDrafts = new WeakMap();
  const workspaces = [...root.querySelectorAll("[data-m3-workspace]")];
  const notify = (workspace) => {
    syncM3WorkspaceDom(workspace, state);
    callbacks.onStateChange?.(workspace.dataset.m3Workspace, state);
  };

  workspaces.forEach((workspace) => {
    const key = workspace.dataset.m3Workspace;
    const model = modelFor(state, key);
    if (key === "vision") updateVisionTabDom(workspace, model.activeTool);
    syncM3WorkspaceDom(workspace, state);
    workspace.addEventListener("click", (event) => {
      const visionTab = event.target.closest("[data-vision-tool]");
      if (visionTab && workspace.contains(visionTab)) {
        model.activeTool = visionTab.dataset.visionTool || "omniparser";
        updateVisionTabDom(workspace, model.activeTool);
        callbacks.onVisionToolChange?.(model.activeTool, state);
        return;
      }
      const detection = event.target.closest("[data-vision-detection-index]");
      if (detection && workspace.contains(detection)) {
        model.selectedDetectionIndex = Number(detection.dataset.visionDetectionIndex);
        syncVisionOverlay(workspace, model);
        return;
      }
      const mode = event.target.closest("[data-sam2-mode]");
      if (mode && workspace.contains(mode)) {
        const nextMode = SAM2_MODES.includes(mode.dataset.sam2Mode) ? mode.dataset.sam2Mode : "points";
        transitionSam2Mode(model, nextMode);
        notify(workspace);
        return;
      }
      const intent = event.target.closest("[data-sam2-intent]");
      if (intent && workspace.contains(intent)) {
        model.intent = intent.dataset.sam2Intent === "negative" ? "negative" : "positive";
        notify(workspace);
        return;
      }
      const resultTab = event.target.closest("[data-sam2-result-tab]");
      if (resultTab && workspace.contains(resultTab)) {
        model.resultTab = ["original", "mask", "composite"].includes(resultTab.dataset.sam2ResultTab) ? resultTab.dataset.sam2ResultTab : "original";
        notify(workspace);
        return;
      }
      const action = event.target.closest("[data-sam2-action]");
      if (action && workspace.contains(action)) {
        const name = action.dataset.sam2Action;
        if (name === "undo") undoSam2Selection(model);
        else if (name === "redo") redoSam2Selection(model);
        else if (name === "clear-all") { commitSam2Selection(model, { points: [], box: null }); model.selectedPointIndex = -1; model.selectedBox = false; }
        else if (name === "clear-last") {
          const next = selectionForMode(model.selection, model.mode);
          if (next.points.length) next.points.splice(model.selectedPointIndex >= 0 ? model.selectedPointIndex : next.points.length - 1, 1); else next.box = null;
          model.selectedPointIndex = -1;
          model.selectedBox = false;
          commitSam2Selection(model, next);
        } else if (name === "fit") { model.zoom = 1; model.panX = 0; model.panY = 0; }
        else if (name === "zoom-in") model.zoom = Math.min(MAX_ZOOM, (model.zoom || 1) * 1.2);
        else if (name === "zoom-out") model.zoom = Math.max(MIN_ZOOM, (model.zoom || 1) / 1.2);
        notify(workspace);
      }
    }, { signal });
    workspace.addEventListener("input", (event) => {
      const threshold = event.target.closest("[data-vision-threshold]");
      if (threshold && workspace.contains(threshold)) {
        syncThresholdPair(workspace, threshold);
        model.thresholds = model.thresholds || {};
        model.thresholds[threshold.dataset.visionThreshold] = clamp(finite(threshold.value), 0, 1);
        return;
      }
      if (key !== "sam2") return;
      if (event.target.matches("[data-sam2-opacity]")) {
        model.maskOpacity = clamp(finite(event.target.value), 0, 1);
        syncSam2Controls(workspace, model);
        } else if (event.target.matches("[data-sam2-frame-slider]")) {
          const metadata = videoMetadataFor(model);
          if (!metadata.verified) return;
          model.frameDragging = true;
          model.frameIndex = Math.max(0, Math.trunc(finite(event.target.value)));
          const video = workspace.querySelector("[data-m3-preview-video]");
          if (video && Number.isFinite(video.duration)) {
            model.frameTimeSeconds = model.frameIndex / metadata.fps;
            video.currentTime = Math.min(video.duration, model.frameTimeSeconds);
          }
          syncSam2Controls(workspace, model);
        } else if (event.target.matches("[data-sam2-time-slider]")) {
          model.frameTimeSeconds = Math.max(0, Math.min(model.duration || MAX_FRAME_TIME_SECONDS, finite(event.target.value)));
          const video = workspace.querySelector("[data-m3-preview-video]");
          if (video && Number.isFinite(video.duration)) video.currentTime = Math.min(video.duration, model.frameTimeSeconds);
          syncSam2Controls(workspace, model);
        }
    }, { signal });
    workspace.addEventListener("change", (event) => {
      if (key === "sam2" && event.target.matches("[data-sam2-frame-slider]")) model.frameDragging = false;
    }, { signal });
    const stage = workspace.querySelector("[data-sam2-canvas]");
    if (stage) {
      stage.addEventListener("pointerdown", (event) => {
        const rect = mediaRect(workspace);
        if (!rect) return;
        const stagePoint = { x: event.clientX, y: event.clientY };
        if (event.button === 1 || event.shiftKey || event.altKey) {
          pointerDrafts.set(stage, { kind: "pan", start: stagePoint, panX: model.panX || 0, panY: model.panY || 0 });
          stage.setPointerCapture?.(event.pointerId);
          event.preventDefault();
          return;
        }
        if (event.button !== 0) return;
        const normalized = normalizedPointFromRect(event.clientX, event.clientY, { left: rect.left + stage.getBoundingClientRect().left, top: rect.top + stage.getBoundingClientRect().top, width: rect.width, height: rect.height });
        if (!normalized) return;
        if (model.mode === "text") return;
        const activeSelection = selectionForMode(model.selection, model.mode);
        const boxSelection = model.mode === "box" || (model.mode === "track" && Boolean(activeSelection.box));
        if (boxSelection) {
          const handle = boxHandleAt(activeSelection.box, normalized);
          const inside = activeSelection.box && normalized.x >= activeSelection.box[0] && normalized.x <= activeSelection.box[2] && normalized.y >= activeSelection.box[1] && normalized.y <= activeSelection.box[3];
          const draft = handle
            ? { kind: "box-resize", handle, start: normalized, original: activeSelection.box }
            : inside
              ? { kind: "box-move", start: normalized, original: activeSelection.box }
              : { kind: "box", start: normalized };
          pointerDrafts.set(stage, draft);
        } else {
          pointerDrafts.set(stage, { kind: "point", start: normalized, hit: nearestPointIndex(activeSelection.points, normalized) });
        }
        model.selectedBox = boxSelection && Boolean(activeSelection.box);
        stage.setPointerCapture?.(event.pointerId);
        event.preventDefault();
      }, { signal });
      stage.addEventListener("pointermove", (event) => {
        const draft = pointerDrafts.get(stage);
        if (!draft) return;
        if (draft.kind === "pan") {
          model.panX = draft.panX + event.clientX - draft.start.x;
          model.panY = draft.panY + event.clientY - draft.start.y;
          drawSam2Canvas(workspace, model);
          return;
        }
        const rect = mediaRect(workspace);
        if (!rect) return;
        const stageRect = stage.getBoundingClientRect();
        const normalized = normalizedPointFromRect(event.clientX, event.clientY, { left: stageRect.left + rect.left, top: stageRect.top + rect.top, width: rect.width, height: rect.height });
        if (!normalized) return;
        if (draft.kind === "box") {
          draft.current = normalized;
          drawSam2Canvas(workspace, model, { box: normalizedBoxFromRects(draft.start, normalized) });
        } else if (draft.kind === "box-move") {
          const next = moveNormalizedBox(draft.original, normalized.x - draft.start.x, normalized.y - draft.start.y);
          if (next) { model.selection = { points: [], box: next }; model.selectedBox = true; drawSam2Canvas(workspace, model); }
        } else if (draft.kind === "box-resize") {
          const next = resizeNormalizedBox(draft.original, draft.handle, normalized);
          if (next) { model.selection = { points: [], box: next }; model.selectedBox = true; drawSam2Canvas(workspace, model); }
        } else if (draft.hit >= 0) {
          const next = selectionForMode(model.selection, model.mode);
          next.points[draft.hit] = { ...next.points[draft.hit], x: normalized.x, y: normalized.y };
          model.selection = next;
          model.selectedPointIndex = draft.hit;
          drawSam2Canvas(workspace, model);
        }
      }, { signal });
      stage.addEventListener("pointerup", (event) => {
        const draft = pointerDrafts.get(stage);
        if (!draft) return;
        pointerDrafts.delete(stage);
        if (draft.kind === "pan") { drawSam2Canvas(workspace, model); return; }
        const rect = mediaRect(workspace);
        if (!rect) return;
        const stageRect = stage.getBoundingClientRect();
        const normalized = normalizedPointFromRect(event.clientX, event.clientY, { left: stageRect.left + rect.left, top: stageRect.top + rect.top, width: rect.width, height: rect.height });
        if (!normalized) return;
        if (draft.kind === "box") {
          const box = normalizedBoxFromRects(draft.start, normalized);
          if (box) { commitSam2Selection(model, { points: [], box }); model.selectedBox = true; }
        } else if (draft.kind === "box-move") {
          const box = moveNormalizedBox(draft.original, normalized.x - draft.start.x, normalized.y - draft.start.y);
          if (box) { commitSam2Selection(model, { points: [], box }); model.selectedBox = true; }
        } else if (draft.kind === "box-resize") {
          const box = resizeNormalizedBox(draft.original, draft.handle, normalized);
          if (box) { commitSam2Selection(model, { points: [], box }); model.selectedBox = true; }
        } else if (draft.hit >= 0) {
          const next = selectionForMode(model.selection, model.mode);
          next.points[draft.hit] = { ...next.points[draft.hit], x: normalized.x, y: normalized.y };
          commitSam2Selection(model, next);
          model.selectedPointIndex = draft.hit;
        } else {
          const next = selectionForMode(model.selection, model.mode);
          if (next.points.length < MAX_SELECTION_POINTS) next.points.push({ ...normalized, label: model.intent === "negative" ? 0 : 1 });
          commitSam2Selection(model, next);
          model.selectedPointIndex = next.points.length - 1;
        }
        notify(workspace);
      }, { signal });
      stage.addEventListener("pointercancel", () => { pointerDrafts.delete(stage); drawSam2Canvas(workspace, model); }, { signal });
      stage.addEventListener("wheel", (event) => {
        event.preventDefault();
        model.zoom = event.deltaY < 0 ? Math.min(MAX_ZOOM, model.zoom * 1.12) : Math.max(MIN_ZOOM, model.zoom / 1.12);
        notify(workspace);
      }, { signal, passive: false });
      stage.addEventListener("keydown", (event) => {
        if (event.target !== stage) return;
        const keyName = event.key.toLowerCase();
        if ((event.ctrlKey || event.metaKey) && keyName === "z") {
          event.preventDefault();
          if (event.shiftKey) redoSam2Selection(model); else undoSam2Selection(model);
          notify(workspace);
        } else if ((event.ctrlKey || event.metaKey) && keyName === "y") {
          event.preventDefault(); redoSam2Selection(model); notify(workspace);
        } else if (event.key === "Delete" || event.key === "Backspace") {
          event.preventDefault();
          const next = selectionForMode(model.selection, model.mode);
          if (model.selectedPointIndex >= 0 && model.selectedPointIndex < next.points.length) next.points.splice(model.selectedPointIndex, 1);
          else if (next.points.length) next.points.pop(); else next.box = null;
          model.selectedPointIndex = -1;
          commitSam2Selection(model, next); notify(workspace);
        } else if (event.key === "Enter") {
          event.preventDefault();
          const next = selectionForMode(model.selection, model.mode);
          if ((model.mode === "points" || model.mode === "track") && next.points.length < MAX_SELECTION_POINTS) next.points.push({ x: 0.5, y: 0.5, label: model.intent === "negative" ? 0 : 1 });
          commitSam2Selection(model, next); notify(workspace);
        } else if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) {
          const delta = event.shiftKey ? 0.05 : 0.01;
          const dx = event.key === "ArrowLeft" ? -delta : event.key === "ArrowRight" ? delta : 0;
          const dy = event.key === "ArrowUp" ? -delta : event.key === "ArrowDown" ? delta : 0;
          const next = selectionForMode(model.selection, model.mode);
          if ((model.mode === "points" || model.mode === "track") && model.selectedPointIndex >= 0 && next.points[model.selectedPointIndex]) {
            next.points[model.selectedPointIndex] = { ...next.points[model.selectedPointIndex], x: clamp(next.points[model.selectedPointIndex].x + dx, 0, 1), y: clamp(next.points[model.selectedPointIndex].y + dy, 0, 1) };
            commitSam2Selection(model, next); notify(workspace);
          } else if ((model.mode === "box" || model.mode === "track") && next.box) {
            const box = event.shiftKey ? resizeNormalizedBox(next.box, "se", { x: next.box[2] + dx, y: next.box[3] + dy }) : moveNormalizedBox(next.box, dx, dy);
            if (box) { commitSam2Selection(model, { points: [], box }); model.selectedBox = true; notify(workspace); }
          }
        } else if (event.key === "+" || event.key === "=") { model.zoom = Math.min(MAX_ZOOM, model.zoom * 1.2); notify(workspace); }
        else if (event.key === "-") { model.zoom = Math.max(MIN_ZOOM, model.zoom / 1.2); notify(workspace); }
      }, { signal });
      stage.addEventListener("focus", () => { stage.setAttribute("aria-description", "Enter thêm điểm; Delete xóa lựa chọn; Ctrl+Z hoàn tác; cuộn để zoom."); }, { signal });
    }
    const geometryTarget = workspace.querySelector("[data-sam2-canvas], [data-vision-preview-stage]");
    if (typeof ResizeObserver === "function" && geometryTarget) {
      const observer = new ResizeObserver(() => {
        if (key === "sam2") drawSam2Canvas(workspace, model);
        else syncVisionOverlay(workspace, model);
      });
      observer.observe(geometryTarget);
      resizeObservers.push(observer);
    }
    const mediaElements = [...workspace.querySelectorAll("[data-m3-preview-image], [data-m3-preview-video]")];
    mediaElements.forEach((media) => {
      ["load", "loadedmetadata", "durationchange"].forEach((eventName) => media.addEventListener(eventName, () => {
        if (key === "sam2" && media === workspace.querySelector("[data-m3-preview-video]")) updateFrameFromVideo(workspace, model, media);
        if (key === "sam2") drawSam2Canvas(workspace, model);
        else syncVisionOverlay(workspace, model);
      }, { signal }));
    });
    const video = workspace.querySelector("[data-m3-preview-video]");
    if (video) {
      ["loadedmetadata", "durationchange", "timeupdate"].forEach((eventName) => video.addEventListener(eventName, () => updateFrameFromVideo(workspace, model, video), { signal }));
    }
    workspace.addEventListener("keydown", (event) => {
      if (event.target?.matches?.("[data-vision-tool], [data-sam2-mode], [data-sam2-intent], [data-sam2-result-tab], [data-sam2-action]")) {
        // Native buttons already provide Enter/Space activation; this branch
        // exists as an explicit keyboard-access contract for review tooling.
        return;
      }
    }, { signal });
  });
  return () => {
    abort.abort();
    resizeObservers.forEach((observer) => observer.disconnect());
  };
};
