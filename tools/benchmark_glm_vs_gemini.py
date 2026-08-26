#!/usr/bin/env python3
"""
Module:  benchmark_glm_vs_gemini.py
Purpose: Run three of this repo's real model-facing jobs against Gemini and against
         GLM-5.2 (free, via OpenRouter), and report pass rate, latency and cost side by side.
Author:  LB
Date:    2026-08-24

    python tools/benchmark_glm_vs_gemini.py                    # both backends, 1 trial each
    python tools/benchmark_glm_vs_gemini.py --trials 3         # 3 trials, medians reported
    python tools/benchmark_glm_vs_gemini.py --task router      # one task only
    python tools/benchmark_glm_vs_gemini.py --backend glm      # skip Gemini (saves quota)
    python tools/benchmark_glm_vs_gemini.py --dry-run          # no API calls; check the suite
    python tools/benchmark_glm_vs_gemini.py --list-models      # what OpenRouter actually has

## What this measures, and why it is not a chatbot bake-off

The question is not "which model is smarter". It is **whether a free OpenRouter model can hold
one of this repo's jobs**, and those jobs are narrow, structured, and already have a definition
of correct written down somewhere in the tree. So every task here is scored against the
project's own gate, not against a rubric invented for the benchmark:

    1. ROUTER      the prompt and the ten routes come out of `router.py` itself, and the five
                   cases are five traps that file's own "Routing notes" warn about.
                   Correct = the destination this repo says is correct.
    2. SYLLABUS    `EXTRACTION_PROMPT` and `SyllabusFacts` imported from
                   `tools/syllabus_to_vault.py`. Scored on the fields AND on the two
                   fabrication guards that module exists to enforce: a fact the syllabus does
                   not state must come back EMPTY, and a due date must not be copied out.
    3. SPEAKABLE   scored by `engine.split.is_speakable()` - the exact function that decides,
                   on the turn path, whether a line reaches Piper. It returns the reason for a
                   refusal, so a failure here says *why* ("fenced code", "code identifier").

That is the whole design: a model does not "pass" because its answer reads well, it passes
because the code that would have consumed the answer accepts it.

## Running keyless, which is the normal state of the Windows box

Neither backend is required. A missing `GOOGLE_API_KEY` or `OPENROUTER_API_KEY` marks that
backend UNAVAILABLE and the other one still runs; `--dry-run` runs neither and only proves the
suite loads. This matters because the authoring machine has no `.env` by design and
`engine/models.py` raises SystemExit at import when the Gemini key is absent - so this module
imports it **lazily**, inside the Gemini backend, and reads the router prompt out of
`router.py` with `ast` rather than importing it (importing `router` builds a live
`ChatGoogleGenerativeAI` at module scope and would take the whole harness down with it).

## Cost

`z-ai/glm-5.2:free` is $0.00 and Gemini's free tier is $0.00, so a cost column of two zeroes
would say nothing. What is reported alongside it is the **shadow price**: what the same tokens
would have cost on the paid variant of each model. That is the number worth knowing, because a
free tier is the thing most likely to move. Rates in `PRICE_USD_PER_MTOK`, dated and sourced.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import platform
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
if __package__ is None and str(REPO_ROOT) not in sys.path:
    # `python tools/benchmark_glm_vs_gemini.py` puts tools/ on the path and not the repo root.
    # Same fix, same reason, as tools/syllabus_to_vault.py.
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import BaseModel, Field, create_model                   # noqa: E402

__all__ = ["main", "TASKS", "Result", "GeminiBackend", "OpenRouterBackend",
           "route_decision_model"]

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Defaults. All overridable from the command line, because the point of the exercise is to be
# able to swap the challenger without editing code.
DEFAULT_GLM_MODEL = "z-ai/glm-5.2:free"
DEFAULT_GEMINI_ROUTER_MODEL = "gemini-3.5-flash-lite"   # what router.py actually uses
DEFAULT_GEMINI_AGENT_MODEL = "gemini-3.5-flash"         # what the agents and extraction use

# USD per million tokens, (prompt, completion).
#
# The z-ai rows were read from https://openrouter.ai/api/v1/models on 2026-08-24: the `:free`
# variant is 0/0 with a 256k context, and the paid `z-ai/glm-5.2` is 0.966 / 3.036. The Gemini
# rows are 0/0 because this project runs on the free tier - see FREE_TIER_DAILY_LIMIT in
# engine/models.py - and their paid rates live in the shadow table below.
#
# These are DATA, not truth. `--list-models` re-reads the OpenRouter side live; if a number
# here and a number there disagree, the live one is right and this table is stale.
PRICE_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "z-ai/glm-5.2:free": (0.0, 0.0),
    "z-ai/glm-5.2": (0.966, 3.036),
    "gemini-3.5-flash-lite": (0.0, 0.0),
    "gemini-3.5-flash": (0.0, 0.0),
}

# What the same tokens would cost if the free tier went away. Keyed by the free model's name.
SHADOW_PRICE_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "z-ai/glm-5.2:free": (0.966, 3.036),      # the paid z-ai/glm-5.2, same date, same source
    "gemini-3.5-flash-lite": (0.10, 0.40),    # Google's published paid rates, 2026-08-24
    "gemini-3.5-flash": (0.30, 2.50),
}


# =======================================================================================
# Task 1 - routing. The prompt is READ from router.py, never re-typed.
# =======================================================================================

def read_router_prompt() -> tuple[str, list[str]]:
    """`(ROUTER_PROMPT, [route values])` lifted out of `router.py` without importing it.

    `router.py` builds a live `ChatGoogleGenerativeAI` at module scope, so importing it on a
    machine with no key raises SystemExit from `engine/models.py` and takes this harness down
    with it. Parsing the source gets the same two objects at zero cost and with no key at all -
    and it is still one source of truth: edit the prompt in `router.py` and this follows it.
    """
    tree = ast.parse((REPO_ROOT / "router.py").read_text(encoding="utf-8"))
    prompt = ""
    routes: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "ROUTER_PROMPT" for t in node.targets):
            prompt = ast.literal_eval(node.value)
        if isinstance(node, ast.ClassDef) and node.name == "AgentRoute":
            for item in node.body:
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
                    routes.append(item.value.value)
    if not prompt or not routes:
        raise SystemExit("router.py no longer defines ROUTER_PROMPT and AgentRoute as plain "
                         "literals; read_router_prompt() needs updating.")
    return prompt, routes


def route_decision_model(routes: list[str]) -> type[BaseModel]:
    """`router.RouteDecision`, rebuilt around the routes just read out of `router.py`.

    Built rather than declared so `destination` is a **closed set**, exactly as the real
    `AgentRoute` enum makes it. That is not decoration: it is most of what the router asks of a
    model. A backend that cannot honour an enum in its schema does not fail here by picking the
    wrong route, it fails by returning something that will not validate - which is precisely
    what `router.py`'s chain would do to it in production, and a fairer thing to measure.
    """
    return create_model(
        "RouteDecision",
        destination=(Literal[tuple(routes)],                            # type: ignore[valid-type]
                     Field(description="The specific agent to route the user's query to.")),
        reasoning=(str, Field(description="A brief 1-sentence explanation of the choice.")),
    )


# The five cases are five traps `router.py`'s own "Routing notes" section warns about. Every
# one of them is a question a keyword matcher gets WRONG, which is the reason that file spends
# a network round trip on routing at all. A model that only handles the easy cases has not
# replaced anything.
ROUTER_CASES: list[tuple[str, str, str]] = [
    ("upload-is-general",
     "hey I just uploaded ECE350_syllabus.pdf, can you deal with it",
     "general"),
    ("sync-is-academic",
     "update my schedule, I think canvas has new stuff on it",
     "academic"),
    ("kicad-is-hardware",
     "what parts are on my amp board schematic",
     "hardware"),
    ("conversion-is-utility",
     "how many ohms is four point seven kilohms",
     "utility"),
    ("test-me-is-quiz",
     "test me on the late work rules from my signals class",
     "quiz"),
]


# =======================================================================================
# Task 2 - syllabus extraction into the vault.
# =======================================================================================
#
# A fixture rather than a real PDF: `data/academic/` holds no syllabus on this box, and a
# benchmark that depends on a file somebody may not have is a benchmark that gets skipped.
# Written to contain three things on purpose -
#
#   * facts that ARE stated (code, title, instructor, grading, late policy),
#   * a fact that is NOT stated (office hours - the fabrication guard),
#   * a due date, which EXTRACTION_PROMPT forbids copying out because deadlines come from the
#     live Canvas feed and a date frozen into a note goes stale and contradicts it.
#
SYLLABUS_SNIPPET = """\
MORGAN STATE UNIVERSITY
Department of Political Science

POSC 201 - American Government and Politics
Fall 2026 | 3 credits | Tuesdays and Thursdays, 11:00 - 12:15, Jenkins Hall 310

Instructor: Dr. A. Whitfield
Email: a.whitfield@morgan.edu
Office: Jenkins Hall 428

COURSE DESCRIPTION
An introduction to the institutions and processes of American national government: the
Constitution, federalism, Congress, the presidency, the courts, parties, interest groups and
public opinion. The course emphasizes reading primary documents and writing analytically about
them.

GRADING
Attendance and participation .......... 10%
Two midterm examinations .............. 40%
Research paper ........................ 25%
Final examination ..................... 25%
Letter grades follow the university scale: A 90-100, B 80-89, C 70-79, D 60-69, F below 60.

LATE AND MISSED WORK
Written work loses 10% of its earned grade for each calendar day it is late. Work is not
accepted more than three days after the deadline and receives a zero at that point. A missed
examination may be made up only with documentation of a university-recognized excuse.

RESEARCH PAPER
The research paper is 8-10 pages, and is due October 14.

ACADEMIC INTEGRITY
Plagiarism, including undisclosed use of generative AI to produce submitted prose, is referred
to the Office of Student Conduct on the first occurrence.
"""


def _score_syllabus(facts: Any) -> list[tuple[str, bool, str]]:
    """Score one extraction. Returns `(check name, passed, what was seen)` per rule.

    The first four are ordinary extraction accuracy. The last two are the ones that matter:
    they are the guards `tools/syllabus_to_vault.py` exists to enforce, and a model that fills
    an unstated field or copies a due date has failed in the specific way that would poison the
    vault, however good the rest of the note looks.
    """
    def get(name: str) -> str:
        return (getattr(facts, name, "") or "").strip()

    code, grading = get("course_code"), get("grading")
    hours, name, other = get("office_hours"), get("course_name"), get("other")
    date_fields = " ".join((grading, get("late_policy"), other, name)).lower()

    return [
        ("course code",
         "POSC" in code.upper() and "201" in code, code or "(empty)"),
        ("course name",
         "american government" in name.lower(), name or "(empty)"),
        ("instructor + email",
         "whitfield" in get("instructor").lower() and "@morgan.edu" in get("instructor").lower(),
         get("instructor").replace("\n", " ")[:60] or "(empty)"),
        ("grading breakdown",
         all(pct in grading for pct in ("10", "40", "25")) and grading.count("%") >= 3,
         grading.replace("\n", " / ")[:60] or "(empty)"),
        # --- the two guards ------------------------------------------------------------
        ("office hours left EMPTY (not stated)",
         hours == "", hours[:60] or "(empty - correct)"),
        ("no due date copied out",
         "october" not in date_fields,
         "October 14 leaked" if "october" in date_fields else "(clean)"),
    ]


# =======================================================================================
# Task 3 - markdown cleanup for the text-to-speech engine.
# =======================================================================================
#
# The raw block carries one instance of every category `engine/split.is_speakable()` refuses:
# a fenced code block, a code identifier with a dot in it, a file path, a URL, a hex literal, a
# markdown table, emoji, and symbols Piper renders as silence.
RAW_MARKDOWN = """\
### Setting the ADC gain

The HX711's gain is set by the **number of pulses** you send after the 24th, so:

```c
hx711_read(&dev);   // 25 pulses -> channel A, gain 128
```

| pulses | channel | gain |
|--------|---------|------|
| 25     | A       | 128  |
| 27     | B       | 32   |

Write `0x80` to `config.gain_register` (see `drivers/hx711.c`), and note the datasheet at
https://example.com/hx711.pdf says settling is ~400ms @ 10 SPS - that's +/-0.02% of full scale.
"""

SPEAKABLE_PROMPT = """\
Rewrite the following technical note as ONE short spoken answer for a text-to-speech voice on a
Raspberry Pi. It will be read aloud by a speech synthesiser, so it must be plain spoken English.

RULES:
- No markdown of any kind: no asterisks, backticks, hashes, underscores, code fences or tables.
- No code, no file paths, no URLs, no hex literals, no identifiers with dots or underscores.
- No emoji and no symbols. Say "ohms", "degrees", "percent", "plus or minus", "at" as WORDS.
- Numbers stay as digits.
- 40 words maximum. Say the single most useful thing and stop.

Reply with the spoken sentence only. No preamble, no quotation marks around it.

NOTE:
{document}
"""


# =======================================================================================
# Backends
# =======================================================================================

@dataclass
class Call:
    """One model call's outcome. `ok` is transport-level success, not answer quality."""

    ok: bool
    latency_s: float
    value: Any = None                 # a pydantic object, or a string for the free-text task
    prompt_tokens: int = 0
    completion_tokens: int = 0
    note: str = ""


class Backend:
    """Two operations are all three tasks need: structured JSON, and free text."""

    name = "backend"
    available = False
    unavailable_because = ""

    def structured(self, prompt: str, schema: type[BaseModel], model: str) -> Call:
        raise NotImplementedError

    def text(self, prompt: str, model: str) -> Call:
        raise NotImplementedError


class GeminiBackend(Backend):
    """The current production path: LangChain's `ChatGoogleGenerativeAI`.

    `engine/models.py` is imported HERE and not at module scope, because it raises SystemExit
    when the key is missing and this harness must survive that. `SystemExit` does not inherit
    from `Exception`, so it is caught by name.
    """

    name = "gemini"

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI   # noqa: PLC0415, F401

            import engine.models                                        # noqa: PLC0415, F401
        except SystemExit as exc:
            first = str(exc).strip().splitlines()
            self.unavailable_because = first[0] if first else "GOOGLE_API_KEY is not set"
            return
        except ImportError as exc:
            self.unavailable_because = f"langchain-google-genai is not installed ({exc})"
            return
        self.available = True

    def _llm(self, model: str):
        if model not in self._cache:
            from langchain_google_genai import ChatGoogleGenerativeAI   # noqa: PLC0415

            # max_retries=0 matches engine/models.LLM_MAX_RETRIES and its reasoning: a retry
            # inside the call would land in the latency number as if it were think time.
            self._cache[model] = ChatGoogleGenerativeAI(
                model=model, temperature=0.0, max_retries=0)
        return self._cache[model]

    @staticmethod
    def _usage(raw: Any) -> tuple[int, int]:
        usage = getattr(raw, "usage_metadata", None) or {}
        return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))

    def structured(self, prompt: str, schema: type[BaseModel], model: str) -> Call:
        # include_raw=True is what keeps the token counts: the plain form returns the parsed
        # pydantic object and throws the response metadata away with it, and then the cost
        # column for this backend would be empty.
        chain = self._llm(model).with_structured_output(schema, include_raw=True)
        t0 = time.perf_counter()
        try:
            out = chain.invoke(prompt)
        except Exception as exc:                                        # noqa: BLE001
            return Call(False, time.perf_counter() - t0,
                        note=f"{type(exc).__name__}: {str(exc)[:120]}")
        dt = time.perf_counter() - t0
        p, c = self._usage(out.get("raw"))
        if out.get("parsing_error") or out.get("parsed") is None:
            return Call(False, dt, prompt_tokens=p, completion_tokens=c,
                        note=f"unparseable: {str(out.get('parsing_error'))[:100]}")
        return Call(True, dt, out["parsed"], p, c)

    def text(self, prompt: str, model: str) -> Call:
        t0 = time.perf_counter()
        try:
            msg = self._llm(model).invoke(prompt)
        except Exception as exc:                                        # noqa: BLE001
            return Call(False, time.perf_counter() - t0,
                        note=f"{type(exc).__name__}: {str(exc)[:120]}")
        dt = time.perf_counter() - t0
        p, c = self._usage(msg)
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        return Call(True, dt, content, p, c)


def _strict_schema(schema: type[BaseModel]) -> dict:
    """A pydantic schema flattened into what a strict `json_schema` response format accepts.

    Two transformations, both needed in practice and neither done by pydantic:

    * **`$ref`/`$defs` inlined.** An enum field becomes a `$ref` into `$defs`, and providers
      differ on whether they follow one. Inlining sidesteps the question.
    * **`additionalProperties: false`, and every property required**, on every object. That is
      what "strict" means to the OpenAI-compatible schema validator; a schema without it is
      rejected with a 400 rather than silently relaxed.
    """
    raw = schema.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = defs.get(node["$ref"].rsplit("/", 1)[-1], {})
            merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
            return walk(merged)
        node = {k: walk(v) for k, v in node.items()}
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        return node

    return walk(raw)


def _status_of(exc: Exception) -> int | None:
    """The HTTP status behind an openai exception, or None if it is not an HTTP error.

    The distinction this supports is the whole point: 400/422 is "your schema was rejected"
    and is a fact about the MODEL; 429/5xx is "the shared pool is busy" and is a fact about
    everyone else's traffic. Conflating them turns other people's load into a verdict.
    """
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None)


def _reason(exc: Exception) -> str:
    """The useful sentence out of a provider error, not the first 160 characters of JSON.

    OpenRouter nests what actually happened in `error.metadata.raw`, and the body on the wire
    is COMPACT json - `{"raw":"..."}`, no space after the colon - while `str(exc)` renders it
    as a Python dict with spaces. The first version matched only the spaced form, so on the Pi
    it fell through to a raw dump truncated mid-dictionary, five times, with the sentence that
    explained everything sitting past the cut. Match both, or parse it.
    """
    body = getattr(getattr(exc, "response", None), "text", "") or str(exc)
    try:
        raw = json.loads(body)["error"]["metadata"]["raw"]
        if isinstance(raw, str) and raw.strip():
            return raw[:200]
    except Exception:                                                   # noqa: BLE001
        pass
    hit = re.search(r"""["']raw["']\s*:\s*["'](.+?)["']\s*[,}]""", body, re.S)
    if hit:
        return hit.group(1)[:200]
    return str(exc)[:160]


class OpenRouterBackend(Backend):
    """GLM - and anything else on OpenRouter - through the OpenAI-compatible client.

    The whole integration is two arguments, `base_url` and `api_key`, which is the reason this
    comparison is cheap to run at all. Everything else in the class is about getting structured
    output back reliably, and about recording it honestly when a model needs the weaker JSON
    mode to manage it.
    """

    name = "glm"

    def __init__(self, referer: str = "https://github.com/mroddball89",
                 retry_429: int = 3) -> None:
        self.retry_429 = retry_429
        self._last_latency = 0.0
        # `.env` is loaded HERE, not left to the Gemini side. `engine/models.py` calls
        # load_dotenv on import, so for a while this backend worked only as a side effect of
        # Gemini having been constructed first — and `--backend glm`, the one invocation that
        # spends no Gemini quota, would have reported a key that was sitting in .env as unset.
        #
        # Explicit path, for the same reason engine/models.py gives: the no-argument form walks
        # up from the CALLER's frame, which finds the wrong thing under a systemd unit whose
        # working directory is not the repo.
        try:
            from dotenv import load_dotenv                              # noqa: PLC0415

            load_dotenv(REPO_ROOT / ".env")
        except ImportError:
            pass                    # python-dotenv absent: fall back to the real environment

        key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if not key:
            self.unavailable_because = (
                f"OPENROUTER_API_KEY is not set - get a free key at "
                f"https://openrouter.ai/keys and put it in {REPO_ROOT / '.env'}")
            return
        if key.startswith("sk-or-") is False:
            # Not fatal — OpenRouter has changed key shapes before and a validator that refuses
            # the next valid one is worse than none. But the placeholder failure this repo
            # already lived through once (D7: `PASTE_NEW_KEY_HERE` on both boxes, twenty
            # minutes of "my API key isn't working") is worth one line of warning.
            print(f"  note: OPENROUTER_API_KEY does not start with 'sk-or-' "
                  f"({len(key)} characters) - check it pasted whole.")
        try:
            from openai import OpenAI                                   # noqa: PLC0415
        except ImportError as exc:
            self.unavailable_because = f"the openai package is not installed ({exc})"
            return
        # These two headers are optional and free; OpenRouter uses them for its public
        # leaderboards, and sending them is the courtesy asked of free-tier traffic.
        self._client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=key,
                              default_headers={"HTTP-Referer": referer,
                                               "X-Title": "Mr Odd Ball - EE Copilot"})
        self.available = True

    def _chat(self, model: str, prompt: str, response_format: dict | None) -> Any:
        kwargs: dict[str, Any] = {"model": model, "temperature": 0.0,
                                  "messages": [{"role": "user", "content": prompt}]}
        if response_format:
            kwargs["response_format"] = response_format
        return self._client.chat.completions.create(**kwargs)

    def _send(self, model: str, prompt: str, response_format: dict | None) -> tuple[Any, str]:
        """One call, retrying a 429 with backoff. Returns `(response, note)`; raises otherwise.

        **A free model on OpenRouter is served out of a shared pool, and that pool 429s.**
        Measured on the Pi 2026-08-24: two calls succeeded and the next five came back
        `code 429, limit_source: upstream_provider_shared_pool, provider Decart` - "temporarily
        rate-limited upstream", with the account's own quota untouched (`usage: 0`). Without a
        retry, a benchmark of a free model mostly measures how busy other people are.

        This is the opposite call from `LLM_MAX_RETRIES = 0` in `engine/models.py`, and
        deliberately so. There, a 429 meant a DAILY quota that would not come back in 8 seconds,
        the retry was certain to fail, and the retrying thread was the one draining the
        microphone. Here the 429 is a transient shared-pool block that clears in seconds,
        nothing is listening, and the alternative is no measurement at all.

        **The wait is not charged to the model.** Only the successful attempt is timed, and the
        note records how many 429s preceded it, so a latency figure stays a latency figure.
        """
        last: Exception | None = None
        for attempt in range(self.retry_429 + 1):
            t0 = time.perf_counter()
            try:
                resp = self._chat(model, prompt, response_format)
            except Exception as exc:                                    # noqa: BLE001
                last = exc
                if _status_of(exc) not in (429, 500, 502, 503) or attempt == self.retry_429:
                    raise
                # 2s, 4s, 8s. The upstream message says "retry shortly" and means it.
                time.sleep(2.0 * (2 ** attempt))
                continue
            self._last_latency = time.perf_counter() - t0
            note = (f"{attempt} upstream 429{'s' if attempt > 1 else ''} before this succeeded"
                    if attempt else "")
            return resp, note
        raise last                                                      # unreachable

    def _elapsed(self, t0: float) -> float:
        """The successful attempt's latency, not the wall clock including 429 backoff."""
        return self._last_latency or (time.perf_counter() - t0)

    @staticmethod
    def _usage(resp: Any) -> tuple[int, int]:
        usage = getattr(resp, "usage", None)
        if not usage:
            return 0, 0
        return int(usage.prompt_tokens or 0), int(usage.completion_tokens or 0)

    def structured(self, prompt: str, schema: type[BaseModel], model: str) -> Call:
        fmt = {"type": "json_schema",
               "json_schema": {"name": schema.__name__, "strict": True,
                               "schema": _strict_schema(schema)}}
        note = ""
        t0 = time.perf_counter()
        try:
            resp, note = self._send(model, prompt, fmt)
        except Exception as exc:                                        # noqa: BLE001
            # **Only a 400/422 means the schema was refused**, and getting this wrong is not
            # cosmetic. The first version fell back on ANY exception, so a shared-pool 429
            # printed "json_schema refused (RateLimitError)" - which reads as "this model
            # cannot do structured output" and would have disqualified it from the router on
            # the strength of somebody else's traffic. Caught on the Pi 2026-08-24.
            #
            # A model that genuinely cannot honour a strict schema DOES answer 400, and that
            # case still falls back, still records it, and still means what it said: passing
            # only in the weaker mode is not passing, because router.py binds a closed enum.
            if _status_of(exc) not in (400, 422):
                return Call(False, self._elapsed(t0),
                            note=f"{type(exc).__name__}: {_reason(exc)}")
            note = f"json_schema refused ({_status_of(exc)}), retried in json_object mode"
            fallback = (f"{prompt}\n\nReply with JSON only, matching exactly this schema:\n"
                        f"{json.dumps(_strict_schema(schema))}")
            try:
                resp, more = self._send(model, fallback, {"type": "json_object"})
                note = f"{note}; {more}" if more else note
            except Exception as exc2:                                   # noqa: BLE001
                return Call(False, self._elapsed(t0),
                            note=f"{type(exc2).__name__}: {_reason(exc2)}")
        dt = self._elapsed(t0)
        p, c = self._usage(resp)
        body = (resp.choices[0].message.content or "").strip()
        # Some models fence their JSON even in JSON mode. Strip one fence rather than failing
        # the model over a cosmetic wrapper - the task is the fields, not the packaging - but
        # say so in the note, because router.py's chain would NOT have tolerated it.
        if body.startswith("```"):
            body = body.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            note = (note + "; " if note else "") + "wrapped its JSON in a code fence"
        try:
            return Call(True, dt, schema.model_validate_json(body), p, c, note)
        except Exception as exc:                                        # noqa: BLE001
            return Call(False, dt, prompt_tokens=p, completion_tokens=c,
                        note=f"unparseable: {type(exc).__name__}: {str(exc)[:90]}")

    def text(self, prompt: str, model: str) -> Call:
        t0 = time.perf_counter()
        try:
            resp, note = self._send(model, prompt, None)
        except Exception as exc:                                        # noqa: BLE001
            return Call(False, self._elapsed(t0),
                        note=f"{type(exc).__name__}: {_reason(exc)}")
        p, c = self._usage(resp)
        return Call(True, self._elapsed(t0),
                    (resp.choices[0].message.content or "").strip(), p, c, note)


# =======================================================================================
# The suite
# =======================================================================================

@dataclass
class Result:
    """One case, one backend, one trial."""

    task: str
    case: str
    backend: str
    model: str
    trial: int
    ok: bool                       # the answer was CORRECT, not merely returned
    latency_s: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    detail: str = ""
    note: str = ""
    subchecks: list[tuple[str, bool, str]] = field(default_factory=list)
    # False = the call never came back at all (429, timeout, 5xx). Kept LAST because every
    # construction below is positional, and a field inserted mid-dataclass would silently
    # shift detail/note/subchecks by one.
    #
    # This distinction is the difference between "the model got it wrong" and "the model never
    # spoke". Scoring an upstream 429 as a wrong answer turns somebody else's traffic into a
    # verdict on the model - which is exactly what the first Pi run reported as "29%".
    answered: bool = True


def run_router(backend: Backend, model: str, trial: int, verbose: bool) -> list[Result]:
    """Task 1 - five edge cases, scored on the exact destination."""
    prompt_template, routes = read_router_prompt()
    schema = route_decision_model(routes)
    valid = set(routes)
    out: list[Result] = []
    for case, question, expected in ROUTER_CASES:
        call = backend.structured(prompt_template.format(question=question), schema, model)
        got = (call.value.destination or "").strip().lower() if call.ok else ""
        ok = call.ok and got == expected
        detail = f"{got or 'n/a'} (wanted {expected})"
        note = call.note
        if call.ok and got not in valid:
            # Defensive: the Literal should make this unreachable, and if it ever fires it
            # means a backend returned a value its own schema forbade.
            note = (note + "; " if note else "") + f"'{got}' is not one of the ten routes"
        out.append(Result("router", case, backend.name, model, trial, ok, call.latency_s,
                          call.prompt_tokens, call.completion_tokens, detail, note,
                          answered=call.ok))
        if verbose:
            print(f"      {'PASS' if ok else 'FAIL'}  {case:<24} {detail}"
                  + (f"   [{note}]" if note else ""))
    return out


def run_syllabus(backend: Backend, model: str, trial: int, verbose: bool) -> list[Result]:
    """Task 2 - one extraction, scored on six rules, two of them the fabrication guards."""
    from tools.syllabus_to_vault import EXTRACTION_PROMPT, SyllabusFacts   # noqa: PLC0415

    prompt = EXTRACTION_PROMPT.format(truncation_note="", document=SYLLABUS_SNIPPET)
    call = backend.structured(prompt, SyllabusFacts, model)
    if not call.ok:
        return [Result("syllabus", "posc201", backend.name, model, trial, False,
                       call.latency_s, call.prompt_tokens, call.completion_tokens,
                       "no usable object", call.note, answered=False)]
    checks = _score_syllabus(call.value)
    passed = sum(1 for _, good, _ in checks if good)
    if verbose:
        for label, good, seen in checks:
            print(f"      {'PASS' if good else 'FAIL'}  {label:<36} {seen}")
    return [Result("syllabus", "posc201", backend.name, model, trial,
                   passed == len(checks), call.latency_s, call.prompt_tokens,
                   call.completion_tokens, f"{passed}/{len(checks)} rules", call.note, checks)]


def run_speakable(backend: Backend, model: str, trial: int, verbose: bool) -> list[Result]:
    """Task 3 - markdown to speech, scored by the turn path's own gate."""
    from engine.split import MAX_WORDS, is_speakable                       # noqa: PLC0415

    call = backend.text(SPEAKABLE_PROMPT.format(document=RAW_MARKDOWN), model)
    if not call.ok:
        return [Result("speakable", "hx711-note", backend.name, model, trial, False,
                       call.latency_s, call.prompt_tokens, call.completion_tokens,
                       "no answer", call.note, answered=False)]
    said = str(call.value).strip().strip('"')
    rejection = is_speakable(said, MAX_WORDS)
    ok = rejection is None
    detail = (f"{len(said.split())} words, accepted" if ok
              else f"rejected: {rejection.reason}"
                   + (f" ({rejection.detail})" if rejection.detail else ""))
    if verbose:
        print(f"      {'PASS' if ok else 'FAIL'}  {detail}")
        print(f"            \"{said[:150]}\"")
    return [Result("speakable", "hx711-note", backend.name, model, trial, ok, call.latency_s,
                   call.prompt_tokens, call.completion_tokens, detail, call.note)]


# task name -> (what it is, the runner, which model tier it belongs to)
TASKS: dict[str, tuple[str, Callable[..., list[Result]], str]] = {
    "router":    ("router classification, 5 edge cases", run_router, "router"),
    "syllabus":  ("syllabus -> vault JSON, 6 rules", run_syllabus, "agent"),
    "speakable": ("markdown -> speech, gated by is_speakable", run_speakable, "agent"),
}


# =======================================================================================
# Reporting
# =======================================================================================

def _cost(model: str, prompt_tok: int, completion_tok: int,
          table: dict[str, tuple[float, float]]) -> float:
    """USD for these tokens at this table's rates. NaN when the model has no rate here."""
    rate = table.get(model)
    if rate is None:
        return float("nan")
    return (prompt_tok * rate[0] + completion_tok * rate[1]) / 1_000_000


def _fmt_usd(value: float) -> str:
    if value != value:                       # NaN - no rate known for this model
        return "?"
    if value == 0:
        return "$0.00"
    return f"${value:.6f}" if value < 0.01 else f"${value:.4f}"


def summarise(results: list[Result], backends: list[tuple[str, str]]) -> str:
    """The comparison table. One block per task, one row per backend."""
    width = 84
    lines = ["=" * width,
             "  MR ODD BALL - GLM-5.2 (free, OpenRouter) vs Gemini, on this repo's own jobs",
             "=" * width]

    for task, (blurb, _, _) in TASKS.items():
        rows = [r for r in results if r.task == task]
        if not rows:
            continue
        lines.append("")
        lines.append(f"  {task.upper()}  -  {blurb}")
        lines.append(f"  {'backend':<8} {'model':<24} {'pass':>10} {'median':>9} "
                     f"{'p_tok':>6} {'c_tok':>6} {'cost':>10}")
        lines.append("  " + "-" * (width - 4))
        for name, model in backends:
            mine = [r for r in rows if r.backend == name]
            if not mine:
                continue
            shown = mine[0].model                        # the model this task actually used
            answered = [r for r in mine if r.answered]
            passed = sum(1 for r in mine if r.ok)
            # Scored over what came BACK. A call the provider never answered is missing data,
            # not a wrong answer, and averaging it in as zero defames the model.
            denom = len(answered) or len(mine)
            lat = statistics.median((r.latency_s for r in answered)
                                    if answered else (r.latency_s for r in mine))
            p_tok = round(statistics.mean(r.prompt_tokens for r in mine))
            c_tok = round(statistics.mean(r.completion_tokens for r in mine))
            cost = _cost(shown, sum(r.prompt_tokens for r in mine),
                         sum(r.completion_tokens for r in mine), PRICE_USD_PER_MTOK)
            silent = len(mine) - len(answered)
            lines.append(f"  {name:<8} {shown:<24} {passed:>4}/{denom:<5} "
                         f"{lat:>8.2f}s {p_tok:>6} {c_tok:>6} {_fmt_usd(cost):>10}"
                         + (f"   ({silent} never answered)" if silent else ""))
        # Failures and quirks named individually. A pass rate alone hides WHICH edge case a
        # model missed, and that is the only part of this worth acting on.
        for r in rows:
            if not r.ok or r.note:
                lines.append(f"      {'FAIL' if not r.ok else 'note'}  "
                             f"{r.backend}/{r.case}: {r.detail}"
                             + (f"  [{r.note}]" if r.note else ""))

    lines.append("")
    lines.append("  OVERALL")
    lines.append(f"  {'backend':<8} {'pass rate':>10} {'median':>9} {'total':>9} "
                 f"{'billed':>10} {'if paid':>10}")
    lines.append("  " + "-" * (width - 4))
    for name, model in backends:
        mine = [r for r in results if r.backend == name]
        if not mine:
            continue
        answered = [r for r in mine if r.answered]
        pct = 100.0 * sum(1 for r in mine if r.ok) / (len(answered) or len(mine))
        billed = sum(_cost(r.model, r.prompt_tokens, r.completion_tokens,
                           PRICE_USD_PER_MTOK) for r in mine)
        shadow = sum(_cost(r.model, r.prompt_tokens, r.completion_tokens,
                           SHADOW_PRICE_USD_PER_MTOK) for r in mine)
        lines.append(f"  {name:<8} {pct:>9.0f}% "
                     f"{statistics.median((r.latency_s for r in answered) if answered else (r.latency_s for r in mine)):>8.2f}s "
                     f"{sum(r.latency_s for r in mine):>8.1f}s "
                     f"{_fmt_usd(billed):>10} {_fmt_usd(shadow):>10}")
    silent_total = [r for r in results if not r.answered]
    lines += [
        "",
        "  'billed' is what this run actually cost. 'if paid' is the same tokens at each",
        "  model's paid rate - the number that matters on the day a free tier moves."]
    if silent_total:
        # Loud, because a pass rate over three answered calls is not a pass rate, and a run
        # this thin should not be quoted at anyone as a comparison.
        by_backend = {}
        for r in silent_total:
            by_backend[r.backend] = by_backend.get(r.backend, 0) + 1
        lines += [
            "",
            "  *** " + ", ".join(f"{n} {b} call(s)" for b, n in sorted(by_backend.items()))
            + " NEVER ANSWERED and are excluded from the rates above.",
            "      Pass rates here are over a handful of calls. Re-run when the provider is",
            "      quieter, or with --retry-429 6, before treating any of this as a result."]
    lines.append("=" * width)
    return "\n".join(lines)


def write_csv(results: list[Result], args: Any) -> Path:
    """Raw rows to `media/data/`, with the meta.json the vlog convention asks for.

    Every chart in this repo has its numbers beside it; this writes the numbers, so a later
    plot of the latency comparison has something to be generated FROM.
    """
    stamp = date.today().isoformat()
    out_dir = REPO_ROOT / "media" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{stamp}-glm-vs-gemini.csv"

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for comment in (
                "# Mr Odd Ball - GLM-5.2 (free, OpenRouter) vs Gemini, one row per case",
                f"# measured {stamp} on {platform.node()} ({platform.system()} "
                f"{platform.machine()})",
                f"# python {platform.python_version()}, {args.trials} trial(s) per case, "
                f"temperature 0, max_retries 0",
                "# ok = the answer was CORRECT by this repo's own gate, not merely returned"):
            w.writerow([comment])
        w.writerow(["timestamp_utc", "task", "case", "backend", "model", "trial", "ok",
                    "latency_s", "answered", "prompt_tokens", "completion_tokens",
                    "cost_usd", "cost_usd_if_paid", "detail", "note"])
        for r in results:
            w.writerow([
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                r.task, r.case, r.backend, r.model, r.trial, int(r.ok),
                f"{r.latency_s:.3f}", int(r.answered), r.prompt_tokens, r.completion_tokens,
                f"{_cost(r.model, r.prompt_tokens, r.completion_tokens, PRICE_USD_PER_MTOK):.8f}",
                f"{_cost(r.model, r.prompt_tokens, r.completion_tokens, SHADOW_PRICE_USD_PER_MTOK):.8f}",
                r.detail, r.note])

    per_backend: dict[str, Any] = {}
    for name in sorted({r.backend for r in results}):
        mine = [r for r in results if r.backend == name]
        per_backend[name] = {
            "models": sorted({r.model for r in mine}),
            "pass_rate_pct": round(100.0 * sum(1 for r in mine if r.ok)
                                   / (sum(1 for r in mine if r.answered) or len(mine)), 1),
            "calls_never_answered": sum(1 for r in mine if not r.answered),
            "median_latency_s": round(statistics.median(r.latency_s for r in mine), 3),
            "calls": len(mine),
        }

    meta = {
        "what": "Pass rate, latency and cost for GLM-5.2 (free, via OpenRouter) and Gemini on "
                "three of Mr Odd Ball's real model-facing jobs.",
        "why": "Every Gemini call on this project comes out of a free tier capped per model "
               "per day (engine/models.py). Routing happens on EVERY turn, so a free model "
               "that can hold the router call alone changes what the day's quota buys.",
        "how_scored": {
            "router": "exact destination match, on five edge cases router.py's own routing "
                      "notes warn about",
            "syllabus": "6 rules over SyllabusFacts, two of them the fabrication guards "
                        "tools/syllabus_to_vault.py exists to enforce",
            "speakable": "engine.split.is_speakable() - the same gate the turn path uses",
        },
        "conditions": {
            "box": platform.node(),
            "os": f"{platform.system()} {platform.release()} {platform.machine()}",
            "python": platform.python_version(),
            "trials_per_case": args.trials,
            "temperature": 0.0,
            "max_retries": 0,
            "openrouter_base_url": OPENROUTER_BASE_URL,
        },
        "backends": per_backend,
        "caveats": [
            "Latency includes the network. On the free OpenRouter tier a request can queue "
            "behind other users' traffic, so a slow trial is not necessarily a slow model - "
            "run --trials 3 or more before quoting a latency at anyone.",
            "Gemini's cost column is $0.00 because this project is on the free tier, not "
            "because the model is free. cost_usd_if_paid is the honest comparison.",
            "Measured off the Pi if conditions.box is not the Pi. The Pi's link is the slower "
            "one and it is the box that ships.",
            "One fixture per task. This says whether a model can do the job at all, not how "
            "it holds up across a hundred syllabi.",
        ],
    }
    (out_dir / f"{stamp}-glm-vs-gemini.meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    return csv_path


def list_openrouter_models(filter_text: str = "glm") -> int:
    """Print what OpenRouter actually serves, with prices. Needs no API key.

    Here because a model id is a moving target: this is how you find out that the free variant
    has been renamed or withdrawn, instead of reading a 404 as a broken script.
    """
    import urllib.request                                              # noqa: PLC0415

    try:
        with urllib.request.urlopen(f"{OPENROUTER_BASE_URL}/models", timeout=30) as resp:
            data = json.load(resp)
    except Exception as exc:                                            # noqa: BLE001
        print(f"could not reach OpenRouter: {type(exc).__name__}: {exc}")
        return 2
    rows = [m for m in data.get("data", []) if filter_text.lower() in m.get("id", "").lower()]
    if not rows:
        print(f"nothing matching '{filter_text}' among {len(data.get('data', []))} models")
        return 1
    print(f"  {'model id':<30} {'context':>10} {'$/Mtok in':>11} {'$/Mtok out':>11}")
    for m in sorted(rows, key=lambda m: m["id"]):
        price = m.get("pricing", {})
        print(f"  {m['id']:<30} {m.get('context_length') or 0:>10,} "
              f"{float(price.get('prompt') or 0) * 1e6:>11.3f} "
              f"{float(price.get('completion') or 0) * 1e6:>11.3f}")
    return 0


# =======================================================================================

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Benchmark GLM-5.2 (free, OpenRouter) against Gemini on this repo's jobs")
    ap.add_argument("--task", choices=sorted(TASKS), action="append",
                    help="run only this task; repeatable. Default: all three.")
    ap.add_argument("--backend", choices=("gemini", "glm"), action="append",
                    help="run only this backend; repeatable. Default: both.")
    ap.add_argument("--trials", type=int, default=1,
                    help="repeats per case. The free tier jitters; use 3+ before quoting "
                         "a latency at anyone.")
    ap.add_argument("--glm-model", default=DEFAULT_GLM_MODEL,
                    help=f"OpenRouter model id (default {DEFAULT_GLM_MODEL})")
    ap.add_argument("--gemini-router-model", default=DEFAULT_GEMINI_ROUTER_MODEL,
                    help=f"default {DEFAULT_GEMINI_ROUTER_MODEL}, as in router.py")
    ap.add_argument("--gemini-agent-model", default=DEFAULT_GEMINI_AGENT_MODEL,
                    help=f"default {DEFAULT_GEMINI_AGENT_MODEL}, as in engine/models.py")
    ap.add_argument("--list-models", nargs="?", const="glm", metavar="SUBSTRING",
                    help="list OpenRouter models matching SUBSTRING (default 'glm') and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="load the suite and print it; make no API calls and need no key")
    ap.add_argument("--retry-429", type=int, default=3, metavar="N",
                    help="retries per call when the free pool is busy, 2s/4s/8s backoff "
                         "(default 3). The wait is never charged to the latency number.")
    ap.add_argument("--no-csv", action="store_true", help="do not write to media/data/")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="per-case results as they run, including what was actually said")
    args = ap.parse_args(argv)

    if args.list_models is not None:
        return list_openrouter_models(args.list_models)

    tasks = args.task or list(TASKS)
    want = args.backend or ["gemini", "glm"]

    if args.dry_run:
        prompt, routes = read_router_prompt()
        from engine.split import MAX_WORDS                                     # noqa: PLC0415
        from tools.syllabus_to_vault import EXTRACTION_PROMPT, SyllabusFacts   # noqa: PLC0415

        print(f"  router prompt   {len(prompt):>6} chars, {len(routes)} routes: "
              f"{', '.join(routes)}")
        print(f"  router cases    {len(ROUTER_CASES):>6}: "
              f"{', '.join(c for c, _, _ in ROUTER_CASES)}")
        print(f"  extraction      {len(EXTRACTION_PROMPT):>6} chars, "
              f"{len(SyllabusFacts.model_fields)} fields, over "
              f"{len(SYLLABUS_SNIPPET)} chars of syllabus")
        print(f"  speakable       {len(RAW_MARKDOWN):>6} chars of markdown, "
              f"budget {MAX_WORDS} words")
        print(f"\n  tasks: {', '.join(tasks)}   backends: {', '.join(want)}   "
              f"trials: {args.trials}")
        print("  dry run - nothing was sent anywhere.")
        return 0

    backends: list[tuple[Backend, dict[str, str]]] = []
    if "gemini" in want:
        backends.append((GeminiBackend(), {"router": args.gemini_router_model,
                                           "agent": args.gemini_agent_model}))
    if "glm" in want:
        backends.append((OpenRouterBackend(retry_429=args.retry_429),
                         {"router": args.glm_model,
                                               "agent": args.glm_model}))

    live = [(b, m) for b, m in backends if b.available]
    for b, _ in backends:
        if not b.available:
            print(f"  SKIPPED {b.name}: {b.unavailable_because}")
    if not live:
        print("\n  Neither backend is usable, so there is nothing to compare. Put "
              "GOOGLE_API_KEY\n  and/or OPENROUTER_API_KEY in .env, or run --dry-run to check "
              "the suite loads.")
        return 2

    results: list[Result] = []
    for backend, models in live:
        print(f"\n  {backend.name} ({', '.join(sorted(set(models.values())))})")
        for task in tasks:
            blurb, runner, kind = TASKS[task]
            print(f"    {task}: {blurb}")
            for trial in range(1, args.trials + 1):
                results.extend(runner(backend, models[kind], trial, args.verbose))

    # One (name, model) pair per backend for the OVERALL block. Where a backend used two models
    # - as Gemini does, lite for routing and flash for the agents - the agent model is named,
    # because that is the one the cost is dominated by. The per-task blocks show the real one.
    table_backends = [(b.name, m["agent"]) for b, m in live]
    print()
    print(summarise(results, table_backends))

    if not args.no_csv:
        path = write_csv(results, args)
        print(f"\n  wrote {path.relative_to(REPO_ROOT)} and its .meta.json")

    # Non-zero if any case failed, so this is usable from a script or a CI step.
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
