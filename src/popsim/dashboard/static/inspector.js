// Inspector (REQ-1C.5): list the citizens of a city and open one to see its status,
// needs, wallet, spending plan, purchase history, actions and jobs.
// `base` is "/api/session" for the live run or "/api/runs/<name>" for a saved run.
import { api, h, num, cityColor } from "./util.js";
import { LineChart, legend } from "./charts.js";
import { cssVar } from "./util.js";

const COLUMNS = [
  ["name", "Name", false], ["role", "Role", false], ["age", "Age", true], ["gender", "Sex", false],
  ["gold", "Gold", true], ["satiety", "Satiety", true], ["stamina", "Stamina", true],
  ["sick", "Sick", false], ["action", "Action", false],
];
const LIVE_REFRESH_MS = 1500;
const NS = "http://www.w3.org/2000/svg";
const GOOD_COLORS = ["--verdigris", "--ochre", "--indigo", "--plum", "--olive"];

function svgEl(tag, attrs = {}, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}
const xy = (p) => (p ? `${num(p.x, 2)}, ${num(p.y, 2)}` : "–");

function clock(day) {
  const d = Math.floor(day);
  return `day ${d}, ${String(Math.round((day - d) * 24) % 24).padStart(2, "0")}:00`;
}

function kv(pairs) {
  return h("dl", { class: "kv" }, pairs.filter(Boolean).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "–")]));
}

function table(heads, rows, numeric = []) {
  if (!rows.length) return h("p", { class: "muted" }, "Nothing yet.");
  return h("table", { class: "mini" },
    h("thead", {}, h("tr", {}, heads.map((t, i) => h("th", { class: numeric.includes(i) ? "num" : "" }, t)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: numeric.includes(i) ? "num" : "" }, c))))));
}

export class Inspector {
  constructor(root, { base, live = false }) {
    this.root = root;
    this.base = base;
    this.live = live;
    this.city = null;
    this.cities = [];
    this.sort = "name";
    this.order = "asc";
    this.page = 1;
    this.filters = { q: "", role: "", sick: "", gold_min: "", gold_max: "", satiety_min: "", satiety_max: "" };
    this.selected = null;
    this.closed = new Set();
    this.visible = true;
    this._busy = false;
    this._last = 0;
    this._timer = null;
    this._chart = null;
    this._dead = false;
    this.build();
  }

  destroy() {
    this._dead = true;
    clearTimeout(this._timer);
    if (this._chart) this._chart.destroy();
  }

  // -- layout ---------------------------------------------------------------
  build() {
    this.citySel = h("select", { "aria-label": "City", onchange: (e) => { this.city = e.target.value; this.page = 1; this.loadList(); } });
    const f = this.filters;
    const bind = (key) => (e) => { f[key] = e.target.value; this.page = 1; this.loadList(); };
    const search = h("input", { type: "search", placeholder: "Name or id", "aria-label": "Search", oninput: bind("q") });
    const role = h("select", { "aria-label": "Role", onchange: bind("role") },
      h("option", { value: "" }, "Any role"), h("option", { value: "worker" }, "Workers"), h("option", { value: "merchant" }, "Merchants"));
    const sick = h("select", { "aria-label": "Health", onchange: bind("sick") },
      h("option", { value: "" }, "Sick or well"), h("option", { value: "true" }, "Sick only"), h("option", { value: "false" }, "Well only"));
    const range = (key, label) => h("input", { type: "number", step: "any", placeholder: label, "aria-label": label, oninput: bind(key) });
    this.countOut = h("span", { class: "muted" });
    this.head = h("tr");
    this.body = h("tbody");
    this.pager = h("div", { class: "row" });
    this.listNote = h("div");
    this.mapHost = h("div", { class: "city-map" });
    this.mapNote = h("p", { class: "hint" });
    this.detailHost = h("div", { class: "citizen" }, h("div", { class: "empty" }, h("p", {}, "Select a citizen to see everything about them.")));

    const list = h("div", { class: "citizen-list" },
      h("div", { class: "toolbar" }, h("label", { class: "row" }, "City", this.citySel), search, role, sick,
        range("gold_min", "Gold from"), range("gold_max", "Gold to"), range("satiety_min", "Satiety from"),
        range("satiety_max", "Satiety to"), this.countOut),
      this.listNote,
      h("details", { class: "city-map-box", open: true, ontoggle: (e) => { if (e.target.open) this.loadMap(); } },
        h("summary", {}, "City map"), h("div", {}, this.mapHost, this.mapNote)),
      h("div", { class: "table-wrap" }, h("table", { class: "mini" }, h("thead", {}, this.head), this.body)),
      this.pager);
    this.root.replaceChildren(h("div", { class: "inspector" }, list, this.detailHost));
    this.renderHead();
  }

  renderHead() {
    this.head.replaceChildren(...COLUMNS.map(([key, label, numeric]) => {
      const on = this.sort === key;
      return h("th", { class: numeric ? "num" : "", "aria-sort": on ? (this.order === "asc" ? "ascending" : "descending") : "none" },
        h("button", { type: "button", class: "sort", onclick: () => {
          if (this.sort === key) this.order = this.order === "asc" ? "desc" : "asc";
          else { this.sort = key; this.order = numeric ? "desc" : "asc"; }
          this.page = 1; this.renderHead(); this.loadList();
        } }, label, on ? (this.order === "asc" ? " ▲" : " ▼") : ""));
    }));
  }

  // -- data -------------------------------------------------------------------
  async start() {
    try {
      this.cities = await api(`${this.base}/cities`);
    } catch (e) {
      this.fail(e);
      return;
    }
    if (this._dead) return;
    this.city = this.city || (this.cities.find((c) => c.citizens > 0) || this.cities[0]).name;
    this.renderCities();
    await this.loadList();
  }

  selectCity(name) {
    if (!this.cities.some((c) => c.name === name)) return;
    this.city = name;
    this.page = 1;
    this.renderCities();
    this.loadList();
  }

  async loadMap() {
    if (!this.city || this._dead) return;
    const seq = (this._mseq = (this._mseq || 0) + 1);
    try {
      const m = await api(`${this.base}/cities/${encodeURIComponent(this.city)}/map`);
      if (seq !== this._mseq || this._dead) return;
      this.drawCityMap(m);
    } catch (e) {
      if (seq === this._mseq) { this.mapHost.replaceChildren(); this.mapNote.textContent = e.message; }
    }
  }

  // The city's streets: nodes, houses, workplaces and citizens at their positions, walkers with a line to where they go.
  drawCityMap(m) {
    if (!m.spatial) {
      this.mapHost.replaceChildren();
      this.mapNote.textContent = "This scenario has no coordinates, so everyone in the city is in the same place and nothing is far away.";
      return;
    }
    const SIZE = 560, PAD = 34, R = Math.max(m.radius, 1e-6);
    const k = (SIZE - 2 * PAD) / (2 * R);
    const px = (x) => SIZE / 2 + (x - m.center.x) * k, py = (y) => SIZE / 2 - (y - m.center.y) * k;  // y up
    const svg = svgEl("svg", { viewBox: `0 0 ${SIZE} ${SIZE}`, role: "img", "aria-label": `Map of ${m.city}`, class: "city-svg" });
    const ink3 = getComputedStyle(document.documentElement).getPropertyValue("--ink-3").trim();
    const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink").trim();
    svg.append(svgEl("circle", { cx: SIZE / 2, cy: SIZE / 2, r: R * k, fill: "none", stroke: ink3, "stroke-dasharray": "4 6" }));
    svg.append(svgEl("circle", { cx: SIZE / 2, cy: SIZE / 2, r: 3, fill: ink3 }));
    const goods = [...new Set(m.nodes.map((n) => n.good))];
    const gcol = (g) => getComputedStyle(document.documentElement).getPropertyValue(GOOD_COLORS[goods.indexOf(g) % GOOD_COLORS.length]).trim();
    for (const hs of m.houses) {
      const r = svgEl("rect", { x: px(hs.x) - 4, y: py(hs.y) - 4, width: 8, height: 8, fill: hs.occupant ? ink3 : "none", stroke: ink3 });
      r.append(svgEl("title", {}, `House${hs.occupant ? ` (lived in by #${hs.occupant})` : " (empty)"}`));
      svg.append(r);
    }
    for (const w of m.workplaces) {
      const d = svgEl("path", { d: `M${px(w.x)} ${py(w.y) - 8} l8 8 l-8 8 l-8 -8 z`, fill: "none", stroke: ink, "stroke-width": 2 });
      d.append(svgEl("title", {}, `Workplace: ${w.task}, ${num(w.wage, 2)} gold per hour`));
      svg.append(d);
    }
    for (const n of m.nodes) {
      const c = svgEl("circle", { cx: px(n.x), cy: py(n.y), r: 10, fill: gcol(n.good), "fill-opacity": 0.25, stroke: gcol(n.good), "stroke-width": 2 });
      c.append(svgEl("title", {}, `${n.good} node, ${n.stock} in stock`));
      svg.append(c, svgEl("text", { x: px(n.x), y: py(n.y) - 15, "text-anchor": "middle", class: "map-note", style: "font-size:13px" }, n.good));
    }
    for (const c of m.citizens) {
      if (c.walking && c.to) svg.append(svgEl("line", { x1: px(c.x), y1: py(c.y), x2: px(c.to.x), y2: py(c.to.y), stroke: ink3, "stroke-width": 1, "stroke-dasharray": "2 3" }));
    }
    for (const c of m.citizens) {
      const sel = c.id === this.selected;
      const dot = svgEl("circle", { cx: px(c.x), cy: py(c.y), r: sel ? 7 : 4, class: "dot", tabindex: 0, role: "button",
        fill: c.walking ? cityColor(1) : cityColor(0), stroke: sel ? ink : "none", "stroke-width": 2, "aria-label": `${c.name}, ${c.action}` });
      dot.append(svgEl("title", {}, `${c.name}: ${c.action}`));
      dot.addEventListener("click", () => this.open(c.id));
      dot.addEventListener("keydown", (e) => { if (e.key === "Enter") this.open(c.id); });
      svg.append(dot);
    }
    this.mapHost.replaceChildren(svg);
    this.mapNote.textContent = `${m.citizens.length} people. Round markers are resource nodes, squares houses (filled when lived in), diamonds workplaces. Blue dots are walking, with a line to where they go. Click a dot to open that person.`;
  }

  fail(e) {
    this.body.replaceChildren();
    this.pager.replaceChildren();
    this.countOut.textContent = "";
    this.listNote.replaceChildren(h("div", { class: "empty" }, h("p", {}, e.message)));
  }

  renderCities() {
    this.citySel.replaceChildren(...this.cities.map((c) =>
      h("option", { value: c.name, selected: c.name === this.city },
        c.name === "travelling" ? `On the road (${c.citizens})` : `${c.name} (${c.citizens})`)));
  }

  query() {
    const q = new URLSearchParams({ sort: this.sort, order: this.order, page: this.page, page_size: 25 });
    for (const [k, v] of Object.entries(this.filters)) if (v !== "") q.set(k, v);
    return q.toString();
  }

  async loadList() {
    if (!this.city) return;
    const seq = (this._seq = (this._seq || 0) + 1);
    try {
      const data = await api(`${this.base}/cities/${encodeURIComponent(this.city)}/citizens?${this.query()}`);
      if (seq !== this._seq || this._dead) return;
      this.listNote.replaceChildren();
      this.renderList(data);
      if (this.mapOpen()) this.loadMap();
    } catch (e) {
      if (seq === this._seq) this.fail(e);
    }
  }

  mapOpen() {
    const box = this.root.querySelector(".city-map-box");
    return !box || box.open;
  }

  renderList(data) {
    this.page = data.page;
    this.countOut.textContent = `${data.total} citizen${data.total === 1 ? "" : "s"}`;
    this.body.replaceChildren(...data.rows.map((r) => h("tr", {
      class: `link${r.id === this.selected ? " selected" : ""}`, tabindex: 0,
      onclick: () => this.open(r.id), onkeydown: (e) => { if (e.key === "Enter") this.open(r.id); } },
      h("td", {}, r.name), h("td", {}, r.role), h("td", { class: "num" }, num(r.age, 1)), h("td", {}, r.gender),
      h("td", { class: "num" }, num(r.gold, 2)), h("td", { class: "num" }, num(r.satiety, 2)),
      h("td", { class: "num" }, num(r.stamina, 0)),
      h("td", {}, r.sick ? h("span", { class: "bad" }, "sick") : "–"), h("td", {}, r.action))));
    if (!data.rows.length) this.body.append(h("tr", {}, h("td", { colspan: COLUMNS.length, class: "muted" }, "No citizens match.")));
    this.pager.replaceChildren(
      h("button", { type: "button", disabled: data.page <= 1, onclick: () => { this.page--; this.loadList(); } }, "Previous"),
      h("span", { class: "muted" }, `Page ${data.page} of ${data.pages}`),
      h("button", { type: "button", disabled: data.page >= data.pages, onclick: () => { this.page++; this.loadList(); } }, "Next"));
  }

  // The live run moves on every frame: refresh the list and the open citizen, at most every 1.5 s.
  refresh() {
    if (!this.live || !this.visible || this._dead || this._busy || !this.city) return;
    const wait = LIVE_REFRESH_MS - (Date.now() - this._last);
    if (wait > 0) {
      if (!this._timer) this._timer = setTimeout(() => { this._timer = null; this.refresh(); }, wait);
      return;
    }
    this._busy = true;
    this._last = Date.now();
    api(`${this.base}/cities`).then((c) => { this.cities = c; this.renderCities(); }).catch(() => {})
      .then(() => Promise.all([this.loadList(), this.selected != null ? this.loadDetail() : null]))
      .finally(() => { this._busy = false; });
  }

  setVisible(v) {
    this.visible = v;
    if (v) { this._last = 0; this.refresh(); }
  }

  open(eid) {
    this.selected = eid;
    for (const tr of this.body.children) tr.classList.remove("selected");
    this.loadDetail().then(() => this.loadList());
    if (this.mapOpen()) this.loadMap();
  }

  async loadDetail() {
    const eid = this.selected;
    try {
      const d = await api(`${this.base}/citizens/${eid}`);
      if (eid !== this.selected || this._dead) return;
      this.renderDetail(d);
    } catch (e) {
      if (eid !== this.selected) return;
      this.detailHost.replaceChildren(h("div", { class: "empty" }, h("p", {}, e.message)));
    }
  }

  // -- detail -----------------------------------------------------------------
  section(title, ...kids) {
    return h("details", { open: !this.closed.has(title), ontoggle: (e) => {
      if (e.target.open) this.closed.delete(title); else this.closed.add(title);
    } }, h("summary", {}, title), h("div", {}, ...kids));
  }

  link(id, name) {
    return id == null ? "–" : h("a", { href: "#", onclick: (e) => { e.preventDefault(); this.open(id); } }, name || `#${id}`);
  }

  cityLink(name) {
    if (!name || !this.cities.some((c) => c.name === name)) return name || "–";
    return h("a", { href: "#", onclick: (e) => { e.preventDefault(); this.selectCity(name); } }, name);
  }

  renderDetail(d) {
    if (this._chart) { this._chart.destroy(); this._chart = null; }
    const s = d.status, w = d.wallet, hist = d.history, plan = d.plan;
    const where = s.travel ? `on the road ${s.travel.origin} to ${s.travel.dest}, ${s.travel.ticks_left} hours left` : s.location;
    const head = h("div", { class: "citizen-head" },
      h("h3", {}, d.name), h("span", { class: "muted" }, `#${d.id} · ${d.role} · ${s.gender} · ${num(s.age_years, 1)} years`),
      d.alive ? "" : h("span", { class: "bad" }, `Died on day ${d.died.day} (${d.died.cause.replaceAll("_", " ")}). Final state shown.`));
    const parts = [head];

    parts.push(this.section("Status", kv([
      ["Location", where], ["Position", s.position ? `${xy(s.position)} km` : null],
      s.walk ? ["Walking", `${s.walk.to}${s.walk.destination ? ` at ${xy(s.walk.destination)}` : ""}, ${s.walk.ticks_left} h left${s.walk.then ? `, then ${s.walk.then}` : ""}`] : null,
      ["Home", s.home],
      ["House", s.house ? `${s.house.at_home ? "at home" : `${num(s.house.distance, 2)} km away`}${s.house.position ? ` (${xy(s.house.position)})` : ""}` : "none, sleeps where it stands"],
      ["Life expectancy", `${num(s.life_expectancy_years, 1)} years`],
      ["Stamina", `${num(s.stamina.current, 1)} of ${num(s.stamina.daily_max, 1)} today (max ${num(s.stamina.absolute_max, 0)})`],
      ["Sleep today", `${num(s.stamina.sleep_hours_today, 1)} h, good-rest streak ${s.stamina.good_rest_streak} days`],
      ["Satiety", `${num(s.satiety, 2)} (burns ${num(s.burn_per_hour, 3)} per hour)`],
      ["Sickness", s.sickness.sick ? h("span", { class: "bad" }, "sick") : `well, ${num(s.sickness.probability * 100, 3)}% risk per hour`],
      ["Immune strength", num(s.sickness.immune_strength, 2)],
    ])));

    parts.push(this.section("Needs", table(["Need", "Level", "Comfort", "Urgent", "Critical", "Urgency", "Hours at zero"],
      d.needs.map((n) => [n.need, this.needBar(n), num(n.comfort, 2), num(n.urgent, 2), num(n.critical, 2), num(n.urgency, 2), num(n.zero_hours, 1)]),
      [2, 3, 4, 5, 6])));

    const locked = w.locked_gold > 0 || Object.keys(w.locked_goods).length
      ? `${num(w.locked_gold, 2)} gold` + Object.entries(w.locked_goods).map(([g, q]) => `, ${q} ${g}`).join("") : "nothing";
    parts.push(this.section("Wallet and inventory", kv([
      ["Gold", num(w.gold, 2)], ...Object.entries(w.inventory).map(([g, q]) => [g, q]), ["Locked in open orders", locked]])));

    parts.push(this.section("Traits and skills", kv([
      ...["risk", "urgency_tolerance", "thrift", "luxury", "forecast_noise", "reactivity", "stock_days"]
        .map((k) => [k.replaceAll("_", " "), num(d.traits[k], 2)]),
      ["Need weights", Object.entries(d.traits.need_weights).map(([k, v]) => `${k} ${num(v, 2)}`).join(", ")],
      ["Job preference", Object.entries(d.traits.job_preference).map(([k, v]) => `${k} ${num(v, 2)}`).join(", ")],
      ["Skills", Object.entries(d.skills).map(([k, v]) => `${k} ${num(v, 2)}`).join(", ")]])));

    const a = d.action, r = a.reason, m = a.merchant;
    parts.push(this.section("Current action", kv([
      ["Action", `${a.label}${a.ticks_left ? `, ${a.ticks_left} h left` : ""}`],
      ["Why", r ? `${r.note || r.choice}${Object.keys(r.scores).length ? ` (scores: ${Object.entries(r.scores).map(([k, v]) => `${k} ${v}`).join(", ")})` : ""}`
        : (hist.recorded ? "no decision yet" : "not recorded")],
      m ? ["Merchant", `${m.mode}${m.good ? ` ${m.good}` : ""}${m.dest ? ` to ${m.dest}` : ""}${m.qty ? `, ${m.qty} units` : ""}${m.max_buy_price ? `, pays up to ${num(m.max_buy_price, 2)}` : ""}`] : null,
      m ? ["Cities visited", m.visited.join(", ")] : null])));

    parts.push(this.section("Open orders", table(["Side", "Good", "Left", "Limit", "City", "Age (h)"],
      d.orders.open.map((o) => [o.side, o.good, o.qty, num(o.price, 2), o.city, o.age_ticks]), [2, 3, 5]),
      h("p", { class: "hint" }, "Bid premium: " + Object.entries(d.orders.bid_premium).map(([g, v]) => `${g} ${num(v * 100, 0)}%`).join(", ")
        + ". Ask discount: " + Object.entries(d.orders.ask_discount).map(([g, v]) => `${g} ${num(v * 100, 0)}%`).join(", ") + ".")));

    parts.push(this.section("Spending plan",
      kv([["Savings target", `${num(plan.savings_target, 2)} gold (has ${num(plan.gold, 2)}, level ${num(plan.wealth_level, 2)})`],
        ["Expected daily spending", `${num(plan.expected_daily_spend, 2)} gold`],
        ["Reservation wage", `${num(plan.reservation_wage_per_hour, 2)} gold per hour`],
        plan.fiscal ? ["Taxes in " + plan.fiscal.city, `income ${num(plan.fiscal.income_tax * 100, 1)}%, sales ${num(plan.fiscal.sales_tax * 100, 1)}%, support ${num(plan.fiscal.support_per_day, 2)} gold per day, treasury ${num(plan.fiscal.treasury, 0)} gold`] : null,
        hist.fiscal ? ["Paid / received so far", `${num(hist.fiscal.taxes_paid, 2)} gold in taxes, ${num(hist.fiscal.support_received, 2)} gold in support`] : null,
        ["Pending payments", plan.pending_payments.length ? plan.pending_payments.length : "none (loans and installments are not built yet)"]]),
      table(["Good", "Has", "Target", "Expects", "Bids up to", "Asks from", "Intends", "Merchant trade"],
        plan.goods.map((g) => [g.good, g.inventory, g.stock_target, num(g.expected_price, 2), num(g.reservation_bid, 2), num(g.reservation_ask, 2),
          g.intent.action === "hold" ? "hold" : `${g.intent.action} ${g.intent.qty} at ${num(g.intent.limit, 2)}`,
          g.merchant_trade ? `${g.merchant_trade.mode}${g.merchant_trade.dest ? ` to ${g.merchant_trade.dest}` : ""}` : "–"]), [1, 2, 3, 4, 5]),
      h("p", { class: "hint" }, plan.note)));

    parts.push(this.section("Purchase and sales history", this.historyBody(d)));
    parts.push(this.section("Actions history", hist.recorded ? table(["From", "Action", "Where", "Hours", "Why"],
      hist.actions.map((x) => [clock(x.start_day), x.label ? `${x.kind} ${x.label}` : x.kind, this.cityLink(x.city), x.ticks, x.reason.note || x.reason.choice]), [3])
      : h("p", { class: "muted" }, "Not recorded. Turn on inspector.record in the scenario to keep histories.")));

    const j = d.jobs;
    parts.push(this.section("Jobs and income",
      kv([["Role", j.role],
        ["Now", !j.current ? "not working" : j.current.public
        ? `public job: ${j.current.good} in ${j.current.city}${j.current.position ? ` at ${xy(j.current.position)}, ${num(j.current.distance, 2)} km from here` : ""}, paid ${num(j.current.wage_per_hour, 2)} gold per hour (${num(j.current.net_wage_per_hour, 2)} after tax)`
        : `gathering ${j.current.good} in ${j.current.city}${j.current.position ? ` at ${xy(j.current.position)}, ${num(j.current.distance, 2)} km from here` : ""}, expects ${num(j.current.expected_income_per_stamina, 3)} gold per stamina (${j.current.node_stock} left at the node)`],
        ["Contracts", j.contracts.length ? j.contracts.length : "none (job contracts are not built yet)"]]),
      h("h4", {}, "Jobs it could take here"),
      table(["Job", "City", "Score", "Income per hour", "Skill", "Distance (km)", "Walk there and back (stamina)"],
        j.options.map((o) => [o.public ? `${o.good} (public)` : o.good, o.city, num(o.score, 2), num(o.expected_income_per_hour, 2), num(o.skill, 2), num(o.distance, 2), num(o.round_trip_stamina, 1)]), [2, 3, 4, 5, 6]),
      h("p", { class: "hint" }, "Income per hour counts only the hours at the job. The walk costs stamina, both ways, and lowers a job's score."),
      h("h4", {}, "Tasks worked"),
      hist.recorded ? table(["From", "Task", "City", "Hours", "Units", "Value (gathered: at its own price; public: wages received)"],
        hist.tasks.map((t) => [clock(t.start_day), t.good, this.cityLink(t.city), num(t.hours, 0), t.units, num(t.est_value, 2)]), [3, 4, 5])
        : h("p", { class: "muted" }, "Not recorded."),
      h("p", { class: "hint" }, j.note)));

    parts.push(this.section("Remembered prices", table(["City", "Good", "Price", "Last seen"],
      d.memory.map((x) => [this.cityLink(x.city), x.good, num(x.price, 2), x.last_seen_day == null ? "starting guess" : `day ${x.last_seen_day}`]), [2])));

    this.detailHost.replaceChildren(...parts);
    this.drawChart(hist);
  }

  needBar(n) {
    const top = Math.max(1.2, n.level * 1.05, n.comfort * 1.05);
    const pos = (v) => `${Math.min(100, (100 * v) / top)}%`;
    return h("span", { class: "need-bar", title: `level ${n.level}` },
      h("i", { style: `width:${pos(Math.max(0, n.level))}` }),
      h("b", { class: "t-comfort", style: `left:${pos(n.comfort)}` }),
      h("b", { class: "t-urgent", style: `left:${pos(n.urgent)}` }),
      h("b", { class: "t-critical", style: `left:${pos(n.critical)}` }),
      h("span", { class: "need-val" }, num(n.level, 2)));
  }

  historyBody(d) {
    const hist = d.history;
    if (!hist.recorded) return h("p", { class: "muted" }, "Not recorded. Turn on inspector.record in the scenario to keep histories.");
    const totals = Object.entries(hist.totals);
    this.chartHost = h("div");
    const legendEl = legend([{ name: "Spent", color: cssVar("--madder") }, { name: "Earned", color: cssVar("--verdigris") }]);
    return [
      h("h4", {}, "Totals per good"),
      table(["Good", "Bought", "Avg paid", "City avg then", "Sold", "Avg received", "City avg then"],
        totals.map(([g, t]) => [g, t.bought_qty, num(t.avg_paid, 2), num(t.market_avg_when_bought, 2), t.sold_qty, num(t.avg_received, 2), num(t.market_avg_when_sold, 2)]), [1, 2, 3, 4, 5, 6]),
      h("h4", {}, "Gold spent and earned per day"), legendEl, this.chartHost,
      h("h4", {}, `Every fill (${hist.fills_total}${hist.fills_total > hist.fills.length ? `, newest ${hist.fills.length} shown` : ""})`),
      table(["When", "City", "Good", "Side", "Qty", "Price", "Total", "With"],
        hist.fills.map((f) => [`day ${f.day}`, this.cityLink(f.city), f.good, f.side, f.qty, num(f.price, 2), num(f.total, 2), this.link(f.counterparty, f.counterparty_name)]), [4, 5, 6]),
    ];
  }

  drawChart(hist) {
    if (!hist.recorded || !this.chartHost || !hist.daily.length) return;
    this._chart = new LineChart(this.chartHost, { label: "Gold spent and earned per day", yMin: 0 });
    this._chart.set([
      { name: "Spent", color: cssVar("--madder"), tipColor: cssVar("--madder"), points: hist.daily.map((x) => [x.day, x.spent]) },
      { name: "Earned", color: cssVar("--verdigris"), tipColor: cssVar("--verdigris"), points: hist.daily.map((x) => [x.day, x.earned]) },
    ]);
  }
}
