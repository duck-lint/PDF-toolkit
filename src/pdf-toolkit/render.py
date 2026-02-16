"""
Render PDF pages to PNG images.

Why this module exists:
- Keeps rendering logic separate from CLI parsing.
- Makes it easier to test and reason about the render flow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import fitz  # PyMuPDF

from .manifest import ManifestRecorder
from .utils import (
    UserError,
    ensure_dir,
    ensure_dir_path,
    ensure_file_exists,
    parse_page_spec,
    validate_positive_int,
)


def _compute_page_digits(page_numbers: List[int]) -> int:
    """
    Decide how many zero-padding digits to use for page numbers.

    Why: we want stable, sortable filenames like p0001, p0002, etc.
    """

    if not page_numbers:
        return 4
    max_page = max(page_numbers)
    return max(4, len(str(max_page)))


def render_pdf_to_pngs(
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    pages_spec: str,
    prefix: str,
    image_format: str,
    overwrite: bool,
    dry_run: bool,
    manifest_path: Path,
    command_string: str,
    options: Dict[str, object],
) -> None:
    """
    Render pages from a PDF into PNG files.

    This function is intentionally explicit: we open the PDF once, compute the
    exact output names, then render each page in order.
    """

    recorder = ManifestRecorder(
        tool_name="pdf-toolkit",
        tool_version=str(options.get("version", "0.0.0")),
        command=command_string,
        options=options,
        inputs={"pdf": str(pdf_path)},
        outputs={"out_dir": str(out_dir), "manifest": str(manifest_path)},
        dry_run=dry_run,
        verbosity=str(options.get("verbosity", "normal")),
    )

    total_pages = 0
    page_indices: List[int] = []
    error_message: str | None = None
    summary: Dict[str, object] = {
        "pages_selected": 0,
        "dpi": dpi,
        "format": image_format.lower(),
        "output_dir": str(out_dir),
    }

    try:
        ensure_file_exists(pdf_path, "PDF")
        validate_positive_int(dpi, "--dpi")
        ensure_dir_path(out_dir, "Output directory")

        if image_format.lower() != "png":
            raise UserError("Only PNG output is supported for now.")

        with fitz.open(pdf_path) as doc:
            total_pages = doc.page_count
            page_indices = parse_page_spec(pages_spec, total_pages)
            page_numbers = [index + 1 for index in page_indices]
            digits = _compute_page_digits(page_numbers)

            recorder.inputs["page_count"] = total_pages
            recorder.inputs["pages_requested"] = pages_spec
            recorder.inputs["pages_selected"] = len(page_indices)
            recorder.outputs["format"] = image_format.lower()
            recorder.outputs["prefix"] = prefix

            recorder.log(
                f"Rendering {len(page_indices)} page(s) from {pdf_path} at {dpi} DPI."
            )

            # DPI -> PDF "zoom" factor. PDFs are 72 DPI by default.
            zoom = dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)

            if not dry_run:
                ensure_dir(out_dir, dry_run=False)

            for position, page_index in enumerate(page_indices, start=1):
                page_number = page_index + 1
                filename = f"{prefix}_p{page_number:0{digits}d}.png"
                output_path = out_dir / filename

                if output_path.exists() and not overwrite:
                    recorder.log(f"Skipping existing file: {output_path}")
                    recorder.add_action(
                        action="render_page",
                        status="skipped",
                        page=page_number,
                        output=str(output_path),
                    )
                    continue

                if dry_run:
                    recorder.log(
                        f"[dry-run] Would render page {page_number} "
                        f"({position}/{len(page_indices)}) -> {output_path}"
                    )
                    recorder.add_action(
                        action="render_page",
                        status="dry-run",
                        page=page_number,
                        output=str(output_path),
                    )
                    continue

                page = doc.load_page(page_index)
                pixmap = page.get_pixmap(matrix=matrix)
                pixmap.save(output_path)

                recorder.log(
                    f"Rendered page {page_number} ({position}/{len(page_indices)}) -> {output_path}"
                )
                recorder.add_action(
                    action="render_page",
                    status="written",
                    page=page_number,
                    output=str(output_path),
                )
    except Exception as exc:  # pragma: no cover - includes validation and PyMuPDF errors
        if isinstance(exc, UserError):
            error_message = str(exc)
        else:
            error_message = f"Failed to render PDF {pdf_path}: {exc}"
        recorder.log(error_message, level="error")
        recorder.add_action(action="render", status="error", error=error_message)
        if isinstance(exc, UserError):
            raise
        raise UserError(error_message) from exc
    finally:
        summary["pages_selected"] = len(page_indices)
        if total_pages > 0:
            summary["page_count"] = total_pages
        summary["status"] = "error" if error_message else "ok"
        if error_message is not None:
            summary["error"] = error_message
        recorder.write_manifest(manifest_path, summary)
