// The scenario editor with the map tab (REQ-1C.8), mounted on a fake server: the three views share
// one scenario, edits through the map are validated and previewed, drafts survive a reload.
import test from "node:test";
import assert from "node:assert/strict";
import { installDom, byClass, byTag, byText, FakeNode } from "./dom_stub.mjs";

const env = installDom();
globalThis.location = { hash: "" };
globalThis.history = { replaceState() {} };
const { mount } = await import("../../src/popsim/dashboard/static/scenarios.js");

const node = (good) => ({ good, initial: 100, max_amount: 200, regen_per_hour: 2, base_rate: 0.7, stamina_cost: 5 });
const BASE = { goods: ["food", "wood"], movement: { default_city_radius: 2, road: { speed: 5, stamina_per_unit: 0.5 } },
  time: { days: 10 }, cities: [
    { name: "A", x: 0, y: 0, radius: 6, population: 10, nodes: [node("food")] },
    { name: "B", x: 40, y: 0, radius: 5, population: 10, nodes: [] }], roads: [], events: [] };

/** A fake server holding one scenario, "demo", and recording what the editor asks of it. */
function fakeServer({ saved = { extends: "baseline.yaml", world_name: "demo" } } = {}) {
  const server = { saved, puts: [], validations: 0, previews: 0 };
  const resolve = (own) => ({ ...JSON.parse(JSON.stringify(BASE)), ...own });
  env.net.calls.length = 0;
  env.net.respond = (url, body) => {
    if (url === "/api/defaults") return { fiscal: { income_tax: 0 } };
    if (url === "/api/scenarios") return [{ name: "demo", cities: ["A", "B"], days: 10, events: 0, error: null }];
    if (url === "/api/scenarios/demo" && !body) return { name: "demo", yaml: JSON.stringify(server.saved), own: server.saved, resolved: resolve(server.saved) };
    if (url === "/api/yaml/render") return { yaml: JSON.stringify(body.data) };
    if (url === "/api/scenarios/validate") { server.validations++; const own = JSON.parse(body.yaml); return { ok: true, errors: [], warnings: [], resolved: resolve(own), error_items: [], warning_items: [] }; }
    if (url === "/api/scenarios/preview") { server.previews++; return { ok: true, world: { nodes: [], houses: [], workplaces: [] } }; }
    if (url === "/api/scenarios/helper") return { data: { ...body.data, roads: [{ a: "A", b: "B" }] }, added: ["road A to B"], notes: [] };
    if (url.startsWith("/api/scenarios/") && body && body.yaml) { server.puts.push(JSON.parse(body.yaml)); return { saved: true, ok: true, errors: [], warnings: [] }; }
    return {};
  };
  return server;
}

const settle = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const tab = (main, label) => byText(main, "button", label)[0];

async function open() {
  const main = new FakeNode("main");
  const cleanup = await mount(main, "demo");
  await settle();
  return { main, cleanup };
}

test("the Map tab sits beside Form and YAML and shows the scenario's cities", async () => {
  fakeServer();
  localStorage.removeItem("popsim.draft:demo");
  const { main, cleanup } = await open();
  assert.deepEqual(byClass(main, "tabs")[0].children.map((b) => b.textContent), ["Form", "Map", "YAML"]);
  tab(main, "Map").fire("click");
  await settle();
  assert.equal(byClass(main, "mapeditor").length, 1);
  assert.equal(byClass(main, "me-city").length, 2);
  assert.equal(byClass(main, "me-city").length + byTag(main, "textarea").length, 2);   // the YAML textarea is not shown
  cleanup();
});

test("an edit on the map is validated, previewed, kept as a draft, and shared with the form", async () => {
  const server = fakeServer();
  localStorage.removeItem("popsim.draft:demo");
  const { main, cleanup } = await open();
  tab(main, "Map").fire("click");
  await settle();
  const v0 = server.validations;
  byText(main, "button", "City")[0].fire("click");
  const svg = byTag(main, "svg")[0];
  const bg = byClass(svg, "me-bg")[0];
  // click on empty ground: a point a bit above and between the cities, in the svg's own pixel units
  bg.fire("pointerdown", { clientX: 500, clientY: 100 });
  await settle(400);                                     // sync + the preview, which waits a moment after an edit
  assert.ok(server.validations > v0, "the edit was validated");
  assert.ok(server.previews >= 1, "and the preview of automatic positions was asked for");
  const draft = JSON.parse(localStorage.getItem("popsim.draft:demo"));
  assert.equal(draft.own.cities.length, 3);              // the draft holds the new city
  assert.match(main.textContent, /Unsaved changes/);
  assert.equal(byClass(main, "me-city").length, 3);
  tab(main, "Form").fire("click");
  await settle();
  assert.match(main.textContent, /City 3/);              // the form sees the same city
  cleanup();
});

test("saving writes the scenario and clears the draft; leaving unsaved work asks the browser to confirm", async () => {
  const server = fakeServer();
  localStorage.removeItem("popsim.draft:demo");
  const { main, cleanup } = await open();
  assert.equal(window.listenerCount("beforeunload"), 1);
  const ev = { prevented: false };
  ev.preventDefault = () => { ev.prevented = true; };
  window.fire("beforeunload", ev);
  assert.equal(ev.prevented, false);                      // nothing changed: leave freely
  tab(main, "Map").fire("click");
  await settle();
  byText(main, "button", "City")[0].fire("click");
  byClass(byTag(main, "svg")[0], "me-bg")[0].fire("pointerdown", { clientX: 500, clientY: 100 });
  await settle(400);
  window.fire("beforeunload", ev);
  assert.equal(ev.prevented, true);                       // unsaved: the browser is asked to confirm
  byText(main, "button", "Save scenario")[0].fire("click");
  await settle();
  assert.equal(server.puts.length, 1);
  assert.equal(server.puts[0].cities.length, 3);
  assert.equal(localStorage.getItem("popsim.draft:demo"), null);
  cleanup();
  assert.equal(window.listenerCount("beforeunload"), 0);  // the guard goes away with the view
});

test("a draft kept before a reload is offered again and can be restored or discarded", async () => {
  const server = fakeServer();
  const draftOwn = { ...server.saved, cities: [{ name: "Z", x: 1, y: 1, radius: 2, population: 3, nodes: [] }] };
  localStorage.setItem("popsim.draft:demo", JSON.stringify({ name: "demo", own: draftOwn, ts: Date.now() }));
  const { main, cleanup } = await open();
  assert.match(main.textContent, /A draft from .* was kept in this browser/);
  byText(main, "button", "Restore the draft")[0].fire("click");
  await settle();
  assert.match(main.textContent, /Unsaved changes/);
  assert.doesNotMatch(main.textContent, /A draft from/);
  cleanup();

  localStorage.setItem("popsim.draft:demo", JSON.stringify({ name: "demo", own: draftOwn, ts: Date.now() }));
  const again = await open();
  byText(again.main, "button", "Discard it")[0].fire("click");
  assert.equal(localStorage.getItem("popsim.draft:demo"), null);
  assert.doesNotMatch(again.main.textContent, /A draft from/);
  again.cleanup();

  // a draft identical to what is saved is not worth mentioning
  localStorage.setItem("popsim.draft:demo", JSON.stringify({ name: "demo", own: server.saved, ts: Date.now() }));
  const same = await open();
  assert.doesNotMatch(same.main.textContent, /A draft from/);
  same.cleanup();
  localStorage.removeItem("popsim.draft:demo");
});

test("a helper from the map goes through the server and lands in the saved scenario", async () => {
  const server = fakeServer();
  localStorage.removeItem("popsim.draft:demo");
  const { main, cleanup } = await open();
  tab(main, "Map").fire("click");
  await settle();
  byText(main, "button", "Connect cities")[0].fire("click");
  await settle();
  assert.match(main.textContent, /road A to B/);
  byText(main, "button", "Apply")[0].fire("click");
  await settle(400);
  byText(main, "button", "Save scenario")[0].fire("click");
  await settle();
  assert.deepEqual(server.puts.at(-1).roads, [{ a: "A", b: "B" }]);
  cleanup();
});
