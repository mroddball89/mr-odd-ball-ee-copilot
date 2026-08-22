# Syllabi go here

Course syllabi as PDFs, one file per course. Subdirectories are fine — the loader recurses.

This directory is deliberately **excluded from the datasheet collection**. Everything else
under `data/` is embedded into Chroma's `datasheets` collection and read by the FIRMWARE agent;
these files go into the `academic` collection and are read only by the ACADEMIC agent. A
semantic search ranks by similarity alone and cannot tell a course outline from a datasheet, so
one pool would let a syllabus chunk ground a firmware answer and be cited as a datasheet.

After adding or changing a syllabus, run both build steps:

```bash
python tools/vector_db.py           # re-embeds both collections
python tools/academic_calendar.py   # re-extracts academic_calendar.json
```

The second one costs **one Gemini call per syllabus file** — see `docs/DECISIONS.md` D3 on the
20-requests-per-model-per-day free tier. It is a build step for exactly that reason: the
deadline check that runs on every turn reads the resulting JSON and spends nothing.

The PDFs themselves and `academic_calendar.json` are gitignored — LB's coursework, not source.
