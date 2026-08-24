/*
  FILE NOTE
  - Mục đích: Render templates cho toàn bộ các trang giao diện của Local AI Hub (Dashboard, Jobs, Vision, Image, Video, Voice, Models, Settings, etc.)
  - Liên kết trực tiếp: src/ui/app.js, src/ui/i18n.js, src/ui/api.js, src/ui/styles.css
  - Vùng ảnh hưởng khi sửa: Toàn bộ nội dung hiển thị của các module, thẻ volume C:/D:, danh sách jobs, artifact previews
*/

import * as shared from "./shared/rendering.js";
const { escapeHtml, formatGb, formatStatus, translateText, uiText, uiTextHtml, dynamicTextHtml, NAVIGATION, component, tool, app, opaqueArtifactId, opaqueArtifactUrl, safeArtifactName, artifactMetadata, artifactProvenance, isMaskArtifact, READINESS_STATUS_LABELS, UI_STATUS_RE, uiStatus, readinessStatus, readinessStatusLabel, statusPill, unsafeUiText, safeUiText, safeUiIdentifier, safeJobId, safeJobStatus, safeJobTimestamp, safeJobCount, safeArtifactMediaType, safeJobArtifacts, safeJobProvenance, safeHotJobDetail, safeReadinessModules, safeStorageVolumes, safeResourceGpu, safeResourceFits, safeResourceErrors, safeResourceActions, safeResourcePlan, readinessSnapshot, MEDIA_EVIDENCE_OPERATIONS, MEDIA_EVIDENCE_LABELS, MEDIA_EVIDENCE_OUTCOME_LABELS, MEDIA_EVIDENCE_EXECUTION_LABELS, unsafeMediaEvidenceText, isMediaEvidenceRecord, exactMediaOperationList, safeMediaEvidenceText, mediaEvidenceFallback, normalizeRuntimeMediaEvidence, normalizeMediaOperationScope, mediaCapabilityEvidence, mediaEvidencePanel, JOB_STATUS_RANK, textKey, jobRecoverySnapshot, readinessModuleDetails, readinessFitLabel, readinessResourceDetails, readinessStorageDetails, heading, card, cardDynamic, field, fieldDynamic, file, files, button, capability, workspaceState, workflowLibraryState, activeTab, moduleTabs, imageModuleTabs, nodeStudio, imageWorkflowRail, videoWorkflowRail, visionWorkflowRail, artifacts, legacyArtifactList, artifactList, provenanceList, formResult } = shared;
export { NAVIGATION, jobRecoverySnapshot };
import { renderProductionModels } from "./features/models/models.js";
import { createDashboardRenderer } from "./features/dashboard/render.js";
import { createJobsRenderer } from "./features/jobs/render.js";
import { createComponentsRenderer } from "./features/components/render.js";
import { createDiagnosticsRenderer } from "./features/diagnostics/render.js";
import { createSettingsRenderer } from "./features/settings/render.js";
import { createAiriRenderer } from "./features/airi/render.js";
import { createVisionRenderer } from "./features/vision/render.js";
import { createSam2Renderer } from "./features/sam2/render.js";
import { createOcrRenderer } from "./features/ocr/render.js";
import { createWhisperRenderer } from "./features/whisper/render.js";
import { createVoiceRenderer } from "./features/voice/render.js";
import { createImageAiRenderer } from "./features/image_ai/render.js";
import { createMediaRenderer } from "./features/media/render.js";
import { createAnimesrRenderer } from "./features/animesr/render.js";
import { createProjectsRenderer } from "./features/projects/render.js";
import { createImageMaskRenderer } from "./features/image_mask/render.js";

// Compatibility pass for legacy templates that still emit a fixed text node
// without a data-i18n marker. Exact text nodes only: server-owned names,
// identifiers, reasons and artifact metadata are never traversed.
const LEGACY_STATIC_COPY = Object.freeze([
  "CONTROL PLANE", "Dashboard", "Readiness metrics", "Hub API", "Module plan",
  "Static readiness snapshot", "Preflight is read-only; install/download is not_run",
  "Review the server snapshot", "Check readiness", "Review module health and attention.",
  "Choose a route", "Open an existing Hub workspace.", "Run from Jobs", "Keep progress and artifacts in Hub.",
  "Readiness snapshot needs review", "Job needs review", "No server-owned module evidence.",
  "SERVER-OWNED VOLUME", "Available", "Unavailable", "Total", "Free", "Used", "Low space", "Next action",
  "Storage projection unavailable", "C:/ and D:/ figures are not available in this snapshot.",
  "Low-space warning", "review storage before new writes.", "Volume statistics are unavailable; no figures are shown.",
  "Verify that the volume is mounted and readable, then refresh storage.", "VISION", "DOCUMENTS", "SPEECH", "VOICE",
  "IMAGE", "MEDIA", "VIDEO AI", "VISION WORKFLOW", "VIDEO WORKFLOW", "Load Input", "Detect / Segment / OCR",
  "Preview & Export", "Transcript queue", "Projects, Assets & Recipes", "Asset Library", "Prompts & Recipes",
  "Compare Board", "Workflow Gallery", "All", "Active", "Attention", "Completed", "Component Operations",
  "Diagnostics subsystem is healthy.", "Diagnostics subsystem is unavailable.", "Diagnostics subsystem state is unknown.",
  "Diagnostics subsystem needs attention.", "No action required.", "Review the managed diagnostic source manually.",
  "Review the bounded diagnostic details.", "Review the managed subsystem state before retrying.",
  "Volume statistics are available from the server-owned allowlist.", "No action is required; refresh after external storage changes.",
  "The server recovery snapshot has jobs that need review.", "Open Jobs to review the server-owned recovery state.",
  "No bounded media acceptance invocation was recorded.", "Keep media operations partial until a separately authorized bounded acceptance is recorded.",
  "Server snapshot only; the UI does not execute media operations.", "Outcome", "Execution", "Cleanup", "Source overwrite", "Reason", "Not checked", "Video grade", "Logo overlay", "Encode", "Generic Media action",
  "Generic media actions are not covered by the three exact server evidence rows.", "Phương tiện operation state is shown from the server snapshot before any separately authorized work.", "Media operation state is shown from the server snapshot before any separately authorized work.",
  "Generic media remains Partial.", "Only exact server-owned evidence can be operational.", "Generic actions remain explanatory and cannot submit a runtime request here.",
  "Artifact previews use opaque Hub URLs and native metadata/range transport.", "Review the exact operation scope; no generic action is enabled from this snapshot.",
  "Generic video and image actions", "Primary media input", "Additional inputs", "Secondary audio or subtitle", "Operation", "Read metadata", "Trim", "Concat", "Resize", "Crop", "Rotate", "Transcode", "Extract audio", "Replace audio", "Mux audio/video", "Burn subtitle", "Extract frames", "Start", "End", "Width", "Height", "FPS", "Rotation", "Flip", "Image format", "Horizontal", "Vertical",
  "Review detailed evidence", "Open server snapshot in Settings", "View exact operation scope", "Generic Media actions remain Partial",
  "Generic media actions are not covered by the three exact evidence rows.", "Use only the published evidence rows; no generic media execution is claimed.",
  "Use the exact evidence summary first; this UI does not claim generic media execution.", "Execution unavailable from this snapshot",
  "No recovery reason was published in this snapshot.", "Review the job state and create a new task when recovery is unavailable.",
  "No fixed catalog leaf is present under the managed root.", "Review the tracked license contract before any install action is enabled.",
  "Complete the separately reviewed provider authorization flow before creating an install plan.",
  "Keep the component non-automatic until every failed requirement and catalog disposition are explicitly reviewed.",
  "Inspect the existing managed runtime; do not download or replace it from this acceptance view.",
  "Managed asset discovery is static only; no files, providers, or workflows were accessed.",
  "Static server-owned evidence is available without runtime execution.", "The requested static section is unavailable or ambiguous.",
  "Restore a unique validated managed source under the fixed server-owned root.", "Catalog output is static only and does not execute imported graphs.",
  "Run the static package CLI; reserve runtime checks for a separately authorized bounded smoke.",
  "No release-evidence packet was published for this local snapshot.", "Use the compatibility report or resource planner; no code has been loaded.",
  "Use the server-owned result for static QA, collection evaluation, or later authorized integration planning.",
  "Review the static evidence before requesting separately authorized runtime work.",
  "This descriptor requires separately configured ComfyUI and model evidence; discovery does not launch either one.",
  "Managed privacy policy passed static validation. No OS, process, device, or runtime probe was performed.",
  "Use the server-owned policy result for static diagnostics only.", "Managed policy discovery is static only. No machine or runtime state was accessed.",
  "Use only the server-owned policy summaries for diagnostics planning.",
  "The managed descriptor passed static validation, but no runtime graph smoke has been authorized.",
  "Use server-owned validated output for a later bounded integration preflight.",
  "Track do máy chủ sở hữu hàng đợi and recovery state; actions only appear when the published record gates them.",
  "Image sequence to video", "Image resize", "Image crop", "Image rotate", "Image flip", "Image convert", "Image compress",
  "Size unavailable", "Check All Updates", "Output", "Image", "AIRI external", "Open AIRI Settings in the AIRI application; Hub only uses an allowlist to call the registered launcher.",
  "No V8 operation", "Model", "Category", "Size", "Status / action", "Import Model", "Check Update", "Download & Install", "Authorize & Install", "Review License", "Manual Review", "Plan Update", "Roll Back", "Update Center", "Source", "Auth", "License", "Integrity", "Fit", "No fit", "Unknown", "Physical fit", "Concurrent fit", "Resource notes", "Next safe action", "RESOURCE PREFLIGHT", "Dry-run resource fit", "Mode", "Target", "Server-owned", "STORAGE CONSTRAINTS", "Allowlisted volume constraints", "Server-owned snapshot", "Low-space action", "Readiness is derived from server-owned static capability evidence.", "Review the module plan before requesting runtime work.", "Publish a server-owned evidence packet and obtain manager QA admission.", "Static descriptors were validated and this extension declares no runtime workload.", "Use the static CLI output for review and reserve runtime verification for a separately authorized smoke.", "The catalog passed static contract checks, but no asset filesystem or provider operation was performed.", "Review dependency preflight and run a bounded approved smoke before enabling image generation.", "No per-module resource fit was published.", "Resource fit is planning evidence only; no provider, install, repair, uninstall, GPU or media operation ran.", "current server capability snapshot",
]);

const localizeLegacyMarkup = (html) => LEGACY_STATIC_COPY.reduce((result, key) => {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return result.replace(new RegExp(`>(\\s*)${escaped}(\\s*)<`, "g"), (_match, prefix, suffix) => `>${prefix}${uiTextHtml(key)}${suffix}<`);
}, String(html || ""));

const renderDashboardFeature = createDashboardRenderer({
  uiTextHtml, escapeHtml, formatGb, readinessSnapshot, jobRecoverySnapshot, readinessStatus,
  safeReadinessModules, statusPill, readinessStatusLabel, textKey, mediaEvidencePanel,
  workflowLibraryState, formatStatus,
});
const renderJobsFeature = createJobsRenderer({ jobRecoverySnapshot, escapeHtml, uiTextHtml, statusPill, readinessStatusLabel, artifactList, provenanceList, heading });
const renderComponentsFeature = createComponentsRenderer({ heading, escapeHtml, statusPill, uiTextHtml, uiText });
const renderDiagnosticsFeature = createDiagnosticsRenderer({ heading, escapeHtml, statusPill });
const renderSettingsFeature = createSettingsRenderer({ heading, card, escapeHtml, uiTextHtml, statusPill, readinessStatusLabel, readinessSnapshot, readinessModuleDetails, readinessResourceDetails, readinessStorageDetails, mediaEvidencePanel });
const renderAiri = createAiriRenderer({ app, heading, card, escapeHtml, statusPill });
const renderVision = createVisionRenderer({ component, tool, heading, visionWorkflowRail, capability, card, file, field, button, formResult });
const renderSam2 = createSam2Renderer({ component, tool, formatStatus, heading, statusPill, workspaceState, card, file, field, button, formResult });
const renderOcr = createOcrRenderer({ component, tool, heading, statusPill, card, file, field, button, formResult });
const renderWhisper = createWhisperRenderer({ component, tool, heading, statusPill, card, file, field, button, formResult });
const renderVoice = createVoiceRenderer({ component, tool, heading, capability, card, field, file, button, formResult });
const { renderImageQuickV5, renderImage } = createImageAiRenderer({ tool, heading, escapeHtml, formatStatus, workspaceState, imageWorkflowRail, card, field, file, button, formResult });
const renderMedia = createMediaRenderer({ component, tool, mediaCapabilityEvidence, heading, statusPill, mediaEvidencePanel, workspaceState, videoWorkflowRail, card, file, files, field, formResult, escapeHtml });
const renderAnime = createAnimesrRenderer({ component, tool, heading, statusPill, workspaceState, card, file, field, button, formResult });
const renderCreativeWorkspace = createProjectsRenderer({ heading, card, cardDynamic, field, fieldDynamic, button, escapeHtml, statusPill, workflowLibraryState, formatGb });
const { renderImageMaskStudio, renderComfyAdvancedV5 } = createImageMaskRenderer({ heading, card, field, file, button, escapeHtml, statusPill });

// Dashboard implementation lives in src/ui/features/dashboard/render.js; this legacy block is intentionally removed.











function renderModels(state) {
  return renderProductionModels({ productionCatalog: state.productionCatalog, legacyModels: state.models, storage: state.storage, updateCenter: state.updateCenter, filters: state.modelFilters, escapeHtml, formatGb, statusPill, card, heading, uiTextHtml });
}

// Components implementation lives in src/ui/features/components/render.js.
// Diagnostics implementation lives in src/ui/features/diagnostics/render.js.
// Settings implementation lives in src/ui/features/settings/render.js.
export function renderPage(route, state) {
  const pages = {
    dashboard: renderDashboardFeature,
    airi: renderAiri,
    vision: renderVision,
    sam2: renderSam2,
    ocr: renderOcr,
    whisper: renderWhisper,
    voice: renderVoice,
    image: renderImageQuickV5,
    media: renderMedia,
    animesr: renderAnime,
    projects: renderCreativeWorkspace,
    jobs: renderJobsFeature,
    components: renderComponentsFeature,
    models: renderModels,
    diagnostics: renderDiagnosticsFeature,
    settings: renderSettingsFeature,
  };
  const pageRoute = route === "video" ? "media" : route;
  const nodeCopy = {
    image: "Compose FLUX/Qwen, SAM2 mask và image transforms trong cùng graph; preset JSON được track, workflow cá nhân autosave local.",
    sam2: "Advanced workflow: Grounding DINO → SAM2 → mask/composite/export. GPU nodes chỉ chạy khi bấm Run Graph.",
    media: "Build video creative graph: input/prompt → transform hoặc generation contract → upscale/interpolate → encode → preview/export. Encode chỉ hiện capability FFmpeg thực tế.",
    animesr: "Advanced order do bạn chọn: Load → AnimeSR → Frame Interpolation → Encode. AnimeSR/RIFE vẫn partial cho tới smoke riêng.",
  };
  const nodeScope = Object.prototype.hasOwnProperty.call(nodeCopy, pageRoute) ? pageRoute : null;
  if (pageRoute === "image" && activeTab(state, "image") === "advanced") {
    return localizeLegacyMarkup(`${imageModuleTabs(state)}${renderComfyAdvancedV5(state)}`);
  }
  if (pageRoute === "image" && activeTab(state, "image") === "studio") {
    return localizeLegacyMarkup(`${imageModuleTabs(state)}${renderImageMaskStudio(state)}`);
  }
  if (pageRoute === "image" && activeTab(state, "image") === "nodes") {
    return localizeLegacyMarkup(`${heading("ADVANCED WORKFLOW", "Image AI Hub Nodes", "Kéo socket trực tiếp, typed sockets, minimap, multi-select và live preview.")}${imageModuleTabs(state)}${nodeStudio("image", nodeCopy.image)}`);
  }
  if (pageRoute === "image") {
    return localizeLegacyMarkup(`${imageModuleTabs(state)}${renderImageQuickV5(state)}`);
  }
  if (nodeScope && activeTab(state, nodeScope) === "nodes") {
    return localizeLegacyMarkup(`${heading("ADVANCED WORKFLOW", `${nodeScope === "sam2" ? "SAM2" : nodeScope === "animesr" ? "AnimeSR" : nodeScope === "media" ? "Media" : "Image AI"} Nodes`, "Node editor chạy offline trong cửa sổ Local AI Hub.")}${moduleTabs(state, nodeScope)}${nodeStudio(nodeScope, nodeCopy[nodeScope])}`);
  }
  const page = (pages[pageRoute] || renderDashboard)(state);
  return localizeLegacyMarkup(nodeScope ? `${moduleTabs(state, nodeScope)}${page}` : page);
}
