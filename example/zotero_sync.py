#!/usr/bin/env python3
"""
Sync Zotero PDF attachments (filtered by a citation-key allow-list) through
an OCR server and write the results into an Obsidian vault.

Configuration is loaded from a `.env` file (via python-dotenv) if one is
found. Any value not supplied by the environment falls back to a
command-line argument. `ZOTERO_DIR`, `RAW_LITERATURE_DIR` and
`ZOTERO_SYNC_FILE` are mandatory (from either source). Everything else is
optional and derived from `BASE_DIR` / `NOTES_DIR` / `VAULT_DIR` where
possible.

Run `zotero_ocr_sync.py --help` for the full list of options.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

from ocr_client.client import ChandraClient

try:
    from dotenv import load_dotenv

    _DOTENV_LOADED = load_dotenv()
except ImportError:
    _DOTENV_LOADED = False


# ---------------------------------------------------------------------
# COLOUR LOGGING
# ---------------------------------------------------------------------


class Colour:
    """Minimal ANSI colour helper. No-ops if stdout isn't a TTY."""

    _ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    RESET = "\033[0m" if _ENABLED else ""
    BOLD = "\033[1m" if _ENABLED else ""
    DIM = "\033[2m" if _ENABLED else ""

    RED = "\033[31m" if _ENABLED else ""
    GREEN = "\033[32m" if _ENABLED else ""
    YELLOW = "\033[33m" if _ENABLED else ""
    BLUE = "\033[34m" if _ENABLED else ""
    MAGENTA = "\033[35m" if _ENABLED else ""
    CYAN = "\033[36m" if _ENABLED else ""


def log_info(msg: str) -> None:
    print(f"{Colour.CYAN}[INFO]{Colour.RESET} {msg}")


def log_ok(msg: str) -> None:
    print(f"{Colour.GREEN}[ OK ]{Colour.RESET} {msg}")


def log_warn(msg: str) -> None:
    print(f"{Colour.YELLOW}[WARN]{Colour.RESET} {msg}")


def log_err(msg: str) -> None:
    print(f"{Colour.RED}[FAIL]{Colour.RESET} {msg}")


def log_step(msg: str) -> None:
    print(f"{Colour.BLUE}{Colour.BOLD}{msg}{Colour.RESET}")


def log_skip(msg: str) -> None:
    print(f"{Colour.DIM}[SKIP]{Colour.RESET} {msg}")


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------


def env_path(name: str) -> Optional[Path]:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zotero_ocr_sync.py",
        description=(
            "Run OCR (via a Chandra-compatible server) over Zotero PDF "
            "attachments whose citation keys are listed in a sync file, "
            "writing results into an Obsidian-style vault under "
            "'Raw Literature/<citationKey>/'.\n\n"
            "Configuration values are read from a .env file first; any "
            "value missing there must be supplied on the command line."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Required (via .env or CLI): --zotero-dir, --raw-literature-dir, "
            "--zotero-sync-file.\n"
            "Optional: --base-dir, --notes-dir, --vault-dir, --state-file, "
            "--ocr-server, --sleep-seconds."
        ),
    )

    parser.add_argument(
        "--base-dir",
        type=Path,
        default=env_path("BASE_DIR"),
        help="Base directory used only to derive defaults for other paths.",
    )
    parser.add_argument(
        "--zotero-dir",
        type=Path,
        default=env_path("ZOTERO_DIR"),
        help="Path to the Zotero data directory (contains zotero.sqlite and storage/).",
    )
    parser.add_argument(
        "--notes-dir",
        type=Path,
        default=env_path("NOTES_DIR"),
        help="Notes directory, used to derive --vault-dir and --state-file defaults.",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=env_path("VAULT_DIR"),
        help="Obsidian vault directory, used to derive --raw-literature-dir default.",
    )
    parser.add_argument(
        "--raw-literature-dir",
        type=Path,
        default=env_path("RAW_LITERATURE_DIR"),
        help="Output directory for OCR results, one subfolder per citation key.",
    )
    parser.add_argument(
        "--zotero-sync-file",
        type=Path,
        default=env_path("ZOTERO_SYNC_FILE"),
        help="JSON file listing which citation keys to process.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=env_path("STATE_FILE"),
        help="JSON file tracking already-processed citation keys. "
        "If omitted, everything in --zotero-sync-file is (re)processed "
        "and no state is persisted.",
    )
    parser.add_argument(
        "--ocr-server",
        type=str,
        default=os.environ.get("OCR_SERVER", "http://127.0.0.1:8080"),
        help="Base URL of the OCR server.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=float(os.environ.get("SLEEP_SECONDS", "15")),
        help="Seconds to sleep between processing consecutive papers.",
    )

    return parser


def resolve_config() -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args()

    if _DOTENV_LOADED:
        log_info("Loaded configuration from .env")
    else:
        log_info(".env not found/usable — relying on command-line arguments")

    # Derive defaults that depend on other paths, only where not already set.
    if args.notes_dir is None and args.base_dir is not None:
        args.notes_dir = args.base_dir / "Notes"

    if args.vault_dir is None and args.notes_dir is not None:
        args.vault_dir = args.notes_dir / "Obsidian"

    if args.raw_literature_dir is None and args.vault_dir is not None:
        args.raw_literature_dir = args.vault_dir / "Raw Literature"

    if args.zotero_sync_file is None and args.notes_dir is not None:
        args.zotero_sync_file = args.notes_dir / "zotero_sync.json"

    if args.state_file is None and args.notes_dir is not None:
        args.state_file = args.notes_dir / ".zotero_sync_state.json"

    missing = [
        name
        for name, value in (
            ("ZOTERO_DIR / --zotero-dir", args.zotero_dir),
            ("RAW_LITERATURE_DIR / --raw-literature-dir", args.raw_literature_dir),
            ("ZOTERO_SYNC_FILE / --zotero-sync-file", args.zotero_sync_file),
        )
        if value is None
    ]
    if missing:
        parser.error(
            "Missing required configuration (set via .env or CLI): "
            + ", ".join(missing)
        )

    if args.state_file is None:
        log_warn(
            "No state file configured — every run will reprocess everything "
            "in the sync file; no progress will be persisted."
        )

    return args


# ---------------------------------------------------------------------
# SYNC FILE
# ---------------------------------------------------------------------


def load_sync_citation_keys(sync_file: Path) -> set[str]:
    """
    Loads the set of citation keys to process from `zotero_sync.json`.

    Accepts:
        [{"id": "key1", ...}, {"id": "key2", ...}, ...]
    or, for convenience:
        ["key1", "key2", ...]
        {"citationKeys": ["key1", "key2", ...]}
    """
    if not sync_file.exists():
        raise FileNotFoundError(f"zotero sync file not found: {sync_file}")

    data = json.loads(sync_file.read_text(encoding="utf-8"))

    if isinstance(data, dict) and "citationKeys" in data:
        keys = data["citationKeys"]
    elif isinstance(data, list):
        keys = []
        for entry in data:
            if isinstance(entry, dict):
                if "id" not in entry:
                    raise ValueError(
                        f"Entry missing 'id' field in {sync_file}: {entry!r}"
                    )
                keys.append(entry["id"])
            else:
                keys.append(entry)
    else:
        raise ValueError(
            f"Unrecognized format in {sync_file}: expected a JSON list of "
            "objects with an 'id' field (or plain citation key strings), "
            "or an object with a 'citationKeys' list."
        )

    return {str(k) for k in keys}


# ---------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------


def load_state(state_file: Optional[Path]) -> dict:
    if state_file is not None and state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {"processed": {}}


def save_state(state_file: Optional[Path], state: dict) -> None:
    if state_file is None:
        return

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------
# ZOTERO DISCOVERY
# ---------------------------------------------------------------------


def discover_largest_pdf_per_citation_key(
    zotero_dir: Path,
    allowed_citation_keys: set[str],
) -> dict[str, Path]:
    """
    Returns {citation_key: pdf_path}, restricted to `allowed_citation_keys`,
    choosing the largest PDF attachment if multiple PDFs exist.
    """

    db_path = zotero_dir / "zotero.sqlite"
    storage_dir = zotero_dir / "storage"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = """
    WITH citation_keys AS (
        SELECT
            i.itemID,
            idv.value AS citation_key
        FROM items i
        JOIN itemData id
            ON i.itemID = id.itemID
        JOIN fields f
            ON id.fieldID = f.fieldID
        JOIN itemDataValues idv
            ON id.valueID = idv.valueID
        WHERE f.fieldName = 'citationKey'
    )
    SELECT
        ck.citation_key,
        attach.key AS attachment_key,
        ia.path
    FROM citation_keys ck
    JOIN itemAttachments ia
        ON ia.parentItemID = ck.itemID
    JOIN items attach
        ON attach.itemID = ia.itemID
    """

    cur.execute(query)

    candidates: dict[str, list[Path]] = {}
    skipped_not_in_sync = 0

    for row in cur.fetchall():
        citation_key = row["citation_key"]
        attachment_key = row["attachment_key"]
        attachment_path = row["path"]

        if citation_key not in allowed_citation_keys:
            skipped_not_in_sync += 1
            continue

        if not attachment_path:
            continue
        if not attachment_path.lower().endswith(".pdf"):
            continue
        if not attachment_path.startswith("storage:"):
            continue

        filename = attachment_path.removeprefix("storage:")
        pdf_path = storage_dir / attachment_key / filename

        if not pdf_path.exists():
            continue

        candidates.setdefault(citation_key, []).append(pdf_path)

    conn.close()

    log_info(
        f"Scanned Zotero DB: {len(candidates)} sync-listed citation keys "
        f"have at least one PDF attachment "
        f"({skipped_not_in_sync} attachment rows skipped, not in sync file)"
    )

    result: dict[str, Path] = {}
    for citation_key, pdfs in candidates.items():
        largest_pdf = max(pdfs, key=lambda p: p.stat().st_size)
        if len(pdfs) > 1:
            log_info(
                f"{citation_key}: {len(pdfs)} PDF attachments found, "
                f"using largest ({human_size(largest_pdf.stat().st_size)})"
            )
        result[citation_key] = largest_pdf

    return result


# ---------------------------------------------------------------------
# RESULT INSPECTION
# ---------------------------------------------------------------------

IMAGE_EXTENSIONS = {".webp", ".png", ".jpg", ".jpeg", ".gif", ".tiff"}


def summarize_zip(zip_path: Path) -> None:
    if not zip_path.exists() or zip_path.suffix.lower() != ".zip":
        return

    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        log_warn(f"Could not inspect zip (not a valid zip): {zip_path}")
        return

    num_files = len(names)
    image_counts: dict[str, int] = {}
    for name in names:
        ext = Path(name).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            image_counts[ext] = image_counts.get(ext, 0) + 1

    total_images = sum(image_counts.values())
    zip_size = zip_path.stat().st_size

    image_breakdown = (
        ", ".join(f"{count} {ext.lstrip('.')}" for ext, count in image_counts.items())
        if image_counts
        else "0"
    )

    log_info(
        f"Result zip: {num_files} files total, {total_images} images "
        f"({image_breakdown}), size {human_size(zip_size)}"
    )


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------


def main() -> None:
    args = resolve_config()

    args.raw_literature_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(args.state_file)

    log_step("=" * 80)
    log_step("Zotero -> OCR sync")
    log_step("=" * 80)
    log_info(f"Zotero dir:         {args.zotero_dir}")
    log_info(f"Raw literature dir: {args.raw_literature_dir}")
    log_info(f"Sync file:          {args.zotero_sync_file}")
    log_info(f"State file:         {args.state_file or '(none — no persistence)'}")
    log_info(f"OCR server:         {args.ocr_server}")
    log_info(f"Sleep between runs: {args.sleep_seconds}s")

    allowed_keys = load_sync_citation_keys(args.zotero_sync_file)
    log_info(f"Sync file lists {len(allowed_keys)} citation key(s)")

    client = ChandraClient(server_url=args.ocr_server, timeout=3600)

    papers = discover_largest_pdf_per_citation_key(args.zotero_dir, allowed_keys)

    already_done = [k for k in papers if k in state["processed"]]
    to_process = [k for k in sorted(papers) if k not in state["processed"]]

    log_step("-" * 80)
    log_info(
        f"{len(papers)} paper(s) matched sync file + have a PDF; "
        f"{len(already_done)} already processed, {len(to_process)} to process"
    )
    log_step("-" * 80)

    succeeded = 0
    failed = 0

    for idx, citation_key in enumerate(to_process, start=1):
        pdf_path = papers[citation_key]

        log_step("")
        log_step("=" * 80)
        log_step(f"[{idx}/{len(to_process)}] {citation_key}")
        log_step("=" * 80)
        log_info(f"Source PDF: {pdf_path} ({human_size(pdf_path.stat().st_size)})")

        output_dir = args.raw_literature_dir / citation_key
        output_dir.mkdir(parents=True, exist_ok=True)

        log_info("Starting OCR request...")
        start_time = time.time()

        try:
            result = client.process(
                str(pdf_path),
                out_dir=str(output_dir),
                response_format="zip",
            )

            elapsed = time.time() - start_time
            result_path = Path(result)

            summarize_zip(result_path)

            state["processed"][citation_key] = {
                "pdf": str(pdf_path),
                "output": str(result),
                "timestamp": int(time.time()),
            }
            save_state(args.state_file, state)

            succeeded += 1
            log_ok(f"{citation_key} finished in {elapsed:.1f}s")
            log_ok(
                f"Progress: {succeeded} succeeded, {failed} failed, "
                f"{len(to_process) - idx} remaining"
            )

        except Exception as exc:
            failed += 1
            log_err(f"{citation_key} failed: {exc}")
            log_err(
                f"Progress: {succeeded} succeeded, {failed} failed, "
                f"{len(to_process) - idx} remaining"
            )

        if idx < len(to_process):
            log_info(f"Sleeping {args.sleep_seconds}s before next paper...")
            time.sleep(args.sleep_seconds)

    log_step("")
    log_step("=" * 80)
    log_ok(
        f"Finished. {succeeded} succeeded, {failed} failed, "
        f"{len(already_done)} already up to date, "
        f"{len(papers) - len(to_process) - len(already_done)} skipped."
    )
    log_step("=" * 80)


if __name__ == "__main__":
    main()
