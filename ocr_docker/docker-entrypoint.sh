#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Config (env vars, with sensible defaults for a single 24GB+ GPU)
# ---------------------------------------------------------------------------
MODEL_CHECKPOINT="${MODEL_CHECKPOINT:-datalab-to/chandra-ocr-2}"
VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-chandra}"
VLLM_PORT="${VLLM_PORT:-8000}"
APP_PORT="${APP_PORT:-8080}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-}"
VLLM_STARTUP_TIMEOUT="${VLLM_STARTUP_TIMEOUT:-900}"   # seconds; model download+load can take a while
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"

echo "[entrypoint] Model checkpoint : ${MODEL_CHECKPOINT}"
echo "[entrypoint] Served model name: ${VLLM_MODEL_NAME}"
echo "[entrypoint] vLLM port        : ${VLLM_PORT}"
echo "[entrypoint] Wrapper API port : ${APP_PORT}"

VLLM_ARGS=(
  serve "${MODEL_CHECKPOINT}"
  --served-model-name "${VLLM_MODEL_NAME}"
  --port "${VLLM_PORT}"
  --host 127.0.0.1
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}"
  --trust-remote-code
)
if [[ -n "${MAX_MODEL_LEN}" ]]; then
  VLLM_ARGS+=(--max-model-len "${MAX_MODEL_LEN}")
fi
# shellcheck disable=SC2206
if [[ -n "${VLLM_EXTRA_ARGS}" ]]; then
  EXTRA=(${VLLM_EXTRA_ARGS})
  VLLM_ARGS+=("${EXTRA[@]}")
fi

echo "[entrypoint] Launching: vllm ${VLLM_ARGS[*]}"
vllm "${VLLM_ARGS[@]}" &
VLLM_PID=$!

cleanup() {
  echo "[entrypoint] Caught signal, shutting down child processes..."
  kill -TERM "${VLLM_PID}" 2>/dev/null || true
  kill -TERM "${APP_PID:-}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup TERM INT

echo "[entrypoint] Waiting for vLLM server to become healthy (timeout ${VLLM_STARTUP_TIMEOUT}s)..."
elapsed=0
until curl -sf "http://127.0.0.1:${VLLM_PORT}/health" >/dev/null 2>&1; do
  if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
    echo "[entrypoint] ERROR: vLLM server process exited during startup. Aborting."
    exit 1
  fi
  if [[ "${elapsed}" -ge "${VLLM_STARTUP_TIMEOUT}" ]]; then
    echo "[entrypoint] ERROR: vLLM server did not become healthy within ${VLLM_STARTUP_TIMEOUT}s."
    kill -TERM "${VLLM_PID}" 2>/dev/null || true
    exit 1
  fi
  sleep 5
  elapsed=$((elapsed + 5))
done
echo "[entrypoint] vLLM server is healthy after ${elapsed}s."

export VLLM_API_BASE="http://127.0.0.1:${VLLM_PORT}/v1"
export VLLM_MODEL_NAME
export MODEL_CHECKPOINT

echo "[entrypoint] Starting API wrapper on 0.0.0.0:${APP_PORT}..."
python3 -m uvicorn server:app --host 0.0.0.0 --port "${APP_PORT}" &
APP_PID=$!

# Exit (and let orchestrators restart the container) if either process dies.
wait -n "${VLLM_PID}" "${APP_PID}"
EXIT_CODE=$?
echo "[entrypoint] A child process exited (code ${EXIT_CODE}). Shutting down."
cleanup
exit "${EXIT_CODE}"

