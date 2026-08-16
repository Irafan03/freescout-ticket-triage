"""
main.py — AI Ticket Triage Service

Endpoints:
  GET  /                 health check
  POST /classify         classify a ticket (manual / test)
  POST /support          create a FreeScout ticket via web session
  POST /webhook          receive FreeScout webhook events (HMAC-verified)
  POST /poll             manually trigger a polling cycle
  GET  /poll/status      show how many tickets have been processed
"""

import asyncio
import hashlib
import hmac
import json
import logging
import re
from base64 import b64decode
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from openai import OpenAI

try:
    from config import (
        OLLAMA_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL,
        CATEGORIES, PRIORITIES, CONFIDENCE_THRESHOLD,
        FREESCOUT_URL, FREESCOUT_ADMIN_EMAIL, FREESCOUT_ADMIN_PASS,
        FREESCOUT_MAILBOX_ID, FREESCOUT_NEEDS_REVIEW_MAILBOX_ID,
        WEBHOOK_SECRET, POLL_INTERVAL_SECONDS,
    )
    from freescout_api import list_conversations, apply_triage_result
except ModuleNotFoundError:
    from backend.config import (
        OLLAMA_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL,
        CATEGORIES, PRIORITIES, CONFIDENCE_THRESHOLD,
        FREESCOUT_URL, FREESCOUT_ADMIN_EMAIL, FREESCOUT_ADMIN_PASS,
        FREESCOUT_MAILBOX_ID, FREESCOUT_NEEDS_REVIEW_MAILBOX_ID,
        WEBHOOK_SECRET, POLL_INTERVAL_SECONDS,
    )
    from backend.freescout_api import list_conversations, apply_triage_result

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def _auto_poll_loop():
    """Poll FreeScout every POLL_INTERVAL_SECONDS automatically in the background."""
    await asyncio.sleep(10)  # wait 10s for service to be fully ready
    while True:
        try:
            _poll_once()
        except Exception as exc:
            logger.warning("Auto-poll error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_auto_poll_loop())
    logger.info("Auto-poll started — interval=%ds", POLL_INTERVAL_SECONDS)
    yield
    task.cancel()


app = FastAPI(
    title="AI Ticket Triage Service",
    description="Classifies FreeScout tickets by category, priority, and sentiment",
    version="3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ollama Cloud — OpenAI-compatible API
client = OpenAI(
    api_key=OLLAMA_API_KEY,
    base_url=f"{OLLAMA_BASE_URL}/v1",
)

SYSTEM_PROMPT = f"""You are a support ticket classifier.

Categories allowed: {", ".join(CATEGORIES)}.
Priorities allowed: {", ".join(PRIORITIES)}.

Priority rules (follow strictly):
- urgent: service is completely broken/inaccessible, security issue, or explicit words like "immediately", "urgent", "ASAP", "right now"
- high: significant frustration, money at stake (refund, double charge), time-sensitive request, recurring bug that blocks usage, or account deletion/closure request
- normal: standard request, no explicit urgency signal, but action is needed
- low: general question, invoice copy request, no action needed urgently, informational

Sentiment allowed: frustrated, neutral, positive, angry.

Respond ONLY with valid JSON, no markdown, no explanation, in this exact shape:
{{
  "category": "...",
  "priority": "...",
  "sentiment": "...",
  "confidence": 0.0,
  "reason": "..."
}}
"""

# ── Suggested Reply prompt (stretch goal) ────────────────────────────────────
# Generates a reply based on what the customer actually wrote.
# Classification fields are provided as supporting context only.

DRAFT_REPLY_PROMPT = """You are a professional customer support agent writing a first response to a customer ticket.

Rules:
- Read the customer's subject and message carefully.
- Answer what the customer actually asked or reported.
- Use the classification fields (category, priority, sentiment) only as supporting context.
- Never invent facts not present in the customer's message.
- Never assume a problem based only on the category label.
- Never make specific promises (refund amounts, deadlines, team assignments) unless the customer explicitly mentioned them.
- Be concise, professional, and empathetic.
- Write in the same language as the customer's message.
- Return ONLY the reply text. No analysis, no labels, no explanation."""


# ── Idempotency — MySQL-backed via db.py ─────────────────────────────────────
try:
    from db import is_processed, mark_processed, count_processed
except ModuleNotFoundError:
    from backend.db import is_processed, mark_processed, count_processed


# ── Pydantic models ───────────────────────────────────────────────────────────

class TicketInput(BaseModel):
    subject: str
    message: str


class ClassificationResult(BaseModel):
    category: str
    priority: str
    sentiment: str
    confidence: float
    reason: str
    needs_review: bool
    draft_reply: str | None = None


class SupportRequest(BaseModel):
    full_name: str
    email: EmailStr
    subject: str
    message: str


class SupportResponse(BaseModel):
    success: bool
    conversation_id: int
    message: str
    classification: dict | None = None


# ── Draft reply generator ─────────────────────────────────────────────────────

def _generate_draft_reply(subject: str, message: str, classification: dict) -> str:
    """
    Generate a suggested reply based on the customer's actual message.

    The customer's subject + message are the primary input.
    Classification fields are provided as supporting context only.
    Falls back to a neutral acknowledgement if the LLM call fails.
    """
    category  = classification.get("category", "general")
    priority  = classification.get("priority", "normal")
    sentiment = classification.get("sentiment", "neutral")

    user_content = (
        f"Customer subject: {subject}\n"
        f"Customer message: {message}\n\n"
        f"Supporting context (do not use as primary basis):\n"
        f"  Category: {category}\n"
        f"  Priority: {priority}\n"
        f"  Sentiment: {sentiment}"
    )

    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": DRAFT_REPLY_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("Draft reply generation failed: %s", exc)
        return "Thank you for contacting our support team. We have received your message and will get back to you as soon as possible."


# ── Core classifier ───────────────────────────────────────────────────────────

def _classify(subject: str, message: str) -> dict:
    """
    Call the LLM and return the structured classification dict.
    Returns a safe fallback (needs_review=True) if the model fails.
    """
    try:
        user_content = f"Subject: {subject}\nMessage: {message}"
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        if data.get("category") not in CATEGORIES:
            data["category"] = "general"
        if data.get("priority") not in PRIORITIES:
            data["priority"] = "normal"

        data["needs_review"] = data.get("confidence", 0.0) < CONFIDENCE_THRESHOLD

        # Generate draft reply based on the customer's actual message (stretch goal)
        data["draft_reply"] = _generate_draft_reply(subject, message, data)

        return data

    except Exception as exc:
        logger.warning("Classification failed: %s", exc)
        return {
            "category":    "general",
            "priority":    "normal",
            "sentiment":   "neutral",
            "confidence":  0.0,
            "reason":      "Classification unavailable",
            "needs_review": True,
            "draft_reply": "Thank you for contacting our support team. We have received your message and will get back to you as soon as possible.",
        }


# ── Webhook signature verification ───────────────────────────────────────────

def _verify_signature(raw_body: bytes, signature_header: str) -> None:
    """
    Verify X-FreeScout-Signature: base64(HMAC-SHA1(raw_body, secret)).
    Raises HTTP 401 if verification fails.
    Only enforced when WEBHOOK_SECRET is set.
    """
    if not WEBHOOK_SECRET:
        return  # disabled — OK for local dev

    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-FreeScout-Signature header")

    try:
        mac = hmac.new(
            WEBHOOK_SECRET.encode("utf-8"),
            raw_body,
            hashlib.sha1,
        )
        expected = mac.digest()
        received = b64decode(signature_header)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid signature format")

    if not hmac.compare_digest(expected, received):
        raise HTTPException(status_code=401, detail="Webhook signature mismatch")


# ── /webhook ─────────────────────────────────────────────────────────────────

@app.post("/webhook", status_code=200)
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_freescout_signature: str = Header(default=""),
):
    """
    Receive an inbound FreeScout webhook (convo.created or convo.updated).

    Flow:
      1. Verify HMAC signature (if WEBHOOK_SECRET is set)
      2. Deduplicate on conversation ID
      3. Classify in background so we return 200 immediately
      4. Write tags/priority back via the REST API
    """
    raw_body = await request.body()

    # 1. Signature check
    _verify_signature(raw_body, x_freescout_signature)

    # 2. Parse
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # The webhook payload shape varies; try common keys
    convo = payload.get("conversation") or payload
    conv_id = convo.get("id") or convo.get("conversation_id")

    if not conv_id:
        logger.warning("Webhook received but no conversation ID found: %s", payload)
        return {"status": "ignored", "reason": "no conversation id"}

    conv_id = int(conv_id)

    # 3. Idempotency via MySQL INSERT IGNORE
    if not mark_processed(conv_id):
        logger.info("Webhook duplicate — conversation %d already processed", conv_id)
        return {"status": "duplicate"}

    # 4. Classify + write-back in background
    subject = convo.get("subject", "")
    preview = convo.get("preview") or convo.get("body", "")
    background_tasks.add_task(_process_conversation, conv_id, subject, preview)

    return {"status": "accepted", "conversation_id": conv_id}


# ── /poll ─────────────────────────────────────────────────────────────────────

@app.post("/poll", status_code=200)
def trigger_poll(background_tasks: BackgroundTasks):
    """
    Manually trigger one polling cycle.

    Fetches the latest active conversations via the REST API and processes
    any that have not been seen yet.  Use this as a cron / scheduler target
    (e.g. every 30 s) when webhooks are not available.
    """
    background_tasks.add_task(_poll_once)
    return {"status": "polling started"}


@app.get("/poll/status")
def poll_status():
    """Return how many conversations have been classified total."""
    return {"processed_total": count_processed()}


def _poll_once() -> None:
    """Fetch active conversations and classify ones not yet processed."""
    logger.info("Poll cycle starting …")
    conversations = list_conversations(status="active", per_page=50)
    new_count = 0
    for convo in conversations:
        conv_id = convo.get("id")
        if not conv_id:
            continue
        conv_id = int(conv_id)
        # INSERT IGNORE is atomic — safe against concurrent polls
        if not mark_processed(conv_id):
            logger.info("Conversation %d already processed — skipping", conv_id)
            continue
        subject = convo.get("subject", "")
        preview = convo.get("preview") or convo.get("body", "")
        _process_conversation(conv_id, subject, preview)
        new_count += 1
    logger.info("Poll cycle done — %d new conversation(s) processed", new_count)


# ── Shared classify + write-back logic ───────────────────────────────────────

def _process_conversation(conv_id: int, subject: str, preview: str) -> None:
    """Classify one conversation and write the result back to FreeScout."""
    logger.info("Processing conversation %d …", conv_id)
    result = _classify(subject, preview)
    ok = apply_triage_result(conv_id, result)
    if ok:
        logger.info(
            "Conversation %d → category=%s priority=%s confidence=%.2f needs_review=%s",
            conv_id,
            result.get("category"),
            result.get("priority"),
            result.get("confidence", 0),
            result.get("needs_review"),
        )
    else:
        logger.warning("Write-back failed for conversation %d", conv_id)


# ── GET / ─────────────────────────────────────────────────────────────────────

@app.get("/")
def home():
    return {"message": "AI Ticket Triage Service is running", "version": "3.0"}


# ── POST /classify (manual / test) ───────────────────────────────────────────

@app.post("/classify", response_model=ClassificationResult)
def classify_ticket(ticket: TicketInput):
    """Classify a ticket without writing anything back to FreeScout."""
    data = _classify(ticket.subject, ticket.message)
    return ClassificationResult(**data)


# ── POST /support (create ticket via web session) ────────────────────────────

def _extract_csrf(html: str) -> str:
    """Extract the _token value from a FreeScout HTML page."""
    marker = 'name="_token" value="'
    idx = html.find(marker)
    if idx == -1:
        marker = 'name="csrf-token" content="'
        idx = html.find(marker)
        if idx == -1:
            raise ValueError("CSRF token not found in page HTML")
    start = idx + len(marker)
    end = html.find('"', start)
    return html[start:end]


def _extract_conv_id(ajax_result: dict, redirect: str) -> int:
    """
    Extract the conversation ID from the AJAX response.

    FreeScout returns either:
      { "status": "success", "redirect_url": "/conversation/123" }
    or
      { "status": "success", "conv_id": 123 }

    Try both, then fall back to parsing the redirect URL.
    """
    # Direct field (some versions)
    if ajax_result.get("conv_id"):
        return int(ajax_result["conv_id"])

    # Parse from redirect URL  e.g. /conversation/123  or  /mailbox/1/view/123
    if redirect:
        # Match last numeric segment in the URL path
        match = re.search(r"/(\d+)(?:[/?#]|$)", redirect)
        if match:
            return int(match.group(1))

    return 0


@app.post("/support", response_model=SupportResponse)
def create_support_ticket(request: SupportRequest):
    """
    1. Classifies the ticket with the LLM.
    2. Creates a FreeScout ticket via authenticated HTTP session.
    3. Writes back tags/priority/note via the REST API immediately.
    """
    base = FREESCOUT_URL.rstrip("/")

    # Classify first so we can write back after ticket creation
    result = _classify(request.subject, request.message)

    ticket_body = f"{request.full_name} wrote:\n\n{request.message}"

    with httpx.Client(base_url=base, follow_redirects=True, timeout=15) as session:

        # Step 1: GET login page → CSRF token
        try:
            login_page = session.get("/login")
            login_page.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Cannot reach FreeScout: {e}")

        csrf_token = _extract_csrf(login_page.text)

        # Step 2: POST login
        login_resp = session.post("/login", data={
            "_token":   csrf_token,
            "email":    FREESCOUT_ADMIN_EMAIL,
            "password": FREESCOUT_ADMIN_PASS,
        })
        if "/login" in str(login_resp.url):
            raise HTTPException(status_code=401, detail="FreeScout login failed")

        # Step 3: GET new-ticket page → fresh CSRF token
        new_ticket_page = session.get(f"/mailbox/{FREESCOUT_MAILBOX_ID}/new-ticket")
        new_ticket_page.raise_for_status()
        fresh_csrf = _extract_csrf(new_ticket_page.text)

        # Step 4: POST /conversation/ajax — create the ticket
        payload = {
            "_token":             fresh_csrf,
            "mailbox_id":         str(FREESCOUT_MAILBOX_ID),
            "is_note":            "",
            "is_phone":           "",
            "type":               "",
            "is_create":          "1",
            "to[]":               request.email,
            "subject":            request.subject,
            "body":               ticket_body,
            "status":             "1",
            "user_id":            "",
            "after_send":         "2",
            "after_send_default": "2",
            "action":             "send_reply",
        }
        ajax_resp = session.post("/conversation/ajax", data=payload)
        ajax_resp.raise_for_status()

        try:
            ajax_result = ajax_resp.json()
        except Exception:
            raise HTTPException(status_code=502, detail=f"Unexpected response: {ajax_resp.text[:200]}")

        if ajax_result.get("status") != "success":
            raise HTTPException(status_code=500, detail=f"FreeScout rejected: {ajax_result}")

        redirect = ajax_result.get("redirect_url", "")

        # Extract conv_id from direct field or redirect URL
        conv_id = _extract_conv_id(ajax_result, redirect)

        logger.info("Ticket created via /support — conversation_id=%d redirect=%s", conv_id, redirect)

    # Step 5: write-back via REST API immediately (not waiting for poll)
    if conv_id:
        # Mark as processed so poll doesn't double-process it
        mark_processed(conv_id)
        apply_triage_result(conv_id, result)
    else:
        logger.warning("/support: could not extract conv_id from ajax_result=%s", ajax_result)

    return SupportResponse(
        success=True,
        conversation_id=conv_id,
        message=f"Ticket created and classified (conversation #{conv_id}).",
        classification={
            "category":   result.get("category"),
            "priority":   result.get("priority"),
            "sentiment":  result.get("sentiment"),
            "confidence": result.get("confidence"),
            "needs_review": result.get("needs_review"),
        },
    )
