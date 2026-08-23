#!/usr/bin/env python3
"""
Module:  verify_upload.py
Purpose: Prove the paperclip works end to end — parser, guards, live HTTP, and the filing.
Author:  LB
Date:    2026-08-23

    python tools/verify_upload.py

No key, no model, no network beyond loopback, and nothing in the real `data/` is touched: every
filing test runs against a temporary tree. That is deliberate — this has to be runnable on
Windows where there is no `.env` at all (`docs/DEPLOY.md`: the Pi is the deployment target).

## What each section is actually defending against

**parser** — the multipart body is split on `CRLF + "--" + boundary`, never on the boundary
alone. Section 1 uploads a payload whose CONTENT contains the boundary string; a `split()`-based
parser truncates that file and reports success, which is a silently corrupted datasheet.

**names** — `filename` comes off the network. Section 2 is the traversal table.

**http** — the round trip, including all four refusals. A rejection that comes back as an
unhandled exception instead of a status code is a 500 the panel renders as "Upload failed" with
no reason in it.

**origin** — a `multipart/form-data` POST is a CORS simple request, so any page in any browser
on this machine can send one to a loopback port with no preflight. Section 4 is the table that
says which ones are refused.

**accept** — the picker's `accept` list and the server's allow-list are written in two files and
must agree, or the OS dialog offers a `.docx` the server then refuses. There is no way to derive
one from the other at runtime (one is HTML), so this compares them as text.

**filing** — the three categories land in the three directories, the indexer is asked for the
right jobs, and a zip cannot write outside the project folder it was unpacked into.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import socket
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# Keyless by construction — D7: the box this is authored on has no key, and section 7 imports
# the agent modules to read their tool tables. `engine/models.py` validates the key at IMPORT
# time, so the dummy is what lets those imports happen; nothing here ever invokes a model, and
# `router.py`'s chain is built without a request being sent. Same preamble, verbatim, as
# tools/verify_academic.py and tools/verify_agents.py.
import os                                                            # noqa: E402

from dotenv import load_dotenv                                       # noqa: E402

load_dotenv(REPO_ROOT / ".env")
_k = os.environ.get("GOOGLE_API_KEY", "").strip()
if len(_k) < 20 or any(p in _k.lower() for p in ("paste", "here", "your-key", "xxx")):
    os.environ["GOOGLE_API_KEY"] = "harness-not-a-real-key-but-long-enough-to-pass"

import engine.server as S                                            # noqa: E402
import tools.file_manager as F                                       # noqa: E402

PASSED = 0
FAILED = 0


def check(ok: bool, what: str, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"   PASS  {what}")
    else:
        FAILED += 1
        print(f"   FAIL  {what}")
    if detail:
        print(f"           {detail}")


def section(name: str) -> None:
    print(f"\n  {name}")


BOUNDARY = b"----OddBallBoundary7MA4YWxkTrZu0gW"

# HTTP's line terminator, named because the oversize check below hand-writes a request and a
# bare newline there is not a valid header separator — the server would sit waiting for the rest
# of the headers and the check would time out rather than fail.
CRLF = "\r\n"


def build_body(filename: str, data: bytes, field: str = "file",
               extra: list[tuple[str, str]] | None = None) -> bytes:
    """A multipart body shaped exactly like a browser's, for the parser and the round trip."""
    out = io.BytesIO()
    for name, value in (extra or []):
        out.write(b"--" + BOUNDARY + b"\r\n")
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(value.encode() + b"\r\n")
    out.write(b"--" + BOUNDARY + b"\r\n")
    out.write(f'Content-Disposition: form-data; name="{field}"; '
              f'filename="{filename}"\r\n'.encode())
    out.write(b"Content-Type: application/octet-stream\r\n\r\n")
    out.write(data + b"\r\n")
    out.write(b"--" + BOUNDARY + b"--\r\n")
    return out.getvalue()


# ============================================================ 1. the parser

section("parser")

body = build_body("note.txt", b"hello")
parts = S.parse_multipart(body, BOUNDARY)
got = S.pick_file_part(parts)
check(got == ("note.txt", b"hello"), "one part round-trips", f"{got!r}")

body = build_body("note.txt", b"payload", extra=[("category", "datasheet")])
parts = S.parse_multipart(body, BOUNDARY)
check(len(parts) == 2, "a form field beside the file is parsed too", f"{len(parts)} parts")
check(S.pick_file_part(parts) == ("note.txt", b"payload"),
      "the FILE part is the one picked, not the text field")

# THE case. A PDF's bytes can contain anything, including this boundary — a parser that
# splits on the bare token cuts the file in half here and reports a successful upload.
hostile = b"%PDF-1.7 ... --" + BOUNDARY + b" ... trailer"
parts = S.parse_multipart(build_body("evil.pdf", hostile), BOUNDARY)
got = S.pick_file_part(parts)
check(got is not None and got[1] == hostile,
      "content containing the boundary survives intact",
      f"{len(hostile)} bytes in, {len(got[1]) if got else 0} out")

# Binary with CRLFs and NULs in it, which is every real PDF.
blob = bytes(range(256)) * 40
parts = S.parse_multipart(build_body("bin.pdf", blob), BOUNDARY)
got = S.pick_file_part(parts)
check(got is not None and got[1] == blob, "binary content is byte-exact",
      f"{len(blob)} bytes")

truncated = build_body("cut.pdf", b"x" * 500)[:120]
check(S.pick_file_part(S.parse_multipart(truncated, BOUNDARY)) is None,
      "a truncated body yields no file rather than a partial one")
check(S.parse_multipart(build_body("a.txt", b"x"), b"") == [],
      "a missing boundary yields nothing rather than raising")
check(S.parse_multipart(b"not multipart at all", BOUNDARY) == [],
      "a body that is not multipart yields nothing")

ct = 'multipart/form-data; boundary=----WebKitFormBoundaryABC123'
check(S.boundary_from(ct) == b"----WebKitFormBoundaryABC123",
      "the boundary is read out of a real Content-Type", S.boundary_from(ct).decode())
check(S.boundary_from('multipart/form-data; boundary="quoted-one"') == b"quoted-one",
      "a quoted boundary is unquoted")
check(S.boundary_from("text/plain") == b"", "no boundary gives an empty token")

# ============================================================ 2. names

section("names")

NAME_CASES = [
    ("datasheet.pdf", "datasheet.pdf", "an ordinary name is untouched"),
    ("../../.ssh/authorized_keys", "authorized_keys", "a traversal becomes a filename"),
    ("..\\..\\windows\\system32\\x.txt", "x.txt", "a backslash traversal too"),
    ("/etc/passwd", "passwd", "an absolute path loses its directories"),
    ("...", "upload", "a name that sanitises to nothing gets a fallback"),
    ("", "upload", "an empty name gets a fallback"),
    ("ECE 350 syllabus.pdf", "ECE 350 syllabus.pdf", "spaces are kept — they are legal"),
    ("amp;board|v2.kicad_sch", "amp_board_v2.kicad_sch", "shell metacharacters are flattened"),
]

for raw, want, why in NAME_CASES:
    got = S.safe_name(raw)
    check(got == want, why, f"{raw!r} -> {got!r}")

long_name = "a" * 400 + ".kicad_sch"
got = S.safe_name(long_name)
check(len(got) <= 120 and got.endswith(".kicad_sch"),
      "a very long name is truncated but KEEPS its extension", f"{len(got)} chars: ...{got[-14:]}")

with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    (d / "a.pdf").write_bytes(b"first")
    second = S.unique_path(d, "a.pdf")
    check(second.name == "a-2.pdf", "a collision is suffixed, never overwritten", second.name)
    second.write_bytes(b"second")
    check(S.unique_path(d, "a.pdf").name == "a-3.pdf", "and again for the third")
    check((d / "a.pdf").read_bytes() == b"first", "the original is still the original")

with tempfile.TemporaryDirectory() as tmp:
    inbox = Path(tmp) / "inbox"
    saved = S.save_upload("../../escape.pdf", b"x", inbox)
    check(saved.parent == inbox.resolve(), "a saved file lands inside the inbox", str(saved))
    try:
        S.save_upload("script.sh", b"#!/bin/sh", inbox)
        check(False, "a disallowed extension is refused")
    except ValueError as exc:
        check("only take" in str(exc), "a disallowed extension is refused", str(exc)[:60])

# ============================================================ 3. origin

section("origin")

ORIGINS = [
    ("http://127.0.0.1:8765", True, "the rig's own origin"),
    ("http://localhost:8765", True, "localhost by name"),
    ("http://127.0.0.1:8766", True, "a stage on another loopback port"),
    ("https://127.0.0.1:9000", True, "https on loopback"),
    ("null", False, "a file:// page — the rig is served over http when he is running"),
    ("", False, "an empty origin string"),
    ("http://evil.example.com", False, "a page on the internet"),
    ("http://127.0.0.1.evil.com", False, "a hostname that only LOOKS like loopback"),
    ("http://localhost.evil.com", False, "and the same trick with localhost"),
]

for origin, want, why in ORIGINS:
    got = S.origin_is_local(origin)
    check(got == want, f"{'accepts' if want else 'refuses'} {origin or '(empty)'} — {why}")

# ============================================================ 4. the accept list

section("accept list")

html = (REPO_ROOT / "hud" / "face-preview.html").read_text(encoding="utf-8")
match = re.search(r'id="chatFile"[^>]*accept="([^"]+)"', html, re.DOTALL)
if match is None:
    check(False, "the picker declares an accept list")
else:
    offered = {s.strip().lower() for s in match.group(1).split(",") if s.strip()}
    missing = sorted(offered - S.ALLOWED_SUFFIXES)
    check(not missing,
          "every suffix the picker offers is one the server accepts",
          f"offered but refused: {', '.join(missing)}" if missing
          else f"{len(offered)} suffixes agree")

html = (REPO_ROOT / "hud" / "face-preview.html").read_text(encoding="utf-8")
check("const UPLOAD_URL" in html and "/upload" in html,
      "the rig knows where to POST")
check(f":{S.DEFAULT_PORT}" in html,
      f"the rig's default upload port matches the server's ({S.DEFAULT_PORT})")

# ============================================================ 5. the live round trip

section("http")


def post(url: str, body: bytes, origin: str | None = None,
         content_type: str | None = None) -> tuple[int, dict]:
    """POST and return (status, parsed-json). An HTTP error is a status, not an exception."""
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", content_type or
                       f"multipart/form-data; boundary={BOUNDARY.decode()}")
    if origin is not None:
        request.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b"{}"
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": raw.decode("utf-8", "replace")[:120]}


with tempfile.TemporaryDirectory() as tmp:
    inbox = Path(tmp) / "inbox"
    # Port 0 lets the OS choose a free one. Hardcoding 8767 here would make this harness fail
    # on the Pi purely because the real assistant was already running, which is a harness that
    # gets skipped rather than fixed.
    httpd = S.serve("127.0.0.1", 0, inbox)
    host, port = httpd.server_address[0], httpd.server_address[1]
    url = f"http://{host}:{port}/upload"
    print(f"           serving on {url}")

    try:
        status, out = post(url, build_body("ECE350_syllabus.pdf", b"%PDF-1.4 syllabus"),
                           origin=f"http://127.0.0.1:8765")
        check(status == 200 and out.get("ok"), "a real upload returns 200 and ok", str(out)[:90])
        landed = inbox / "ECE350_syllabus.pdf"
        check(landed.is_file() and landed.read_bytes() == b"%PDF-1.4 syllabus",
              "the bytes are on disk, byte-exact")
        check(out.get("filename") == "ECE350_syllabus.pdf" and out.get("bytes") == 17,
              "the reply names the file and its size", str(out.get("bytes")))
        check(out.get("relpath", "").startswith("..") is False and "path" in out,
              "the reply carries a path", out.get("path", "")[-40:])

        status, out = post(url, build_body("ECE350_syllabus.pdf", b"different"),
                           origin="http://127.0.0.1:8765")
        check(status == 200 and out.get("filename") == "ECE350_syllabus-2.pdf",
              "a second file of the same name is suffixed, not overwritten",
              out.get("filename", ""))
        check((inbox / "ECE350_syllabus.pdf").read_bytes() == b"%PDF-1.4 syllabus",
              "and the first one is untouched")

        status, out = post(url, build_body("virus.exe", b"MZ"), origin="http://127.0.0.1:8765")
        check(status == 415 and not out.get("ok"),
              "a disallowed extension is refused with 415", f"{status} {out.get('error','')[:50]}")
        check(not (inbox / "virus.exe").exists(), "and nothing was written")

        empty = (b"--" + BOUNDARY + b"\r\nContent-Disposition: form-data; name=\"x\"\r\n\r\n"
                 b"no file\r\n--" + BOUNDARY + b"--\r\n")
        status, out = post(url, empty, origin="http://127.0.0.1:8765")
        check(status == 400, "a form with no file is refused with 400", str(status))

        status, out = post(url, b"x=1", origin="http://127.0.0.1:8765",
                           content_type="application/x-www-form-urlencoded")
        check(status == 415, "a non-multipart POST is refused with 415", str(status))

        status, out = post(url, build_body("sneak.pdf", b"x"), origin="http://evil.example.com")
        check(status == 403 and not (inbox / "sneak.pdf").exists(),
              "a POST from a page on the internet is refused with 403, and writes nothing",
              str(status))

        status, out = post(url, build_body("curl.pdf", b"x"), origin=None)
        check(status == 200, "a request with NO Origin (curl) is allowed", str(status))

        # Oversize goes over a RAW SOCKET, not urllib. The guard being tested refuses on the
        # Content-Length header without reading a byte of the body, and urllib will not send a
        # Content-Length that disagrees with the data it was handed — it overwrote the header
        # with 0, the server correctly answered 411, and the check failed for a reason that had
        # nothing to do with the guard.
        #
        # Sending the actual 65 MB would test the opposite of what the guard is for.
        request = CRLF.join([
            "POST /upload HTTP/1.1",
            f"Host: {host}:{port}",
            "Origin: http://127.0.0.1:8765",
            f"Content-Type: multipart/form-data; boundary={BOUNDARY.decode()}",
            f"Content-Length: {S.MAX_UPLOAD_BYTES + 1}",
            "Connection: close",
            "", "",
        ]).encode()
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(request)
            # Read to EOF, not one recv(). The status line arrives in the first packet and the
            # JSON body may not — checking only the first chunk made the reason-text assertion
            # fail against a server that had sent it perfectly well.
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        reply = b"".join(chunks).decode("utf-8", "replace")
        check(reply.startswith("HTTP/1.1 413"),
              "an oversize Content-Length is refused with 413 before the body is read",
              reply.splitlines()[0] if reply else "(no reply)")
        check("MB and the limit is" in reply,
              "and the refusal says how big it was and what the limit is",
              reply.rsplit("\r\n\r\n", 1)[-1][:80])

        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=10) as response:
            health = json.loads(response.read())
        check(health.get("ok") and health.get("waiting") == 3,
              "GET /healthz reports what is waiting", str(health.get("waiting")))

        # The endpoint and the tool must agree about what "waiting" MEANS. They did not on the
        # first deploy to the Pi: `/healthz` said 1 and the tool said 0, both describing an
        # inbox holding only the committed `.gitkeep`. Asserted against a real directory with a
        # real dotfile in it, because that is the only shape in which the two ever disagreed.
        (inbox / ".gitkeep").write_bytes(b"")
        (inbox / ".DS_Store").write_bytes(b"junk")
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=10) as response:
            health = json.loads(response.read())
        saved_inbox, F.INBOX_DIR = F.INBOX_DIR, inbox
        try:
            tool_count = len(F.inbox_files())
        finally:
            F.INBOX_DIR = saved_inbox
        check(health.get("waiting") == tool_count == 3,
              "and /healthz and list_inbox agree, with dotfiles in the directory",
              f"healthz={health.get('waiting')} tool={tool_count}")
    finally:
        httpd.shutdown()
        httpd.server_close()

# ============================================================ 6. filing

section("filing")


class _FakeIndexer:
    """Stands in for the real one, so filing can be checked without loading torch.

    The rebuild is the slow part — 11.4 s on the Pi before it embeds anything, and it scales
    with the whole corpus. What this
    section is proving is that the right JOBS are asked for — that an academic upload asks for
    the calendar and a datasheet does not — and that is a question about the call, not about
    the embedding.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[set, set]] = []

    def request(self, jobs, sources=None):
        self.calls.append((set(jobs), set(sources or ())))

    def status(self):
        return F._IndexState()


def with_temp_data(fn):
    """Run `fn(inbox)` with the file manager pointed at a throwaway tree and a fake indexer."""
    saved = (F.INBOX_DIR, F.ACADEMIC_DIR, F.PROJECTS_DIR, F.DATA_DIR, F._INDEXER)
    tmp = Path(tempfile.mkdtemp())
    fake = _FakeIndexer()
    try:
        F.DATA_DIR = tmp
        F.INBOX_DIR = tmp / "inbox"
        F.ACADEMIC_DIR = tmp / "academic"
        F.PROJECTS_DIR = tmp / "projects"
        F._INDEXER = fake
        F.INBOX_DIR.mkdir(parents=True)
        return fn(tmp, fake)
    finally:
        (F.INBOX_DIR, F.ACADEMIC_DIR, F.PROJECTS_DIR, F.DATA_DIR, F._INDEXER) = saved
        shutil.rmtree(tmp, ignore_errors=True)


CATEGORY_WORDS = [
    ("academic", "academic"), ("Academic", "academic"), ("syllabus", "academic"),
    ("course", "academic"), ("datasheet", "datasheet"), ("Datasheets", "datasheet"),
    ("component", "datasheet"), ("schematic", "schematic"), ("project_file", "schematic"),
    ("PCB", "schematic"), ("gerber", "schematic"), ("kicad", "schematic"),
]
for word, want in CATEGORY_WORDS:
    check(F._CATEGORIES.get(F._slug(word)) == want, f"category {word!r} -> {want}")

check(F._CATEGORIES.get(F._slug("recipe")) is None, "an invented category maps to nothing")


def _find_cases(tmp: Path, _fake) -> None:
    for name in ("ECE350_syllabus.pdf", "esp32_datasheet.pdf", "amp_board.kicad_sch"):
        (F.INBOX_DIR / name).write_bytes(b"x")

    path, err = F._find_in_inbox("ECE350_syllabus.pdf")
    check(path is not None and path.name == "ECE350_syllabus.pdf", "an exact name resolves", err)

    path, err = F._find_in_inbox("data/inbox/amp_board.kicad_sch")
    check(path is not None and path.name == "amp_board.kicad_sch",
          "a path the model prefixed still resolves", err)

    path, err = F._find_in_inbox("amp board")
    check(path is not None and path.name == "amp_board.kicad_sch",
          "a dictated name resolves through the slug", err)

    path, err = F._find_in_inbox("nothing_like_this.pdf")
    check(path is None and "no file called" in err,
          "a name that is not there is reported, with what IS there", err[:70])

    (F.INBOX_DIR / "amp_board.kicad_pcb").write_bytes(b"x")
    path, err = F._find_in_inbox("amp_board")
    check(path is None and "more than one" in err,
          "an ambiguous name is refused rather than guessed", err[:70])


with_temp_data(_find_cases)


def _academic_case(tmp: Path, fake: _FakeIndexer) -> None:
    """A syllabus is filed and converted into a vault NOTE — never indexed.

    Three shapes in three days, which is why this is pinned rather than assumed: dates were
    extracted with a model (D22 retired that), prose was embedded for retrieval (D23 removed
    that), and now one extraction writes a Markdown note (D24). The assertion is the JOB it
    asks for, because each of those looked the same from the outside.
    """
    (F.INBOX_DIR / "ECE350_syllabus.pdf").write_bytes(b"%PDF")
    out = F.process_inbox_file.invoke(
        {"filename": "ECE350_syllabus.pdf", "category": "academic"})
    check((tmp / "academic" / "ECE350_syllabus.pdf").is_file(),
          "an academic PDF lands in data/academic/", out[:70])
    check(not (F.INBOX_DIR / "ECE350_syllabus.pdf").exists(), "and leaves the inbox")

    # The `syllabus` job, and NOT `vectors`. data/academic/ is excluded from the datasheet walk,
    # so an embedding rebuild would spend the 11.4s fixed cost and do nothing for this file.
    check(fake.calls and fake.calls[0][0] == {"syllabus"},
          "it asks for the syllabus conversion, and NOT an embedding rebuild",
          str(fake.calls))
    check(fake.calls[0][1] == {"ECE350_syllabus.pdf"},
          "and names the one file, so it costs ONE api call and not one per syllabus on disk",
          str(fake.calls[0][1]))

    lowered = out.lower()
    check("background" in lowered and "not finished" in lowered,
          "the answer says it is running in the background and is not done", out[-90:])
    check("canvas" in lowered,
          "and still points him at Canvas for the dates", out[-70:])
    check("index_status" in out,
          "and names how to check whether it finished")


with_temp_data(_academic_case)


def _academic_takes_any_suffix(tmp: Path, fake: _FakeIndexer) -> None:
    """A non-PDF course document is filed, but not sent to the converter.

    Filed rather than refused, because it has nowhere better to go: refusing it means it gets
    categorised as a `datasheet` instead and lands in the pool the FIRMWARE agent retrieves
    from. Not converted, because `pypdf` is the only reader there is.
    """
    (F.INBOX_DIR / "notes.txt").write_bytes(b"hi")
    out = F.process_inbox_file.invoke({"filename": "notes.txt", "category": "academic"})
    check((tmp / "academic" / "notes.txt").is_file(),
          "a non-PDF course document is filed rather than refused", out[:70])
    check(not fake.calls, "and no conversion is started for it — only a PDF can be read")
    check("stored and not read" in out.lower(), "and the answer says so", out[-60:])


with_temp_data(_academic_takes_any_suffix)


def _syllabus_stays_out_of_the_datasheets(tmp: Path, fake: _FakeIndexer) -> None:
    """The exclusion that is now the ONLY thing keeping a syllabus out of firmware answers.

    While there were two collections, a leak had a second pool to land in. There is one pool
    now, and it is the one the FIRMWARE agent retrieves from — so `data/academic/` being
    excluded from the walk is load-bearing in a way it was not before.
    """
    import tools.vector_db as V

    check(V.EXCLUDED_FROM_DATASHEETS.name == "academic",
          "vector_db still excludes data/academic/ from the datasheet walk",
          str(V.EXCLUDED_FROM_DATASHEETS))
    check(not hasattr(V, "ACADEMIC_COLLECTION"),
          "and the academic collection constant is gone, not merely unused")
    check(V.DATASHEET_COLLECTION == "datasheets",
          "the datasheet collection is untouched — the firmware RAG still works")


with_temp_data(_syllabus_stays_out_of_the_datasheets)


def _datasheet_case(tmp: Path, fake: _FakeIndexer) -> None:
    (F.INBOX_DIR / "esp32.pdf").write_bytes(b"%PDF")
    out = F.process_inbox_file.invoke(
        {"filename": "esp32.pdf", "category": "datasheet", "folder": "espressif"})
    check((tmp / "espressif" / "esp32.pdf").is_file(),
          "a datasheet lands in the folder the model chose", out[:70])
    check(fake.calls and fake.calls[0][0] == {"vectors"},
          "it asks for the vector store and NOT the calendar — a datasheet has no deadlines",
          str(fake.calls))

    (F.INBOX_DIR / "part.pdf").write_bytes(b"%PDF")
    F.process_inbox_file.invoke({"filename": "part.pdf", "category": "datasheet"})
    check((tmp / F.DEFAULT_DATASHEET_FOLDER / "part.pdf").is_file(),
          f"and with no folder given it goes to {F.DEFAULT_DATASHEET_FOLDER}/")

    (F.INBOX_DIR / "evil.pdf").write_bytes(b"%PDF")
    F.process_inbox_file.invoke(
        {"filename": "evil.pdf", "category": "datasheet", "folder": "../../../etc"})
    escaped = list(tmp.parent.glob("etc/evil.pdf"))
    check(not escaped and any(p.name == "evil.pdf" for p in tmp.rglob("*.pdf")),
          "a folder name that tries to escape data/ is flattened, not followed")


with_temp_data(_datasheet_case)


def _schematic_case(tmp: Path, fake: _FakeIndexer) -> None:
    (F.INBOX_DIR / "amp_board.kicad_sch").write_bytes(b"(kicad_sch)")
    out = F.process_inbox_file.invoke(
        {"filename": "amp_board.kicad_sch", "category": "schematic", "project": "amp board"})
    check((tmp / "projects" / "amp board" / "amp_board.kicad_sch").is_file(),
          "a schematic lands in its project folder under data/projects/", out[:70])
    check(not fake.calls,
          "and starts NO rebuild — kicad_parser reads it live, so it works immediately")

    (F.INBOX_DIR / "lonely.kicad_pcb").write_bytes(b"(kicad_pcb)")
    F.process_inbox_file.invoke({"filename": "lonely.kicad_pcb", "category": "schematic"})
    check((tmp / "projects" / "lonely" / "lonely.kicad_pcb").is_file(),
          "with no project name it is filed under the file's own name")

    (F.INBOX_DIR / "board.pdf").write_bytes(b"%PDF")
    F.process_inbox_file.invoke(
        {"filename": "board.pdf", "category": "schematic", "project": "amp board"})
    check((tmp / "projects" / "amp board" / "board.pdf").is_file(),
          "a board PDF goes to the same project")
    check(fake.calls and fake.calls[-1][0] == {"vectors"},
          "and DOES start a rebuild — a PDF is only readable through the vector store",
          str(fake.calls[-1]))


with_temp_data(_schematic_case)


def _zip_case(tmp: Path, fake: _FakeIndexer) -> None:
    archive = F.INBOX_DIR / "gerbers.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("amp.kicad_sch", "(kicad_sch)")
        zf.writestr("fab/top.gbr", "G04*")
        zf.writestr("../../../escape.txt", "pwned")
        zf.writestr("/absolute.txt", "pwned")

    out = F.process_inbox_file.invoke(
        {"filename": "gerbers.zip", "category": "schematic", "project": "amp"})
    project = tmp / "projects" / "amp"
    check((project / "amp.kicad_sch").is_file(), "a zip is unpacked into the project", out[:70])
    check((project / "fab" / "top.gbr").is_file(), "including its subdirectories")
    check(not (tmp.parent / "escape.txt").exists() and not list(tmp.parent.glob("escape.txt")),
          "a member pointing outside the folder writes nothing OUTSIDE it")
    check(not any(p.name == "escape.txt" for p in tmp.rglob("*")),
          "and is skipped rather than flattened into the project")
    check("skipped" in out.lower(), "and the refusal is reported, not silent", out[-90:])
    check(not archive.exists(), "the zip itself is consumed once it is unpacked")
    check("amp.kicad_sch" in out, "and the answer names the schematic it found", out[:110])


with_temp_data(_zip_case)


def _dotfile_cases(tmp: Path, fake: _FakeIndexer) -> None:
    """`.gitkeep` is committed so the folders survive a fresh clone, and the FIRST live upload
    caught what that costs: he reported two files waiting and offered to file the one whose
    category was "unclear". `.DS_Store` and `Thumbs.db` come through the same door."""
    (F.INBOX_DIR / ".gitkeep").write_bytes(b"")
    (F.INBOX_DIR / ".DS_Store").write_bytes(b"junk")
    check(F.inbox_files() == [], "an inbox holding only dotfiles is EMPTY", str(F.inbox_files()))
    check("empty" in F.list_inbox.invoke({}).lower(), "and list_inbox says so")

    (F.INBOX_DIR / "real.pdf").write_bytes(b"%PDF")
    listing = F.list_inbox.invoke({})
    check("real.pdf" in listing and ".gitkeep" not in listing and ".DS_Store" not in listing,
          "and a real upload beside them is the only thing listed",
          listing.replace("\n", " | "))

    F.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    (F.PROJECTS_DIR / ".gitkeep").write_bytes(b"")
    check("no uploaded projects" in F.list_project_files.invoke({"project": ""}).lower(),
          "and a projects folder holding only .gitkeep has no projects in it")


with_temp_data(_dotfile_cases)


def _list_cases(tmp: Path, fake: _FakeIndexer) -> None:
    check("empty" in F.list_inbox.invoke({}).lower(), "an empty inbox says so")
    (F.INBOX_DIR / "ECE350_syllabus.pdf").write_bytes(b"x" * 2048)
    (F.INBOX_DIR / "amp.kicad_sch").write_bytes(b"x")
    listing = F.list_inbox.invoke({})
    check("ECE350_syllabus.pdf" in listing and "amp.kicad_sch" in listing,
          "list_inbox names what is waiting")
    check("academic" in listing and "schematic" in listing,
          "and guesses a category for each, as a hint", listing.replace("\n", " | ")[:110])

    check("no uploaded projects" in F.list_project_files.invoke({"project": ""}).lower(),
          "an empty projects folder says so")
    (F.PROJECTS_DIR / "amp_board").mkdir(parents=True)
    (F.PROJECTS_DIR / "amp_board" / "amp_board.kicad_sch").write_bytes(b"x")
    projects = F.list_project_files.invoke({"project": ""})
    check("amp_board" in projects and "amp_board.kicad_sch" in projects,
          "list_project_files names the project and its files")


with_temp_data(_list_cases)

GUESSES = [
    ("ECE350_syllabus.pdf", "academic"),
    ("ece 350 course outline.pdf", "academic"),
    ("PHYS201.pdf", "academic"),
    ("esp32_datasheet.pdf", "datasheet"),
    ("HX711 reference manual.pdf", "datasheet"),
    ("amp_board.kicad_sch", "schematic"),
    ("gerbers.zip", "schematic"),
    ("holiday.png", ""),
]
for name, want in GUESSES:
    got = F.guess_category(Path(name))
    check(got == want, f"guess_category({name!r}) -> {want or 'no guess'}", got or "(none)")

# ============================================================ 7. the agents can reach it

section("agent wiring")

from tools.kicad_parser import UPLOADED_PROJECTS, search_roots

check(UPLOADED_PROJECTS == REPO_ROOT / "data" / "projects",
      "the KiCad reader looks in data/projects/", str(UPLOADED_PROJECTS))
roots = search_roots()
check(all(r.is_dir() for r in roots),
      "search_roots returns only directories that exist",
      ", ".join(str(r) for r in roots) or "(none yet)")
if UPLOADED_PROJECTS.is_dir():
    check(roots and roots[0] == UPLOADED_PROJECTS,
          "and looks there FIRST — a board he just uploaded beats one from last term")

names = {t.name for t in F.FILE_TOOLS}
check(names == {"list_inbox", "process_inbox_file", "list_project_files", "index_status"},
      "the four file tools are exported", ", ".join(sorted(names)))

import agents.hardware_agent as HW

bound = {t.name for t in HW.TOOLS}
check(names <= bound, "the HARDWARE agent binds all four", ", ".join(sorted(bound - names)))
check("extract_kicad_bom" in bound and "calculate_ipc2221_trace_width" in bound,
      "without losing the tools it already had")
check(set(HW._BY_NAME) == bound, "and its dispatch table covers every one of them")

import agents.firmware_agent as FW

bound = {t.name for t in FW._ALL_TOOLS}
check({t.name for t in F.FILE_TOOLS} <= bound, "the FIRMWARE agent binds the file tools")
check({"extract_kicad_bom", "analyze_kicad_pcb"} <= bound,
      "and the KiCad readers, so a pinout question can look at his actual board",
      ", ".join(sorted(bound)))
check("data/projects/" in FW.FIRMWARE_PROMPT_TEMPLATE,
      "and its prompt names data/projects/")

import agents.persona_agent as PA

check("paperclip" in PA.PERSONA_PROMPT_TEMPLATE,
      "the GENERAL/persona agent is told about the paperclip")
check("process_inbox_file" in PA.PERSONA_PROMPT_TEMPLATE,
      "and about the tool that files it")

# The seam. Everything above proves the endpoint works and the filing works; this proves the
# assistant actually STARTS the endpoint. Read as source rather than executed, because running
# `main()` would open a microphone — and a feature that is built and not wired is the failure
# this repo has already had twice (the typed channel in D6, gesture approval in D20).
import inspect

import engine.run_voice as RV

wiring = inspect.getsource(RV.main)
for token, why in [
    ("serve_uploads", "run_voice starts the upload server"),
    ('hud_cfg["upload_port"]', "and takes the port from config, not a literal"),
    ("--no-upload", "there is a flag to run without it"),
    ("upload_server.shutdown", "and it is shut down with the rest"),
    ("inbox_files", "and a startup line names anything already waiting"),
]:
    check(token in wiring, why, token)

check("except OSError" in wiring,
      "a taken port is caught, so losing the paperclip cannot cost him his voice")

# The rig's file-chooser diagnostic must be guarded: PyGObject raises TypeError on an unknown
# signal name, and that would take the WINDOW down, not just the log line.
chooser = (REPO_ROOT / "hud" / "float.py").read_text(encoding="utf-8")
check("run-file-chooser" in chooser and "except TypeError" in chooser,
      "float.py watches the file chooser, and a bad signal name cannot break the window")

# Both CLIs, run as SCRIPTS in a fresh interpreter. This is not the same test as importing them:
# `python tools/file_manager.py` puts `tools/` on sys.path and not the repo root, so a
# module-level `from engine.server import ...` resolves when an agent imports the file and
# raises `ModuleNotFoundError` when the CLI runs it. Every check above passed while that was
# broken; the Pi found it, one command after the deploy.
import subprocess                                                    # noqa: E402

for argv, why in [
    (["tools/file_manager.py", "--list"], "python tools/file_manager.py --list runs"),
    (["tools/file_manager.py", "--projects"], "python tools/file_manager.py --projects runs"),
    (["engine/server.py", "--help"], "python engine/server.py --help runs"),
]:
    done = subprocess.run([sys.executable, *argv], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=60)
    check(done.returncode == 0, why,
          (done.stderr.strip().splitlines() or ["exit 0"])[-1][:100])

# The Canvas sync, which replaced PDF date extraction on 2026-08-23.
import agents.academic_agent as AC                                   # noqa: E402
import tools.canvas_sync as CS                                       # noqa: E402

check([t.name for t in AC.ACADEMIC_TOOLS] == ["sync_canvas_calendar", "read_from_vault"],
      "the ACADEMIC agent binds the Canvas sync AND the vault reader",
      ", ".join(t.name for t in AC.ACADEMIC_TOOLS))
check("sync_canvas_calendar" in AC.ACADEMIC_PROMPT_TEMPLATE
      and "Do NOT call it to answer an ordinary question" in AC.ACADEMIC_PROMPT_TEMPLATE,
      "and is told when to call it and when not to — a sync per question is a network "
      "round trip per turn")
check("using ONLY the calendar below" in AC.ACADEMIC_PROMPT_TEMPLATE
      and "say you do not know" in AC.ACADEMIC_PROMPT_TEMPLATE,
      "and the strict-grounding directive SURVIVED the RAG removal, both halves")
check("Do not answer a policy question without looking" in AC.ACADEMIC_PROMPT_TEMPLATE,
      "and he is told to search his notes before answering a policy question",
      "the notes are behind a tool call, so refusing without looking is the failure now")
check("Never take a date out of a note" in AC.ACADEMIC_PROMPT_TEMPLATE,
      "and that Canvas owns every date even when a note carries one")

# The feed URL is a credential. There must be no default in the source, or committing the file
# publishes the token — this repo has a GitHub remote.
source = (REPO_ROOT / "tools" / "canvas_sync.py").read_text(encoding="utf-8")
check(not re.search(r"user_[A-Za-z0-9]{20,}", source),
      "no Canvas feed token is hardcoded in canvas_sync.py")
check(CS.CANVAS_ICS_ENV == "ODDBALL_CANVAS_ICS" and "instructure.com" not in source.replace(
      "<school>.instructure.com", ""),
      "the URL comes from .env, which is gitignored and not deployed")
# The env var has to be CLEARED for this one. The preamble above loads `.env`, so on the Pi —
# where the URL is genuinely configured — `canvas_url("")` returns it and the check cannot fire.
# It passed on Windows and failed on the Pi, which is the wrong way round for a check about a
# missing configuration: it was testing the authoring box's emptiness, not the code.
_saved_ics = os.environ.pop(CS.CANVAS_ICS_ENV, None)
try:
    CS.canvas_url("")
    check(False, "a missing feed URL raises with instructions")
except ValueError as exc:
    check("Calendar Feed" in str(exc), "a missing feed URL raises with instructions",
          str(exc).splitlines()[0][:70])
finally:
    if _saved_ics is not None:
        os.environ[CS.CANVAS_ICS_ENV] = _saved_ics

check(CS.canvas_url("https://example.test/x.ics") == "https://example.test/x.ics",
      "an explicit URL wins over the environment")
try:
    CS.canvas_url("not-a-url")
    check(False, "a URL that is not a URL is refused")
except ValueError as exc:
    check("does not look like a URL" in str(exc), "a URL that is not a URL is refused")

for title, want in [("Midterm Exam 2", "exam"), ("Final Exam", "exam"),
                    ("Knowledge Check VM.1", "quiz"), ("Quiz 3", "quiz"),
                    ("Group Project Milestone 1", "project"),
                    ("Homework 4", "assignment"), ("Data Activity VM.1", "assignment")]:
    check(CS._classify(title) == want, f"_classify({title!r}) -> {want}", CS._classify(title))

title, course, section = CS._split_summary(
    "Sample AI Policy&#8212;Responsible Use  [POSC201.W02_Fall 2026]")
check(course == "POSC201", "the course code is cleaned of section and term", course)
check(section == "POSC201.W02_Fall 2026", "and the full string is kept as `section`", section)
check("—" in title and "&#" not in title,
      "and HTML entities are unescaped — that text is read aloud by Piper", title[-30:])

check(CS._split_summary("No brackets here")[0] == "No brackets here",
      "a summary with no bracket keeps its text rather than raising")

import datetime as _dt                                               # noqa: E402

# THE date bug. Canvas stores an 11:59 PM due time as 03:59Z the NEXT day, so `.date()` on the
# raw UTC value is off by one — silently, always, in the direction that suggests an extra day.
utc_late = _dt.datetime(2026, 9, 2, 3, 59, tzinfo=_dt.timezone.utc)
local_day = utc_late.astimezone().date()
check(CS._due_date(utc_late) == local_day,
      "a UTC due time is converted to the LOCAL date, not truncated",
      f"{utc_late.isoformat()} -> {CS._due_date(utc_late)}")
check(CS._due_date(_dt.date(2026, 9, 1)) == _dt.date(2026, 9, 1),
      "an all-day date is used as-is")
check(CS._due_date(None) is None, "a missing date is None rather than a crash")

import router

prompt = router.ROUTER_PROMPT
check("sync Canvas" in prompt and "ACADEMIC, not OS" in prompt,
      "the router sends 'update my schedule' to ACADEMIC rather than OS")
check("UPLOAD IS ALWAYS GENERAL" in prompt,
      "the router has an unambiguous rule for an upload announcement")
check("is not an upload: route that normally" in prompt,
      "and a rule that a question about the CONTENT still routes normally")
check("HARDWARE" in prompt and "ACADEMIC" in prompt,
      "and still names the routes that question would go to")

# `FILE_INSTRUCTION` is the text three agents share. The one rule that must survive any
# edit to it is the honesty rule — everything else is guidance, and this is the sentence
# that stops him calling a datasheet searchable while it is still being embedded.
check("NEVER claim you have filed something unless the tool actually ran"
      in F.FILE_INSTRUCTION, "the shared instruction forbids claiming an unrun tool")
check("Never tell him a document is searchable" in F.FILE_INSTRUCTION,
      "and forbids calling a background rebuild finished")

# ============================================================

print("\n" + "=" * 76)
total = PASSED + FAILED
if FAILED:
    print(f"{PASSED}/{total} checks passed — {FAILED} FAILED")
else:
    print(f"{total}/{total} checks passed  — all green")
    print("parser, names, origin, accept list, live HTTP round trip, filing, agent wiring")
raise SystemExit(1 if FAILED else 0)
