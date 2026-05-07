#!/usr/bin/env bash
# Epic 03 signoff: guardrails block a low-confidence /suggest response,
# emit guardrail_invalid_suggestion incident.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
mkdir -p .data

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

INCIDENT_DB="${ROOT_DIR}/.data/epic03_signoff_incidents.sqlite3"
HITL_DB="${ROOT_DIR}/.data/epic03_signoff_hitl.sqlite3"
RAG_DB="${ROOT_DIR}/.data/epic03_signoff_rag.sqlite3"
KNOWLEDGE_DB="${ROOT_DIR}/.data/epic03_signoff_knowledge.sqlite3"
TRACE_DB="${ROOT_DIR}/.data/epic03_signoff_traces.sqlite3"
NL_DB="${ROOT_DIR}/.data/epic03_signoff_nl.sqlite3"
BACKUP_DB="${ROOT_DIR}/.data/epic03_signoff_backups.sqlite3"
rm -f "${INCIDENT_DB}" "${HITL_DB}" "${RAG_DB}" "${KNOWLEDGE_DB}" "${TRACE_DB}" "${NL_DB}" "${BACKUP_DB}"

export INCIDENT_DB_PATH="${INCIDENT_DB}"
export HITL_TICKET_DB_PATH="${HITL_DB}"
export RAG_DB_PATH="${RAG_DB}"
export KNOWLEDGE_DB_PATH="${KNOWLEDGE_DB}"
export ANSWER_TRACE_DB_PATH="${TRACE_DB}"
export NL_OPS_DB_PATH="${NL_DB}"
export BACKUP_DB_PATH="${BACKUP_DB}"
export OPENROUTER_BASE_URL="http://127.0.0.1:18500"
export OPENROUTER_API_KEY="stub-key"
export OPENROUTER_STUB_RESPONSE="I don't know."
export TELEGRAM_BOT_TOKEN="stub-token"
export TELEGRAM_ALERT_CHAT_ID=""

python3.11 "${ROOT_DIR}/scripts/_lib_openrouter_stub.py" >/tmp/epic03-stub.log 2>&1 &
STUB_PID=$!
uvicorn services.api.app.main:app --port 8000 >/tmp/epic03-api.log 2>&1 &
API_PID=$!
trap 'kill "${API_PID}" "${STUB_PID}" >/dev/null 2>&1 || true' EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null --max-time 1 http://127.0.0.1:18500/ && break
  sleep 0.5
done
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null --max-time 1 http://127.0.0.1:8000/health/live && break
  sleep 0.5
done

echo "== suggest with guardrail-blocking response =="
RESPONSE=$(curl -s -X POST http://127.0.0.1:8000/suggest \
  -H 'content-type: application/json' \
  -d '{"text":"How can I unlock my account?"}')
echo "${RESPONSE}"

python3 - "${RESPONSE}" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
if body.get("response_mode") != "blocked_invalid":
    raise SystemExit(f"Epic 03 demo failed: response_mode={body.get('response_mode')}")
if "low_confidence" not in body["guardrail_decision"]["reasons"]:
    raise SystemExit("Epic 03 demo failed: missing low_confidence reason")
if not body.get("delivery_blocked"):
    raise SystemExit("Epic 03 demo failed: delivery_blocked is false")
print({"reasons": body["guardrail_decision"]["reasons"]})
PY

echo "== verify guardrail incident =="
INCIDENTS=$(curl -s http://127.0.0.1:8000/incidents/guardrail_invalid_suggestion)
python3 - "${INCIDENTS}" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
if not data["items"]:
    raise SystemExit("Epic 03 demo failed: no guardrail_invalid_suggestion incident")
print({"incidents": len(data["items"])})
PY

echo "Epic 03 demo OK."
