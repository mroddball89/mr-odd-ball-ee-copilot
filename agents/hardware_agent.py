import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from engine.models import AGENT_MODEL
from engine.llm_text import extract_text_content
from engine.split import SPOKEN_INSTRUCTION
from tools.trace_calculator import calculate_ipc2221_trace_width
from tools.memory_manager import format_memory_for_llm

HARDWARE_PROMPT_TEMPLATE = """
You are an expert PCB Layout Engineer and Hardware Designer.
You enforce strict geometric and electrical rules.

When asked about PCB trace widths, power loss, or current carrying capacity, 
you MUST use the `calculate_ipc2221_trace_width` tool. Do not guess or estimate.

{chat_history}

EXAMPLE 1:
User: I need to route a 5A power line on a 2oz internal layer. Max temp rise is 20C. What trace width do I need?
AI: I will calculate the exact trace width required using the IPC-2221 standard tool.
[Action: AI triggers calculate_ipc2221_trace_width with args: current_amps=5, temp_rise_c=20, thickness_oz=2.0, layer_type="internal"]
Result: Required Trace Width is 35.12 mils (0.892 mm).
AI: For a 5A current with a 20°C rise on 2oz internal copper, you must use a minimum trace width of 35.12 mils (0.892 mm).

User Question: {question}
""" + SPOKEN_INSTRUCTION

# The tool returns a complete, correct sentence — and one that cannot be said out loud, because
# it carries "°C" and a bracketed millimetre conversion. This second pass turns the measured
# numbers into speech WITHOUT recomputing them: the width is quoted from the tool, never from
# the model. That distinction is the whole point — D30 measured models stating first-year
# electronics relationships fluently and wrongly, so the model is allowed to phrase the answer
# and never to derive it.
SUMMARY_PROMPT_TEMPLATE = """
A PCB trace width calculation has already been performed with the IPC-2221 standard tool.
The numbers below are correct and measured. Do NOT recalculate them and do NOT round them
differently — quote them exactly as given.

User asked: {question}
Tool returned: {result}

Answer the user's question in one or two short sentences, using those exact numbers.
""" + SPOKEN_INSTRUCTION


def run_hardware_agent(query: str) -> str:
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1)
    tools = [calculate_ipc2221_trace_width]
    llm_with_tools = llm.bind_tools(tools)
    
    # Retrieve saved history from SD card
    history = format_memory_for_llm()
    
    prompt_template = ChatPromptTemplate.from_template(HARDWARE_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)
    
    response = llm_with_tools.invoke(prompt)
    
    if response.tool_calls:
        tool_call = response.tool_calls[0]
        result = calculate_ipc2221_trace_width.invoke(tool_call["args"])

        # Second pass: phrase the measured result. The raw tool string is appended verbatim
        # so engine/split.py can lift it onto a card — the number LB needs to write down is
        # then on screen as well as in the air, which matters because a trace width heard
        # once at 160 words per minute is gone.
        summary_prompt = SUMMARY_PROMPT_TEMPLATE.format(question=query, result=result)
        summary = extract_text_content(llm.invoke(summary_prompt).content)
        return f"{summary}\n\nTool Execution Result: {result}"

    return extract_text_content(response.content)