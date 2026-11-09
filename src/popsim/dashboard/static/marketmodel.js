// Market tab: the parts that need no DOM (REQ-1C.9), so they can be tested on their own.

/** A number typed by the user, or null when it is empty or not a number. */
export function parseNum(text) {
  const s = String(text ?? "").trim().replace(",", ".");
  if (s === "" || !/^-?\d*\.?\d+$/.test(s)) return null;
  return Number(s);
}

export function isCents(value, unit) {
  const c = value * unit;
  return Math.abs(c - Math.round(c)) < 1e-6;
}

/** Problems with an order ticket, as sentences, before anything is sent (empty list: fine to send).
 *  ``acct`` is the account state from the server (gold, goods, unit). */
export function ticketProblems(t, acct) {
  const out = [];
  const unit = acct.unit || 100;
  const qty = parseNum(t.qty);
  const price = parseNum(t.price);
  if (qty == null || !Number.isInteger(qty) || qty < 1) out.push("Quantity must be a whole number of at least 1.");
  if (price == null || price <= 0) out.push(t.type === "market" ? "Enter the worst price you accept." : "Enter a price above 0.");
  else if (!isCents(price, unit)) out.push(`Prices are in whole cents (1/${unit} gold).`);
  if (t.type === "limit" && String(t.expiry ?? "").trim() !== "") {
    const e = parseNum(t.expiry);
    if (e == null || e <= 0) out.push("Expiry must be a positive number of hours, or empty for none.");
  }
  if (out.length) return out;
  if (t.side === "buy" && qty * price > acct.gold + 1e-9) {
    out.push(`Not enough gold: this needs ${fmt(qty * price)}, the wallet has ${fmt(acct.gold)}.`);
  }
  if (t.side === "sell" && qty > (acct.goods?.[t.good] ?? 0)) {
    out.push(`Not enough ${t.good}: this needs ${qty}, the wallet has ${acct.goods?.[t.good] ?? 0}.`);
  }
  return out;
}

export function fmt(v, digits = 2) {
  if (v == null || v === "" || !isFinite(v)) return "–";
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

/** "Buy 5 food at 4 (limit)" */
export function orderLabel(o) {
  const verb = o.side === "buy" ? "Buy" : "Sell";
  return `${verb} ${o.qty} ${o.good} at ${fmt(o.price)}${o.type === "market" ? " worst (market)" : ""}`;
}

/** The status a person reads: queued orders say when they run. */
export function statusText(o) {
  const part = o.filled > 0 && o.left > 0 ? `, ${o.filled} of ${o.qty} filled` : "";
  switch (o.status) {
    case "queued": return "queued, runs at the next tick";
    case "resting": return `on the book${part}`;
    case "cancelling": return `cancel runs at the next tick${part}`;
    case "filled": return `filled${o.avg_price != null ? ` at about ${fmt(o.avg_price)}` : ""}`;
    case "cancelled": return o.filled > 0 ? `cancelled, ${o.filled} of ${o.qty} filled` : "cancelled";
    case "expired": return o.filled > 0 ? `expired, ${o.filled} of ${o.qty} filled` : "expired";
    case "rejected": return `rejected: ${o.note || "the market refused it"}`;
    default: return o.status;
  }
}

/** The price to suggest for a ticket: the side's far touch (buy at the best ask, sell at the best bid). */
export function suggestPrice(side, row) {
  if (!row) return null;
  const p = side === "buy" ? (row.ask ?? row.last) : (row.bid ?? row.last);
  return p ?? null;
}

/** Order book rows top to bottom: asks (highest first), then bids (best first). */
export function bookRows(book) {
  const asks = [...(book.asks || [])].reverse().map(([price, qty, own]) => ({ side: "ask", price, qty, own }));
  const bids = (book.bids || []).map(([price, qty, own]) => ({ side: "bid", price, qty, own }));
  const max = Math.max(1, ...asks.map((r) => r.qty), ...bids.map((r) => r.qty));
  return [...asks, ...bids].map((r) => ({ ...r, share: r.qty / max }));
}
