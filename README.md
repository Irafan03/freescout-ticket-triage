# AI Ticket Triage for FreeScout

Automatic classification of support tickets by **category**, **priority**, and **sentiment** using an LLM. Low-confidence tickets are routed to a dedicated *Needs Review* queue. A suggested reply draft is included in every triage note.

Built on top of [mikeyperes/freescout-api-webhooks](https://github.com/mikeyperes/freescout-api-webhooks) (free, open source).

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
Customer email
      │
      ▼
FreeScout conversation created
      │
      ├─► Webhook POST /webhook  (if configured)
      │
      └─► GET /poll every 30 s  (fallback — used by default)
                │
                ▼
        LLM classifies ticket
        { category, priority, sentiment, confidence, reason }
                │
                ├── confidence >= 0.75 → Auto-triaged  ✔
                │       └── POST note + update priority
                │
                └── confidence < 0.75  → Needs Review  ⚠
                        └── POST note + move to Needs Review mailbox
```

Every processed ticket gets a styled internal note visible in FreeScout:

- Category, priority, sentiment, confidence bar, reason
- "Auto-triaged" or "Needs Review" badge
- Suggested reply draft the agent can copy

---

## Prerequisites

- Docker Desktop
- Python 3.10+
- An [Ollama Cloud](https://ollama.com) API key (or any OpenAI-compatible LLM)

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

# FreeScout
FREESCOUT_URL=http://localhost:8080
FREESCOUT_ADMIN_EMAIL=admin@example.com
FREESCOUT_ADMIN_PASS=Admin123!
FREESCOUT_MAILBOX_ID=1

# API key — generated inside FreeScout after startup (see step 4)
FREESCOUT_API_KEY=

# Needs Review mailbox — set to a different mailbox ID to enable routing
# Leave equal to FREESCOUT_MAILBOX_ID to use badge-only mode
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

Wait ~60 seconds for the first boot. Open http://localhost:8080 and log in with `admin@example.com` / `Admin123!`.

The `module-init` container clones `mikeyperes/freescout-api-webhooks` automatically. The `module-migrate` container runs the DB migrations. No manual module installation needed.

### 4. Generate a FreeScout API key

1. In FreeScout: **Manage → API & Webhooks → New Key**
2. Copy the key and paste it into `.env` as `FREESCOUT_API_KEY`

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

The API is now available at http://localhost:8000.

---

## Demo — send a test ticket

Open `support.html` in your browser (double-click the file). Fill in:

- **Name:** imane
- **Email:** imane@test.com
- **Subject:** Charged twice for my subscription
- **Message:** Hello, I renewed my subscription yesterday, but my credit card was charged twice. Please refund the duplicate payment as soon as possible.

Click **Send**. The ticket appears in FreeScout.

Then trigger the poll (or wait 30 s for the auto-poll):

```bash
curl -X POST http://localhost:8000/poll
```

Reload the conversation in FreeScout — you will see the triage note:

```
🤖 AI Ticket Triage  ✔ Auto-triaged
Category   billing
Priority   HIGH
Sentiment  frustrated
Confidence ████████████ 100%
Reason     User reports a double charge and requests a refund.

✏ SUGGESTED REPLY DRAFT
Thank you for reaching out about a billing issue. We have received your
request and our billing team will review it within 1 business day...
```

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| POST | `/classify` | Classify a ticket (no write-back) |
| POST | `/support` | Create a ticket via `support.html` |
| POST | `/webhook` | Receive FreeScout webhook (HMAC-verified) |
| POST | `/poll` | Manually trigger a poll cycle |
| GET | `/poll/status` | Number of processed conversations |

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
  "draft_reply": "Thank you for reporting this technical issue..."
}
```

---

## Configuring webhooks (optional)

Webhooks let FreeScout push new tickets to the service instantly instead of waiting for the poll.

1. You need a public URL for the service (use [ngrok](https://ngrok.com) for local dev):
   ```bash
   ngrok http 8000
   # Copy the https://xxxx.ngrok.io URL
   ```
2. In FreeScout: **Manage → API & Webhooks → Webhooks → Add**
   - URL: `https://xxxx.ngrok.io/webhook`
   - Events: `convo.created`
   - Secret: any string — copy it to `WEBHOOK_SECRET` in `.env`
3. Restart the service.

The service verifies the `X-FreeScout-Signature` header (HMAC-SHA1, base64-encoded) on every incoming webhook.

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
│   ├── main.py           # FastAPI app — all endpoints
│   ├── freescout_api.py  # REST client — polling, note post, needs-review routing
│   ├── config.py         # All settings loaded from .env
│   └── db.py             # MySQL idempotency store
├── tests/
│   ├── test_tickets.json # 20 hand-labeled tickets
│   └── eval_results.json # Latest accuracy results
├── dataset/
│   └── tickets.csv       # Full labeled dataset
├── docker/
│   └── docker-compose.yml
├── evaluate.py           # Run accuracy evaluation
├── support.html          # Web form to create test tickets
├── .env.example
└── requirements.txt
```

---

## Needs Review routing

When the LLM confidence is below `CONFIDENCE_THRESHOLD` (default 0.75):

1. The triage note displays a **⚠ Needs Review** badge instead of **✔ Auto-triaged**.
2. The conversation is moved to the mailbox set in `FREESCOUT_NEEDS_REVIEW_MAILBOX_ID`.

If `FREESCOUT_NEEDS_REVIEW_MAILBOX_ID` equals `FREESCOUT_MAILBOX_ID` (the default), the ticket stays in the same mailbox — only the badge changes. Set a different mailbox ID to activate actual queue routing.

---

## Stretch goals implemented

- **Suggested reply drafts** — every triage note includes a category-specific draft reply the agent can copy directly into the reply box.

---

## Architecture notes

The service uses the open source `mikeyperes/freescout-api-webhooks` module instead of the paid official module. All required REST endpoints are available:

- `GET /api/v1/conversations` — polling
- `GET /api/v1/conversations/{id}` — fetch conversation + threads
- `PUT /api/v1/conversations/{id}` — update priority, mailbox
- `POST /api/v1/conversations/{id}/threads` — post internal note

The module is mounted automatically into the FreeScout Docker container via the `module-init` container in `docker-compose.yml`. No manual installation is needed.
