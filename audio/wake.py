#!/usr/bin/env python3
"""
Module:  wake.py
Purpose: Wake-word detection — turn "hey <phrase>" on a microphone into a callback.
Author:  LB
Date:    2026-08-10

Library first, CLI second. `WakeDetector` holds no audio device and no network connection,
which is what lets tools/verify_wake.py prove it works with nothing plugged in.

    python audio/wake.py --list-devices     # what can I listen to?
    python audio/wake.py --wav clip.wav     # score a recording
    python audio/wake.py                    # live, prints detections
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol

import numpy as np

LOG = logging.getLogger("oddball.wake")

# Relative paths in config/oddball.toml are anchored here, not at the CWD.
REPO_ROOT = Path(__file__).resolve().parents[1]

# openWakeWord's melspectrogram frontend is built around these. 1280 samples at 16kHz is
# exactly 80ms, and every published threshold assumes that window.
SAMPLE_RATE_HZ = 16_000
FRAME_SAMPLES = 1280
FRAME_MS = FRAME_SAMPLES / SAMPLE_RATE_HZ * 1000.0

# Report a wake attempt that fell short only if it got this far. Above the loudest measured
# negative (0.0885, D27), so the room does not fill the log with noise.
NEAR_MISS_FLOOR = 0.10


@dataclass(frozen=True)
class Detection:
    """One firing of the wake word."""

    model: str      # which loaded model fired
    score: float    # 0..1 confidence at the moment it crossed
    at_s: float     # detector clock, seconds


class ScoringModel(Protocol):
    """The slice of openWakeWord's Model that this detector actually needs.

    Declared as a Protocol so the tests can pass a stub and drive the threshold and
    refractory logic directly, without audio and without a 1MB neural network.
    """

    def predict(self, x: np.ndarray) -> dict: ...
    def reset(self) -> None: ...


class WakeDetector:
    """Scores 80ms frames and reports when the wake phrase is heard.

    Args:
        model:        anything satisfying ScoringModel — normally openwakeword.model.Model.
        threshold:    score in 0..1 above which a frame counts as a detection.
        refractory_s: seconds to ignore further detections after one fires.
        clock:        monotonic seconds source; injectable so tests need no sleeping.
    """

    def __init__(
        self,
        model: ScoringModel,
        threshold: float = 0.5,
        refractory_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold must be in (0, 1], got {threshold}")
        self._model = model
        self._threshold = float(threshold)
        self._refractory_s = float(refractory_s)
        self._clock = clock
        self._last_fire_s = float("-inf")
        self.last_score = 0.0          # most recent frame's best score, for meters
        self._near_peak = 0.0          # best score of an attempt that has not fired

    def feed(self, frame: np.ndarray) -> Detection | None:
        """Score one frame. Returns a Detection on the rising edge, else None.

        Args:
            frame: exactly FRAME_SAMPLES mono int16 samples at SAMPLE_RATE_HZ.

        Raises:
            TypeError:  frame is not int16.
            ValueError: frame is not exactly FRAME_SAMPLES long.

        The size check is deliberate. openWakeWord's predict() accepts a wrongly-sized array
        without complaint and simply produces worse scores, so a resampling or block-size
        mistake upstream would show up as "the wake word got unreliable" rather than as an
        error. Better to refuse it here.
        """
        frame = np.asarray(frame)
        if frame.dtype != np.int16:
            raise TypeError(f"expected int16 samples, got {frame.dtype}")
        if frame.shape != (FRAME_SAMPLES,):
            raise ValueError(
                f"expected exactly {FRAME_SAMPLES} samples ({FRAME_MS:.0f}ms at "
                f"{SAMPLE_RATE_HZ}Hz), got shape {frame.shape}"
            )

        scores = self._model.predict(frame)
        if not scores:
            return None
        name, score = max(scores.items(), key=lambda kv: kv[1])
        self.last_score = float(score)

        if self.last_score < self._threshold:
            # A wake word that does NOT fire leaves no trace, which makes "he stopped hearing
            # me" impossible to tell apart from "he never heard anything at all" — LB reported
            # exactly that on 2026-08-13 and there was nothing in the log to look at.
            #
            # So a near miss is reported once, when the score falls back to the floor: the
            # peak it reached and how far short it fell. 0.10 is above the loudest measured
            # negative (0.0885, D27), so ordinary room noise stays silent.
            if self.last_score >= NEAR_MISS_FLOOR:
                self._near_peak = max(self._near_peak, self.last_score)
            elif self._near_peak >= NEAR_MISS_FLOOR:
                LOG.info("near miss: peaked %.3f, needed %.2f — heard you, scored too low",
                         self._near_peak, self._threshold)
                self._near_peak = 0.0
            return None

        self._near_peak = 0.0

        now = self._clock()
        if now - self._last_fire_s < self._refractory_s:
            return None
        self._last_fire_s = now

        # Clear the frontend's rolling buffer, or the frames still holding the tail of the
        # phrase re-fire the instant the refractory window expires.
        self.reset()
        return Detection(model=name, score=self.last_score, at_s=now)

    def reset(self) -> None:
        """Forget everything heard so far.

        Called on every detection, and again by audio/gate.py when the microphone reopens
        after he has been speaking — the model's rolling buffer is several frames deep, so
        without this the tail of his own voice is still inside it and can fire once the gate
        is already open again.
        """
        self._model.reset()


def ensure_feature_models(framework: str = "onnx") -> Path:
    """Make sure openWakeWord's shared frontend is on disk. Returns the resources directory.

    `melspectrogram` and `embedding_model` are not wake words — they are the feature
    extractor that EVERY wake model runs on top of, custom or pre-trained. openWakeWord does
    not fetch them on demand; it raises a bare NO_SUCHFILE naming a path inside site-packages,
    which reads like a broken install.

    This used to be handled only on the pre-trained-name branch below, which meant it worked
    on any machine that had once used the `hey_jarvis` stand-in and failed on a fresh one
    configured straight to the custom model — exactly what happened on the Pi's first run.
    """
    import openwakeword

    resources = Path(openwakeword.__file__).parent / "resources" / "models"
    ext = "onnx" if framework == "onnx" else "tflite"
    if all((resources / f"{n}.{ext}").exists() for n in ("melspectrogram", "embedding_model")):
        return resources

    LOG.info("openWakeWord's feature models are missing — downloading once into %s", resources)
    # A NON-EMPTY list is what stops it also pulling every pre-trained wake word (~10MB we
    # never load). The feature and VAD models are downloaded unconditionally before that
    # list is consulted, and no official model name contains this sentinel.
    openwakeword.utils.download_models(model_names=["__features_only__"])
    return resources


def build_model(model: str, framework: str = "onnx"):
    """Load an openWakeWord model by pre-trained name or by path to a .onnx/.tflite.

    `framework` must be passed explicitly: openWakeWord defaults to tflite, which has no
    Windows wheels, so leaving it implicit works on the Pi and fails on the desktop.
    """
    import openwakeword
    from openwakeword.model import Model

    resources = ensure_feature_models(framework)
    path = Path(model)
    if path.suffix in {".onnx", ".tflite"}:
        # A relative path in config/oddball.toml means "relative to the repo", not "relative to
        # wherever this was launched from". Resolving against the CWD meant the same config
        # worked from ~/oddball and failed from C:\WINDOWS\System32 — which is exactly where an
        # ssh session lands, so the documented way of starting it was the way that broke.
        candidates = [path] if path.is_absolute() else [Path.cwd() / path, REPO_ROOT / path]
        for candidate in candidates:
            if candidate.exists():
                path = candidate
                break
        else:
            tried = "\n".join(f"  {c}" for c in candidates)
            raise FileNotFoundError(
                f"wake model not found: {model}\ntried:\n{tried}\n"
                f"Models are excluded from the deploy (see CLAUDE.md) and must be copied "
                f"to {REPO_ROOT / 'models'} separately."
            )
        wakeword_models = [str(path)]
    else:
        # A pre-trained name additionally needs its own weights. Same reasoning as
        # ensure_feature_models() above: fetch once here so a new machine works on the first
        # run rather than the second.
        if not any(resources.glob(f"{model}*.{'onnx' if framework == 'onnx' else 'tflite'}")):
            LOG.info("pre-trained model %r not present — downloading once into %s",
                     model, resources)
            openwakeword.utils.download_models(model_names=[model])
        wakeword_models = [model]

    LOG.info("loading wake model %r via %s", model, framework)
    return Model(wakeword_models=wakeword_models, inference_framework=framework)


# --------------------------------------------------------------------------- frame sources

def wav_frames(path: str | Path) -> Iterator[np.ndarray]:
    """Yield FRAME_SAMPLES-sized int16 frames from a mono 16kHz WAV. Trailing partial dropped."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: need 16-bit PCM, got {w.getsampwidth() * 8}-bit")
        if w.getframerate() != SAMPLE_RATE_HZ:
            raise ValueError(f"{path}: need {SAMPLE_RATE_HZ}Hz, got {w.getframerate()}Hz")
        channels = w.getnchannels()
        while True:
            raw = w.readframes(FRAME_SAMPLES)
            block = np.frombuffer(raw, dtype=np.int16)
            if channels > 1:
                block = block.reshape(-1, channels)[:, 0].copy()
            if block.shape[0] < FRAME_SAMPLES:
                return
            yield block


def list_devices() -> str:
    """Human-readable table of available input devices, grouped by host API.

    The host API is not cosmetic. Windows lists the same physical microphone once per API
    (MME, DirectSound, WASAPI, WDM-KS), and the WDM-KS entries cannot be opened by the
    blocking reads this code uses — they fail with PortAudio's
    `Unanticipated host error [-9999]: 'Blocking API not supported yet'`, which names the
    wrong problem. Without the API in this listing there is no way to tell from the output
    which of five identically-named "Microphone" rows is the one that works, so a name
    substring can match an unusable duplicate and look like a broken microphone.
    """
    import sounddevice as sd

    apis = {i: a["name"] for i, a in enumerate(sd.query_hostapis())}
    lines, default_in = [], sd.default.device[0]
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] < 1:
            continue
        mark = "*" if i == default_in else " "
        api = apis.get(d["hostapi"], "?")
        warn = "  <- cannot be opened for blocking reads" if api == "Windows WDM-KS" else ""
        lines.append(
            f" {mark} [{i:>2}] {api:<16} {d['default_samplerate']:>6.0f}Hz  {d['name']}{warn}"
        )
    if not lines:
        return "no input devices found"
    return "input devices (* = system default):\n" + "\n".join(lines)


def _wasapi_settings(device: str | int | None):
    """`WasapiSettings(auto_convert=True)` when `device` is on WASAPI, else None.

    **Without this, a WASAPI device cannot be opened at all**, and the error says nothing
    useful:

        sounddevice.PortAudioError: Error opening InputStream:
            Invalid sample rate [PaErrorCode -9997]

    WASAPI is the only Windows host API that refuses a rate the hardware does not natively
    support. MME and DirectSound are compatibility shims and resample silently, which is why
    they open at 16 kHz on any device and why this never came up until the wake device was
    pinned to WASAPI (2026-08-26). The C270's native rate is 48000; `SAMPLE_RATE_HZ` is 16000,
    because that is what openWakeWord's models take.

    `auto_convert` turns on WASAPI's own sample-rate conversion. So the honest accounting is
    that this costs a resample — the same one MME was doing invisibly — and WASAPI's remaining
    advantages are that it is not a shim and that its converter is the better of the two. An
    earlier version of `docs/DEPLOY.md` claimed WASAPI avoided a resample entirely by taking
    48000 and decimating 3:1. **That was wrong**: nothing in this file decimates, and the
    stream would not have opened to try.

    Returns None off Windows and for every other host API, so this is a no-op everywhere it is
    not needed rather than a branch the caller has to think about.
    """
    import sounddevice as sd

    if not hasattr(sd, "WasapiSettings"):
        return None
    try:
        info = sd.query_devices(device if device not in ("", None) else None, "input")
        host = sd.query_hostapis(info["hostapi"])["name"]
    except Exception:                                                  # noqa: BLE001
        # A device that cannot be queried is a device the InputStream below will reject with a
        # better message than anything this helper could invent.
        return None
    return sd.WasapiSettings(auto_convert=True) if "WASAPI" in host.upper() else None


def mic_frames(device: str | int | None = None) -> Iterator[np.ndarray]:
    """Yield frames from a live microphone until the caller stops consuming.

    Raises:
        ValueError: the chosen device cannot capture, with the current list in the message.

    Device INDICES ARE NOT STABLE. A Bluetooth headset that drops and reconnects renumbers
    everything after it, so an index that worked minutes ago can silently become an output-only
    endpoint. PortAudio's response to that is `Invalid number of channels [PaErrorCode -9998]`,
    which says nothing about the real problem. Check it here and say so plainly instead.
    """
    import sounddevice as sd

    if device not in ("", None):
        try:
            info = sd.query_devices(device)
        except Exception as exc:
            raise ValueError(f"no such input device {device!r}.\n{list_devices()}") from exc
        if info["max_input_channels"] < 1:
            raise ValueError(
                f"device {device!r} is {info['name']!r}, which has no input channels — it "
                f"cannot record.\nDevice numbers shift when Bluetooth devices reconnect; "
                f"prefer a name substring.\n{list_devices()}"
            )

    with sd.InputStream(
        samplerate=SAMPLE_RATE_HZ,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        device=device if device not in ("", None) else None,
        extra_settings=_wasapi_settings(device),
    ) as stream:
        LOG.info("listening at %dHz on %s", SAMPLE_RATE_HZ, stream.device)
        while True:
            block, overflowed = stream.read(FRAME_SAMPLES)
            if overflowed:
                # Dropped audio means a missed syllable, which shows up as a missed wake.
                LOG.warning("input overflow — a frame was dropped")
            yield block[:, 0].copy()


# ---------------------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mr Odd Ball wake-word detector (debug CLI)")
    ap.add_argument("--list-devices", action="store_true", help="show input devices and exit")
    ap.add_argument("--wav", metavar="FILE", help="score a 16kHz mono WAV instead of the mic")
    ap.add_argument("--model", default=None, help="override the configured model")
    ap.add_argument("--threshold", type=float, default=None, help="override the threshold")
    ap.add_argument("--device", default=None,
                    help="input device: index, or a substring of its name")
    ap.add_argument("--meter", action="store_true",
                    help="live score meter. Speak and watch the number — this is how you find "
                         "the right threshold, rather than guessing at openWakeWord's default.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="stop after N seconds (useful over ssh, where ctrl-c is awkward)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_devices:
        print(list_devices())
        return 0

    # Share one config source with the orchestrator rather than keeping a second set of
    # defaults here. Imported inside main() so the library half stays import-clean.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from orchestrator.settings import load_config

    cfg = load_config()
    wake_cfg = cfg["wake"]
    model_name = args.model or wake_cfg["model"]
    threshold = args.threshold if args.threshold is not None else wake_cfg["threshold"]

    detector = WakeDetector(
        model=build_model(model_name, wake_cfg["framework"]),
        threshold=threshold,
        refractory_s=wake_cfg["refractory_s"],
    )

    if args.wav:
        best, hits = 0.0, 0
        for frame in wav_frames(args.wav):
            det = detector.feed(frame)
            best = max(best, detector.last_score)
            if det:
                hits += 1
                print(f"  DETECTED {det.model} score={det.score:.3f} at {det.at_s:.2f}s")
        print(f"{args.wav}: {hits} detection(s), peak score {best:.3f}, threshold {threshold}")
        return 0 if hits else 1

    device = args.device if args.device is not None else wake_cfg["device"]
    if device not in ("", None):
        try:
            device = int(device)
        except ValueError:
            pass                            # leave it as a name substring for sounddevice

    print(f"listening for {model_name!r} at threshold {threshold} on device {device!r}.")
    if args.meter:
        print("speak the wake phrase. peak is the highest score seen so far.\n")

    started, peak, hits = time.monotonic(), 0.0, 0
    try:
        for frame in mic_frames(device):
            det = detector.feed(frame)
            peak = max(peak, detector.last_score)
            if det:
                hits += 1
                print(f"\n  DETECTED {det.model} score={det.score:.3f}")
            elif args.meter:
                # 40-cell bar with the threshold marked, so "nearly fired" is visible
                n = int(detector.last_score * 40)
                mark = int(threshold * 40)
                cells = "".join("#" if i < n else ("|" if i == mark else ".") for i in range(40))
                print(f"\r  [{cells}] now {detector.last_score:.3f}  peak {peak:.3f}  hits {hits}",
                      end="", flush=True)
            if args.seconds and time.monotonic() - started >= args.seconds:
                break
    except KeyboardInterrupt:
        pass

    print(f"\n\npeak score {peak:.3f} against threshold {threshold} — {hits} detection(s)")
    if hits == 0 and peak > 0:
        print(f"  never fired. a threshold of ~{max(0.05, peak * 0.8):.2f} would have.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
