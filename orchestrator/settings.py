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

# `bool` is a subclass of `int`, so load_config() rejects a bool anywhere an int is wanted.
# These two keys genuinely ARE booleans, so they are exempted by name rather than by loosening
# the check for everything.
_REAL_BOOLS = {("brain", "enabled"), ("brain", "prewarm")}


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

    for section, fields in _SCHEMA.items():
        if section not in cfg:
            raise KeyError(f"{path}: missing [{section}] section")
        for key, want_type, ok, why in fields:
            if key not in cfg[section]:
                raise KeyError(f"{path}: missing {section}.{key}")
            value = cfg[section][key]
            # bool is a subclass of int in Python; a stray `true` must not pass as a port.
            # Keys that are genuinely boolean are listed in _REAL_BOOLS rather than weakening
            # this for every int in the file.
            if (section, key) in _REAL_BOOLS:
                if not isinstance(value, bool):
                    raise ValueError(f"{path}: {section}.{key} should be true or false, "
                                     f"got {type(value).__name__}")
                continue
            if isinstance(value, bool) or not isinstance(value, want_type):
                raise ValueError(
                    f"{path}: {section}.{key} should be {want_type}, got {type(value).__name__}"
                )
            if not ok(value):
                raise ValueError(f"{path}: {section}.{key}={value!r} {why}")

    return cfg


if __name__ == "__main__":
    import json

    print(json.dumps(load_config(), indent=2))
