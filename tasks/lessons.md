# Lessons

Patterns worth not repeating, written as rules. Each one comes from a real failure in this
repo, and names it — a rule with no incident behind it is a preference.

---

## L1 — A guard is only correct at the altitude it was written for

**From:** D8, 2026-08-21. `_NEEDS_A_NUMBER` demanded a digit before a one-letter unit, which
is right about a whole utterance (`a mile` is not amperes) and wrong about the fragment
`_find_unit` is actually handed (`convert 5 a to ma` splits to `src="a"`, digit already eaten
by the frame regex). Every symbol conversion an engineer types was refused.

**Why:** the guard and the caller drifted apart silently. Nothing failed loudly, because the
failure mode was a *refusal*, and a refusal looks like the module working as designed.

**How to apply:** when a helper's rule depends on surrounding context, check what the callers
actually pass it — not what the module docstring says arrives. If the caller consumes part of
the input before calling, the guard has lost the evidence it was written to read.

---

## L2 — A default that fires on "missing" must not fire on "misparsed"

**From:** D8, defect 2. `amount` defaulted to 1.0 so `how many meters in a mile` works. It also
fired for `how many milliamps in point 5 amps` — where the 5 was still sitting in the fragment,
unread — and he confidently said "1 amp is 1000 milliamps".

**Why:** "no value was supplied" and "a value was supplied and I failed to read it" are
different states, and collapsing them turns a parse failure into a wrong answer. Wrong answers
do not escalate; refusals do.

**How to apply:** before defaulting, assert the input is actually empty of the thing being
defaulted. Here that became an invariant worth more than the special case: **the parse must
account for every digit**, and a leftover digit means refuse.

---

## L3 — Leaving a compound unit out of the table does not make it refuse

**From:** D8, defect 3. `amp hour` was not in the unit table, so `how many amps in 3000
milliamp hours` matched the bare `milliamp`, dropped the `hours`, and answered "3 amps" —
current, from a charge.

**Why:** a partial match is not a non-match. The table's longest-first matcher will always find
*something*, and the dimensional check can only protect what it can see.

**How to apply:** when a unit has a common compound form (Ah, Wh, mAh), it belongs in the table
in its own category, or the base unit inside it will be matched alone and answered in the wrong
dimension.

---

## L4 — Green is not the same as right; a check can pin the bug

**From:** D8. `verify_convert.py` asserted `'m' does not parse an English word as a unit`. That
check was green throughout, and it was the bug — written against the old altitude (L1), it
locked the refusal in place and would have failed any correct fix.

**Why:** harnesses are code, and a check written from the same misunderstanding as the
implementation agrees with it perfectly.

**How to apply:** when a fix turns a check red, read the check before weakening it — but do not
assume it is right either. Ask what property it was *trying* to state, then write that property
in both directions. Here it split into: multi-word fragments must not yield a unit, bare
fragments must.

---

## L5 — Measure the "before" by running it, not by remembering it

**From:** D8. The before column of `media/data/2026-08-21-ampere-conversion.csv` is a live run
of `orchestrator/convert.py` pulled out of git at `ff3a43b` and loaded beside the fixed one in
the same process.

**Why:** LB's media convention says a fix with no "before" is an anecdote — and a "before"
transcribed from memory into a CSV is an anecdote in a spreadsheet. It also cannot be
re-derived later, which is the whole point of committing the script.

**How to apply:** `git show <commit>:<path>` into a temp module and run both. Register the
module in `sys.modules` before executing it, or frozen dataclasses fail to resolve their own
annotations.

---

## L6 — A bare `except` around an unverified API reports "your data is broken"

**From:** D9, 2026-08-21. The KiCad tool as first written looped over `schematic.symbols`.
kiutils has no such attribute — it is `schematicSymbols` — and the body sat inside
`except Exception as e: return f"Failed to parse schematic: {e}"`. So every schematic came back
as *"Failed to parse schematic: 'Schematic' object has no attribute 'symbols'"*: a message that
names the user's file, blames the user's file, and is entirely about our typo. Measured at 100%
of schematic questions before it was found.

**Why:** the `try/except` was written to convert *the library's* failures into a sentence, and
it converted *our own* failures into the same sentence. Those need to read differently, because
one of them means "go and look at your file" and the other means "go and look at this code".
Catching them together makes the wrong one the visible one, and the message is confident about
a thing it has not checked.

**How to apply:** before writing the `except`, run the API once and print what you are about to
reach for — `[f.name for f in dataclasses.fields(X)]` takes ten seconds. Then pin the attribute
in a harness so a rename fails there and not in front of LB. `verify_kicad.py` asserts both that
`schematicSymbols` exists and that `symbols` does not, so if kiutils ever adds it, the docstring
gets revisited rather than quietly becoming wrong.

Related: this is [[L4]] pointed at a library instead of a check. A green harness and a fluent
error message are both forms of something agreeing with you when nobody has looked.

---

## L7 — Ask what the number means before reporting a count

**From:** D9. `len(board.layers)` is 29 for a two-layer board, and `len(board.nets)` is one more
than the number of nets, because KiCad's table holds silkscreen, mask, paste, courtyard, fab and
nine user layers, and its net list starts with the unassigned net 0.

**Why:** both are correct counts of the wrong thing, and both produce a fluent sentence nobody
would question. "This is a 29-layer board" is the same failure as D30's confident wrong physics
and D8's confident wrong conversion — a number, not a crash.

**How to apply:** when reporting `len(something)` from a file format, say out loud what a person
asking the question means by it, then check the collection contains only that. If the raw count
is still worth showing, show it **labelled and beside** the real answer, never instead of it.

---

## L8 — A measurement that kills its subject will eventually corrupt its subject

**From:** D10, 2026-08-21. `measure_launch.py` ran fifteen Firefox trials, stopping the browser
between each one. Firefox increments `toolkit.startup.recent_crashes` in `prefs.js` every time
it is stopped before startup completes; past `toolkit.startup.max_resumed_crashes` (default 3)
it refuses to run. The counter reached **17**.

The refusal is the dangerous part: Firefox starts, writes its prefs, and shuts down **cleanly**.
Exit 0, no stderr, no crash report, nothing in the journal, `Result=success` in systemd. It is
**indistinguishable from the bug the measurement existed to prove was fixed** — and it silently
invalidated a whole run, which was then read as "the cgroup fix does not work".

**Why:** the harness was a hidden input to the thing it measured. Every trial made the next
trial's subject slightly less healthy, so the results degraded over the run in the direction of
the hypothesis. That is the shape of a false positive that survives review, because each
individual number looks fine.

**How to apply:** if a harness stops the thing it measures, ask what that thing records about
being stopped, and reset it between runs. `kill_firefox()` now sends SIGTERM and waits 20s
before SIGKILL, and `reset_crash_counter()` runs before *and* after the suite. More generally:
**when a measurement contradicts a hand-run of the same test, suspect the measurement first** —
it has more moving parts and it is the thing that was written most recently.

---

## L9 — `pkill -f` matches the command line that invoked it

**From:** D10. `ssh oddball-pi 'pkill -f /usr/lib/firefox'` killed the ssh session twice, because
`-f` matches the **whole command line** and the remote shell's command line contains the pattern
being searched for. It reads as a network fault — exit 255, connection closed — so the first one
was written off as the Pi being flaky under load.

**Why:** the pattern is data to `pkill` and text to the shell, and the shell got there first.
This is already recorded in the pre-merge `~/oddball/tasks/lessons.md`; it was not carried
across in the merge, so it was learned twice.

**How to apply:** `pkill -x <name>` matches the process name exactly and cannot match its own
invocation. Reach for `-f` only when the name genuinely is not enough, and then anchor it so it
cannot match a shell — or better, stop the systemd unit, which names exactly one thing.

### The bracket trick does NOT fix this, and believing it does is how it bit a third time

**2026-08-23.** Learned again, deploying the upload pipeline. The remedy recorded below — write
`pkill -f '[f]loat.py'` — was applied *correctly* and the ssh session died anyway:

```bash
ssh oddball-pi 'pkill -f "[f]loat.py"; setsid nohup python3 hud/float.py ... &'
#                       ^ does not match          ^ THIS DOES
```

`[f]loat.py` is a regex that matches the text `float.py`. It protects the pattern *itself* from
matching — and does nothing about **the rest of the command line**, which in a restart contains
the plain name because it is relaunching the thing. Kill and restart in one command is exactly
the case the trick cannot cover, and it is the common case.

**Kill by PID.** `pgrep -f` first, look at what came back, then `kill <pid>`. Two steps, and the
first one is a read. The `[f]` form is only safe when the command does nothing else.

Cost: the assistant service was untouched (0 restarts, both ports still bound) but the face
window went down and had to be brought back by hand, and the Wayland environment had to be
reconstructed from constants because the process whose `/proc/<pid>/environ` held it was gone.
Read that environment out **before** killing anything that has it.

---

## L10 — "It isn't there" is only evidence once the path is confirmed

**From:** D10. `ls ~/.mozilla` returned "No such file or directory", and that was read as
"Firefox has no profile" — a conclusion that sent the investigation toward profile creation and
permissions for a long time. This Firefox build uses XDG paths: the profile was in
`~/.config/mozilla/firefox`, present and healthy, the whole time.

**Why:** a negative result from the wrong location is indistinguishable from a negative result
from the right one, and it is more convincing than a positive one because nothing contradicts
it. The absence was real; the inference was not.

**How to apply:** before concluding something does not exist, confirm the place you looked is
the place it would be — `find`, the package's own docs, or ask the program (`strace`, a `--help`
that names its config path). Related to [[L7]]: both are correct observations of the wrong
thing, and neither crashes.

---

## L11 — A substring check over a whole file matches the comment explaining it

**From:** D11. A new harness check asserted that every package in `requirements.txt` appears in
`stage_install.sh`, by searching the script's full text. Mutation-tested by deleting `sympy`
from its install stage: **the check stayed green.** The script's own header comment explains
*why sympy matters*, so `"sympy" in text` was true with the install line gone.

**Why:** documentation about a thing contains the name of the thing. Any substring search over
a file that includes prose is really searching two languages at once — the code and the
commentary about the code — and the commentary is written precisely where the code is most
load-bearing. The check was strictly worse than nothing: it occupied the slot a real check
would have had, and reported green.

**How to apply:** parse the lines that *do* something (`startswith("run ")`) rather than the
file that describes them. And mutation-test every new check — break the thing it watches and
confirm it goes red. This one was written, run, and passed before the mutation test showed it
could not fail. Same family as [[L4]]: green is not the same as right.

---

## L12 — A loaded page is not text, and a non-empty list is not non-empty content

**From:** D12. `_build_collection` guarded `if not documents: return 0` and then handed the
split result straight to Chroma. Both of LB's Pi camera PDFs are image-only — no text layer — so
they load as two perfectly valid page objects whose `page_content` is `""`. Two documents, zero
chunks, and `Chroma.from_documents([])` dies with *"Expected Embeddings to be non-empty list or
numpy array, got []"*: a message about Chroma's internals for a problem entirely about the file.

**Why:** the guard was on the wrong quantity. "Did the loader return anything" and "is there
anything to embed" are different questions, and a PDF answers yes to the first and no to the
second without any error anywhere. Container non-emptiness is not content non-emptiness — the
same confusion as [[L5]] (`inBom` defaulting to False makes absence and exclusion one value).

The crash was the lucky outcome. One layer up, the same missing guard writes an **empty
collection**, which from the outside is indistinguishable from a working one: the retriever
returns zero chunks, the agent says the datasheets do not cover it, and that sentence is exactly
what it says when grounding is working fine. Related to [[L7]] and [[L10]] — a correct
observation of the wrong thing, and nothing crashes.

**How to apply:** guard the quantity you are about to *use*, not the one you were handed, at
every step that can shrink it. And when a step can legitimately produce nothing, say which input
produced nothing and name it — "2 pages carried no extractable text — pi_cam3.pdf" is
actionable; a traceback out of a vector store is not.

## A wheel-tag query answers a question about one release SERIES, not about a package

**2026-08-22 (D14).** I queried PyPI for `mediapipe` aarch64 wheels, saw `cp39`-`cp312`, and
wrote up "this cannot install on the Pi's Python 3.13" as a measured platform limit. It was
true of the `0.10.x` series and false of the package: `1.0.1` ships one
`py3-none-manylinux_2_28_aarch64` wheel that installs on any Python 3.

**Why:** LB read the write-up and asked for a Python 3.12 venv rebuild on the strength of it —
on a Debian trixie box with no `python3.12` package, no `uv` and no `pyenv`. The wrong finding
would have cost an interpreter build and a 1.9 G venv rebuild, to pin a November 2024 release
and inherit its old API forever.

**How to apply:** sort the releases, look at the NEWEST, and check whether the tag *shape*
changed. `py3-none` where `cp3xx` used to be is a packaging decision with consequences. A major
version bump is exactly when a maintainer changes ABI tags, drops APIs, or both — here it did
both, because 1.x also removed `mp.solutions`.

And the worse half: the wrong finding was written up *persuasively* — a table, a stated
provenance, an all-caps warning. Confidence and formatting made a partial check read as
settled, and it propagated straight into a work request. **Write up what was actually queried**
("the 0.10.x wheels"), not the generalisation it appears to support. A finding stated more
broadly than it was checked is the kind that gets acted on before anyone rechecks it.

## "It installs" is not "it runs" — verify by executing the path that uses it

**2026-08-22 (D15).** Hours after writing the lesson above, I made its sibling. I proved
`mediapipe` was fine on the Pi's Python 3.13 with `pip install --dry-run` **on the box**, told
LB in bold not to rebuild his venv on 3.12, and was wrong: it installs, imports, and then
SIGKILLs the process when the detector is constructed. `import mediapipe` succeeds. The call
one line later kills you.

**Why:** LB had asked for the 3.12 venv. I talked him out of it with a real measurement that
answered the wrong question — *will pip resolve this?* rather than *does the feature work?*
Twice in one day I took a true, narrow result and stated it one step wider than it was checked.

**How to apply:** a dependency is verified when **the code path that uses it has been
executed**, on the target box. Not resolved, not imported — run. For anything with a native
backend, the construction call is the test, and it belongs in the installer's `--check`, not in
a document. And when a crash cannot be caught — SIGKILL cannot — the fix is not a better
`try`/`except`, it is running the thing in a child process so its death is a returncode instead
of taking the caller with it.

The corollary about ordering: my first sidecar tried in-process and fell back on failure. On
the Pi the fallback was unreachable, because the process died constructing the thing it was
about to decide not to use. Fallbacks after an uncatchable failure are not fallbacks. Found by
running it.

## `git status` clean does not mean the deployed bytes are clean

**2026-08-22.** I rewrote two shell scripts with Python's `Path.write_text()` on Windows, which
turned them into CRLF. `git status` stayed clean, `git diff` was empty, and the tar-over-ssh
deploy carried the `\r` onto the Pi, where `install_autostart.sh` died with
`$'\r': command not found`.

**Why:** `.gitattributes` has `* text=auto eol=lf` and had a comment predicting this exact
failure. It still happened, because that protection lives on the **git** path and the deploy
does not use the git path — `tar` ships working-copy bytes while git shows normalised ones. A
CRLF working file hashes identically to its LF blob, so every git-shaped check says fine.

**How to apply:** when the deploy mechanism is not `git clone` or `git pull`, git's cleanliness
is not evidence about what ships. Check the actual bytes before deploying anything that a
kernel or a parser reads strictly — shell shebangs, `.desktop` entries, systemd units:

    for f in *.sh config/*.desktop config/*.service; do grep -qU $'\r' "$f" && echo "CRLF $f"; done

And prefer the Edit tool or `sed -i` over Python `write_text()` for files that run on Linux;
`write_text()` uses the platform's newline translation unless you pass `newline="\n"`.

## "It runs" is not "it works" — for anything visual, the artefact is the screenshot

**2026-08-22 (D16).** Third rung of the same ladder in one day. I signed the avatar off on
`/healthz` 200, `/ui` 200, `clients: 1`, and a live `sleeping -> thinking -> speaking ->
sleeping` on the state socket. Every one of those assertions was true. LB then sent a photo of
his screen: an **empty rectangle with a title bar on it**.

Two defects, neither of which emits any error: WebKitGTK's DMA-BUF renderer painted torn buffer
garbage instead of the page, and `frameless=True` was silently ignored because GTK3's Wayland
backend never negotiates xdg-decoration.

**Why:** the ladder goes `pip resolves` → `it imports` → `the code path runs` → **`a human can
use it`**, and I stopped one rung short three times running. D14 stopped at resolves, D15 at
imports, D16 at runs. Protocol assertions cannot see a title bar.

**How to apply:** if a change has a visual output, **take the screenshot before saying it
works** — `grim` on Wayland, and pull it back and actually look at it. Two techniques that paid
off here: `evaluate_js` against the *live* window to ask the page what it thinks it is drawing
(that is what proved the DOM was fine and the compositor was not), and a numeric control —
mean colour of the region that should contain the thing versus a region that should not —
because "I can sort of see it" is not a measurement and I got that wrong once in this session
by counting a different window's pixels bleeding through.

Related: `pkill -f launch_ui.py` over ssh kills its own command line, because the pattern
matches the remote `bash -c` string. Use `pkill -f '[l]aunch_ui.py'`. DEPLOY.md already
documented the bracket trick for `pgrep` and I did not apply it to `pkill`; it cost three
silent no-op deploys that looked like the window failing to start.

## Before adding a surface, find out what already renders that thing

**2026-08-22 (D17).** I built a floating avatar ball — a window, a FastAPI server, a state
broadcaster, two WebKit env vars, a labwc rule — next to `hud/face-preview.html`, which is
1565 lines of SVG that **already renders the character**, already has fifteen states, already
has `roll` and `bounce` gestures better than the ones I wrote, and was already connected to the
same bridge. The real Mr Odd Ball is visible in every screenshot I took to prove the ball
worked. Four rounds of genuine debugging went into a component that should not have existed.

**Why:** I read `hud/face-preview.html` on the first exploration pass and catalogued it
correctly as "the full character rig". Then I treated the new avatar as *additional* and never
revisited that assumption, through four decision entries. The directive said "implement a
floating overlay UI" and it was reasonable to read that as new; it was not reasonable to build,
deploy, debug and pin it without once asking whether the thing it drew already existed.

**How to apply:** when a request implies a new visual surface, grep for what renders that
subject **before** writing any of it, and say out loud what you found — "there is already an
X; do you want it animated, or a second one?" Duplicating a surface is worse than duplicating
logic: the user sees both, and they disagree.

The corollary that saved it: once I did look, the feature was four small edits, because the
existing thing had the hooks (`T.enter` fires a gesture on state entry) and the animations
(`roll`, `travel:150 spin:540`; `bounce`, `rise:70 bounces:3 damp:1.5`) already. **The existing
implementation is usually better than the one you would write** — its roll ties spin to
displacement so he unwinds on the way back, and its bounce is a raised cosine with zero
velocity at contact. Mine were a linear translate and an ease.

It also hid a real bug: `setState` starts `if (!STATES[next]) return;` and there was no
`thinking` row, so the one state the engine sends most often had **never** animated his face.
Building the wrong thing is how I found it, which is not a recommendation.

## The retry you are relying on may live inside the thing that failed to arrive

**2026-08-22.** LB rebooted the Pi and got `Could not connect to 127.0.0.1: Connection refused`
where his face should be. `config/oddball-face.desktop` had a comment arguing that startup
order did not matter, "because the rig retries its WebSocket every 2s forever".

That was true of the **socket** and irrelevant to the **page**. `hud_bridge` serves the rig over
HTTP on the same port, so when the GET fails there is no page, so no JavaScript, so nothing
retries anything. The retry being depended on was inside the asset that had not loaded.

**Why:** `oddball.service` is `Type=simple`, so systemd reported it active 8s after boot — at
`exec`, not at `listen`. Binding 8765 happens much later, after faster-whisper and an
onnxruntime model load off an SD card. The desktop entry started the window 2s after that.

**How to apply:** when arguing that a startup race is benign, name the exact component that
recovers and check it is *present* in the failure case. "It reconnects" is not a property of a
system, it is a property of some code, and that code has to have been loaded. And for a client
that fetches its own UI over a socket it also talks to, the fetch is the fragile half.

Bonus, caught in the same hour by reading the log rather than trusting it: WebKit emits
`load-changed(FINISHED)` **after** `load-failed`, so my "reset the backoff on success" handler
fired on every failure and the delay stayed at 1s forever. It logged "retrying in 1s" eight
times in a row and looked like a working retry loop. Exponential backoff that never backs off
is a thing you only notice if you read the numbers.

## A wheel-tag query answers a question about one release SERIES, not about a package

**2026-08-22 (D14).** I queried PyPI for `mediapipe` aarch64 wheels, saw `cp39`–`cp312`,
and wrote up "this cannot install on the Pi's Python 3.13" as a measured platform limit.
It was true of the `0.10.x` series and false of the package: `1.0.1` ships one
`py3-none-manylinux_2_28_aarch64` wheel that installs on any Python 3.

LB read the write-up and asked for a Python 3.12 venv rebuild on the strength of it — on a
Debian trixie box that has no `python3.12` package, no `uv` and no `pyenv`. The wrong finding
would have cost an interpreter build and a 1.9 G venv rebuild to pin a November 2024 release.

**The rule:** sort the releases, look at the NEWEST, and check whether the tag *shape* changed.
`py3-none` appearing where `cp3xx` used to be is a packaging decision with consequences. A
major version bump is exactly when a maintainer changes ABI tags, drops APIs, or both — here it
did both, because 1.x also removed `mp.solutions`.

**The worse half:** the wrong finding was written up *persuasively* — a table, a stated
provenance, an all-caps warning. Confidence and formatting made a partial check read as
settled, and it propagated straight into a work request from LB. **Write up what was actually
queried** ("the 0.10.x wheels"), not the generalisation it appears to support. A finding stated
more broadly than it was checked is the kind of error that gets acted on.

---

## L13 — A prescribed fix names a mechanism; check the mechanism exists before applying it

**From:** D18. Six bugs arrived with six fixes attached. Applied literally, one of them
(`pause_threshold = 2.0`) would have edited a library this project does not use, and another
(hold `cv2.VideoCapture` open in `__init__`) would have added a held camera device and a
lifecycle to manage in exchange for **zero milliseconds** — because the object it caches on
lives for exactly one detection inside a child process that then exits.

Two others were already built and shipped: markdown and code-block stripping, and the vault
being reachable from GENERAL. Applying those would have been a second implementation of
something already working, which is how two mechanisms end up disagreeing later.

**Why:** a bug report is authoritative about the **symptom** and speculative about the
**cause**. "The TTS skips words" was true and important; "because the LLM includes markdown"
was wrong, and the real cause — `is_speakable()` refusing the whole sentence over a `°`, and
substituting a canned line — was both worse and invisible from outside. Fixing the named cause
would have left the symptom exactly where it was, and it would have looked like the fix failed
rather than like the diagnosis missed.

Same shape as [[L7]] and [[L10]]: a correct observation attached to the wrong mechanism.

**How to apply:** for each prescribed fix, grep for the thing it names **first**. If it does
not exist, find this stack's analogue and say which one you changed. If it already exists, say
so and do not build a second one. If it exists but the fix would not help, measure the thing it
claims to improve before deciding — the gesture latency table already existed and settled the
question in one read, showing the camera open was 204 ms of 2,217 ms while the mediapipe import
was 1,009 ms.

And when a raised number goes red in a harness, read what the harness was protecting before
changing it. `hangover < budget / 2` was not stale — it fired **correctly**, because a 2.0 s
hangover really does dominate a 2.5 s budget. Widening the budget to make the ratio pass would
have been [[L4]] in reverse: green, and asserting something untrue. The cost is now printed on
every run instead.

## L14 — A specification names a framework; check the framework is still in the repo

**2026-08-23 (D21).** The request said *"Create a new `POST /upload` endpoint in the FastAPI
server (`engine/server.py` or equivalent)"*. There is no FastAPI server. D17 deleted `ui/` and
dropped `fastapi`, `uvicorn` and `pywebview` from `requirements.txt` **four days earlier** —
`requirements.txt` still carries the note saying so.

Building it as written would have reinstated an ASGI stack on the voice loop's import path
without anyone deciding to. The commit would have read "add upload endpoint"; the diff would
have read "reverse D17".

**Why:** a specification is authoritative about the **goal** and speculative about the
**codebase**, in exactly the way [[L13]] says a bug report is authoritative about the symptom and
speculative about the cause. The person writing it is outside the repo and their picture of it
is as old as the last time they looked. LB wanted a paperclip. FastAPI was his guess at the
plumbing, and it was stale.

**How to apply:** grep for every framework, module and path a request names, before writing
anything that assumes it. If it is gone, say it is gone and say what replaced it — *then* build
the goal. `engine/server.py` exists at the path he asked for, serving `POST /upload` with
`multipart/form-data`, on `http.server`. He got the endpoint he specified; he did not get the
dependency he assumed.

And check the *constraint* the same way rather than reasoning about it. "Serve `/upload` from
the same port as the rig" was the obvious plan and is impossible — one query settled it:

    >>> [f.name for f in dataclasses.fields(websockets.http11.Request)]
    ['path', 'headers', '_exception']

No body. Ten seconds, against the installed version, instead of an afternoon discovering it.

## L15 — A test that builds its own world never sees what the repository put in the real one

**2026-08-23 (D21).** `tools/verify_upload.py` had 123 checks green, including nine on the inbox
listing. Every one ran against a `tempfile.mkdtemp()` tree the test had populated itself. Then
the first live end-to-end run, curl through the real endpoint:

```
2 file(s) waiting in data/inbox/:
  .gitkeep         (1 KB)  — category unclear
  flat.kicad_sch   (6 KB)  — looks like a schematic
```

**`data/inbox/.gitkeep` is committed** so the directory survives a fresh clone. To him it looked
like a document LB had uploaded and wanted filed, and he was one turn away from asking which
kind of document `.gitkeep` was.

**Why:** a temp directory contains exactly what the test put there, by construction. That is
what makes it a good fixture and it is also what makes it blind: the real directory has contents
that came from the *repository* — `.gitkeep`, and later `.DS_Store` and `Thumbs.db` — and no
amount of testing against a world you built yourself will ever produce them.

Same family as [[L11]]: a check that cannot fail, for a reason that is invisible from inside it.

**How to apply:** run the feature once against the real tree before calling it done — the actual
directory, the actual endpoint, the actual CLI. It took one curl and one `--list`. Then fold what
that run found back into the harness rather than only fixing it: `verify_upload.py` now asserts
that an inbox holding only dotfiles is *empty*, and names `.DS_Store` explicitly.

## L16 — A lookup table keyed differently from the lookup has silent holes

**2026-08-23 (D21).** `process_inbox_file(filename, category)` slugs what the model sends before
looking it up — `_slug` strips punctuation, because a category dictated through Whisper will not
keep it. The table it looked in had the literal key `"project_file"`.

`_slug("project_file")` is `"projectfile"`. **The key was unreachable**, and `project_file` is a
word the prompt offers the model *by name*. The tool would have refused it with "I do not have a
category called 'project_file'" — after telling the model that was one of the categories.

**Why:** normalising on one side of a lookup and not the other produces a table that is right
everywhere you look at it and wrong where it is used. Nothing raises; the entry is simply never
hit, and the failure surfaces as a tool that "does not trigger sometimes".

**How to apply:** build the keys through the same function the lookup uses —
`{_slug(word): key for word, key in {...}.items()}` — so the two cannot disagree. And enumerate
every literal the *prompt* offers in the harness, not just the ones that look plausible:
`project_file` was in the table, in the prompt, and untested, and it is the one that failed.

The same run turned up a second one worth naming: `Path.relative_to(REPO_ROOT)` **raises** when
the path is not under the repo, and it was building the "Filed it to data/espressif/" sentence
inside a tool called from an agent turn — where a raised exception becomes a spoken traceback.
`tools/kicad_parser.py` is built around "these tools never raise" and fuzzes 600 calls to prove
it. A new tool in the same position gets the same rule, and `pathlib` has more sharp edges in it
than it looks.

## L17 — A URL that authenticates is a password with a scheme on the front

**2026-08-23 (D22).** The request said *"The URL to hardcode (or default) is:
`https://…/feeds/calendars/user_hBJNTDIYLYslxNmEdKLmv56ON13DQ0QrSjnzMDiC.ics`"*. That trailing
token **is** the authentication — anyone holding the URL reads the whole Canvas calendar, with
no login, until it is reset. This repo has a GitHub remote and a tracked branch, so hardcoding
it would have published it in the same commit that added the feature.

**Why:** a secret usually announces itself. It is called a key, or a token, or it sits behind
`getpass`. A *URL* announces the opposite — URLs are the one kind of string that belongs in
source, and this one arrived inside the feature request, already written out, in the position
where a constant goes. Nothing about its shape says stop.

And the cleanup is not symmetric with the mistake. A committed API key is fixed by rotating it;
a committed feed URL is fixed by rotating it too, but only if somebody notices, and the whole
point of the thing is that it is quiet.

**How to apply:** the test is not "does this look like a secret" — it is **"what can someone do
if they have it?"** For any URL going into source, answer that question out loud before pasting.
If the answer involves reading or writing the user's data, it belongs in `.env` with the API key,
and **there is no default constant** — a fallback that is a live token is a published token the
moment anyone commits.

Then make it impossible to reintroduce: `tools/verify_upload.py` greps the module for
`user_[A-Za-z0-9]{20,}` and fails. A rule nobody can check is a rule that lasts one commit.

## L18 — A shell heredoc eats a backslash level, and it can write a NUL into a document

**2026-08-23.** `docs/DEPLOY.md` carried a literal `\x00` in the middle of a shell snippet. It
came from writing `tr "\0" "\n"` through a `python - <<'PY'` heredoc: a backslash level was
consumed in transport, so `"\0"` reached Python as an escape rather than as two characters, and
Python wrote the byte. `git` classified the file as binary. `grep` refused to search it. The
documented command was uncopyable. It survived a full commit and a deploy.

The repair was bitten by the *same* mechanism: `b.replace(NUL, b"\0")` arrived as `b"\0"` and
replaced a NUL with a NUL, reporting success. It only worked as `bytes([92, 48])`.

Same family as the CRLF entry above and the earlier `f"...\r\n"` that became a real newline
inside an f-string: **anything backslash-shaped is unreliable through this path.**

**Why:** the corruption is invisible in every normal view. The markdown renders, the diff looks
plausible, `git status` is clean. The only tell was `grep` saying "Binary file … matches" while
looking for something else entirely.

**How to apply:** never put a backslash escape inside a heredoc-delivered script — build the
bytes from codes (`bytes([92, 48])`), or use the Edit tool, which does not go through a shell.
And sweep for NUL alongside CRLF before committing:

    python -c "import pathlib;print([n for n in ... if b'\x00' in pathlib.Path(n).read_bytes()])"

## L19 — A model given an empty document does not report an empty document

**2026-08-23 (D24).** `tools/syllabus_to_vault.py` sends a syllabus PDF to Gemini and writes the
result into the vault as fact. An image-only scan extracts to **zero characters** — the normal
state of a photographed syllabus, and exactly what LB's two Pi camera PDFs were (D12).

Hand a model an empty document and ask for a grading breakdown and it does not say "this is
empty". It writes a complete, plausible, entirely invented one, which then sits in long-term
memory and is read back by three agents as though LB had written it himself.

**Why:** the guard people write is `try/except` around the *call*, and the call succeeds. There
is no error. The output is well-formed, correctly structured, and fictional. Nothing downstream
can tell the difference, because the difference is not in the shape of the answer.

Same family as [[L6]] and [[L7]]: a confident, fluent, wrong result that no exception marks.

**How to apply:** when a model's output becomes durable state, put a check on the **input** and
put it **upstream of the API call**. `MIN_USABLE_CHARS` runs before any network request, so a
folder of scans costs nothing and the refusal names the cause. And say `no API call was made` in
the message — a silent skip is indistinguishable from success, which is the second half of the
same bug.

The corollary: give every field a way to say *absent*, and render absence rather than dropping
it. A schema with no empty option is a schema that instructs the model to invent.

## L20 — A substring search finds only the words the document literally contains

**2026-08-23 (D24).** `read_from_vault` is a substring scan — no tokenising, no stemming — which
is the right trade for dozens of Markdown notes and no index. The first generated course note had
a heading called "Late and missed work". So:

    read_from_vault("office hours")  -> found
    read_from_vault("late policy")   -> NOT FOUND

The single likeliest question, missing, from a feature built specifically to answer it. Every
check passed; the note was correct; the search was correct; and the two did not meet.

**Why:** the search and the document were written by different people at different times, and
nothing connects a heading's wording to a query's wording. Retrieval systems paper over this with
embeddings, which is precisely the machinery that was removed for being too heavy — so the
vocabulary gap came back, and it came back invisible.

**How to apply:** when the retrieval is exact-match, the **writer** owns findability. Put the
phrasings a person would type into the document itself — the generated notes carry a
`*Search terms:*` line. Do not fix it by loosening the matcher: tokenising a tool three agents
depend on makes every two-word query match too much, to solve a problem one line of text solves.

And test findability against the real searcher, not against the string. `"late policy" in note`
and `read_from_vault("late policy")` are different assertions, and only the second is the feature.

---

## L21 — A fallback must not share a shutdown signal with the thing it is a fallback for

The typed chat channel exists because the microphone does not work: measured 2026-08-19, wake
utterances peaked 0.17–0.28 against a 0.76 threshold. `engine/run_voice.py` says so in a comment
directly above the code — *"Typing is the channel that works when the microphone does not."*

That channel looped on `while not stop.is_set()`. And `_listen_thread` calls `stop.set()` when
the audio device goes away, which is correct for everything that reads audio.

Measured 2026-08-24: the C270's microphone stopped enumerating. `mic_frames` raised
`no such input device 'C270'` **0.08 seconds after start**, `stop` was set, and the typed channel
exited before it had drained a single message. The WebSocket stayed up, the panel kept accepting
text, his face kept animating, and every typed line went nowhere. 8 hours later the log held
24 spoken turns and **zero** typed lines.

**Why:** one event carried two meanings — "stop pulling frames" and "the process is ending". They
agree until the day the microphone dies on its own, which is the one day the difference decides
whether the fallback runs. The name `stop` is what hid it: it reads as belonging to everything.

**How to apply:** when you write a fallback, name the thing it is a fallback FOR, then check the
fallback does not depend on it — not just in the obvious place (does it read audio? no) but
through shared events, shared threads, shared queues and shared shutdown paths. A dependency that
only matters during failure is invisible during success, and success is when you are looking.

The fix was to split the event: `stop` ends the audio threads, `closing` ends the typed channel,
and only the shutdown path sets `closing`. Two meanings, two names.

## L22 — A new persistent file makes every existing harness a writer to it

**What happened:** `vault/reflections.md` is written by `Engine.ask` whenever a turn fails. The
new harness for it, `tools/verify_reflections.py`, redirected the ledger to a temp directory
before writing a single entry — carefully, deliberately, because `tools/verify_engine.py` already
says in its own comments that *"a test that edits production data is a test nobody runs twice"*.

Then `tools/verify_engine.py` was run. Its section 5 drives model failures **on purpose** to check
that failure lines name the right layer. Every one of those deliberate 400s was appended to LB's
real ledger — and from there injected into every agent prompt as something that had "gone wrong".
Two junk entries, discovered only by listing the ledger at the end of the session.

The new harness was careful. The old one could not have been: it was written before the file it
was now writing to existed.

**Why:** adding a write to a shared code path retroactively changes what every existing test does.
`verify_engine.py` was not modified, was not wrong when it was written, and started corrupting
production data anyway — because `Engine.ask` gained a side effect underneath it. The blast radius
of a new persistent file is not "the code that writes it", it is **every path that reaches that
code**, including the ones already written and already passing.

It is worse than an ordinary side effect, too, because this file feeds back into the prompt: a
test's deliberate failures become the assistant's beliefs about its own history.

**How to apply:** when you add a file that code on a hot path writes to, do not stop at redirecting
it in the new harness. Grep for every harness that can reach the writing path — here, anything
importing `engine.core`, `agents/os_agent.py` or `agents/screen_agent.py` — and give the file
**one** override they can all use, rather than monkeypatching the module in each of them:

```python
VAULT_DIR = Path(os.environ.get("ODDBALL_VAULT_DIR")
                 or Path(__file__).resolve().parents[1] / "vault")
```

then one line at the top of each harness, above every `tools/` import, because the module reads
its location once at import time:

```python
os.environ.setdefault("ODDBALL_VAULT_DIR", tempfile.mkdtemp(prefix="oddball-harness-vault-"))
```

Set it at module scope, never inside the section that needs it. The leak is always in whichever
section runs first, which is never the section you are adding — `verify_launch.py` leaked from a
section about desktop entries that has nothing to do with ledgers.

**And then check the real file.** Run the whole sweep, clearing between harnesses, and assert both
ledgers come back empty. That is the only step that catches this class of bug: every harness was
green the entire time it was happening, twice, and the second leak was found only by listing the
ledger after a sweep that reported 12,220 passing checks.


## L23 — A table selected at import makes every harness a test of the wrong platform

**What happened.** Porting the assistant from the Pi to Windows, the destructive-command
blocklist in `tools/os_controller.py` went from doing its job to doing nothing, and every
harness in the repo stayed green through the transition. 16 of 17 destructive Windows commands
were reported as **allowed**: `format C: /y`, `del /s /q C:\`, `Remove-Item -Recurse -Force`,
`vssadmin delete shadows /all /quiet`, `iwr ... | iex`. Measured, not estimated —
`media/data/2026-08-26-windows-blocklist-gap.csv`.

Every pattern in `FORBIDDEN` was a Linux command shape. Point `subprocess.run(shell=True)` at
`cmd.exe` and not one of them can match anything that shell would ever run. And `refuse()` has
no way to say so: it does not raise, does not warn, and has no "I do not recognise this
platform" return. It finds no match, returns `None`, and answers **allowed**.

`tools/verify_os_guard.py` was green because it fed Linux strings to a Linux table. Both halves
of that stayed true. Both halves had stopped being relevant.

**Why:** a harness proves *the code you tested* works. It never proves that the code you tested
is *the code that will run*. The moment any table, backend, path or pattern list is selected at
import — by `sys.platform`, an environment variable, a config key, a feature flag — the harness
has silently acquired a second job it was not written to do, and the failure is invisible from
every direction anybody normally looks. Nothing errors. Nothing warns. The count goes up.

This is the same shape as **L22**, and worth noticing that it is: there, a new persistent file
made every existing harness a writer to it. Here, a new platform makes every existing harness a
test of the other one. In both cases the harnesses were green throughout, and in both cases the
only thing that found it was asking a question the harness was not asking.

**How to apply:** when a module selects one of several tables at import, add the dullest
possible check and put it FIRST, before every check about whether the patterns work:

```python
# tools/os_controller.py — the module states which table it chose
def active_table_name() -> str:
    return "windows" if _IS_WINDOWS else "linux"

# the harness asserts the RUNNING platform got one, and that it is not empty
check(guard.active_table_name() == ("windows" if _IS_WINDOWS else "linux"),
      f"the loaded table is the one for {sys.platform}")
check(len(guard.FORBIDDEN) > 0,
      "and it is not empty — an empty table refuses nothing and reports success")
check(guard.FORBIDDEN is not (guard._LINUX if _IS_WINDOWS else guard._WINDOWS),
      "and it is not the OTHER platform's table, which would match nothing here")
```

Three lines, none of them clever. They are the checks that would have caught this on day one.

Then **make the corpus follow the selection.** `MUST_REFUSE` and `MUST_ALLOW` are selected by
platform now, in the same way and for the same reason `FORBIDDEN` is: a test written in a
language the shell does not speak is not a weak test, it is not a test.

**Two smaller rules from the same day.**

*A probe must be a thing the running platform genuinely refuses.* Section 4 of that harness
called `run_command("rm -rf /")` on a hardcoded literal. On Windows that is not a refusal — so
it fell through to `subprocess.run` and **the harness executed a command**, against the promise
in its own first line. It surfaced as two ordinary-looking assertion failures somewhere else
entirely. Generalised: a harness that reaches real execution when a guard MISSES has no way to
report that the guard missed. Parameterise the probe from the same selection the table uses.

*Do not translate the other platform's table.* It was tried on paper and is wrong in both
directions: `vssadmin delete shadows` and `iwr | iex` have no Linux equivalent to translate
FROM, and half the Linux rows (`mkfs`, `dd of=/dev/`, `chown -R`) have no Windows meaning to
translate TO. Write the new one against the new platform's shapes, keep the old one verbatim,
and select. Do not merge them either — on Windows the Linux `rm` row starts matching inside
unrelated commands and names the wrong cause, and a refusal that gives the wrong reason is how
a guard gets switched off in frustration.

---

## L24 — A guard that never fires makes the checks after it pass while testing nothing

**2026-08-28, building the vault notebook.**

`engine/core.py` holds a note open across turns: "What should I write down?" then "What should
I call it?". Backing out needed a cancel, and I reached for `orchestrator/instant.is_sleep` —
the end-anchored dismissal matcher that already handles "goodnight", "that's all", "leave me
alone". It looked like the same job.

It is not. **"Never mind" and "forget it" are not in that list, and should not be**: neither of
them means LB wants Mr Odd Ball to stop listening. They mean he wants *this* to stop. A
dismissal ends the conversation; a cancel ends one action inside it.

### What made it worth a lesson is how it failed

Not as one red line. `tools/verify_notes.py` ran this:

```python
turn(esc, "take a note")
r = turn(esc, "never mind")
check(esc.note_draft is None, "a dismissal cancels the draft")        # RED, correctly
check(not kv.find_notes("never mind"), "and nothing was written")     # GREEN, meaninglessly

turn(esc, "take a note")
r = turn(esc, "")
check(esc.note_draft is None, "silence cancels the draft")            # GREEN, meaninglessly
```

The cancel did not fire, so "never mind" became the note's **content**. The draft moved on to
awaiting a name. The next `"take a note"` was then read as **the name**, and a note called
"take a note" was written. By the time the silence check ran there was no draft left to
cancel — so it passed, having tested nothing at all.

One broken guard, one honest failure, and **two checks that went green for the wrong reason**.
The second and third were not weak tests; they were correct tests, downstream of state the
first one was supposed to have reset.

### The rule

**A check on state that a previous step was supposed to establish is only as good as that
step.** When a guard fails, assume every assertion after it in the same sequence is now
untrustworthy — and write the ones that matter to be independently falsifiable:

```python
turn(esc, "take a note")
held = esc.note_draft is not None            # prove there WAS something to cancel
r = turn(esc, "")
check(held and esc.note_draft is None and "nothing written down" in r.speech.lower(),
      "silence cancels the draft, and he says so")
```

Asserting on the **speech as well as the state** is what makes it bite: "no draft" is true both
when the cancel worked and when there was never a draft, and only one of those says "nothing
written down".

Related: L15 (a test that builds its own world never sees what the repository put in the real
one) is the same family — a check that cannot distinguish success from a vacuum.

---

## L25 — A textual scan of a well-commented file reads the prose, not the code

**2026-08-28, same session.** `tools/verify_notes.py` asserts that
`orchestrator/note_intent.py` cannot reach a model. First attempt:

```python
_source = inspect.getsource(note_intent)
for forbidden in ("langchain", "genai", "requests", "urllib"):
    check(forbidden not in _source, ...)
check("agents" not in _source, "it imports nothing from agents/")
```

Two red, both wrong. The module's own docstring contains the sentences *"Nothing here imports
`agents/`"* and *"a planner returns a request"* — so the scan found the module's **promise not
to do the thing** and reported it as the thing.

The fix is to parse:

```python
tree = ast.parse(inspect.getsource(note_intent))
imported = {a.name.split(".")[0] for n in ast.walk(tree)
            if isinstance(n, ast.Import) for a in n.names}
imported |= {n.module.split(".")[0] for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom) and n.module}
```

Which is also **stronger than what was intended**: it can assert the whole import set is a
subset of the standard library plus `orchestrator/`, rather than checking a list of forbidden
words someone has to remember to extend.

This is L23's rule arriving from the other direction — *use `ast`, not string matching* — and
it will keep arriving, because this repo comments heavily and every comment is text a naive
scan will read. **If a check is about what the code DOES, parse the code.**

---

## L26 — Look for what the machine has already recorded, before inventing a corpus

**2026-08-28.** The single most expensive habit of the whole session, and it cost the same
mistake three times before I noticed the pattern.

### Three times, same shape

1. **The note matcher.** I wrote `orchestrator/note_intent.py` against a corpus I made up, and
   shipped it. `captures/` — which `--save-captures` has been filling since the port — held LB
   trying to take a note by voice **ten minutes before he asked me to build it**. Both real
   utterances failed: one stored a note whose body was "in the vault", the other was missed
   entirely. Neither was findable from invented examples.

2. **The wake threshold.** I ranked "re-fit `[wake].threshold`" as the top priority on a board
   entry recording scores of 0.17–0.28. `captures/` holds **31 wakes across three days** — a
   capture only exists after one fires. The figure was from the Pi and stale. I recommended
   work on a problem that had already gone away.

3. **Restart-on-failure.** Approved and started. `data/oddball.log` has **exactly one** start
   marker across fourteen hours: nothing has ever crashed. I was building insurance against an
   event with no instances.

Three items, and the evidence that settled all three was sitting in two directories in the repo
I had open the whole time.

### Why it kept happening

An invented corpus feels like progress: it is fast, it is under my control, and every example
in it passes. Real recordings are awkward, badly transcribed, and full of things that are
nobody's intent — *"family home yes k t o p 3 nsu 5dk"*. **That awkwardness is the value.** The
invented negatives could argue the matcher was safe; the 27 real ones proved it, and then found
two bugs the invented ones could not.

It is L15 — *a test that builds its own world never sees what the repository put in the real
one* — but L15 is written about fixtures. This is the same failure one level up, in **choosing
what to work on at all**.

### The rule

**Before writing a corpus, planning work, or accepting a number from the task board, spend two
minutes asking what this machine has already recorded about it.** In this repo that is:

    captures/           every utterance after a wake, with tiny.en's transcript in the filename
    data/oddball.log    start markers, exceptions, dropped frames, wake scores
    data/face.log       the window
    media/data/*.csv    every measurement anyone has taken
    vault/*.md          corrections and reflections he has written himself

A number on the task board is a **claim with a date on it**. `captures/` is what happened.
When they disagree, the directory wins.

### And the corollary that saved two wrong reports

Real data also disproves things, and twice it disproved *me* before I said them out loud: the
3,940 dropped frames looked like audio corruption until `run_voice.py:484` showed the queue is
drained at end of turn anyway, and the minimum-duration guard for false dismissals died on the
fact that 2.0s of every recording is `hangover_s` silence. **Check the mechanism before
reporting the finding**, not after.

---

## L27 — A model's capability list is a claim; the real prompt is the test

**2026-08-29.** `minimax/minimax-m2.7:free` was switched in as the PERSONA model on the strength
of its OpenRouter page, which advertises `tools` and `tool_choice`. I checked that page *before*
writing the refactor, specifically because PERSONA is also GENERAL and GENERAL is the only route
that can file an upload. The check was real and it was not enough.

    bare 10-word prompt, tools bound             tool_calls  3/3
    the REAL 8,175-char persona prompt           tool_calls  0/3
    the same prompt, gemini-3.5-flash-lite       tool_calls  1/1

Three configurations of the real prompt — temperature 0.8 and 0.2, vault+file tools and vault
only — and it called nothing in any of them. **Twice it said "I've written that down" while
calling nothing**, which is the exact sentence `knowledge_vault.VAULT_INSTRUCTION` forbids.

### Why this is worse than a model that simply cannot do it

A model with no tool support fails loudly at bind time. This one passes every cheap test, passes
its own documentation, and fails **silently** on the one route that has no fallback — while
narrating success. `save_to_vault` and `process_inbox_file` would have stopped working and the
only symptom would have been notes quietly not existing.

### The rule

**Probe with the prompt you actually ship.** A capability is not a property of the model alone;
it is a property of the model under your prompt, your temperature, and your tool count. The
persona prompt is 8,175 characters of personality, vault rules, file rules and chat history, and
that is the context tool-calling has to survive.

`tools/probe_persona_tools.py` is the generalisation: it runs candidates against
`PERSONA_PROMPT_TEMPLATE` itself and scores **tool calls, not reply text** — because the reply
is the thing that lies. It also flags the dangerous case explicitly: no call plus a claim of
success. Measured across six free models, only two passed, and one 404'd on a slug I had guessed
rather than looked up. `nvidia/nemotron-3.5-lightning:free` is 3/3 and is what runs now.

### And the thing the failure exposed underneath

Chasing why minimax answered "It's 9 35." to a question about a transistor turned up that
`tools/verify_notes.py` and `tools/verify_academic.py` had both been writing their test
utterances into **LB's real conversation log** — `memory_manager.MEMORY_FILE` was a bare
relative string with no override, the one persistent store in the repo that had neither. Those
turns were being injected into every agent prompt as things he had recently said. L22 again,
from the other direction: an old file, and new harnesses that became writers to it.

---

## L28 — A Windows path in a Python string is an escape sequence, and the module stops importing

Migrating `agents/os_agent.py` off the Raspberry Pi prompt meant putting a real path into it —
`C:\Users\user\OneDrive\Desktop` — because the whole point of the fix was that the model should
quote a fact instead of composing `$Home\Desktop`. Doing that broke the file three times in a
row, in three different strings:

    OS_PROMPT_TEMPLATE = """...C:\Users\..."""   SyntaxError: truncated \UXXXXXXXX escape
    the module docstring                          same
    propose_launch's docstring                    same

`\U` opens an eight-digit unicode escape. `C:\Users` is therefore not a path, it is a malformed
escape, and the failure is not a wrong string — **the module does not import at all.** Nothing
downstream of it runs. `\N` and `\x` behave the same way; `\D` and `\O` are merely deprecated,
which is worse, because those pass today and warn.

### The rule

**Any string in this repo that contains a Windows path gets the `r` prefix — docstrings
included.** Now that the target is Windows, that is prompts, examples, comments-inside-prose,
and every harness fixture. `agents/os_agent.py` carries the reasoning at both of its raw strings
so the next person to add a path does not delete the `r` as noise.

### The part that generalises past the escape

The same bug bit *the patch scripts writing the fix*, twice, for the same reason — a heredoc'd
`python - <<'EOF'` doing `s.replace(old, new)` is itself Python source, and `new` held the path.
One of them was worse than a SyntaxError: `\b` in a non-raw literal is a **backspace character**,
so a regex meant to read `\s*\b(?:that\s+)?...` was written to disk containing `\x08`, compiled
without complaint, and silently matched nothing. It was caught only because the function it
belonged to was tested against real utterances immediately after being written.

So: when a fix is applied by a generated script, the script is code too, and a string that
survives one layer of quoting can still be mangled by the next. **Test the value, not the
edit** — `print(pattern.pattern)` and run the function on a real input. A `replace()` that
reports success has proved that the text was written, and nothing whatever about what it says.

---

## L29 — A flag that answers a coarser question than the one being asked

`engine/run_voice.py` had one event, `in_turn`, and the microphone thread used it to decide
where frames go. It means **a turn is running**. It was being read as **he is listening**, and
those are different for most of a turn's wall clock — a turn captures for two seconds, then
thinks for anything up to five minutes, then speaks.

Two failures came out of that single conflation, and only the small one was visible:

    8,530 "utterance buffer full" lines in one morning, at 12.5 a second
    a permission gate that read 16 seconds of audio recorded BEFORE its own question

The second one approved a shell command. `frames_q` holds 200 frames, the queue was being
filled the whole time the turn was thinking, and when the gate finally called `_capture()` the
recorder consumed that backlog at memory speed. `UtteranceRecorder` measures `max_s` against
the *wall clock*, so a 15-second cap never fired on 16 seconds of buffered audio: the log shows
`capture spoke: 18.08s audio, 0.96s voiced`, longer than the configured maximum, and it
transcribed to "Yes. Yes."

### The rule

**When a flag is read somewhere it was not written for, check that it answers that question and
not merely a related one.** `in_turn` was correct for its own job — do not feed the wake
detector, because a wake word inside a turn is a loop. It was never a statement about the
microphone. The fix was a second event, `capturing`, set for exactly the length of
`Turn._capture`, in a `try/finally` so that no exit path can leave it raised.

The tell was there in the log for anyone reading it: a capture longer than `max_s`. A number
that exceeds its own configured limit is never a tuning problem — it means the thing being
measured is not the thing the limit describes.

### And the check that hid the throttle bug for a whole run

Rate-limiting the dropped-frame warning to one line a second seemed obviously right, and the
first version started its clock at `time.monotonic()`. So a burst shorter than one second
logged **nothing at all** — a silence indistinguishable from a healthy microphone, in the one
place whose entire job is to report that the microphone is not being read.

It passed. The harness check was `check(bool(drops) or frames_q.qsize() < 310, ...)`, and the
second clause was true regardless, so the assertion could not fail. **An `or` in a check is
almost always a check that has been widened until it stopped biting** — the honest version was
`check(bool(drops), ...)`, which went red immediately. Initialise the throttle's clock to 0.0
so the first event of a burst always reports; throttle the repeats, never the onset.


---

## L30 - The gate that rejected the answer for a character nobody can see

2026-08-29, 20:20. LB asked "Can you create a circuit using an LED and Arduino Uno?" and got a
correct answer on the card. What Piper actually said out loud was one word:

    Sure!

Then the conversation window opened, LB was still reading the screen, and after 1.5s of silence
the rig played the greeting - "What's up LB? What can I do for you?" - which reads exactly like
being skipped past. Three separate things had to line up, and the log named none of them.

**One.** The reply had no `SPOKEN:` line, which is correct: `persona_agent.py` says in as many
words that it writes none, because "the whole reply IS the spoken half". But `engine/split.py`
never implemented its half of that contract. It sent the reply to `memory.speakable.extract()`
like any other - and `extract()` is built for corpus paragraphs, where picking the ONE best
sentence out of a page is the whole point. Given an answer that was already the right length,
picking one sentence could only lose something.

**Two.** It picked the wrong sentence, and not because the scoring was wrong. The real answer
scored 7.5 against "Sure!" at 4.25. It was thrown out by `verify()`, which rejects anything
non-ASCII - and the model had written "built-in" with U+2011 NON-BREAKING HYPHEN. `extract()`
fell through to the only sentence that verified.

**Three.** The two gates disagreed and neither said so. `is_speakable()` passed the full reply;
`verify()`, one call deeper, refused it. Nothing logged the refusal, because a fallback is not
an error.

### The rule

**A rejection rule needs a normalisation step in front of it, or it rejects on spelling rather
than on substance.** This is the same failure the module already had documented one notch over:
`expand_symbols()` exists because "The trace needs 0.9 mm for a 20 degree C rise" was never
spoken, on account of the degree sign. U+2011 is that bug again, and worse, because a degree
sign is at least visible in a log. A non-breaking hyphen renders identically to the hyphen next
to it.

So typographic punctuation - the space that is not a space, the hyphen that is not a hyphen,
the curly quote, the ellipsis, the zero-width joiner - is now normalised to ASCII in
`expand_symbols()` before any gate judges it. `verify()`'s ASCII rule is untouched and still
rejects "Cafe" spelled with an accent, because a letter is part of a word and rewriting it
changes the word. Punctuation is not heard.

And `split()` now says the whole reply when the whole reply is speech, held to BOTH gates so
that "say all of it" is not a softer route to the speaker than extraction was.

### The tell

`synth 0.27s` on a 20-second turn. Every other answer that evening synthesised in 0.30-0.56s
carrying a real sentence; the shortest work produced the shortest audio, and the turn line said
so. **When a stage's cost collapses while the stage before it did the full work, the output of
that stage got smaller, not faster.**


---

## L31 - A second way to do something he could already do

Gesture approval worked. A thumbs up at the camera answered the permission gate, four security
guarantees were written down and tested, `verify_pointer.py` had seventeen checks and a probe
that weakened each one. None of that was the question.

The question was what it COST the turns that did not need it. 2026-08-29, 19:52:

    19:52:24  launch intent: 'open' + 'arduino ide' -> Want me to open Arduino IDE?
    19:52:49  gesture read timed out after 20s
    19:52:49  no camera - not probing again for 600s
    19:52:58  heard 'Yes' -> True

**Twenty of that turn's thirty-eight seconds were spent asking a camera that was not plugged
in.** And the thing it would have rescued - a yes Whisper could not hear - was a yes he then
said out loud successfully. LB's own summary: he can just say yes.

### The rule

**A fallback earns its place by what it costs when it is not needed, not by how well it works
when it is.** The camera was the gate's second channel and it charged every unreadable
transcript ~2.2 seconds on the Pi (D15) and up to 20 on a box with no webcam. The retry that
was already there rescues the same case for the price of asking again.

Two related traps this makes concrete:

- **A guarantee's strength is not evidence the feature is worth having.** The pointer's
  cannot-type guarantee was the most carefully reasoned thing in `docs/DEPLOY.md`, including an
  honest note that Windows downgraded it from kernel-enforced to inspection-only. All true, all
  irrelevant to whether LB wanted the AI moving his mouse. He did not.
- **Removing it removed the argument, not just the code.** There is now no code in this repo
  that can move a pointer or synthesise a keystroke, so the downgrade is moot. That is a
  stronger position than the careful one, and it was available the whole time.

### What was kept, deliberately

`media/data/2026-08-22-gesture-approval-*.csv` and `media/charts/gesture-approval-latency.svg`.
A feature that was built, measured and then removed is the one most worth having measured - the
CSVs are what make this an argument rather than an opinion. The `startle` in
`hud_bridge.play_gesture` also stays: it animates the cartoon face and never touched a camera.
Same word, different thing, and grepping for "gesture" cannot tell them apart - which is why
`verify_gate_state.py` section 6 parses imports with `ast` instead.


---

## L32 - Reporting a gap honestly is not the same as closing it

`tools/vector_db.py` printed this on every rebuild, for days:

    datasheets: 3 page(s) carried NO extractable text - esp32devkitv1_schematics.pdf,
                pi_cam3.pdf, pi_cam3_noir_wide.pdf
                These are image-only PDFs. Nothing can be retrieved from them until
                they are OCR'd or replaced with text-bearing files.

That message is good. It names the files, explains the cause, and says what would fix it -
everything the D9 empty-BOM lesson asks for. It was also, for as long as it went unread, a
**substitute** for the fix. On 2026-08-29 LB uploaded a schematic and was told "its text is
being indexed in the background", which was true about the intent and false about the outcome:
it contributed zero chunks and always would have.

### The rule

**A warning that recurs unchanged is a backlog item, not a diagnostic.** The first print is
information. The hundredth is a decision that has been made by default. Grep the logs for
messages that appear on every run and ask which of them are describing work nobody scheduled.

The related failure is the honest sentence in front of a user: "it is being indexed" was
literally true - a rebuild did start - and useless, because the thing he cared about was
whether he would be able to ask about the board. `agents/persona_agent.py` is told not to
promise searchability, and it obeyed. Obeying was not enough.

### And one correction to received wisdom, measured

Everyone's instinct for OCR of small schematic text is to raise the DPI. Swept it:
`media/data/2026-08-29-ocr-dpi-sweep.csv`, charted in `media/charts/ocr-dpi-sweep.svg`.

    esp32devkitv1_schematics    404 -> 432 -> 431 -> 428 chars over 150 -> 400 dpi
    pi_cam3                    2023 -> 2062 -> 2069 -> 2078 chars

**Flat.** RapidOCR resizes to its detector's input size before doing anything, so a 400 dpi
raster is downsampled back to roughly what 150 dpi already was - 4x the memory for the same
characters, on a path that sits between LB pressing the paperclip and hearing an answer. Sweep
the parameter before you tune it; the curve is often flat where the folklore says it is steep.

### And the fixture that could not fail

`verify_ocr.py` section 4 asserts that a page which already HAS text is never OCR'd over. It
passed. It also passed with `--probe`, which deliberately makes the OCR pass greedy - because
the fixture PDF had one page and the "already has text" document pointed at page index 1, which
did not exist. `ocr_pdf` filtered it as out of range, so the protected thing was **unreachable
rather than protected**. Same shape as L29's `or` that widened a check until it stopped biting.
The probe is the only reason it was caught. Run it.

---

## L33 - Ask which job actually needs the model, not how to make the model cheaper

**2026-09-02, the quiz.** It sent every marked answer to Gemini - one request, off a tier
counted in requests at 20 a day, to decide whether "V = I R" matches "V = I * R". Ten questions
was half of LB's daily budget, and a second quiz took the router, the persona agent and the
firmware agent down with it until midnight.

The instinct was to make the call cheaper: a smaller model, a shorter prompt, batching. All
three would have worked and all three are the wrong question. **Marking a known answer against
a spoken one is string comparison, arithmetic and a little algebra.** There was no job for a
language model in it at all.

Before optimising a call, ask what it is deciding. If the answer is already on disk, the call is
not expensive - it is unnecessary. 10 requests to 0, and 820 ms to 0.63 ms, fell out of asking
the second question instead of the first.

## L34 - A character class is a claim about the world, and hyphens are punctuation

Same day, four bugs, all one shape: a regex admitting something whose meaning it had guessed.

- The gate deciding whether a string was algebra was "contains one of `+-*/^=` or a digit".
  **`Inter-Integrated Circuit` contains a hyphen**, so it went to sympy, parsed as
  `inter - integrated*circuit`, and a correct answer was marked wrong. Every hyphenated
  technical term in the bank.
- `infer_kind` treated "digits followed by any letter" as a number, so **`2x` was numeric** and
  a bare "2" marked correct against an answer of `2x`. `2V` is two volts; `2x` is algebra; the
  difference is whether the letter is a unit, and the regex had never been asked.
- The inline-answer marker was unanchored, so the question "A question with no answer
  anywhere?" **matched on its own word "answer"** and stored "anywhere?" as the answer - and
  counted as a successful parse rather than a dropped one.
- Pages were joined with a bare `\f`, and **`^` in MULTILINE mode does not follow a form feed**.
  A question at the top of page 2 was invisible.

Every one of these is a character class that was written for the case in front of me and then
met a case I had not pictured. None was found by re-reading the code; all four came from a
fixture with a real hyphen, a real `2x`, a real second page. **Write the regex, then write the
input that embarrasses it.**

## L35 - Run it once, out loud, before you call it done

The harness was 76/76 green. Then I ran an actual session end to end and found two things no
check was looking for:

- **"Correct. Correct - B, act only on maxims..."** The caller prefixed a verdict word onto a
  `why` that already opened with one. Every mark stuttered. Invisible in an assertion on
  `verdict == "correct"`; obvious in one line of transcript.
- **It re-asked a question in silence.** `pick` wraps when the deck is exhausted - correct for a
  drill, and indistinguishable from the `random.choice` repeat bug this whole stage existed to
  fix, because nothing said "going round again".

Both are speech defects, and the harness was checking state. A green suite means the properties
you thought of hold. It says nothing about the ones you did not - and for a thing that talks,
the transcript is where those live. **Read the output as a user would hear it.**

## L36 - Do not lower the threshold until the wrong answer passes

The prose grader marks "it is how much a material opposes current" as **partial** against
"Resistance is the opposition to current flow". That is a decent answer and it wanted to be
correct. Lowering `PROSE_PASS` fixed it - and every value low enough to pass that paraphrase
also passed answers that were plainly wrong, because the grader is matching content words and
has no semantics to distinguish the two.

So it stayed partial, the official answer is shown alongside, and the limitation is written into
the module docstring and D53 rather than tuned away. **A grader that flatters is a grader he
cannot revise against**, and the honest verdict was available the whole time: "part of it, and
here is the answer."

---

## L37 - A docstring that disagrees with its code is a bug report nobody has read yet

`classify_yes.normalise` said *"Apostrophes are dropped rather than kept so `don't` and `dont`
are the same word."* The code replaced them with a **space**. That mismatch was already on the
board, filed under "Also found, lower" as a tidiness issue.

It was not tidiness. `don't` became `don t`, so `_NO`'s `dont` never matched, so
`is_yes("Don't run it")` fell through to `_YES`, matched `run it`, and **returned True** - in
front of PowerShell execution. One character, three weeks, and the most natural refusal a
person can make.

The reason it was misfiled: **the defect and its consequence were one function apart.** Seeing
the mismatch requires reading `normalise`. Seeing that it opens a permission gate requires also
noticing that `_YES` contains "run it". Nobody made the second hop, because the first one
looked like a comment problem.

When code and its own docstring disagree, do not file it as a comment fix. Ask what the
docstring was PROMISING and who is relying on that promise. Someone wrote it down because it
mattered.

## L38 - Fixing the thing on the board would not have fixed the bug

The board said: *"`is_yes` bare words - delete `do`, `please`, `course` from `_YES`. One line,
and it is in front of shell execution."* Accurate, real, and **not the severe half**.

Deleting those three words leaves `is_yes("Don't run it")` returning True, because `run it` is a
legitimate yes phrase and the apostrophe bug had already destroyed the "don't" before any list
was consulted. The one-line fix would have shipped, the item would have been ticked, and the
gate would still have approved refusals.

What found it was **measuring the function before editing it** - running the actual thing
against the actual sentences a person says, instead of trusting the note from the last person
who looked. Three minutes. The note was written by me, from a code read, and it was wrong about
which defect mattered.

A task-board entry is a hypothesis about a bug, not a specification of it. Reproduce it first.

## L39 - Check that a new fixture would have failed before

Seven fixtures were added to the destructive-command corpus. Six caught the bug they were
written for. The seventh, `ri -rec -force C:\`, was **already passing** - a bare drive root is
refused by a different rule entirely, so it certified nothing about the abbreviated `-rec` it
was written to test. Replacing the drive root with an ordinary directory made it bite.

Third occurrence of this exact shape: L29's widened `or`, `verify_ocr`'s unreachable page index,
and now this. A green check on a path the check cannot actually reach is worse than no check,
because it is counted.

The remedy is now cheap and there is no excuse for skipping it: **reconstruct the old predicate
in a scratch file and run the new corpus at it.** Anything that does not go red is measuring
something else.

## L40 - Fix both halves of a safety pair, or neither is fixed

D4's design has three legs: the model composes, a human approves, a blocklist backstops. Two of
them were broken at once, and the two failures met on the same sentence - saying *"Don't run
it"* to `Remove-Item C:\Users\user -Rec -Force` approved a command the blocklist then let
through, because `-Rec` is a prefix PowerShell accepts and the pattern demanded `-recurse`.

Either hole alone is survivable: a bad approval still meets the blocklist, and a blocklist gap
still needs an approval. It is the **conjunction** that deletes a home directory, and neither
harness could see it because each tested its own half.

So `verify_consent.py` section 4 runs both halves against one sentence. When a system's safety
is a chain, at least one test has to assert on the chain rather than on the links.

---

## L41 - Read the log before asking what is wrong, and read it for SILENCE as well as errors

LB asked what Mr Odd Ball had been struggling with. `data/oddball.log`: 23,606 lines, eight
days, **zero errors, three warnings**. By the usual measure, nothing was wrong.

Three real problems were in there, and the reason none had been reported is that **none of them
fails**. A note truncated mid-sentence is announced as "Added to your note." A 147-second turn
looks like a slow network. Room tone answered by a persona model looks like a conversation.

So the useful queries were not "what errored" but:

    which routes are slowest, and what were they answering
    what did he actually SAY, and how often
    what did the turn extras record that nobody acted on

The last one found the headline. `hit max_s` had been written into every capped turn since the
cap existed and read by nothing.

## L42 - A warning logged and not surfaced is a bug with a paper trail

    17:10:37 WARNING utterance hit the 15s cap - keeping what we have

Correct, timestamped, and useless. The process knew the recording had been cut off; the person
speaking into it did not, and was told his note had saved cleanly. He then spent three minutes
trying to repair a sentence he did not know was broken, and the repair was routed to a chit-chat
model and thrown away.

**When a component detects something the user needs to know, logging it is not handling it.**
The test is not "did we notice" but "who was told". `Engine.ask` grew a `truncated` flag for
exactly this: one fact, known only to the audio layer, carried to the one place that could
speak it.

## L43 - Set the limit on the ACTIVITY, not on the process

The recording cap was one number for two different jobs. 15 seconds is generous for "what's the
trace width for five amps" and it cut a dictated paragraph in half. The cap had already been
raised once, 10 -> 15, for the same reason, and it failed again at the new number because the
number was never the problem.

The instinct is to raise it. The comment on the constant says why not: it exists *so a
television cannot record forever*, and there was a Fusion 360 tutorial in the same log being
transcribed as commands.

What resolved it was noticing the two jobs are distinguishable from state the process already
holds. **A television cannot open a note draft.** So the cap is 15s to ask and 90s to dictate,
and it only ever rises for one turn that LB himself opened one utterance earlier.

Before tuning a limit that serves two purposes, check whether the two purposes can be told
apart. If they can, the limit was never one number.

## L44 - Cost the cheap thing before optimising the expensive one

Thirty turns spent 8.2 minutes - 32% of all the assistant's thinking time in eight days - and
sixty API calls answering: "Okay." "Whoa." "ball." "Yeah, yeah, yeah, yeah."

Not one was a question. The most expensive single turn in the entire log, 147.8 seconds, was
answering "Yeah, yeah, yeah, yeah."

Two independent things had to be true for that: the utterance reached a paid model at all, and
nothing bounded how long it could take. **Both fixes were small and neither would have been
enough alone.** A timeout still spends the calls; a filter still leaves the real questions
exposed to an unbounded wait.

The lesson is the order of the question. Before making a slow path faster, ask how much of the
traffic on it should have been there.

## L45 - Never lose what was heard, unless the repair is worse than the loss

My first design for the truncated note wrote the fragment immediately - "never lose what was
heard" - and re-opened the draft for the rest. It preserved every word and produced this in the
vault:

    ...as market commodities rather than public goods, incentivizes
    ---
    things such as predatory pricing, regulatory capture...

`append_note` puts a horizontal rule between blocks. The principle was right and its application
put a page break through the middle of a sentence.

Holding the content for one more utterance instead is not a new risk: the "what should I call
it?" turn has always carried dictated content in memory across a turn. **Check whether the
careful thing you are about to do is already being done elsewhere** - the established window was
sitting one branch away, and matching it was both safer and less code.

---

## L46 - Check the brief against the code before building to it

LB asked for "a blanking window in the recorder immediately following the TTS output so he
doesn't hear himself". Correct instinct, wrong mechanism: `audio/gate.py` has muted capture
during TTS since 2026-08-11, with a tail and a detector reset. That half of the brief was
already built, and building to it would have produced a second gate behind a working one.

The echoes were his OWN voice - the tail of the wake phrase, arriving after openWakeWord had
already fired and opened the recorder.

Ten minutes reading the module that supposedly had the gap is what found this. **A brief is a
description of a symptom plus a guess at the cause.** Take the symptom seriously and check the
guess, especially when the person writing it is right about everything else.

## L47 - If a number would settle the question, check whether anything ever wrote it down

Two explanations fit `capture spoke: 2.48s audio, 0.16s voiced` equally well: the recorder
opened into the wake word's tail, or something in the room made a noise a second later. They
need completely different fixes.

`Capture.waited_s` distinguishes them perfectly. It had been **computed and never logged**
since the class was written. And the fallback - reading the saved audio - was closed too:
`_finish` trims from `first_voiced_i - PREROLL`, so the offset inside the listening window is
destroyed before the file is written.

Eight days of logs, 197 saved recordings, and the question was unanswerable from all of it.

The fix was one line in a log statement. Before running an experiment, before building a
replay harness, **ask what single number would end the argument, and check whether anything is
recording it.** Often the answer is that the code already knows and never says.

And when you then add the instrument, make sure your own fix cannot erase it: `waited` now
reports a separate `_first_voice_at`, because reporting the delayed trigger would have hidden
exactly the evidence that says whether the delay was needed.

## L48 - Delay the decision, do not delete the data

The obvious implementation of "ignore the first 250ms" is to drop those frames. It is wrong,
and the module's own existing comment says why: the first consonant of a sentence lands in the
frame before the VAD is convinced, which is why `PREROLL_S` exists at all. Dropping audio
would eat the first word of "Hey Mr Odd Ball, what time is it?" said in one breath.

`ignore_start_s` suppresses only the *trigger*. Frames are still buffered and still counted, so
when real speech starts, the pre-roll hands back everything that was skipped - the capture is
**byte-identical** to one taken with no window at all. A wake tail, followed by silence rather
than speech, simply never triggers.

That property is what let this ship before the confirming measurement: it can fail by being
useless, never by eating a word. **A guard that degrades to a no-op is worth far more than one
that degrades to data loss**, and it is often available for the cost of moving a condition.

## L49 - A partial sweep is how a green suite hides a break

`verify_credible.py` was broken by Stage 18 and stayed broken through it, because I ran fifteen
harnesses and not that one. Its stub `Engine.ask(self, text)` did not accept the `truncated`
keyword the stage had added. Nothing else touched it, so nothing else noticed.

It surfaced a day later only because the next stage happened to touch `_capture` too.

Both stubs are now `**_`-tolerant, which is right - a harness about what `credible.assess` does
with numbers should not break when the signature above it grows. But the real lesson is the
sweep: **when a change alters a signature that harnesses stub, run all of them, not the ones
that seem related.** The related ones are exactly the ones that already stub it correctly.
