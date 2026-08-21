export function createAnimesrRenderer(deps) {
  const { component, tool, heading, statusPill, workspaceState, card, file, field, button, formResult } = deps;
  return function renderAnime(state) {
      const item = component(state, "animesr");
      return heading("VIDEO AI", "AnimeSR", "Workflow upscale chính chạy bằng worker AnimeSR trong Hub. Anime Upscale Studio chỉ là legacy/debug fallback, không còn là action chính.", statusPill(tool(state, "upscale_anime_video").tool_status || item.component_status || "missing")) + workspaceState("AnimeSR / Interpolate", tool(state, "upscale_anime_video"), "Chuẩn bị clip ngắn; video smoke hiện deferred do resource contention.") + `
        <div class="workspace-grid workspace-grid--two">
          ${card("Upscale queue", `<form data-job-form data-tool="upscale_anime_video" class="stack">${file("Video input", "asset_id", "video/*")}${field("Model", `<select name="model"><option value="AnimeSR_v2">AnimeSR v2</option><option value="AnimeSR_v1-PaperModel">AnimeSR v1 Paper</option></select>`)}<div class="form-grid">${field("Scale", `<select name="scale"><option value="2">2×</option><option value="3">3×</option><option value="4">4×</option></select>`)}${field("Chunk seconds", `<input name="chunk_seconds" type="number" min="10" value="120" />`)}</div><div class="check-grid"><label><input name="half" type="checkbox" checked /> Half precision</label><label><input name="use_rife" type="checkbox" /> RIFE (partial)</label><label><input name="use_realesrgan" type="checkbox" /> Real-ESRGAN (partial)</label></div><div class="form-actions">${button("Thêm AnimeSR job", "button--primary")}</div>${formResult("anime-result")}</form>`)}
          ${card("Progress & output", `<div class="preview-empty"><span>⇱</span><strong>Queue, progress, cancel và resume nằm ở Jobs</strong><p>Hub không mở Anime Upscale Studio để chạy normal workflow.</p></div><details class="advanced"><summary>Advanced / legacy</summary><p>Legacy Studio chỉ nên dùng debug khi direct worker báo limitation đã được ghi nhận.</p></details>`, "", "card--flat")}
        </div>`;
  };
}
