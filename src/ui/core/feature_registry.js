/* V7 feature ownership registry.  Feature modules own labels/render contracts;
 * pages.js remains a compatibility composition facade during migration. */
export const FEATURE_REGISTRY = Object.freeze({
  dashboard: { namespace: "dashboard", owner: "src/ui/features/dashboard/", route: "dashboard" },
  projects: { namespace: "projects", owner: "src/ui/features/projects/", route: "projects" },
  workflow_library: { namespace: "workflow_library", owner: "src/ui/features/workflow_library/", route: "workflows" },
  jobs: { namespace: "jobs", owner: "src/ui/features/jobs/", route: "jobs" },
  artifacts: { namespace: "artifacts", owner: "src/ui/features/artifacts/", route: "artifacts" },
  settings: { namespace: "settings", owner: "src/ui/features/settings/", route: "settings" },
  diagnostics: { namespace: "diagnostics", owner: "src/ui/features/diagnostics/", route: "diagnostics" },
  components: { namespace: "components", owner: "src/ui/features/components/", route: "components" },
  models: { namespace: "models", owner: "src/ui/features/models/", route: "models" },
  runtimes: { namespace: "runtimes", owner: "src/ui/features/runtimes/", route: "runtimes" },
  node_studio: { namespace: "node_studio", owner: "src/ui/features/node_studio/", route: "node-studio" },
  image_ai: { namespace: "image_ai", owner: "src/ui/features/image_ai/", route: "image" },
  image_mask: { namespace: "image_mask", owner: "src/ui/features/image_mask/", route: "image-mask" },
  sam2: { namespace: "sam2", owner: "src/ui/features/sam2/", route: "sam2" },
  animesr: { namespace: "animesr", owner: "src/ui/features/animesr/", route: "animesr" },
  media: { namespace: "media", owner: "src/ui/features/media/", route: "media" },
  whisper: { namespace: "whisper", owner: "src/ui/features/whisper/", route: "whisper" },
  vision: { namespace: "vision", owner: "src/ui/features/vision/", route: "vision" },
  voice: { namespace: "voice", owner: "src/ui/features/voice/", route: "voice" },
  ocr: { namespace: "ocr", owner: "src/ui/features/ocr/", route: "ocr" },
});

export const FACADE_POLICY = "compatibility_composition_only";
export const featureOwner = (route) => Object.values(FEATURE_REGISTRY).find((item) => item.route === route) || null;
export const featureNamespaces = () => Object.keys(FEATURE_REGISTRY);
