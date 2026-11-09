// Compare: overlay the same city/good series from several runs.
import { api, h, cssVar } from "./util.js";
import { LineChart, legend, smooth } from "./charts.js";

const RUN_COLORS = ["--indigo", "--ochre", "--verdigris", "--plum", "--olive", "--madder"];

export async function mount(main, _arg, query) {
  const runs = await api("/api/runs");
  const picked = new Set((query.get("runs") || "").split(",").filter(Boolean));
  if (picked.size < 2) for (const r of runs) { if (picked.size >= 2) break; picked.add(r.name); }
  const cache = new Map();
  let log = true, window = 3, metric = "price";
  let charts = [];

  main.append(h("h1", {}, "Compare runs"),
    h("p", { class: "lede" }, "Overlay the same series from different runs, for example a scenario against its control. Same seed means differences come from the scenario, not chance."));
  if (runs.length < 2) {
    main.append(h("div", { class: "empty", style: "margin-top:24px" }, h("p", {}, "You need at least two runs to compare.")));
    return;
  }
  const pick = h("div", { class: "compare-pick", role: "group", "aria-label": "Runs to compare" },
    runs.map((r) => h("label", {}, h("input", { type: "checkbox", checked: picked.has(r.name),
      onchange: (e) => { e.target.checked ? picked.add(r.name) : picked.delete(r.name); render(); } }), r.name)));
  const metricSel = h("select", { "aria-label": "Series", onchange: (e) => { metric = e.target.value; render(); } },
    h("option", { value: "price" }, "Prices"), h("option", { value: "population" }, "Population"),
    h("option", { value: "mean_gold" }, "Mean gold"), h("option", { value: "gini" }, "Gini"),
    h("option", { value: "sick" }, "Sick"));
  const logBtn = h("button", { type: "button", class: "toggle", "aria-pressed": "true",
    onclick: (e) => { log = !log; e.target.setAttribute("aria-pressed", String(log)); render(); } }, "Log scale");
  const smoothSel = h("select", { "aria-label": "Smoothing", onchange: (e) => { window = +e.target.value; render(); } },
    ...[[1, "No smoothing"], [3, "3-day average"], [7, "7-day average"]].map(([v, t]) => h("option", { value: v, selected: v === 3 }, t)));
  const grid = h("div", { class: "chart-grid" });
  main.append(pick, h("div", { class: "toolbar" }, metricSel, logBtn, smoothSel), grid);

  async function render() {
    for (const c of charts) c.destroy();
    charts = [];
    const names = runs.map((r) => r.name).filter((n) => picked.has(n));
    for (const n of names) if (!cache.has(n)) cache.set(n, await api(`/api/runs/${encodeURIComponent(n)}`));
    const datas = names.map((n) => cache.get(n));
    const cities = [...new Set(datas.flatMap((d) => (d.config ? d.config.cities.map((c) => c.name) : d.cities_daily.map((r) => r.city))))];
    const goods = [...new Set(datas.flatMap((d) => d.prices_daily.map((r) => r.good)))];
    const colors = names.map((_, i) => cssVar(RUN_COLORS[i % RUN_COLORS.length]));
    grid.replaceChildren();
    grid.style.gridTemplateColumns = `repeat(${Math.max(1, Math.min(metric === "price" ? cities.length : 3, 3))}, minmax(0, 1fr))`;
    if (!names.length) { grid.append(h("p", { class: "muted" }, "Pick at least one run.")); return; }
    const panels = metric === "price" ? goods.flatMap((g) => cities.map((c) => [g, c])) : cities.map((c) => [null, c]);
    for (const [good, city] of panels) {
      const host = h("div");
      grid.append(h("div", { class: "panel" }, h("h3", {}, good ? `${good} in ${city}` : `${metricSel.selectedOptions[0].text} in ${city}`),
        legend(names.map((n, i) => ({ name: n, color: colors[i], dash: i % 2 === 1 }))), host));
      const ch = new LineChart(host, { log: metric === "price" && log, label: `${good || metric} in ${city}`, yMin: metric === "price" ? null : 0 });
      const series = datas.map((d, i) => {
        const pts = good
          ? d.prices_daily.filter((r) => r.city === city && r.good === good).map((r) => [r.day, r.vwap ?? r.last ?? null])
          : d.cities_daily.filter((r) => r.city === city).map((r) => [r.day, r[metric]]);
        return { name: names[i], color: colors[i], tipColor: colors[i], dash: i % 2 === 1, points: smooth(pts, good ? window : 1) };
      });
      const events = datas.flatMap((d) => d.events.map((e) => ({ x: Number(e.day), label: e.type.replace("_", " ") })));
      const seen = new Set();
      ch.set(series, events.filter((e) => { const k = `${e.x}|${e.label}`; if (seen.has(k)) return false; seen.add(k); return true; }));
      charts.push(ch);
    }
    history.replaceState(null, "", `#/compare?runs=${names.map(encodeURIComponent).join(",")}`);
  }
  await render();
  return () => charts.forEach((c) => c.destroy());
}
