/* Pure bounded bootstrap hand-off state used by the installed updater card. */

export const BOOTSTRAP_PHASES = Object.freeze(["pending", "downloading", "verifying", "staged", "restarting"]);

export const isBootstrapPending = (value) => value?.bootstrap_pending === true && BOOTSTRAP_PHASES.includes(String(value?.bootstrap_status || ""));

export const bootstrapIdentity = (value) => [
  value?.bootstrap_transaction_id || value?.transaction_id || "",
  value?.bootstrap_payload_id || value?.candidate_payload_id || value?.payload_id || "",
  value?.latest_build || value?.source_commit || "",
].join(":");

export function createBootstrapFlow({ getStatus, restart, schedule = globalThis.setTimeout, cancel = globalThis.clearTimeout, storage = null, maxPolls = 20, countdownSeconds = 5 } = {}) {
  let pollTimer = null;
  let countdownTimer = null;
  let pollCount = 0;
  let countdownKey = "";
  let restartInFlight = false;
  const getStored = (key) => {
    try { return storage?.getItem(`local-ai-hub-bootstrap:${key}`) || ""; } catch { return ""; }
  };
  const setStored = (key, value) => {
    try { if (storage && key) storage.setItem(`local-ai-hub-bootstrap:${key}`, value); } catch { /* advisory */ }
  };
  const stopCountdown = () => {
    if (countdownTimer !== null) cancel(countdownTimer);
    countdownTimer = null;
    countdownKey = "";
  };
  const defer = (value, render) => {
    const key = bootstrapIdentity(value);
    stopCountdown();
    setStored(key, "deferred");
    const next = { ...(value || {}), bootstrap_restart_authorized: false, bootstrap_deferred: true };
    if (typeof render === "function") render(next);
    return next;
  };
  const startCountdown = (value, render) => {
    const key = bootstrapIdentity(value);
    if (!key || value?.bootstrap_restart_authorized !== true || getStored(key) === "deferred" || getStored(key) === "attempted") return;
    if (countdownKey === key && countdownTimer !== null) return;
    countdownKey = key;
    let remaining = Math.max(0, Number(countdownSeconds) || 0);
    const tick = () => {
      if (countdownKey !== key) return;
      if (remaining <= 0) {
        countdownTimer = null;
        setStored(key, "attempted");
        if (!restartInFlight && typeof restart === "function") {
          restartInFlight = true;
          Promise.resolve(restart(value, { automatic: true })).finally(() => { restartInFlight = false; });
        }
        return;
      }
      if (typeof render === "function") render({ ...(value || {}), bootstrap_countdown: remaining });
      remaining -= 1;
      countdownTimer = schedule(tick, 1000);
    };
    tick();
  };
  const observe = async (value, render) => {
    if (typeof render === "function") render(value);
    if (!isBootstrapPending(value)) {
      if (pollTimer !== null) cancel(pollTimer);
      pollTimer = null;
      pollCount = 0;
      stopCountdown();
      return value;
    }
    if (value?.bootstrap_status === "staged") startCountdown(value, render);
    if (pollTimer === null && pollCount < maxPolls && typeof getStatus === "function") {
      pollTimer = schedule(async () => {
        pollTimer = null;
        pollCount += 1;
        try { await observe(await getStatus(), render); } catch { /* durable marker remains visible */ }
      }, 1500);
    }
    return value;
  };
  return Object.freeze({ observe, defer, stopCountdown, get state() { return { pollCount, countdownKey, restartInFlight }; } });
}
