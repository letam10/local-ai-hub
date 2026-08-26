export function createJobsRenderer(deps) {
  const { jobRecoverySnapshot, escapeHtml, uiTextHtml, statusPill, statusExplanation, statusImpact, readinessStatusLabel, artifactList, provenanceList, heading } = deps;
  const ACTIVE = new Set(["queued", "starting", "running", "cancelling"]);
  const TERMINAL = new Set(["completed", "failed", "unavailable", "cancelled", "interrupted"]);
  const durationText = (job) => {
    if (!job.startedAt || !job.finishedAt) return "Chưa công bố";
    const start = Date.parse(job.startedAt);
    const finish = Date.parse(job.finishedAt);
    if (!Number.isFinite(start) || !Number.isFinite(finish) || finish < start) return "Chưa công bố";
    const seconds = Math.round((finish - start) / 1000);
    return seconds < 60 ? `${seconds} giây` : `${Math.floor(seconds / 60)} phút ${seconds % 60} giây`;
  };

  return function renderJobs(state) {
    const recovery = jobRecoverySnapshot(state);
    const tabFilter = state.jobFilter || "all";
    const query = String(state.jobQuery || "").trim().toLowerCase();
    const typeFilter = String(state.jobTypeFilter || "all");
    const sort = state.jobSort === "oldest" ? "oldest" : "newest";
    const page = Number.isInteger(state.jobPage) && state.jobPage > 0 ? state.jobPage : 1;
    const pageSize = 50;
    const filtered = recovery.records.filter((job) => {
      const active = job.active === true;
      const tabOk = tabFilter === "all" || (tabFilter === "active" && active) || (tabFilter === "attention" && ["failed", "unavailable", "cancelled", "interrupted"].includes(job.status)) || (tabFilter === "completed" && job.status === "completed");
      const queryOk = !query || `${job.title} ${job.tool} ${job.id} ${job.reason}`.toLowerCase().includes(query);
      const typeOk = typeFilter === "all" || job.jobType === typeFilter;
      return tabOk && queryOk && typeOk;
    }).sort((left, right) => {
      const a = String(left.finishedAt || left.createdAt || "");
      const b = String(right.finishedAt || right.createdAt || "");
      return sort === "oldest" ? a.localeCompare(b) : b.localeCompare(a);
    });
    const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
    const safePage = Math.min(page, pageCount);
    const pageItems = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);
    const rows = pageItems.map((job) => {
      const active = job.active === true;
      const terminal = TERMINAL.has(job.status);
      const durable = job.source === "durable";
      const durableReconstructOnly = durable && job.retryMode === "reconstruct_only";
      const reconstructOnlyPending = job.reconstructOnlyPending === true;
      const actions = active && !durable && job.actionId
        ? `<button class="button button--compact button--danger" type="button" data-focus-key="job-action-cancel" data-cancel-job="${escapeHtml(job.actionId)}">${uiTextHtml("Hủy tác vụ")}</button>`
        : job.resumable && job.actionId
          ? `<button class="button button--compact" type="button" data-focus-key="job-action-resume" ${durable ? `data-resume-durable-job="${escapeHtml(job.id)}"` : `data-resume-job="${escapeHtml(job.id)}"`}>${uiTextHtml(durableReconstructOnly ? "Tạo lại tác vụ" : job.status === "cancelled" ? "Tiếp tục" : "Thử lại")}</button>`
          : "";
      const deleteAction = terminal && !durable ? `<button class="button button--compact button--danger" type="button" data-delete-job="${escapeHtml(job.id)}">Xóa khỏi lịch sử</button>` : "";
      const timestampRows = [["Tạo lúc", job.createdAt], ["Bắt đầu", job.startedAt], ["Kết thúc", job.finishedAt], ["Thời lượng", durationText(job)]].filter(([, value]) => value).map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      const artifactSummary = job.artifacts.length ? `<div class="job-artifact-summary" data-artifact-state="available"><strong>Đầu ra</strong><span>${escapeHtml(String(job.artifacts.length))} artifact khả dụng</span><span class="sr-only">${escapeHtml(String(job.artifacts.length))} available</span></div>${artifactList(job.artifacts)}` : `<div class="job-artifact-summary" data-artifact-state="unavailable"><strong>Đầu ra</strong><span>Không có artifact được công bố trong snapshot này.</span><span class="sr-only">Preview unavailable in this snapshot.</span></div>`;
      const reason = job.reason || "Tác vụ chưa công bố nguyên nhân chi tiết.";
      const impact = statusImpact(job.status, job.title);
      const explanation = statusExplanation({ name: job.title, technicalId: job.id, purpose: `Tác vụ ${job.jobType} xử lý dữ liệu trong Hub.`, status: job.status, reason, impact, nextAction: job.nextAction, compact: true });
      const errorCode = job.errorCode ? `<p class="job-error-code"><strong>Mã lỗi</strong> <code>${escapeHtml(job.errorCode)}</code></p>` : "";
      const retryExplanation = durableReconstructOnly && job.resumable
        ? `<span class="job-action-note">Tạo lại chỉ tạo bản ghi mới; chưa thực thi trong V8.</span>`
        : "";
      const noAction = actions || deleteAction
        ? ""
        : terminal && !job.resumable
          ? `<span class="job-action-note">${durable ? "Không thể tạo lại tác vụ từ snapshot này." : "Không thể thử lại trong phiên này."} ${escapeHtml(job.nextAction || "Hãy tạo lại tác vụ từ workspace.")}</span>`
          : `<span class="job-action-note">Không có thao tác an toàn nào cho snapshot này.</span>`;
      const lifecycle = reconstructOnlyPending ? "Đã tạo · chưa thực thi" : job.lifecycle || (active ? "Đang xử lý" : job.status === "completed" ? "Đã hoàn tất" : "Đã kết thúc");
      const sourceLabel = job.source === "durable" ? "Durable" : "Hot";
      const recoveryLabel = reconstructOnlyPending ? "Đã tạo · chưa thực thi" : durableReconstructOnly ? (job.resumable ? "Có thể tạo lại" : "Không thể tạo lại") : (job.resumable ? "Có thể thử lại" : "Không có");
      return `<article class="job-card" id="job-${escapeHtml(job.id)}" data-job-id="${escapeHtml(job.id)}" data-job-source="${escapeHtml(job.source)}" data-job-status="${escapeHtml(job.status)}" data-job-resumable="${job.resumable === true}"><div class="job-card__header"><div><div class="job-card__title"><strong>${escapeHtml(job.title)}</strong><span class="sr-only" data-job-source-label="${escapeHtml(job.source)}">${sourceLabel}</span><span class="tag job-source">${escapeHtml(job.source === "durable" ? "Durable" : "Lịch sử Hub")}</span></div><div class="row-meta">Loại: ${escapeHtml(job.jobType)} · ${escapeHtml(job.id)}</div></div>${statusPill(job.status, readinessStatusLabel(job.status))}</div><div class="job-card__summary"><div><span>Trạng thái</span><strong>${escapeHtml(lifecycle)}</strong><span class="sr-only">Lifecycle</span></div><div><span>Đầu ra</span><strong>${job.artifacts.length ? "Có artifact" : "Chưa có artifact"}</strong></div><div><span>Khôi phục</span><strong>${recoveryLabel}</strong><span class="sr-only">Recovery reason</span></div></div><div class="progress-track" role="progressbar" aria-label="${escapeHtml(job.title)} tiến độ" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${job.progress}"><div class="progress-bar" style="width:${job.progress}%"></div></div>${job.lifecycleNote ? `<p class="job-message">${escapeHtml(job.lifecycleNote)}</p>` : ""}${errorCode}${explanation}<div class="job-timestamps">${timestampRows}</div>${provenanceList(job.provenance)}${artifactSummary}<div class="form-actions">${actions}${deleteAction}${retryExplanation}${noAction}</div></article>`;
    }).join("");
    const filters = [["all", "Tất cả"], ["active", "Đang chạy"], ["attention", "Cần chú ý"], ["completed", "Đã hoàn tất"]].map(([id, label]) => `<button class="tab ${tabFilter === id ? "is-selected" : ""}" type="button" data-focus-key="job-filter-${id}" data-job-filter="${id}" aria-pressed="${tabFilter === id}">${uiTextHtml(label)}</button>`).join("");
    const types = [...new Set(recovery.records.map((item) => item.jobType).filter(Boolean))].sort();
    const counts = recovery.counts;
    const clearButton = counts.attention || counts.total ? `<button class="button button--danger button--compact" type="button" data-clear-terminal-history>Xóa lịch sử đã kết thúc</button>` : "";
    return `<section id="jobs-page" class="jobs-page" data-job-recovery-source="${escapeHtml(recovery.source)}" data-job-recovery-status="${escapeHtml(recovery.status)}">${heading("CONTROL PLANE", "Tác vụ & Lịch sử", "Mỗi tác vụ cho biết đã làm gì, vì sao thành công/thất bại, đầu ra còn hay không và bước xử lý tiếp theo.", clearButton)}<div class="job-recovery-banner card"><div class="card-title-row"><div><span class="eyebrow">RECOVERY SNAPSHOT</span><h2>Trạng thái hàng đợi và phục hồi</h2><p>${escapeHtml(recovery.reason)}</p></div>${statusPill(recovery.status, readinessStatusLabel(recovery.status))}</div><div class="job-recovery-counts"><div><span>Đang chạy</span><strong>${escapeHtml(String(counts.active))}</strong></div><div><span>Cần chú ý</span><strong>${escapeHtml(String(counts.attention))}</strong></div><div><span>Bị gián đoạn</span><strong>${escapeHtml(String(counts.interrupted))}</strong></div><div><span>Có thể khôi phục</span><strong>${escapeHtml(String(counts.recoverable))}</strong></div></div><p class="small">Nguồn: ${escapeHtml(recovery.source)} · execution: ${escapeHtml(recovery.execution)}${recovery.dryRun ? " · dry_run: true" : ""}</p></div><div class="job-history-controls card"><div class="job-filters" role="group" aria-label="Bộ lọc lịch sử tác vụ">${filters}</div><div class="job-filter-grid"><label><span>Tìm tác vụ</span><input type="search" data-job-search value="${escapeHtml(state.jobQuery || "")}" placeholder="Tên, loại hoặc mã kỹ thuật" /></label><label><span>Loại</span><select data-job-type-filter><option value="all">Tất cả loại</option>${types.map((type) => `<option value="${escapeHtml(type)}" ${typeFilter === type ? "selected" : ""}>${escapeHtml(type)}</option>`).join("")}</select></label><label><span>Sắp xếp</span><select data-job-sort><option value="newest" ${sort === "newest" ? "selected" : ""}>Mới nhất trước</option><option value="oldest" ${sort === "oldest" ? "selected" : ""}>Cũ nhất trước</option></select></label></div><p class="small">Chỉ bản ghi đã kết thúc mới có thể xóa khỏi lịch sử. File đầu ra và artifact không bị xóa.</p></div><div class="job-action-status" data-job-action-status role="status" aria-live="polite"></div><div class="job-list">${rows || `<div class="empty-state"><strong>Không có tác vụ phù hợp</strong><span>Đổi bộ lọc hoặc chọn Tất cả để xem snapshot server-owned.</span></div>`}</div><div class="job-pagination"><span>Trang ${safePage} / ${pageCount} · ${filtered.length} tác vụ</span><div class="form-actions"><button class="button button--compact" type="button" data-job-page="prev" ${safePage <= 1 ? "disabled" : ""}>Trang trước</button><button class="button button--compact" type="button" data-job-page="next" ${safePage >= pageCount ? "disabled" : ""}>Trang sau</button></div></div></section>`;
  };
}
