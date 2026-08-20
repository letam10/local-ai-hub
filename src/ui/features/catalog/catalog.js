/* Shared catalog presentation primitives.  Dynamic server values are escaped
 * by the caller; only fixed labels are eligible for i18n. */
export const INSTALL_DISPOSITIONS = Object.freeze({
  AUTO_INSTALL_READY: "Download & Install",
  AUTH_REQUIRED: "Authorize & Install",
  LICENSE_REQUIRED: "Review License",
  MANUAL_IMPORT_ONLY: "Import Model",
  UNSUPPORTED_SOURCE: "Manual Review",
});

export const modelStateLabel = (item) => ({
  INSTALLED: "Installed",
  NOT_INSTALLED: "Not installed",
  PARTIAL: "Partial",
  UNAVAILABLE: "Unavailable",
  OPERATIONAL: "Operational",
}[item?.status] || "Unknown");

export const catalogFeature = Object.freeze({ id: "catalog", namespaces: ["models", "runtimes", "components"], refresh: "manual" });
