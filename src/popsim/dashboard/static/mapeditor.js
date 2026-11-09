// Map editor (REQ-1C.8): place and edit a scenario's cities, roads, resources, workplaces and houses on
// a map. It edits the same scenario object as the form and the YAML tab; `ctx` is how it talks to them.
//
// ctx = {
//   state(): the editor state { own, resolved, report, yaml, name }
//   edited(): call after own changed (marks it dirty and re-validates)
//   setOwn(obj): replace the whole own mapping
// }
import { api, h, toast, cssVar } from "./util.js";
import * as M from "./mapmodel.js";

const NS = "http://www.w3.org/2000/svg";
const W = 1000, H = 600;
const GOOD_COLORS = ["--verdigris", "--ochre", "--indigo", "--plum", "--olive"];
const NODE_FIELDS = [["initial", "Start stock"], ["max_amount", "Max stock"], ["regen_per_hour", "Regrowth / h"],
  ["base_rate", "Gathered / h"], ["stamina_cost", "Stamina / unit"]];
const TOOLS = [["select", "Select", "Select and move things; drag the background to pan"],
  ["city", "City", "Click the map to place a city"],
  ["road", "Road", "Drag from one city to another"],
  ["resource", "Resource", "Click inside a city to place a resource"],
  ["workplace", "Workplace", "Click inside a city to place a workplace"],
  ["house", "House", "Click inside a city to place a house"],
  ["scatter", "Scatter", "Drag a circle inside a city to fill it with resources, workplaces or houses"]];
const HELPERS = [["arrange_cities", "Arrange cities"], ["connect_cities", "Connect cities"], ["fill_resources", "Fill in resources"],
  ["fill_workplaces", "Fill in workplaces"], ["fill_houses", "Fill in houses"], ["remove_positions", "Remove positions"]];

function s(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return e;
}

const fmt = (v, d = 1) => (v == null || !isFinite(v) ? "–" : String(Math.round(v * 10 ** d) / 10 ** d));
const toNum = (v) => { if (v === "" || v == null) return undefined; const n = parseFloat(v); return isFinite(n) ? n : undefined; };

function setNested(obj, path, v) {
  let cur = obj;
  for (const k of path.slice(0, -1)) { if (typeof cur[k] !== "object" || cur[k] == null) cur[k] = {}; cur = cur[k]; }
  if (v === undefined) delete cur[path[path.length - 1]]; else cur[path[path.length - 1]] = v;
  if (v === undefined && path.length > 1) {  // prune empty parents
    const parent = path.slice(0, -1).reduce((a, k) => (a == null ? a : a[k]), obj);
    if (parent && !Object.keys(parent).length) setNested(obj, path.slice(0, -1), undefined);
  }
}

export class MapEditor {
  constructor(ctx) {
    this.ctx = ctx;
    this.history = new M.History(100);
    this.tool = "select";
    this.sel = null;            // { kind: "city"|"resource"|"workplace"|"house"|"road", ci, i, ri, ei }
    this.view = null;           // { k: px per km, ox, oy }
    this.world = null;          // the server's preview of automatic positions
    this.pendingRoad = null;    // name of the city a road is being drawn from
    this.drag = null;
    this.rubber = null;         // { x, y } while drawing a road or scatter circle
    this.scatter = null;        // { ci, cx, cy, r } while dragging a scatter circle
    this.opts = { good: null, task: "guard", wage: 2, kind: "resource", count: 8, richness: 1, nearest: 0, helperCount: 3 };
    this.confirm = null;        // { title, added, notes, apply } while a helper waits for a yes
    this.previewTimer = null;
    this.previewSeq = 0;
    this.root = h("div", { class: "mapeditor", tabindex: -1, onkeydown: (e) => this.onKey(e) });
    this.banner = h("div");
    this.toolbar = h("div", { class: "me-toolbar", role: "toolbar", "aria-label": "Map tools" });
    this.options = h("div", { class: "me-options" });
    this.canvasHost = h("div", { class: "me-canvas" });
    this.panel = h("div", { class: "me-panel", "aria-live": "polite" });
    this.confirmHost = h("div");
    this.problems = h("div", { class: "me-problems" });
    this.readout = null;
    this.root.append(this.banner, this.toolbar, this.options, this.confirmHost,
      h("div", { class: "me-body" }, this.canvasHost, this.panel), this.problems);
  }

  // -- entry points ---------------------------------------------------------
  /** Called by the host after every validation: redraw and fetch the preview of automatic positions. */
  update() {
    this.draw();
    this.drawChrome();
    clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(() => this.fetchPreview(), 250);
  }

  /** A different scenario was loaded: forget the view, selection, undo steps and preview of the old one. */
  reset() {
    this.history = new M.History(100);
    this.sel = null;
    this.view = null;
    this.world = null;
    this.pendingRoad = null;
    this.scatter = null;
    this.confirm = null;
    this.tool = "select";
  }

  destroy() { clearTimeout(this.previewTimer); this.stopListening(); }

  get st() { return this.ctx.state(); }

  async fetchPreview() {
    const st = this.st;
    if (!st || !st.yaml) return;
    const seq = ++this.previewSeq;
    try {
      const res = await api("/api/scenarios/preview", { method: "POST", body: { yaml: st.yaml } });
      if (seq !== this.previewSeq) return;
      if (res.world) this.world = res.world;
      this.draw();
    } catch { /* the preview is a nicety; the form still works */ }
  }

  // -- editing ----------------------------------------------------------------
  /** Run `fn(own, resolved)` as one undo step. `fn` may return { ok:false, error } to refuse. */
  edit(fn) {
    const st = this.st;
    const before = JSON.stringify(st.own);
    const res = fn(st.own, st.resolved);
    if (res && res.ok === false) { toast(res.error, "error"); return res; }
    if (JSON.stringify(st.own) !== before) { this.history.push(before); this.ctx.edited(); }
    this.draw();
    this.drawChrome();
    return res || { ok: true };
  }

  applyData(newOwn, label) {
    const before = JSON.stringify(this.st.own);
    this.history.push(before);
    this.ctx.setOwn(newOwn);
    this.ctx.edited();
    this.sel = null;
    this.confirm = null;
    this.fit();
    this.draw();
    this.drawChrome();
    if (label) toast(label);
  }

  undo() { this.restore(this.history.undo(JSON.stringify(this.st.own))); }
  redo() { this.restore(this.history.redo(JSON.stringify(this.st.own))); }
  restore(snapshot) {
    if (snapshot == null) return;
    this.ctx.setOwn(JSON.parse(snapshot));
    this.ctx.edited();
    this.sel = null;
    this.draw();
    this.drawChrome();
  }

  async helper(action, params = {}, title = "") {
    try {
      const res = await api("/api/scenarios/helper", { method: "POST", body: { data: this.st.own, action, params } });
      this.confirm = { title: title || action, added: res.added, notes: res.notes, data: res.data };
      this.drawConfirm();
    } catch (e) { toast(e.message, "error"); }
  }

  // -- geometry -------------------------------------------------------------
  layout() { const st = this.st; return M.layoutOf(st.own, st.resolved, this.world); }
  sx(x) { return this.view.ox + x * this.view.k; }
  sy(y) { return this.view.oy - y * this.view.k; }
  wx(px) { return (px - this.view.ox) / this.view.k; }
  wy(py) { return (this.view.oy - py) / this.view.k; }
  step() { return M.gridStep(this.view.k); }

  fit() {
    const lay = this.layout();
    let x0 = -40, x1 = 40, y0 = -25, y1 = 25;
    if (lay.cities.length) {
      x0 = Math.min(...lay.cities.map((c) => c.x - c.radius)); x1 = Math.max(...lay.cities.map((c) => c.x + c.radius));
      y0 = Math.min(...lay.cities.map((c) => c.y - c.radius)); y1 = Math.max(...lay.cities.map((c) => c.y + c.radius));
    }
    const pad = 70;
    const k = Math.min((W - 2 * pad) / Math.max(1e-6, x1 - x0), (H - 2 * pad) / Math.max(1e-6, y1 - y0), 60);
    this.view = { k, ox: W / 2 - ((x0 + x1) / 2) * k, oy: H / 2 + ((y0 + y1) / 2) * k };
  }

  /** Pointer position in world km. */
  point(e) {
    const r = this.svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) * W) / (r.width || W), py = ((e.clientY - r.top) * H) / (r.height || H);
    return { x: this.wx(px), y: this.wy(py), px, py };
  }

  snapPoint(p) { const st = this.step(); return { x: M.snap(p.x, st), y: M.snap(p.y, st) }; }

  // -- drawing ---------------------------------------------------------------
  drawChrome() {
    this.drawBanner();
    this.drawToolbar();
    this.drawOptions();
    this.drawPanel();
    this.drawConfirm();
    this.drawProblems();
  }

  drawBanner() {
    const state = M.spatialState(this.st.own, this.st.resolved);
    if (state === "all") { this.banner.replaceChildren(); return; }
    const some = state === "some";
    this.banner.replaceChildren(h("div", { class: "report" },
      h("p", {}, some ? "Some cities have no position yet." : "This scenario has no coordinates, so the map has nothing to draw."),
      h("p", { class: "hint" }, "Giving the cities positions makes the scenario spatial: people walk to work and results differ from the version without coordinates. You can go back with Remove positions."),
      h("button", { type: "button", class: "primary", onclick: () => this.helper("arrange_cities", {}, "Give cities positions") },
        some ? "Arrange the rest" : "Give cities positions")));
  }

  drawToolbar() {
    this.toolbar.replaceChildren(
      ...TOOLS.map(([id, label, title]) => h("button", { type: "button", class: "toggle", title, "aria-pressed": String(this.tool === id),
        onclick: () => { this.tool = id; this.pendingRoad = null; this.scatter = null; this.drawChrome(); this.draw(); } }, label)),
      h("span", { class: "me-sep" }),
      h("button", { type: "button", disabled: !this.history.canUndo, onclick: () => this.undo(), title: "Undo (Ctrl+Z)" }, "Undo"),
      h("button", { type: "button", disabled: !this.history.canRedo, onclick: () => this.redo(), title: "Redo (Shift+Ctrl+Z)" }, "Redo"),
      h("span", { class: "me-sep" }),
      h("button", { type: "button", onclick: () => this.zoomBy(1.4), "aria-label": "Zoom in" }, "+"),
      h("button", { type: "button", onclick: () => this.zoomBy(1 / 1.4), "aria-label": "Zoom out" }, "−"),
      h("button", { type: "button", onclick: () => { this.fit(); this.draw(); } }, "Fit"));
  }

  drawOptions() {
    const goods = M.goodsOf(this.st.resolved);
    if (!this.opts.good || !goods.includes(this.opts.good)) this.opts.good = goods[0];
    const sel = (label, value, items, onchange) => h("label", { class: "field" }, label,
      h("select", { onchange: (e) => onchange(e.target.value) }, items.map(([v, t]) => h("option", { value: v, selected: v === value }, t))));
    const num = (label, key, props = {}) => h("label", { class: "field" }, label,
      h("input", { type: "number", value: this.opts[key], ...props, onchange: (e) => { const v = toNum(e.target.value); if (v != null) this.opts[key] = v; } }));
    const kids = [];
    if (this.tool === "resource") kids.push(sel("Good", this.opts.good, goods.map((g) => [g, g]), (v) => { this.opts.good = v; }));
    if (this.tool === "workplace") kids.push(
      h("label", { class: "field" }, "Task", h("input", { value: this.opts.task, onchange: (e) => { this.opts.task = e.target.value || "guard"; } })),
      num("Wage / h", "wage", { step: "any", min: 0 }));
    if (this.tool === "scatter") {
      const what = this.opts.kind === "resource" ? sel("Good", this.opts.good, goods.map((g) => [g, g]), (v) => { this.opts.good = v; })
        : this.opts.kind === "workplace" ? h("label", { class: "field" }, "Task", h("input", { value: this.opts.task, onchange: (e) => { this.opts.task = e.target.value || "guard"; } })) : "";
      kids.push(sel("Fill with", this.opts.kind, [["resource", "Resources"], ["workplace", "Workplaces"], ["house", "Houses"]],
        (v) => { this.opts.kind = v; this.drawOptions(); }), what, num("How many", "count", { min: 1, step: 1 }),
      this.opts.kind === "resource" ? num("Richness", "richness", { min: 0.1, max: 10, step: 0.1 }) : "",
      this.opts.kind === "workplace" ? num("Wage / h", "wage", { step: "any", min: 0 }) : "");
    }
    // helpers
    const nearest = sel("Extra roads", String(this.opts.nearest), [["0", "Only what is needed"], ["1", "+ nearest neighbour"], ["2", "+ 2 nearest"]],
      (v) => { this.opts.nearest = +v; });
    const helpers = h("div", { class: "row me-helpers" }, h("span", { class: "muted" }, "Helpers:"),
      ...HELPERS.map(([id, label]) => h("button", { type: "button", onclick: () => this.helper(id, this.helperParams(id), label) }, label)),
      nearest, num("Count", "helperCount", { min: 1, step: 1 }));
    this.options.replaceChildren(h("div", { class: "row" }, ...kids), helpers,
      h("p", { class: "hint" }, TOOLS.find((t) => t[0] === this.tool)[2] + ". Items left without a position are placed automatically and shown faded."));
  }

  helperParams(id) {
    if (id === "connect_cities") return { nearest: this.opts.nearest };
    if (id === "fill_workplaces") return { count: this.opts.helperCount, task: this.opts.task, wage: this.opts.wage };
    if (id === "fill_houses") return { count: this.opts.helperCount };
    return {};
  }

  drawConfirm() {
    const c = this.confirm;
    if (!c) { this.confirmHost.replaceChildren(); return; }
    this.confirmHost.replaceChildren(h("div", { class: "report me-confirm", role: "alertdialog", "aria-label": c.title },
      h("h3", {}, c.title),
      c.added.length ? h("ul", {}, c.added.slice(0, 12).map((a) => h("li", {}, a)), c.added.length > 12 ? h("li", { class: "muted" }, `…and ${c.added.length - 12} more`) : "")
        : h("p", { class: "muted" }, "Nothing to add."),
      ...c.notes.map((n) => h("p", { class: "hint" }, n)),
      h("div", { class: "row" },
        h("button", { type: "button", class: "primary", disabled: !c.added.length, onclick: () => this.applyData(c.data, `${c.title}: done`) }, "Apply"),
        h("button", { type: "button", onclick: () => { this.confirm = null; this.drawConfirm(); } }, c.added.length ? "Cancel" : "Close"))));
  }

  zoomBy(f, px = W / 2, py = H / 2) {
    const wx = this.wx(px), wy = this.wy(py);
    this.view.k = Math.min(400, Math.max(0.05, this.view.k * f));
    this.view.ox = px - wx * this.view.k;
    this.view.oy = py + wy * this.view.k;
    this.draw();
  }

  problemSets() {
    const items = this.st.report || {};
    const set = (list) => (list || []).map((i) => ({ ...i, target: M.errorTarget(i.path) })).filter((i) => i.target);
    return { errors: set(items.error_items), warnings: set(items.warning_items) };
  }

  hasProblem(list, kind, a) {
    return list.some((p) => p.target.kind === kind && (kind === "road" ? (a.ri != null ? p.target.ri === a.ri : p.target.ei === a.ei)
      : p.target.ci === a.ci && (kind === "city" || p.target.i === a.i)));
  }

  draw() {
    const st = this.st;
    if (!st || !st.own) return;
    if (!this.view) this.fit();
    const lay = this.layout();
    const goods = M.goodsOf(st.resolved);
    const col = (g) => cssVar(GOOD_COLORS[Math.max(0, goods.indexOf(g)) % GOOD_COLORS.length]);
    const ink = cssVar("--ink"), ink2 = cssVar("--ink-2"), ink3 = cssVar("--ink-3"), line = cssVar("--line-2"), madder = cssVar("--madder"), ochre = cssVar("--ochre");
    const { errors, warnings } = this.problemSets();
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "application", "aria-label": "Scenario map", class: "me-svg", tabindex: 0, "data-key": "svg" });
    this.svg = svg;
    svg.addEventListener("wheel", (e) => { e.preventDefault(); const p = this.point(e); this.zoomBy(Math.exp(-e.deltaY * 0.0015), p.px, p.py); }, { passive: false });
    svg.addEventListener("pointermove", (e) => { if (!this.drag) this.onMove(e); });

    // grid
    const step = this.step();
    const gx0 = Math.floor(this.wx(0) / step) * step, gx1 = this.wx(W), gy0 = Math.floor(this.wy(H) / step) * step, gy1 = this.wy(0);
    const grid = s("g", { stroke: line, "stroke-width": 1 });
    for (let x = gx0; x <= gx1; x += step) grid.append(s("line", { x1: this.sx(x), y1: 0, x2: this.sx(x), y2: H }));
    for (let y = gy0; y <= gy1; y += step) grid.append(s("line", { x1: 0, y1: this.sy(y), x2: W, y2: this.sy(y) }));
    const bg = s("rect", { x: 0, y: 0, width: W, height: H, fill: "transparent", class: "me-bg", onpointerdown: (e) => this.onBackground(e) });
    svg.append(bg, grid);

    const byName = Object.fromEntries(lay.cities.map((c) => [c.name, c]));
    const speed = ((st.resolved.movement || {}).road || {}).speed || 5, rate = ((st.resolved.movement || {}).road || {}).stamina_per_unit || 0.5;

    // roads
    const roads = s("g");
    for (const r of lay.roads) {
      const a = byName[r.a], b = byName[r.b];
      if (!a || !b) continue;
      const straight = Math.hypot(a.x - b.x, a.y - b.y), length = r.length ?? straight;
      const hours = r.hours ?? length / speed, stamina = r.stamina_cost ?? length * rate;
      const selected = this.sel && this.sel.kind === "road" && (r.event ? this.sel.ei === r.ei : this.sel.ri === r.ri);
      const bad = this.hasProblem(errors, "road", r), warn = this.hasProblem(warnings, "road", r);
      const x1 = this.sx(a.x), y1 = this.sy(a.y), x2 = this.sx(b.x), y2 = this.sy(b.y);
      const g = s("g", { class: "me-road", opacity: r.inherited ? 0.55 : 1 });
      g.append(s("line", { x1, y1, x2, y2, stroke: bad ? madder : warn ? ochre : selected ? ink : ink2, "stroke-width": selected ? 5 : 3,
        "stroke-dasharray": r.event ? "7 6" : null, "stroke-linecap": "round" }));
      g.append(s("line", { x1, y1, x2, y2, stroke: "transparent", "stroke-width": 16, onpointerdown: (e) => { e.stopPropagation(); this.select({ kind: "road", ri: r.ri, ei: r.ei }); } }));
      g.append(s("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 8, "text-anchor": "middle", class: "map-road-label", style: "font-size:12px" },
        `${fmt(length)} km · ${fmt(hours)} h · ${fmt(stamina)} st${r.event ? ` · day ${r.day}` : ""}`));
      roads.append(g);
    }
    svg.append(roads);

    // cities
    const cityLayer = s("g");
    for (const c of lay.cities) {
      const selected = this.sel && this.sel.kind === "city" && this.sel.ci === c.ci;
      const bad = this.hasProblem(errors, "city", c), warn = this.hasProblem(warnings, "city", c);
      const g = s("g", { opacity: c.inherited ? 0.6 : 1 });
      const stroke = bad ? madder : warn ? ochre : selected ? ink : ink2;
      g.append(s("circle", { cx: this.sx(c.x), cy: this.sy(c.y), r: Math.max(6, c.radius * this.view.k), fill: ink2, "fill-opacity": 0.07, stroke,
        "stroke-width": selected || bad ? 3 : 1.5, "stroke-dasharray": selected ? null : "5 5", class: "me-city", tabindex: 0, role: "button", "data-key": `city:${c.ci}`,
        "aria-label": `City ${c.name}, population ${c.population}`, onpointerdown: (e) => this.onCityDown(c, e), onpointerup: (e) => this.onCityUp(c, e),
        onkeydown: (e) => { if (e.key === "Enter") this.select({ kind: "city", ci: c.ci }); } }));
      g.append(s("circle", { cx: this.sx(c.x), cy: this.sy(c.y), r: 3, fill: ink3, "pointer-events": "none" }));
      g.append(s("text", { x: this.sx(c.x), y: this.sy(c.y) - Math.max(6, c.radius * this.view.k) - 6, "text-anchor": "middle", class: "map-city-name", style: "font-size:20px", "pointer-events": "none" }, c.name));
      cityLayer.append(g);
    }
    svg.append(cityLayer);

    // houses, resources, workplaces
    const itemLayer = s("g");
    const itemEl = (it, shape) => {
      const selected = this.sel && this.sel.kind === it.kind && this.sel.ci === it.ci && this.sel.i === it.i;
      const bad = this.hasProblem(errors, it.kind, it), warn = this.hasProblem(warnings, it.kind, it);
      const x = this.sx(it.x), y = this.sy(it.y);
      const outline = bad ? madder : warn ? ochre : selected ? ink : null;
      const label = it.kind === "resource" ? `${it.good} resource` : it.kind === "workplace" ? `${it.task} workplace` : "house";
      const g = s("g", { class: `me-item${it.auto ? " auto" : ""}`, opacity: it.inherited ? 0.6 : it.auto ? 0.65 : 1, tabindex: 0, role: "button", "data-key": `${it.kind}:${it.ci}:${it.i}`,
        "aria-label": `${label}${it.auto ? ", automatic position" : ""}`,
        onpointerdown: (e) => this.onItemDown(it, e), onkeydown: (e) => { if (e.key === "Enter") this.select({ kind: it.kind, ci: it.ci, i: it.i }); } });
      g.append(shape(x, y), s("title", {}, `${label}${it.auto ? " (automatic)" : ""}`));
      if (outline) g.append(s("circle", { cx: x, cy: y, r: 13, fill: "none", stroke: outline, "stroke-width": 2.5 }));
      if (it.auto) g.append(s("text", { x, y: y + 22, "text-anchor": "middle", style: "font-size:10px", fill: ink3, "pointer-events": "none" }, "auto"));
      return g;
    };
    for (const it of lay.houses) itemLayer.append(itemEl(it, (x, y) => s("rect", { x: x - 4, y: y - 4, width: 8, height: 8, fill: it.auto ? "none" : ink3, stroke: ink3, "stroke-width": 1.5 })));
    for (const it of lay.nodes) itemLayer.append(itemEl(it, (x, y) => s("circle", { cx: x, cy: y, r: 8, fill: col(it.good), "fill-opacity": 0.3, stroke: col(it.good), "stroke-width": 2 })));
    for (const it of lay.workplaces) itemLayer.append(itemEl(it, (x, y) => s("path", { d: `M${x} ${y - 8} l8 8 l-8 8 l-8 -8 z`, fill: "none", stroke: ink, "stroke-width": 2 })));
    svg.append(itemLayer);

    // radius handle of the selected city
    if (this.sel && this.sel.kind === "city" && lay.cities[this.sel.ci]) {
      const c = lay.cities[this.sel.ci];
      svg.append(s("circle", { cx: this.sx(c.x + c.radius), cy: this.sy(c.y), r: 7, fill: ink, stroke: "white", "stroke-width": 2, class: "me-handle",
        role: "slider", "aria-label": "City radius", tabindex: 0, "data-key": "radius", onpointerdown: (e) => this.beginDrag(e, { type: "radius", ci: c.ci }),
        onkeydown: (e) => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); this.edit((o, r) => M.setCityRadius(o, r, c.ci, c.radius + (e.key === "ArrowRight" ? 1 : -1) * this.step())); } } }));
    }

    // rubber band and scatter circle
    if (this.pendingRoad && this.rubber && byName[this.pendingRoad]) {
      const a = byName[this.pendingRoad];
      svg.append(s("line", { x1: this.sx(a.x), y1: this.sy(a.y), x2: this.sx(this.rubber.x), y2: this.sy(this.rubber.y), stroke: ink, "stroke-dasharray": "4 4", "pointer-events": "none" }));
    }
    if (this.scatter) {
      const c = lay.cities[this.scatter.ci];
      svg.append(s("circle", { cx: this.sx(c.x + this.scatter.cx), cy: this.sy(c.y + this.scatter.cy), r: this.scatter.r * this.view.k, fill: ochre, "fill-opacity": 0.12,
        stroke: ochre, "stroke-width": 2, "pointer-events": "none" }));
    }

    // scale bar and readout
    const bar = this.step() * this.view.k;
    svg.append(s("g", { "pointer-events": "none" }, s("line", { x1: W - 30 - bar, y1: H - 22, x2: W - 30, y2: H - 22, stroke: ink, "stroke-width": 3 }),
      s("text", { x: W - 30 - bar / 2, y: H - 30, "text-anchor": "middle", style: "font-size:12px", fill: ink }, `${fmt(this.step(), 2)} km`)));
    this.readout = s("text", { x: 14, y: H - 14, style: "font-size:12px", fill: ink2, "pointer-events": "none" }, "");
    svg.append(this.readout);
    // Redrawing replaces every element, so give the keyboard focus back to the one that had it
    // (otherwise an arrow key press would end the keyboard session).
    const had = typeof document !== "undefined" && document.activeElement && this._oldSvg && this._oldSvg.contains && this._oldSvg.contains(document.activeElement)
      ? document.activeElement.getAttribute && document.activeElement.getAttribute("data-key") : null;
    this.canvasHost.replaceChildren(svg);
    this._oldSvg = svg;
    if (had && typeof svg.querySelector === "function") {
      const again = svg.querySelector(`[data-key="${had}"]`);
      if (again && again.focus) again.focus({ preventScroll: true });
    }
  }

  drawProblems() {
    const { errors, warnings } = this.problemSets();
    const raw = this.st.report || {};
    const all = [...(raw.error_items || []).map((i) => ({ ...i, bad: true })), ...(raw.warning_items || []).map((i) => ({ ...i, bad: false }))];
    if (!all.length) { this.problems.replaceChildren(); return; }
    this.problems.replaceChildren(h("h3", {}, `${raw.error_items ? raw.error_items.length : 0} problems, ${raw.warning_items ? raw.warning_items.length : 0} warnings`),
      h("ul", {}, all.map((i) => {
        const t = M.errorTarget(i.path);
        return h("li", { class: i.bad ? "bad" : "muted" }, t ? h("button", { type: "button", class: "linklike", onclick: () => this.focusOn(t) }, i.message) : i.message,
          i.path ? h("span", { class: "hint" }, ` (${i.path})`) : "");
      })));
    void errors; void warnings;
  }

  focusOn(t) {
    const lay = this.layout();
    const it = t.kind === "city" ? lay.cities[t.ci] : t.kind === "road" ? null : lay[{ resource: "nodes", workplace: "workplaces", house: "houses" }[t.kind]].find((x) => x.ci === t.ci && x.i === t.i);
    this.select(t.kind === "road" ? t : { kind: t.kind, ci: t.ci, i: t.i });
    if (it) { this.view.ox = W / 2 - it.x * this.view.k; this.view.oy = H / 2 + it.y * this.view.k; this.draw(); }
  }

  // -- selection and panel ------------------------------------------------------
  select(sel) { this.sel = sel; this.draw(); this.drawPanel(); }

  drawPanel() {
    // do not rebuild the panel under the user's hands while they type in it (unless the selection changed)
    const key = JSON.stringify(this.sel);
    const typing = typeof document !== "undefined" && document.activeElement && this.panel.contains
      && this.panel.contains(document.activeElement) && ["INPUT", "SELECT"].includes(document.activeElement.tagName);
    if (typing && key === this._panelKey) return;
    this._panelKey = key;
    const sel = this.sel, st = this.st, lay = this.layout();
    if (!sel) {
      this.panel.replaceChildren(h("h3", {}, "Nothing selected"),
        h("p", { class: "muted" }, "Choose a tool and click the map, or select a city, road or item to edit it here."),
        h("p", { class: "hint" }, "Keys: arrows move the selected item, Delete removes it, Esc cancels, Ctrl+Z undoes."));
      return;
    }
    const inh = (key) => (M.inherited(st.own, key) ? h("p", { class: "hint" }, "Comes from the parent scenario. Editing it copies the parent's list into this scenario.") : "");
    const field = (label, input, hint) => h("label", { class: "field" }, label, input, hint ? h("span", { class: "hint" }, hint) : "");
    const numIn = (value, onchange, props = {}) => h("input", { type: "number", step: "any", value: value ?? "", ...props,
      onchange: (e) => onchange(toNum(e.target.value)) });
    const del = (label, fn) => h("button", { type: "button", class: "danger", onclick: fn }, label);
    let kids = [];

    if (sel.kind === "city") {
      const c = lay.cities[sel.ci];
      if (!c) { this.sel = null; return this.drawPanel(); }
      const city = M.citiesOf(st.own, st.resolved)[sel.ci];
      const set = (path, v) => this.edit((o, r) => { setNested(M.ownList(o, r, "cities")[sel.ci], path, v); });
      kids = [h("h3", {}, `City ${c.name}`), inh("cities"),
        field("Name", h("input", { value: c.name, onchange: (e) => { const res = this.edit((o, r) => M.renameCity(o, r, sel.ci, e.target.value)); if (res.ok === false) e.target.value = c.name; } })),
        field("Population", numIn(city.population, (v) => set(["population"], v === undefined ? undefined : Math.min(5000, Math.max(1, Math.round(v)))), { min: 1, max: 5000, step: 1 })),
        field("x (km)", numIn(city.x, (v) => v != null && this.edit((o, r) => M.moveCity(o, r, sel.ci, v, c.y)))),
        field("y (km)", numIn(city.y, (v) => v != null && this.edit((o, r) => M.moveCity(o, r, sel.ci, c.x, v)))),
        field("Radius (km)", numIn(city.radius, (v) => {
          const out = this.edit((o, r) => ({ ok: true, outside: M.setCityRadius(o, r, sel.ci, v == null ? M.defaultRadius(r) : v) }));
          if (out.outside && out.outside.length) toast(`${out.outside.length} item${out.outside.length > 1 ? "s are" : " is"} now outside ${c.name}; they are outlined in red.`, "error");
        }, { min: 0.1, placeholder: String(M.defaultRadius(st.resolved)) })),
        field("Houses", Array.isArray(city.houses) ? h("span", { class: "muted" }, `${city.houses.length} listed (use the YAML tab or the map to move them)`)
          : numIn(M.houseCount(city) || "", (v) => this.edit((o, r) => { const cc = M.ownList(o, r, "cities")[sel.ci]; if (v) cc.houses = { count: Math.min(M.MAX_HOUSES, Math.round(v)) }; else delete cc.houses; }), { min: 0, step: 1, placeholder: "none" })),
        h("h4", {}, "This city's tax policy"),
        field("Income tax", numIn(city.fiscal && city.fiscal.income_tax, (v) => set(["fiscal", "income_tax"], v), { min: 0, max: 0.95, step: 0.01, placeholder: "scenario's" })),
        field("Sales tax", numIn(city.fiscal && city.fiscal.sales_tax, (v) => set(["fiscal", "sales_tax"], v), { min: 0, max: 0.95, step: 0.01, placeholder: "scenario's" })),
        field("Support gold / day", numIn(city.fiscal && city.fiscal.support && city.fiscal.support.amount, (v) => set(["fiscal", "support", "amount"], v), { min: 0, placeholder: "scenario's" })),
        h("div", { class: "row" },
          h("button", { type: "button", onclick: () => this.edit((o, r) => M.addItem(o, r, sel.ci, "workplace", { task: this.opts.task, wage: this.opts.wage }, null)) }, "Add a workplace"),
          h("button", { type: "button", onclick: () => this.edit((o, r) => M.addItem(o, r, sel.ci, "house", {}, null)) }, "Add a house")),
        del(`Delete ${c.name}`, () => this.deleteCity(sel.ci))];
    } else if (sel.kind === "road") {
      const roads = sel.ei != null ? M.eventsOf(st.own, st.resolved) : M.roadsOf(st.own, st.resolved);
      const r0 = roads[sel.ei != null ? sel.ei : sel.ri];
      if (!r0) { this.sel = null; return this.drawPanel(); }
      const byName = Object.fromEntries(lay.cities.map((c) => [c.name, c]));
      const a = byName[r0.a], b = byName[r0.b];
      const straight = a && b ? Math.hypot(a.x - b.x, a.y - b.y) : null;
      const mv = (st.resolved.movement || {}).road || { speed: 5, stamina_per_unit: 0.5 };
      const len = r0.length ?? straight;
      const setR = (k, v) => this.edit((o, r) => { const list = M.ownList(o, r, sel.ei != null ? "events" : "roads"); const x = list[sel.ei != null ? sel.ei : sel.ri]; if (v === undefined) delete x[k]; else x[k] = v; });
      kids = [h("h3", {}, `Road ${r0.a} to ${r0.b}`), inh(sel.ei != null ? "events" : "roads"),
        field("Length (km)", numIn(r0.length, (v) => setR("length", v), { min: 0, placeholder: straight != null ? `${fmt(straight, 2)} (straight line)` : "" }),
          "Longer than the straight line for a winding road"),
        field("Hours", numIn(r0.hours, (v) => setR("hours", v), { min: 0, placeholder: len != null ? `${fmt(len / mv.speed, 2)} (from length)` : "" })),
        field("Stamina", numIn(r0.stamina_cost, (v) => setR("stamina_cost", v), { min: 0, placeholder: len != null ? `${fmt(len * mv.stamina_per_unit, 2)} (from length)` : "" })),
        field("Opens on day", numIn(sel.ei != null ? r0.day : "", (v) => {
          if (sel.ei != null) {
            if (v == null) { this.edit((o, r) => { M.eventToRoad(o, r, sel.ei); }); this.sel = null; this.draw(); this.drawPanel(); }
            else setR("day", v);
          } else if (v != null) { this.edit((o, r) => { M.roadToEvent(o, r, sel.ri, v); }); this.sel = null; this.draw(); this.drawPanel(); }
        }, { min: 0, placeholder: "open from the start" }), "Leave empty for a road that exists from day 0"),
        del("Delete road", () => { this.edit((o, r) => (sel.ei != null ? M.deleteEvent(o, r, sel.ei) : M.deleteRoad(o, r, sel.ri))); this.sel = null; this.draw(); this.drawPanel(); })];
    } else {
      const list = { resource: lay.nodes, workplace: lay.workplaces, house: lay.houses }[sel.kind];
      const it = list.find((x) => x.ci === sel.ci && x.i === sel.i);
      if (!it) { this.sel = null; return this.drawPanel(); }
      const city = M.citiesOf(st.own, st.resolved)[sel.ci];
      const entry = sel.kind === "house" ? (Array.isArray(city.houses) ? city.houses[sel.i] : {}) : city[{ resource: "nodes", workplace: "workplaces" }[sel.kind]][sel.i];
      const setE = (k, v) => this.edit((o, r) => { const e = M.entryOf(o, r, sel.kind, sel.ci, sel.i); if (v === undefined) delete e[k]; else e[k] = v; });
      const goods = M.goodsOf(st.resolved);
      const pos = h("div", { class: "row" }, h("span", { class: "muted" }, it.auto ? `Automatic${it.pending ? " (loading)" : ""}: ${fmt(it.x, 2)}, ${fmt(it.y, 2)} km` : `At ${fmt(it.x, 2)}, ${fmt(it.y, 2)} km`),
        h("button", { type: "button", onclick: () => this.edit((o, r) => M.setAuto(o, r, sel.kind, sel.ci, sel.i, !it.auto, { x: it.x, y: it.y })) },
          it.auto ? "Place by hand" : "Place automatically"));
      if (sel.kind === "resource") {
        kids = [h("h3", {}, `${entry.good} resource in ${city.name}`), inh("cities"),
          field("Good", h("select", { onchange: (e) => setE("good", e.target.value) }, goods.map((g) => h("option", { value: g, selected: g === entry.good }, g)))),
          ...NODE_FIELDS.map(([k, label]) => field(label, numIn(entry[k], (v) => setE(k, v), { min: 0 }))), pos];
      } else if (sel.kind === "workplace") {
        kids = [h("h3", {}, `${entry.task} workplace in ${city.name}`), inh("cities"),
          field("Task", h("input", { value: entry.task ?? "", onchange: (e) => setE("task", e.target.value || undefined) })),
          field("Wage / h", numIn(entry.wage, (v) => setE("wage", v), { min: 0 })),
          field("Slots", numIn(entry.slots, (v) => setE("slots", v == null ? undefined : Math.max(1, Math.round(v))), { min: 1, step: 1, placeholder: "scenario's" })),
          field("Stamina / h", numIn(entry.stamina_per_hour, (v) => setE("stamina_per_hour", v), { min: 0, placeholder: "scenario's" })), pos];
      } else {
        kids = [h("h3", {}, `House in ${city.name}`), inh("cities"), pos];
      }
      kids.push(del("Delete", () => { this.edit((o, r) => M.deleteItem(o, r, sel.kind, sel.ci, sel.i)); this.sel = null; this.draw(); this.drawPanel(); }));
    }
    this.panel.replaceChildren(...kids);
  }

  deleteCity(ci) {
    const st = this.st;
    const info = M.cascadeInfo(st.own, st.resolved, ci);
    const lines = [info.roads.length ? `${info.roads.length} road${info.roads.length > 1 ? "s" : ""}` : "", info.events.length ? `${info.events.length} event${info.events.length > 1 ? "s" : ""}` : ""].filter(Boolean);
    const ok = typeof confirm === "function" ? confirm(`Delete ${info.name}${lines.length ? ` together with ${lines.join(" and ")} that mention it` : ""}?`) : true;
    if (!ok) return;
    this.edit((o, r) => { M.deleteCity(o, r, ci); });
    this.sel = null;
    this.draw();
    this.drawPanel();
  }

  // -- pointer handling ------------------------------------------------------------
  onBackground(e) {
    const p = this.point(e);
    if (this.tool === "city") {
      const q = this.snapPoint(p);
      const res = this.edit((o, r) => M.addCity(o, r, q.x, q.y));
      if (res.ok) { this.sel = { kind: "city", ci: res.ci }; this.draw(); this.drawPanel(); }
      return;
    }
    if (this.tool === "road") { this.pendingRoad = null; this.draw(); return; }
    if (["resource", "workplace", "house", "scatter"].includes(this.tool)) { toast("Click inside a city.", "error"); return; }
    this.sel = null;
    this.beginDrag(e, { type: "pan", px: p.px, py: p.py, ox: this.view.ox, oy: this.view.oy });
    this.drawPanel();
  }

  onCityDown(c, e) {
    e.stopPropagation();
    const p = this.point(e);
    switch (this.tool) {
      case "select": case "city":
        this.sel = { kind: "city", ci: c.ci };
        this.beginDrag(e, { type: "city", ci: c.ci, dx: p.x - c.x, dy: p.y - c.y });
        this.draw(); this.drawPanel();
        break;
      case "road":
        if (!this.pendingRoad) { this.pendingRoad = c.name; this.rubber = { x: c.x, y: c.y }; this.draw(); }
        else this.finishRoad(c);
        break;
      case "resource": case "workplace": case "house": this.placeItem(c, this.snapPoint(p)); break;
      case "scatter": {
        this.scatter = { ci: c.ci, cx: p.x - c.x, cy: p.y - c.y, r: 0 };
        this.beginDrag(e, { type: "scatter", ci: c.ci });
        break;
      }
      default: break;
    }
  }

  onCityUp(c, e) {
    if (this.tool === "road" && this.pendingRoad && this.pendingRoad !== c.name && this.drag == null) { e.stopPropagation(); this.finishRoad(c); }
  }

  finishRoad(c) {
    const from = this.pendingRoad;
    this.pendingRoad = null;
    this.rubber = null;
    const res = this.edit((o, r) => M.addRoad(o, r, from, c.name));
    if (res.ok) { this.sel = { kind: "road", ri: res.ri }; }
    this.draw(); this.drawPanel();
  }

  placeItem(c, q) {
    const kind = this.tool;
    const spec = kind === "resource" ? M.newNode(this.opts.good) : kind === "workplace" ? { task: this.opts.task, wage: this.opts.wage } : {};
    const res = this.edit((o, r) => M.addItem(o, r, c.ci, kind, spec, [q.x - c.x, q.y - c.y]));
    if (res.ok) { this.sel = { kind, ci: c.ci, i: res.i }; this.draw(); this.drawPanel(); }
  }

  onItemDown(it, e) {
    e.stopPropagation();
    if (this.tool !== "select") { const c = this.layout().cities[it.ci]; this.onCityDown(c, e); return; }
    this.sel = { kind: it.kind, ci: it.ci, i: it.i };
    this.beginDrag(e, { type: "item", kind: it.kind, ci: it.ci, i: it.i });
    this.draw(); this.drawPanel();
  }

  beginDrag(e, drag) {
    drag.before = JSON.stringify(this.st.own);
    drag.moved = false;
    this.drag = drag;
    this.listen();
  }

  listen() {
    if (this._up || typeof window === "undefined") return;
    this._move = (e) => this.onMove(e);
    this._up = (e) => this.onUp(e);
    window.addEventListener("pointermove", this._move);
    window.addEventListener("pointerup", this._up);
  }

  stopListening() {
    if (this._up) { window.removeEventListener("pointermove", this._move); window.removeEventListener("pointerup", this._up); }
    this._up = this._move = null;
  }

  onMove(e) {
    if (!this.svg) return;
    const p = this.point(e);
    if (this.readout) this.readout.textContent = `${fmt(p.x, 1)}, ${fmt(p.y, 1)} km`;
    if (this.pendingRoad) { this.rubber = { x: p.x, y: p.y }; if (!this.drag) this.draw(); }
    const d = this.drag;
    if (!d) return;
    const st = this.st;
    const q = this.snapPoint(p);
    d.moved = true;
    if (d.type === "pan") { this.view.ox = d.ox + (p.px - d.px); this.view.oy = d.oy + (p.py - d.py); }
    else if (d.type === "city") M.moveCity(st.own, st.resolved, d.ci, M.snap(p.x - d.dx, this.step()), M.snap(p.y - d.dy, this.step()));
    else if (d.type === "radius") {
      const c = M.citiesOf(st.own, st.resolved)[d.ci];
      M.setCityRadius(st.own, st.resolved, d.ci, Math.max(this.step(), M.snap(Math.hypot(p.x - Number(c.x), p.y - Number(c.y)), this.step())));
    } else if (d.type === "item") M.moveItem(st.own, st.resolved, d.kind, d.ci, d.i, q.x, q.y);
    else if (d.type === "scatter" && this.scatter) {
      const c = M.citiesOf(st.own, st.resolved)[d.ci];
      this.scatter.r = Math.max(0, Math.hypot(p.x - Number(c.x) - this.scatter.cx, p.y - Number(c.y) - this.scatter.cy));
    }
    this.draw();
  }

  onUp() {
    const d = this.drag;
    this.drag = null;
    this.stopListening();
    if (!d) return;
    if (d.type === "scatter") {
      const sc = this.scatter;
      this.scatter = null;
      this.draw();
      if (sc && sc.r > 0.05) {
        const what = this.opts.kind === "resource" ? this.opts.good : this.opts.task;
        this.helper("scatter", { city: M.citiesOf(this.st.own, this.st.resolved)[sc.ci].name, kind: this.opts.kind, what, count: this.opts.count,
          center: [M.round2(sc.cx), M.round2(sc.cy)], radius: M.round2(sc.r), richness: this.opts.richness, wage: this.opts.wage }, "Scatter");
      }
      return;
    }
    if (d.type === "pan") return;
    if (d.moved && JSON.stringify(this.st.own) !== d.before) {
      this.history.push(d.before);   // one undo step for the whole drag
      this.ctx.edited();
    }
    this.drawChrome();
  }

  onKey(e) {
    const tag = (e.target && e.target.tagName) || "";
    if (["INPUT", "SELECT", "TEXTAREA"].includes(tag)) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) this.redo(); else this.undo(); return; }
    if (e.key === "Escape") {
      if (this.confirm) { this.confirm = null; this.drawConfirm(); return; }
      if (this.pendingRoad || this.scatter) { this.pendingRoad = null; this.scatter = null; this.draw(); return; }
      this.tool = "select"; this.drawChrome(); this.draw(); return;
    }
    const sel = this.sel;
    if (!sel) return;
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      if (sel.kind === "city") this.deleteCity(sel.ci);
      else if (sel.kind === "road") { this.edit((o, r) => (sel.ei != null ? M.deleteEvent(o, r, sel.ei) : M.deleteRoad(o, r, sel.ri))); this.sel = null; this.draw(); this.drawPanel(); }
      else { this.edit((o, r) => M.deleteItem(o, r, sel.kind, sel.ci, sel.i)); this.sel = null; this.draw(); this.drawPanel(); }
      return;
    }
    const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[e.key];
    if (!dir || sel.kind === "road") return;
    e.preventDefault();
    const st = this.step(), lay = this.layout();
    if (sel.kind === "city") {
      const c = lay.cities[sel.ci];
      this.edit((o, r) => M.moveCity(o, r, sel.ci, c.x + dir[0] * st, c.y + dir[1] * st));
    } else {
      const list = { resource: lay.nodes, workplace: lay.workplaces, house: lay.houses }[sel.kind];
      const it = list.find((x) => x.ci === sel.ci && x.i === sel.i);
      if (it) this.edit((o, r) => { M.moveItem(o, r, sel.kind, sel.ci, sel.i, it.x + dir[0] * st, it.y + dir[1] * st); });
    }
    this.drawPanel();
  }
}
