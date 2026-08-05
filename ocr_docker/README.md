# Chandra OCR Server

Wraps [datalab-to/chandra](https://github.com/datalab-to/chandra) (OCR: PDFs/images → Markdown + HTML + JSON + extracted images) in a single Docker container for a remote box with **one 24 GB+ GPU**. Inside the container:

1. `vllm serve datalab-to/chandra-ocr-2 ...` runs the model as an OpenAI-compatible server on `127.0.0.1:8000` inside the same container.
2. `server.py` (FastAPI) wraps the `chandra` CLI and exposes it over HTTP. Each request runs `chandra <upload> <job_dir> ...` in an isolated temp directory, then returns the full output folder (Markdown + HTML + metadata JSON + extracted images) as a zip — or just the Markdown/HTML/JSON alone, if you'd rather not deal with a zip.
3. `docker-entrypoint.sh` starts both processes, waits for vLLM to report healthy before starting the API, and exits (so your orchestrator can restart the container) if either process dies.

A Python client (`../ocr_client/client.py`) is included for querying the endpoint with all available options.

## ⚠️ Model license

- **Model license**: Chandra's code is Apache-2.0, but the model weights use a modified OpenRAIL-M license (free for research/personal use and startups under $2 M funding/revenue; commercial self-hosting beyond that needs a license from datalab.to). Make sure your usage qualifies.

## Build & run

Model weights are cached on the **host**, not baked into the image, so they survive rebuilds. Create the directory once on the host:

```bash
mkdir -p /shared/huggingface/cache
```

Then:

```bash
cp ../.env.example .env        # adjust GPU_MEMORY_UTILIZATION, HF_TOKEN, etc.
docker compose up --build -d
docker compose logs -f          # watch model download + vLLM startup
```

Or without compose:

```bash
docker build -t chandra-ocr-server .
docker run -d --gpus all \
  -p 8080:8080 \
  -v /shared/huggingface/cache:/data/hf-cache \
  --env-file .env \
  --name chandra-ocr \
  chandra-ocr-server
```

First startup will download the model weights into `/shared/huggingface/cache` on the host and can take several minutes — subsequent rebuilds/restarts reuse that cache and skip the download. The container `HEALTHCHECK` / `/health` endpoint won't report healthy until vLLM has finished loading.

Per-request job scratch space (`WORK_DIR=/data/jobs`) lives only inside the container and is deleted after each response — no volume is mounted for it, by design.

Check it's alive:

```bash
curl http://<remote-host>:8080/health
curl http://<remote-host>:8080/v1/info
```

## API

### `POST /v1/process`

`multipart/form-data` with:

| Field                     | Type    | Default | Notes                                                        |
|---------------------------|---------|---------|----------------------------------------------------------------|
| `file`                    | file    | —       | PDF or image (`.pdf .png .jpg .jpeg .tif .tiff .bmp .webp`)   |
| `method`                  | string  | `vllm`  | `vllm` or `hf` (hf requires the `[hf]` extra + torch; not installed by default) |
| `response_format`         | string  | `zip`   | `zip` (full output folder), `markdown`, `html`, or `json`     |
| `page_range`              | string  | none    | e.g. `"1-5,7,9-12"` (PDF only)                                |
| `max_output_tokens`       | int     | none    | Max tokens per page                                           |
| `max_workers`             | int     | none    | Parallel vLLM request workers                                 |
| `batch_size`              | int     | none    | Pages per batch (chandra default: 28 for vllm)                |
| `include_images`          | bool    | `true`  | Extract embedded images                                       |
| `include_headers_footers` | bool    | `false` | Keep page headers/footers in output                            |

`response_format=zip` returns `application/zip` containing everything chandra writes for that file: `<name>.md`, `<name>.html`, `<name>_metadata.json`, and any extracted `image_N.png` files — i.e. the full folder, as requested.

```bash
curl -X POST http://<host>:8080/v1/process \
  -F "file=@invoice.pdf" \
  -F "response_format=zip" \
  -F "include_images=true" \
  -o result.zip
```

### `GET /health`

Pings the internal vLLM server's `/models` endpoint.

### `GET /v1/info`

Returns the active model/config defaults.

## Python client

```bash
pip install requests

# Full folder (Markdown + HTML + JSON + images), sensible defaults
python ../ocr_client/client.py invoice.pdf

# Just the Markdown text
python ../ocr_client/client.py invoice.pdf --format markdown

# Only pages 1–5, skip image extraction, custom server
python ../ocr_client/client.py scan.pdf --page-range 1-5 --no-images --server http://gpu-box:8080
```

```python
from ocr_client.client import ChandraClient

client = ChandraClient("http://gpu-box:8080")
result_dir = client.process("invoice.pdf", out_dir="./results")
# ./results/invoice/invoice.md
# ./results/invoice/invoice.html
# ./results/invoice/invoice_metadata.json
# ./results/invoice/image_0.png, ...
```

## Tuning for a single 24 GB GPU

- `GPU_MEMORY_UTILIZATION` (default `0.90`) — lower if you see OOMs or are sharing the GPU with anything else.
- `MAX_MODEL_LEN` — cap context length if you need more KV-cache headroom.
- `MAX_CONCURRENT_JOBS` (default `2`) — this only limits how many `chandra` CLI processes run concurrently on the CPU side (PDF rendering, zipping); vLLM queues/batches actual GPU inference across all of them regardless.
- `VLLM_EXTRA_ARGS` — pass any additional raw `vllm serve` flags, e.g. `--max-num-seqs 64`.

## Files

```
Dockerfile              # vLLM + FastAPI wrapper, single image
docker-entrypoint.sh     # boots vLLM, waits for health, then starts the API
server.py                # FastAPI wrapper around the `chandra` CLI
../ocr_client/client.py  # example Python client
requirements.txt         # pinned wrapper/inference deps
docker-compose.yml        # convenience compose file with GPU reservation
.env.example              # documented config options
```
