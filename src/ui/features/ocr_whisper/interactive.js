/*
 * M4 OCR/Whisper interaction state.
 *
 * This module owns ephemeral preview, PDF-page, OCR-region and media-timeline
 * state only.  Durable jobs and artifacts remain server-owned; no local path
 * is placed in a request or public projection.
 */

const MAX_PDF_PAGE = 100000;
const MAX_DURATION_SECONDS = 86400;
const ARTIFACT_URL_RE = /^\/api\/artifacts\/artifact_[a-f0-9]{32}$/;

const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, Number(value) || 0));
const safePreviewUrl = (value) => {
  const candidate = String(value || "");
  return candidate.startsWith("blob:") || ARTIFACT_URL_RE.test(candidate) ? candidate : "";
};
const numberValue = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;

const createOcrState = (initial = {}) => ({
  resultTab: ["text", "markdown", "tables", "json", "boxes"].includes(initial.resultTab) ? initial.resultTab : "text",
  pdfPage: Number.isInteger(initial.pdfPage) && initial.pdfPage >= 1 ? initial.pdfPage : 1,
  pdfPageCount: Number.isInteger(initial.pdfPageCount) && initial.pdfPageCount >= 0 ? initial.pdfPageCount : 0,
  region: Array.isArray(initial.region) ? initial.region.slice(0, 4) : null,
  sourceFile: initial.sourceFile || null,
  sourceArtifact: initial.sourceArtifact || null,
  localPreviewUrl: safePreviewUrl(initial.localPreviewUrl),
  sourceError: "",
  job: initial.job && typeof initial.job === "object" ? initial.job : null,
});

const createWhisperState = (initial = {}) => ({
  resultTab: ["transcript", "srt", "json"].includes(initial.resultTab) ? initial.resultTab : "transcript",
  currentTime: Math.max(0, numberValue(initial.currentTime)),
  duration: clamp(numberValue(initial.duration), 0, MAX_DURATION_SECONDS),
  start: clamp(numberValue(initial.start), 0, MAX_DURATION_SECONDS),
  end: clamp(numberValue(initial.end, 10), 0, MAX_DURATION_SECONDS),
  sourceFile: initial.sourceFile || null,
  sourceArtifact: initial.sourceArtifact || null,
  localPreviewUrl: safePreviewUrl(initial.localPreviewUrl),
  sourceError: "",
  job: initial.job && typeof initial.job === "object" ? initial.job : null,
});

export const ensureM4State = (state) => {
  if (!state.m4 || typeof state.m4 !== "object") state.m4 = {};
  if (!state.m4.ocr || typeof state.m4.ocr !== "object") state.m4.ocr = createOcrState();
  if (!state.m4.whisper || typeof state.m4.whisper !== "object") state.m4.whisper = createWhisperState();
  const ocr = state.m4.ocr;
  ocr.resultTab = ["text", "markdown", "tables", "json", "boxes"].includes(ocr.resultTab) ? ocr.resultTab : "text";
  ocr.pdfPage = Math.max(1, Math.min(MAX_PDF_PAGE, Math.trunc(numberValue(ocr.pdfPage, 1))));
  ocr.pdfPageCount = Math.max(0, Math.min(MAX_PDF_PAGE, Math.trunc(numberValue(ocr.pdfPageCount))));
  ocr.region = Array.isArray(ocr.region) && ocr.region.length === 4 ? ocr.region.map((value) => clamp(value, 0, 1)) : null;
  const whisper = state.m4.whisper;
  whisper.resultTab = ["transcript", "srt", "json"].includes(whisper.resultTab) ? whisper.resultTab : "transcript";
  whisper.duration = clamp(whisper.duration, 0, MAX_DURATION_SECONDS);
  whisper.currentTime = clamp(whisper.currentTime, 0, whisper.duration || MAX_DURATION_SECONDS);
  whisper.start = clamp(whisper.start, 0, whisper.duration || MAX_DURATION_SECONDS);
  whisper.end = clamp(whisper.end, whisper.start, whisper.duration || MAX_DURATION_SECONDS);
  if (whisper.end <= whisper.start) whisper.end = Math.min(MAX_DURATION_SECONDS, whisper.start + 0.1);
  return state.m4;
};

const modelFor = (state, key) => ensureM4State(state)[key];
const sourceFor = (model) => {
  const artifact = model?.sourceArtifact && typeof model.sourceArtifact === "object" ? model.sourceArtifact : {};
  const url = safePreviewUrl(model?.localPreviewUrl) || safePreviewUrl(artifact.url);
  return {
    url,
    name: String(model?.sourceFile?.name || artifact.name || "").slice(0, 180),
    mediaType: String(model?.sourceFile?.type || artifact.media_type || "").split(";", 1)[0].toLowerCase(),
    size: Number.isFinite(Number(model?.sourceFile?.size)) ? Number(model.sourceFile.size) : Number(artifact.size_bytes),
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
  if (selection) selection.textContent = source.url || source.name
    ? `${source.name || "Tệp đã chọn"} · ${source.mediaType || "loại chưa rõ"} · ${formatBytes(source.size)}`
    : "Chưa chọn tệp";
};

const syncResultTabs = (workspace, model) => {
  workspace.querySelectorAll("[data-m4-result-tab]").forEach((button) => {
    const selected = button.dataset.m4ResultTab === model.resultTab;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  workspace.querySelectorAll("[data-m4-result-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.m4ResultPanel !== model.resultTab;
  });
};

const normalizedPoint = (clientX, clientY, rect) => {
  if (!rect || rect.width <= 0 || rect.height <= 0) return null;
  return { x: clamp((clientX - rect.left) / rect.width, 0, 1), y: clamp((clientY - rect.top) / rect.height, 0, 1) };
};

const normalizedRegion = (start, end) => {
  if (!start || !end) return null;
  const region = [Math.min(start.x, end.x), Math.min(start.y, end.y), Math.max(start.x, end.x), Math.max(start.y, end.y)];
  return region[2] - region[0] > 0.002 && region[3] - region[1] > 0.002 ? region.map((value) => Number(value.toFixed(8))) : null;
};

const actualMediaRect = (stage, media) => {
  if (!stage || !media || media.hidden) return null;
  const stageRect = stage.getBoundingClientRect();
  const mediaRect = media.getBoundingClientRect();
  if (mediaRect.width <= 0 || mediaRect.height <= 0) return null;
  return { left: mediaRect.left - stageRect.left, top: mediaRect.top - stageRect.top, width: mediaRect.width, height: mediaRect.height };
};

const activeWhisperMedia = (workspace) => workspace.querySelector("[data-m4-whisper-audio]:not([hidden]), [data-m4-whisper-video]:not([hidden])");

const syncOcrDom = (workspace, model) => {
  const source = sourceFor(model);
  updateFileControl(workspace, source);
  const isPdf = source.mediaType === "application/pdf" || /\.pdf$/i.test(source.name);
  const ocrResult = model.job?.result?.ocr_result || model.job?.result?.ocr;
  const resultPages = Array.isArray(ocrResult?.pages) ? ocrResult.pages : [];
  if (resultPages.length) model.pdfPageCount = Math.max(model.pdfPageCount || 0, ...resultPages.map((page) => Math.max(0, Math.trunc(numberValue(page?.page_number)))));
  const image = workspace.querySelector("[data-m4-ocr-image]");
  const pdf = workspace.querySelector("[data-m4-ocr-pdf]");
  const hasSupportedSource = Boolean(source.url) && (isPdf || source.mediaType.startsWith("image/"));
  if (image) {
    image.hidden = !source.url || isPdf || !source.mediaType.startsWith("image/");
    if (source.url && !isPdf && image.src !== source.url) image.src = source.url;
    if (source.name) image.alt = `Xem trước ${source.name}`;
  }
  if (pdf) {
    pdf.hidden = !source.url || !isPdf;
    if (source.url && isPdf) {
      const page = Math.max(1, Math.min(MAX_PDF_PAGE, model.pdfPage));
      const separator = source.url.includes("#") ? "&" : "#";
      const target = `${source.url}${separator}page=${page}`;
      if (pdf.src !== target) pdf.src = target;
    }
  }
  const empty = workspace.querySelector("[data-m4-media-empty]");
  if (empty) empty.hidden = hasSupportedSource;
  const region = workspace.querySelector("[data-ocr-region]");
  const activeRegion = model.regionDraft || model.region;
  const stage = workspace.querySelector("[data-m4-ocr-stage]");
  const mediaRect = actualMediaRect(stage, image);
  if (region) {
    region.hidden = !activeRegion || !mediaRect;
    if (activeRegion && mediaRect) {
      region.style.left = `${mediaRect.left + activeRegion[0] * mediaRect.width}px`;
      region.style.top = `${mediaRect.top + activeRegion[1] * mediaRect.height}px`;
      region.style.width = `${(activeRegion[2] - activeRegion[0]) * mediaRect.width}px`;
      region.style.height = `${(activeRegion[3] - activeRegion[1]) * mediaRect.height}px`;
    }
  }
  const selectionSummary = workspace.querySelector("[data-ocr-selection-summary]");
  if (selectionSummary) selectionSummary.textContent = activeRegion
    ? `Vùng chuẩn hóa: [${activeRegion.map((value) => Number(value.toFixed(4))).join(", ")}]`
    : "Toàn trang · click và kéo trên ảnh để giới hạn vùng OCR.";
  const resultOverlay = workspace.querySelector("[data-ocr-result-overlay]");
  if (resultOverlay) {
    resultOverlay.style.display = mediaRect ? "block" : "none";
    if (mediaRect) {
      resultOverlay.style.left = `${mediaRect.left}px`;
      resultOverlay.style.top = `${mediaRect.top}px`;
      resultOverlay.style.width = `${mediaRect.width}px`;
      resultOverlay.style.height = `${mediaRect.height}px`;
    }
    resultOverlay.replaceChildren();
    const page = resultPages.find((item) => Number(item?.page_number) === model.pdfPage) || resultPages[0];
    const blocks = Array.isArray(page?.blocks) ? page.blocks : [];
    blocks.forEach((block, index) => {
      const box = Array.isArray(block?.normalized_box) && block.normalized_box.length === 4 ? block.normalized_box : null;
      if (!box || isPdf) return;
      const element = resultOverlay.ownerDocument.createElement("div");
      element.className = "m4-ocr-result-box";
      element.style.left = `${box[0] * 100}%`;
      element.style.top = `${box[1] * 100}%`;
      element.style.width = `${(box[2] - box[0]) * 100}%`;
      element.style.height = `${(box[3] - box[1]) * 100}%`;
      element.title = `OCR ${index + 1}`;
      resultOverlay.append(element);
    });
  }
  const pageInput = workspace.querySelector("[data-ocr-page-number]");
  const pageSupported = workspace.dataset.ocrPageSupported === "true";
  if (pageInput) {
    pageInput.value = String(model.pdfPage);
    pageInput.max = String(model.pdfPageCount || MAX_PDF_PAGE);
    pageInput.disabled = !pageSupported || !Boolean(source.url && isPdf);
  }
  const pageValue = workspace.querySelector("[data-ocr-page-value]");
  if (pageValue) pageValue.textContent = model.pdfPageCount ? `Trang ${model.pdfPage} / ${model.pdfPageCount}` : `Trang ${model.pdfPage} / chưa xác định`;
  const previous = workspace.querySelector("[data-ocr-page-action=previous]");
  const next = workspace.querySelector("[data-ocr-page-action=next]");
  const hasPdfSource = Boolean(source.url && isPdf);
  if (previous) previous.disabled = !pageSupported || !hasPdfSource || model.pdfPage <= 1;
  if (next) next.disabled = !pageSupported || !hasPdfSource || model.pdfPage >= (model.pdfPageCount || MAX_PDF_PAGE);
  syncResultTabs(workspace, model);
};

const timeLabel = (seconds) => {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(value / 60);
  const remainder = value % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
};

const syncWhisperDom = (workspace, model) => {
  const source = sourceFor(model);
  updateFileControl(workspace, source);
  const isVideo = source.mediaType.startsWith("video/") || /\.(mp4|webm|mov|mkv)$/i.test(source.name);
  const audio = workspace.querySelector("[data-m4-whisper-audio]");
  const video = workspace.querySelector("[data-m4-whisper-video]");
  const media = isVideo ? video : audio;
  if (audio) audio.hidden = !source.url || isVideo;
  if (video) video.hidden = !source.url || !isVideo;
  if (media) {
    if (source.url && media.src !== source.url) {
      media.src = source.url;
      media.load?.();
    }
  }
  const empty = workspace.querySelector("[data-m4-media-empty]");
  if (empty) empty.hidden = Boolean(source.url && (isVideo || source.mediaType.startsWith("audio/")));
  const duration = model.duration > 0 ? model.duration : MAX_DURATION_SECONDS;
  const current = workspace.querySelector("[data-whisper-current]");
  const startRange = workspace.querySelector("[data-whisper-start-range]");
  const endRange = workspace.querySelector("[data-whisper-end-range]");
  const startNumber = workspace.querySelector("[data-whisper-start-number]");
  const endNumber = workspace.querySelector("[data-whisper-end-number]");
  [current, startRange, endRange].forEach((input) => { if (input) input.max = String(duration); });
  if (current) current.value = String(Math.min(model.currentTime, duration));
  if (startRange) startRange.value = String(Math.min(model.start, duration));
  if (endRange) endRange.value = String(Math.min(model.end, duration));
  if (startNumber) startNumber.value = String(Number(model.start.toFixed(2)));
  if (endNumber) endNumber.value = String(Number(model.end.toFixed(2)));
  const currentLabel = workspace.querySelector("[data-whisper-current-time]");
  const durationLabel = workspace.querySelector("[data-whisper-duration]");
  if (currentLabel) currentLabel.textContent = timeLabel(model.currentTime);
  if (durationLabel) durationLabel.textContent = model.duration > 0 ? timeLabel(model.duration) : "--:--";
  const timelineProgress = workspace.querySelector("[data-whisper-timeline-progress]");
  if (timelineProgress) timelineProgress.style.width = `${duration > 0 ? clamp(model.currentTime / duration, 0, 1) * 100 : 0}%`;
  const rangeNote = workspace.querySelector("[data-whisper-range-note]");
  if (rangeNote) rangeNote.textContent = `Đoạn chọn: ${timeLabel(model.start)} – ${timeLabel(model.end)}`;
  syncResultTabs(workspace, model);
};

export const syncM4WorkspaceDom = (workspace, state) => {
  if (!workspace) return;
  const key = workspace.dataset.m4Workspace;
  const model = modelFor(state, key);
  if (key === "ocr") syncOcrDom(workspace, model);
  if (key === "whisper") syncWhisperDom(workspace, model);
};

export const rememberM4FileSelection = (input, state) => {
  const workspace = input?.closest?.("[data-m4-workspace]");
  if (!workspace || !input.files?.length) return false;
  const key = workspace.dataset.m4Workspace;
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
  if (key === "ocr") {
    model.pdfPage = 1;
    model.pdfPageCount = 0;
    model.region = null;
  } else {
    model.currentTime = 0;
    model.duration = 0;
    model.start = 0;
    model.end = 10;
  }
  syncM4WorkspaceDom(workspace, state);
  return true;
};

export const setM4UploadedArtifact = (workspace, state, artifact) => {
  if (!workspace || !artifact || typeof artifact !== "object") return false;
  const model = modelFor(state, workspace.dataset.m4Workspace);
  model.sourceArtifact = {
    id: artifact.id,
    name: artifact.name,
    size_bytes: artifact.size_bytes,
    media_type: artifact.media_type,
    url: artifact.url,
  };
  const input = workspace.querySelector("input[data-m4-source-input], input[data-asset-key]");
  if (input && typeof artifact.id === "string") input.dataset.uploadedArtifactId = artifact.id;
  syncM4WorkspaceDom(workspace, state);
  return true;
};

export const mountM4InteractiveWorkspaces = (root, state, callbacks = {}) => {
  ensureM4State(state);
  const abort = new AbortController();
  const { signal } = abort;
  const observers = [];
  const regionDrafts = new WeakMap();
  const workspaces = [...root.querySelectorAll("[data-m4-workspace]")];
  const notify = (workspace) => {
    syncM4WorkspaceDom(workspace, state);
    callbacks.onStateChange?.(workspace.dataset.m4Workspace, state);
  };
  workspaces.forEach((workspace) => {
    const key = workspace.dataset.m4Workspace;
    const model = modelFor(state, key);
    syncM4WorkspaceDom(workspace, state);
    workspace.addEventListener("click", (event) => {
      const resultTab = event.target.closest("[data-m4-result-tab]");
      if (resultTab && workspace.contains(resultTab)) {
        model.resultTab = resultTab.dataset.m4ResultTab || model.resultTab;
        notify(workspace);
        return;
      }
      if (key === "ocr") {
        const pageAction = event.target.closest("[data-ocr-page-action]");
        if (pageAction && workspace.contains(pageAction)) {
          const delta = pageAction.dataset.ocrPageAction === "previous" ? -1 : 1;
          model.pdfPage = Math.max(1, Math.min(model.pdfPageCount || MAX_PDF_PAGE, model.pdfPage + delta));
          notify(workspace);
          return;
        }
        if (event.target.closest("[data-ocr-clear-region]") && workspace.dataset.ocrRegionSupported === "true") {
          model.region = null;
          notify(workspace);
          return;
        }
      }
      if (key === "whisper") {
        const seek = event.target.closest("[data-whisper-seek]");
        if (seek && workspace.contains(seek)) {
          const seconds = Math.max(0, numberValue(seek.dataset.whisperSeek) / 1000);
          model.currentTime = seconds;
           const media = activeWhisperMedia(workspace);
          if (media && Number.isFinite(media.duration)) media.currentTime = Math.min(seconds, media.duration);
          syncWhisperDom(workspace, model);
        }
      }
    }, { signal });
    workspace.addEventListener("input", (event) => {
      if (key === "ocr" && event.target.matches("[data-ocr-page-number]")) {
        model.pdfPage = Math.max(1, Math.min(model.pdfPageCount || MAX_PDF_PAGE, Math.trunc(numberValue(event.target.value, 1))));
        syncOcrDom(workspace, model);
        return;
      }
      if (key !== "whisper") return;
      const target = event.target;
      if (target.matches("[data-whisper-current]")) {
        model.currentTime = clamp(target.value, 0, model.duration || MAX_DURATION_SECONDS);
        const media = workspace.querySelector("[data-m4-whisper-audio]:not([hidden]), [data-m4-whisper-video]:not([hidden])");
        if (media && Number.isFinite(media.duration)) media.currentTime = model.currentTime;
      } else if (target.matches("[data-whisper-start-range], [data-whisper-start-number]")) {
        model.start = clamp(target.value, 0, model.duration || MAX_DURATION_SECONDS);
        if (model.end <= model.start) model.end = Math.min(MAX_DURATION_SECONDS, model.start + 0.1);
      } else if (target.matches("[data-whisper-end-range], [data-whisper-end-number]")) {
        model.end = clamp(target.value, model.start, model.duration || MAX_DURATION_SECONDS);
      }
      syncWhisperDom(workspace, model);
    }, { signal });
    const mediaElements = [...workspace.querySelectorAll("[data-m4-whisper-audio], [data-m4-whisper-video]")];
    mediaElements.forEach((media) => {
      ["loadedmetadata", "durationchange"].forEach((eventName) => media.addEventListener(eventName, () => {
        if (media !== activeWhisperMedia(workspace)) return;
        if (Number.isFinite(media.duration) && media.duration > 0) {
          model.duration = Math.min(MAX_DURATION_SECONDS, media.duration);
          model.currentTime = Math.min(model.currentTime, model.duration);
          model.end = Math.min(Math.max(model.end, model.start + 0.1), model.duration);
          syncWhisperDom(workspace, model);
        }
      }, { signal }));
      media.addEventListener("timeupdate", () => {
        if (media !== activeWhisperMedia(workspace)) return;
        if (Number.isFinite(media.currentTime)) {
          model.currentTime = Math.max(0, media.currentTime);
          const current = workspace.querySelector("[data-whisper-current]");
          if (current) current.value = String(model.currentTime);
          const label = workspace.querySelector("[data-whisper-current-time]");
          if (label) label.textContent = timeLabel(model.currentTime);
          const progress = workspace.querySelector("[data-whisper-timeline-progress]");
          if (progress) progress.style.width = `${model.duration > 0 ? clamp(model.currentTime / model.duration, 0, 1) * 100 : 0}%`;
        }
      }, { signal });
    });
    const stage = workspace.querySelector("[data-m4-ocr-stage]");
    const image = workspace.querySelector("[data-m4-ocr-image]");
    if (key === "ocr" && stage && image) {
      ["load", "error"].forEach((eventName) => image.addEventListener(eventName, () => syncOcrDom(workspace, model), { signal }));
      stage.addEventListener("pointerdown", (event) => {
        if (event.button !== 0 || image.hidden || workspace.dataset.ocrRegionSupported !== "true") return;
        const draft = { start: normalizedPoint(event.clientX, event.clientY, image.getBoundingClientRect()) };
        if (!draft.start) return;
        regionDrafts.set(stage, draft);
        stage.setPointerCapture?.(event.pointerId);
        event.preventDefault();
      }, { signal });
      stage.addEventListener("pointermove", (event) => {
        const draft = regionDrafts.get(stage);
        if (!draft) return;
        draft.end = normalizedPoint(event.clientX, event.clientY, image.getBoundingClientRect());
        model.regionDraft = normalizedRegion(draft.start, draft.end);
        syncOcrDom(workspace, model);
      }, { signal });
      const finishRegion = (event) => {
        const draft = regionDrafts.get(stage);
        if (!draft) return;
        model.region = normalizedRegion(draft.start, draft.end);
        delete model.regionDraft;
        regionDrafts.delete(stage);
        stage.releasePointerCapture?.(event.pointerId);
        notify(workspace);
      };
      stage.addEventListener("pointerup", finishRegion, { signal });
      stage.addEventListener("pointercancel", finishRegion, { signal });
    }
    if (typeof ResizeObserver === "function") {
      const observer = new ResizeObserver(() => syncM4WorkspaceDom(workspace, state));
      observer.observe(workspace);
      observers.push(observer);
    }
  });
  return () => {
    abort.abort();
    observers.forEach((observer) => observer.disconnect());
  };
};
