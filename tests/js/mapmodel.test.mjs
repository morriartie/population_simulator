// Map editor model (REQ-1C.8): the edits the map makes to a scenario, without any DOM.
import test from "node:test";
import assert from "node:assert/strict";
import * as M from "../../src/popsim/dashboard/static/mapmodel.js";

const resolved = {
  goods: ["food", "wood"], movement: { default_city_radius: 2 },
  cities: [
    { name: "A", x: 0, y: 0, radius: 4, population: 10, nodes: [{ good: "food", initial: 1 }], houses: { count: 3 } },
    { name: "B", x: 30, y: 40, population: 10, nodes: [] },
    { name: "C", x: 60, y: 0, radius: 3, population: 10 },
  ],
  roads: [{ a: "A", b: "B" }, { a: "B", b: "C", hours: 5, stamina_cost: 9 }],
  events: [{ day: 3, type: "add_road", a: "A", b: "C" }, { day: 5, type: "mint", city: "A", amount: 10 },
           { day: 6, type: "stimulus", city: "all", per_npc: 5 }, { day: 7, type: "mint", city: "B", amount: 1 }],
};
const fresh = () => ({ own: { extends: "baseline.yaml", foo: { bar: 1 } }, resolved: M.clone(resolved) });

test("an inherited list is copied before it is edited, and the parent is untouched", () => {
  const { own, resolved: r } = fresh();
  assert.equal(M.inherited(own, "cities"), true);
  const res = M.addCity(own, r, 10.123, -5);
  assert.ok(res.ok);
  assert.equal(own.cities.length, 4);                       // the three inherited cities plus the new one
  assert.deepEqual(own.cities[3], { name: "City 4", x: 10.12, y: -5, population: 40, housing_pct: 100 });  // no nodes: they go outside cities
  assert.equal(r.cities.length, 3);                         // resolved (the parent's) is not modified
  assert.deepEqual(own.foo, { bar: 1 });                    // keys the editor does not know are kept
  assert.equal(M.inherited(own, "cities"), false);
});

test("city names stay unique and the number of cities is capped", () => {
  const { own, resolved: r } = fresh();
  M.addCity(own, r, 0, 0); M.addCity(own, r, 0, 0);
  const names = own.cities.map((c) => c.name);
  assert.equal(new Set(names).size, names.length);
  while (own.cities.length < M.MAX_CITIES) assert.ok(M.addCity(own, r, 1, 1).ok);
  const over = M.addCity(own, r, 1, 1);
  assert.equal(over.ok, false);
  assert.match(over.error, /At most 50/);
  assert.equal(own.cities.length, M.MAX_CITIES);
});

test("deleting a city removes its roads and events and nothing else", () => {
  const { own, resolved: r } = fresh();
  own.cities = M.clone(resolved.cities); own.cities[0].custom = [1, 2];
  const info = M.cascadeInfo(own, r, 0);
  assert.equal(info.name, "A");
  assert.equal(info.roads.length, 1);
  assert.equal(info.events.length, 2);                      // add_road A-C and mint A (not stimulus "all", not mint B)
  M.deleteCity(own, r, 0);
  assert.deepEqual(own.cities.map((c) => c.name), ["B", "C"]);
  assert.deepEqual(own.roads, [{ a: "B", b: "C", hours: 5, stamina_cost: 9 }]);
  assert.deepEqual(own.events.map((e) => e.type + (e.city || "")), ["stimulusall", "mintB"]);
  assert.deepEqual(own.cities[1], resolved.cities[2]);      // the other cities are exactly as they were
  assert.deepEqual(own.foo, { bar: 1 });
});

test("renaming a city follows it in roads and events", () => {
  const { own, resolved: r } = fresh();
  assert.equal(M.renameCity(own, r, 1, "A").ok, false);     // taken
  assert.equal(M.renameCity(own, r, 1, "  ").ok, false);
  assert.ok(M.renameCity(own, r, 1, "Bree").ok);
  assert.deepEqual(own.roads.map((x) => [x.a, x.b]), [["A", "Bree"], ["Bree", "C"]]);
  assert.equal(own.events.find((e) => e.city === "B"), undefined);
  assert.equal(own.events.find((e) => e.city === "Bree").type, "mint");
});

test("a road needs two different cities and cannot be added twice, in either direction", () => {
  const { own, resolved: r } = fresh();
  assert.equal(M.addRoad(own, r, "A", "A").ok, false);
  assert.equal(M.addRoad(own, r, "B", "A").ok, false);      // A-B already exists
  assert.equal(own.roads, undefined);                        // a refused road changes nothing, not even by copying
  const ok = M.addRoad(own, r, "A", "C");
  assert.ok(ok.ok);
  assert.deepEqual(own.roads[ok.ri], { a: "A", b: "C" });     // hours, stamina and length are derived
});

test("a road can open on a later day and come back", () => {
  const { own, resolved: r } = fresh();
  M.roadToEvent(own, r, 1, 12);
  assert.deepEqual(own.roads, [{ a: "A", b: "B" }]);
  assert.deepEqual(own.events.at(-1), { day: 12, type: "add_road", a: "B", b: "C", hours: 5, stamina_cost: 9 });
  M.eventToRoad(own, r, own.events.length - 1);
  assert.deepEqual(own.roads.at(-1), { a: "B", b: "C", hours: 5, stamina_cost: 9 });
});

test("items stop at the city edge, whatever the rounding", () => {
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) - 0.5;
  for (let n = 0; n < 2000; n++) {
    const r = 0.5 + Math.abs(rnd()) * 30;
    const [dx, dy] = M.clampOffset(rnd() * 100, rnd() * 100, r);
    assert.ok(Math.hypot(dx, dy) <= r + 1e-9, `${dx},${dy} outside ${r}`);
  }
  assert.deepEqual(M.clampOffset(1.234, -0.5, 4), [1.23, -0.5]);   // inside: only rounded
});

test("moving and placing items is clamped to the city and can go back to automatic", () => {
  const { own, resolved: r } = fresh();
  const added = M.addItem(own, r, 0, "resource", M.newNode("wood"), [1, 1]);
  assert.deepEqual(own.cities[0].nodes[added.i].at, [1, 1]);
  const at = M.moveItem(own, r, "resource", 0, added.i, 100, 0);       // far away: stops at the edge of radius 4
  assert.ok(Math.hypot(...at) <= 4);
  M.setAuto(own, r, "resource", 0, added.i, true);
  assert.equal("at" in own.cities[0].nodes[added.i], false);
  M.setAuto(own, r, "resource", 0, added.i, false, { x: 2, y: 1 });
  assert.deepEqual(own.cities[0].nodes[added.i].at, [2, 1]);
  assert.deepEqual(own.cities[0].nodes[0], resolved.cities[0].nodes[0]);  // the first node is untouched
});

test("automatic houses stay a count until one is placed by hand", () => {
  const { own, resolved: r } = fresh();
  assert.equal(M.addItem(own, r, 0, "house", {}, null).i, 3);
  assert.deepEqual(own.cities[0].houses, { count: 4 });
  M.addItem(own, r, 0, "house", {}, [1, 0]);
  assert.deepEqual(own.cities[0].houses, [{}, {}, {}, {}, { at: [1, 0] }]);   // now a list, the 4 automatic ones kept
  M.deleteItem(own, r, "house", 0, 4);
  assert.equal(M.houseCount(own.cities[0]), 4);
  const b = fresh();
  M.deleteItem(b.own, b.resolved, "house", 0, 0);                     // a count just goes down
  assert.deepEqual(b.own.cities[0].houses, { count: 2 });
});

test("the radius can shrink and the items left outside are reported, not dropped", () => {
  const { own, resolved: r } = fresh();
  M.addItem(own, r, 0, "resource", M.newNode("wood"), [3, 0]);
  M.addItem(own, r, 0, "workplace", { task: "guard", wage: 1 }, [0.5, 0]);
  M.addItem(own, r, 0, "house", {}, [0, 3.5]);
  const outside = M.setCityRadius(own, r, 0, 1);
  assert.deepEqual(outside.map((o) => `${o.kind}${o.i}`).sort(), ["house3", "resource1"]);
  assert.equal(own.cities[0].nodes.length, 2);                        // still there
  assert.equal(M.setCityRadius(own, r, 0, 0).valueOf().length >= 0, true);
  assert.equal(own.cities[0].radius, 0.1);                            // never below 0.1 km
});

test("deleting items removes only that item", () => {
  const { own, resolved: r } = fresh();
  M.addItem(own, r, 1, "resource", M.newNode("food"), null);
  M.addItem(own, r, 1, "resource", M.newNode("wood"), null);
  M.deleteItem(own, r, "resource", 1, 0);
  assert.deepEqual(own.cities[1].nodes.map((n) => n.good), ["wood"]);
  M.deleteItem(own, r, "resource", 1, 0);
  assert.equal("nodes" in own.cities[1], false);
});

test("the layout uses explicit positions, then the server preview, then the city centre", () => {
  const { own, resolved: r } = fresh();
  own.cities = M.clone(resolved.cities);
  own.cities[0].nodes = [{ good: "food", at: [1, 2] }, { good: "wood" }];
  const world = { nodes: [{ ci: 0, i: 1, x: 3.5, y: -1 }] };
  let lay = M.layoutOf(own, r, world);
  assert.deepEqual(lay.nodes.map((n) => [n.x, n.y, n.auto]), [[1, 2, false], [3.5, -1, true]]);
  lay = M.layoutOf(own, r, null);                                      // preview not loaded yet
  assert.deepEqual(lay.nodes[1], { kind: "resource", ci: 0, i: 1, good: "wood", x: 0, y: 0, auto: true, pending: true, inherited: false });
  assert.equal(lay.houses.length, 3);                                  // {count: 3}
  assert.equal(lay.cities[1].radius, 2);                               // the default radius
  assert.deepEqual(lay.roads.map((x) => [x.event, x.a, x.b]), [[false, "A", "B"], [false, "B", "C"], [true, "A", "C"]]);
});

test("validation paths point at the item they concern", () => {
  assert.deepEqual(M.errorTarget("cities[1].nodes[0].at"), { kind: "resource", ci: 1, i: 0 });
  assert.deepEqual(M.errorTarget("cities[0].workplaces[2]"), { kind: "workplace", ci: 0, i: 2 });
  assert.deepEqual(M.errorTarget("cities[0].houses[4].at"), { kind: "house", ci: 0, i: 4 });
  assert.deepEqual(M.errorTarget("cities[3].population"), { kind: "city", ci: 3 });
  assert.deepEqual(M.errorTarget("roads[2].length"), { kind: "road", ri: 2 });
  assert.deepEqual(M.errorTarget("events[1].hours"), { kind: "road", ei: 1 });
  assert.equal(M.errorTarget("time.days"), null);
  assert.equal(M.errorTarget(null), null);
});

test("undo and redo walk through the snapshots", () => {
  const h = new M.History(3);
  h.push("a"); h.push("b"); h.push("c");
  assert.equal(h.undo("d"), "c");
  assert.equal(h.undo("c"), "b");
  assert.equal(h.redo("b"), "c");
  assert.equal(h.canRedo, true);
  h.push("x");                                        // a new edit drops what could be redone
  assert.equal(h.canRedo, false);
  for (const s of ["1", "2", "3", "4", "5"]) h.push(s);
  assert.equal(h.past.length, 3);                    // the limit
  assert.equal(new M.History().undo("x"), null);
});

test("scenarios without coordinates are recognised", () => {
  const { own, resolved: r } = fresh();
  assert.equal(M.spatialState(own, r), "all");
  const none = { cities: [{ name: "A" }, { name: "B" }] };
  assert.equal(M.spatialState({ cities: none.cities }, {}), "none");
  assert.equal(M.spatialState({ cities: [{ name: "A", x: 1, y: 1 }, { name: "B" }] }, {}), "some");
  assert.equal(M.spatialState({}, {}), "all");        // no cities yet: ready for the first one
});

test("the grid step is a round number of km", () => {
  for (const s of [0.5, 3, 12, 40, 250]) {
    const step = M.gridStep(s);
    assert.ok([1, 2, 5].includes(step / 10 ** Math.floor(Math.log10(step))), String(step));
    assert.ok(step * s >= 59 && step * s <= 61 * 2.5);
  }
});


// ------------------------------------------- housing, resource points and the eraser

const spatial = () => {
  const { own, resolved: r } = fresh();
  r.cities[0].housing_pct = 100; r.cities[0].population = 10;
  return { own, r };
};

test("a city generates round(population x pct / 100) houses; a city's pct beats the scenario's", () => {
  assert.equal(M.generatedHouses({ population: 10, housing_pct: 120 }, {}), 12);
  assert.equal(M.generatedHouses({ population: 10, housing_pct: 80 }, {}), 8);
  assert.equal(M.generatedHouses({ population: 10 }, { housing_pct: 50 }), 5);
  assert.equal(M.generatedHouses({ population: 10 }, {}), 0);          // no percentage anywhere: none
  assert.equal(M.generatedHouses({ population: 5, housing_pct: 50 }, {}), 3);   // 2.5 rounds up, like the builder
});

test("generated houses are drawn first, can be moved but not deleted, and a lower pct drops stale positions", () => {
  const { own, r } = spatial();
  const lay = M.layoutOf(own, r, null);
  assert.equal(lay.houses.filter((h) => h.kind === "ghouse").length, 10 + 0);
  assert.equal(lay.houses.filter((h) => h.kind === "house").length, 3);       // the {count: 3} of the fixture
  M.moveItem(own, r, "ghouse", 0, 4, 1, 1);
  assert.deepEqual(own.cities[0].house_positions, [{ index: 4, at: [1, 1] }]);
  assert.equal(M.layoutOf(own, r, null).houses.find((h) => h.kind === "ghouse" && h.i === 4).moved, true);
  const res = M.deleteItem(own, r, "ghouse", 0, 4);
  assert.equal(res.ok, false);
  assert.equal(own.cities[0].house_positions.length, 1);
  assert.equal(M.setHousingPct(own, r, 0, 30), 1);                             // 3 houses left: index 4 is gone
  assert.equal("house_positions" in own.cities[0], false);
  M.moveItem(own, r, "ghouse", 0, 1, 0.5, 0.5);
  M.resetHouse(own, r, 0, 1);
  assert.equal("house_positions" in own.cities[0], false);
});

test("a resource point is placed outside the cities and branches off the nearest road", () => {
  const { own, r } = spatial();
  let res = M.addNode(own, r, "wood", 1, 1);                                     // inside city A
  assert.equal(res.ok, false);
  assert.match(res.error, /outside the cities/);
  res = M.addNode(own, r, "wood", 12, 8);
  assert.ok(res.ok);
  const n = own.nodes[res.oi];
  assert.deepEqual(n.at, [12, 8]);
  assert.deepEqual(n.branch.road, ["A", "B"]);
  assert.ok(n.branch.t > 0 && n.branch.t < 1);
  const lay = M.layoutOf(own, r, null);
  assert.equal(lay.overNodes.length, 1);
  assert.ok(Math.abs(lay.overNodes[0].branch.jy - n.branch.t * 40) < 1e-6);   // the junction is on the road A-B (x = 0.6 t... )
  const noRoads = { own: { extends: "x" }, r: { ...M.clone(r), roads: [], events: [] } };
  assert.match(M.addNode(noRoads.own, noRoads.r, "wood", 12, 8).error, /Draw a road first/);
});

test("moving a resource point keeps it out of cities and its branch follows; the junction slides to another road", () => {
  const { own, r } = spatial();
  const { oi } = M.addNode(own, r, "food", 12, 8);
  M.moveNode(own, r, oi, 0.5, 0.5);                                               // dragged into city A: stops at its edge
  const lay = M.layoutOf(own, r, null);
  assert.ok(Math.hypot(lay.overNodes[0].x, lay.overNodes[0].y) > 4);
  M.moveNode(own, r, oi, 45, 30);
  assert.notDeepEqual(own.nodes[oi].branch.road, ["A", "B"]);                    // now nearer the other road
  M.moveJunction(own, r, oi, 5, 20);
  assert.deepEqual(own.nodes[oi].branch.road, ["A", "B"]);
  M.deleteNode(own, r, oi);
  assert.equal(own.nodes.length, 0);
});

test("the eraser hits only the kinds that are ticked, only inside its circle, and spares generated houses", () => {
  const { own, r } = spatial();
  M.addNode(own, r, "food", 12, 8);
  M.addNode(own, r, "wood", 30, 20);
  M.addItem(own, r, 0, "workplace", { task: "guard", wage: 1 }, [1, 0]);
  const lay = M.layoutOf(own, r, null);
  const at = lay.overNodes[0];
  const circle = { x: at.x, y: at.y, r: 0.5 };
  let hit = M.eraseTargets(lay, circle, { ...M.DEFAULT_ERASE });
  assert.deepEqual(hit.targets.map((t) => t.key), ["node:0"]);                    // not the other node, not anything else
  assert.deepEqual(M.eraseTargets(lay, circle, { ...M.DEFAULT_ERASE, resources: false }).targets, []);
  hit = M.eraseTargets(lay, { x: 0, y: 0, r: 5 }, { ...M.DEFAULT_ERASE });       // all of city A
  const kinds = new Set(hit.targets.map((t) => t.kind));
  assert.ok(kinds.has("workplace") && kinds.has("house") && !kinds.has("city") && !kinds.has("road"));
  assert.ok(hit.skipped.length > 0 && hit.skipped.every((k) => k.startsWith("ghouse:")));
  assert.equal(M.eraseTargets(lay, { x: 5.5, y: 0, r: 0.4 }, { ...M.DEFAULT_ERASE }).targets.length, 0);   // just outside the circle
  const road = M.eraseTargets(lay, { x: 15, y: 20, r: 1 }, { ...M.DEFAULT_ERASE, roads: true });
  assert.deepEqual(road.targets.map((t) => t.key), ["road:0"]);                   // the line A-B passes through the circle
  const city = M.eraseTargets(lay, { x: 0, y: 0, r: 1 }, { ...M.DEFAULT_ERASE, cities: true });
  assert.ok(city.targets.some((t) => t.key === "city:0"));
});

test("applying an erase removes the right items whatever their order, and a city takes its roads with it", () => {
  const { own, r } = spatial();
  M.addNode(own, r, "food", 12, 8);
  M.addNode(own, r, "wood", 30, 20);
  M.addItem(own, r, 0, "workplace", { task: "a", wage: 1 }, [1, 0]);
  M.addItem(own, r, 0, "workplace", { task: "b", wage: 1 }, [-1, 0]);
  M.addItem(own, r, 0, "workplace", { task: "c", wage: 1 }, [0, 1]);
  const targets = [{ kind: "workplace", ci: 0, i: 0 }, { kind: "workplace", ci: 0, i: 2 }, { kind: "node", oi: 0 }];
  const done = M.applyErase(own, r, targets);
  assert.deepEqual(own.cities[0].workplaces.map((w) => w.task), ["b"]);
  assert.deepEqual(own.nodes.map((n) => n.good), ["wood"]);
  assert.deepEqual(done, { workplace: 2, node: 1 });
  const cas = M.eraseCascade(own, r, [{ kind: "city", ci: 1 }]);
  assert.equal(cas.cities, 1);
  assert.ok(cas.roads >= 2 && cas.events >= 1);
  M.applyErase(own, r, [{ kind: "city", ci: 1 }]);
  assert.deepEqual(own.cities.map((c) => c.name), ["A", "C"]);
  assert.ok(own.roads.every((rd) => rd.a !== "B" && rd.b !== "B"));
  assert.equal(M.eraseCascade(own, r, [{ kind: "workplace", ci: 0, i: 0 }]).cities, 0);
});
