/* Translation ownership is structured by feature.  The legacy dictionary is
 * still the compatibility fallback, but new copy must be added to a namespace
 * rather than growing a single unowned map. */
export const I18N_NAMESPACES = Object.freeze({
  core: ["Dashboard", "Settings", "Jobs", "Language", "Change theme"],
  dashboard: ["GPU", "Storage", "Models", "Active jobs"],
  components: ["Components / AI Setup", "Download & Install", "Import Model"],
  models: ["Models & Storage", "Installed", "Not installed", "Size unavailable"],
  runtimes: ["Runtimes", "Environment", "Runtime state"],
  projects: ["Projects & Recipes", "Asset Library", "Compare"],
  node_studio: ["Node Studio", "Workflow", "Inspector"],
  image_mask: ["Image & Mask Studio", "Layer", "Mask"],
  sam2: ["SAM2", "Segmentation", "Tracking"],
  media: ["Media", "Probe", "Transform"],
  whisper: ["Whisper", "Transcript", "SRT"],
  vision: ["Vision", "Detection", "Recognition"],
  voice: ["Voice", "Text to speech", "Voice conversion"],
  ocr: ["OCR", "Text", "Document"],
});

export const namespaceKeys = () => Object.keys(I18N_NAMESPACES);
