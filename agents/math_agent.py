import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from tools.math_sandbox import math_repl_tool
from tools.memory_manager import format_memory_for_llm

MATH_PROMPT_TEMPLATE = """
You are an expert Electrical Engineering Math & Physics Engine.
When asked to solve complex equations or physics problems, write and execute Python code using your REPL tool.
You MUST use the print(...) function to view results.

{chat_history}

EXAMPLE 1:
User: Calculate the cut-off frequency of an RC low-pass filter with R=10k ohms and C=1uF.
AI: I will write a Python script using f = 1 / (2 * pi * R * C).
[Action: AI triggers math_repl_tool]
Result: Cut-off frequency: 15.92 Hz
AI: The cut-off frequency is 15.92 Hz.

User Question: {question}
"""

def run_math_agent(query: str) -> str:
    llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.0)
    llm_with_tools = llm.bind_tools([math_repl_tool])
    
    # Retrieve saved history from SD card
    history = format_memory_for_llm()
    
    prompt_template = ChatPromptTemplate.from_template(MATH_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)
    
    response = llm_with_tools.invoke(prompt)
    
    if response.tool_calls:
        tool_call = response.tool_calls[0]
        result = math_repl_tool.invoke(tool_call["args"])
        return f"Python Sandbox Execution Result:\n{result}"
        
    return response.content