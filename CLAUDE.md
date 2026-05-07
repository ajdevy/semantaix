# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Setup

Requires Python 3.11:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Copy `.env.example` to `.env` and fill in required secrets (OpenRouter API key, Telegram token, operator chat IDs).

## Commands

```bash
# Lint
ruff check .

# Test with coverage (100% required on platform_common/ and services/)
pytest --cov --cov-config=.coveragerc --cov-report=term-missing

# Run a single test file
pytest tests/test_foo.py -v

# Full stack (Docker)
docker compose up --build -d

# Full Epic signoff (CI parity + live demo)
bash scripts/run_all_epic_feature_signoffs.sh
```

CI runs `ruff check .` then `pytest` with coverage on every PR and push to main.

## Architecture

**Semantaix** is a Docker-first microservices platform with five FastAPI services behind an nginx reverse proxy:

| Service | Port | Role |
|---------|------|------|
| `api` | 8000 | Core business logic (all epics) |
| `web_ui` | 8001 | Admin shell UI |
| `bot_gateway` | 8002 | Telegram webhook ingress |
| `ingest_worker` | 8003 | Heartbeat placeholder |
| `scheduler` | 8004 | Heartbeat placeholder |

**Infrastructure:** nginx (port 80) routes `/api` → api, `/admin` → web_ui, `/telegram/webhook` → bot_gateway. Qdrant (port 6333) is the vector store. SQLite databases live in `.data/`.

### Key Data Stores (SQLite)

Each concern has its own DB file in `.data/`:
- `semantaix_story1.db` — Telegram message transcripts
- `semantaix_incidents.db` — Incidents + event timeline
- `semantaix_hitl.db` — HITL tickets + runtime config
- `semantaix_rag.db` — RAG chunks (SHA-256 dedup)
- `semantaix_knowledge.db` — Knowledge candidates + moderation queue

### Core API Flows (`services/api/`)

- **`/suggest`** — Builds RAG context from `rag.py`, calls OpenRouter (`openrouter_client.py`), runs guardrails (`guardrails.py`), creates HITL ticket if blocked
- **`/incidents/*`** — Dedup window (300 s default), status lifecycle, event timeline in `incidents.py`
- **`/hitl/tickets/*`** — Route/assign/reply workflow in `hitl.py`; runtime config (operator mappings) also stored here
- **`/knowledge/extract`** — Pulls transcript lines → moderation candidates (`knowledge_moderation.py`)
- **`/knowledge/candidates/*`** — Approve (triggers RAG reindex) or reject via `knowledge_moderation.py`
- **`/rag/ingest`** + **`/rag/retrieve`** — Line-split ingest with dedup; token-overlap retrieval in `rag.py`

### Shared Foundation (`platform_common/`)

- `settings.py` — Single `Settings` class (Pydantic, env-based) shared by all services
- `app_factory.py` — Creates FastAPI app with `/health/live`, `/ready`, `/startup` endpoints

### Bot Gateway (`services/bot_gateway/`)

Validates Telegram webhook payload, normalizes + persists messages to transcript DB, handles admin `/hitl_config @user chat_id` command to update runtime config via the api service.

### Guardrails (`services/api/app/guardrails.py`)

Scores suggestions: 0.2 (blocked — empty, too long, low confidence, policy violation) or 0.95 (valid). Blocked suggestions route to HITL.

## Code Conventions

- Line length: 100 characters (`ruff`, `pyproject.toml`)
- Python 3.11 type hints throughout
- Repository classes own all DB access; no raw SQL outside `*Repository` classes
- Test files mirror source structure under `tests/`; async tests use `pytest-asyncio`
- 100% coverage enforced — add tests for every new branch
