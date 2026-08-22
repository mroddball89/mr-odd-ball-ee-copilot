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
