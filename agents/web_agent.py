import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from tools.web_search import perform_web_search
from engine.models import AGENT_MODEL
from engine.llm_text import extract_text_content
from engine.response import Card, CardKind, Pending, Response
from engine.split import SPOKEN_INSTRUCTION, is_speakable, split
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
""" + SPOKEN_INSTRUCTION

# extract_text_content used to live here. It was the right idea in the wrong place — the
# hardware and math agents needed it too and did not have it, so their answers came back as
# stringified block lists with kilobytes of base64 signature inside. It now lives in
# engine/llm_text.py and is imported above, so there is one copy and the next agent added
# cannot forget it.


def propose_web_search(query: str) -> Response:
    """Work out what to search for, and ask. **Nothing is sent to the internet here.**

    The mirror of `agents/os_agent.py:propose_os_action`, and gated for a different reason:
    the OS gate protects the machine, this one protects the boundary. LB's rule is local-first
    with cloud opt-in per request, so leaving the Pi is a thing he agrees to each time.

    A search query, unlike a shell command, IS speakable — it is a phrase in English. So this
    one asks with the real text rather than a paraphrase, which is strictly better: there is
    no gap between what is approved and what is heard.
    """
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1)
    llm_with_tools = llm.bind_tools([perform_web_search])

    history = format_memory_for_llm()

    prompt_template = ChatPromptTemplate.from_template(WEB_PROMPT_TEMPLATE)
    prompt = prompt_template.format(chat_history=history, question=query)

    response = llm_with_tools.invoke(prompt)

    if response.tool_calls:
        tool_args = response.tool_calls[0]["args"]
        search_query = tool_args.get("query", "")
        spoken = f"I'd search the web for {search_query}. Want me to?"

        # A query long or symbol-dense enough to fail the filter falls back to pointing at the
        # card, rather than being read out badly.
        if is_speakable(spoken) is not None:
            spoken = "I'd have to look that one up online. It's on the screen. Want me to?"

        return Response(
            speech=spoken,
            cards=[Card(CardKind.MARKDOWN, "Would search for", f"`{search_query}`")],
            route="web",
            pending=Pending(kind="web", tool_args=tool_args, spoken=spoken, shown=search_query),
            raw=f"Proposed search:\n{search_query}")

    return split(extract_text_content(response.content), route="web")


def resume_web_search(pending: Pending) -> Response:
    """Run the approved search and summarise what came back."""
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1)
    result = perform_web_search.invoke(pending.tool_args)

    # Feed search results back into LLM. This is the path that actually produces the answer LB
    # hears, so it needs the SPOKEN contract too — without it the summary arrives with URLs in
    # it, and a URL read aloud is "h t t p s colon slash slash".
    summary_prompt = (
        f"The user asked to search for: {pending.shown}\n"
        f"The web search returned: {result}\n"
        "Summarize the answer clearly in plain text. Where a claim came from a particular "
        "source, name the source at the end under a 'Sources:' heading so it can be shown on "
        "screen rather than spoken."
    ) + SPOKEN_INSTRUCTION

    summary = extract_text_content(llm.invoke(summary_prompt).content)
    out = split(summary, route="web", fallback="I've put what I found on the screen.")

    # The raw results go on their own card. They are the evidence behind the summary, and a
    # summary nobody can check is just a claim.
    return Response(
        speech=out.speech,
        cards=list(out.cards) + [Card(CardKind.LOG, "Search results", str(result))],
        route="web",
        raw=summary)


def run_web_agent(query: str) -> str:
    """The old blocking entry point, kept for `main.py --text` and the existing tests.

    Deliberately NOT the path the voice loop takes — it reads stdin, which a spoken turn
    cannot answer.

    A thumbs up at the camera counts as the `y`, same as on the OS path — see
    `agents/os_agent.py:run_os_agent` for what that does and does not change. The gate here
    protects the boundary rather than the machine: LB's rule is local-first with cloud opt-in
    per request, so leaving the Pi is still a thing he agrees to each time, only now he can
    agree to it from across the room. Set `ODDBALL_GESTURE=0` to keep the camera shut.
    """
    proposed = propose_web_search(query)
    if proposed.pending is None:
        return proposed.raw or proposed.speech

    print("\n⚠️  SECURITY CHECK: The AI wants to search the web for:")
    print(f"   > '{proposed.pending.shown}'")
    print("   👍 thumbs up at the camera to approve, or answer below.")

    from tools.gesture_control import approve_by_gesture_or_keyboard

    if approve_by_gesture_or_keyboard("   Allow search? (y/n): "):
        print("   Searching...", flush=True)
        return resume_web_search(proposed.pending).raw
    return "Action aborted by the user. No web search was performed."