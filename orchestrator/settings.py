#!/usr/bin/env python3
"""
Module:  settings.py
Purpose: Load and validate config/oddball.toml.
Author:  LB
Date:    2026-08-10

Validation is strict and happens at load. A wake threshold of 5 instead of 0.5 would
otherwise present as "he never wakes up", which is a much worse hour than an error message.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "oddball.toml"

# key -> (type, validator, message)
_SCHEMA: dict[str, list[tuple[str, type | tuple, Any, str]]] = {
    "wake": [
        ("model",         str,          lambda v: bool(v),            "must be a non-empty name or path"),
        ("threshold",     (int, float), lambda v: 0 < v <= 1,         "must be in (0, 1]"),
        ("refractory_s",  (int, float), lambda v: v >= 0,             "must be >= 0"),
        ("listen_s",      (int, float), lambda v: v > 0,              "must be > 0"),
        # 0 is legal and means OFF — every turn ends by going straight back to sleep, which is
        # the behaviour before conversation mode existed. The upper bound is not tidiness: a
        # window measured in minutes is a microphone that is effectively always open, and that
        # should be a typo caught at load rather than a privacy surprise found later.
        ("conversation_s", (int, float), lambda v: 0 <= v <= 300,     "must be in [0, 300] seconds"),
        ("resting_state", str,          lambda v: bool(v),            "must be a state name"),
        ("device",        str,          lambda v: True,               ""),
        ("framework",     str,          lambda v: v in ("onnx", "tflite"), "must be onnx or tflite"),
    ],
    "listen": [
        ("threshold",  (int, float), lambda v: 0 < v <= 1,   "must be in (0, 1]"),
        ("wait_s",     (int, float), lambda v: 0 < v <= 30,  "must be in (0, 30] seconds"),
        # An upper bound because a long hangover is spent on every single turn and presents
        # as "he got slow" rather than as a misconfiguration.
        ("hangover_s", (int, float), lambda v: 0 < v <= 5,   "must be in (0, 5] seconds"),
        ("max_s",      (int, float), lambda v: 0 < v <= 60,  "must be in (0, 60] seconds"),
    ],
    "stt": [
        ("model",        str, lambda v: bool(v),                    "must be a whisper model name"),
        ("compute_type", str, lambda v: v in ("int8", "int8_float32", "float32", "float16"),
         "must be int8, int8_float32, float32 or float16"),
        ("cpu_threads",  int, lambda v: 1 <= v <= 16,               "must be in [1, 16]"),
    ],
    "speech": [
        ("engine",      str,          lambda v: v in ("piper",),    "must be piper"),
        ("voice",       str,          lambda v: bool(v),            "must be a voice name or path"),
        ("device",      str,          lambda v: True,               ""),
        ("volume",      (int, float), lambda v: 0 < v <= 1,         "must be in (0, 1]"),
        # An upper bound as well as a lower one: a tail of 60 would leave him deaf for a
        # minute after every sentence, which presents as "he stopped answering".
        ("gate_tail_s", (int, float), lambda v: 0 <= v <= 5,        "must be in [0, 5] seconds"),
        # Bluetooth is ~150-250ms; 2000 is generous headroom and still catches a value typed
        # in seconds by mistake, which would delay his mouth by half a minute.
        ("output_latency_ms", (int, float), lambda v: 0 <= v <= 2000,
         "must be in [0, 2000] milliseconds"),
        # Silence written before the first real sample, to wake a suspended sink. 1000ms is
        # already absurd for that job and still catches a value typed in seconds.
        ("prime_ms", (int, float), lambda v: 0 <= v <= 1000,
         "must be in [0, 1000] milliseconds"),
        ("greeting",    list,         lambda v: bool(v) and all(isinstance(s, str) and s.strip()
                                                                for s in v),
         "must be a non-empty list of non-empty strings"),
    ],
    "hud": [
        ("host", str, lambda v: bool(v),               "must be a host"),
        ("port", int, lambda v: 1 <= v <= 65535,       "must be a valid port"),
        # The file-upload endpoint. A SECOND port, not a second host: it binds wherever the rig
        # binds, so `--host 0.0.0.0` opens both or neither and there is no configuration in
        # which the page is reachable from the LAN but its paperclip is not.
        ("upload_port", int, lambda v: 1 <= v <= 65535, "must be a valid port"),
    ],
    "brain": [
        ("enabled",      bool,         lambda v: True,          ""),
        ("model",        str,          lambda v: bool(v),       "must be a path to a .gguf"),
        ("binary",       str,          lambda v: bool(v),       "must be a path to llama-server"),
        ("port",         int,          lambda v: 1 <= v <= 65535, "must be a valid port"),
        # Four cores on the Pi 5. Upper bound catches a value typed as a core count on a
        # bigger machine, which oversubscribes and measures SLOWER, not faster.
        ("threads",      int,          lambda v: 1 <= v <= 16,  "must be in [1, 16]"),
        # 4096 holds the persona plus a conversation. Below ~1024 the persona alone would not
        # fit and every reply would be truncated with no obvious cause.
        ("ctx",          int,          lambda v: 512 <= v <= 32768, "must be in [512, 32768]"),
        ("max_tokens",   int,          lambda v: 16 <= v <= 2048, "must be in [16, 2048]"),
        ("temperature",  (int, float), lambda v: 0 <= v <= 2,   "must be in [0, 2]"),
        # 0 disables the idle timer. An upper bound of a day, because "never reset" should be
        # spelled 0 rather than reached by typing a big number.
        ("idle_reset_s", (int, float), lambda v: 0 <= v <= 86400, "must be in [0, 86400] seconds"),
        ("prewarm",      bool,         lambda v: True,          ""),
        # Empty means no stall phrase, which is the default — see D31 and turn.py.
        ("stall_phrase", str,          lambda v: True,          ""),
    ],
}

# Keys that are VALIDATED IF PRESENT, and simply absent-means-default if not.
#
# `dictation_max_s` and `wake_tail_s` were left out of `_SCHEMA` ENTIRELY, and that was
# deliberate rather than an oversight: every key in `_SCHEMA` is REQUIRED — `load_config` raises
# `KeyError` when one is missing — so listing them there would stop an older config file from
# starting at all. `engine/run_voice.py` states the intent where it reads them: *"a deploy that
# has not picked up the new key behaves exactly as it did before rather than failing to start."*
#
# The cost of that was silence, and it is the exact failure this module's header exists to
# prevent. `wake_tail_s = 25`, typed for `0.25`, passes today. It would deafen him to the first
# twenty-five seconds of every capture that follows a wake word, and present as "he never hears
# me" — a much worse hour than an error message.
#
# So: a second tier. Absent is fine and means the default the caller already passes to `.get`.
# Present and out of range is an error at load, like everything else.
_OPTIONAL: dict[str, list[tuple[str, type | tuple, Any, str]]] = {
    "listen": [
        # 0 means off — the cap is left alone. 300 is five minutes of continuous unbroken
        # speech, already absurd for one note, and it catches a value typed in minutes.
        ("dictation_max_s", (int, float), lambda v: 0 <= v <= 300,
         "must be in [0, 300] seconds (0 = off)"),
        # 0 means off. The wake phrase itself runs about 1.2s; a suppression window longer than
        # the phrase it exists to swallow is a typo, and past 2.0 it starts eating the question.
        # `UtteranceRecorder.ignore_start_s` refuses anything at or above PREROLL_S anyway, so
        # this is the earlier and friendlier of two guards rather than the only one.
        ("wake_tail_s", (int, float), lambda v: 0 <= v <= 2.0,
         "must be in [0, 2] seconds (0 = off)"),
        # 0 means off — quiz turns get the ordinary `wait_s`. Above a minute this stops being
        # thinking time and becomes a microphone held open on a silent room, which is the
        # complaint `[wake] greet_threshold` exists to answer.
        ("quiz_wait_s", (int, float), lambda v: 0 <= v <= 60,
         "must be in [0, 60] seconds (0 = off)"),
        # 0 means off. Bounded BELOW `dictation_max_s`'s 300 on purpose: dictation raises the
        # cap for one turn LB opened himself, where quiz mode is a mode that stays raised for
        # the whole session. See the argument in config/oddball.toml.
        ("quiz_max_s", (int, float), lambda v: 0 <= v <= 120,
         "must be in [0, 120] seconds (0 = off)"),
    ],
    "wake": [
        # 0 means "always greet", which is the behaviour before 2026-09-06. 1 would mean he
        # effectively never greets, which is legal and is how you switch the greeting off.
        ("greet_threshold", (int, float), lambda v: 0 <= v <= 1, "must be in [0, 1]"),
    ],
    "quiz": [
        ("commit_required", bool, lambda v: True, ""),
        # 0 means never give up on a silent quiz, which is a legal choice and must not be a
        # startup error. 20 is far past the point where the room is empty.
        ("idle_turns", int, lambda v: 0 <= v <= 20, "must be in [0, 20] (0 = never give up)"),
        # 0 means never remind him of the commit phrase.
        ("nudge_after_ignored", int, lambda v: 0 <= v <= 10, "must be in [0, 10] (0 = never)"),
    ],
}

# `bool` is a subclass of `int`, so load_config() rejects a bool anywhere an int is wanted.
# These two keys genuinely ARE booleans, so they are exempted by name rather than by loosening
# the check for everything.
_REAL_BOOLS = {("brain", "enabled"), ("brain", "prewarm"), ("quiz", "commit_required")}


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    """Read the TOML config, checking every key exists and is in range.

    Raises:
        FileNotFoundError: the config is missing.
        KeyError:          a required section or key is absent.
        ValueError:        a value is the wrong type or out of range.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")

    with path.open("rb") as fh:
        cfg = tomllib.load(fh)

    def _check(section: str, key: str, value, want_type, ok, why: str) -> None:
        """Type- and range-check one value. Shared by both tiers.

        One copy of the rule, not two. A required key and an optional one differ only in what
        happens when they are ABSENT; once a value exists, it is checked identically, and two
        copies of that check is precisely the drift this file exists to catch.
        """
        # bool is a subclass of int in Python; a stray `true` must not pass as a port.
        # Keys that are genuinely boolean are listed in _REAL_BOOLS rather than weakening
        # this for every int in the file.
        if (section, key) in _REAL_BOOLS:
            if not isinstance(value, bool):
                raise ValueError(f"{path}: {section}.{key} should be true or false, "
                                 f"got {type(value).__name__}")
            return
        if isinstance(value, bool) or not isinstance(value, want_type):
            raise ValueError(
                f"{path}: {section}.{key} should be {want_type}, got {type(value).__name__}"
            )
        if not ok(value):
            raise ValueError(f"{path}: {section}.{key}={value!r} {why}")

    for section, fields in _SCHEMA.items():
        if section not in cfg:
            raise KeyError(f"{path}: missing [{section}] section")
        for key, want_type, ok, why in fields:
            if key not in cfg[section]:
                raise KeyError(f"{path}: missing {section}.{key}")
            _check(section, key, cfg[section][key], want_type, ok, why)

    # The optional tier. A missing SECTION is as legal as a missing key — `[quiz]` does not
    # exist in a config written before 2026-09-06, and the caller's own default is the answer.
    for section, fields in _OPTIONAL.items():
        if section not in cfg:
            continue
        for key, want_type, ok, why in fields:
            if key not in cfg[section]:
                continue
            _check(section, key, cfg[section][key], want_type, ok, why)

    return cfg


if __name__ == "__main__":
    import json

    print(json.dumps(load_config(), indent=2))
