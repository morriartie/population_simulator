// Shared helpers for the dashboard views.

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `${method} ${path} failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

let toastTimer = null;
export function toast(msg, kind = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, kind === "error" ? 6000 : 3000);
}

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export const CITY_COLORS = ["--verdigris", "--ochre", "--indigo", "--plum", "--olive"];
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
export function cityColor(i) { return cssVar(CITY_COLORS[i % CITY_COLORS.length]); }

export function num(v, digits = 2) {
  if (v == null || v === "" || !isFinite(v)) return "–";
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function truthy(v) { return v === true || v === "True" || v === "true" || v === 1; }

export async function refreshLiveDot() {
  try {
    const s = await api("/api/session");
    document.getElementById("live-dot").hidden = !s || s.status === "none";
  } catch {
    document.getElementById("live-dot").hidden = true;
  }
}


// Tab strip over several panels. ``panels`` is [[id, label, element], ...]; ``onChange(id)`` runs on every switch.
export function tabs(panels, onChange = () => {}) {
  const bar = h("div", { class: "tabs", role: "tablist" });
  const wrap = h("div");
  const buttons = new Map();
  function select(id) {
    for (const [pid, , el] of panels) {
      el.hidden = pid !== id;
      buttons.get(pid).setAttribute("aria-selected", String(pid === id));
    }
    onChange(id);
  }
  for (const [id, label] of panels) {
    const b = h("button", { type: "button", role: "tab", onclick: () => select(id) }, label);
    buttons.set(id, b);
    bar.append(b);
  }
  wrap.append(bar, ...panels.map((p) => p[2]));
  wrap.select = select;
  select(panels[0][0]);
  return wrap;
}
