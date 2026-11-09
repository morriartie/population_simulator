// Small canvas line-chart library: multiple series, gaps for nulls, linear/log
// y axis, event markers, a day cursor and a hover read-out.

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function niceStep(span, target) {
  const raw = span / Math.max(1, target);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
}

function fmt(v) {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  if (a >= 10000) return (v / 1000).toFixed(0) + "k";
  if (a >= 1000) return (v / 1000).toFixed(1) + "k";
  if (a >= 100 || Number.isInteger(v)) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  if (a === 0) return "0";
  return v.toPrecision(2);
}
export { fmt };

export class LineChart {
  constructor(host, opts = {}) {
    this.opts = { log: false, yMin: null, xLabel: "day", unit: "", ...opts };
    this.host = host;
    this.host.classList.add("chart");
    if (opts.tall) this.host.classList.add("tall");
    this.canvas = document.createElement("canvas");
    this.canvas.setAttribute("role", "img");
    this.canvas.setAttribute("aria-label", opts.label || "chart");
    this.tip = document.createElement("div");
    this.tip.className = "tip";
    this.tip.hidden = true;
    host.append(this.canvas, this.tip);
    this.series = [];
    this.events = [];
    this.cursor = null;
    this.hoverX = null;
    this.onPick = opts.onPick || null;
    this._ro = new ResizeObserver(() => this.draw());
    this._ro.observe(host);
    this.canvas.addEventListener("mousemove", (e) => this._hover(e));
    this.canvas.addEventListener("mouseleave", () => { this.hoverX = null; this.tip.hidden = true; this.draw(); });
    this.canvas.addEventListener("click", (e) => {
      if (!this.onPick || !this._geom) return;
      const x = this._invX(e.offsetX);
      if (x != null) this.onPick(Math.round(x));
    });
  }

  set(series, events = []) {
    this.series = series;
    this.events = events;
    this.draw();
  }
  setLog(log) { this.opts.log = log; this.draw(); }
  setCursor(x) { this.cursor = x; this.draw(); }
  destroy() { this._ro.disconnect(); }

  _ranges() {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const s of this.series) for (const [x, y] of s.points) {
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y == null || !isFinite(y) || (this.opts.log && y <= 0)) continue;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
    }
    if (this.opts.xMax != null) x1 = Math.max(x1, this.opts.xMax);
    if (!isFinite(x0)) { x0 = 0; x1 = 1; }
    if (x1 === x0) x1 = x0 + 1;
    if (!isFinite(y0)) { y0 = this.opts.log ? 1 : 0; y1 = this.opts.log ? 10 : 1; }
    if (!this.opts.log) {
      if (this.opts.yMin != null) y0 = Math.min(y0, this.opts.yMin);
      if (y1 === y0) { y1 = y0 + 1; }
      const pad = (y1 - y0) * 0.06;
      y1 += pad;
      if (this.opts.yMin == null) y0 -= pad;
    } else {
      if (y1 / y0 < 1.5) { y0 /= 1.25; y1 *= 1.25; }
      y0 /= 1.08; y1 *= 1.08;
    }
    return { x0, x1, y0, y1 };
  }

  _invX(px) {
    const g = this._geom;
    if (!g || px < g.l || px > g.r) return null;
    return g.x0 + (px - g.l) / (g.r - g.l) * (g.x1 - g.x0);
  }

  _hover(e) {
    if (!this._geom) return;
    const x = this._invX(e.offsetX);
    this.hoverX = x == null ? null : Math.round(x);
    this.draw();
    if (this.hoverX == null) { this.tip.hidden = true; return; }
    const lines = [`<b>${this.opts.xLabel} ${this.hoverX}</b>`];
    for (const s of this.series) {
      const p = s.points.find((q) => q[0] === this.hoverX);
      if (p && p[1] != null) lines.push(`<span style="color:${s.tipColor || "#fff"}">●</span> ${s.name}: ${fmt(p[1])}${this.opts.unit}`);
    }
    this.tip.innerHTML = lines.join("<br>");
    this.tip.hidden = false;
    const w = this.host.clientWidth;
    const left = e.offsetX + 160 > w ? e.offsetX - 170 : e.offsetX;
    this.tip.style.left = left + "px";
    this.tip.style.top = e.offsetY + "px";
  }

  draw() {
    const W = this.host.clientWidth, H = this.host.clientHeight;
    if (!W || !H) return;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = W * dpr;
    this.canvas.height = H * dpr;
    const ctx = this.canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const ink = css("--ink"), ink3 = css("--ink-3"), line2 = css("--line-2");
    const font = getComputedStyle(document.body).fontFamily;
    ctx.font = `11px ${font}`;

    const { x0, x1, y0, y1 } = this._ranges();
    const log = this.opts.log;
    const ty = (v) => (log ? Math.log10(v) : v);
    const Y0 = ty(y0), Y1 = ty(y1);
    // y ticks
    let ticks = [];
    if (log) {
      for (let e = Math.floor(Y0); e <= Math.ceil(Y1) && e - Math.floor(Y0) < 30; e++) {
        for (const m of [1, 2, 5]) { const v = m * 10 ** e; if (v >= y0 && v <= y1) ticks.push(v); }
      }
      if (ticks.length > 7) ticks = ticks.filter((v) => Math.abs(Math.log10(v) % 1) < 1e-9);
    } else {
      const st = niceStep(y1 - y0, 4);
      for (let v = Math.ceil(y0 / st) * st; v <= y1 + 1e-9; v += st) ticks.push(+v.toFixed(10));
    }
    const labelW = Math.max(...ticks.map((v) => ctx.measureText(fmt(v)).width), 10);
    const l = labelW + 10, r = W - 10, t = 8, b = H - 22;
    this._geom = { l, r, t, b, x0, x1 };
    const px = (x) => l + (x - x0) / (x1 - x0) * (r - l);
    const py = (v) => b - (ty(v) - Y0) / (Y1 - Y0) * (b - t);

    ctx.strokeStyle = line2; ctx.lineWidth = 1; ctx.fillStyle = ink3; ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (const v of ticks) {
      const yy = Math.round(py(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(l, yy); ctx.lineTo(r, yy); ctx.stroke();
      ctx.fillText(fmt(v), l - 6, yy);
    }
    // x ticks
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const xs = niceStep(x1 - x0, Math.max(2, Math.floor((r - l) / 70)));
    for (let x = Math.ceil(x0 / xs) * xs; x <= x1 + 1e-9; x += xs) {
      const w = ctx.measureText(fmt(x)).width / 2;
      ctx.fillText(fmt(x), Math.min(Math.max(px(x), l + w), W - w - 2), b + 6);
    }

    // events
    ctx.setLineDash([4, 4]); ctx.strokeStyle = ink3;
    ctx.textAlign = "left"; ctx.textBaseline = "top";
    for (const ev of this.events) {
      if (ev.x < x0 || ev.x > x1) continue;
      const xx = Math.round(px(ev.x)) + 0.5;
      ctx.beginPath(); ctx.moveTo(xx, t); ctx.lineTo(xx, b); ctx.stroke();
      ctx.fillStyle = ink3; ctx.fillText(ev.label, xx + 4, t);
    }
    ctx.setLineDash([]);

    // series
    ctx.save();
    ctx.beginPath(); ctx.rect(l, t - 2, r - l, b - t + 4); ctx.clip();
    for (const s of this.series) {
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 2; ctx.lineJoin = "round";
      ctx.setLineDash(s.dash ? [6, 4] : []);
      ctx.beginPath();
      let pen = false;
      for (const [x, y] of s.points) {
        if (y == null || !isFinite(y) || (log && y <= 0)) { pen = false; continue; }
        const X = px(x), Yv = py(y);
        if (!pen) { ctx.moveTo(X, Yv); pen = true; } else ctx.lineTo(X, Yv);
      }
      ctx.stroke();
    }
    ctx.restore();
    ctx.setLineDash([]);

    // cursor + hover
    for (const [x, colour, w] of [[this.cursor, ink, 1.5], [this.hoverX, ink3, 1]]) {
      if (x == null || x < x0 || x > x1) continue;
      const xx = Math.round(px(x)) + 0.5;
      ctx.strokeStyle = colour; ctx.lineWidth = w;
      ctx.beginPath(); ctx.moveTo(xx, t); ctx.lineTo(xx, b); ctx.stroke();
    }
    // baseline
    ctx.strokeStyle = ink3; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(l, b + 0.5); ctx.lineTo(r, b + 0.5); ctx.stroke();
  }
}

export function legend(items) {
  const el = document.createElement("div");
  el.className = "legend";
  for (const it of items) {
    const s = document.createElement("span");
    s.innerHTML = `<i class="${it.dash ? "dash" : ""}" style="border-top-color:${it.color}"></i>${it.name}`;
    el.append(s);
  }
  return el;
}

export function smooth(points, window) {
  if (window <= 1) return points;
  const out = [];
  const buf = [];
  for (const [x, y] of points) {
    buf.push(y);
    if (buf.length > window) buf.shift();
    const vals = buf.filter((v) => v != null && isFinite(v));
    out.push([x, vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null]);
  }
  return out;
}
