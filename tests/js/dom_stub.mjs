// A small stand-in for the browser DOM, enough to run the dashboard's own rendering and event code
// under Node: elements with children, attributes, listeners and bubbling events.

export class FakeNode {
  constructor(tag = "#node") {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attrs = {};
    this.listeners = {};
    this.style = {};
    this.className = "";
    this._text = null;
    const self = this;
    this.classList = {
      add: (c) => { if (!self.className.split(/\s+/).includes(c)) self.className = `${self.className} ${c}`.trim(); },
      remove: (c) => { self.className = self.className.split(/\s+/).filter((x) => x && x !== c).join(" "); },
      toggle: (c, on) => (on === undefined ? self.classList.contains(c) : on) ? (self.classList.add(c), true) : (self.classList.remove(c), false),
      contains: (c) => self.className.split(/\s+/).includes(c),
    };
  }
  append(...kids) {
    for (const k of kids.flat()) {
      const node = typeof k === "string" ? new FakeText(k) : k;
      node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") this.className = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  focus() { globalThis.document.activeElement = this; }
  querySelector(sel) {
    const m = /^\[([\w-]+)="(.*)"\]$/.exec(sel);
    return m ? findAll(this, (n) => n.attrs && n.attrs[m[1]] === m[2])[0] || null : null;
  }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 600 }; }
  get textContent() { return this._text ?? this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  /** Fire an event on this element and let it bubble, as a browser would. */
  fire(type, props = {}) {
    const ev = { type, target: this, clientX: 0, clientY: 0, stopped: false, defaultPrevented: false,
      stopPropagation() { this.stopped = true; }, preventDefault() { this.defaultPrevented = true; }, ...props };
    for (let n = this; n && !ev.stopped; n = n.parentNode) for (const fn of [...(n.listeners[type] || [])]) fn({ ...ev, currentTarget: n, stopPropagation: () => { ev.stopped = true; }, preventDefault: () => { ev.defaultPrevented = true; } });
    return ev;
  }
}
class FakeText extends FakeNode {
  constructor(t) { super("#text"); this._text = String(t); }
}

export function walk(root, fn) { fn(root); for (const c of root.children) walk(c, fn); }
export function findAll(root, pred) { const out = []; walk(root, (n) => pred(n) && out.push(n)); return out; }
export const byClass = (root, cls) => findAll(root, (n) => n.classList && n.classList.contains(cls));
export const byTag = (root, tag) => findAll(root, (n) => n.tagName === tag.toUpperCase());
export const byText = (root, tag, text) => findAll(root, (n) => n.tagName === tag.toUpperCase() && n.textContent === text);

export function installDom() {
  // timers must not keep the test process alive (toasts hide themselves after a few seconds)
  const realSetTimeout = globalThis.__realSetTimeout || globalThis.setTimeout;
  globalThis.__realSetTimeout = realSetTimeout;
  globalThis.setTimeout = (fn, ms) => { const t = realSetTimeout(fn, ms); if (t && t.unref) t.unref(); return t; };
  const toastNode = new FakeNode("div");
  const windowListeners = {};
  const doc = {
    documentElement: new FakeNode("html"), activeElement: null,
    createElement: (t) => new FakeNode(t), createElementNS: (_ns, t) => new FakeNode(t),
    createTextNode: (t) => new FakeText(t), getElementById: () => toastNode,
  };
  globalThis.document = doc;
  globalThis.Node = FakeNode;
  globalThis.getComputedStyle = () => ({ getPropertyValue: (name) => ({ "--madder": "RED", "--ochre": "AMBER" }[name] || "#445566") });
  globalThis.window = {
    addEventListener: (t, fn) => (windowListeners[t] ||= []).push(fn),
    removeEventListener: (t, fn) => { windowListeners[t] = (windowListeners[t] || []).filter((f) => f !== fn); },
    fire: (t, ev = {}) => (windowListeners[t] || []).slice().forEach((fn) => fn({ type: t, stopPropagation() {}, preventDefault() {}, ...ev })),
    listenerCount: (t) => (windowListeners[t] || []).length,
  };
  globalThis.confirm = () => true;
  globalThis.localStorage = (() => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) }; })();
  // network: tests set `calls` and `respond`
  const calls = [];
  const net = { calls, respond: () => ({}) };
  globalThis.fetch = async (url, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ url, method: opts.method || "GET", body });
    const data = await net.respond(url, body);
    const status = data && data.__status ? data.__status : 200;
    return { ok: status < 400, status, json: async () => data };
  };
  return { toast: toastNode, net, doc };
}
