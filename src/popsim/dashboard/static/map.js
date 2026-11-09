// World map: cities, roads (as of a given day), prices, travellers and shipments.
import { cityColor, cssVar } from "./util.js";

const NS = "http://www.w3.org/2000/svg";
const W = 1000, H = 560;

function el(tag, attrs = {}, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}

export function eventDay(ev, cfg) {
  if (ev.day != null) return Number(ev.day) + (Number(ev.hour) || 0) / 24;
  const h = (cfg && cfg.time && cfg.time.hours_per_tick) || 1;
  return (Number(ev.tick) || 0) * h / 24;
}

// Pixels per km on the world map, set by layout() when cities have coordinates.
let SCALE = 1;

export function layout(cities, world) {
  const n = cities.length;
  const pos = {};
  if (world && world.spatial && world.cities && world.cities.length) {
    // Real coordinates (REQ-3A.1), to scale: fit the cities and their areas into the frame, y up.
    const pts = world.cities;
    const x0 = Math.min(...pts.map((c) => c.x - c.radius)), x1 = Math.max(...pts.map((c) => c.x + c.radius));
    const y0 = Math.min(...pts.map((c) => c.y - c.radius)), y1 = Math.max(...pts.map((c) => c.y + c.radius));
    const mx = 110, my = 100;
    SCALE = Math.min((W - 2 * mx) / Math.max(1e-6, x1 - x0), (H - 2 * my) / Math.max(1e-6, y1 - y0));
    const offX = (W - SCALE * (x1 - x0)) / 2, offY = (H - SCALE * (y1 - y0)) / 2;
    for (const c of pts) pos[c.name] = [offX + (c.x - x0) * SCALE, H - (offY + (c.y - y0) * SCALE)];
    return pos;
  }
  SCALE = 1;
  cities.forEach((c, i) => {
    if (Array.isArray(c.pos) && c.pos.length === 2) {
      pos[c.name] = [80 + c.pos[0] * (W - 160), 70 + c.pos[1] * (H - 140)];
    } else if (n === 1) pos[c.name] = [W / 2, H / 2];
    else if (n === 2) pos[c.name] = i === 0 ? [270, 300] : [730, 250];
    else {
      const a = -Math.PI / 2 + (i / n) * Math.PI * 2;
      pos[c.name] = [W / 2 + Math.cos(a) * 340, H / 2 + 10 + Math.sin(a) * 195];
    }
  });
  return pos;
}

// A road with every value filled in: explicit ones as given, the rest derived from the cities'
// positions and the movement rates (REQ-3A.2), as the simulation does.
function completeRoad(r, world, cfg) {
  if (!world || !world.spatial) return r;
  const a = world.cities.find((c) => c.name === r.a), b = world.cities.find((c) => c.name === r.b);
  if (!a || !b) return r;
  const mv = (cfg && cfg.movement && cfg.movement.road) || { speed: 5, stamina_per_unit: 0.5 };
  const length = r.length ?? Math.hypot(a.x - b.x, a.y - b.y);
  return { ...r, length, hours: r.hours ?? length / mv.speed, stamina_cost: r.stamina_cost ?? length * mv.stamina_per_unit };
}

export function roadsAt(cfg, day) {
  const roads = new Map();
  const key = (a, b) => [a, b].sort().join("|");
  for (const r of cfg.roads || []) roads.set(key(r.a, r.b), { ...r, since: 0 });
  const future = [];
  const evs = [...(cfg.events || [])].sort((x, y) => eventDay(x, cfg) - eventDay(y, cfg));
  for (const ev of evs) {
    if (!["add_road", "set_road", "remove_road"].includes(ev.type)) continue;
    const d = eventDay(ev, cfg);
    if (d > day) { if (ev.type !== "remove_road") future.push({ ...ev, opens: d }); continue; }
    if (ev.type === "remove_road") roads.delete(key(ev.a, ev.b));
    else roads.set(key(ev.a, ev.b), { ...ev, since: d });
  }
  return { active: [...roads.values()], future };
}

/**
 * state: { cities: {name: {population, prices: {good: v}, sick}}, transit: [{from,to,npcs}],
 *          shipped: {"A|B|wood": qty}, day }
 */
export function drawMap(host, cfg, state, { world = null, onCity = null } = {}) {
  const cities = cfg.cities || [];
  const goods = cfg.goods || ["food", "wood"];
  const pos = layout(cities, world);
  const spatial = !!(world && world.spatial);
  const area = (name) => (world && world.cities.find((c) => c.name === name)) || null;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "World map" });
  const ink2 = cssVar("--ink-2"), ink3 = cssVar("--ink-3"), line = cssVar("--line");

  // faint survey grid
  const grid = el("g", { stroke: cssVar("--line-2"), "stroke-width": 1 });
  for (let x = 50; x < W; x += 50) grid.append(el("line", { x1: x, y1: 0, x2: x, y2: H }));
  for (let y = 40; y < H; y += 50) grid.append(el("line", { x1: 0, y1: y, x2: W, y2: y }));
  svg.append(grid);

  const roadsNow = roadsAt(cfg, state.day);
  const active = roadsNow.active.map((r) => completeRoad(r, world, cfg));
  const future = roadsNow.future.map((r) => completeRoad(r, world, cfg));
  const roadLayer = el("g");
  for (const r of future) {
    if (!pos[r.a] || !pos[r.b]) continue;
    const [x1, y1] = pos[r.a], [x2, y2] = pos[r.b];
    roadLayer.append(el("line", { x1, y1, x2, y2, stroke: ink3, "stroke-width": 1.5, "stroke-dasharray": "3 7" }));
    roadLayer.append(el("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 10, "text-anchor": "middle", class: "map-road-label" },
      `road opens day ${Math.round(r.opens * 10) / 10}`));
  }
  for (const r of active) {
    if (!pos[r.a] || !pos[r.b]) continue;
    const [x1, y1] = pos[r.a], [x2, y2] = pos[r.b];
    const total = Object.entries(state.shipped || {})
      .filter(([k]) => { const [f, t] = k.split("|"); return (f === r.a && t === r.b) || (f === r.b && t === r.a); })
      .reduce((s, [, q]) => s + q, 0);
    const width = 3 + Math.min(9, Math.log10(1 + total) * 3);
    roadLayer.append(el("line", { x1, y1, x2, y2, stroke: ink2, "stroke-width": width, "stroke-linecap": "round", opacity: 0.85 }));
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    const ang = Math.atan2(y2 - y1, x2 - x1);
    const nx = -Math.sin(ang), ny = Math.cos(ang);
    roadLayer.append(el("text", { x: mx + nx * 30, y: my + ny * 30 + 6, "text-anchor": "middle", class: "map-road-label" },
      `${spatial && r.length != null ? `${Math.round(r.length * 10) / 10} km, ` : ""}${Math.round(r.hours * 10) / 10} h, ${Math.round(r.stamina_cost * 10) / 10} stamina`));
    // travellers as dots, offset to each side by direction
    for (const tr of state.transit || []) {
      const fwd = tr.from === r.a && tr.to === r.b, back = tr.from === r.b && tr.to === r.a;
      if (!fwd && !back) continue;
      const side = fwd ? -1 : 1;
      const n = Math.min(tr.npcs, 14);
      for (let i = 0; i < n; i++) {
        const t = 0.25 + (0.5 * (i + 0.5)) / n;
        const px = x1 + (x2 - x1) * t + nx * side * 12, py = y1 + (y2 - y1) * t + ny * side * 12;
        roadLayer.append(el("circle", { cx: px, cy: py, r: 3.2, fill: cityColor(cities.findIndex((c) => c.name === tr.from)) }));
      }
    }
    // shipments
    const lines = Object.entries(state.shipped || {})
      .filter(([k]) => { const [f, t] = k.split("|"); return (f === r.a && t === r.b) || (f === r.b && t === r.a); })
      .sort((a, b) => b[1] - a[1]).slice(0, 3);
    lines.forEach(([k, q], i) => {
      const [f, t, g] = k.split("|");
      roadLayer.append(el("text", { x: mx - nx * 34, y: my - ny * 34 + 6 + i * 24 - (lines.length - 1) * 12, "text-anchor": "middle", class: "map-road-label" },
        `${f} to ${t}: ${Math.round(q).toLocaleString()} ${g}`));
    });
  }
  svg.append(roadLayer);

  const cityLayer = el("g");
  cities.forEach((c, i) => {
    const [x, y] = pos[c.name];
    const s = (state.cities || {})[c.name] || {};
    const pop = s.population ?? c.population;
    const ar = spatial && area(c.name) ? area(c.name).radius * SCALE : 0;  // the city's real extent, to scale
    const r = spatial ? Math.max(22, Math.min(90, ar)) : 18 + Math.sqrt(Math.max(0, pop)) * 3.2;
    const col = cityColor(i);
    const g = el("g", onCity ? { class: "map-city", role: "button", tabindex: 0, "aria-label": `Open the map of ${c.name}` } : {});
    if (onCity) {
      g.addEventListener("click", () => onCity(c.name));
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onCity(c.name); } });
    }
    g.append(el("circle", { cx: x, cy: y, r: r + 7, fill: "none", stroke: col, "stroke-width": 1, opacity: 0.45 }));
    g.append(el("circle", { cx: x, cy: y, r, fill: cssVar("--sheet") }));
    g.append(el("circle", { cx: x, cy: y, r, fill: col, "fill-opacity": 0.16, stroke: col, "stroke-width": 2.5 }));
    g.append(el("text", { x, y: y + 6, "text-anchor": "middle", class: "map-pop" }, `${pop}`));
    g.append(el("text", { x, y: y - r - 18, "text-anchor": "middle", class: "map-city-name" }, c.name));
    cityLayer.append(g);
    const prices = goods.map((g) => {
      const v = s.prices ? s.prices[g] : null;
      return `${g} ${v == null ? "–" : Number(v).toFixed(2)}`;
    }).join("    ");
    cityLayer.append(el("text", { x, y: y + r + 32, "text-anchor": "middle", class: "map-price" }, prices));
    if (s.sick) cityLayer.append(el("text", { x, y: y + r + 56, "text-anchor": "middle", class: "map-note" }, `${s.sick} sick`));
  });
  svg.append(cityLayer);

  svg.append(el("text", { x: 20, y: H - 18, class: "map-note" }, `Prices in gold. Road width grows with goods shipped; dots are people on the road.${spatial ? " Drawn to scale." : ""}${onCity ? " Click a city to see its streets." : ""}`));
  void line;
  host.replaceChildren(svg);
}
