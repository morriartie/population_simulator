// The map editor's interaction code (REQ-1C.8), run against a small DOM stand-in: tools, dragging,
// the side panel, helpers with their confirmation, keyboard, problems and the preview.
import test from "node:test";
import assert from "node:assert/strict";
import { installDom, byClass, byTag, byText, findAll } from "./dom_stub.mjs";

const env = installDom();
const { MapEditor } = await import("../../src/popsim/dashboard/static/mapeditor.js");
const M = await import("../../src/popsim/dashboard/static/mapmodel.js");

const node = (good) => ({ good, initial: 100, max_amount: 200, regen_per_hour: 2, base_rate: 0.7, stamina_cost: 5 });

function setup({ spatial = true, extra = {} } = {}) {
  const cities = [
    { name: "A", ...(spatial ? { x: 0, y: 0, radius: 6 } : {}), population: 10, nodes: [node("food")] },
    { name: "B", ...(spatial ? { x: 40, y: 0, radius: 5 } : {}), population: 10, nodes: [] },
  ];
  const resolved = { goods: ["food", "wood"], movement: { default_city_radius: 2, road: { speed: 5, stamina_per_unit: 0.5 } },
    cities: M.clone(cities), roads: [], events: [] };
  const st = { own: { extends: "baseline.yaml", keep: { me: true }, cities: M.clone(cities), ...extra }, resolved, report: null, yaml: "extends: baseline.yaml", name: "t" };
  const log = { edited: 0 };
  const ed = new MapEditor({ state: () => st, edited: () => { log.edited++; }, setOwn: (o) => { st.own = o; } });
  env.net.calls.length = 0;
  env.net.respond = () => ({});
  ed.update();
  return { ed, st, log };
}

const at = (ed, x, y) => ({ clientX: ed.sx(x), clientY: ed.sy(y) });
const cityEl = (ed, i) => byClass(ed.svg, "me-city")[i];
const bgEl = (ed) => byClass(ed.svg, "me-bg")[0];
const tool = (ed, label) => byText(ed.toolbar, "button", label)[0].fire("click");
const helperBtn = (ed, label) => byText(ed.options, "button", label)[0].fire("click");
const toastText = () => env.toast.textContent;

test("the map draws every city, road and item, and nothing else", () => {
  const { ed, st } = setup();
  st.own.roads = [{ a: "A", b: "B" }];
  st.own.events = [{ day: 9, type: "add_road", a: "B", b: "A", hours: 3 }, { day: 2, type: "mint", city: "A", amount: 5 }];
  ed.draw();
  assert.equal(byClass(ed.svg, "me-city").length, 2);
  assert.equal(byClass(ed.svg, "me-road").length, 2);                 // the road and the add_road event, not the mint
  assert.equal(byClass(ed.svg, "me-item").length, 1);                 // A's food node
  const label = byTag(byClass(ed.svg, "me-road")[0], "text")[0].textContent;
  assert.match(label, /40 km · 8 h · 20 st/);                          // derived from the straight line: 40 km at 5 km/h
  assert.match(byTag(byClass(ed.svg, "me-road")[1], "text")[0].textContent, /day 9/);
  assert.equal(byClass(ed.toolbar, "toggle").length, 7);               // seven tools
});

test("dragging a city moves it as one undo step and keeps everything else", () => {
  const { ed, st, log } = setup();
  const before = JSON.stringify(st.own.cities[1]);
  cityEl(ed, 0).fire("pointerdown", at(ed, 0, 0));
  assert.deepEqual(ed.sel, { kind: "city", ci: 0 });
  assert.equal(window.listenerCount("pointerup"), 1);
  for (const [x, y] of [[3, 1], [8, 2], [12.3, 4.4]]) window.fire("pointermove", at(ed, x, y));
  window.fire("pointerup");
  const step = ed.step();
  assert.equal(st.own.cities[0].x, M.round2(M.snap(12.3, step)));
  assert.equal(st.own.cities[0].y, M.round2(M.snap(4.4, step)));
  assert.equal(log.edited, 1);                                         // one edit, however many mouse moves
  assert.equal(window.listenerCount("pointerup"), 0);                  // the drag listeners are gone
  assert.equal(JSON.stringify(st.own.cities[1]), before);
  assert.deepEqual(st.own.keep, { me: true });
  ed.undo();
  assert.equal(st.own.cities[0].x, 0);
  ed.redo();
  assert.equal(st.own.cities[0].x, M.round2(M.snap(12.3, step)));
});

test("the radius handle resizes the selected city and reports what falls outside", () => {
  const { ed, st } = setup();
  ed.edit((o, r) => M.addItem(o, r, 0, "resource", M.newNode("wood"), [5, 0]));
  ed.select({ kind: "city", ci: 0 });
  const handle = byClass(ed.svg, "me-handle")[0];
  assert.ok(handle, "a selected city shows its radius handle");
  handle.fire("pointerdown", at(ed, 6, 0));
  window.fire("pointermove", at(ed, 2.2, 0));
  window.fire("pointerup");
  assert.ok(st.own.cities[0].radius < 6 && st.own.cities[0].radius >= 1);
  assert.equal(st.own.cities[0].nodes.length, 2);                      // the node outside is kept, not dropped
});

test("the City tool places a city with a node of every good, and selects it", () => {
  const { ed, st, log } = setup();
  tool(ed, "City");
  bgEl(ed).fire("pointerdown", at(ed, 20.3, 15.2));
  assert.equal(st.own.cities.length, 3);
  const c = st.own.cities[2];
  assert.equal(c.name, "City 3");
  assert.deepEqual(c.nodes.map((n) => n.good), ["food", "wood"]);
  assert.equal(c.x, M.round2(M.snap(20.3, ed.step())));
  assert.deepEqual(ed.sel, { kind: "city", ci: 2 });
  assert.equal(log.edited, 1);
  assert.match(ed.panel.textContent, /City City 3/);
  assert.equal(byClass(ed.svg, "me-city").length, 3);
});

test("a road is dragged from one city to another; a second one between them is refused", () => {
  const { ed, st } = setup();
  tool(ed, "Road");
  cityEl(ed, 0).fire("pointerdown", at(ed, 0, 0));
  assert.equal(ed.pendingRoad, "A");
  window.fire("pointermove", at(ed, 20, 0));                           // (also handled by the canvas while a road is pending)
  ed.svg.fire("pointermove", at(ed, 30, 0));
  assert.ok(ed.rubber);
  cityEl(ed, 1).fire("pointerup", at(ed, 40, 0));
  assert.deepEqual(st.own.roads, [{ a: "A", b: "B" }]);
  assert.deepEqual(ed.sel, { kind: "road", ri: 0 });
  assert.equal(ed.pendingRoad, null);
  cityEl(ed, 1).fire("pointerdown", at(ed, 40, 0));
  cityEl(ed, 0).fire("pointerdown", at(ed, 0, 0));                     // click, click works too
  assert.equal(st.own.roads.length, 1);
  assert.match(toastText(), /already have a road/);
  cityEl(ed, 0).fire("pointerdown", at(ed, 0, 0));
  cityEl(ed, 0).fire("pointerdown", at(ed, 0, 0));
  assert.match(toastText(), /two different cities/);
  assert.equal(st.own.roads.length, 1);
});

test("the road panel shows derived values, overrides them, and can make the road open later", () => {
  const { ed, st } = setup();
  st.own.roads = [{ a: "A", b: "B" }];
  ed.select({ kind: "road", ri: 0 });
  const inputs = byTag(ed.panel, "input");
  assert.match(inputs[0].attrs.placeholder, /40 \(straight line\)/);
  assert.equal(inputs[1].attrs.placeholder, "8 (from length)");
  inputs[0].value = "55";
  inputs[0].listeners.change[0]({ target: inputs[0] });
  assert.equal(st.own.roads[0].length, 55);
  const day = byTag(ed.panel, "input")[3];
  day.value = "12";
  day.listeners.change[0]({ target: day });
  assert.deepEqual(st.own.roads, []);
  assert.deepEqual(st.own.events[0], { day: 12, type: "add_road", a: "A", b: "B", length: 55 });
  assert.equal(ed.sel, null);
});

test("resources, workplaces and houses are placed by clicking inside a city, and not outside", () => {
  const { ed, st } = setup();
  tool(ed, "Resource");
  byTag(ed.options, "select")[0].listeners.change[0]({ target: { value: "wood" } });
  cityEl(ed, 0).fire("pointerdown", at(ed, 3, 2));
  const added = st.own.cities[0].nodes.at(-1);
  assert.equal(added.good, "wood");
  assert.equal(added.at[0], M.round2(M.snap(3, ed.step())) - 0);
  assert.deepEqual(ed.sel, { kind: "resource", ci: 0, i: 1 });
  bgEl(ed).fire("pointerdown", at(ed, 20, 20));
  assert.match(toastText(), /Click inside a city/);
  assert.equal(st.own.cities[0].nodes.length, 2);
  tool(ed, "Workplace");
  cityEl(ed, 1).fire("pointerdown", at(ed, 41, 1));
  assert.deepEqual(st.own.cities[1].workplaces[0].task, "guard");
  tool(ed, "House");
  cityEl(ed, 1).fire("pointerdown", at(ed, 38, -1));
  assert.equal(st.own.cities[1].houses.length, 1);
  assert.ok(Array.isArray(st.own.cities[1].houses[0].at));
  for (const city of st.own.cities) for (const kind of ["nodes", "workplaces", "houses"]) for (const it of city[kind] || [])
    if (it.at) assert.ok(Math.hypot(...it.at) <= city.radius, "never outside its city");
});

test("an item cannot be dragged out of its city", () => {
  const { ed, st } = setup();
  ed.edit((o, r) => M.addItem(o, r, 0, "resource", M.newNode("wood"), [1, 1]));
  const item = byClass(ed.svg, "me-item")[1];
  item.fire("pointerdown", at(ed, 1, 1));
  window.fire("pointermove", at(ed, 30, 30));
  window.fire("pointerup");
  assert.ok(Math.hypot(...st.own.cities[0].nodes[1].at) <= 6);
  assert.ok(st.own.cities[0].nodes[1].at[0] > 0);                      // it went towards the pointer, to the edge
});

test("automatic items are drawn faded at the preview position and can be placed by hand", async () => {
  const { ed, st } = setup();
  env.net.respond = (url) => ({ ok: true, world: { nodes: [{ ci: 0, i: 0, x: 3, y: 2 }], houses: [], workplaces: [] } });
  await ed.fetchPreview();
  assert.equal(env.net.calls.at(-1).url, "/api/scenarios/preview");
  assert.equal(env.net.calls.at(-1).body.yaml, "extends: baseline.yaml");
  const auto = byClass(ed.svg, "auto");
  assert.equal(auto.length, 1);
  assert.equal(auto[0].children[0].attrs.cx, String(ed.sx(3)));       // drawn where the server says the simulation puts it
  assert.match(auto[0].textContent, /auto/);
  ed.select({ kind: "resource", ci: 0, i: 0 });
  assert.match(ed.panel.textContent, /Automatic: 3, 2 km/);
  byText(ed.panel, "button", "Place by hand")[0].fire("click");
  assert.deepEqual(st.own.cities[0].nodes[0].at, [3, 2]);              // the very spot the preview showed
  assert.equal(byClass(ed.svg, "auto").length, 0);
  byText(ed.panel, "button", "Place automatically")[0].fire("click");
  assert.equal("at" in st.own.cities[0].nodes[0], false);
});

test("Scatter drags a circle in a city and asks the server, which shows what it would add", async () => {
  const { ed, st } = setup();
  tool(ed, "Scatter");
  cityEl(ed, 0).fire("pointerdown", at(ed, 1, 0));
  window.fire("pointermove", at(ed, 3, 0));
  assert.ok(ed.scatter && ed.scatter.r > 1.9);
  const reply = M.clone(st.own);
  reply.cities[0].nodes.push({ ...node("food"), at: [1.5, 0.2] });
  env.net.respond = () => ({ data: reply, added: ["A: 1 nodes scattered in a circle of 2 km"], notes: [] });
  window.fire("pointerup");
  await new Promise((r) => setTimeout(r, 5));
  const call = env.net.calls.at(-1);
  assert.equal(call.url, "/api/scenarios/helper");
  assert.equal(call.body.action, "scatter");
  assert.deepEqual({ city: call.body.params.city, kind: call.body.params.kind, what: call.body.params.what, count: call.body.params.count, center: call.body.params.center, radius: call.body.params.radius },
    { city: "A", kind: "resource", what: "food", count: 8, center: [1, 0], radius: 2 });
  assert.equal(st.own.cities[0].nodes.length, 1);                      // nothing changed yet: it asks first
  assert.match(ed.confirmHost.textContent, /scattered in a circle/);
  byText(ed.confirmHost, "button", "Cancel")[0].fire("click");
  assert.equal(ed.confirmHost.children.length, 0);
  assert.equal(st.own.cities[0].nodes.length, 1);
});

test("a helper's result is applied only when accepted, as one undo step", async () => {
  const { ed, st, log } = setup();
  const reply = M.clone(st.own);
  reply.roads = [{ a: "A", b: "B" }];
  env.net.respond = () => ({ data: reply, added: ["road A to B, 40.0 km (joins a cut-off city)"], notes: ["note"] });
  helperBtn(ed, "Connect cities");
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(env.net.calls.at(-1).body, { data: st.own, action: "connect_cities", params: { nearest: 0 } });
  assert.match(ed.confirmHost.textContent, /joins a cut-off city/);
  assert.match(ed.confirmHost.textContent, /note/);
  assert.equal(st.own.roads, undefined);
  byText(ed.confirmHost, "button", "Apply")[0].fire("click");
  assert.deepEqual(st.own.roads, [{ a: "A", b: "B" }]);
  assert.equal(log.edited, 1);
  ed.undo();
  assert.equal(st.own.roads, undefined);
  // an empty result offers nothing to apply
  env.net.respond = () => ({ data: st.own, added: [], notes: ["Every city is already connected."] });
  helperBtn(ed, "Connect cities");
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(byText(ed.confirmHost, "button", "Apply")[0].attrs.disabled, "");
  // a server error is shown, not thrown
  env.net.respond = () => ({ __status: 400, error: "give every city a position first (Arrange cities)" });
  helperBtn(ed, "Connect cities");
  await new Promise((r) => setTimeout(r, 5));
  assert.match(toastText(), /position first/);
});

test("a scenario without coordinates offers to give the cities positions", async () => {
  const { ed, st } = setup({ spatial: false });
  assert.match(ed.banner.textContent, /no coordinates/);
  const reply = M.clone(st.own);
  reply.cities.forEach((c, i) => Object.assign(c, { x: i * 30, y: 0 }));
  env.net.respond = () => ({ data: reply, added: ["A at (0, 0)", "B at (30, 0)"], notes: [] });
  byText(ed.banner, "button", "Give cities positions")[0].fire("click");
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(env.net.calls.at(-1).body.action, "arrange_cities");
  byText(ed.confirmHost, "button", "Apply")[0].fire("click");
  assert.equal(st.own.cities[1].x, 30);
  assert.equal(ed.banner.children.length, 0);                          // now spatial: the banner is gone
});

test("keys: arrows move, Delete removes, Escape cancels, Ctrl+Z undoes", () => {
  const { ed, st } = setup();
  ed.select({ kind: "city", ci: 0 });
  const step = ed.step();
  ed.root.fire("keydown", { key: "ArrowRight", target: ed.svg });
  assert.equal(st.own.cities[0].x, M.round2(step));
  ed.root.fire("keydown", { key: "z", ctrlKey: true, target: ed.svg });
  assert.equal(st.own.cities[0].x, 0);
  ed.root.fire("keydown", { key: "z", ctrlKey: true, shiftKey: true, target: ed.svg });
  assert.equal(st.own.cities[0].x, M.round2(step));
  ed.select({ kind: "resource", ci: 0, i: 0 });
  ed.root.fire("keydown", { key: "Delete", target: ed.svg });
  assert.equal(st.own.cities[0].nodes, undefined);
  assert.equal(ed.sel, null);
  tool(ed, "Road");
  cityEl(ed, 0).fire("pointerdown", at(ed, 0, 0));
  assert.equal(ed.pendingRoad, "A");
  ed.root.fire("keydown", { key: "Escape", target: ed.svg });
  assert.equal(ed.pendingRoad, null);
  ed.root.fire("keydown", { key: "Escape", target: ed.svg });
  assert.equal(ed.tool, "select");
  // keys typed into a form field are not shortcuts
  ed.select({ kind: "city", ci: 0 });
  const x = st.own.cities[0].x;
  ed.root.fire("keydown", { key: "ArrowLeft", target: { tagName: "INPUT" } });
  assert.equal(st.own.cities[0].x, x);
});

test("deleting a city asks first and takes its roads and events with it", () => {
  const { ed, st } = setup();
  st.own.roads = [{ a: "A", b: "B" }];
  st.own.events = [{ day: 3, type: "mint", city: "B", amount: 1 }, { day: 4, type: "mint", city: "A", amount: 1 }];
  let asked = "";
  globalThis.confirm = (m) => { asked = m; return false; };
  ed.deleteCity(0);
  assert.equal(st.own.cities.length, 2);                                // declined: nothing happens
  assert.match(asked, /Delete A together with 1 road and 1 event/);
  globalThis.confirm = () => true;
  ed.deleteCity(0);
  assert.deepEqual(st.own.cities.map((c) => c.name), ["B"]);
  assert.deepEqual(st.own.roads, []);
  assert.deepEqual(st.own.events.map((e) => e.city), ["B"]);
});

test("problems from validation are listed with their path and select the item on click", () => {
  const { ed, st } = setup();
  st.report = { error_items: [{ path: "cities[0].nodes[0].at", message: "[9, 9] is outside the city radius 6" }, { path: null, message: "something general" }],
    warning_items: [{ path: "cities[1]", message: "'B' has no road" }] };
  ed.drawProblems();
  assert.match(ed.problems.textContent, /1 problems, 1 warnings|2 problems, 1 warnings/);
  const links = byClass(ed.problems, "linklike");
  assert.equal(links.length, 2);                                        // the general error has no item to jump to
  links[0].fire("click");
  assert.deepEqual(ed.sel, { kind: "resource", ci: 0, i: 0 });
  ed.draw();
  const outlined = findAll(ed.svg, (n) => n.attrs && n.attrs.stroke === "RED");
  assert.ok(outlined.length >= 1, "the item with the problem is outlined");
  assert.ok(findAll(ed.svg, (n) => n.attrs && n.attrs.stroke === "AMBER").length >= 1, "and the city with a warning");
});

test("the side panel edits a city, refuses a duplicate name and keeps roads in step with a rename", () => {
  const { ed, st } = setup();
  st.own.roads = [{ a: "A", b: "B" }];
  ed.select({ kind: "city", ci: 1 });
  const name = byTag(ed.panel, "input")[0];
  name.value = "A";
  name.listeners.change[0]({ target: name });
  assert.match(toastText(), /already a city called A/);
  assert.equal(name.value, "B");                                         // put back
  name.value = "Bree";
  name.listeners.change[0]({ target: name });
  assert.equal(st.own.cities[1].name, "Bree");
  assert.deepEqual(st.own.roads, [{ a: "A", b: "Bree" }]);
  const pop = byTag(ed.panel, "input")[1];
  pop.value = "99999";
  pop.listeners.change[0]({ target: pop });
  assert.equal(st.own.cities[1].population, 5000);                      // kept in range
  const tax = byTag(ed.panel, "input").find((i) => i.attrs.max === "0.95");
  tax.value = "0.2";
  tax.listeners.change[0]({ target: tax });
  assert.deepEqual(st.own.cities[1].fiscal, { income_tax: 0.2 });
  tax.value = "";
  tax.listeners.change[0]({ target: tax });
  assert.equal("fiscal" in st.own.cities[1], false);                    // an emptied override is removed
});

test("scrolling zooms around the pointer, and Fit brings everything back", () => {
  const { ed } = setup();
  const k0 = ed.view.k;
  const px = ed.sx(10), py = ed.sy(0);
  const ev = ed.svg.fire("wheel", { clientX: px, clientY: py, deltaY: -300 });
  assert.ok(ed.view.k > k0);
  assert.ok(Math.abs(ed.sx(10) - px) < 1e-6, "the point under the pointer stays put");
  assert.equal(ev.defaultPrevented, true);
  byText(ed.toolbar, "button", "Fit")[0].fire("click");
  assert.ok(Math.abs(ed.view.k - k0) < 1e-9);
});

test("panning the background does not edit anything", () => {
  const { ed, st, log } = setup();
  const ox = ed.view.ox;
  bgEl(ed).fire("pointerdown", { clientX: 100, clientY: 100 });
  window.fire("pointermove", { clientX: 160, clientY: 130 });
  window.fire("pointerup");
  assert.equal(ed.view.ox, ox + 60);
  assert.equal(log.edited, 0);
  assert.equal(ed.history.canUndo, false);
  assert.equal(st.own.cities[0].x, 0);
});

test("the editor works on an inherited scenario by copying the parent's lists first", () => {
  const { ed, st } = setup();
  delete st.own.cities;                                                 // the cities come from the parent scenario
  ed.draw();
  assert.equal(byClass(ed.svg, "me-city").length, 2);
  cityEl(ed, 0).fire("pointerdown", at(ed, 0, 0));
  assert.match(ed.panel.textContent, /Comes from the parent scenario/);
  window.fire("pointermove", at(ed, 6, 0));
  window.fire("pointerup");
  assert.equal(st.own.cities.length, 2);                                // now this scenario's own list
  assert.ok(st.own.cities[0].x > 0 && st.own.cities[1].x === 40);
});

test("reset forgets the old scenario's view, selection and undo steps", () => {
  const { ed } = setup();
  ed.edit((o, r) => M.moveCity(o, r, 0, 5, 5));
  ed.select({ kind: "city", ci: 0 });
  ed.reset();
  assert.equal(ed.sel, null);
  assert.equal(ed.view, null);
  assert.equal(ed.history.canUndo, false);
  ed.destroy();
});

test("keyboard focus stays on the same item when the map is redrawn", () => {
  const { ed, st } = setup();
  const el = cityEl(ed, 0);
  el.focus();
  ed.select({ kind: "city", ci: 0 });                                   // (redraws: the old element is gone)
  cityEl(ed, 0).focus();
  const before = document.activeElement;
  ed.root.fire("keydown", { key: "ArrowRight", target: before });
  assert.notEqual(document.activeElement, before, "the element was replaced by the redraw");
  assert.equal(document.activeElement.attrs["data-key"], "city:0");     // but focus moved to its replacement
  assert.ok(byClass(ed.svg, "me-city")[0] === document.activeElement);
  ed.root.fire("keydown", { key: "ArrowRight", target: document.activeElement });
  assert.equal(st.own.cities[0].x, M.round2(2 * ed.step()));            // so a second key press still works
});
