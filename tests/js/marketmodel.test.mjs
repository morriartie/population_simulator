// The Market tab's pure helpers (REQ-1C.9): ticket checks, labels, suggestions, book rows.
import test from "node:test";
import assert from "node:assert/strict";
import { parseNum, isCents, ticketProblems, orderLabel, statusText, suggestPrice, bookRows, fmt }
  from "../../src/popsim/dashboard/static/marketmodel.js";

const acct = { unit: 100, gold: 50, goods: { wood: 4, food: 0 } };
const t = (o) => ({ side: "buy", type: "limit", good: "food", qty: "2", price: "3", expiry: "", ...o });

test("numbers are read with a dot or a comma and nothing else", () => {
  assert.equal(parseNum("2.5"), 2.5);
  assert.equal(parseNum(" 2,5 "), 2.5);
  assert.equal(parseNum(""), null);
  assert.equal(parseNum("abc"), null);
  assert.equal(parseNum("1e3"), null);
});

test("prices are whole cents", () => {
  assert.ok(isCents(1.25, 100));
  assert.ok(isCents(0.1 + 0.2, 100));
  assert.ok(!isCents(1.005, 100));
});

test("a good ticket has no problems", () => {
  assert.deepEqual(ticketProblems(t(), acct), []);
  assert.deepEqual(ticketProblems(t({ side: "sell", good: "wood", qty: "4" }), acct), []);
});

test("quantity, price and expiry are checked before anything is sent", () => {
  assert.match(ticketProblems(t({ qty: "0" }), acct)[0], /whole number/);
  assert.match(ticketProblems(t({ qty: "1.5" }), acct)[0], /whole number/);
  assert.match(ticketProblems(t({ qty: "" }), acct)[0], /whole number/);
  assert.match(ticketProblems(t({ price: "" }), acct)[0], /price/);
  assert.match(ticketProblems(t({ price: "0" }), acct)[0], /price/);
  assert.match(ticketProblems(t({ price: "1.005" }), acct)[0], /whole cents/);
  assert.match(ticketProblems(t({ expiry: "-1" }), acct)[0], /Expiry/);
  assert.match(ticketProblems(t({ type: "market", price: "" }), acct)[0], /worst price/);
  assert.deepEqual(ticketProblems(t({ type: "market", expiry: "x" }), acct), []); // no expiry on a market order
});

test("funds are checked against the wallet", () => {
  assert.match(ticketProblems(t({ qty: "20", price: "3" }), acct)[0], /Not enough gold/);
  assert.match(ticketProblems(t({ side: "sell", good: "wood", qty: "5" }), acct)[0], /Not enough wood/);
  assert.match(ticketProblems(t({ side: "sell", good: "food", qty: "1" }), acct)[0], /Not enough food/);
});

test("orders and their statuses read as sentences", () => {
  const o = { side: "buy", qty: 5, good: "food", price: 4, type: "limit", status: "queued", filled: 0, left: 5 };
  assert.equal(orderLabel(o), "Buy 5 food at 4");
  assert.equal(orderLabel({ ...o, side: "sell", type: "market" }), "Sell 5 food at 4 worst (market)");
  assert.match(statusText(o), /queued, runs at the next tick/);
  assert.match(statusText({ ...o, status: "resting", filled: 2, left: 3 }), /on the book, 2 of 5 filled/);
  assert.match(statusText({ ...o, status: "cancelling" }), /next tick/);
  assert.match(statusText({ ...o, status: "rejected", note: "bad_price" }), /rejected: bad_price/);
  assert.match(statusText({ ...o, status: "cancelled", filled: 2 }), /cancelled, 2 of 5 filled/);
});

test("the suggested price is the far touch of the side", () => {
  const row = { bid: 3, ask: 5, last: 4 };
  assert.equal(suggestPrice("buy", row), 5);
  assert.equal(suggestPrice("sell", row), 3);
  assert.equal(suggestPrice("buy", { bid: null, ask: null, last: 4 }), 4);
  assert.equal(suggestPrice("buy", null), null);
});

test("the book is listed asks first (highest at the top) then bids, with depth shares", () => {
  const rows = bookRows({ asks: [[5, 2, false], [6, 4, true]], bids: [[3, 8, false]] });
  assert.deepEqual(rows.map((r) => [r.side, r.price]), [["ask", 6], ["ask", 5], ["bid", 3]]);
  assert.equal(rows[2].share, 1);
  assert.equal(rows[0].own, true);
  assert.deepEqual(bookRows({}), []);
});

test("numbers are formatted and gaps are dashes", () => {
  assert.equal(fmt(null), "–");
  assert.equal(fmt(2.5), (2.5).toLocaleString(undefined, { maximumFractionDigits: 2 }));
});
