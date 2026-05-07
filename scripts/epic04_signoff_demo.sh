#!/usr/bin/env bash
# Epic 04 signoff: blocked /suggest creates HITL ticket, route assigns operator,
# resolve closes the ticket.
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

INCIDENT_DB="${ROOT_DIR}/.data/epic04_signoff_incidents.sqlite3"
HITL_DB="${ROOT_DIR}/.data/epic04_signoff_hitl.sqlite3"
RAG_DB="${ROOT_DIR}/.data/epic04_signoff_rag.sqlite3"
KNOWLEDGE_DB="${ROOT_DIR}/.data/epic04_signoff_knowledge.sqlite3"
TRACE_DB="${ROOT_DIR}/.data/epic04_signoff_traces.sqlite3"
NL_DB="${ROOT_DIR}/.data/epic04_signoff_nl.sqlite3"
BACKUP_DB="${ROOT_DIR}/.data/epic04_signoff_backups.sqlite3"
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
export HITL_PRIMARY_OPERATOR_USERNAME="@ops_demo"

python3.11 "${ROOT_DIR}/scripts/_lib_openrouter_stub.py" >/tmp/epic04-stub.log 2>&1 &
STUB_PID=$!
uvicorn services.api.app.main:app --port 8000 >/tmp/epic04-api.log 2>&1 &
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

echo "== suggest -> blocked + ticket =="
SUGGEST=$(curl -s -X POST http://127.0.0.1:8000/suggest \
  -H 'content-type: application/json' \
  -d '{"text":"Customer wants help with refund.","chat_id":42}')
echo "${SUGGEST}"
TICKET_ID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['hitl_ticket_id'])" "${SUGGEST}")

echo "== list tickets =="
curl -s http://127.0.0.1:8000/hitl/tickets | python3 -m json.tool

echo "== resolve ticket ${TICKET_ID} =="
RESOLVE=$(curl -s -X POST "http://127.0.0.1:8000/hitl/tickets/${TICKET_ID}/resolve")
python3 - "${RESOLVE}" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
if body["status"] != "resolved":
    raise SystemExit(f"Epic 04 demo failed: ticket status={body['status']}")
print({"resolved_id": body["id"]})
PY

echo "== verify operator assignment in original suggest =="
python3 - "${SUGGEST}" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
if body.get("hitl_operator_username") != "@ops_demo":
    raise SystemExit(
        f"Epic 04 demo failed: operator={body.get('hitl_operator_username')}"
    )
print({"operator": body["hitl_operator_username"]})
PY

echo "Epic 04 demo OK."
