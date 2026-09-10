#!/usr/bin/env python3
"""
Module:  verify_local_router.py
Purpose: Prove the local router's grammar, its toggle and its log parser, without a server.
Author:  LB
Date:    2026-09-10

    python tools/verify_local_router.py

Keyless and network-free, per D7. **No request is ever sent** — `requests.post` is replaced for
the three checks that need a reply, and section 6 asserts that the real one was never called.

## What is worth checking here, and what is not

The thing this stage claims is that a 1.5B model *cannot* emit an invalid route, because the
schema becomes a decoding grammar rather than a request. That claim lives in two places and
only one of them can be tested offline:

    the schema        this file. The enum is generated from AgentRoute, the payload is shaped
                      the way Ollama's /api/chat wants, and the prompt is Gemini's prompt.
    the grammar       `tools/evaluate_local_router.py`, against a real server, because whether
                      Ollama honours `format` is a fact about Ollama.

So this harness tests the half that is ours. The most valuable section is 3: the log parser
that separates a Gemini decision from a free-path one. Get that wrong and the gym silently
scores Qwen against `orchestrator/route_hint.py`'s keyword list, reports a number, and the
number means nothing — a green that is worse than a red.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GOOGLE_API_KEY", "harness-not-a-real-key-but-long-enough-to-pass")
os.environ.setdefault("ODDBALL_VAULT_DIR", tempfile.mkdtemp(prefix="oddball-harness-vault-"))

import requests                                                        # noqa: E402

import orchestrator.local_router as local                              # noqa: E402
import router as router_mod                                            # noqa: E402
import tools.router_gym_corpus as corpus                               # noqa: E402
from router import AgentRoute, ROUTER_PROMPT, RouteDecision            # noqa: E402
from tools.harness_lib import bootstrap, check, counts, section        # noqa: E402

REPO_ROOT = bootstrap()

# Every real network call this harness might have made, counted. Section 6 reads it.
_REAL_POST = requests.post
_SENT: list[str] = []

# Snapshotted HERE, at import, before any section can touch it. Section 5 used to set
# `_router_chain = None` itself and then assert that no chain had been built - erasing the
# evidence it was looking for, so the check would have survived a regression. What that
# assertion is really about is what `import router` does on its own, and by this line that
# has already happened.
_CHAIN_AT_IMPORT = router_mod._router_chain


class _Reply:
    """The shape `requests.post` returns, reduced to what local_router touches."""

    def __init__(self, payload: object, status: int = 200, text: str = "") -> None:
        self.status_code = status
        self.ok = 200 <= status < 400
        self.text = text or json.dumps(payload)
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


def _answers(content: str, status: int = 200):
    """A stand-in for requests.post that replies with `content` as the model's message."""
    def _post(url, **kwargs):
        _SENT.append(url)
        return _Reply({"message": {"role": "assistant", "content": content}}, status)
    return _post


def _raises(exc: Exception):
    def _post(url, **kwargs):
        _SENT.append(url)
        raise exc
    return _post


# =========================================================================================

def s1_schema() -> None:
    section("1. the grammar is generated from AgentRoute, not typed out beside it")

    schema = local.route_schema()
    enum = schema["properties"]["destination"]["enum"]

    check(enum == [r.value for r in AgentRoute],
          "destination.enum is exactly AgentRoute, in order",
          f"enum={enum}")
    check(set(enum) == {r.value for r in AgentRoute},
          "no route can be routed to that the enum does not permit")

    # The check that bites when somebody adds a twelfth route and forgets this file. It cannot
    # fail today; it exists so that it can fail later, which is the whole reason verify_router.py
    # has the same shape of assertion about route_hint.
    check(len(enum) == len(AgentRoute) == 11,
          f"all {len(AgentRoute)} routes are in the grammar",
          "a route added to the enum is in the grammar with nothing else edited")

    check(schema["required"] == ["destination", "reasoning"],
          "both fields are required, so neither can be omitted")
    check(list(schema["properties"]) == ["destination", "reasoning"],
          "destination is emitted FIRST — the model commits to a route before explaining it")
    check(schema["properties"]["reasoning"]["type"] == "string",
          "reasoning is a plain string")

    # Fields RouteDecision has must be fields the grammar produces, or validation fails on a
    # live turn. This is the pair that has to agree and is easiest to let drift.
    check(set(schema["properties"]) == set(RouteDecision.model_fields),
          "the schema's keys are exactly RouteDecision's fields",
          f"schema={sorted(schema['properties'])} model={sorted(RouteDecision.model_fields)}")


def s2_payload() -> None:
    section("2. the payload is Ollama's native shape, and Gemini's prompt")

    payload = local.build_payload("what trace width do I need for five amps", "qwen2.5:1.5b")

    check(payload["model"] == "qwen2.5:1.5b", "the model name is passed through")
    check(payload["stream"] is False, "stream=False — one JSON body, not a token stream")
    check(payload["format"] == local.route_schema(),
          "the SCHEMA goes in `format`, which is what makes it a grammar",
          "not `response_format`, which is the OpenAI-compatible surface and only a request")
    check(payload["options"]["temperature"] == 0.0,
          "temperature 0, matching ChatGoogleGenerativeAI(temperature=0.0) in router.py")
    check(payload["options"]["num_predict"] > 0,
          "num_predict caps the reasoning string — the grammar bounds shape, not length")
    check(payload["keep_alive"] == local.KEEP_ALIVE,
          f"keep_alive={local.KEEP_ALIVE} so the model stays resident between turns")

    messages = payload["messages"]
    check(len(messages) == 1 and messages[0]["role"] == "user",
          "ONE user message — exactly what ChatPromptTemplate.from_template sends to Gemini",
          "a system/user split would make the gym compare two prompts instead of two models")
    check(messages[0]["content"] == ROUTER_PROMPT.format(
              question="what trace width do I need for five amps"),
          "the prompt is ROUTER_PROMPT, byte for byte")
    check("json" not in messages[0]["content"].lower().split("user query")[0][-200:],
          "nothing begs the model for JSON — under a grammar it cannot produce anything else")

    check(local.native_base_url().endswith("11434"),
          "the /v1 suffix is stripped for the native endpoint",
          f"got {local.native_base_url()!r}")

    os.environ["ODDBALL_LOCAL_ROUTER_BASE_URL"] = "http://127.0.0.1:11434"
    check(local.native_base_url() == "http://127.0.0.1:11434",
          "a URL WITHOUT /v1 is left alone — both spellings work")
    os.environ["ODDBALL_LOCAL_ROUTER_BASE_URL"] = "http://127.0.0.1:11434/v1/"
    check(local.native_base_url() == "http://127.0.0.1:11434",
          "a trailing slash after /v1 is handled too")
    del os.environ["ODDBALL_LOCAL_ROUTER_BASE_URL"]


def s3_log_parser() -> None:
    section("3. the gym scores Gemini's decisions and NOTHING ELSE")

    # The three lines engine/core.py can emit, written out in the exact format the logging
    # module produces them in. Only the first is a paid decision.
    paid = ("07:35:32 INFO    route 'Do I have any assignments due to the' -> academic "
            "(The user is asking about upcoming assignment deadlines.)")
    hint = "07:35:32 INFO    route 'how much disk space is left' -> os (local, no api call)"
    corp = "07:35:32 INFO    route 'esp32 sleep current' -> firmware (corpus, no api call)"
    qwen = ("07:35:32 INFO    route 'quiz me on filters' -> quiz "
            "(The user wants to be tested. — local, no api call)")

    log = REPO_ROOT / "tools" / "_verify_local_router_tmp.log"
    log.write_text("\n".join([paid, hint, corp, qwen]) + "\n", encoding="utf-8")
    try:
        found = corpus.from_log(log)
    finally:
        log.unlink(missing_ok=True)

    check(len(found) == 1, "exactly ONE of four route lines is a Gemini decision",
          f"got {[(e.utterance, e.route) for e in found]}")
    if found:
        check(found[0].route == "academic" and found[0].tier == "gold",
              "the paid line is harvested, tagged gold")
    check(all("no api call" not in e.notes.get("reasoning", "") for e in found),
          "no free-path line survives the filter",
          "scoring against route_hint's keyword list would be a green that means nothing")

    # `%r` is a Python repr, so an apostrophe changes the quoting of the WHOLE line. This is the
    # case a naive strip("'") corrupts, and most of LB's speech has an apostrophe in it.
    apostrophe = ('07:35:32 INFO    route "what\'s on my amp schematic" -> hardware '
                  '(Reading his own KiCad file is the hardware agent\'s job.)')
    log.write_text(apostrophe + "\n", encoding="utf-8")
    try:
        found = corpus.from_log(log)
    finally:
        log.unlink(missing_ok=True)
    check(len(found) == 1 and found[0].utterance == "what's on my amp schematic",
          "a double-quoted repr round-trips through ast.literal_eval with the apostrophe intact",
          f"got {[e.utterance for e in found]}")

    # A route the enum does not have must not enter the corpus. `sleep` and `note` are logged
    # as routes by the turn line and are free intents, not agents.
    bogus = "07:35:32 INFO    route 'goodnight' -> sleep (whatever)"
    log.write_text(bogus + "\n", encoding="utf-8")
    try:
        found = corpus.from_log(log)
    finally:
        log.unlink(missing_ok=True)
    check(not found, "a route that is not an AgentRoute is refused",
          "sleep/note/correction are intents; a model cannot be scored against a label it "
          "cannot emit")


def s4_decision() -> None:
    section("4. a reply becomes a RouteDecision, and a bad one becomes a named error")

    good = json.dumps({"destination": "hardware", "reasoning": "trace width is IPC-2221."})
    requests.post = _answers(good)
    try:
        decision = local.route_locally("trace width for 5 amps", model="qwen2.5:1.5b")
        check(decision.destination is AgentRoute.HARDWARE,
              "a valid body becomes a RouteDecision with the right route")
        check(isinstance(decision, RouteDecision),
              "the SAME type Gemini returns — nothing downstream can tell them apart")
    except local.LocalRouterError as exc:
        check(False, "a valid body becomes a RouteDecision", str(exc))

    # The failure the grammar is supposed to make impossible. Checked anyway: this module's
    # whole claim is that the grammar holds, and an unguarded json.loads would turn a broken
    # claim into a traceback three frames from where it was made.
    for body, what in ((good.join(("Here is the JSON:\n```json\n", "\n```")),
                        "conversational filler and a markdown fence"),
                       (json.dumps({"destination": "kitchen", "reasoning": "why not"}),
                        "a route that is not in the enum"),
                       (json.dumps({"reasoning": "no destination at all"}),
                        "a missing required field"),
                       ("", "an empty body")):
        requests.post = _answers(body)
        try:
            local.route_locally("anything", model="qwen2.5:1.5b")
            check(False, f"{what} is rejected", "it was accepted as a route")
        except local.LocalRouterError:
            check(True, f"{what} is rejected as LocalRouterError, not a raw exception")

    for exc, what, wanted in (
            (requests.exceptions.ConnectionError("refused"), "a dead server", "ollama serve"),
            (requests.exceptions.Timeout("slow"), "a timeout", "cold model")):
        requests.post = _raises(exc)
        try:
            local.route_locally("anything", model="qwen2.5:1.5b")
            check(False, f"{what} raises", "it returned normally")
        except local.LocalRouterError as err:
            check(wanted in str(err).lower() or wanted in str(err),
                  f"{what} names the fix, not just the fault", str(err)[:88])

    requests.post = _answers("{}", status=404)
    try:
        local.route_locally("anything", model="not-pulled")
        check(False, "a 404 raises")
    except local.LocalRouterError as err:
        check("ollama pull not-pulled" in str(err),
              "a 404 says which command pulls the missing model", str(err)[:88])

    requests.post = _REAL_POST


def s5_toggle() -> None:
    section("5. the toggle, and the fallback that must not be silent")

    was = os.environ.pop("ODDBALL_LOCAL_ROUTER", None)
    try:
        check(local.local_router_model() == "", "unset means no local model")
        check(router_mod.router_provider() == "google",
              "unset leaves routing on Gemini — the seam is inert until named")

        os.environ["ODDBALL_LOCAL_ROUTER"] = "  qwen2.5:1.5b  "
        check(local.local_router_model() == "qwen2.5:1.5b",
              "the model name is stripped, so a stray space in .env cannot break the toggle")
        check(router_mod.router_provider() == "local", "a model name switches the provider")

        # Read per call, not captured at import. A harness that has to reload a module to change
        # a variable is a harness that will forget to.
        os.environ["ODDBALL_LOCAL_ROUTER"] = ""
        check(router_mod.router_provider() == "google",
              "clearing it switches back with no reload")

        # The fallback. Gemini's chain must NOT be built while it is unused, and it must be
        # reached when the local router dies — those are the two halves of the same branch.
        os.environ["ODDBALL_LOCAL_ROUTER"] = "qwen2.5:1.5b"
        requests.post = _raises(requests.exceptions.ConnectionError("refused"))
        router_mod._router_chain = None
        called: list[str] = []

        class _FakeChain:
            def invoke(self, payload):
                called.append(payload["question"])
                return RouteDecision(destination=AgentRoute.GENERAL, reasoning="from gemini")

        real_builder = router_mod._gemini_chain
        router_mod._gemini_chain = lambda: _FakeChain()
        try:
            decision = router_mod.router_agent("a question")
            check(called == ["a question"] and decision.reasoning == "from gemini",
                  "a dead Ollama falls back to Gemini rather than taking him off the air")

            os.environ["ODDBALL_LOCAL_ROUTER_STRICT"] = "1"
            called.clear()
            try:
                router_mod.router_agent("a question")
                check(False, "STRICT=1 raises instead of paying")
            except local.LocalRouterError:
                check(not called, "STRICT=1 raises and never reaches Gemini",
                      "the gym must never score a fallback as a local success")
        finally:
            router_mod._gemini_chain = real_builder
            os.environ.pop("ODDBALL_LOCAL_ROUTER_STRICT", None)
            requests.post = _REAL_POST
    finally:
        os.environ.pop("ODDBALL_LOCAL_ROUTER", None)
        if was is not None:
            os.environ["ODDBALL_LOCAL_ROUTER"] = was

    # **The chain IS built at import now, deliberately, and the snapshot is what proves it.**
    # Lazy construction was tried on 2026-09-10 and reverted the same day: it moved the build
    # onto the daemon thread that `_route_within_deadline` ABANDONS, so the first Gemini
    # fallback would have imported langchain inside the 20-second turn budget, and two
    # abandoned threads could be inside the initialiser at once. Building at import costs one
    # object on a local-only rig and removes both problems.
    check(_CHAIN_AT_IMPORT is not None,
          "the Gemini chain is built at import, on the main thread, before any turn exists",
          "so no turn pays to construct it and no two threads race to")


def s6_corpus() -> None:
    section("6. the corpus is real, labelled, and nothing was sent to a server")

    examples, stats = corpus.build()

    # **On a fresh clone this is a different number, deliberately.** The silver rows come from
    # `media/data/2026-09-03-wasted-turns.raw.csv`, which is gitignored — it is verbatim speech
    # in LB's house. So the harness asserts what is true on the machine it is running on rather
    # than a constant that would go red for anyone who clones. Exactly the split the wake
    # fixtures already have, and .gitignore says so there too.
    have_recovered = corpus.RECOVERED_CSV.exists()
    if not have_recovered:
        print("           (no *.raw.csv on this machine — corpus is seeds plus whatever the "
              "live log holds)")
    wanted = 50 if have_recovered else len(corpus.SEED)
    check(len(examples) >= wanted, f"{len(examples)} labelled examples assembled",
          "the brief asked for 50-100" if have_recovered else
          "seed-only on this clone, which is enough to check coverage but not agreement")
    check(stats["silver"] > 0 or not have_recovered,
          "the recovered 2026-09-03 transcripts are in it",
          "the live log is truncated; that file is the surviving record of eight days")

    routes = {e.route for e in examples}
    check(routes == set(corpus.ROUTE_VALUES),
          "every one of the eleven routes has at least one example",
          f"missing: {sorted(set(corpus.ROUTE_VALUES) - routes)}")
    check(all(e.route in corpus.ROUTE_VALUES for e in examples),
          "no example carries a label the model cannot emit")

    seeds = [e for e in examples if e.tier == "seed"]
    check(seeds and all(not e.notes for e in seeds),
          "hand-authored probes are tiered `seed`, so they stay out of the agreement rate")

    utterances = [e.utterance for e in examples]
    check(len(utterances) == len(set(utterances)), "no duplicate utterances")

    check(corpus.CORPUS_PATH.name in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8"),
          "the corpus file is gitignored — it is verbatim speech in LB's house",
          "memory: never-publish-personal-data, and the copilot repo is public")

    check(not _SENT or all("127.0.0.1" in u for u in _SENT),
          f"no request left this machine ({len(_SENT)} stubbed, 0 real)",
          f"urls={sorted(set(_SENT))}")
    check(requests.post is _REAL_POST, "requests.post was restored after the stubs")


def s7_no_self_scoring() -> None:
    section("7. through the real Engine: a LOCAL decision cannot be harvested as Gemini's")

    # The failure this section exists for, stated before it is tested. With the router local,
    # `engine/core.py` logs a routing decision exactly as it always did. If that line is not
    # marked, `router_gym_corpus.from_log` harvests it as GOLD — and the next gym run scores
    # qwen2.5:1.5b against qwen2.5:1.5b's own past answers and reports agreement approaching
    # 100%. The number would be real, reproducible, and about nothing.
    import logging                                                     # noqa: PLC0415

    import engine.core as core                                         # noqa: PLC0415
    from engine.core import Engine                                     # noqa: PLC0415

    class _Decision:
        destination = AgentRoute.QUIZ
        reasoning = "The user wants to be tested."

    lines: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler = _Capture()
    logger = logging.getLogger("oddball.engine")
    logger.addHandler(handler)
    was_level, logger.level = logger.level, logging.INFO
    real_router, core.router_agent = core.router_agent, lambda q: _Decision()
    was_env = os.environ.get("ODDBALL_LOCAL_ROUTER")

    try:
        for local_on, label in ((True, "local"), (False, "gemini")):
            lines.clear()
            if local_on:
                os.environ["ODDBALL_LOCAL_ROUTER"] = "qwen2.5:1.5b"
            else:
                os.environ.pop("ODDBALL_LOCAL_ROUTER", None)
            try:
                # The agent leg is allowed to fail; the route line is logged before dispatch.
                Engine().ask("quiz me on filters please, something hard")
            except Exception:                                          # noqa: BLE001
                pass

            routed = [ln for ln in lines if ln.startswith("route ")]
            check(len(routed) == 1, f"{label}: exactly one route line per turn",
                  f"got {routed}")
            if not routed:
                continue

            log = REPO_ROOT / "tools" / f"_verify_selfscore_{label}.log"
            log.write_text(routed[0] + "\n", encoding="utf-8")
            try:
                harvested = corpus.from_log(log)
            finally:
                log.unlink(missing_ok=True)

            if local_on:
                # The trailing ")" matters: `from_log` matches the parenthetical's CONTENTS,
                # so the marker it filters on is the last thing inside the brackets.
                check(routed[0].endswith("no api call)"),
                      "local: the route line is marked as costing nothing",
                      routed[0][:96])
                check(not harvested,
                      "local: the gym REFUSES it as ground truth",
                      "otherwise the model would be scored against its own past answers")
            else:
                check(not routed[0].endswith("no api call)"),
                      "gemini: the route line is unmarked, exactly as before this stage",
                      routed[0][:96])
                check(len(harvested) == 1 and harvested[0].tier == "gold",
                      "gemini: the gym DOES harvest it as gold",
                      "the filter must not have become a filter on everything")
    finally:
        core.router_agent = real_router
        logger.removeHandler(handler)
        logger.level = was_level
        os.environ.pop("ODDBALL_LOCAL_ROUTER", None)
        if was_env is not None:
            os.environ["ODDBALL_LOCAL_ROUTER"] = was_env


def s8_regressions() -> None:
    section("8. the five defects found in review, each with the assertion that pins it")

    # (a) An opted-OUT rig must not execute this module's import-time code, and must not be
    # breakable by a variable it never set. `router_provider()` imported local_router before
    # reading the environment, so a typo'd ODDBALL_LOCAL_ROUTER_TIMEOUT_S raised ValueError on
    # EVERY turn of a rig with the feature switched off.
    was = os.environ.pop("ODDBALL_LOCAL_ROUTER", None)
    os.environ["ODDBALL_LOCAL_ROUTER_TIMEOUT_S"] = "30s"
    try:
        # **Asserted on the parse tree, not on behaviour.** By this line the harness has
        # already imported local_router itself, so calling router_provider() and finding it
        # works proves nothing about what it would do on a clean rig. What has to be true is
        # structural: the function body contains no import at all. tasks/lessons.md — in this
        # repo, check the structure, not the prose.
        import ast                                                     # noqa: PLC0415

        tree = ast.parse((REPO_ROOT / "router.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "router_provider")
        imports = [n for n in ast.walk(fn) if isinstance(n, (ast.Import, ast.ImportFrom))]
        check(not imports,
              "router_provider() contains no import statement whatsoever",
              "it ran local_router's import-time code on every turn of every opted-out rig")
        check(router_mod.router_provider() == "google",
              "...and still answers correctly when the toggle is unset")
        check(local.timeout_s() > 0,
              "a non-numeric timeout falls back to the default instead of raising",
              "on the turn path a ValueError here would fail every turn of an opted-out rig")
    finally:
        os.environ.pop("ODDBALL_LOCAL_ROUTER_TIMEOUT_S", None)
        if was is not None:
            os.environ["ODDBALL_LOCAL_ROUTER"] = was

    # (b) The timeout that would have paid Gemini for a turn already answered.
    deadline = local._router_deadline_s()
    check(local.timeout_s() < deadline,
          f"the local timeout ({local.timeout_s():g}s) is under the router deadline "
          f"({deadline:g}s)",
          "otherwise core abandons the turn, THEN the late failure buys a discarded Gemini call")
    os.environ["ODDBALL_LOCAL_ROUTER_TIMEOUT_S"] = "600"
    try:
        check(local.timeout_s() < deadline,
              "an env var asking for 600s is clamped under the deadline too",
              f"got {local.timeout_s():g}s")
    finally:
        os.environ.pop("ODDBALL_LOCAL_ROUTER_TIMEOUT_S", None)

    # (c) `requests` applies its timeout per socket operation, so one float means twice the
    # wait. The connect and read halves are passed separately.
    seen: list = []

    def _capture(url, **kwargs):
        seen.append(kwargs.get("timeout"))
        raise requests.exceptions.ConnectionError("refused")

    requests.post = _capture
    try:
        local.route_locally("anything", model="qwen2.5:1.5b")
    except local.LocalRouterError:
        pass
    finally:
        requests.post = _REAL_POST
    check(bool(seen) and isinstance(seen[0], tuple) and len(seen[0]) == 2,
          "the timeout is a (connect, read) pair, not one float",
          f"got {seen[0]!r} — a single float applies to EACH socket operation")

    # (d) An --extra file must not be able to declare its own rows `gold`. Gold means "Gemini
    # decided this on the turn path" and the report prints it under exactly those words.
    forged = REPO_ROOT / "tools" / "_verify_forged_tier.csv"
    forged.write_text("utterance,route,tier\nquiz me on ohms law,quiz,gold\n", encoding="utf-8")
    try:
        injected = corpus.from_file(forged)
    finally:
        forged.unlink(missing_ok=True)
    check(bool(injected) and all(e.tier == "extra" for e in injected),
          "a file claiming tier=gold is clamped to extra",
          f"got {[e.tier for e in injected]} — gold has to be earned, not declared")

    # (e) Gold must survive a rebuild. `engine/run_voice.py` opens the log with mode="w", so it
    # is truncated on every start — and an overwriting save meant the corpus could never grow
    # past one session, which is exactly what the "a few days of use away" plan depends on.
    tmp = REPO_ROOT / "tools" / "_verify_merge_corpus.jsonl"
    try:
        corpus.save([corpus.Example("banked yesterday", "hardware", "gold", "old.log")], tmp)
        corpus.save([corpus.Example("harvested today", "math", "gold", "new.log")], tmp)
        merged = {e.utterance for e in corpus.load(tmp)}
        check(merged == {"banked yesterday", "harvested today"},
              "a second save MERGES rather than overwriting, so gold accumulates",
              f"got {sorted(merged)}")
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    print("\n  verify_local_router — the schema, the toggle, and the log parser\n")
    s1_schema()
    s2_payload()
    s3_log_parser()
    s4_decision()
    s5_toggle()
    s6_corpus()
    s7_no_self_scoring()
    s8_regressions()

    print("\n" + "=" * 78)
    print(f"  {counts.total} checks, {counts.passed} passed, {counts.failed} failed")
    print("=" * 78)
    if counts.failed:
        print(f"\n  {counts.failed} RED\n")
        return 1
    print(f"\n  {counts.passed}/{counts.total} checks passed — all green")
    print("  Whether Ollama HONOURS the grammar is a fact about Ollama: "
          "tools/evaluate_local_router.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
