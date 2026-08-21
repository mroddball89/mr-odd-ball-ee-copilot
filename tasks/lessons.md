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
