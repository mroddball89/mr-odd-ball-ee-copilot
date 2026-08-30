from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from engine.models import AGENT_MODEL, LLM_MAX_RETRIES
from engine.llm_text import extract_text_content
from tools.memory_manager import format_memory_for_llm

EVALUATION_PROMPT = """
You are an expert Engineering Tutor.
{chat_history}
You asked the user the following question: {question}
The official correct answer is: {correct_answer}
The user replied with: {user_answer}

Your job is to grade the user's answer.
1. Start by clearly stating "Correct!" or "Incorrect."
2. Briefly explain why, referencing the official answer.
3. Be encouraging, but strictly enforce engineering accuracy. Do not accept fundamentally wrong math or concepts.
"""

def evaluate_quiz_answer(question: str, correct_answer: str, user_answer: str) -> str:
    """Grade one quiz answer.

    ## Why this agent takes {chat_history} when it needs no history

    It does not want the conversation log — a grader that can see the last forty turns can see
    the answer it is about to mark. It wants what `format_memory_for_llm` now carries in FRONT
    of that log: LB's standing corrections and the machine's state.

    `tools/verify_awareness.py` caught this agent as the one of the eight that did not call this
    function, which meant "always spell out the units" applied to every route except the one
    where LB is being marked on units. A rule with a hole in it is not a rule.
    """
    # 1. Initialize the LLM
    llm = ChatGoogleGenerativeAI(model=AGENT_MODEL, temperature=0.1, max_retries=LLM_MAX_RETRIES)

    # 2. Build the grading prompt
    prompt_template = ChatPromptTemplate.from_template(EVALUATION_PROMPT)
    prompt = prompt_template.format(
        chat_history=format_memory_for_llm(),
        question=question,
        correct_answer=correct_answer,
        user_answer=user_answer
    )

    # 3. Execute the evaluation
    response = llm.invoke(prompt)
    return extract_text_content(response.content)