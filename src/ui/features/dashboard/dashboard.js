/* Dashboard feature projection definitions. Rendered values remain server
 * data and are escaped by the compatibility facade. */
export const DASHBOARD_SECTIONS = Object.freeze(["core-status", "gpu-summary", "storage-summary", "installed-modules", "missing-modules", "models", "active-jobs", "recent-projects", "recent-artifacts"]);
export const dashboardSectionOrder = () => [...DASHBOARD_SECTIONS];
