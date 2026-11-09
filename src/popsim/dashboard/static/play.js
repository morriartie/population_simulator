// Play: start a live run, control it, watch it, save it.
import { api, h, toast, refreshLiveDot } from "./util.js";
import { RunView } from "./runview.js";
import { Inspector } from "./inspector.js";
import { tabs } from "./util.js";

const SPEEDS = [0.25, 0.5, 1, 2, 4, 8, 16, 32, 64];

export async function mount(main, _arg, query) {
  const session = await api("/api/session");
  if (session.status !== "none") return mountLive(main, session);
  return mountStart(main, query.get("scenario"));
}

async function mountStart(main, preselect) {
  const list = (await api("/api/scenarios")).filter((s) => !s.error);
  main.append(h("h1", {}, "Play"),
    h("p", { class: "lede" }, "Run a scenario live and watch prices, people and caravans change hour by hour. Pause or step at any point, then save the run to explore it later."));
  if (!list.length) {
    main.append(h("div", { class: "empty", style: "margin-top:20px" }, h("p", {}, "There are no valid scenarios to play."),
      h("a", { class: "btn", href: "#/scenarios" }, "Create a scenario")));
    return;
  }
  const sel = h("select", { id: "play-scenario", onchange: () => sync() },
    list.map((s) => h("option", { value: s.name, selected: s.name === preselect }, s.name)));
  const days = h("input", { type: "number", min: 1, step: 1, id: "play-days" });
  const seed = h("input", { type: "number", step: 1, id: "play-seed", placeholder: "scenario's" });
  const name = h("input", { id: "play-name" });
  const info = h("p", { class: "muted", style: "flex-basis:100%;margin:0" });
  function sync() {
    const s = list.find((x) => x.name === sel.value);
    days.placeholder = s.days;
    name.value = `${s.name}-live`;
    info.textContent = `${s.cities.join(", ")}. ${s.events ? `${s.events} scheduled event${s.events > 1 ? "s" : ""}.` : "No scheduled events."}`;
  }
  sync();
  const start = h("button", { type: "button", class: "primary", onclick: async () => {
    start.disabled = true;
    try {
      await api("/api/session", { method: "POST", body: {
        scenario: sel.value, name: name.value.trim() || undefined,
        days: days.value ? +days.value : undefined, seed: seed.value !== "" ? +seed.value : undefined } });
      main.replaceChildren();
      const s = await api("/api/session");
      cleanup = await mountLive(main, s);
      refreshLiveDot();
    } catch (e) { toast(e.message, "error"); start.disabled = false; }
  } }, "Start live run");
  main.append(h("div", { class: "start-form" },
    h("label", { class: "field" }, "Scenario", sel),
    h("label", { class: "field" }, "Days", days),
    h("label", { class: "field" }, "Seed", seed),
    h("label", { class: "field" }, "Save as", name),
    start, info));
  let cleanup = null;
  return () => cleanup && cleanup();
}

function goPlay() {
  if (location.hash === "#/play") window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = "#/play";
}

function mountLive(main, initial) {
  let state = initial;
  const view = { rv: null };
  const clock = h("span", { class: "clock" });
  const status = h("span", { class: "muted" });
  const bar = h("div");
  const playBtn = h("button", { type: "button", class: "primary", onclick: () => control(state.status === "playing" ? "pause" : "play") });
  const hourBtn = h("button", { type: "button", onclick: () => control("step_hour") }, "Step 1 hour");
  const dayBtn = h("button", { type: "button", onclick: () => control("step_day") }, "Step 1 day");
  const speedOut = h("output", { for: "speed" });
  const speed = h("input", { id: "speed", type: "range", min: 0, max: SPEEDS.length - 1, step: 1, "aria-label": "Speed in days per second",
    value: Math.max(0, SPEEDS.indexOf(state.speed)),
    oninput: (e) => { const v = SPEEDS[+e.target.value]; speedOut.textContent = `${v} days/s`; control("speed", v); } });
  speedOut.textContent = `${state.speed} days/s`;
  const extendDays = h("input", { type: "number", min: 1, step: 1, value: 10, "aria-label": "Days to add" });
  const extendBox = h("span", { class: "row", hidden: true }, extendDays,
    h("button", { type: "button", onclick: () => control("extend", +extendDays.value || 10) }, "Add days"));
  const saveName = h("input", { value: state.name, "aria-label": "Run name" });
  const saveBtn = h("button", { type: "button", onclick: save }, "Save run");
  const discardBtn = h("button", { type: "button", class: "danger", onclick: discard }, "Discard");
  const savedNote = h("span");

  const host = h("div");
  const citizensHost = h("div");
  let insp = null;
  let pendingCity = null;
  const views = tabs([["charts", "Charts and map", host], ["citizens", "Citizens", citizensHost]], (id) => {
    if (id === "citizens" && !insp) {
      insp = new Inspector(citizensHost, { base: "/api/session", live: true });
      if (pendingCity) insp.city = pendingCity;
      insp.start();
    }
    if (insp) insp.setVisible(id === "citizens");
  });
  const openCity = (name) => { pendingCity = name; views.select("citizens"); if (insp) insp.selectCity(name); };
  main.append(
    h("div", { class: "run-head" }, h("div", {}, h("h1", {}, `Live: ${state.scenario}`),
      h("p", { class: "lede" }, `World ${state.world}, market ${state.market === "api" ? "through the market API" : "in-process"}. One live run at a time.`))),
    h("div", { class: "transport", role: "group", "aria-label": "Playback" },
      clock, playBtn, hourBtn, dayBtn, h("label", { class: "row" }, "Speed", speed, speedOut), extendBox,
      h("div", { class: "spacer" }), saveName, saveBtn, discardBtn,
      h("div", { class: "progress", "aria-hidden": "true" }, bar), status, savedNote),
    views);

  function renderState() {
    const day = state.day, hour = Math.floor(state.hour);
    const total = state.total_days;
    clock.textContent = `Day ${day}, ${String(hour).padStart(2, "0")}:00`;
    playBtn.textContent = state.status === "playing" ? "Pause" : "Play";
    playBtn.disabled = state.status === "finished" || state.status === "error";
    hourBtn.disabled = dayBtn.disabled = state.status === "finished" || state.status === "error";
    extendBox.hidden = state.status !== "finished";
    bar.style.width = `${Math.min(100, (100 * state.tick) / Math.max(1, state.total_ticks))}%`;
    status.textContent = state.status === "error" ? `Stopped by an error: ${state.error}`
      : state.status === "finished" ? `Finished all ${total} days. Save the run, or add days to keep going.`
      : `${state.status === "playing" ? "Playing" : "Paused"}, ${Math.round(total)} days planned.`;
    status.className = state.status === "error" ? "bad" : "muted";
    if (state.saved_as) savedNote.replaceChildren("Saved as ", h("a", { href: `#/runs/${encodeURIComponent(state.saved_as)}` }, state.saved_as), ".");
  }
  renderState();

  async function control(action, value) {
    try { state = await api("/api/session/control", { method: "POST", body: { action, value } }); renderState(); }
    catch (e) { toast(e.message, "error"); }
  }
  async function save() {
    saveBtn.disabled = true;
    try {
      const r = await api("/api/session/save", { method: "POST", body: { name: saveName.value.trim() || undefined } });
      toast(`Saved run ${r.saved_as}`);
      state.saved_as = r.saved_as;
      renderState();
    } catch (e) { toast(e.message, "error"); }
    saveBtn.disabled = false;
  }
  async function discard() {
    if (!state.saved_as && !confirm("Discard this live run without saving?")) return;
    await api("/api/session", { method: "DELETE" });
    refreshLiveDot();
    goPlay();
  }

  const es = new EventSource("/api/session/stream");
  es.addEventListener("reset", (e) => {
    const snap = JSON.parse(e.data);
    state = snap.state;
    renderState();
    if (!view.rv) view.rv = new RunView(host, { live: true, onCity: openCity });
    view.rv.setData(snap);
  });
  es.addEventListener("frame", (e) => {
    const msg = JSON.parse(e.data);
    state = msg.state;
    renderState();
    if (view.rv) view.rv.append(msg.delta, msg.now);
    if (insp) insp.refresh();
  });
  es.addEventListener("closed", () => {
    es.close();
    toast("The live run was closed.");
    goPlay();
  });

  return () => { es.close(); if (view.rv) view.rv.destroy(); if (insp) insp.destroy(); };
}
