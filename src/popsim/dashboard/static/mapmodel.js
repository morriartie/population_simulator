// Map editor model (REQ-1C.8): everything the editor does to a scenario, with no DOM, so it can be
// tested on its own. All functions work on the scenario's *own* mapping (what the file says) and
// the resolved scenario (what it inherits). Lists replace inherited lists, so a function that edits
// `cities`, `roads` or `events` first copies the inherited list into the scenario, as the form does.
// Nothing here ever drops a key it does not know about.

export const MAX_CITIES = 50, MAX_NODES = 200, MAX_WORKPLACES = 100, MAX_HOUSES = 5000;
export const DEFAULT_NODE = { initial: 250, max_amount: 350, regen_per_hour: 4, base_rate: 0.7, stamina_cost: 5 };
const EDGE = 0.999;  // items stop just inside the city edge, so rounding never pushes them out

export const clone = (x) => (x === undefined ? undefined : JSON.parse(JSON.stringify(x)));
export const round2 = (v) => Math.round(v * 100) / 100;
export const snap = (v, step) => (step > 0 ? Math.round(v / step) * step : v);
const hasXY = (c) => c.x != null && c.y != null;

// ---------------------------------------------------------------- reading

export function citiesOf(own, resolved) { return (own.cities !== undefined ? own.cities : resolved && resolved.cities) || []; }
export function roadsOf(own, resolved) { return (own.roads !== undefined ? own.roads : resolved && resolved.roads) || []; }
export function eventsOf(own, resolved) { return (own.events !== undefined ? own.events : resolved && resolved.events) || []; }
export function goodsOf(resolved) { return (resolved && resolved.goods) || ["food", "wood"]; }
export function defaultRadius(resolved) { return (resolved && resolved.movement && resolved.movement.default_city_radius) || 2; }
export function radiusOf(city, resolved) { return city.radius != null ? Number(city.radius) : defaultRadius(resolved); }
export const inherited = (own, key) => own[key] === undefined;

/** "none" (no coordinates anywhere), "all" (every city has them, or there are no cities) or "some". */
export function spatialState(own, resolved) {
  const cs = citiesOf(own, resolved);
  if (!cs.length) return "all";
  const n = cs.filter(hasXY).length;
  return n === 0 ? "none" : n === cs.length ? "all" : "some";
}

export function ownList(own, resolved, key) {
  if (own[key] === undefined) own[key] = clone((resolved && resolved[key]) || []);
  return own[key];
}

export function nextCityName(own, resolved) {
  const names = new Set(citiesOf(own, resolved).map((c) => c.name));
  let n = names.size + 1, nm = `City ${n}`;
  while (names.has(nm)) nm = `City ${++n}`;
  return nm;
}

export function houseCount(city) {
  const h = city.houses;
  return Array.isArray(h) ? h.length : h && h.count ? Number(h.count) : 0;
}

/** The houses of a city as a list, turning `{count: N}` into N automatic houses (a no-op for a list). */
export function housesAsList(city) {
  if (Array.isArray(city.houses)) return city.houses;
  const n = houseCount(city);
  city.houses = Array.from({ length: n }, () => ({}));
  return city.houses;
}

/**
 * Everything the map draws, with absolute positions. Explicit positions come from the scenario; automatic
 * ones from the server's preview (`world`), falling back to the city centre while that is still loading.
 */
export function layoutOf(own, resolved, world) {
  const cs = citiesOf(own, resolved);
  const inh = inherited(own, "cities");
  const cities = cs.map((c, ci) => ({
    ci, name: c.name, x: Number(c.x ?? 0), y: Number(c.y ?? 0), radius: radiusOf(c, resolved),
    population: c.population, inherited: inh,
  }));
  const pick = (list, ci, i) => (list || []).find((p) => p.ci === ci && p.i === i);
  const place = (ci, spec, previewItem) => {
    const c = cities[ci];
    if (spec && Array.isArray(spec.at)) return { x: c.x + Number(spec.at[0]), y: c.y + Number(spec.at[1]), auto: false };
    return previewItem ? { x: previewItem.x, y: previewItem.y, auto: true, pending: false } : { x: c.x, y: c.y, auto: true, pending: true };
  };
  const nodes = [], workplaces = [], houses = [];
  cs.forEach((c, ci) => {
    (c.nodes || []).forEach((n, i) => nodes.push({ kind: "resource", ci, i, good: n.good, ...place(ci, n, pick(world && world.nodes, ci, i)), inherited: inh }));
    (c.workplaces || []).forEach((w, i) => workplaces.push({ kind: "workplace", ci, i, task: w.task, wage: w.wage, ...place(ci, w, pick(world && world.workplaces, ci, i)), inherited: inh }));
    const hl = Array.isArray(c.houses) ? c.houses : Array.from({ length: houseCount(c) }, () => ({}));
    hl.forEach((hs, i) => houses.push({ kind: "house", ci, i, ...place(ci, hs, pick(world && world.houses, ci, i)), inherited: inh }));
  });
  const roads = roadsOf(own, resolved).map((r, ri) => ({ ri, a: r.a, b: r.b, length: r.length, hours: r.hours, stamina_cost: r.stamina_cost, event: false, inherited: inherited(own, "roads") }));
  eventsOf(own, resolved).forEach((ev, ei) => {
    if (ev.type === "add_road" || ev.type === "set_road") roads.push({ ri: null, ei, a: ev.a, b: ev.b, day: ev.day, length: ev.length, hours: ev.hours, stamina_cost: ev.stamina_cost, event: true, type: ev.type, inherited: inherited(own, "events") });
  });
  return { cities, nodes, workplaces, houses, roads };
}

/** Map a validation path (`cities[1].nodes[0].at`) to the item it concerns, or null. */
export function errorTarget(path) {
  if (!path) return null;
  let m = /^cities\[(\d+)\]\.(nodes|workplaces|houses)\[(\d+)\]/.exec(path);
  if (m) return { kind: { nodes: "resource", workplaces: "workplace", houses: "house" }[m[2]], ci: +m[1], i: +m[3] };
  m = /^cities\[(\d+)\]/.exec(path);
  if (m) return { kind: "city", ci: +m[1] };
  m = /^roads\[(\d+)\]/.exec(path);
  if (m) return { kind: "road", ri: +m[1] };
  m = /^events\[(\d+)\]/.exec(path);
  if (m) return { kind: "road", ei: +m[1] };
  return null;
}

// -------------------------------------------------------------- cities

export function addCity(own, resolved, x, y) {
  const cs = ownList(own, resolved, "cities");
  if (cs.length >= MAX_CITIES) return { ok: false, error: `At most ${MAX_CITIES} cities.` };
  const name = nextCityName(own, resolved);
  // A new city gets one node of every good (placed automatically), so it can feed itself right away.
  const nodes = goodsOf(resolved).map((good) => ({ good, ...DEFAULT_NODE }));
  cs.push({ name, x: round2(x), y: round2(y), population: 40, nodes });
  return { ok: true, ci: cs.length - 1, name };
}

export function moveCity(own, resolved, ci, x, y) {
  const c = ownList(own, resolved, "cities")[ci];
  c.x = round2(x);
  c.y = round2(y);
}

/** Items that would lie outside a city of radius `r` (they are shown in red, not dropped). */
export function outsideRadius(own, resolved, ci, r) {
  const c = citiesOf(own, resolved)[ci];
  const out = [];
  const far = (spec) => Array.isArray(spec.at) && Math.hypot(spec.at[0], spec.at[1]) > r + 1e-9;
  (c.nodes || []).forEach((n, i) => far(n) && out.push({ kind: "resource", ci, i }));
  (c.workplaces || []).forEach((w, i) => far(w) && out.push({ kind: "workplace", ci, i }));
  if (Array.isArray(c.houses)) c.houses.forEach((hs, i) => hs && far(hs) && out.push({ kind: "house", ci, i }));
  return out;
}

export function setCityRadius(own, resolved, ci, r) {
  const c = ownList(own, resolved, "cities")[ci];
  c.radius = round2(Math.max(0.1, r));
  return outsideRadius(own, resolved, ci, c.radius);
}

/** What goes with a city if it is deleted: the roads and events that mention it. */
export function cascadeInfo(own, resolved, ci) {
  const name = citiesOf(own, resolved)[ci].name;
  return {
    name,
    roads: roadsOf(own, resolved).filter((r) => r.a === name || r.b === name),
    events: eventsOf(own, resolved).filter((ev) => ev.a === name || ev.b === name || ev.city === name),
  };
}

export function deleteCity(own, resolved, ci) {
  const info = cascadeInfo(own, resolved, ci);
  ownList(own, resolved, "cities").splice(ci, 1);
  if (info.roads.length) own.roads = ownList(own, resolved, "roads").filter((r) => r.a !== info.name && r.b !== info.name);
  if (info.events.length) own.events = ownList(own, resolved, "events").filter((ev) => ev.a !== info.name && ev.b !== info.name && ev.city !== info.name);
  return info;
}

export function renameCity(own, resolved, ci, name) {
  const cs = ownList(own, resolved, "cities");
  const old = cs[ci].name;
  name = String(name).trim();
  if (!name) return { ok: false, error: "A city needs a name." };
  if (name !== old && cs.some((c) => c.name === name)) return { ok: false, error: `There is already a city called ${name}.` };
  cs[ci].name = name;
  if (roadsOf(own, resolved).some((r) => r.a === old || r.b === old))
    for (const r of ownList(own, resolved, "roads")) { if (r.a === old) r.a = name; if (r.b === old) r.b = name; }
  if (eventsOf(own, resolved).some((ev) => [ev.a, ev.b, ev.city].includes(old)))
    for (const ev of ownList(own, resolved, "events")) for (const k of ["a", "b", "city"]) if (ev[k] === old) ev[k] = name;
  return { ok: true };
}

// --------------------------------------------------------------- roads

const samePair = (r, a, b) => (r.a === a && r.b === b) || (r.a === b && r.b === a);

export function addRoad(own, resolved, a, b) {
  if (a === b) return { ok: false, error: "A road needs two different cities." };
  if (roadsOf(own, resolved).some((r) => samePair(r, a, b))) return { ok: false, error: `${a} and ${b} already have a road.` };
  ownList(own, resolved, "roads").push({ a, b });
  return { ok: true, ri: own.roads.length - 1 };
}

export function deleteRoad(own, resolved, ri) { ownList(own, resolved, "roads").splice(ri, 1); }

/** A road that opens on `day` becomes an `add_road` event instead of a road open from day 0. */
export function roadToEvent(own, resolved, ri, day) {
  const roads = ownList(own, resolved, "roads");
  const r = roads.splice(ri, 1)[0];
  ownList(own, resolved, "events").push({ day: Number(day), type: "add_road", ...r });
}

export function eventToRoad(own, resolved, ei) {
  const events = ownList(own, resolved, "events");
  const { day, type, ...rest } = events.splice(ei, 1)[0];
  ownList(own, resolved, "roads").push(rest);
  void day; void type;
}

export function deleteEvent(own, resolved, ei) { ownList(own, resolved, "events").splice(ei, 1); }

// ------------------------------------------------- nodes, workplaces, houses

export function clampOffset(dx, dy, radius) {
  const len = Math.hypot(dx, dy), max = radius * EDGE;
  if (len <= max) return [round2(dx), round2(dy)];
  // scale down, then take the rounding off the outside so the result is never past the edge
  const f = max / len;
  return [Math.trunc(dx * f * 100) / 100, Math.trunc(dy * f * 100) / 100];
}

const KINDS = { resource: "nodes", workplace: "workplaces", house: "houses" };
const CAPS = { resource: MAX_NODES, workplace: MAX_WORKPLACES, house: MAX_HOUSES };

/** Add an item to a city. `offset` null means "place automatically". */
export function addItem(own, resolved, ci, kind, spec, offset) {
  const cs = ownList(own, resolved, "cities");
  const c = cs[ci];
  const r = radiusOf(c, resolved);
  const item = { ...spec };
  if (offset) item.at = clampOffset(offset[0], offset[1], r);
  if (kind === "house") {
    if (houseCount(c) >= CAPS.house) return { ok: false, error: `At most ${CAPS.house} houses per city.` };
    if (!offset && !Array.isArray(c.houses)) {
      c.houses = { count: houseCount(c) + 1 };  // automatic houses stay a plain count
      return { ok: true, i: c.houses.count - 1 };
    }
    housesAsList(c).push(item);
    return { ok: true, i: c.houses.length - 1 };
  }
  const key = KINDS[kind];
  if ((c[key] || []).length >= CAPS[kind]) return { ok: false, error: `At most ${CAPS[kind]} ${key} per city.` };
  (c[key] = c[key] || []).push(item);
  return { ok: true, i: c[key].length - 1 };
}

export function newNode(good) { return { good, ...DEFAULT_NODE }; }

/** The scenario entry of a node, workplace or house, ready to be written to (a house count becomes a list). */
export function entryOf(own, resolved, kind, ci, i) { return entry(own, resolved, kind, ci, i, true); }

function entry(own, resolved, kind, ci, i, forWrite) {
  const c = ownList(own, resolved, "cities")[ci];
  if (kind === "house") return (forWrite ? housesAsList(c) : c.houses)[i];
  return c[KINDS[kind]][i];
}

/** Move an item to an absolute position; it stops at the city edge. Houses of a `{count}` become a list. */
export function moveItem(own, resolved, kind, ci, i, x, y) {
  const c = ownList(own, resolved, "cities")[ci];
  const e = entry(own, resolved, kind, ci, i, true);
  e.at = clampOffset(x - Number(c.x ?? 0), y - Number(c.y ?? 0), radiusOf(c, resolved));
  return e.at;
}

/** Turn an automatic position into an explicit one (at `abs`, the position the preview showed) or back. */
export function setAuto(own, resolved, kind, ci, i, auto, abs) {
  const c = ownList(own, resolved, "cities")[ci];
  const e = entry(own, resolved, kind, ci, i, true);
  if (auto) delete e.at;
  else if (abs) e.at = clampOffset(abs.x - Number(c.x ?? 0), abs.y - Number(c.y ?? 0), radiusOf(c, resolved));
  else e.at = [0, 0];
}

export function deleteItem(own, resolved, kind, ci, i) {
  const c = ownList(own, resolved, "cities")[ci];
  if (kind === "house" && !Array.isArray(c.houses)) {
    const n = houseCount(c) - 1;
    if (n > 0) c.houses = { count: n }; else delete c.houses;
    return;
  }
  const list = kind === "house" ? c.houses : c[KINDS[kind]];
  list.splice(i, 1);
  if (kind === "house" && !list.length) delete c.houses;
  if (kind !== "house" && !list.length && c[KINDS[kind]] === list) delete c[KINDS[kind]];
}

// ----------------------------------------------------------------- history

/** Undo and redo over snapshots of the scenario (as JSON text). */
export class History {
  constructor(limit = 100) { this.limit = limit; this.past = []; this.future = []; }
  push(snapshot) {
    if (this.past[this.past.length - 1] === snapshot) return;
    this.past.push(snapshot);
    if (this.past.length > this.limit) this.past.shift();
    this.future = [];
  }
  undo(current) {
    if (!this.past.length) return null;
    this.future.push(current);
    return this.past.pop();
  }
  redo(current) {
    if (!this.future.length) return null;
    this.past.push(current);
    return this.future.pop();
  }
  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }
}

/** A grid step in km that gives lines roughly `px` pixels apart at the current scale. */
export function gridStep(pxPerKm, px = 60) {
  const raw = px / pxPerKm;
  const pow = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (m * pow >= raw) return m * pow;
  return 10 * pow;
}
