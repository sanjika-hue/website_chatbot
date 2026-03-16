# Tastebud

Intelligence layer for [hashteelab.com](https://hashteelab.com). Observes user behavior, generates LLM-powered insights, and serves personalized recommendations.

```
browse → track → analyze → personalize
```

## Setup

### Local (development)

```bash
pip install -r requirements.txt
python main.py
# → http://localhost:8000
```

Requires an OpenAI-compatible LLM endpoint. Use [LM Studio](https://lmstudio.ai) or [Ollama](https://ollama.com) locally.

### Docker (production)

```bash
cp .env.example .env    # edit as needed
docker compose up -d
```

This starts Tastebud + Ollama. Pull a model on first run:

```bash
docker compose exec ollama ollama pull qwen3:4b
```

For NVIDIA GPU passthrough, uncomment the `deploy` section in `docker-compose.yml`.

## Environment

All config is via env vars. See `.env.example`.

| Variable | Default | |
|----------|---------|--|
| `LLM_URL` | `localhost:1234/v1/chat/completions` | Any OpenAI-compatible endpoint |
| `LLM_MODEL` | `qwen/qwen3-4b-2507` | Model ID |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `DATA_DIR` | `./data` | SQLite + JSONL storage path |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port |
| `MIN_EVENTS_FOR_REC` | `3` | Events before first recommendation |
| `REC_COOLDOWN` | `120` | Seconds between recommendations |
| `INSIGHT_COOLDOWN` | `10` | Seconds between insight requests |

## API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/events` | Ingest batched tracking events |
| `GET` | `/events` | Query events by session, type, page |
| `GET` | `/events/sessions` | List all sessions with stats |
| `GET` | `/recommendation` | Get LLM recommendation for a session |
| `POST` | `/insight` | Generate insight from highlighted text |
| `POST` | `/for-you` | Generate personalized page content |
| `GET` | `/health` | Health check |

### `POST /events`

```json
{
  "events": [{
    "session_id": "uuid",
    "event": "page_view",
    "page": "/en/products/obi",
    "timestamp": 1700000000,
    "data": { "scrollDepth": 45, "timeOnPage": 12 }
  }]
}
```

Triggers background LLM analysis after `MIN_EVENTS_FOR_REC` events.

### `POST /insight`

```json
{
  "session_id": "uuid",
  "page": "/en/products/obi",
  "highlighted_text": "Detects anomalies via audio fingerprinting",
  "product_slug": "obi",
  "follow_up_question": "How early can it detect bearing wear?"
}
```

→ `{ "message": "...", "questions": [{ "text": "...", "id": "q1" }] }`

`follow_up_question` is optional. When present, bypasses cooldown.

### `POST /for-you`

```json
{
  "session_id": "uuid",
  "answers": {
    "industry": "manufacturing",
    "challenge": "downtime",
    "scale": "medium",
    "detail": "detect bearing wear in CNC machines"
  }
}
```

→ `{ "sections": [{ "type": "recommendation", "title": "...", "content": "...", "stat": "48h", "product": "obi", "link": "/products/obi" }] }`

Section types: `recommendation`, `use_case`, `cta`.

## Storage

| File | Format | Purpose |
|------|--------|---------|
| `data/events.db` | SQLite | Structured queries, indexed |
| `data/events.jsonl` | JSONL | Append-only raw log |

Tables: `events`, `recommendations`, `insights`. Data persists via Docker volume.

## Architecture

```
Website (Next.js)          Tastebud (FastAPI)          Ollama / LM Studio
─────────────────          ──────────────────          ──────────────────
tracker.ts ──POST /events──▶ SQLite + JSONL
                             │
                             ├─ background ──▶ LLM ──▶ recommendation
                             │
hover dwell ──POST /insight─▶ LLM ──▶ { message, questions }
                             │
/for-you ────POST /for-you──▶ browsing + questionnaire ──▶ LLM ──▶ { sections[] }
```

## Project structure

```
├── main.py           # Routes and app setup
├── config.py         # Env vars + defaults
├── db.py             # SQLite connection + schema
├── llm.py            # LLM client + formatters
├── models.py         # Pydantic request models
├── prompts.py        # System prompts
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Stack

FastAPI · SQLite · JSONL · httpx · Ollama

## License

Proprietary — Hashtee Engineering
