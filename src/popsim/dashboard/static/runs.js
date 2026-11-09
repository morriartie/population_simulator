// Runs: list of result folders, and the run view for one of them.
import { api, h, num, tabs } from "./util.js";
import { Inspector } from "./inspector.js";
import { RunView } from "./runview.js";

export async function mount(main, name) {
  return name ? mountRun(main, name) : mountList(main);
}

async function mountList(main) {
  const runs = await api("/api/runs");
  main.append(
    h("h1", {}, "Runs"),
    h("p", { class: "lede" }, "Every finished run in the runs folder. Open one to explore it, or start a new one from Play."),
  );
  if (!runs.length) {
    main.append(h("div", { class: "empty", style: "margin-top:24px" },
      h("p", {}, "No runs yet."),
      h("p", { class: "muted" }, "Start a live run in Play and save it, or run a scenario from the command line."),
      h("a", { class: "btn", href: "#/play" }, "Start a run")));
    return;
  }
  const rows = runs.map((r) => h("tr", { class: "link", tabindex: 0,
    onclick: () => { location.hash = `#/runs/${encodeURIComponent(r.name)}`; },
    onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/runs/${encodeURIComponent(r.name)}`; } },
    h("td", {}, h("a", { href: `#/runs/${encodeURIComponent(r.name)}` }, r.name)),
    h("td", {}, r.scenario || h("span", { class: "muted" }, "unknown")),
    h("td", { class: "num" }, num(r.days, 1)),
    h("td", { class: "num" }, r.seed ?? "–"),
    h("td", { class: "num" }, num(r.population, 0)),
    h("td", { class: "num" }, num(r.deaths, 0)),
    h("td", {}, h("span", { class: `status ${r.ok ? "good" : "bad"}` }, r.ok ? "Balanced" : "Check failed")),
    h("td", { class: "muted" }, new Date(r.modified).toLocaleString()),
  ));
  main.append(h("table", { style: "margin-top:22px" },
    h("thead", {}, h("tr", {}, ...["Run", "Scenario"].map((t) => h("th", {}, t)),
      ...["Days", "Seed", "Alive", "Deaths"].map((t) => h("th", { class: "num" }, t)),
      h("th", {}, "Ledger"), h("th", {}, "Saved"))),
    h("tbody", {}, rows)));
}

async function mountRun(main, name) {
  const data = await api(`/api/runs/${encodeURIComponent(name)}`);
  const s = data.summary || {};
  const deaths = Object.values(s.deaths || {}).reduce((a, b) => a + b, 0);
  const ok = s.ledger && s.ledger.ok && s.conservation_daily_ok && s.escrow_matches_market;
  main.append(
    h("p", {}, h("a", { href: "#/runs" }, "All runs")),
    h("div", { class: "run-head" },
      h("div", {},
        h("h1", {}, name),
        h("p", { class: "lede" },
          `${s.scenario ? `Scenario ${s.scenario}, ` : ""}seed ${s.seed ?? "?"}, ${num(s.days, 1)} days. `,
          `${num(s.population, 0)} people alive at the end, ${deaths} died. `,
          h("span", { class: ok ? "good" : "bad" }, ok ? "Ledger balanced." : "A conservation check failed."))),
      h("div", { class: "spacer" }),
      h("a", { class: "btn", href: `#/compare?runs=${encodeURIComponent(name)}` }, "Compare with other runs"),
      s.scenario ? h("a", { class: "btn", href: `#/play?scenario=${encodeURIComponent(s.scenario)}` }, "Play this scenario again") : ""),
  );
  if (!data.config) {
    main.append(h("p", { class: "muted" }, "This run was saved before maps were recorded, so the map shows no cities. Re-run it to get the full view."));
  }
  const host = h("div");
  let insp = null, pendingCity = null, views = null;
  const view = new RunView(host, { onCity: data.inspectable ? (name) => { pendingCity = name; views.select("citizens"); if (insp) insp.selectCity(name); } : null });
  view.setData(data);
  const citizensHost = h("div");
  views = tabs([["charts", "Charts and map", host], ["citizens", "Citizens", citizensHost]], (id) => {
    if (id !== "citizens" || insp) return;
    if (!data.inspectable) {
      citizensHost.append(h("div", { class: "empty" }, h("p", {}, "This run was saved without a snapshot, so its citizens cannot be inspected."),
        h("p", { class: "muted" }, "Run it with --save from the command line, or save it from Play, to keep one.")));
      return;
    }
    insp = new Inspector(citizensHost, { base: `/api/runs/${encodeURIComponent(name)}` });
    if (pendingCity) insp.city = pendingCity;
    insp.start();
  });
  main.append(views);
  return () => { view.destroy(); if (insp) insp.destroy(); };
}
