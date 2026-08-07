import os
from dotenv import load_dotenv

load_dotenv()

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://ollama.com")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:31b")

# FreeScout HTTP session credentials
FREESCOUT_URL          = os.getenv("FREESCOUT_URL", "http://localhost:8080")
FREESCOUT_ADMIN_EMAIL  = os.getenv("FREESCOUT_ADMIN_EMAIL", "admin@example.com")
FREESCOUT_ADMIN_PASS   = os.getenv("FREESCOUT_ADMIN_PASS", "Admin123!")
FREESCOUT_MAILBOX_ID   = int(os.getenv("FREESCOUT_MAILBOX_ID", "1"))

# Fixed category list — passed into the LLM prompt to keep labels consistent
CATEGORIES = [
    "billing",
    "technical",
    "account",
    "shipping",
    "general",
    "cancellation",
]

# Fixed priority list — passed into the LLM prompt and used for response validation
PRIORITIES = [
    "low",
    "normal",
    "high",
    "urgent",
]

# Confidence gate: tickets below this threshold are routed to "Needs review"
CONFIDENCE_THRESHOLD = 0.75

if not OLLAMA_API_KEY:
    raise ValueError("OLLAMA_API_KEY is missing in the .env file")
