// Run view: atlas (map + day ledger + scrubber), price/society/trade charts and
// tables. Works for saved runs (setData) and live runs (append + setNow).
import { h, cityColor, CITY_COLORS, num, truthy } from "./util.js";
import { LineChart, legend, smooth } from "./charts.js";
import { drawMap, eventDay } from "./map.js";
import { PoliciesPanel } from "./policies.js";

const SOCIETY = [
  ["population", "Population"],
  ["mean_gold", "Mean gold per person"],
  ["gini", "Gold inequality (Gini)"],
  ["mean_satiety", "Mean satiety"],
  ["mean_daily_max", "Mean daily stamina ceiling"],
  ["sick", "Sick"],
];

// Public finance (REQ-5.5): shown only when the scenario taxes, pays support or has public jobs.
const FINANCE = [
  ["treasury", "Treasury (gold)"],
  ["tax_collected", "Taxes collected per day (gold)"],
  ["wages_paid", "Public wages paid per day (gold)"],
  ["support_paid", "Support paid per day (gold)"],
];

function eventLabel(ev) {
  switch (ev.type) {
    case "stimulus": return "stimulus";
    case "mint": return `mint ${ev.city ?? ""}`;
    case "add_road": case "set_road": return `road ${ev.a}–${ev.b}`;
    case "remove_road": return `road closed ${ev.a}–${ev.b}`;
    default: return ev.type;
  }
}

export class RunView {
  constructor(root, { live = false, onCity = null } = {}) {
    this.root = root;
    this.live = live;
    this.onCity = onCity;
    this.data = null;
    this.day = 0;
    this.follow = true;
    this.city = "";
    this.log = true;
    this.window = 7;      // rolling window in days, used while ``smoothOn``
    this.smoothOn = false;
    this.now = null;
    this.charts = [];
    this._replay = null;
    this._raf = null;
  }

  destroy() {
    for (const c of this.charts) c.destroy();
    this.charts = [];
    clearInterval(this._replay);
    cancelAnimationFrame(this._raf);
  }

  // -- data ---------------------------------------------------------------
  setData(data) {
    this.data = {
      config: data.config || { cities: [], roads: [], events: [], goods: ["food", "wood"] },
      prices_daily: data.prices_daily || [], cities_daily: data.cities_daily || [],
      flows: data.flows || [], deaths: data.deaths || [], events: data.events || [],
      checks: data.checks || [], transit: data.transit || [], summary: data.summary || null,
      world: data.world || null,
      policies: data.policies || [], policy_daily: data.policy_daily || [], policy_events: data.policy_events || [],
      signal_daily: data.signal_daily || [], jobs_daily: data.jobs_daily || [], sites: data.sites || [],
    };
    if (data.now) this.now = data.now;
    this.day = this.live ? this.maxDay() : 0;
    this.build();
  }

  append(delta, now) {
    if (!this.data) return;
    for (const [k, rows] of Object.entries(delta || {})) {
      if (this.data[k]) this.data[k].push(...rows);
    }
    if (now) this.now = now;
    if (this.follow) this.day = this.maxDay();
    this.scheduleRefresh();
  }

  scheduleRefresh() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = null; this.refresh(); });
  }

  maxDay() {
    const rows = this.data.cities_daily;
    const last = rows.length ? rows[rows.length - 1].day : 0;
    if (this.live && this.now) return Math.max(last, this.now.day);
    return last;
  }

  fiscalOn() {
    const cfg = this.data.config || {};
    const on = (x) => !!x && (x.income_tax > 0 || x.sales_tax > 0 || (x.support && x.support.amount > 0));
    return on(cfg.fiscal) || (cfg.cities || []).some((c) => on(c.fiscal) || (c.workplaces || []).length > 0);
  }

  cities() { return (this.data.config.cities || []).map((c) => c.name); }
  goods() { return this.data.config.goods || ["food", "wood"]; }

  priceSeries(city, good) {
    const rows = this.data.prices_daily.filter((r) => r.city === city && r.good === good);
    return rows.map((r) => [r.day, r.vwap ?? r.last ?? null]);
  }

  // -- layout ---------------------------------------------------------------
  build() {
    this.destroy();
    const cities = this.cities();
    const goods = this.goods();

    this.mapHost = h("div", { class: "map" });
    this.ledgerHost = h("div", { class: "ledger" });
    this.dayLabel = h("span", { class: "day" });
    this.slider = h("input", {
      type: "range", min: 0, max: Math.max(0, this.maxDay()), step: 1, value: this.day,
      "aria-label": "Day", oninput: (e) => { this.follow = false; this.setDay(+e.target.value); this.updateFollow(); },
    });
    this.replayBtn = h("button", { type: "button", onclick: () => this.toggleReplay() }, "Replay");
    this.followBtn = h("button", { type: "button", hidden: true, onclick: () => { this.follow = true; this.setDay(this.maxDay()); this.updateFollow(); } }, "Follow live");
    const scrub = h("div", { class: "scrub" }, this.dayLabel, this.slider, this.live ? this.followBtn : this.replayBtn);
    const atlas = h("section", { class: "atlas", "aria-label": "World map" }, this.mapHost, this.ledgerHost, scrub);

    const logBtn = h("button", { type: "button", class: "toggle", "aria-pressed": String(this.log),
      onclick: (e) => { this.log = !this.log; e.target.setAttribute("aria-pressed", String(this.log)); this.priceCharts.forEach((c) => c.setLog(this.log)); } }, "Log scale");
    const smoothBtn = h("button", { type: "button", class: "toggle", "aria-pressed": String(this.smoothOn),
      onclick: (e) => { this.smoothOn = !this.smoothOn; e.target.setAttribute("aria-pressed", String(this.smoothOn)); this.refresh(); } }, "Smooth");
    const smoothN = h("input", { type: "number", min: 2, max: 90, step: 1, value: this.window, "aria-label": "Smoothing window in days",
      oninput: (e) => { const n = Math.floor(+e.target.value); if (n >= 2 && n <= 90) { this.window = n; if (this.smoothOn) this.refresh(); } } });
    const smoothBar = h("div", { class: "toolbar" }, smoothBtn, "rolling average over", smoothN, "days",
      h("span", { class: "muted" }, "Applies to every daily chart; cumulative shipments are left as they are."));
    const toolbar = h("div", { class: "toolbar" }, logBtn,
      h("span", { class: "muted" }, "Daily volume-weighted price in gold. Days without trades carry the last trade price. Click a chart to jump to that day."));

    // prices
    this.priceCharts = [];
    const priceGrid = h("div", { class: "chart-grid", style: `grid-template-columns:repeat(${Math.min(goods.length, 3)}, minmax(0, 1fr))` });
    for (const g of goods) {
      const host = h("div");
      const panel = h("div", { class: "panel" }, h("h3", {}, `Price of ${g}`),
        legend(cities.map((c, i) => ({ name: c, color: cityColor(i) }))), host);
      priceGrid.append(panel);
      const ch = new LineChart(host, { log: this.log, tall: true, label: `Price of ${g}`, onPick: (d) => this.pick(d) });
      ch.good = g;
      this.priceCharts.push(ch);
    }
    // society
    this.societyCharts = [];
    const socGrid = h("div", { class: "chart-grid" });
    for (const [key, title] of SOCIETY) {
      const host = h("div");
      socGrid.append(h("div", { class: "panel" }, h("h3", {}, title), host));
      const ch = new LineChart(host, { label: title, yMin: 0, onPick: (d) => this.pick(d) });
      ch.key = key;
      this.societyCharts.push(ch);
    }
    // trade
    this.tradeHost = h("div");
    this.tradeLegend = h("div");
    this.tradePanel = h("div", { class: "panel" }, h("h3", {}, "Goods shipped by merchants (cumulative)"), this.tradeLegend, this.tradeHost);
    this.tradeChart = new LineChart(this.tradeHost, { label: "Goods shipped", yMin: 0, onPick: (d) => this.pick(d) });
    this.tradeEmpty = h("p", { class: "muted" }, "No shipments yet. Merchants only travel once cities are connected by a road.");

    // public finance
    const finGrid = h("div", { class: "chart-grid" });
    if (this.fiscalOn()) {
      for (const [key, title] of FINANCE) {
        const host = h("div");
        finGrid.append(h("div", { class: "panel" }, h("h3", {}, title), host));
        const ch = new LineChart(host, { label: title, yMin: 0, onPick: (d) => this.pick(d) });
        ch.key = key;
        this.societyCharts.push(ch);
      }
    }

    this.tables = h("div");
    this.jobCharts = new Map();
    this.jobGrid = h("div", { class: "chart-grid" });
    this.jobHead = h("h3", {}, "Jobs");
    this.jobNote = h("p", { class: "muted" }, "Share of the city's citizen-hours spent on every kind of job (sleeping, walking and the like are listed too). The jobs come from the data, so new kinds appear on their own.");
    this.xchgCharts = new Map();
    this.xchgGrid = h("div", { class: "chart-grid" });
    this.xchgHead = h("h3", {}, "Exports and imports");
    this.xchgNote = h("p", { class: "muted" }, "Goods shipped out of (solid) and into (dashed) the city by merchants, per day.");
    this.citySel = h("select", { "aria-label": "City", onchange: (e) => { this.city = e.target.value; this.refreshCityCharts(); } },
      h("option", { value: "" }, "All cities"),
      ...cities.map((c) => h("option", { value: c, selected: c === this.city }, c)));
    this.cityHead = h("h2", {}, "City detail");
    this.cityBar = h("div", { class: "toolbar" }, this.citySel);
    this.resHead = h("h2", {}, "Resources");
    this.resNote = h("p", { class: "muted" }, "The resource contract: which need each good satisfies, what supplies it and which signal should trigger new supply. The share is each city's latest reading up to the chosen day.");
    this.resHost = h("div", { class: "table-wrap" });
    this.demandCharts = new Map();
    this.demandGrid = h("div", { class: "chart-grid" });
    this.demandHead = h("h2", {}, "Demand");
    this.demandNote = h("p", { class: "muted" }, "Needs and waiting tasks put points on a city's board once a day; the rules of the policies below act on them.");
    this.policyPanel = new PoliciesPanel(this);
    this.policyPanel.build();
    this.charts = [...this.priceCharts, ...this.societyCharts, this.tradeChart, ...this.policyPanel.charts];

    this.root.replaceChildren(
      atlas,
      smoothBar,
      h("h2", {}, "Prices"), toolbar, priceGrid,
      h("h2", {}, "Society"), socGrid,
      this.cityHead, this.cityBar,
      this.jobHead, this.jobNote, this.jobGrid,
      this.xchgHead, this.xchgNote, this.xchgGrid,
      ...(this.fiscalOn() ? [h("h2", {}, "Public finance"),
        h("p", { class: "muted" }, "Each city taxes and spends from its own treasury, with a policy fixed for the run."), finGrid] : []),
      h("h2", {}, "Trade"), h("div", { class: "chart-grid wide" }, this.tradePanel), this.tradeEmpty,
      this.resHead, this.resNote, this.resHost,
      this.demandHead, this.demandNote, this.demandGrid,
      this.policyHead = h("h2", {}, "Policies"),
      this.policyPanel.el,
      this.tables,
    );
    this.refresh();
  }

  updateFollow() {
    if (this.followBtn) this.followBtn.hidden = this.follow;
  }

  pick(d) {
    this.follow = false;
    this.updateFollow();
    this.setDay(Math.max(0, Math.min(this.maxDay(), d)));
  }

  toggleReplay() {
    if (this._replay) {
      clearInterval(this._replay);
      this._replay = null;
      this.replayBtn.textContent = "Replay";
      return;
    }
    if (this.day >= this.maxDay()) this.setDay(0);
    this.replayBtn.textContent = "Stop";
    this._replay = setInterval(() => {
      if (this.day >= this.maxDay()) { this.toggleReplay(); return; }
      this.setDay(this.day + 1);
    }, 140);
  }

  setDay(d) {
    this.day = d;
    this.slider.value = d;
    this.renderDay();
    this.refreshResources();
    this.policyPanel.setDay(d);
    for (const c of this.charts) c.setCursor(d);
  }

  // -- rendering ------------------------------------------------------------
  refresh() {
    if (!this.data || !this.slider) return;
    this.slider.max = Math.max(0, this.maxDay());
    this.slider.value = this.day;
    this.refreshCharts();
    this.refreshCityCharts();
    this.refreshResources();
    this.refreshDemand();
    this.policyPanel.refresh();
    this.policyHead.hidden = this.policyPanel.el.hidden;
    this.renderDay();
    this.renderTables();
  }

  eventsForCharts() {
    const cfg = this.data.config;
    const evs = this.data.events.length ? this.data.events : (cfg.events || []);
    return evs.map((ev) => ({ x: ev.day != null ? Number(ev.day) : eventDay(ev, cfg), label: eventLabel(ev) }));
  }

  refreshCharts() {
    const cities = this.cities();
    const events = this.eventsForCharts();
    const xMax = this.live ? Math.max(this.maxDay(), (this.data.config.time || {}).days || 0) : null;
    for (const ch of this.priceCharts) {
      ch.opts.xMax = xMax;
      ch.set(cities.map((c, i) => ({ name: c, color: cityColor(i), tipColor: cityColor(i),
        points: this.sm(this.priceSeries(c, ch.good)) })), events);
      ch.setCursor(this.day);
    }
    for (const ch of this.societyCharts) {
      ch.opts.xMax = xMax;
      ch.set(cities.map((c, i) => ({ name: c, color: cityColor(i), tipColor: cityColor(i),
        points: this.sm(this.data.cities_daily.filter((r) => r.city === c).map((r) => [r.day, r[ch.key]])) })), events);
      ch.setCursor(this.day);
    }
    // cumulative shipments by route and good
    const routes = new Map();
    for (const f of this.data.flows) {
      const k = `${f.good}: ${f.from} to ${f.to}`;
      if (!routes.has(k)) routes.set(k, { from: f.from, rows: [] });
      routes.get(k).rows.push(f);
    }
    const maxD = this.maxDay();
    const series = [...routes.entries()].map(([name, { from, rows }], i) => {
      const byDay = new Map();
      for (const r of rows) byDay.set(r.day, (byDay.get(r.day) || 0) + r.qty);
      let tot = 0;
      const pts = [];
      for (let d = 0; d <= maxD; d++) { tot += byDay.get(d) || 0; pts.push([d, tot]); }
      const ci = cities.indexOf(from);
      const good = name.split(":")[0];
      return { name, color: cityColor(ci < 0 ? i : ci), tipColor: cityColor(ci < 0 ? i : ci), dash: this.goods().indexOf(good) > 0, points: pts };
    });
    this.tradePanel.hidden = series.length === 0;
    this.tradeEmpty.hidden = series.length !== 0;
    this.tradeChart.opts.xMax = xMax;
    this.tradeChart.set(series, events);
    this.tradeChart.setCursor(this.day);
    this.tradeLegend.replaceChildren(legend(series));
  }

  // Rolling average over the chosen number of days (a trailing window, so a live run never changes past points).
  sm(points) { return smooth(points, this.smoothOn ? this.window : 1); }

  // City detail (REQ-5A.15): the selected city, or every city. Charts exist only for the cities shown.
  chartsFor(map, grid, shown, label, extra = {}) {
    for (const [c, e] of [...map]) {
      if (shown.includes(c)) continue;
      e.panel.remove();
      e.chart.destroy();
      this.charts = this.charts.filter((x) => x !== e.chart);
      map.delete(c);
    }
    for (const c of shown) {
      if (map.has(c)) continue;
      const host = h("div", { class: "tall" });
      const key = h("div");
      const panel = h("div", { class: "panel" }, h("h3", {}, c), host, key);
      grid.append(panel);
      const chart = new LineChart(host, { label: label(c), yMin: 0, onPick: (d) => this.pick(d), ...extra });
      map.set(c, { panel, chart, key });
      this.charts.push(chart);
    }
  }

  refreshCityCharts() {
    if (this.city && !this.cities().includes(this.city)) this.city = "";
    this.citySel.value = this.city;
    const shown = this.city ? [this.city] : this.cities();
    this.refreshJobs(shown);
    this.refreshExchange(shown);
  }

  refreshJobs(shown) {
    const rows = this.data.jobs_daily, events = this.eventsForCharts();
    const xMax = this.live ? Math.max(this.maxDay(), (this.data.config.time || {}).days || 0) : null;
    const jobs = [...new Set(rows.map((r) => r.job))].sort();
    const have = shown.filter((c) => rows.some((r) => r.city === c));
    this.chartsFor(this.jobCharts, this.jobGrid, have, (c) => `Job shares in ${c}`, { unit: "%" });
    for (const [city, { chart, key }] of this.jobCharts) {
      const mine = rows.filter((r) => r.city === city);
      const days = [...new Set(mine.map((r) => r.day))].sort((a, b) => a - b);
      chart.opts.xMax = xMax;
      const series = jobs.filter((j) => mine.some((r) => r.job === j)).map((j) => ({
        name: j, color: cityColor(jobs.indexOf(j)), tipColor: cityColor(jobs.indexOf(j)),
        dash: Math.floor(jobs.indexOf(j) / CITY_COLORS.length) % 2 === 1,  // the palette repeats: dash the second round
        points: this.sm(days.map((d) => {
          const r = mine.find((x) => x.day === d && x.job === j);
          return [d, r ? Math.round(r.share * 1000) / 10 : 0];
        })) }));
      chart.set(series, events);
      key.replaceChildren(legend(series));
      chart.setCursor(this.day);
    }
    this.jobHead.hidden = this.jobNote.hidden = this.jobGrid.hidden = have.length === 0;
  }

  // Exports and imports per city: a line per good, solid for what leaves and dashed for what arrives.
  refreshExchange(shown) {
    const flows = this.data.flows, events = this.eventsForCharts(), goods = this.goods();
    const xMax = this.live ? Math.max(this.maxDay(), (this.data.config.time || {}).days || 0) : null;
    const maxD = this.maxDay();
    const have = shown.filter((c) => flows.some((f) => f.from === c || f.to === c));
    this.chartsFor(this.xchgCharts, this.xchgGrid, have, (c) => `Exports and imports of ${c}`, { unit: " units" });
    for (const [city, { chart, key }] of this.xchgCharts) {
      const series = [];
      for (const g of goods) {
        for (const [dir, key] of [["export", "from"], ["import", "to"]]) {
          const mine = flows.filter((f) => f.good === g && f[key] === city);
          if (!mine.length) continue;
          const byDay = new Map();
          for (const f of mine) byDay.set(f.day, (byDay.get(f.day) || 0) + f.qty);
          const pts = [];
          for (let d = 0; d <= maxD; d++) pts.push([d, byDay.get(d) || 0]);
          const col = cityColor(goods.indexOf(g));
          series.push({ name: `${dir} ${g}`, color: col, tipColor: col, dash: dir === "import", points: this.sm(pts) });
        }
      }
      chart.opts.xMax = xMax;
      chart.set(series, events);
      key.replaceChildren(legend(series));
      chart.setCursor(this.day);
    }
    this.xchgHead.hidden = this.xchgNote.hidden = this.xchgGrid.hidden = have.length === 0;
  }

  // Resources (REQ-5B.6): one row per entry of the contract in the run's config.
  refreshResources() {
    const res = this.data.config.resources || {};
    const rows = Object.entries(res).filter(([, e]) => e && typeof e === "object");
    this.resHead.hidden = this.resNote.hidden = this.resHost.hidden = rows.length === 0;
    if (!rows.length) return;
    const on = this.now && this.now.policies ? this.now.policies
      : Object.fromEntries((this.data.policies || []).map((p) => [p.name, p.enabled]));
    const share = (sig) => {
      const latest = new Map();
      for (const r of this.data.signal_daily) if (r.signal === sig && r.day <= this.day) latest.set(r.city, r);
      return [...latest.values()].map((r) => `${r.city} ${num(r.share * 100, 0)}%`).join(", ") || "–";
    };
    const cell = (...kids) => h("td", {}, ...kids);
    this.resHost.replaceChildren(h("table", { class: "mini" },
      h("thead", {}, h("tr", {}, ...["Good", "Satisfies", "Use per day", "Supply", "Trigger", "Share now"].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows.map(([g, e]) => {
        const sat = e.satisfies, sup = e.supply, trig = e.trigger;
        const supply = sup ? `${sup.process}${sup.policy ? ` (${sup.policy} ${on[sup.policy] ? "on" : "off"})` : ""}`
          : e.input ? `${e.byproduct_of ? `byproduct of ${e.byproduct_of}` : "input"}${(e.consumed_by || []).length ? `, used by ${e.consumed_by.join(", ")}` : ""}` : "–";
        const when = trig ? `${trig.signal} ${trig.share_gt != null ? `> ${num(trig.share_gt * 100, 0)}%` : trig.count_gte != null ? `>= ${trig.count_gte}` : ""}` : "–";
        return h("tr", {}, cell(g), cell(sat ? `${sat.need}, +${num(sat.per_unit, 2)} per unit` : "–"),
          cell(e.daily_units != null ? num(e.daily_units, 2) : "–"), cell(supply), cell(when), cell(trig ? share(trig.signal) : "–"));
      }))));
  }

  // Demand (REQ-5A.5): one chart per signal that was ever above zero, a line per city.
  refreshDemand() {
    const rows = this.data.signal_daily, cities = this.cities(), events = this.eventsForCharts();
    const xMax = this.live ? Math.max(this.maxDay(), (this.data.config.time || {}).days || 0) : null;
    const title = (k) => k.startsWith("need:") ? `Residents with an urgent ${k.slice(5)} need`
      : k.startsWith("service:") ? `Open tasks that need the ${k.slice(8).replaceAll("_", " ")} office` : k;
    let any = false;
    for (const kind of [...new Set(rows.map((r) => r.signal))]) {
      const mine = rows.filter((r) => r.signal === kind);
      if (!mine.some((r) => r.count > 0)) continue;
      any = true;
      let ch = this.demandCharts.get(kind);
      if (!ch) {
        const host = h("div");
        this.demandGrid.append(h("div", { class: "panel" }, h("h3", {}, title(kind)), host));
        ch = new LineChart(host, { label: title(kind), yMin: 0, onPick: (d) => this.pick(d) });
        this.demandCharts.set(kind, ch);
        this.charts.push(ch);
      }
      ch.opts.xMax = xMax;
      ch.set(cities.map((c, i) => ({ name: c, color: cityColor(i), tipColor: cityColor(i),
        points: this.sm(mine.filter((r) => r.city === c).map((r) => [r.day, r.count])) })), events);
      ch.setCursor(this.day);
    }
    this.demandHead.hidden = this.demandNote.hidden = this.demandGrid.hidden = !any;
  }

  stateAt(d) {
    const cfg = this.data.config;
    const cities = {};
    for (const c of cfg.cities || []) cities[c.name] = { population: c.population, prices: {}, sick: 0 };
    // Latest daily row up to day d (a live run's current day has no row yet).
    for (const r of this.data.cities_daily) {
      if (r.day <= d && cities[r.city]) Object.assign(cities[r.city], { population: r.population, sick: r.sick, row: r });
    }
    for (const r of this.data.prices_daily) {
      if (r.day > d || !cities[r.city]) continue;
      const v = r.vwap ?? r.last;
      if (v != null) cities[r.city].prices[r.good] = v;
    }
    const shipped = {};
    for (const f of this.data.flows) if (f.day <= d) {
      const k = `${f.from}|${f.to}|${f.good}`;
      shipped[k] = (shipped[k] || 0) + f.qty;
    }
    let transit = this.data.transit.filter((t) => t.day === d);
    // Live and on the latest hour: use the current state of the world.
    if (this.live && this.follow && this.now) {
      for (const [name, c] of Object.entries(this.now.cities)) {
        if (!cities[name]) continue;
        cities[name].population = c.population;
        for (const [g, v] of Object.entries(c.prices)) if (v != null) cities[name].prices[g] = v;
      }
      transit = this.now.transit;
    }
    return { day: d, cities, shipped, transit };
  }

  renderDay() {
    if (!this.data) return;
    const d = this.day;
    const st = this.stateAt(d);
    const hour = this.live && this.follow && this.now ? `, ${String(Math.floor(this.now.hour)).padStart(2, "0")}:00` : "";
    this.dayLabel.textContent = `Day ${d}${hour}`;
    drawMap(this.mapHost, this.data.config, st, { world: this.data.world, onCity: this.onCity });

    const goods = this.goods();
    const rows = (this.data.config.cities || []).map((c, i) => {
      const s = st.cities[c.name];
      const r = s.row || {};
      return h("tr", {},
        h("td", {}, h("span", { class: "swatch", style: `background:${cityColor(i)}` }), c.name),
        h("td", { class: "num" }, num(s.population, 0)),
        h("td", { class: "num" }, num(r.mean_gold, 1)),
        ...goods.map((g) => h("td", { class: "num" }, num(s.prices[g], 2))),
        h("td", { class: "num" }, num(r.sick, 0)));
    });
    const check = [...this.data.checks].reverse().find((c) => c.day <= d);
    const deathsSoFar = this.data.deaths.filter((x) => x.day <= d).length;
    const ok = check ? truthy(check.totals_zero) && truthy(check.goods_balance_ok) : null;
    this.ledgerHost.replaceChildren(
      h("h3", {}, `Cities on day ${d}`),
      h("table", { class: "city-figures" },
        h("thead", {}, h("tr", {}, h("th", {}, "City"), h("th", { class: "num" }, "People"), h("th", { class: "num" }, "Gold each"),
          ...goods.map((g) => h("th", { class: "num" }, g)), h("th", { class: "num" }, "Sick"))),
        h("tbody", {}, rows)),
      h("p", { class: "muted", style: "margin-top:14px" },
        check ? `Gold in circulation: ${num(check.gold_supply, 2)}. ` : "",
        `${deathsSoFar} ${deathsSoFar === 1 ? "death" : "deaths"} so far. `,
        check ? `${num(check.travelling, 0)} on the road.` : ""),
      ok == null ? "" : h("p", { class: ok ? "good" : "bad" },
        ok ? "Ledger balanced: no gold or goods created or lost." : "Conservation check failed on this day."),
    );
  }

  renderTables() {
    const { events, deaths, checks } = this.data;
    const failing = checks.filter((c) => !(truthy(c.totals_zero) && truthy(c.goods_balance_ok)));
    const byCause = {};
    for (const d of deaths) {
      const k = `${d.city ?? "?"}|${d.cause}`;
      byCause[k] = (byCause[k] || 0) + 1;
    }
    const summ = this.data.summary;
    const open = [...this.tables.querySelectorAll("details")].map((d) => d.open);
    this.tables.replaceChildren(
      h("details", {}, h("summary", {}, `Events (${events.length})`), h("div", {},
        events.length ? h("table", { class: "mini" },
          h("thead", {}, h("tr", {}, h("th", {}, "Day"), h("th", {}, "Type"), h("th", {}, "Details"))),
          h("tbody", {}, events.map((e) => h("tr", {}, h("td", {}, num(e.day, 1)), h("td", {}, e.type),
            h("td", {}, Object.entries(e).filter(([k]) => !["tick", "day", "type"].includes(k)).map(([k, v]) => `${k}: ${v}`).join(", "))))))
          : h("p", { class: "muted" }, "No events in this run."))),
      h("details", {}, h("summary", {}, `Deaths (${deaths.length})`), h("div", {},
        deaths.length ? h("table", { class: "mini" },
          h("thead", {}, h("tr", {}, h("th", {}, "City"), h("th", {}, "Cause"), h("th", { class: "num" }, "Count"))),
          h("tbody", {}, Object.entries(byCause).sort((a, b) => b[1] - a[1]).map(([k, n]) => {
            const [city, cause] = k.split("|");
            return h("tr", {}, h("td", {}, city), h("td", {}, cause.replace("_", " ")), h("td", { class: "num" }, n));
          })))
          : h("p", { class: "muted" }, "Nobody has died."))),
      h("details", {}, h("summary", {}, "Conservation checks"), h("div", {},
        h("p", { class: failing.length ? "bad" : "good" }, failing.length
          ? `${failing.length} of ${checks.length} daily checks failed (days ${failing.map((c) => c.day).join(", ")}).`
          : `All ${checks.length} daily checks passed: every asset sums to zero and goods changed only by regeneration and consumption.`),
        summ ? h("p", { class: summ.ledger && summ.ledger.ok ? "good" : "bad" },
          summ.ledger && summ.ledger.ok ? `Full ledger replay matches (${num(summ.ledger.transfers, 0)} transfers).` : "Ledger replay does not match.") : "",
        summ ? h("p", { class: summ.escrow_matches_market ? "good" : "bad" },
          summ.escrow_matches_market ? "Escrow held by each city equals what the market reports as locked." : "Escrow does not match the market.") : "",
      )),
    );
    this.tables.querySelectorAll("details").forEach((d, i) => { if (open[i]) d.open = true; });
  }
}
