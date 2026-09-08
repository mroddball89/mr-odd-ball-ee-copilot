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

__all__ = ["patch", "patch_torch_load"]

_PATCHED = False
_LOAD_PATCHED = False


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
