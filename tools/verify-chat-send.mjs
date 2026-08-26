#!/usr/bin/env node
/**
 * Module: verify-chat-send.mjs
 * Purpose: Prove a typed line reaches the engine — including after the socket has reconnected.
 * Author:  LB
 * Date:    2026-08-24
 * Usage:   node tools/verify-chat-send.mjs [path/to/face-preview.html]
 *
 * Why this exists
 * ---------------
 * LB reported that typing into the chat panel "doesn't always trigger a response". It was not
 * a queue, a priority or a delay — the typed line was never leaving the browser.
 *
 * `wireChat(ws)` is called on every `onopen`, but each block inside it is guarded by a
 * `dataset.wired` flag so its listeners attach exactly once. The submit handler therefore
 * closed over the `ws` it was wired with — the FIRST one — and kept sending into it forever.
 * After any reconnect (`systemctl restart oddball`, a Wi-Fi blink, a closed lid) every typed
 * line went into a dead socket. `send()` on a CLOSED socket does not throw, the spec says to
 * discard the data, so the `catch {}` never fired and the line had already been echoed into the
 * transcript. It looked exactly like he had received the question and ignored it.
 *
 * Section 2 is the regression: wire on socket A, reconnect onto socket B, and assert the line
 * arrives at **B**. It fails against the old code and passes against the new.
 *
 * Zero dependencies, Node >= 18. The rig's own source is executed — the wiring is sliced out of
 * face-preview.html and run in a `vm` against stub DOM and a stub WebSocket, the same technique
 * verify-rig.mjs uses for the transform tables. Testing a copy of this logic would test a copy.
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

let passed = 0, failed = 0;

function section(name) { console.log(`\n${name}`); }

function check(ok, what, detail = "") {
  if (ok) { passed++; console.log(`   PASS  ${what}`); }
  else { failed++; console.log(`   FAIL  ${what}${detail ? `\n         ${detail}` : ""}`); }
}

/* ------------------------------------------------------- slicing the rig apart */

/** Return the source from `start` through the end of the brace-balanced block it opens. */
function blockFrom(src, start) {
  const open = src.indexOf("{", start);
  if (open < 0) throw new Error("no block opens after the marker");
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    const c = src[i];
    // Good enough for this file: the sliced region has no braces inside string literals or
    // regexes that are unbalanced. If that ever changes, this throws rather than lying.
    if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error("unbalanced braces");
}

const wireChatAt = SRC.indexOf("function wireChat(ws) {");
if (wireChatAt < 0) {
  console.error("Cannot find `function wireChat(ws) {` in the rig — has hud/face-preview.html " +
                "been restructured?");
  process.exit(1);
}
// The preamble (`let socket`, `send`, `notConnected`) is taken if it is there and skipped if it
// is not. That is deliberate: it lets this suite run against an OLD copy of the rig, which is
// the only way to know these checks fail on the bug rather than passing on everything —
//
//     git show HEAD~1:hud/face-preview.html > /tmp/old.html && node tools/verify-chat-send.mjs /tmp/old.html
//
// Sections 2 and 3 fail there, which is what makes them a regression test and not decoration.
const socketDecl = SRC.lastIndexOf("let socket = null;", wireChatAt);
const WIRING = (socketDecl >= 0 ? SRC.slice(socketDecl, wireChatAt) : "")
             + blockFrom(SRC, wireChatAt);

/* ---------------------------------------------------------------- the stub DOM */

function makeEl(id) {
  return {
    id, value: "", hidden: false, disabled: false, textContent: "", dataset: {}, files: null,
    _l: {},
    addEventListener(type, fn) { (this._l[type] ??= []).push(fn); },
    fire(type, ev) { for (const fn of this._l[type] ?? []) fn(ev ?? { preventDefault() {} }); },
    click() { this.fire("click"); },
    scrollIntoView() {},
  };
}

/** A WebSocket that behaves like the real one where it matters: how a dead one fails. */
class FakeSocket {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  constructor(name) { this.name = name; this.readyState = FakeSocket.OPEN; this.sent = []; }
  send(data) {
    // The two failure modes are NOT the same, and that asymmetry is the bug being tested.
    if (this.readyState === FakeSocket.CONNECTING) throw new Error("InvalidStateError");
    if (this.readyState !== FakeSocket.OPEN) return;      // CLOSING/CLOSED: discarded, silently
    this.sent.push(JSON.parse(data));
  }
  close() { this.readyState = FakeSocket.CLOSED; }
}

function newHarness() {
  const els = new Map();
  const cards = [], transcript = [], states = [];
  const ctx = {
    WebSocket: FakeSocket,
    LINK_RETRY_MS: 2000,
    UPLOAD_URL: "http://127.0.0.1:8767/upload",
    UPLOAD_MAX_BYTES: 64 * 1024 * 1024,
    fetch: async () => ({ ok: true, json: async () => ({ ok: true, filename: "x.pdf" }) }),
    FormData: class { append() {} },
    console,
    $: (id) => { if (!els.has(id)) els.set(id, makeEl(id)); return els.get(id); },
    setState: (name) => states.push(name),
    chatMessage: (m) => {
      if (m.type === "card") cards.push(m.value);
      else if (m.type === "transcript") transcript.push(m.value);
    },
  };
  vm.createContext(ctx);
  vm.runInContext(WIRING, ctx, { filename: "face-preview.html#wireChat" });
  return {
    ctx, cards, transcript, states,
    el: ctx.$,
    connect(name) { const ws = new FakeSocket(name); ctx.wireChat(ws); return ws; },
    type(text) {
      const input = ctx.$("chatInput");
      input.value = text;
      ctx.$("chatForm").fire("submit");
    },
  };
}

/* --------------------------------------------------------------- 1. the basics */

section("1. a typed line reaches the socket that is live");
{
  const h = newHarness();
  const a = h.connect("A");
  h.type("how wide for 3 amps");

  check(a.sent.length === 1 && a.sent[0].type === "text",
        "Enter sends one {type:'text'} message",
        `got ${JSON.stringify(a.sent)}`);
  check(a.sent[0]?.value === "how wide for 3 amps", "the message carries the text typed");
  check(h.el("chatInput").value === "", "the box is cleared once the line is on the wire");
  check(h.transcript.includes("how wide for 3 amps"), "the line is echoed into the transcript");
  // Request 2 of the report: the UI must switch to thinking instantly rather than a round trip
  // later. `engine/turn.py:answer_typed` sets it too and remains the authority.
  check(h.states.includes("thinking"), "the thinking pose is set on the same frame as the send",
        `states seen: ${JSON.stringify(h.states)}`);
}

/* ------------------------------------------------------------ 2. the regression */

section("2. and it still reaches it AFTER a reconnect  <- the reported bug");
{
  const h = newHarness();
  const a = h.connect("A");
  h.type("first");
  check(a.sent.length === 1, "the first socket got the first line");

  // Exactly what the page does on its own: onclose -> connect() -> onopen -> wireChat(newWs).
  a.close();
  const b = h.connect("B");
  h.type("second");

  check(b.sent.length === 1 && b.sent[0].value === "second",
        "a line typed after reconnecting arrives at the NEW socket",
        `socket B received ${JSON.stringify(b.sent)} — this is the check that fails against ` +
        `a handler that closed over its own ws parameter`);
  check(a.sent.length === 1, "and nothing was posted into the dead one",
        `socket A received ${JSON.stringify(a.sent)}`);
  check(h.el("chatInput").value === "", "the box is cleared, so the send really did happen");
}

/* ------------------------------------------- 3. a failed send is never silent */

section("3. a line that cannot be sent says so, and is not thrown away");
{
  const h = newHarness();
  const a = h.connect("A");
  a.close();                       // no reconnect: the engine is down, the page is still open
  h.type("what time is it");

  check(a.sent.length === 0, "nothing is sent into a closed socket");
  check(h.el("chatInput").value === "what time is it",
        "the text stays in the box, so it does not have to be retyped",
        `box holds ${JSON.stringify(h.el("chatInput").value)}`);
  check(!h.transcript.includes("what time is it"),
        "and it is NOT echoed as though he had received it");
  check(h.cards.some((c) => c.kind === "error" && /not connected/i.test(c.title ?? "")),
        "an error card explains why nothing happened",
        `cards: ${JSON.stringify(h.cards)}`);
  check(!h.states.includes("thinking"),
        "he is not left in the thinking pose over a question that never left the browser");
}

/* ------------------------------------------------------- 4. the same for a gate */

section("4. Approve/Deny holds the decision on screen until the click is on the wire");
{
  const h = newHarness();
  const a = h.connect("A");
  h.el("chatPending").hidden = false;
  h.el("btnApprove").click();
  check(a.sent.some((m) => m.type === "approve" && m.value === true),
        "a click sends {type:'approve'}");
  check(h.el("chatPending").hidden === true, "and the buttons go away once it is sent");
}
{
  const h = newHarness();
  const a = h.connect("A");
  a.close();
  h.el("chatPending").hidden = false;
  h.el("btnApprove").click();
  // A gate that gets no answer declines, in silence — engine/turn.py:_wait_for_typed_answer.
  // Hiding the buttons on a click that never left would make that look like an approval.
  check(h.el("chatPending").hidden === false,
        "a click that could not be sent leaves the buttons up to be clicked again");
  check(h.cards.some((c) => c.kind === "error"), "and says the click did not reach him");
}

/* ---------------------------------------------------------------------- verdict */

console.log(`\n${failed === 0 ? "OK" : "FAILED"}  ${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
