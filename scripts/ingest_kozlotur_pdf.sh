#!/usr/bin/env bash
# One-shot live ingestion of the Kozlotur buggy-tour brochure into the running
# stack. Copies the PDF into the api container's operator_uploads volume via
# `docker cp`, then calls POST /knowledge/operator_upload through nginx so the
# normal extract → soft_wrap → knowledge_candidate → rag_chunks pipeline runs.
#
# Idempotent: a second run hits the binary_sha256 short-circuit
# (services/api/app/main.py:_perform_operator_upload) and returns
# deduplicated=true with inserted_chunks=0.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

SRC_PDF="${1:-${HOME}/Downloads/Презентация 26.pdf}"
API_CONTAINER="${API_CONTAINER:-semantaix-api-1}"
CONTAINER_DIR="/app/.data/operator_uploads"
CONTAINER_PATH="${CONTAINER_DIR}/kozlotur_brochure_26.pdf"
NGINX_BASE="${NGINX_BASE:-http://127.0.0.1}"

OPERATOR_USERNAME="${OPERATOR_USERNAME:-}"
if [[ -z "${OPERATOR_USERNAME}" && -f .env ]]; then
  OPERATOR_USERNAME="$(grep -E '^HITL_PRIMARY_OPERATOR_USERNAME=' .env | cut -d= -f2- || true)"
fi
OPERATOR_USERNAME="${OPERATOR_USERNAME:-@flexsentlabs}"

if [[ ! -f "${SRC_PDF}" ]]; then
  echo "PDF not found at: ${SRC_PDF}" >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "${API_CONTAINER}"; then
  echo "Container ${API_CONTAINER} is not running. Start the stack: docker compose up -d" >&2
  exit 1
fi

echo "== copying PDF into api container =="
# docker cp preserves host file mode (the PDF in ~/Downloads is mode 0600). The
# api container's non-root user can't chmod a file it doesn't own, so we stage
# a world-readable copy in a temp dir first and copy from there.
STAGE_DIR=$(mktemp -d)
trap 'rm -rf "${STAGE_DIR}"' EXIT
cp "${SRC_PDF}" "${STAGE_DIR}/kozlotur_brochure_26.pdf"
chmod 0644 "${STAGE_DIR}/kozlotur_brochure_26.pdf"
docker cp "${STAGE_DIR}/kozlotur_brochure_26.pdf" "${API_CONTAINER}:${CONTAINER_PATH}"
docker exec "${API_CONTAINER}" ls -la "${CONTAINER_PATH}"

echo "== POST /knowledge/operator_upload =="
PAYLOAD=$(python3 - <<PY
import json
print(json.dumps({
    "operator_username": "${OPERATOR_USERNAME}",
    "source_file_type": "pdf",
    "source_file_name": "kozlotur_brochure_26.pdf",
    "stored_binary_path": "${CONTAINER_PATH}",
    "is_confidential": False,
}, ensure_ascii=False))
PY
)
UPLOAD=$(curl -sS -X POST "${NGINX_BASE}/api/knowledge/operator_upload" \
  -H 'content-type: application/json' \
  --data "${PAYLOAD}")
echo "${UPLOAD}" | python3 -m json.tool

python3 - "${UPLOAD}" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
required = {"candidate_id", "source_id", "inserted_chunks", "deduplicated"}
missing = required - set(data)
if missing:
    raise SystemExit(f"upload response missing keys {missing}: {data}")
if data["deduplicated"]:
    print(f"[ok] already ingested (sha256 dedup); source_id={data['source_id']}")
else:
    if data["inserted_chunks"] <= 0:
        raise SystemExit(f"first ingest returned inserted_chunks={data['inserted_chunks']}")
    print(f"[ok] ingested {data['inserted_chunks']} chunks as {data['source_id']}")
PY

echo "== POST /rag/retrieve =="
RETRIEVE=$(curl -sS -X POST "${NGINX_BASE}/api/rag/retrieve" \
  -H 'content-type: application/json' \
  --data '{"query":"хочу поехать на багги тур","limit":3}')
echo "${RETRIEVE}" | python3 -m json.tool

python3 - "${RETRIEVE}" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
items = data.get("items") or []
if not items:
    raise SystemExit("rag retrieve returned no items — chunks not indexed")
top = items[0]
if not top["source_id"].startswith("knowledge_candidate:"):
    raise SystemExit(f"top item is not a knowledge_candidate: {top}")
if "багги" not in top["chunk_text"].lower():
    raise SystemExit(f"top chunk does not mention 'багги': {top['chunk_text'][:200]}")
print(f"[ok] top match score={top['score']:.3f} source={top['source_id']}")
PY

echo "Live ingest OK. The bot should now answer 'хочу поехать на багги тур'."
