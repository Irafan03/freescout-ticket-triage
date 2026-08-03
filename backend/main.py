import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

from config import OLLAMA_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL, CATEGORIES, CONFIDENCE_THRESHOLD


app = FastAPI(
    title="AI Ticket Triage Service",
    description="Service that classifies FreeScout tickets using AI",
    version="1.0"
)

# Ollama Cloud uses an OpenAI-compatible API
client = OpenAI(
    api_key=OLLAMA_API_KEY,
    base_url=f"{OLLAMA_BASE_URL}/v1"
)

SYSTEM_PROMPT = f"""You are a support ticket classifier.

Categories allowed: {", ".join(CATEGORIES)}.

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

        # Confidence gate : si confidence < seuil, on envoie en révision humaine
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
