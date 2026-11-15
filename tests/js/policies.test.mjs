// The Policies panel (REQ-5A.12): a card per policy, its state, the live switch, what its rules decided by day,
// the sites on the map by tag, and what each open task is waiting for.
import test from "node:test";
import assert from "node:assert/strict";
import { installDom, byTag, byClass } from "./dom_stub.mjs";

const env = installDom();
const ctx = new Proxy(() => {}, { get: (_t, k) => (k === "measureText" ? () => ({ width: 10 }) : () => {}), set: () => true });
const realCreate = globalThis.document.createElement;
globalThis.document.createElement = (t) => { const n = realCreate(t); if (t === "canvas") { n.getContext = () => ctx; n.getBoundingClientRect = () => ({ left: 0, top: 0, width: 300, height: 200 }); } return n; };
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.devicePixelRatio = 1;

const { PoliciesPanel, sitesAt } = await import("../../src/popsim/dashboard/static/policies.js");

const grid = { x0: -10, y0: -10, cell: 2, nx: 4, ny: 4, min_fertility: 0.45, values: [[0.1, 0.5, 0.7, 0.9], [0.2, 0.6, 0.8, 0.3], [0.5, 0.5, 0.5, 0.5], [0.9, 0.9, 0.1, 0.1]] };
const policy = (enabled = true, extra = {}) => ({ name: "farming", title: "Farming", description: "Plants fields.", enabled, rules: ["plant_food"],
  threshold: 0.1, settings: { "rule plant_food": "need:food: share above 10%", "plant_food max active": 2 }, fertility: grid, ...extra });

const evs = [
  { day: 2, policy: "farming", kind: "planted", city: "A", site: 9, process: "field", x: -2, y: 2, fertility: 0.7, tags: ["planting_spot", "waiting_seeds"] },
  { day: 4, policy: "farming", kind: "stage", city: "A", site: 9, process: "field", tags: ["growing", "planting_spot"], added: "growing", ready_day: 8 },
  { day: 8, policy: "farming", kind: "matured", city: "A", site: 9, node: 9, process: "field", x: -2, y: 2, good: "food", max_amount: 350 },
  { day: 3, policy: "farming", kind: "planted", city: "B", site: 10, process: "field", x: 2, y: 2, fertility: 0.6, tags: ["planting_spot", "waiting_seeds"] },
  { day: 6, policy: "farming", kind: "cancelled", city: "B", site: 10, process: "field", why: "timeout", x: 2, y: 2 },
  { day: 3, policy: null, kind: "office_opened", city: "A", site: 5, task: "estate_finance", x: 0, y: 0, rule: "finance_office" },
];

function rv(over = {}) {
  const data = {
    config: { cities: [{ name: "A" }, { name: "B" }], time: { days: 10 } }, events: [],
    world: { cities: [{ name: "A", x: -6, y: -6, radius: 1 }, { name: "B", x: 4, y: 4, radius: 1 }], roads: [{ a: "A", b: "B" }],
      nodes: [{ id: 3, overworld: true, x: 0, y: 0 }] },
    policies: [policy()],
    policy_daily: [
      { day: 2, policy: "farming", rule: "plant_food", city: "A", residents: 20, urgent: 8, share: 0.4, threshold: 0.1, triggered: true, held_days: 1, action: "created", plots_growing: 1 },
      { day: 2, policy: "farming", rule: "plant_food", city: "B", residents: 20, urgent: 1, share: 0.05, threshold: 0.1, triggered: false, held_days: 0, action: "idle", plots_growing: 0 },
      { day: 5, policy: "farming", rule: "plant_food", city: "A", residents: 20, urgent: 6, share: 0.3, threshold: 0.1, triggered: true, held_days: 4, action: "waiting: fields growing", plots_growing: 1 },
    ],
    policy_events: evs,
    sites: [{ id: 11, process: "field", city: "A", policy: "farming", tags: ["planting_spot", "waiting_seeds"],
      requirements: { waiting_seeds: [{ kind: "items", label: "seed 0/5", met: false }, { kind: "labor", label: "labor 0.0/2 h", met: false }] },
      blockers: { waiting_seeds: ["estate finance office not staffed"] } }],
    ...over.data,
  };
  return { live: false, now: null, data, cities: () => ["A", "B"], maxDay: () => 10, pick() {}, ...over, data };
}

const text = (n) => n.textContent;

test("the panel is hidden when the run has no policy activity and none is on", () => {
  const p = new PoliciesPanel(rv({ data: { policies: [policy(false)], policy_daily: [], policy_events: [], sites: [] } }));
  p.build();
  assert.equal(p.el.hidden, true);
  const q = new PoliciesPanel(rv({ data: { policies: [] } }));
  q.build();
  assert.equal(q.el.hidden, true);
});

test("sites are rebuilt from the events by day", () => {
  assert.deepEqual(sitesAt(evs, "farming", 1), []);
  let s = sitesAt(evs, "farming", 3);
  assert.deepEqual(s.map((x) => [x.id, x.tags.includes("waiting_seeds")]), [[9, true], [10, true]]);
  s = sitesAt(evs, "farming", 5);
  assert.equal(s.find((x) => x.id === 9).ready, 8);
  assert.deepEqual(sitesAt(evs, "farming", 7).map((x) => x.id), [9]);  // site 10 was cancelled on day 6
  assert.equal(sitesAt(evs, "farming", 9)[0].grown, "food");
});

test("a card shows the policy, its rules and what they decided on the chosen day", () => {
  const p = new PoliciesPanel(rv());
  p.build();
  assert.match(text(p.el), /Farming/);
  assert.match(text(byClass(p.el, "badge")[0]), /^On$/);
  p.setDay(2);
  assert.match(text(p.el), /plant_food/);
  assert.match(text(p.el), /40% \(8\/20\)/);
  assert.match(text(p.el), /created/);
  p.setDay(5);
  assert.match(text(p.el), /waiting: fields growing/);
});

test("the map counts sites by stage and day", () => {
  const p = new PoliciesPanel(rv());
  p.build();
  p.setDay(3);
  assert.match(text(p.el), /2 waiting for seeds or labor, 0 growing, 0 grown/);
  p.setDay(5);
  assert.match(text(p.el), /1 waiting for seeds or labor, 1 growing, 0 grown/);
  p.setDay(9);
  assert.match(text(p.el), /0 waiting for seeds or labor, 0 growing, 1 grown/);
  p.setDay(0);
  assert.match(text(p.el), /0 waiting for seeds or labor, 0 growing, 0 grown/);
});

test("open sites list what they wait for and what blocks them; offices are listed", () => {
  const p = new PoliciesPanel(rv());
  p.build();
  p.setDay(4);
  assert.match(text(p.el), /seed 0\/5; labor 0\.0\/2 h/);
  assert.match(text(p.el), /estate finance office not staffed/);
  assert.match(text(p.el), /opened a estate finance office/);
});

test("a finished run has no switch; a live run switches the policy through the control route", async () => {
  const done = new PoliciesPanel(rv());
  done.build();
  assert.equal(byTag(done.el, "button").length, 0);

  const live = new PoliciesPanel(rv({ live: true, now: { day: 5, policies: { farming: true }, sites: [] } }));
  live.build();
  env.net.calls.length = 0;
  const btn = byTag(live.el, "button")[0];
  assert.equal(text(btn), "Switch off");
  btn.fire("click");
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(env.net.calls[0].body, { action: "policy", value: { name: "farming", enabled: false } });
  assert.equal(env.net.calls[0].url, "/api/session/control");
  assert.match(text(byClass(live.el, "badge")[0]), /^Off$/);
  assert.equal(text(byTag(live.el, "button")[0]), "Switch on");
});

test("a policy without a fertility field still gets a card with its rules and chart", () => {
  const other = { name: "forestry", title: "Forestry", description: "d", enabled: true, rules: ["plant_trees"], threshold: 0.1, settings: { a: 1 } };
  const p = new PoliciesPanel(rv({ data: { policies: [other] } }));
  p.build();
  assert.match(text(p.el), /Forestry/);
  assert.match(text(p.el), /a\s*1/);
});
