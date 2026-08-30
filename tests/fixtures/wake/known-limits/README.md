# Known limits — scored every run, not enforced

These four clips document measured, **accepted** limitations of `models/hey_mr_odd_ball.onnx`
(see D27). `verify_wake.py --fixtures` scores and prints them but does not fail on them, so the
harness stays green and a genuinely new regression is still visible.

| clip | measured | why it is here |
|---|---|---|
| `far-01.wav`, `far-02.wav` | 0.3432, 0.0023 — do not fire | Far-field does not work. Missed in both recording sessions, so it is the model and the microphone, not a bad take. **This is a desk-range assistant.** A retrain does not fix it. |
| `mister-odd-ball-01.wav` | **0.9912 — fires** | "Mister Odd Ball" without the "hey". Higher than the best true positive (0.9386), so no threshold separates it — it has to be trained out. Round 2 is built and staged in `training/`; LB chose not to run it. |
| `hey-mr-on-call-01.wav` | **0.8214 — fires** | Phonetically adjacent, unrelated meaning. Same story. |

Move a clip back into `positive/` or `negative/` to make it binding again — for example after
running the round-2 retrain, which targets the bottom two directly.
