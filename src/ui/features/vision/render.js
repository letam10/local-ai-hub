export function createVisionRenderer(deps) {
  const { component, tool, heading, visionWorkflowRail, capability, card, file, field, button, formResult } = deps;
  return function renderVision(state) {
      const omni = component(state, "omniparser");
      const rf = component(state, "rfdetr");
      const ground = component(state, "groundingdino");
      return heading("VISION", "Vision Studio", "Tải ảnh/screenshot vào Hub, chạy parser hoặc detector, xem JSON và artifact ngay trong cửa sổ này.") + visionWorkflowRail(state) + `
        <div class="capability-grid">${capability("OmniParser", omni, "Parse UI, vùng tương tác và ảnh annotation.", tool(state, "parse_screen"))}${capability("RF-DETR", rf, "Phát hiện object theo threshold.", tool(state, "detect_objects"))}${capability("Grounding DINO", ground, "Prompt → boxes, có thể dùng lại trong SAM2.", tool(state, "ground_objects"))}</div>
        <div class="workspace-grid workspace-grid--three" style="margin-top:16px">
          ${card("OmniParser", `<form data-job-form data-tool="parse_screen" class="stack">${file("Ảnh hoặc screenshot", "asset_id", "image/*")}${field("Box threshold", `<input name="box_threshold" type="number" min="0" max="1" step="0.01" value="0.05" />`)}<div class="form-actions">${button("Phân tích UI", "button--primary")}</div>${formResult("vision-omni-result")}</form>`)}
          ${card("RF-DETR", `<form data-job-form data-tool="detect_objects" class="stack">${file("Ảnh/video", "asset_id", "image/*,video/*")}${field("Detection threshold", `<input name="threshold" type="number" min="0" max="1" step="0.01" value="0.50" />`)}<div class="form-actions">${button("Phát hiện object", "button--primary")}</div>${formResult("vision-rf-result")}</form>`)}
          ${card("Grounding DINO", `<form data-job-form data-tool="ground_objects" class="stack">${file("Ảnh", "asset_id", "image/*")}${field("Prompt", `<input name="prompt" required placeholder="person . bag ." />`)}<div class="form-grid">${field("Box", `<input name="box_threshold" type="number" step="0.01" value="0.35" />`)}${field("Text", `<input name="text_threshold" type="number" step="0.01" value="0.25" />`)}</div><div class="form-actions">${button("Tạo boxes", "button--primary")}</div>${formResult("vision-ground-result")}</form>`)}
        </div>`;
  };
}
