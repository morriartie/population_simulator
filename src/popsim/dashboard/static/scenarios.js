// Scenarios: list, form editor + YAML tab (kept in sync), validation and saving.
import { api, h, toast } from "./util.js";
import { MapEditor } from "./mapeditor.js";

const EVENT_TYPES = [
  ["stimulus", "Stimulus (treasury pays every resident)"],
  ["mint", "Mint new gold into a treasury"],
  ["add_road", "Build a road"],
  ["set_road", "Change a road"],
  ["remove_road", "Close a road"],
];
const NODE_FIELDS = [
  ["initial", "Start stock"], ["max_amount", "Max stock"], ["regen_per_hour", "Regrowth / h"],
  ["base_rate", "Gathered / h"], ["stamina_cost", "Stamina / unit"],
];

const clone = (x) => (x === undefined ? undefined : JSON.parse(JSON.stringify(x)));
const getPath = (o, path) => path.reduce((a, k) => (a == null ? undefined : a[k]), o);
function setPath(o, path, v) {
  let cur = o;
  for (const k of path.slice(0, -1)) { if (typeof cur[k] !== "object" || cur[k] == null) cur[k] = {}; cur = cur[k]; }
  if (v === undefined) delete cur[path[path.length - 1]]; else cur[path[path.length - 1]] = v;
  // prune empty parents
  if (v === undefined && path.length > 1) {
    const parent = getPath(o, path.slice(0, -1));
    if (parent && !Object.keys(parent).length) setPath(o, path.slice(0, -1), undefined);
  }
}
const toNum = (s, int = false) => {
  if (s === "" || s == null) return undefined;
  const n = int ? parseInt(s, 10) : parseFloat(s);
  return isFinite(n) ? n : undefined;
};

// ``at: [dx, dy]`` of a node, house or workplace (an offset from the city centre). Empty means "generate".
function setAt(obj, axis, raw) {
  const cur = Array.isArray(obj.at) ? [...obj.at] : [undefined, undefined];
  cur[axis] = toNum(raw);
  if (cur[0] === undefined && cur[1] === undefined) delete obj.at;
  else obj.at = [cur[0] ?? 0, cur[1] ?? 0];
}

export async function mount(main, name) {
  const st = {
    list: [], defaults: await api("/api/defaults"),
    name: null, original: null, own: {}, resolved: null, yaml: "", report: null,
    tab: "form", dirty: false, timer: null, seq: 0,
  };

  // The map editor edits st.own like the form does (REQ-1C.8).
  const mapEd = new MapEditor({ state: () => st, edited: () => changed(true), setOwn: (o) => { st.own = o; } });
  const guard = (e) => { if (st.dirty) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", guard);

  // A draft is kept in the browser after every edit, so a reload or a crash loses nothing.
  const draftKey = () => `popsim.draft:${st.original ?? "new"}`;
  const readDraft = () => { try { return JSON.parse(localStorage.getItem(draftKey()) || "null"); } catch { return null; } };
  const saveDraft = () => { try { localStorage.setItem(draftKey(), JSON.stringify({ name: st.name, own: st.own, ts: Date.now() })); } catch { /* storage full or off */ } };
  const clearDraft = () => { try { localStorage.removeItem(draftKey()); } catch { /* ignore */ } };

  const listHost = h("div", { class: "scen-list", role: "list" });
  const editor = h("div");
  main.append(
    h("h1", {}, "Scenarios"),
    h("p", { class: "lede" }, "Scenarios describe a world: its cities, resources, roads and the events that happen during a run. Edit one here, then play it."),
    h("div", { class: "split" },
      h("div", {}, h("div", { class: "row", style: "margin-bottom:10px" },
        h("button", { type: "button", onclick: () => startNew() }, "New scenario")), listHost),
      editor));

  async function refreshList() {
    st.list = await api("/api/scenarios");
    listHost.replaceChildren(...st.list.map((s) => h("button", {
      type: "button", role: "listitem", "aria-current": String(s.name === st.original),
      onclick: () => { if (!st.dirty || confirm("Discard unsaved changes?")) location.hash = `#/scenarios/${encodeURIComponent(s.name)}`; },
    }, s.name, h("span", { class: "meta" }, s.error ? `Error: ${s.error}` :
      `${s.cities.join(", ") || "no cities"}; ${s.days} days${s.events ? `; ${s.events} event${s.events > 1 ? "s" : ""}` : ""}`))));
  }

  async function load(n) {
    const s = await api(`/api/scenarios/${encodeURIComponent(n)}`);
    Object.assign(st, { name: n, original: n, own: s.own, resolved: s.resolved, yaml: s.yaml, report: null, dirty: false });
    mapEd.reset();
    const draft = readDraft();
    st.draft = draft && JSON.stringify(draft.own) !== JSON.stringify(s.own) ? draft : null;
    render();
    await validateNow();
  }

  function startNew(fromCurrent = false) {
    if (st.dirty && !confirm("Discard unsaved changes?")) return;
    const base = fromCurrent ? clone(st.own) : { extends: "baseline.yaml", world_name: "my_scenario" };
    let nm = fromCurrent ? `${st.name}_copy` : "my_scenario";
    const taken = new Set(st.list.map((s) => s.name));
    for (let i = 2; taken.has(nm); i++) nm = `${fromCurrent ? `${st.name}_copy` : "my_scenario"}${i}`;
    if (fromCurrent) base.world_name = nm;
    Object.assign(st, { name: nm, original: null, own: base, dirty: true, report: null, draft: null });
    mapEd.reset();
    history.replaceState(null, "", "#/scenarios");
    refreshList();
    sync(true);
  }

  // -- syncing -----------------------------------------------------------------
  function changed(structural = false) {
    st.dirty = true;
    saveDraft();
    clearTimeout(st.timer);
    st.timer = setTimeout(() => sync(structural), structural ? 0 : 350);
  }

  async function sync(structural) {
    const my = ++st.seq;
    try {
      const { yaml } = await api("/api/yaml/render", { method: "POST", body: { data: st.own } });
      if (my !== st.seq) return;
      st.yaml = yaml;
      await validateNow();
      if (my !== st.seq) return;
      if (st.tab === "map" && st.resolvedOnce) { renderAside(); mapEd.update(); }
      else if (structural || !st.resolvedOnce) { st.resolvedOnce = true; render(); } else renderAside();
    } catch (e) { toast(e.message, "error"); }
  }

  async function validateNow() {
    try {
      const rep = await api("/api/scenarios/validate", { method: "POST", body: { yaml: st.yaml } });
      st.report = rep;
      if (rep.resolved) st.resolved = rep.resolved;
    } catch (e) {
      st.report = { ok: false, errors: [e.message], warnings: [] };
    }
    renderAside();
  }

  async function yamlEdited(text) {
    st.yaml = text;
    st.dirty = true;
    clearTimeout(st.timer);
    st.timer = setTimeout(async () => {
      try {
        const { data } = await api("/api/yaml/parse", { method: "POST", body: { yaml: text } });
        st.own = data;
        await validateNow();
      } catch (e) {
        st.report = { ok: false, errors: [e.message], warnings: [] };
        renderAside();
      }
    }, 500);
  }

  // -- rendering ----------------------------------------------------------------
  const reportHost = h("div");
  const actionsHost = h("div", { class: "actions" });

  function eff(path) {
    const v = getPath(st.resolved, path);
    return v === undefined ? getPath(st.defaults, path) : v;
  }
  const inherited = (path) => getPath(st.own, path) === undefined;
  const parentName = () => st.own.extends || "nothing (standalone)";

  function scalar(label, path, { type = "number", int = false, step, min, max, hint } = {}) {
    const inh = inherited(path);
    const input = h("input", { type, value: eff(path) ?? "", step, min, max,
      oninput: (e) => {
        const v = type === "number" ? toNum(e.target.value, int) : (e.target.value || undefined);
        setPath(st.own, path, v);
        e.target.closest(".field").classList.toggle("inherited", v === undefined);
        changed();
      } });
    return h("div", { class: `field${inh ? " inherited" : ""}` }, h("label", {}, label, input),
      h("span", { class: "hint" }, hint || (inh ? `Inherited from ${parentName()}` : "Set in this scenario")));
  }

  function ownList(key) {
    if (st.own[key] === undefined) st.own[key] = clone(eff([key])) || [];
    return st.own[key];
  }

  function citySelect(value, onchange, { all = false } = {}) {
    const names = (eff(["cities"]) || []).map((c) => c.name);
    return h("select", { onchange },
      all ? h("option", { value: "all", selected: value === "all" }, "All cities") : "",
      ...names.map((n) => h("option", { value: n, selected: n === value }, n)),
      value && value !== "all" && !names.includes(value) ? h("option", { value, selected: true }, `${value} (missing)`) : "");
  }

  function renderCities() {
    const cities = eff(["cities"]) || [];
    const goods = eff(["goods"]) || ["food", "wood"];
    const inh = inherited(["cities"]);
    const fs = h("fieldset", { class: inh ? "inherited" : "" }, h("legend", {}, "Cities"),
      inh ? h("p", { class: "hint" }, `Inherited from ${parentName()}. Editing creates this scenario's own copy.`) : "");
    cities.forEach((c, ci) => {
      const nodes = c.nodes || [];
      const card = h("div", { class: "city-card" },
        h("div", { class: "row" },
          h("label", { class: "field" }, "Name", h("input", { value: c.name, onchange: (e) => {
            const old = ownList("cities")[ci].name, nw = e.target.value.trim();
            ownList("cities")[ci].name = nw;
            if ((eff(["roads"]) || []).some((r) => r.a === old || r.b === old))
              for (const r of ownList("roads")) { if (r.a === old) r.a = nw; if (r.b === old) r.b = nw; }
            if ((eff(["events"]) || []).some((ev) => [ev.a, ev.b, ev.city].includes(old)))
              for (const ev of ownList("events")) for (const k of ["a", "b", "city"]) if (ev[k] === old) ev[k] = nw;
            changed(true);
          } })),
          h("label", { class: "field" }, "Population", h("input", { type: "number", min: 1, value: c.population,
            oninput: (e) => { ownList("cities")[ci].population = toNum(e.target.value, true); changed(); } })),
          ...[["x", "x (km)"], ["y", "y (km)"], ["radius", "Radius (km)"]].map(([k, label]) =>
            h("label", { class: "field" }, label, h("input", { type: "number", step: "any", min: k === "radius" ? 0 : undefined,
              value: c[k] ?? "", placeholder: k === "radius" ? String(eff(["movement", "default_city_radius"]) ?? 2) : "none",
              oninput: (e) => {
                const city = ownList("cities")[ci], before = city[k] != null;
                city[k] = toNum(e.target.value);
                changed((city[k] != null) !== before);  // redraw only when the position columns appear or vanish
              } }))),
          h("div", { class: "spacer" }),
          h("button", { type: "button", class: "danger", onclick: () => { ownList("cities").splice(ci, 1); changed(true); } }, `Remove ${c.name}`)),
        h("table", { class: "mini", style: "margin-top:8px" },
          h("thead", {}, h("tr", {}, h("th", {}, "Resource"), ...NODE_FIELDS.map(([, t]) => h("th", {}, t)),
            ...(c.x != null ? [h("th", {}, "Offset x"), h("th", {}, "Offset y")] : []), h("th", {}, ""))),
          h("tbody", {}, nodes.map((nd, ni) => h("tr", {},
            h("td", {}, h("select", { onchange: (e) => { ownList("cities")[ci].nodes[ni].good = e.target.value; changed(); } },
              goods.map((g) => h("option", { value: g, selected: g === nd.good }, g)))),
            ...NODE_FIELDS.map(([k]) => h("td", {}, h("input", { type: "number", step: "any", min: 0, value: nd[k] ?? "",
              "aria-label": `${c.name} ${nd.good} ${k}`,
              oninput: (e) => { ownList("cities")[ci].nodes[ni][k] = toNum(e.target.value); changed(); } }))),
            ...(c.x != null ? [0, 1].map((axis) => h("td", {}, h("input", { type: "number", step: "any", placeholder: "auto",
              value: (nd.at || [])[axis] ?? "", "aria-label": `${c.name} ${nd.good} offset ${axis ? "y" : "x"}`,
              oninput: (e) => { setAt(ownList("cities")[ci].nodes[ni], axis, e.target.value); changed(); } }))) : []),
            h("td", {}, h("button", { type: "button", class: "danger", "aria-label": `Remove ${nd.good} node`,
              onclick: () => { ownList("cities")[ci].nodes.splice(ni, 1); changed(true); } }, "Remove")))))),
        h("button", { type: "button", style: "margin-top:6px", onclick: () => {
          const city = ownList("cities")[ci];
          city.nodes = city.nodes || [];
          city.nodes.push({ good: goods[0], initial: 200, max_amount: 300, regen_per_hour: 3, base_rate: 0.7, stamina_cost: 5 });
          changed(true);
        } }, "Add resource"));
      // this city's own tax and support policy (REQ-5.5); empty fields use the scenario's
      const own = (path, label, step = "any", max) => h("label", { class: "field" }, label, h("input", { type: "number", step, min: 0, max,
        value: getPath(c, path) ?? "", placeholder: `${eff(["fiscal", ...path]) ?? 0} (scenario)`,
        oninput: (e) => { setPath(ownList("cities")[ci], ["fiscal", ...path], toNum(e.target.value)); changed(); } }));
      card.append(h("div", { class: "row", style: "margin-top:10px" },
        h("span", { class: "muted" }, "This city's policy:"), own(["income_tax"], "Income tax", 0.01, 0.95),
        own(["sales_tax"], "Sales tax", 0.01, 0.95), own(["support", "amount"], "Support gold / day")));
      // houses (REQ-3A.8) and workplaces (REQ-3A.4)
      const houseCount = Array.isArray(c.houses) ? c.houses.length : (c.houses && c.houses.count) || 0;
      card.append(h("div", { class: "row", style: "margin-top:10px" },
        h("label", { class: "field" }, "Houses (% of population)", h("input", { type: "number", min: 0, max: 1000, step: "any", value: c.housing_pct ?? "",
          placeholder: `${eff(["housing_pct"]) ?? 0} (scenario)`,
          oninput: (e) => { ownList("cities")[ci].housing_pct = toNum(e.target.value); changed(); } })),
        h("label", { class: "field" }, "Extra houses", h("input", { type: "number", min: 0, step: 1, value: houseCount || "", placeholder: "none",
          disabled: Array.isArray(c.houses),
          oninput: (e) => { const n = toNum(e.target.value, true); ownList("cities")[ci].houses = n ? { count: n } : undefined; changed(); } })),
        h("span", { class: "hint" }, `${Math.max(0, Math.floor((c.population || 0) * (c.housing_pct ?? eff(["housing_pct"]) ?? 0) / 100 + 0.5))} houses are generated from the percentage `
          + "(120 gives more houses than people, 80 fewer); extra houses are added on top. Each house shelters one person and gives better rest.")));
      const wps = c.workplaces || [];
      card.append(h("h4", { style: "margin:10px 0 4px" }, "Workplaces (public jobs paid by this city's treasury)"));
      if (wps.length) card.append(h("table", { class: "mini" },
        h("thead", {}, h("tr", {}, h("th", {}, "Task"), h("th", {}, "Wage / h"), h("th", {}, "Slots"), h("th", {}, "Stamina / h"),
          ...(c.x != null ? [h("th", {}, "Offset x"), h("th", {}, "Offset y")] : []), h("th", {}, ""))),
        h("tbody", {}, wps.map((wp, wi) => h("tr", {},
          h("td", {}, h("input", { value: wp.task ?? "", oninput: (e) => { ownList("cities")[ci].workplaces[wi].task = e.target.value || undefined; changed(); } })),
          h("td", {}, h("input", { type: "number", step: "any", min: 0, value: wp.wage ?? "", oninput: (e) => { ownList("cities")[ci].workplaces[wi].wage = toNum(e.target.value); changed(); } })),
          h("td", {}, h("input", { type: "number", step: 1, min: 1, value: wp.slots ?? "", placeholder: String(eff(["fiscal", "public_jobs", "slots"]) ?? 3),
            oninput: (e) => { ownList("cities")[ci].workplaces[wi].slots = toNum(e.target.value, true); changed(); } })),
          h("td", {}, h("input", { type: "number", step: "any", min: 0, value: wp.stamina_per_hour ?? "", placeholder: String(eff(["fiscal", "public_jobs", "stamina_per_hour"]) ?? 3.5),
            oninput: (e) => { ownList("cities")[ci].workplaces[wi].stamina_per_hour = toNum(e.target.value); changed(); } })),
          ...(c.x != null ? [0, 1].map((axis) => h("td", {}, h("input", { type: "number", step: "any", placeholder: "auto", value: (wp.at || [])[axis] ?? "",
            oninput: (e) => { setAt(ownList("cities")[ci].workplaces[wi], axis, e.target.value); changed(); } }))) : []),
          h("td", {}, h("button", { type: "button", class: "danger", onclick: () => { ownList("cities")[ci].workplaces.splice(wi, 1); changed(true); } }, "Remove")))))));
      card.append(h("button", { type: "button", style: "margin-top:6px", onclick: () => {
        const city = ownList("cities")[ci];
        city.workplaces = city.workplaces || [];
        city.workplaces.push({ task: "guard", wage: 1 });
        changed(true);
      } }, "Add workplace"));
      fs.append(card);
    });
    fs.append(h("button", { type: "button", style: "margin-top:12px", onclick: () => {
      const list = ownList("cities");
      let n = list.length + 1, nm = `City ${n}`;
      while (list.some((c) => c.name === nm)) nm = `City ${++n}`;
      list.push({ name: nm, population: 40, nodes: [{ good: goods[0], initial: 250, max_amount: 350, regen_per_hour: 4, base_rate: 0.7, stamina_cost: 5 }] });
      changed(true);
    } }, "Add city"));
    return fs;
  }

  // Resource points outside the cities (REQ-3A.11): each hangs off a road through a junction.
  function renderWorldNodes() {
    const nodes = eff(["nodes"]) || [];
    const goods = eff(["goods"]) || ["food", "wood"];
    const cities = eff(["cities"]) || [];
    const inh = inherited(["nodes"]);
    const names = cities.map((c) => c.name);
    const fs = h("fieldset", { class: inh ? "inherited" : "" }, h("legend", {}, "Resource points (outside the cities)"),
      h("p", { class: "hint" }, "Each one lies on the overworld and branches off a road at a junction, t from 0 (first city) to 1 (second city). Needs cities with coordinates."),
      h("div", { class: "row" }, h("label", { class: "field" }, "Houses (% of population), all cities",
        h("input", { type: "number", min: 0, max: 1000, step: "any", value: eff(["housing_pct"]) ?? "", placeholder: "0",
          oninput: (e) => { const v = toNum(e.target.value); if (v === undefined) delete st.own.housing_pct; else st.own.housing_pct = v; changed(); } }))));
    if (nodes.length) fs.append(h("table", { class: "mini" },
      h("thead", {}, h("tr", {}, h("th", {}, "Resource"), ...NODE_FIELDS.map(([, t]) => h("th", {}, t)), h("th", {}, "x"), h("th", {}, "y"),
        h("th", {}, "Road from"), h("th", {}, "to"), h("th", {}, "t"), h("th", {}, ""))),
      h("tbody", {}, nodes.map((nd, ni) => {
        const set = (fn) => { fn(ownList("nodes")[ni]); changed(); };
        const road = (nd.branch && nd.branch.road) || ["", ""];
        const citySel = (idx) => h("select", { onchange: (e) => set((n) => { n.branch = n.branch || { road: ["", ""], t: 0.5 }; n.branch.road = [...(n.branch.road || ["", ""])]; n.branch.road[idx] = e.target.value; if (!n.branch.road[0] && !n.branch.road[1]) delete n.branch; }) },
          h("option", { value: "" }, "auto"), names.map((n) => h("option", { value: n, selected: n === road[idx] }, n)));
        return h("tr", {},
          h("td", {}, h("select", { onchange: (e) => set((n) => { n.good = e.target.value; }) }, goods.map((g) => h("option", { value: g, selected: g === nd.good }, g)))),
          ...NODE_FIELDS.map(([k]) => h("td", {}, h("input", { type: "number", step: "any", min: 0, value: nd[k] ?? "", "aria-label": `Resource point ${ni + 1} ${k}`,
            oninput: (e) => set((n) => { n[k] = toNum(e.target.value); }) }))),
          ...[0, 1].map((axis) => h("td", {}, h("input", { type: "number", step: "any", placeholder: "auto", value: (nd.at || [])[axis] ?? "", "aria-label": `Resource point ${ni + 1} ${axis ? "y" : "x"}`,
            oninput: (e) => set((n) => setAt(n, axis, e.target.value)) }))),
          h("td", {}, citySel(0)), h("td", {}, citySel(1)),
          h("td", {}, h("input", { type: "number", step: "any", min: 0, max: 1, value: nd.branch && nd.branch.t != null ? nd.branch.t : "", placeholder: "auto",
            oninput: (e) => set((n) => { const v = toNum(e.target.value); if (n.branch) { if (v === undefined) delete n.branch.t; else n.branch.t = v; } }) })),
          h("td", {}, h("button", { type: "button", class: "danger", "aria-label": `Remove resource point ${ni + 1}`,
            onclick: () => { ownList("nodes").splice(ni, 1); changed(true); } }, "Remove")));
      }))));
    fs.append(h("button", { type: "button", style: "margin-top:6px", onclick: () => {
      ownList("nodes").push({ good: goods[0], initial: 200, max_amount: 300, regen_per_hour: 3, base_rate: 0.7, stamina_cost: 5 });
      changed(true);
    } }, "Add resource point"));
    return fs;
  }

  // Straight-line distance between two cities and what a road of that length costs (REQ-3A.2).
  function derived(a, b, length) {
    const cs = eff(["cities"]) || [];
    const ca = cs.find((c) => c.name === a), cb = cs.find((c) => c.name === b);
    if (!ca || !cb || ca.x == null || cb.x == null || ca.y == null || cb.y == null) return null;
    const straight = Math.hypot(ca.x - cb.x, ca.y - cb.y);
    const len = length ?? straight, mv = eff(["movement", "road"]) || { speed: 5, stamina_per_unit: 0.5 };
    return { straight, hours: len / mv.speed, stamina: len * mv.stamina_per_unit };
  }
  const fmt = (v) => (Math.round(v * 100) / 100).toString();

  function renderRoads() {
    const roads = eff(["roads"]) || [];
    const cities = (eff(["cities"]) || []).map((c) => c.name);
    const spatial = (eff(["cities"]) || []).length > 0 && (eff(["cities"]) || []).every((c) => c.x != null && c.y != null);
    const fs = h("fieldset", { class: inherited(["roads"]) ? "inherited" : "" }, h("legend", {}, "Roads open from day 0"));
    if (!roads.length) fs.append(h("p", { class: "hint" }, "No roads: cities start isolated. Add a road here, or build one later with an event."));
    else {
      fs.append(h("table", { class: "mini" },
        h("thead", {}, h("tr", {}, h("th", {}, "From"), h("th", {}, "To"), ...(spatial ? [h("th", {}, "Length (km)")] : []),
          h("th", {}, "Hours"), h("th", {}, "Stamina"), h("th", {}, ""))),
        h("tbody", {}, roads.map((r, i) => {
          const d = spatial ? derived(r.a, r.b, r.length) : null;
          const num = (k, ph) => h("input", { type: "number", step: "any", min: 0, value: r[k] ?? "", placeholder: ph == null ? "" : `${fmt(ph)} (from length)`,
            oninput: (e) => { ownList("roads")[i][k] = toNum(e.target.value); changed(); } });
          return h("tr", {},
            h("td", {}, citySelect(r.a, (e) => { ownList("roads")[i].a = e.target.value; changed(true); })),
            h("td", {}, citySelect(r.b, (e) => { ownList("roads")[i].b = e.target.value; changed(true); })),
            ...(spatial ? [h("td", {}, h("input", { type: "number", step: "any", min: 0, value: r.length ?? "", placeholder: d ? `${fmt(d.straight)} (straight line)` : "",
              oninput: (e) => { ownList("roads")[i].length = toNum(e.target.value); changed(); } }))] : []),
            h("td", {}, num("hours", d && d.hours)), h("td", {}, num("stamina_cost", d && d.stamina)),
            h("td", {}, h("button", { type: "button", class: "danger", onclick: () => { ownList("roads").splice(i, 1); changed(true); } }, "Remove")));
        }))));
      if (spatial) fs.append(h("p", { class: "hint" }, "Leave length, hours or stamina empty to derive them from the cities' positions. A value you type overrides the derived one."));
    }
    fs.append(h("button", { type: "button", style: "margin-top:8px", disabled: cities.length < 2,
      onclick: () => { ownList("roads").push(spatial ? { a: cities[0], b: cities[1] } : { a: cities[0], b: cities[1], hours: 8, stamina_cost: 20 }); changed(true); } }, "Add road"));
    return fs;
  }

  function eventFields(ev, i) {
    const upd = (k, v) => { ownList("events")[i][k] = v; changed(); };
    const numIn = (k, label) => h("label", { class: "field" }, label, h("input", { type: "number", step: "any", min: 0, value: ev[k] ?? "",
      oninput: (e) => upd(k, toNum(e.target.value)) }));
    switch (ev.type) {
      case "stimulus": return [h("label", { class: "field" }, "City", citySelect(ev.city || "all", (e) => upd("city", e.target.value), { all: true })),
        numIn("per_npc", "Gold per person")];
      case "mint": return [h("label", { class: "field" }, "City", citySelect(ev.city, (e) => upd("city", e.target.value))), numIn("amount", "Gold created")];
      case "remove_road": return [h("label", { class: "field" }, "From", citySelect(ev.a, (e) => upd("a", e.target.value))),
        h("label", { class: "field" }, "To", citySelect(ev.b, (e) => upd("b", e.target.value)))];
      default: {
        const d = derived(ev.a, ev.b, ev.length);
        const opt = (k, label, ph) => h("label", { class: "field" }, label, h("input", { type: "number", step: "any", min: 0, value: ev[k] ?? "",
          placeholder: ph == null ? "" : `${fmt(ph)} (derived)`, oninput: (e) => upd(k, toNum(e.target.value)) }));
        return [h("label", { class: "field" }, "From", citySelect(ev.a, (e) => upd("a", e.target.value))),
          h("label", { class: "field" }, "To", citySelect(ev.b, (e) => upd("b", e.target.value))),
          ...(d ? [opt("length", "Length (km)", d.straight)] : []),
          opt("hours", "Hours", d && d.hours), opt("stamina_cost", "Stamina", d && d.stamina)];
      }
    }
  }

  function renderEvents() {
    const events = eff(["events"]) || [];
    const cities = (eff(["cities"]) || []).map((c) => c.name);
    const fs = h("fieldset", { class: inherited(["events"]) ? "inherited" : "" }, h("legend", {}, "Events"));
    if (!events.length) fs.append(h("p", { class: "hint" }, "No events. Add a stimulus, a new road or freshly minted gold at a given day."));
    events.forEach((ev, i) => {
      fs.append(h("div", { class: "row city-card" },
        h("label", { class: "field" }, "Day", h("input", { type: "number", min: 0, step: "any", value: ev.day ?? "",
          oninput: (e) => { ownList("events")[i].day = toNum(e.target.value); changed(); } })),
        h("label", { class: "field" }, "Hour", h("input", { type: "number", min: 0, max: 23, step: 1, value: ev.hour ?? 0,
          oninput: (e) => { const v = toNum(e.target.value, true); if (v) ownList("events")[i].hour = v; else delete ownList("events")[i].hour; changed(); } })),
        h("label", { class: "field" }, "What happens", h("select", { onchange: (e) => {
          const t = e.target.value, cur = ownList("events")[i];
          const keep = { day: cur.day, ...(cur.hour ? { hour: cur.hour } : {}), type: t };
          if (t === "stimulus") Object.assign(keep, { city: "all", per_npc: 50 });
          else if (t === "mint") Object.assign(keep, { city: cities[0], amount: 1000 });
          else if (t === "remove_road") Object.assign(keep, { a: cities[0], b: cities[1] ?? cities[0] });
          else if (derived(cities[0], cities[1] ?? cities[0], undefined)) Object.assign(keep, { a: cities[0], b: cities[1] ?? cities[0] });
          else Object.assign(keep, { a: cities[0], b: cities[1] ?? cities[0], hours: 8, stamina_cost: 20 });
          ownList("events")[i] = keep;
          changed(true);
        } }, EVENT_TYPES.map(([v, t]) => h("option", { value: v, selected: v === ev.type }, t)))),
        ...eventFields(ev, i),
        h("div", { class: "spacer" }),
        h("button", { type: "button", class: "danger", onclick: () => { ownList("events").splice(i, 1); changed(true); } }, "Remove")));
    });
    fs.append(h("button", { type: "button", style: "margin-top:10px", onclick: () => {
      ownList("events").push({ day: 10, type: "stimulus", city: "all", per_npc: 50 });
      changed(true);
    } }, "Add event"));
    return fs;
  }

  // Taxes and public spending (REQ-5.5): one policy for the whole run; cities may override it below.
  function renderFiscal() {
    return h("fieldset", {}, h("legend", {}, "Taxes and public spending"),
      h("div", { class: "grid-fields" },
        scalar("Income tax", ["fiscal", "income_tax"], { min: 0, max: 0.95, step: 0.01, hint: "Share of wages and sales proceeds, to the city where earned" }),
        scalar("Sales tax", ["fiscal", "sales_tax"], { min: 0, max: 0.95, step: 0.01, hint: "Share of each transaction's value, paid by the buyer" }),
        scalar("Support (gold / day)", ["fiscal", "support", "amount"], { min: 0, step: 0.5, hint: "For residents whose survival needs are low" }),
        scalar("Support hour", ["fiscal", "support", "hour"], { min: 0, max: 23, step: 1, int: true }),
        scalar("Public job slots", ["fiscal", "public_jobs", "slots"], { min: 1, step: 1, int: true, hint: "People per workplace, unless it says otherwise" })),
      h("p", { class: "hint" }, "Everything starts at zero: no taxes and no support. Treasuries start with the share of gold set above and pay the wages and support. Each city can have its own rates, below."));
  }

  function renderForm() {
    const others = st.list.map((s) => s.name).filter((n) => n !== st.name);
    const ext = st.own.extends ?? "";
    return h("div", {},
      h("fieldset", {}, h("legend", {}, "General"),
        h("div", { class: "grid-fields" },
          h("div", { class: "field" }, h("label", {}, "File name", h("input", { value: st.name, required: true,
            oninput: (e) => { st.name = e.target.value.trim(); st.dirty = true; renderAside(); } })),
            h("span", { class: "hint" }, `Saved as scenarios/${st.name}.yaml`)),
          h("div", { class: "field" }, h("label", {}, "Based on", h("select", { onchange: (e) => {
            setPath(st.own, ["extends"], e.target.value || undefined); changed(true);
          } }, h("option", { value: "", selected: !ext }, "Nothing (standalone)"),
            h("option", { value: "default", selected: ext === "default" }, "Built-in defaults"),
            ...others.map((n) => h("option", { value: `${n}.yaml`, selected: ext === `${n}.yaml` }, n)))),
            h("span", { class: "hint" }, "Settings you don't change here come from this scenario")),
          scalar("World name", ["world_name"], { type: "text" }),
          scalar("Days", ["time", "days"], { min: 1, step: 1 }),
          scalar("Seed", ["seed"], { int: true, step: 1 }),
          scalar("Treasury share", ["money", "treasury_share"], { min: 0, max: 0.95, step: 0.05,
            hint: inherited(["money", "treasury_share"]) ? `Inherited. Fraction of gold held by city treasuries` : "Fraction of gold held by city treasuries" }),
          scalar("Merchant share", ["npc", "merchant_share"], { min: 0, max: 1, step: 0.05,
            hint: inherited(["npc", "merchant_share"]) ? "Inherited. Fraction of each city's people who trade" : "Fraction of each city's people who trade" }),
        )),
      renderFiscal(), renderCities(), renderWorldNodes(), renderRoads(), renderEvents(),
      h("p", { class: "hint" }, "Needs, pricing, traits and other fine-grained settings are edited in the YAML tab; see the built-in defaults for every key."));
  }

  function renderAside() {
    const r = st.report;
    reportHost.replaceChildren(!r ? "" : h("div", { class: `report ${r.ok ? "" : "fail"}`, role: "status" },
      r.ok ? h("p", { class: "good" }, r.warnings.length ? "Valid, with warnings." : "Valid. Ready to save and play.")
        : h("p", { class: "bad" }, `${r.errors.length} ${r.errors.length === 1 ? "problem" : "problems"} to fix before saving:`),
      r.errors.length ? h("ul", { class: "bad" }, r.errors.map((e) => h("li", {}, e))) : "",
      r.warnings.length ? h("ul", { class: "muted" }, r.warnings.map((w) => h("li", {}, w))) : ""));
    const canPlay = st.original && !st.dirty && r && r.ok;
    actionsHost.replaceChildren(
      h("button", { type: "button", class: "primary", disabled: !r || !r.ok || !st.name, onclick: save }, "Save scenario"),
      h("button", { type: "button", onclick: () => { clearTimeout(st.timer); sync(false); } }, "Validate"),
      st.original ? h("button", { type: "button", onclick: () => startNew(true) }, "Duplicate") : "",
      st.original && st.dirty ? h("button", { type: "button", onclick: () => { clearDraft(); load(st.original); } }, "Discard changes") : "",
      h("div", { class: "spacer" }),
      h("span", { class: "muted" }, st.dirty ? "Unsaved changes" : st.original ? "Saved" : ""),
      h("a", { class: "btn", href: canPlay ? `#/play?scenario=${encodeURIComponent(st.original)}` : null,
        "aria-disabled": String(!canPlay), style: canPlay ? "" : "opacity:.45;pointer-events:none" }, "Play this scenario"));
  }

  async function save() {
    const put = (overwrite) => api(`/api/scenarios/${encodeURIComponent(st.name)}`, { method: "PUT", body: { yaml: st.yaml, overwrite } });
    try {
      let res;
      try {
        res = await put(st.name === st.original);
      } catch (e) {
        if (e.status === 400 && /already exists/.test(e.message) && confirm(`Replace the existing scenario ${st.name}?`)) res = await put(true);
        else throw e;
      }
      if (!res.saved) { st.report = res; renderAside(); toast("Fix the problems listed before saving.", "error"); return; }
      toast(`Saved scenario ${st.name}`);
      clearDraft();   // the draft was kept under the old name
      st.original = st.name;
      st.dirty = false;
      st.draft = null;
      clearDraft();
      await refreshList();
      history.replaceState(null, "", `#/scenarios/${encodeURIComponent(st.name)}`);
      renderAside();
    } catch (e) { toast(e.message, "error"); }
  }

  function render() {
    if (!st.name) {
      editor.replaceChildren(h("div", { class: "empty" }, h("p", {}, "Pick a scenario on the left to edit it, or start a new one."),
        h("button", { type: "button", onclick: () => startNew() }, "New scenario")));
      return;
    }
    const formTab = h("button", { type: "button", role: "tab", "aria-selected": String(st.tab === "form"), onclick: () => { st.tab = "form"; render(); } }, "Form");
    const yamlTab = h("button", { type: "button", role: "tab", "aria-selected": String(st.tab === "yaml"), onclick: () => { st.tab = "yaml"; render(); } }, "YAML");
    const mapTab = h("button", { type: "button", role: "tab", "aria-selected": String(st.tab === "map"), onclick: () => { st.tab = "map"; render(); } }, "Map");
    const body = st.tab === "form" ? renderForm()
      : st.tab === "map" ? mapEd.root
      : h("div", {}, h("textarea", { class: "yaml", spellcheck: "false", "aria-label": "Scenario YAML", oninput: (e) => yamlEdited(e.target.value) }, st.yaml),
        h("p", { class: "hint" }, "Any key from the built-in defaults can be set here. Lists replace the inherited list; mappings merge key by key."));
    const draft = st.draft && h("div", { class: "report", role: "status" },
      h("p", {}, `A draft from ${new Date(st.draft.ts).toLocaleString()} was kept in this browser and differs from the saved scenario.`),
      h("div", { class: "row" },
        h("button", { type: "button", class: "primary", onclick: () => { st.own = st.draft.own; st.draft = null; st.dirty = true; mapEd.reset(); sync(true); } }, "Restore the draft"),
        h("button", { type: "button", onclick: () => { clearDraft(); st.draft = null; render(); } }, "Discard it")));
    editor.replaceChildren(h("h2", { style: "margin-top:0" }, st.original ? st.name : `New: ${st.name}`),
      h("div", { class: "tabs", role: "tablist" }, formTab, mapTab, yamlTab), draft || "", reportHost, body, actionsHost);
    renderAside();
    if (st.tab === "map") mapEd.update();
    refreshList();
  }

  await refreshList();
  if (name) await load(name);
  else render();
  return () => { clearTimeout(st.timer); mapEd.destroy(); window.removeEventListener("beforeunload", guard); };
}
