#!/usr/bin/env bash
# Epic 11 signoff: calendar availability connect -> configure -> availability
# round-trip. Enables the default project's calendar, defines a service, and
# verifies the opt-in gate end-to-end through the live API:
#   * disabled project -> availability question flows to HITL (no calendar work)
#   * enabled-but-not-connected -> calendar OWNS the question and escalates
#     (routed to the calendar operator), never a fabricated answer or a 500
#   * settings reflect enablement + the configured service
# A full Google freeBusy round-trip needs live OAuth, so the offline signoff
# proves the gate + escalation legs (the connected leg is covered by
# tests/e2e/test_e2e_epic11_availability.py with Google mocked).
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

INCIDENT_DB="${ROOT_DIR}/.data/epic11_signoff_incidents.sqlite3"
HITL_DB="${ROOT_DIR}/.data/epic11_signoff_hitl.sqlite3"
RAG_DB="${ROOT_DIR}/.data/epic11_signoff_rag.sqlite3"
KNOWLEDGE_DB="${ROOT_DIR}/.data/epic11_signoff_knowledge.sqlite3"
TRACE_DB="${ROOT_DIR}/.data/epic11_signoff_traces.sqlite3"
NL_DB="${ROOT_DIR}/.data/epic11_signoff_nl.sqlite3"
BACKUP_DB="${ROOT_DIR}/.data/epic11_signoff_backups.sqlite3"
PROJECTS_DB="${ROOT_DIR}/.data/epic11_signoff_projects.sqlite3"
OPERATORS_DB="${ROOT_DIR}/.data/epic11_signoff_operators.sqlite3"
CALENDAR_DB="${ROOT_DIR}/.data/epic11_signoff_calendar.sqlite3"
rm -f "${INCIDENT_DB}" "${HITL_DB}" "${RAG_DB}" "${KNOWLEDGE_DB}" "${TRACE_DB}" \
  "${NL_DB}" "${BACKUP_DB}" "${PROJECTS_DB}" "${OPERATORS_DB}" "${CALENDAR_DB}"

export INCIDENT_DB_PATH="${INCIDENT_DB}"
export HITL_TICKET_DB_PATH="${HITL_DB}"
export RAG_DB_PATH="${RAG_DB}"
export KNOWLEDGE_DB_PATH="${KNOWLEDGE_DB}"
export ANSWER_TRACE_DB_PATH="${TRACE_DB}"
export NL_OPS_DB_PATH="${NL_DB}"
export BACKUP_DB_PATH="${BACKUP_DB}"
export PROJECTS_DB_PATH="${PROJECTS_DB}"
export OPERATORS_DB_PATH="${OPERATORS_DB}"
export CALENDAR_DB_PATH="${CALENDAR_DB}"
export OPENROUTER_BASE_URL="http://127.0.0.1:18511"
export OPENROUTER_API_KEY="stub-key"
export OPENROUTER_STUB_RESPONSE="I don't know."
export TELEGRAM_BOT_TOKEN="stub-token"
export TELEGRAM_ALERT_CHAT_ID=""
export HITL_PRIMARY_OPERATOR_USERNAME="@ops_demo"
export INTERNAL_SERVICE_TOKEN="epic11-internal-token"

AUTH_HEADER="Authorization: Bearer ${INTERNAL_SERVICE_TOKEN}"

python3.11 "${ROOT_DIR}/scripts/_lib_openrouter_stub.py" >/tmp/epic11-stub.log 2>&1 &
STUB_PID=$!
uvicorn services.api.app.main:app --port 8011 >/tmp/epic11-api.log 2>&1 &
API_PID=$!
trap 'kill "${API_PID}" "${STUB_PID}" >/dev/null 2>&1 || true' EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null --max-time 1 http://127.0.0.1:18511/ && break
  sleep 0.5
done
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null --max-time 1 http://127.0.0.1:8011/health/live && break
  sleep 0.5
done

PROJECT_ID=1
OPERATOR="@ops_demo"

echo "== availability question on DISABLED project -> HITL (no calendar) =="
DISABLED=$(curl -s -X POST http://127.0.0.1:8011/conversations/inbound \
  -H 'content-type: application/json' \
  -d '{"text":"можно записаться на маникюр в субботу в 15:00?","chat_id":4242,"trace_id":"epic11-disabled"}')
echo "${DISABLED}"
python3 - "${DISABLED}" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
if not body.get("escalated") or body.get("response_mode") != "human_only":
    raise SystemExit(f"Epic 11 demo failed: disabled project not HITL: {body}")
print({"disabled_escalated": True})
PY

echo "== enable calendar for project ${PROJECT_ID} (operator) =="
ENABLE=$(curl -s -X POST "http://127.0.0.1:8011/calendar/projects/${PROJECT_ID}/enable" \
  -H "${AUTH_HEADER}" -H 'content-type: application/json' \
  -d "{\"actor\":\"${OPERATOR}\",\"actor_role\":\"operator\"}")
echo "${ENABLE}"

echo "== define a service (маникюр, Saturdays) =="
curl -s -X POST "http://127.0.0.1:8011/calendar/projects/${PROJECT_ID}/services" \
  -H "${AUTH_HEADER}" -H 'content-type: application/json' \
  -d "{\"actor\":\"${OPERATOR}\",\"actor_role\":\"operator\",\"name\":\"маникюр\",\"duration_minutes\":60,\"service_days\":[\"sat\"],\"working_hours\":{\"sat\":[\"10:00\",\"19:00\"]}}" \
  | python3 -m json.tool

echo "== settings reflect enablement + service =="
SETTINGS=$(curl -s "http://127.0.0.1:8011/calendar/projects/${PROJECT_ID}/settings" -H "${AUTH_HEADER}")
echo "${SETTINGS}"
python3 - "${SETTINGS}" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
if not body.get("enabled"):
    raise SystemExit(f"Epic 11 demo failed: project not enabled: {body}")
if not any(r.get("name") == "маникюр" for r in body.get("service_rules", [])):
    raise SystemExit(f"Epic 11 demo failed: service not configured: {body}")
print({"enabled": True, "service": "маникюр"})
PY

echo "== availability question on ENABLED-but-not-connected -> escalate (no 500) =="
ENABLED=$(curl -s -X POST http://127.0.0.1:8011/conversations/inbound \
  -H 'content-type: application/json' \
  -d '{"text":"можно записаться на маникюр в субботу в 15:00?","chat_id":4243,"trace_id":"epic11-enabled"}')
echo "${ENABLED}"
python3 - "${ENABLED}" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
if not body.get("escalated") or body.get("response_mode") != "human_only":
    raise SystemExit(f"Epic 11 demo failed: enabled-not-connected not escalated: {body}")
if body.get("answer_text"):
    raise SystemExit(f"Epic 11 demo failed: fabricated availability answer: {body}")
print({"enabled_not_connected_escalated": True})
PY

echo "Epic 11 demo OK."
