/* Shared bounded bootstrap coordinator used by the shipped updater card. */

export const BOOTSTRAP_PHASES = Object.freeze(["pending", "downloading", "verifying", "staged", "restarting"]);

export const isBootstrapPending = (value) => value?.bootstrap_pending === true && BOOTSTRAP_PHASES.includes(String(value?.bootstrap_status || ""));

export const bootstrapIdentity = (value) => [
  value?.bootstrap_transaction_id || value?.transaction_id || "",
  value?.bootstrap_payload_id || value?.candidate_payload_id || value?.payload_id || "",
  value?.latest_build || value?.source_commit || "",
].join(":");

export function createBootstrapFlow({
  getStatus,
  restart,
  schedule = globalThis.setTimeout,
  cancel = globalThis.clearTimeout,
  storage = null,
  maxPolls = null,
  countdownSeconds = 5,
  pollIntervalMs = 1500,
  pollBackoffMaxMs = 15000,
} = {}) {
  // ``maxPolls`` remains accepted for callers from the earlier harness, but
  // is intentionally ignored: an active durable transaction must not stop
  // being observed at an arbitrary 20-request cutoff.
  void maxPolls;
  let pollTimer = null;
  let pollInFlight = null;
  let pollRequestId = 0;
  let pollResume = null;
  let generation = 0;
  let pollCount = 0;
  let pollDelay = Math.max(500, Number(pollIntervalMs) || 1500);
  let countdownTimer = null;
  let countdownKey = "";
  let restartInFlight = false;
  let lastValue = null;

  const getStored = (key) => {
    try { return storage?.getItem(`local-ai-hub-bootstrap:${key}`) || ""; } catch { return ""; }
  };
  const setStored = (key, value) => {
    try { if (storage && key) storage.setItem(`local-ai-hub-bootstrap:${key}`, value); } catch { /* advisory only */ }
  };
  const cancelTimer = (timer) => {
    if (timer !== null && timer !== undefined) {
      try { cancel(timer); } catch { /* a torn-down surface owns no timer */ }
    }
  };
  const stopCountdown = () => {
    cancelTimer(countdownTimer);
    countdownTimer = null;
    countdownKey = "";
  };
  const stopPolling = () => {
    generation += 1;
    cancelTimer(pollTimer);
    pollTimer = null;
    pollResume = null;
    pollCount = 0;
    pollDelay = Math.max(500, Number(pollIntervalMs) || 1500);
  };

  const startCountdown = (value, render, token) => {
    const key = bootstrapIdentity(value);
    if (!key || token !== generation || value?.bootstrap_restart_authorized !== true || getStored(key) === "deferred" || getStored(key) === "attempted") return;
    if (countdownKey === key && countdownTimer !== null) return;
    if (countdownKey && countdownKey !== key) stopCountdown();
    countdownKey = key;
    let remaining = Math.max(0, Number(countdownSeconds) || 0);
    const tick = () => {
      if (token !== generation || countdownKey !== key || !isBootstrapPending(lastValue) || bootstrapIdentity(lastValue) !== key) return;
      const visible = { ...(lastValue || value), bootstrap_countdown: remaining, bootstrap_count: remaining };
      if (typeof render === "function") render(visible);
      if (remaining <= 0) {
        countdownTimer = null;
        setStored(key, "attempted");
        if (!restartInFlight && typeof restart === "function") {
          restartInFlight = true;
          Promise.resolve().then(() => restart(visible, { automatic: true })).catch(() => {}).finally(() => { restartInFlight = false; });
        }
        return;
      }
      remaining -= 1;
      countdownTimer = schedule(tick, 1000);
    };
    tick();
  };

  const schedulePoll = (render, token) => {
    if (token !== generation || pollTimer !== null || !isBootstrapPending(lastValue) || typeof getStatus !== "function") return;
    if (pollInFlight !== null) {
      // A request cannot be cancelled by a torn-down/re-entered view.  Keep a
      // generation-owned continuation so its completion cannot strand the new
      // observer, while still allowing only one request at a time.
      if (pollInFlight.generation !== token) pollResume = { generation: token, render };
      return;
    }
    const delay = pollDelay;
    const request = { id: ++pollRequestId, generation: token, render };
    let timer = null;
    const run = async () => {
      if (pollTimer === timer) pollTimer = null;
      if (token !== generation || !isBootstrapPending(lastValue)) return;
      // A stale timer callback must not replace or clear a newer request.
      if (pollInFlight !== null) return;
      pollInFlight = request;
      pollCount += 1;
      try {
        const value = await getStatus();
        if (token !== generation) return;
        pollDelay = Math.max(500, Number(pollIntervalMs) || 1500);
        await observe(value, render);
      } catch {
        if (token === generation && isBootstrapPending(lastValue)) {
          pollDelay = Math.min(Math.max(pollDelay * 2, 2000), Math.max(2000, Number(pollBackoffMaxMs) || 15000));
          if (typeof render === "function") render({ ...(lastValue || {}), bootstrap_poll_error: true, bootstrap_poll_retry_ms: pollDelay });
        }
      } finally {
        if (pollInFlight !== request) return;
        pollInFlight = null;
        if (token === generation && isBootstrapPending(lastValue)) {
          schedulePoll(render, token);
        } else if (pollResume?.generation === generation && isBootstrapPending(lastValue)) {
          const resume = pollResume;
          pollResume = null;
          schedulePoll(resume.render, resume.generation);
        } else {
          pollResume = null;
        }
      }
    };
    timer = schedule(run, delay);
    pollTimer = timer;
  };

  async function observe(value, render) {
    const next = value && typeof value === "object" ? value : { bootstrap_pending: false, bootstrap_status: "blocked" };
    const key = bootstrapIdentity(next);
    if (countdownKey && (
      countdownKey !== key
      || next.bootstrap_status !== "staged"
      || next.bootstrap_restart_authorized !== true
    )) stopCountdown();
    lastValue = next;
    if (typeof render === "function") render(next);
    if (!isBootstrapPending(next)) {
      stopPolling();
      stopCountdown();
      return next;
    }
    const token = generation;
    if (next.bootstrap_status === "staged") startCountdown(next, render, token);
    schedulePoll(render, token);
    return next;
  }

  const defer = (value, render) => {
    const key = bootstrapIdentity(value);
    stopCountdown();
    setStored(key, "deferred");
    const next = { ...(value || {}), bootstrap_restart_authorized: false, bootstrap_deferred: true };
    lastValue = next;
    if (typeof render === "function") render(next);
    schedulePoll(render, generation);
    return next;
  };

  return Object.freeze({
    observe,
    defer,
    stop() { stopPolling(); stopCountdown(); lastValue = null; },
    stopCountdown,
    get state() { return { pollCount, pollInFlight: pollInFlight !== null, countdownKey, restartInFlight, generation, lastValue }; },
  });
}
