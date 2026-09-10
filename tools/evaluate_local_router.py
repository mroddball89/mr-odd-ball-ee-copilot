#!/usr/bin/env python3
"""
Module:  evaluate_local_router.py
Purpose: Ask whether qwen2.5:1.5b routes the way Gemini did, before it is allowed near a turn.
Author:  LB
Date:    2026-09-10

    ollama serve                                  # in another shell
    ollama pull qwen2.5:1.5b
    python tools/evaluate_local_router.py                     # the gym
    python tools/evaluate_local_router.py --dry-run           # no network, proves the wiring
    python tools/evaluate_local_router.py --model llama3.2:3b # try a different candidate

Writes `media/data/<date>-local-router-gym.csv` and the unredacted
`media/data/<date>-local-router-gym.raw.csv`, which is gitignored.

## What this is

Stage 21 Step 2 asked for an offline gym: *"replay a corpus, score it, change one parameter,
replay again, compare."* This is that, narrowed to one decision — **who routes** — because that
is the decision with a candidate waiting for it.

Nothing here touches the turn path. `router.py` will keep calling Gemini until
`ODDBALL_LOCAL_ROUTER` is set, and the point of this script is to find out whether setting it
would be a good idea. The corpus and its provenance are `tools/router_gym_corpus.py`.

## Three numbers, and they answer different questions

    agreement    on real logged utterances, how often does Qwen land where the rig landed?
                 The headline. Computed over REACHABLE rows only — a turn that
                 `orchestrator/route_hint.py` already answers for free cannot be made cheaper.

    coverage     can it reach all eleven routes at all? Scored on hand-authored probes for the
                 four routes eight days of real use never produced. A model that cannot say
                 `firmware` is disqualified whatever its agreement rate is, and the real corpus
                 cannot see that because it contains no firmware questions.

    integrity    how many calls came back as anything other than a valid RouteDecision?
                 **Reported separately from disagreement and never averaged into it.** A wrong
                 route is a routing problem and costs one bad answer; a crash is an engineering
                 problem and costs the whole turn — `engine/core.py` treats a router exception
                 as a failed turn. They have different fixes, so they get different numbers.

## Agreement is not accuracy, and the report says so out loud

Gemini's label is what the rig *did*, not what was *right*. `vault/corrections.md` exists
because it was sometimes wrong. So a disagreement is a question, not a defect, and this script
prints every one of them in full rather than only counting them: on a corpus this size the
twenty lines of disagreement are more informative than the percentage above them.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GOOGLE_API_KEY", "harness-not-a-real-key-but-long-enough-to-pass")
os.environ.setdefault("ODDBALL_VAULT_DIR", tempfile.mkdtemp(prefix="oddball-gym-vault-"))

import requests                                                        # noqa: E402

from orchestrator.local_router import (LocalRouterError, build_payload,  # noqa: E402
                                       native_base_url, route_locally)
from router import AgentRoute                                          # noqa: E402
from tools.router_gym_corpus import (CORPUS_PATH, Example, ROUTE_VALUES,  # noqa: E402
                                     build, load, save)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "media" / "data"

# The model this stage is about. Overridable, because the whole value of a gym is that a second
# candidate costs one flag rather than a rewrite.
DEFAULT_MODEL = os.environ.get("ODDBALL_LOCAL_ROUTER", "").strip() or "qwen2.5:1.5b"


# Routes that are spelled differently and do the same thing. Kept as a list of SETS rather than
# a flat alias map, so adding a future pair does not require picking which name is canonical —
# and so this file never has to claim one of them is "the real" route.
#
# Deliberately only the one pair. Every other route reaches a different agent with different
# tools, and folding any of them together would hide a real failure: `hardware` -> `firmware`
# is a wrong answer even though both are engineering, because they call different modules.
_EQUIVALENT: tuple[frozenset[str], ...] = (
    frozenset({"persona", "general"}),
)


def _same_destination(got: str, expected: str) -> bool:
    """True when both labels reach the same agent. Exact match, or a known equivalence."""
    if got == expected:
        return True
    return any(got in group and expected in group for group in _EQUIVALENT)


class Result:
    """One replayed utterance: what was expected, what came back, and what it cost."""

    __slots__ = ("example", "got", "ms", "error")

    def __init__(self, example: Example, got: str = "", ms: float = 0.0, error: str = "") -> None:
        self.example = example
        self.got = got
        self.ms = ms
        self.error = error

    @property
    def ok(self) -> bool:
        """Agreed with the label. False for a crash too — but `crashed` distinguishes them.

        **Scored on where the turn LANDS, not on the word.** `engine/core.py:1252` ends with

            # PERSONA and GENERAL both go to the character.
            from agents.persona_agent import run_persona_agent
            return split(run_persona_agent(text), route=route.value)

        so the two routes are one branch: same module, same function, same spoken answer.
        `tools/verify_agents.py` says the same thing from the other side, skipping GENERAL in
        its dispatch sweep because it "falls through to persona by design".

        Counting them as different cost the 2026-09-10 gym eighteen of its forty-four headline
        errors — a fifth of the whole corpus scored wrong for a distinction the user cannot
        hear. A router metric exists to predict what LB experiences; where two labels produce
        one behaviour, one behaviour is what gets measured.
        """
        return not self.error and _same_destination(self.got, self.example.route)

    @property
    def crashed(self) -> bool:
        return bool(self.error)


# --------------------------------------------------------------------------------------------
# preflight — fail with an instruction, not a stack trace
# --------------------------------------------------------------------------------------------

def preflight(model: str) -> str | None:
    """Why this run cannot happen, or None. Checked before the first of a hundred calls.

    A hundred `ConnectionError`s scrolling past is a worse way to learn that `ollama serve` is
    not running than one line saying so. This is the same argument `engine/models.py` makes for
    validating the API key at import.
    """
    url = native_base_url()
    try:
        tags = requests.get(f"{url}/api/tags", timeout=5)
        tags.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return (f"Ollama is not answering at {url} ({exc.__class__.__name__}).\n"
                f"  Install it from https://ollama.com/download, then in another shell:\n"
                f"      ollama serve\n"
                f"      ollama pull {model}")

    have = {m.get("name", "") for m in tags.json().get("models", [])}
    # Ollama reports "qwen2.5:1.5b"; a bare "qwen2.5" means the :latest tag. Accept either
    # spelling of the same model rather than sending LB to pull something he already has.
    if model not in have and f"{model}:latest" not in have:
        listed = ", ".join(sorted(have)) or "nothing"
        return (f"Ollama is running at {url} but does not have {model!r}.\n"
                f"  It has: {listed}\n"
                f"  Pull it with:  ollama pull {model}")
    return None


# --------------------------------------------------------------------------------------------
# the replay
# --------------------------------------------------------------------------------------------

def replay(examples: list[Example], model: str, progress: bool = True) -> list[Result]:
    """Route every example locally. Never raises — a crash is a datum, not the end of the run."""
    results: list[Result] = []
    for index, example in enumerate(examples, 1):
        started = time.monotonic()
        try:
            decision = route_locally(example.utterance, model=model)
            results.append(Result(example, decision.destination.value,
                                  (time.monotonic() - started) * 1000))
        except LocalRouterError as exc:
            results.append(Result(example, ms=(time.monotonic() - started) * 1000,
                                  error=str(exc)))
        except Exception as exc:                                       # noqa: BLE001
            # **Anything else is a datum too, and catching only LocalRouterError contradicted
            # the docstring above.** An unexpected exception on example 90 of 105 used to end
            # the run before `write_csv`, throwing away eighty-nine completed calls to report a
            # traceback. The type is kept in the message because an error that is NOT a
            # LocalRouterError is itself the finding.
            results.append(Result(example, ms=(time.monotonic() - started) * 1000,
                                  error=f"{type(exc).__name__}: {exc}"))
        if progress:
            latest = results[-1]
            mark = "." if latest.ok else ("!" if latest.crashed else "x")
            print(mark, end="", flush=True)
            if index % 50 == 0:
                print(f"  {index}/{len(examples)}")
    if progress:
        print()
    return results


# --------------------------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------------------------

def _rate(results: list[Result]) -> str:
    if not results:
        return "   n/a  (0 examples)"
    agreed = sum(1 for r in results if r.ok)
    return f"{agreed / len(results) * 100:5.1f}%  ({agreed}/{len(results)})"


def report(results: list[Result], model: str) -> int:
    """Print the three numbers and everything needed to argue with them. Returns an exit code."""
    scored = [r for r in results if r.example.tier != "seed" and r.example.reachable]
    unreachable = [r for r in results if r.example.tier != "seed" and not r.example.reachable]
    seeds = [r for r in results if r.example.tier == "seed"]
    crashes = [r for r in results if r.crashed]

    print(f"\n  {'=' * 74}")
    print(f"  {model} vs the route the rig actually took")
    print(f"  {'=' * 74}\n")

    print(f"  agreement   {_rate(scored)}   on utterances that still reach the router")
    print(f"  coverage    {_rate(seeds)}   on hand-authored probes for the missing routes")
    print(f"  integrity   {len(results) - len(crashes)}/{len(results)} calls returned a valid "
          f"RouteDecision")
    if unreachable:
        print(f"\n  ({len(unreachable)} more rows are answered free before the router and are "
              f"not scored;\n   {_rate(unreachable).strip()} of them would have agreed anyway.)")

    # Rows whose free-path annotation THREW are scored as router-bound, which is the safe
    # default but a silent one. Counted here so a class of utterance that reliably breaks the
    # annotator cannot quietly inflate the headline.
    unknown = [r for r in scored if r.example.claimed_by.startswith("unknown:")]
    if unknown:
        print(f"\n  {len(unknown)} rows could not be checked against the free paths and were "
              f"scored anyway:")
        for reason, count in Counter(r.example.claimed_by for r in unknown).most_common():
            print(f"    {count:>3}  {reason}")

    # Latency. The reason for the whole exercise, so it is not an afterthought at the bottom.
    timings = sorted(r.ms for r in results if not r.crashed)
    if timings:
        p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
        print(f"\n  latency     median {statistics.median(timings):.0f}ms   "
              f"p95 {p95:.0f}ms   worst {timings[-1]:.0f}ms")
        print(f"              gemini-3.5-flash-lite measured 890ms median, and 285,985ms worst "
              f"(2026-08-29)")

    if crashes:
        print(f"\n  {len(crashes)} CALLS DID NOT RETURN A VALID DECISION")
        print("  A crash is a failed turn, not a wrong answer. Fix these before reading the "
              "percentage above.")
        for error, count in Counter(r.error.split(".")[0] for r in crashes).most_common():
            print(f"    {count:>3}  {error[:96]}")

    # Only meaningful when the corpus actually CONTAINED every route. On a --limit run it
    # would otherwise blame the model for routes it was never asked about.
    asked = {r.example.route for r in results}
    seen = {r.got for r in results if not r.crashed}
    missed = sorted(ROUTE_VALUES - seen) if asked >= ROUTE_VALUES else []
    if missed:
        print(f"\n  NEVER EMITTED: {', '.join(missed)}")
        print("  The grammar permits these; the model did not choose them. On a corpus with "
              "examples\n  of each, that is a routing failure and not a corpus gap.")

    # The confusion table, printed only for routes that appear, so it stays readable at 11x11.
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in scored + seeds:
        confusion[r.example.route][r.got if not r.crashed else "CRASH"] += 1
    if confusion:
        print("\n  Where it sent them (rows = the label, columns = what Qwen said):\n")
        for expected in sorted(confusion):
            total = sum(confusion[expected].values())
            # Counted with the same rule the headline uses, or the table contradicts the
            # number above it — `persona 2/20` printed under a headline that scores those
            # eighteen as agreeing is a report arguing with itself.
            hits = sum(n for got, n in confusion[expected].items()
                       if _same_destination(got, expected))
            # The spread still lists an equivalent route, marked, because the raw confusion is
            # the diagnostic: hiding it would make the pair invisible the day it stops being
            # harmless.
            spread = "  ".join(
                f"{got}:{n}{'=' if got != expected and _same_destination(got, expected) else ''}"
                for got, n in confusion[expected].most_common() if got != expected)
            print(f"    {expected:<10} {hits:>3}/{total:<3}  {spread}")

    disagreements = [r for r in scored + seeds if not r.ok and not r.crashed]
    if disagreements:
        print(f"\n  All {len(disagreements)} disagreements, in full — read these rather than "
              f"the percentage:\n")
        for r in sorted(disagreements, key=lambda r: r.example.route):
            print(f"    {r.example.route:>9} -> {r.got:<9} {r.example.tier:<6} "
                  f"{r.example.utterance[:56]!r}")

    print("\n  Agreement is not accuracy. The label is what the rig DID, and vault/corrections.md")
    print("  exists because that was sometimes wrong. Every line above is a question.\n")

    # Non-zero only for the failure that is unambiguous. A low agreement rate is a judgement
    # call LB makes by reading the disagreements; a crash is not.
    return 1 if crashes else 0


def write_csv(results: list[Result], model: str, when: str) -> tuple[Path, Path]:
    """Two files: the raw one for LB, the redacted one for the repo.

    The published CSV carries **no transcripts**. `media/data/*.raw.csv` is gitignored for
    exactly this reason — 127 lines of speech in LB's house — and a gym that quietly published
    them under a new filename would be that rule failing on its first test.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = DATA_DIR / f"{when}-local-router-gym.raw.csv"
    pub_path = DATA_DIR / f"{when}-local-router-gym.csv"

    raw_fields = ["utterance", "expected", "got", "tier", "reachable", "claimed_by",
                  "agreed", "ms", "error"]
    with raw_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=raw_fields)
        writer.writeheader()
        for r in results:
            writer.writerow({"utterance": r.example.utterance, "expected": r.example.route,
                             "got": r.got, "tier": r.example.tier,
                             "reachable": r.example.reachable, "claimed_by": r.example.claimed_by,
                             "agreed": r.ok, "ms": f"{r.ms:.0f}", "error": r.error[:120]})

    with pub_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["model", "expected", "got", "tier", "reachable",
                                                "agreed", "ms", "crashed"])
        writer.writeheader()
        for r in results:
            writer.writerow({"model": model, "expected": r.example.route, "got": r.got,
                             "tier": r.example.tier, "reachable": r.example.reachable,
                             "agreed": r.ok, "ms": f"{r.ms:.0f}", "crashed": r.crashed})
    return raw_path, pub_path


# --------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="does a local model route like Gemini did?")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"default {DEFAULT_MODEL}")
    parser.add_argument("--corpus", type=Path, help="a JSONL from tools/router_gym_corpus.py")
    parser.add_argument("--limit", type=int, default=0, help="stop after N examples")
    parser.add_argument("--rebuild", action="store_true", help="reassemble the corpus first")
    parser.add_argument("--dry-run", action="store_true",
                        help="build everything and send nothing — proves the wiring offline")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    corpus_path = args.corpus or CORPUS_PATH
    if args.rebuild or not corpus_path.exists():
        examples, _ = build()
        save(examples, corpus_path)
        print(f"  built {len(examples)} examples -> "
              f"{corpus_path.relative_to(REPO_ROOT).as_posix()}")
    else:
        examples = load(corpus_path)

    if args.limit:
        examples = examples[:args.limit]
    if not examples:
        print("  the corpus is empty — run `python tools/router_gym_corpus.py` first")
        return 1

    scorable = sum(1 for e in examples if e.tier != "seed" and e.reachable)
    print(f"\n  {len(examples)} examples, {scorable} of them real and still router-bound")
    if scorable < 50:
        # The brief asked for 50-100. Said plainly rather than reported as if it were met.
        print(f"  NOTE: the brief wanted 50-100 real examples and there are {scorable}. "
              f"Every day of\n        ordinary use adds gold rows; re-run "
              f"`tools/router_gym_corpus.py` to pick them up.")

    if args.dry_run:
        payload = build_payload(examples[0].utterance, args.model)
        enum = payload["format"]["properties"]["destination"]["enum"]
        print(f"\n  dry run — nothing was sent to {native_base_url()}\n")
        print(f"    model         {payload['model']}")
        print(f"    endpoint      {native_base_url()}/api/chat")
        print(f"    grammar enum  {len(enum)} routes: {', '.join(enum)}")
        print(f"    required      {payload['format']['required']}")
        print(f"    prompt        {len(payload['messages'][0]['content'])} chars, "
              f"1 user message (identical to Gemini's)")
        print(f"    options       {payload['options']}\n")
        return 0

    problem = preflight(args.model)
    if problem:
        print(f"\n  {problem}\n")
        return 1

    print(f"  replaying through {args.model} — . agreed  x disagreed  ! crashed\n")
    results = replay(examples, args.model)
    code = report(results, args.model)

    # A --limit run is a spot check, not the day's evidence. Writing it to the canonical name
    # would let `--limit 5` silently overwrite a full replay from the same morning, in the one
    # directory this project treats as its record. Named apart instead.
    stamp = date.today().isoformat() + (f"-partial{len(examples)}" if args.limit else "")
    raw_path, pub_path = write_csv(results, args.model, stamp)
    print(f"  Wrote {pub_path.relative_to(REPO_ROOT).as_posix()} (redacted, committed)")
    print(f"        {raw_path.relative_to(REPO_ROOT).as_posix()} (transcripts, gitignored)\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
