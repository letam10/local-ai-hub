/* Dashboard's feature-owned renderer. The pages.js compatibility layer passes
 * only the bounded formatting/projection helpers it already owns. */
export function createDashboardRenderer(deps) {
  const { uiTextHtml, escapeHtml, formatGb, readinessSnapshot, jobRecoverySnapshot, readinessStatus, safeReadinessModules, statusPill, readinessStatusLabel, textKey, mediaEvidencePanel, workflowLibraryState, formatStatus } = deps;
  return function renderDashboard(state) {
    const source = state && typeof state === "object" ? state : {};
    const health = source.health && typeof source.health === "object" ? source.health : {};
    const disk = health.disk && typeof health.disk === "object" ? health.disk : {};
    const gpu = health.gpu && typeof health.gpu === "object" ? health.gpu : {};
    const productization = source.productization && typeof source.productization === "object" ? source.productization : {};
    const storage = source.storage && typeof source.storage === "object" ? source.storage : (productization.storage && typeof productization.storage === "object" ? productization.storage : {});
    const readinessView = readinessSnapshot(source);
    const volumes = readinessView.volumes;
    const jobRecovery = jobRecoverySnapshot(source);
    const jobs = jobRecovery.records;
    const components = Array.isArray(source.components) ? source.components : [];
    const componentFallback = components.map((item) => item);
    const control = source.capabilities && typeof source.capabilities === "object" ? source.capabilities : {};
    const readiness = readinessView.status !== "unknown" ? readinessView.status : readinessStatus(control.status || health.status);
    const transport = readinessStatus(health.status || "unknown");
    const transportReady = ["healthy", "operational", "ready"].includes(transport);
    const activeJobs = jobRecovery.counts.active;
    const metricStatic = (label, value, detail) => {
      if (label === "Hub API" && transportReady) {
        value = formatStatus(transport);
        detail = "Loopback API đang chạy; readiness module hiển thị riêng.";
      }
      return `<article class="metric-card"><span data-i18n="${escapeHtml(label)}">${uiTextHtml(label)}</span><strong>${escapeHtml(value)}</strong><small data-i18n="${escapeHtml(detail)}">${uiTextHtml(detail)}</small></article>`;
    };
    const metricSnapshot = (label, value, detail) => `<article class="metric-card"><span data-i18n="${escapeHtml(label)}">${uiTextHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`;
    const statusRank = { error: 0, unavailable: 0, missing: 0, not_published: 0, partial: 1, not_run: 1, planned: 2, starting: 3, installed: 4, operational: 5, healthy: 5, ready: 5, clean: 5 };
    const rankOf = (status) => Object.prototype.hasOwnProperty.call(statusRank, status) ? statusRank[status] : 3;
    const productCapabilities = productization.capabilities && typeof productization.capabilities === "object" ? productization.capabilities : {};
    const modules = Array.isArray(productCapabilities.modules) ? readinessView.modules : safeReadinessModules({ ...source, components: componentFallback });
    modules.sort((left, right) => {
      const rankDifference = rankOf(left.status) - rankOf(right.status);
      if (rankDifference) return rankDifference;
      const labelDifference = textKey(left.label) < textKey(right.label) ? -1 : textKey(left.label) > textKey(right.label) ? 1 : 0;
      if (labelDifference) return labelDifference;
      return textKey(left.id) < textKey(right.id) ? -1 : textKey(left.id) > textKey(right.id) ? 1 : 0;
    });
    const attention = [];
    if (!transportReady && readiness !== "healthy" && readiness !== "operational") attention.push({ id: "hub-api", title: "Hub API", detail: uiTextHtml("Loopback API transport needs review"), status: readiness });
    modules.filter((item) => ["error", "unavailable", "missing", "partial", "not_published", "not_run"].includes(item.status)).forEach((item) => attention.push({ id: `module-${item.id}`, title: item.label, detail: item.kind, status: item.status }));
    jobs.filter((item) => ["failed", "unavailable", "cancelled", "interrupted"].includes(String(item?.status || ""))).forEach((item, index) => {
      const jobId = item?.id || `job-${index + 1}`;
      attention.push({ id: `job-${jobId}`, title: jobId, detail: item?.reason || item?.lifecycleNote || uiTextHtml("Job needs review"), status: item?.status || "failed" });
    });
    attention.sort((left, right) => { const rankDifference = rankOf(left.status) - rankOf(right.status); if (rankDifference) return rankDifference; return textKey(left.title) < textKey(right.title) ? -1 : textKey(left.title) > textKey(right.title) ? 1 : 0; });
    const attentionItems = attention.slice(0, 4);
    const moduleRows = modules.length ? modules.slice(0, 12).map((item) => `<div class="dashboard-module-row"><div class="row-main"><strong>${escapeHtml(item.label)}</strong><span class="row-meta">${escapeHtml(item.kind)}${item.version ? ` · ${escapeHtml(item.version)}` : ""}</span></div>${statusPill(item.status, readinessStatusLabel(item.status))}</div>`).join("") : `<p class="small muted">Chưa có module trong readiness snapshot.</p>`;
    const attentionRows = attentionItems.length ? attentionItems.map((item) => `<div class="dashboard-module-row"><div class="row-main"><strong>${escapeHtml(item.title)}</strong><span class="row-meta">${escapeHtml(item.detail)}</span></div>${statusPill(item.status, readinessStatusLabel(item.status))}</div>`).join("") : `<p class="small muted">Không có hạng mục cần chú ý.</p>`;
    const moduleEvidenceRows = modules.length ? modules.slice(0, 12).map((item) => `<div class="dashboard-module-row"><div class="row-main"><strong>${escapeHtml(item.label)}</strong><span class="row-meta">${escapeHtml(item.reason)}</span><span class="row-meta">${escapeHtml(item.nextAction)}</span></div>${statusPill(item.status, readinessStatusLabel(item.status))}</div>`).join("") : `<p class="small muted">${uiTextHtml("No server-owned module evidence.")}</p>`;
    const quickActions = [["image", "Image AI", "Compose và chỉnh sửa ảnh"], ["media", "Media", "Transform media trong Hub"], ["jobs", "Jobs", "Theo dõi queue và artifact"], ["models", "Models & Storage", "Kiểm tra inventory"]].map(([route, label, detail]) => `<button class="button button--compact" type="button" data-route="${escapeHtml(route)}"><strong>${uiTextHtml(label)}</strong><span class="row-meta">${uiTextHtml(detail)}</span></button>`).join("");
    const readinessAction = `<button class="button button--compact" type="button" data-readiness-route="settings"><strong>${uiTextHtml("Readiness & Module Plan")}</strong><span class="row-meta">${uiTextHtml("Review the server snapshot")}</span></button>`;
    const workflowSteps = [["01", "Check readiness", "Review module health and attention."], ["02", "Choose a route", "Open an existing Hub workspace."], ["03", "Run from Jobs", "Keep progress and artifacts in Hub."]].map(([step, title, detail]) => `<li class="dashboard-module-row"><span class="tag">${escapeHtml(step)}</span><div class="row-main"><strong>${uiTextHtml(title)}</strong><span class="row-meta">${uiTextHtml(detail)}</span></div></li>`).join("");
    const gpuValue = gpu.name || "Chưa phát hiện";
    const gpuDetail = gpu.memory_free_mib != null ? `${gpu.memory_free_mib} MiB VRAM trống` : "Snapshot GPU chưa sẵn sàng";
    const diskValue = disk.free_bytes != null ? formatGb(disk.free_bytes) : "—";
    const readinessNote = readiness === "healthy" || readiness === "operational" ? "Hub API snapshot ổn định; readiness của từng module vẫn được hiển thị riêng." : "Kiểm tra các mục cần chú ý trước khi chạy workflow.";
    const planStatus = readinessView.planStatus;
    const planReason = readinessView.planReason;
    const planAction = readinessView.planAction;
    const storageStatus = readinessStatus(storage.status, "unavailable");
    const storageExecution = readinessView.execution;
    const storageVolumeCard = (volume) => {
      const volumeId = String(volume?.id || "").toLowerCase();
      const label = volumeId === "c" ? "C:" : volumeId === "d" ? "D:" : "Volume";
      const status = readinessStatus(volume?.status, "unavailable");
      const available = volume?.available === true;
      const value = (key) => available ? formatGb(volume[`${key}Bytes`]) : "—";
      const low = volume?.lowSpace === true;
      const reason = volume?.reason || uiTextHtml("Volume statistics are unavailable; no figures are shown.");
      const action = volume?.nextAction || uiTextHtml("Verify that the volume is mounted and readable, then refresh storage.");
      return `<article class="dashboard-storage-volume" data-volume-id="${escapeHtml(volumeId || "unknown")}" data-status="${escapeHtml(status)}" data-low-space="${low}"><div class="card-title-row"><div><span class="eyebrow">${uiTextHtml("SERVER-OWNED VOLUME")}</span><h3>${escapeHtml(label)}</h3></div>${statusPill(status, uiTextHtml(status === "available" ? "Available" : "Unavailable"))}</div><div class="dashboard-storage-values"><div><span>${uiTextHtml("Total")}</span><strong>${escapeHtml(value("total"))}</strong></div><div><span>${uiTextHtml("Free")}</span><strong>${escapeHtml(value("free"))}</strong></div><div><span>${uiTextHtml("Used")}</span><strong>${escapeHtml(value("used"))}</strong></div></div><p class="dashboard-storage-reason">${escapeHtml(reason)}</p>${low ? `<div class="callout callout--warning dashboard-storage-warning" role="alert"><strong>${uiTextHtml("Low space")}</strong><span>${escapeHtml(action)}</span></div>` : `<div class="dashboard-storage-action"><strong>${uiTextHtml("Next action")}</strong><span>${escapeHtml(action)}</span></div>`}</article>`;
    };
    const storageHtml = volumes.length ? volumes.map(storageVolumeCard).join("") : `<div class="empty-state compact"><strong>${uiTextHtml("Storage projection unavailable")}</strong><span>${uiTextHtml("C:/ and D:/ figures are not available in this snapshot.")}</span></div>`;
    const lowSpaceVolumes = volumes.filter((volume) => volume?.lowSpace === true);
    const storageWarning = lowSpaceVolumes.length ? `<div class="callout callout--warning dashboard-storage-warning" role="alert"><strong>${uiTextHtml("Low-space warning")}</strong><span>${escapeHtml(lowSpaceVolumes.map((volume) => String(volume?.id || "").toLowerCase() === "c" ? "C:" : String(volume?.id || "").toLowerCase() === "d" ? "D:" : "volume").join(", "))} ${uiTextHtml("review storage before new writes.")}</span></div>` : "";
    const workflowLibraryHtml = workflowLibraryState(source.workflowLibrary);
    return `<section class="dashboard-page" aria-labelledby="dashboard-title"><section class="dashboard-hero"><div><span class="eyebrow">CONTROL PLANE</span><h1 id="dashboard-title">Dashboard</h1><p>${escapeHtml(readinessNote)}</p></div>${statusPill(readiness, formatStatus(readiness))}</section>${workflowLibraryHtml}<section class="dashboard-metric-grid" aria-label="Readiness metrics">${metricStatic("Hub API", formatStatus(readiness), "Static readiness snapshot")}${metricStatic("Module plan", formatStatus(planStatus), "Preflight is read-only; install/download is not_run")}${metricSnapshot("GPU", gpuValue, gpuDetail)}${metricStatic("Ổ đĩa", diskValue, "Dung lượng trống")}${metricSnapshot("Jobs hoạt động", String(activeJobs), `${jobs.length} bản ghi trong queue`)}</section><section class="dashboard-storage card" aria-labelledby="dashboard-storage-title" data-storage-status="${escapeHtml(storageStatus)}" data-execution="${escapeHtml(storageExecution)}"><div class="card-title-row"><div><span class="eyebrow" data-i18n="STORAGE PROJECTION">${uiTextHtml("STORAGE PROJECTION")}</span><h2 id="dashboard-storage-title" data-i18n="C:/ & D:/ dung lượng">${uiTextHtml("C:/ & D:/ dung lượng")}</h2><p class="small"><span data-i18n="Server-owned, allowlisted volume snapshot">${uiTextHtml("Server-owned, allowlisted volume snapshot")}</span> · <span data-i18n="execution:">${uiTextHtml("execution:")}</span> ${escapeHtml(storageExecution)}</p></div>${statusPill(storageStatus, formatStatus(storageStatus))}</div><div class="dashboard-storage-grid">${storageHtml}</div>${storageWarning}</section>${mediaEvidencePanel(source, "compact")}<section class="job-recovery-card card" aria-labelledby="dashboard-recovery-title" data-recovery-source="${escapeHtml(jobRecovery.source)}" data-recovery-status="${escapeHtml(jobRecovery.status)}"><div class="card-title-row"><div><span class="eyebrow" data-i18n="JOB RECOVERY">${uiTextHtml("JOB RECOVERY")}</span><h2 id="dashboard-recovery-title" data-i18n="Recovery attention">${uiTextHtml("Recovery attention")}</h2><p>${escapeHtml(jobRecovery.reason)}</p></div>${statusPill(jobRecovery.status, readinessStatusLabel(jobRecovery.status))}</div><div class="job-recovery-counts" aria-label="Job recovery counts"><div data-i18n-container="Active" data-recovery-count="active"><span>Active</span><strong>${escapeHtml(String(jobRecovery.counts.active))}</strong></div><div data-i18n-container="Attention" data-recovery-count="attention"><span>Attention</span><strong>${escapeHtml(String(jobRecovery.counts.attention))}</strong></div><div data-i18n-container="Interrupted" data-recovery-count="interrupted"><span>Interrupted</span><strong>${escapeHtml(String(jobRecovery.counts.interrupted))}</strong></div><div data-i18n-container="Recoverable" data-recovery-count="recoverable"><span>Recoverable</span><strong>${escapeHtml(String(jobRecovery.counts.recoverable))}</strong></div></div><div class="job-recovery-guidance"><span data-i18n="Next action">${uiTextHtml("Next action")}</span><p>${escapeHtml(jobRecovery.nextAction)}</p></div><button class="button button--compact" type="button" data-route="jobs" data-recovery-focus="${jobRecovery.counts.attention ? "attention" : "all"}" aria-controls="jobs-page" data-i18n="Open focused Jobs">${uiTextHtml("Open focused Jobs")}</button></section><section class="dashboard-main-grid"><section class="dashboard-primary card" aria-labelledby="dashboard-modules-title"><div class="card-title-row"><div><span class="eyebrow" data-i18n="MODULE HEALTH">${uiTextHtml("MODULE HEALTH")}</span><h2 id="dashboard-modules-title" data-i18n="Tình trạng module">${uiTextHtml("Tình trạng module")}</h2></div><span class="tag">${escapeHtml(String(modules.length))} module</span></div><div class="dashboard-module-list">${moduleRows}</div><details><summary data-i18n="Reason & next action">${uiTextHtml("Reason & next action")}</summary><div class="dashboard-module-list">${moduleEvidenceRows}</div></details><div class="callout" data-module-plan-status="${escapeHtml(planStatus)}"><strong data-i18n="Module preflight">${uiTextHtml("Module preflight")}</strong><p>${escapeHtml(planReason)}</p><p>${escapeHtml(planAction)}</p></div></section><aside class="dashboard-aside"><section class="card" aria-labelledby="dashboard-attention-title"><div class="card-title-row"><h2 id="dashboard-attention-title">Cần chú ý</h2><span class="tag">Tối đa 4</span></div><div class="dashboard-attention-list">${attentionRows}</div></section><section class="card" aria-labelledby="dashboard-quick-title"><div class="card-title-row"><h2 id="dashboard-quick-title">Điều hướng nhanh</h2></div><div class="dashboard-quick-actions">${quickActions}${readinessAction}</div></section><section class="card" aria-labelledby="dashboard-workflow-title"><div class="card-title-row"><h2 id="dashboard-workflow-title">Workflow ngắn</h2></div><ol class="dashboard-module-list">${workflowSteps}</ol></section></aside></section></section>`;
  };
}
