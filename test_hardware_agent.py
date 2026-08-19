import os
import sys

print("[1/4] Starting script...", flush=True)

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from tools.trace_calculator import calculate_ipc2221_trace_width

print("[2/4] Imports loaded successfully.", flush=True)

# Make sure your API key is present — from `.env`, which is gitignored.
from dotenv import load_dotenv

load_dotenv()
if "GOOGLE_API_KEY" not in os.environ:
    raise SystemExit("GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key.")

llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.1)
llm_with_tools = llm.bind_tools([calculate_ipc2221_trace_width])

print("[3/4] LLM initialized. Sending query to Google API...", flush=True)

test_query = "What trace width do I need for a 3 Amp motor trace on 1oz external copper with 10C rise?"
prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a PCB designer. Use calculate_ipc2221_trace_width for trace width questions."),
    ("user", "{input}")
])

chain = prompt | llm_with_tools
response = chain.invoke({"input": test_query})

print("[4/4] Received response from API!", flush=True)

if response.tool_calls:
    tool_call = response.tool_calls[0]
    print(f"\nTool Triggered: {tool_call['name']}")
    print(f"Arguments: {tool_call['args']}")
    result = calculate_ipc2221_trace_width.invoke(tool_call['args'])
    print(f"Result: {result}")
else:
    print(f"Text Response: {response.content}")