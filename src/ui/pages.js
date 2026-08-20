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
  return renderProductionModels({ productionCatalog: state.productionCatalog, legacyModels: state.models, storage: state.storage, filters: state.modelFilters, escapeHtml, formatGb, statusPill, card, heading });
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
