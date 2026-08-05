# Chandra OCR Python Client

A thin Python wrapper around the Chandra OCR server's HTTP API. Provides both a library API (`ChandraClient`) and a CLI entry point.

## Installation

```bash
pip install requests
```

## Library usage

```python
from ocr_client.client import ChandraClient

client = ChandraClient("http://gpu-box:8080")
result_dir = client.process("invoice.pdf", out_dir="./results")
# ./results/invoice/invoice.md
# ./results/invoice/invoice.html
# ./results/invoice/invoice_metadata.json
# ./results/invoice/image_0.png, ...
```

## CLI usage

```bash
# Full folder (Markdown + HTML + JSON + images), sensible defaults
python client.py invoice.pdf

# Just the Markdown text
python client.py invoice.pdf --format markdown

# Only pages 1–5, skip image extraction, custom server
python client.py scan.pdf --page-range 1-5 --no-images --server http://gpu-box:8080
```

## Configuration

All options are available as CLI flags. The server URL can also be set via `.env`:

```bash
# Copy .env.example to .env and adjust
cp .env.example .env
```

| Env var         | Default                     | Description                         |
|-----------------|-----------------------------|-------------------------------------|
| `OCR_SERVER`    | `http://127.0.0.1:8080`    | Base URL of the Chandra OCR server  |

## Supported response formats

| Format   | Returns                                              |
|----------|------------------------------------------------------|
| `zip`    | Full output folder (default): `.md`, `.html`, `_metadata.json`, extracted images |
| `markdown` | Plain-text Markdown content                        |
| `html`   | Plain-text HTML content                              |
| `json`   | Metadata JSON object                                 |

## Supported file types

`.pdf`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.webp`
