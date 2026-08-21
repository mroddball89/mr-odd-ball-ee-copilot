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
