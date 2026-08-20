/* Update Center presentation.  It only renders server-owned metadata reports;
 * no source URL, path, command or automatic apply action is accepted here. */

export function renderUpdateCenter({ updateCenter = {}, escapeHtml, card, heading, statusPill }) {
  const schedule = updateCenter.settings || {};
  const records = Array.isArray(updateCenter.records) ? updateCenter.records : [];
  const rows = records.map((item) => `<div class="update-center__row"><div><strong>${escapeHtml(item.component_id || "component")}</strong><span class="small">${escapeHtml(item.local_status || "UNKNOWN")} · ${escapeHtml(item.installed_revision || "unknown")} → ${escapeHtml(item.latest_supported_revision || "unknown")}</span></div><div>${statusPill(String(item.status || "CHECK_FAILED").toLowerCase())}<button class="button button--compact" type="button" data-check-update="${escapeHtml(item.component_id || "")}">Check Update</button></div></div>`).join("") || `<div class="empty-state compact">Chưa có báo cáo update. Bấm “Check All Updates” để kiểm tra metadata theo yêu cầu.</div>`;
  const settingsValue = ["manual", "startup_24h", "daily", "weekly"].includes(schedule.policy) ? schedule.policy : "manual";
  return heading("UPDATE CENTER", "Update Center", "Chỉ kiểm tra metadata khi bạn yêu cầu hoặc theo lịch nhẹ; không tự tải/cài đặt.", `<button class="button button--compact" type="button" data-check-all-updates>Check All Updates</button>`) + card("Update checking", `<div class="form-grid"><label class="field"><span>Lịch kiểm tra</span><select data-update-schedule><option value="manual"${settingsValue === "manual" ? " selected" : ""}>Manual only</option><option value="startup_24h"${settingsValue === "startup_24h" ? " selected" : ""}>On startup (tối đa 24 giờ/lần)</option><option value="daily"${settingsValue === "daily" ? " selected" : ""}>Daily</option><option value="weekly"${settingsValue === "weekly" ? " selected" : ""}>Weekly</option></select></label><button class="button button--compact" type="button" data-save-update-schedule>Lưu lịch</button></div><div class="update-center__list">${rows}</div>`, "", "card--wide");
}

export const updatesFeature = Object.freeze({ id: "updates", refreshPolicy: "manual", schedules: ["manual", "startup_24h", "daily", "weekly"] });
