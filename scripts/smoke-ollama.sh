#!/usr/bin/env bash
# Smoke test for the Ollama model service (P0.4).
#
# Verifies, against a RUNNING compose stack, that:
#   - the qwen3 model is present in `ollama list` inside the ollama container
#   - a one-line generation prompt streams multiple non-empty JSON chunks
#     from the /api/generate streaming endpoint
#
# Prerequisites (this script does NOT bring these up for you):
#   1. Bring up the ollama service:
#        docker compose -f docker-compose.yml -f docker-compose.copilot.yml up -d ollama
#      (run from docker/development-easy/, or pass full -f paths from repo root)
#   2. Provision the model into the persistent volume (one-time, needs egress):
#        bash scripts/pull-model.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose_dir="${script_dir}/../docker/development-easy"
compose_project="development-easy"

MODEL="${MODEL:-qwen3:4b-instruct-2507}"

cd "${compose_dir}"

compose() {
    docker compose -p "${compose_project}" -f docker-compose.yml -f docker-compose.copilot.yml "$@"
}

if ! compose ps --status running --services 2>/dev/null | grep -qx "ollama"; then
    echo "FAIL: ollama service is not running. Start it with:" >&2
    echo "  docker compose -f docker-compose.yml -f docker-compose.copilot.yml up -d ollama" >&2
    exit 1
fi

echo "Checking model '${MODEL}' is present..."
if ! model_list="$(compose exec -T ollama ollama list)"; then
    echo "FAIL: 'ollama list' failed inside the ollama container" >&2
    exit 1
fi

if ! grep -qF "${MODEL}" <<< "${model_list}"; then
    echo "FAIL: model '${MODEL}' not found in 'ollama list' output:" >&2
    echo "${model_list}" >&2
    echo "Provision it first with: bash scripts/pull-model.sh" >&2
    exit 1
fi
echo "Model present."

echo "Requesting a one-line generation and checking for streamed tokens..."
gen_output="$(compose exec -T ollama sh -c "curl -s http://localhost:11434/api/generate -d '{\"model\": \"${MODEL}\", \"prompt\": \"Reply with the single word: ok\"}'")"

if [[ -z "${gen_output}" ]]; then
    echo "FAIL: /api/generate returned empty output" >&2
    exit 1
fi

chunk_count="$(grep -c '"response"' <<< "${gen_output}" || true)"

if [[ "${chunk_count}" -lt 2 ]]; then
    echo "FAIL: expected multiple streamed JSON chunks (got ${chunk_count})" >&2
    echo "${gen_output}" >&2
    exit 1
fi

if ! grep -q '"done":true' <<< "${gen_output}"; then
    echo "FAIL: streamed response never reached a done:true chunk" >&2
    echo "${gen_output}" >&2
    exit 1
fi

echo "Streamed ${chunk_count} response chunks."
echo "OLLAMA SMOKE OK"
