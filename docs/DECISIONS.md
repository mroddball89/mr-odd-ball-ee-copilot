# Decisions

Numbered so they can be cited from code comments and commit messages. A decision here is one
somebody could reasonably have made differently — not a fact, and not a preference.

---

## D1 — The EE Copilot is the host, not the assistant

**2026-08-19.** Two working systems had to become one: the terminal EE Copilot and the
standalone voice assistant at `mroddball89/mr-odd-ball-ai`. Either could have absorbed the
other.

LB chose the copilot as the host. Mr Odd Ball's wake word, ears, voice, face and personality
move into it; `router.py` becomes the single dispatcher.

**What this cost.** The assistant's tier system — `orchestrator/classify.py`,
`orchestrator/tiers.py`, `brains/local.py`, `brains/gemini.py` — is not carried over. That
includes Tier 1, the local LFM2.5 that answered personality questions on the Pi with no quota
and no network. See D3, which is the bill for it arriving.

**What survived, and why it is not a tier.** `orchestrator/router.py` came across as
`orchestrator/instant.py`. It is the lookup tables — time, date, unit conversion, constants,
definitions, ~9,800 checks' worth — reached as one route among nine rather than as a tier in
front of everything. Deleting it would have thrown away working, tested code to satisfy a
naming convention.

---

## D2 — The spoken half comes from the agent, not from a summariser

**2026-08-19.** A reply has to become two things: something short enough to say, and
everything else. The obvious implementation is to generate the answer and then summarise it
down to 40 words.

We do the opposite. Every agent prompt ends with a `SPOKEN:` line contract, so the model that
wrote the answer writes the sentence too.

**Why.** A summariser reading a finished reply has to guess which of three numbers was the
result and which two were working. It guesses wrong on exactly the replies that matter — the
ones with intermediate values in them. The model that did the work does not have to guess.

Extraction (`memory/speakable.py`) is the fallback. **Generation never is**: D30 in the
assistant's own decision log measured local models stating first-year electronics relationships
fluently and wrongly, and a generated summary of a correct answer can be wrong the same way,
one step further from anywhere it would be noticed.

Whatever produces the sentence, `engine/split.py:is_speakable()` judges it. Policing the safe
path identically to the risky one is what stops the safe path drifting.

---

## D3 — The Gemini free tier is 20 requests per day, not ~1,500

**2026-08-19. This corrects a documented assumption, and it is load-bearing.**

`~/oddball/CLAUDE.md` records: *"A free Gemini API key from AI Studio is available separately
(~1,500 req/day)."* The first end-to-end run of `Engine.ask()` exhausted the quota in **five
questions**. From the 429 body:

```
quotaId:     GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaMetric: generativelanguage.googleapis.com/generate_content_free_tier_requests
quotaValue:  20
model:       gemini-3.5-flash
```

**Wrong by a factor of 75.** Data: `media/data/2026-08-19-gemini-free-tier-quota.csv`.

This is not a footnote, because the merged architecture spends requests faster than the
terminal copilot did: a turn costs a router call **plus** one or two agent calls. At 20/day
that is roughly seven to ten questions before he goes quiet for the rest of the day.

### What was done about it

The quota is **per model, per project, per day** — so each model name has its own bucket.
Splitting jobs across models multiplies the usable budget, and it is the right engineering
call independently:

| job | model | why |
|---|---|---|
| routing | `gemini-3.5-flash-lite` | 9-way classification against a fixed schema. No reasoning. **890 ms.** |
| agents | `gemini-3.5-flash` | register values, IPC-2221, physics — where accuracy is worth paying for |
| persona | `gemini-3.5-flash-lite` | jokes. Being wrong is cheap. |

Latency: `media/data/2026-08-19-router-model-latency.csv`. `gemini-3.1-flash-lite` also routed
correctly but took **25.5 s** cold, which is unusable on the turn path. `gemini-2.5-flash` and
`gemini-2.5-flash-lite` return 404 for this key.

`engine/models.py` is now the one place model names live — they were hardcoded in seven files,
so this was previously a seven-file edit that could be done in six.

### What is still open

The three real fixes, in the order LB should consider them:

1. **Widen UTILITY.** It already costs nothing and answers from lookup tables. Every question
   it absorbs is a free question.
2. **Enable billing.** LB's standing decision is no card, so this is his call.
3. **Put Tier 1 back for PERSONA.** `brains/local.py` ran a local LFM2.5 on the Pi with no
   quota at all, and chit-chat is exactly the traffic it was good at. D1 dropped it; D3 is the
   argument for bringing that one piece back.

A 429 is now reported as what it is — *"I've used up my 20 free questions for today"* — and
not as a crash. Reporting a quota ceiling as a fault sends LB looking for a bug that is not
there.

---

## D4 — The permission gate speaks a paraphrase and shows the exact command

**2026-08-19.** The gate used to block on `input("Allow execution? (y/n): ")`. A voice turn has
no stdin, so it had to become a suspended state: `propose_os_action()` returns a `Pending` and
nothing runs; `resume_os_action()` runs it after approval.

The hard part is what the question sounds like. `cat /sys/class/thermal/thermal_zone0/temp`
read aloud is *"cat slash sys slash class slash thermal slash thermal underscore zone zero
slash temp"* — LB cannot judge what he is approving from that. So `Pending` carries two
strings: a plain description for the ear, and the exact command for the eye.

**The risk is stated rather than solved.** Approving from a paraphrase means trusting the
paraphrase. What makes it acceptable:

- the exact text is rendered **before** the question is asked, not after
- the blocklist in `tools/os_controller.py` runs regardless of what was approved
- `orchestrator/classify_yes.py` treats silence, a mumble, a timeout and a refusal all as no
- if the model puts the command into its own description, the description is discarded and a
  fallback that points at the card is used instead

This reverses the Phase 6 note in `~/oddball/docs/STATE.md` — *"he authors the command, the
model never writes one"*. LB was shown the conflict and chose to keep the copilot's OS agent as
built. Recorded here so the reversal is deliberate and visible rather than an accident of the
merge.

---

## D5 — The quiz lock has a loose exit, and an escape that does not depend on hearing

**2026-08-19.** `main.py` left quiz mode on exactly one phrase: `exit quiz`. That is safe to
type and would not have survived being spoken — `tiny.en` turned *"What is the date?"* into
*"What is today?"* and *"Set a timer"* into *"at a timer"*.

`engine/core.py:_is_quiz_exit()` matches a family of phrases, plus single words (`exit`,
`quit`, `enough`, `stop`, `escape`) as **whole words** — loose on purpose, because the failure
modes are not symmetric. A false positive drops one answer and LB asks to be quizzed again. A
false negative traps him in a loop that keeps asking questions, with an imperfect transcriber
standing between him and the exit.

`Engine.leave_quiz()` is the way out that does not depend on being heard at all, and the HUD
carries a visible `QUIZ MODE` chip with the exit phrase printed on it. A mode you cannot see is
a mode you get stuck in.

---

## D6 — The typed channel does everything the voice does

**2026-08-19.** Typing was going to be for questions, with waking and dismissing left to the
microphone. That was wrong, and the Pi proved it within an hour of being deployed:

| | |
|---|---|
| capture gain | already maxed, 16/16 at +30 dB — no software headroom |
| peak mic RMS | 0.035–0.17, against ~0.1–0.3 for healthy speech |
| wake scores | 0.17–0.28 against a threshold of 0.76 |
| what Whisper heard | *"Don't you? Hey, hey, thank you. Everybody, I want to let you hold me."* |

The persona agent was politely answering that, which is what made an input fault look like an
agent fault.

**So the typed channel is not a convenience, it is the one that works.** It has to be able to
do everything the voice can, including the two things that are not questions:
`instant.is_wake()` and `instant.is_sleep()`. The second is a wrapper on the existing
`_is_dismissal` rather than a second list, so the typed and spoken doors out cannot drift.

Both use the **end-anchor rule** — the phrase has to BE the line, not appear in it. A question
that mentions him stays a question. `tools/verify_typed.py --probe` swaps in "contains the
phrase" and 4 of 12 negatives are then obeyed instead of answered, including *"why did my board
go to sleep"*. That is `verify_turn.py`'s "I bought it at the goodbye sale" bug arriving on a
new channel, caught before it shipped this time.

`_WAKE_FILLER` is deliberately a **different set** from `_DISMISS_FILLER`. "mr", "odd" and
"ball" are filler around a dismissal ("Mr Odd Ball, that's all") and are the entire content of a
wake phrase; one shared set would make "hey mr odd ball" reduce to nothing and match every wake
phrase at once.

**The underlying bug was worse than the missing feature.** `hud_bridge` had been collecting
`{"type":"text"}` on an inbound queue since the panel was built, and nothing drained it outside
a permission gate. Typing did nothing at all, silently. Built and not wired is the failure mode
a harness that only tests the transport cannot see — `verify_chat.py` proved the message
arrived, and it did arrive, at a queue nobody read.

---

## D7 — He runs on the Pi. Windows is for writing him.

**2026-08-19.** LB's call, after the Pi was working: *"I'm only going to use him on the pi."*
The Windows `.env` was deleted and should not be recreated.

The consequence is a constraint on everything written from here: **every harness must run with
no API key present**, because the machine they are authored on has none.
`tools/verify_agents.py` substitutes a dummy when what it loads is unusable, and says so;
`--live` is the only mode that needs a real key and it runs on the Pi.

This also settles what the two directories on the Pi are for. `~/mr-odd-ball` is the copilot.
`~/oddball` is the pre-merge assistant, stopped and disabled, kept as a fallback — not a
second install to keep in step.

---

## D8 — A guard written for a whole sentence was applied to a fragment

**2026-08-21.** LB: *"he is getting the ampere conversion wrong."* He was, in three unrelated
ways, and the three met on amperes because amperes are what an EE student converts most.
Measured before and after on fourteen questions —
`media/data/2026-08-21-ampere-conversion.csv`, chart beside it:

| | right | refused | **wrong** |
|---|---|---|---|
| before (`ff3a43b`) | 3 | 6 | **5** |
| after | 14 | 0 | **0** |

### 1. The symbol forms were refused — "5 A in mA" reached the network

`_NEEDS_A_NUMBER` says a one-letter alias only counts as a unit with a digit in front of it,
which is what tells `5 a` from `a mile`. That rule is correct about a whole utterance and
wrong about a **fragment**, and `_find_unit` only ever sees fragments: by the time it is
called, the frame regex has eaten the number into its own `amount` group, so
`convert 5 a to ma` arrives as `src="a"` with the 5 nowhere in it. Every symbol conversion an
engineer actually types — 5 A in mA, 5 V in mV, 10 F in uF, 5 s in ms — was refused.

A guarded alias now also passes when it **is the entire fragment**, because that is the
grammar saying a unit belongs there: nothing but a unit fills the `to ___` of
`convert 5 amps to ___`. The article is still safe, and for a reason worth keeping: in
`how many meters in a mile` the source fragment is `a mile`, where `a` is not the whole
fragment, so it stays an article.

The harness had a check pinning the old rule — `'m' does not parse an English word as a unit`.
It was asserting the bug. It has been split into the two halves that are actually true:
multi-word fragments must not yield a unit, bare fragments must.

### 2. The amount defaulted to 1 with the number still visible in the sentence

`amount` defaults to 1.0 so `how many meters in a mile` works. The default fired whenever the
number failed to land in the group, **not only when there was no number** —
`how many milliamps in point 5 amps` parsed as `amount=None, src="point 5 amps"` and he said
*"1 amp is 1000 milliamps."* The 5 was sitting in the fragment, and was answered as a 1.

This is D42's factor error arriving by a different road, and the fix is an invariant rather
than another special case: **the frame must account for every digit.** A digit left in either
fragment means the parse did not understand the question, so it refuses. Spoken decimals
(`point 5`, `.5`) are now parsed properly and answered; `1/2 amp` is refused rather than
guessed at. Refusing escalates to a tier that can answer — a wrong number does not.

### 3. The amp hour is charge, and he was reading it as current

`how many amps in 3000 milliamp hours` matched the bare `milliamp`, dropped the `hours`
silently, and answered *"3 amps"* — a dimensional error delivered as a confident number, which
is the exact failure the category check exists to prevent. Leaving battery capacity out of the
table did not make him refuse it; it made him answer it wrongly. `amperehour` is now in
`charge` at 3600 C, so mAh↔Ah↔C all work and mAh→A is refused by the check that was always
there.

`Unit.symbol_forms` exists for this one unit: the prefix machinery builds symbol forms only
from one-letter aliases, so `milli` + `ah` gives `milliah` and never `mah`.

### What is deliberately still refused

`kohm` and `mohm` are not aliases, and should not become them. `m` is milli in SI, so `mohm`
would resolve to milliohms — while roughly half the people who type it mean megohms. That is a
1,000,000x error with a plausible reading on both sides, which is the worst shape this table
can hold. `kilohm` and `megohm` (the standard spellings) both work.

---

## D9 — He reads LB's KiCad files with kiutils, and the obvious parser was measured first

**2026-08-21.** The HARDWARE agent could compute an IPC-2221 trace width and nothing else — it
had never been able to look at an actual design. It now has two tools,
`tools/kicad_parser.py`: `extract_kicad_bom` reads a schematic's parts, `analyze_kicad_pcb`
reads a board's layer stack, nets and footprints. Both work offline and neither needs KiCad
installed, which matters because the Pi does not have it and never will.

**The parser is `kiutils`, not a regex.** A `.kicad_sch` is an S-expression whose useful fields
sit four levels deep inside quoted strings that may themselves contain brackets. A regex over
that is a parser that works on the file you tested it against.

### The tutorial implementation was run before it was replaced

Measured on twelve questions against eight fixtures —
`media/data/2026-08-21-kicad-parser.csv`, chart beside it. "Before" is a **live run** of the
textbook version, kept verbatim in `media/scripts/measure_kicad_parser.py`, not a note of what
it used to do:

| | right | error | **wrong** |
|---|---|---|---|
| the tutorial parser | 2 | 7 | **3** |
| shipped | 12 | 0 | **0** |

**Every schematic question is in the error column, and one wrong attribute name puts it
there.** kiutils has no `Schematic.symbols` — it is `schematicSymbols`. Wrapped in the
customary `except Exception`, that AttributeError comes back as *"Failed to parse schematic:
'Schematic' object has no attribute 'symbols'"*, which reads like a corrupt file, for every
file. See L5.

The three genuinely **wrong** answers are all board questions, and all the same shape as D8 —
a number, said confidently, that nobody would question:

- **"29 layers"** for a two-layer board. `len(board.layers)` counts adhesive, paste,
  silkscreen, mask, courtyard, fab and nine user layers alongside the copper. What anybody
  means by "a four-layer board" is the copper count, so that is the headline and the table size
  is reported beside it, labelled.
- **"6 nets"** where there are 5. Net 0 is KiCad's unassigned net and exists on every board,
  including an empty one — it inflates every net count by exactly one, forever.

### Three more defects that the fixtures caught and the measurement does not show

**`inBom` defaults to `False` in kiutils, so absence and exclusion are the same value.** A
filter written as `if not symbol.inBom: continue` returns an **empty BOM** for any file that
does not write the `in_bom` token — and an empty BOM does not look like a failure, it looks
like an empty sheet. The exclusion rule is therefore KiCad's own `#` reference prefix, stable
across every format version; `inBom` is consulted only when some symbol in that file carries
`True`, which proves the token is being written. `tests/fixtures/kicad/no-inbom.kicad_sch` is a
file in the second state.

**A multi-unit part is several symbol blocks with one reference.** A TL074 is one 14-pin chip
drawn as four amplifiers plus a power unit — five blocks, all `U1`. Counting blocks orders five
quad op-amps. De-duplicated by reference, and the unit carrying a real footprint wins, because
KiCad writes the power unit with an empty Footprint field.

**A hierarchical design keeps almost nothing on the root sheet.** Reading only the file you
were handed gives a BOM of two connectors for a 90-part board. The walk follows each sheet's
`Sheetfile` property, and distinguishes the two ways a file can be met twice: an ancestor is a
**cycle** and the design is malformed, while a non-ancestor is a **repeated sheet**, which is
legal and means those parts really are on the board more than once. Both are reported; neither
is silent.

### A name is accepted as well as a path, because he is listening

A dictated path does not survive Whisper — "slash home slash pi slash amp dot kicad underscore
sch" comes back as prose. So a bare project name is searched for under `ODDBALL_KICAD_ROOT`
(`.env`, default `~/kicad`), matching on case- and punctuation-free slugs so "the amp board"
finds `amp_board/`. **Two matches are reported as two matches.** Answering confidently about
the wrong board is worse than asking which one, because the answer is correct — about something
LB did not ask about.

### What the voice does with a bill of materials

Nothing, and that is the point. The tool result is appended under `Tool Execution Result:`,
which `engine/split.py` already cards, so the listing lands on the HUD and the spoken half is
one sentence the model writes from it. D2's rule holds: the model may phrase the answer and may
never derive it, so `SUMMARY_PROMPT_TEMPLATE` forbids naming any part that is not in the tool's
output — asked to summarise a BOM, a model will otherwise mention the decoupling capacitor it
believes ought to be there, and LB will go looking for it.

### Deliberately not built

Netlist and connectivity extraction ("is pin 3 tied to ground"), DRC, and gerber export.
kiutils can reach some of it; each is its own tool with its own failure modes.

---

## D10 — He opens applications by handing them to systemd, and asking costs no API call

**2026-08-21.** LB: *"he is struggling to open Firefox and different apps on the pi."* He was
not misunderstanding the request — `router.py` already routed "launching applications" to OS.
Five independent defects sat on the execution path, and every one had to go before a window
could appear.

| # | defect | why it was invisible |
|---|---|---|
| 1 | **No display.** `oddball.service` sets no `Environment=`, and `Linger=yes` starts it at boot, *before labwc exists*. | Intermittent — see below. |
| 2 | **`subprocess.run(capture_output=True, timeout=15)`.** Blocks until exit, and **kills the child** on timeout. A GUI app that did find a display appeared and died at 15s — and the turn thread is the speech thread, so he was deaf for it. | Looked like the app "not opening". |
| 3 | **The failure was spoken as success.** `failed = result.startswith("Terminal Error:")`. `firefox &` returns 0 instantly, Firefox dies unseen, and he says **"Done. The output's on the screen."** | A *confident success* is the one shape nobody escalates. |
| 4 | **Wrong cgroup.** Anything spawned inherits `oddball.service`'s, and `KillMode=control-group` kills it on the next restart. | Only visible after a deploy. |
| 5 | **A refusal was reported as a malfunction.** `Action Blocked:` set `failed=True`. | `os_controller.py`'s own docstring says this is how a guard gets switched off. |

**Defect 1 is intermittent, and that is why it presented as "struggling" rather than "broken".**
The desktop session imports its environment into the systemd user manager at login, so a
service *restarted* after login inherits `WAYLAND_DISPLAY`, while the same service started at
boot does not. Measured: the running process had `WAYLAND_DISPLAY=wayland-0` and `DISPLAY=:0`
after a manual restart, and neither after a reboot.

### The catalogue is the machine's, not a table we maintain

`~/oddball/hardware/apps.py` was a hand-written allow-list of three rows. A `which` sweep of the
Pi found **`nautilus` missing** — one row in three would have failed exactly as that file's own
comment predicted: *"he says he opened it and nothing appears."* So the source of truth is the
XDG desktop-entry database (`tools/app_catalogue.py`): 62 entries, 32 launchable after
filtering `NoDisplay`, `Hidden`, non-`Application` and session-ending entries. `apt install vlc`
and VLC is openable with no code change, and `rm`/`dd`/`bash` are excluded for free because they
are not applications.

LB chose this over curation, and chose to keep the single permission gate covering all of it.

### A transient service, not a scope, and not a bare Popen

```
systemd-run --user --collect --unit=oddball-app-<id>-<ts> -p Type=exec --setenv=... -- /usr/bin/firefox
```

`--scope` runs **synchronously** (defect 2 unfixed) and inherits our environment wholesale —
including `Nice=-5`, which would put Firefox above the audio thread on the one unit whose
comments say audio must never be starved. A transient **service** is forked by the user manager
from a clean environment, gets its own cgroup, and can be named, inspected and stopped.

The fix is therefore not "stop blocking" but **"run something that finishes."** `capture_output`
and `timeout` stay and are now correct: the thing being run is a control-plane command that
returns in ~150 ms. systemd owns the process lifetime, so there is no `Popen`, no
`start_new_session`, no orphan reaping.

**`Type=exec` was measured rather than assumed, and the usual claim about it is wrong on
systemd 257:**

| | `Type=simple` | `Type=exec` |
|---|---|---|
| binary missing | rc=1 | rc=1 |
| binary present, **cannot exec** (bad shebang, corrupt ELF, missing `.so`) | **rc=0** | rc=1 |

A *missing* binary is caught either way — systemd validates `ExecStart` at load. What
`Type=simple` reports as success is a program that exists and then fails to `execve`, because
the start job completes at `fork()`. So `_which()` catches *not installed* and `Type=exec`
catches *installed but broken*; both are kept.

### Opening Firefox went from three API calls to none

The router ran unconditionally, so **every** turn began with a Gemini request. A launch cost
three: route it, write `firefox`, then paraphrase that into a speakable question. Against D3's
20 requests/model/day, six launches was a day's quota.

`orchestrator/instant.py` already answered time, date, conversions, constants, definitions and
arithmetic for free — the merge wired it in as the UTILITY *destination* rather than as a pass
in front, so the free path could only be reached by paying for it. It now runs first, and a new
planner (`orchestrator/launch_intent.py`) recognises a launch with no model at all.

| | before | after |
|---|---|---|
| "open firefox" (whole approval conversation) | 3 | **0** |
| "what time is it" / a conversion / a definition / a sum | 1 | **0** |

Verified by monkeypatching `router_agent` to raise. `formula` deliberately stays behind the
router: measured against a 15-question corpus it is the only intent that claims questions
belonging to an agent — *"design a low pass filter with a cutoff of one kilohertz"* is a MATH
problem and `formula` answers it with a formula.

**A launch needs a verb AND a target AND nothing left over but filler.** That end-anchor rule is
inherited from `apps.py` and is the whole safety argument: *"how do I open a file in Python"*,
*"why did my browser crash"*, *"is firefox installed"* and *"what is firefox"* all decline.
Mutation-tested — removing the anchor turns checks red, and the failure it describes is a
question starting a program.

`instant.py`'s planner seam had never been used, and its log line reached for
`plan.name`/`plan.argv` — attributes of `hardware.actions.Plan`, a class that did not come
across in the merge. It was a guaranteed `AttributeError` waiting for the first planner anybody
injected, and because the exception was caught it took the entire free tier down into the router
fallback *silently*.

### What was measured

`media/data/2026-08-21-app-launch.csv`, chart beside it. Five trials per arm on the Pi:

| arm | alive after launch | survived a service restart | what he said |
|---|---|---|---|
| `firefox` (old) | 0/5 | never started | "That didn't work — the error is on the screen." |
| `firefox &` (old) | 0/5 | never started | **"Done. The output's on the screen."** |
| `launch_app` | **5/5** | **survived** | "Opening Firefox now." |

Restart survival is measured on a `sleep`, not on Firefox, and separately confirmed by hand
with the browser: a window on a real screen is a bad probe for a cgroup property because a
human can close it.

### What didn't work

- **The measurement poisoned its own subject.** Firefox increments
  `toolkit.startup.recent_crashes` whenever it is stopped before startup completes; past 3 it
  starts, writes prefs and **shuts down cleanly** — exit 0, no output, no crash report,
  `Result=success`. Fifteen trials drove it to **17**, and the run then reported that the cgroup
  fix had failed. Indistinguishable from the bug being fixed. See L8.
- **`pkill -f /usr/lib/firefox` killed the ssh session, twice** — `-f` matches the command line
  that invoked it. Already recorded in the pre-merge repo's lessons and not carried across in
  the merge, so it was learned again. See L9.
- **`ls ~/.mozilla` returning nothing** was read as "Firefox has no profile" for a long time.
  This build uses XDG paths; the profile was in `~/.config/mozilla/firefox` the whole time. L10.
- **An `os.path.exists` fallback beside `shutil.which`** — dead code, since `which` already
  resolves absolute paths, and it silently bypassed the harness's injection seam so the tests
  passed on Windows and failed on the Pi.
- **A regex that stripped `%f` before unescaping `%%`**, turning `%%f` into `%` instead of `%f`.
  Field codes need one left-to-right pass. The harness caught it.

---

## D11 — Syllabi get their own collection, and the deadline banner is global

**2026-08-21.** LB asked for two architectural corrections — remove a tier system in favour of
a router, and make knowledge agents retrieve locally before generating. **Both were already
built** (D1 removed the tiers on 2026-08-19; `agents/firmware_agent.py` has queried ChromaDB
before calling Gemini since the same day). Checked before changing anything, and recorded here
because "it already works" is a finding, not a non-event: the request was aimed at a version of
the repo that no longer exists.

What was genuinely missing was the **ACADEMIC route**. `agents/lab_agent.py` was a 0-byte stub,
in no enum and no dispatch table.

### The syllabus lives in a second collection, not a second store and not one pool

`tools/vector_db.py` embedded everything under `data/` into one Chroma collection. Dropping
syllabi in there would have let a course outline ground a firmware answer: semantic search ranks
by similarity alone and has no idea what kind of document a chunk came from.

Two named collections in the same store — `datasheets` (everything except `data/academic/`) and
`academic` — rather than one pool with a metadata filter. **A filter is a thing you can forget
to pass; a collection is not.** `get_retriever(k, collection=...)` defaults to `datasheets`, so
the existing firmware call site did not change and a caller who omits the argument gets the safe
one.

The exclusion is by resolved **path**, not by substring. `"academic" in source` would also drop
`data/sensors/academic_press_sensor.pdf` — losing a datasheet because of its filename, silently.
Verified in both directions against a throwaway store built from that exact adversarial name.

**This renames the pre-existing collection** (Chroma's default was `langchain`), so an old store
reads as empty rather than failing. `chroma_db/` is gitignored and rebuilding is already the
documented step after adding PDFs.

### The academic agent is stricter than the firmware agent, on purpose

The firmware agent may fall back on its own knowledge when the datasheets fall short, provided
it says so. That is right for firmware: an ESP32 register is public record and checkable.

**A syllabus is not public record.** There is no general knowledge about when LB's midterm is,
so a fluent answer is a *fabricated* one with nothing to check it against — and it is the worst
shape in this repo, the same one D8 and D9 both document: a confident number nobody would
question. "Your project is due the 24th" is exactly that. So the prompt is LB's directive
unhedged — answer from the provided context ONLY, otherwise say you do not know. No
"but generally". There is no generally.

### Dates are extracted once, because the banner had to be free

Retrieval is the wrong tool for "what's due Friday": asked for what is due soonest, a semantic
search returns the paragraph that reads most like the question, not the one with the nearest
date. So `tools/academic_calendar.py` extracts dates into `academic_calendar.json` as a **build
step** — one Gemini call per syllabus file, paid on the day a syllabus is added.

That split is what makes the next decision affordable at all.

### The banner is global — LB's call, against the first proposal

It was first scoped to ACADEMIC-routed turns. LB overruled that: it fires on **every** turn, like
`_with_backup_reminder`, whether he asked about firmware, the time, or nothing at all. He is
right, and the reason is the one that makes reminders worth having — scoped to coursework
questions, he would only ever see it when he was already thinking about coursework. A deadline
warning that fires while he is debugging firmware at 2am is the one that earns its place.

It costs **a JSON read and no API call**, which is the only reason it can sit on the turn path
under D3's 20-requests-per-day ceiling. A check that cost a request could not go there at any
price. Shown and never spoken, for `_with_backup_reminder`'s reason: an alarm read aloud in the
middle of an unrelated answer is startling.

Because the check is global, `academic_agent.py` deliberately does **not** append its own
deadline card — otherwise the one route where LB is already discussing coursework is the one
that shows it twice.

**Scope, stated rather than assumed:** it reaches routed and free turns, and *not* quiz mode or
a pending approval's yes/no. Those are conversations already in progress rather than fresh
questions, and it is exactly where the backup reminder already draws the line. Worth knowing it
is a line, not an oversight — if LB wants it inside the quiz lock too, that is one more call.

### What was verified

`tools/verify_agents.py` — 52/52 offline, no key needed (D7). The route-coverage assertion
(`set(ROUTE_TARGETS) == set(AgentRoute)`) is what forces a new enum member to be wired into
`_dispatch` rather than merely importable. Beyond the harness, three properties were proved
directly:

| property | how |
|---|---|
| the banner shows on a **non**-academic, **zero-API-call** turn, unspoken | UTILITY turn with `router_agent` patched to raise — nothing reached the router |
| retrieve **then** generate, on the `academic` collection, with the strict directive in the prompt | call order recorded against a fake LLM |
| a syllabus cannot be retrieved from `datasheets`, or a datasheet from `academic` | real embeddings, real Chroma, throwaway store, adversarial filename |

### Deliberately not built

Per-course filtering ("what's due in ECE 350"), recurring deadlines, and any write path — he
reads the calendar and never edits it. Adding a deadline by voice means a spoken date reaching a
file that a warning banner is driven from, and `tiny.en` is the transcriber that turned "What is
the date?" into "What is today?" (D5).

### Found while syncing the Pi: two packages that installed nowhere

`stage_install.sh` existed **only on the Pi** while `docs/DEPLOY.md` instructed you to run it —
so a deploy to a new box followed an instruction pointing at a file the repo did not contain. It
is committed now, and the missing file was the smaller half.

Its stages are hand-grouped, which is the whole point (a resolver backtrack stays isolated to one
group). The cost is drift, and it had already happened twice: **`sympy` and `kiutils` were in
`requirements.txt` and in no stage at all.** On a fresh Pi that means every derivative question
answers *"ModuleNotFoundError: no module named 'sympy'"* — the exact bug `verify_agents.py` was
written for, one layer earlier than it was looking — and every KiCad question answers with an
install instruction, because `tools/kicad_parser.py` wraps that import by design so the HARDWARE
agent still starts without it. Both absences are silent: the venv builds clean.

`verify_agents.py` now asserts every `requirements.txt` package appears in a stage.

**And that check was vacuous when first written**, which is the part worth recording. It searched
the script's whole text, and the script's own header comment explains why sympy matters — so
`"sympy" in text` was true with the install line deleted. Mutation-testing it (delete sympy, rerun)
is what exposed that, and it stayed green. It now reads only lines beginning `run `, and the same
mutation turns it red. See L11, and L4 for the previous time a green check held a bug in place.

Also cleaned: `DECISIONS.md`, `lessons.md` and `todo.md` were sitting at the Pi's repo root,
orphans of an older layout that `tar` had never deleted because **tar-over-ssh does not delete**.
The root `DECISIONS.md` was a 487-line pre-D11 copy — a stale decision log at the top of the tree,
looking authoritative. Verified a strict subset (0 unique lines) before removing.

---

## D12 — The RAG install pulls 2.4 GB of CUDA onto a machine with no GPU

**2026-08-21.** The whole of `requirements-rag.txt` was missing from the Pi, so **both** grounded
agents were running ungrounded — FIRMWARE answering datasheet questions from Gemini's training
weights (saying so, as its prompt requires) and ACADEMIC refusing outright. LB asked to install it.

**The documented command must not be run as written.** `pip install --dry-run` on the Pi, which
installs nothing:

| | download |
|---|---|
| `nvidia-*` wheels + `triton` (12 measured) | **2,377 MB** |
| `torch` (PyPI, CUDA-linked) | 427 MB |
| what is actually needed (chromadb, transformers, tokenizers, pypdf…) | ~470 MB |

Plus `cuda-toolkit`, `cuda-bindings` and five more `nvidia-*` packages not sampled. Roughly
**3.3 GB down and 6 GB+ unpacked onto an SD card, for silicon that is not in the machine.**
`sentence-transformers` → `torch`, and on Linux/aarch64 the default PyPI torch is the CUDA build.

PyTorch's CPU index resolves for this exact platform and avoids all of it:

```
torch-2.13.0+cpu-cp313-cp313-manylinux_2_28_aarch64.whl     155 MB
Would install: Jinja2, MarkupSafe, networkx, torch+cpu      <- zero nvidia packages
```

**155 MB against 2,804 MB.** Measured after the fact: the venv went 885 M → **1.9 G**, where the
default index would have put it near 7 G.

`--extra-index-url` is now in `requirements-rag.txt`. Torch was installed from the CPU index
*first*, so `sentence-transformers` found it already satisfied and never reached for the CUDA
build — the ordering is the mechanism, not a precaution.

**The check that proves it is not the version string.** `2.13.0+cpu` with CUDA wheels beside it
means the index was ignored, so the real check is `ls site-packages | grep -c nvidia` → **0**.

### Then the build died on LB's own datasheets

```
ValueError: Expected Embeddings to be non-empty list or numpy array, got [] in upsert.
```

A message about Chroma's internals for a problem entirely about the input file. Both Pi camera
PDFs load as perfectly good page objects with **0 extractable characters** — image-only exports
with no text layer. `datasheets: 2 pages -> 0 chunks`.

`_build_collection` guarded `not documents` and not `not chunks`, which is **a defect introduced
by D11 the day before**. A page is not text. The crash was the good outcome: the same guard
missing one layer up would have written an *empty collection*, which is indistinguishable from a
working one from the outside — the firmware agent would answer ungrounded forever while a store
sat on disk claiming it had been built. D9's empty BOM, exactly.

Textless pages are now counted, **named**, and skipped, usable files beside them still index, and
a build that writes nothing says so loudly instead of reporting success. `verify_academic.py
--store` carries an image-only fixture; before the fix it takes the build down.

LB's two camera PDFs were removed from the Pi — the originals are on the Windows box, and real
text-bearing PDFs come later.

### Not done, and worth its own decision

**`onnxruntime` is already installed** for the wake word and Piper, and Chroma ships an ONNX build
of the same `all-MiniLM-L6-v2` model — so torch could be dropped **entirely**, not merely
de-CUDA'd. That changes `tools/vector_db.py`'s embedding path and needs its own re-verification,
so it is a decision rather than a rider on an install. Tracked in `tasks/todo.md`.

## D13 — A vault, a floating ball and a thumbs up: three additions, three guarded imports

**2026-08-21.** Three features landed together on `oddball-integration`, and what they have in
common is more interesting than what any of them does: **none of them may cost the voice loop
anything when its dependencies are absent.** Every import is guarded, and each degrades to the
behaviour that existed the day before.

| feature | missing dependency | what LB loses | what still works |
|---|---|---|---|
| Markdown vault | none — stdlib + `langchain_core` | — | — |
| desktop avatar | `fastapi`, `uvicorn`, `pywebview` | the floating ball | `hud/face-preview.html` on 8765, unchanged |
| gesture approval | `opencv-python`, `mediapipe` | thumbs-up approval | typing `y`, exactly as before |

### The vault is not a second conversation log

`tools/memory_manager.py` keeps the last 40 turns and rotates. That is the whole of his memory,
and it means a part number LB settled on this morning is gone by tonight. `tools/knowledge_vault.py`
is the other half: Markdown files under `vault/`, written only when asked, never rotated,
greppable with `grep` and diffable in git.

No index, no embeddings. The search is a substring scan over a folder that will hold dozens of
files, not millions — an index here is a moving part bought with nothing. `tools/vector_db.py`
is the other end of that trade and stays where it is; it exists for hundreds of pages of PDF,
which is the case a substring scan genuinely cannot serve.

Bound to **HARDWARE, FIRMWARE and GENERAL/persona** as the *same two tool objects*, imported
from one module, so three agents cannot end up writing to three folders. Two things it is
careful about, both because a model supplies the arguments:

- **Paths.** `vault / "../../.ssh/authorized_keys"` resolves fine and writes fine. Both
  `filename` and `folder` are flattened to a single safe segment and the result is asserted to
  still be inside the vault. Verified: `folder="../../etc"`, `filename="../../../pwned.md"`
  lands at `vault/etc/pwned.md`.
- **Size.** `read_from_vault` output goes straight into a prompt. Capped at 24k characters, and
  it *says* when it truncated — a prompt quietly cut in half is one the model answers
  confidently from the wrong evidence.

FIRMWARE and PERSONA had no tool-call path at all before this. Both now run a **bounded
two-step**: tools bound on the first invoke, and the second invoke uses the *unbound* model.
That unbinding is the loop bound — a model that can still see the tools can call them again,
and "remember this" has no natural stopping point.

### Not Chromium, and not a second source of state

The overlay is `pywebview` over the system WebKit view. A Chromium window for a 120px ball is
~250 MB and a core of a Pi 5 already running whisper, piper and onnxruntime — the entire budget,
spent on a circle.

The harder question was **state**. There are now two surfaces showing what he is doing: the full
character rig on 8765 and the overlay on 8000. Two surfaces reading two sources is exactly how
they come to disagree, and a face that lies about the microphone is the single most misleading
thing this rig can do (D41's argument, one surface further).

So neither surface owns the state. `HudBridge.set_state()` — already the one writer — mirrors
into `ui/avatar_state.py`, which is **stdlib only on purpose**: if the fan-out lived in
`ui/server.py`, that mirror would drag FastAPI onto the import path of the voice loop, and a box
without it would fail to start the assistant rather than merely lack a ball.

Verified end to end: one `bridge.set_state()` call, `['sleeping', 'thinking', 'speaking', 'idle']`
out of the `/ws/state` socket, replayed state on connect, and the subscriber released on
disconnect across three open/close cycles.

**One defect found and fixed in review.** The websocket handler blocked on `await queue.get()`,
so a closed window was not noticed until the *next* state change — `/healthz` reported clients
that were not there, and opening and closing the overlay while he rested counted up. A task now
races the receive side, and the count returns to 0 immediately.

### The obvious thumbs-up test approves a wave

This is the part worth keeping. The natural test is *"thumb tip above the index knuckle and
above the wrist"*:

```python
if thumb_tip < index_mcp and thumb_tip < wrist:
    return "THUMBS_UP"
```

**An open palm passes it.** Hand up, fingers spread, the thumb is above both landmarks. So the
obvious test turns a wave at the camera into an approval — and on `agents/os_agent.py`'s path,
what it approves is a shell command.

`THUMBS_UP` therefore additionally requires all four fingers **curled** (each tip below its own
PIP joint), and `OPEN_PALM` is tested first so the two are mutually exclusive by construction.
The classifier is a pure function of 21 landmarks, so it is tested with no camera at all — six
cases, including the one above, all green.

Failure directions are asymmetric and every one falls safe: no camera, no hand, an open palm,
an exception → the keyboard is still asked. Only a clear thumbs up short-circuits it, and the
gesture never *declines* on LB's behalf either. The blocklist in `tools/os_controller.py` runs
regardless of how approval arrived, and the exact command is still printed before the question.
**A gesture replaces the keystroke, not the review.** `ODDBALL_GESTURE=0` keeps the camera shut.

### mediapipe does not have a wheel for the Pi's Python

Not discovered on the Pi — read off the wheel index before shipping the requirement, which is
the cheap order to do it in:

- mediapipe publishes aarch64 wheels for **cp39–cp312**
- the Pi runs **Python 3.13.5** (`requirements.txt` has said so since the merge)

So `pip install mediapipe` there finds nothing, and there is no source build worth attempting on
an SD card. It is the second entry in `stage_install.sh`'s own stated trap — a package in
`requirements.txt` and not in the stage list installs *nowhere*, silently. It has its own stage
now, `vision`, deliberately separate from `ui` so mediapipe cannot take FastAPI down with it,
and a non-zero RC on that line is **the documented case, not a broken box**. `opencv-python` has
a cp313 aarch64 wheel and installs on its own.

Two apt packages pip also cannot supply, in the same class as `libportaudio2`:
`python3-gi gir1.2-webkit2-4.1 python3-gi-cairo`. Without them `import webview` succeeds and
`webview.start()` then fails looking for a toolkit.

### Not measured yet, and not to be written up as if it were

Everything above was verified on the Windows authoring box. **No number in this entry came off
the Pi.** Three that need to, before any of it is narrated:

1. camera-open + inference latency per approval — the claim is ~40–80 ms inference with the
   camera open dominating, and that is an estimate, not a measurement
2. overlay RSS against a Chromium window showing the same page — the 250 MB figure is the
   published Chromium baseline, not this page on this box
3. whether `transparent=True` composites under Bookworm's Wayfire session in practice

Until those exist under `media/data/`, this is a design decision with a verified integration,
not a measured result.

## D14 — I checked one release series and called it a platform limit

**2026-08-22.** D13 shipped `mediapipe>=0.10.14` with a long comment explaining that gesture
control could not work on the Pi, because mediapipe's aarch64 wheels stop at cp312 and the Pi
runs Python 3.13.5. LB read that, took it at face value — reasonably, it was stated as measured
— and asked for the venv to be rebuilt on Python 3.12 so the feature would work.

**The finding was wrong, and the fix it implied was the expensive one.** Both halves came from
querying the PyPI JSON API for `mediapipe` and looking at the `cp3xx` tags on the aarch64
wheels. That much was accurate. What it missed is that the answer only held for the `0.10.x`
series, and there is a `1.x`:

| | `mp.solutions.hands` | aarch64 wheel | Python |
|---|---|---|---|
| 0.10.18 | yes | cp39–cp312 | 3.12 and below |
| 0.10.20 – 0.10.35 | yes | **none at all** | — |
| **1.0.1** | **removed** | `py3-none-manylinux_2_28_aarch64` | **any 3.x** |

`py3-none` is ABI-independent — mediapipe 1.x stopped building per-interpreter wheels. It
installs on the Pi's existing venv. Verified there, not inferred from the tag:

```
$ venv/bin/pip install --dry-run --no-input mediapipe
Would install ... mediapipe-1.0.1 opencv-contrib-python-5.0.0.93 ...
```

### What the wrong answer would have cost

Debian ships exactly one Python 3 per release and trixie's is 3.13. On this Pi:

```
$ apt-cache policy python3.12       # returns nothing whatsoever
$ command -v uv pyenv               # none
```

So "just use 3.12" is not a flag, it is: source an interpreter Debian does not package, build
or install it, rebuild a 1.9 G venv against it, re-verify every harness — to pin a mediapipe
from November 2024 and inherit its 0.10.x API forever. All of it avoidable, and none of it
would have been questioned, because the document said the platform made it necessary.

### The rule this earns

**A wheel-tag query answers a question about one release series, not about a package.** The
series is the variable most likely to move, and a `requires_python` floor or an ABI-tag change
is exactly the kind of thing a maintainer does at a major version. Sort the releases, look at
the newest, and check whether the tags changed shape — `py3-none` appearing where `cp3xx` used
to be is a packaging decision with consequences, not a detail.

The second-order lesson is worse and worth naming: **the finding was written up persuasively.**
It had a table, a measured provenance, and an explicit "READ THIS BEFORE THE PI INSTALL FAILS
AND YOU BLAME THE PIN". Confidence and formatting made a partial check read as a settled fact,
and it propagated straight into a work request. A measurement's write-up should carry what was
actually queried — here, "the 0.10.x wheels" — not the generalisation it seemed to support.

### The port, which is the part that was real work

mediapipe 1.x removed `mp.solutions` entirely, so `GestureRecognizer` had to move to
`mediapipe.tasks.python.vision.HandLandmarker`. `tools/gesture_control.py` now supports both:

- Tasks preferred, and the only one that installs on 3.13
- legacy `Hands` if that is what is present, so a 3.12 box pinned to 0.10.18 is unaffected
- **one `_classify()`**, shared, because both APIs return the same 21 normalised landmarks in
  the same order. The fork costs a constructor, not a second copy of the decision logic — which
  matters, because that logic is the part with the safety property in it (D13).

The Tasks path needs `models/hand_landmarker.task`, 7.8 MB, gitignored and fetched by
`--fetch-model`. `stage_install.sh` fetches it after the `vision` stage, because a missing model
degrades to `NO_CAMERA` — indistinguishable from a camera fault, and it sends you to the wrong
place. `--backend` reports which API loaded, whether the model is there, and why not if not.

Measured while porting: `detect()` on a blank 640x480 frame is **3 ms**. The 40–80 ms figure
D13 quoted for inference was an estimate and is withdrawn with the rest of it; the camera open
dominates either way, which is why the frame is grabbed and the device released per call.

### Also settled in the same pass

**`opencv-python` is no longer a dependency.** mediapipe pulls `opencv-contrib-python`, a
superset providing the same `cv2`. Listing both makes two distributions fight over one import
name, and D13 listed both.

**The venv needs `--system-site-packages`,** which is the one real Pi-side change here.
pywebview reaches for PyGObject at `webview.start()`; PyGObject is a Debian system package and
is not pip-installable into a sealed venv. `hud/float.py` dodges this by running on
`/usr/bin/python3` — `launch_ui.py` cannot, because it needs pywebview *from* the venv. One line
in `venv/pyvenv.cfg` rather than a 1.9 G rebuild.

**`gir1.2-webkit2-4.1` is the one apt package actually missing** on this Pi. `python3-gi`,
`python3-gi-cairo`, `libportaudio2` and `pipewire-alsa` were already in from the `float.py`
work; the box has `gir1.2-webkit-6.0` (GTK4) and pywebview's GTK backend asks for the GTK3
WebKit2 4.1 typelib by name. `stage_install.sh` now `dpkg-query`s all five and prints the
`apt install` line for whatever is missing — it does not run `sudo` itself, because the script
is run detached and a password prompt in a detached job hangs forever.

### Not measured, still

D13 listed three Pi-side measurements as outstanding and they remain outstanding. The 3 ms
above is Windows, on a blank frame, and is a floor rather than a figure. Nothing here should be
narrated as a Pi result until it is one.

## D15 — "It installs" is not "it runs", and I made the same class of error twice in a day

**2026-08-22.** D14, written hours earlier, retracted D13's claim that mediapipe could not work
on the Pi's Python 3.13. Its evidence was `pip install --dry-run` **on the box itself**,
resolving `mediapipe-1.0.1` cleanly, and it concluded — in bold — *"Do not rebuild the venv on
3.12 for this."*

Then the code ran:

```
$ venv/bin/python tools/gesture_control.py --backend
INFO: Created TensorFlow Lite XNNPACK delegate for CPU.
Killed
exit=137
```

**mediapipe 1.0.1 installs on Python 3.13 and cannot execute there.** Every
`mediapipe.tasks.vision` task — `HandLandmarker` and `GestureRecognizer` alike — is SIGKILLed
the instant the XNNPACK delegate comes up. Not an exception; the process is gone.

Ruled out on the box, in this order: OOM (6.4 GB available, `systemd-oomd` inactive, nothing in
the journal), thermal or undervoltage throttling (`vcgencmd get_throttled` → `0x0`), and the
model file (a completely different task with a different model dies identically).

What it actually is: mediapipe wraps that construction in `CallWithCoreDumpProtection`, which
converts a fatal signal into SIGKILL specifically so no core is written. The real fault is
masked by design, and exit 137 is all the diagnostic there is. Three
`madvise(MADV_NOHUGEPAGE) failed on altstack: Invalid argument` lines precede it, which is that
protection layer failing to set itself up against this kernel and glibc.

### So LB was right, and I talked him out of it with a measurement

He asked for a Python 3.12 venv. I produced a table, a dry-run transcript and a paragraph about
what a 3.12 rebuild would cost, and told him it was unnecessary. The dry-run was real. It
answered a question — *will pip resolve this?* — that was not the question that mattered.

**Two corrections in one day, both the same shape:** a real measurement, generalised one step
past what it covered. D14 was "these wheel tags" read as "this package". D15 is "it installs"
read as "it works". The second is worse, because D14's own lesson was written and committed
before this one happened.

The rule, and it is now the first check in `install_gesture_venv.sh --check`:

> **A dependency is verified when the code path that uses it has been executed.** Not when it
> resolves, not when it imports. `import mediapipe` succeeds on the Pi's 3.13 — it is
> *constructing the detector* that kills the process, one call later.

### What shipped instead: a sidecar, and crash isolation as architecture

Measured on the Pi:

| mediapipe | interpreter | installs | runs |
|---|---|---|---|
| 1.0.1 | 3.13.5 | yes | **no — SIGKILL** |
| 0.10.18 | 3.12.14 | yes | **yes**, 88 ms/frame |
| 0.10.20+ | any | no aarch64 wheel at all | — |

Note the failing variable is the **version, not the API**: 0.10.18 carries both `mp.solutions`
and `mp.tasks`, and on this Pi both work. 1.0.1 specifically is the one that dies.

Rebuilding the main venv on 3.12 — LB's original request, and now clearly *possible* — is still
the wrong trade. It is 1.9 GB of faster-whisper, ctranslate2, piper and onnxruntime, all
verified on 3.13.5, and a missing cp312 wheel anywhere in it takes down the thing that actually
talks in order to fix the camera. So: `.venv-gesture`, 400 MB, Python 3.12 from `uv`,
mediapipe 0.10.18 and nothing else. `tools/install_gesture_venv.sh` builds it in two minutes.
Debian ships no `python3.12` at all on trixie, which is why the interpreter comes from `uv`'s
prebuilt standalone builds rather than apt.

**And `get_gesture()` now always runs in a child process — even when it could not possibly
crash.** That is the part worth keeping. A SIGKILL cannot be caught, so an in-process detector
that dies takes the assistant with it *at an OS approval prompt*, which is the worst place in
this program for a sudden exit. The first version of the sidecar got this wrong: it tried
in-process first and fell back, so on the Pi the fallback was unreachable — the process died
constructing the thing it was about to decide not to use. Caught by running it, not by reading
it.

Every failure mode collapses to `NO_CAMERA` and `NO_CAMERA` falls to the keyboard: non-zero
exit, 20 s timeout, unparseable stdout, missing interpreter. A worker that misbehaves cannot
produce an approval.

### The cost, measured rather than estimated

An approval is **2,217 ms** (median of 10; min 2,197, max 2,271 — very tight):

```
interpreter start          22 ms
import mediapipe        1,009 ms   <- per approval; the child is built and thrown away
build HandLandmarker       55 ms
open camera               204 ms
4 warmup frames           602 ms   <- 150 ms each: the webcam gives ~6.6 fps, not the 15 asked
inference                  47 ms
```

**102 ms of 2,217 is detection.** The thing that looks expensive is not the expensive thing.
`media/charts/gesture-approval-latency.svg`, data and script beside it.

D13 estimated "~40–80 ms per frame on a Pi 5 CPU" and quoted a 3 ms figure measured on Windows
against a blank frame. Both are withdrawn. The real inference is 47 ms and it was never the
number that mattered.

A persistent worker would pay the 1.0 s import once and bring an approval to roughly 850 ms.
Not built: it turns a subprocess call into a lifecycle to manage, and 2.2 s at a prompt that has
already stopped to ask a question is tolerable. Tracked.

`WARMUP_FRAMES` stays at 4 rather than being cut to reclaim 600 ms. The first frames off a
freshly opened camera are auto-exposure garbage, and there is no measurement of detection rate
against warmup count to trade on. Cutting it on the reasoning that "2 is probably enough" would
be D14 for the third time.

### Two smaller things confirmed on the box

**The venv needed `--system-site-packages`.** pywebview reaches for PyGObject at
`webview.start()`; PyGObject is a Debian system package, not pip-installable into a sealed venv.
`/usr/bin/python3 -c 'import gi'` gives 3.50.0 and `venv/bin/python` gave
`ModuleNotFoundError`. One line in `venv/pyvenv.cfg`, not a 1.9 GB rebuild.

**`gir1.2-webkit2-4.1` is the only apt package actually missing.** `python3-gi`,
`python3-gi-cairo`, `libportaudio2` and `pipewire-alsa` were already installed from the
`float.py` work. The box has `gir1.2-webkit-6.0` (GTK4, what `float.py` uses) and pywebview's
GTK backend asks for the GTK3 WebKit2 4.1 typelib by name. `stage_install.sh` `dpkg-query`s all
five and prints the `apt install` line for whatever is missing; it does not run `sudo` itself,
because the script runs detached and a password prompt in a detached job hangs forever.

## D16 — Two environment variables, found by looking at the screen

**2026-08-22.** D15 signed the avatar off on protocol evidence: `/healthz` 200, `/ui` 200,
`clients: 1`, and `sleeping -> thinking -> speaking -> sleeping` out of the state socket. It
explicitly did not claim anything about pixels, and said a screenshot was still owed.

LB sent a photo of the screen. **The window was an empty rectangle with a title bar on it.**

Both of those are defects and neither produces an error message anywhere.

### `WEBKIT_DISABLE_DMABUF_RENDERER=1`, or the page never paints

The window was not blank in the sense of "background with nothing drawn". It was **torn buffer
garbage**: white, rows of black dashes, and horizontal fragments of the chat panel that was
behind it, streaked across the surface.

The page was fine. A JS probe run inside the live window — `evaluate_js` against the real
running instance — reported:

```json
{"found": true, "w": 120, "h": 120, "x": 90, "y": 90, "display": "block",
 "visibility": "visible", "bg": "radial-gradient(circle at 30% 30%, rgb(0, 242",
 "innerW": 300, "innerH": 300}
```

Perfect layout, correct gradient, visible, centred. The pixels simply never reached the
screen. Disabling WebKitGTK's DMA-BUF renderer fixes it completely. Measured with a control —
mean colour of the 120x120 centre against the window's own corner:

| | centre | corner | B−R at centre |
|---|---|---|---|
| default | (229,244,249) | (253,253,253) | +20 |
| `WEBKIT_DISABLE_DMABUF_RENDERER=1` | (196,232,248) | (255,255,255) | **+52** |
| `WEBKIT_DISABLE_COMPOSITING_MODE=1` | (196,232,248) | (255,255,255) | +52 |
| `LIBGL_ALWAYS_SOFTWARE=1` | (196,233,249) | (255,255,255) | +53 |

All three workarounds fix it identically; the DMA-BUF one is the narrowest, so it is the one
that ships. Note `hud/float.py` was never affected — it is GTK4 with WebKit 6.0, a different
WebKit build. This is the GTK3 / WebKit2 4.1 stack pywebview uses.

### `GDK_BACKEND=x11`, or `frameless=True` is silently ignored

labwc drew a full server-side title bar with minimise, maximise and close on a 300px ball.

pywebview is not at fault — `webview/platforms/gtk.py:229` does call `set_decorated(False)`.
**GTK3's Wayland backend never negotiates xdg-decoration**, so the compositor is never told,
and labwc applies its server-side default. Under XWayland the same call goes out as an X11
hint, which labwc honours. Transparency and always-on-top both survive the move — verified by
screenshot, not assumed.

The alternative was a labwc window rule in LB's `~/.config/labwc/rc.xml`. Rejected: it edits
his desktop configuration to fix our window, and it would silently stop applying if the window
title ever changed.

`GDK_BACKEND=x11` is set only when `DISPLAY` exists. Forcing it with no XWayland running turns
"opens with an unwanted title bar" into "does not open at all", which is a strictly worse
failure.

### And a CSS bug found by reading the file afterwards

`.sleeping { box-shadow: ... }` never applied. An id selector outranks a class one, so
`#ball { box-shadow: 0 0 25px ... }` won and he slept wearing his full waking halo. The opacity
in the same rule *did* apply, only because `#ball` does not declare opacity — so the rule half
worked, which is why nothing looked obviously wrong. Now `#ball.sleeping`.

### What this says about the last two decisions

D14 was "it resolves" mistaken for "it installs". D15 was "it installs" mistaken for "it runs".
D16 is **"it runs" mistaken for "it works"** — every protocol assertion in D15 was true, and the
thing on screen was still an empty box with a title bar.

The ladder has a top and it is the only rung that was ever the point:

    pip resolves it     ->  D14 said this was enough. It was not.
    it imports          ->  it SIGKILLed one call later.
    the code path runs  ->  D15 verified this. Still an empty rectangle.
    a human can use it  ->  screenshot it, or ask.

For anything with a visual output, **the artefact is the screenshot.** `curl /healthz` cannot
see a title bar. D15 at least knew it was owed one and said so; the honest improvement is to
take it before signing off, not to note that somebody should.

`media/captures/2026-08-22-avatar-render-before-after.png` is the pair, and
`2026-08-22-avatar-on-desktop.png` is the working desktop.

### Still open, and it is a preference not a bug

The ball lands **on top of the chat panel**. Wayland lets no client place its own window, so
labwc decides, and `hud/float.py` has the same constraint (D41's note about `gtk4-layer-shell`
not being installable here). Moving him is `Super+drag`. Whether 300x300 always-on-top in the
middle of the screen is the right presence at all is LB's call, not a measurement.
