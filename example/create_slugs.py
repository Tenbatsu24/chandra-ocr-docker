#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

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


# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------


def env_path(name: str) -> Optional[Path]:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Obsidian research note stubs from Zotero sync data."
    )

    parser.add_argument(
        "--sync-file",
        type=Path,
        default=env_path("ZOTERO_SYNC_FILE"),
        help="Path to zotero_sync.json",
    )

    parser.add_argument(
        "--state-file",
        type=Path,
        default=env_path("ZOTERO_SYNC_STATE_FILE"),
        help="Path to .zotero_sync_state.json",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=env_path("OBSIDIAN_RESEARCH_DIR"),
        help="Output directory for generated research notes",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing notes",
    )

    return parser


def resolve_config() -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args()

    if _DOTENV_LOADED:
        log_info("Loaded configuration from .env")
    else:
        log_info(".env not found/usable — relying on command-line arguments")

    missing = []

    if args.sync_file is None:
        missing.append("sync-file / ZOTERO_SYNC_FILE")

    if args.state_file is None:
        missing.append("state-file / ZOTERO_SYNC_STATE_FILE")

    if args.output_dir is None:
        missing.append("output-dir / OBSIDIAN_RESEARCH_DIR")

    if missing:
        parser.error("Missing configuration: " + ", ".join(missing))

    return args


# ---------------------------------------------------------------------
# SYNC FILE
# ---------------------------------------------------------------------


def load_sync_citation_keys(sync_file: Path) -> set[str]:
    """
    Loads the set of citation keys to process from `zotero_sync.json`.

    Accepts:
        [{"id": "key1", ...}, {"id": "key2", ...}, ...]
    or:
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
            f"Unrecognized format in {sync_file}: expected a JSON list "
            "or an object containing citationKeys."
        )

    return {str(k) for k in keys}


# ---------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------


def load_state(state_file: Optional[Path]) -> dict:
    if state_file is not None and state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {"processed": {}}


# ---------------------------------------------------------------------
# BIB ENTRIES
# ---------------------------------------------------------------------


def load_bib_entries(sync_file: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(sync_file.read_text(encoding="utf-8"))

    entries: dict[str, dict[str, Any]] = {}

    if not isinstance(data, list):
        raise ValueError(
            "Expected BetterBibTeX CSL JSON export to be a list of entries."
        )

    for item in data:
        if not isinstance(item, dict):
            continue

        key = item.get("id")
        if key:
            entries[str(key)] = item

    return entries


# ---------------------------------------------------------------------
# NOTE GENERATION
# ---------------------------------------------------------------------


def first_n_authors(entry: dict[str, Any], n: int = 3) -> list[str]:
    authors = []

    for author in entry.get("author", [])[:n]:
        if not isinstance(author, dict):
            continue

        family = author.get("family", "")
        given = author.get("given", "")

        name = f"{given} {family}".strip()
        if name:
            authors.append(name)

    return authors


def sanitize_filename(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def note_filename(citation_key: str, title: str) -> str:
    return f"{citation_key}.md"


def build_note(
    *,
    title: str,
    authors: list[str],
    citation_key: str,
    raw_literature_dir: str,
    abstract: str,
) -> str:
    author_block = "\n".join(f"  - {a}" for a in authors)

    return f"""---
title: {sanitize_filename(title)}
authors:
{author_block if author_block else "  -"}
tags:
zotero_citation_key: {citation_key}
abstract: |
  {abstract.replace(chr(10), chr(10) + "  ")}
---

# Summary

TODO

# Main Claims

TODO

# Key Insights

TODO

# Validation / Benchmarking / Evidence

TODO

# What This Work Does NOT Claim

TODO

# Open Gaps / Future Work

TODO

# Notes

TODO

"""


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------


def main() -> int:
    args = resolve_config()

    log_step("Loading data")

    entries = load_bib_entries(args.sync_file)
    state = load_state(args.state_file)

    processed = state.get("processed", {})

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    created = 0

    for citation_key, meta in entries.items():
        state_entry = processed.get(citation_key)

        if state_entry is None:
            log_skip(f"{citation_key}: not OCR processed")
            continue

        title = meta.get("title", citation_key)

        authors = first_n_authors(meta, n=3)

        abstract = meta.get("abstract", "") or ""

        raw_literature_dir = str(state_entry.get("output", ""))

        note_text = build_note(
            title=title,
            authors=authors,
            citation_key=citation_key,
            raw_literature_dir=raw_literature_dir,
            abstract=abstract,
        )

        note_path = output_dir / note_filename(
            citation_key,
            title,
        )

        if note_path.exists() and not args.overwrite:
            log_skip(f"{note_path.name}")
            continue

        note_path.write_text(
            note_text,
            encoding="utf-8",
        )

        created += 1
        log_ok(note_path.name)

    log_info(f"Generated {created} note(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
