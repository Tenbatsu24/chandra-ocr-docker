"""
Chandra OCR HTTP wrapper.

The `chandra` pip package ships a CLI (`chandra <input> <output_dir> ...`) that
talks to a vLLM OpenAI-compatible server for inference. This module wraps that
CLI in a small FastAPI service so the whole thing can be queried like a normal
REST API:

    POST /v1/process   (multipart file upload + form options)  -> zip / md / html / json
    GET  /health        -> checks the underlying vLLM server is reachable
    GET  /v1/info        -> current defaults / config

Design notes:
- One request = one subprocess call to `chandra`, writing to an isolated
  per-job temp directory, so concurrent requests never collide.
- A semaphore caps how many `chandra` subprocesses run at once
  (MAX_CONCURRENT_JOBS). The vLLM server underneath handles its own
  batching/queueing across those concurrent callers.
- Job directories are deleted after the response has been sent
  (via Starlette's BackgroundTask attached to the response).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("chandra-server")

# --------------------------------------------------------------------------- #
# Configuration (all overridable via environment variables / docker-compose)
# --------------------------------------------------------------------------- #
VLLM_API_BASE = os.environ.get("VLLM_API_BASE", "http://127.0.0.1:8000/v1")
MODEL_CHECKPOINT = os.environ.get("MODEL_CHECKPOINT", "datalab-to/chandra-ocr-2")
VLLM_MODEL_NAME = os.environ.get("VLLM_MODEL_NAME", "chandra")
WORK_DIR = Path(os.environ.get("WORK_DIR", "/data/jobs"))
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))
CHANDRA_BIN = os.environ.get("CHANDRA_BIN", "chandra")
DEFAULT_METHOD = os.environ.get("CHANDRA_DEFAULT_METHOD", "vllm")
JOB_TIMEOUT_SECONDS = int(os.environ.get("JOB_TIMEOUT_SECONDS", "1800"))

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
RESPONSE_FORMATS = {"zip", "markdown", "html", "json"}

WORK_DIR.mkdir(parents=True, exist_ok=True)
job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

app = FastAPI(
    title="Chandra OCR Server",
    description="HTTP wrapper around the chandra OCR CLI (vLLM backend).",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _safe_filename(name: str) -> str:
    # Strip any path components a client might sneak in.
    return Path(name).name or "upload"


@app.get("/health")
async def health():
    """Checks both this wrapper and the underlying vLLM server."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{VLLM_API_BASE}/models")
            r.raise_for_status()
            models = r.json()
        return {"status": "ok", "vllm_backend": "up", "models": models}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"vLLM backend unavailable: {exc}"
        ) from exc


@app.get("/v1/info")
async def info():
    return {
        "model_checkpoint": MODEL_CHECKPOINT,
        "vllm_model_name": VLLM_MODEL_NAME,
        "vllm_api_base": VLLM_API_BASE,
        "default_method": DEFAULT_METHOD,
        "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
        "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
        "response_formats": sorted(RESPONSE_FORMATS),
    }


@app.post("/v1/process")
async def process_document(
    file: UploadFile = File(..., description="PDF or image file to OCR"),
    method: str = Form(
        DEFAULT_METHOD, description="Inference backend: vllm (default) or hf"
    ),
    response_format: str = Form("zip", description="zip | markdown | html | json"),
    page_range: Optional[str] = Form(None, description='e.g. "1-5,7,9-12" (PDF only)'),
    max_output_tokens: Optional[int] = Form(
        None, description="Max output tokens per page"
    ),
    max_workers: Optional[int] = Form(
        None, description="Parallel vLLM request workers"
    ),
    batch_size: Optional[int] = Form(
        None, description="Pages per batch (default: 28 for vllm)"
    ),
    include_images: bool = Form(True, description="Extract and return embedded images"),
    include_headers_footers: bool = Form(
        False, description="Keep page headers/footers in output"
    ),
):
    if response_format not in RESPONSE_FORMATS:
        raise HTTPException(
            400, f"response_format must be one of {sorted(RESPONSE_FORMATS)}"
        )

    filename = _safe_filename(file.filename or "upload")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported file extension '{suffix}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    job_id = uuid.uuid4().hex
    job_dir = WORK_DIR / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_path = input_dir / filename
    try:
        with open(input_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except Exception as exc:  # noqa: BLE001
        _cleanup(job_dir)
        raise HTTPException(500, f"Failed to save upload: {exc}") from exc

    cmd = [CHANDRA_BIN, str(input_path), str(output_dir), "--method", method]
    if page_range:
        cmd += ["--page-range", page_range]
    if max_output_tokens is not None:
        cmd += ["--max-output-tokens", str(max_output_tokens)]
    if max_workers is not None:
        cmd += ["--max-workers", str(max_workers)]
    if batch_size is not None:
        cmd += ["--batch-size", str(batch_size)]
    cmd += ["--include-images"] if include_images else ["--no-images"]
    cmd += (
        ["--include-headers-footers"]
        if include_headers_footers
        else ["--no-headers-footers"]
    )

    logger.info("job=%s cmd=%s", job_id, " ".join(cmd))

    async with job_semaphore:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=JOB_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            _cleanup(job_dir)
            raise HTTPException(
                504, f"Processing timed out after {JOB_TIMEOUT_SECONDS}s"
            )
        except FileNotFoundError:
            _cleanup(job_dir)
            raise HTTPException(
                500, f"'{CHANDRA_BIN}' executable not found in the container image."
            )

    if proc.returncode != 0:
        _cleanup(job_dir)
        err_tail = stderr.decode(errors="replace")[-3000:]
        raise HTTPException(500, f"chandra failed (exit {proc.returncode}): {err_tail}")

    stem = input_path.stem
    result_dir = output_dir / stem
    if not result_dir.exists():
        _cleanup(job_dir)
        raise HTTPException(
            500,
            f"chandra ran successfully but expected output dir '{result_dir}' was not found. "
            f"stdout: {stdout.decode(errors='replace')[-1000:]}",
        )

    if response_format == "zip":
        archive_base = str(job_dir / "result")
        shutil.make_archive(archive_base, "zip", root_dir=str(result_dir))
        archive_path = Path(archive_base + ".zip")
        return FileResponse(
            path=str(archive_path),
            media_type="application/zip",
            filename=f"{stem}_chandra.zip",
            background=BackgroundTask(_cleanup, job_dir),
        )

    if response_format == "markdown":
        md_path = result_dir / f"{stem}.md"
        content = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        return PlainTextResponse(
            content,
            media_type="text/markdown",
            background=BackgroundTask(_cleanup, job_dir),
        )

    if response_format == "html":
        html_path = result_dir / f"{stem}.html"
        content = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
        return PlainTextResponse(
            content,
            media_type="text/html",
            background=BackgroundTask(_cleanup, job_dir),
        )

    # response_format == "json"
    json_path = result_dir / f"{stem}_metadata.json"
    content = json_path.read_text(encoding="utf-8") if json_path.exists() else "{}"
    return JSONResponse(
        content=json.loads(content),
        background=BackgroundTask(_cleanup, job_dir),
    )
