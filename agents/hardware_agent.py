from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from engine.models import AGENT_MODEL, LLM_MAX_RETRIES
from engine.llm_text import extract_text_content
from engine.split import SPOKEN_INSTRUCTION
from tools.trace_calculator import calculate_ipc2221_trace_width
from tools.kicad_parser import analyze_kicad_pcb, extract_kicad_bom
from tools.knowledge_vault import (VAULT_INSTRUCTION, VAULT_TOOLS, followup_prompt,
                                   run_vault_calls)
from tools.memory_manager import format_memory_for_llm

HARDWARE_PROMPT_TEMPLATE = """
You are an expert PCB Layout Engineer and Hardware Designer.
You enforce strict geometric and electrical rules.

When asked about PCB trace widths, power loss, or current carrying capacity,
you MUST use the `calculate_ipc2221_trace_width` tool. Do not guess or estimate.

You can also READ THE USER'S OWN KICAD FILES. `extract_kicad_bom` reads a schematic and
returns its bill of materials; `analyze_kicad_pcb` reads a board and returns its layer stack,
nets and footprints. Use them whenever the user asks what is on one of his designs — the parts,
the count of something, the layer count. Never answer such a question from memory, and never
describe a design you have not read.

Both accept a full file path OR just the project's name, so pass whatever the user gave you.
He is usually speaking rather than typing, so a name like "the amp board" is normal and correct
to pass straight through.

{chat_history}

EXAMPLE 1:
User: I need to route a 5A power line on a 2oz internal layer. Max temp rise is 20C. What trace width do I need?
AI: I will calculate the exact trace width required using the IPC-2221 standard tool.
[Action: AI triggers calculate_ipc2221_trace_width with args: current_amps=5, temp_rise_c=20, thickness_oz=2.0, layer_type="internal"]
Result: Required Trace Width is 35.12 mils (0.892 mm).
AI: For a 5A current with a 20°C rise on 2oz internal copper, you must use a minimum trace width of 35.12 mils (0.892 mm).

EXAMPLE 2:
User: Have I got any 10k resistors on the amp board schematic?
AI: I will read that schematic and pull its bill of materials.
[Action: AI triggers extract_kicad_bom with args: file_path="the amp board"]
Result: 3x 10k R_0805_2012Metric R1, R2, R4
AI: Yes — three of them, R1, R2 and R4, all 0805.

User Question: {question}
""" + VAULT_INSTRUCTION + SPOKEN_INSTRUCTION

# The tools return complete, correct answers — and ones that cannot be said out loud. The trace
# calculator carries "°C" and a bracketed millimetre conversion; the KiCad tools return a
# multi-line table of part numbers and footprint names. This second pass turns the measured
# result into speech WITHOUT recomputing it: numbers are quoted from the tool, never from the
# model. That distinction is the whole point — D30 measured models stating first-year
# electronics relationships fluently and wrongly, so the model is allowed to phrase the answer
# and never to derive it.
#
# The "do not add" clause is the same rule pointed at the KiCad path, and it is not theoretical:
# asked to summarise a BOM, a model will happily mention the decoupling capacitor it believes
# ought to be there. A part named in the answer that is not on the schematic is worse than no
# answer, because LB will go looking for it.
SUMMARY_PROMPT_TEMPLATE = """
A hardware tool has already run. The block below is its exact output — either measured with the
IPC-2221 standard or read directly out of the user's KiCad file. It is correct.

Do NOT recalculate anything and do NOT round differently — quote the numbers exactly as given.
Do NOT name any component, reference, footprint, layer or net that does not appear below, and
do not comment on what the design "should" also have.

User asked: {question}
Tool returned: {result}

Answer the user's question in one or two short sentences, using only what is above.
""" + SPOKEN_INSTRUCTION

# Every tool this agent can reach, addressed by the name the model actually emits.
#
# A dict rather than an if/elif chain, because the chain's failure mode is silent and ugly: a
# model that names a tool no branch matches leaves `result` unbound, and the agent dies with a
# NameError several lines later — reported to LB as a crash in the hardware agent rather than
# as "the model asked for a tool that does not exist". With one tool that could not happen.
# With three it can.
#
# The two vault tools are appended rather than listed: they are the SAME two objects the
# firmware and persona agents bind, imported from one place, so the three agents cannot end up
# writing to three different folders or describing the tool three different ways.
TOOLS = [calculate_ipc2221_trace_width, extract_kicad_bom, analyze_kicad_pcb] + VAULT_TOOLS
_BY_NAME = {t.name: t for t in TOOLS}

# How much of a tool result is shown to the summarising model. A 200-part BOM is thousands of
# tokens of footprint names, and the summary only needs the shape of it — the header line
# already carries the totals. The FULL result still goes to the card, so nothing is hidden from
# LB, only from the second prompt.
_SUMMARY_LINE_BUDGET = 60


def _for_summary(result: str) -> str:
    """The tool result, trimmed to something worth spending prompt on — and honest about it."""
    lines = result.splitlines()
    if len(lines) <= _SUMMARY_LINE_BUDGET:
        return result
    kept = lines[:_SUMMARY_LINE_BUDGET]
    hidden = len(lines) - _SUMMARY_LINE_BUDGET
    return "\n".join(kept) + (
        f"\n... ({hidden} more lines, not shown to you — do not guess at what is in them; "
        f"the totals on the first lines are complete)")


def run_hardware_agent(query: str) -> str:
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1, max_retries=LLM_MAX_RETRIES)
    llm_with_tools = llm.bind_tools(TOOLS)

    # Retrieve saved history from SD card
    history = format_memory_for_llm()

    prompt_template = ChatPromptTemplate.from_template(HARDWARE_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)

    response = llm_with_tools.invoke(prompt)

    if response.tool_calls:
        # Vault calls are taken first and separately. They are not measurements, so the
        # SUMMARY_PROMPT_TEMPLATE below — which is written entirely around "a hardware tool
        # measured this, quote it exactly" — is the wrong frame for them: it would order the
        # model to quote numbers out of a note that has none.
        vault_results = run_vault_calls(response.tool_calls)
        if vault_results:
            summary = extract_text_content(
                llm.invoke(followup_prompt(prompt, vault_results)).content)
            joined = "\n\n".join(text for _name, text in vault_results)
            return f"{summary}\n\nTool Execution Result: {joined}"

        tool_call = response.tool_calls[0]
        chosen = _BY_NAME.get(tool_call.get("name", ""))
        if chosen is None:
            # Not a crash, and not silence: say which tools exist. This is the branch the old
            # if/elif chain reached as a NameError.
            return (f"I tried to use a tool called {tool_call.get('name')!r}, which I do not "
                    f"have. I can calculate IPC-2221 trace widths, read a KiCad schematic's "
                    f"bill of materials, or summarise a KiCad board.\n"
                    f"SPOKEN: I reached for a tool I do not have, so I could not answer that.")

        # The tools themselves never raise — but binding the arguments can, when the model
        # invents a field or omits a required one, and that surfaces from LangChain rather
        # than from the tool.
        try:
            result = chosen.invoke(tool_call["args"])
        except Exception as exc:                                          # noqa: BLE001
            return (f"The {chosen.name} tool could not be run: {type(exc).__name__}: {exc}\n"
                    f"SPOKEN: I could not run that tool with the details I was given.")

        # Second pass: phrase the measured result. The raw tool string is appended verbatim
        # so engine/split.py can lift it onto a card — the number LB needs to write down is
        # then on screen as well as in the air, which matters because a trace width heard
        # once at 160 words per minute is gone, and a bill of materials cannot be heard at all.
        summary_prompt = SUMMARY_PROMPT_TEMPLATE.format(
            question=query, result=_for_summary(result))
        summary = extract_text_content(llm.invoke(summary_prompt).content)
        return f"{summary}\n\nTool Execution Result: {result}"

    return extract_text_content(response.content)
