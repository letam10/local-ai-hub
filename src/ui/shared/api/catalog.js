/* Server-owned catalog client.  It never accepts a path, URL, command or
 * model payload from the browser; filters are bounded query values only. */
export async function getProductionCatalog({ query = "", category = "", installed = null } = {}) {
  const params = new URLSearchParams();
  if (query) params.set("q", String(query).slice(0, 80));
  if (category) params.set("category", String(category).slice(0, 48));
  if (installed !== null) params.set("installed", installed ? "true" : "false");
  const response = await fetch(`/api/productization/catalog?${params.toString()}`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error("catalog_unavailable");
  return response.json();
}

export async function planComponentInstall(componentId) {
  const response = await fetch("/api/productization/plans", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ component_id: String(componentId) }) });
  return response.json();
}

export async function confirmComponentInstall(planId, confirmed) {
  const response = await fetch(`/api/productization/plans/${encodeURIComponent(planId)}/confirm`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ confirmed: Boolean(confirmed) }) });
  return response.json();
}
