#!/usr/bin/env bash
# Staged install, so a resolver backtrack is isolated to one group rather than stalling the
# whole requirements file with no output (pip -q gives you nothing while it backtracks).
#
# ## Why this file is in the repo
#
# It lived only on the Pi until 2026-08-21, while docs/DEPLOY.md instructed you to run it —
# so a deploy to a NEW box followed an instruction pointing at a file that did not exist. It
# is committed now, and the requirements audit below is the reason that mattered more than the
# missing file did.
#
# ## It must stay in step with requirements.txt
#
# The stages are hand-written rather than derived from requirements.txt, which is the point —
# grouping is what isolates a backtrack. The cost is that a package added to requirements.txt
# and not added here is installed **nowhere**, and nothing says so: the venv builds clean, the
# copilot starts, and the gap only appears as a spoken error on the one route that needed it.
#
# That had already happened twice by the time this was committed:
#
#   sympy    the MATH agent's REPL runs in THIS interpreter, so "available to the agent" and
#            "installed in the venv" are one statement. Missing, the Pi answers a derivative
#            question with "ModuleNotFoundError: no module named 'sympy'" — which is exactly
#            what it did on 2026-08-19, and what docs/DEPLOY.md describes.
#   kiutils  reads .kicad_sch and .kicad_pcb (D9). tools/kicad_parser.py wraps the import by
#            design so the HARDWARE agent still starts without it — meaning its absence is
#            silent, and every schematic question comes back as an install instruction.
#
# `python tools/verify_agents.py` is what catches both. Run it after this script, always.
#
# The RAG extras (requirements-rag.txt: torch, chromadb, embeddings) are deliberately NOT here.
# They are optional, they are gigabytes on aarch64, and the copilot is complete without them —
# see the header of that file.
cd "$HOME/mr-odd-ball" || exit 1
PIP="venv/bin/pip"

run() {
  local name="$1"; shift
  echo "=== STAGE $name START $(date +%H:%M:%S)"
  timeout 900 $PIP install --no-input "$@" > "/tmp/stage_$name.log" 2>&1
  local rc=$?
  echo "=== STAGE $name RC=$rc $(date +%H:%M:%S)"
  tail -2 "/tmp/stage_$name.log"
}

run llm    langchain-google-genai python-dotenv pydantic
run audio  sounddevice numpy websockets piper-tts faster-whisper 'onnxruntime>=1.10,<2' 'tqdm>=4,<5' 'scipy>=1,<2' 'scikit-learn>=1,<2' 'requests>=2,<3'
run wake   --no-deps 'openwakeword>=0.6.0'
run tools  langchain-community langchain-text-splitters langchain-experimental
run agents 'sympy>=1.13' 'kiutils>=1.4.8'
run search ddgs duckduckgo-search

echo "=== ALL STAGES DONE $(date +%H:%M:%S)"
echo
echo "Now run the harness — it is what catches a package that installed nowhere:"
echo "  venv/bin/python tools/verify_agents.py"
