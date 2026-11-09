// popsim dashboard: hash router and shared helpers.
import * as runs from "./runs.js";
import * as compare from "./compare.js";
import * as scenarios from "./scenarios.js";
import * as play from "./play.js";
import * as market from "./market.js";
import { h, refreshLiveDot } from "./util.js";

const VIEWS = { runs, compare, scenarios, play, market };
let cleanup = null;

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "") || "runs";
  const [path, qs] = raw.split("?");
  const [view, ...rest] = path.split("/").map(decodeURIComponent);
  return { view: VIEWS[view] ? view : "runs", arg: rest.join("/") || null, query: new URLSearchParams(qs || "") };
}

async function route() {
  const { view, arg, query } = parseHash();
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  for (const a of document.querySelectorAll("[data-nav]")) a.classList.toggle("active", a.dataset.nav === view);
  const main = document.getElementById("main");
  main.replaceChildren();
  try {
    cleanup = (await VIEWS[view].mount(main, arg, query)) || null;
  } catch (e) {
    main.replaceChildren(h("div", { class: "empty" }, h("p", {}, e.message)));
  }
  refreshLiveDot();
}

window.addEventListener("hashchange", route);
route();
