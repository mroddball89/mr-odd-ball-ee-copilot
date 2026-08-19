import os

# Ensure your API key is set — from `.env`, which is gitignored. Loaded BEFORE the agent
# import, because ChatGoogleGenerativeAI reads GOOGLE_API_KEY from the environment.
from dotenv import load_dotenv

load_dotenv()
if "GOOGLE_API_KEY" not in os.environ:
    raise SystemExit("GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key.")

from agents.math_agent import run_math_agent

def test_sandbox():
    print("Initializing Math Agent Test...\n", flush=True)
    
    # Let's ask it a complex engineering math question
    test_query = "Calculate the resonant frequency of an LC circuit with a 10uH inductor and a 100nF capacitor. Print the result in kHz."
    print(f"User Query: {test_query}\n", flush=True)
    
    result = run_math_agent(test_query)
    print(result)

if __name__ == "__main__":
    test_sandbox()