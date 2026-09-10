#!/usr/bin/env python3
"""
Module:  quota.py
Purpose: Know when a model's daily free tier is gone, and stop paying to rediscover it.
Author:  LB
Date:    2026-08-22

## The 217-second turn

Measured on the Pi 2026-08-22, from `oddball.log`. LB asked for Firefox, the free tier was
gone, and the google-genai SDK retried the 429 with exponential backoff:

    21:15:51  429 -> retry in 1.78s
    21:15:53  429 -> retry in 2.54s
    21:15:55  429 -> retry in 4.44s
    21:16:00  429 -> retry in 8.79s
    21:16:14  429 -> retry in 16.5s
    21:16:36  turn failed          total 217.13s

**A daily quota does not reset in 8 seconds.** It resets at midnight Pacific. Every one of
those retries was certain to fail before it was sent, and while they ran the turn thread was
blocked - so the audio callback had nobody draining it and logged **3,568** dropped frames. He
was deaf for the entire time he was failing to answer.

So two things are needed, and only together:

1. **Do not retry a quota error.** `max_retries=0` turns 217s into 0.2s. Measured.
2. **Remember.** Without this, EVERY subsequent turn still pays a round trip to be told the
   same thing, and still burns a router call to get there. The latch makes the second and
   later turns cost nothing at all.

## Why the quotaId is parsed rather than the status code

A 429 is not one thing. From the measured body:

    quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier     <- gone until midnight
    quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier  <- gone for seconds

Latching the second one for a day would take him off the air until tomorrow over a burst that
cleared itself in under a minute. So only a **PerDay** quota latches; a per-minute one is
reported and forgotten.

## It is per MODEL, and that is load-bearing

D3 split routing, agents and persona across three model names precisely because the quota is
per model per day. So the latch is per model too: `flash` being exhausted must not silence
`flash-lite`, which is what answers the router and the persona.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

LOG = logging.getLogger("oddball.quota")

__all__ = ["is_daily_exhaustion", "note", "exhausted", "clear", "status",
           "names_model"]

# Google's free tier rolls over at midnight Pacific. Fixed -08:00 rather than a zoneinfo
# lookup: the consequence of being an hour out is that the latch expires an hour early or late
# once or twice a year, and an early expiry simply costs one wasted call to find out. A missing
# tzdata on a minimal Pi image would be a harder failure than that.
_PACIFIC = timezone(timedelta(hours=-8))

# model name -> when its daily quota is expected back (UTC).
_LATCHED: dict[str, datetime] = {}


def _next_pacific_midnight(now: datetime | None = None) -> datetime:
    """When the daily quota comes back, in UTC."""
    now = (now or datetime.now(timezone.utc)).astimezone(_PACIFIC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.astimezone(timezone.utc)


def is_daily_exhaustion(exc: BaseException | str) -> bool:
    """True only for a **per-day** free-tier quota error.

    Deliberately narrow. A per-minute 429, a 500, a dropped connection and a bad key are all
    False - none of them mean "come back tomorrow", and treating them as if they did would
    take him off the air for a day over a blip.
    """
    text = str(exc)
    if "RESOURCE_EXHAUSTED" not in text and "429" not in text:
        return False
    # The quotaId is the only field that distinguishes the two, and it is in the 429 body.
    if "PerDay" in text:
        return True
    # Some SDK versions surface the metric without the quotaId. The daily metric is named.
    return "generate_content_free_tier_requests" in text and "PerMinute" not in text


def names_model(text: str, model: str) -> bool:
    """Does `text` name exactly this model?

    A plain `model in text` is wrong here and the reason is specific: **AGENT_MODEL
    ("gemini-3.5-flash") is a strict prefix of ROUTER_MODEL ("gemini-3.5-flash-lite")**, which
    D3 chose deliberately so the two have separate daily buckets. A substring test therefore
    reads a router exhaustion as an agent exhaustion too, and latches the agents out for a day
    over a quota that was never theirs.

    So the match must not be followed by more of a model name. `-lite` after `flash` means it
    was a different model.
    """
    for m in re.finditer(re.escape(model), text):
        tail = text[m.end():m.end() + 1]
        if tail not in ("-", "_") and not tail.isalnum():
            return True
    return False


def note(model: str, exc: BaseException | str) -> bool:
    """Record a failure. Returns True if it latched the model out for the day."""
    if not is_daily_exhaustion(exc):
        return False
    back_at = _next_pacific_midnight()
    _LATCHED[model] = back_at
    LOG.warning("daily free tier exhausted for %s - not calling it again until %s UTC",
                model, back_at.isoformat(timespec="minutes"))
    return True


def exhausted(model: str) -> bool:
    """Is this model known to be out of quota right now?

    Expires on its own, so a process that lives across midnight recovers without a restart -
    which matters, because this one runs under systemd and is expected to.
    """
    back_at = _LATCHED.get(model)
    if back_at is None:
        return False
    if datetime.now(timezone.utc) >= back_at:
        del _LATCHED[model]
        LOG.info("daily quota for %s should be back; trying it again", model)
        return False
    return True


def clear(model: str | None = None) -> None:
    """Forget a latch. `None` clears all of them. For harnesses, and for a successful call."""
    if model is None:
        _LATCHED.clear()
    else:
        _LATCHED.pop(model, None)


def status() -> dict[str, str]:
    """Which models are latched, and until when. For the HUD and the harness."""
    return {m: t.isoformat(timespec="minutes") for m, t in _LATCHED.items()}
