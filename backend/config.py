import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

CATEGORIES = [
    "billing",
    "technical",
    "account",
    "shipping",
    "general",
    "cancellation"
]

PRIORITIES = [
    "low",
    "normal",
    "high",
    "urgent"
]

CONFIDENCE_THRESHOLD = 0.75

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing in the .env file")