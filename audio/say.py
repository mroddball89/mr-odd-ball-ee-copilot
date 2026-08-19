#!/usr/bin/env python3
"""
Module:  say.py
Purpose: Text to speech — turn a line of text into Mr Odd Ball's voice, and into his mouth.
Author:  LB
Date:    2026-08-11

Library first, CLI second, exactly like audio/wake.py. `Speaker` holds no audio device until
it speaks and takes its synthesiser as an argument, which is what lets tools/verify_speech.py
prove the whole thing with no speaker attached.

    python audio/say.py "hello"                 # speak it
    python audio/say.py "hello" --wav out.wav   # synthesise without a speaker
    python audio/say.py --list-voices           # what voices are installed
    python audio/say.py --list-devices          # where can he speak

The envelope is the point. `speak()` reports a 0..1 loudness for every 20ms block *before*
that block is written to the card, and orchestrator/run_wake.py forwards it to the rig, which
drives his mouth from it. Reporting before the write is deliberate: the mouth then leads the
sound by one block, and leading reads as lip-sync where lagging reads as dubbing.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
import wave
from collections import deque
from pathlib import Path
from typing import Callable, Iterator, Protocol

import numpy as np

LOG = logging.getLogger("oddball.say")

# Relative voice paths in config/oddball.toml are anchored here, not at the CWD — the same
# rule as the wake model, and for the same reason: an ssh session starts in System32.
REPO_ROOT = Path(__file__).resolve().parents[1]
VOICES_DIR = REPO_ROOT / "voices"

# One envelope value per 20ms. Fast enough that his mouth tracks individual syllables
# (~4-5Hz in speech, so ~10 blocks each), slow enough that the rig link carries ~50 small
# messages a second rather than thousands.
BLOCK_MS = 20.0

# Envelope shaping. Speech RMS spends most of its time low, so a linear mapping leaves his
# mouth nearly shut through ordinary talking. 0.6 lifts the quiet parts without flattening
# the loud ones, and matches the exponent the rig's own synthetic envelope already uses
# (`Math.pow(syl, 0.55)` in hud/face-preview.html) so live and synthetic look like the
# same character rather than two different mouths.
ENVELOPE_CURVE = 0.6


class SynthFn(Protocol):
    """Anything that turns text into mono float32 audio.

    Declared as a Protocol so the verifier can hand `Speaker` a sine wave instead of a
    60MB neural network, and so a second engine could be dropped in without touching
    playback, the envelope, or the mic gate.
    """

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        """Returns (mono float32 samples in [-1, 1], sample rate in Hz)."""
        ...


# ------------------------------------------------------------------------------- synthesis

class PiperSynth:
    """Piper TTS. Loads the voice once and keeps it; loading costs ~0.4s, speaking does not.

    Args:
        voice: a bare voice name ("en_US-joe-medium"), or a path to a .onnx.
        voices_dir: where bare names are looked up.
    """

    def __init__(self, voice: str, voices_dir: str | Path = VOICES_DIR) -> None:
        self.path = resolve_voice(voice, voices_dir)
        from piper import PiperVoice

        LOG.info("loading voice %s", self.path.stem)
        self._voice = PiperVoice.load(str(self.path))
        self.sample_rate = int(self._voice.config.sample_rate)

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        # Piper yields one chunk per sentence, each normalised to full scale on its own.
        chunks = [c.audio_float_array for c in self._voice.synthesize(text)]
        if not chunks:
            return np.zeros(0, dtype=np.float32), self.sample_rate
        return np.concatenate(chunks).astype(np.float32), self.sample_rate


def resolve_voice(voice: str, voices_dir: str | Path = VOICES_DIR) -> Path:
    """Find a voice file from a name or a path.

    Raises:
        FileNotFoundError: naming what was tried, what *is* installed, and the one command
            that fixes it. Voices are gitignored (63MB each), so "missing" is the normal
            state of a fresh checkout rather than an error — the message has to say so.
    """
    voices_dir = Path(voices_dir)
    path = Path(voice)
    candidates = (
        [path] if path.is_absolute()
        else [Path.cwd() / path, REPO_ROOT / path] if path.suffix == ".onnx"
        else [voices_dir / f"{voice}.onnx"]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate

    installed = sorted(p.stem for p in voices_dir.glob("*.onnx")) if voices_dir.is_dir() else []
    tried = "\n".join(f"  {c}" for c in candidates)
    have = ("\ninstalled voices:\n" + "\n".join(f"  {v}" for v in installed)) if installed \
        else "\nno voices are installed."
    raise FileNotFoundError(
        f"voice not found: {voice}\ntried:\n{tried}{have}\n"
        f"install one with:\n"
        f"  python -m piper.download_voices {voice} --data-dir {voices_dir}"
    )


def list_voices(voices_dir: str | Path = VOICES_DIR) -> str:
    """Human-readable list of installed voices."""
    voices_dir = Path(voices_dir)
    found = sorted(voices_dir.glob("*.onnx")) if voices_dir.is_dir() else []
    if not found:
        return (f"no voices in {voices_dir}\n"
                f"  python -m piper.download_voices en_US-joe-medium --data-dir {voices_dir}")
    lines = [f"  {p.stem:<38} {p.stat().st_size / 1e6:5.1f}MB" for p in found]
    return f"voices in {voices_dir}:\n" + "\n".join(lines)


# -------------------------------------------------------------------------------- envelope

def envelope_blocks(
    audio: np.ndarray,
    sample_rate: int,
    block_ms: float = BLOCK_MS,
    curve: float = ENVELOPE_CURVE,
) -> Iterator[tuple[np.ndarray, float]]:
    """Split audio into blocks, each paired with its 0..1 loudness.

    Args:
        audio:       mono float32 in [-1, 1].
        sample_rate: Hz.
        block_ms:    block length; one envelope value is produced per block.
        curve:       exponent applied to the normalised RMS. See ENVELOPE_CURVE.

    Yields:
        (block, envelope) where envelope is in [0, 1] and 1.0 is the loudest block of
        *this* utterance.

    Normalising against the utterance's own peak rather than a fixed reference is what makes
    his mouth behave the same on a quiet voice as on a loud one, and keeps the mouth
    independent of the volume setting — turning him down should not make him mumble visually.
    """
    block = max(1, int(sample_rate * block_ms / 1000.0))
    if audio.size == 0:
        return

    n = audio.size - audio.size % block          # a trailing partial block has no envelope
    if n == 0:
        n, block = audio.size, audio.size
    frames = audio[:n].reshape(-1, block)

    rms = np.sqrt(np.mean(np.square(frames.astype(np.float64)), axis=1))
    peak = float(rms.max())
    # An all-silent utterance would divide by zero; it also has nothing to animate.
    env = np.power(rms / peak, curve) if peak > 0 else np.zeros_like(rms)

    for i in range(frames.shape[0]):
        yield frames[i], float(np.clip(env[i], 0.0, 1.0))

    tail = audio[n:]
    if tail.size:
        yield tail, 0.0                          # trailing scrap: play it, close his mouth


# -------------------------------------------------------------------------------- playback

def as_device(device: str | int | None) -> str | int:
    """Normalise a device from config or the command line.

    `"7"` from a TOML file or an argv is an index, not a name. Passing the string straight
    through makes sounddevice do a *name* lookup for a device literally called "7", which
    fails with "no such device" while the device sits right there in the list — a confusing
    ten minutes the first time. Indices themselves remain a bad idea for anything lasting;
    they shift when devices reconnect.
    """
    if device in ("", None):
        return ""
    if isinstance(device, int):
        return device
    try:
        return int(device)
    except ValueError:
        return device               # a name substring; sounddevice resolves it


# Characters Piper cannot voice. It renders them as silence or as a stumble, and either is
# audible in a way that no code review catches — the text looks fine on screen.
#
# Found live on the Pi 2026-08-13: LFM2.5 answered "Yes, I'm right here on that little
# Raspberry Pi! 🤓 What's up with you?" despite a persona that forbids emoji in as many words.
# Every model does this; brains/gemini.py already strips markdown for the same reason, having
# been told just as plainly not to emit it.
#
# This lives HERE, not in the brains, because it is a property of the synthesiser rather than
# of any one caller. Applying it in Speaker means greetings, Tier 0, Tier 1 and Tier 3 are all
# covered by one rule that cannot be forgotten at a new call site.
_UNSPEAKABLE = re.compile(
    "["
    "\U0001f000-\U0001faff"      # emoticons, pictographs, transport, supplemental symbols
    "\U00002600-\U000027bf"      # miscellaneous symbols and dingbats
    "\U00002190-\U000021ff"      # arrows
    "\U00002b00-\U00002bff"      # miscellaneous symbols and arrows
    "\U0000fe00-\U0000fe0f"      # variation selectors, which follow an emoji
    "\U0000200d"                 # zero-width joiner, which glues emoji together
    "]+"
)
# Markdown that survives a system prompt forbidding it. Piper reads an asterisk as nothing or
# as a stumble; a backtick fares no better.
_MARKUP = re.compile(r"[*_`#]+")


def speakable(text: str) -> str:
    """Strip what Piper cannot pronounce, leaving ordinary punctuation alone.

    Deliberately narrow: it removes symbols and markup, never words, and never the sentence
    punctuation that gives the voice its prosody. Returns "" only if there was nothing
    pronounceable to begin with.
    """
    text = _UNSPEAKABLE.sub(" ", text)
    text = _MARKUP.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def resample(audio: np.ndarray, from_hz: int, to_hz: int) -> np.ndarray:
    """Rate-convert mono float audio. Uses scipy, already present via openwakeword."""
    if from_hz == to_hz:
        return audio
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(from_hz, to_hz)
    return resample_poly(audio, to_hz // g, from_hz // g).astype(np.float32)


class _NullStream:
    """A sink that accepts audio and drops it. Used by --wav and by the verifier."""

    rate = None                                  # "whatever you have"; see Speaker.speak()

    def write(self, block: np.ndarray) -> None:  # noqa: D102
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass


class Speaker:
    """Speaks text aloud, reporting a mouth-opening value as it goes.

    Args:
        synth:       anything satisfying SynthFn.
        device:      output device index or name substring; "" is the system default.
        volume:      0..1 scale applied to playback only, never to the envelope.
        block_ms:    envelope/playback block size.
        open_stream: injectable sink factory taking (sample_rate) — defaults to sounddevice.
                     The verifier passes one that plays nothing, which is how every rule in
                     here is tested on a machine with no speaker.
    """

    def __init__(
        self,
        synth: SynthFn,
        device: str | int = "",
        volume: float = 0.8,
        block_ms: float = BLOCK_MS,
        open_stream: Callable[[int], object] | None = None,
        extra_latency_ms: float = 0.0,
        prime_ms: float = 200.0,
    ) -> None:
        if not 0.0 < volume <= 1.0:
            raise ValueError(f"volume must be in (0, 1], got {volume}")
        if extra_latency_ms < 0:
            raise ValueError(f"extra_latency_ms must be >= 0, got {extra_latency_ms}")
        if prime_ms < 0:
            raise ValueError(f"prime_ms must be >= 0, got {prime_ms}")
        self._synth = synth
        self._device = as_device(device)
        self._volume = float(volume)
        self._block_ms = float(block_ms)
        self._extra_latency_s = float(extra_latency_ms) / 1000.0
        self._prime_ms = float(prime_ms)
        self._open_stream = open_stream or self._sounddevice_stream
        self.last_duration_s = 0.0

    def _sounddevice_stream(self, sample_rate: int):
        import sounddevice as sd

        device = self._device if self._device not in ("", None) else None
        if device is not None:
            try:
                info = sd.query_devices(device)
            except Exception as exc:
                # Do NOT flatten this into "no such device". The common failure on Windows is
                # the opposite — the substring matches the same physical speaker once per host
                # API (MME, DirectSound, WASAPI, WDM-KS) and sounddevice refuses to guess. Its
                # own message lists the candidates, which is exactly what you need to pick an
                # index; overwriting it names the wrong problem, the same way PortAudio's
                # -9998 and -9999 did on the input side.
                raise ValueError(
                    f"could not use output device {device!r}: {exc}\n"
                    f"On Windows the same speaker appears once per host API, so a name "
                    f"substring is often ambiguous — use an index from the list below.\n"
                    f"{list_devices()}"
                ) from exc
            # The mirror of mic_frames()' input check. PortAudio's own error for this is
            # `Invalid number of channels [-9998]`, which names the wrong problem.
            if info["max_output_channels"] < 1:
                raise ValueError(
                    f"device {device!r} is {info['name']!r}, which has no output channels — "
                    f"it cannot play.\n{list_devices()}"
                )
        # Negotiate the rate rather than assuming the card will take the voice's.
        # Piper speaks at 22050Hz; WASAPI and the Pi's HDMI output are commonly 48000-only
        # and refuse anything else with PortAudio's `Invalid sample rate [-9997]`. MME and
        # DirectSound resample silently, so this fails on some devices and not others on the
        # very same machine — which reads like a broken voice rather than a rate mismatch.
        rate = int(sample_rate)
        try:
            sd.check_output_settings(device=device, samplerate=rate, channels=1,
                                     dtype="float32")
        except Exception:
            rate = int(sd.query_devices(device if device is not None
                                        else sd.default.device[1])["default_samplerate"])
            LOG.info("device will not take %dHz — resampling to %dHz", sample_rate, rate)

        # latency="low" because the default is the device's HIGH latency — often 100-200ms of
        # buffer. write() returns as soon as there is room, so it runs that far ahead of what
        # is actually audible, and anything keyed to write() (his mouth) runs ahead with it.
        # speak() compensates for whatever is left by reading stream.latency back.
        stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32",
                                 blocksize=int(rate * self._block_ms / 1000.0),
                                 device=device, latency="low")
        stream.rate = rate            # speak() reads this back and converts to match
        return stream

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        """Synthesise without playing. Returns (mono float32, sample rate)."""
        return self._synth(speakable(text))

    def speak(
        self,
        text: str,
        on_envelope: Callable[[float], None] | None = None,
        on_start: Callable[[], None] | None = None,
    ) -> float:
        """Say `text` aloud. Returns its duration in seconds.

        Args:
            text:        what to say.
            on_envelope: called with 0..1 per block, describing the audio being *heard* at
                         that moment, and once with 0.0 at the end so his mouth closes
                         rather than sticking open on the last syllable.
            on_start:    called once when sound actually begins — after synthesis, before
                         the first block is queued. Callers use it to enter the `speaking`
                         state, because synthesis takes ~0.4s and announcing it any earlier
                         means his mouth moves through nearly half a second of silence.

        Blocks until playback finishes; run it off the event loop.
        """
        audio, sample_rate = self._synth(text)
        self.last_duration_s = audio.size / sample_rate if sample_rate else 0.0
        if audio.size == 0:
            LOG.warning("nothing to say — synthesiser returned no audio for %r", text)
            if on_start:
                on_start()
            if on_envelope:
                on_envelope(0.0)
            return 0.0

        emit = on_envelope or (lambda _v: None)
        with self._open_stream(sample_rate) as stream:
            # The sink advertises the rate it will actually accept; a card that cannot take
            # the voice's rate gets the audio converted rather than an exception.
            rate = getattr(stream, "rate", None) or sample_rate
            if rate != sample_rate:
                audio = resample(audio, sample_rate, rate)
                self.last_duration_s = audio.size / rate

            # write() returns when there is ROOM in the buffer, not when the audio is heard,
            # so it runs ahead of the speaker by the stream's latency. Sending the envelope
            # at write time therefore moves his mouth before the sound arrives — which is
            # exactly what it looked like. Hold each value back by that many blocks so the
            # number describes what is currently audible.
            #
            # `extra_latency_s` covers what PortAudio CANNOT see. Bluetooth A2DP buffers
            # 150-250ms downstream of the sound card, so on the Pi with the Bose connected
            # PortAudio reports 20ms and is wrong by an order of magnitude.
            block_s = self._block_ms / 1000.0
            reported = float(getattr(stream, "latency", 0.0) or 0.0)
            lead = min(50, max(0, round((reported + self._extra_latency_s) / block_s)))
            LOG.debug("output latency %.0fms reported + %.0fms configured — "
                      "delaying the mouth by %d blocks (%.0fms)",
                      reported * 1000, self._extra_latency_s * 1000, lead, lead * block_s * 1000)

            # WAKE THE SINK BEFORE THE FIRST REAL SAMPLE.
            #
            # PipeWire suspends an idle output node, and resuming it swallows the start of
            # whatever arrives. Measured on the Pi 2026-08-15 with `pw-dump`:
            #
            #     node: alsa_output.platform-107c701400.hdmi.hdmi-stereo   state: "suspended"
            #
            # LB heard that as "the beginning is inaudible" — the first word clipped on every
            # reply, worst after a pause, which is exactly when the node has had time to
            # suspend. Writing silence first pays the wake-up cost where nothing is lost.
            #
            # It goes BEFORE on_start() and before the envelope loop on purpose. Padding the
            # audio without padding the animation is the whole point: his mouth still begins
            # with the first real syllable. Pad both and the clipping is gone but the lip-sync
            # is out by the padding, which trades one visible fault for another.
            #
            # Fixing it here rather than in WirePlumber (`session.suspend-timeout-seconds=0`)
            # is deliberate: this travels with the deploy, survives a rebuild of the Pi, and
            # does not hold an HDMI sink awake forever for a device that talks a few times an
            # hour.
            priming = int(rate * self._prime_ms / 1000.0)
            if priming > 0:
                stream.write(np.zeros(priming, dtype=np.float32))

            if on_start:
                on_start()
            pending: deque[float] = deque()
            for block, env in envelope_blocks(audio, rate, self._block_ms):
                pending.append(env)
                stream.write(np.ascontiguousarray(block * self._volume, dtype=np.float32))
                if len(pending) > lead:
                    emit(pending.popleft())
            # The last `lead` blocks are written but not yet heard. Pace them out in real
            # time, or his mouth stops moving before he stops talking.
            while pending:
                time.sleep(block_s)
                emit(pending.popleft())
        emit(0.0)
        return self.last_duration_s

    def to_wav(self, text: str, path: str | Path) -> float:
        """Synthesise to a 16-bit mono WAV. Returns duration in seconds."""
        audio, sample_rate = self._synth(text)
        pcm = np.clip(audio, -1.0, 1.0)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes((pcm * 32767.0).astype("<i2").tobytes())
        return audio.size / sample_rate if sample_rate else 0.0


def list_devices() -> str:
    """Human-readable table of output devices, grouped by host API.

    Same shape and same reason as audio/wake.py's input listing: Windows enumerates one row
    per host API, and which row you pick decides whether it works.
    """
    import sounddevice as sd

    apis = {i: a["name"] for i, a in enumerate(sd.query_hostapis())}
    lines, default_out = [], sd.default.device[1]
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] < 1:
            continue
        mark = "*" if i == default_out else " "
        api = apis.get(d["hostapi"], "?")
        lines.append(
            f" {mark} [{i:>2}] {api:<16} {d['default_samplerate']:>6.0f}Hz  {d['name']}"
        )
    if not lines:
        return "no output devices found"
    return "output devices (* = system default):\n" + "\n".join(lines)


# ------------------------------------------------------------------------------------ cli

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mr Odd Ball's voice (debug CLI)")
    ap.add_argument("text", nargs="?", help="what to say")
    ap.add_argument("--voice", default=None, help="override speech.voice")
    ap.add_argument("--device", default=None, help="output device index or name substring")
    ap.add_argument("--volume", type=float, default=None, help="override speech.volume")
    ap.add_argument("--wav", metavar="FILE", help="write to a WAV instead of playing it")
    ap.add_argument("--list-voices", action="store_true", help="show installed voices and exit")
    ap.add_argument("--list-devices", action="store_true", help="show output devices and exit")
    ap.add_argument("--meter", action="store_true",
                    help="print the mouth envelope as a bar while he speaks — this is what "
                         "the rig receives, made visible without a browser")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_voices:
        print(list_voices())
        return 0
    if args.list_devices:
        print(list_devices())
        return 0
    if not args.text:
        ap.error("say what? pass some text, or --list-voices / --list-devices")

    # One config source with the orchestrator, as in audio/wake.py. Imported here so the
    # library half stays import-clean.
    sys.path.insert(0, str(REPO_ROOT))
    from orchestrator.settings import load_config

    speech = load_config()["speech"]
    speaker = Speaker(
        synth=PiperSynth(args.voice or speech["voice"]),
        device=args.device if args.device is not None else speech["device"],
        volume=args.volume if args.volume is not None else speech["volume"],
        extra_latency_ms=speech["output_latency_ms"],
        prime_ms=speech["prime_ms"],
        open_stream=(lambda _sr: _NullStream()) if args.wav else None,
    )

    if args.wav:
        secs = speaker.to_wav(args.text, args.wav)
        print(f"wrote {args.wav} — {secs:.2f}s")
        return 0

    peak = 0.0

    def meter(v: float) -> None:
        nonlocal peak
        peak = max(peak, v)
        n = int(v * 40)
        print(f"\r  mouth [{'#' * n}{'.' * (40 - n)}] {v:.2f}", end="", flush=True)

    secs = speaker.speak(args.text, on_envelope=meter if args.meter else None)
    print(f"\n{secs:.2f}s" + (f", peak envelope {peak:.2f}" if args.meter else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
