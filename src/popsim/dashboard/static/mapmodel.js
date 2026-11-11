// Map editor model (REQ-1C.8): everything the editor does to a scenario, with no DOM, so it can be
// tested on its own. All functions work on the scenario's *own* mapping (what the file says) and
// the resolved scenario (what it inherits). Lists replace inherited lists, so a function that edits
// `cities`, `roads` or `events` first copies the inherited list into the scenario, as the form does.
// Nothing here ever drops a key it does not know about.

export const MAX_CITIES = 50, MAX_NODES = 200, MAX_WORKPLACES = 100, MAX_HOUSES = 5000, MAX_OVERWORLD_NODES = 500;
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
export function worldNodesOf(own, resolved) { return (own.nodes !== undefined ? own.nodes : resolved && resolved.nodes) || []; }
/** Houses generated for a city (REQ-3.7): round(population * housing_pct / 100); no housing_pct anywhere means none. */
export function housingPct(city, resolved) {
  const v = city.housing_pct != null ? city.housing_pct : resolved && resolved.housing_pct != null ? resolved.housing_pct : 0;
  return Number(v) || 0;
}
export function generatedHouses(city, resolved) {
  return Math.max(0, Math.floor(Number(city.population || 0) * housingPct(city, resolved) / 100 + 0.5));
}
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
    const g = generatedHouses(c, resolved);
    for (let i = 0; i < g; i++) {   // generated first, then the manual ones (the builder's order)
      const moved = (c.house_positions || []).find((hp) => hp.index === i);
      houses.push({ kind: "ghouse", ci, i, ...place(ci, moved, pick(world && world.houses, ci, i)), moved: !!moved, inherited: inh });
    }
    const hl = Array.isArray(c.houses) ? c.houses : Array.from({ length: houseCount(c) }, () => ({}));
    hl.forEach((hs, i) => houses.push({ kind: "house", ci, i, ...place(ci, hs, pick(world && world.houses, ci, g + i)), inherited: inh }));
  });
  const roads = roadsOf(own, resolved).map((r, ri) => ({ ri, a: r.a, b: r.b, length: r.length, hours: r.hours, stamina_cost: r.stamina_cost, event: false, inherited: inherited(own, "roads") }));
  eventsOf(own, resolved).forEach((ev, ei) => {
    if (ev.type === "add_road" || ev.type === "set_road") roads.push({ ri: null, ei, a: ev.a, b: ev.b, day: ev.day, length: ev.length, hours: ev.hours, stamina_cost: ev.stamina_cost, event: true, type: ev.type, inherited: inherited(own, "events") });
  });
  // Resource nodes on the overworld (REQ-3A.11): absolute positions, each with the junction of its branch.
  const byName = Object.fromEntries(cities.map((c) => [c.name, c]));
  const pairs = roadPairs(roads);
  const winh = inherited(own, "nodes");
  const overNodes = worldNodesOf(own, resolved).map((n, oi) => {
    const hit = ((world && world.nodes) || []).find((p) => p.oi === oi);
    const has = Array.isArray(n.at);
    let x = has ? Number(n.at[0]) : hit ? hit.x : 0, y = has ? Number(n.at[1]) : hit ? hit.y : 0;
    let branch = null;
    const br = n.branch && n.branch.road;
    if (br && byName[br[0]] && byName[br[1]]) {
      const t = n.branch.t == null ? 0.5 : Number(n.branch.t), a = byName[br[0]], b = byName[br[1]];
      branch = { road: [br[0], br[1]], t, jx: a.x + (b.x - a.x) * t, jy: a.y + (b.y - a.y) * t };
    } else if (has || hit) {
      const j = nearestJunction(byName, pairs, x, y);
      if (j) branch = { road: j.road, t: j.t, jx: j.jx, jy: j.jy };
    }
    if (!has && !hit && branch) { x = branch.jx; y = branch.jy; }
    return { kind: "node", oi, good: n.good, x, y, auto: !has, pending: !has && !hit, branch, inherited: winh };
  });
  return { cities, nodes, overNodes, workplaces, houses, roads };
}

/** The distinct (a, b) pairs of the roads, in order (REQ-3A.11 branches hang off these). */
export function roadPairs(roads) {
  const out = [];
  for (const r of roads) if (!out.some((p) => (p[0] === r.a && p[1] === r.b) || (p[0] === r.b && p[1] === r.a))) out.push([r.a, r.b]);
  return out;
}

/** The road nearest to a point and the foot of the perpendicular on it, clamped; ties go to the first road. */
export function nearestJunction(byName, pairs, x, y) {
  let best = null;
  for (const [a, b] of pairs) {
    const A = byName[a], B = byName[b];
    if (!A || !B) continue;
    const dx = B.x - A.x, dy = B.y - A.y, L2 = dx * dx + dy * dy;
    const t = L2 === 0 ? 0 : Math.max(0, Math.min(1, ((x - A.x) * dx + (y - A.y) * dy) / L2));
    const jx = A.x + dx * t, jy = A.y + dy * t, d = Math.hypot(x - jx, y - jy);
    if (!best || d < best.d - 1e-12) best = { road: [a, b], t, jx, jy, d };
  }
  return best;
}

/** Map a validation path (`cities[1].nodes[0].at`) to the item it concerns, or null. */
export function errorTarget(path) {
  if (!path) return null;
  let m = /^nodes\[(\d+)\]/.exec(path);
  if (m) return { kind: "node", oi: +m[1] };
  m = /^cities\[(\d+)\]\.(nodes|workplaces|houses)\[(\d+)\]/.exec(path);
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
  // Resource points lie outside cities (REQ-3A.11), so a new city has none; houses come from housing_pct (REQ-3.7).
  cs.push({ name, x: round2(x), y: round2(y), population: 40, housing_pct: 100 });
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
  (c.house_positions || []).forEach((hp) => far(hp) && out.push({ kind: "ghouse", ci, i: hp.index }));
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
  if (kind === "ghouse") {   // a generated house is only moved: the override is stored by its index (REQ-3.7)
    const at = clampOffset(x - Number(c.x ?? 0), y - Number(c.y ?? 0), radiusOf(c, resolved));
    c.house_positions = (c.house_positions || []).filter((hp) => hp.index !== i);
    c.house_positions.push({ index: i, at });
    c.house_positions.sort((a, b) => a.index - b.index);
    return at;
  }
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

/** Put a moved generated house back where the builder placed it. */
export function resetHouse(own, resolved, ci, i) {
  const c = ownList(own, resolved, "cities")[ci];
  c.house_positions = (c.house_positions || []).filter((hp) => hp.index !== i);
  if (!c.house_positions.length) delete c.house_positions;
}

/** Set a city's housing percentage; positions of generated houses that no longer exist are dropped. */
export function setHousingPct(own, resolved, ci, v) {
  const c = ownList(own, resolved, "cities")[ci];
  if (v === undefined) delete c.housing_pct; else c.housing_pct = Math.max(0, Math.min(1000, v));
  const g = generatedHouses(c, resolved);
  const dropped = (c.house_positions || []).filter((hp) => hp.index >= g).length;
  if (dropped) { c.house_positions = c.house_positions.filter((hp) => hp.index < g); if (!c.house_positions.length) delete c.house_positions; }
  return dropped;
}

export function deleteItem(own, resolved, kind, ci, i) {
  if (kind === "ghouse") return { ok: false, error: "Generated houses follow the housing percentage; they can be moved but not deleted." };
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

// ------------------------------------------- resource points on the overworld (REQ-3A.11)

const insideCity = (cities, x, y, pad = 0) => cities.find((c) => Math.hypot(x - c.x, y - c.y) <= c.radius + pad);

function cityLookup(own, resolved) {
  const lay = layoutOf(own, resolved, null);
  return { lay, byName: Object.fromEntries(lay.cities.map((c) => [c.name, c])), pairs: roadPairs(lay.roads) };
}

/** Add a resource point at an absolute position, branching from the nearest road. */
export function addNode(own, resolved, good, x, y) {
  const { lay, byName, pairs } = cityLookup(own, resolved);
  if (insideCity(lay.cities, x, y)) return { ok: false, error: "Resource points go outside the cities." };
  if (worldNodesOf(own, resolved).length >= MAX_OVERWORLD_NODES) return { ok: false, error: `At most ${MAX_OVERWORLD_NODES} resource points.` };
  const j = nearestJunction(byName, pairs, x, y);
  if (!j) return { ok: false, error: "Draw a road first: a resource point branches off a road." };
  const list = ownList(own, resolved, "nodes");
  list.push({ good, ...DEFAULT_NODE, at: [round2(x), round2(y)], branch: { road: j.road, t: round2(j.t) } });
  return { ok: true, oi: list.length - 1 };
}

/** Move a resource point; it stops at the edge of a city it is dragged into, and its branch follows. */
export function moveNode(own, resolved, oi, x, y) {
  const { lay, byName, pairs } = cityLookup(own, resolved);
  const c = insideCity(lay.cities, x, y);
  if (c) {
    const len = Math.hypot(x - c.x, y - c.y) || 1, f = (c.radius + 0.01) / len;
    x = c.x + (x - c.x) * f; y = c.y + (y - c.y) * f;
  }
  const n = ownList(own, resolved, "nodes")[oi];
  n.at = [round2(x), round2(y)];
  const j = nearestJunction(byName, pairs, x, y);
  if (j) n.branch = { road: j.road, t: round2(j.t) };
  return n.at;
}

/** Slide the junction of a resource point to the road nearest to (x, y), possibly another road. */
export function moveJunction(own, resolved, oi, x, y) {
  const { byName, pairs } = cityLookup(own, resolved);
  const j = nearestJunction(byName, pairs, x, y);
  if (!j) return null;
  ownList(own, resolved, "nodes")[oi].branch = { road: j.road, t: round2(j.t) };
  return j;
}

export function deleteNode(own, resolved, oi) { ownList(own, resolved, "nodes").splice(oi, 1); }

// ------------------------------------------------------------------ eraser

export const ERASE_KINDS = [["houses", "Houses"], ["jobs", "Jobs"], ["resources", "Resource nodes"], ["roads", "Roads"], ["cities", "Cities"]];
export const DEFAULT_ERASE = { houses: true, jobs: true, resources: true, roads: false, cities: false };

function segmentDistance(px, py, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y, L2 = dx * dx + dy * dy;
  const t = L2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - a.x) * dx + (py - a.y) * dy) / L2));
  return Math.hypot(px - (a.x + dx * t), py - (a.y + dy * t));
}

/**
 * What an eraser circle at (x, y) with radius r hits, for the kinds that are on. An item is hit when its
 * position is inside the circle, a road when its straight line crosses it, a city when its centre is inside.
 * Generated houses are never hit (their count follows the housing percentage): they are only counted.
 */
export function eraseTargets(lay, circle, kinds) {
  const { x, y, r } = circle;
  const inside = (it) => Math.hypot(it.x - x, it.y - y) <= r;
  const targets = [];
  const skipped = [];
  if (kinds.houses) for (const h of lay.houses) if (inside(h)) { if (h.kind === "ghouse") skipped.push(`ghouse:${h.ci}:${h.i}`); else targets.push({ key: `house:${h.ci}:${h.i}`, kind: "house", ci: h.ci, i: h.i }); }
  if (kinds.jobs) for (const w of lay.workplaces) if (inside(w)) targets.push({ key: `workplace:${w.ci}:${w.i}`, kind: "workplace", ci: w.ci, i: w.i });
  if (kinds.resources) {
    for (const n of lay.nodes) if (inside(n)) targets.push({ key: `resource:${n.ci}:${n.i}`, kind: "resource", ci: n.ci, i: n.i });
    for (const n of lay.overNodes || []) if (inside(n)) targets.push({ key: `node:${n.oi}`, kind: "node", oi: n.oi });
  }
  if (kinds.roads) {
    const byName = Object.fromEntries(lay.cities.map((c) => [c.name, c]));
    for (const rd of lay.roads) {
      const a = byName[rd.a], b = byName[rd.b];
      if (a && b && segmentDistance(x, y, a, b) <= r) targets.push(rd.event ? { key: `event:${rd.ei}`, kind: "event", ei: rd.ei } : { key: `road:${rd.ri}`, kind: "road", ri: rd.ri });
    }
  }
  if (kinds.cities) for (const c of lay.cities) if (inside(c)) targets.push({ key: `city:${c.ci}`, kind: "city", ci: c.ci });
  return { targets, skipped };
}

/** What a set of erase targets takes with it beyond themselves, for the confirmation (none: nothing to ask). */
export function eraseCascade(own, resolved, targets) {
  const cities = targets.filter((t) => t.kind === "city");
  const roadsHit = targets.filter((t) => t.kind === "road" || t.kind === "event");
  const lay = layoutOf(own, resolved, null);
  const names = new Set(cities.map((t) => lay.cities[t.ci].name));
  const roadList = new Set(), events = new Set();
  for (const rd of lay.roads) {
    if (names.has(rd.a) || names.has(rd.b)) (rd.event ? events : roadList).add(rd.event ? rd.ei : rd.ri);
  }
  for (const ev of eventsOf(own, resolved)) void ev;
  const pairKey = (a, b) => [a, b].sort().join("|");
  const dying = new Set();
  for (const t of roadsHit) { const rd = lay.roads.find((r) => (t.kind === "event" ? r.ei === t.ei : r.ri === t.ri)); if (rd) dying.add(pairKey(rd.a, rd.b)); }
  for (const rd of lay.roads) if (names.has(rd.a) || names.has(rd.b)) dying.add(pairKey(rd.a, rd.b));
  const orphans = (lay.overNodes || []).filter((n) => n.branch && dying.has(pairKey(n.branch.road[0], n.branch.road[1]))
    && !targets.some((t) => t.kind === "node" && t.oi === n.oi)).length;
  const evExtra = eventsOf(own, resolved).filter((ev) => names.has(ev.a) || names.has(ev.b) || names.has(ev.city)).length;
  return { cities: cities.length, roads: roadList.size, events: Math.max(events.size, evExtra), orphanedNodes: orphans };
}

/** Delete the targets in an order that keeps the other indexes valid. Returns counts per kind. */
export function applyErase(own, resolved, targets) {
  const done = {};
  const bump = (k) => { done[k] = (done[k] || 0) + 1; };
  const byDesc = (a, b) => b - a;
  const items = targets.filter((t) => ["house", "workplace", "resource"].includes(t.kind))
    .sort((a, b) => a.ci - b.ci || byDesc(a.i, b.i));
  for (const t of items) { deleteItem(own, resolved, t.kind, t.ci, t.i); bump(t.kind); }
  for (const t of targets.filter((x) => x.kind === "node").sort((a, b) => byDesc(a.oi, b.oi))) { deleteNode(own, resolved, t.oi); bump("node"); }
  for (const t of targets.filter((x) => x.kind === "road").sort((a, b) => byDesc(a.ri, b.ri))) { deleteRoad(own, resolved, t.ri); bump("road"); }
  for (const t of targets.filter((x) => x.kind === "event").sort((a, b) => byDesc(a.ei, b.ei))) { deleteEvent(own, resolved, t.ei); bump("event"); }
  for (const t of targets.filter((x) => x.kind === "city").sort((a, b) => byDesc(a.ci, b.ci))) { deleteCity(own, resolved, t.ci); bump("city"); }
  return done;
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
