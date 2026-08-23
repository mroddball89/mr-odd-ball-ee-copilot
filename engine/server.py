#!/usr/bin/env python3
"""
Module:  server.py
Purpose: POST /upload — take a file from the chat panel and land it in data/inbox/.
Author:  LB
Date:    2026-08-23

    python engine/server.py                  # run it alone, for testing
    curl -F file=@datasheet.pdf http://127.0.0.1:8767/upload

Dropping a PDF into `data/academic/` over SSH is the last part of this project that still
needed a file manager. This is the other end of the paperclip in `hud/face-preview.html`: the
browser POSTs a file here, it lands in `data/inbox/`, and `tools/file_manager.py` — bound to the
agents — moves it where it belongs and rebuilds whatever index it feeds.

## Why this is not FastAPI, and why it is not on port 8765

The request that produced this file asked for a FastAPI endpoint in `engine/server.py`. **There
is no FastAPI server in this repo, and that is deliberate.** D17 deleted `ui/` and dropped
`fastapi`, `uvicorn` and `pywebview` from requirements, because a second surface had been built
beside the one that already existed. Bringing an ASGI stack back for a single endpoint would put
it on the import path of the voice loop for the sake of eleven lines of routing. Receiving a
file is a byte split and a `write_bytes`; `http.server` does it.

Sharing port 8765 with the rig was tried and is not possible. `orchestrator/hud_bridge.py`
serves the page from `websockets`' `process_request` hook, and that hook is handed a
`websockets.http11.Request` whose fields are `path`, `headers` and `_exception` — **there is no
body**. The bytes of a POST stay in the transport buffer where the frame parser will meet them.
Reading them would mean reaching into the sans-io protocol's private reader, which is a fragile
place to put a file transfer. So this listens on its own port, and the rig is told which one the
same way it is told where the WebSocket is.

## The three guards, and what each is actually for

**Extension allow-list.** Not a security boundary — nothing here executes what it is given — but
a typo boundary. `data/` is walked by `tools/vector_db.py` with `**/*.pdf` and the inbox is
walked by the file manager; a stray `.DS_Store` or a 2 GB video in there is noise in a folder
whose whole job is to be unambiguous.

**Size cap.** The body is read into memory before it is parsed, because that is what makes the
multipart parser a pure function of bytes and therefore testable. 64 MB is roughly ten times the
largest datasheet in `data/`, and a Pi 5 with 8 GB can hold it; the cap is what stops a mis-aimed
`curl` from being an out-of-memory kill of the assistant.

**Origin check.** This binds to 127.0.0.1, which stops the network reaching it — but it does not
stop *the browser on this machine* reaching it. A `multipart/form-data` POST is a CORS "simple
request": any page LB happens to have open can send one to any loopback port with no preflight,
and although it cannot read the reply, the file still lands. So a request carrying an `Origin`
header must carry a local one. Requests with **no** Origin (curl, a script) are allowed — that is
a shell on the box, which already has `cp`.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

LOG = logging.getLogger("oddball.upload")

REPO_ROOT = Path(__file__).resolve().parents[1]

# The staging directory. Nothing reads from here except `tools/file_manager.py`, and nothing
# stays here — a file in the inbox is a file Mr Odd Ball has not filed yet, which is exactly
# what `list_inbox` reports.
INBOX_DIR = REPO_ROOT / "data" / "inbox"

DEFAULT_HOST = "127.0.0.1"
# 8765 is the rig and its WebSocket; 8766 is `tools/face_stage.py`'s documented port and is left
# free so a stage can run beside the live assistant. This is the next one along.
DEFAULT_PORT = 8767

# What the picker offers and what this accepts. The two lists must agree, or the OS file dialog
# will happily hand over something the server then rejects — so the `accept` attribute in
# `hud/face-preview.html` is written from this same list, and `tools/verify_upload.py` checks
# the two have not drifted apart.
ALLOWED_SUFFIXES = frozenset({
    # documents — syllabi, datasheets, notes
    ".pdf", ".txt", ".md", ".csv",
    # KiCad, which is the whole reason data/projects/ exists
    ".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_prl", ".net",
    # bundles — gerbers, a zipped project
    ".zip",
    # board photos and scope captures
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
})

# See the module docstring. Read into memory, so this is also the memory cost of one upload.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

# A filename is chosen by whoever is uploading and lands on a real filesystem. Everything
# outside this becomes an underscore, which turns "../../.ssh/authorized_keys" into a filename
# rather than a traversal — the same rule, for the same reason, as `tools/knowledge_vault.py`.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_MAX_NAME_LEN = 120

# `filename="x.pdf"` out of a Content-Disposition header. The quoted form first, because that is
# what every browser sends; the bare form is for hand-rolled clients.
_FILENAME_RE = re.compile(r'filename\*?=(?:"([^"]*)"|([^;]+))', re.IGNORECASE)
_NAME_RE = re.compile(r'\bname=(?:"([^"]*)"|([^;]+))', re.IGNORECASE)
_BOUNDARY_RE = re.compile(r'boundary=(?:"([^"]+)"|([^;]+))', re.IGNORECASE)

# Origins allowed to POST here. Host only: the PORT is deliberately not checked, because the rig
# may be served from 8765 by the live assistant or from 8766 by a stage, and pinning the port
# would break the stage for no gain — anything on loopback is already inside the boundary.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


# --- the multipart parser ----------------------------------------------------------------
#
# Hand-rolled because `cgi.FieldStorage` was removed in Python 3.13, and because pulling a whole
# web framework in to split a byte string on a delimiter is the trade D17 already refused once.
# Kept as a pure function of bytes, so `tools/verify_upload.py` can assert on it without opening
# a socket.

def parse_multipart(body: bytes, boundary: bytes) -> list[tuple[dict[str, str], bytes]]:
    """Split a `multipart/form-data` body into its parts.

    Args:
        body:     the whole request body.
        boundary: the boundary token from the Content-Type header, without the leading `--`.

    Returns:
        One `(headers, data)` per part, in order, with header names lowercased. Malformed input
        yields the parts it could read rather than raising — a truncated upload should be
        reported as a short file, not as a stack trace inside a request handler.

    Delimiters are found as `CRLF + "--" + boundary`, never as a bare `split()` on the boundary.
    A `.pdf` can contain any byte sequence at all, including this one; requiring the CRLF in
    front is what every real parser does and what stops a file being cut in half by its own
    contents.
    """
    if not boundary:
        return []

    sep = b"--" + boundary
    start = body.find(sep)
    if start < 0:
        return []

    parts: list[tuple[dict[str, str], bytes]] = []
    pos = start + len(sep)

    while True:
        if body[pos:pos + 2] == b"--":          # the closing delimiter: "--boundary--"
            break
        if body[pos:pos + 2] != b"\r\n":        # anything else here is malformed
            break
        pos += 2

        end = body.find(b"\r\n" + sep, pos)
        if end < 0:                             # unterminated final part — a truncated upload
            break

        segment = body[pos:end]
        head, marker, data = segment.partition(b"\r\n\r\n")
        pos = end + 2 + len(sep)
        if not marker:                          # a part with no header block at all
            continue

        headers: dict[str, str] = {}
        for line in head.split(b"\r\n"):
            name, colon, value = line.partition(b":")
            if colon:
                headers[name.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
        parts.append((headers, data))

    return parts


def boundary_from(content_type: str) -> bytes:
    """The boundary token out of a Content-Type header, or `b""` if there is not one."""
    match = _BOUNDARY_RE.search(content_type or "")
    if not match:
        return b""
    return (match.group(1) or match.group(2) or "").strip().encode("latin-1")


def _disposition(headers: dict[str, str]) -> tuple[str, str]:
    """`(field_name, filename)` from a part's Content-Disposition. Either may be empty."""
    disposition = headers.get("content-disposition", "")
    name = _NAME_RE.search(disposition)
    filename = _FILENAME_RE.search(disposition)
    return (
        ((name.group(1) or name.group(2)) if name else "").strip(),
        ((filename.group(1) or filename.group(2)) if filename else "").strip(),
    )


def pick_file_part(parts: list[tuple[dict[str, str], bytes]]) -> tuple[str, bytes] | None:
    """The part holding the upload: the one named `file`, else the first with a filename.

    Returns `(filename, data)`, or None when no part carried a filename at all — which is what a
    form submitted with an empty picker looks like, and is a 400 rather than a crash.
    """
    named, first = None, None
    for headers, data in parts:
        field, filename = _disposition(headers)
        if not filename:
            continue
        if first is None:
            first = (filename, data)
        if field == "file" and named is None:
            named = (filename, data)
    return named or first


# --- naming ------------------------------------------------------------------------------

def safe_name(raw: str) -> str:
    """One filesystem-safe filename. Never empty, never a path, never `.` or `..`.

    Takes the basename on BOTH separators. A browser sends the bare name, but a hand-rolled
    client may send whatever it was given, and `Path("a\\b.pdf").name` on Linux is the whole
    string — so splitting on `/` alone would leave a backslash in a Pi filename, and splitting on
    the backslash alone would leave a directory in a Windows one.
    """
    text = (raw or "").replace("\\", "/").split("/")[-1]
    cleaned = _SAFE_NAME.sub("_", text).strip(". ").strip()
    if not cleaned:
        return "upload"
    if len(cleaned) > _MAX_NAME_LEN:
        # Truncate the STEM, not the string, or the extension goes and with it the routing: the
        # file manager and the vector store both decide what a file is by its suffix.
        stem, dot, suffix = cleaned.rpartition(".")
        if dot and len(suffix) <= 12:
            cleaned = stem[:_MAX_NAME_LEN - len(suffix) - 1] + "." + suffix
        else:
            cleaned = cleaned[:_MAX_NAME_LEN]
    return cleaned


def unique_path(directory: Path, filename: str) -> Path:
    """A path in `directory` that does not exist yet, by suffixing `-2`, `-3`, ...

    Deliberately not an overwrite. Uploading `datasheet.pdf` twice usually means two different
    datasheets that a phone or a browser both called `datasheet.pdf`, and silently replacing the
    first is a data loss that looks like a successful upload.
    """
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, dot, suffix = filename.rpartition(".")
    stem, suffix = (stem, "." + suffix) if dot else (filename, "")
    for n in range(2, 1000):
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"a thousand files are already called {filename}")


def save_upload(filename: str, data: bytes, inbox: Path | None = None) -> Path:
    """Write one uploaded file into the inbox. Returns where it landed.

    Raises:
        ValueError: the extension is not on the allow-list.
        OSError:    the write failed, or the sanitised path escaped the inbox.
    """
    inbox = (inbox or INBOX_DIR).resolve()
    name = safe_name(filename)

    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"{name!r} is a {suffix or 'suffixless'} file, and I only take "
            f"{', '.join(sorted(ALLOWED_SUFFIXES))}")

    inbox.mkdir(parents=True, exist_ok=True)
    path = unique_path(inbox, name)

    # Belt and braces over `safe_name`. A guard whose only check is the guard above it is a guard
    # nobody notices has stopped working — the same argument `knowledge_vault._resolve` makes for
    # its own second check.
    if inbox not in path.resolve().parents:
        raise OSError(f"refusing to write outside the inbox: {path}")

    path.write_bytes(data)
    LOG.info("saved %s (%d bytes)", path, len(data))
    return path


# --- the HTTP surface --------------------------------------------------------------------

def pending_uploads(inbox: Path | None = None) -> list[Path]:
    """Files genuinely waiting to be filed, oldest first. The one definition of "waiting".

    Dotfiles are not uploads. `data/inbox/.gitkeep` is committed so the directory survives a
    fresh clone, and `.DS_Store` and `Thumbs.db` arrive at the same door.

    This lives here, in the layer with no dependencies, and `tools/file_manager.inbox_files`
    calls it — because the first deploy to the Pi had the two counting differently. `/healthz`
    reported `"waiting": 1` while the startup line that fires on a non-empty inbox stayed
    silent, and both were describing the same empty directory. Two answers to one question is
    how they disagree, which is the argument `engine/core.py` already makes about the
    conversation log living in exactly one place.
    """
    inbox = inbox or INBOX_DIR
    if not inbox.exists():
        return []
    files = [p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def origin_is_local(origin: str) -> bool:
    """True if `origin` is a loopback http(s) origin. An ABSENT origin is handled by the caller.

    `null` — which is what a `file://` page sends — is deliberately NOT local. The paperclip only
    means anything while the assistant is running, and while it is running the rig is served over
    HTTP by the bridge. Allowing `null` would also allow every sandboxed iframe on the internet,
    which is a large door to open for a case that cannot happen.
    """
    if not origin:
        return False
    parts = urlsplit(origin)
    return parts.scheme in ("http", "https") and (parts.hostname or "") in _LOCAL_HOSTS


class UploadHandler(BaseHTTPRequestHandler):
    """`POST /upload`, plus a `GET /healthz` so a caller can tell "not running" from "refused"."""

    server_version = "OddBallUpload/1.0"
    protocol_version = "HTTP/1.1"

    # Set by `serve()`. An attribute on the class rather than a global, so a harness can point one
    # server at a temporary directory without affecting another.
    inbox: Path = INBOX_DIR

    def log_message(self, fmt: str, *args) -> None:
        """Into the logger, not onto stderr.

        `BaseHTTPRequestHandler` prints to stderr by default, and stderr on the Pi is the journal
        for `oddball.service` — one line per request, interleaved with the turn timings that are
        the actual measurement in that log.
        """
        LOG.debug("%s - %s", self.address_string(), fmt % args)

    # --- replying

    def _send(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            # The browser hung up mid-reply. The file is already saved; there is nothing to undo
            # and nothing worth logging above debug.
            LOG.debug("client closed before the reply was written")

    def _send_cors(self) -> None:
        """Echo the Origin back when it is one we accept, so the page can READ the reply.

        Echoed rather than `*`, and only for an origin that would pass `_allowed()` — a wildcard
        here would let a page on the internet read the inbox's absolute path, which contains LB's
        home directory.
        """
        origin = self.headers.get("Origin", "")
        if origin_is_local(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _allowed(self) -> bool:
        """Whether this request may write. See the module docstring's third guard."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True                     # curl, or a script: already has the filesystem
        return origin_is_local(origin)

    # --- the methods

    def do_OPTIONS(self) -> None:            # noqa: N802 — BaseHTTPRequestHandler's spelling
        """Preflight. A multipart POST does not trigger one, but a client that adds a header will,
        and answering it costs four lines."""
        self.send_response(HTTPStatus.NO_CONTENT.value)
        self._send_cors()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:                # noqa: N802
        path = urlsplit(self.path).path
        if path in ("/healthz", "/upload"):
            # `/upload` answers a GET too, with the same body, because "is the upload endpoint
            # there" is a question a browser asks by typing the URL in.
            self._send(HTTPStatus.OK, {
                "ok": True,
                "inbox": str(self.inbox),
                "waiting": len(pending_uploads(self.inbox)),
                "max_bytes": MAX_UPLOAD_BYTES,
                "accepts": sorted(ALLOWED_SUFFIXES),
            })
            return
        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "no such path"})

    def do_POST(self) -> None:               # noqa: N802
        if urlsplit(self.path).path != "/upload":
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "no such path"})
            return

        if not self._allowed():
            LOG.warning("refused an upload from origin %r", self.headers.get("Origin"))
            self._send(HTTPStatus.FORBIDDEN,
                       {"ok": False, "error": "uploads are accepted from this machine only"})
            return

        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                       {"ok": False, "error": "send multipart/form-data"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length <= 0:
            self._send(HTTPStatus.LENGTH_REQUIRED,
                       {"ok": False, "error": "a Content-Length is required"})
            return
        if length > MAX_UPLOAD_BYTES:
            # Answered WITHOUT reading the body, which is the point of checking the header:
            # reading 2 GB in order to refuse it is the denial of service, not the upload.
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
                "ok": False,
                "error": f"that file is {length / 1e6:.0f} MB and the limit is "
                         f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB"})
            return

        try:
            body = self.rfile.read(length)
        except OSError as exc:
            LOG.warning("upload body did not arrive: %s", exc)
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "the upload was cut off"})
            return

        chosen = pick_file_part(parse_multipart(body, boundary_from(content_type)))
        if chosen is None:
            self._send(HTTPStatus.BAD_REQUEST,
                       {"ok": False, "error": "no file was attached to that form"})
            return

        filename, data = chosen
        if not data:
            self._send(HTTPStatus.BAD_REQUEST,
                       {"ok": False, "error": f"{filename} came through empty"})
            return

        try:
            path = save_upload(filename, data, self.inbox)
        except ValueError as exc:
            self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": str(exc)})
            return
        except OSError as exc:
            LOG.exception("could not save %r", filename)
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR,
                       {"ok": False, "error": f"could not save it: {exc}"})
            return

        self._send(HTTPStatus.OK, {
            "ok": True,
            "filename": path.name,
            "path": str(path),
            # Relative as well as absolute: the absolute path is what LB needs in a log, and the
            # relative one is what the agents pass around, since `data/inbox/x.pdf` means the
            # same thing on Windows and on the Pi.
            "relpath": path.relative_to(REPO_ROOT).as_posix()
            if path.is_relative_to(REPO_ROOT) else path.name,
            "bytes": len(data),
        })


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          inbox: Path | None = None) -> ThreadingHTTPServer:
    """Bind the upload server and start serving it on a daemon thread. Returns the server.

    Args:
        host:  loopback by default, and there is no reason to change it. See the Origin note.
        port:  see DEFAULT_PORT.
        inbox: where uploads land; `data/inbox/` by default. A harness passes a temp dir.

    Raises:
        OSError: the port is taken. The caller decides what that means — for
                 `engine/run_voice.py` it means log it and carry on without uploads, because
                 losing the paperclip must never cost him his voice.
    """
    target = (inbox or INBOX_DIR).resolve()
    target.mkdir(parents=True, exist_ok=True)

    handler = type("BoundUploadHandler", (UploadHandler,), {"inbox": target})
    httpd = ThreadingHTTPServer((host, port), handler)
    # Otherwise a request still in flight keeps the process alive at shutdown, and on the Pi that
    # presents as `systemctl restart oddball` hanging for as long as a 60 MB upload takes.
    httpd.daemon_threads = True

    threading.Thread(target=httpd.serve_forever, name="upload", daemon=True).start()
    LOG.info("upload endpoint on http://%s:%d/upload -> %s", host, port, target)
    return httpd


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--inbox", default=None, help=f"where uploads land (default {INBOX_DIR})")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    httpd = serve(args.host, args.port, Path(args.inbox) if args.inbox else None)
    print(f"POST a file:  curl -F file=@thing.pdf http://{args.host}:{args.port}/upload")
    print("Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()
        print("\nstopped.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
