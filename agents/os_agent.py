import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from tools.os_controller import execute_terminal_command
from engine.llm_text import extract_text_content
from engine.split import SPOKEN_INSTRUCTION
from tools.memory_manager import format_memory_for_llm

OS_PROMPT_TEMPLATE = """
You are an expert Linux System Administrator running on a Raspberry Pi.
You have access to the system terminal via the `execute_terminal_command` tool.

{chat_history}

EXAMPLE 1:
User: What is the current CPU temperature of the Pi?
AI: I will check the thermal zone file to get the CPU temperature.
[Action: AI triggers execute_terminal_command with args: command="cat /sys/class/thermal/thermal_zone0/temp"]
Result: Terminal Output: 45000
AI: The current CPU temperature of the Raspberry Pi is 45.0°C.

User Question: {question}
""" + SPOKEN_INSTRUCTION

def run_os_agent(query: str) -> str:
    llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.1)
    llm_with_tools = llm.bind_tools([execute_terminal_command])
    
    # Retrieve saved history from SD card
    history = format_memory_for_llm()
    
    prompt_template = ChatPromptTemplate.from_template(OS_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)
    
    response = llm_with_tools.invoke(prompt)
    
    if response.tool_calls:
        tool_call = response.tool_calls[0]
        tool_args = tool_call["args"]
        proposed_command = tool_args.get("command", "Unknown Command")
        
        print(f"\n⚠️ SECURITY CHECK: The AI wants to execute:")
        print(f"   > {proposed_command}")
        
        user_approval = input("   Allow execution? (y/n): ").strip().lower()
        if user_approval == 'y':
            result = execute_terminal_command.invoke(tool_args)
            return f"OS Execution Result:\n{result}"
        else:
            return "Action aborted by the user. No terminal commands were executed."

    return extract_text_content(response.content)