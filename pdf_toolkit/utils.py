"""
Shared utility helpers.

This module keeps the "sharp edges" (validation and parsing) in one place so
the rest of the code can stay focused on PDF/image work.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple


class UserError(Exception):
    """Raised for user-facing problems that should show a clear message."""


def normalize_path(value: str) -> Path:
    """
    Convert user input to a Path.

    We do not resolve() here because we want to preserve relative paths in
    manifests and error messages.
    """

    return Path(value).expanduser()


def ensure_file_exists(path: Path, label: str) -> Path:
    """Validate that a path exists and is a file."""

    if not path.exists():
        raise UserError(f"{label} not found: {path}")
    if not path.is_file():
        raise UserError(f"{label} is not a file: {path}")
    return path


def ensure_dir(path: Path, dry_run: bool) -> None:
    """
    Create a directory if needed, unless this is a dry-run.

    Why: dry-run should never touch the filesystem, but real runs should
    create output folders automatically.
    """

    if dry_run:
        return
    path.mkdir(parents=True, exist_ok=True)


def ensure_dir_path(path: Path, label: str) -> None:
    """Ensure a path is either a directory or does not exist yet."""

    if path.exists() and not path.is_dir():
        raise UserError(f"{label} is not a directory: {path}")


def ensure_file_path(path: Path, label: str) -> None:
    """Ensure a path is a file path (not an existing directory)."""

    if path.exists() and path.is_dir():
        raise UserError(f"{label} is a directory, not a file: {path}")


def ensure_pdf_has_pages(total_pages: int) -> None:
    """Fail early if a PDF has no pages."""

    if total_pages <= 0:
        raise UserError("PDF has no pages.")


def validate_positive_int(value: int, label: str) -> int:
    """Common validation for options like --dpi or --pages_per_file."""

    if value <= 0:
        raise UserError(f"{label} must be a positive integer.")
    return value


def validate_degrees(degrees: int) -> int:
    """
    Ensure rotation degrees are supported.

    We only allow 90/180/270 so results are predictable for PDFs and PNGs.
    """

    if degrees not in {90, 180, 270}:
        raise UserError("Degrees must be one of 90, 180, 270 (clockwise).")
    return degrees


def parse_page_spec(spec: str, total_pages: int) -> List[int]:
    """
    Parse a page selection string into zero-based page indices.

    Examples:
    - "all"
    - "1-3,5,7-9"

    We keep this strict and explicit so mistakes are caught early.
    """

    ensure_pdf_has_pages(total_pages)

    raw = spec.strip()
    if not raw:
        raise UserError("Page selection is empty.")

    compact = raw.replace(" ", "")
    lowered = compact.lower()
    if lowered in {"all", "*"}:
        return list(range(total_pages))

    tokens = compact.split(",")
    if any(token == "" for token in tokens):
        raise UserError("Page selection contains an empty token (check commas).")

    pages: List[int] = []
    seen = set()

    for token in tokens:
        if "-" in token:
            parts = token.split("-")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise UserError(
                    f"Invalid range '{token}'. Use formats like 1-3 or 5."
                )
            if not (parts[0].isdigit() and parts[1].isdigit()):
                raise UserError(
                    f"Invalid range '{token}'. Page numbers must be digits."
                )
            start = int(parts[0])
            end = int(parts[1])
        else:
            if not token.isdigit():
                raise UserError(
                    f"Invalid page token '{token}'. Use formats like 1 or 2-4."
                )
            start = int(token)
            end = start

        if start < 1 or end < 1:
            raise UserError("Page numbers are 1-based and must be >= 1.")
        if start > end:
            raise UserError(f"Invalid range '{token}': start > end.")

        for page_number in range(start, end + 1):
            if page_number > total_pages:
                raise UserError(
                    f"Page {page_number} is out of range. PDF has {total_pages} pages."
                )
            if page_number in seen:
                raise UserError(f"Duplicate page {page_number} in selection.")
            seen.add(page_number)
            pages.append(page_number - 1)  # zero-based for PyMuPDF

    if not pages:
        raise UserError("Page selection produced no pages.")

    return pages


def parse_page_ranges(spec: str, total_pages: int) -> List[Tuple[int, int]]:
    """
    Parse a ranges string into zero-based (start, end) tuples (inclusive).

    This is for splitting PDFs into multiple output files.
    Example: "1-120,121-240"
    """

    ensure_pdf_has_pages(total_pages)

    raw = spec.strip()
    if not raw:
        raise UserError("Ranges selection is empty.")

    compact = raw.replace(" ", "")
    if compact.lower() in {"all", "*"}:
        raise UserError(
            "Use explicit ranges like 1-120,121-240 or --pages_per_file."
        )

    tokens = compact.split(",")
    if any(token == "" for token in tokens):
        raise UserError("Ranges selection contains an empty token (check commas).")

    ranges: List[Tuple[int, int]] = []
    seen_pages = set()

    for token in tokens:
        if "-" in token:
            parts = token.split("-")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise UserError(
                    f"Invalid range '{token}'. Use formats like 1-3 or 5."
                )
            if not (parts[0].isdigit() and parts[1].isdigit()):
                raise UserError(
                    f"Invalid range '{token}'. Page numbers must be digits."
                )
            start = int(parts[0])
            end = int(parts[1])
        else:
            if not token.isdigit():
                raise UserError(
                    f"Invalid page token '{token}'. Use formats like 1 or 2-4."
                )
            start = int(token)
            end = start

        if start < 1 or end < 1:
            raise UserError("Page numbers are 1-based and must be >= 1.")
        if start > end:
            raise UserError(f"Invalid range '{token}': start > end.")

        for page_number in range(start, end + 1):
            if page_number > total_pages:
                raise UserError(
                    f"Page {page_number} is out of range. PDF has {total_pages} pages."
                )
            if page_number in seen_pages:
                raise UserError(
                    f"Ranges overlap on page {page_number}. Overlaps are not allowed."
                )
            seen_pages.add(page_number)

        ranges.append((start - 1, end - 1))

    if not ranges:
        raise UserError("Ranges selection produced no pages.")

    return ranges
