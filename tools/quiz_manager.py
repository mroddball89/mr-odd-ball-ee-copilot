import json
import random
import os

QUIZ_FILE = "quiz_data.json"

def load_quiz_data():
    """Loads the Q&A data. Creates a default template if missing."""
    if not os.path.exists(QUIZ_FILE):
        default_data = [
            {"question": "What is the standard formula for Ohm's Law?", "answer": "V = I * R"},
            {"question": "What is the typical forward voltage drop of a standard Red LED?", "answer": "Around 1.8V to 2.0V"},
            {"question": "What does I2C stand for?", "answer": "Inter-Integrated Circuit"}
        ]
        with open(QUIZ_FILE, 'w') as f:
            json.dump(default_data, f, indent=4)
        return default_data
    
    with open(QUIZ_FILE, 'r') as f:
        return json.load(f)

def get_random_question():
    """Pulls a random question and answer pair from the dataset."""
    data = load_quiz_data()
    return random.choice(data)