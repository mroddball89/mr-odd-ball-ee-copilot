#!/usr/bin/env node
/**
 * Module: verify-rig.mjs
 * Purpose: Automated pre-publish verification for the Mr Odd Ball character rig.
 * Author:  LB
 * Date:    2026-08-10
 * Usage:   node tools/verify-rig.mjs [path/to/face-preview.html]
 *
 * Why this exists
 * ---------------
 * docs/RIG-NOTES.md carries a pre-publish checklist, and CLAUDE.md records that running it
 * caught several real rig bugs — while skipping it shipped one. A checklist that lives only
 * in prose gets skipped under pressure. This turns it into one command.
 *
 * Zero dependencies. Node >= 18.
 *
 * How it stays honest
 * -------------------
 * Every NUMBER is parsed out of the rig itself — the EYE / JAW / STATES / GESTURES tables and
 * the SVG element attributes — so tuning values can never drift out of sync with these checks.
 * The transform FORMULAS are reimplemented here (composition order, pivots, the jaw envelope).
 * If you change how a transform is composed in face-preview.html, update the matching helper
 * below. Changing a constant needs no edit here.
 *
 * Exit code 0 = all checks passed, 1 = at least one failed.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const RIG = process.argv[2] ?? resolve(HERE, "..", "hud", "face-preview.html");
const SRC = readFileSync(RIG, "utf8");

/* ------------------------------------------------------------------ reporting */

const results = [];
let currentSection = "";

const section = (name) => { currentSection = name; };
const pass = (msg, detail = "") => results.push({ ok: true, section: currentSection, msg, detail });
const fail = (msg, detail = "") => results.push({ ok: false, section: currentSection, msg, detail });
const check = (cond, msg, detail = "") => (cond ? pass(msg, detail) : fail(msg, detail));

/* ------------------------------------------------------- parsing out of the rig */

/** Extract `const NAME = {...};` with correct brace matching, and evaluate it to a real object. */
function parseObjectLiteral(name, { required = true } = {}) {
  // Tolerant of alignment whitespace. An exact `const NAME = {` match silently missed
  // `const keyGest  = {` (two spaces), which turned a runtime check into a vacuous pass.
  const m = SRC.match(new RegExp(`\\bconst\\s+${name}\\s*=\\s*\\{`));
  if (!m) {
    if (required) throw new Error(`could not find "const ${name} = {…}" in ${RIG}`);
    return null;
  }
  const open = SRC.indexOf("{", m.index);
  let depth = 0, end = -1, inStr = null, inLine = false, inBlock = false;
  for (let i = open; i < SRC.length; i++) {
    const c = SRC[i], n = SRC[i + 1];
    // Comments must be skipped before strings, or a lone apostrophe in prose ("he's looking
    // anywhere but at you") opens a string that never closes and eats the closing brace.
    if (inLine) { if (c === "\n") inLine = false; continue; }
    if (inBlock) { if (c === "*" && n === "/") { inBlock = false; i++; } continue; }
    if (inStr) { if (c === inStr && SRC[i - 1] !== "\\") inStr = null; continue; }
    if (c === "/" && n === "/") { inLine = true; i++; continue; }
    if (c === "/" && n === "*") { inBlock = true; i++; continue; }
    if (c === '"' || c === "'" || c === "`") { inStr = c; continue; }
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) { end = i; break; } }
  }
  if (end === -1) throw new Error(`unbalanced braces in "${name}"`);
  return vm.runInNewContext(`(${SRC.slice(open, end + 1)})`);
}

/** Pull numeric attributes off the SVG element carrying `id`. */
function svgEl(id) {
  const m = SRC.match(new RegExp(`<(\\w+)\\b[^>]*\\bid="${id}"[^>]*>`, "s"));
  if (!m) throw new Error(`no SVG element with id="${id}"`);
  const tag = m[0];
  const attr = (n) => {
    const a = tag.match(new RegExp(`\\b${n}="([^"]*)"`));
    return a ? a[1] : null;
  };
  const num = (n) => { const v = attr(n); return v === null ? null : parseFloat(v); };
  return {
    tag: m[1], raw: tag,
    cx: num("cx"), cy: num("cy"), r: num("r"), rx: num("rx"), ry: num("ry"),
    stroke: num("stroke-width") ?? 0,
    transform: attr("transform"),
  };
}

/* --------------------------------------------------------------- geometry utils */

const DEG = Math.PI / 180;
const rot = (x, y, deg, px, py) => {
  const c = Math.cos(deg * DEG), s = Math.sin(deg * DEG);
  const dx = x - px, dy = y - py;
  return [px + dx * c - dy * s, py + dx * s + dy * c];
};

/** Sample N points around an ellipse (optionally rotated about a pivot). */
function ellipsePoints(cx, cy, rx, ry, n = 720, rotDeg = 0, px = cx, py = cy) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * 2 * Math.PI;
    let x = cx + rx * Math.cos(a), y = cy + ry * Math.sin(a);
    if (rotDeg) [x, y] = rot(x, y, rotDeg, px, py);
    out.push([x, y]);
  }
  return out;
}

/**
 * Clearance of a point inside an axis-aligned ellipse, in px along the ray from its centre.
 * Positive = inside. This is exact: the boundary along the same ray sits at |d|/k.
 */
function clearanceInEllipse([x, y], cx, cy, RX, RY) {
  const dx = x - cx, dy = y - cy;
  const k = Math.hypot(dx / RX, dy / RY);
  if (k === 0) return Math.min(RX, RY);
  const d = Math.hypot(dx, dy);
  return d / k - d;
}

/* ---------------------------------------------------------------- load the rig */

const EYE = parseObjectLiteral("EYE");
const JAW = parseObjectLiteral("JAW");
const STATES = parseObjectLiteral("STATES");
const GESTURES = parseObjectLiteral("GESTURES", { required: false });
const TAU = parseObjectLiteral("TAU");

const ball = svgEl("ball");
const eyeL = svgEl("eyeL");
const mawEdge = svgEl("mawEdge");
const teethEdge = svgEl("teethEdge");

// Outer painted extent = geometry + half the stroke. Inner = geometry - half the stroke.
const BALL_OUT = ball.r + ball.stroke / 2;
const BALL_IN = ball.r - ball.stroke / 2;
const MAW_RX = mawEdge.rx + mawEdge.stroke / 2;
const MAW_RY = mawEdge.ry + mawEdge.stroke / 2;
const MAW_ROT = parseFloat((mawEdge.transform.match(/rotate\((-?[\d.]+)/) || [])[1] ?? 0);

const VIEWBOX = (SRC.match(/viewBox="0 0 (\d+) (\d+)"/) || []).slice(1).map(Number);

/* ============================================================ 1. structure */

section("structure");

{
  const opens = (SRC.match(/<g\b/g) || []).length;
  const closes = (SRC.match(/<\/g>/g) || []).length;
  const selfClosing = (SRC.match(/<g\b[^>]*\/>/g) || []).length;
  check(opens - selfClosing === closes,
    "SVG <g> open/close balance",
    `${opens} open (${selfClosing} self-closing), ${closes} close`);
}

{
  // every id the script looks up must exist in the markup
  const wanted = [...SRC.matchAll(/\$\("([\w-]+)"\)/g)].map((m) => m[1]);
  const missing = [...new Set(wanted)].filter((id) => !new RegExp(`\\bid="${id}"`).test(SRC));
  check(missing.length === 0, "every id referenced by $() exists in the markup",
    missing.length ? `missing: ${missing.join(", ")}` : `${new Set(wanted).size} ids resolved`);
}

{
  // every url(#x) reference must be defined
  const refs = [...new Set([...SRC.matchAll(/url\(#([\w-]+)\)/g)].map((m) => m[1]))];
  const missing = refs.filter((id) => !new RegExp(`\\bid="${id}"`).test(SRC));
  check(missing.length === 0, "every url(#…) reference is defined",
    missing.length ? `missing: ${missing.join(", ")}` : `${refs.length} refs resolved`);
}

/* ============================================================ 2. syntax */

section("syntax");

{
  const m = SRC.match(/<script>([\s\S]*?)<\/script>/);
  if (!m) fail("found a <script> block to check");
  else {
    try {
      new vm.Script(m[1], { filename: "face-preview inline script" });
      pass("inline script compiles", `${m[1].split("\n").length} lines`);
    } catch (e) {
      fail("inline script compiles", e.message);
    }
  }
}

/* ============================================================ 2b. runtime smoke test

   Compiling proves syntax; it does not prove the loop runs. This executes the real script
   against a minimal DOM stub and drives every state and every gesture through the actual
   keydown handler, stepping frames each time. It catches what static checks cannot: a typo'd
   element lookup, a channel read before it exists, a transform built from undefined. It also
   proves the key bindings are wired, not just declared. */

section("runtime");

/**
 * Build a fresh DOM stub and boot the rig's script in it.
 * `WebSocketImpl` is deliberately a parameter: the rig must run identically whether or not
 * a WebSocket exists, because it is a standalone file that merely happens to listen.
 * `search` is the query string, so a boot can be driven the way a real URL would drive it
 * (`?solo=1`). Default "" is the "opened by double-clicking the file" case.
 */
function bootRig(WebSocketImpl, search = "") {
  const mkEl = (id = "") => {
    const e = {
      id, textContent: "", value: "0", dataset: {}, attrs: {}, handlers: {},
      style: { setProperty: (k, v) => { e.attrs[`style:${k}`] = v; } },
      classList: { toggle: () => {} },
      setAttribute: (k, v) => {
        if (v === undefined || v === null || String(v).includes("undefined") || String(v).includes("NaN"))
          throw new Error(`bad ${k} on #${id}: ${v}`);
        e.attrs[k] = v;
      },
      addEventListener: (t, fn) => { (e.handlers[t] ??= []).push(fn); },
      matches: () => false,
    };
    return e;
  };

  const els = new Map();
  const getEl = (id) => { if (!els.has(id)) els.set(id, mkEl(id)); return els.get(id); };

  const mkButtons = (cls, attr) =>
    [...SRC.matchAll(new RegExp(`button class="${cls}" data-${attr}="(\\w+)"`, "g"))]
      .map((m) => { const b = mkEl(`btn-${m[1]}`); b.dataset[attr] = m[1]; return b; });

  const stateBtns = mkButtons("state", "s");
  const gestBtns = mkButtons("gest", "g");

  let rafCb = null, keyCb = null, clock = 0;
  // <html>. Solo mode sets `dataset.solo` here, and the CSS keys off it — so this element
  // carries the only observable difference between a solo boot and an ordinary one, which
  // is what the solo checks read back.
  const docEl = mkEl("html");
  const sandbox = {
    document: {
      documentElement: docEl,
      getElementById: getEl,
      querySelectorAll: (sel) =>
        sel.includes("button.state") ? stateBtns : sel.includes("button.gest") ? gestBtns : [],
    },
    matchMedia: () => ({ matches: false }),
    performance: { now: () => clock },
    requestAnimationFrame: (cb) => { rafCb = cb; },
    addEventListener: (t, fn) => { if (t === "keydown") keyCb = fn; },
    // Retry timers are swallowed: the link reconnects forever by design, and running those
    // callbacks here would just recurse.
    setTimeout: () => 0,
    clearTimeout: () => {},
    // A browser always has these, and the link code derives its URL from them. Modelled as
    // a file:// page — the "opened by double-clicking" case — with whatever query string
    // this boot was asked for.
    location: { search, protocol: "file:", host: "" },
    URLSearchParams,
    Math, Number, String, Array, Object, JSON, isNaN, parseInt, parseFloat,
  };
  if (WebSocketImpl) sandbox.WebSocket = WebSocketImpl;

  const step = (frames = 6, dt = 40) => {
    for (let i = 0; i < frames; i++) { clock += dt; rafCb?.(clock); }
  };
  const press = (key) => keyCb?.({ key, target: { matches: () => false }, preventDefault() {} });

  const body = SRC.match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(body, sandbox, { filename: "face-preview" });
  return { getEl, step, press, docEl, rafCb: () => rafCb, keyCb: () => keyCb };
}

{
  let H;
  try {
    // No WebSocket in this sandbox, exactly like a browser opening the file with nothing
    // running. If the link code ever stops degrading silently, the rig stops booting here.
    H = bootRig(null);
    if (!H.rafCb()) throw new Error("script never called requestAnimationFrame");
    if (!H.keyCb()) throw new Error("script never registered a keydown handler");
    H.step(4);
    pass("script boots and the animation loop runs with NO WebSocket available",
      `${Object.keys(STATES).length} states loaded`);
  } catch (e) {
    fail("script boots and the animation loop runs with NO WebSocket available", e.message);
  }
}

{
  // The live link drives the rig from orchestrator/run_wake.py. Boot with a stubbed
  // WebSocket and push the exact wire messages the bridge sends, to prove the two ends
  // actually agree — a rename on either side would otherwise fail silently at 3am.
  try {
    let sock = null;
    class StubSocket {
      constructor(url) { this.url = url; sock = this; }
      close() { if (this.onclose) this.onclose(); }
    }
    const { getEl, step } = bootRig(StubSocket);
    if (!sock) throw new Error("the rig never opened a WebSocket");

    const url = sock.url;
    sock.onopen?.();
    const openLabel = getEl("rLink").textContent;

    sock.onmessage({ data: JSON.stringify({ type: "state", value: "listening" }) });
    step(4);
    const gotState = getEl("rState").textContent;

    sock.onmessage({ data: JSON.stringify({ type: "gesture", value: "startle" }) });
    step(2);
    const gotGesture = getEl("rGest").textContent;

    // Garbage must not take the animation loop down with it.
    sock.onmessage({ data: "not json at all" });
    sock.onmessage({ data: JSON.stringify({ type: "state", value: "nonsense_state" }) });
    step(4);
    const survived = getEl("rState").textContent === "listening";

    check(
      url === "ws://127.0.0.1:8765" && openLabel === "live" &&
      gotState === "listening" && gotGesture === "startle" && survived,
      "the live link applies state and gesture messages, and ignores malformed ones",
      `url=${url} link=${openLabel} state=${gotState} gesture=${gotGesture} ` +
      `survived-garbage=${survived}`);
  } catch (e) {
    fail("the live link applies state and gesture messages, and ignores malformed ones", e.message);
  }
}

{
  // Live lip-sync (step 4). audio/say.py emits one 0..1 loudness per 20ms audio block and
  // orchestrator/hud_bridge.py forwards it as {"type":"mouth"}. Three things have to hold,
  // and only the first is obvious.
  const boot = () => {
    let sock = null;
    class StubSocket {
      constructor(url) { this.url = url; sock = this; }
      close() { if (this.onclose) this.onclose(); }
    }
    const H = bootRig(StubSocket);
    if (!sock) throw new Error("the rig never opened a WebSocket");
    sock.onopen?.();
    sock.onmessage({ data: JSON.stringify({ type: "state", value: "speaking" }) });
    // hold `v` for `frames` frames, sampling the mouth each frame. null = send nothing.
    const hold = (v, frames) => {
      const seen = [];
      for (let i = 0; i < frames; i++) {
        if (v !== null) sock.onmessage({ data: JSON.stringify({ type: "mouth", value: v }) });
        H.step(1);
        seen.push(+H.getEl("rMouth").textContent);
      }
      return seen;
    };
    return { ...H, sock, hold };
  };
  const spread = (a) => Math.max(...a) - Math.min(...a);

  try {
    const { hold } = boot();
    const open = hold(1.0, 20), shut = hold(0.0, 20);
    const openEnd = open.at(-1), shutEnd = shut.at(-1);
    // 0.30 + v*0.70, so a live 1.0 means a fully open mouth and a live 0.0 means the 0.30
    // floor — and holding 0.0 must be *steady*. The synthetic envelope is never steady, so
    // steadiness is what proves the live value actually took over rather than coinciding.
    const steady = spread(shut.slice(10)) < 0.02;
    check(openEnd > 0.95 && shutEnd < 0.35 && steady,
      "a live mouth envelope drives the jaw and overrides the synthetic one",
      `held 1.0 -> ${openEnd.toFixed(2)}, held 0.0 -> ${shutEnd.toFixed(2)}, ` +
      `spread while held ${spread(shut.slice(10)).toFixed(3)}`);
  } catch (e) {
    fail("a live mouth envelope drives the jaw and overrides the synthetic one", e.message);
  }

  try {
    const { hold, sock, getEl, step } = boot();
    // Out of range. Unclamped this would push jawY past JAW.maxSy and his mouth back through
    // the silhouette — the v0.1 bug this harness was written to catch.
    const over = hold(5.0, 20).at(-1);
    const jawY = Math.min(JAW.syA + over * JAW.syB, JAW.maxSy);
    // Values that are not numbers at all. mkEl.setAttribute throws on NaN, so a poisoned
    // channel fails loudly here rather than rendering an invisible mouth.
    for (const bad of ["banana", null, undefined, NaN, {}, [1, 2]])
      sock.onmessage({ data: JSON.stringify({ type: "mouth", value: bad }) });
    step(4);
    const after = +getEl("rMouth").textContent;
    const under = hold(-3.0, 20).at(-1);
    check(over <= 1.0 && under >= 0.0 && Number.isFinite(after) && jawY <= JAW.maxSy + 1e-9,
      "live mouth values are clamped to 0..1 and non-numbers are ignored",
      `sent 5.0 -> ${over.toFixed(2)} (jawY ${jawY.toFixed(3)} <= maxSy ${JAW.maxSy}), ` +
      `sent -3.0 -> ${under.toFixed(2)}, after garbage -> ${after.toFixed(2)}`);
  } catch (e) {
    fail("live mouth values are clamped to 0..1 and non-numbers are ignored", e.message);
  }

  try {
    const { hold } = boot();
    hold(0.0, 20);                       // pinned flat at the 0.30 floor, and provably steady
    // Now the link goes quiet mid-sentence. He must resume the synthetic envelope rather
    // than freeze with his jaw wherever the last packet left it. 320 frames at 40ms is 12.8s,
    // longer than one full phrase cycle of envelope() (~10.1s), so a pause cannot fake it.
    const quiet = hold(null, 320);
    check(spread(quiet) > 0.05,
      "a stalled live link falls back to the synthetic envelope instead of freezing",
      `mouth moved ${spread(quiet).toFixed(2)} over 12.8s of silence ` +
      `(${Math.min(...quiet).toFixed(2)}..${Math.max(...quiet).toFixed(2)})`);
  } catch (e) {
    fail("a stalled live link falls back to the synthetic envelope instead of freezing", e.message);
  }
}

{
  const { getEl, step, press } = (() => {
    try { return bootRig(null); } catch { return { getEl: () => ({}), step() {}, press() {} }; }
  })();

  try {
    // every state, via its real key binding
    const orderM = SRC.match(/const order = \[([^\]]*)\]/);
    const order = orderM ? vm.runInNewContext(`[${orderM[1]}]`) : [];
    const keyState = parseObjectLiteral("keyState", { required: false }) ?? {};
    const visited = [];
    for (let i = 0; i < order.length; i++) { press(String(i + 1)); step(); visited.push(getEl("rState").textContent); }
    for (const k of Object.keys(keyState)) { press(k); step(); visited.push(getEl("rState").textContent); }

    const missed = Object.keys(STATES).filter((s) => !visited.includes(s));
    check(missed.length === 0, "every state can be entered from its key and renders a frame",
      missed.length ? `never reached: ${missed.join(", ")}` : `${visited.length} states stepped`);

    // every gesture, stepped across its full duration and past the end
    const keyGest = parseObjectLiteral("keyGest", { required: false }) ?? {};
    // Guard against a vacuous pass: an empty map here would step nothing and still report
    // success. This check has already caught itself doing exactly that once.
    check(Object.keys(keyGest).length === Object.keys(GESTURES ?? {}).length,
      "every gesture has a key binding to drive it",
      `bound: ${Object.keys(keyGest).length}, defined: ${Object.keys(GESTURES ?? {}).length}`);
    // Read the gesture's own contribution out of #bodyG. Comparing the whole transform string
    // is wrong: it also carries the STATE zoom, which is still easing after a state change,
    // so a settled gesture would look like a moved one.
    const locomotion = () => {
      const m = (getEl("bodyG").attrs.transform ?? "")
        .match(/translate\((-?[\d.]+) (-?[\d.]+)\) rotate\((-?[\d.]+)/);
      return m ? { tx: +m[1], ty: +m[2], spin: +m[3] } : null;
    };
    const moved = (l) => l && (Math.abs(l.tx) + Math.abs(l.ty) + Math.abs(l.spin)) > 0.01;

    const stuck = [];
    for (const [k, name] of Object.entries(keyGest)) {
      const g = GESTURES[name];
      const total = Math.ceil((g.dur * 1000) / 40);
      press("1"); step(4);                                    // known state first

      press(k);
      step(Math.max(2, Math.round(total * 0.25)));            // sample mid-flight
      // Locomotion gestures return him exactly where he started, so comparing before with
      // after proves nothing — the movement has to be caught while it is happening.
      if ((g.travel || g.rise) && !moved(locomotion())) stuck.push(`${name}: #bodyG never moved`);

      step(total + 8);                                        // run past the end
      if (getEl("rGest").textContent !== "–") stuck.push(`${name}: never released`);
      if (moved(locomotion())) stuck.push(`${name}: #bodyG left off-origin`);
    }
    check(stuck.length === 0, "every gesture fires, plays out, and releases cleanly",
      stuck.length ? stuck.join("; ") : `${Object.keys(keyGest).length} gestures played to completion`);

    // a gesture fired mid-speech must not wedge anything
    press("6"); step(10); press(" "); step(30); press("1"); step(10);
    check(getEl("rGest").textContent === "–", "a gesture fired during speaking still releases");
  } catch (e) {
    fail("script boots and the animation loop runs", e.message);
  }
}

/* ============================================================ 2b. solo mode
 *
 * `?solo=1` is what the Pi runs (config/oddball-face.desktop): the page hides every bit of
 * development chrome and clears its background, so the GTK window's transparency reaches the
 * compositor and only the character is drawn. See D41.
 *
 * Worth checking rather than eyeballing, because the failure is invisible from here. The two
 * halves of the transparency live in different files and in different languages, and if either
 * one is missing the result is not a broken-looking window — it is a window that looks EXACTLY
 * like no change was made. That is indistinguishable from "the deploy didn't land", which is
 * how an evening gets spent re-copying files that were already correct.
 */

section("solo");

{
  const style = SRC.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? "";
  // Selector + body for every rule. Nested @media wrappers are skipped rather than parsed:
  // `[^{}]` cannot cross a brace, so the engine lands on the inner rule, which is the one
  // carrying declarations. Every solo rule is top-level, so this sees all of them.
  const rules = [...style.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map((m) => ({ sel: m[1].trim().replace(/\s+/g, " "), body: m[2] }));
  const soloRules = rules.filter((r) => r.sel.includes("data-solo"));
  const decl = (r, prop) =>
    r.body.match(new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`))?.[1].trim() ?? null;
  const soloFor = (target, prop) =>
    soloRules.filter((r) => r.sel.split(",").some((s) => s.trim().endsWith(target)))
      .map((r) => decl(r, prop)).find((v) => v !== null) ?? null;

  check(soloRules.length > 0, "the rig has a solo mode at all",
    `${soloRules.length} rules keyed on [data-solo]`);

  // Every element that is not him. Hidden, not deleted — the runtime checks above look up
  // #caption and #rLink and the consistency checks count the panel's buttons, so removing
  // the markup would take this harness down with it.
  const CHROME = ["header", ".caption", ".panel", ".paper-grid"];
  const shown = CHROME.filter((c) => soloFor(c, "display") !== "none");
  check(shown.length === 0, "solo hides every piece of development chrome",
    shown.length ? `still visible: ${shown.join(", ")}` : CHROME.join(", "));

  // Half the transparency. The other half is `--transparent` in tools/spike_gtk_face.py;
  // this check owns the half that lives in the rig.
  check(soloFor("body", "background") === "transparent",
    "solo clears the page background, so the window's alpha reaches the compositor",
    `body background in solo: ${soloFor("body", "background")}`);

  // ...and the ordinary page must NOT be transparent, or opening the rig in a browser gives
  // a white sheet with a character on it and no stage at all.
  const plainBody = rules.find((r) => r.sel === "body" && decl(r, "background"));
  check(plainBody && decl(plainBody, "background") === "var(--stage)",
    "without solo the page still paints its stage",
    `body background: ${plainBody ? decl(plainBody, "background") : "no rule found"}`);

  // LB's call: he is a character on the desktop, not a fullscreen takeover.
  const wrap = soloFor(".rig-wrap", "width");
  check(wrap !== null && wrap.includes("560px"), "solo keeps the 560px cap on his size",
    `.rig-wrap width in solo: ${wrap}`);

  // Both directions, so this cannot pass vacuously: a rig that set the attribute
  // unconditionally would satisfy the first check and fail the second.
  try {
    const on = bootRig(null, "?solo=1");
    const off = bootRig(null, "");
    const zero = bootRig(null, "?solo=0");
    check(on.docEl.dataset.solo === "on", "?solo=1 puts the page in solo mode",
      `data-solo=${JSON.stringify(on.docEl.dataset.solo)}`);
    check(off.docEl.dataset.solo === undefined,
      "a plain URL does NOT — the rig opens as the development page it is",
      `data-solo=${JSON.stringify(off.docEl.dataset.solo)}`);
    check(zero.docEl.dataset.solo === undefined, "?solo=0 turns it off rather than on",
      `data-solo=${JSON.stringify(zero.docEl.dataset.solo)}`);
  } catch (e) {
    fail("the rig boots with ?solo=1", e.message);
  }

  // This harness extracts the rig with a NON-GREEDY match on the first <script> block. A
  // second block anywhere above it would silently capture the wrong text: every runtime
  // check would then be testing a few lines of unrelated script and passing vacuously.
  const blocks = [...SRC.matchAll(/<script\b/g)].length;
  check(blocks === 1, "exactly one <script> block, which is what the runtime checks extract",
    `${blocks} found`);
}

/* ============================================================ 3. consistency */

section("consistency");

{
  const stateKeys = Object.keys(STATES);
  const buttons = [...SRC.matchAll(/button class="state" data-s="(\w+)"/g)].map((m) => m[1]);
  const orderM = SRC.match(/const order = \[([^\]]*)\]/);
  const order = orderM ? vm.runInNewContext(`[${orderM[1]}]`) : [];
  // Keys are split: digits 1-8 keep the original eight, v0.2 states are on letters.
  const keyState = parseObjectLiteral("keyState", { required: false }) ?? {};
  const bound = [...order, ...Object.values(keyState)];

  const setEq = (a, b) => a.length === b.length && a.every((x) => b.includes(x));

  check(setEq(stateKeys, buttons), "STATES keys match the panel buttons",
    `states: ${stateKeys.length}, buttons: ${buttons.length}` +
    (setEq(stateKeys, buttons) ? "" : ` | only in STATES: ${stateKeys.filter(k => !buttons.includes(k))} | only in panel: ${buttons.filter(k => !stateKeys.includes(k))}`));

  check(setEq(stateKeys, bound), "every state is reachable from the keyboard",
    `digits: ${order.length}, letters: ${Object.keys(keyState).length}` +
    (setEq(stateKeys, bound) ? "" : ` | unbound: ${stateKeys.filter(k => !bound.includes(k))} | stale: ${bound.filter(k => !stateKeys.includes(k))}`));

  check(new Set(bound).size === bound.length, "no state is bound to two different keys",
    `${bound.length} bindings`);

  // every state must define every channel the loop reads, or it silently eases toward undefined
  const channels = ["eye", "pupil", "bob", "tilt", "blink", "drift", "mouthBase", "glow", "glowA", "sweat", "cap"];
  const bad = stateKeys.filter((k) => channels.some((c) => STATES[k][c] === undefined));
  check(bad.length === 0, "every state defines every channel the loop reads",
    bad.length ? `incomplete: ${bad.join(", ")}` : `${stateKeys.length} states × ${channels.length} channels`);

  const badBlink = stateKeys.filter((k) => !Array.isArray(STATES[k].blink) || STATES[k].blink.length !== 2
    || STATES[k].blink[0] > STATES[k].blink[1]);
  check(badBlink.length === 0, "every blink range is a valid [min,max]",
    badBlink.length ? `bad: ${badBlink.join(", ")}` : "");
}

if (GESTURES) {
  const gKeys = Object.keys(GESTURES);
  const buttons = [...SRC.matchAll(/button class="gest" data-g="(\w+)"/g)].map((m) => m[1]);
  const setEq = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
  check(setEq(gKeys, buttons), "GESTURES keys match the gesture buttons",
    `gestures: ${gKeys.length}, buttons: ${buttons.length}`);

  const bad = gKeys.filter((k) => !(GESTURES[k].dur > 0));
  check(bad.length === 0, "every gesture has a positive duration",
    bad.length ? `bad: ${bad.join(", ")}` : gKeys.map((k) => `${k} ${GESTURES[k].dur}s`).join(", "));
}

/* ============================================================ 4. geometry */

section("geometry");

const worst = {};

/* ---- 4a. pupil stays inside its sclera, across gaze × pupil scale × blink.
   Pupil group and sclera are both children of #eyeLG, so the eye's vertical scale applies
   to both equally and cancels — containment is checked in that shared local space. */
{
  const SCLERA_IN_RX = eyeL.rx - eyeL.stroke / 2;
  const SCLERA_IN_RY = eyeL.ry - eyeL.stroke / 2;
  // Reachable pupil scales = the state table, PLUS the largest additive gesture offset,
  // since a gesture stacks on top of whatever state is current.
  const pupilScales = Object.values(STATES).map((s) => s.pupil);
  const gPupil = GESTURES ? Math.max(0, ...Object.values(GESTURES).map((g) => g.pupil ?? 0)) : 0;
  const minPupil = Math.min(...pupilScales), maxPupil = Math.max(...pupilScales) + gPupil;

  let min = Infinity, at = null;
  for (let p = minPupil; p <= maxPupil + 1e-9; p += 0.01) {
    const maxX = EYE.rxr - EYE.prx * p - EYE.margin;
    const maxY = EYE.ryr - EYE.pry * p - EYE.margin;
    for (let gx = -1; gx <= 1; gx += 0.05) {
      for (let gy = -1; gy <= 1; gy += 0.05) {
        // Mirrors the rig: gaze is a VECTOR clamped to the unit disc, not two independent
        // axes. Clamping to a square let full diagonal deflection put the pupil outside the
        // elliptical sclera — the sclera is an ellipse, so the travel region must be too.
        const g = Math.hypot(gx, gy), k = g > 1 ? 1 / g : 1;
        const px = gx * k * Math.max(0, maxX), py = gy * k * Math.max(0, maxY);
        for (const pt of ellipsePoints(EYE.lx + px, EYE.cy + py, EYE.prx * p, EYE.pry * p, 180)) {
          const c = clearanceInEllipse(pt, EYE.lx, EYE.cy, SCLERA_IN_RX, SCLERA_IN_RY);
          if (c < min) { min = c; at = { pupil: +p.toFixed(2), gx: +gx.toFixed(2), gy: +gy.toFixed(2) }; }
        }
      }
    }
  }
  worst["pupil inside sclera"] = min;
  check(min > 0, "pupil never paints outside its sclera",
    `worst margin ${min.toFixed(1)}px at pupil=${at.pupil} gaze=(${at.gx}, ${at.gy})`);
}

/* ---- 4b. THE ONE THAT WAS MISSING.
   Mouth stays inside the ball silhouette across mouth × jaw phase × BALL SQUASH phase.
   #ball carries squash-and-stretch that #mouthG does not inherit, so the ball's inner edge
   moves relative to the jaw. The earlier hand-check held the ball static and therefore
   reported a margin roughly 3x looser than the truth. Both live under #headG, whose tilt and
   bob apply to each equally and so cancel. */
{
  const mawPts = ellipsePoints(mawEdge.cx, mawEdge.cy, MAW_RX, MAW_RY, 720, MAW_ROT, mawEdge.cx, mawEdge.cy);

  /**
   * One pose. Swept per STATE, because the terms are not independent: talkPunch, the syllable
   * bob and the lateral sway exist only while `speaking`, and each state carries its own `bob`
   * amplitude. Crossing every state's bob with the speaking-only terms would explore poses the
   * rig can never actually reach and report a failure that isn't real.
   * The mouth value itself is swept 0..1 for every state, because the mouth slider overrides
   * mouthBase in any state.
   */
  function pose(stateName, mouth, bobPh, talkPh, swayPh) {
    const S = STATES[stateName];
    const speaking = stateName === "speaking";
    const jawY = Math.min(JAW.syA + mouth * JAW.syB, JAW.maxSy);
    const jawX = 1 + (JAW.rest - mouth) * JAW.narrow;

    const breathe = bobPh * 0.012 * S.bob;
    const talkPunch = speaking ? (mouth - 0.30) * 0.030 : 0;
    const sy = 1 - breathe - talkPunch;
    const BRX = BALL_IN * (1 + breathe + talkPunch), BRY = BALL_IN * sy;

    const talkBob = speaking ? talkPh * JAW.bob * mouth : 0;
    const jawDX = speaking ? swayPh * JAW.sway * mouth : 0;
    const jawDY = (mouth - JAW.rest) * JAW.drop + talkBob;

    let clear = Infinity, worstPt = null;
    for (const [x0, y0] of mawPts) {
      // translate(jawDX,jawDY) · translate(Jx,Jp) · scale(jawX,jawY) · translate(-Jx,-Jp)
      const x = JAW.x + (x0 - JAW.x) * jawX + jawDX;
      const y = JAW.pivot + (y0 - JAW.pivot) * jawY + jawDY;
      const c = clearanceInEllipse([x, y], ball.cx, ball.cy, BRX, BRY);
      if (c < clear) { clear = c; worstPt = [x, y]; }
    }
    return { clear, worstPt, sy };
  }

  const PH = [-1, 0, 1];
  let min = Infinity, at = null;
  const rows = [];

  for (let mouth = 0; mouth <= 1.0001; mouth += 0.005) {
    let rowWorst = null;
    for (const s of Object.keys(STATES)) {
      for (const bobPh of PH) for (const talkPh of PH) for (const swayPh of PH) {
        const p = pose(s, mouth, bobPh, talkPh, swayPh);
        if (p.clear < min) { min = p.clear; at = { state: s, mouth: +mouth.toFixed(3), sy: +p.sy.toFixed(4) }; }
        if (!rowWorst || p.clear < rowWorst.clear) rowWorst = { ...p, state: s };
      }
    }
    if ([0.55, 0.88, 1.0].some((m) => Math.abs(mouth - m) < 0.0025)) {
      rows.push({
        mouth: mouth.toFixed(2), state: rowWorst.state,
        at: `${rowWorst.worstPt[0].toFixed(0)},${rowWorst.worstPt[1].toFixed(0)}`,
        clear: rowWorst.clear.toFixed(1),
      });
    }
  }

  worst["mouth inside silhouette"] = min;
  check(min > 0, "mouth never breaks the ball silhouette (ball squash included)",
    `worst margin ${min.toFixed(1)}px at mouth=${at.mouth} in "${at.state}" ballSy=${at.sy}`);

  // Clearance is measured radially against the ball's live squashed ellipse, at whichever
  // point on the mouth outline comes closest. Comparing the mouth's LOWEST y against the ball
  // edge at x=500 (the old measure) misses the true worst case: rotate(-6) drops the mouth's
  // left side, so it comes nearest the silhouette off-centre, where the ball has curved in.
  console.log("\n  mouth containment, measured — ball squash included, closest point:");
  console.log("  ┌────────┬────────────┬───────────────┬────────────┐");
  console.log("  │ mouth  │ state      │ closest at    │  clearance │");
  console.log("  ├────────┼────────────┼───────────────┼────────────┤");
  for (const r of rows) {
    console.log(`  │ ${r.mouth.padStart(6)} │ ${r.state.padEnd(10)} │ ${r.at.padStart(13)} │ ${(r.clear + "px").padStart(10)} │`);
  }
  console.log("  └────────┴────────────┴───────────────┴────────────┘");
}

/* ---- 4c. teeth net scale is exactly 1 — asserts D10's inverse transform.
   teethAnti is a child of mouthG, so a point is transformed by A, then by M. M·A must be I. */
{
  let maxErr = 0;
  const probes = [[493, 570], [173, 570], [813, 570], [493, 442], [493, 698], [250, 500]];
  for (let mouth = 0; mouth <= 1.0001; mouth += 0.01) {
    const jawY = Math.min(JAW.syA + mouth * JAW.syB, JAW.maxSy);
    const jawX = 1 + (JAW.rest - mouth) * JAW.narrow;
    for (const talkPh of [-1, 0, 1]) {
      const talkBob = talkPh * JAW.bob * mouth;
      const jawDX = talkPh * JAW.sway * mouth;
      const jawDY = (mouth - JAW.rest) * JAW.drop + talkBob;
      for (const [x0, y0] of probes) {
        // A: translate(Jx,Jp) scale(1/jawX,1/jawY) translate(-Jx,-Jp) translate(-jawDX,-jawDY)
        let x = x0 - jawDX, y = y0 - jawDY;
        x = JAW.x + (x - JAW.x) / jawX;
        y = JAW.pivot + (y - JAW.pivot) / jawY;
        // M: translate(jawDX,jawDY) translate(Jx,Jp) scale(jawX,jawY) translate(-Jx,-Jp)
        x = JAW.x + (x - JAW.x) * jawX + jawDX;
        y = JAW.pivot + (y - JAW.pivot) * jawY + jawDY;
        maxErr = Math.max(maxErr, Math.hypot(x - x0, y - y0));
      }
    }
  }
  worst["teeth net scale error"] = maxErr;
  check(maxErr < 1e-9, "upper teeth net transform is exactly identity (D10)",
    `max drift ${maxErr.toExponential(2)}px`);
}

/* ---- 4d. eyes stay inside the silhouette. Both live under #headG so tilt/bob cancel,
   but the ball squashes and the eyes do not. */
{
  // The rig clamps the eye to EYE.maxScale precisely because gesture offsets stack on top of
  // state values, so that ceiling — not the raw sum — is what is actually reachable.
  const gEye = GESTURES ? Math.max(0, ...Object.values(GESTURES).map((g) => g.eye ?? 0)) : 0;
  const rawMax = Math.max(...Object.values(STATES).map((s) => s.eye)) + gEye;
  const maxEye = Math.min(rawMax, EYE.maxScale ?? Infinity);
  const maxBob = Math.max(...Object.values(STATES).map((s) => s.bob));

  check(EYE.maxScale !== undefined && rawMax > EYE.maxScale,
    "the eye ceiling is doing work (states+gestures exceed it, so it is load-bearing)",
    `raw max ${rawMax.toFixed(2)} clamped to ${EYE.maxScale}`);
  const EYE_OUT_RX = eyeL.rx + eyeL.stroke / 2;
  const EYE_OUT_RY = eyeL.ry + eyeL.stroke / 2;

  let min = Infinity, at = null;
  for (const cx of [EYE.lx, EYE.rx]) {
    for (let es = 0; es <= maxEye + 1e-9; es += 0.02) {
      for (const bobPh of [-1, 0, 1]) {
        const breathe = bobPh * 0.012 * maxBob;
        const BRX = BALL_IN * (1 + breathe), BRY = BALL_IN * (1 - breathe);
        for (const pt of ellipsePoints(cx, EYE.cy, EYE_OUT_RX, EYE_OUT_RY * es, 360)) {
          const c = clearanceInEllipse(pt, ball.cx, ball.cy, BRX, BRY);
          if (c < min) { min = c; at = { cx, eyeScale: +es.toFixed(2) }; }
        }
      }
    }
  }
  worst["eyes inside silhouette"] = min;
  check(min > 0, "eyes never break the ball silhouette",
    `worst margin ${min.toFixed(1)}px at eye x=${at.cx} scale=${at.eyeScale}`);
}

/* ---- 4e. locomotion keeps him inside the viewBox.
   He is r=468 in a 1000-unit box — 94% of the frame — so any travel needs the zoom-out the
   gesture layer applies. This asserts that pairing holds for every sample of every gesture. */
if (GESTURES) {
  const [VW, VH] = VIEWBOX;
  let min = Infinity, at = null;

  for (const [name, g] of Object.entries(GESTURES)) {
    if (!g.travel && !g.rise && typeof g.sample !== "function") continue;
    for (let i = 0; i <= 2000; i++) {
      const p = i / 2000;
      let tx, ty, zoom;
      if (typeof g.sample === "function") {
        // A scripted gesture supplies its own motion, so drive the REAL sampler rather than
        // reimplementing it — otherwise this check silently stops covering the set-pieces.
        const S = { tx:0, ty:0, spin:0, zoom:1, tapeL:0, tapeR:0, prop:0 };
        g.sample(p, S);
        ({ tx, ty, zoom } = S);
      } else {
        // Mirrors sampleGesture() in face-preview.html — keep the two in step.
        tx = (g.travel ?? 0) * Math.sin(2 * Math.PI * p);
        ty = -(g.rise ?? 0) * (0.5 - 0.5 * Math.cos(2 * Math.PI * (g.bounces ?? 1) * p))
                            * Math.exp(-(g.damp ?? 0) * p);
        // zoom keys off normalised DISPLACEMENT, not the clock, so he is smallest exactly
        // when he is furthest out. Keying it to the clock left him oversized at the extremes.
        const dn = Math.hypot(g.travel ? tx / g.travel : 0, g.rise ? ty / g.rise : 0);
        zoom = 1 - (1 - (g.zoom ?? 1)) * Math.min(1, dn);
      }
      const R = BALL_OUT * zoom;   // spin is irrelevant to a circular bound
      const cx = ball.cx + tx, cy = ball.cy + ty;
      const m = Math.min(cx - R, cy - R, VW - (cx + R), VH - (cy + R));
      if (m < min) { min = m; at = { gesture: name, p: +p.toFixed(2) }; }
    }
  }

  if (at) {
    worst["locomotion inside viewBox"] = min;
    check(min >= 0, "roll and bounce keep him inside the viewBox",
      `worst margin ${min.toFixed(1)}px during "${at.gesture}" at t=${at.p}`);
  }
}

/* ---- 4f. the sleep Zs stay on screen for as long as they can actually be seen.
   They drift up and out of frame by design, but each fades to nothing at the end of its
   travel — so the guarantee is bounded by visibility, not by position alone. */
{
  const Z = parseObjectLiteral("ZZZ", { required: false });
  if (Z) {
    const [VW, VH] = VIEWBOX;
    const VISIBLE = 0.05;
    let min = Infinity, at = null;
    for (let i = 0; i <= 2000; i++) {
      const ph = i / 2000;
      const op = Math.sin(Math.PI * ph);
      if (op < VISIBLE) continue;
      const s = Z.s0 + (Z.s1 - Z.s0) * ph;
      const cx = Z.x + Z.dx * ph, cy = Z.y + Z.dy * ph, h = Z.half * s;
      const m = Math.min(cx - h, cy - h, VW - (cx + h), VH - (cy + h));
      if (m < min) { min = m; at = { ph: +ph.toFixed(2), op: +op.toFixed(2) }; }
    }
    worst["zzz on screen while visible"] = min;
    check(min >= 0, "the sleep Zs stay in frame for as long as they are visible",
      `worst margin ${min.toFixed(1)}px at phase ${at.ph} (opacity ${at.op})`);
  }
}

/* ---- 4g. thought marks: on screen, and clear of his own silhouette.
   Unlike the Zs these hold position, so the bound is absolute — worst case is the slot at
   full pulse and full bob. They also have to sit OUTSIDE the ball, or a question mark ends up
   pasted across his forehead instead of floating beside it. */
{
  const TH = parseObjectLiteral("THINK", { required: false });
  if (TH) {
    const [VW, VH] = VIEWBOX;
    // The marks are only ever drawn by states that also shrink him, so the silhouette they
    // have to clear is the SHRUNKEN one. Take the least generous zoom among those states.
    const thinking = Object.values(STATES).filter((s) => (s.think ?? 0) > 0);
    const zoom = thinking.length ? Math.max(...thinking.map((s) => s.zoom ?? 1)) : 1;
    const R = BALL_OUT * zoom;

    let minFrame = Infinity, minClear = Infinity, atF = null, atC = null;
    TH.slots.forEach((sl, i) => {
      const reach = TH.half * sl.s * (1 + TH.pulse);
      for (const d of [-TH.bob, 0, TH.bob]) {
        const cx = sl.x, cy = sl.y + d;
        const f = Math.min(cx - reach, cy - reach, VW - (cx + reach), VH - (cy + reach));
        if (f < minFrame) { minFrame = f; atF = i; }
        // Full clearance, not just the centre: a mark that overlaps his face is pasted ON
        // him rather than floating around him, which is the whole point of the layout.
        const c = Math.hypot(cx - ball.cx, cy - ball.cy) - reach - R;
        if (c < minClear) { minClear = c; atC = i; }
      }
    });

    worst["thought marks in frame"] = minFrame;
    worst["thought marks clear of face"] = minClear;
    check(minFrame >= 0, "thought marks stay inside the viewBox",
      `worst margin ${minFrame.toFixed(1)}px at slot ${atF}`);
    check(minClear > 0, "thought marks clear his silhouette entirely",
      `worst gap ${minClear.toFixed(1)}px at slot ${atC}, against a ${zoom}× silhouette`);
  }
}

/* ============================================================ report */

const failed = results.filter((r) => !r.ok);
let last = "";
console.log("");
for (const r of results) {
  if (r.section !== last) { console.log(`  ${r.section}`); last = r.section; }
  console.log(`   ${r.ok ? "PASS" : "FAIL"}  ${r.msg}${r.detail ? `\n           ${r.detail}` : ""}`);
}

if (Object.keys(worst).length) {
  console.log("\n  worst-case margins");
  for (const [k, v] of Object.entries(worst)) {
    console.log(`   ${k.padEnd(30)} ${typeof v === "number" && v < 1e-6 ? v.toExponential(2) : v.toFixed(1) + "px"}`);
  }
}

console.log(`\n  ${results.length - failed.length}/${results.length} checks passed` +
  (GESTURES ? "" : "  (no GESTURES table found — locomotion checks skipped)"));
console.log("");
process.exit(failed.length ? 1 : 0);
