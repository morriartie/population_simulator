// Policies (REQ-5A): one card per policy with its state, an on/off switch in a live run, what its rules decided,
// the sites it has made (a fertility map with each site coloured by its tag, a progress ring while one grows) and
// what each open task is waiting for. The card is built from data, so any policy gets it; the map appears when the
// policy has a fertility field.
import { h, api, toast, cityColor, cssVar, num } from "./util.js";
import { LineChart, legend } from "./charts.js";

const LOW = [238, 240, 234], HIGH = [47, 125, 109];
const mix = (t) => LOW.map((lo, i) => Math.round(lo + (HIGH[i] - lo) * t));

function settingsRows(settings) {
  return Object.entries(settings || {})
    .filter(([k, v]) => k !== "enabled" && (typeof v !== "object" || v === null))
    .map(([k, v]) => h("tr", {}, h("td", {}, k.replaceAll("_", " ")), h("td", { class: "num" }, String(v))));
}

function legendItems(items) {
  return h("div", { class: "legend" }, items.map(([name, color]) => h("span", {}, h("i", { class: "sq", style: `background:${color}` }), name)));
}

// What the sites of a policy looked like on day d, rebuilt from the events (so the scrubber works on a saved run).
export function sitesAt(events, policy, d) {
  const out = new Map();
  for (const e of events) {
    if (e.policy !== policy || e.day > d) continue;
    if (e.kind === "planted") out.set(e.site, { id: e.site, x: e.x, y: e.y, city: e.city, process: e.process, tags: e.tags || [], since: e.day, ready: null, grown: null });
    else if (e.kind === "stage" && out.has(e.site)) {
      const s = out.get(e.site);
      s.tags = e.tags || [];
      if (e.ready_day != null) { s.ready = e.ready_day; s.since = e.day; }
    } else if (e.kind === "matured" && out.has(e.site)) out.get(e.site).grown = e.good;
    else if (e.kind === "cancelled") out.delete(e.site);
  }
  return [...out.values()];
}

class PolicyView {
  constructor(panel, policy) {
    this.panel = panel;
    this.policy = policy;
    this.day = 0;
    this.charts = [];
    this.status = h("div");
    this.sitesHost = h("div");
    this.shareHost = h("div");
    this.sitesChartHost = h("div");
    this.legendShare = h("div");
    this.legendHost = h("div");
    this.mapNote = h("p", { class: "muted" });
    this.canvas = h("canvas", { role: "img", "aria-label": "Fertility map with cities, roads, food spots and the sites being planted" });
    const g = policy.fertility;
    this.mapPanel = g ? h("div", { class: "panel" }, h("h3", {}, "Fertility f(x, y) and sites"), this.legendHost, this.canvas, this.mapNote) : null;
    const opts = { yMin: 0, onPick: (d) => panel.rv.pick(d) };
    this.shareChart = new LineChart(this.shareHost, { ...opts, label: "Residents at the urgent level", unit: "%" });
    this.sitesChart = new LineChart(this.sitesChartHost, { ...opts, label: "Sites in progress" });
    this.charts.push(this.shareChart, this.sitesChart);
    this.el = h("div", { class: "policy-body" },
      this.status,
      h("div", { class: "policy-grid" },
        this.mapPanel || h("div", { class: "panel" }, h("h3", {}, "Sites in progress"), this.sitesChartHost),
        h("div", {},
          h("div", { class: "panel" }, h("h3", {}, "Residents at the urgent level"), this.legendShare, this.shareHost),
          this.mapPanel ? h("div", { class: "panel" }, h("h3", {}, "Sites in progress"), this.sitesChartHost) : "")),
      this.sitesHost);
  }

  rows() { return this.panel.rv.data.policy_daily.filter((r) => r.policy === this.policy.name); }
  events() { return this.panel.rv.data.policy_events.filter((e) => e.policy === this.policy.name || (e.policy == null && e.kind.startsWith("office"))); }

  refresh() {
    const rv = this.panel.rv, cities = rv.cities(), rows = this.rows();
    const rules = [...new Set(rows.map((r) => r.rule))];
    const many = rules.length > 1;
    const share = [];
    cities.forEach((c, i) => {
      for (const rule of rules) {
        share.push({ name: many ? `${c} (${rule})` : c, color: cityColor(i), tipColor: cityColor(i), dash: many && rules.indexOf(rule) > 0,
          points: rows.filter((r) => r.city === c && r.rule === rule).map((r) => [r.day, r.share * 100]) });
      }
    });
    const thr = rows.length ? rows[0].threshold : this.policy.threshold;
    if (thr != null && rows.length && thr < 1) {
      share.push({ name: `threshold ${num(thr * 100, 0)}%`, color: cssVar("--madder"), tipColor: cssVar("--madder"), dash: true,
        points: [...new Set(rows.map((r) => r.day))].map((d) => [d, thr * 100]) });
    }
    const marks = rv.data.events.filter((e) => e.type === "policy").map((e) => ({ x: Number(e.day), label: `${e.policy} ${e.enabled ? "on" : "off"}` }));
    const xMax = rv.live ? Math.max(rv.maxDay(), (rv.data.config.time || {}).days || 0) : null;
    this.shareChart.opts.xMax = xMax;
    this.shareChart.set(share, marks);
    this.legendShare.replaceChildren(legend(share));
    const act = cities.map((c, i) => ({ name: c, color: cityColor(i), tipColor: cityColor(i),
      points: rows.filter((r) => r.city === c && r.rule === rules[0]).map((r) => [r.day, r.plots_growing]) }));
    this.sitesChart.opts.xMax = xMax;
    this.sitesChart.set(act, marks);
    this.setDay(this.day);
  }

  setDay(d) {
    this.day = d;
    const rv = this.panel.rv;
    const latest = new Map();
    for (const r of this.rows()) if (r.day <= d) latest.set(`${r.rule}|${r.city}`, r);
    const head = h("tr", {}, h("th", {}, "Rule"), h("th", {}, "City"), h("th", { class: "num" }, "Signal"), h("th", { class: "num" }, "Threshold"),
      h("th", { class: "num" }, "Held (days)"), h("th", { class: "num" }, "Sites"), h("th", {}, "Decision"));
    const body = [];
    for (const rule of this.policy.rules || []) {
      rv.cities().forEach((c, i) => {
        const r = latest.get(`${rule}|${c}`);
        const sw = h("span", { class: "swatch", style: `background:${cityColor(i)}` });
        if (!r) { body.push(h("tr", {}, h("td", {}, rule), h("td", {}, sw, c), h("td", { colspan: 5, class: "muted" }, "no check yet"))); return; }
        body.push(h("tr", {}, h("td", {}, rule), h("td", {}, sw, c),
          h("td", { class: "num" }, `${num(r.share * 100, 0)}% (${r.urgent}/${r.residents})`),
          h("td", { class: "num" }, r.threshold < 1 ? `${num(r.threshold * 100, 0)}%` : `${r.threshold}`),
          h("td", { class: "num" }, r.held_days),
          h("td", { class: "num" }, r.plots_growing),
          h("td", { class: /^(created|exists)/.test(r.action) ? "good" : r.action.startsWith("blocked") ? "bad" : "muted" }, r.action)));
      });
    }
    this.status.replaceChildren(h("table", { class: "city-figures" }, h("thead", {}, head), h("tbody", {}, body)));
    this.renderSites(d);
    this.drawMap();
  }

  renderSites(d) {
    const rv = this.panel.rv;
    const live = rv.live ? (rv.now && rv.now.sites) : rv.data.sites;
    const list = (live || []).filter((s) => s.policy === this.policy.name);
    const offices = this.events().filter((e) => e.day <= d && e.kind.startsWith("office"));
    this.sitesHost.replaceChildren(
      list.length ? h("details", { open: true }, h("summary", {}, `Open sites and what they wait for (${list.length})`),
        h("table", { class: "mini" },
          h("thead", {}, h("tr", {}, h("th", {}, "Site"), h("th", {}, "City"), h("th", {}, "Tags"), h("th", {}, "Waiting for"), h("th", {}, "Blocked by"))),
          h("tbody", {}, list.map((s) => h("tr", {},
            h("td", {}, `${s.process} #${s.id}`), h("td", {}, s.city), h("td", {}, s.tags.join(", ").replaceAll("_", " ")),
            h("td", {}, Object.values(s.requirements).flat().filter((q) => !q.met).map((q) => q.label).join("; ") || "–"),
            h("td", { class: "bad" }, Object.values(s.blockers).flat().join("; ") || ""))))))
        : "",
      offices.length ? h("details", {}, h("summary", {}, `Offices (${offices.length} events)`),
        h("table", { class: "mini" }, h("tbody", {}, offices.map((e) => h("tr", {}, h("td", {}, `Day ${e.day}`), h("td", {}, e.city),
          h("td", {}, e.kind === "office_opened" ? `opened a ${e.task.replaceAll("_", " ")} office` : `closed the ${e.task.replaceAll("_", " ")} office`)))))) : "");
  }

  drawMap() {
    const g = this.policy.fertility;
    if (!g || !this.mapPanel) return;
    const rv = this.panel.rv, d = this.day;
    const CELL = 10;
    this.canvas.width = g.nx * CELL;
    this.canvas.height = g.ny * CELL;
    this.canvas.style.aspectRatio = `${g.nx} / ${g.ny}`;
    const ctx = this.canvas.getContext("2d");
    const X = (x) => (x - g.x0) / g.cell * CELL;
    const Y = (y) => this.canvas.height - (y - g.y0) / g.cell * CELL;  // y points up, as on the world map
    const S = CELL / g.cell;
    for (let j = 0; j < g.ny; j++) for (let i = 0; i < g.nx; i++) {
      const f = g.values[j][i];
      ctx.fillStyle = f < g.min_fertility ? "#E4E1D6" : `rgb(${mix(0.18 + 0.82 * f).join(",")})`;
      ctx.fillRect(i * CELL, this.canvas.height - (j + 1) * CELL, CELL, CELL);
    }
    const world = rv.data.world || { cities: [], roads: [], nodes: [] };
    const city = (n) => world.cities.find((c) => c.name === n);
    ctx.lineWidth = 2;
    ctx.strokeStyle = cssVar("--ink-2");
    for (const r of world.roads || []) {
      const a = city(r.a), b = city(r.b);
      if (!a || !b) continue;
      ctx.beginPath(); ctx.moveTo(X(a.x), Y(a.y)); ctx.lineTo(X(b.x), Y(b.y)); ctx.stroke();
    }
    ctx.fillStyle = cssVar("--sheet");
    ctx.strokeStyle = cssVar("--ink");
    world.cities.forEach((c, i) => {
      ctx.beginPath(); ctx.arc(X(c.x), Y(c.y), c.radius * S, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = cityColor(i); ctx.font = "bold 13px system-ui"; ctx.textAlign = "center";
      ctx.fillText(c.name, X(c.x), Y(c.y) + 4); ctx.fillStyle = cssVar("--sheet");
    });
    const sites = sitesAt(rv.data.policy_events, this.policy.name, d);
    const siteIds = new Set(sites.map((s) => s.id));
    // food spots that were there from the start (the world layout of a live run may already include grown ones)
    ctx.fillStyle = cssVar("--ochre");
    for (const n of world.nodes || []) {
      if (!n.overworld || siteIds.has(n.id) || this.grownLater(n)) continue;
      ctx.beginPath(); ctx.arc(X(n.x), Y(n.y), 5, 0, Math.PI * 2); ctx.fill();
    }
    let waiting = 0, growing = 0, grown = 0;
    for (const s of sites) {
      ctx.lineWidth = 1.5; ctx.strokeStyle = cssVar("--ink");
      if (s.grown) {
        grown++;
        ctx.fillStyle = s.grown === "wood" ? cssVar("--indigo") : cssVar("--olive");
        ctx.fillRect(X(s.x) - 6, Y(s.y) - 6, 12, 12); ctx.strokeRect(X(s.x) - 6, Y(s.y) - 6, 12, 12);
      } else if (s.tags.includes("growing")) {
        growing++;
        const total = Math.max(0.01, (s.ready ?? d + 1) - s.since), t = Math.max(0, Math.min(1, (d - s.since) / total));
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = cssVar("--line"); ctx.beginPath(); ctx.arc(X(s.x), Y(s.y), 8, 0, Math.PI * 2); ctx.stroke();
        ctx.strokeStyle = cssVar("--olive"); ctx.beginPath(); ctx.arc(X(s.x), Y(s.y), 8, -Math.PI / 2, -Math.PI / 2 + t * Math.PI * 2); ctx.stroke();
      } else {
        waiting++;
        ctx.lineWidth = 2.5; ctx.strokeStyle = cssVar("--madder");
        ctx.beginPath(); ctx.arc(X(s.x), Y(s.y), 8, 0, Math.PI * 2); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(X(s.x) - 3, Y(s.y)); ctx.lineTo(X(s.x) + 3, Y(s.y)); ctx.moveTo(X(s.x), Y(s.y) - 3); ctx.lineTo(X(s.x), Y(s.y) + 3); ctx.stroke();
      }
    }
    this.mapNote.textContent = `Day ${d}: ${waiting} waiting for seeds or labor, ${growing} growing, ${grown} grown into spots.`;
    this.legendHost.replaceChildren(legendItems([
      ["Fertile land (darker is better)", `rgb(${mix(0.8).join(",")})`], ["Too poor to plant", "#E4E1D6"], ["Food spot", cssVar("--ochre")],
      ["Site waiting for seeds or labor", cssVar("--madder")], ["Growing (ring shows progress)", cssVar("--olive")],
      ["Grown field", cssVar("--olive")], ["Grown grove", cssVar("--indigo")]]));
  }

  // Nodes in the world layout of a live run that grew after this view's day (so they are drawn only later).
  grownLater(n) {
    const m = this.panel.rv.data.policy_events.find((e) => e.kind === "matured" && e.node === n.id);
    return !!m && m.day > this.day;
  }
}

// -- panel -------------------------------------------------------------------------------------------

export class PoliciesPanel {
  constructor(rv) {
    this.rv = rv;
    this.cards = [];
    this.el = h("section", { class: "policies", "aria-label": "Policies" });
  }

  get charts() { return this.cards.flatMap((c) => (c.view ? c.view.charts : [])); }

  shown() {
    const d = this.rv.data;
    return (d.policies || []).length > 0 && (this.rv.live || d.policy_daily.length > 0 || d.policies.some((p) => p.enabled));
  }

  enabled(p) {
    const now = this.rv.live && this.rv.now && this.rv.now.policies;
    return now && p.name in now ? now[p.name] : p.enabled;
  }

  build() {
    this.cards = (this.rv.data.policies || []).map((p) => {
      const card = { policy: p, view: new PolicyView(this, p) };
      card.badge = h("span", { class: "badge" });
      card.btn = this.rv.live ? h("button", { type: "button", onclick: () => this.toggle(card) }) : null;
      card.body = h("div");
      card.el = h("article", { class: "policy-card" },
        h("header", {}, h("h3", {}, p.title), card.badge, card.btn),
        h("p", { class: "muted" }, p.description),
        card.view.el,
        h("details", {}, h("summary", {}, "Rules and settings"), h("table", { class: "mini" }, h("tbody", {}, settingsRows(p.settings)))),
        card.body);
      return card;
    });
    this.el.replaceChildren(...this.cards.map((c) => c.el));
    this.el.hidden = !this.shown();
    this.refresh();
  }

  async toggle(card) {
    const next = !this.enabled(card.policy);
    try {
      await api("/api/session/control", { method: "POST", body: { action: "policy", value: { name: card.policy.name, enabled: next } } });
      if (this.rv.now) this.rv.now.policies = { ...(this.rv.now.policies || {}), [card.policy.name]: next };
      this.refresh();
      toast(`${card.policy.title} policy ${next ? "on" : "off"}`);
    } catch (e) {
      toast(e.message, "error");
    }
  }

  refresh() {
    this.el.hidden = !this.shown();
    for (const c of this.cards) {
      const on = this.enabled(c.policy);
      c.badge.textContent = on ? "On" : "Off";
      c.badge.className = `badge ${on ? "on" : "off"}`;
      if (c.btn) c.btn.textContent = on ? "Switch off" : "Switch on";
      c.view.refresh();
      const evs = this.rv.data.policy_events.filter((e) => e.policy === c.policy.name && ["planted", "matured", "cancelled"].includes(e.kind)).slice(-8).reverse();
      const text = (e) => e.kind === "planted" ? `commissioned a ${e.process} at (${num(e.x, 1)}, ${num(e.y, 1)}), fertility ${num(e.fertility, 2)}`
        : e.kind === "matured" ? `a ${e.process} became a ${e.good} spot at (${num(e.x, 1)}, ${num(e.y, 1)}), up to ${e.max_amount}`
        : `a ${e.process} was cancelled (${e.why})`;
      c.body.replaceChildren(evs.length ? h("details", {}, h("summary", {}, `Recent actions (${evs.length} shown)`),
        h("table", { class: "mini" }, h("tbody", {}, evs.map((e) => h("tr", {}, h("td", {}, `Day ${e.day}`), h("td", {}, e.city), h("td", {}, text(e))))))) : "");
    }
  }

  setDay(d) { for (const c of this.cards) c.view.setDay(d); }
}
