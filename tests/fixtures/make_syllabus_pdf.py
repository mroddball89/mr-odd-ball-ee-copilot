#!/usr/bin/env python3
"""
Module:  make_syllabus_pdf.py
Purpose: Build a small text-bearing PDF, so syllabus tests do not need a real document.
Author:  LB
Date:    2026-08-23

    python tests/fixtures/make_syllabus_pdf.py out.pdf

`tools/verify_syllabus.py` uses this. It exists because the alternative fixtures are both bad:
committing a real syllabus puts LB's course paperwork in a public repo, and `pypdf`'s
`add_blank_page` produces a page with **no text**, which is the one case the module under test
refuses before it does anything interesting.

Hand-written rather than pulled from `reportlab` or `fpdf`. A PDF with one Helvetica text object
is about forty lines and no dependency; adding a PDF-generation library to `requirements.txt` to
support a test fixture is a wheel on the Pi for something the Pi never runs.

The xref offsets are computed as the file is assembled — `pypdf` can often recover from a broken
table by scanning, and a fixture that relies on that is a fixture testing the recovery path.
"""

from __future__ import annotations

import sys
from pathlib import Path

# A syllabus with every field the extractor looks for, plus one it must NOT take (a due date —
# those come from Canvas, and a date copied out of a PDF goes stale and contradicts the feed).
DEFAULT_TEXT = """ECE 350 - Signals and Systems
Fall 2026, Morgan State University

Instructor: Dr. A. Rivera
Email: a.rivera@morgan.edu
Office: Schaefer Engineering 214

Office Hours: Tuesdays and Thursdays, 2:00 PM to 4:00 PM, or by appointment.

Grading Breakdown:
Homework 20 percent
Laboratory reports 15 percent
Midterm Examination 25 percent
Final Examination 30 percent
Participation 10 percent

Late Work Policy:
Late homework loses 10 percent per day, up to a maximum of three days.
After three days late work receives no credit. Extensions require documentation
and must be requested before the deadline, not after it.

Attendance: more than three unexcused absences lowers the final grade by one letter.

Required text: Oppenheim, Signals and Systems, second edition.

Homework 4 is due on October 14, 2026.
"""


def _escape(text: str) -> bytes:
    """PDF string escaping. Backslash, and both parentheses, are the delimiters here."""
    out = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return out.encode("latin-1", "replace")


def build(text: str = DEFAULT_TEXT) -> bytes:
    """One single-page PDF carrying `text`. Returns the file's bytes."""
    lines = text.strip().splitlines()
    # BT/ET wrap the text object; Td sets the origin, TL the leading, T* advances one line.
    body = [b"BT", b"/F1 9 Tf", b"36 756 Td", b"11 TL"]
    for line in lines:
        body.append(b"(" + _escape(line) + b") Tj")
        body.append(b"T*")
    body.append(b"ET")
    stream = b"\n".join(body)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"

    start = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(start).encode() + b"\n%%EOF\n")
    return bytes(out)


def write(path: Path, text: str = DEFAULT_TEXT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build(text))
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "syllabus.pdf")
    write(target)
    print(f"wrote {target} ({target.stat().st_size} bytes)")
