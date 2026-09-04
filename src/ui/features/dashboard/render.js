/* Dashboard deliberately stays a compact control-plane surface.
 * Detailed storage, module, media-evidence and recovery projections belong
 * to their own routes so a refresh cannot turn the landing page into a
 * second copy of every subsystem.
 */
export function createDashboardRenderer(deps) {
  const {
    uiTextHtml,
    escapeHtml,
    formatGb,
    readinessSnapshot,
    jobRecoverySnapshot,
    readinessStatus,
    safeReadinessModules,
    statusPill,
    statusExplanation,
    statusImpact,
    textKey,
    formatStatus,
  } = deps;

  const SUCCESS_STATUSES = new Set(["healthy", "operational", "ready"]);
  const ACTIONABLE_STATUSES = new Set([
    "attention",
    "blocked",
    "cancelling",
    "degraded",
    "error",
    "failed",
    "needs_setup",
    "recovery_required",
    "stale_session",
  ]);
  const ATTENTION_JOB_STATUSES = new Set(["blocked", "cancelling", "failed", "interrupted"]);
  const ATTENTION_TRANSPORT_STATUSES = new Set([
    ...ACTIONABLE_STATUSES,
    "incompatible",
    "missing",
    "partial",
    "unavailable",
  ]);
  const STATUS_RANK = Object.freeze({
    error: 0,
    failed: 0,
    blocked: 0,
    needs_setup: 1,
    degraded: 1,
    attention: 1,
    cancelling: 2,
    interrupted: 2,
    starting: 3,
    running: 4,
    queued: 4,
    completed: 5,
    healthy: 5,
    operational: 5,
    ready: 5,
    partial: 6,
    unavailable: 6,
    missing: 6,
    not_installed: 6,
    not_run: 7,
    unknown: 8,
  });
  const UNSAFE_VOLUME_TEXT = /(?:[a-z]:[\\/]|\\\\|(?:file|data|https?):|(?:api[_-]?key|password|secret|token)\s*[:=])/i;

  const rankOf = (value) => {
    const status = String(value || "unknown");
    return Object.prototype.hasOwnProperty.call(STATUS_RANK, status) ? STATUS_RANK[status] : 4;
  };

  const compareText = (left, right) => {
    const leftKey = textKey(String(left || ""));
    const rightKey = textKey(String(right || ""));
    return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
  };

  const finiteBytes = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const safeVolumeText = (value, fallback) => {
    const candidate = typeof value === "string" ? value.trim().slice(0, 240) : "";
    return candidate && !UNSAFE_VOLUME_TEXT.test(candidate) ? candidate : fallback;
  };

  return function renderDashboard(state) {
    const source = state && typeof state === "object" ? state : {};
    const health = source.health && typeof source.health === "object" ? source.health : {};
    const healthDisk = health.disk && typeof health.disk === "object" ? health.disk : {};
    const storage = source.storage && typeof source.storage === "object" ? source.storage : {};
    const storageDisk = storage.disk && typeof storage.disk === "object" ? storage.disk : {};
    const gpu = health.gpu && typeof health.gpu === "object" ? health.gpu : {};
    const productization = source.productization && typeof source.productization === "object" ? source.productization : {};
    const readinessView = readinessSnapshot(source);
    const jobRecovery = jobRecoverySnapshot(source);
    const jobs = Array.isArray(jobRecovery.records) ? jobRecovery.records : [];
    const control = source.capabilities && typeof source.capabilities === "object" ? source.capabilities : {};
    const readiness = readinessView.status !== "unknown"
      ? readinessView.status
      : readinessStatus(control.status || health.status);
    const transport = readinessStatus(health.status || "unknown");
    const transportReady = SUCCESS_STATUSES.has(transport);
    const activeJobs = Number.isInteger(jobRecovery.counts?.active) ? jobRecovery.counts.active : 0;
    const freeBytes = finiteBytes(healthDisk.free_bytes) ?? finiteBytes(storageDisk.free_bytes);
    const productCapabilities = productization.capabilities && typeof productization.capabilities === "object"
      ? productization.capabilities
      : {};
    const rawModules = Array.isArray(productCapabilities.modules)
      ? readinessView.modules
      : safeReadinessModules({ ...source, components: Array.isArray(source.components) ? source.components : [] });
    const modules = rawModules.slice().sort((left, right) => {
      const statusDifference = rankOf(left.status) - rankOf(right.status);
      if (statusDifference) return statusDifference;
      const labelDifference = compareText(left.label, right.label);
      return labelDifference || compareText(left.id, right.id);
    });

    const attention = [];
    if (!transportReady && ATTENTION_TRANSPORT_STATUSES.has(transport)) {
      attention.push({
        id: "hub-api",
        title: "Hub API",
        technicalId: "hub-api",
        purpose: "Kết nối loopback để Hub đọc snapshot và nhận thao tác.",
        reason: "Loopback API chưa đạt trạng thái healthy.",
        impact: "Các trang có thể hiển thị snapshot cũ hoặc không nhận thao tác.",
        nextAction: "Làm mới API và mở Diagnostics để xem nguyên nhân.",
        status: transport,
      });
    }
    modules.filter((item) => ACTIONABLE_STATUSES.has(item.status)).forEach((item) => {
      attention.push({
        id: `module-${item.id}`,
        title: item.label,
        technicalId: item.id,
        purpose: item.purpose || "Capability server-owned cho workflow.",
        reason: item.reason,
        impact: item.impact || statusImpact(item.status, item.label),
        nextAction: item.nextAction,
        status: item.status,
      });
    });
    jobs.filter((item) => ATTENTION_JOB_STATUSES.has(String(item?.status || ""))).forEach((item, index) => {
      const jobId = item?.id || `job-${index + 1}`;
      attention.push({
        id: `job-${jobId}`,
        title: item?.title || "Tác vụ Hub",
        technicalId: jobId,
        purpose: `Tác vụ ${item?.jobType || item?.tool || "Hub"} xử lý dữ liệu người dùng.`,
        reason: item?.reason || item?.lifecycleNote || "Tác vụ chưa có kết quả thành công.",
        impact: statusImpact(item?.status || "failed", item?.title || "Tác vụ"),
        nextAction: item?.nextAction || "Mở Jobs để xem chi tiết và tạo lại khi backend sẵn sàng.",
        status: item?.status || "failed",
      });
    });
    attention.sort((left, right) => {
      const statusDifference = rankOf(left.status) - rankOf(right.status);
      return statusDifference || compareText(left.title, right.title) || compareText(left.id, right.id);
    });
    const attentionItems = attention.slice(0, 4);

    const metric = (key, label, value, detail, extra = "") => `<article class="metric-card" data-dashboard-metric="${escapeHtml(key)}"${extra}><span>${uiTextHtml(label)}</span><strong>${escapeHtml(String(value))}</strong><small>${uiTextHtml(detail)}</small></article>`;
    const attentionRows = attentionItems.length
      ? attentionItems.map((item) => statusExplanation({
        name: item.title,
        technicalId: item.technicalId,
        purpose: item.purpose,
        status: item.status,
        reason: item.reason,
        impact: item.impact,
        nextAction: item.nextAction,
        compact: true,
      })).join("")
      : `<p class="small muted">Không có hạng mục cần chú ý.</p>`;

    const quickActions = [
      ["image", "Image AI", "Compose và chỉnh sửa ảnh"],
      ["media", "Media", "Transform media trong Hub"],
      ["jobs", "Jobs", "Theo dõi queue và artifact"],
      ["models", "Models & Storage", "Kiểm tra inventory và dung lượng"],
      ["settings", "Readiness & Module Plan", "Xem bằng chứng server-owned"],
    ].map(([route, label, detail]) => `<button class="button button--compact" type="button" data-route="${escapeHtml(route)}"><strong>${uiTextHtml(label)}</strong><span class="row-meta">${uiTextHtml(detail)}</span></button>`).join("");

    const recentJobs = jobs.slice(0, 6).map((item, index) => {
      const id = String(item?.id || `job-${index + 1}`);
      const status = readinessStatus(item?.status, "unknown");
      const reconstructOnly = item?.reconstructOnlyPending === true;
      const title = item?.title || item?.tool || "Tác vụ Hub";
      const note = reconstructOnly ? "Đã tạo · chưa thực thi" : `${item?.progress || 0}%`;
      return `<li class="dashboard-recent-job" data-dashboard-job-id="${escapeHtml(id)}" data-dashboard-job-status="${escapeHtml(status)}" data-reconstruct-only="${reconstructOnly}"><div class="row-main"><strong>${escapeHtml(title)}</strong><span class="row-meta">${escapeHtml(note)} · ${reconstructOnly ? escapeHtml("reconstruct-only") : escapeHtml(id)}</span></div>${statusPill(status, formatStatus(status))}</li>`;
    }).join("");
    const recentJobsHtml = recentJobs || `<li class="dashboard-recent-job"><span class="row-meta">Chưa có bản ghi tác vụ.</span></li>`;
    const readinessNote = SUCCESS_STATUSES.has(readiness)
      ? "Snapshot Hub ổn định; chi tiết từng module nằm trong Readiness."
      : "Kiểm tra các mục cần chú ý trước khi chạy workflow.";
    const gpuValue = gpu.name || gpu.model || "Chưa phát hiện";
    const gpuDetail = gpu.memory_free_mib != null ? `${gpu.memory_free_mib} MiB VRAM trống` : "Snapshot GPU chưa sẵn sàng";
    const diskValue = freeBytes === null ? "—" : formatGb(freeBytes);
    const activeJobExtra = ` data-dashboard-active-count="${escapeHtml(String(activeJobs))}"`;
    const volumeValues = Array.isArray(readinessView.volumes) ? readinessView.volumes : [];
    const volumeLabel = (value) => ({ available: "Sẵn sàng", unavailable: "Chưa khả dụng", partial: "Một phần" }[String(value || "").toLowerCase()] || "Chưa rõ");
    const serverVolumes = volumeValues.filter((item) => item && ["c", "d"].includes(String(item.id || "").toLowerCase()));
    const volumeCards = serverVolumes.map((item) => {
      const total = finiteBytes(item.totalBytes);
      const used = finiteBytes(item.usedBytes);
      const free = finiteBytes(item.freeBytes);
      const percent = total !== null && total > 0 && used !== null
        ? Math.max(0, Math.min(100, Math.round((used / total) * 1000) / 10))
        : null;
      const status = ["available", "unavailable", "partial"].includes(String(item.status || "").toLowerCase()) ? String(item.status).toLowerCase() : "unavailable";
      const volumeId = String(item.id || "").toLowerCase();
      const safeLabel = safeVolumeText(item.label, volumeId.toUpperCase());
      const reason = safeVolumeText(item.reason, percent === null ? "Chưa có số liệu volume." : `${percent}% đã dùng`);
      const nextAction = safeVolumeText(item.nextAction, "Làm mới snapshot để cập nhật số liệu.");
      const progressValue = percent === null ? 0 : percent;
      return `<article class="dashboard-storage-volume" data-dashboard-volume="${escapeHtml(volumeId)}" data-status="${escapeHtml(status)}" data-low-space="${item.lowSpace === true}" data-dashboard-volume-total="${total === null ? "" : total}" data-dashboard-volume-used="${used === null ? "" : used}" data-dashboard-volume-free="${free === null ? "" : free}" data-dashboard-volume-percent="${percent === null ? "" : percent}"><div class="card-title-row"><div><span class="eyebrow">Ổ ĐĨA DO MÁY CHỦ SỞ HỮU</span><h3>${escapeHtml(safeLabel)}</h3></div>${statusPill(status, volumeLabel(status))}</div><div class="dashboard-storage-values"><div><span>Tổng</span><strong>${total === null ? "—" : escapeHtml(formatGb(total))}</strong></div><div><span>Đã dùng</span><strong>${used === null ? "—" : escapeHtml(formatGb(used))}</strong></div><div><span>Trống</span><strong>${free === null ? "—" : escapeHtml(formatGb(free))}</strong></div></div><div class="progress-track"><progress class="progress-bar" data-dashboard-volume-progress value="${progressValue}" max="100" aria-label="${escapeHtml(`Phần trăm đã dùng ${safeLabel}`)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progressValue}">${progressValue}%</progress></div><p class="dashboard-storage-reason">${escapeHtml(reason)}</p><p class="dashboard-storage-action"><strong>${item.lowSpace === true ? "Sắp hết dung lượng" : "Trạng thái"}</strong><span>${escapeHtml(nextAction)}</span></p></article>`;
    }).join("");
    const volumeSection = `<section class="dashboard-tier dashboard-storage card" data-dashboard-tier="volumes" aria-labelledby="dashboard-storage-title"><div class="card-title-row"><div><span class="eyebrow">LƯU TRỮ</span><h2 id="dashboard-storage-title">Tổng quan ổ lưu trữ</h2><p class="small">Chỉ metadata volume từ allowlist server-owned; làm mới Dashboard không quét thư mục.</p></div><span class="tag">${escapeHtml(String(serverVolumes.length))} volume</span></div><div class="dashboard-storage-grid">${volumeCards || `<p class="small muted">Chưa có volume server-owned khả dụng.</p>`}</div></section>`;

    return `<section class="dashboard-page" aria-labelledby="dashboard-title" data-dashboard-simplified="true"><header class="dashboard-hero"><div class="dashboard-hero__copy"><span class="eyebrow">TRUNG TÂM ĐIỀU KHIỂN</span><h1 id="dashboard-title">Dashboard</h1><p>${escapeHtml(readinessNote)}</p></div>${statusPill(readiness, formatStatus(readiness))}</header><section class="dashboard-tier dashboard-tier--summary card" data-dashboard-tier="summary" aria-labelledby="dashboard-summary-title"><div class="card-title-row"><div><span class="eyebrow">TÓM TẮT</span><h2 id="dashboard-summary-title">Tóm tắt hệ thống</h2></div><span class="tag">4 chỉ số</span></div><div class="dashboard-metric-grid">${metric("api", "Hub API", formatStatus(transport), transportReady ? "Loopback API đang phản hồi" : "Kiểm tra trạng thái kết nối")}${metric("gpu", "GPU", gpuValue, gpuDetail)}${metric("disk-free", "Dung lượng trống", diskValue, "Dung lượng còn lại trên volume chính")}${metric("active-jobs", "Jobs hoạt động", activeJobs, `${jobs.length} bản ghi trong queue`, activeJobExtra)}</div></section>${volumeSection}<section class="dashboard-tier dashboard-tier--attention card" data-dashboard-tier="attention" aria-labelledby="dashboard-attention-title"><div class="card-title-row"><div><span class="eyebrow">CẦN CHÚ Ý</span><h2 id="dashboard-attention-title">Cần chú ý</h2></div><span class="tag" data-dashboard-attention-count="${escapeHtml(String(attentionItems.length))}">Tối đa 4 mục</span></div><div class="dashboard-attention-list">${attentionRows}</div></section><section class="dashboard-tier dashboard-tier--actions card" data-dashboard-tier="actions" aria-labelledby="dashboard-actions-title"><div class="card-title-row"><div><span class="eyebrow">THAO TÁC</span><h2 id="dashboard-actions-title">Thao tác nhanh và tác vụ gần đây</h2></div></div><div class="dashboard-quick-actions">${quickActions}</div><div class="dashboard-recent-jobs-wrap"><h3>Tác vụ gần đây</h3><ul class="dashboard-recent-jobs">${recentJobsHtml}</ul></div></section></section>`;
  };
}
