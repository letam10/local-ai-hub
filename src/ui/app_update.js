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
.app-update-modal{position:fixed;inset:0;z-index:10000;display:grid;place-items:center;padding:24px;background:rgba(3,7,18,.76)}.app-update-modal__panel{width:min(560px,100%);display:grid;gap:14px;padding:22px;border:1px solid var(--border-color,#45639d);border-radius:16px;background:#111b33;box-shadow:0 18px 60px rgba(0,0,0,.4)}.app-update-modal__panel h2{margin:0;font-size:1.15rem}.app-update-modal__panel p{margin:0;color:#b7c3df;line-height:1.5}.app-update-modal__builds{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.app-update-modal__build{display:grid;gap:3px;padding:10px 12px;border-radius:10px;background:rgba(15,23,42,.5)}.app-update-modal__build span{font-size:.76rem;opacity:.75}.app-update-modal__build code{overflow-wrap:anywhere}.app-update-modal__actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}.app-update-modal__actions button{min-height:38px}@media (max-width:520px){.app-update-modal__builds{grid-template-columns:1fr}}
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
  update_available: "Có bản cập nhật",
  up_to_date: "Đã mới nhất",
  idle: "Đang ổn định",
  checking: "Đang kiểm tra",
  preparing: "Đang chuẩn bị",
  downloading: "Đang tải",
  verifying: "Đang xác minh",
  staging: "Đang stage",
  staged: "Đã stage · chờ khởi động lại",
  confirm_restart: "Sẵn sàng khởi động lại",
  ready_to_restart: "Sẵn sàng khởi động lại",
  restarting: "Đang khởi động lại",
  succeeded: "Đã cập nhật",
  rolled_back: "Đã khôi phục phiên bản trước",
  blocked: "Đang bị chặn an toàn",
  error: "Cập nhật thất bại",
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
  if (value?.phase === "preparing" && Number.isFinite(Number(value?.progress))) return `Đang chuẩn bị payload cập nhật (${Number(value.progress)}%).`;
  if (value?.phase === "restarting") return `Đang khởi động lại Local AI Hub (${Number.isFinite(Number(value?.progress)) ? Number(value.progress) : 0}%).`;
  if (value?.status === "available" || value?.phase === "update_available") return "Main CI đã xanh và có payload mới đã được đóng gói. Bạn có thể xem thay đổi trước khi cập nhật.";
  if (value?.status === "up_to_date") return "Local AI Hub đang chạy đúng build main mới nhất đã có artifact.";
  if (value?.phase === "succeeded") return "Cập nhật đã vượt qua API/frontend readiness; payload hiện tại đang hoạt động bình thường.";
  if (value?.status === "auth_required") return "Repo là private. Hãy đăng nhập GitHub CLI một lần trên máy này; Hub không lưu hoặc hiển thị token.";
  if (value?.status === "oauth_configuration_required") return "Native GitHub Device Flow cần OAuth App client_id do chủ repo cung cấp; vẫn có thể dùng GitHub CLI đã đăng nhập.";
  if (value?.status === "blocked_active_jobs") return "Không thể cập nhật khi Hub còn job hoạt động. Dữ liệu và payload hiện tại vẫn được giữ nguyên.";
  if (value?.status === "incompatible_runtime") return "Payload yêu cầu runtime/launcher khác; updater đã fail-closed và chưa đổi current pointer.";
  if (value?.phase === "rolled_back" || value?.status === "rolled_back" || value?.status === "rollback") return `Readiness của payload mới chưa đạt; app đã phục hồi payload trước. Mã lỗi: ${String(value?.reason_code || value?.code || "unknown")}. Kiểm tra Diagnostics rồi thử lại khi phù hợp.`;
  if (value?.status === "failed" || value?.phase === "error") return `Cập nhật không hoàn tất; payload hiện tại vẫn được giữ nguyên. Mã lỗi: ${String(value?.reason_code || value?.code || "unknown")}.`;
  if (value?.phase === "blocked") return `Cập nhật đang bị chặn an toàn. Mã lý do: ${String(value?.reason_code || value?.code || "unknown")}. Payload hiện tại và dữ liệu người dùng vẫn được giữ nguyên.`;
  if (value?.status === "no_artifact") return "Main chưa có update artifact thành công. Hub sẽ không cập nhật từ một build chưa qua CI.";
  if (value?.status === "staged" || value?.phase === "confirm_restart") return "Candidate đã được stage và xác minh; current pointer chưa đổi. Chọn “Khởi động lại và cập nhật” khi bạn sẵn sàng, hoặc chọn “Để sau”.";
  if (value?.retry_attempts) return `Đã thử lại kiểm tra GitHub ${value.retry_attempts}/3 lần do lỗi tạm thời; kết quả hiện tại đã được xác minh.`;
  if (value?.code === "GITHUB_CLI_REQUIRED") return "Cần cài GitHub CLI (gh) để Hub đọc artifact của repo private mà không nhúng token vào ứng dụng.";
  return String(value?.action || "Không thể xác minh bản cập nhật lúc này; phiên bản đang chạy vẫn được giữ nguyên.");
};

const make = (tag, className = "") => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
};

const showConfirmModal = ({ title, current, candidate, message, confirmLabel, cancelLabel = "Hủy" }) => new Promise((resolve) => {
  const overlay = make("div", "app-update-modal");
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  const panel = make("section", "app-update-modal__panel");
  const heading = make("h2"); heading.textContent = title;
  const copy = make("p"); copy.textContent = message;
  const builds = make("div", "app-update-modal__builds");
  const oldBuild = make("div", "app-update-modal__build");
  const oldLabel = make("span"); oldLabel.textContent = "Build hiện tại (được giữ nguyên)";
  const oldValue = make("code"); oldValue.textContent = String(current || "—");
  oldBuild.append(oldLabel, oldValue);
  const newBuild = make("div", "app-update-modal__build");
  const newLabel = make("span"); newLabel.textContent = "Build candidate";
  const newValue = make("code"); newValue.textContent = String(candidate || "—");
  newBuild.append(newLabel, newValue); builds.append(oldBuild, newBuild);
  const actions = make("div", "app-update-modal__actions");
  const cancel = make("button", "button button--compact"); cancel.type = "button"; cancel.textContent = cancelLabel;
  const confirm = make("button", "button button--compact"); confirm.type = "button"; confirm.textContent = confirmLabel;
  actions.append(cancel, confirm); panel.append(heading, copy, builds, actions); overlay.append(panel); document.body.append(overlay);
  const finish = (value) => { overlay.remove(); resolve(value); };
  cancel.addEventListener("click", () => finish(false));
  confirm.addEventListener("click", () => finish(true));
  overlay.addEventListener("click", (event) => { if (event.target === overlay) finish(false); });
  panel.addEventListener("keydown", (event) => { if (event.key === "Escape") finish(false); });
  confirm.focus();
});

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
  const auth = make("button", "button button--compact"); auth.type = "button"; auth.dataset.appUpdateAuth = "true"; auth.textContent = "Đăng nhập GitHub"; auth.hidden = true;
  const authCancel = make("button", "button button--compact"); authCancel.type = "button"; authCancel.dataset.appUpdateAuthCancel = "true"; authCancel.textContent = "Hủy đăng nhập"; authCancel.hidden = true;
  actions.append(refresh, changes, update, restart, auth, authCancel);
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
  const state = String(value?.phase || value?.status || "unavailable");
  card.dataset.state = state;
  card.querySelector("[data-update-badge]").textContent = statusLabel(state);
  card.querySelector("[data-update-current]").textContent = shortBuild(value?.current_payload_id || value?.current_payload || value?.current_build);
  card.querySelector("[data-update-latest]").textContent = shortBuild(value?.candidate_payload_id || value?.latest_payload || value?.latest_build);
  card.querySelector("[data-update-message]").textContent = statusMessage(value);
  card.querySelector("[data-app-update-changes]").disabled = !(value?.latest_build || value?.candidate_payload_id);
  card.querySelector("[data-app-update-apply]").disabled = !(value?.can_prepare === true || value?.available === true);
  const restartButton = card.querySelector("[data-app-update-restart]");
  restartButton.hidden = !((value?.can_restart === true || ["staged", "activated", "ready_to_restart", "confirm_restart"].includes(state)) && value?.requires_restart !== false && value?.restart_required !== false);
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
  if (!(lastStatus?.can_prepare === true || lastStatus?.available === true)) return;
  const button = card.querySelector("[data-app-update-apply]");
  button.disabled = true;
  render(card, { ...(lastStatus || {}), status: "preparing", phase: "preparing", available: false, can_prepare: false, can_restart: false, requires_restart: true, progress: 5 });
  card.querySelector("[data-update-message]").textContent = "Đang tải artifact, xác minh identity/hash, stage payload và chạy candidate preflight…";
  try {
    const value = await api(API.prepare, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed: true }) });
    const staged = {
      ...(lastStatus || {}),
      ...value,
      status: "staged",
      phase: "confirm_restart",
      available: false,
      can_prepare: false,
      can_restart: true,
      requires_restart: true,
      staged_payload: value.payload_id,
      staged_build: value.source_commit,
      candidate_payload_id: value.candidate_payload_id || value.payload_id,
    };
    render(card, staged);
    const confirmed = await showConfirmModal({
      title: "Bản cập nhật đã sẵn sàng",
      current: shortBuild(staged.current_payload_id || staged.current_payload || staged.current_build),
      candidate: shortBuild(staged.candidate_payload_id || staged.payload_id || staged.source_commit),
      message: "Artifact và candidate preflight đã đạt. Payload cũ được giữ nguyên để rollback; current pointer chỉ đổi sau khi bạn xác nhận khởi động lại.",
      confirmLabel: "Khởi động lại và cập nhật",
      cancelLabel: "Để sau",
    });
    if (confirmed) await restart(card);
  } catch (error) {
    const payload = error?.payload && typeof error.payload === "object" ? error.payload : {};
    const code = payload.reason_code || payload.code || error.message;
    // Re-render the complete server projection.  In particular, transient
    // prepare failures carry can_prepare=true; changing only text would leave
    // lastStatus.can_prepare=false and make the visible retry a no-op.
    render(card, {
      ...payload,
      status: payload.status || "blocked",
      phase: payload.phase || "error",
      action: `Cập nhật bị chặn an toàn: ${code}. Bản đang chạy không bị thay đổi.`,
    });
  }
};

const restart = async (card) => {
  const button = card.querySelector("[data-app-update-restart]");
  button.disabled = true;
  render(card, { ...(lastStatus || {}), status: "restarting", phase: "restarting", available: false, can_prepare: false, can_restart: false, progress: 85 });
  card.querySelector("[data-update-message]").textContent = "Đang khởi động lại Local AI Hub bằng stable launcher…";
  try {
    const bridge = window.pywebview?.api;
    if (!bridge?.restart_after_update) throw new Error("Native restart bridge chưa khả dụng trong payload này.");
    const value = await bridge.restart_after_update();
    if (value?.status !== "completed") throw new Error(value?.code || "Restart bị chặn.");
  } catch (error) {
    const failed = {
      ...(lastStatus || {}),
      status: "staged",
      phase: "confirm_restart",
      can_prepare: false,
      can_restart: true,
      requires_restart: true,
    };
    render(card, failed);
    card.querySelector("[data-update-message]").textContent = `${error.message} Mã lỗi: ${error.payload?.reason_code || error.payload?.code || "restart_failed"}. Payload mới vẫn được stage an toàn; bạn có thể thử lại.`;
    button.disabled = false;
  }
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
