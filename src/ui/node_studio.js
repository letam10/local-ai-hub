import {
  cancelJob,
  escapeHtml,
  getDirtyNodes,
  getNodePreset,
  getNodePresets,
  getNodeRegistry,
  getNodeRun,
  runNodeGraph,
  uploadFile,
  validateNodeGraph,
} from "./api.js";

const LOCAL_PREFIX = "local-ai-hub-node-studio-v1";
const STAGE_WIDTH = 2600;
const STAGE_HEIGHT = 1800;
const scopePreset = { image: "image_draft", sam2: "sam2_segment", media: "media_encode", animesr: "animesr_pipeline" };
const categoryColor = { utility: "#6c8cff", image: "#cf7cff", vision: "#42c6a0", media: "#f1ad5f", video: "#f17c8e", annotation: "#8794ad" };

const clone = (value) => JSON.parse(JSON.stringify(value));
const uuid = () => `node_${globalThis.crypto?.randomUUID?.().replaceAll("-", "") || Math.random().toString(16).slice(2)}`;
const graphKey = (scope) => `${LOCAL_PREFIX}:${scope}`;
const asNumber = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;

function emptyGraph(scope) {
  return { schema_version: 1, id: `local-${scope}`, title: `Workflow ${scope}`, scope, nodes: [], edges: [], groups: [] };
}

function artifactFrom(value) {
  if (!value || typeof value !== "object") return null;
  if (value.id && value.url) return value;
  for (const item of Object.values(value)) {
    const found = artifactFrom(item);
    if (found) return found;
  }
  return null;
}

function nodePosition(node, output = false, index = 0) {
  const position = node.position || {};
  return {
    x: asNumber(position.x, 80) + (output ? 232 : 0),
    y: asNumber(position.y, 80) + 70 + index * 26,
  };
}

class NodeStudio {
  constructor(root, { showToast }) {
    this.root = root;
    this.scope = root.dataset.scope || "image";
    this.showToast = showToast;
    this.registry = new Map();
    this.encoderCapabilities = {};
    this.presets = [];
    this.graph = emptyGraph(this.scope);
    this.selected = new Set();
    this.dirty = new Set();
    this.nodeStates = new Map();
    this.pendingOutput = null;
    this.pan = { x: 0, y: 0 };
    this.zoom = 0.72;
    this.history = [];
    this.future = [];
    this.activeJobId = null;
    this.autoPreview = localStorage.getItem(`${graphKey(this.scope)}:auto`) === "true";
    this.draft = localStorage.getItem(`${graphKey(this.scope)}:draft`) !== "false";
    this.paletteSearch = "";
    this.commandOpen = false;
    this.autoTimer = null;
    this.pollTimer = null;
    this.drag = null;
    this.abort = new AbortController();
    this.eventsBound = false;
  }

  async initialize() {
    this.root.innerHTML = `<div class="node-studio-loading">Đang nạp Node Studio offline…</div>`;
    try {
      const [registry, presetList] = await Promise.all([getNodeRegistry(this.scope), getNodePresets()]);
      this.registry = new Map((registry.nodes || []).map((item) => [item.type, item]));
      this.encoderCapabilities = registry.encoder_capabilities || {};
      this.presets = (presetList.presets || []).filter((item) => item.scope === this.scope);
      const saved = this.readLocalGraph();
      if (saved) this.graph = saved;
      else await this.loadPreset(scopePreset[this.scope], { quiet: true });
      this.dirty = new Set(this.graph.nodes.map((item) => item.id));
      this.render();
      this.bindGlobalKeys();
    } catch (error) {
      this.root.innerHTML = `<div class="callout callout--warning">Không thể nạp Node Studio: ${escapeHtml(error.message)}</div>`;
    }
  }

  destroy() {
    this.abort.abort();
    if (this.autoTimer) clearTimeout(this.autoTimer);
    if (this.pollTimer) clearInterval(this.pollTimer);
  }

  readLocalGraph() {
    try {
      const value = JSON.parse(localStorage.getItem(graphKey(this.scope)) || "null");
      return value && value.schema_version === 1 && Array.isArray(value.nodes) && Array.isArray(value.edges) ? value : null;
    } catch { return null; }
  }

  persist() {
    localStorage.setItem(graphKey(this.scope), JSON.stringify(this.graph));
    localStorage.setItem(`${graphKey(this.scope)}:auto`, String(this.autoPreview));
    localStorage.setItem(`${graphKey(this.scope)}:draft`, String(this.draft));
  }

  snapshot() { return JSON.stringify(this.graph); }

  mutate(callback, changedIds = []) {
    const before = this.snapshot();
    callback();
    const after = this.snapshot();
    if (before === after) return;
    this.history.push(before);
    if (this.history.length > 60) this.history.shift();
    this.future = [];
    changedIds.forEach((id) => this.markDirty(id));
    this.persist();
    this.render();
    this.scheduleAutoPreview();
  }

  undo() {
    const previous = this.history.pop();
    if (!previous) return;
    this.future.push(this.snapshot());
    this.graph = JSON.parse(previous);
    this.dirty = new Set(this.graph.nodes.map((item) => item.id));
    this.persist();
    this.render();
  }

  redo() {
    const next = this.future.pop();
    if (!next) return;
    this.history.push(this.snapshot());
    this.graph = JSON.parse(next);
    this.dirty = new Set(this.graph.nodes.map((item) => item.id));
    this.persist();
    this.render();
  }

  markDirty(nodeId) {
    if (!nodeId) return;
    const local = new Set([nodeId]);
    const queue = [nodeId];
    while (queue.length) {
      const current = queue.shift();
      for (const edge of this.graph.edges) {
        if (edge.source.node === current && !local.has(edge.target.node)) {
          local.add(edge.target.node);
          queue.push(edge.target.node);
        }
      }
    }
    local.forEach((id) => this.dirty.add(id));
    // The API performs the same DAG propagation.  The local result is immediate;
    // this response keeps the UI aligned with the backend validator.
    getDirtyNodes(this.graph, [nodeId]).then((result) => {
      if (result.valid) result.dirty_nodes.forEach((id) => this.dirty.add(id));
    }).catch(() => {});
  }

  definition(node) { return this.registry.get(node.type); }
  selectedNode() { return this.graph.nodes.find((node) => this.selected.has(node.id)) || null; }

  visibleDefinitions() {
    const query = this.paletteSearch.trim().toLocaleLowerCase();
    return [...this.registry.values()].filter((item) => !query || `${item.title} ${item.description} ${item.category}`.toLocaleLowerCase().includes(query));
  }

  render() {
    if (!this.registry.size) return;
    const definitions = this.visibleDefinitions();
    const selected = this.selectedNode();
    this.root.innerHTML = `
      <section class="node-studio" aria-label="Node Studio ${escapeHtml(this.scope)}">
        <div class="node-toolbar">
          <div class="node-toolbar__group"><button class="button button--primary" type="button" data-node-action="run">Run Graph</button><button class="button" type="button" data-node-action="cancel" ${this.activeJobId ? "" : "disabled"}>Cancel</button><label class="node-toggle"><input type="checkbox" data-node-option="auto" ${this.autoPreview ? "checked" : ""} /> Auto Preview</label><label class="node-toggle"><input type="checkbox" data-node-option="draft" ${this.draft ? "checked" : ""} /> Draft ảnh</label></div>
          <div class="node-toolbar__group"><select class="node-preset" data-node-preset><option value="">Preset Hub…</option>${this.presets.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)}</option>`).join("")}</select><button class="button button--compact" type="button" data-node-action="save-local">Lưu local</button><button class="button button--compact" type="button" data-node-action="save-as">Save As</button><button class="button button--compact" type="button" data-node-action="export">Export JSON</button><button class="button button--compact" type="button" data-node-action="import">Import JSON</button></div>
        </div>
        <div class="node-studio__layout">
          <aside class="node-palette">
            <div class="node-palette__title"><strong>Palette</strong><button class="icon-button" type="button" title="Thêm node (Ctrl+K)" data-node-action="command">+</button></div>
            <input class="node-search" data-node-search value="${escapeHtml(this.paletteSearch)}" placeholder="Tìm / thêm node…" />
            <div class="node-palette__actions"><button class="button button--compact" type="button" data-node-add="comment">+ Comment</button><button class="button button--compact" type="button" data-node-add="group">+ Group</button></div>
            <div class="node-palette__list">${definitions.map((item) => `<button class="node-palette-item" type="button" data-node-add="${escapeHtml(item.type)}"><span style="--node-color:${categoryColor[item.category] || categoryColor.utility}"></span><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.category)} · ${escapeHtml(item.status)}</small></button>`).join("")}</div>
          </aside>
          <div class="node-canvas-shell">
            <div class="node-canvas-tools"><span>${escapeHtml(this.graph.title || "Untitled workflow")}</span><span>${this.graph.nodes.length} node · ${this.graph.edges.length} link</span><button class="button button--compact" type="button" data-node-action="reset-view">Reset view</button></div>
            <div class="node-canvas" data-node-canvas tabindex="0"><div class="node-stage" style="width:${STAGE_WIDTH}px;height:${STAGE_HEIGHT}px;transform:translate(${this.pan.x}px,${this.pan.y}px) scale(${this.zoom})">${this.renderEdges()}${this.graph.nodes.map((node) => this.renderNode(node)).join("")}</div></div>
            <div class="node-minimap">${this.graph.nodes.map((node) => `<i class="node-minimap__dot" style="left:${Math.max(2, Math.min(94, asNumber(node.position?.x, 0) / STAGE_WIDTH * 100))}%;top:${Math.max(2, Math.min(94, asNumber(node.position?.y, 0) / STAGE_HEIGHT * 100))}%;background:${categoryColor[this.definition(node)?.category] || categoryColor.utility}"></i>`).join("")}</div>
          </div>
          <aside class="node-inspector">${this.renderInspector(selected)}</aside>
        </div>
        ${this.commandOpen ? this.renderCommandMenu() : ""}
        <input class="node-import-input" type="file" accept="application/json,.json" hidden data-node-import />
      </section>`;
    this.bindEvents();
  }

  renderEdges() {
    const nodes = new Map(this.graph.nodes.map((node) => [node.id, node]));
    return `<svg class="node-edges" viewBox="0 0 ${STAGE_WIDTH} ${STAGE_HEIGHT}" aria-hidden="true">${this.graph.edges.map((edge) => {
      const source = nodes.get(edge.source.node);
      const target = nodes.get(edge.target.node);
      const sourceDefinition = source && this.definition(source);
      const targetDefinition = target && this.definition(target);
      if (!source || !target || !sourceDefinition || !targetDefinition) return "";
      const sourceIndex = Math.max(0, (sourceDefinition.outputs || []).findIndex((port) => port.name === edge.source.port));
      const targetIndex = Math.max(0, (targetDefinition.inputs || []).findIndex((port) => port.name === edge.target.port));
      const from = nodePosition(source, true, sourceIndex);
      const to = nodePosition(target, false, targetIndex);
      const bend = Math.max(55, Math.abs(to.x - from.x) * 0.45);
      const port = sourceDefinition.outputs[sourceIndex];
      return `<path d="M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}" class="node-edge" data-type="${escapeHtml(port?.type || "METADATA")}" />`;
    }).join("")}</svg>`;
  }

  renderNode(node) {
    const definition = this.definition(node);
    if (!definition) return "";
    const state = this.nodeStates.get(node.id) || {};
    const selected = this.selected.has(node.id);
    const position = node.position || {};
    const color = categoryColor[definition.category] || categoryColor.utility;
    const inputs = (definition.inputs || []).map((port) => `<button class="node-socket node-socket--input" type="button" title="${escapeHtml(port.type)}" style="--socket:${color}" data-node-input="${escapeHtml(node.id)}:${escapeHtml(port.name)}"><span>${escapeHtml(port.label || port.name)}</span><i>${escapeHtml(port.type)}</i></button>`).join("");
    const outputs = (definition.outputs || []).map((port) => `<button class="node-socket node-socket--output ${this.pendingOutput?.node === node.id && this.pendingOutput?.port === port.name ? "is-pending" : ""}" type="button" title="${escapeHtml(port.type)}" style="--socket:${color}" data-node-output="${escapeHtml(node.id)}:${escapeHtml(port.name)}"><i>${escapeHtml(port.type)}</i><span>${escapeHtml(port.label || port.name)}</span></button>`).join("");
    return `<article class="graph-node ${selected ? "is-selected" : ""} ${this.dirty.has(node.id) ? "is-dirty" : ""}" data-node-card="${escapeHtml(node.id)}" style="left:${asNumber(position.x, 80)}px;top:${asNumber(position.y, 80)}px;--node-color:${color}"><header data-node-drag="${escapeHtml(node.id)}"><span class="graph-node__category">${escapeHtml(definition.category)}</span><strong>${escapeHtml(node.data?.title || definition.title)}</strong><span class="graph-node__status" data-status="${escapeHtml(state.status || (this.dirty.has(node.id) ? "dirty" : definition.status))}">${escapeHtml(state.cache_hit ? "cache" : state.status || (this.dirty.has(node.id) ? "dirty" : definition.status))}</span></header><div class="graph-node__body"><div class="graph-node__ports">${inputs}</div><div class="graph-node__ports graph-node__ports--out">${outputs}</div></div></article>`;
  }

  renderInspector(selected) {
    if (!selected) return `<div class="node-inspector__empty"><strong>Inspector / Preview</strong><p>Chọn node để chỉnh typed properties, xem output, link và lỗi.</p><p>Shift-click để multi-select. Kéo node, lăn chuột để zoom, kéo nền để pan.</p></div>`;
    const definition = this.definition(selected);
    const state = this.nodeStates.get(selected.id) || {};
    const data = selected.data || {};
    const fields = (definition.properties || []).map((property) => this.renderProperty(selected, property, data[property.name])).join("");
    const linked = this.graph.edges.filter((edge) => edge.source.node === selected.id || edge.target.node === selected.id);
    return `<div class="node-inspector__head"><div><span class="eyebrow">${escapeHtml(definition.category)}</span><h3>${escapeHtml(definition.title)}</h3><p>${escapeHtml(definition.description)}</p></div><button class="button button--compact" type="button" data-node-action="duplicate">Duplicate</button></div><div class="node-state" data-status="${escapeHtml(state.status || "idle")}">${escapeHtml(state.message || (this.dirty.has(selected.id) ? "Đã dirty; Run Graph sẽ chỉ tính lại downstream." : "Sẵn sàng."))}</div><div class="node-properties">${fields || `<p class="muted small">Node này không có property.</p>`}</div><div class="node-inspector__section"><strong>Links</strong>${linked.length ? linked.map((edge) => `<div class="node-link-row"><span>${escapeHtml(edge.source.node)} → ${escapeHtml(edge.target.node)}</span><button class="button button--compact" type="button" data-node-disconnect="${escapeHtml(edge.id)}">×</button></div>`).join("") : `<p class="muted small">Chưa có link.</p>`}</div><div class="node-inspector__section"><strong>Preview</strong>${this.renderPreview(state.output)}</div>${state.error ? `<div class="form-result form-result--error">${escapeHtml(state.error)}</div>` : ""}</div>`;
  }

  renderProperty(node, property, value) {
    const current = value ?? property.default ?? "";
    const name = escapeHtml(property.name);
    const label = escapeHtml(property.label || property.name);
    if (property.kind === "asset") return `<label class="node-property"><span>${label}</span><input type="file" data-node-asset="${escapeHtml(node.id)}:${name}" accept="${escapeHtml(property.accept || "*")}" /><small>${typeof current === "string" && current ? `artifact: ${escapeHtml(current.slice(0, 18))}…` : "Chưa chọn artifact"}</small></label>`;
    if (property.kind === "textarea") return `<label class="node-property"><span>${label}</span><textarea data-node-property="${escapeHtml(node.id)}:${name}">${escapeHtml(current)}</textarea></label>`;
    if (property.kind === "boolean") return `<label class="node-toggle node-property"><input type="checkbox" data-node-property="${escapeHtml(node.id)}:${name}" ${current ? "checked" : ""} /><span>${label}</span></label>`;
    if (property.kind === "select") return `<label class="node-property"><span>${label}</span><select data-node-property="${escapeHtml(node.id)}:${name}">${(property.options || []).map((option) => `<option value="${escapeHtml(option)}" ${String(option) === String(current) ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}</select></label>`;
    if (property.kind === "encoder") {
      const encoders = this.encoderCapabilities.encoders || [];
      return `<label class="node-property"><span>${label}</span><select data-node-property="${escapeHtml(node.id)}:${name}"><option value="auto">Auto (capability thật)</option>${encoders.map((encoder) => `<option value="${escapeHtml(encoder.id)}" ${encoder.id === current ? "selected" : ""}>${escapeHtml(encoder.codec)} · ${escapeHtml(encoder.id)}${encoder.hardware ? " · GPU" : ""}</option>`).join("")}</select><small>${this.encoderCapabilities.available ? "FFmpeg -encoders/-h encoder đã cache trong Hub." : escapeHtml(this.encoderCapabilities.reason || "FFmpeg chưa khả dụng.")}</small></label>`;
    }
    const type = property.kind === "number" ? "number" : property.kind === "color" ? "color" : "text";
    const extras = property.kind === "number" ? ` min="${escapeHtml(property.min ?? "")}" max="${escapeHtml(property.max ?? "")}" step="${escapeHtml(property.step ?? "any")}"` : "";
    return `<label class="node-property"><span>${label}</span><input type="${type}" value="${escapeHtml(current)}" data-node-property="${escapeHtml(node.id)}:${name}"${extras} /></label>`;
  }

  renderPreview(output) {
    if (!output) return `<div class="node-preview-empty">Output artifact sẽ cập nhật ngay khi node hoàn tất.</div>`;
    const a = output.a && artifactFrom(output.a);
    const b = output.b && artifactFrom(output.b);
    if (a && b) return `<div class="node-compare"><figure><img src="${escapeHtml(a.url)}" alt="Before" /><figcaption>Before</figcaption></figure><figure><img src="${escapeHtml(b.url)}" alt="After" /><figcaption>After</figcaption></figure></div>`;
    const artifact = artifactFrom(output);
    if (!artifact) return `<pre class="node-metadata">${escapeHtml(JSON.stringify(output, null, 2).slice(0, 3500))}</pre>`;
    const media = String(artifact.media_type || "");
    if (media.startsWith("image/")) return `<img class="node-preview-media" src="${escapeHtml(artifact.url)}" alt="${escapeHtml(artifact.name)}" />`;
    if (media.startsWith("video/")) return `<video class="node-preview-media" src="${escapeHtml(artifact.url)}" controls preload="metadata"></video>`;
    if (media.startsWith("audio/")) return `<audio class="node-preview-media" src="${escapeHtml(artifact.url)}" controls preload="metadata"></audio>`;
    return `<a class="button button--compact" href="${escapeHtml(artifact.url)}" target="_blank" rel="noopener">Mở ${escapeHtml(artifact.name)}</a>`;
  }

  renderCommandMenu() {
    const definitions = this.visibleDefinitions().slice(0, 24);
    return `<div class="node-command-backdrop" data-node-action="close-command"><div class="node-command" role="dialog" aria-label="Thêm node"><div class="split"><strong>Thêm node</strong><button class="button button--compact" type="button" data-node-action="close-command">Đóng</button></div><input autofocus data-node-command-search value="${escapeHtml(this.paletteSearch)}" placeholder="Tìm node…" /><div class="node-command__list">${definitions.map((item) => `<button type="button" data-node-add="${escapeHtml(item.type)}"><span style="--node-color:${categoryColor[item.category] || categoryColor.utility}"></span><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.description)}</small></button>`).join("")}</div></div></div>`;
  }

  bindEvents() {
    if (this.eventsBound) return;
    this.eventsBound = true;
    const signal = this.abort.signal;
    this.root.querySelector("[data-node-search]")?.addEventListener("input", (event) => { this.paletteSearch = event.target.value; this.render(); }, { signal });
    this.root.querySelector("[data-node-command-search]")?.addEventListener("input", (event) => { this.paletteSearch = event.target.value; this.render(); }, { signal });
    this.root.addEventListener("click", (event) => this.handleClick(event), { signal });
    this.root.addEventListener("change", (event) => this.handleChange(event), { signal });
    this.root.addEventListener("input", (event) => this.handleInput(event), { signal });
    this.root.addEventListener("pointerdown", (event) => this.handlePointerDown(event), { signal });
    this.root.addEventListener("wheel", (event) => this.handleWheel(event), { signal, passive: false });
  }

  bindGlobalKeys() {
    document.addEventListener("keydown", (event) => {
      if (!this.root.isConnected) return;
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
      if (event.ctrlKey && event.key.toLowerCase() === "k") { event.preventDefault(); this.commandOpen = true; this.render(); return; }
      if (typing) return;
      if (event.ctrlKey && event.key.toLowerCase() === "z") { event.preventDefault(); this.undo(); return; }
      if (event.ctrlKey && event.key.toLowerCase() === "y") { event.preventDefault(); this.redo(); return; }
      if (event.ctrlKey && event.key.toLowerCase() === "d") { event.preventDefault(); this.duplicateSelected(); return; }
      if (["Delete", "Backspace"].includes(event.key)) { event.preventDefault(); this.deleteSelected(); }
    }, { signal: this.abort.signal });
  }

  handleClick(event) {
    const action = event.target.closest("[data-node-action]")?.dataset.nodeAction;
    if (action) { this.handleAction(action); return; }
    const add = event.target.closest("[data-node-add]")?.dataset.nodeAdd;
    if (add) { this.addNode(add); return; }
    const disconnect = event.target.closest("[data-node-disconnect]")?.dataset.nodeDisconnect;
    if (disconnect) { this.mutate(() => { this.graph.edges = this.graph.edges.filter((edge) => edge.id !== disconnect); }, []); return; }
    const output = event.target.closest("[data-node-output]")?.dataset.nodeOutput;
    if (output) { this.chooseOutput(output); return; }
    const input = event.target.closest("[data-node-input]")?.dataset.nodeInput;
    if (input) { this.connectInput(input); return; }
    const card = event.target.closest("[data-node-card]")?.dataset.nodeCard;
    if (card) {
      if (event.shiftKey) this.selected.has(card) ? this.selected.delete(card) : this.selected.add(card);
      else this.selected = new Set([card]);
      this.render();
    }
  }

  handleChange(event) {
    const option = event.target.closest("[data-node-option]")?.dataset.nodeOption;
    if (option) {
      this[option === "auto" ? "autoPreview" : "draft"] = event.target.checked;
      this.persist();
      this.scheduleAutoPreview();
      return;
    }
    const preset = event.target.closest("[data-node-preset]")?.value;
    if (preset) { this.loadPreset(preset); return; }
    const asset = event.target.closest("[data-node-asset]");
    if (asset) { this.uploadAsset(asset); return; }
    const property = event.target.closest("[data-node-property]");
    if (property) this.updateProperty(property);
    const imported = event.target.closest("[data-node-import]");
    if (imported?.files?.[0]) this.importGraph(imported.files[0]);
  }

  handleInput(event) {
    const property = event.target.closest("[data-node-property]");
    if (property && !["checkbox", "file"].includes(property.type)) this.updateProperty(property, { render: false });
  }

  handlePointerDown(event) {
    const drag = event.target.closest("[data-node-drag]")?.dataset.nodeDrag;
    if (drag) { this.startNodeDrag(event, drag); return; }
    const canvas = event.target.closest("[data-node-canvas]");
    if (canvas) this.startPan(event, canvas);
  }

  handleWheel(event) {
    if (!event.target.closest("[data-node-canvas]")) return;
    event.preventDefault();
    this.zoom = Math.max(0.3, Math.min(1.5, this.zoom + (event.deltaY < 0 ? 0.08 : -0.08)));
    this.render();
  }

  handleAction(action) {
    if (action === "run") { this.run(); return; }
    if (action === "cancel") { this.cancel(); return; }
    if (action === "reset-view") { this.pan = { x: 0, y: 0 }; this.zoom = 0.72; this.render(); return; }
    if (action === "save-local") { this.persist(); this.showToast("Workflow đã lưu local trong WebView."); return; }
    if (action === "save-as") { this.saveAs(); return; }
    if (action === "export") { this.exportGraph(); return; }
    if (action === "import") { this.root.querySelector("[data-node-import]")?.click(); return; }
    if (action === "duplicate") { this.duplicateSelected(); return; }
    if (action === "command") { this.commandOpen = true; this.render(); return; }
    if (action === "close-command") { this.commandOpen = false; this.render(); }
  }

  addNode(type) {
    const definition = this.registry.get(type);
    if (!definition) return;
    const properties = Object.fromEntries((definition.properties || []).map((property) => [property.name, property.default]));
    const offset = this.graph.nodes.length * 26;
    const node = { id: uuid(), type, position: { x: 340 + (offset % 420), y: 180 + (offset % 360) }, data: properties };
    this.mutate(() => { this.graph.nodes.push(node); this.selected = new Set([node.id]); this.commandOpen = false; }, [node.id]);
  }

  chooseOutput(value) {
    const [nodeId, port] = value.split(":");
    const node = this.graph.nodes.find((item) => item.id === nodeId);
    const definition = node && this.definition(node);
    const portDefinition = definition?.outputs?.find((item) => item.name === port);
    if (!portDefinition) return;
    this.pendingOutput = { node: nodeId, port, type: portDefinition.type };
    this.render();
  }

  connectInput(value) {
    const [targetNode, targetPort] = value.split(":");
    if (!this.pendingOutput) { this.showToast("Chọn output socket trước.", "warning"); return; }
    const target = this.graph.nodes.find((item) => item.id === targetNode);
    const targetDefinition = target && this.definition(target);
    const input = targetDefinition?.inputs?.find((item) => item.name === targetPort);
    if (!input) return;
    if (input.type !== this.pendingOutput.type) { this.showToast(`Không thể nối ${this.pendingOutput.type} vào ${input.type}.`, "error"); return; }
    const source = this.pendingOutput;
    this.mutate(() => {
      if (!input.multi) this.graph.edges = this.graph.edges.filter((edge) => !(edge.target.node === targetNode && edge.target.port === targetPort));
      if (!this.graph.edges.some((edge) => edge.source.node === source.node && edge.source.port === source.port && edge.target.node === targetNode && edge.target.port === targetPort)) {
        this.graph.edges.push({ id: `edge_${Date.now()}_${Math.random().toString(16).slice(2, 7)}`, source: { node: source.node, port: source.port }, target: { node: targetNode, port: targetPort } });
      }
      this.pendingOutput = null;
    }, [targetNode]);
  }

  updateProperty(element, { render = true } = {}) {
    const [nodeId, name] = element.dataset.nodeProperty.split(":");
    const node = this.graph.nodes.find((item) => item.id === nodeId);
    if (!node) return;
    const definition = this.definition(node);
    const property = definition?.properties?.find((item) => item.name === name);
    let value = element.type === "checkbox" ? element.checked : element.value;
    if (property?.kind === "number") value = asNumber(value, property.default ?? 0);
    const before = this.snapshot();
    node.data = { ...(node.data || {}), [name]: value };
    if (before !== this.snapshot()) {
      this.history.push(before); this.future = []; this.markDirty(nodeId); this.persist();
      if (render) this.render(); else this.scheduleAutoPreview();
    }
  }

  async uploadAsset(input) {
    const file = input.files?.[0];
    if (!file) return;
    const [nodeId, name] = input.dataset.nodeAsset.split(":");
    input.disabled = true;
    try {
      const artifact = await uploadFile(file);
      const node = this.graph.nodes.find((item) => item.id === nodeId);
      if (!node) return;
      this.mutate(() => { node.data = { ...(node.data || {}), [name]: artifact.id }; }, [nodeId]);
      this.showToast(`Đã dùng artifact ${artifact.name} trong node.`);
    } catch (error) { this.showToast(error.message, "error"); }
    finally { input.disabled = false; }
  }

  startNodeDrag(event, nodeId) {
    if (event.button !== 0) return;
    event.preventDefault();
    if (!this.selected.has(nodeId)) this.selected = new Set([nodeId]);
    const selected = [...this.selected];
    const origins = Object.fromEntries(selected.map((id) => {
      const node = this.graph.nodes.find((item) => item.id === id);
      return [id, { x: asNumber(node?.position?.x, 0), y: asNumber(node?.position?.y, 0) }];
    }));
    this.drag = { mode: "node", startX: event.clientX, startY: event.clientY, origins, before: this.snapshot() };
    this.attachPointerFinish();
  }

  startPan(event, canvas) {
    if (event.button !== 0 || event.target !== canvas) return;
    this.drag = { mode: "pan", startX: event.clientX, startY: event.clientY, originPan: { ...this.pan } };
    this.attachPointerFinish();
  }

  attachPointerFinish() {
    const signal = this.abort.signal;
    const move = (event) => {
      if (!this.drag) return;
      const dx = event.clientX - this.drag.startX;
      const dy = event.clientY - this.drag.startY;
      if (this.drag.mode === "pan") this.pan = { x: this.drag.originPan.x + dx, y: this.drag.originPan.y + dy };
      else for (const [id, origin] of Object.entries(this.drag.origins)) {
        const node = this.graph.nodes.find((item) => item.id === id);
        if (node) node.position = { x: Math.max(0, origin.x + dx / this.zoom), y: Math.max(0, origin.y + dy / this.zoom) };
      }
      this.render();
    };
    const finish = () => {
      if (this.drag?.mode === "node" && this.drag.before !== this.snapshot()) { this.history.push(this.drag.before); this.future = []; [...this.selected].forEach((id) => this.markDirty(id)); this.persist(); this.scheduleAutoPreview(); }
      this.drag = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
    };
    window.addEventListener("pointermove", move, { signal });
    window.addEventListener("pointerup", finish, { signal, once: true });
  }

  deleteSelected() {
    if (!this.selected.size) return;
    const remove = new Set(this.selected);
    this.mutate(() => { this.graph.nodes = this.graph.nodes.filter((node) => !remove.has(node.id)); this.graph.edges = this.graph.edges.filter((edge) => !remove.has(edge.source.node) && !remove.has(edge.target.node)); this.selected.clear(); }, []);
  }

  duplicateSelected() {
    const node = this.selectedNode();
    if (!node) return;
    this.mutate(() => {
      const copy = clone(node); copy.id = uuid(); copy.position = { x: asNumber(node.position?.x, 0) + 42, y: asNumber(node.position?.y, 0) + 42 };
      this.graph.nodes.push(copy); this.selected = new Set([copy.id]);
    }, []);
  }

  async loadPreset(id, { quiet = false } = {}) {
    if (!id) return;
    try {
      const result = await getNodePreset(id);
      if (!result.validation?.valid) throw new Error("Preset Hub không qua schema validation.");
      this.graph = result.graph;
      this.selected.clear(); this.history = []; this.future = []; this.nodeStates.clear(); this.dirty = new Set(this.graph.nodes.map((node) => node.id)); this.persist();
      if (!quiet) { this.render(); this.showToast("Đã nạp preset workflow Hub."); }
    } catch (error) { if (!quiet) this.showToast(error.message, "error"); }
  }

  saveAs() {
    const title = window.prompt("Tên workflow local", this.graph.title || "Workflow local");
    if (!title) return;
    this.mutate(() => { this.graph.title = title; this.graph.id = `local-${title.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "workflow"}`; }, []);
    this.showToast("Đã Save As local. Export JSON nếu muốn lưu file ngoài Git.");
  }

  exportGraph() {
    const blob = new Blob([JSON.stringify(this.graph, null, 2)], { type: "application/json" });
    const href = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href; link.download = `${this.graph.id || "workflow"}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  }

  async importGraph(file) {
    try {
      const graph = JSON.parse(await file.text());
      const result = await validateNodeGraph(graph, false);
      if (!result.validation?.valid) throw new Error(result.validation?.errors?.[0]?.message || "Workflow JSON không hợp lệ.");
      this.graph = result.validation.graph;
      this.selected.clear(); this.history = []; this.future = []; this.nodeStates.clear(); this.dirty = new Set(this.graph.nodes.map((node) => node.id)); this.persist(); this.render();
      this.showToast("Đã import workflow JSON vào local Node Studio.");
    } catch (error) { this.showToast(error.message, "error"); }
  }

  scheduleAutoPreview() {
    if (!this.autoPreview) return;
    const hasHeavy = this.graph.nodes.some((node) => this.definition(node)?.heavy);
    if (hasHeavy && !this.draft) { this.showToast("Node GPU nặng chỉ chạy khi bấm Run Graph, hoặc bật Draft ảnh rõ ràng.", "warning"); return; }
    if (this.autoTimer) clearTimeout(this.autoTimer);
    this.autoTimer = setTimeout(() => this.run({ auto: true }), 420);
  }

  async run({ auto = false } = {}) {
    if (this.activeJobId) return;
    try {
      const result = await runNodeGraph(this.graph, Boolean(auto && this.draft));
      this.activeJobId = result.job?.id || null;
      this.showToast(this.activeJobId ? `Đã tạo ${this.activeJobId}.` : "Đã gửi Run Graph.");
      this.render();
      if (this.activeJobId) this.startPoll();
    } catch (error) { this.showToast(error.message, "error"); }
  }

  async cancel() {
    if (!this.activeJobId) return;
    try { await cancelJob(this.activeJobId); this.showToast("Đang hủy graph job do Hub sở hữu."); }
    catch (error) { this.showToast(error.message, "error"); }
  }

  startPoll() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    const poll = async () => {
      if (!this.activeJobId) return;
      try {
        const result = await getNodeRun(this.activeJobId);
        const run = result.run || {};
        for (const node of run.nodes || []) this.nodeStates.set(node.id, node);
        this.render();
        if (["completed", "failed", "error", "cancelled", "unavailable"].includes(run.status)) {
          clearInterval(this.pollTimer); this.pollTimer = null; this.activeJobId = null;
          if (run.status === "completed") this.dirty.clear();
          this.persist(); this.render();
        }
      } catch { /* job may still be entering its worker thread */ }
    };
    poll();
    this.pollTimer = setInterval(poll, 2000);
  }
}

let activeStudios = [];

export function disposeNodeStudios() {
  activeStudios.forEach((studio) => studio.destroy());
  activeStudios = [];
}

export function mountNodeStudios(options) {
  disposeNodeStudios();
  activeStudios = [...document.querySelectorAll("[data-node-studio]")].map((root) => new NodeStudio(root, options));
  activeStudios.forEach((studio) => studio.initialize());
}
