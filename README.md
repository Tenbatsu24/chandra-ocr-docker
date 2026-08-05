# Chandra OCR Docker

Containerized deployment of [Chandra OCR 2](https://github.com/datalab-to/chandra) — an OCR engine that converts PDFs and images into structured Markdown, HTML, JSON, and extracted images — served over HTTP from a single GPU-backed Docker container.

This repo also includes a Python client and an example pipeline that automates running OCR over a Zotero library and writing results into an Obsidian vault.

## Repository layout

```
ocr_docker/        Docker image + FastAPI server wrapping the `chandra` CLI
ocr_client/        Python client library + CLI for querying the server
example/           Zotero → OCR → Obsidian automation scripts
.env.example       Global env template (used by all subprojects)
Makefile           `make clean` to remove Python cache/build artifacts
pyproject.toml     Ruff configuration (black-compatible formatting)
```

## Quick start

### 1. Build and run the server

See [ocr_docker/README.md](ocr_docker/README.md) for full instructions.

```bash
cd ocr_docker
cp ../.env.example .env       # adjust GPU_MEMORY_UTILIZATION, HF_TOKEN, etc.
docker compose up --build -d
docker compose logs -f        # watch model download + vLLM startup
```

### 2. Query the server

```bash
python ocr_client/client.py invoice.pdf
python ocr_client/client.py invoice.pdf --format markdown
```

### 3. Run the Zotero → Obsidian pipeline

See [example/README.md](example/README.md) for full instructions.

```bash
cd example
cp .env.example .env          # set ZOTERO_DIR, RAW_LITERATURE_DIR, ZOTERO_SYNC_FILE, etc.
python zotero_sync.py         # OCR all listed papers
python create_slugs.py        # generate Obsidian note stubs
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Docker container (single GPU)                       │
│                                                      │
│  ┌──────────────────┐    ┌──────────────────────┐   │
│  │  vllm serve      │    │  server.py (FastAPI) │   │
│  │  chandra-ocr-2   │◄───│  POST /v1/process    │   │
│  │  :8000           │    │  GET  /health        │   │
│  └──────────────────┘    │  GET  /v1/info       │   │
│                          └──────────────────────┘   │
│                          docker-entrypoint.sh        │
│                          (boots both, waits for      │
│                           vLLM, exits on failure)    │
└─────────────────────────────────────────────────────┘
         ▲
         │ HTTP
┌────────┴──────────────────────────────────────────┐
│  ocr_client/client.py  (Python library + CLI)      │
│  example/zotero_sync.py  (Zotero → OCR)            │
│  example/create_slugs.py  (OCR → Obsidian notes)   │
└───────────────────────────────────────────────────┘
```

- **`ocr_docker/`** runs two processes inside one container: vLLM (inference backend) and a FastAPI wrapper. The entrypoint waits for vLLM to become healthy before starting the API and exits if either process dies, letting your orchestrator restart the container.
- **`ocr_client/`** is a standalone HTTP client that mirrors every server form field. It's used directly from the CLI and by `example/zotero_sync.py`.
- **`example/`** demonstrates a real-world workflow: `zotero_sync.py` reads a Zotero SQLite database, finds PDFs for citation keys in a sync file, runs each through the OCR server, and writes results to `Raw Literature/<citationKey>/` inside an Obsidian vault. `create_slugs.py` then generates note stubs from a BetterBibTeX export, keyed on the same citation keys.

## Configuration

Each subproject has its own `.env.example`. Copy it to `.env` in that directory and adjust as needed. All server behaviour is controlled via environment variables — see `ocr_docker/.env.example` for the full list.

## License note

Chandra's code is Apache-2.0, but the model weights use a modified OpenRAIL-M license (free for research/personal use and startups under $2 M funding/revenue; commercial self-hosting beyond that needs a license from datalab.to). Make sure your usage qualifies.
