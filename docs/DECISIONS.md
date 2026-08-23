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
installed, which matters because the Pi did not have it.

**That last clause said "and never will", and on 2026-08-22 it stopped being true** — LB
installed KiCad 9.0.2 on the Pi. It changes nothing about the design — the whole point is that
`kiutils` reads the files directly, so the parser never depended on KiCad being absent or
present — but a flat prediction in a decision log is a thing people navigate by, and this one
was wrong. See D19 for what the install bought: a free validation corpus.

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

> **2026-08-23, D26.** Two things about that paragraph aged badly. The test it describes was
> never committed, so for four days this page asserted a green suite that did not exist — see
> D26; `tools/verify_gestures.py` is now real. And "each tip below its own PIP joint" turns out
> to be satisfied by a **claw** and by a **pinch**, both of which therefore classified as
> `THUMBS_UP` and approved shell commands. The curl clause was necessary and it was not
> sufficient. Fixed in D26 by ordering, without changing the test above.

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

## D17 — I built a second character next to the one that already existed

**2026-08-22.** D13 through D16 are the story of a floating avatar: a 120px gradient ball in a
pywebview window, a FastAPI server to feed it state, a stdlib-only broadcaster so the mirror
would not drag FastAPI onto the voice loop, two WebKit environment variables, a labwc window
rule to pin it to a corner. All of it worked. All of it is now deleted.

**`hud/face-preview.html` already renders the character.** It is 1565 lines of SVG and an eased
render loop; it has fifteen states, a gesture system, live lip-sync, and it was already
connected to `hud_bridge`. The screenshots I took to prove the ball worked have the real Mr Odd
Ball in them, four times, at the top of every frame. I did not see it, because I had decided in
the first ten minutes that the avatar was an *additional* small face and never revisited that.

LB: *"I already have a main frontend UI rendering my character. I do NOT want a separate
glowing blue orb in the corner."*

### What the rig already had, that I reimplemented badly

```js
bounce:  { dur:1.70, attack:.30, rise:70,  bounces:3, damp:1.5, zoom:.86, mouth:.10 },
roll:    { dur:1.90, attack:.30, travel:150, spin:540, zoom:.70, eye:.10 },
```

**The two animations LB asked for were already in the file, as named gestures**, better than
mine: my roll was `translateX` plus a `rotate`, this one ties spin to *displacement* so he
winds up going out and unwinds coming back; my bounce was a `translateY` ease, this one is a
raised cosine so vertical velocity is zero at every ground contact and there is no impact cusp.
Both are covered by `tools/verify-rig.mjs`, which proves they keep him inside the viewBox.

`setState` already had the hook to fire one: `if (T.enter) playGesture(T.enter)` — *"a state can
fire a gesture, never the reverse."*

So the entire feature was four edits: a `thinking` row, `enter`/`loop` on two states, a
five-line restart in the sampler, and a panel button.

### And the rig had a real bug, which the orb had been hiding

```js
function setState(next) {
  if (!STATES[next]) return;
```

**There was no `thinking` state.** `hud_bridge` broadcasts exactly five names and
`engine/turn.py` sends the literal string `"thinking"` — which hit that guard and returned.
**His face has never once reacted to him thinking.** The three rows that look like they cover
it (`local`, `claude`, `gemini`) are reachable only from the dev panel; the tier system they
belong to went in the merge (D1).

I did not find that by reading the rig. I found it because building a second face forced me to
ask what the first one did with the state, and by then I had already built the second face.

### Looping a one-shot gesture, safely

`enter` fires once. LB wants rolling *while* thinking, so states may now declare `loop:1`, and
`sampleGesture` restarts the gesture at completion instead of clearing it.

That is only seamless because every channel in the generic sampler is periodic and lands on
exactly zero at `p=1` — `tx` and `spin` are `sin(2πp)`, `ty` is a damped raised cosine over a
whole number of bounces, `zoom` is derived from those. The rig's own comment says it out loud:
*"Out, through centre, back — so a roll returns him exactly where he started."* A gesture with
its own `sample()` — `finish` — makes no such promise, which is why `loop` is opt-in per state
and defaults to 0.

Measured on the Pi from a real turn, three frames of each state, tracking his body colour
`#2B5599` read out of the rig's own CSS:

| state | horizontal | vertical | size |
|---|---|---|---|
| thinking | **43 px** | 6.5 px | **107 px** — rolls out, spins, shrinks |
| speaking | 0.7 px | **11.1 px** | 23 px — bounces in place |

Cleanly distinguishable, and the right way round. `media/captures/2026-08-22-face-thinking-speaking.png`.

### What was deleted

`ui/` entirely, `launch_ui.py`, `config/mroddball.desktop`, `tools/wait_for_ui.sh`,
`tools/install_labwc_rule.sh`, the `--avatar` flag and its in-process server, the
`hud_bridge.set_state` mirror, and `fastapi` / `uvicorn` / `pywebview` from requirements. The
labwc rule was reverted on the Pi first and `~/.config/labwc/rc.xml` removed once it was
byte-identical to the system default, so the box is back to how I found it.

`gir1.2-webkit2-4.1` — the apt package LB installed at my request — is dropped from
`stage_install.sh`'s list. It is pywebview's alone; `float.py` uses the GTK4 WebKit 6.0 that
was already there. Left installed on the Pi, where it is harmless.

The captures from the dead end stay in `media/captures/`. A failed experiment that gets quietly
deleted is a lesson nobody can check.

### The rule

**Before adding a surface, find out what renders that thing today.** Not "is there a related
file" — I had `hud/face-preview.html` open on the first pass and catalogued it as "the full
character rig with the chat column", which is exactly right and should have ended the idea of a
second face on the spot.

The directive said "implement a transparent floating overlay UI", and it was reasonable to read
that as new. What was not reasonable was building it, deploying it, debugging its renderer,
pinning it to a corner, and writing four decision entries about it, without once asking whether
the character it drew already existed somewhere in the repo. Four rounds of real debugging —
D14, D15, D16, and the placement work — went into a component that should not have been built.
The debugging was sound. The thing being debugged should not have existed.

---

## D18 — Six reported bugs: four real, one already built, one that would have made things worse

**2026-08-22.** LB reported six synchronisation and latency faults, with a prescribed fix for
each. Checked against the code before touching it, they sorted into four different kinds, and
that sorting is the useful part of this entry.

| # | reported | what was actually there |
|---|---|---|
| 1 | TTS drops words; strip markdown, remove code, expand symbols | markdown and code **already handled**; symbol expansion missing, and it was the real cause |
| 2 | avatar sleeps while the OS agent runs bash | **real bug, confirmed** |
| 3 | mic cuts off on a pause; raise `pause_threshold` to 2.0 | no `speech_recognition` in this stack; `hangover_s` is the analogue, raised 1.10 -> 2.00 |
| 4 | vault not bound to GENERAL; add error logging | **already bound**; logging was genuinely absent |
| 5 | hold the camera open in `__init__` to kill 2-3 s of latency | **would save nothing and cost something — not done** |
| 6 | flush frames, lower confidence to 0.5 | flushing already existed; confidence lowered |

### 1 — the words were not dropped by the synthesiser, they were refused by policy

The report said the TTS engine skips words because of markdown. `audio/say.py:speakable()` has
stripped markdown and emoji since 2026-08-13, and `engine/split.py` lifts fenced code onto
cards and **rejects** any spoken candidate containing a fence. Both were already done.

What was missing is symbol expansion, and the failure it causes is far worse than a stumble:
`is_speakable()` refuses text containing Ω μ ° ± τ ², and a refused sentence is **replaced
wholesale** by "I've put it on the screen." So *"The trace needs 0.9 mm for a 20°C rise"* was
never spoken — not mispronounced, not truncated, **not said at all** — and the more correctly
an agent wrote an engineering answer, the more reliably it was thrown away.

`expand_symbols()` runs on the candidate **before** the gate judges it. Note what it does not
do: the gate is unchanged and still runs on everything. This only normalises the input, so a
sentence is refused for being unsayable rather than for spelling "ohms" with a Greek letter.
On the harness corpus, seven sentences went from dropped to spoken.

Cards keep the original notation — a card is read, not heard, and `20°C` is right on screen.

### 2 — the real one, and it was one missing line

`Turn._deliver()` resolves a gate with `Engine.ask(answer)`, and on an **approved** gate that
is not a question — it is `resume_os_action()`, spawning a subprocess and waiting on it.
Nothing set a state first. The last state set was `listening` (spoken path, waiting for the
yes) or `speaking` (typed path), and `run_voice.py`'s idle timer then drops the face to the
resting state — `sleeping` — after a delay that knows nothing about the work about to happen.

The one stretch of a turn that visibly takes time was the one stretch showing no sign of life.

Set unconditionally rather than for `kind == "os"`: a web search is a network round trip on the
same path, and a rule that names one route is a rule the next route forgets.
`tools/verify_gate_state.py` asserts the state **at the instant the action runs** — checking it
afterwards proves nothing, because the interesting moment is already over — and the fix is
mutation-tested: remove the line and two checks go red.

### 3 — the prescribed knob does not exist here, and the analogue has now been raised four times

There is no `speech_recognition` in this project; capture is a custom VAD over openWakeWord's
Silero. The analogue is `hangover_s`, and it has now gone **0.6 -> 0.75 -> 1.10 -> 2.00**, every
time for the same complaint. A number raised three times and still wrong was being nudged rather
than solved.

**What it costs, stated rather than buried: 0.9 s is added to every turn**, including the free
lookups that answer in microseconds. "What time is it" now waits 2 s of silence first.

`tools/verify_stt.py` held `hangover < budget / 2` — "the hangover must never dominate the
turn" — and at 2.0 s of a 2.5 s budget it fired correctly, because **it now does dominate**.
Re-inflating the budget to 4.1 so the ratio passes would have been a lie about what the answer
budget is. The ratio is replaced by an explicit ceiling, and the harness now **prints the 80%
share on every run** rather than hiding it behind a pass. If that share climbs further, the
answer is not a bigger number — it is push-to-talk, or a VAD that can tell a thinking pause
from a finished sentence.

### 4 — already routed; the gap was that failures were invisible

`save_to_vault` and `read_from_vault` are bound to firmware, hardware **and persona** — and
`engine/core.py` sends GENERAL to persona, so GENERAL has had the vault all along.

The real gap: every failure path returned a string and logged nothing. The model then
*paraphrases* that string, so the spoken version of a failure is one smoothed-over sentence
with the cause gone. The log is the only place an errno survives. `PermissionError` is now
named explicitly — the vault lives under the repo, and a repo deployed by `tar` over ssh can
arrive read-only, which is exactly the failure LB asked to be able to see.

### 5 — the camera fix would have bought nothing, and this is why

The request: move `cv2.VideoCapture(0)` into `__init__` so the stream stays open, killing the
2-3 s block.

**`get_gesture()` in the parent always spawns a child process** — `--once`, whose own help text
is "print exactly one gesture token on stdout and exit". That is not an optimisation, it is
crash isolation: mediapipe 1.x on this Pi does not raise, it **SIGKILLs** (D15), and doing the
work in-process risks killing the voice loop at a security prompt.

So a `GestureRecognizer` lives for exactly one detection and dies with its process. Caching the
camera on the instance saves **zero milliseconds**, because the open happens once per process
either way. From the measured 2,217 ms:

    import mediapipe        1,009 ms   45%   <-- the actual cost
    warmup frames             602 ms   27%
    open camera               204 ms    9%   <-- all that __init__ could ever touch
    build landmarker           55 ms
    inference                  47 ms
    interpreter start          22 ms

Holding the device would also make it unavailable to anything else for the whole session, for a
feature used a few times an hour. **The real fix is the persistent worker** — pay the 1.0 s
import once, keep a pipe open, ~850 ms per approval — which was already considered and tracked.
Implementing the requested change would have added a held device and a lifecycle to manage in
exchange for nothing measurable.

### 6 — done, and honestly still a guess

`WARMUP_FRAMES` 4 -> 5 and detection confidence 0.6 -> 0.5, now **one named constant** instead
of a literal repeated in each of the two mediapipe API branches.

Worth being explicit, because "make approval more forgiving" sounds like a safety change and is
not: this threshold governs *is there a hand in frame*, **not** *is it a thumbs up*. The gesture
decision is `_classify()`, pure geometry with no confidence in it, and it still requires the
other four fingers curled — an open palm is not an approval at any detection confidence. So
this makes a hand easier to **find** and does not make approval easier to **get**.

Both numbers remain guesses at the same suspected cause (an underexposed frame), for the reason
D14 is about: **there is no detection-rate measurement to tune against.** What makes them
acceptable is direction — a missed thumbs-up costs one retry and falls back to the keyboard.
The honest next step is a measurement, not another nudge. Tracked in `tasks/todo.md`.

---

## D19 — The parser met 29 files it did not help shape, and got all of them

**2026-08-22.** LB installed KiCad 9.0.2 on the Pi and asked whether the parser works. It does,
and the install is worth more than the answer: `kicad-demos` ships **85 schematics and 16
boards** written by KiCad itself, at a format version this parser has never seen.

That matters because **every fixture in `tests/fixtures/kicad/` is hand-written.** They were
built to pin known hazards (D9), which is the right way to test the hazards — and it means the
whole existing corpus was authored by the same person who wrote the parser, against the same
mental model of the format. A hand-written fixture cannot surprise you about what KiCad
actually emits.

### The result

| | parsed | failed | refused by design |
|---|---|---|---|
| schematics (root sheet per project) | **14** | 0 | — |
| boards | **15** | 0 | 1 |

**1,319 parts across 14 designs, 0 failures.** The largest is `vme-wren` at **820 parts across
36 hierarchical sheets** — an order of magnitude past anything in the fixtures, and the sheet
walk handled it.

The D9 fixes are visible in the output rather than merely asserted by a harness:

- `Copper layers: 2 (F.Cu, B.Cu)` and `4 (F.Cu, In1.Cu, ...)` — not the 29 that
  `len(board.layers)` returns once adhesive, paste, silkscreen, mask, courtyard, fab and the
  nine user layers are counted.
- `Nets: 52 named, 53 including KiCad's unassigned net 0` — the off-by-one that inflates every
  net count on every board, forever, reported instead of absorbed.

### The one refusal is the size guard, and it is the right answer

`vme-wren.kicad_pcb` is **70 MB**, over `_MAX_BYTES` (50 MB), and comes back as *"vme-wren.kicad_pcb
is 70 megabytes, which is larger than I will read in the middle of a conversation."* A sentence
that is true, actionable and speakable — not a hang, not an OOM, and not a traceback. Note that
its **schematic** parsed fine at 820 parts; it is the board that is enormous.

### Name resolution, which is the part that exists for the voice

Pointed at the demos as `ODDBALL_KICAD_ROOT`, a dictated name behaves as D9 requires:

    "video"              -> resolved, 186 parts across 8 sheets
    "pic programmer"     -> "That name matches more than one file and I will not guess between them"
    "tiny tapeout"       -> same, and lists the candidates
    "nonexistent board"  -> a clear not-found naming where it looked

The demos are an unusually adversarial root — most projects hold several `.kicad_sch` files
because sub-sheets are files too — so ambiguity is the *correct* outcome for most of them, and
refusing is the whole point. Answering confidently about the wrong board is worse than asking
which one, because the answer is right about something LB did not ask about.

### What this does not prove

These are **demo files**, curated by the KiCad project to load cleanly. They are not evidence
about a half-finished board with a broken symbol link in it, which is the state LB's own designs
will actually be in. The hand-written fixtures still carry that job — they hold the malformed
cases on purpose, and nothing here replaces them.

Nor is it a regression suite yet: it was run once, by hand, from a scratch script. Wiring
`/usr/share/kicad/demos` into `tools/verify_kicad.py` as an *optional* corpus (skipped when the
directory is absent, so Windows and a fresh clone stay green) is tracked in `tasks/todo.md`.

### The measurement script had the bug, not the parser

First run reported two schematics as EMPTY. Both were fine — 820 parts and 160 parts. The check
was `"0 parts" in out`, which matches inside **"820 parts"**.

That is **L11, committed the previous day**, in a throwaway script written by the same person
who wrote the lesson. Recorded because the lesson evidently needs the second telling: a
substring check over free text finds the substring wherever it lives, and the failure looks
like a finding rather than like a bug.

---

## D20 — He asked four times, and it was never the launcher

**2026-08-22.** LB: *"he struggled with opening firefox thats my main concern."* From the log,
between 21:08 and 21:13 he asked five times and Firefox never appeared.

**The launcher was never the problem.** Run on the Pi with the display variables stripped
exactly as the service has them, `launch("firefox")` returns `kind: launched` and Firefox
appears — `find_display()` globs `/run/user/1000/wayland-[0-9]` and finds the socket the unit
never gave it. D10's machinery is intact.

What failed was the **confirmation**, every single time:

    21:08:15  heard 'Can you open firefox?'
    21:08:15  route 0ms -> os | free launch (firefox), gate os waiting     <- correct, 0 API calls
    21:08:20  capture: peak mic rms 0.0201
    21:08:22  heard '' in 1.59s
    21:08:22  gate: heard '' -> None                                       <- DECLINED

Twice, identically. The mic levels are the whole diagnosis:

| what he said | mic RMS | transcribed |
|---|---|---|
| "Can you open firefox?" | **0.2821** | correctly |
| his confirmation | **0.0201** | **empty** |
| "Open Firefox." | **0.3300** | correctly |
| his confirmation | **0.0166** | **empty** |

**Commands are 15-20x louder than confirmations.** He states the request deliberately and
confirms casually, and the casual "yes" is below what this microphone delivers. `is_yes("")` is
None, None declines, and the decline was **silent** — so from the outside it is indistinguishable
from a crash. He asked again, which is the correct response to what he could observe.

### The fix he already owned, wired into the wrong path

`approve_by_gesture_or_keyboard` was called only from `run_os_agent()` and `run_web_agent()` —
the old blocking terminal functions, whose own docstrings say *"Deliberately NOT the path the
voice loop takes."* The camera worked, the model was on disk, the sidecar venv was built, and
`--backend` reported `available: True`.

**The thumbs up existed and the voice loop could not reach it.** Same shape as D6's typed
channel: built, and not wired.

`Turn._spoken_gate_answer()` now reads three channels in a deliberate order:

1. **Voice** — instant, and how he actually answers.
2. **The camera, only when voice was unreadable.** A gesture costs a subprocess and ~2.2 s
   (D15 measured it), so checking it on every approval would tax every gate to rescue the few
   that need it. The harness asserts `camera_checks == 0` on a clear spoken yes.
3. **A click**, which beats both because it is unambiguous.

### A silent decline is a bug, not a default

*"I didn't catch that. Please give a thumbs up to the camera or say yes clearly."* Then it
listens once more. `GATE_ATTEMPTS = 2` — one retry, not a loop, because a gate that keeps
asking eventually gets a yes out of a television, and the safe default is already a decline.

Mutation-tested: make the gate return `"yes"` when nothing is readable and three checks go red.

### The 217-second turn, and why the retries were certain to fail

By 21:10 the daily free tier was gone, and the google-genai SDK was retrying a **daily** quota
429 with exponential backoff — 1.78s, 2.54s, 4.44s, 8.79s, 16.5s — for **217 seconds** on one
turn. A daily quota does not come back in 8 seconds; it comes back at midnight. Every retry was
known to be futile before it was sent.

Worse: the turn thread is the thread that drains the microphone. **3,568 audio frames were
dropped** while he sat there retrying. He was deaf for the whole time he was failing to answer.

Two changes, and only together:

- **`LLM_MAX_RETRIES = 0`.** Measured with the quota actually exhausted: the default (6) took
  **217 s** to give up, zero took **0.2 s**. The cost is real and worth naming — a genuinely
  transient blip now fails the turn instead of being retried invisibly. At 40 words a turn that
  is the right trade: "something went wrong", asked again, three seconds. Overridable with
  `ODDBALL_LLM_MAX_RETRIES`.
- **A latch** (`engine/quota.py`). Without it every later turn still pays a round trip to be
  told the same thing. With it, the second and later turns cost nothing at all.

**The 429 is not one thing**, and the two kinds now say different sentences:

    quotaId: ...PerDayPerProjectPerModel-FreeTier      -> "API quota exceeded for today"
    quotaId: ...PerMinutePerProjectPerModel-FreeTier   -> "back in a few seconds"

An **unlabelled** 429 resolves to transient, deliberately. The errors are not symmetric:
guessing "daily" also latches the model out until midnight, taking him off the air for a day
over what may have been a two-second burst. Guessing "transient" costs one wrong sentence and a
retry that now takes 0.2 s.

**The latch is per model**, because D3 split routing, agents and persona across three model
names precisely so one running dry does not silence the others. And matching the model name
needed care: **`gemini-3.5-flash` is a strict prefix of `gemini-3.5-flash-lite`**, so
`model in text` reads a router exhaustion as an agent exhaustion and silences the agents over a
quota that was never theirs. `names_model()` requires the match not be followed by more name.
That is L11 again, written one commit after L11.

### The launcher: it was "fire fox", not the trailing clause

LB asked for the end-anchor rule to tolerate trailing conversational clauses. It does now — but
the measurement found a bigger cause first. **`tiny.en` writes "firefox" as two words**, and
plain *"Open fire fox."* missed the catalogue entirely. Three of the four attempts died there,
before any clause was involved.

- `_strip_joined()` matches an app name spoken as separate words, by **exact concatenation
  only**. Not edit distance, which this module and `app_catalogue.resolve()` both refuse for
  the stated reason: a threshold loose enough to catch "tawny" -> "thonny" is loose enough to
  catch "shut up" -> "shut down". Concatenation cannot do that — "shutup" is not "shutdown".
- `DESCRIPTOR` lets a trailing appositive through: "open firefox, the internet browser". It is
  **nouns only**, and that is the entire safety argument — a leftover built only from nouns
  cannot express a second action, so "open firefox and delete my files" still refuses on
  "delete". Mutation-tested **on the Pi**: add "delete" to `DESCRIPTOR` and it launches.

**That mutation test was first run on Windows and proved nothing**, because the Windows
catalogue has no `firefox` entry — Firefox is not installed there, so the phrase never matched
a target and every result was "refused" for the wrong reason. The checks now live in
`verify_launch.py` section 7, which swaps in a **fixture** catalogue containing Firefox, so the
property is tested on every machine on every run rather than by hand on one.

### What is NOT fixed

**The microphone.** Every capture that transcribed was **clipping at digital full scale
(0.9999)**; every one that failed sat at 0.015-0.019 RMS. The gain is maxed (D6: 16/16, +30 dB),
so there is no usable middle — he is either on top of it and distorting, or inaudible. Nothing
in this commit improves that; it routes around it. The thumbs up is a way to answer that does
not need the microphone at all, which is the honest description of what was done.

Also unfixed and now recorded: the running service has **no `WAYLAND_DISPLAY`**. It started 8
seconds after boot, before the desktop imported its environment into the user manager, exactly
as D10 defect 1 describes. It does not matter today because `find_display()` discovers the
socket, but the unit is still wrong and a future path that trusts the environment will fail.

## D21 — The endpoint I was asked for was on a framework this repo deleted on purpose

**2026-08-23.** LB asked for a paperclip in the chat panel: pick a file, it uploads, Mr Odd Ball
files it and rebuilds whatever index it feeds. The specification named the endpoint precisely —
*"Create a new `POST /upload` endpoint in the FastAPI server (`engine/server.py` or
equivalent)"*.

**There is no FastAPI server.** D17 deleted `ui/` and dropped `fastapi`, `uvicorn` and
`pywebview` from `requirements.txt` four days ago, and `requirements.txt` still carries the
gravestone: *"There was briefly a `fastapi` / `uvicorn` / `pywebview` block here."*

So the request as literally written was to reverse a decision that had a reason, and the reason
had not stopped being true. What LB wants is a paperclip. FastAPI is how he expected to get one.
Those are different things, and only the first is the deliverable.

`engine/server.py` exists now, at the path he named, serving `POST /upload` and accepting
`multipart/form-data` — on `http.server`, in a daemon thread, with a hand-rolled multipart
parser. No new dependency, and nothing new on the voice loop's import path.

### Sharing port 8765 was the first plan, and it is not possible

`orchestrator/hud_bridge.py` serves the rig over HTTP from `websockets`' `process_request` hook,
which is why the page and its socket live on one port and the rig can derive one from the other.
The obvious move was to answer `POST /upload` from the same hook.

It cannot be done. Checked against the installed library rather than assumed:

```
>>> [f.name for f in dataclasses.fields(websockets.http11.Request)]
['path', 'headers', '_exception']
```

**A `Request` has no body.** websockets 15.0.1 parses the request line and the headers and stops;
the bytes of a POST body stay in the transport buffer, where the sans-io frame parser will meet
them next and make nothing good of them. Reading them means reaching into
`protocol.reader` — a private stream on a library whose whole job is a different protocol — and
putting a file transfer on top of it.

So: a second port, 8767. Not 8766, which is `tools/face_stage.py`'s documented port and has to
stay free so a stage can run beside the live assistant. The rig learns it as a constant with a
`?up=host:port` override, mirroring the `?ws=` it already had.

### The guard that is not about the network

Binding to 127.0.0.1 stops the *network* reaching the endpoint. It does not stop **the browser on
this machine**: a `multipart/form-data` POST is a CORS "simple request", so any page LB has open
can fire one at any loopback port with no preflight. It cannot read the reply — and it does not
need to, because the file has already landed.

So an `Origin` header, when present, has to be a loopback http(s) origin. Absent — curl, a
script — is allowed, because that is a shell on the box and a shell already has `cp`.

`null` is refused, and that is the one worth writing down: `null` is what a `file://` page sends,
and it is also what every sandboxed iframe on the internet sends. The rig only needs the
paperclip while the assistant is running, and while it is running the page is served over HTTP by
the bridge. Allowing `null` would have opened a large door for a case that cannot happen.

### The rebuild does not run inside the turn, and that is the whole design

`process_inbox_file` moves the file and then hands the index rebuild to a background thread.

`tools/vector_db.build_vector_database()` imports `langchain_huggingface`, which imports torch
and transformers, and then re-embeds every page under `data/`. These tools are called from
**inside an agent turn**: blocking there is a face frozen mid-`thinking` with the microphone shut,
an LLM call timing out underneath it, and `run_voice.py`'s idle timer dropping him to `sleeping`
while the work is still running. That is the same bug `engine/turn.py` documents at its
permission gate — *"he falls asleep while running a command"* — reached by a different road.

So the tool returns two facts, and the prompt makes him say both: the file is filed, and it is
**not searchable yet**. `index_status` is how he answers "is it ready".

**And then it was measured, on the Pi, and "minutes" was wrong.**

| stage | seconds |
|---|---|
| `import torch` | 2.1 |
| `import langchain_huggingface` | 1.3 |
| load `all-MiniLM-L6-v2` off the SD card | 8.0 |
| **before a single chunk is embedded** | **11.4** |
| per chunk after that | 14.4 ms |

`media/data/2026-08-23-index-rebuild-familyhub.csv`, three trials, spread under 0.15 s. A cold
page cache costs ~2 s more.

Eleven seconds, not minutes — and every sentence in the repo that said "minutes" was corrected
to the measurement, including the one the model reads. **The design does not change**, which is
the useful part: an 11 s freeze on the turn path is still a face stuck in `thinking` with the
microphone shut, and the per-chunk cost multiplies by the **whole corpus** rather than by the
uploaded file, because `build_vector_database()` rebuilds both collections from scratch. So the
first datasheet costs ~15 s and the fiftieth costs far more.

What did change is what he SAYS. The tool used to promise "a few minutes on the Pi"; it now
promises no duration at all, because the honest answer depends on how much has been uploaded and
there is no version of that sentence which is true in both cases. D8 and D9 are both about
confident numbers nobody would question — this is one that was written into a prompt.

Measured, because the in-memory read and the 64 MB cap are choices with a cost and the cost is
paid on the button:

| uploaded | round trip | throughput |
|---|---|---|
| 16 KB — a schematic | **1.5 ms** | — |
| 1 MB — a datasheet | **4.0 ms** | 265 MB/s |
| 16 MB — a gerber bundle | 22.9 ms | 733 MB/s |
| 60 MB — against the cap | 181 ms | 347 MB/s |

Median of three, and the run-to-run spread at the top rung is real — a second run of the same
script gave 220 ms for the 60 MB rung against this one's 181. That is an SSD and a scheduler,
not a change in the code, and it is why the committed CSV is the number rather than any sentence
here. Nothing about the conclusion moves: the top rung is a fifth of a second either way.

`media/data/2026-08-23-upload-latency.csv`, `media/charts/upload-latency.svg`. Loopback on the
Windows box, not the Pi — the meta file says so and says to re-run it there. The curve is flat
across everything LB would actually attach, so the simple single-shot design is the right one and
a streaming upload with a progress bar would be complexity bought with nothing.

### Extraction is incremental now, and that is a quota fix

`extract_deadlines_from_syllabi()` re-read every syllabus and spent one API call per file. Filing
one uploaded syllabus into a folder holding five would have spent a quarter of the day's free tier
(D3: 20 requests per model per day) re-reading four documents that had not changed. It now takes a
`sources` argument and the file manager passes the one file that arrived.

That merge has a failure mode which had to be closed before it could ship: an incremental run drops
the target file's old deadlines from the carry-over set on the assumption it is about to re-read
them. If the API call then fails — a 429, a network blip — those deadlines are gone, and the only
symptom is a deadline banner that quietly stops appearing. The failure branch puts them back.

### Two bugs the harnesses found, and one only a live run could

`tools/verify_upload.py` is 134 checks. Two of them failed the first time on real defects:

**`project_file` could never match.** `_CATEGORIES` is looked up by slug, and `_slug` strips the
underscore — so the key `"project_file"`, a word the prompt offers the model *by name*, was
unreachable. The keys are slugged at build time now. A lookup table whose keys are not in the form
the lookup uses is a table with silent holes in it.

**`relative_to(REPO_ROOT)` raises.** It was building the "Filed it to data/espressif/" sentence,
inside a tool called from an agent turn — where a raised exception becomes a spoken traceback.

And then the thing no harness had thought to ask. The first real end-to-end run, curl to
paperclip endpoint to `file_manager --list`:

```
2 file(s) waiting in data/inbox/:
  .gitkeep         (1 KB)  — category unclear
  flat.kicad_sch   (6 KB)  — looks like a schematic
```

**`.gitkeep` is committed so the folder survives a fresh clone**, and it therefore looked to him
like something LB had uploaded and wanted filed. The fix is one clause; the lesson is that the
harness tested `data/inbox` against a *temporary directory it had made itself*, which by
construction contained only what the test put there. The real directory has a file in it that the
repository put there, and no test that builds its own world was ever going to see that.
`tools/verify_upload.py` covers it now, including `.DS_Store` and `Thumbs.db`, which arrive at the
same door.

### The rule

**When a request names a framework, check whether the framework is there before building on it.**
The deliverable was a paperclip. FastAPI was one person's guess at the plumbing, made from outside
the repo, and it was four days out of date. Building it as specified would have reversed D17
without anyone deciding to — the commit message would have said "add upload endpoint" and the
diff would have said "reinstate FastAPI, uvicorn and an ASGI stack on the voice loop's import
path". A specification is evidence about the goal, not about the codebase.

## D22 — The syllabus was never the source of truth, and the URL was a password

**2026-08-23.** LB: *"The static syllabus PDFs contain outdated dates. Instead, we are switching
to a live Canvas LMS `.ics` calendar feed."*

He is right, and the reason is worth stating precisely. **A syllabus is a snapshot.** A date
moved in week four is right in Canvas and wrong in the PDF, and the PDF's version is the one
`extract_deadlines_from_syllabi()` extracted, at one Gemini call per document, against a quota
D3 measured at 20 requests per model per day. The feed costs one HTTP GET and no model call at
all. It is strictly better on accuracy AND on quota, which is a rare combination.

`tools/canvas_sync.py` fetches the feed, parses it with `icalendar`, and writes the same
`academic_calendar.json` everything already reads. Nothing downstream changed: the banner, the
day-granularity comparison, `format_calendar_for_llm`, the deadline check on every turn.

### The URL is a credential, and it was going to be committed

The spec said *"The URL to hardcode (or default) is: …user_hBJNTDIYLYslxNmEdKLmv56ON13DQ0QrSjnzMDiC.ics"*.

**That token IS the authentication.** Anyone holding the URL reads LB's entire calendar, with no
login, until he resets it in Canvas. This repository has a GitHub remote and the branch is
tracked. Hardcoding it would have published it in the same commit that added the feature, and
the fix afterwards is not `git rm` — it is resetting the token in Canvas, because the history
keeps it.

So it lives in `.env` as `ODDBALL_CANVAS_ICS`, gitignored and excluded from the deploy tarball,
written once per machine. Exactly the treatment `GOOGLE_API_KEY` gets (D7) and the convention
`ODDBALL_KICAD_ROOT` follows. There is deliberately **no fallback constant**: a default that is
a live token is a published token the moment anyone commits.

`tools/verify_upload.py` now greps the module for `user_[A-Za-z0-9]{20,}` and fails if one ever
reappears.

### Four things the real feed taught, and the obvious implementation gets none of them right

Measured against the live feed — 139 events, one course, 68 KB:

**1. The date is not the date.** All 139 `DTSTART`s came back as `date`, so the naive reading
happened to work. But Canvas emits a *datetime* for anything with a time of day, and an
assignment due 11:59 PM is stored as **03:59Z the next day**. `.date()` on that UTC value is off
by one — every time, silently, and in the direction that tells LB he has an extra day. The Pi is
`America/New_York`, so it would never have self-corrected. `_due_date` converts to local before
it truncates, and the harness pins `2026-09-02T03:59Z -> 2026-09-01`.

**2. Summaries carry HTML entities.** `Sample AI Policy&#8212;Responsible Use`, 2 of 139. That
text is read aloud by Piper and printed on a card. `&#8212;` is not a word.

**3. The bracketed course is not a course code.** It is `POSC201.W02_Fall 2026` — code, section
and term. "POSC201.W02_Fall 2026, Knowledge Check VM.1" is not a sentence anyone wants spoken,
so the code is cleaned to `POSC201` and the raw string kept as `section`.

**4. One course produced 139 deadlines.** A syllabus extraction produced ten or twenty.

That last one is not a detail, it is a different order of magnitude, and it broke something
quietly: `format_calendar_for_llm()` puts the calendar in the prompt of **every** ACADEMIC turn.
Measured, one course:

    139 entries -> 9,580 characters -> ~2,400 tokens, per question
    extrapolated to five courses     -> ~12,000 tokens, per question

Most of it weekly knowledge checks three months out that nobody is asking about. So the renderer
is bounded: everything inside 60 days, **plus every exam and project at any date** — that second
clause is the load-bearing half, because "when is the final?" is exactly the question a plain
horizon would break — capped at 120 lines, and it **says what it omitted**. A model handed a
silently truncated calendar answers "nothing is due then" about a date it was never shown, which
is the fabrication this whole route exists to prevent. After the first real sync on the Pi: 89
lines of 139, 6,332 characters, ~1,583 tokens.

### Two writers, one file, and neither can erase the other

`canvas_sync` and `extract_deadlines_from_syllabi` both write `academic_calendar.json`. They
coexist through the `source` field that already existed for the incremental merge: Canvas rows
are marked `canvas`, PDF rows carry the filename, and each writer only ever replaces its own.
`extract_deadlines_from_syllabi` now preserves Canvas rows on **every** run including a full
rebuild — without that, running the old script once would silently revert the calendar to stale
dates.

The syllabus RAG is untouched, and that was explicit in the request. `tools/vector_db.py` still
embeds `data/academic/` into the `academic` collection; the agent still retrieves policy prose
from it. **Prose is what a syllabus is actually good for** — the late policy, the grading split,
what the course covers — and none of that is in a calendar feed. Only date extraction retired.

The upload path went from two model calls to zero: `tools/file_manager.py` no longer asks for
the calendar job when a syllabus is filed, and the sentence he says now points at Canvas for
dates instead of promising to read them out of the PDF.

### What the harnesses caught that I did not

Two failures on the first full run, and one was a real deployment bug:

**`icalendar` was in `requirements.txt` and in no `stage_install.sh` stage.** `verify_agents.py`
cross-checks those two lists precisely because they drift, and it said so by name: *"INSTALLED
NOWHERE on a fresh Pi: icalendar"*. Every existing box would have been fine and every new one
would have failed at import.

**`verify_upload.py` asserted the academic upload requests both vectors and the calendar.** That
was correct until this commit and is now exactly backwards, so the check was inverted rather than
deleted — the absence of the calendar job is the design decision, and it should fail if someone
puts it back without meaning to.

### And a NUL byte, written by me, in a document

`docs/DEPLOY.md` had a literal `\x00` in the middle of the face-window restart snippet — from a
previous session, where a `tr "\0" "\n"` written through a shell heredoc lost a backslash level
and produced the byte instead of the escape. `git` treated the file as binary; the command was
uncopyable. Found only because `grep` said *"Binary file docs/DEPLOY.md matches"* while I was
looking for something else.

The repair was itself bitten by the same thing — `b"\\0"` arrived as `b"\0"` and replaced a NUL
with a NUL — and only worked when built from `bytes([92, 48])`, which no escape processing can
touch. The sweep now covers NUL as well as CRLF.

### The rule

**A URL that authenticates is a password with a scheme on the front.** It does not look like a
secret, it arrives in the same sentence as the feature request, and it gets pasted into source
because that is where URLs go. The test is not "does this look like a key" but "what can someone
do if they have it" — and for a Canvas feed the answer is "read everything, forever, without
logging in". Treat it like the API key, because it is one.

## D23 — Removing the syllabus RAG, and the premise for removing it was already gone

**2026-08-23.** LB: *"Because it is so reliable, having a redundant system that parses syllabus
PDFs is creating unnecessary complexity and risk of conflicting data. I want to completely excise
the Syllabus PDF / Academic RAG feature."*

Done, in full. The reasoning is recorded because **the stated premise was not true when it was
stated**, and that was said before the deletion rather than after.

### The redundancy had already been removed

D22, one commit earlier, retired PDF *date extraction* entirely. By the time this request
arrived nothing scraped a date from a syllabus, nothing wrote a deadline from one, and the two
writers of `academic_calendar.json` were separated by a `source` field so that neither could
erase the other. **There was no conflicting data and no redundant system.**

What remained of the academic RAG was one capability, and it overlapped with nothing: *policy
prose*. "Late homework loses 10% per day." "The midterm is 25% of the grade." "This section
covers chapters 1 through 6." A calendar feed carries titles and dates and none of that.

So this change does not simplify a duplicated path. It removes the only path that could answer a
question about how a course is run, and the honest description of the trade is:

    lost:    every question about a course policy, format, or grading
    gained:  ~250 lines, one Chroma collection, one torch import off the ACADEMIC turn

LB was told this, and confirmed it by asking again in the same message. It is his product and
his call — the ACADEMIC route is a schedule manager now, and it says so.

### What "he cannot answer that" has to be taught explicitly

The dangerous part of removing a retrieval path is not the deletion. It is that **a model asked
about a late policy with no context will answer from what universities usually do**, fluently,
and that is D11's fabrication arriving through a door D11 does not cover — the old prompt
guarded against inventing a *date*, because dates were the thing it had context for.

So the new prompt carries a section that did not exist before:

    WHAT YOU CANNOT ANSWER:
    You do not have his syllabi... You have titles and dates and nothing else.
    ...Do NOT describe what such a policy usually says. A plausible late penalty is worse than
    admitting you do not have it, because it stops him checking the real one.

`tools/verify_academic.py` section 2 pins both halves. A prompt is the only enforcement there
is, so the check is that the sentence is present, not that the model obeys it.

### The exclusion outlived the collection, and now matters more

`tools/vector_db.py` had two collections precisely because a semantic search cannot tell a course
outline from a datasheet: one pool would let a syllabus ground a firmware answer and be cited as
one. The `academic` collection is deleted. **`data/academic/` is still excluded from the walk.**

That is the load-bearing half of what survived. While there were two pools, a leak had somewhere
legitimate to land; there is one pool now and it is the one the FIRMWARE agent retrieves from. So
the exclusion went from a tidiness measure to the only barrier, and it is asserted in two
harnesses rather than one.

`data/academic/` also still exists, and had to: `academic_calendar.json` lives in it.

### The upload category was kept, and that is not inconsistency

`process_inbox_file(category="academic")` still works. It moves the file and requests **no**
rebuild, and the sentence it returns says he cannot read it.

Deleting the category would have been the tidier-looking choice and is wrong: an uploaded
syllabus would then have nowhere to go, so it would be filed as a `datasheet` — landing in the
one pool the firmware agent retrieves from, and grounding a register-level answer in a course
outline. **A destination that stores and admits it cannot read beats no destination at all.**

The PDF-only rule on that path was dropped at the same time, because it was there so a `.txt`
would not be filed somewhere only a PDF loader looks. Nothing looks now, so refusing a file for
that reason would be refusing it for a reason that no longer exists.

### What was actually deleted

    agents/academic_agent.py   retrieval, the Sources card, NO_SYLLABI, the syllabus prompt half
    tools/vector_db.py         ACADEMIC_COLLECTION and its build; the exclusion KEPT
    tools/academic_calendar.py extract_deadlines_from_syllabi, _documents_by_source,
                               EXTRACTION_PROMPT, Deadline, SyllabusExtraction, and with them
                               the pydantic and typing imports and this module's only use of
                               pypdf. It is a reader now, and its CLI prints instead of building.

`pypdf` reaches `data/` through exactly one caller: `vector_db.load_pdfs`, for the firmware
agent's datasheets. That was directive 3's requirement and it is now structurally true rather
than merely observed.

**Untouched, and checked rather than assumed:** `agents/firmware_agent.py`,
`agents/hardware_agent.py`, `tools/kicad_parser.py`, `engine/server.py` and the paperclip in
`hud/face-preview.html` have a zero-line diff.

### The rule

**When the reason for a change has expired, say so before making it.** The request was specific,
technically detailed, and rested on a redundancy that a previous commit had already removed —
which is easy to happen when the work is moving faster than the person directing it can re-read
it. The useful thing was not to refuse, and not to quietly comply either: it was to name what
would actually be lost, in one sentence, and then do the whole job properly.

## D24 — Putting the syllabus back, as a note instead of an index

**2026-08-23.** D23 removed the academic RAG and with it every answer about a course policy.
This restores those answers at a fraction of the cost, and LB's framing is the design:
*"dropping it into the Markdown Vault takes 5 seconds, avoids RAG overhead, and keeps the system
fast and lightweight."*

    the RAG    a Chroma collection, torch on the answer path, chunk retrieval at question time,
               re-embedding on every upload, and a syllabus that could ground a firmware answer
    this       one API call, once, producing a Markdown file that `grep` can find

`tools/syllabus_to_vault.py` reads a PDF with `pypdf`, sends the text to Gemini with a
structured-output schema, and writes `vault/courses/<CODE>.md`. After that it is free forever:
`read_from_vault` is already bound to HARDWARE, FIRMWARE and GENERAL.

### The failure this module exists to prevent

**An extraction is a model writing facts into long-term memory.** Everything else in the vault
was dictated by LB. This is the first writer that is a language model, and once a note is in
`vault/courses/` the other agents read it back as though he had written it himself.

Two guards, neither optional:

**A textless PDF never reaches the model.** LB's Pi camera PDFs loaded as perfectly good page
objects with **zero extractable characters** (D12), which is the normal state of a photographed
syllabus. A model handed an empty document does not report an empty document — it writes a
complete, plausible, entirely invented course policy. The guard runs *before* the network call,
so a folder of scans costs nothing, and the refusal says `no API call was made` because a silent
skip is indistinguishable from success.

**Absence is recorded as absence.** Every field may be an empty string, the prompt insists on it,
and the renderer prints *not stated in the syllabus* rather than dropping the heading. Dropping
it would make "this syllabus has no late policy" and "nobody has run the converter" identical in
a grep, and those call for opposite actions. The note is stamped with its source file and date,
so a reader can always tell a machine wrote it.

Due dates are **excluded on purpose**. They come from Canvas (D22), and a date copied out of a
PDF goes stale and then contradicts the feed. The fixture deliberately contains "Homework 4 is
due on October 14, 2026" and no date reaches the note.

### `replace`, and why the model cannot reach it

`save_to_vault` appends and never overwrites, correctly: the model does not know what is already
in a note, so "save the pinout" arriving twice must not lose the first one.

That is exactly wrong for a **derived** artifact. A corrected syllabus re-uploaded would stack a
stale late policy above the current one in one file, and `read_from_vault` would return both with
nothing to say which is current — the conflicting-data failure D22 and D23 removed from the
calendar, rebuilt inside the vault.

So `write_note(..., replace=True)` is a Python argument on a module function, and the `@tool`
wrapper never passes it. **A build step may rebuild its own artifact; a model may not silently
erase a note LB dictated.**

### Verified against a real syllabus, not a fixture

`Morgan Syllabus POSC 201 Fall 2026`, 13 pages, already on the Pi. It produced instructor with
office and email, office hours verbatim, a five-line grading breakdown, the complete late policy
(*"Late assignments will NOT be accepted and will receive a grade of zero"*), and five standing
rules including the Respondus lockdown requirement and the plagiarism policy.

Nothing invented, no dates carried across, and the note is named `POSC201` — exactly the course
code the Canvas feed uses, so the two halves line up without either knowing about the other.

### The bug only running it could find

`read_from_vault` is a **substring** scan against filename or body, with no tokenising and no
stemming. That is the right trade for dozens of notes and no index, and the consequence is that
a note is findable only by words it literally contains. The first real note had a heading called
"Late and missed work":

    read_from_vault("office hours")  -> found
    read_from_vault("late policy")   -> NOT FOUND

The likeliest question of all, missing. Fixed in the **note**, not the searcher: tokenising a
tool three agents already depend on would make every two-word query match far more than it
should. Every note now carries a `*Search terms:*` line, and all six phrasings are verified
against the real searcher.

### And L11, walked into twice in the same file

The harness check "converts named files, never `convert_all`" was first written as
`"convert_all" not in source` — which matched the docstring sentence *explaining* that
`convert_all` is not used. That is [[L11]] verbatim, a lesson already in this repo.

The second attempt stripped lines starting with `#` or a quote, and still failed, because a
docstring's *middle* lines start with neither. It works now because it walks the **AST** and
collects `ast.Call` names, which is the only version that is actually about code.

### The rule

**When you delete a capability, the cheapest replacement is usually not the one you deleted.**
The academic RAG answered policy questions by embedding hundreds of chunks and retrieving four
of them per question, forever. The same questions are answered by one extraction into one file
that `grep` finds — because a syllabus is a handful of static facts, not a corpus. The RAG was
the right tool for datasheets and the wrong one here, and that only became visible after it was
removed for an unrelated reason.

## D25 — The notes existed for four hours before the route that needed them could read them

**2026-08-23.** D24 put LB's syllabi into `vault/courses/` as plain Markdown. It flagged, as an
open item, that the ACADEMIC route could not reach them — and this closes it. LB: *"the Markdown
Vault isn't a bloated vector DB — it's just clean text files."*

`read_from_vault` is now bound to ACADEMIC alongside `sync_canvas_calendar`.

### The gap was invisible from every direction except use

Every check was green. The notes were correct. The vault search worked. HARDWARE, FIRMWARE and
GENERAL could all read them. And a policy question still failed, because **routing decides which
agent answers, and coursework questions route to ACADEMIC** — the one agent D23 had deliberately
left with no vault tool.

    "what's the late policy"          -> GENERAL  -> reads the vault -> correct
    "what's the POSC 201 late policy" -> ACADEMIC -> no vault tool   -> "I don't know"

Same question, same note on disk, opposite answers, decided by whether the sentence sounded like
coursework. That is the shape of a bug no unit-level check finds: every part worked.

### Two tools, and the division between them is absolute

    sync_canvas_calendar   WRITES. Deadlines, from the live feed.
    read_from_vault        READS.  Everything else, from vault/courses/*.md.

**Canvas owns every date; the notes own everything else.** The prompt says so twice and forbids
the crossing explicitly — *"Never take a date out of a note"* — because a note is made from a
syllabus PDF, and a date in one is a snapshot the feed may already have moved past. That is D22's
whole argument, and this is the door it could have come back through.

### The failure mode moved, and the prompt had to move with it

Under D23 the danger was **inventing** a policy: the agent had no source, and a model with no
source answers from what universities usually do. The prompt was written against that, and said
plainly that he could not answer such questions at all.

That sentence is now false, and leaving it would have been worse than useless — it would have
taught him to refuse while holding the answer. But the naive replacement has its own failure: the
notes are **behind a tool call**, not in the prompt, so a model that is still told "answer only
from what you have" will refuse *without searching*. To LB those are identical.

So the contract grew a third rule, and it is the one this route did not need until today:

    3. LOOK before saying you do not know. ... An agent that skips the search and refuses is
       indistinguishable, to LB, from one that has no notes at all.

Refusing is cheap and looks like obedience to rule 1, which is exactly why it needed saying.

### Verified live on the Pi, both directions

    "what is the late policy for POSC 201?"
      -> searched the vault, found POSC201.md, and returned all five clauses of the real policy,
         including "will not accept emailed assignments"

    "what is the late policy for ECE 350?"          (no note exists)
      -> "I have no notes on that course yet. You can upload the syllabus with the paperclip."

The second is the one that matters. It looked, found nothing, and said so — rather than
describing what a course usually does, which is the failure every version of this prompt since
D11 has been written to prevent.

### And one harness lesson, again

A check read `"Do NOT describe what such a policy usually says" in prompt` and failed, because
the prompt is wrapped at 100 columns and the phrase straddles a line break. The property being
tested has nothing to do with where the author wrapped, so the check flattens whitespace first.
Sibling of [[L11]]: a string check against prose is testing the prose's formatting.

### The rule

**A capability is not delivered until the path a user actually takes reaches it.** The note was
written, correct, findable and reachable by three agents — and the question LB would really ask
went to the fourth. Ask "who answers this when he phrases it the normal way?", and check that
one, before calling the feature done.

---

## D26 — Four new gestures, and two of them were already approving shell commands

**2026-08-23.** LB asked for the `jaredrhod/barehands` vocabulary — `PINCH`, `CLAP`, `CLAW`,
`FLICK` — on top of the `THUMBS_UP` and `OPEN_PALM` that `tools/gesture_control.py` already
had, plus a live camera window to tune them in, because recognition was unreliable and there
was no way to see what MediaPipe was actually looking at.

Both were built. The interesting part is what adding the gestures uncovered in the old ones.

### The claw was already a thumbs up

`THUMBS_UP` is what `agents/os_agent.py` runs a shell command on, so its test is deliberately
stricter than the obvious one — D-era note above: it requires the thumb high **and all four
fingers not extended**, where extended means `tip.y < pip.y`. That extra clause is what stops
an open palm being an approval.

It does not stop a claw. A claw holds every fingertip below its PIP joint, so **no finger is
extended, so the curl clause is satisfied** — and a claw's thumb sits above the index knuckle
like everyone else's. A pinch does the same thing: the index curls down to meet the thumb, so
it is not extended either.

```
$ python tools/verify_gestures.py --probe
   CLAW      old order -> THUMBS_UP    new order -> CLAW
   PINCH     old order -> THUMBS_UP    new order -> PINCH
```

That was latent from 2026-08-19 and harmless while nobody made claws at the camera on purpose.
The request to add the claw as a gesture LB performs deliberately is what turned it into a
routine false approval, in the same commit that would have added it.

### Fixed by ordering, not by tightening

`PINCH` and `CLAW` are now tested **before** `THUMBS_UP`, and the discriminator is tip-vs-**MCP**
rather than a new threshold:

    thumbs up   tip.y > mcp.y    fingertips BELOW the knuckles — folded into the palm
    claw        tip.y < mcp.y    fingertips ABOVE the knuckles — strained, half-open

Those cannot both hold, so the two are mutually exclusive **by construction** — the same
property `OPEN_PALM` has had since D-era, and for the same reason. A threshold would have been
a number to argue about; this is a geometric fact about a fist.

**The thumbs-up test itself was not touched.** Not one constant in it moved. LB has reported
missed thumbs-ups before, and `WARMUP_FRAMES` above is still carrying an unmeasured guess from
that; tightening approval geometry in the same commit as four new gestures would have made the
next missed approval impossible to attribute to either. The new gestures are guards placed in
front of the gate, not edits to it. Net effect: strictly fewer false approvals, identical true
ones, and `verify_gate_state.py` still 20/20.

### Ratios, not distances

The obvious pinch test is "distance between landmarks 4 and 8 below a constant". That constant
is correct at exactly one camera distance — landmarks are normalised to the frame, so the same
hand at 80 cm is half the size it is at 40 cm, and a threshold tuned close up reads every
relaxed hand as a pinch across the room.

Every distance is now divided by `_hand_scale()`, the wrist-to-middle-knuckle span — the one
length on a hand that does not change when the fingers move. Thresholds are in palm-lengths and
hold at any distance and on any size of hand.

### FLICK cannot exist on the approval path, and that is structural

`get_gesture()` reads one frame from a camera it then closes, inside a child process that then
exits (D15's crash isolation). Motion needs two frames and something that remembers the first;
that architecture has neither. So `FLICK` is produced only by `classify_stream()`, the
continuous path, and `tools/live_test_gestures.py` is currently its only caller. Said out loud
in the module header rather than left as a gesture that mysteriously never fires.

It is edge-triggered: firing clears the history, so one swipe of the hand is one flick rather
than a flick on every frame for as long as the hand is moving.

### `CLAP` does not test what its name says

"Palms facing each other" means recovering two palm normals from 21 landmarks each and
comparing them — and the wrist/index/pinky triangle those normals come from is nearly
degenerate exactly when the palms face each other edge-on to the lens, which is the only pose
it would ever be measured in. At 640×480 and ~6.6 fps that is a calculation that looks rigorous
and fires at random.

So the test is the observable part: **two hands, both upright and open, close together**, with
the gap measured in palm-lengths. The docstring states the gap plainly instead of implying
precision that is not there. Two open palms held side by side also read as `CLAP`; nothing in
the security gate is reachable from `CLAP`, so the cost is a wrong app command, not a wrong
approval.

### The test this file claimed already existed

The 2026-08-19 entry above says the classifier "is tested with no camera at all — six cases,
including the one above, all green". **There was no such test in the repo.** It was true when
written and never committed, which is the worst case: a reviewer reads the sentence and stops
looking, which is plausibly why the claw collision survived four days.

`tools/verify_gestures.py` now exists — 31 checks, pure geometry, no camera and no mediapipe
import. Its `--probe` restores the old branch order and expects section 3 to go red, because a
regression test for an ordering bug that still passes when the ordering is wrong is not a test.
Sibling of the harness lessons in D25 and [[L11]].

### The rule

**When a new value is added to an enum that a security check compares against, the check is
not "does the new value equal the old one" — it is "can the new value REACH the old one".**
The claw was never going to be confused with a thumbs up by a human. It was confused with one
by a boolean that had been correct for two gestures and was never re-read for six.

---

## D27 — The router call was worth removing; the keyword list that would have removed it was not

**2026-08-23.** The ask was a zero-token local router: an extensive keyword dictionary in
`router.py`, checked before the Gemini Flash structured call, returning a route instantly when
anything matched. Two dictionaries were specified — one for ACADEMIC / FIRMWARE / HARDWARE /
VAULT, and a second for launching desktop applications.

The cost being attacked is real and this repo has measured it twice. `router_agent()` is a
network round trip on every paid turn — **750 ms on Windows, 9.8 s on the Pi** — and against
D3's free tier of 20 requests per model per day it is the call every turn pays before it pays
for anything else. D1 named that price when it chose semantic routing, and said Stage 8 would
measure it. It did.

So the goal was right. Most of the specified implementation was already built, and the rest of
it would have broken things.

### Half of it shipped two days ago

The second dictionary — `open`, `launch`, `start`, `run application`, plus KiCad, Firefox,
Chromium, gedit, mousepad, VS Code and the terminal — is **D10**, landed 2026-08-21.
`orchestrator/launch_intent.py` already recognises every one of those verbs and resolves the
target against the Pi's XDG desktop database rather than a hardcoded app list, which is why
`nautilus` works and why a hand-written table was refused there too. "Open KiCad" has cost zero
tokens since that day, and `tools/verify_launch.py` mutation-tests the rule that keeps it safe.

Rebuilding it inside `router.py` would have been strictly worse than not rebuilding it. The
free tier runs *before* the router, so a second copy would either be dead code that never sees
a launch, or — reached first — a launch path that skips `Engine._gate` and starts programs
without asking. The bare-keyword form was the dangerous one: `open` as a keyword claims *"how
do I open a file in Python"*, which is check number one in `verify_launch.py`'s negative set.

### The other half collided with routes it did not know about

Checked against the actual enum — ten routes, `FIRMWARE HARDWARE MATH OS QUIZ WEB PERSONA
UTILITY ACADEMIC GENERAL`:

| the rule | what it also matches |
|---|---|
| `[a-z]{3,4}` + digits → ACADEMIC | **`esp32`**, `stm32`, `msp430`, `pic16` — the FIRMWARE keywords in the same proposal |
| `test`, `quiz`, `exam` → ACADEMIC | `AgentRoute.QUIZ`, which ROUTER_PROMPT separates by name: *"test me on this" is QUIZ even in an academic context* |
| `current` → HARDWARE | "what's the **current** time" |
| `ohms law`, `voltage` → HARDWARE | both are `formula` triggers — **free today**, and a keyword rule would make them paid |
| `raspberry pi` → FIRMWARE | "reboot the pi", "what's the Pi's CPU temp" are OS |
| `vault`, `remember`, `save` → VAULT | **there is no VAULT route.** The vault is a *tool*, bound into HARDWARE, FIRMWARE, ACADEMIC and PERSONA |

That last row is the one worth keeping. "Remember I used a 10k resistor on the amp board" has
no destination a route table can name, because the thing that should happen is `save_to_vault`
*inside* the agent that can also read the KiCad file. Routing it anywhere strips the tools off
it. A keyword pointing at a route that does not exist is not a near miss — it is a category
error about what the vault is.

And the `esp32` row is the shape of the whole problem: two rules in one document, each
individually reasonable, that claim the same string and disagree.

### What was built instead

The band between "needs no agent" and "needs judgement": turns that DO need an agent, where
naming it is not a judgement call. `orchestrator/route_hint.py`, a pure function of a string,
called from `_routed_turn` between `_free_turn` and the quota latch.

- **ACADEMIC** — Canvas sync phrases, course-paperwork phrases (`syllabus`, `late policy`,
  `office hours`, `grading policy`), deadline phrases, and course codes.
- **OS** — machine stats as phrases: `cpu temp`, `how much ram`, `disk space`, `uptime`.
- **Everything ambiguous stays on Gemini.** `voltage`, `current`, `led`, `resistor`, `i2c`,
  `pinout`, `datasheet`, `test`, `quiz`, `save`, `remember` are absent on purpose.

Three things carry the safety, and all three are borrowed from rules this repo already had:

**Every trigger is a phrase, never a bare keyword.** The same rule `instant.py` arrived at
three separate times — after "time constant" announced the clock, after a bare "today" hijacked
a baseball question, and after "goodbye sale" ended a conversation.

**The course codes are read from `vault/courses/*.md`,** the ACADEMIC agent's own notes, the
way `app_catalogue` reads apps from XDG instead of a curated table. This is what kills the
`esp32` collision *structurally* rather than by adding an exception: `esp32` is not a file in
that directory. On Windows the directory does not exist, the rule switches itself off, and the
turn falls through to the paid router exactly as before.

**An upload is refused outright, before anything else is checked.** ROUTER_PROMPT is
unambiguous that a new upload is always GENERAL, because GENERAL is the only route that can
file a document. *"I just uploaded ECE350_syllabus.pdf"* names a syllabus **and** a course code
and would otherwise be the single strongest ACADEMIC match in the file — and filing it is the
one thing ACADEMIC cannot do.

### The greetings were the cheaper win, and they needed the same rule

`instant.py` has always held canned answers for `hello`, `thanks` and `identity` — "Hey LB.",
"Any time." — and every one of them still bought a router call to reach a PERSONA agent that
would improvise a different answer. D3's first listed remedy is *widen UTILITY; every question
it absorbs is a free question*, and a greeting is the purest case there is.

They could not be promoted as written. `hello` fired on a bare "hey", so *"hey what's the trace
width for 5 amps"* was answered **"Hey LB."**. Behind the router that wasted a classification.
In front of it, it deletes HARDWARE from the answer path. `identity` was `_has(text, "who",
"you")` — both words anywhere — which claims *"who would you recommend for a resistor
supplier"*.

So they took the end-anchor rule: the greeting has to BE the utterance. That algorithm already
existed in this file **three times over** — `_is_dismissal`, `is_wake`, and now these — so it
was extracted to `instant._is_bare` and the other two now call it. Three copies of the one rule
that holds D38 back is three places for it to drift.

### What it costs and what it saves

| turn | before | after |
|---|---|---|
| "hello" / "thanks" / "who are you" | 1 router + 1 persona | **0** |
| "sync canvas" | 1 router + academic | academic only |
| "what's the CPU temp" | 1 router + 2 OS calls | 2 OS calls |
| "what's the trace width for 5 amps" | 1 router + agent | unchanged, deliberately |

**The ACADEMIC and OS hints save one call of two or three, not the whole turn.** The agent
still runs. Saying otherwise would overstate it — only the social three are actually free, and
they are free because the answer was already sitting in a table.

### The harness

`tools/verify_router.py` — 164 checks, keyless, no network. It closes the Stage 8 item that had
been open since the merge, and the half of that item's sentence that mattered turned out to be
*"PERSONA/UTILITY don't swallow EE questions"*.

The negative corpus is longer than the positive one, because the failure modes are not
symmetric: a hint that misses costs one router call, which is what every turn cost yesterday,
while a hint that matches too much removes an agent from the answer path and looks exactly like
a correct answer from the outside.

Two of its four mutations are not bugs that shipped — they are **the designs specified in this
entry, reinstated**:

```
M1  upload guard removed       -> 6 red   (an uploaded syllabus is never filed)
M2  social end-anchor removed  -> 7 red   ("hey whats the trace width" answers Hey LB.)
M3  bare-keyword dictionary    -> 21 red  ("the current time" is HARDWARE, "quiz me" is ACADEMIC)
M4  naive course-code regex    -> 9 red   (esp32 and stm32 route to ACADEMIC)
```

M3 is the one that matters. Without it this entry is an opinion about a keyword list; with it,
the keyword list is a thing that turns twenty-one checks red on demand.

### Three of the named apps did not actually work, and finding that took a fixture

"The launcher already does this" was true in outline and wrong in three places. Handing
`launch_intent` a Pi-shaped `.desktop` tree on Windows — six entries, built in a
`TemporaryDirectory` — showed **`schematic editor`**, **`pcb editor`** and **`vscode`** all
falling through to the paid router, while `kicad`, `firefox`, `terminal` and `fire fox` worked.
The claim had never been tested against a catalogue that contained multi-word app names.

Two different bugs, and neither was in the end-anchor rule:

**`_targets` only ever offered whole names.** `app_catalogue.resolve()` has had a fourth tier
since D10 — *a whole-word phrase in the Name* — which answers "schematic editor" against
"KiCad Schematic Editor" perfectly well. But `launch_intent._targets` builds the candidate
phrases, and it built them from full names, entry ids and `ROLES` only. **The catalogue knew
the answer; the function that decides what counts as naming an app never asked the question.**
Trailing sub-phrases of a multi-word Name are now targets, two words minimum — a single
trailing word is "editor", "manager", "player", which is exactly what `ROLES` owns and
deliberately maps to a *category* rather than to whichever app happens to end in it.

**`vscode` is a nickname, and nothing in four tiers can reach one.** It is not the Name, not
the entry id, not a role, not a phrase inside the Name. `tools/app_catalogue.ALIASES` now maps
it (and "vs code") to "visual studio code" before the tiers run.

That last one is a curated table, in a module whose docstring refuses curated tables, so the
distinction has to be exact: **the refused table listed which apps exist** and went stale the
moment one was installed — it shipped without `nautilus`. This one maps what a person *says* to
a phrase the catalogue resolves on its own. With VS Code uninstalled, "vscode" resolves to
nothing, precisely as "visual studio code" does, and `verify_launch.py` asserts that. `ROLES`
has been the same shape since D10. It is also not edit-distance matching, which stays refused
for the reason both files state: a threshold loose enough to catch "tawny" → "thonny" is loose
enough to catch "shut up" → "shut down". These are exact keys and nothing is guessed.

`tools/verify_launch.py` is 208 checks now, up from 194, and its five mutations still bite.

### The rule

**A local fast path is worth building exactly as wide as the set of inputs with one right
answer — and not one keyword wider.** The router was bought to resolve ambiguity, so every
ambiguous string handed back to a phrase list spends its whole value to save its cost. D38 for
the seventh time, in its most expensive form yet: the danger is never the rule that fails to
match, it is the one that matches too much.

Second, smaller: **before optimising a path, read it.** Half of what was asked for here had
shipped two days earlier, and the two dictionaries in one request contradicted each other on
`esp32` before either reached the code.

Third, and it is the one that nearly got away: **"that already works" is a claim, and a claim
needs a fixture.** The launcher did already handle the verbs. It did not handle three of the
apps that were named, and no amount of reading the code was going to show that — the failure
lived in the gap between two functions that each looked right. It took eleven lines of
`.desktop` files in a temp directory. Same lesson as D26's missing gesture test, arriving from
the opposite direction: there the test was claimed and absent, here the behaviour was claimed
and untested.
