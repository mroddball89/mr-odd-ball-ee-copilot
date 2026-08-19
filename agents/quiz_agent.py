from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate

EVALUATION_PROMPT = """
You are an expert Engineering Tutor.
You asked the user the following question: {question}
The official correct answer is: {correct_answer}
The user replied with: {user_answer}

Your job is to grade the user's answer.
1. Start by clearly stating "Correct!" or "Incorrect."
2. Briefly explain why, referencing the official answer. 
3. Be encouraging, but strictly enforce engineering accuracy. Do not accept fundamentally wrong math or concepts.
"""

def evaluate_quiz_answer(question: str, correct_answer: str, user_answer: str) -> str:
    # 1. Initialize the LLM
    llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.1)
    
    # 2. Build the grading prompt
    prompt_template = ChatPromptTemplate.from_template(EVALUATION_PROMPT)
    prompt = prompt_template.format(
        question=question, 
        correct_answer=correct_answer, 
        user_answer=user_answer
    )
    
    # 3. Execute the evaluation
    response = llm.invoke(prompt)
    return response.content