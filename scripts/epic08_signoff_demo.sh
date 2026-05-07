#!/usr/bin/env bash
# Epic 08 signoff: trace persistence, why-this-answer UI, NL knowledge ops,
# and trace-linked corrections (publish + moderation branches).
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

DEMO_ROOT="${ROOT_DIR}/.data/epic08_signoff"
rm -rf "${DEMO_ROOT}"
mkdir -p "${DEMO_ROOT}"

INCIDENT_DB="${DEMO_ROOT}/incidents.sqlite3"
HITL_DB="${DEMO_ROOT}/hitl.sqlite3"
RAG_DB="${DEMO_ROOT}/rag.sqlite3"
KNOWLEDGE_DB="${DEMO_ROOT}/knowledge.sqlite3"
TRACE_DB="${DEMO_ROOT}/traces.sqlite3"
NL_DB="${DEMO_ROOT}/nl.sqlite3"
BACKUP_DB="${DEMO_ROOT}/backups.sqlite3"

export INCIDENT_DB_PATH="${INCIDENT_DB}"
export HITL_TICKET_DB_PATH="${HITL_DB}"
export RAG_DB_PATH="${RAG_DB}"
export KNOWLEDGE_DB_PATH="${KNOWLEDGE_DB}"
export ANSWER_TRACE_DB_PATH="${TRACE_DB}"
export NL_OPS_DB_PATH="${NL_DB}"
export BACKUP_DB_PATH="${BACKUP_DB}"
export OPENROUTER_BASE_URL="http://127.0.0.1:18500"
export OPENROUTER_API_KEY="stub-key"
export OPENROUTER_STUB_RESPONSE="Use the reset link via the email link policy."
export TELEGRAM_BOT_TOKEN="stub-token"
export NL_OPS_ENABLED="true"
export NL_OPS_ADMIN_USER_IDS=""

python3.11 "${ROOT_DIR}/scripts/_lib_openrouter_stub.py" >/tmp/epic08-stub.log 2>&1 &
STUB_PID=$!
uvicorn services.api.app.main:app --port 8000 >/tmp/epic08-api.log 2>&1 &
API_PID=$!
uvicorn services.web_ui.app.main:app --port 8001 >/tmp/epic08-web.log 2>&1 &
WEB_PID=$!
trap 'kill "${API_PID}" "${WEB_PID}" "${STUB_PID}" >/dev/null 2>&1 || true' EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null --max-time 1 http://127.0.0.1:18500/ && break
  sleep 0.5
done
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null --max-time 1 http://127.0.0.1:8000/health/live && break
  sleep 0.5
done
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null --max-time 1 http://127.0.0.1:8001/health/live && break
  sleep 0.5
done

echo "== seed knowledge so retrieval grounds the trace =="
curl -s -X POST http://127.0.0.1:8000/rag/ingest \
  -H 'content-type: application/json' \
  -d '{"source_id":"kb","text":"reset password by following the email link policy"}' \
  >/dev/null

echo "== /suggest writes a trace =="
SUGGEST=$(curl -s -X POST http://127.0.0.1:8000/suggest \
  -H 'content-type: application/json' \
  -d '{"text":"reset password help","trace_id":"epic08-demo"}')
echo "${SUGGEST}" | python3 -m json.tool

echo "== fetch trace via API =="
TRACE=$(curl -s http://127.0.0.1:8000/answer-traces/epic08-demo)
python3 - "${TRACE}" <<'PY'
import json, sys
trace = json.loads(sys.argv[1])
if trace.get("guardrail_outcome") != "valid":
    raise SystemExit(f"Epic 08 demo failed: guardrail={trace.get('guardrail_outcome')}")
if not trace.get("retrieval"):
    raise SystemExit("Epic 08 demo failed: empty retrieval")
print({"trace_id": trace["trace_id"], "grounded": trace["grounded"]})
PY

echo "== fetch why-this-answer UI =="
DETAIL=$(curl -s http://127.0.0.1:8001/answer-traces/epic08-demo)
python3 - "${DETAIL}" <<'PY'
import sys
text = sys.argv[1]
for marker in ("Why this answer", "Sources", "Policy / guardrails", "Model routing"):
    if marker not in text:
        raise SystemExit(f"Epic 08 demo failed: '{marker}' missing from UI")
print({"ui_sections_ok": True})
PY

echo "== NL knowledge op (create) =="
PROPOSE=$(curl -s -X POST http://127.0.0.1:8000/knowledge/nl-ops \
  -H 'content-type: application/json' \
  -d '{"user_id":"u1","utterance":"add reset password requires the email link"}')
echo "${PROPOSE}" | python3 -m json.tool
SESSION_ID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['id'])" "${PROPOSE}")
TOKEN=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['confirm_token'])" "${PROPOSE}")

curl -s -X POST "http://127.0.0.1:8000/knowledge/nl-ops/${SESSION_ID}/confirm" \
  -H 'content-type: application/json' \
  -d "{\"confirm_token\":\"${TOKEN}\"}" | python3 -m json.tool

echo "== publish-branch trace correction =="
PUB=$(curl -s -X POST http://127.0.0.1:8000/answer-traces/epic08-demo/corrections \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"org","user_id":"u1","edited_text":"Updated reset password copy from trace.","branch":"publish"}')
python3 - "${PUB}" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
if body.get("status") != "published":
    raise SystemExit(f"Epic 08 demo failed: publish status={body.get('status')}")
print({"publish_source_id": body["source_id"]})
PY

echo "== moderation-branch trace correction =="
MOD=$(curl -s -X POST http://127.0.0.1:8000/answer-traces/epic08-demo/corrections \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"org","user_id":"u1","edited_text":"Pending review correction.","branch":"moderation"}')
python3 - "${MOD}" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
if body.get("status") != "pending_moderation":
    raise SystemExit(f"Epic 08 demo failed: moderation status={body.get('status')}")
if body.get("candidate_id") is None:
    raise SystemExit("Epic 08 demo failed: candidate_id missing")
print({"candidate_id": body["candidate_id"]})
PY

echo "== audit log surfaced =="
AUDIT=$(curl -s http://127.0.0.1:8000/answer-traces/epic08-demo/audit)
python3 - "${AUDIT}" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
op_types = {entry["op_type"] for entry in data["items"]}
required = {"correction_published", "correction_pending_moderation"}
if not required.issubset(op_types):
    raise SystemExit(f"Epic 08 demo failed: audit ops missing, got {op_types}")
print({"audit_ops": sorted(op_types)})
PY

echo "Epic 08 demo OK."
