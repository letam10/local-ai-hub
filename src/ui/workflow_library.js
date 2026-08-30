/*
 * Typed UI adapter for the server-owned Workflow Library.
 *
 * There is deliberately no guessed fetch route here.  The explicit Hub API
 * contract is preferred when it is present; a desktop bridge may provide the
 * same named methods.  Local drafts remain independent until the user chooses
 * a server-owned Library action.
 */

export const WORKFLOW_LIBRARY_ADAPTER_VERSION = "workflow-library-adapter.v1";

const PARTIAL = {
  status: "partial",
  reason: "Workflow Library server-owned adapter chưa khả dụng.",
  action: "Tiếp tục chỉnh sửa local draft; kiểm tra endpoint typed trước khi đồng bộ.",
};

const detached = (value) => {
  try { return value === undefined ? undefined : JSON.parse(JSON.stringify(value)); } catch { return null; }
};

const resultOrPartial = (value) => {
  if (!value || typeof value !== "object") return { ...PARTIAL };
  return detached(value) || { ...PARTIAL };
};

export function createWorkflowLibraryAdapter(bridge = globalThis.pywebview?.api?.workflow_library, http = null) {
  const invoke = async (method, payload) => {
    const target = bridge && typeof bridge[method] === "function" ? bridge : http;
    if (!target || typeof target[method] !== "function") return { ...PARTIAL };
    try {
      return resultOrPartial(await target[method](detached(payload)));
    } catch {
      return {
        status: "partial",
        reason: "Workflow Library bridge không phản hồi; local draft vẫn được giữ.",
        action: "Kiểm tra bridge server-owned rồi thử lại bằng thao tác user-mediated.",
      };
    }
  };
  return {
    version: WORKFLOW_LIBRARY_ADAPTER_VERSION,
    list: () => invoke("list"),
    get: (id) => invoke("get", { id }),
    save: (entry, expectedRevision) => invoke("save", { entry, expected_revision: expectedRevision }),
    remove: (id, expectedRevision) => invoke("remove", { id, expected_revision: expectedRevision }),
    setFavorite: (id, favorite, expectedRevision) => invoke("set_favorite", { id, favorite: Boolean(favorite), expected_revision: expectedRevision }),
    markOpened: (id, expectedRevision) => invoke("mark_opened", { id, expected_revision: expectedRevision }),
    planMigration: (entries) => invoke("plan_migration", { entries }),
    confirmMigration: (entries, expectedRevision) => invoke("confirm_migration", { entries, expected_revision: expectedRevision }),
  };
}

export const workflowLibraryFallback = () => ({ ...PARTIAL });

export function workflowLibraryEntry(graph, scope = "image") {
  const value = detached(graph) || {};
  const fallbackId = "local-" + scope;
  const id = String(value.id || fallbackId).toLowerCase().replace(/[^a-z0-9._-]+/g, "-").slice(0, 96) || fallbackId;
  return {
    schema_version: "workflow-entry.v1",
    id,
    title: String(value.title || ("Workflow " + scope)).slice(0, 160),
    description: "Declarative Hub Nodes draft; execution requires a separately authorized bounded smoke.",
    scope: String(value.scope || scope).slice(0, 80),
    graph: value,
    revision: 1,
    status: "draft",
    source: "local",
    tags: ["v5", "hub-nodes"],
    created_at: "",
    updated_at: "",
  };
}
