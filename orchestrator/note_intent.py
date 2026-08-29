#!/usr/bin/env python3
"""
Module:  note_intent.py
Purpose: Recognise "take a note" — and "add to it", "read it back", "delete it" — with no model.
Author:  LB
Date:    2026-08-28

    python -m orchestrator.note_intent "take a note that the reg is an LM317"
    python -m orchestrator.note_intent            # the whole corpus, matched and refused

## Why this exists

Dictating a note cost **three Gemini calls**. Measured 2026-08-28 against the free tier: none
of the eight phrasings LB actually uses — "take a note", "write this down in my ECE350 folder",
"make a new folder called amp board and save this note there" — matched anything in
`orchestrator/instant.py` or `orchestrator/route_hint.py`. All eight fell through to
`router.py`, and from there the best case is GENERAL to `agents/persona_agent.py`, which does
have `save_to_vault` bound: one call to route, one to make the tool call, one to say what
happened. Against D3's measured 20 requests per model per day, that is **six notes a day**.

None of the three needs a model. "Take a note that X" has one right answer, the vault is a
folder of Markdown files (`tools/knowledge_vault.py`), and the question to ask back is "What
should I call it?" whatever model you have.

So this is a **pure function of a string**, injected into `orchestrator.instant.Router` as a
planner — the same seam `orchestrator/launch_intent.py` uses, and for the same reasons.
Nothing here imports `agents/`, `engine/`, `tools/` or any model.

## What is stored is a slice of the RAW text, and never a paraphrase

`tools/corrections.py` refused to let a model rewrite LB's standing rules, twice over: for
quota, and for authority. Both arguments apply here without a word changed. `normalise()`
strips `/` and `-`, which would turn "the reg is an LM317, not a 7805" into "the reg is an
lm317 not a 7805" and "/home/lb/kicad" into "homelbkicad". So **matching runs on the
normalised text and extraction runs on the original**, because those are two different jobs.

A note is evidence of what LB decided. A paraphrase of it is a model's opinion about what he
decided, and the day they differ is the day the part number is wrong.

## The two anchors, which are the whole safety argument

D38 has come back seven times in this repo on bare-keyword matching, and the sentence is always
the same: **the danger is never the rule that fails to match, it is the one that matches too
much.** A note matcher that fires on "how do I take notes in Python" eats a question and
answers it with "written down", which is both useless and smug.

    1. THE UTTERANCE MUST OPEN WITH THE VERB.

       A question does not begin with an order. "Take a note that the TL072 is FET input"
       opens with the verb; "how do I take notes in Python" opens with "how", and
       `_REQUEST_OPENERS` refuses it outright. Same rule as `corrections._directive_span`.

    2. ACTING ON AN EXISTING NOTE REQUIRES THE WORD "NOTE".

       This one is structural rather than a blocklist, and it is what keeps three working
       features from being stolen:

           "read my screen"          -> no "note". Stays SCREEN.
           "delete the temp files"   -> no "note". Stays OS.
           "add this to the BOM"     -> no "note". Stays HARDWARE.

       A blocklist of things-that-are-not-notes would need every noun LB owns. Requiring the
       noun he *is* naming needs one word, and it cannot be incomplete. `read`, `append`,
       `delete` and `list` all carry it; only `new` does not, because its openers ("write this
       down", "jot down") are unambiguous imperatives that name no object at all.

`python -m orchestrator.note_intent` prints the corpus with both anchors in force, and
`tools/verify_notes.py --probe` removes them and shows the negatives going red.

## Deliberately absent: "remember"

"Remember that the reg is an LM317" is a note and "remember that thing I told you about the
TL072?" is a question, and they open with the same two words. It is the one verb in the
vocabulary that means *recall* exactly as often as it means *record*, so a start anchor does
not separate them and nothing cheap does.

It is also the one that costs least to leave out: `agents/persona_agent.py` already has
`save_to_vault` bound and already handles "remember that I'm using the 2N3904" — that path
still works, it just still costs three calls. Adding the verb here would make the common case
free and the question case broken, which is the wrong side of the trade.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from orchestrator.instant import _is_bare, normalise

LOG = logging.getLogger("oddball.note")

__all__ = ["NoteRequest", "look_up", "is_cancel", "NEW", "APPEND", "READ", "LIST", "DELETE",
           "NOTE_WORDS", "OPENERS", "FILLER", "CANCELS"]

# The five operations. Plain strings rather than an Enum, matching `engine/response.CardKind`:
# they cross into `engine/core.py` as data and an Enum would only be unwrapped again.
NEW = "new"
APPEND = "append"
READ = "read"
LIST = "list"
DELETE = "delete"


@dataclass(frozen=True)
class NoteRequest:
    """A recognised request about the notebook. **Carries no authority to do any of it.**

    `engine/core.py` decides what happens, exactly as it does with `LaunchRequest` — and for
    `DELETE` it puts the resolved path on a card and asks first. This object is the result of
    reading a sentence, nothing more.

    Args:
        op:      one of NEW, APPEND, READ, LIST, DELETE.
        content: what to write, sliced verbatim out of the raw utterance. "" when LB gave the
                 command without the words, which is normal for voice — he is asked for them.
        target:  which existing note he means, for APPEND, READ and DELETE. Resolved against
                 the filesystem by `knowledge_vault.find_notes`, never here.
        folder:  the vault folder he named, for NEW and LIST. "" means he named none.
        name:    what to call a NEW note, when he volunteered it in the same breath ("call it
                 op amp pinouts"). "" means he is asked.
        verb:    which opener matched, for the log.
    """

    op: str
    content: str = ""
    target: str = ""
    folder: str = ""
    name: str = ""
    verb: str = ""


# The word that anchors the three operations acting on an existing note, plus `list`. See the
# module docstring — this is a structural requirement, not a heuristic.
NOTE_WORDS: tuple[str, ...] = ("note", "notes")

# What may be dropped from the front of an utterance before the opener is looked for. Kept
# short on purpose, the same discipline as `launch_intent.FILLER`: every word added here widens
# what counts as a command.
FILLER: frozenset[str] = frozenset({
    "please", "can", "could", "would", "you", "u", "hey", "ok", "okay", "so", "now",
    "just", "quickly", "quick", "for", "me", "lets", "let", "will", "and", "then",
})

# Indirect openings — the polite preamble LB actually uses in front of the imperative.
#
# **Found in `captures/`, not invented.** On 2026-08-28 at 08:49 and 08:50, ten minutes before
# asking for this feature, he said into the microphone:
#
#     "can you save a note for me in the vault"      matched, but stored "in the vault"
#     "i need to add a note to the vault"            missed entirely
#
# The second one opens with "i need to", which `_drop_filler` cannot reach because FILLER is
# word-by-word and "i", "need" and "to" are not in it — and must not be. **"I take notes in
# Python" is a statement**, and a bare "i" in FILLER would strip it down to "take notes in
# python", match the `_NEW` opener, and file a note saying "in python".
#
# So these are PHRASES, matched at the start and nowhere else, exactly like the openers below.
# A phrase cannot do what a bare word does: "i need to" appears at the front of a request and
# "i take" does not appear at the front of anything here.
_PREAMBLE: tuple[str, ...] = (
    "i need to", "i need you to", "i want to", "i want you to", "i would like to",
    "i would like you to", "id like to", "id like you to", "i have to", "i gotta",
    "im going to", "i am going to", "im gonna", "let me", "help me", "i should",
    "do me a favor and", "do me a favour and", "go ahead and", "if you could",
    # **"tell me" and nothing longer.** Measured 2026-08-29 07:24:45: "tell me what notes you
    # have saved for me" fell through every table here, routed to OS, and took 303 seconds to
    # answer a question the vault could have answered instantly.
    #
    # The phrase must stop at "me". Adding "tell me what" would strip the word the `_LIST`
    # opener "what notes" is anchored on, and the utterance that motivated the entry would go
    # back to missing — a longer preamble is sorted first and wins.
    #
    # "show me" is deliberately NOT here for that same reason: `_LIST` already carries "show me
    # my notes" and "show me the notes" in full, and a preamble would eat their openers.
    "tell me",
)

# "…that you have for me", "…you have saved for me". LB describing the LISTENER'S possession
# of the notes, which is conversational scaffolding and never a note's name.
#
# **Measured, not imagined.** `oddball.log` 2026-08-29 07:20:27, "read me back the notes that
# you have for me": the `_READ` opener matched, `_clean_target` chewed the remainder down to
# the target "you have for", `knowledge_vault.find_notes` returned [] for it, and he answered
# "I don't have a note called you have for". He was asking for the LIST.
#
# **Anchored to the end, and the anchor is the whole safety argument.** A note really can be
# called "things you have to buy" — and it survives, because after "you have" this pattern
# demands nothing but courtesy words and then `$`. "to buy note" is not courtesy, so the match
# fails and the name is left exactly as LB said it. Applied ONLY to target and folder
# extraction, never to a NEW or APPEND body: the verbatim guarantee is not negotiable, and the
# cheapest way to keep it is for this regex to never see the content.
_POSSESSION_TAIL = re.compile(
    r"\s*\b(?:that\s+|which\s+)?(?:youve|you|u)\s+"
    r"(?:have|has|ve|got|saved|stored|kept|wrote|written|made|took|taken)"
    r"(?:\s+(?:saved|stored|kept|written|down|for|of))*"
    r"(?:\s+(?:me|us|mine|ours))?\s*$", re.IGNORECASE)


def _drop_possession(flat: str) -> str:
    """Remove a trailing "that you have for me" clause. Returns the text unchanged if none."""
    return " ".join(_POSSESSION_TAIL.sub("", flat).split())


# Which prepositions can introduce a FOLDER after a list opener — "what notes do I have IN amp
# board". See `_folder_after_preposition`.
_FOLDER_PREPOSITIONS: frozenset[str] = frozenset({
    "in", "inside", "under", "from", "within", "into", "on",
})

_FOLDER_PREPOSITION = re.compile(
    rf"^(?:{'|'.join(sorted(_FOLDER_PREPOSITIONS))})\s+(?P<f>.+)$", re.IGNORECASE)


def _folder_after_preposition(flat_rest: str, opener: str) -> str:
    """The folder a LIST names, or "" — and "" is the common answer.

    The old rule was `_clean_target(rest)`, which treats *whatever is left over* as a folder
    name. That is fine for "what notes do I have in amp board" and wrong for every sentence
    that trails off politely: "tell me what notes you have saved for me" asked for a listing of
    a folder called "you have saved for me", found nothing, and said the vault was empty.

    A folder is named by a PREPOSITION or it is not named at all. The opener may carry the
    preposition itself — `_LIST` contains "what notes are in" — so it is checked too, or that
    phrasing would lose the folder it just introduced.
    """
    stripped = _drop_possession(flat_rest)
    if not stripped:
        return ""
    tail = opener.split()[-1] if opener else ""
    if tail in _FOLDER_PREPOSITIONS:
        return _clean_target(stripped)
    hit = _FOLDER_PREPOSITION.match(stripped)
    return _clean_target(hit.group("f")) if hit else ""


# "…in the vault", "…to my vault". A DESTINATION that names the vault itself rather than a
# folder in it — so it is removed from the content and leaves the folder alone. Without this,
# "can you save a note for me in the vault" stores a note whose entire body is "in the vault".
#
# Deliberately not a `_FOLDER_PATTERNS` entry: those capture a folder NAME, and matching this
# there would file the note into `vault/vault/`.
_VAULT_TAIL = re.compile(r"\b(?:in|into|to|inside|under)\s+(?:the|my)\s+vault\b", re.IGNORECASE)

# Openers that make the utterance a QUESTION or a request for information, whatever follows.
# Checked before every table below and refused outright — "what notes do I have" is the one
# exception and is listed as a LIST opener in full, so it matches as a phrase rather than
# escaping this rule as a bare "what".
_REQUEST_OPENERS: frozenset[str] = frozenset({
    "how", "why", "when", "who", "which", "where", "is", "are", "was", "were", "do",
    "does", "did", "should", "shall", "may", "might", "must",
})

# --- the openers, one table per operation ---------------------------------------------------
#
# Every entry is a PHRASE, never a bare keyword, and every one is an imperative. Ordered by
# nothing — `_opening` sorts them longest-first at match time, so "take a new note" wins over
# "take a note" and the remainder is not left holding the word "new".

_NEW: tuple[str, ...] = (
    "take a note", "take a new note", "take note", "take notes", "make a note",
    "make a new note", "start a new note", "start a note", "new note", "note that",
    "note this", "note down", "write this down", "write that down", "write it down",
    "write down", "jot this down", "jot that down", "jot down", "jot this", "save a note",
    "save this note", "save a new note", "add a note", "add a new note", "log this",
    "log that", "put this in my notes", "put that in my notes", "keep a note",
)

# Starting from the FOLDER rather than from the note — "make a new folder called amp board and
# save this note there". LB asked for exactly this phrasing and it is the one shape where the
# folder is the subject of the sentence and the note has not been dictated yet.
#
# **These require the note word and the plain `_NEW` openers do not**, which is the whole
# reason they are a separate table. "Make a new folder called builds" with no note in it is a
# request to the machine — `mkdir` — and belongs to OS, where it is gated. The word "note" is
# what separates a folder in his notebook from a folder on his disk.
_NEW_FOLDER: tuple[str, ...] = (
    "make a new folder", "make a folder", "create a new folder", "create a folder",
    "start a new folder", "new folder", "add a new folder", "add a folder",
)

_APPEND: tuple[str, ...] = (
    "add to", "add this to", "add that to", "add more to", "append to", "append this to",
    "add on to", "add onto", "tack this onto", "put this in", "put that in",
)

_READ: tuple[str, ...] = (
    "read me", "read back", "read out", "read my", "read the", "read", "say back",
    "what does my", "what does the", "whats in my", "whats in the", "whats on my",
    "tell me whats in", "play back", "recite",
)

_LIST: tuple[str, ...] = (
    "what notes do i have", "what notes have i got", "what notes are there",
    "what notes are in", "which notes do i have", "list my notes", "list the notes",
    "list my", "list all my notes", "show me my notes", "show my notes", "show me the notes",
    "how many notes", "whats in my vault", "whats in the vault", "what notes",
)

_DELETE: tuple[str, ...] = (
    "delete my", "delete the", "delete", "remove my", "remove the", "throw away my",
    "throw away the", "throw out my", "get rid of my", "get rid of the", "bin my",
    "trash my", "scrap my", "forget my", "erase my", "erase the",
)

# `(operation, openers, needs_the_note_word)`.
#
# Ordered, and the order is load-bearing in exactly one place: LIST is checked before READ,
# because "read me my notes" with no target is a request for the list and "read me" is a READ
# opener that would swallow it. Everywhere else the tables are disjoint.
#
# The third field is anchor 2, applied per table rather than per operation — `_NEW` is exempt
# because its openers name no object, and `_NEW_FOLDER` is not, because "make a new folder"
# without a note in it is a request to the filesystem.
OPENERS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    (NEW, _NEW, False),
    (NEW, _NEW_FOLDER, True),
    (APPEND, _APPEND, True),
    (LIST, _LIST, True),
    (READ, _READ, True),
    (DELETE, _DELETE, True),
)

# Words with no identity of their own when LB names a note or a folder out loud.
_TARGET_FILLER: frozenset[str] = frozenset({
    "my", "the", "a", "an", "that", "this", "it", "one", "of", "from", "in", "to",
    "note", "notes", "file", "called", "named", "titled", "about", "on", "back", "out",
    "me", "say", "said", "says", "please",
})

# What separates the note's NAME from its CONTENT in an append. "add to my regulator note that
# it runs hot" — everything before is which note, everything after is what to add.
_CONTENT_MARKERS: tuple[str, ...] = ("that", "saying", "with")

# --- folder and name phrases, matched against the RAW text -----------------------------------
#
# Against the raw rather than the normalised text, because these are cut OUT of the content and
# what is left has to still be exactly what LB said. Ordered: the "new folder called X" shape
# must win over the plain "in the X folder" shape, or the word "called" is left in the folder
# name.

_FOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "in a new folder called amp board", "into a folder named ECE350"
    re.compile(r"\b(?:in|into|inside|under|to)\s+(?:a\s+new\s+|a\s+|my\s+|the\s+)?"
               r"folder\s+(?:called|named|titled)\s+(?P<f>[A-Za-z0-9 _-]+?)"
               r"(?=\s+(?:that|saying|and|which)\b|[.,;:]|$)", re.IGNORECASE),
    # "in my ECE350 folder", "under the amp board folder"
    re.compile(r"\b(?:in|into|inside|under|to)\s+(?:my|the)\s+"
               r"(?P<f>[A-Za-z0-9 _-]+?)\s+folder\b", re.IGNORECASE),
    # "in my ECE350 notes" — the folder said as a plural rather than as a folder
    re.compile(r"\b(?:in|into|inside|under|to)\s+(?:my|the)\s+"
               r"(?P<f>[A-Za-z0-9 _-]+?)\s+notes\b", re.IGNORECASE),
)

# "call it op amp pinouts", "and call it the regulator choice". Anchored to the END, because a
# name LB volunteers comes last — and because anchoring it anywhere would eat the word "called"
# out of the middle of a note's own content.
_NAME_PATTERN = re.compile(
    r"[,;]?\s*\b(?:and\s+)?(?:call|name|title)\s+it\s+(?P<n>[A-Za-z0-9 _-]+?)\s*[.!]?$",
    re.IGNORECASE)


# --- backing out of a note in progress -------------------------------------------------------
#
# `instant.is_sleep` is the DISMISSAL matcher — "goodnight", "that's all", "leave me alone" —
# and it ends the conversation. It was tried here first and it is the wrong list: "never mind"
# and "forget it" are not in it, and they should not be, because neither of them means LB wants
# Mr Odd Ball to stop listening. They mean he wants *this* to stop.
#
# The harness caught the difference immediately, and it caught it as a cascade rather than as
# one red line: "never mind" became the note's contents, the NEXT "take a note" became its
# name, and two checks after that passed while testing nothing. A cancel that does not cancel
# does not fail where it happens.

CANCELS: tuple[str, ...] = (
    "never mind", "nevermind", "forget it", "forget about it", "forget that",
    "cancel", "cancel that", "cancel it", "dont bother", "do not bother", "dont worry about it",
    "leave it", "skip it", "scratch that", "stop", "stop it", "no", "nope", "no thanks",
    "not now", "actually no", "changed my mind", "i changed my mind",
)

# Small, like every filler set in this repo. A cancel is scoped to a turn where the only thing
# in progress IS the note, so it can afford to be a little broader than a dismissal — but the
# cost of a wrong match is still LB's note thrown away, so it is not broad.
_CANCEL_FILLER: frozenset[str] = frozenset({
    "ok", "okay", "alright", "well", "so", "now", "then", "please", "just", "um", "uh",
    "actually", "sorry", "hey", "mr", "odd", "ball", "oddball", "thanks", "thank", "you",
})


def is_cancel(text: str) -> bool:
    """Is LB backing out of the note he is part-way through dictating?

    **End-anchored**, the same `instant._is_bare` rule as wake and dismissal: the cancel has to
    BE the utterance. "Forget it" cancels; "take a note that I should forget it about the old
    layout" does not, because that is a note with the word in it.
    """
    return _is_bare(normalise(text), CANCELS, _CANCEL_FILLER)


def _drop_filler(words: list[str]) -> list[str]:
    """Remove leading filler. "hey can you take a note" -> "take a note"."""
    while words and words[0] in FILLER:
        words.pop(0)
    return words


def _opening(flat: str, phrases: tuple[str, ...]) -> "tuple[str, str] | None":
    """The phrase this utterance OPENS with, and what followed it.

    Args:
        flat:    normalised text, already stripped of leading filler.
        phrases: the openers to try.

    Returns:
        `(opener, remainder)`, or None. Longest first, so "take a new note" beats "take a
        note" and "add more to" beats "add to".
    """
    for phrase in sorted(phrases, key=len, reverse=True):
        if flat == phrase:
            return phrase, ""
        if flat.startswith(phrase + " "):
            return phrase, flat[len(phrase) + 1:].strip()
    return None


def _slice_after(raw: str, marker: str) -> str:
    """Everything in the RAW text after `marker`, which is given normalised.

    The mirror of `corrections._slice_raw`, which keeps everything *from* its marker onward —
    same problem, opposite half. The marker's words are matched across whatever punctuation and
    capitals the original used, so the normalised "write this down" finds "Write this down,".

    Falls back to the raw text with nothing removed when the marker cannot be located, which
    happens when normalisation dropped the punctuation that was inside it.
    """
    words = marker.split()
    pattern = r"[^A-Za-z0-9]*".join(
        r"".join(rf"{re.escape(ch)}['’]?" for ch in word) for word in words)
    hit = re.search(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", raw, re.IGNORECASE)
    if hit is None:
        return raw.strip()
    return raw[hit.end():].strip()


# Filler that TRAILS the verb rather than preceding it — "jot this down FOR ME: the tab is
# live". `_drop_filler` cannot reach these, because it runs before the opener is found and
# these words come after it.
#
# The same shape as `launch_intent.DESCRIPTOR`, and it takes the same care: every entry is a
# fixed courtesy phrase that cannot begin a sentence LB would want stored. "for me" is listed
# rather than "for" and "me" separately, because "me and LB agreed on the TL072" is a perfectly
# good note and a bare "me" would eat its first word.
_POST_VERB = re.compile(
    r"^\s*(?:for\s+(?:me|us)|please|real\s+quick|quickly|quick|if\s+you\s+(?:can|would|could))"
    r"\b[\s,:;.-]*", re.IGNORECASE)


def _strip_lead(raw: str) -> str:
    """Remove a leading connective, ":" or "," from a content slice.

    "note that the reg is an LM317" leaves "that the reg is an LM317", and the note should not
    open with the word "that". Only the connective is removed and only from the front.

    **The list is short deliberately, and it cost a bug to learn that.** It began with "it" and
    "is" in it, which turned "add to my regulator note that IT needs a heatsink" into the note
    "needs a heatsink" — a sentence with its subject removed, stored as LB's own words. Every
    word here is one that can only ever be a joint between the command and the note; anything
    that could be the note's first word does not belong.
    """
    trimmed = raw
    for _ in range(3):                       # "for me, please: ..." — bounded, never a loop
        after = _POST_VERB.sub("", trimmed, count=1)
        if after == trimmed:
            break
        trimmed = after
    return re.sub(r"^\s*(?:that|this|about|saying|which)\b[\s,:;-]*|^[\s,:;-]+",
                  "", trimmed, count=1, flags=re.IGNORECASE).strip(" ,:;-\t\n")


def _clean_target(flat: str) -> str:
    """A note or folder name with the scaffolding trimmed off. "my regulator note" -> "regulator".

    **Trimmed from the ENDS only, never from the middle**, and that distinction is the whole
    function. Dropping filler wherever it appears turned "read back my parts to order note"
    into the name "parts order", which matches no file — LB's note is called "parts to order",
    and "to" is a word inside its name rather than grammar around it. The scaffolding is always
    at the edges ("my ... note"); the name is whatever is left between them.
    """
    words = flat.split()
    while words and words[0] in _TARGET_FILLER:
        words.pop(0)
    while words and words[-1] in _TARGET_FILLER:
        words.pop()
    return " ".join(words)


# A trailing phrase that says what the folder is FOR rather than what it is called — "create a
# folder called ECE350 for my notes". Trimmed off the folder name.
#
# Deliberately requires the preposition. A bare trailing "notes" is NOT trimmed, because "new
# folder called scratch notes" names a folder called "scratch notes" and stripping it to
# "scratch" would file LB's notes somewhere he did not ask for. The preposition is what makes
# the difference between a purpose and a name, and it is the only thing that does.
_PURPOSE_TAIL = re.compile(
    r"\s+(?:for|in|to|inside|under)\s+(?:my\s+|the\s+)?(?:notes?|vault|notebook)\s*$",
    re.IGNORECASE)


def _trim_folder(folder: str) -> str:
    """A folder name with any "...for my notes" purpose phrase taken off the end."""
    return " ".join(_PURPOSE_TAIL.sub("", folder).split())


def _take_folder(raw: str) -> tuple[str, str]:
    """Pull a folder phrase out of the raw text.

    Returns:
        `(folder, raw_without_it)`. `folder` is "" when none was named, and the raw text comes
        back unchanged — the folder phrase is REMOVED rather than merely read, because "take a
        note in my ECE350 folder that the midterm is week 9" must not store the words "in my
        ECE350 folder" as part of the note.
    """
    for pattern in _FOLDER_PATTERNS:
        hit = pattern.search(raw)
        if hit is None:
            continue
        folder = _trim_folder(" ".join(hit.group("f").split()))
        rest = (raw[:hit.start()] + " " + raw[hit.end():]).strip()
        return folder, " ".join(rest.split())
    return "", raw


def _take_name(raw: str) -> tuple[str, str]:
    """Pull a volunteered name out of the raw text. Same contract as `_take_folder`."""
    hit = _NAME_PATTERN.search(raw)
    if hit is None:
        return "", raw
    name = " ".join(hit.group("n").split())
    return name, (raw[:hit.start()] + " " + raw[hit.end():]).strip(" ,.;:\t\n")


def _split_target(flat_rest: str, raw_rest: str) -> tuple[str, str]:
    """For an append: which note, and what to add to it.

    Args:
        flat_rest: the normalised remainder after the opener.
        raw_rest:  the same remainder, raw.

    Returns:
        `(target, content)`. The word "note" terminates the target, and one of
        `_CONTENT_MARKERS` starts the content — "add to my regulator note that it runs hot"
        splits at both. With no marker at all the whole remainder is the target and the content
        is "", which is not a failure: he asks what to add.
    """
    words = flat_rest.split()

    # Prefer the marker that comes AFTER the note word, so "add to my heatsink note that the
    # tab is live" does not split on a "that" belonging to the note's own name.
    note_at = next((i for i, w in enumerate(words) if w in NOTE_WORDS), -1)
    start = note_at + 1 if note_at >= 0 else 0
    marker_at = next((i for i in range(start, len(words)) if words[i] in _CONTENT_MARKERS), -1)

    if marker_at >= 0:
        target = " ".join(words[:marker_at])
        content = _strip_lead(_slice_after(raw_rest, words[marker_at]))
        return _clean_target(target), content

    if note_at >= 0 and note_at + 1 < len(words):
        before = _clean_target(" ".join(words[:note_at]))

        # **Which side of the noun the name is on is not fixed, and assuming it was cost LB a
        # note.** Measured 2026-08-29 07:18:02: "add to my note about the topic for my English
        # research paper" put everything identifying the note AFTER the word "note", so
        # `words[:note_at]` was ["my"], `_clean_target` reduced it to "", `_build` returned
        # None on the empty target, and the turn fell through to the router — 129 seconds and
        # two paid calls to not append one line to a file.
        #
        # "add to my regulator note it needs a heatsink" still splits at the noun, because
        # there the words before it are a real name. The test is whether anything SURVIVES
        # `_clean_target` on the left: a name survives, pure scaffolding does not.
        if before:
            content = _strip_lead(_slice_after(raw_rest, words[note_at]))
            return before, content

        # Nothing but filler in front of the noun, so the name is what follows it — and there
        # is no content, because a sentence shaped "add to my note about X" has named the note
        # and not the addition. He is asked what to add, which is the same thing "add more to
        # my amp board note" already does.
        return _clean_target(flat_rest), ""

    return _clean_target(flat_rest), ""


def look_up(q) -> "NoteRequest | None":
    """Is this a request about the notebook? Returns it, or None. Never raises.

    Args:
        q: an `instant.Query` — `q.raw` is what was said, `q.text` is `normalise(q.raw)`. The
           planner signature `instant.Router` calls, identical to `launch_intent.look_up`.

    Returns:
        A `NoteRequest`, or None. **None is the overwhelmingly common answer**, and is correct
        for everything that needs judgement — see the module docstring for what is deliberately
        refused.
    """
    flat = getattr(q, "text", "") or normalise(getattr(q, "raw", "") or "")
    raw = getattr(q, "raw", "") or flat
    if not flat:
        return None

    words = _drop_filler(flat.split())
    if not words:
        return None

    # Filler, then preamble, then filler again — "can you help me take a note" needs all three
    # passes ("can you" is filler, "help me" is a preamble, and nothing is left over). Bounded,
    # so a pathological utterance cannot spin here.
    for _ in range(3):
        lead = " ".join(words)
        stripped = next((lead[len(p) + 1:] for p in sorted(_PREAMBLE, key=len, reverse=True)
                         if lead.startswith(p + " ")), None)
        if stripped is None:
            break
        words = _drop_filler(stripped.split())
    if not words:
        return None

    # Anchor 1: a question is never a command, whatever it goes on to say.
    if words[0] in _REQUEST_OPENERS:
        return None

    lead = " ".join(words)
    for op, phrases, needs_note_word in OPENERS:
        found = _opening(lead, phrases)
        if found is None:
            continue
        opener, rest_flat = found

        # Anchor 2: naming an existing note — or a folder in the notebook — requires LB to have
        # said "note". The plain NEW openers are exempt: they are imperatives that name no
        # object at all, so there is nothing for them to be confused with.
        if needs_note_word and not any(w in NOTE_WORDS for w in lead.split()):
            return None

        request = _build(op, opener, rest_flat, raw, from_folder=phrases is _NEW_FOLDER)
        if request is not None:
            LOG.info("note intent %s (%r) -> %r", op, opener, request)
        return request

    return None


# The folder name when the sentence STARTS with the folder — "make a new folder called amp
# board and ...". Anchored to the front of what follows the opener, and stopping at the
# conjunction, so "and save this note there" is not swallowed into the folder's name.
_LEADING_FOLDER = re.compile(
    r"^\s*(?:called|named|titled|for)?\s*(?P<f>[A-Za-z0-9 _-]+?)"
    r"(?=\s+(?:and|that|then|so|saying|which)\b|[.,;:]|$)", re.IGNORECASE)


def _build(op: str, opener: str, rest_flat: str, raw: str,
           from_folder: bool = False) -> "NoteRequest | None":
    """Turn a matched opener and its remainder into a request. None to refuse after all."""
    # Applied before anything else reads the remainder. "…in the vault" says WHERE, and every
    # branch below would otherwise treat it as what it is looking for — content, a folder name,
    # or a note's title.
    rest_raw = " ".join(_VAULT_TAIL.sub(" ", _slice_after(raw, opener)).split())
    rest_flat = " ".join(_VAULT_TAIL.sub(" ", rest_flat).split())

    if from_folder:
        hit = _LEADING_FOLDER.match(rest_raw)
        if hit is None:
            return None                    # "make a new folder" naming none — let it route
        folder = _trim_folder(" ".join(hit.group("f").split()))
        tail = rest_raw[hit.end():]

        # **The tail is command residue, not the note.** "and save this note there" is LB
        # saying where the note goes, not what it says — storing it would file his own
        # instruction as the thing he wanted remembered. Only an explicit content marker
        # promotes any of it to content; otherwise he is asked, which is the honest answer to
        # a sentence that named a folder and no note.
        content = ""
        for marker in _CONTENT_MARKERS + (":",):
            spot = re.search(rf"(?<![A-Za-z0-9]){re.escape(marker)}(?![A-Za-z0-9])"
                             if marker.isalpha() else re.escape(marker), tail)
            if spot:
                content = _strip_lead(tail[spot.end():])
                break

        name = ""
        if content:
            name, content = _take_name(content)
        return NoteRequest(op=NEW, content=content, folder=folder, name=name, verb=opener)

    if op == NEW:
        folder, rest_raw = _take_folder(rest_raw)
        name, rest_raw = _take_name(rest_raw)
        return NoteRequest(op=NEW, content=_strip_lead(rest_raw), folder=folder,
                           name=name, verb=opener)

    if op == APPEND:
        folder, rest_raw = _take_folder(rest_raw)
        target, content = _split_target(rest_flat, rest_raw)
        if not target:
            return None            # "add to my notes" names nothing to add to; let it route
        return NoteRequest(op=APPEND, target=target, content=content, folder=folder,
                           verb=opener)

    if op == LIST:
        folder, _ = _take_folder(rest_raw)
        if not folder:
            folder = _folder_after_preposition(rest_flat, opener)
        return NoteRequest(op=LIST, folder=folder, verb=opener)

    target = _clean_target(_drop_possession(rest_flat))
    if op == READ and not target:
        # "read me my notes" with nothing named is a request for the list, not for a note.
        return NoteRequest(op=LIST, verb=opener)
    if not target:
        return None                # a bare "delete my note" names nothing. Never guess.
    return NoteRequest(op=op, target=target, verb=opener)


# The corpus the CLI prints. Positives first, then the refusals that matter — every one of
# these is a working feature that a greedier matcher would steal.
_CORPUS: tuple[str, ...] = (
    "take a note that the reg is an LM317 not a 7805",
    "take a note",
    "write this down in my ECE350 folder: the midterm is week 9",
    "make a note in a new folder called amp board that the tab on the regulator is live",
    "jot down that I need two more 10k trimmers, and call it parts to order",
    "add to my regulator note that it needs a heatsink",
    "read me my regulator note",
    "whats in my ECE350 notes",
    "what notes do I have in amp board",
    "delete my scratch note",
    "read me my notes",
    # --- must NOT match -----------------------------------------------------------------
    "how do I take notes in Python",
    "what did I note about the TL072",
    "read my screen",
    "read that dialog to me",
    "delete the temp files",
    "add this to the bill of materials",
    "open notepad",
    "whats the trace width for 5 amps",
    "remember that I'm using the 2N3904",
    "always use absolute paths instead",
)


def main(argv: "list[str] | None" = None) -> int:
    import sys

    from orchestrator.instant import Query

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    args = argv if argv is not None else sys.argv[1:]
    for utterance in (args or _CORPUS):
        found = look_up(Query(raw=utterance, text=normalise(utterance)))
        if found is None:
            print(f"  {utterance!r:70} -> (router decides)")
            continue
        bits = [f"op={found.op}"]
        for field_name in ("target", "folder", "name", "content"):
            value = getattr(found, field_name)
            if value:
                bits.append(f"{field_name}={value!r}")
        print(f"  {utterance!r:70} -> {'  '.join(bits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
