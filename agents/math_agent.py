import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from engine.models import AGENT_MODEL, LLM_MAX_RETRIES
from engine.llm_text import extract_text_content
from engine.split import SPOKEN_INSTRUCTION
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
""" + SPOKEN_INSTRUCTION

# Like the hardware agent's: the sandbox already produced the number, and this pass is only
# allowed to phrase it. "Do not recalculate" is not politeness — the whole reason the REPL
# exists is that the model's arithmetic is not trustworthy, and a summary that quietly redoes
# the sum throws away the guarantee the sandbox was bought for.
SUMMARY_PROMPT_TEMPLATE = """
A Python sandbox has already executed the calculation. Its printed output is below and is
correct. Do NOT recalculate anything and do NOT change any digits — quote the numbers exactly.

User asked: {question}
Sandbox printed: {result}

Answer the user's question in one or two short sentences, using those exact numbers, and name
the unit.
""" + SPOKEN_INSTRUCTION


def run_math_agent(query: str) -> str:
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.0, max_retries=LLM_MAX_RETRIES)
    llm_with_tools = llm.bind_tools([math_repl_tool])
    
    # Retrieve saved history from SD card
    history = format_memory_for_llm()
    
    prompt_template = ChatPromptTemplate.from_template(MATH_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)
    
    response = llm_with_tools.invoke(prompt)
    
    if response.tool_calls:
        tool_call = response.tool_calls[0]
        result = math_repl_tool.invoke(tool_call["args"])

        # The code that produced the number is worth showing — it is how LB checks the working,
        # and on camera it is the difference between "the assistant said 15.92" and a visible
        # derivation. It goes on a card as a fenced block; the sandbox output goes on another.
        code = tool_call["args"].get("query") or tool_call["args"].get("code") or ""
        summary = extract_text_content(
            llm.invoke(SUMMARY_PROMPT_TEMPLATE.format(question=query, result=result)).content)

        parts = [summary]
        if code.strip():
            parts.append(f"```python\n{code.strip()}\n```")
        parts.append(f"Python Sandbox Execution Result:\n{result}")
        return "\n\n".join(parts)

    return extract_text_content(response.content)