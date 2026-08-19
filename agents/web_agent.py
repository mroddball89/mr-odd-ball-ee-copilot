import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from tools.web_search import perform_web_search
from tools.memory_manager import format_memory_for_llm

WEB_PROMPT_TEMPLATE = """
You are an expert Research Assistant.
You have access to the internet via the `perform_web_search` tool.
When a user asks about current events, component pricing, or information outside your training data, you MUST use the tool to search the web.

{chat_history}

EXAMPLE 1:
User: What is the current price of a Raspberry Pi 5?
AI: I will check the web for the latest pricing.
[Action: AI triggers perform_web_search with args: query="current price Raspberry Pi 5"]
Result: Web Search Result: The 4GB model is $60 and the 8GB model is $80.
AI: Based on current web results, the Raspberry Pi 5 is priced around $60 for the 4GB model and $80 for the 8GB model.

User Question: {question}
"""

def extract_text_content(content) -> str:
    """Extracts clean text from LLM response objects, removing API metadata and signatures."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                text_parts.append(block["text"])
        return "\n".join(text_parts)
    return str(content)

def run_web_agent(query: str) -> str:
    llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.1)
    llm_with_tools = llm.bind_tools([perform_web_search])
    
    history = format_memory_for_llm()
    
    prompt_template = ChatPromptTemplate.from_template(WEB_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)
    
    response = llm_with_tools.invoke(prompt)
    
    if response.tool_calls:
        tool_call = response.tool_calls[0]
        tool_args = tool_call["args"]
        search_query = tool_args.get("query", "Unknown Query")
        
        # 🛑 HUMAN-IN-THE-LOOP CHECK 🛑
        print(f"\n⚠️  SECURITY CHECK: The AI wants to search the web for:")
        print(f"   > '{search_query}'")
        
        user_approval = input("   Allow search? (y/n): ").strip().lower()
        
        if user_approval == 'y':
            print("   Searching...", flush=True)
            result = perform_web_search.invoke(tool_args)
            
            # Feed search results back into LLM
            summary_prompt = f"The user asked: {query}\nThe web search returned: {result}\nSummarize the answer clearly in plain text."
            final_summary = llm.invoke(summary_prompt)
            
            # Cleanly extract text only
            return extract_text_content(final_summary.content)
        else:
            return "Action aborted by the user. No web search was performed."
            
    return extract_text_content(response.content)