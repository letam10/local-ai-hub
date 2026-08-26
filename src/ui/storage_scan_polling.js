/*
 * Small, testable polling coordinator for the Models & Storage page.
 * It owns no DOM and never performs storage work; it only schedules lightweight
 * loopback snapshots while an already-running server worker is active.
 */

export const STORAGE_SCAN_POLL_INTERVAL_MS = 750;
export const STORAGE_SCAN_POLL_INITIAL_DELAY_MS = 250;
export const STORAGE_SCAN_POLL_CEILING_MS = 45 * 60 * 1000;
export const STORAGE_SCAN_ACTIVE_STATES = Object.freeze(["running", "cancelling"]);
export const STORAGE_SCAN_TERMINAL_STATES = Object.freeze(["cancelled", "partial", "completed", "unavailable"]);

const activeState = (value) => STORAGE_SCAN_ACTIVE_STATES.includes(String(value || ""));

export function createStorageScanPoller({
  getSnapshot,
  isRouteActive,
  onSnapshot,
  onCeiling,
  onError,
  schedule = (callback, delay) => globalThis.setTimeout(callback, delay),
  now = () => Date.now(),
  intervalMs = STORAGE_SCAN_POLL_INTERVAL_MS,
  initialDelayMs = STORAGE_SCAN_POLL_INITIAL_DELAY_MS,
  ceilingMs = STORAGE_SCAN_POLL_CEILING_MS,
} = {}) {
  if (typeof getSnapshot !== "function" || typeof isRouteActive !== "function" || typeof onSnapshot !== "function") {
    throw new TypeError("Storage scan poller requires snapshot, route and snapshot callbacks.");
  }
  let generation = 0;

  const stop = () => { generation += 1; };
  const start = (scanId = "") => {
    const token = ++generation;
    const startedAt = now();
    let lastStatus = "running";

    const poll = async () => {
      if (token !== generation || !isRouteActive()) return;
      if (now() - startedAt >= ceilingMs) {
        if (activeState(lastStatus) && typeof onCeiling === "function") {
          onCeiling({
            scan: {
              scan_id: scanId,
              status: lastStatus,
              polling_limited: true,
              polling_message: "Quét vẫn đang chạy nền; bấm Theo dõi tiếp để cập nhật.",
            },
          });
        }
        return;
      }
      try {
        const result = await getSnapshot();
        const scan = result?.scan && typeof result.scan === "object" ? result.scan : {};
        if (scanId && scan.scan_id && scan.scan_id !== scanId) return;
        if (token !== generation || !isRouteActive()) return;
        lastStatus = String(scan.status || "");
        onSnapshot(result ? { ...result, scan: { ...scan, polling_limited: false } } : result);
        if (activeState(lastStatus)) schedule(poll, intervalMs);
      } catch (error) {
        if (typeof onError === "function") onError(error);
        if (token === generation && isRouteActive() && activeState(lastStatus)) schedule(poll, intervalMs);
      }
    };
    schedule(poll, initialDelayMs);
    return token;
  };

  return Object.freeze({ start, stop });
}
