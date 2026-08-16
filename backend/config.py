import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM (Ollama-compatible OpenAI API) ───────────────────────────────────────
OLLAMA_API_KEY  = os.getenv("OLLAMA_API_KEY")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://ollama.com")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "gemma4:31b")

# ── FreeScout connection ──────────────────────────────────────────────────────
# Base URL of your FreeScout instance (no trailing slash)
FREESCOUT_URL         = os.getenv("FREESCOUT_URL", "http://localhost:8080")

# HTTP session credentials — used by /support to create tickets via the web UI
# (fallback path when no API key is available)
FREESCOUT_ADMIN_EMAIL = os.getenv("FREESCOUT_ADMIN_EMAIL", "admin@example.com")
FREESCOUT_ADMIN_PASS  = os.getenv("FREESCOUT_ADMIN_PASS", "Admin123!")
FREESCOUT_MAILBOX_ID  = int(os.getenv("FREESCOUT_MAILBOX_ID", "1"))

# API key from the free mikeyperes/freescout-api-webhooks module.
# Generate it at:  Manage → API & Webhooks → New Key
# Used for: GET /api/v1/conversations (polling) + PUT (write-back tags/priority)
FREESCOUT_API_KEY = os.getenv("FREESCOUT_API_KEY", "")

# Mailbox ID for the dedicated "Needs Review" queue.
# Low-confidence tickets (confidence < CONFIDENCE_THRESHOLD) are moved here.
# Set to the same as FREESCOUT_MAILBOX_ID to disable mailbox routing
# and rely only on the ⚠ badge in the note.
FREESCOUT_NEEDS_REVIEW_MAILBOX_ID = int(os.getenv("FREESCOUT_NEEDS_REVIEW_MAILBOX_ID", str(os.getenv("FREESCOUT_MAILBOX_ID", "1"))))

# Optional HMAC secret for incoming webhooks.
# If you configure an external tool (e.g. n8n, custom PHP hook) to POST to
# /webhook on this service, set this secret and the sender must include
# X-FreeScout-Signature: base64(HMAC-SHA1(raw_body, secret))
# Leave blank to disable signature verification (not recommended in production).
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# Polling interval in seconds used by the /poll endpoint.
# The background poller calls GET /api/v1/conversations?status=active&page=1
# and processes any conversation not yet seen.
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

# ── Classifier settings ───────────────────────────────────────────────────────
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
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))

# ── Validation ────────────────────────────────────────────────────────────────
if not OLLAMA_API_KEY:
    raise ValueError("OLLAMA_API_KEY is missing in the .env file")
