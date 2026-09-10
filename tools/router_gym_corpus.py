#!/usr/bin/env python3
"""
Module:  router_gym_corpus.py
Purpose: Assemble the labelled utterances the router gym scores against, and say where each came from.
Author:  LB
Date:    2026-09-10

    python tools/router_gym_corpus.py              # build it, print what it is made of

Writes `data/router_gym_corpus.jsonl` — **gitignored**, because every line of it is something
LB actually said in his house. Same rule as `media/data/*.raw.csv`; see the memory note
`never-publish-personal-data` and note that the copilot repo is public.

## The hole this file exists to work around

Stage 21 planned the gym against *"data/oddball.log — 23,933 lines of turns with routes and
timings"*. That is not what is on disk. `data/oddball.log` is gitignored **and truncated on each
start**: it held 242 lines and **two** paid routing decisions when this was written. The corpus
the gym needs cannot be read out of it.

So the corpus is assembled from three sources of different quality, and the tier is carried on
every row rather than averaged away:

    gold    `route %r -> %s (%s)` lines in the live log. Gemini decided these, on the turn
            path, with the real prompt. The only rows that are literally what the brief asked
            for, and there are almost none of them left.
    silver  `media/data/2026-09-03-wasted-turns.raw.csv` — 127 real utterances with the route
            the turn actually took, harvested off the log on 2026-09-03 BEFORE it was
            truncated. This is the surviving record of eight days of use.
    extra   anything LB points `--extra` at, in either format.

**Gold accumulates from here forward.** Every ordinary day of use adds rows, and the file is
appended to rather than rebuilt, so the 50-100 gold examples the brief wants are a few days
away rather than something that can be conjured today.

## Why silver is not gold, stated plainly rather than in a footnote

The silver route column comes from the `turn:` summary line, which records **where the turn
ended up** — not who decided it. Three things follow, and the gym must not paper over any:

1. It contains routes that are not routes. `sleep`, `note` and `correction` are free-path
   intents with no `AgentRoute` value. Scoring a model against a label it cannot physically
   emit is scoring nothing; those rows are dropped, counted, and named in the report.
2. Some of the rest were decided by `orchestrator/route_hint.py` for free, not by Gemini.
3. Some never reached any router: `Engine.FREE_INTENTS` answered them from a lookup table.

`reachable_today()` below re-runs this repo's own free paths over each utterance and marks the
rows that would still cost a router call. That is the population the whole pivot is about — a
turn the hint already answers for nothing cannot be made cheaper by a local model — so the gym
headlines the reachable set and reports the rest separately.
"""

from __future__ import annotations

import ast
import csv
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Before anything reaches engine/models.py. D7: a harness must run on a box with no .env, and
# the corpus builder is keyless by construction — it never sends a request anywhere.
os.environ.setdefault("GOOGLE_API_KEY", "harness-not-a-real-key-but-long-enough-to-pass")
os.environ.setdefault("ODDBALL_VAULT_DIR", tempfile.mkdtemp(prefix="oddball-gym-vault-"))

from router import AgentRoute                                          # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = REPO_ROOT / "data" / "router_gym_corpus.jsonl"
LIVE_LOG = REPO_ROOT / "data" / "oddball.log"
RECOVERED_CSV = REPO_ROOT / "media" / "data" / "2026-09-03-wasted-turns.raw.csv"

ROUTE_VALUES = frozenset(r.value for r in AgentRoute)

# `engine/core.py:836` — `LOG.info("route %r -> %s (%s)", text, t.route, decision.reasoning)`.
#
# The utterance is `%r`, so it arrives as a **Python repr**: single-quoted unless it contains an
# apostrophe, with backslash escapes. `ast.literal_eval` is what turns that back into the string
# rather than a strip of the outer quotes, which would mangle every "what's" and "I'm" in the
# corpus — and those are most of them. tasks/lessons.md: in this repo, parse the structure, not
# the prose.
_ROUTE_LINE = re.compile(r"route (?P<utterance>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\") "
                         r"-> (?P<route>[a-z]+) \((?P<why>.*)\)\s*$")

# The two free branches log through the SAME format string, and telling them apart is the
# difference between scoring Gemini and scoring a keyword list. `engine/core.py:1076` ends
# "(local, no api call)" and `:1108` ends "(corpus, no api call)"; `local_router.route_locally`
# ends "(local qwen2.5:1.5b, 412ms, no api call)". Every free branch in the repo ends with this
# phrase, and that is now a contract `tools/verify_local_router.py` asserts on.
_FREE_MARKER = "no api call"


@dataclass
class Example:
    """One labelled utterance: what was said, where it went, and how much that label is worth."""

    utterance: str
    route: str
    tier: str                       # "gold" | "silver" | "extra"
    source: str                     # the file it came from, for auditing a surprising row
    reachable: bool = True          # would this still cost a router call today?
    claimed_by: str = ""            # which free path takes it, when it is not reachable
    notes: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------------
# would this utterance still reach the paid router?
# --------------------------------------------------------------------------------------------

def _free_paths():
    """The repo's own free branches, imported late so a caller can build a corpus without them.

    Returns `(look_up, instant_router, free_intents)`, or `(None, None, frozenset())` if the
    engine cannot be imported. Degrading is deliberate: reachability is an *annotation*, and a
    corpus that cannot be annotated is still a corpus. The gym reports which it got.
    """
    try:
        from engine.core import Engine                                 # noqa: PLC0415
        from orchestrator import file_intent, launch_intent, note_intent  # noqa: PLC0415
        from orchestrator.instant import Router as InstantRouter       # noqa: PLC0415
        from orchestrator.route_hint import look_up                    # noqa: PLC0415
    except Exception:                                                  # noqa: BLE001
        return None, None, frozenset()

    # **The router must be built the way `Engine` builds it, or this annotation is fiction.**
    #
    # This used to be a bare `InstantRouter()`. `engine/core.py:1011` passes three planners,
    # and without them `instant.route` cannot recognise a note, a file request or a launch —
    # so every one of them was annotated `reachable=True` and scored against the router.
    #
    # Measured in the 2026-09-10 gym: seventeen `os` rows, eleven of them ordinary vault
    # notes that `note_intent` answers for nothing. They were counted as router errors, and
    # the conclusion drawn from them was that the ROUTER's prompt needed rewriting — a change
    # that would have moved free work onto a paid path to fix a number this function got wrong.
    planners = {"note": note_intent.look_up, "file": file_intent.look_up,
                "launch": launch_intent.look_up}

    # `Engine.FREE_INTENTS` is not the whole free set either. A dismissal is handled at
    # `engine/core.py:1050` by its own branch ABOVE the FREE_INTENTS check, so "sleep" never
    # appears in that frozenset — and "Go to sleep." was therefore scored as a routing miss.
    # The planner keys are read off the dict rather than retyped, so the two cannot drift.
    free = frozenset(Engine.FREE_INTENTS) | {Engine.SLEEP_ROUTE} | set(planners)
    return look_up, InstantRouter(planners=planners), free


def reachable_today(utterance: str, paths=None) -> tuple[bool, str]:
    """False when a free path answers this utterance before the router is consulted.

    Returns `(reachable, claimed_by)` — the second is "hint:os", "free:ack" or "" so a
    surprising exclusion can be argued with rather than merely obeyed.

    Order matches `Engine._routed_turn`: the free intents are checked in front of the router,
    and `route_hint` in front of it too. Getting the order wrong here would only change the
    label in `claimed_by`, not the verdict, but a report that names the wrong culprit is worse
    than one that names none.

    ## The third free path is deliberately not modelled

    `_routed_turn` has three passes in front of the router, not two: `_free_turn`,
    `_hinted_route` and `_corpus_route` (`engine/core.py:1102`), and this reproduces the first
    two. `orchestrator/corpus_hint` needs a loaded vector store and an embedding model, which
    would turn a corpus build from instant and offline into a slow one that needs `chroma_db`
    present — for an annotation.

    What it costs, stated so it is not discovered later: firmware-ish rows that the corpus band
    would claim for free are scored as router-bound, which makes the reachable count slightly
    OPTIMISTIC about how much traffic the router still sees. The recovered corpus contains no
    firmware rows at all, so today the error is zero; it becomes real the day one appears.
    """
    look_up, instant, free_intents = paths or _free_paths()
    if look_up is None:
        return True, ""

    try:
        reply = instant.route(utterance)
        if reply.handled and reply.intent in free_intents:
            return False, f"free:{reply.intent}"
        hint = look_up(utterance)
        if hint:
            return False, f"hint:{hint}"
    except Exception as exc:                                           # noqa: BLE001
        # An annotation must never take the corpus down with it. Recorded, not swallowed.
        return True, f"unknown:{type(exc).__name__}"
    return True, ""


# --------------------------------------------------------------------------------------------
# the three sources
# --------------------------------------------------------------------------------------------

def from_log(log_path: Path = LIVE_LOG) -> list[Example]:
    """Gold rows: routing decisions Gemini actually made, on the turn path.

    Free-path lines share the format string and are excluded by their `no api call` suffix.
    A malformed line is skipped rather than raising — this reads a log written by a live
    process that may have been killed mid-write.
    """
    if not log_path.exists():
        return []

    found: list[Example] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _ROUTE_LINE.search(line)
        if not match:
            continue
        why = match.group("why")
        if why.endswith(_FREE_MARKER):
            continue
        route = match.group("route")
        if route not in ROUTE_VALUES:
            continue
        try:
            utterance = ast.literal_eval(match.group("utterance"))
        except (ValueError, SyntaxError):
            continue
        if isinstance(utterance, str) and utterance.strip():
            found.append(Example(utterance=utterance, route=route, tier="gold",
                                 source=log_path.name, notes={"reasoning": why}))
    return found


def from_recovered_csv(csv_path: Path = RECOVERED_CSV) -> tuple[list[Example], dict[str, int]]:
    """Silver rows, plus a count of what was dropped and why.

    The drop reasons are returned rather than logged because they are the honest part of this
    file: 30 of the 127 rows carry a "route" that is not an `AgentRoute` at all, and a report
    that quietly showed 97 would be hiding the most interesting thing in the source.
    """
    if not csv_path.exists():
        return [], {"missing file": 1}

    kept: list[Example] = []
    dropped: dict[str, int] = {}
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            utterance = (row.get("transcript") or "").strip()
            route = (row.get("route") or "").strip()
            if not utterance:
                dropped["empty transcript"] = dropped.get("empty transcript", 0) + 1
                continue
            if route not in ROUTE_VALUES:
                key = f"not an AgentRoute: {route!r}"
                dropped[key] = dropped.get(key, 0) + 1
                continue
            kept.append(Example(utterance=utterance, route=route, tier="silver",
                                source=csv_path.name,
                                notes={"agent_s": row.get("agent_s", "")}))
    return kept, dropped


# The four routes eight days of real use never once produced, and four probes each.
#
# ## Why this exists, and why it is a separate tier
#
# The recovered corpus is 82 rows of ordinary evenings: persona 37, os 19, general 12. It
# contains **no firmware, no quiz, no screen and no web at all** — which is a fact about how LB
# talks to the rig, not a fact about the router. Scoring a replacement router on that corpus and
# reporting one number would certify a model that has never been asked a single datasheet
# question, on a rig whose whole purpose is electrical engineering.
#
# So these are labelled `seed` and **kept out of the headline agreement rate**, because their
# label is LB's judgement rather than a decision Gemini made. They answer a different and
# narrower question, which the report asks separately: *can this model reach every route at all?*
# A model that cannot emit `firmware` for "what's the pinout of the ESP32" is disqualified
# before its match rate is worth reading.
#
# Every line is lifted from a place in this repo that already asserts the routing: the quoted
# examples in `router.py`'s own ROUTER_PROMPT, and the `live()` probes in
# `tools/verify_agents.py`. Nothing here is a new opinion about where a question belongs.
SEED: tuple[tuple[str, str], ...] = (
    ("whats the pinout of the esp32", "firmware"),
    ("how do I set the prescaler bits on timer one", "firmware"),
    ("what does the datasheet say about the sleep current", "firmware"),
    ("write me an interrupt handler in C for the uart", "firmware"),

    ("quiz me on filters", "quiz"),
    ("test me on calculus", "quiz"),
    ("ask me some questions about Kant", "quiz"),
    ("quiz me on the french revolution", "quiz"),

    ("what am I looking at", "screen"),
    ("what does that error say", "screen"),
    ("read that dialog to me", "screen"),
    ("why is this window complaining", "screen"),

    ("how much does an stm32 nucleo board cost right now", "web"),
    ("whats the latest news on the raspberry pi 5", "web"),
    ("look up the current price of copper", "web"),
    ("search the web for a replacement for the lm317", "web"),

    # Two routes the real corpus has exactly one and two rows of. Not enough to see a pattern
    # in, and both are core EE traffic.
    ("what trace width do I need for five amps", "hardware"),
    ("how wide does this power line need to be", "hardware"),
    ("whats on my amp schematic", "hardware"),
    ("design a low pass filter with a cutoff of one kilohertz", "math"),
    ("what is the reactance of a ten microfarad cap at sixty hertz", "math"),
)


def from_seed() -> list[Example]:
    """Hand-authored coverage probes for the routes real use never produced."""
    return [Example(utterance=text, route=route, tier="seed", source="SEED in this file")
            for text, route in SEED]


def from_file(path: Path) -> list[Example]:
    """An `--extra` corpus: JSONL of `{utterance, route}`, or CSV with those two columns.

    Both formats accept `transcript` as an alias for `utterance`, because that is what the
    repo's own CSVs call the column and nobody should have to rename it to use one.
    """
    rows: list[dict] = []
    if path.suffix.lower() == ".jsonl":
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    else:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))

    out: list[Example] = []
    for row in rows:
        utterance = (row.get("utterance") or row.get("transcript") or "").strip()
        route = (row.get("route") or "").strip()
        if utterance and route in ROUTE_VALUES:
            # **The tier is NOT taken from the file, and the temptation to allow it is the
            # point.** `gold` means "Gemini decided this, on the turn path", and the report
            # prints it under exactly those words. A hand-written CSV with a `tier` column
            # reading `gold` would have been counted there and believed — the one number in
            # this whole apparatus that has to be earned rather than declared.
            out.append(Example(utterance=utterance, route=route, tier="extra",
                               source=path.name))
    return out


# --------------------------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------------------------

def build(extra: Path | None = None, annotate: bool = True,
          seed: bool = True) -> tuple[list[Example], dict]:
    """Every available example, deduplicated, annotated, best tier first.

    Deduplication is by the utterance **as spoken**, not normalised: two transcripts that differ
    only in punctuation are two different inputs to the model, and collapsing them would hide
    exactly the fragility a 1.5B is most likely to have. Gold wins a collision with silver,
    because gold's label is the one that was made by the model being replaced.
    """
    gold = from_log()
    silver, dropped = from_recovered_csv()
    extras = from_file(extra) if extra else []
    seeds = from_seed() if seed else []

    by_utterance: dict[str, Example] = {}
    rank = {"gold": 0, "extra": 1, "silver": 2, "seed": 3}
    for example in sorted(gold + extras + silver + seeds, key=lambda e: rank.get(e.tier, 4)):
        by_utterance.setdefault(example.utterance, example)

    examples = list(by_utterance.values())

    if annotate:
        paths = _free_paths()
        for example in examples:
            example.reachable, example.claimed_by = reachable_today(example.utterance, paths)

    stats = {
        "gold": sum(1 for e in examples if e.tier == "gold"),
        "silver": sum(1 for e in examples if e.tier == "silver"),
        "extra": sum(1 for e in examples if e.tier == "extra"),
        "seed": sum(1 for e in examples if e.tier == "seed"),
        "reachable": sum(1 for e in examples if e.reachable),
        "duplicates_dropped": (len(gold) + len(silver) + len(extras) + len(seeds)
                               - len(examples)),
        "csv_dropped": dropped,
        "annotated": annotate and _free_paths()[0] is not None,
    }
    return examples, stats


def save(examples: list[Example], path: Path = CORPUS_PATH) -> Path:
    """Write the corpus as JSONL, **merging with whatever is already there**.

    ## Why this merges instead of overwriting

    It used to open `"w"` and write `build()`'s output, and the docstring above claimed that
    "gold accumulates from here forward". Both halves were false together, which is the worst
    way for a claim to be wrong.

    `engine/run_voice.py` opens the log with `logging.FileHandler(args.log, mode="w")` — the
    log is **truncated on every start**. So `from_log` can only ever see the gold rows from the
    current run, and an overwriting save meant every rebuild silently destroyed the gold
    harvested before the last restart. The corpus could never grow past one session, and the
    plan that says the 50-100 real examples are "a few days of ordinary use away" could not
    happen.

    Merging by utterance, better tier winning, makes that plan true: run the assistant, run
    `tools/router_gym_corpus.py`, and the day's decisions are added to the ones already banked.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[Example] = []
    if path.exists():
        try:
            existing = load(path)
        except (ValueError, TypeError):
            # A corrupt corpus must not stop a rebuild — it is derived data, and refusing to
            # write would leave the broken file in place forever.
            existing = []

    rank = {"gold": 0, "extra": 1, "silver": 2, "seed": 3}
    merged: dict[str, Example] = {}
    for example in sorted(list(examples) + existing, key=lambda e: rank.get(e.tier, 4)):
        merged.setdefault(example.utterance, example)

    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for example in merged.values():
            fh.write(json.dumps(asdict(example), ensure_ascii=False) + "\n")
    return path


def load(path: Path = CORPUS_PATH) -> list[Example]:
    """Read a corpus back. Unknown keys are ignored so an older file still loads."""
    fields = set(Example.__dataclass_fields__)
    out: list[Example] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out.append(Example(**{k: v for k, v in row.items() if k in fields}))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse                                                    # noqa: PLC0415

    parser = argparse.ArgumentParser(description="assemble the router gym corpus")
    parser.add_argument("--extra", type=Path, help="another JSONL or CSV of utterance,route")
    parser.add_argument("--no-seed", action="store_true",
                        help="leave out the hand-authored coverage probes")
    parser.add_argument("--out", type=Path, default=CORPUS_PATH)
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    examples, stats = build(extra=args.extra, seed=not args.no_seed)
    save(examples, args.out)

    print(f"\n  {len(examples)} labelled utterances -> "
          f"{args.out.relative_to(REPO_ROOT).as_posix()}\n")
    print(f"    gold   {stats['gold']:>4}  (Gemini decided these on the turn path)")
    print(f"    silver {stats['silver']:>4}  (route the turn took, recovered 2026-09-03)")
    print(f"    extra  {stats['extra']:>4}")
    print(f"    seed   {stats['seed']:>4}  (hand-authored probes for routes real use never hit)")
    print(f"\n    reachable today  {stats['reachable']:>4}  "
          f"(the rest are answered free before the router)")
    if stats["duplicates_dropped"]:
        print(f"    duplicates dropped {stats['duplicates_dropped']:>2}")
    if not stats["annotated"]:
        print("    NOTE: reachability could not be annotated — the engine did not import.")

    if stats["csv_dropped"]:
        print("\n  Dropped from the recovered CSV:")
        for reason, count in sorted(stats["csv_dropped"].items(), key=lambda kv: -kv[1]):
            print(f"    {count:>3}  {reason}")

    counts: dict[str, int] = {}
    for example in examples:
        counts[example.route] = counts.get(example.route, 0) + 1
    print("\n  By route:")
    for route in sorted(ROUTE_VALUES):
        n = counts.get(route, 0)
        flag = "   <- no examples" if n == 0 else ""
        print(f"    {route:<10} {n:>4}{flag}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
