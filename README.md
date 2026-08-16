# AI Ticket Triage for FreeScout

Automatic classification of support tickets by **category**, **priority**, and **sentiment** using an LLM. Low-confidence tickets are routed to a dedicated *Needs Review* queue. A suggested reply draft is generated for every ticket based on the customer's actual message.

Built entirely on open-source tools — no paid modules required.

---

## Accuracy results

Evaluated on `tests/test_tickets.json` — 20 hand-labeled tickets.

| Field      | Correct | Total | Accuracy |
|------------|---------|-------|----------|
| Category   | 19      | 20    | **95 %** |
| Priority   | 20      | 20    | **100 %** |

Model: `gemma4:31b` · Confidence threshold: `0.75` · Sent to review: `0`

Target set in the brief: category ≥ 85 %. **Achieved.**

Full results: [`tests/eval_results.json`](tests/eval_results.json)

---

## How it works

```
Customer submits a ticket
          │
          ▼
FreeScout conversation created
          │
          ├─► PHP module fires webhook → POST /webhook  (instant)
          │
          └─► Background auto-poll every 30 s  (fallback, always running)
                        │
                        ▼
               LLM classifies ticket
               { category, priority, sentiment, confidence, reason }
                        │
                        ├── confidence >= 0.75 → ✔ Auto-triaged
                        │       └── writes tags + priority to FreeScout
                        │
                        └── confidence < 0.75  → ⚠ Needs Review
                                └── moves to Needs Review mailbox
```

Every processed ticket shows a triage panel in the FreeScout right sidebar:

- Category, Priority (colour-coded), Sentiment
- Confidence bar
- Reason
- Suggested Reply Draft (based on the customer's actual message, not the category)

No manual command needed — triage runs automatically as soon as a ticket arrives.

---

## Open-source API module

This project uses [mikeyperes/freescout-api-webhooks](https://github.com/mikeyperes/freescout-api-webhooks), a **free open-source** FreeScout module that provides:

- `GET /api/v1/conversations` — list conversations for polling
- `PUT /api/v1/conversations/{id}` — write back tags, priority, meta fields
- Outbound webhook on `convo.created` events
- Right-sidebar triage panel rendered from conversation meta

The module is cloned and mounted automatically by Docker — no manual installation, no purchase required.

---

## Prerequisites

- Docker Desktop
- Python 3.10+
- An [Ollama Cloud](https://ollama.com) API key (or any OpenAI-compatible LLM endpoint)

---

## Setup

### 1. Clone the repo

```bash
git clone <your-repo-url>
cd freescout-ticket-triage
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# LLM — Ollama Cloud (or any OpenAI-compatible endpoint)
OLLAMA_API_KEY=your_key_here
OLLAMA_BASE_URL=https://ollama.com
OLLAMA_MODEL=gemma4:31b

# FreeScout instance
FREESCOUT_URL=http://localhost:8080
FREESCOUT_ADMIN_EMAIL=admin@example.com
FREESCOUT_ADMIN_PASS=Admin123!
FREESCOUT_MAILBOX_ID=1

# API key — generated inside FreeScout after startup (see step 4)
FREESCOUT_API_KEY=

# Needs Review mailbox — set to a different mailbox ID to enable routing
# Leave equal to FREESCOUT_MAILBOX_ID for badge-only mode
FREESCOUT_NEEDS_REVIEW_MAILBOX_ID=1

# Webhook HMAC secret — leave blank for local dev
WEBHOOK_SECRET=

CONFIDENCE_THRESHOLD=0.75
POLL_INTERVAL_SECONDS=30
```

### 3. Start FreeScout + database

```bash
docker compose -f docker/docker-compose.yml up -d
```

Wait ~60 seconds for first boot. Open http://localhost:8080 and log in with `admin@example.com` / `Admin123!`.

Docker automatically:
- Clones `mikeyperes/freescout-api-webhooks` into the FreeScout container (`module-init`)
- Runs the DB migrations (`module-migrate`)

No manual module installation needed.

### 4. Generate a FreeScout API key

1. In FreeScout: **Manage → API & Webhooks → New Key**
2. Copy the key into `.env` as `FREESCOUT_API_KEY`

### 5. (Optional) Create a Needs Review mailbox

1. In FreeScout: **Manage → Mailboxes → New Mailbox** — name it "Needs Review"
2. Note the mailbox ID from the URL (e.g. `/mailbox/2/...`)
3. Set `FREESCOUT_NEEDS_REVIEW_MAILBOX_ID=2` in `.env`

### 6. Install Python dependencies and start the service

```bash
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

The service starts at http://localhost:8000 and immediately begins polling FreeScout every 30 seconds in the background. No manual trigger needed.

---

## Demo — send a test ticket

Open `support.html` in your browser. Fill in any subject and message, then click **Send**.

The ticket appears in FreeScout and is classified automatically within seconds. Reload the conversation — the triage panel appears in the right sidebar with category, priority, confidence, and a suggested reply draft.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| POST | `/classify` | Classify a ticket (no write-back to FreeScout) |
| POST | `/support` | Create a ticket via `support.html` |
| POST | `/webhook` | Receive FreeScout webhook (HMAC-verified) |
| POST | `/poll` | Manually trigger one poll cycle |
| GET | `/poll/status` | Total number of processed conversations |

Interactive docs: http://localhost:8000/docs

### Example — classify only

```bash
curl -X POST http://localhost:8000/classify \
  -H "Content-Type: application/json" \
  -d "{\"subject\": \"App crashes on login\", \"message\": \"Every time I log in the app crashes. I cannot access my account at all.\"}"
```

```json
{
  "category": "technical",
  "priority": "urgent",
  "sentiment": "frustrated",
  "confidence": 0.98,
  "reason": "Service completely inaccessible — matches urgent criteria.",
  "needs_review": false,
  "draft_reply": "Thank you for reporting this. I can see the app is crashing on login..."
}
```

---

## Running the evaluation

```bash
python evaluate.py
```

Results are printed to the terminal and saved to `tests/eval_results.json`.

To add more labeled test cases, append objects to `tests/test_tickets.json`:

```json
{
  "subject": "...",
  "message": "...",
  "expected_category": "billing",
  "expected_priority": "high"
}
```

Valid categories: `billing`, `technical`, `account`, `shipping`, `general`, `cancellation`  
Valid priorities: `low`, `normal`, `high`, `urgent`

---

## Project structure

```
freescout-ticket-triage/
├── backend/
│   ├── main.py           # FastAPI app — all endpoints + auto-poll background task
│   ├── freescout_api.py  # REST client — polling, write-back, needs-review routing
│   ├── config.py         # All settings loaded from .env
│   └── db.py             # MySQL idempotency store (INSERT IGNORE)
├── tests/
│   ├── test_tickets.json # 20 hand-labeled tickets
│   └── eval_results.json # Latest accuracy results
├── dataset/
│   └── tickets.csv       # Full labeled dataset
├── docker/
│   └── docker-compose.yml
├── evaluate.py           # Accuracy evaluation script
├── support.html          # Web form to create test tickets
├── .env.example
└── requirements.txt
```

---

## Needs Review routing

When the LLM confidence is below `CONFIDENCE_THRESHOLD` (default `0.75`):

1. The sidebar shows a **⚠ Needs Review** badge instead of **✔ AI Classified**.
2. The conversation is moved to the mailbox set in `FREESCOUT_NEEDS_REVIEW_MAILBOX_ID`.

If both mailbox IDs are equal (the default), the ticket stays in place — only the badge changes. Set a different mailbox ID to activate actual queue routing.

---

## Suggested Reply

Every triage result includes a suggested reply draft generated by the LLM. The reply is based on the **customer's actual message and subject** — not on the category label. The LLM is explicitly instructed to:

- Answer what the customer actually asked or reported
- Never invent facts not present in the message
- Never make unsupported promises (refunds, deadlines, team assignments)
- Match the language of the customer's message

---

## Idempotency

Every processed conversation ID is stored in a MySQL `triage_processed` table using `INSERT IGNORE`. If FreeScout retries a webhook or the poller runs again, the ticket is silently skipped — it is never classified twice.

---

## Architecture notes

The triage panel visible in the FreeScout sidebar is rendered entirely by the open-source `mikeyperes/freescout-api-webhooks` PHP module. The Python service writes classification results to `conversation.meta` via a single `PUT /api/v1/conversations/{id}` call. The PHP module reads those fields and renders the sidebar panel — no separate note or thread is posted.

The automatic poll runs as an `asyncio` background task inside the FastAPI process. It starts 10 seconds after service startup and repeats every `POLL_INTERVAL_SECONDS` (default 30). This means tickets are classified within 30 seconds of arriving in FreeScout, with no manual intervention.
