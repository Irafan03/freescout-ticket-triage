import os
from dotenv import load_dotenv

load_dotenv()

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://ollama.com")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:31b")

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

if not OLLAMA_API_KEY:
    raise ValueError("OLLAMA_API_KEY is missing in the .env file")
