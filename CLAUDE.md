# Server — Tastebud (Hashtee Backend)

## Quick Start

```bash
# Local development
uv venv && uv pip install -r requirements.txt
cp .env.example .env   # then edit with your config
bash hooks/install.sh  # set up git hooks (one-time)
.venv/bin/python main.py

# Production (EC2)
bash scripts/setup.sh    # install deps, pull, sync
bash scripts/start.sh    # start in tmux session "tastebud"
bash scripts/restart.sh  # stop + setup + start
bash scripts/stop.sh     # kill server
```

## Git Hooks

Tracked in `hooks/`. Run `bash hooks/install.sh` after cloning to activate.

- **pre-commit:** Python syntax check on staged `.py` files

## Stack

- **Framework:** FastAPI with Uvicorn
- **Python:** 3.13 (managed by uv on EC2)
- **Database:** SQLite (structured) + JSONL (append-only events)
- **LLM:** OpenAI-compatible endpoint (RunPod/vLLM)
- **OCR:** docling + pymupdf
- **Dependency management:** uv (production), pip (local)

## Project Structure

```
main.py          # FastAPI app — all routes and endpoints
config.py        # Environment variable loading (python-dotenv)
db.py            # SQLite connection, schema init, queries
models.py        # Pydantic request/response models
llm.py           # LLM client (httpx) and response formatting
ocr.py           # Document-to-markdown conversion (docling abstraction)
prompts.py       # System prompts for LLM features
Dockerfile       # Python 3.12-slim container
docker-compose.yml
scripts/         # Deployment scripts (setup, start, stop, restart)
data/            # Runtime data (SQLite DB, JSONL logs, LLM config)
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/events` | Ingest tracking events |
| GET | `/events` | Query events |
| GET | `/events/sessions` | List sessions |
| GET | `/recommendation` | Get session recommendation |
| POST | `/insight` | Generate insight from text |
| POST | `/for-you` | Personalized content |
| POST | `/ocr` | Document to markdown (rate limited) |
| GET | `/health` | Health check |
| GET | `/admin` | Admin panel (password protected) |

## Key Conventions

### OCR Module (`ocr.py`)

- Abstracted behind `convert_document()` — swappable with VLM later
- Lazy-loads the DocumentConverter to avoid slow startup
- Single PDF page extraction via pymupdf before passing to docling
- Do NOT rename to docling-specific names

### Rate Limiting

- OCR: 5 conversions per IP (in-memory dict)
- `OCR_WHITELIST_IPS` env var bypasses limits (comma-separated)

### Configuration

All config in `config.py` via `os.getenv()` with `python-dotenv`:

```
LLM_URL, LLM_MODEL, LLM_API_KEY    # LLM endpoint
CORS_ORIGINS                         # Comma-separated allowed origins
ADMIN_PASSWORD                       # Admin panel auth
OCR_WHITELIST_IPS                    # Rate limit bypass
HOST, PORT                           # Server bind (default 0.0.0.0:8000)
DATA_DIR                             # Storage path (default ./data)
```

## Deployment

- **Host:** EC2 (Ubuntu), behind nginx reverse proxy
- **Domain:** `binbin.hashteelabs.com/api/*` → `localhost:8000`
- **Process manager:** tmux session named `tastebud`
- **nginx config:** `/etc/nginx/sites-enabled/custom-domains.conf`
  - `client_max_body_size 25m` for OCR uploads
  - CORS handled at nginx level for multiple origins
  - `proxy_read_timeout 300s` for slow OCR

## Do NOT

- Add type checkers, linters, or formatters without being asked
- Pin dependency versions in requirements.txt (intentional)
- Store secrets in code — use `.env`
- Rename ocr.py to anything docling-specific
