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

__all__ = ["patch", "patch_torch_load", "patch_torchaudio_load", "patch_trim_mmap", "patch_dataloader"]

_PATCHED = False
_LOAD_PATCHED = False
_TA_PATCHED = False
_TRIM_PATCHED = False
_DL_PATCHED = False


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
    """Make openWakeWord's feature trimming work on Windows. True if applied.

    ## The bug is not where the traceback points

    `trim_mmap` fails at `os.remove(mmap_path)` with WinError 32, so the obvious fix is to
    close the memmap `trim_mmap` itself opened. That was tried and it still failed, because the
    handle holding the file is the CALLER's:

        # openwakeword/utils.py, compute_features_from_generator
        fp = open_memmap(output_file, mode='w+', ...)
        ... fills fp ...
        trim_mmap(output_file)          # fp is still open, in this scope

    POSIX lets you unlink an open file, so upstream never sees this. Windows does not, and it
    killed augmentation at 17:02 — after all 80,300 clips had been featurised, twice.

    ## Deferring the trim instead of vendoring the caller

    The fix is to run the trim after `compute_features_from_generator` RETURNS, at which point
    `fp` has gone out of scope and the handle is released.

    So `trim_mmap` is replaced by a recorder that only notes the path, and
    `compute_features_from_generator` is wrapped: call the original, let it "trim" into the
    recorder, then collect and do the real trim with nothing else holding the file.

    The alternative was copying the 40-line caller into this repo to add one `del`. Rejected —
    that is a second implementation of a function upstream may change, and it would silently
    stop tracking their version. The wrapper touches nothing about how features are computed.

    ## Also fixed: a suffix strip that is not one

    `mmap_path.strip(".npy")` removes any of the CHARACTERS ".npy" from both ends, so it eats
    the "n" of "train":

        'positive_features_train.npy'.strip('.npy') -> 'positive_features_trai'

    which is where the stray `positive_features_trai2.npy` came from. Harmless in itself, but
    debris from a failed run should look like the file it belongs to.

    ## And the no-op case

    When nothing needs trimming — which is every run here, because `n_total` is an exact file
    count and the writer fills exactly that many rows — the copy, the delete and the rename are
    all skipped. That is not merely faster: it means the common path never touches the
    filesystem operation that breaks.
    """
    global _TRIM_PATCHED
    if _TRIM_PATCHED:
        return False

    import gc                                                         # noqa: PLC0415
    import os                                                         # noqa: PLC0415

    import numpy as np                                                # noqa: PLC0415

    # `openwakeword.data` imports `acoustics`, which needs the scipy alias. Called here rather
    # than assumed, so this works whatever order a caller applies the patches in.
    patch()

    import openwakeword.data as oww_data                              # noqa: PLC0415
    import openwakeword.utils as oww_utils                            # noqa: PLC0415
    from numpy.lib.format import open_memmap                          # noqa: PLC0415

    _TRIM_PATCHED = True
    pending: "list[str]" = []

    def _record_only(mmap_path):
        """Stands in for trim_mmap DURING feature computation. Trims nothing, notes the path."""
        pending.append(str(mmap_path))

    def _really_trim(mmap_path: str) -> None:
        source = np.load(mmap_path, mmap_mode="r")
        index = -1
        while np.all(source[index, :, :] == 0):
            index -= 1
        n_new = source.shape[0] + index + 1
        n_old = source.shape[0]

        if n_new >= n_old:
            # Nothing to trim. Skip the copy/remove/rename entirely — see the docstring.
            del source
            gc.collect()
            LOG.debug("no empty rows in %s; nothing to trim", os.path.basename(mmap_path))
            return

        target_path = mmap_path.removesuffix(".npy") + "2.npy"
        target = open_memmap(target_path, mode="w+", dtype=np.float32,
                             shape=(n_new, source.shape[1], source.shape[2]))
        for start in range(0, n_new, 1024):
            stop = min(start + 1024, n_new)
            target[start:stop] = source[start:stop]
        target.flush()

        del source
        del target
        gc.collect()

        os.remove(mmap_path)
        os.rename(target_path, mmap_path)
        LOG.info("trimmed %s: %d -> %d rows", os.path.basename(mmap_path), n_old, n_new)

    original = oww_utils.compute_features_from_generator

    def _compute(*args, **kwargs):
        pending.clear()
        result = original(*args, **kwargs)
        # `original` has returned, so its `fp` is unreachable. THIS is the whole fix.
        gc.collect()
        for path in pending:
            _really_trim(path)
        pending.clear()
        return result

    oww_data.trim_mmap = _record_only
    oww_utils.trim_mmap = _record_only          # in case a future version imports it at module level
    oww_utils.compute_features_from_generator = _compute

    LOG.info("deferred openWakeWord's feature trim until after the writer closes its memmap "
             "(WinError 32 on Windows)")
    return True


def patch_dataloader() -> bool:
    """Force `num_workers=0` on every DataLoader. Returns True if applied.

    ## Why training dies where augmentation did not

    `openwakeword/train.py:865` builds its loaders with `num_workers=n_cpus`. On Windows,
    multiprocessing uses **spawn**, not fork: each worker starts a fresh interpreter and
    re-imports the main module to rebuild the child's `__main__`. The main module here is
    `openwakeword.train`, which imports `openwakeword.data`, which imports `acoustics` — and
    that import is broken against scipy 1.17.

        multiprocessing/spawn.py -> runpy.run_module('openwakeword.train')
        -> openwakeword/data.py:36 import acoustics
        -> ImportError: cannot import name 'sph_harm' from 'scipy.special'

    `patch()` fixed that in THIS interpreter. The children are new interpreters and never ran
    it, so the shim is not there. Nothing applied in the parent can reach them.

    ## The two ways out, and why this one

    The alternative is to repair `acoustics` itself in site-packages, so every interpreter
    gets it. That keeps the loader workers, and it was refused: it is an untracked edit to a
    third-party package, invisible to git, unreproducible on another machine, and silently
    undone by the next `pip install --upgrade`. This repo's rule for the piper clone applies
    just as well here.

    Setting `num_workers=0` removes the child processes entirely, so there is nothing to
    re-import and nothing to patch.

    ## What it costs, measured against what it buys

    Loading here is memmap slices turned into tensors — memory-bandwidth work, not CPU work —
    against a small MLP training step. And on Windows, spawn'd loader workers each pay a full
    interpreter start plus a torch import, which for this shape of job frequently makes
    `num_workers=0` the FASTER choice rather than a sacrifice.

    It is still a real change to how upstream runs, so it is stated rather than hidden: if
    training turns out to be starved waiting on data, fixing `acoustics` for the whole
    environment is the other lever.
    """
    global _DL_PATCHED
    if _DL_PATCHED:
        return False

    import torch.utils.data as tud                                    # noqa: PLC0415

    _DL_PATCHED = True
    original = tud.DataLoader

    class _DataLoader(original):
        def __init__(self, *args, **kwargs):
            if kwargs.get("num_workers"):
                LOG.debug("DataLoader num_workers %s -> 0 (Windows spawn cannot re-import "
                          "openwakeword.train)", kwargs["num_workers"])
                kwargs["num_workers"] = 0
                # prefetch_factor is only meaningful with workers, and torch raises if it is
                # set while num_workers is 0.
                kwargs.pop("prefetch_factor", None)
            super().__init__(*args, **kwargs)

    tud.DataLoader = _DataLoader
    import torch                                                      # noqa: PLC0415
    torch.utils.data.DataLoader = _DataLoader
    LOG.info("DataLoader workers forced to 0 — Windows spawn re-imports a module that "
             "cannot be imported without this process's patches")
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
