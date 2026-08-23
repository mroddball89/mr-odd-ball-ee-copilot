#!/usr/bin/env python3
"""
Module:  gesture_control.py
Purpose: Approve an action with your hand instead of the keyboard. Camera in, gesture out.
Author:  LB
Date:    2026-08-21 (ported to the mediapipe Tasks API 2026-08-22)

    python tools/gesture_control.py --fetch-model    # once, ~7.8 MB
    python tools/gesture_control.py                  # one shot: what does the camera see
    python tools/gesture_control.py --watch          # keep reading, ctrl-C to stop
    python tools/gesture_control.py --backend        # which mediapipe API is in use

    python tools/live_test_gestures.py               # the camera window, for tuning these

Returns exactly one of `TAP`, `DRAG`, `FLICK`, `PINCH`, `CLAW`, `POINT`, `FIST`, `THUMBS_UP`,
`THUMBS_DOWN`, `OPEN_PALM`, `NONE` or `NO_CAMERA`. Wired into the terminal security prompts in
`agents/os_agent.py` and `agents/web_agent.py`: **a thumbs up is a yes, a thumbs down is a no,
and every other token falls through to the keyboard.**

`TAP`, `DRAG` and `FLICK` are movements, so they need consecutive frames. `get_gesture()` reads
ONE frame from a camera it then closes, in a child process that then exits (see `_ask_sidecar`),
so there is no previous frame for a hand to have moved from: **the one-shot path returns poses
only.** Movements come from `classify_stream()`, the continuous path, which
`tools/live_test_gestures.py` and any future always-on loop use. They are named in `_VALID`
anyway, because a whitelist that omitted them would silently turn a future streaming sidecar's
honest answer into `NO_CAMERA`.

## Two mediapipe APIs and a sidecar, because the Pi forced all three (D14, D15)

mediapipe **1.x removed `mp.solutions`** — the legacy Solutions API — and replaced it with
`mediapipe.tasks`. It also stopped building per-interpreter wheels: 1.0.1 ships one
`py3-none-manylinux_2_28_aarch64.whl` that installs on **any** Python 3.

It installs on the Pi's 3.13.5 and then **does not run there.** Measured on the box: every
`vision` task — `HandLandmarker` and `GestureRecognizer` alike — is SIGKILLed the moment the
XNNPACK delegate comes up, with no OOM, no throttling and 6.4 GB free. mediapipe wraps that
construction in `CallWithCoreDumpProtection`, which converts a fatal signal into SIGKILL to
suppress the core dump, so the real fault is masked and exit 137 is all you get.

| | APIs present | aarch64 wheel | installs on 3.13 | **runs on this Pi** |
|---|---|---|---|---|
| 0.10.18 | `solutions` **and** `tasks` | cp39–cp312 | no | **yes** — both of them |
| 0.10.20+ | both | none at all | — | — |
| 1.0.1 | `tasks` only | `py3-none` | yes | **no** — SIGKILL |

Note what the failing variable is: **the mediapipe version, not the API.** 0.10.18 carries both
`mp.solutions` and `mp.tasks`, and on this Pi both work. It is 1.0.1 specifically that dies.

So there are three paths and this module supports all of them, in this order:

1. **Tasks in-process** — mediapipe 1.x. Correct wherever it runs; not this Pi.
2. **Solutions in-process** — mediapipe 0.10.18, on a Python <= 3.12 interpreter.
3. **A sidecar** — `ODDBALL_GESTURE_PYTHON` names a *second* interpreter that has a working
   mediapipe, and this module shells out to it for one token. That is what lets the Pi keep
   its 3.13 venv for whisper, piper and ctranslate2 while gesture detection runs on a small
   3.12 venv beside it, instead of rebuilding 1.9 GB to move one leaf feature.

`_classify()` is shared by 1 and 2 — both APIs hand back the same 21 normalised landmarks in
the same order, so the decision logic with the safety property in it exists once. The sidecar
runs this same file, so it shares that logic too, by being it.

The Tasks path needs a model file, `models/hand_landmarker.task` (7.8 MB). It is NOT fetched
automatically at approval time — a security prompt is the last place to start a download. It
is gitignored and re-downloadable, exactly like the whisper models; `--fetch-model` gets it.

## Pi budget — measured, and it is 2.2 seconds

One approval, end to end from the assistant's venv, median of 10 trials on the Pi:

    interpreter start          22 ms
    import mediapipe        1,009 ms   <-- paid per approval, because of the child process
    build HandLandmarker       55 ms
    open camera               204 ms
    4 warmup frames           602 ms   <-- 150 ms each; the webcam gives ~6.6fps, not 15
    inference                  47 ms
    ------------------------------------
    TOTAL                   2,217 ms   (min 2,197, max 2,271 — very tight)

**That table is the 2026-08-21 measurement and WARMUP_FRAMES is now 5**, so expect ~2,367 ms
until it is re-measured. The row is left at 4 rather than quietly edited to 5: the numbers
beside it were measured together, and a table with one value swapped by hand is a table that
no longer describes any single run.

**Only 102 ms of that is detection.** The rest is a whole Python interpreter and a mediapipe
import, thrown away and rebuilt every time, because the work cannot happen in this process.
`media/charts/gesture-approval-latency.svg` plots it; the CSVs are beside it.

The obvious improvement is a **persistent worker** — pay the 1.0 s import once and keep a pipe
open — which would bring an approval to roughly 850 ms. Not done: it turns a subprocess call
into a lifecycle to manage, and 2.2 s at a prompt that already stops to ask a question is
tolerable. Tracked in `tasks/todo.md`.

`WARMUP_FRAMES` is deliberately NOT tuned down to save the 602 ms. The first frames off a
freshly opened camera are auto-exposure garbage and a black frame reliably detects no hand —
so cutting it is a trade of reliability for latency, and there is no measurement of detection
rate versus warmup count to make that trade on. Guessing here would be the exact mistake D14
is about.

It went the other way on 2026-08-22: **4 -> 5**, with `MIN_DETECTION_CONFIDENCE` 0.6 -> 0.5,
after LB reported thumbs-up going undetected. Both changes point at the same suspected cause —
an underexposed frame — and both are still guesses, for the reason above: there is no
detection-rate measurement to tune against. What makes them acceptable is direction. A missed
thumbs-up costs one retry and falls back to the keyboard; neither change can turn a non-approval
into an approval, because that decision is `_classify()`, which is pure geometry.

**The honest next step is a measurement, not another nudge** — detection rate against warmup
count and confidence, over a set of saved frames. Tracked in `tasks/todo.md`.

The camera is NOT held open between calls. An approval happens a few times an hour and a held
`VideoCapture` is a device nobody else can use.

## The thumbs-up test is stricter than the obvious one, and it has to be

The obvious test is "thumb tip is above the index knuckle and above the wrist". **An open palm
passes that test** — with your hand up and open, the thumb is above both. So the obvious test
turns a wave into an approval, and what it approves is a shell command on `os_agent`'s path.

So THUMBS_UP additionally requires the other four fingers to be **curled**: each tip below its
own PIP joint. Open palm is checked first, and the two are mutually exclusive by construction.
A gesture that fails both is `NONE`, which declines to the keyboard — the safe direction.

Image coordinates run y-DOWN, so "above" is a smaller y. Every comparison below is in
normalised landmark space (0..1 of the frame), so it is resolution-independent.

#
#
 
T
h
e
 
v
o
c
a
b
u
l
a
r
y
 
i
s
 
j
a
r
e
d
r
h
o
d
/
b
a
r
e
h
a
n
d
s
'
,
 
a
n
d
 
s
o
 
i
s
 
t
h
e
 
g
e
o
m
e
t
r
y




*
*
2
0
2
6
-
0
8
-
2
3
.
*
*
 
T
h
e
 
s
e
t
 
h
e
r
e
 
i
s
 
`
b
a
r
e
h
a
n
d
s
`
'
 
g
e
s
t
u
r
e
 
v
o
c
a
b
u
l
a
r
y
,
 
m
i
n
u
s
 
i
t
s
 
c
l
a
p
,
 
p
l
u
s
 
a
 
t
h
u
m
b
s


u
p
 
a
n
d
 
a
 
t
h
u
m
b
s
 
d
o
w
n
.
 
N
o
t
 
j
u
s
t
 
t
h
e
 
n
a
m
e
s
 
—
 
t
h
e
 
*
m
e
a
s
u
r
e
m
e
n
t
s
*
,
 
p
o
r
t
e
d
 
f
r
o
m
 
`
s
t
a
g
e
.
h
t
m
l
`
,
 
w
h
o
s
e


c
o
m
m
e
n
t
s
 
c
a
r
r
y
 
t
h
e
 
c
o
r
p
u
s
 
e
a
c
h
 
c
o
n
s
t
a
n
t
 
w
a
s
 
f
i
t
t
e
d
 
a
g
a
i
n
s
t
 
(
"
v
3
.
9
.
3
2
 
—
 
h
i
s
 
f
i
n
a
l
 
p
i
n
c
h
 
s
a
m
p
l
e
"
,


"
f
i
t
t
e
d
 
f
r
o
m
 
h
i
s
 
p
i
n
c
h
 
c
o
r
p
u
s
,
 
3
 
c
o
r
r
e
c
t
 
/
 
3
 
f
i
s
t
s
"
)
.
 
T
h
e
 
c
o
n
s
t
a
n
t
s
 
i
n
 
t
h
i
s
 
f
i
l
e
 
b
e
f
o
r
e
 
t
h
a
t


d
a
t
e
 
w
e
r
e
 
d
e
r
i
v
e
d
 
f
r
o
m
 
g
e
o
m
e
t
r
y
 
a
n
d
 
h
o
n
e
s
t
l
y
 
l
a
b
e
l
l
e
d
 
a
s
 
g
u
e
s
s
e
s
.
 
T
h
e
s
e
 
a
r
e
 
n
o
t
 
g
u
e
s
s
e
s
,
 
a
n
d


t
h
a
t
 
i
s
 
t
h
e
 
e
n
t
i
r
e
 
r
e
a
s
o
n
 
t
o
 
p
o
r
t
 
r
a
t
h
e
r
 
t
h
a
n
 
r
e
i
n
v
e
n
t
.




 
 
 
 
p
o
s
e
s
 
 
 
 
 
 
 
 
O
P
E
N
_
P
A
L
M
 
 
F
I
S
T
 
 
P
I
N
C
H
 
 
C
L
A
W
 
 
P
O
I
N
T
 
 
T
H
U
M
B
S
_
U
P
 
 
T
H
U
M
B
S
_
D
O
W
N


 
 
 
 
m
o
v
e
m
e
n
t
s
 
 
 
 
T
A
P
 
 
D
R
A
G
 
 
F
L
I
C
K




#
#
 
E
v
e
r
y
 
f
i
n
g
e
r
 
t
e
s
t
 
i
s
 
n
o
w
 
m
e
a
s
u
r
e
d
 
a
g
a
i
n
s
t
 
t
h
e
 
H
A
N
D
,
 
n
o
t
 
a
g
a
i
n
s
t
 
t
h
e
 
i
m
a
g
e




T
h
i
s
 
i
s
 
t
h
e
 
c
h
a
n
g
e
 
t
h
a
t
 
m
a
t
t
e
r
s
 
a
n
d
 
i
t
 
i
s
 
w
h
y
 
r
e
c
o
g
n
i
t
i
o
n
 
w
a
s
 
u
n
r
e
l
i
a
b
l
e
.




T
h
e
 
o
l
d
 
t
e
s
t
s
 
w
e
r
e
 
i
m
a
g
e
-
c
o
o
r
d
i
n
a
t
e
 
c
o
m
p
a
r
i
s
o
n
s
 
—
 
`
t
i
p
.
y
 
<
 
p
i
p
.
y
`
 
f
o
r
 
"
e
x
t
e
n
d
e
d
"
,
 
`
t
i
p
.
y
`


a
g
a
i
n
s
t
 
`
m
c
p
.
y
`
 
f
o
r
 
t
h
e
 
c
l
a
w
.
 
T
h
a
t
 
i
s
 
a
n
 
a
n
g
l
e
 
m
e
a
s
u
r
e
d
 
a
g
a
i
n
s
t
 
t
h
e
 
*
*
f
r
a
m
e
*
*
,
 
a
n
d
 
i
t
 
i
s
 
o
n
l
y


c
o
r
r
e
c
t
 
w
h
i
l
e
 
t
h
e
 
h
a
n
d
 
i
s
 
h
e
l
d
 
u
p
r
i
g
h
t
.
 
*
*
A
 
t
h
u
m
b
s
 
u
p
 
i
s
 
n
a
t
u
r
a
l
l
y
 
m
a
d
e
 
w
i
t
h
 
t
h
e
 
p
a
l
m
 
t
u
r
n
e
d


s
i
d
e
-
o
n
*
*
,
 
a
n
d
 
w
h
e
n
 
i
t
 
i
s
,
 
t
h
e
 
f
i
n
g
e
r
s
 
c
u
r
l
 
s
i
d
e
w
a
y
s
 
r
a
t
h
e
r
 
t
h
a
n
 
d
o
w
n
w
a
r
d
:
 
t
h
e
i
r
 
t
i
p
s
 
l
a
n
d
 
a
t


r
o
u
g
h
l
y
 
t
h
e
i
r
 
P
I
P
s
'
 
h
e
i
g
h
t
,
 
`
t
i
p
.
y
 
<
 
p
i
p
.
y
`
 
s
t
a
r
t
s
 
c
o
m
i
n
g
 
b
a
c
k
 
t
r
u
e
 
f
o
r
 
a
 
f
u
l
l
y
 
c
u
r
l
e
d
 
f
i
n
g
e
r
,


a
n
d
 
t
h
e
 
"
a
l
l
 
f
o
u
r
 
f
i
n
g
e
r
s
 
c
u
r
l
e
d
"
 
c
l
a
u
s
e
 
f
a
i
l
s
.




L
B
 
p
h
o
t
o
g
r
a
p
h
e
d
 
e
x
a
c
t
l
y
 
t
h
a
t
 
o
n
 
2
0
2
6
-
0
8
-
2
3
 
a
t
 
0
9
:
1
1
 
—
 
a
 
c
l
e
a
n
 
t
h
u
m
b
s
 
u
p
,
 
e
v
e
r
y
 
l
a
n
d
m
a
r
k


c
o
r
r
e
c
t
l
y
 
p
l
a
c
e
d
,
 
c
l
a
s
s
i
f
i
e
d
 
`
N
O
N
E
`
.
 
`
m
e
d
i
a
/
c
a
p
t
u
r
e
s
/
g
e
s
t
u
r
e
-
n
o
n
e
-
2
0
2
6
0
8
2
3
-
1
7
1
1
2
0
.
p
n
g
`
.
 
N
o
t
h
i
n
g


w
a
s
 
w
r
o
n
g
 
w
i
t
h
 
t
h
e
 
d
e
t
e
c
t
i
o
n
;
 
t
h
e
 
q
u
e
s
t
i
o
n
 
b
e
i
n
g
 
a
s
k
e
d
 
o
f
 
i
t
 
w
a
s
 
w
r
o
n
g
.




T
w
o
 
r
o
t
a
t
i
o
n
-
i
n
v
a
r
i
a
n
t
 
p
r
i
m
i
t
i
v
e
s
 
r
e
p
l
a
c
e
 
a
l
l
 
o
f
 
i
t
,
 
b
o
t
h
 
f
r
o
m
 
`
b
a
r
e
h
a
n
d
s
`
:




*
 
*
*
`
_
c
u
r
l
(
)
`
*
*
 
—
 
t
h
e
 
d
o
t
 
p
r
o
d
u
c
t
 
o
f
 
a
 
f
i
n
g
e
r
'
s
 
p
r
o
x
i
m
a
l
 
s
e
g
m
e
n
t
 
w
i
t
h
 
i
t
s
 
d
i
s
t
a
l
 
o
n
e
.
 
S
t
r
a
i
g
h
t


 
 
i
s
 
~
+
0
.
9
,
 
h
o
o
k
e
d
 
g
o
e
s
 
n
e
g
a
t
i
v
e
,
 
*
i
n
 
a
n
y
 
c
a
m
e
r
a
 
o
r
i
e
n
t
a
t
i
o
n
*
.
 
I
t
s
 
o
w
n
 
n
o
t
e
 
o
n
 
w
h
y
 
a
n
 
a
n
g
l
e
 
a
n
d


 
 
n
o
t
 
a
 
d
i
s
t
a
n
c
e
:
 
"
m
o
n
o
c
u
l
a
r
 
z
 
i
s
 
t
o
o
 
w
e
a
k
 
f
o
r
 
d
i
s
t
a
n
c
e
s
 
.
.
.
 
a
n
g
l
e
s
 
s
u
r
v
i
v
e
 
t
h
e
 
f
o
r
e
s
h
o
r
t
e
n
i
n
g


 
 
t
h
a
t
 
l
i
e
s
 
a
b
o
u
t
 
l
e
n
g
t
h
s
.
"


*
 
*
*
`
_
r
e
a
c
h
(
)
`
*
*
 
—
 
t
i
p
-
t
o
-
w
r
i
s
t
 
o
v
e
r
 
k
n
u
c
k
l
e
-
t
o
-
w
r
i
s
t
.
 
B
o
t
h
 
m
e
a
s
u
r
e
d
 
f
r
o
m
 
t
h
e
 
s
a
m
e
 
p
o
i
n
t
,
 
s
o


 
 
r
o
t
a
t
i
n
g
 
t
h
e
 
h
a
n
d
 
c
a
n
n
o
t
 
c
h
a
n
g
e
 
t
h
e
 
r
a
t
i
o
.




T
h
e
 
o
n
e
 
t
e
s
t
 
s
t
i
l
l
 
i
n
 
i
m
a
g
e
 
c
o
o
r
d
i
n
a
t
e
s
 
i
s
 
`
_
t
h
u
m
b
_
d
i
r
e
c
t
i
o
n
(
)
`
,
 
a
n
d
 
t
h
a
t
 
i
s
 
d
e
l
i
b
e
r
a
t
e
:
 
u
p
 
a
n
d


d
o
w
n
 
a
r
e
 
f
a
c
t
s
 
a
b
o
u
t
 
g
r
a
v
i
t
y
,
 
n
o
t
 
a
b
o
u
t
 
t
h
e
 
h
a
n
d
.
 
E
v
e
r
y
t
h
i
n
g
 
e
l
s
e
 
i
s
 
h
a
n
d
-
r
e
l
a
t
i
v
e
 
p
r
e
c
i
s
e
l
y
 
s
o


t
h
a
t
 
t
h
i
s
 
o
n
e
 
c
a
n
 
b
e
 
w
o
r
l
d
-
r
e
l
a
t
i
v
e
 
a
n
d
 
s
t
i
l
l
 
m
e
a
n
 
s
o
m
e
t
h
i
n
g
.




#
#
 
T
h
r
e
e
 
o
f
 
t
h
e
s
e
 
a
r
e
 
t
e
s
t
e
d
 
B
E
F
O
R
E
 
t
h
e
 
t
h
u
m
b
,
 
a
n
d
 
t
h
a
t
 
i
s
 
a
 
s
a
f
e
t
y
 
p
r
o
p
e
r
t
y




`
T
H
U
M
B
S
_
U
P
`
 
i
s
 
w
h
a
t
 
`
a
g
e
n
t
s
/
o
s
_
a
g
e
n
t
.
p
y
`
 
r
u
n
s
 
a
 
s
h
e
l
l
 
c
o
m
m
a
n
d
 
o
n
.
 
T
h
e
 
p
r
e
-
2
0
2
6
-
0
8
-
2
3
 
c
l
a
s
s
i
f
i
e
r


r
e
q
u
i
r
e
d
 
t
h
e
 
f
o
u
r
 
f
i
n
g
e
r
s
 
t
o
 
b
e
 
u
n
-
e
x
t
e
n
d
e
d
,
 
a
n
d
 
*
*
a
 
c
l
a
w
 
a
n
d
 
a
 
p
i
n
c
h
 
b
o
t
h
 
s
a
t
i
s
f
i
e
d
 
t
h
a
t
*
*
 
—
 
s
o


b
o
t
h
 
c
l
a
s
s
i
f
i
e
d
 
a
s
 
`
T
H
U
M
B
S
_
U
P
`
 
a
n
d
 
a
p
p
r
o
v
e
d
 
s
h
e
l
l
 
c
o
m
m
a
n
d
s
.
 
`
t
o
o
l
s
/
v
e
r
i
f
y
_
g
e
s
t
u
r
e
s
.
p
y
 
-
-
p
r
o
b
e
`


s
t
i
l
l
 
r
e
p
r
o
d
u
c
e
s
 
i
t
.




T
h
e
 
v
o
c
a
b
u
l
a
r
y
 
i
s
 
b
i
g
g
e
r
 
n
o
w
,
 
s
o
 
t
h
e
 
g
u
a
r
d
 
i
s
 
s
t
a
t
e
d
 
a
s
 
a
n
 
i
n
v
a
r
i
a
n
t
 
r
a
t
h
e
r
 
t
h
a
n
 
a
s
 
a
n
 
o
r
d
e
r
i
n
g


a
c
c
i
d
e
n
t
:
 
*
*
o
n
l
y
 
a
 
h
a
n
d
 
w
i
t
h
 
a
l
l
 
f
o
u
r
 
f
i
n
g
e
r
s
 
c
l
o
s
e
d
 
c
a
n
 
r
e
a
c
h
 
t
h
e
 
t
h
u
m
b
 
b
r
a
n
c
h
e
s
 
a
t
 
a
l
l
*
*
,
 
a
n
d


w
h
i
c
h
 
o
f
 
`
T
H
U
M
B
S
_
U
P
`
 
/
 
`
T
H
U
M
B
S
_
D
O
W
N
`
 
/
 
`
F
I
S
T
`
 
i
t
 
b
e
c
o
m
e
s
 
i
s
 
d
e
c
i
d
e
d
 
b
y
 
t
h
e
 
t
h
u
m
b
 
a
l
o
n
e
.
 
A
 
c
l
a
w


h
a
s
 
i
t
s
 
f
i
n
g
e
r
s
 
h
o
o
k
e
d
 
b
u
t
 
r
e
a
c
h
i
n
g
 
(
n
o
t
 
c
l
o
s
e
d
)
;
 
a
 
p
i
n
c
h
 
h
a
s
 
t
h
r
e
e
 
f
i
n
g
e
r
s
 
o
u
t
.
 
N
e
i
t
h
e
r
 
c
a
n


a
r
r
i
v
e
.
 
`
v
e
r
i
f
y
_
g
e
s
t
u
r
e
s
.
p
y
`
 
a
s
s
e
r
t
s
 
t
h
i
s
 
e
x
h
a
u
s
t
i
v
e
l
y
 
r
a
t
h
e
r
 
t
h
a
n
 
t
r
u
s
t
i
n
g
 
t
h
e
 
r
e
a
d
i
n
g
.




#
#
 
D
i
s
t
a
n
c
e
s
 
a
r
e
 
m
e
a
s
u
r
e
d
 
i
n
 
p
a
l
m
-
s
p
a
n
s
,
 
n
o
t
 
i
n
 
f
r
a
m
e
-
w
i
d
t
h
s




`
P
I
N
C
H
`
 
i
s
 
"
l
a
n
d
m
a
r
k
 
4
 
t
o
u
c
h
e
s
 
l
a
n
d
m
a
r
k
 
8
"
,
 
a
n
d
 
t
h
e
 
o
b
v
i
o
u
s
 
i
m
p
l
e
m
e
n
t
a
t
i
o
n
 
c
o
m
p
a
r
e
s
 
t
h
e
i
r


d
i
s
t
a
n
c
e
 
t
o
 
a
 
c
o
n
s
t
a
n
t
.
 
T
h
a
t
 
c
o
n
s
t
a
n
t
 
i
s
 
c
o
r
r
e
c
t
 
a
t
 
e
x
a
c
t
l
y
 
o
n
e
 
c
a
m
e
r
a
 
d
i
s
t
a
n
c
e
:
 
l
a
n
d
m
a
r
k
s
 
a
r
e


n
o
r
m
a
l
i
s
e
d
 
t
o
 
t
h
e
 
F
R
A
M
E
,
 
s
o
 
t
h
e
 
s
a
m
e
 
h
a
n
d
 
a
t
 
8
0
 
c
m
 
i
s
 
h
a
l
f
 
t
h
e
 
s
i
z
e
 
i
t
 
i
s
 
a
t
 
4
0
 
c
m
.




E
v
e
r
y
 
d
i
s
t
a
n
c
e
 
h
e
r
e
 
i
s
 
d
i
v
i
d
e
d
 
b
y
 
`
_
h
a
n
d
_
s
c
a
l
e
(
)
`
 
—
 
w
r
i
s
t
 
t
o
 
m
i
d
d
l
e
 
k
n
u
c
k
l
e
,
 
`
b
a
r
e
h
a
n
d
s
`
'
 
`
s
p
a
n
`


—
 
t
h
e
 
o
n
e
 
l
e
n
g
t
h
 
o
n
 
a
 
h
a
n
d
 
t
h
a
t
 
d
o
e
s
 
n
o
t
 
c
h
a
n
g
e
 
w
h
e
n
 
t
h
e
 
f
i
n
g
e
r
s
 
m
o
v
e
.
 
T
h
r
e
s
h
o
l
d
s
 
a
r
e
 
i
n


p
a
l
m
-
s
p
a
n
s
 
a
n
d
 
h
o
l
d
 
a
t
 
a
n
y
 
d
i
s
t
a
n
c
e
 
a
n
d
 
o
n
 
a
n
y
 
s
i
z
e
 
o
f
 
h
a
n
d
.
 
S
p
e
e
d
s
 
l
i
k
e
w
i
s
e
,
 
i
n
 
s
p
a
n
s
 
p
e
r


s
e
c
o
n
d
 
r
a
t
h
e
r
 
t
h
a
n
 
f
r
a
m
e
-
w
i
d
t
h
s
 
p
e
r
 
s
e
c
o
n
d
.




## What this is not

It is not a second authority. The blocklist in `tools/os_controller.py` runs regardless of how
approval arrived, and the exact command is still printed before the question is asked. A
gesture replaces the keystroke, not the review.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections import deque
from pathlib import Path

LOG = logging.getLogger("oddball.gesture")

__all__ = ["GestureRecognizer", "get_gesture", "gesture_approves",
           "approve_by_gesture_or_keyboard", "MODEL_PATH", "MODEL_URL", "fetch_model",
           "sidecar_python"]

REPO_ROOT = Path(__file__).resolve().parents[1]

# The Tasks-API hand landmarker. Google's published float16 build — the same file the
# mediapipe docs point at, pinned to revision 1 of the URL so a silent upstream reroll cannot
# change what the gate is running.
MODEL_PATH = REPO_ROOT / "models" / "hand_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")

# Pi camera budget. 640x480 is what mediapipe wants anyway — it downscales internally — and
# the fps cap keeps the driver from negotiating a 30fps mode we immediately throw away.
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FRAME_FPS = 15

# 2 since 2026-08-23. barehands scales objects with two hands, and a detector capped at one can
# never see a second. It is not free — mediapipe runs the landmark model once PER HAND, so a
# frame with two hands costs about twice the 47 ms inference measured in the budget above.
# That is ~2.26 s instead of ~2.22 s for an approval, which is inside the noise of that table.
#
# It does not weaken the gate. Hands are classified INDIVIDUALLY and then resolved by
# `_PRIORITY`, so a second hand can only ever add a gesture of its own — it cannot combine
# with the first to manufacture a THUMBS_UP that neither hand is making.
MAX_HANDS = 2

# The first frame off a freshly opened camera is auto-exposure garbage: on the Pi's module it
# is usually near-black, and a black frame reliably detects no hand. Pull and discard a few.
#
# 5 as of 2026-08-22, up from 4. The measured cost is ~150 ms per frame on this webcam (it
# gives ~6.6 fps, not the 15 requested), so this buys a little more auto-exposure settle time
# for 150 ms of the 2.2 s budget. Raised alongside the confidence drop below, because both
# failures LB reported — "it didn't see my thumb" — are consistent with an underexposed frame.
WARMUP_FRAMES = 5

# How sure mediapipe must be that it is looking at a hand at all.
#
# 0.5 as of 2026-08-22, down from 0.6, because LB reported thumbs-up going undetected. It is
# ONE constant now rather than a literal repeated in each API branch — two numbers that must
# agree is one number somebody edits.
#
# Lowering this is safe in a way that is worth being explicit about, because "make approval
# more forgiving" sounds like the opposite: this threshold governs *is there a hand in frame*,
# NOT *is it a thumbs up*. The gesture decision is `_classify()`, which is pure geometry with
# no confidence in it, and it still demands the other four fingers be curled — an open palm
# is not an approval at any detection confidence. So this makes the hand easier to FIND and
# does not make approval easier to GET.
MIN_DETECTION_CONFIDENCE = 0.5
MIN_TRACKING_CONFIDENCE = 0.5

# Hand landmark indices, named. `landmarks[8]` in a boolean expression is how the open-palm
# and thumbs-up tests come to look identical to a reviewer. Identical in both mediapipe APIs.
WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_MCP = 9      # the far end of `_hand_scale()`; see the palm-lengths section above
# (mcp, pip, tip) for each of the four fingers that must curl for a thumbs up.
FINGERS = (
    (5, 6, 8),      # index
    (9, 10, 12),    # middle
    (13, 14, 16),   # ring
    (17, 18, 20),   # pinky
)
# The knuckle row, wrist included: the five points that move together as one rigid palm no
# matter what the fingers are doing. Their mean is the palm centroid, which is what `FLICK`
# tracks — a fingertip would add finger motion to hand motion and read a wave as a flick.
PALM = (WRIST, 5, 9, 13, 17)

# The four finger chains as (mcp, pip, dip, tip). `FINGERS` above is the (mcp, pip, tip)
# form the y-comparison era used; this is the full chain, because the curl test needs the
# DIP joint to see which way the last segment points.
CHAINS = (
    (5, 6, 7, 8),        # index
    (9, 10, 11, 12),     # middle
    (13, 14, 15, 16),    # ring
    (17, 18, 19, 20),    # pinky
)

# --- thresholds, ported from jaredrhod/barehands 2026-08-23 ------------------------------
#
# These are NOT geometry-derived guesses any more. Every number below is the value that
# project arrived at by fitting against a corpus of real hands — its source carries the
# version history in comments ("v3.9.32 — his final pinch sample", "fitted from his pinch
# corpus, 3 correct / 3 fists"). Where a number here differs from barehands it is noted.
#
# Taking measured constants over invented ones is the whole reason to port rather than
# reinvent: the previous set of thresholds in this file were honest guesses, and they are
# what LB was fighting.

# EXTENDED: a finger counts as out when its tip is 1.45x further from the wrist than its own
# knuckle is. A ratio of distances FROM A COMMON POINT, so it does not care which way the
# hand is rotated — unlike the `tip.y < pip.y` test this replaces.
EXTEND_REACH = 1.45

# CURL: the dot product of a finger's proximal segment (mcp->pip) with its distal one
# (dip->tip), in 3D. An extended finger's segments are aligned, ~+0.9. A hooked finger's
# distal segment points back against the proximal one, going negative. barehands' note on
# why this and not distances: "monocular z is too weak for distances... angles survive the
# foreshortening that lies about lengths."
CURL_FOLDED = 0.35             # below this the finger is folded
CURL_STRAIGHT = 0.60           # above this it is straight

# PINCH: thumb-index gap over palm span. Two regimes, because a palm turned side-on to the
# camera reads a wider gap for the same real touch. barehands v3.9.32 / v3.9.24.
PINCH_MAX_RATIO = 0.32         # frontal palm (aspect < 2.0)
PINCH_MAX_RATIO_PROFILE = 0.38 # rotated palm

# THE CONTRAST LAW (barehands v3.8.2). A closed fist also puts the thumb on the index tip,
# so the gap alone cannot tell a pinch from a clench. The tell is CONTRAST: in a real pinch
# the index curls IN to the thumb while middle/ring/pinky stay OUT. Measured there at >= 0.28
# for every correct sample and <= 0.07 for every impostor; cut at 0.18 with a back-arch floor
# of 1.30 as the fist wall.
PINCH_CONTRAST = 0.18
PINCH_BACK_ARCH = 1.30

# THE CLAW. Its signature is a deliberately WIDE mouth — the thumb-index C-gap — with the
# index and middle folded. barehands walked this floor up over several rounds (0.58 -> 0.72
# -> 0.80) because a half-closed hand kept impersonating it; 0.80 is where it landed, with
# the note "a claw is intentional. Bigger distance between thumb and index."
CLAW_MOUTH_MIN = 0.80
CLAW_MOUTH_MAX = 1.45
CLAW_INDEX_CURL = 0.60         # c8  — index must fold
CLAW_MIDDLE_CURL = 0.35        # c12 — the middle finger leads; always folded in a real claw

# ...and the fingers must still be REACHING. barehands keeps "distance ratios ... as loose
# sanity rails around his measured real-claw envelope"; this is that rail, and it is the one
# thing standing between a claw and a fist. Both hook every finger past the curl bars above,
# so curl alone cannot separate them:
#
#     claw   fingers hooked, tips still out past the knuckles   reach ~1.34
#     fist   fingers folded INTO the palm, tips behind them     reach ~1.17
#
# Without this rail a closed fist with the thumb held clear reads as a claw — measured, in the
# first run of `verify_gestures.py --dump` after the port.
CLAW_MIN_REACH = 1.28

# THUMB direction, for THUMBS_UP / THUMBS_DOWN. This is the ONE test that is deliberately
# still in image coordinates: up and down are facts about the world, not about the hand, so
# rotating the hand must NOT rotate the answer. Measured as a fraction of palm span so it is
# distance-independent, and required in both directions so a level thumb is neither.
THUMB_RISE = 0.55              # thumb tip this far above the wrist, in palm spans
THUMB_DROP = 0.55              # ...or this far below it

# SANITY (barehands v3.9.30). Palm span over palm width. A hand resting on a face read 7-8
# there — the knuckle row collapsed, a geometrically impossible hand — while no real hand in
# a two-day corpus exceeded 5.5. Past this the tracker is guessing, and a guess must not be
# allowed to produce a gesture at all, least of all at a security prompt.
ASPECT_GARBAGE = 6.0
ASPECT_FRONTAL = 2.0           # below this the palm faces the camera; picks the pinch regime

# --- the movement layer ------------------------------------------------------------------
#
# "Every gesture is a movement, not a pose." — barehands, The Gestures.md
#
# TAP, DRAG and FLICK are not hand shapes. They are things that happen to a PINCH over time,
# which is why the previous FLICK — an open palm swiped across frame — could never be told
# apart from a stationary open palm and kept reading as OPEN_PALM. It was the wrong gesture.
#
# Speeds are in palm-spans per second, not frame-widths: the same physical hand motion covers
# more of the frame close up, and a throw is a property of the hand, not of the lens.
TAP_MAX_S = 0.40               # a pinch shorter than this, that went nowhere, is a tap
TAP_MAX_TRAVEL = 0.45          # ...in palm spans
DRAG_MIN_TRAVEL = 0.60         # a pinch that has moved this far is a drag
FLICK_MIN_SPEED = 2.2          # release faster than this throws
FLICK_WINDOW_S = 0.25          # the window the release speed is measured over
TRACK_HISTORY = 12             # samples kept per hand; ~1.2 s at this webcam's ~9.7 fps

# Set ODDBALL_GESTURE=0 to keep the camera shut and go straight to the keyboard. Wanted on the
# Windows authoring box, where `--text` mode is used for debugging agents and opening a webcam
# on every approval prompt is friction with no purpose.
_DISABLED = os.environ.get("ODDBALL_GESTURE", "1").strip().lower() in ("0", "false", "no", "off")

# A second interpreter that has a working mediapipe, for when THIS one does not (see the
# header). Named by env, or found at the conventional path below, which is where
# `tools/install_gesture_venv.sh` puts it.
_SIDECAR_DEFAULT = REPO_ROOT / ".venv-gesture" / ("Scripts/python.exe" if os.name == "nt"
                                                  else "bin/python")
SIDECAR_PYTHON = os.environ.get("ODDBALL_GESTURE_PYTHON", "").strip()

# Set in the child's environment by `_ask_sidecar`, so the sidecar can never shell out again.
# Without it a misconfigured ODDBALL_GESTURE_PYTHON pointing at this same interpreter would
# fork forever, at a security prompt, which is the worst possible place for it.
_IS_SIDECAR = os.environ.get("ODDBALL_GESTURE_SIDECAR", "") == "1"

# How long the sidecar gets. It opens a camera and runs one inference: ~0.5s of work, and the
# budget is generous because a Pi under load is slow, not broken. It is still a hard ceiling —
# an approval prompt that hangs on a wedged subprocess is worse than one that falls to the
# keyboard.
SIDECAR_TIMEOUT_S = 20.0

# The sidecar protocol whitelist. The parent accepts a child's stdout token ONLY if it is in
# here — anything else becomes NO_CAMERA (see `_ask_sidecar`), so a token added to `_classify`
# and forgotten here would be silently downgraded to "the camera is broken".
_VALID = ("TAP", "DRAG", "FLICK", "PINCH", "CLAW", "POINT", "FIST",
          "THUMBS_UP", "THUMBS_DOWN", "OPEN_PALM", "NONE", "NO_CAMERA")

# Which gesture wins when the two hands in frame disagree. Most deliberate first; the two
# permissive resting poses last.
#
# `THUMBS_UP` outranking `OPEN_PALM` is the one entry worth defending: a hand held open while
# the other gives a deliberate thumbs up is the approval, and reading it as OPEN_PALM would
# just send LB to the keyboard. It cannot manufacture an approval — a hand only reaches this
# list already classified, and no non-thumbs-up pose classifies as THUMBS_UP.
# Movement events outrank poses: a TAP is a pinch that already ended, and reporting the pose
# it passed through instead would lose the event. Then the deliberate poses, then the two
# resting ones. THUMBS_DOWN sits beside THUMBS_UP; neither can be reached from the other,
# because the thumb cannot be both above and below the wrist.
_PRIORITY = ("TAP", "FLICK", "DRAG", "PINCH", "CLAW", "POINT",
             "THUMBS_UP", "THUMBS_DOWN", "FIST", "OPEN_PALM", "NONE")


def sidecar_python() -> str:
    """Which interpreter should actually open the camera. Never "" in the parent.

    `ODDBALL_GESTURE_PYTHON` wins, then the conventional `.venv-gesture` beside the repo, then
    **this interpreter** — because the work happens in a child process either way (see
    `_ask_sidecar`), and "no sidecar configured" must not mean "do it here".

    Returns "" only in the child, where it means: stop, do the work.
    """
    if _IS_SIDECAR:
        return ""
    if SIDECAR_PYTHON:
        return SIDECAR_PYTHON
    if _SIDECAR_DEFAULT.exists():
        return str(_SIDECAR_DEFAULT)
    return sys.executable


def _ask_sidecar(python: str) -> str:
    """Run one gesture read in `python` and return its answer.

    ## This is crash isolation, and on this Pi it is not optional

    mediapipe 1.x on Python 3.13 does not raise when its vision task comes up — **it SIGKILLs
    the process** (D15). No `try`/`except` can catch that. Constructing the detector in the
    assistant's own interpreter therefore risks killing the voice loop *at a security prompt*,
    which is precisely the worst place in the program for it to happen.

    So the camera is opened in a **child process, always**, even when no separate sidecar
    interpreter is configured and the child is a second copy of this one. A child that dies is
    a returncode, not a corpse where the assistant used to be. The cost is one process spawn
    per approval — a few times an hour, against a call that already opens a camera.

    The child runs THIS FILE with `--once`, so the classifier — the part with the safety
    property in it — is shared by being the same code, not by being copied.

    Any failure at all is `NO_CAMERA`: a non-zero exit, a timeout, an unparseable answer, a
    missing interpreter. A subprocess that misbehaves must never produce an approval.
    """
    import subprocess

    env = dict(os.environ, ODDBALL_GESTURE_SIDECAR="1")
    try:
        done = subprocess.run(
            [python, str(Path(__file__).resolve()), "--once"],
            capture_output=True, text=True, timeout=SIDECAR_TIMEOUT_S,
            cwd=str(REPO_ROOT), env=env, check=False)
    except subprocess.TimeoutExpired:
        LOG.warning("gesture read timed out after %.0fs", SIDECAR_TIMEOUT_S)
        return "NO_CAMERA"
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("gesture worker failed to run (%s: %s)", type(exc).__name__, exc)
        return "NO_CAMERA"

    if done.returncode != 0:
        # -9/137 is the documented mediapipe-1.x-on-this-Pi failure. Naming it here is what
        # stops the next person rediscovering it from a silent keyboard fallback — and it is
        # the whole reason this runs in a child at all.
        killed = done.returncode in (137, -9)
        LOG.warning("gesture worker exited %d%s", done.returncode,
                    " (SIGKILL — mediapipe cannot run in that interpreter; see D15)"
                    if killed else "")
        return "NO_CAMERA"

    answer = (done.stdout or "").strip().splitlines()
    token = answer[-1].strip() if answer else ""
    if token not in _VALID:
        LOG.warning("gesture worker said %r, which is not a gesture", token[:40])
        return "NO_CAMERA"
    return token


def fetch_model(dest: Path = MODEL_PATH) -> Path:
    """Download the hand landmarker model. Returns its path.

    Called by `--fetch-model` and by the deploy docs, never on the approval path.
    """
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    LOG.info("downloading %s -> %s", MODEL_URL, dest)
    urllib.request.urlretrieve(MODEL_URL, dest)
    return dest


def _dist(a, b) -> float:
    """2-D Euclidean distance between two landmarks, in normalised frame units."""
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def _dist_xy(ax: float, ay: float, bx: float, by: float) -> float:
    """Distance between two bare (x, y) pairs. The movement layer has no landmarks."""
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _hand_scale(landmarks) -> float:
    """The palm span: wrist to middle knuckle. barehands calls this `span`.

    The yardstick every ratio here divides by, so a threshold means the same thing at 40 cm
    from the camera and at 80 cm. Measured across the palm because the palm is rigid — it is
    the same length whether the hand is open, clawed or in a fist.

    Never 0: a degenerate hand on a garbage frame would otherwise divide by zero.
    """
    return max(_dist(landmarks[WRIST], landmarks[MIDDLE_MCP]), 1e-6)


def _palm_centroid(landmarks) -> tuple[float, float]:
    """Mean of the wrist and the four knuckles — where the hand *is*, ignoring the fingers."""
    xs = [landmarks[i].x for i in PALM]
    ys = [landmarks[i].y for i in PALM]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _seg(landmarks, a: int, b: int) -> tuple[float, float, float]:
    """The unit vector from landmark a to landmark b, in 3D."""
    p, q = landmarks[a], landmarks[b]
    dx, dy = q.x - p.x, q.y - p.y
    dz = getattr(q, "z", 0.0) - getattr(p, "z", 0.0)
    length = (dx * dx + dy * dy + dz * dz) ** 0.5 or 1.0
    return dx / length, dy / length, dz / length


def _curl(landmarks, mcp: int, pip: int, dip: int, tip: int) -> float:
    """How hooked this finger is: +1 straight, 0 bent square, negative folded back.

    The dot product of the finger's proximal segment (mcp->pip) with its distal one
    (dip->tip). An extended finger keeps those aligned, ~+0.9. A hooked finger's last segment
    points back against the first, so the dot goes negative.

    ## Why an ANGLE and not a distance

    This is the measurement that makes the whole classifier orientation-proof, and it is
    barehands' finding, stated in its own source:

        monocular z is too weak for distances ... a hooked finger's distal segment points back
        AGAINST its proximal segment (dot < 0-ish); an extended finger's segments stay aligned
        (dot ~ +0.9) in ANY camera orientation — angles survive the foreshortening that lies
        about lengths.

    The version of this file before 2026-08-23 asked `tip.y < pip.y`, which is an angle
    measured against the IMAGE, not against the hand. It is correct only while the hand is
    held upright, and a thumbs up is naturally made with the palm turned side-on — so LB's
    thumbs up photographed at 09:11 that morning classified as NONE, with every finger
    correctly detected and every landmark in the right place. See `media/captures/`.
    """
    a = _seg(landmarks, mcp, pip)
    b = _seg(landmarks, dip, tip)
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _reach(landmarks, tip: int, mcp: int) -> float:
    """How far the tip is from the wrist, relative to its own knuckle. barehands' `fR`.

    Both distances start at the wrist, so the ratio is unchanged by rotating the hand — the
    second rotation-invariant primitive, and the one that says a finger is OUT rather than
    merely UNFOLDED.
    """
    knuckle = _dist(landmarks[WRIST], landmarks[mcp])
    return _dist(landmarks[WRIST], landmarks[tip]) / knuckle if knuckle > 0 else 9.0


def _extended(landmarks, mcp: int, tip: int) -> bool:
    """True when this finger is out: its tip reaches 1.45x past its own knuckle.

    NOTE the signature changed on 2026-08-23, from `(landmarks, pip, tip)` to
    `(landmarks, mcp, tip)`. The old one compared y coordinates and took the PIP; this one
    compares reaches and takes the MCP. Both callers were updated. It is a different test,
    not a refactor.
    """
    return _reach(landmarks, tip, mcp) > EXTEND_REACH


def _finger_open(landmarks, chain) -> bool:
    """This finger is OUT — straight, or at least reaching well past its knuckle.

    Either signal admits, because they fail in different circumstances: `_reach` under-reads a
    finger pointing towards the lens (foreshortening), and `_curl` under-reads when the tracker
    puts the DIP joint slightly wrong on a blurred frame.
    """
    mcp, pip, dip, tip = chain
    return (_curl(landmarks, mcp, pip, dip, tip) > CURL_STRAIGHT
            or _reach(landmarks, tip, mcp) > EXTEND_REACH)


def _finger_shut(landmarks, chain) -> bool:
    """This finger is IN — genuinely folded, by BOTH measures.

    ## Why this is not simply `not _finger_open`

    It is the security test, and the gap between the two is deliberate. `THUMBS_UP` runs a
    shell command on `agents/os_agent.py`'s path, and it is only reachable when all four
    fingers are shut — so "shut" has to mean *folded*, not merely *not proven to be out*.

    A hand whose fingers are neither clearly out nor clearly folded — half-curled, or badly
    tracked — satisfies neither predicate and falls through to `NONE`, which declines to the
    keyboard. That is the safe direction, and it is the whole point of leaving a band between
    them rather than splitting hand-space down the middle with one threshold.

    Measured 2026-08-23: LB's pointing hand read index `curl +0.60, reach 1.37`. Under the
    single-threshold version its index was "not extended" (1.37 < 1.45), which made the hand
    all-fingers-closed, which sent a POINT into the thumb branch and out as `THUMBS_UP` — an
    approval, from a hand that is not remotely a fist. See `media/captures/`.
    """
    mcp, pip, dip, tip = chain
    return (_curl(landmarks, mcp, pip, dip, tip) < CURL_FOLDED
            and _reach(landmarks, tip, mcp) < EXTEND_REACH)


def _aspect(landmarks) -> float:
    """Palm span over palm width. Over ASPECT_GARBAGE the tracker is hallucinating."""
    width = _dist(landmarks[FINGERS[0][0]], landmarks[FINGERS[3][0]])   # index MCP to pinky MCP
    return _hand_scale(landmarks) / width if width > 0 else 9.0


def _pinch_ratio(landmarks) -> float:
    """Thumb tip to index tip, in palm spans. barehands' `ratio`; the mouth of the claw too."""
    return _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / _hand_scale(landmarks)


def _curls(landmarks) -> list[float]:
    """The four finger curls, index to pinky. barehands' c8, c12, c16, c20."""
    return [_curl(landmarks, *chain) for chain in CHAINS]


def _is_pinch(landmarks) -> bool:
    """Thumb and index touching — and the other three NOT, which is the whole test.

    A closed fist also lays the thumb on the index tip, so gap alone cannot tell a pinch from
    a clench. barehands' CONTRAST LAW is the fix: in a real pinch the index curls IN while
    middle, ring and pinky stay OUT, and that contrast separated every correct sample from
    every impostor in its corpus where absolute finger height did not.
    """
    gap = _pinch_ratio(landmarks)
    ceiling = (PINCH_MAX_RATIO if _aspect(landmarks) < ASPECT_FRONTAL
               else PINCH_MAX_RATIO_PROFILE)
    if gap >= ceiling:
        return False

    index_reach = _reach(landmarks, 8, 5)
    back = (_reach(landmarks, 12, 9) + _reach(landmarks, 16, 13) + _reach(landmarks, 20, 17)) / 3
    return back - index_reach > PINCH_CONTRAST and back > PINCH_BACK_ARCH


def _is_claw(landmarks) -> bool:
    """The claw: a deliberately wide mouth with the index and middle hooked.

    barehands' fingerprint, after it walked the mouth floor up from 0.58 to 0.80 chasing
    half-closed impostors: the middle finger leads (it is folded in every real claw), the
    index must fold too (or a pointing hand passes), and the thumb-index C must gape.

    The pinky carries NO vote — it often stays straight in a real claw, which is exactly the
    range an impostor occupies, so it discriminates nothing.
    """
    mouth = _pinch_ratio(landmarks)
    if not CLAW_MOUTH_MIN < mouth < CLAW_MOUTH_MAX:
        return False
    c8, c12, _c16, _c20 = _curls(landmarks)
    if not (c8 < CLAW_INDEX_CURL and c12 < CLAW_MIDDLE_CURL):
        return False
    # Hooked, but not collapsed: this is what makes it a claw rather than a fist.
    return all(_reach(landmarks, tip, mcp) > CLAW_MIN_REACH
               for mcp, _pip, _dip, tip in CHAINS[:3])


def _thumb_direction(landmarks) -> str:
    """"UP", "DOWN" or "" — where the thumb points, in the WORLD, not in the hand.

    Deliberately still an image-y comparison, and that is not an oversight. Up and down are
    facts about gravity; rotating the hand must not rotate the answer. Everything else in this
    module is hand-relative precisely so that this one test can be world-relative and mean
    something.

    Measured in palm spans from the wrist so it stays distance-independent, and required in
    one direction or the other by a real margin, so a thumb held level is neither.
    """
    rise = (landmarks[WRIST].y - landmarks[THUMB_TIP].y) / _hand_scale(landmarks)
    if rise > THUMB_RISE:
        return "UP"
    if rise < -THUMB_DROP:
        return "DOWN"
    return ""


def _classify(landmarks) -> str:
    """One hand's 21 landmarks -> a pose.

    'PINCH', 'CLAW', 'POINT', 'THUMBS_UP', 'THUMBS_DOWN', 'FIST', 'OPEN_PALM' or 'NONE'.

    Pure: hand it any sequence of 21 objects with `.x`, `.y` and optionally `.z`. No camera,
    no model file, no mediapipe. Shared by both backends.

    TAP, DRAG and FLICK are not here — they are movements, and this sees one instant. They
    live in `classify_stream`.

    **The order of these branches is a safety property.** `THUMBS_UP` is what
    `agents/os_agent.py` runs a shell command on, so every pose that could be mistaken for it
    is resolved first. See the header.
    """
    if _aspect(landmarks) > ASPECT_GARBAGE:
        return "NONE"                  # the tracker is guessing; a guess is not a gesture

    out = [_finger_open(landmarks, chain) for chain in CHAINS]
    shut = [_finger_shut(landmarks, chain) for chain in CHAINS]

    # Open palm first, as it has been since 2026-08-19: it is the permissive pose and it
    # overlaps the naive thumbs-up test, so checking it first is what stops a wave being a yes.
    if all(out):
        return "OPEN_PALM"

    # Then the two deliberate shapes that would otherwise fall through into the thumb tests.
    if _is_pinch(landmarks):
        return "PINCH"
    if _is_claw(landmarks):
        return "CLAW"

    # Pointing: index out, the other three genuinely folded. Was NONE before 2026-08-23 — LB
    # photographed one at 09:12 that morning and it is in `media/captures/`.
    if out[0] and all(shut[1:]):
        return "POINT"

    # A closed hand, and ONLY a properly closed one — see `_finger_shut` for why this demands
    # all four folded rather than merely not-extended. Which gesture it is then depends on the
    # thumb, and only on the thumb.
    if all(shut):
        thumb = _thumb_direction(landmarks)
        if thumb == "UP":
            return "THUMBS_UP"
        if thumb == "DOWN":
            return "THUMBS_DOWN"
        return "FIST"

    return "NONE"


def _classify_frame(hands) -> str:
    """Every hand in one frame -> a single pose token, resolved by `_PRIORITY`.

    Pure, like `_classify`.
    """
    if not hands:
        return "NONE"
    seen = {_classify(h) for h in hands}
    for gesture in _PRIORITY:
        if gesture in seen:
            return gesture
    return "NONE"



class GestureRecognizer:
    """One hand detector, reused across calls, over whichever mediapipe API is installed.

    `cv2` and `mediapipe` are imported inside `__init__`, not at module scope, and that is
    load-bearing: `agents/os_agent.py` imports this module, so a top-level `import cv2` on a
    box without OpenCV would take out the entire OS route rather than just the camera. Same
    reasoning as `tools/kicad_parser.py` wrapping `kiutils`.

    `self.backend` is "tasks", "solutions" or "" — reported by `--backend`, because "which
    mediapipe am I on" is the first question any problem here will raise.
    """

    def __init__(self) -> None:
        self._detect = None
        self._close = None
        self._cv2 = None
        self.backend = ""
        self.why = ""

        # TAP, DRAG and FLICK are movements, so they are the only gestures that need the
        # recogniser to remember anything. Samples are (monotonic seconds, x, y, palm span)
        # of the pinch being tracked. Only `classify_stream` touches any of this —
        # `get_gesture()`, the one-shot path the security gate runs on, never appends here
        # and so carries no state between approvals at all.
        self._history: deque = deque(maxlen=TRACK_HISTORY)
        self._pinch = False           # was a pinch live on the previous frame?
        self._started = 0.0           # when the current pinch began
        self._travel = 0.0            # how far it has moved since, in palm spans
        self.last_release = ""        # what the last TAP or FLICK measured, for the debugger

        try:
            import cv2
        except Exception as exc:                                          # noqa: BLE001
            self.why = f"opencv is not installed ({type(exc).__name__})"
            LOG.info("gesture control unavailable: %s — the keyboard still works", self.why)
            return
        self._cv2 = cv2

        try:
            import mediapipe as mp
        except Exception as exc:                                          # noqa: BLE001
            self.why = f"mediapipe is not installed ({type(exc).__name__})"
            LOG.info("gesture control unavailable: %s — the keyboard still works", self.why)
            return

        # mediapipe 1.x — the Tasks API. Preferred: it is the only one with a wheel that
        # installs on the Pi's Python 3.13.
        if hasattr(mp, "tasks"):
            if not MODEL_PATH.exists():
                self.why = (f"{MODEL_PATH.name} is missing — run "
                            f"`python tools/gesture_control.py --fetch-model`")
                LOG.info("gesture control unavailable: %s", self.why)
                return
            try:
                from mediapipe.tasks import python as mpp
                from mediapipe.tasks.python import vision

                landmarker = vision.HandLandmarker.create_from_options(
                    vision.HandLandmarkerOptions(
                        base_options=mpp.BaseOptions(model_asset_path=str(MODEL_PATH)),
                        running_mode=vision.RunningMode.IMAGE,
                        num_hands=MAX_HANDS,
                        min_hand_detection_confidence=MIN_DETECTION_CONFIDENCE,
                        min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
                    ))
            except Exception as exc:                                      # noqa: BLE001
                self.why = f"the Tasks landmarker would not load ({type(exc).__name__}: {exc})"
                LOG.warning("gesture control unavailable: %s", self.why)
                return

            def detect(rgb):
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                return landmarker.detect(image).hand_landmarks

            self._detect, self._close, self.backend = detect, landmarker.close, "tasks"
            return

        # mediapipe 0.10.x — the legacy Solutions API. Still correct on a Python <= 3.12 box
        # pinned to 0.10.18, and it needs no model file.
        if hasattr(mp, "solutions"):
            try:
                hands = mp.solutions.hands.Hands(
                    max_num_hands=MAX_HANDS,
                    min_detection_confidence=MIN_DETECTION_CONFIDENCE,
                    min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
                )
            except Exception as exc:                                      # noqa: BLE001
                self.why = f"Hands() would not construct ({type(exc).__name__}: {exc})"
                LOG.warning("gesture control unavailable: %s", self.why)
                return

            def detect(rgb):
                found = hands.process(rgb).multi_hand_landmarks
                return [h.landmark for h in (found or [])]

            self._detect, self._close, self.backend = detect, hands.close, "solutions"
            return

        self.why = (f"mediapipe {getattr(mp, '__version__', '?')} has neither `tasks` nor "
                    f"`solutions` — this module knows no third API")
        LOG.warning("gesture control unavailable: %s", self.why)

    @property
    def available(self) -> bool:
        """True when a detector was built. False means keyboard only, and `why` says why."""
        return self._detect is not None

    def get_gesture(self) -> str:
        """One frame from the camera -> one gesture token. The approval path.

        Returns a POSE — 'PINCH', 'CLAW', 'POINT', 'THUMBS_UP', 'THUMBS_DOWN', 'FIST',
        'OPEN_PALM' or 'NONE'. Optimised for Raspberry Pi camera processing: the camera is
        opened, warmed, read once and closed.

        **Never 'TAP', 'DRAG' or 'FLICK'.** Those are movements; one frame carries none, and
        this method holds no state across calls by design — in the parent process it is a
        fresh child every time. Use `detect_hands` + `classify_stream` on a live loop.

        Returns 'NO_CAMERA' when there is no camera, when mediapipe or OpenCV are missing, or
        when the model file has not been fetched — none of which is ever an approval.
        """
        if not self.available:
            return "NO_CAMERA"

        cv2 = self._cv2
        cap = cv2.VideoCapture(0)
        try:
            if not cap.isOpened():
                return "NO_CAMERA"
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, FRAME_FPS)

            ret, frame = False, None
            for _ in range(WARMUP_FRAMES):
                ret, frame = cap.read()
                if not ret:
                    break
        finally:
            cap.release()

        if not ret or frame is None:
            return "NO_CAMERA"

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return _classify_frame(self._detect(rgb_frame))

    def detect_hands(self, rgb):
        """One RGB frame -> a list of hands, each a sequence of 21 landmarks.

        The raw output, before any gesture decision. Split out from `classify_stream` so a
        caller that wants to DRAW the skeleton and also name the gesture runs the detector
        once for both — `tools/live_test_gestures.py` is the caller that needs this, and at
        88 ms per inference on the Pi, running it twice a frame would halve the frame rate of
        the tuning window.

        Returns [] when no detector was built, so a caller can treat "no mediapipe" and "no
        hand" the same way.
        """
        if not self.available:
            return []
        return self._detect(rgb)

    def classify_stream(self, hands, now: float | None = None) -> str:
        """Hands from a LIVE loop -> a gesture token, movements included.

        The difference from `_classify_frame` is memory. TAP, DRAG and FLICK are things that
        happen to a pinch over time, so they need to know what the hand was doing a moment ago.

        ## Why FLICK was rewritten on 2026-08-23

        The first version defined a flick as *an open palm moving quickly across the frame*.
        LB reported it "often gets confused with open palm", which was not a tuning problem —
        it was the definition. A moving open palm and a still open palm are the same hand, and
        the only thing separating them was a speed threshold that had to be met between two
        frames 100 ms apart. Below the threshold it reported OPEN_PALM, which is the exact
        complaint.

        barehands does not define it that way, and says why on its first line: **"Every gesture
        is a movement, not a pose."** There, a flick is *a pinch released at speed* — you are
        carrying something and you let go while your hand is still travelling. That has no
        overlap with an open palm at all, because it starts from a pinch. This now matches.

        Call once per frame, in order. Gaps are handled; out-of-order calls are meaningless.

        Args:
            hands: what `detect_hands` returned for this frame.
            now:   monotonic timestamp; defaults to now. Injectable so the movement logic is
                   testable without a camera and without sleeping.

        Returns:
            One of the tokens in `_VALID`, except `NO_CAMERA`.
        """
        if now is None:
            now = time.monotonic()

        pose = _classify_frame(hands)

        # The pinch being tracked is the first hand that has one. Its palm centre is the
        # position — a fingertip would add finger motion to hand motion and read a wave as
        # travel — and its scale converts that position into palm spans.
        centre = scale = None
        for hand in hands:
            if _classify(hand) == "PINCH":
                centre = _palm_centroid(hand)
                scale = _hand_scale(hand)
                break

        was = self._pinch
        self._pinch = centre is not None
        if centre is not None:
            self._history.append((now, centre[0], centre[1], scale))
            if was:
                self._travel += (_dist_xy(self._history[-2][1], self._history[-2][2],
                                          centre[0], centre[1]) / max(scale, 1e-6))
            else:
                self._history.clear()
                self._history.append((now, centre[0], centre[1], scale))
                self._started = now
                self._travel = 0.0
            return "DRAG" if self._travel > DRAG_MIN_TRAVEL else "PINCH"

        # No pinch this frame. If there was one last frame, it just ended — and how it ended
        # is the event. This is the only place TAP and FLICK are produced.
        if was:
            held = now - self._started
            speed = self._release_speed(now)
            self._pinch = False
            if speed > FLICK_MIN_SPEED:
                self.last_release = f"FLICK {speed:.1f} spans/s"
                self._history.clear()
                return "FLICK"
            if held < TAP_MAX_S and self._travel < TAP_MAX_TRAVEL:
                self.last_release = f"TAP {held * 1000:.0f} ms"
                self._history.clear()
                return "TAP"
            self._history.clear()

        return pose

    def _release_speed(self, now: float) -> float:
        """How fast the hand was travelling as the pinch ended, in palm spans per second.

        Measured over the last `FLICK_WINDOW_S` of samples rather than the final pair: at this
        webcam's ~9.7 fps a single mis-placed landmark between two frames can look like a
        throw, and a window of several frames cannot be faked by one bad one. barehands guards
        the same failure with a two-frame sustain on its release read.
        """
        if len(self._history) < 2:
            return 0.0
        latest = self._history[-1]
        for when, x, y, scale in self._history:
            dt = latest[0] - when
            if dt <= 0 or dt > FLICK_WINDOW_S:
                continue
            travel = _dist_xy(x, y, latest[1], latest[2]) / max(scale, 1e-6)
            return travel / dt
        return 0.0


    def close(self) -> None:
        """Release the detector. Safe to call twice."""
        if self._close is not None:
            try:
                self._close()
            except Exception:                                             # noqa: BLE001
                pass
            self._detect = self._close = None


# One recogniser for the process. Built on first use rather than at import, so importing this
# module costs nothing until something actually looks at the camera.
_RECOGNIZER: GestureRecognizer | None = None


def _recognizer() -> GestureRecognizer:
    global _RECOGNIZER
    if _RECOGNIZER is None:
        _RECOGNIZER = GestureRecognizer()
    return _RECOGNIZER


def get_gesture() -> str:
    """What the camera sees right now. **Never raises, never dies, never blocks forever.**

    Always out-of-process in the parent — see `_ask_sidecar` for why that is a requirement and
    not an optimisation. In the child (`--once`), this is the in-process read.
    """
    if _DISABLED:
        return "NO_CAMERA"

    python = sidecar_python()
    if python:
        return _ask_sidecar(python)

    # The child. This is the only place mediapipe is ever constructed, and if it takes the
    # process down with it, the parent sees a returncode.
    try:
        return _recognizer().get_gesture()
    except Exception as exc:                                              # noqa: BLE001
        LOG.warning("gesture read failed (%s: %s)", type(exc).__name__, exc)
        return "NO_CAMERA"


def gesture_approves() -> bool:
    """True only for a clear thumbs up. Every other outcome, including error, is False."""
    return get_gesture() == "THUMBS_UP"


def approve_by_gesture_or_keyboard(prompt: str = "   Allow execution? (y/n): ") -> bool:
    """Ask for approval by camera, then by keyboard. **Only a yes returns True.**

    The order matters and only in one direction: a thumbs up short-circuits the keyboard, but
    nothing else short-circuits anything. No camera, no hand, an open palm, an exception —
    all of them fall through to `input()`, so the worst a broken camera can do is make LB type
    a letter he was already going to type.

    Args:
        prompt: what to print when falling back to the keyboard.

    Returns:
        True if approved.
    """
    seen = get_gesture()
    if seen == "THUMBS_UP":
        print("   👍 Thumbs up — approved by gesture.")
        return True
    if seen == "THUMBS_DOWN":
        # Added 2026-08-23 with the gesture itself. This is the ONE case where the camera is
        # allowed to end the question without the keyboard, and it is safe for a reason that
        # does not generalise: it returns False. A declined action does not run, and a gesture
        # that can only ever decline cannot approve anything by being misread. The asymmetry
        # is the whole design — a wrong THUMBS_UP executes a shell command, a wrong
        # THUMBS_DOWN costs LB one retry.
        print("   👎 Thumbs down — declined by gesture.")
        return False
    if seen not in ("NO_CAMERA", "NONE"):
        print(f"   (camera saw {seen}, which is not an approval)")

    try:
        return input(prompt).strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        # No stdin, or ctrl-C at the prompt. Both are declines. A gate that defaults open
        # under an unexpected condition is not a gate.
        print()
        return False


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description="read a hand gesture from the camera")
    ap.add_argument("--fetch-model", action="store_true",
                    help=f"download {MODEL_PATH.name} (~7.8 MB) and exit")
    ap.add_argument("--backend", action="store_true",
                    help="report which mediapipe API is in use, and exit")
    ap.add_argument("--once", action="store_true",
                    help="print exactly one gesture token on stdout and exit. This is the "
                         "sidecar protocol — the parent reads the last stdout line, so nothing "
                         "else may go there.")
    ap.add_argument("--watch", action="store_true", help="keep reading until ctrl-C")
    ap.add_argument("--interval", type=float, default=1.0, metavar="S",
                    help="seconds between reads under --watch (default 1.0)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    if args.fetch_model:
        path = fetch_model()
        print(f"  {path}  ({path.stat().st_size / 1e6:.2f} MB)")
        return 0

    if args.once:
        # stdout carries the token and nothing else; mediapipe is noisy, but on stderr.
        print(get_gesture())
        return 0

    worker = sidecar_python()

    if args.backend:
        print(f"  this interpreter: {sys.executable}")
        print(f"  model:            {MODEL_PATH} "
              f"{'present' if MODEL_PATH.exists() else 'MISSING'}")
        print(f"  disabled:         {_DISABLED} (ODDBALL_GESTURE)")
        if worker:
            # Deliberately NOT constructing the detector here. On a box where mediapipe
            # cannot run, doing so would kill this very process — which is exactly the
            # failure `--backend` exists to diagnose, and a diagnostic that dies of the fault
            # it is reporting on is useless.
            own = Path(worker).resolve() == Path(sys.executable).resolve()
            print(f"  camera worker:    {worker}{'  (same interpreter)' if own else ''}")
            print(f"  worker says:      {_ask_sidecar(worker)}")
            print()
            print("  --- as reported from inside the worker ---")
            import subprocess
            probe = subprocess.run(
                [worker, str(Path(__file__).resolve()), "--backend"],
                capture_output=True, text=True, check=False,
                cwd=str(REPO_ROOT), env=dict(os.environ, ODDBALL_GESTURE_SIDECAR="1"))
            for line in (probe.stdout or "").splitlines():
                print(f"  {line}")
            if probe.returncode != 0:
                print(f"  worker --backend exited {probe.returncode}"
                      f"{'  (SIGKILL — mediapipe cannot run there; see D15)' if probe.returncode in (137, -9) else ''}")
            return 0

        rec = _recognizer()
        print(f"  backend:          {rec.backend or '(none)'}")
        print(f"  available:        {rec.available}")
        if rec.why:
            print(f"  why not:          {rec.why}")
        return 0 if rec.available else 1

    if _DISABLED:
        print("  ODDBALL_GESTURE=0 is set, so get_gesture() will report NO_CAMERA.")

    if not args.watch:
        print(f"  {get_gesture()}")
        return 0

    print(f"  watching via {worker or 'this interpreter'} — pinch, claw, point, fist, "
          f"thumbs up/down, open palm; ctrl-C to stop")
    print("  (poses only: each read is one frame from a camera that is then closed, so TAP, "
          "DRAG and FLICK cannot appear — run tools/live_test_gestures.py for those)")
    try:
        while True:
            print(f"  {time.strftime('%H:%M:%S')}  {get_gesture()}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
