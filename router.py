import os
from enum import Enum
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate

# 1. Define the possible destinations
class AgentRoute(str, Enum):
    FIRMWARE = "firmware"
    HARDWARE = "hardware"
    MATH = "math"
    OS = "os"
    QUIZ = "quiz"
    WEB = "web"
    GENERAL = "general"

# 2. Define the strict JSON structure
class RouteDecision(BaseModel):
    destination: AgentRoute = Field(description="The specific agent to route the user's query to.")
    reasoning: str = Field(description="A brief 1-sentence explanation of why this route was chosen.")

ROUTER_PROMPT = """
You are the Master Orchestrator for an Electrical Engineering AI Copilot.
Your only job is to analyze the user's query and route it to the correct specialized agent.

Available Agents:
- FIRMWARE: For questions about C/C++, RTOS, microcontroller registers, and reading datasheets.
- HARDWARE: For physical PCB layout questions, trace widths, IPC-2221 calculations, and hardware constraints.
- MATH: For complex physics equations, filter frequencies, and raw mathematical calculations.
- WEB: For searching the internet for current events, news, latest pricing, or up-to-date information not found in local memory.
- GENERAL: For casual conversation or anything outside the scope of electrical engineering.

User Query: {question}
"""

# ==========================================
# 🚀 OPTIMIZATION: Pre-build the engine once!
# ==========================================
_llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.0)
_structured_llm = _llm.with_structured_output(RouteDecision)
_prompt = ChatPromptTemplate.from_template(ROUTER_PROMPT)
_router_chain = _prompt | _structured_llm

def router_agent(query: str) -> RouteDecision:
    # Now, when you ask a question, it just executes instantly
    # without having to rebuild the API connection and schema!
    decision = _router_chain.invoke({"question": query})
    return decision