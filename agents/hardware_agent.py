import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
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
"""

def run_hardware_agent(query: str) -> str:
    llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.1)
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
        return f"Tool Execution Result: {result}"
        
    return response.content