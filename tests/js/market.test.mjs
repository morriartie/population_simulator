// The Market tab mounted on a fake server (REQ-1C.9): the start screen, opening the account, the
// order ticket (checks, quote, queueing), top-up, cancelling, and the disconnected state.
import test from "node:test";
import assert from "node:assert/strict";
import { installDom, byTag, byClass, findAll, FakeNode } from "./dom_stub.mjs";

const env = installDom();
const ctx = new Proxy(() => {}, { get: (_t, k) => (k === "measureText" ? () => ({ width: 10 }) : () => {}), set: () => true });
const realCreate = globalThis.document.createElement;
globalThis.document.createElement = (t) => { const n = realCreate(t); if (t === "canvas") { n.getContext = () => ctx; n.getBoundingClientRect = () => ({ left: 0, top: 0, width: 300, height: 200 }); } return n; };
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.devicePixelRatio = 1;
globalThis.location = { hash: "#/market" };
globalThis.HashChangeEvent = class { constructor(t) { this.type = t; } };
globalThis.window.dispatchEvent = () => {};
const sources = [];
globalThis.EventSource = class {
  constructor(url) { this.url = url; this.l = {}; this.closed = false; sources.push(this); }
  addEventListener(t, fn) { this.l[t] = fn; }
  close() { this.closed = true; }
  emit(t, data) { this.l[t] && this.l[t]({ data: JSON.stringify(data) }); }
};
globalThis.prompt = () => null;

const { mount } = await import("../../src/popsim/dashboard/static/market.js");

const STATE = { status: "paused", day: 3, hour: 5, tick: 77, total_ticks: 500, total_days: 20, speed: 2, name: "demo-live", scenario: "demo", world: "w", market: "in-process" };
const ACCT = {
  open: true, unit: 100, gold: 100, goods: { food: 0, wood: 4 }, locked: { gold: 0, goods: {} }, value: { food: 0, wood: 8 },
  net_worth: 108, injected: { gold: 100, goods: {}, value: 100 }, profit: 8, realized: 0, taxes: 0,
  orders: [], finished: [], history: [], totals: {}, networth: [{ day: 3, net_worth: 108, profit: 8, injected: 100 }], fills: 0, actions: 1 };
const VIEW = {
  city: "A", good: "food", tick: 77, day: 3,
  goods: [{ good: "food", last: 2, bid: 1.9, ask: 2.2, spread: 0.3, volume: 10, change: 0.1, held: 0, locked: 0 },
    { good: "wood", last: 3, bid: null, ask: null, spread: null, volume: 0, change: null, held: 4, locked: 0 }],
  book: { good: "food", bids: [[1.9, 5, false]], asks: [[2.2, 3, false]] },
  cross: [{ city: "A", last: 2, bid: 1.9, ask: 2.2 }, { city: "B", last: 3, bid: null, ask: null }],
  series: [{ day: 1, last: 2, vwap: 2, volume: 5 }, { day: 2, last: 2.1, vwap: 2.05, volume: 6 }] };

function server({ session = STATE, acct = ACCT } = {}) {
  const s = { acct: { ...acct }, session: { ...session }, orders: [], topups: [] };
  env.net.calls.length = 0;
  sources.length = 0;
  env.net.respond = (url, body) => {
    const u = url.split("?")[0];
    if (u === "/api/session") return s.session.status === "none" ? { status: "none" } : s.session;
    if (u === "/api/scenarios") return [{ name: "demo", cities: ["A", "B"], days: 5, events: 0 }];
    if (u === "/api/runs") return [{ name: "old", days: 10, inspectable: true }, { name: "plain", days: 4, inspectable: false }];
    if (u === "/api/session/market") return { world: "w", cities: ["A", "B"], goods: ["food", "wood"], unit: 100, open: s.acct.open };
    if (u.startsWith("/api/session/market/")) return VIEW;
    if (u === "/api/session/player" && !body) return s.acct;
    if (u === "/api/session/player") { s.acct = { ...ACCT }; return s.acct; }
    if (u === "/api/session/player/quote") return { fill_qty: 1, rest_qty: 1, avg_price: 2.2, gross: 2.2, tax: 0, locked_gold: 4.4, wallet_after_lock: 95.6, estimate: true };
    if (u === "/api/session/player/orders") { const o = { id: 1, ...body, left: body.qty, filled: 0, status: "queued", city: body.city, age_ticks: 0, price: body.price, type: body.type }; s.orders.push(o); s.acct = { ...s.acct, orders: [o] }; return o; }
    if (u.startsWith("/api/session/player/orders/")) { const o = s.acct.orders[0]; s.acct = { ...s.acct, orders: [] }; return { ...o, status: "cancelled" }; }
    if (u === "/api/session/player/topup") { s.topups.push(body); return s.acct; }
    return {};
  };
  return s;
}
const flush = () => new Promise((r) => setImmediate(r));
const all = (root, pred) => findAll(root, pred);
const button = (root, text) => all(root, (n) => n.tagName === "BUTTON" && n.textContent === text)[0];
const field = (root, id) => all(root, (n) => n.attrs && n.attrs.id === id)[0];
const type = (el, value) => { el.value = value; el.fire("input"); };
async function opened(opts) {
  const s = server(opts);
  const main = new FakeNode("main");
  const cleanup = await mount(main);
  await flush(); await flush(); await flush();
  return { s, main, cleanup };
}

test("with no live run the tab offers to start one or to continue a saved run that has a snapshot", async () => {
  const s = server({ session: { status: "none" } });
  const main = new FakeNode("main");
  await mount(main);
  assert.match(main.textContent, /Start from a scenario/);
  const runs = all(main, (n) => n.tagName === "OPTION").map((o) => o.textContent);
  assert.ok(runs.some((r) => r.startsWith("old")) && !runs.some((r) => r.startsWith("plain")));
  all(main, (n) => n.attrs && n.attrs["aria-label"] === "Saved run")[0].value = "old";
  button(main, "Continue it").fire("click");
  await flush();
  const post = env.net.calls.find((c) => c.method === "POST" && c.url === "/api/session");
  assert.deepEqual(post.body, { resume: "old" });
});

test("an account that is not open yet shows the opening card, and opening it shows the market", async () => {
  const { s, main } = await opened({ acct: { open: false, unit: 100 } });
  assert.match(main.textContent, /Open your trading account/);
  const card = all(main, (n) => n.tagName === "H2" && n.textContent === "Open your trading account")[0].parentNode;
  const grid = all(main, (n) => n.tagName === "H2" && n.textContent === "Order ticket")[0].parentNode.parentNode;
  assert.equal(card.hidden, false);
  assert.equal(grid.hidden, true);
  button(main, "Open account").fire("click");
  await flush(); await flush(); await flush();
  assert.equal(card.hidden, true);
  assert.equal(grid.hidden, false);
  assert.match(main.textContent, /Order ticket/);
  assert.match(main.textContent, /Order book: food in A/);
});

test("the market shows goods, the book, the cities and the wallet", async () => {
  const { main } = await opened();
  const text = main.textContent;
  assert.match(text, /Same good in every city/);
  assert.match(text, /Net worth/);
  assert.match(text, /Profit \(net worth minus injected\)/);
  const rows = all(main, (n) => n.tagName === "TR" && n.attrs && n.attrs["data-good"]);
  assert.deepEqual(rows.map((r) => r.attrs["data-good"]), ["food", "wood"]);
  assert.match(main.textContent, /Day 3, 05:00/);
  assert.match(main.textContent, /orders you queue run when time moves/);
});

test("the ticket is blocked, with the reason, until it is valid; a valid one is queued and not matched", async () => {
  const { main } = await opened();
  const submit = all(main, (n) => n.tagName === "BUTTON" && n.textContent === "Queue order")[0];
  assert.ok(submit.disabled === true);
  type(field(main, "t-qty"), "500");
  type(field(main, "t-price"), "2");
  assert.match(main.textContent, /Not enough gold/);
  assert.ok(submit.disabled === true);
  type(field(main, "t-qty"), "2");
  assert.equal(all(main, (n) => n.tagName === "LI").length, 0);
  assert.equal(!!submit.disabled, false);
  await new Promise((r) => setTimeout(r, 300));
  await flush();
  assert.match(main.textContent, /Locks 4.4 gold when queued/);
  assert.match(main.textContent, /Estimate at today's book/);
  const form = all(main, (n) => n.tagName === "FORM")[0];
  form.fire("submit");
  await flush(); await flush(); await flush();
  const post = env.net.calls.find((c) => c.method === "POST" && c.url === "/api/session/player/orders");
  assert.deepEqual(post.body, { city: "A", good: "food", side: "buy", qty: 2, price: 2, type: "limit" });
  assert.match(main.textContent, /queued, runs at the next tick/);
});

test("a queued order can be cancelled from the orders list", async () => {
  const { s, main } = await opened({ acct: { ...ACCT, orders: [{ id: 7, city: "A", good: "food", side: "buy", qty: 2, left: 2, filled: 0, price: 2, type: "limit", status: "queued", age_ticks: 1 }] } });
  assert.match(main.textContent, /Buy 2 food at 2/);
  button(main, "Cancel").fire("click");
  await flush(); await flush();
  const del = env.net.calls.find((c) => c.method === "DELETE");
  assert.equal(del.url, "/api/session/player/orders/7");
});

test("a top-up sends the amount and refuses an empty one", async () => {
  const { s, main } = await opened();
  button(main, "Add gold").fire("click");
  await flush();
  assert.equal(s.topups.length, 0);
  type(field(main, "tu-gold"), "50");
  button(main, "Add gold").fire("click");
  await flush();
  assert.deepEqual(s.topups[0], { gold: 50 });
  all(main, (n) => n.attrs && n.attrs["aria-label"] === "Good to add")[0].value = "food";
  type(field(main, "tu-qty"), "5");
  button(main, "Add goods").fire("click");
  await flush();
  assert.deepEqual(s.topups[1], { goods: { food: 5 }, city: "A" });
});

test("when the stream drops the order ticket is disabled and says so", async () => {
  const { main } = await opened();
  type(field(main, "t-qty"), "1");
  type(field(main, "t-price"), "2");
  const submit = all(main, (n) => n.tagName === "BUTTON" && n.textContent === "Queue order")[0];
  assert.equal(!!submit.disabled, false);
  sources[0].onerror();
  assert.ok(submit.disabled === true);
  assert.match(main.textContent, /Disconnected from the live run/);
});

test("a finished run blocks orders and offers more days", async () => {
  const { main } = await opened({ session: { ...STATE, status: "finished" } });
  type(field(main, "t-qty"), "1");
  type(field(main, "t-price"), "2");
  const submit = all(main, (n) => n.tagName === "BUTTON" && n.textContent === "Queue order")[0];
  assert.ok(submit.disabled === true);
  assert.match(main.textContent, /The run is finished/);
});

test("leaving the tab closes the stream", async () => {
  const { cleanup } = await opened();
  cleanup();
  assert.ok(sources[0].closed);
});
