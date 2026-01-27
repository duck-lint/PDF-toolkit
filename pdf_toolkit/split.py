"""
Split a PDF into multiple PDFs.

Why this module exists:
- Isolates split logic from CLI parsing.
- Makes the split strategy (ranges vs pages_per_file) easy to read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import fitz  # PyMuPDF

from .manifest import ManifestRecorder
from .utils import (
    ensure_dir,
    ensure_dir_path,
    ensure_file_exists,
    ensure_pdf_has_pages,
    parse_page_ranges,
    validate_positive_int,
    UserError,
)


def _chunk_ranges(total_pages: int, pages_per_file: int) -> List[Tuple[int, int]]:
    """
    Create (start, end) ranges for automatic chunking.

    Pages are zero-based and inclusive in these tuples.
    """

    validate_positive_int(pages_per_file, "--pages_per_file")
    ranges: List[Tuple[int, int]] = []
    start = 0
    while start < total_pages:
        end = min(start + pages_per_file - 1, total_pages - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def _compute_part_digits(num_parts: int) -> int:
    """Zero-pad part numbers for stable filenames like part01, part02."""

    return max(2, len(str(num_parts)))


def split_pdf(
    pdf_path: Path,
    out_dir: Path,
    prefix: str,
    ranges_spec: str | None,
    pages_per_file: int | None,
    overwrite: bool,
    dry_run: bool,
    manifest_path: Path,
    command_string: str,
    options: Dict[str, object],
) -> None:
    """
    Split a PDF into multiple output files.

    You can choose either explicit ranges or auto chunking.
    """

    ensure_file_exists(pdf_path, "PDF")
    ensure_dir_path(out_dir, "Output directory")

    if ranges_spec and pages_per_file:
        raise UserError("Use either --ranges or --pages_per_file, not both.")

    recorder = ManifestRecorder(
        tool_name="pdf_toolkit",
        tool_version=options.get("version", "0.0.0"),
        command=command_string,
        options=options,
        inputs={"pdf": str(pdf_path)},
        outputs={"out_dir": str(out_dir), "manifest": str(manifest_path)},
        dry_run=dry_run,
    )

    try:
        with fitz.open(pdf_path) as doc:
            total_pages = doc.page_count
            ensure_pdf_has_pages(total_pages)

            if ranges_spec:
                ranges = parse_page_ranges(ranges_spec, total_pages)
                recorder.inputs["ranges"] = ranges_spec
            elif pages_per_file is not None:
                ranges = _chunk_ranges(total_pages, pages_per_file)
                recorder.inputs["pages_per_file"] = pages_per_file
            else:
                raise UserError("Either --ranges or --pages_per_file is required.")

            recorder.inputs["page_count"] = total_pages
            recorder.outputs["prefix"] = prefix

            num_parts = len(ranges)
            digits = _compute_part_digits(num_parts)

            recorder.log(
                f"Splitting {pdf_path} into {num_parts} part(s). Total pages: {total_pages}."
            )

            if not dry_run:
                ensure_dir(out_dir, dry_run=False)

            for index, (start, end) in enumerate(ranges, start=1):
                part_name = f"{prefix}_part{index:0{digits}d}.pdf"
                output_path = out_dir / part_name
                human_range = f"{start + 1}-{end + 1}"

                if output_path.exists() and not overwrite:
                    recorder.log(f"Skipping existing file: {output_path}")
                    recorder.add_action(
                        action="split_part",
                        status="skipped",
                        part=index,
                        pages=human_range,
                        output=str(output_path),
                    )
                    continue

                if dry_run:
                    recorder.log(
                        f"[dry-run] Would write part {index} "
                        f"({human_range}) -> {output_path}"
                    )
                    recorder.add_action(
                        action="split_part",
                        status="dry-run",
                        part=index,
                        pages=human_range,
                        output=str(output_path),
                    )
                    continue

                out_doc = fitz.open()
                out_doc.insert_pdf(doc, from_page=start, to_page=end)
                out_doc.save(output_path)
                out_doc.close()

                recorder.log(f"Wrote part {index} ({human_range}) -> {output_path}")
                recorder.add_action(
                    action="split_part",
                    status="written",
                    part=index,
                    pages=human_range,
                    output=str(output_path),
                )
    except Exception as exc:  # pragma: no cover - PyMuPDF errors
        raise UserError(f"Failed to split PDF {pdf_path}: {exc}") from exc

    summary = {
        "parts": num_parts,
        "page_count": total_pages,
        "output_dir": str(out_dir),
    }
    recorder.write_manifest(manifest_path, summary)
