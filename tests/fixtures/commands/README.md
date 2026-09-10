# Command fixtures — LB's actual voice

Recorded live on the Pi (C270 webcam, 16kHz mono) on 2026-08-12 by running with
`--save-captures`, then promoted here. **The filename encodes the intent the router must
reach, not the words spoken** — which is the point.

| file | said | heard by tiny.en | routes to |
|---|---|---|---|
| `time-01.wav` | "what time is it" | "What time is it?" | `time` |
| `time-02.wav` | "what time is it" | "What time is it?" | `time` |
| `time-03.wav` | "what time is it" | "with time, is it?" | `time` |
| `time-04.wav` | "what time is it" | "with time is it." | `time` |
| `date-01.wav` | "what day is it today" | "What day is it today?" | `date` |

Every accuracy number before these — including the model choice in D29 — was measured on
**synthesised** speech, because there was nothing else. These are the first recordings of a real
person giving real commands, and they are what the wake fixtures are to `verify_wake.py`.

Two things they capture that TTS never would:

- **`tiny.en` hears "what" as "with" about half the time**, at least at this distance from this
  microphone. It does not matter for Tier 0, because the router matches the keyword `time` — but
  it will matter in Phase 2, when a language model reads the transcript instead of a keyword
  table. That is why the fixtures assert the **intent**, not the transcript: the transcript is
  allowed to be imperfect, the answer is not.
- **The tail of the wake phrase bleeds into the front of the capture.** The wake word does not
  fire the instant you stop saying it, so `run_wake.py` hands the turn the preceding 0.4s to
  avoid clipping the first word — and that lead-in sometimes contains "…Odd Ball".

Recording more is one flag: `--save-captures captures`, then copy the good ones here. Anything
that exposes a new failure is worth keeping even if it fails, in the spirit of
`../wake/known-limits/`.
