#!/usr/bin/env python3
"""
Module:  local_router.py
Purpose: Route a question with a model on THIS machine, and make invalid JSON unreachable.
Author:  LB
Date:    2026-09-10

    setx ODDBALL_LOCAL_ROUTER qwen2.5:1.5b     # then restart the shell, and him

## Why the router is the leg worth moving

`engine/models.py` describes the router job in one line — *"a 9-way classification with a fixed
schema. No reasoning, no long output."* There are eleven routes now and the sentence is still
true, and it is a description of the cheapest possible LLM call: eleven candidate strings and a
sentence nobody hears out loud. Paying a network round trip and a slice of a 20-a-day free tier
for it is the worst trade in the system.

It is also the leg with the worst measured tail. From `data/oddball.log`, 2026-08-29, four
routes on `gemini-3.5-flash-lite` that all eventually returned **HTTP 200**:

    "organize the STL files"       90,984 ms
    "add to my note"             126,844 ms
    "read me back the notes"     162,453 ms
    "tell me what notes"         285,985 ms

`ROUTER_DEADLINE_S` in `engine/core.py` exists to survive that. A model on the desk cannot
produce it: there is no network in the path to be slow.

## Ollama's NATIVE endpoint, not its OpenAI-compatible one

`engine/models.py` reaches Ollama through `ChatOpenAI` pointed at `/v1`, and says why — no new
client, no second code path. This module deliberately does **not** do that, and the reason is
the entire point of the file.

The OpenAI-compatible surface takes `response_format`, which on most backends is a *request*:
the model is asked for JSON and usually complies. Ollama's native `/api/chat` takes a JSON
schema in **`format`**, and compiles it into a decoding grammar. That is a different kind of
guarantee. Under a grammar, the tokens that spell `Here is the JSON:` are not discouraged — they
are **not in the sampler's candidate set at all**. Neither is a route name that is not in the
enum, or a markdown fence, or a fourth key.

For a 1.5B model this is the difference between a router and a liability. `engine/core.py`
treats a router exception as a failed turn, so one conversational preamble is one turn LB does
not get an answer to.

So: `requests` against `http://127.0.0.1:11434/api/chat`, no new dependency either way, and the
`/v1` suffix in `ODDBALL_LOCAL_ROUTER_BASE_URL` is stripped if present — that URL is the one
already documented in `engine/models.py`, and asking LB to remember which endpoint takes which
parameter is asking him to get it wrong once.

## The enum is generated, never typed

`ROUTE_SCHEMA` builds `destination.enum` from `AgentRoute` itself. A route added to the enum is
in the grammar on the next call with nothing else edited, and — more to the point — a route
*removed* cannot linger here as a value the grammar still permits. `tools/verify_local_router.py`
asserts the two are equal, because "generated from" is a claim that stops being true the first
time somebody hardcodes a list "just for now".

## Import direction, stated because it is load-bearing

This module imports `router` at module scope. `router` imports **this** module lazily, inside
the function that needs it. That asymmetry is what keeps the cycle from closing, and it only
works in that direction: `router.py` must never import this at module scope.
"""

from __future__ import annotations

import logging
import os
import time

import requests

from router import LOCAL_ROUTER_VAR, AgentRoute, ROUTER_PROMPT, RouteDecision

LOG = logging.getLogger("oddball.local_router")

__all__ = ["LocalRouterError", "local_router_model", "native_base_url", "route_schema",
           "build_payload", "route_locally", "warm", "timeout_s", "CONNECT_TIMEOUT_S",
           "KEEP_ALIVE"]


class LocalRouterError(RuntimeError):
    """The local router could not answer. Carries text a person can act on.

    A distinct type rather than a bare RuntimeError because `router.router_agent` catches
    exactly this to decide whether falling back to Gemini is appropriate. A `KeyboardInterrupt`
    or a bug in this module is not a reason to spend a free-tier request.
    """


# The toggle. Opt-in **by model name**, mirroring `ODDBALL_PERSONA_MODEL` in engine/models.py,
# and for the reason recorded there at length: that constant was defaulted to switch provider on
# the mere presence of a key, it switched by accident, and the failure was silent on the one
# route that files every upload. A name is a decision; a key being present is not.
def local_router_model() -> str:
    """The Ollama model name to route with, or "" when the router stays on Gemini.

    Read on every call rather than captured at import, so a harness can set the variable and a
    test can unset it without reloading the module. The cost is one dict lookup per turn.

    The variable's NAME lives in `router.py` and is imported, so `router.router_provider()` can
    decide whether to route locally without importing this module at all. That matters more
    than it looks: `router_provider()` runs on every turn of every rig, and importing this file
    to answer it would run this module's import-time code on machines that have opted out.
    """
    return os.environ.get(LOCAL_ROUTER_VAR, "").strip()


def native_base_url() -> str:
    """Ollama's native API root, with any `/v1` suffix removed.

    `engine/models.py` documents `http://127.0.0.1:11434/v1` as the local endpoint, because the
    persona reaches it through an OpenAI-compatible client. Structured output lives one level up
    at `/api/chat`, so the same URL LB already has in his notes has to work here too. Accepting
    both spellings is one `removesuffix`; making him remember which leg wants which is a support
    question every time he sets up a new box.
    """
    url = os.environ.get("ODDBALL_LOCAL_ROUTER_BASE_URL", "http://127.0.0.1:11434/v1").strip()
    return url.rstrip("/").removesuffix("/v1").rstrip("/")


# How long to wait for the TCP connection. Separate from the read timeout below because
# `requests` applies its `timeout=` to each socket operation independently, not to the call as
# a whole — so a single float means the worst case is twice what it looks like. Connecting to
# something on this machine either works immediately or is not there.
CONNECT_TIMEOUT_S = 3.0


def _router_deadline_s() -> float:
    """`engine.core.ROUTER_DEADLINE_S`, or 20.0 if the engine is not importable.

    Imported inside the function on purpose. This module is imported BY the routing path, and a
    module-scope `from engine.core import ...` would drag the whole engine into any process that
    only wanted to build a payload — `tools/router_gym_corpus.py` among them.
    """
    try:
        from engine.core import ROUTER_DEADLINE_S                      # noqa: PLC0415
        return float(ROUTER_DEADLINE_S)
    except Exception:                                                  # noqa: BLE001
        return 20.0


def timeout_s() -> float:
    """How long one local classification may take. **Always less than the router deadline.**

    ## The bug this function exists to prevent

    This was a constant, `float(os.environ.get(..., "30"))`, and 30 is longer than
    `ROUTER_DEADLINE_S`, which is 20. That ordering is not a wasted ten seconds; it is a bill.

    `engine/core.py:_route_within_deadline` gives up at 20s and **abandons** the thread rather
    than killing it — it cannot cancel a blocking call. The turn goes to GENERAL and LB gets an
    answer. Ten seconds later the abandoned thread's request finally times out, `router_agent`
    catches `LocalRouterError`, sees that strict mode is off, and makes a **real, billed Gemini
    call whose answer is discarded**. On a cold model that is every turn until the model is
    resident, and the `LOG.warning` lands in `data/oddball.log` attached to the *next* turn —
    which, in a repo whose first rule is to read the log before theorising, is worse than
    silence.

    So the local call must fail while the deadline still owns the turn. Two seconds of headroom
    for the fallback to be worth attempting at all.

    A parsing failure falls back to the default rather than raising: this is read on the turn
    path, and a typo in an environment variable must not be able to break every turn on a rig
    whose owner never opted into the feature.
    """
    ceiling = max(1.0, _router_deadline_s() - 2.0)
    raw = os.environ.get("ODDBALL_LOCAL_ROUTER_TIMEOUT_S", "")
    try:
        wanted = float(raw) if raw.strip() else ceiling
    except ValueError:
        LOG.warning("ODDBALL_LOCAL_ROUTER_TIMEOUT_S=%r is not a number; using %.0fs",
                    raw, ceiling)
        return ceiling
    return min(wanted, ceiling)

# How long Ollama keeps the model resident after a call. **This is the constant that decides
# whether local routing is fast.** Ollama's default is 5 minutes; a desk assistant is idle for
# longer than that all the time, so the default would mean LB pays the cold-load cost on the
# first question after every coffee. 1.5B at Q4 is roughly a gigabyte of the 32 GB in this box.
KEEP_ALIVE = os.environ.get("ODDBALL_LOCAL_ROUTER_KEEP_ALIVE", "30m")


def route_schema() -> dict:
    """The JSON schema Ollama compiles into a decoding grammar.

    `destination.enum` is generated from `AgentRoute`, so the grammar and the enum cannot drift.

    ## Why `destination` is the first property

    Ollama's grammar emits object keys in schema order, so this decides what the model commits
    to first. Putting `reasoning` first would give a 1.5B a few tokens of scratch space to think
    in, which is the usual argument for reasoning-before-answer — and it was rejected here for
    two reasons. It puts the latency of a sentence in front of every route on the turn path,
    which is the cost this whole file exists to remove. And the sentence is not read by anything
    except a log line: `engine/core.py:836` prints it and nothing consumes it. Scratch space
    that nobody reads is not reasoning, it is delay.

    If the gym ever measures the order as worth it, this docstring is where the reversal gets
    argued, and `tools/evaluate_local_router.py` is what would have to show it.
    """
    return {
        "type": "object",
        "properties": {
            "destination": {
                "type": "string",
                "enum": [route.value for route in AgentRoute],
                "description": RouteDecision.model_fields["destination"].description,
            },
            "reasoning": {
                "type": "string",
                "description": RouteDecision.model_fields["reasoning"].description,
            },
        },
        "required": ["destination", "reasoning"],
    }


def build_payload(query: str, model: str | None = None) -> dict:
    """The exact body POSTed to `/api/chat`. Separated so a harness can assert on it offline.

    ## The prompt is Gemini's prompt, byte for byte

    `ROUTER_PROMPT` is sent as a single user message, because that is precisely what
    `ChatPromptTemplate.from_template(...) | llm` sends: `from_template` builds one
    HumanMessagePromptTemplate and nothing else. Splitting it into a system message and a user
    message would probably suit a small model better — and it would mean
    `tools/evaluate_local_router.py` was comparing two prompts rather than two models, which is
    the one thing that evaluation must not do.

    Nothing here asks for JSON, and that is not an oversight. Under `format` the model cannot
    produce anything else, so a "respond only with JSON" instruction would be spending tokens
    of a 1.5B's attention to forbid something already impossible.
    """
    return {
        "model": model or local_router_model(),
        "messages": [{"role": "user", "content": ROUTER_PROMPT.format(question=query)}],
        "format": route_schema(),
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {
            # Matching `router.py`'s ChatGoogleGenerativeAI(temperature=0.0). Classification
            # wants the argmax, and a route that changes between two identical questions is a
            # bug LB would have to reproduce to believe.
            "temperature": 0.0,
            # The grammar guarantees the SHAPE, not the length: `reasoning` is a free string and
            # a small model will happily fill it with a paragraph. This caps the whole object.
            # 200 tokens is comfortable for a route plus one sentence and refuses an essay.
            "num_predict": 200,
        },
    }


def route_locally(query: str, model: str | None = None) -> RouteDecision:
    """Classify `query` with the local model. Raises LocalRouterError if it cannot.

    Args:
        query: the user's utterance, as transcribed.
        model: override the model name; defaults to `ODDBALL_LOCAL_ROUTER`.

    Returns:
        A `RouteDecision` — the same type `router_agent` returns from Gemini, so nothing
        downstream can tell which model produced it.

    Raises:
        LocalRouterError: server unreachable, model not pulled, HTTP error, timeout, or a body
            that did not parse. Every message names the fix, because the most likely cause by
            far is that `ollama serve` is not running.
    """
    name = model or local_router_model()
    if not name:
        raise LocalRouterError(
            "ODDBALL_LOCAL_ROUTER is not set, so there is no local model to route with. "
            "Set it to a model name you have pulled, e.g. `setx ODDBALL_LOCAL_ROUTER qwen2.5:1.5b`.")

    url = f"{native_base_url()}/api/chat"
    started = time.monotonic()

    try:
        reply = requests.post(url, json=build_payload(query, name),
                              timeout=(CONNECT_TIMEOUT_S, timeout_s()))
    except requests.exceptions.ConnectionError as exc:
        raise LocalRouterError(
            f"nothing is listening at {url}. Start the server with `ollama serve`, or unset "
            f"ODDBALL_LOCAL_ROUTER to put routing back on Gemini.") from exc
    except requests.exceptions.Timeout as exc:
        raise LocalRouterError(
            f"{name} did not answer within {timeout_s():g}s. A cold model can take that long on "
            f"its first call — call warm() at startup so the turn path never pays for the load. "
            f"If it happens on every call the model is too big for this box.") from exc
    except requests.exceptions.RequestException as exc:          # noqa: BLE001 - reported, not hidden
        raise LocalRouterError(f"the request to {url} failed: {exc}") from exc

    if reply.status_code == 404:
        # Ollama's own words for this are "model 'x' not found, try pulling it first", which is
        # correct and easy to miss inside a stack trace. Said plainly instead.
        raise LocalRouterError(
            f"Ollama does not have a model called {name!r}. Pull it with `ollama pull {name}`, "
            f"or check the name with `ollama list`.")
    if not reply.ok:
        raise LocalRouterError(f"{url} returned HTTP {reply.status_code}: {reply.text[:200]}")

    try:
        body = reply.json()
        content = body["message"]["content"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LocalRouterError(
            f"the reply from {url} was not an Ollama chat response: {reply.text[:200]}") from exc

    # Under `format` this parse cannot fail — which is exactly why it is checked rather than
    # assumed. The claim this module makes is that the grammar holds; an unguarded parse here
    # would turn a broken claim into a traceback three frames from where it was made.
    try:
        decision = RouteDecision.model_validate_json(content)
    except ValueError as exc:
        # **Two different faults reach this line and they have opposite fixes.** The grammar
        # bounds the SHAPE of the object, not its length, so a chatty model that fills
        # `reasoning` until it hits `num_predict` returns a JSON object cut off mid-string —
        # valid generation, invalid JSON. Ollama says which happened in `done_reason`, and
        # without reading it this branch used to tell LB to upgrade Ollama over a number in a
        # config file. Wrong advice costs more than no advice: it sends him to fix the one
        # thing that is not broken.
        if body.get("done_reason") == "length":
            raise LocalRouterError(
                f"{name} hit the {build_payload('', name)['options']['num_predict']}-token cap "
                f"mid-object, so the JSON was truncated rather than malformed. Its `reasoning` "
                f"ran long — raise num_predict in build_payload, or pick a less chatty model. "
                f"This is not an Ollama version problem.") from exc
        raise LocalRouterError(
            f"{name} returned something the schema should have made impossible: {content[:200]}. "
            f"Check that this Ollama build supports `format` as a JSON schema (0.5.0 and later)."
        ) from exc

    # **DEBUG, not INFO, and deliberately.** `engine/core.py` logs the routing decision for
    # every turn already, and marks this branch itself. Logging at INFO here would put two
    # route lines in `data/oddball.log` for one turn — which is not merely noise: the gym reads
    # that file for ground truth, and a duplicated decision would be one utterance counted
    # twice. The milliseconds are not lost either; `Turnlog.route_s` records the router leg and
    # prints it on the `turn:` line.
    LOG.debug("route %r -> %s (local %s, %.0fms, no api call)",
              query, decision.destination.value, name, (time.monotonic() - started) * 1000)
    return decision


def warm(model: str | None = None) -> float | None:
    """Load the model into RAM ahead of the first question. Returns seconds taken, or None.

    Never raises. A rig that starts before `ollama serve` does should come up and route on
    Gemini, not refuse to start — the same call this file's docstring makes about fallback.

    Worth calling from engine startup for the reason `engine/core.py` warms the embedding model
    at line 21 of the log: the first question should not be the one that pays for the load.
    """
    name = model or local_router_model()
    if not name:
        return None

    started = time.monotonic()
    try:
        # An empty `messages` list is Ollama's documented way to load a model without generating
        # anything. It costs the load and no tokens.
        reply = requests.post(f"{native_base_url()}/api/chat",
                              json={"model": name, "messages": [], "keep_alive": KEEP_ALIVE},
                              timeout=(CONNECT_TIMEOUT_S, timeout_s()))
        reply.raise_for_status()
    except requests.exceptions.RequestException as exc:          # noqa: BLE001 - logged, not fatal
        LOG.warning("could not warm the local router (%s); the first route will pay for the "
                    "load, or fall back to Gemini: %s", name, exc)
        return None

    took = time.monotonic() - started
    LOG.info("local router %s warm in %.2fs (resident for %s)", name, took, KEEP_ALIVE)
    return took
