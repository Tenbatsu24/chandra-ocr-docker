"""
Example client for the Chandra OCR server (server.py).

Library usage:

    from client import ChandraClient

    client = ChandraClient("http://gpu-box:8080")
    result_dir = client.process("invoice.pdf", out_dir="./results")
    # -> ./results/invoice/invoice.md, invoice.html, invoice_metadata.json, image_0.png, ...

CLI usage:

    python client.py invoice.pdf
    python client.py invoice.pdf --out ./results --format markdown
    python client.py scan.png --no-images --page-range 1-5 --server http://gpu-box:8080

Requires: pip install requests
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path
from typing import Optional

import requests

DEFAULT_SERVER = "http://127.0.0.1:8080"
SUPPORTED_FORMATS = ("zip", "markdown", "html", "json")


class ChandraClient:
    """Thin wrapper around the Chandra OCR server's HTTP API."""

    def __init__(self, server_url: str = DEFAULT_SERVER, timeout: int = 600):
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict:
        r = requests.get(f"{self.server_url}/health", timeout=10)
        r.raise_for_status()
        return r.json()

    def info(self) -> dict:
        r = requests.get(f"{self.server_url}/v1/info", timeout=10)
        r.raise_for_status()
        return r.json()

    def process(
        self,
        file_path: str,
        out_dir: str = "./chandra_output",
        *,
        method: str = "vllm",
        response_format: str = "zip",
        page_range: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        max_workers: Optional[int] = None,
        batch_size: Optional[int] = None,
        include_images: bool = True,
        include_headers_footers: bool = False,
    ) -> Path:
        """
        Send a PDF/image to the server and save the result locally.

        Returns the path written: a directory (for response_format="zip",
        containing the full output folder: markdown + html + metadata json +
        extracted images) or a single file (for "markdown" / "html" / "json").
        """
        if response_format not in SUPPORTED_FORMATS:
            raise ValueError(f"response_format must be one of {SUPPORTED_FORMATS}")

        src = Path(file_path)
        if not src.exists():
            raise FileNotFoundError(src)

        data = {
            "method": method,
            "response_format": response_format,
            "include_images": str(include_images).lower(),
            "include_headers_footers": str(include_headers_footers).lower(),
        }
        if page_range:
            data["page_range"] = page_range
        if max_output_tokens is not None:
            data["max_output_tokens"] = str(max_output_tokens)
        if max_workers is not None:
            data["max_workers"] = str(max_workers)
        if batch_size is not None:
            data["batch_size"] = str(batch_size)

        out_dir_path = Path(out_dir)
        out_dir_path.mkdir(parents=True, exist_ok=True)

        with open(src, "rb") as fh:
            files = {"file": (src.name, fh, "application/octet-stream")}
            resp = requests.post(
                f"{self.server_url}/v1/process",
                data=data,
                files=files,
                timeout=self.timeout,
            )

        if resp.status_code != 200:
            raise RuntimeError(f"Server error {resp.status_code}: {resp.text[:2000]}")

        stem = src.stem

        if response_format == "zip":
            dest_dir = out_dir_path / stem
            dest_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(dest_dir)
                names = zf.namelist()
            print(f"Extracted {len(names)} file(s) to {dest_dir}")
            return dest_dir

        if response_format == "markdown":
            dest = out_dir_path / f"{stem}.md"
            dest.write_text(resp.text, encoding="utf-8")
            return dest

        if response_format == "html":
            dest = out_dir_path / f"{stem}.html"
            dest.write_text(resp.text, encoding="utf-8")
            return dest

        # response_format == "json"
        dest = out_dir_path / f"{stem}_metadata.json"
        dest.write_text(resp.text, encoding="utf-8")
        return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Query a Chandra OCR server.")
    parser.add_argument("file", help="Path to a PDF or image file to process")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Server base URL")
    parser.add_argument(
        "--out", default="./chandra_output", help="Local output directory"
    )
    parser.add_argument(
        "--format",
        dest="response_format",
        choices=SUPPORTED_FORMATS,
        default="zip",
        help="zip (full folder, default) | markdown | html | json",
    )
    parser.add_argument("--method", default="vllm", choices=["vllm", "hf"])
    parser.add_argument(
        "--page-range", default=None, help='e.g. "1-5,7,9-12" (PDF only)'
    )
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument(
        "--no-images", action="store_true", help="Skip extracting images"
    )
    parser.add_argument(
        "--headers-footers",
        action="store_true",
        help="Keep page headers/footers in output",
    )
    parser.add_argument(
        "--timeout", type=int, default=600, help="Client-side request timeout"
    )
    args = parser.parse_args()

    client = ChandraClient(server_url=args.server, timeout=args.timeout)

    try:
        print(f"Server health: {client.health()}")
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not reach {args.server}/health ({exc})", file=sys.stderr)
        print("Proceeding anyway...", file=sys.stderr)

    result = client.process(
        args.file,
        out_dir=args.out,
        method=args.method,
        response_format=args.response_format,
        page_range=args.page_range,
        max_output_tokens=args.max_output_tokens,
        max_workers=args.max_workers,
        batch_size=args.batch_size,
        include_images=not args.no_images,
        include_headers_footers=args.headers_footers,
    )
    print(f"Done -> {result}")


if __name__ == "__main__":
    main()
