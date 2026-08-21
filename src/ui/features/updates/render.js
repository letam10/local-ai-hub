/* Update Center presentation.  It only renders server-owned metadata reports;
 * no source URL, path, command or automatic apply action is accepted here. */

export function renderUpdateCenter({ updateCenter = {}, escapeHtml, card, heading, statusPill }) {
  const schedule = updateCenter.settings || {};
  const records = Array.isArray(updateCenter.records) ? updateCenter.records : [];
  const rows = records.map((item) => {
    const id = escapeHtml(item.component_id || "");
    const updateButton = item.status === "UPDATE_AVAILABLE" ? `<button class="button button--compact button--accent" type="button" data-plan-update="${id}">Plan Update</button>` : "";
    const rollbackButton = item.rollback_available ? `<button class="button button--compact" type="button" data-rollback-update="${id}">Roll Back</button>` : "";
    return `<div class="update-center__row"><div><strong>${id || "component"}</strong><span class="small">${escapeHtml(item.local_status || "UNKNOWN")} · ${escapeHtml(item.installed_revision || "unknown")} → ${escapeHtml(item.latest_supported_revision || "unknown")}</span><span class="small">${escapeHtml((item.changed_parts || []).join(", ") || "no changed parts")}</span></div><div>${statusPill(String(item.status || "CHECK_FAILED").toLowerCase())}<button class="button button--compact" type="button" data-check-update="${id}">Check Update</button>${updateButton}${rollbackButton}</div></div>`;
  }).join("") || `<div class="empty-state compact">Chưa có báo cáo update. Bấm “Check All Updates” để kiểm tra metadata theo yêu cầu.</div>`;
  const settingsValue = ["manual", "startup_24h", "daily", "weekly"].includes(schedule.policy) ? schedule.policy : "manual";
  const pending = updateCenter.pendingPlan;
  const pendingCard = pending?.status === "planned" ? `<div class="callout callout--warning"><strong>Update plan ${escapeHtml(pending.plan_id || "")}</strong><p>Thay đổi: ${escapeHtml((pending.changed_parts || []).join(", ") || "unknown")}; tải: ${Number(pending.download_required ? 1 : 0)} package theo server.</p><button class="button button--compact button--accent" type="button" data-confirm-update="${escapeHtml(pending.plan_id || "")}">Confirm Update</button></div>` : "";
  return heading("UPDATE CENTER", "Update Center", "Chỉ kiểm tra metadata khi bạn yêu cầu hoặc theo lịch nhẹ; không tự tải/cài đặt.", `<button class="button button--compact" type="button" data-check-all-updates>Check All Updates</button>`) + card("Update checking", `${pendingCard}<div class="form-grid"><label class="field"><span>Lịch kiểm tra</span><select data-update-schedule><option value="manual"${settingsValue === "manual" ? " selected" : ""}>Manual only</option><option value="startup_24h"${settingsValue === "startup_24h" ? " selected" : ""}>On startup (tối đa 24 giờ/lần)</option><option value="daily"${settingsValue === "daily" ? " selected" : ""}>Daily</option><option value="weekly"${settingsValue === "weekly" ? " selected" : ""}>Weekly</option></select></label><button class="button button--compact" type="button" data-save-update-schedule>Lưu lịch</button></div><div class="update-center__list">${rows}</div>`, "", "card--wide");
}

export const updatesFeature = Object.freeze({ id: "updates", refreshPolicy: "manual", schedules: ["manual", "startup_24h", "daily", "weekly"] });
