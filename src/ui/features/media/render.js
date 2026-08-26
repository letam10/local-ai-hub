export function createMediaRenderer(deps) {
  const { component, tool, mediaCapabilityEvidence, heading, statusPill, mediaEvidencePanel, workspaceState, videoWorkflowRail, card, file, files, field, formResult, escapeHtml, uiTextHtml } = deps;
  const renderMediaPage = function renderMedia(state) {
      const item = component(state, "ffmpeg");
      const genericTool = tool(state, "run_media_operation");
      const mediaEvidence = mediaCapabilityEvidence(state);
      const genericContract = {
        ...genericTool,
        tool_status: "partial",
        status: "partial",
        reason: "Generic media actions are not covered by the three exact server evidence rows.",
        action: "Use the exact evidence summary first; this UI does not claim generic media execution.",
      };
      return heading("MEDIA", "Media workspace", "Media operation state is shown from the server snapshot before any separately authorized work.", statusPill("partial", "Partial")) + mediaEvidencePanel(state, "media") + workspaceState("Generic Media action", genericContract, "Review the exact operation scope; no generic action is enabled from this snapshot.") + videoWorkflowRail(state) + `
        <div class="workspace-grid workspace-grid--two">
          ${card("Generic video and image actions", `<form data-job-form data-tool="run_media_operation" data-media-generic-action="explanatory" data-media-generic-status="partial" class="stack" aria-describedby="media-generic-guidance">${file("Primary media input", "asset_id", "audio/*,video/*,image/*")}${files("Additional inputs", "input_asset_ids", "video/*,image/*")}${file("Secondary audio or subtitle", "secondary_asset_id", "audio/*,.srt,.ass")}${field("Operation", `<select name="operation"><option value="probe">Read metadata</option><option value="trim">Trim</option><option value="concat">Concat</option><option value="resize">Resize</option><option value="crop">Crop</option><option value="rotate">Rotate</option><option value="fps">FPS</option><option value="transcode">Transcode</option><option value="extract_audio">Extract audio</option><option value="replace_audio">Replace audio</option><option value="mux">Mux audio/video</option><option value="burn_subtitle">Burn subtitle</option><option value="extract_frames">Extract frames</option><option value="image_sequence_video">Image sequence to video</option><option value="image_resize">Image resize</option><option value="image_crop">Image crop</option><option value="image_rotate">Image rotate</option><option value="image_flip">Image flip</option><option value="image_convert">Image convert</option><option value="image_compress">Image compress</option></select>`)}<div class="form-grid">${field("Start", `<input name="start" type="number" min="0" step="0.1" value="0" />`)}${field("End", `<input name="end" type="number" min="0.1" step="0.1" value="5" />`)}${field("Width", `<input name="width" type="number" min="2" value="1280" />`)}${field("Height", `<input name="height" type="number" min="-2" value="-2" />`)}${field("FPS", `<input name="fps" type="number" min="1" value="30" />`)}${field("Rotation", `<select name="degrees"><option value="90">90</option><option value="180">180</option><option value="270">270</option></select>`)}${field("Flip", `<select name="axis"><option value="horizontal">Horizontal</option><option value="vertical">Vertical</option></select>`)}${field("Image format", `<select name="format"><option value="png">PNG</option><option value="jpg">JPG</option><option value="webp">WEBP</option></select>`)}</div><div id="media-generic-guidance" class="callout callout--warning" role="status"><strong>Generic media remains Partial.</strong><span>${escapeHtml(mediaEvidence.genericReason)} ${escapeHtml(mediaEvidence.genericNextAction)}</span></div><div class="form-actions"><button class="button" type="submit" disabled data-media-generic-action="explanatory">Execution unavailable from this snapshot</button></div>${formResult("media-result")}</form>`) }
          ${card("Media safety boundary", `<ul class="notice-list"><li>Only exact server-owned evidence can be operational.</li><li>Generic actions remain explanatory and cannot submit a runtime request here.</li><li>Artifact previews use opaque Hub URLs and native metadata/range transport.</li></ul>`, "", "card--flat")}
        </div>`;
  };
  // Video Creative is intentionally a separate route from the generic Media
  // contract.  It presents the video pipeline and Node Studio entry point
  // without pretending that generic media operations are executable.
  renderMediaPage.video = function renderVideo(state) {
    const genericTool = tool(state, "run_media_operation");
    const videoContract = {
      ...genericTool,
      tool_status: "partial",
      status: "partial",
      reason: "Video workflow actions remain partial until the exact server-owned evidence is available.",
      action: "Open Video Node Studio to edit a typed graph; no worker starts from this page.",
    };
    return heading("VIDEO", "Sáng tạo video", "Không gian riêng cho pipeline video: nhập, biến đổi, nâng cấp, mã hóa và xuất artifact.", statusPill("partial", "Partial"))
      + mediaEvidencePanel(state, "video")
      + videoWorkflowRail(state, "video")
      + workspaceState("Thao tác video", videoContract, "Mở Node Studio video để chỉnh graph; chỉ chạy khi backend và bằng chứng cho phép.")
      + `<div class="workspace-grid workspace-grid--two"><section class="card video-route-card"><div class="card-title-row"><div><span class="eyebrow">VIDEO NODES</span><h2>Pipeline video typed</h2><p>Load Video → Transform → AnimeSR/RIFE → Grade → Encode → Preview → Save.</p></div></div><button class="button button--primary" type="button" data-workspace-tab="video:nodes">Mở Node Studio video</button></section>${card("Ranh giới an toàn", `<ul class="notice-list"><li>Chỉ artifact opaque do Hub quản lý mới được dùng làm đầu vào.</li><li>Node partial/unavailable không được xem là đã chạy.</li><li>Trang này không tự khởi động FFmpeg, GPU hay provider.</li></ul>`, "", "card--flat")}</div>`;
  };
  return renderMediaPage;
}
