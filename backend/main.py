import json

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from openai import OpenAI

from config import (
    OLLAMA_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL, CATEGORIES, PRIORITIES, CONFIDENCE_THRESHOLD,
    FREESCOUT_URL, FREESCOUT_ADMIN_EMAIL, FREESCOUT_ADMIN_PASS, FREESCOUT_MAILBOX_ID,
)


app = FastAPI(
    title="AI Ticket Triage Service",
    description="Service that classifies FreeScout tickets using AI",
    version="1.0"
)

# Allow support.html (opened as a local file) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ollama Cloud — OpenAI-compatible API
client = OpenAI(
    api_key=OLLAMA_API_KEY,
    base_url=f"{OLLAMA_BASE_URL}/v1"
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


# ── Pydantic models ─────────────────────────────────────────────────────────

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


class SupportRequest(BaseModel):
    full_name: str
    email: EmailStr
    subject: str
    message: str


class SupportResponse(BaseModel):
    success: bool
    conversation_id: int
    message: str


# ── Existing endpoints ───────────────────────────────────────────────────────

@app.get("/")
def home():
    return {"message": "AI Ticket Triage Service is running"}


@app.post("/classify", response_model=ClassificationResult)
def classify_ticket(ticket: TicketInput):
    user_content = f"Subject: {ticket.subject}\nMessage: {ticket.message}"

    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2
        )

        raw_text = response.choices[0].message.content.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()

        data = json.loads(raw_text)

        # Validate returned values against the fixed lists
        if data.get("category") not in CATEGORIES:
            raise HTTPException(status_code=502, detail=f"Model returned unknown category: {data.get('category')}")
        if data.get("priority") not in PRIORITIES:
            raise HTTPException(status_code=502, detail=f"Model returned unknown priority: {data.get('priority')}")

        needs_review = data.get("confidence", 0.0) < CONFIDENCE_THRESHOLD

        return ClassificationResult(
            category=data["category"],
            priority=data["priority"],
            sentiment=data["sentiment"],
            confidence=data["confidence"],
            reason=data["reason"],
            needs_review=needs_review
        )

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"Model returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /support — create a FreeScout ticket via HTTP session ───────────────
#
# Flow:
#   1. GET  /login           → extract CSRF token from the login page HTML
#   2. POST /login           → authenticate, get session cookie
#   3. GET  /conversation/new-ticket → extract fresh CSRF token
#   4. POST /conversation/ajax       → create the ticket (action=save_draft)
#
# FreeScout handles everything internally:
#   - folder assignment (Unassigned)
#   - counter updates
#   - conversation numbering
# ─────────────────────────────────────────────────────────────────────────────

def _extract_csrf(html: str) -> str:
    """Extract the _token value from a FreeScout HTML page."""
    # <input type="hidden" name="_token" value="...">
    marker = 'name="_token" value="'
    idx = html.find(marker)
    if idx == -1:
        # meta tag fallback: <meta name="csrf-token" content="...">
        marker = 'name="csrf-token" content="'
        idx = html.find(marker)
        if idx == -1:
            raise ValueError("CSRF token not found in page HTML")
    start = idx + len(marker)
    end = html.find('"', start)
    return html[start:end]


@app.post("/support", response_model=SupportResponse)
def create_support_ticket(request: SupportRequest):
    """
    Creates a FreeScout ticket by replaying the same HTTP requests
    the browser makes when an admin creates a new ticket manually.

    No API module or direct DB access required.
    """
    base = FREESCOUT_URL.rstrip("/")

    # httpx client that keeps cookies across requests (like a browser session)
    with httpx.Client(base_url=base, follow_redirects=True, timeout=15) as session:

        # ── Step 1: GET login page → CSRF token ────────────────────────
        try:
            login_page = session.get("/login")
            login_page.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Cannot reach FreeScout: {e}")

        try:
            csrf_token = _extract_csrf(login_page.text)
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e))

        # ── Step 2: POST login ──────────────────────────────────────────
        login_resp = session.post("/login", data={
            "_token": csrf_token,
            "email":  FREESCOUT_ADMIN_EMAIL,
            "password": FREESCOUT_ADMIN_PASS,
        })

        # FreeScout redirects to / on success
        if "/login" in str(login_resp.url):
            raise HTTPException(
                status_code=401,
                detail="FreeScout login failed — check FREESCOUT_ADMIN_EMAIL and FREESCOUT_ADMIN_PASS in .env"
            )

        # ── Step 3: GET new-ticket page → fresh CSRF token ─────────────
        new_ticket_page = session.get(f"/mailbox/{FREESCOUT_MAILBOX_ID}/new-ticket")
        new_ticket_page.raise_for_status()

        try:
            fresh_csrf = _extract_csrf(new_ticket_page.text)
        except ValueError as e:
            raise HTTPException(status_code=502, detail=f"Could not get CSRF for new ticket: {e}")

        # ── Step 4: POST /conversation/ajax — create the ticket ─────────
        # Payload mirrors the browser request for creating a new published ticket.
        # action=send_reply + is_create=1 → FreeScout creates & publishes the conversation
        # (save_draft + is_create=1 always sets state=DRAFT — visible only in Drafts)
        payload = {
            "_token":       fresh_csrf,
            "mailbox_id":   str(FREESCOUT_MAILBOX_ID),
            "is_note":      "",
            "is_phone":     "",
            "type":         "",
            "is_create":    "1",
            "to[]":         request.email,
            "subject":      request.subject,
            "body":         f"{request.full_name} wrote:\n\n{request.message}",
            "status":       "1",        # STATUS_ACTIVE = 1 → open ticket
            "user_id":      "",         # no assignee → goes to Unassigned
            "after_send":   "2",
            "after_send_default": "2",
            "action":       "send_reply",   # creates & publishes (not draft)
        }

        ajax_resp = session.post("/conversation/ajax", data=payload)
        ajax_resp.raise_for_status()

        # FreeScout returns JSON: {"status": "success", "redirect_url": "..."}
        try:
            result = ajax_resp.json()
        except Exception:
            raise HTTPException(
                status_code=502,
                detail=f"Unexpected response from FreeScout: {ajax_resp.text[:200]}"
            )

        if result.get("status") != "success":
            raise HTTPException(
                status_code=500,
                detail=f"FreeScout rejected the ticket: {result}"
            )

        # Extract conversation ID from redirect URL  e.g. /conversation/5
        redirect = result.get("redirect_url", "")
        conv_id = int(redirect.rstrip("/").split("/")[-1]) if redirect else 0

        return SupportResponse(
            success=True,
            conversation_id=conv_id,
            message=f"Ticket created successfully (conversation #{conv_id}).",
        )
