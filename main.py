import warnings
warnings.filterwarnings("ignore")

import os

# =========================================================
# 1. LOAD THE API KEY FIRST (BEFORE OTHER IMPORTS)
# =========================================================
# The key lives in `.env`, which is gitignored. Copy `.env.example` to `.env` and fill it in.
# It is loaded before the agent imports because ChatGoogleGenerativeAI reads GOOGLE_API_KEY
# from the environment at construction time, and router.py builds its chain at import.
from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("GOOGLE_API_KEY"):
    raise SystemExit(
        "GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key from "
        "https://aistudio.google.com/apikey"
    )

# =========================================================
# 2. IMPORT ROUTER, AGENTS, & TOOLS
# =========================================================
from router import router_agent, AgentRoute
from agents.firmware_agent import run_firmware_agent
from agents.hardware_agent import run_hardware_agent
from agents.math_agent import run_math_agent
from agents.os_agent import run_os_agent
from agents.web_agent import run_web_agent

# Import Memory Manager
from tools.memory_manager import add_message, check_for_backup_reminder

# Import Quiz Tools & Agent
from tools.quiz_manager import get_random_question
from agents.quiz_agent import evaluate_quiz_answer


def start_copilot():
    print("=" * 50)
    print("⚡ MR. ODD BALL - EE COPILOT ONLINE ⚡")
    print("=" * 50)
    print("Type 'exit' or 'quit' to close the assistant.\n")

    # State variables for Quiz Mode
    quiz_mode_active = False
    current_quiz_item = None

    while True:
        user_query = input("\n👤 You: ").strip()

        if user_query.lower() in ['exit', 'quit']:
            print("Shutting down... Goodbye!")
            break

        if not user_query:
            continue

        # ---------------------------------------------------------
        # MODE A: QUIZ MODE LOCK (Bypasses Router)
        # ---------------------------------------------------------
        if quiz_mode_active:
            if user_query.lower() == 'exit quiz':
                quiz_mode_active = False
                print("\n🧠 Copilot:\nExiting Quiz Mode. Returning to normal Copilot functions.\n" + "-" * 50)
                continue

            # Grade the user's answer
            print("🤖 Grading...", end=" ", flush=True)
            evaluation = evaluate_quiz_answer(
                question=current_quiz_item["question"],
                correct_answer=current_quiz_item["answer"],
                user_answer=user_query
            )
            print("[DONE]\n")
            print(f"🧠 Copilot:\n{evaluation}\n")

            # Load the next question automatically
            current_quiz_item = get_random_question()
            print(f"👉 Next Question: {current_quiz_item['question']}")
            print("(Type your answer, or type 'exit quiz' to stop)")
            print("-" * 50)
            continue

        # ---------------------------------------------------------
        # MODE B: NORMAL ROUTING MODE
        # ---------------------------------------------------------
        try:
            # 1. Log question to SD card memory
            add_message("user", user_query)

            # 2. Route the question
            print("🤖 Routing...", end=" ", flush=True)
            decision = router_agent(user_query)
            print(f"[{decision.destination.upper()}]\n")

            # 3. Execute target agent
            if decision.destination == AgentRoute.QUIZ:
                quiz_mode_active = True
                current_quiz_item = get_random_question()
                response = f"Entering Quiz Mode! Type 'exit quiz' at any time to stop.\n\n👉 First Question: {current_quiz_item['question']}"

            elif decision.destination == AgentRoute.FIRMWARE:
                response = run_firmware_agent(user_query)

            elif decision.destination == AgentRoute.HARDWARE:
                response = run_hardware_agent(user_query)

            elif decision.destination == AgentRoute.MATH:
                response = run_math_agent(user_query)

            elif decision.destination == AgentRoute.OS:
                response = run_os_agent(user_query)

            elif decision.destination == AgentRoute.WEB:    
                response = run_web_agent(user_query)

            else:
                response = "I am a specialized Engineering Copilot. How can I help you today?"

            # 4. Check 15-day backup timer
            if check_for_backup_reminder():
                response += "\n\n⚠️ SYSTEM ALARM: Master, your memory file is 15 days old! Please transfer `sd_card_memory.json` to your portable hard drive to preserve memory."

            # 5. Save assistant answer to SD card memory
            add_message("assistant", response)

            # 6. Display answer
            print(f"🧠 Copilot:\n{response}\n")
            print("-" * 50)

        except Exception as e:
            print(f"❌ An error occurred: {e}")


if __name__ == "__main__":
    start_copilot()