/*
 * Execute the shipped app_update.js module with a deliberately tiny DOM.
 * This is a contract harness, not a browser substitute: it verifies that the
 * production module wires the shared coordinator and exposes the legacy
 * consent state correctly without installing jsdom or claiming native UX.
 */

const productionMessageHistory = [];

class FakeNode {
  constructor(tagName) {
    this.tagName = String(tagName || "div").toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.className = "";
    this.id = "";
    this.type = "";
    this._textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.style = {};
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(value) {
    this._textContent = String(value ?? "");
    if (this.dataset?.updateMessage === "true") productionMessageHistory.push(this._textContent);
  }

  append(...nodes) {
    nodes.flat().forEach((node) => {
      if (!node || typeof node !== "object") return;
      node.parentNode = this;
      this.children.push(node);
    });
  }

  prepend(...nodes) {
    const valid = nodes.flat().filter((node) => node && typeof node === "object");
    valid.forEach((node) => { node.parentNode = this; });
    this.children.unshift(...valid);
  }

  insertBefore(node, reference) {
    if (!node || typeof node !== "object") return;
    node.parentNode = this;
    const index = this.children.indexOf(reference);
    if (index < 0) this.children.push(node);
    else this.children.splice(index, 0, node);
  }

  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
    this.parentNode = null;
  }

  setAttribute(name, value) {
    const key = String(name);
    const text = String(value);
    this.attributes[key] = text;
    if (key === "id") this.id = text;
    if (key === "class") this.className = text;
  }

  addEventListener(name, callback) {
    const values = this.listeners.get(name) || [];
    values.push(callback);
    this.listeners.set(name, values);
  }

  focus() {}

  _matches(selector) {
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return this.className.split(/\s+/).includes(selector.slice(1));
    const data = selector.match(/^\[data-([a-z0-9-]+)\]$/i);
    if (data) {
      const key = data[1].split("-").map((part, index) => index ? `${part[0].toUpperCase()}${part.slice(1)}` : part).join("");
      return Object.prototype.hasOwnProperty.call(this.dataset, key);
    }
    return false;
  }

  querySelectorAll(selector) {
    const found = [];
    const visit = (node) => {
      node.children.forEach((child) => {
        if (child._matches(selector)) found.push(child);
        visit(child);
      });
    };
    visit(this);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  closest(selector) {
    let current = this;
    while (current) {
      if (current._matches(selector)) return current;
      current = current.parentNode;
    }
    return null;
  }
}

class FakeTimers {
  constructor() {
    this.nextId = 0;
    this.timers = new Map();
  }

  set(callback, delay) {
    const handle = Object.freeze({ id: ++this.nextId });
    this.timers.set(handle, { callback, delay });
    return handle;
  }

  clear(handle) {
    this.timers.delete(handle);
  }

  async runNext() {
    const entry = this.timers.entries().next();
    if (entry.done) return false;
    const [handle, timer] = entry.value;
    this.timers.delete(handle);
    await timer.callback();
    return true;
  }
}

const timers = new FakeTimers();
const moduleView = new FakeNode("main");
moduleView.id = "module-view";
const dashboard = new FakeNode("section");
dashboard.className = "dashboard-page";
const hero = new FakeNode("section");
hero.className = "dashboard-hero";
dashboard.append(hero);
moduleView.append(dashboard);
const head = new FakeNode("head");
const body = new FakeNode("body");
const documentListeners = new Map();

globalThis.document = {
  readyState: "complete",
  head,
  body,
  createElement: (tag) => new FakeNode(tag),
  addEventListener: (name, callback) => {
    const values = documentListeners.get(name) || [];
    values.push(callback);
    documentListeners.set(name, values);
  },
  querySelector: (selector) => {
    if (selector === "#module-view") return moduleView;
    if (selector === "#module-view .dashboard-page") return dashboard;
    if (selector === "#local-ai-hub-app-update-style") return head.querySelector("#local-ai-hub-app-update-style");
    return moduleView.querySelector(selector) || head.querySelector(selector) || body.querySelector(selector);
  },
};

globalThis.MutationObserver = class {
  constructor() {}
  observe() {}
  disconnect() {}
};

const storage = new Map();
storage.getItem = storage.get.bind(storage);
storage.setItem = storage.set.bind(storage);
let statusRequests = 0;
let applyCalls = 0;
let automaticRestartCalls = 0;
const countdownValues = [];
const legacyStaged = {
  status: "staged",
  phase: "staged",
  bootstrap_pending: true,
  bootstrap_status: "staged",
  bootstrap_restart_authorized: false,
  additional_confirmation_required: true,
  can_restart: true,
  requires_restart: true,
  current_payload_id: "main-bbbbbbbbbbbb",
  candidate_payload_id: "main-aaaaaaaaaaaa",
  bootstrap_transaction_id: "txn-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  bootstrap_payload_id: "main-aaaaaaaaaaaa",
  source_commit: "a".repeat(40),
};
const authorizedStaged = {
  ...legacyStaged,
  bootstrap_restart_authorized: true,
  additional_confirmation_required: false,
};

globalThis.fetch = async (path, options = {}) => {
  if (String(path) === "/api/app-update/status") {
    statusRequests += 1;
    return { ok: true, status: 200, json: async () => statusRequests === 1 ? legacyStaged : authorizedStaged };
  }
  if (String(path) === "/api/app-update/prepare") {
    applyCalls += 1;
    return { ok: true, status: 200, json: async () => legacyStaged };
  }
  throw new Error(`unexpected_fetch:${String(path)}:${String(options.method || "GET")}`);
};

globalThis.window = {
  sessionStorage: storage,
  setTimeout: timers.set.bind(timers),
  clearTimeout: timers.clear.bind(timers),
  pywebview: {
    api: {
      restart_after_update: async () => {
        automaticRestartCalls += 1;
        return { status: "completed" };
      },
    },
  },
};

const flush = async () => {
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
};

await import("../src/ui/app_update.js?production-harness=1");
await flush();
const card = dashboard.querySelector("[data-app-update-card]");
if (!card) throw new Error("production app_update.js did not mount its card");
const restartButton = card.querySelector("[data-app-update-restart]");
const initialMessage = card.querySelector("[data-update-message]").textContent;
const initialRestartLabel = restartButton.textContent;
const initialRestartVisible = !restartButton.hidden;
if (!initialRestartVisible) throw new Error("legacy staged state hid the required confirmation action");
if (!initialMessage.includes("xác nhận lần khởi động lại")) throw new Error("legacy consent explanation was not rendered");
if (!initialRestartLabel.includes("Xác nhận khởi động lại")) throw new Error("legacy confirmation label was not rendered");

for (let step = 0; step < 30 && automaticRestartCalls === 0; step += 1) {
  if (!(await timers.runNext())) throw new Error(`production coordinator stopped at step ${step}`);
  await flush();
  const message = card.querySelector("[data-update-message]").textContent;
  const match = message.match(/sau (\d+)s/);
  if (match) countdownValues.push(Number(match[1]));
}
for (const message of productionMessageHistory) {
  const match = message.match(/sau (\d+)s/);
  if (match) countdownValues.push(Number(match[1]));
}
if (statusRequests < 2) throw new Error("production coordinator did not poll the shipped status route");
if (automaticRestartCalls !== 1) throw new Error(`production automatic restart count=${automaticRestartCalls}`);
if (applyCalls !== 0) throw new Error(`production harness unexpectedly clicked Update ${applyCalls} times`);
if (!countdownValues.includes(5) || !countdownValues.includes(0)) throw new Error(`production countdown was not visible: ${countdownValues.join(",")}`);

process.stdout.write(JSON.stringify({
  productionModule: true,
  statusRequests,
  initialRestartVisible,
  initialRestartLabel,
  initialMessage,
  countdownValues,
  automaticRestartCalls,
  applyCalls,
}));
