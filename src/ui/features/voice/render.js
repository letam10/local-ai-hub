export function createVoiceRenderer(deps) {
  const { component, tool, heading, capability, card, field, file, button, formResult } = deps;
  return function renderVoice(state) {
      const tts = component(state, "qwen3_tts");
      const seed = component(state, "seed_vc");
      return heading("VOICE", "Voice Studio", "Qwen3-TTS và Seed-VC chạy bằng worker nền trong Hub, không mở secondary Voice GUI.") + `
        <div class="capability-grid">${capability("Qwen3-TTS", tts, "Text to Speech, Voice Design, Voice Clone, Batch.", tool(state, "text_to_speech"))}${capability("Seed-VC", seed, "Voice Conversion với source/target audio.", tool(state, "convert_voice"))}</div>
        <div class="workspace-grid workspace-grid--three" style="margin-top:16px">
          ${card("Text to Speech", `<form data-job-form data-tool="text_to_speech" class="stack">${field("Text", `<textarea name="text" required placeholder="Nhập nội dung cần đọc…"></textarea>`)}<div class="form-grid">${field("Language", `<input name="language" value="Vietnamese" />`)}${field("Speaker", `<input name="speaker" value="Ryan" />`)}</div><div class="form-actions">${button("Tạo giọng nói", "button--primary")}</div>${formResult("tts-result")}</form>`)}
          ${card("Voice Design / Clone", `<form data-job-form data-tool="design_voice" data-tool-by-field="operation" data-tool-map='{"design":"design_voice","clone":"clone_voice"}' class="stack">${field("Operation", `<select name="operation"><option value="design">Voice Design</option><option value="clone">Voice Clone</option></select>`)}${field("Text", `<textarea name="text" required placeholder="Nội dung đầu ra…"></textarea>`)}${file("Reference audio (chỉ Voice Clone)", "reference_asset_id", "audio/*")}${field("Reference text", `<input name="reference_text" placeholder="Tùy chọn" />`)}<div class="form-actions">${button("Chạy Qwen3-TTS", "button--primary")}</div>${formResult("voice-design-result")}</form>`)}
          ${card("Voice Conversion", `<form data-job-form data-tool="convert_voice" class="stack">${file("Source audio", "source_asset_id", "audio/*")}${file("Target voice", "target_asset_id", "audio/*")}${field("Diffusion steps", `<input name="diffusion_steps" type="number" min="1" max="50" value="4" />`)}<div class="form-actions">${button("Chuyển giọng", "button--primary")}</div>${formResult("seed-result")}</form>`)}
        </div>`;
  };
}
