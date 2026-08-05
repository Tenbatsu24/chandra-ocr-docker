ARG BASE_IMAGE=vllm/vllm-openai:v0.23.0
FROM ${BASE_IMAGE}

WORKDIR /app

COPY requirements.txt .
RUN python3 -m pip install -r requirements.txt --break-system-packages

COPY server.py docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# --- Runtime configuration (override at `docker run` / compose time) ---
# HF_HOME points at your persistent, host-backed model cache so weights
# survive container rebuilds -- mount your host's /shared/huggingface/cache
# to this same path at run time (see docker-compose.yml).
# WORK_DIR is intentionally NOT mounted anywhere -- per-request job folders
# are ephemeral scratch space, cleaned up automatically after each response.
ENV MODEL_CHECKPOINT=datalab-to/chandra-ocr-2 \
    VLLM_MODEL_NAME=chandra \
    VLLM_PORT=8000 \
    APP_PORT=8080 \
    GPU_MEMORY_UTILIZATION=0.90 \
    WORK_DIR=/data/jobs \
    MAX_CONCURRENT_JOBS=2 \
    JOB_TIMEOUT_SECONDS=1800 \
    HF_HOME=/data/hf-cache

RUN mkdir -p /data/jobs /data/hf-cache

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=10s --start-period=600s --retries=3 \
    CMD curl -sf http://127.0.0.1:${APP_PORT}/health || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
