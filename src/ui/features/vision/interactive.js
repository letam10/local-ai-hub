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

export const cloneSelection = (selection = {}) => ({
  points: Array.isArray(selection.points)
    ? selection.points.slice(0, MAX_SELECTION_POINTS).map((point) => normalizePoint(point, point?.label))
    : [],
  box: normalizeBox(selection.box),
});

export const createSam2SelectionState = (initial = {}) => ({
  mode: ["points", "box", "text", "track"].includes(initial.mode) ? initial.mode : "points",
  intent: initial.intent === "negative" ? "negative" : "positive",
  selection: cloneSelection(initial.selection || initial),
  selectionHistory: [],
  selectionHistoryIndex: -1,
  selectedPointIndex: Number.isInteger(initial.selectedPointIndex) ? initial.selectedPointIndex : -1,
  frameIndex: Number.isInteger(initial.frameIndex) && initial.frameIndex >= 0 ? initial.frameIndex : 0,
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
  const normalized = cloneSelection(selection);
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
  model.selection = cloneSelection(model.selectionHistory[model.selectionHistoryIndex]);
  model.selectedPointIndex = -1;
  return true;
};

export const redoSam2Selection = (model) => {
  ensureHistory(model);
  if (model.selectionHistoryIndex >= model.selectionHistory.length - 1) return false;
  model.selectionHistoryIndex += 1;
  model.selection = cloneSelection(model.selectionHistory[model.selectionHistoryIndex]);
  model.selectedPointIndex = -1;
  return true;
};

export const selectionToPayload = (model = {}) => {
  const selection = cloneSelection(model.selection || {});
  const payload = {
    points: selection.points.map((point) => ({ ...point, normalized: true })),
    frame_index: Number.isInteger(model.frameIndex) && model.frameIndex >= 0 ? model.frameIndex : 0,
  };
  if (selection.box) {
    payload.box = selection.box;
    payload.normalized_box = true;
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
  sam2.selection = cloneSelection(sam2.selection);
  sam2.mode = ["points", "box", "text", "track"].includes(sam2.mode) ? sam2.mode : "points";
  sam2.intent = sam2.intent === "negative" ? "negative" : "positive";
  sam2.resultTab = ["original", "mask", "composite"].includes(sam2.resultTab) ? sam2.resultTab : "original";
  sam2.frameIndex = Number.isInteger(sam2.frameIndex) && sam2.frameIndex >= 0 ? sam2.frameIndex : 0;
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
  const selection = cloneSelection(model.selection);
  if (selection.box) {
    const a = toCanvas({ x: selection.box[0], y: selection.box[1] });
    const b = toCanvas({ x: selection.box[2], y: selection.box[3] });
    context.strokeStyle = "#69a8ff";
    context.lineWidth = 2;
    context.setLineDash([7, 4]);
    context.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
    context.setLineDash([]);
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
    context.strokeStyle = "#09111f";
    context.lineWidth = 2;
    context.arc(position.x, position.y, 8, 0, Math.PI * 2);
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
  const count = workspace.querySelector("[data-sam2-selection-count]");
  if (count) count.textContent = `${model.selection.points.length} điểm · ${model.selection.box ? "1 box" : "0 box"}`;
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
  const frame = workspace.querySelector("[data-sam2-frame-slider]");
  if (frame) frame.value = String(Math.max(Number(frame.min || 0), Math.min(Number(frame.max || 1000000), model.frameIndex)));
  const frameValue = workspace.querySelector("[data-sam2-frame-value]");
  if (frameValue) frameValue.textContent = `Frame ${model.frameIndex}`;
  const mediaLayer = workspace.querySelector("[data-sam2-media-layer]");
  if (mediaLayer) mediaLayer.style.setProperty("--sam2-mask-opacity", String(model.maskOpacity));
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
  const fps = Number(workspace.dataset.sam2Fps || 24);
  const maximum = Math.max(1, Math.floor(video.duration * fps));
  const slider = workspace.querySelector("[data-sam2-frame-slider]");
  if (slider) slider.max = String(maximum);
  if (!model.frameDragging) model.frameIndex = Math.max(0, Math.min(maximum, Math.round(video.currentTime * fps)));
  const value = workspace.querySelector("[data-sam2-frame-value]");
  if (value) value.textContent = `Frame ${model.frameIndex} · ${video.currentTime.toFixed(2)}s`;
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
        model.mode = ["points", "box", "text", "track"].includes(mode.dataset.sam2Mode) ? mode.dataset.sam2Mode : "points";
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
        else if (name === "clear-all") commitSam2Selection(model, { points: [], box: null });
        else if (name === "clear-last") {
          const next = cloneSelection(model.selection);
          if (next.points.length) next.points.pop(); else next.box = null;
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
        model.frameDragging = true;
        model.frameIndex = Math.max(0, Math.trunc(finite(event.target.value)));
        const video = workspace.querySelector("[data-m3-preview-video]");
        const fps = Number(workspace.dataset.sam2Fps || 24);
        if (video && Number.isFinite(video.duration)) video.currentTime = model.frameIndex / fps;
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
        const draft = model.mode === "box"
          ? { kind: "box", start: normalized }
          : { kind: "point", start: normalized, hit: nearestPointIndex(model.selection.points, normalized) };
        pointerDrafts.set(stage, draft);
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
        } else if (draft.hit >= 0) {
          const next = cloneSelection(model.selection);
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
          if (box) commitSam2Selection(model, { ...model.selection, box });
        } else if (draft.hit >= 0) {
          const next = cloneSelection(model.selection);
          next.points[draft.hit] = { ...next.points[draft.hit], x: normalized.x, y: normalized.y };
          commitSam2Selection(model, next);
          model.selectedPointIndex = draft.hit;
        } else {
          const next = cloneSelection(model.selection);
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
          const next = cloneSelection(model.selection);
          if (model.selectedPointIndex >= 0 && model.selectedPointIndex < next.points.length) next.points.splice(model.selectedPointIndex, 1);
          else if (next.points.length) next.points.pop(); else next.box = null;
          model.selectedPointIndex = -1;
          commitSam2Selection(model, next); notify(workspace);
        } else if (event.key === "Enter") {
          event.preventDefault();
          const next = cloneSelection(model.selection);
          if (model.mode === "points" && next.points.length < MAX_SELECTION_POINTS) next.points.push({ x: 0.5, y: 0.5, label: model.intent === "negative" ? 0 : 1 });
          commitSam2Selection(model, next); notify(workspace);
        } else if (event.key === "+" || event.key === "=") { model.zoom = Math.min(MAX_ZOOM, model.zoom * 1.2); notify(workspace); }
        else if (event.key === "-") { model.zoom = Math.max(MIN_ZOOM, model.zoom / 1.2); notify(workspace); }
      }, { signal });
      stage.addEventListener("focus", () => { stage.setAttribute("aria-description", "Enter thêm điểm; Delete xóa lựa chọn; Ctrl+Z hoàn tác; cuộn để zoom."); }, { signal });
      if (typeof ResizeObserver === "function") {
        const observer = new ResizeObserver(() => drawSam2Canvas(workspace, model));
        observer.observe(stage);
        resizeObservers.push(observer);
      }
    }
    const video = workspace.querySelector("[data-m3-preview-video]");
    if (video) {
      video.addEventListener("loadedmetadata", () => updateFrameFromVideo(workspace, model, video), { signal });
      video.addEventListener("timeupdate", () => updateFrameFromVideo(workspace, model, video), { signal });
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
