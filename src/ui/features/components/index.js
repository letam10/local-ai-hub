export const componentActionLabel = (disposition) => ({
  AUTO_INSTALL_READY: "Download & Install",
  AUTH_REQUIRED: "Authorize & Install",
  LICENSE_REQUIRED: "Review License",
  MANUAL_IMPORT_ONLY: "Import Model",
  UNSUPPORTED_SOURCE: "Manual Review",
}[String(disposition || "MANUAL_IMPORT_ONLY")] || "Manual Review");

export const componentsFeature = Object.freeze({
  id: "components",
  owns: [
    "component-catalog",
    "install-plan",
    "maintenance-plan",
    "component-operation-journal",
    "component-source-acceptance",
  ],
  actionLabel: "componentActionLabel",
});
