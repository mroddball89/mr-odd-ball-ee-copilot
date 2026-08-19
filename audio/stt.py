#!/usr/bin/env python3
"""
Module:  stt.py
Purpose: Speech to text — turn a captured utterance into a string.
Author:  LB
Date:    2026-08-12

Library first, CLI second, like audio/wake.py and audio/say.py. `Transcriber` takes its model
as an argument, so tools/verify_stt.py drives the wiring with a stub and no 75MB download.

    python audio/stt.py clip.wav            # transcribe a 16kHz mono WAV
    python audio/stt.py clip.wav --model base.en

**The decode arguments below are load-bearing, not defaults copied from a README.** Measured on
the Pi against nine synthesised commands (2026-08-12): with faster-whisper's own defaults,
`tiny.en` misheard "What day is it today?" as "What they use it today." Turning off timestamps
and previous-text conditioning fixed that *and* made it faster. See docs/STT.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

LOG = logging.getLogger("oddball.stt")

REPO_ROOT = Path(__file__).resolve().parents[1]
# Alongside the wake model and the Piper voices: big, re-downloadable, gitignored, and NOT in
# the deploy tarball. Keeping it here rather than in ~/.cache means "where did 75MB go" has an
# answer, and means a box can be made airtight by copying one directory.
WHISPER_DIR = REPO_ROOT / "models" / "whisper"

SAMPLE_RATE_HZ = 16_000


@dataclass(frozen=True)
class Heard:
    """One transcribed utterance."""

    text: str           # stripped; "" if nothing intelligible was said
    took_s: float       # wall-clock transcription time, for the per-turn latency line

    def __bool__(self) -> bool:
        return bool(self.text)


class TranscribingModel(Protocol):
    """The slice of faster-whisper's WhisperModel this actually needs."""

    def transcribe(self, audio: np.ndarray, **kw): ...


class Transcriber:
    """Turns float32 16kHz audio into text.

    Args:
        model: anything satisfying TranscribingModel — normally faster_whisper.WhisperModel.
    """

    def __init__(self, model: TranscribingModel) -> None:
        self._model = model

    def transcribe(self, audio: np.ndarray) -> Heard:
        """Transcribe one utterance.

        Args:
            audio: mono float32 in [-1, 1] at SAMPLE_RATE_HZ.

        Returns:
            Heard, with `text` empty if the utterance held no words.
        """
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        t0 = time.monotonic()
        segments, _info = self._model.transcribe(
            audio,
            language="en",              # skip language detection; it costs time and can be wrong
            beam_size=1,                # greedy. Beam search roughly doubles the time here
            without_timestamps=True,    # we want the words, not when they happened
            condition_on_previous_text=False,   # every turn is independent; see the note above
            vad_filter=False,           # audio/listen.py already trimmed it with Silero
        )
        # Each segment carries its own leading space, so a plain " ".join doubles them and
        # produces "What time  is it?" — which breaks any exact matching downstream and reads
        # as sloppiness in the logs. Strip each, then join.
        text = " ".join(s.text.strip() for s in segments if s.text.strip())
        took = time.monotonic() - t0
        LOG.info("heard %r in %.2fs", text, took)
        return Heard(text=text, took_s=took)


def build_model(name: str = "tiny.en", compute_type: str = "int8", cpu_threads: int = 4):
    """Load a faster-whisper model, downloading it once into models/whisper/.

    `cpu_threads` is passed explicitly: the Pi 5 has four cores and leaving this to the
    default measurably wasted them.

    Sizes measured on the Pi (int8, 4 threads, the decode arguments above), nine synthesised
    commands:

        tiny.en   1.05-1.40s   7/9 exact
        base.en   2.03-2.49s   8/9 exact

    `tiny.en` is the default because it is the only one that fits PLAN.md's under-2s turn once
    ~0.4s of Piper synthesis is added. Most of what it gets wrong survives the reflex router,
    which matches on keywords rather than whole sentences — "What is today?" still routes to
    the date. **This was measured on synthesised speech, not on LB's voice**, so treat the
    accuracy figure as indicative and switch to base.en if he is misheard in practice.
    """
    from faster_whisper import WhisperModel

    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    LOG.info("loading whisper %r (%s, %d threads)", name, compute_type, cpu_threads)
    kw = dict(device="cpu", compute_type=compute_type, cpu_threads=cpu_threads,
              download_root=str(WHISPER_DIR))
    try:
        # Local first, and not as an optimisation. Left to itself faster-whisper calls
        # huggingface.co on EVERY start to check the model revision — so an assistant whose
        # whole premise is that nothing leaves the machine would refuse to boot without
        # internet, and would phone home each time it did.
        return WhisperModel(name, local_files_only=True, **kw)
    except Exception:
        LOG.info("%r is not in %s yet — downloading it once", name, WHISPER_DIR)
        return WhisperModel(name, local_files_only=False, **kw)


def wav_audio(path: str | Path) -> np.ndarray:
    """Read a 16kHz mono WAV as float32 in [-1, 1]."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SAMPLE_RATE_HZ:
            raise ValueError(f"{path}: need {SAMPLE_RATE_HZ}Hz, got {w.getframerate()}Hz")
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            raw = raw.reshape(-1, w.getnchannels())[:, 0]
    return raw.astype(np.float32) / 32768.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="transcribe a recording (debug CLI)")
    ap.add_argument("wav", help="a 16kHz mono WAV")
    ap.add_argument("--model", default=None, help="override stt.model")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    sys.path.insert(0, str(REPO_ROOT))
    from orchestrator.settings import load_config

    stt_cfg = load_config()["stt"]
    model = build_model(args.model or stt_cfg["model"], stt_cfg["compute_type"],
                        stt_cfg["cpu_threads"])
    audio = wav_audio(args.wav)
    heard = Transcriber(model).transcribe(audio)
    print(f"{heard.text!r}  ({heard.took_s:.2f}s for {audio.size / SAMPLE_RATE_HZ:.2f}s audio)")
    return 0 if heard else 1


if __name__ == "__main__":
    raise SystemExit(main())
