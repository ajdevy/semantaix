# Semantaix Bootstrap

Initial Docker-first skeleton for the Semantaix Option B architecture.

## Services

- `api`: FastAPI backend
- `web_ui`: FastAPI admin shell
- `bot_gateway`: Telegram webhook ingress placeholder
- `ingest_worker`: worker heartbeat service
- `scheduler`: scheduler heartbeat service
- `nginx`: reverse proxy (`/api`, `/admin`, `/telegram/webhook`)
- `qdrant`: vector store
- `postgres`: optional profile service (`--profile with-postgres`)

## Quick Start

1. Copy env template:
   - `cp .env.example .env`
2. Build and run:
   - `docker compose up --build -d`
3. Verify health:
   - `curl http://localhost/health/live`
   - `curl http://localhost/api/health/live`
   - `curl http://localhost/admin/health/live`

## Run Tests

- `python3 -m venv .venv && source .venv/bin/activate`
- `pip install -r requirements-dev.txt`
- `pytest`
