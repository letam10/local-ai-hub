import { createBootstrapFlow, isBootstrapPending, bootstrapIdentity } from "../src/ui/app_update_bootstrap.js";

const timers = [];
const schedule = (callback, delay) => { timers.push({ callback, delay }); return timers.length; };
const cancel = (handle) => { if (handle) timers[handle - 1] = null; };
const runNext = async () => {
  const item = timers.shift();
  if (!item) return false;
  if (item) await item.callback();
  return true;
};
const storage = new Map();
storage.getItem = storage.get.bind(storage);
storage.setItem = storage.set.bind(storage);
const card = { message: "", renderCount: 0 };
const rendered = [];
const render = (value) => {
  card.renderCount += 1;
  card.message = value?.bootstrap_count ? `countdown:${value.bootstrap_count}` : String(value?.bootstrap_status || "");
  rendered.push({ status: value?.bootstrap_status, message: card.message });
};
const statuses = [
  { bootstrap_pending: true, bootstrap_status: "pending", bootstrap_transaction_id: "txn-a", bootstrap_payload_id: "main-a" },
  { bootstrap_pending: true, bootstrap_status: "staged", bootstrap_restart_authorized: true, bootstrap_transaction_id: "txn-a", bootstrap_payload_id: "main-a" },
];
let statusRequests = 0;
let restartCalls = 0;
const flow = createBootstrapFlow({
  schedule, cancel, storage, maxPolls: 2, countdownSeconds: 2,
  getStatus: async () => { statusRequests += 1; return statuses.shift() || statuses.at(-1); },
  restart: async (_value, options) => { restartCalls += 1; if (!options.automatic) throw new Error("unexpected_manual_restart"); },
});

if (!isBootstrapPending(statuses[0])) throw new Error("pending state was not recognized");
if (bootstrapIdentity(statuses[0]) !== "txn-a:main-a:") throw new Error("identity binding missing");
await flow.observe({ bootstrap_pending: true, bootstrap_status: "pending", bootstrap_transaction_id: "txn-a", bootstrap_payload_id: "main-a" }, render);
if (statusRequests !== 0) throw new Error("status was requested before the bounded timer");
await runNext();
if (statusRequests !== 1) throw new Error("pending poll did not run");
await runNext();
await runNext();
await runNext();
await runNext();
if (restartCalls !== 1) throw new Error(`automatic restart count=${restartCalls}`);
if (card.renderCount < 3 || !rendered.some((item) => item.status === "staged")) throw new Error("staged state was not rendered");

const deferredFlow = createBootstrapFlow({ schedule, cancel, storage: new Map(), countdownSeconds: 2, restart: async () => { throw new Error("restart_should_not_run"); } });
let deferredRender = null;
const staged = { bootstrap_pending: true, bootstrap_status: "staged", bootstrap_restart_authorized: true, bootstrap_transaction_id: "txn-defer", bootstrap_payload_id: "main-defer" };
await deferredFlow.observe(staged, (value) => { deferredRender = value; });
deferredFlow.defer(staged, (value) => { deferredRender = value; });
await runNext();
if (deferredRender?.bootstrap_restart_authorized !== false) throw new Error("defer did not cancel consented restart");

process.stdout.write(JSON.stringify({ statusRequests, restartCalls, rendered: card.renderCount, deferred: deferredRender?.bootstrap_deferred === true }));
