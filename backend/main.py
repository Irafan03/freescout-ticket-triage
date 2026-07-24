import json
from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from groq import Groq

from config import GROQ_API_KEY, CATEGORIES


app = FastAPI(
    title="AI Ticket Triage Service",
    description="Service that classifies FreeScout tickets using AI",
    version="1.0"
)

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = f"""You are a support ticket classifier.

Categories allowed: {", ".join(CATEGORIES)}.

Priority rules (follow strictly):
- urgent: service is completely broken/inaccessible, security issue, or explicit words like "immediately", "urgent", "ASAP", "right now"
- high: significant frustration, money at stake (refund, double charge), or time-sensitive request
- normal: standard request, no explicit urgency signal, but action is needed
- low: general question, no action needed urgently, informational

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


@app.get("/")
def home():
    return {"message": "AI Ticket Triage Service is running"}


@app.post("/classify", response_model=ClassificationResult)
def classify_ticket(ticket: TicketInput):
    user_content = f"Subject: {ticket.subject}\nMessage: {ticket.message}"

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2
        )

        raw_text = response.choices[0].message.content.strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()

        data = json.loads(raw_text)

        return ClassificationResult(**data)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))