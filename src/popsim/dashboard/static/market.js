// Market: trade by hand on the live world as the player (REQ-1C.9).
import { api, h, toast, refreshLiveDot, tabs } from "./util.js";
import { LineChart } from "./charts.js";
import {
  parseNum, ticketProblems, fmt, orderLabel, statusText, suggestPrice, bookRows,
} from "./marketmodel.js";

const SPEEDS = [0.25, 0.5, 1, 2, 4, 8, 16, 32, 64];

export async function mount(main) {
  const session = await api("/api/session");
  if (session.status === "none") return mountStart(main);
  return mountLive(main, session);
}

// -- no live run: start one, or resume a saved run ---------------------------------------------------
async function mountStart(main) {
  const scenarios = (await api("/api/scenarios")).filter((s) => !s.error);
  const runs = (await api("/api/runs")).filter((r) => r.inspectable);
  main.append(h("h1", {}, "Market"),
    h("p", { class: "lede" }, "Buy and sell by hand on a world while its citizens trade. You need a live run first: start one from a scenario, or continue a saved run."));
  let cleanup = null;
  async function go(body) {
    try {
      await api("/api/session", { method: "POST", body });
      main.replaceChildren();
      cleanup = await mountLive(main, await api("/api/session"));
      refreshLiveDot();
    } catch (e) { toast(e.message, "error"); }
  }
  const sel = h("select", { "aria-label": "Scenario" }, scenarios.map((s) => h("option", { value: s.name }, s.name)));
  const rsel = h("select", { "aria-label": "Saved run" }, runs.map((r) => h("option", { value: r.name }, `${r.name} (${r.days} days)`)));
  main.append(
    h("div", { class: "start-form" },
      h("label", { class: "field" }, "Start from a scenario", sel),
      h("button", { type: "button", class: "primary", disabled: !scenarios.length, onclick: () => go({ scenario: sel.value }) }, "Start live run")),
    h("div", { class: "start-form" },
      h("label", { class: "field" }, "Or continue a saved run", rsel),
      h("button", { type: "button", disabled: !runs.length, onclick: () => go({ resume: rsel.value }) }, "Continue it"),
      !runs.length && h("p", { class: "muted", style: "flex-basis:100%;margin:0" }, "No saved run has a snapshot yet. Save one from Play.")));
  return () => cleanup && cleanup();
}

// -- the live market -----------------------------------------------------------------------------------
function mountLive(main, initial) {
  let state = initial;
  let info = null;      // world: cities, goods, unit
  let acct = { open: false, unit: 100 };
  let view = null;      // market view of the chosen city and good
  let city = null;
  let good = null;
  let connected = true;
  let busy = false;
  let again = false;
  let quoteTimer = null;
  let ready = false;  // the ticket exists
  const ticket = { side: "buy", type: "limit", qty: "", price: "", expiry: "", priceTouched: false };

  // transport
  const clock = h("span", { class: "clock" });
  const status = h("span", { class: "muted" });
  const playBtn = h("button", { type: "button", class: "primary", onclick: () => control(state.status === "playing" ? "pause" : "play") });
  const hourBtn = h("button", { type: "button", onclick: () => control("step_hour") }, "Step 1 hour");
  const dayBtn = h("button", { type: "button", onclick: () => control("step_day") }, "Step 1 day");
  const speed = h("select", { "aria-label": "Speed in days per second", onchange: (e) => control("speed", +e.target.value) },
    SPEEDS.map((v) => h("option", { value: v, selected: v === state.speed }, `${v} days/s`)));
  const extendDays = h("input", { type: "number", min: 1, step: 1, value: 10, "aria-label": "Days to add" });
  const extendBox = h("span", { class: "row", hidden: true }, extendDays,
    h("button", { type: "button", onclick: () => control("extend", +extendDays.value || 10) }, "Add days"));
  const live = h("span", { class: "muted" });

  const lede = h("p", { class: "lede" });
  const bookTitle = h("h2", {}, "Order book");
  const body = h("div", { class: "market" });
  main.append(
    h("div", { class: "run-head" }, h("div", {}, h("h1", {}, "Market"),
      lede)),
    h("div", { class: "transport", role: "group", "aria-label": "Playback" },
      clock, playBtn, hourBtn, dayBtn, h("label", { class: "row" }, "Speed", speed), extendBox,
      h("div", { class: "spacer" }), status, live),
    body);

  function renderState() {
    clock.textContent = `Day ${state.day}, ${String(Math.floor(state.hour)).padStart(2, "0")}:00`;
    playBtn.textContent = state.status === "playing" ? "Pause" : "Play";
    const stuck = state.status === "finished" || state.status === "error";
    playBtn.disabled = hourBtn.disabled = dayBtn.disabled = stuck;
    extendBox.hidden = state.status !== "finished";
    status.textContent = state.status === "error" ? `Stopped by an error: ${state.error}`
      : state.status === "finished" ? "The run is finished. Add days to keep trading."
      : state.status === "playing" ? "Playing" : "Paused: orders you queue run when time moves.";
    status.className = state.status === "error" ? "bad" : "muted";
    live.textContent = connected ? "" : "Disconnected from the live run. Reload to reconnect.";
    live.className = connected ? "muted" : "bad";
    lede.textContent = `World ${state.world}. You trade as the player, outside the world: any city, no travel. An order you send is queued and runs at the next tick, mixed in with the citizens' orders.`;
    if (typeof syncSubmit === "function" && ready) syncSubmit();
  }

  async function control(action, value) {
    try { state = await api("/api/session/control", { method: "POST", body: { action, value } }); renderState(); refresh(); }
    catch (e) { toast(e.message, "error"); }
  }

  // account panel and market panels are built once; refresh() fills them
  const openCard = h("div", { class: "card", hidden: true });
  const cityPick = h("select", { "aria-label": "City", onchange: () => { city = cityPick.value; ticket.priceTouched = false; refresh(); } });
  const goodsHost = h("div");
  const bookHost = h("div");
  const chartHost = h("div");
  const crossHost = h("div");
  const walletHost = h("div");
  const topupHost = h("div");
  const ordersHost = h("div");
  const historyHost = h("div");
  const perfHost = h("div");
  const ticketHost = h("form", { class: "ticket", onsubmit: (e) => { e.preventDefault(); submit(); } });
  const quoteOut = h("div", { class: "quote", "aria-live": "polite" });
  const problems = h("ul", { class: "problems" });
  const submitBtn = h("button", { type: "submit", class: "primary" }, "Queue order");

  let chart = null;
  let perf = null;

  const left = h("div", { class: "market-main" },
    h("div", { class: "row" }, h("label", { class: "field" }, "City", cityPick)),
    h("h2", {}, "Goods"), goodsHost,
    h("div", { class: "market-pair" },
      h("div", {}, bookTitle, bookHost),
      h("div", {}, h("h2", {}, "Price history"), chartHost)),
    h("h2", {}, "Same good in every city"), crossHost);
  const right = h("div", { class: "market-side" },
    h("h2", {}, "Order ticket"), ticketHost, quoteOut, problems,
    h("h2", {}, "Wallet"), walletHost, topupHost);
  const lower = tabs([["orders", "Orders", ordersHost], ["history", "History", historyHost], ["perf", "Performance", perfHost]]);
  const grid = h("div", { class: "market-grid", hidden: true }, left, right);
  body.append(openCard, grid, h("div", { hidden: false, id: "market-lower" }, lower));
  lower.hidden = true;
  ready = true;
  renderState();

  function buildTicket() {
    const goods = info.goods;
    const side = h("div", { class: "seg", role: "group", "aria-label": "Side" },
      ["buy", "sell"].map((s) => h("button", { type: "button", "aria-pressed": String(ticket.side === s), onclick: () => {
        ticket.side = s; ticket.priceTouched = false; buildTicket(); applySuggestion(); quote(); } }, s === "buy" ? "Buy" : "Sell")));
    const goodSel = h("select", { "aria-label": "Good", onchange: (e) => { good = e.target.value; ticket.priceTouched = false; refresh(); } },
      goods.map((g) => h("option", { value: g, selected: g === good }, g)));
    const type = h("select", { "aria-label": "Order type", onchange: (e) => { ticket.type = e.target.value; buildTicket(); quote(); } },
      [["limit", "Limit"], ["market", "Market (worst price)"]].map(([v, t]) => h("option", { value: v, selected: v === ticket.type }, t)));
    const qty = h("input", { id: "t-qty", inputmode: "numeric", autocomplete: "off", value: ticket.qty, oninput: (e) => { ticket.qty = e.target.value; quote(); } });
    const price = h("input", { id: "t-price", inputmode: "decimal", autocomplete: "off", value: ticket.price, oninput: (e) => { ticket.price = e.target.value; ticket.priceTouched = true; quote(); } });
    const expiry = h("input", { id: "t-expiry", inputmode: "decimal", autocomplete: "off", placeholder: "none", value: ticket.expiry, oninput: (e) => { ticket.expiry = e.target.value; quote(); } });
    ticketHost.replaceChildren(side,
      h("label", { class: "field" }, "Good", goodSel),
      h("label", { class: "field" }, "Type", type),
      h("label", { class: "field" }, "Quantity", qty),
      h("label", { class: "field" }, ticket.type === "market" ? "Worst price you accept" : "Limit price", price),
      ticket.type === "limit" && h("label", { class: "field" }, "Expires after (hours)", expiry),
      submitBtn);
    syncSubmit();
  }

  function applySuggestion() {
    if (ticket.priceTouched || !view) return;
    const row = view.goods.find((g) => g.good === good);
    const p = suggestPrice(ticket.side, row);
    ticket.price = p == null ? "" : String(p);
    const el = document.getElementById("t-price");
    if (el) el.value = ticket.price;
  }

  function currentProblems() {
    return ticketProblems({ ...ticket, good }, acct);
  }
  function syncSubmit() {
    const bad = currentProblems();
    problems.replaceChildren(...(ticket.qty !== "" || ticket.price !== "" ? bad.map((b) => h("li", {}, b)) : []));
    submitBtn.disabled = !connected || !acct.open || state.status === "finished" || state.status === "error" || bad.length > 0;
  }

  function quote() {
    syncSubmit();
    clearTimeout(quoteTimer);
    quoteTimer = setTimeout(async () => {
      const qty = parseNum(ticket.qty), price = parseNum(ticket.price);
      if (!acct.open || qty == null || price == null || currentProblems().length) { quoteOut.replaceChildren(); return; }
      try {
        const q = await api("/api/session/player/quote", { method: "POST", body: { city, good, side: ticket.side, qty, price, type: ticket.type } });
        const tax = ticket.side === "buy" ? "sales tax" : "income tax";
        quoteOut.replaceChildren(
          h("p", {}, ticket.side === "buy" ? `Locks ${fmt(q.locked_gold)} gold when queued.` : `Locks ${qty} ${good} when queued.`),
          h("p", { class: "muted" },
            q.fill_qty ? `Estimate at today's book: ${q.fill_qty} would fill at about ${fmt(q.avg_price)} (${fmt(q.gross)} gold), ${tax} ${fmt(q.tax)}.`
              : "Estimate at today's book: nothing would fill right away.",
            q.rest_qty && ticket.type === "limit" ? ` ${q.rest_qty} would rest on the book.` : "",
            ticket.type === "market" && q.rest_qty ? ` The unfilled ${q.rest_qty} would be cancelled.` : "",
            " The book can change before the order runs."),
          h("p", { class: "muted" }, `Wallet after locking: ${fmt(q.wallet_after_lock)} gold.`));
      } catch (e) { quoteOut.replaceChildren(h("p", { class: "bad" }, e.message)); }
    }, 250);
  }

  async function submit() {
    const qty = parseNum(ticket.qty), price = parseNum(ticket.price);
    const body = { city, good, side: ticket.side, qty, price, type: ticket.type };
    const e = parseNum(ticket.expiry);
    if (ticket.type === "limit" && e != null) body.expires_hours = e;
    submitBtn.disabled = true;
    try {
      const o = await api("/api/session/player/orders", { method: "POST", body });
      toast(`Queued: ${orderLabel(o)}. It runs at the next tick.`);
      ticket.qty = "";
      buildTicket();
      quoteOut.replaceChildren();
      await refresh();
    } catch (err) { toast(err.message, "error"); }
    syncSubmit();
  }

  // -- renderers --------------------------------------------------------------------------------------
  const th = (...c) => h("tr", {}, c.map((x) => h("th", { scope: "col" }, x)));

  function renderGoods() {
    goodsHost.replaceChildren(h("table", { class: "mkt-table" },
      h("thead", {}, th("Good", "Last", "Best bid", "Best ask", "Spread", "Volume (last day)", "Change", acct.open ? "You hold" : "")),
      h("tbody", {}, view.goods.map((g) => h("tr", { class: g.good === good ? "sel" : "", "data-good": g.good },
        h("td", {}, h("button", { type: "button", class: "linklike", "aria-pressed": String(g.good === good), onclick: () => { good = g.good; ticket.priceTouched = false; refresh(); } }, g.good)),
        h("td", {}, fmt(g.last)), h("td", {}, fmt(g.bid)), h("td", {}, fmt(g.ask)), h("td", {}, fmt(g.spread)),
        h("td", {}, fmt(g.volume)),
        h("td", { class: g.change > 0 ? "good" : g.change < 0 ? "bad" : "" }, g.change == null ? "–" : (g.change > 0 ? "+" : "") + fmt(g.change)),
        h("td", {}, acct.open ? `${g.held}${g.locked ? ` (${g.locked} locked)` : ""}` : ""))))));
  }

  function renderBook() {
    bookTitle.textContent = `Order book: ${good} in ${city}`;
    const rows = bookRows(view.book);
    bookHost.replaceChildren(h("table", { class: "mkt-table book" },
      h("thead", {}, th("Side", "Price", "Quantity", "")),
      h("tbody", {}, rows.length ? rows.map((r) => h("tr", { class: r.side },
        h("td", {}, r.side === "ask" ? "Sell" : "Buy"), h("td", {}, fmt(r.price)), h("td", {}, fmt(r.qty)),
        h("td", {}, h("span", { class: "depth", style: `width:${Math.round(r.share * 100)}%` }), r.own ? h("span", { class: "own", title: "Your order is at this price" }, " ● yours") : null)))
        : h("tr", {}, h("td", { colspan: 4, class: "muted" }, "The book is empty.")))));
  }

  function renderChart() {
    if (!chart) chart = new LineChart(chartHost, { label: "Price history", unit: "", yMin: null });
    const pts = (k) => view.series.filter((r) => r[k] != null).map((r) => [r.day, r[k]]);
    chart.set([{ name: "last price", color: getComputedStyle(document.documentElement).getPropertyValue("--verdigris").trim() || "#2F7D6D", points: pts("last") },
      { name: "day average", color: getComputedStyle(document.documentElement).getPropertyValue("--ochre").trim() || "#C8902E", points: pts("vwap"), width: 1 }]);
  }

  function renderCross() {
    crossHost.replaceChildren(h("table", { class: "mkt-table" },
      h("thead", {}, th("City", `Last ${good}`, "Best bid", "Best ask", "")),
      h("tbody", {}, view.cross.map((c) => h("tr", { class: c.city === city ? "sel" : "" },
        h("td", {}, h("button", { type: "button", class: "linklike", onclick: () => { city = c.city; cityPick.value = c.city; ticket.priceTouched = false; refresh(); } }, c.city)),
        h("td", {}, fmt(c.last)), h("td", {}, fmt(c.bid)), h("td", {}, fmt(c.ask)), h("td", {}, c.city === city ? "viewing" : ""))))));
  }

  function renderWallet() {
    if (!acct.open) { walletHost.replaceChildren(); topupHost.replaceChildren(); return; }
    walletHost.replaceChildren(h("dl", { class: "kv" },
      h("dt", {}, "Gold"), h("dd", {}, `${fmt(acct.gold)}${acct.locked.gold ? ` (${fmt(acct.locked.gold)} locked in orders)` : ""}`),
      ...Object.keys(acct.goods).flatMap((g) => [h("dt", {}, g), h("dd", {}, `${acct.goods[g]}${acct.locked.goods[g] ? ` (${acct.locked.goods[g]} locked)` : ""} worth ${fmt(acct.value[g])}`)]),
      h("dt", {}, "Net worth"), h("dd", {}, fmt(acct.net_worth)),
      h("dt", {}, "Injected so far"), h("dd", {}, `${fmt(acct.injected.value)} gold-equivalent`),
      h("dt", {}, "Profit (net worth minus injected)"), h("dd", { class: acct.profit >= 0 ? "good" : "bad" }, fmt(acct.profit)),
      h("dt", {}, "Realized on sales"), h("dd", {}, fmt(acct.realized)),
      h("dt", {}, "Taxes paid"), h("dd", {}, fmt(acct.taxes))));
    if (!topupHost.children.length) buildTopup();
  }

  function buildTopup() {
    const gold = h("input", { id: "tu-gold", inputmode: "decimal", autocomplete: "off", "aria-label": "Gold to add" });
    const g = h("select", { "aria-label": "Good to add" }, info.goods.map((x) => h("option", { value: x }, x)));
    const q = h("input", { id: "tu-qty", inputmode: "numeric", autocomplete: "off", "aria-label": "Quantity to add" });
    topupHost.replaceChildren(h("h2", {}, "Top up"),
      h("p", { class: "muted" }, "Adds money or goods that did not exist before, and is counted as an injection. Profit is shown net of it."),
      h("div", { class: "row" }, h("label", { class: "field" }, "Gold", gold),
        h("button", { type: "button", onclick: () => topup({ gold: parseNum(gold.value) }, gold) }, "Add gold")),
      h("div", { class: "row" }, h("label", { class: "field" }, "Good", g), h("label", { class: "field" }, "Quantity", q),
        h("button", { type: "button", onclick: () => topup({ goods: { [g.value]: parseNum(q.value) }, city }, q) }, "Add goods")));
  }
  async function topup(body, input) {
    const amount = body.gold ?? Object.values(body.goods || {})[0];
    if (amount == null || amount <= 0) { toast("Enter an amount above 0.", "error"); return; }
    try {
      await api("/api/session/player/topup", { method: "POST", body });
      input.value = "";
      toast("Added to the account.");
      await refresh();
    } catch (e) { toast(e.message, "error"); }
  }

  async function cancel(o) {
    try { await api(`/api/session/player/orders/${o.id}`, { method: "DELETE" }); toast(o.status === "queued" ? "Cancelled." : "Cancel queued: it runs at the next tick."); await refresh(); }
    catch (e) { toast(e.message, "error"); }
  }
  async function edit(o) {
    const qty = prompt("Quantity", String(o.qty));
    if (qty === null) return;
    const price = prompt("Price", String(o.price));
    if (price === null) return;
    try { await api(`/api/session/player/orders/${o.id}`, { method: "PUT", body: { qty: parseNum(qty), price: parseNum(price) } }); await refresh(); }
    catch (e) { toast(e.message, "error"); }
  }
  async function cancelAll() {
    try { const r = await api("/api/session/player/orders", { method: "DELETE" }); toast(`${r.cancelled} order${r.cancelled === 1 ? "" : "s"} cancelled or queued to cancel.`); await refresh(); }
    catch (e) { toast(e.message, "error"); }
  }

  function renderOrders() {
    const open = acct.orders;
    ordersHost.replaceChildren(
      h("div", { class: "row" }, h("h2", {}, "Open orders"),
        h("button", { type: "button", class: "danger", disabled: !open.some((o) => o.status !== "cancelling"), onclick: cancelAll }, "Cancel all")),
      open.length ? h("table", { class: "mkt-table" },
        h("thead", {}, th("Order", "City", "Status", "Age (ticks)", "")),
        h("tbody", {}, open.map((o) => h("tr", { "data-order": o.id },
          h("td", {}, orderLabel(o)), h("td", {}, o.city), h("td", {}, statusText(o)), h("td", {}, o.age_ticks),
          h("td", { class: "row" },
            o.status === "queued" ? h("button", { type: "button", onclick: () => edit(o) }, "Edit") : null,
            h("button", { type: "button", disabled: o.status === "cancelling", onclick: () => cancel(o) }, "Cancel"))))))
        : h("p", { class: "muted" }, "No open orders."),
      h("h2", {}, "Recent results"),
      acct.finished.length ? h("table", { class: "mkt-table" },
        h("thead", {}, th("Order", "City", "Result")),
        h("tbody", {}, acct.finished.map((o) => h("tr", {}, h("td", {}, orderLabel(o)), h("td", {}, o.city), h("td", {}, statusText(o))))))
        : h("p", { class: "muted" }, "Orders that finished appear here."));
  }

  function renderHistory() {
    historyHost.replaceChildren(
      Object.keys(acct.totals).length ? h("table", { class: "mkt-table" },
        h("thead", {}, th("Good", "Bought", "Avg buy", "Sold", "Avg sell", "Spent", "Earned", "Realized profit")),
        h("tbody", {}, Object.entries(acct.totals).map(([g, t]) => h("tr", {}, h("td", {}, g), h("td", {}, t.bought), h("td", {}, fmt(t.avg_buy)),
          h("td", {}, t.sold), h("td", {}, fmt(t.avg_sell)), h("td", {}, fmt(t.spent)), h("td", {}, fmt(t.earned)),
          h("td", { class: t.realized >= 0 ? "good" : "bad" }, fmt(t.realized)))))) : null,
      acct.history.length ? h("table", { class: "mkt-table" },
        h("thead", {}, th("Day", "Tick", "City", "Good", "Side", "Qty", "Price", "Total", "Tax", "With", "Role")),
        h("tbody", {}, acct.history.map((f) => h("tr", {}, h("td", {}, f.day), h("td", {}, f.tick), h("td", {}, f.city), h("td", {}, f.good),
          h("td", {}, f.side === "buy" ? "Buy" : "Sell"), h("td", {}, f.qty), h("td", {}, fmt(f.price)), h("td", {}, fmt(f.total)),
          h("td", {}, fmt(f.tax)), h("td", {}, `#${f.counterparty}`), h("td", {}, f.role)))))
        : h("p", { class: "muted" }, "No trades yet."));
  }

  function renderPerf() {
    if (!perf) {
      perfHost.replaceChildren(h("div", { id: "perf-chart" }));
      perf = new LineChart(perfHost.firstChild, { label: "Net worth and profit by day", unit: "" });
    }
    const col = (v, f) => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || f;
    const pts = (k) => acct.networth.map((r) => [r.day, r[k]]);
    perf.set([{ name: "net worth", color: col("--verdigris", "#2F7D6D"), points: pts("net_worth") },
      { name: "profit (net of injected)", color: col("--ochre", "#C8902E"), points: pts("profit") },
      { name: "injected", color: col("--indigo", "#3F4F8A"), points: pts("injected"), width: 1 }]);
  }

  function renderOpenCard() {
    openCard.hidden = acct.open;
    grid.hidden = !acct.open;
    lower.hidden = !acct.open;
    if (acct.open) return;
    openCard.replaceChildren(h("h2", {}, "Open your trading account"),
      h("p", {}, "You trade as the player: you are not a citizen, you never travel, and you can use any city. The account starts with a grant of gold from the central authority, set by the scenario (by default half of the gold the citizens started with). You can add more at any time. Everything you add is counted as an injection, so profit is always shown net of it."),
      h("button", { type: "button", class: "primary", onclick: async () => {
        try { await api("/api/session/player", { method: "POST", body: {} }); await refresh(true); }
        catch (e) { toast(e.message, "error"); }
      } }, "Open account"));
  }

  // -- refresh ----------------------------------------------------------------------------------------
  async function refresh(rebuild = false) {
    if (busy) { again = true; return; }
    busy = true;
    try {
      if (!info) {
        info = await api("/api/session/market");
        city = info.cities[0];
        good = info.goods[0];
        cityPick.replaceChildren(...info.cities.map((c) => h("option", { value: c }, c)));
        rebuild = true;
      }
      acct = await api(`/api/session/player?city=${encodeURIComponent(city)}`);
      acct.unit = acct.unit || info.unit;
      renderOpenCard();
      if (acct.open) {
        view = await api(`/api/session/market/${encodeURIComponent(city)}?good=${encodeURIComponent(good)}`);
        cityPick.value = city;
        if (rebuild || !ticketHost.children.length) buildTicket();
        applySuggestion();
        renderGoods(); renderBook(); renderChart(); renderCross(); renderWallet();
        renderOrders(); renderHistory(); renderPerf();
        syncSubmit();
      }
    } catch (e) {
      if (!/no live run/.test(e.message)) toast(e.message, "error");
    } finally {
      busy = false;
      if (again) { again = false; setTimeout(refresh, 0); }
    }
  }

  let last = 0;
  const es = new EventSource("/api/session/stream");
  es.onopen = () => { connected = true; renderState(); };
  es.onerror = () => { connected = false; renderState(); };
  es.addEventListener("reset", (e) => { state = JSON.parse(e.data).state; renderState(); refresh(); });
  es.addEventListener("frame", (e) => {
    state = JSON.parse(e.data).state;
    renderState();
    const now = Date.now();
    if (now - last > 400) { last = now; refresh(); }
  });
  es.addEventListener("closed", () => { es.close(); toast("The live run was closed."); location.hash = "#/market"; window.dispatchEvent(new HashChangeEvent("hashchange")); });

  refresh();  // do not wait for the stream to show the market

  return () => { clearTimeout(quoteTimer); es.close(); if (chart) chart.destroy(); if (perf) perf.destroy(); };
}
