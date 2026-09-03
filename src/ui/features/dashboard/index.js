export { DASHBOARD_SECTIONS, dashboardSectionOrder } from "./dashboard.js";
export const dashboardFeature = Object.freeze({ id: "dashboard", refreshPolicy: "manual", owns: ["summary", "attention", "actions"] });
