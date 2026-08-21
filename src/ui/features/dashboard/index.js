export { DASHBOARD_SECTIONS, dashboardSectionOrder } from "./dashboard.js";
export const dashboardFeature = Object.freeze({ id: "dashboard", refreshPolicy: "manual", owns: ["core-status", "gpu-summary", "storage-summary", "catalog-summary", "jobs-summary"] });
