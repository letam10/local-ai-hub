import { createBootstrapFlow, isBootstrapPending, bootstrapIdentity } from "../src/ui/app_update_bootstrap.js";

class FakeClock {
  constructor() {
    this.nextId = 0;
    this.timers = new Map();
  }

  schedule(callback, delay) {
    const handle = Object.freeze({ id: ++this.nextId });
    this.timers.set(handle, { callback, delay });
    return handle;
  }

  cancel(handle) {
    if (handle !== null && handle !== undefined) this.timers.delete(handle);
  }

  async runNext() {
    const entry = this.timers.entries().next();
    if (entry.done) return false;
    const [handle, timer] = entry.value;
    this.timers.delete(handle);
    await timer.callback();
    return true;
  }

  get size() {
    return this.timers.size;
  }
}

const makeStorage = () => {
  const storage = new Map();
  storage.getItem = storage.get.bind(storage);
  storage.setItem = storage.set.bind(storage);
  return storage;
};

const makeRenderLog = () => {
  const values = [];
  const render = (value) => {
    values.push(value);
  };
  return { values, render };
};

const identity = (transaction, payload, source = "a".repeat(40)) => ({
  bootstrap_pending: true,
  bootstrap_status: "staged",
  bootstrap_restart_authorized: true,
  bootstrap_transaction_id: transaction,
  bootstrap_payload_id: payload,
  source_commit: source,
});

if (!isBootstrapPending({ bootstrap_pending: true, bootstrap_status: "pending" })) throw new Error("pending state was not recognized");
if (bootstrapIdentity({ bootstrap_transaction_id: "txn-a", bootstrap_payload_id: "main-a", source_commit: "a" }) !== "txn-a:main-a:a") throw new Error("identity binding missing");

// A durable transaction may remain pending beyond the old arbitrary cutoff.
// The first three requests fail transiently, then the same transaction reaches
// staged and performs exactly one authorized automatic restart.
const clock = new FakeClock();
const storage = makeStorage();
const log = makeRenderLog();
const staged = identity("txn-a", "main-a");
let pollRequests = 0;
let restartCalls = 0;
let transientErrors = 0;
const flow = createBootstrapFlow({
  schedule: clock.schedule.bind(clock),
  cancel: clock.cancel.bind(clock),
  storage,
  maxPolls: 1,
  countdownSeconds: 2,
  pollIntervalMs: 10,
  pollBackoffMaxMs: 40,
  getStatus: async () => {
    pollRequests += 1;
    if (pollRequests <= 3) {
      transientErrors += 1;
      throw new Error("transient");
    }
    if (pollRequests <= 64) return { ...staged, bootstrap_status: "downloading", bootstrap_restart_authorized: false };
    return staged;
  },
  restart: async (_value, options) => {
    restartCalls += 1;
    if (options?.automatic !== true) throw new Error("unexpected_manual_restart");
  },
});

await flow.observe({ ...staged, bootstrap_status: "pending", bootstrap_restart_authorized: false }, log.render);
for (let step = 0; step < 160 && restartCalls === 0; step += 1) {
  if (!(await clock.runNext())) throw new Error(`durable poll stopped at step ${step}`);
}
if (transientErrors !== 3) throw new Error(`transient error count=${transientErrors}`);
if (pollRequests <= 60) throw new Error(`poll cutoff remained active at ${pollRequests}`);
if (restartCalls !== 1) throw new Error(`automatic restart count=${restartCalls}`);
if (!log.values.some((value) => value.bootstrap_status === "staged" && value.bootstrap_countdown === 2)) throw new Error("late staged state was not rendered");
if (!log.values.some((value) => value.bootstrap_countdown === 1) || !log.values.some((value) => value.bootstrap_countdown === 0)) throw new Error("visible countdown was incomplete");
if (!log.values.some((value) => value.bootstrap_poll_error === true)) throw new Error("transient retry was not visible");

// Defer is a durable user choice: it cancels the countdown and never turns a
// later repeated staged snapshot back into an automatic restart.
const deferredClock = new FakeClock();
const deferredStorage = makeStorage();
let deferredRestartCalls = 0;
let deferredValue = null;
let deferWasVisible = false;
const deferredFlow = createBootstrapFlow({
  schedule: deferredClock.schedule.bind(deferredClock),
  cancel: deferredClock.cancel.bind(deferredClock),
  storage: deferredStorage,
  countdownSeconds: 2,
  getStatus: async () => staged,
  restart: async () => { deferredRestartCalls += 1; },
});
await deferredFlow.observe(staged, (value) => { deferredValue = value; });
deferredFlow.defer(staged, (value) => { deferredValue = value; deferWasVisible = value?.bootstrap_deferred === true; });
if (deferredValue?.bootstrap_restart_authorized !== false || deferredValue?.bootstrap_deferred !== true) throw new Error("defer state was not visible");
for (let step = 0; step < 5; step += 1) await deferredClock.runNext();
if (deferredRestartCalls !== 0) throw new Error("defer still allowed an automatic restart");
if (deferredFlow.state.countdownKey !== "") throw new Error("defer did not cancel countdown");

// Terminal, blocked and identity changes cancel old timers.  Re-entry can
// observe a new transaction without reviving the old transaction's countdown.
for (const terminal of [
  { bootstrap_pending: false, bootstrap_status: "completed" },
  { bootstrap_pending: true, bootstrap_status: "blocked", bootstrap_transaction_id: "txn-a", bootstrap_payload_id: "main-a" },
]) {
  const terminalClock = new FakeClock();
  let terminalRestartCalls = 0;
  const terminalFlow = createBootstrapFlow({
    schedule: terminalClock.schedule.bind(terminalClock),
    cancel: terminalClock.cancel.bind(terminalClock),
    countdownSeconds: 2,
    restart: async () => { terminalRestartCalls += 1; },
  });
  await terminalFlow.observe(staged, () => {});
  await terminalFlow.observe(terminal, () => {});
  while (await terminalClock.runNext()) {}
  if (terminalRestartCalls !== 0 || terminalFlow.state.countdownKey !== "") throw new Error("terminal state revived a restart");
}

const identityClock = new FakeClock();
let identityRestartCalls = 0;
const identityFlow = createBootstrapFlow({
  schedule: identityClock.schedule.bind(identityClock),
  cancel: identityClock.cancel.bind(identityClock),
  countdownSeconds: 2,
  restart: async () => { identityRestartCalls += 1; },
});
await identityFlow.observe(staged, () => {});
await identityFlow.observe({ ...identity("txn-b", "main-b"), bootstrap_restart_authorized: false }, () => {});
while (await identityClock.runNext()) {}
if (identityRestartCalls !== 0 || identityFlow.state.countdownKey !== "") throw new Error("transaction change revived old countdown");

const revokedClock = new FakeClock();
let revokedRestartCalls = 0;
const revokedFlow = createBootstrapFlow({
  schedule: revokedClock.schedule.bind(revokedClock),
  cancel: revokedClock.cancel.bind(revokedClock),
  countdownSeconds: 1,
  restart: async () => { revokedRestartCalls += 1; },
});
await revokedFlow.observe(staged, () => {});
await revokedFlow.observe({ ...staged, bootstrap_restart_authorized: false }, () => {});
while (await revokedClock.runNext()) {}
if (revokedRestartCalls !== 0 || revokedFlow.state.countdownKey !== "") throw new Error("consent revocation did not cancel countdown");

// Duplicate renders/observations schedule one countdown and one automatic
// restart.  stop() models route leave; re-entry is a fresh snapshot.
const reentryClock = new FakeClock();
let reentryRestartCalls = 0;
let reentryStatus = staged;
const reentryFlow = createBootstrapFlow({
  schedule: reentryClock.schedule.bind(reentryClock),
  cancel: reentryClock.cancel.bind(reentryClock),
  countdownSeconds: 1,
  getStatus: async () => reentryStatus,
  restart: async () => { reentryRestartCalls += 1; },
});
await reentryFlow.observe(staged, () => {});
await reentryFlow.observe(staged, () => {});
await reentryFlow.observe(staged, () => {});
if (reentryClock.size !== 2) throw new Error(`duplicate render scheduled ${reentryClock.size} timers`);
for (let step = 0; step < 8 && reentryRestartCalls === 0; step += 1) {
  if (!(await reentryClock.runNext())) throw new Error("duplicate-render timer disappeared");
}
if (reentryRestartCalls !== 1) throw new Error(`duplicate automatic restart count=${reentryRestartCalls}`);
reentryFlow.stop();
reentryStatus = { ...identity("txn-reentry", "main-reentry"), bootstrap_restart_authorized: false };
await reentryFlow.observe(reentryStatus, () => {});
for (let step = 0; step < 4; step += 1) await reentryClock.runNext();
if (reentryRestartCalls !== 1) throw new Error("route re-entry revived prior restart");

// A staged snapshot without durable consent is visible but can never trigger
// the automatic restart path.
const noConsentClock = new FakeClock();
let noConsentRestartCalls = 0;
const noConsentFlow = createBootstrapFlow({
  schedule: noConsentClock.schedule.bind(noConsentClock),
  cancel: noConsentClock.cancel.bind(noConsentClock),
  countdownSeconds: 0,
  getStatus: async () => ({ ...staged, bootstrap_restart_authorized: false }),
  restart: async () => { noConsentRestartCalls += 1; },
});
await noConsentFlow.observe({ ...staged, bootstrap_restart_authorized: false }, () => {});
for (let step = 0; step < 4; step += 1) await noConsentClock.runNext();
if (noConsentRestartCalls !== 0) throw new Error("consent-less state restarted automatically");

process.stdout.write(JSON.stringify({
  pollRequests,
  transientErrors,
  restartCalls,
  deferredRestartCalls,
  revokedRestartCalls,
  deferred: deferWasVisible,
  reentryRestartCalls,
  noConsentRestartCalls,
  stagedCountdownVisible: log.values.filter((value) => Number.isFinite(value.bootstrap_countdown)).map((value) => value.bootstrap_countdown),
}));
