#!/usr/bin/env python3
"""
Module:  oww_compat.py
Purpose: Make openWakeWord's training modules importable on this machine. Import FIRST.
Author:  LB
Date:    2026-09-08

## The one incompatibility, and why it is shimmed rather than worked around

`openwakeword.data` imports `acoustics` at module level, and `acoustics` 0.2.6 fails to import
against scipy 1.17:

    acoustics/directivity.py:20  from scipy.special import sph_harm
    ImportError: cannot import name 'sph_harm' from 'scipy.special'

scipy removed `sph_harm` and renamed it `sph_harm_y`. Nothing else about `acoustics` is broken.

**`acoustics` is used for exactly one call in the whole training pipeline** —
`openwakeword/data.py:434`:

    noise_clip = acoustics.generator.noise(combined_size, color=np.random.choice(noise_color))

That is coloured noise for augmentation, and it lives in `acoustics.generator`, which has
nothing to do with `acoustics.directivity` or with spherical harmonics. The import chain fails
on a subpackage the training code never touches.

## Three fixes were available; this is the least bad

  1. **Downgrade scipy.** Refused. `orchestrator/`, `tools/quiz_grade.py` and the whole signal
     path run on scipy 1.17 on this box, and pinning it backwards to train one wake model
     would put the assistant's own dependencies at risk for a build step.
  2. **Stub out `acoustics` entirely** with a fake module exposing a hand-written `noise()`.
     Refused: it silently swaps the real coloured-noise generator for a reimplementation, and
     augmentation quality is the thing we are trying to improve. A shim that changes the data
     is not a shim.
  3. **Alias the one removed name.** Taken. `acoustics` then imports for real and the genuine
     `acoustics.generator.noise` is used, unmodified.

## The argument order, stated because it is a guess this file cannot verify

`sph_harm(m, n, theta, phi)` took azimuth as `theta`; `sph_harm_y(n, m, theta, phi)` takes
polar as `theta`. The mapping below swaps both the degree/order pair and the angles to match.

**It is never called.** Nothing in the training path evaluates a spherical harmonic — the alias
exists only so that `import acoustics.directivity` succeeds. If some future code DOES call it,
verify the convention against scipy's own docs rather than trusting this line.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger("oddball.training")

__all__ = ["patch", "patch_torch_load", "patch_torchaudio_load", "patch_trim_mmap"]

_PATCHED = False
_LOAD_PATCHED = False
_TA_PATCHED = False
_TRIM_PATCHED = False


def patch() -> bool:
    """Install the scipy compatibility alias. Idempotent. Returns True if it was needed.

    Call this BEFORE importing `openwakeword.data` or `openwakeword.train`, or the import of
    `acoustics` has already failed and the alias is too late.
    """
    global _PATCHED
    if _PATCHED:
        return False

    import scipy.special as sp                                        # noqa: PLC0415

    _PATCHED = True
    if hasattr(sp, "sph_harm"):
        # A newer acoustics, or an older scipy. Nothing to do, and saying so matters: a shim
        # that stays silent when it did nothing is a shim nobody can tell is dead.
        LOG.debug("scipy.special.sph_harm is present — no shim needed")
        return False

    if not hasattr(sp, "sph_harm_y"):
        raise ImportError(
            "scipy.special has neither sph_harm nor sph_harm_y. This shim was written for the "
            "1.17 rename and does not fit this scipy; check what `acoustics` needs before "
            "training.")

    sp.sph_harm = lambda m, n, theta, phi: sp.sph_harm_y(n, m, phi, theta)
    LOG.info("shimmed scipy.special.sph_harm -> sph_harm_y so `acoustics` imports")
    return True


def patch_torch_load() -> bool:
    """Let `torch.load` read the piper-sample-generator checkpoint. Returns True if applied.

    ## What breaks without it

    PyTorch 2.6 changed `torch.load`'s `weights_only` default from False to True. The
    piper-sample-generator v2.0.0 checkpoint is not a state dict — it is a pickled
    `piper_train.vits.models.SynthesizerTrn`, so the safe loader refuses it:

        UnpicklingError: Weights only load failed.
        Unsupported global: GLOBAL piper_train.vits.models.SynthesizerTrn

    ## Why `weights_only=False` is acceptable HERE and is not a general habit

    `weights_only=True` exists because unpickling executes arbitrary code, so a checkpoint from
    an untrusted source is a remote-code-execution vector. That is a real risk and this is not
    an argument that it does not matter.

    It does not apply to this file. `training/fetch_corpora.py` and the documented setup pull
    `en_US-libritts_r-medium.pt` from the rhasspy/piper-sample-generator GitHub **release**,
    which is the upstream project's own signed artefact — the same trust boundary as the source
    we already run. Torch's own message names exactly this case: *"do those steps only if you
    trust the source of the checkpoint."*

    The alternative, `add_safe_globals([SynthesizerTrn])`, was considered and not used: it
    allowlists one class at a time and the VITS checkpoint pulls in a chain of them, so it turns
    into a guess-and-retry loop that ends up trusting the same file with more ceremony.

    ## Why it lives here rather than in the generator

    `training/piper-sample-generator/` is a gitignored clone of a third-party repo. An edit
    there is invisible to git, unreviewable, and silently lost the next time anybody re-clones
    it — which the setup instructions tell them to do. This module is tracked.

    **Scoped, not global.** Only calls that do not already state `weights_only` are affected,
    so any caller with an opinion keeps it.
    """
    global _LOAD_PATCHED
    if _LOAD_PATCHED:
        return False

    import torch                                                      # noqa: PLC0415

    _LOAD_PATCHED = True
    original = torch.load

    def _load(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return original(*args, **kwargs)

    torch.load = _load
    LOG.info("torch.load defaults to weights_only=False for the piper checkpoint")
    return True


def patch_torchaudio_load() -> bool:
    """Route `torchaudio.load` through soundfile. Returns True if applied.

    ## What breaks without it

    torchaudio 2.11 removed its native I/O backends and routes every `load()` through
    TorchCodec:

        ImportError: TorchCodec is required for load_with_torchcodec.

    `backend="soundfile"` does not help — the argument is still accepted and then ignored.
    Augmentation dies on the first clip it tries to read, which is 12 seconds into a phase that
    otherwise runs for hours.

    ## Why not just install TorchCodec

    Tried first, and it is worse than not having it. The wheel installs, then fails to load its
    own DLL:

        OSError: Could not load this library: ...\\torchcodec\\libtorchcodec_core4.dll

    It needs FFmpeg shared libraries that are not on this box, and once installed it poisons the
    import path — `torchaudio.load` then raises the DLL error instead of the clean ImportError,
    so the failure gets harder to read rather than easier. It was uninstalled.

    ## Why soundfile is sufficient HERE, stated as a limit rather than a claim

    soundfile is libsndfile: WAV, FLAC, OGG. It cannot read MP3 or anything FFmpeg-only.

    **Everything this pipeline reads is 16 kHz mono WAV** — the synthetic clips piper writes,
    the MIT impulse responses, and LB's own captures, which `audio/turn.py` writes with the
    `wave` module. So the formats soundfile lacks are formats that do not appear.

    If a future corpus arrives as MP3, this shim is the thing that will fail, and it will fail
    loudly on the first file rather than silently degrading. That is the right failure.

    ## The contract

    Matches `torchaudio.load`'s signature for the arguments openWakeWord and speechbrain
    actually pass: returns `(waveform, sample_rate)` with waveform shaped
    `(channels, frames)` when `channels_first`, float32 in [-1, 1] when `normalize`.
    """
    global _TA_PATCHED
    if _TA_PATCHED:
        return False

    import torch                                                      # noqa: PLC0415
    import torchaudio                                                 # noqa: PLC0415

    _TA_PATCHED = True

    # If a working backend exists, leave it alone. A shim that fires when it is not needed is a
    # shim that hides a working library behind a narrower one.
    try:
        import numpy as _np                                           # noqa: PLC0415

        with __import__("tempfile").NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            probe = tmp.name
        import wave as _wave                                          # noqa: PLC0415

        with _wave.open(probe, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16_000)
            handle.writeframes(b"\x00\x00" * 160)
        torchaudio.load(probe)
        __import__("os").unlink(probe)
        LOG.debug("torchaudio.load works natively — no shim needed")
        return False
    except Exception:                                                 # noqa: BLE001
        pass

    import soundfile as sf                                            # noqa: PLC0415

    def _load(uri, frame_offset=0, num_frames=-1, normalize=True,
              channels_first=True, format=None, buffer_size=4096, backend=None):
        data, sample_rate = sf.read(
            str(uri), start=int(frame_offset),
            frames=int(num_frames) if num_frames not in (-1, None) else -1,
            dtype="float32" if normalize else "int16", always_2d=True)
        tensor = torch.from_numpy(data)          # soundfile gives (frames, channels)
        if channels_first:
            tensor = tensor.T.contiguous()
        return tensor, sample_rate

    torchaudio.load = _load

    # `torchaudio.info` is not merely routed through TorchCodec in 2.11 — it is GONE:
    #
    #     AttributeError: module 'torchaudio' has no attribute 'info'
    #
    # `openwakeword/data.py` calls it in three places (:220, :253, :271) to get a clip's
    # duration before deciding how to batch it. Two of those wrap the call in
    # `except RuntimeError`, so this raises RuntimeError on a bad file rather than whatever
    # soundfile would throw — otherwise their guard does not catch it and augmentation dies on
    # one unreadable clip instead of skipping it.
    class _Info:
        __slots__ = ("sample_rate", "num_frames", "num_channels", "bits_per_sample", "encoding")

        def __init__(self, handle):
            self.sample_rate = handle.samplerate
            self.num_frames = handle.frames
            self.num_channels = handle.channels
            self.bits_per_sample = {"PCM_16": 16, "PCM_24": 24, "PCM_32": 32,
                                    "PCM_S8": 8, "PCM_U8": 8, "FLOAT": 32,
                                    "DOUBLE": 64}.get(handle.subtype, 0)
            self.encoding = handle.subtype

    def _info(uri, format=None, buffer_size=4096, backend=None):
        try:
            return _Info(sf.SoundFile(str(uri)))
        except Exception as exc:                                      # noqa: BLE001
            raise RuntimeError(f"could not read metadata from {uri}: {exc}") from exc

    torchaudio.info = _info

    LOG.info("torchaudio.load and .info routed through soundfile "
             "(torchaudio %s has no native backend)", torchaudio.__version__)
    return True


def patch_trim_mmap() -> bool:
    """Replace `openwakeword.data.trim_mmap`, which cannot complete on Windows. True if applied.

    ## Two upstream bugs, in eight lines of code

    `trim_mmap` drops the unused rows off the end of a feature file by copying the full rows
    into a new mmap, deleting the original, and renaming. Both of the last two steps are wrong.

    **1. It deletes a file it still has open.**

        mmap_file1 = np.load(mmap_path, mmap_mode='r')
        ...
        os.remove(mmap_path)
        PermissionError: [WinError 32] The process cannot access the file because it is
        being used by another process

    The other process is itself. POSIX allows unlinking an open file, so this is invisible on
    Linux and fatal on Windows — it killed augmentation at 17:25, after every one of the 80,300
    positive clips had already been featurised.

    **2. `mmap_path.strip(".npy")` is not a suffix strip.** `str.strip` removes any of the
    CHARACTERS ".", "n", "p", "y" from both ends, so it eats the "n" of "train" too:

        'positive_features_train.npy'.strip('.npy')  ->  'positive_features_trai'

    which is where the stray `positive_features_trai2.npy` on disk came from. Harmless on its
    own — the name is only ever temporary — but it means the leftover from a failed run does
    not look like the file it belongs to, so nobody recognises it as debris.

    ## What this replacement changes, and what it does not

    The trimming logic, the batch size, and the dtype are copied verbatim. The only differences
    are `removesuffix` instead of `strip`, and closing both memmaps before touching the
    filesystem. It is deliberately not an improvement: a rewrite here would be a second
    implementation of something openWakeWord may fix upstream.

    Patched on `openwakeword.data`, which is where `compute_features_from_generator` imports it
    from **at call time** (`utils.py:563` does the import inside the function), so replacing the
    module attribute is enough — there is no already-bound reference to miss.
    """
    global _TRIM_PATCHED
    if _TRIM_PATCHED:
        return False

    import gc                                                         # noqa: PLC0415
    import os                                                         # noqa: PLC0415

    import numpy as np                                                # noqa: PLC0415

    # `openwakeword.data` imports `acoustics`, which needs the scipy alias. Called here rather
    # than assumed, so this function works whatever order a caller applies the patches in.
    patch()

    import openwakeword.data as oww_data                              # noqa: PLC0415
    from numpy.lib.format import open_memmap                          # noqa: PLC0415
    from tqdm import tqdm                                             # noqa: PLC0415

    _TRIM_PATCHED = True

    def trim_mmap(mmap_path):
        mmap_file1 = np.load(mmap_path, mmap_mode="r")
        index = -1
        while np.all(mmap_file1[index, :, :] == 0):
            index -= 1
        n_new = mmap_file1.shape[0] + index + 1

        output_file2 = str(mmap_path).removesuffix(".npy") + "2.npy"
        mmap_file2 = open_memmap(output_file2, mode="w+", dtype=np.float32,
                                 shape=(n_new, mmap_file1.shape[1], mmap_file1.shape[2]))

        for start in tqdm(range(0, mmap_file1.shape[0], 1024),
                          total=mmap_file1.shape[0] // 1024, desc="Trimming empty rows"):
            stop = min(start + 1024, n_new)
            if start >= n_new:
                break
            mmap_file2[start:stop] = mmap_file1[start:stop].copy()
        mmap_file2.flush()

        # **Both handles closed before the filesystem is touched.** This is the whole fix.
        del mmap_file1
        del mmap_file2
        gc.collect()

        os.remove(mmap_path)
        os.rename(output_file2, mmap_path)

    oww_data.trim_mmap = trim_mmap
    LOG.info("patched openwakeword.data.trim_mmap (WinError 32 on remove, and a strip() that "
             "ate a letter of the filename)")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    did = patch()
    print(f"shim applied: {did}")

    import acoustics                                                  # noqa: E402
    import numpy as np                                                # noqa: E402

    for colour in ("white", "pink", "brown"):
        clip = acoustics.generator.noise(16_000, color=colour)
        print(f"  noise({colour:6}) -> {clip.shape}  rms {np.sqrt((clip ** 2).mean()):.3f}")

    import openwakeword.data                                          # noqa: E402,F401
    import openwakeword.train                                         # noqa: E402,F401
    print("  openwakeword.data and .train import cleanly")
