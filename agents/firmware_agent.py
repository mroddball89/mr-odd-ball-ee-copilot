import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from tools.memory_manager import format_memory_for_llm

FIRMWARE_PROMPT_TEMPLATE = """
You are an expert Embedded Systems & Firmware Engineer.
Always provide exact register names, bitmasks, or C/C++ code snippets when applicable.
If referencing a datasheet, be precise.

{chat_history}

EXAMPLE 1:
User: How do I configure GPIO 13 as an output on ESP32?
AI: To configure GPIO 13 as an output on the ESP32, you must set the corresponding bit in the GPIO_ENABLE_REG register.
Here is the C code snippet:
// Set GPIO 13 as output
REG_WRITE(GPIO_ENABLE_REG, BIT13);

User Question: {question}
"""

def run_firmware_agent(query: str) -> str:
    # 1. Initialize the LLM
    llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.1)
    
    # 2. Retrieve saved history from SD card (or local storage)
    history = format_memory_for_llm()
    
    # 3. Build the prompt with memory context and the user's new question
    prompt_template = ChatPromptTemplate.from_template(FIRMWARE_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)
    
    # 4. Execute the agent and return the text response
    response = llm.invoke(prompt)
    
    return response.content