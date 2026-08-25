/* Dashboard-only installed product updater.
 *
 * This module is intentionally independent from component/model updates. It
 * talks only to the loopback app-update routes and the bounded pywebview
 * restart bridge. Dynamic GitHub values are written with textContent.
 */

const API = Object.freeze({
  status: "/api/app-update/status",
  changes: "/api/app-update/changes",
  prepare: "/api/app-update/prepare",
  rollback: "/api/app-update/rollback",
});

let lastStatus = null;
let checking = false;

const css = `
.app-update-card{margin:0 0 18px;padding:18px 20px;border:1px solid var(--border-color,#334155);border-radius:16px;background:linear-gradient(135deg,rgba(77,125,255,.12),rgba(128,170,255,.04));display:grid;gap:14px}
.app-update-card__head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
.app-update-card__title{display:grid;gap:4px}.app-update-card__title h2{margin:0;font-size:1.05rem}.app-update-card__title p{margin:0;opacity:.78;font-size:.9rem}
.app-update-card__badge{display:inline-flex;align-items:center;border-radius:999px;padding:5px 10px;font-size:.78rem;font-weight:700;background:rgba(148,163,184,.16)}
.app-update-card[data-state="available"] .app-update-card__badge{background:rgba(34,197,94,.16);color:#86efac}
.app-update-card[data-state="auth_required"] .app-update-card__badge,.app-update-card[data-state="unavailable"] .app-update-card__badge{background:rgba(245,158,11,.16);color:#fcd34d}
.app-update-card__builds{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.app-update-build{padding:10px 12px;border-radius:12px;background:rgba(15,23,42,.22);display:grid;gap:3px}.app-update-build span{font-size:.76rem;opacity:.72}.app-update-build code{font-size:.86rem;overflow-wrap:anywhere}
.app-update-card__actions{display:flex;gap:8px;flex-wrap:wrap}.app-update-card__actions button{min-height:38px}
.app-update-card__message{margin:0;font-size:.88rem}.app-update-card__changes{margin:0;padding-left:20px;display:grid;gap:6px;font-size:.86rem}.app-update-card__changes code{margin-right:6px}
@media (max-width:720px){.app-update-card__builds{grid-template-columns:1fr}}
`;

const injectStyle = () => {
  if (document.querySelector("#local-ai-hub-app-update-style")) return;
  const style = document.createElement("style");
  style.id = "local-ai-hub-app-update-style";
  style.textContent = css;
  document.head.append(style);
};

const api = async (path, options = {}) => {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); } catch { payload = { status: "error", error: "Phản hồi updater không phải JSON." }; }
  if (!response.ok) {
    const error = new Error(payload.error || payload.code || `HTTP ${response.status}`);
    error.payload = payload;
    throw error;
  }
  return payload;
};

const shortBuild = (value) => {
  const text = String(value || "—");
  return /^[0-9a-f]{40}$/.test(text) ? text.slice(0, 12) : text;
};

const statusLabel = (value) => ({
  available: "Có bản cập nhật",
  up_to_date: "Đã mới nhất",
  checking: "Đang kiểm tra",
  downloading: "Đang tải",
  verifying: "Đang xác minh",
  staging: "Đang stage",
  ready_to_restart: "Sẵn sàng khởi động lại",
  restarting: "Đang khởi động lại",
  rollback: "Đang rollback",
  auth_required: "Cần đăng nhập GitHub",
  oauth_configuration_required: "Cần cấu hình OAuth",
  blocked_active_jobs: "Đang có job hoạt động",
  incompatible_runtime: "Runtime không tương thích",
  failed: "Cập nhật thất bại",
  no_artifact: "Chưa có gói cập nhật",
  unavailable: "Updater chưa sẵn sàng",
  activated: "Đã tải xong",
}[value] || "Trạng thái cập nhật");

const statusMessage = (value) => {
  if (value?.status === "available") return "Main CI đã xanh và có payload mới đã được đóng gói. Bạn có thể xem thay đổi trước khi cập nhật.";
  if (value?.status === "up_to_date") return "Local AI Hub đang chạy đúng build main mới nhất đã có artifact.";
  if (value?.status === "auth_required") return "Repo là private. Hãy đăng nhập GitHub CLI một lần trên máy này; Hub không lưu hoặc hiển thị token.";
  if (value?.status === "oauth_configuration_required") return "Native GitHub Device Flow cần OAuth App client_id do chủ repo cung cấp; vẫn có thể dùng GitHub CLI đã đăng nhập.";
  if (value?.status === "blocked_active_jobs") return "Không thể cập nhật khi Hub còn job hoạt động. Dữ liệu và payload hiện tại vẫn được giữ nguyên.";
  if (value?.status === "incompatible_runtime") return "Payload yêu cầu runtime/launcher khác; updater đã fail-closed và chưa đổi current pointer.";
  if (value?.status === "failed") return "Cập nhật không hoàn tất; payload hiện tại vẫn được giữ nguyên hoặc đã rollback.";
  if (value?.status === "no_artifact") return "Main chưa có update artifact thành công. Hub sẽ không cập nhật từ một build chưa qua CI.";
  if (value?.code === "GITHUB_CLI_REQUIRED") return "Cần cài GitHub CLI (gh) để Hub đọc artifact của repo private mà không nhúng token vào ứng dụng.";
  return String(value?.action || "Không thể xác minh bản cập nhật lúc này; phiên bản đang chạy vẫn được giữ nguyên.");
};

const make = (tag, className = "") => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
};

const ensureCard = () => {
  const dashboard = document.querySelector("#module-view .dashboard-page");
  if (!dashboard) return null;
  let card = dashboard.querySelector("[data-app-update-card]");
  if (card) return card;
  card = make("section", "app-update-card");
  card.dataset.appUpdateCard = "true";
  card.dataset.state = "checking";
  card.setAttribute("aria-labelledby", "app-update-title");

  const head = make("div", "app-update-card__head");
  const title = make("div", "app-update-card__title");
  const eyebrow = make("span", "eyebrow"); eyebrow.textContent = "LOCAL AI HUB UPDATE";
  const h2 = make("h2"); h2.id = "app-update-title"; h2.textContent = "Cập nhật Local AI Hub";
  const subtitle = make("p"); subtitle.textContent = "Đồng bộ bản stable từ main đã qua GitHub Actions — không git pull vào ứng dụng.";
  title.append(eyebrow, h2, subtitle);
  const badge = make("span", "app-update-card__badge"); badge.dataset.updateBadge = "true"; badge.textContent = "Đang kiểm tra";
  head.append(title, badge);

  const builds = make("div", "app-update-card__builds");
  const current = make("div", "app-update-build");
  const currentLabel = make("span"); currentLabel.textContent = "Build đang chạy";
  const currentValue = make("code"); currentValue.dataset.updateCurrent = "true"; currentValue.textContent = "—";
  current.append(currentLabel, currentValue);
  const latest = make("div", "app-update-build");
  const latestLabel = make("span"); latestLabel.textContent = "Main mới nhất đã đóng gói";
  const latestValue = make("code"); latestValue.dataset.updateLatest = "true"; latestValue.textContent = "—";
  latest.append(latestLabel, latestValue);
  builds.append(current, latest);

  const message = make("p", "app-update-card__message");
  message.dataset.updateMessage = "true";
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "polite");
  message.textContent = "Đang kiểm tra main CI…";

  const actions = make("div", "app-update-card__actions");
  const refresh = make("button", "button button--compact"); refresh.type = "button"; refresh.dataset.appUpdateCheck = "true"; refresh.textContent = "Kiểm tra lại";
  const changes = make("button", "button button--compact"); changes.type = "button"; changes.dataset.appUpdateChanges = "true"; changes.textContent = "Xem thay đổi"; changes.disabled = true;
  const update = make("button", "button button--compact"); update.type = "button"; update.dataset.appUpdateApply = "true"; update.textContent = "Cập nhật Local AI Hub"; update.disabled = true;
  const restart = make("button", "button button--compact"); restart.type = "button"; restart.dataset.appUpdateRestart = "true"; restart.textContent = "Khởi động lại để áp dụng"; restart.hidden = true;
  const rollback = make("button", "button button--compact"); rollback.type = "button"; rollback.dataset.appUpdateRollback = "true"; rollback.textContent = "Quay lại payload trước"; rollback.hidden = true;
  const auth = make("button", "button button--compact"); auth.type = "button"; auth.dataset.appUpdateAuth = "true"; auth.textContent = "Đăng nhập GitHub"; auth.hidden = true;
  const authCancel = make("button", "button button--compact"); authCancel.type = "button"; authCancel.dataset.appUpdateAuthCancel = "true"; authCancel.textContent = "Hủy đăng nhập"; authCancel.hidden = true;
  actions.append(refresh, changes, update, restart, rollback, auth, authCancel);
  const authDetail = make("p", "app-update-card__message"); authDetail.dataset.updateAuth = "true"; authDetail.hidden = true; card.append(authDetail);

  const changeList = make("ol", "app-update-card__changes"); changeList.dataset.updateChangesList = "true"; changeList.hidden = true;
  card.append(head, builds, message, actions, changeList);
  const hero = dashboard.querySelector(".dashboard-hero");
  if (hero?.nextSibling) dashboard.insertBefore(card, hero.nextSibling);
  else dashboard.prepend(card);
  return card;
};

const render = (card, value) => {
  if (!card) return;
  lastStatus = value;
  const state = String(value?.status || "unavailable");
  card.dataset.state = state;
  card.querySelector("[data-update-badge]").textContent = statusLabel(state);
  card.querySelector("[data-update-current]").textContent = shortBuild(value?.current_build || value?.current_payload);
  card.querySelector("[data-update-latest]").textContent = shortBuild(value?.latest_build);
  card.querySelector("[data-update-message]").textContent = statusMessage(value);
  card.querySelector("[data-app-update-changes]").disabled = !value?.latest_build;
  card.querySelector("[data-app-update-apply]").disabled = value?.available !== true;
  const authButton = card.querySelector("[data-app-update-auth]");
  const needsAuth = value?.status === "auth_required" || value?.status === "oauth_configuration_required";
  authButton.hidden = !needsAuth;
  authButton.disabled = value?.status === "oauth_configuration_required";
  authButton.textContent = value?.status === "oauth_configuration_required" ? "OAuth cần cấu hình" : "Đăng nhập GitHub";
  card.querySelector("[data-app-update-auth-cancel]").hidden = !card.dataset.authSession;
};

const check = async (refresh = false) => {
  if (checking) return;
  const card = ensureCard();
  if (!card) return;
  checking = true;
  render(card, { ...(lastStatus || {}), status: "checking", action: "Đang hỏi GitHub về main build mới nhất…" });
  try {
    const value = await api(`${API.status}${refresh ? "?refresh=1" : ""}`);
    render(card, value);
  } catch (error) {
    render(card, { status: "unavailable", code: error.payload?.code, action: error.message });
  } finally { checking = false; }
};

const showChanges = async (card) => {
  const list = card.querySelector("[data-update-changes-list]");
  list.hidden = false;
  list.replaceChildren();
  const loading = make("li"); loading.textContent = "Đang tải danh sách thay đổi…"; list.append(loading);
  try {
    const value = await api(API.changes);
    list.replaceChildren();
    const commits = Array.isArray(value.commits) ? value.commits : [];
    if (!commits.length) {
      const row = make("li"); row.textContent = value.reason || "Chưa có commit range chi tiết cho payload legacy hiện tại."; list.append(row); return;
    }
    for (const commit of commits) {
      const row = make("li");
      const code = make("code"); code.textContent = String(commit.sha || "");
      const text = document.createTextNode(String(commit.message || ""));
      row.append(code, text); list.append(row);
    }
  } catch (error) {
    list.replaceChildren(); const row = make("li"); row.textContent = `Không thể tải thay đổi: ${error.message}`; list.append(row);
  }
};

const applyUpdate = async (card) => {
  if (!lastStatus?.available) return;
  const latest = shortBuild(lastStatus.latest_build);
  if (!window.confirm(`Cập nhật Local AI Hub lên main@${latest}? Payload hiện tại được giữ lại để rollback.`)) return;
  const button = card.querySelector("[data-app-update-apply]");
  button.disabled = true;
  render(card, { ...(lastStatus || {}), status: "downloading", available: true });
  card.querySelector("[data-update-message]").textContent = "Đang tải, xác minh SHA, stage payload và kiểm tra imports…";
  try {
    const value = await api(API.prepare, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true }) });
    lastStatus = { ...(lastStatus || {}), status: value.status, available: false, current_payload: value.payload_id, current_build: value.source_commit };
    card.dataset.state = "ready_to_restart";
    card.querySelector("[data-update-badge]").textContent = "Sẵn sàng khởi động lại";
    card.querySelector("[data-update-current]").textContent = shortBuild(value.source_commit);
    card.querySelector("[data-update-message]").textContent = "Payload mới đã được xác minh và kích hoạt atomically. Launcher, shortcut và DATA_ROOT không thay đổi.";
    card.querySelector("[data-app-update-restart]").hidden = value.restart_required !== true;
    card.querySelector("[data-app-update-rollback]").hidden = false;
  } catch (error) {
    card.querySelector("[data-update-message]").textContent = `Cập nhật bị chặn an toàn: ${error.payload?.code || error.message}. Bản đang chạy không bị thay đổi.`;
    button.disabled = false;
  }
};

const restart = async (card) => {
  const button = card.querySelector("[data-app-update-restart]");
  button.disabled = true;
  render(card, { ...(lastStatus || {}), status: "restarting", available: false });
  card.querySelector("[data-update-message]").textContent = "Đang khởi động lại Local AI Hub bằng stable launcher…";
  try {
    const bridge = window.pywebview?.api;
    if (!bridge?.restart_after_update) throw new Error("Native restart bridge chưa khả dụng trong payload này.");
    const value = await bridge.restart_after_update();
    if (value?.status !== "completed") throw new Error(value?.code || "Restart bị chặn.");
  } catch (error) {
    card.querySelector("[data-update-message]").textContent = `${error.message} Bạn có thể đóng Local AI Hub và mở lại shortcut Desktop; payload mới đã được stage an toàn.`;
    button.disabled = false;
  }
};

const rollback = async (card) => {
  if (!window.confirm("Quay current.json về payload trước? File dữ liệu người dùng không bị xóa.")) return;
  const button = card.querySelector("[data-app-update-rollback]");
  button.disabled = true;
  render(card, { ...(lastStatus || {}), status: "rollback", available: false });
  try {
    const value = await api(API.rollback, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true }) });
    card.querySelector("[data-update-message]").textContent = `Đã quay pointer về ${value.payload_id}. Khởi động lại để áp dụng.`;
    card.querySelector("[data-app-update-restart]").hidden = false;
  } catch (error) {
    card.querySelector("[data-update-message]").textContent = `Không thể rollback: ${error.payload?.code || error.message}`;
  } finally { button.disabled = false; }
};

const startAuth = async (card) => {
  const detail = card.querySelector("[data-update-auth]");
  detail.hidden = false;
  detail.textContent = "Đang chuẩn bị GitHub Device Flow…";
  try {
    const value = await api("/api/app-update/auth/device/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (value.status === "device_login_required") {
      card.dataset.authSession = String(value.session_id || "");
      detail.textContent = `Mở ${String(value.verification_uri || "GitHub")} và nhập mã ${String(value.user_code || "")} để xác thực. Token chỉ được lưu trong Credential Manager.`;
      pollAuth(card, String(value.session_id || ""));
    } else {
      detail.textContent = statusMessage(value);
    }
  } catch (error) {
    detail.textContent = `Đăng nhập chưa khả dụng: ${error.payload?.code || error.message}`;
  }
};

const pollAuth = async (card, sessionId) => {
  if (!sessionId) return;
  const detail = card.querySelector("[data-update-auth]");
  try {
    const value = await api("/api/app-update/auth/device/poll", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId }) });
    if (value.status === "authorization_pending") {
      detail.textContent = "Đang chờ xác nhận GitHub… mã xác thực vẫn chỉ hiển thị cục bộ.";
      window.setTimeout(() => pollAuth(card, sessionId), Math.max(2000, Number(value.retry_after || 5) * 1000));
      return;
    }
    if (value.status === "authenticated") {
      delete card.dataset.authSession;
      detail.textContent = "Đã xác thực GitHub an toàn. Đang kiểm tra artifact main…";
      check(true);
      return;
    }
    detail.textContent = statusMessage(value);
  } catch (error) {
    detail.textContent = `Device Flow chưa hoàn tất: ${error.payload?.code || error.message}`;
  }
};

const cancelAuth = async (card) => {
  const sessionId = String(card.dataset.authSession || "");
  if (!sessionId) return;
  delete card.dataset.authSession;
  card.querySelector("[data-update-auth]").textContent = "Đang hủy Device Flow…";
  try {
    await api("/api/app-update/auth/device/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: sessionId }) });
  } catch (error) {
    card.querySelector("[data-update-auth]").textContent = `Không thể hủy Device Flow: ${error.payload?.code || error.message}`;
  }
  render(card, lastStatus || { status: "auth_required" });
};

document.addEventListener("click", (event) => {
  const card = event.target.closest?.("[data-app-update-card]");
  if (!card) return;
  if (event.target.closest("[data-app-update-check]")) { check(true); return; }
  if (event.target.closest("[data-app-update-changes]")) { showChanges(card); return; }
  if (event.target.closest("[data-app-update-apply]")) { applyUpdate(card); return; }
  if (event.target.closest("[data-app-update-restart]")) { restart(card); return; }
  if (event.target.closest("[data-app-update-rollback]")) { rollback(card); }
  if (event.target.closest("[data-app-update-auth]")) { startAuth(card); }
  if (event.target.closest("[data-app-update-auth-cancel]")) { cancelAuth(card); }
});

injectStyle();
const observer = new MutationObserver(() => {
  if (!document.querySelector("#module-view .dashboard-page")) return;
  const dashboard = document.querySelector("#module-view .dashboard-page");
  const existing = dashboard.querySelector("[data-app-update-card]");
  const card = existing || ensureCard();
  if (!card || existing) return;
  if (lastStatus) render(card, lastStatus);
  else check(false);
});
const view = document.querySelector("#module-view");
if (view) observer.observe(view, { childList: true, subtree: true });
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { ensureCard(); check(false); }, { once: true });
else { ensureCard(); check(false); }
