"""
Rotate PDF pages or PNG images.

We split this into two functions so each has a clear responsibility:
- rotate_pdf_pages: modifies PDF page rotations and writes a new PDF.
- rotate_images_in_folder: rotates PNGs using Pillow.
"""

from __future__ import annotations

from pathlib import Path
import os
import tempfile
from typing import Dict, List, Optional

import fitz  # PyMuPDF
from PIL import Image

from .manifest import ManifestRecorder
from .utils import (
    ensure_dir,
    ensure_dir_path,
    ensure_file_exists,
    ensure_file_path,
    parse_page_spec,
    validate_degrees,
    UserError,
)


def rotate_pdf_pages(
    pdf_path: Path,
    out_pdf: Path,
    degrees: int,
    pages_spec: str,
    overwrite: bool,
    inplace: bool,
    dry_run: bool,
    manifest_path: Path,
    command_string: str,
    options: Dict[str, object],
) -> None:
    """
    Rotate selected PDF pages and save to a new PDF.

    We do NOT modify the original unless --inplace is explicitly set.
    """

    recorder = ManifestRecorder(
        tool_name="pdf-toolkit",
        tool_version=str(options.get("version", "0.0.0")),
        command=command_string,
        options=options,
        inputs={"pdf": str(pdf_path)},
        outputs={"out_pdf": str(out_pdf), "manifest": str(manifest_path)},
        dry_run=dry_run,
        verbosity=str(options.get("verbosity", "normal")),
    )

    temp_path: Optional[Path] = None
    page_indices: List[int] = []
    total_pages = 0
    error_message: str | None = None
    summary: Dict[str, object] = {
        "pages_selected": 0,
        "degrees": degrees,
        "output_pdf": str(out_pdf),
    }

    try:
        ensure_file_exists(pdf_path, "PDF")
        ensure_file_path(out_pdf, "Output PDF")
        degrees = validate_degrees(degrees)
        summary["degrees"] = degrees

        if out_pdf.resolve() == pdf_path.resolve():
            if not inplace:
                raise UserError(
                    "Output PDF is the same as input. Use --inplace to allow this."
                )
            if not overwrite:
                raise UserError(
                    "In-place rotation overwrites the input file. Use --overwrite to proceed."
                )

        if out_pdf.exists() and not overwrite and out_pdf.resolve() != pdf_path.resolve():
            recorder.log(f"Skipping because output exists: {out_pdf}")
            recorder.add_action(
                action="rotate_pdf",
                status="skipped",
                output=str(out_pdf),
            )
            summary["status"] = "skipped"
            summary["reason"] = "output exists"
            return

        with fitz.open(pdf_path) as doc:
            total_pages = int(doc.page_count)
            page_indices = parse_page_spec(pages_spec, total_pages)
            recorder.inputs["page_count"] = total_pages
            recorder.inputs["pages_requested"] = pages_spec
            recorder.inputs["pages_selected"] = len(page_indices)
            recorder.inputs["degrees"] = degrees

            recorder.log(
                f"Rotating {len(page_indices)} page(s) by {degrees} degrees."
            )

            for position, page_index in enumerate(page_indices, start=1):
                page = doc.load_page(page_index)
                current_rotation = page.rotation
                new_rotation = (current_rotation + degrees) % 360
                page.set_rotation(new_rotation)

                recorder.log(
                    f"Rotated page {page_index + 1} "
                    f"({position}/{len(page_indices)}) "
                    f"{current_rotation} -> {new_rotation}"
                )
                recorder.add_action(
                    action="rotate_pdf_page",
                    status="dry-run" if dry_run else "updated",
                    page=page_index + 1,
                    from_rotation=current_rotation,
                    to_rotation=new_rotation,
                )

            if dry_run:
                recorder.log(f"[dry-run] Would write rotated PDF to {out_pdf}")
            else:
                ensure_dir(out_pdf.parent, dry_run=False)
                save_path = out_pdf
                if out_pdf.resolve() == pdf_path.resolve():
                    # Write to a temp file first, then replace the original.
                    handle, temp_name = tempfile.mkstemp(
                        prefix=f"{out_pdf.stem}_tmp_",
                        suffix=out_pdf.suffix,
                        dir=str(out_pdf.parent),
                    )
                    os.close(handle)
                    temp_path = Path(temp_name)
                    save_path = temp_path

                doc.save(save_path, incremental=False, deflate=True)
                recorder.log(f"Wrote rotated PDF to {save_path}")
            if temp_path is not None:
                temp_path.replace(out_pdf)
                recorder.log(f"Replaced original PDF with rotated file: {out_pdf}")
    except Exception as exc:  # pragma: no cover - includes validation and PyMuPDF errors
        if isinstance(exc, UserError):
            error_message = str(exc)
        else:
            error_message = f"Failed to rotate PDF {pdf_path}: {exc}"
        recorder.log(error_message, level="error")
        recorder.add_action(action="rotate_pdf", status="error", error=error_message)
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        if isinstance(exc, UserError):
            raise
        raise UserError(error_message) from exc
    finally:
        summary["pages_selected"] = len(page_indices)
        if total_pages > 0:
            summary["page_count"] = total_pages
        if "status" not in summary:
            summary["status"] = "error" if error_message else "ok"
        if error_message is not None:
            summary["error"] = error_message
        recorder.write_manifest(manifest_path, summary)


def _collect_image_files(in_dir: Path, pattern: str) -> List[Path]:
    """Return a sorted list of matching files for stable processing order."""

    return sorted(path for path in in_dir.glob(pattern) if path.is_file())


def rotate_images_in_folder(
    in_dir: Path,
    out_dir: Path,
    pattern: str,
    degrees: int,
    overwrite: bool,
    inplace: bool,
    dry_run: bool,
    manifest_path: Path,
    command_string: str,
    options: Dict[str, object],
) -> None:
    """
    Rotate images in a folder using Pillow.

    The output filenames are identical to the input filenames.
    """

    recorder = ManifestRecorder(
        tool_name="pdf-toolkit",
        tool_version=str(options.get("version", "0.0.0")),
        command=command_string,
        options=options,
        inputs={"in_dir": str(in_dir), "glob": pattern, "degrees": degrees},
        outputs={"out_dir": str(out_dir), "manifest": str(manifest_path)},
        dry_run=dry_run,
        verbosity=str(options.get("verbosity", "normal")),
    )

    files: List[Path] = []
    error_message: str | None = None
    summary: Dict[str, object] = {
        "images_processed": 0,
        "degrees": degrees,
        "output_dir": str(out_dir),
    }

    try:
        if not in_dir.exists() or not in_dir.is_dir():
            raise UserError(f"Input directory not found: {in_dir}")
        ensure_dir_path(out_dir, "Output directory")

        degrees = validate_degrees(degrees)
        summary["degrees"] = degrees

        if out_dir.resolve() == in_dir.resolve():
            if not inplace:
                raise UserError(
                    "Output directory is the same as input. Use --inplace to allow this."
                )
            if not overwrite:
                raise UserError(
                    "In-place rotation overwrites existing files. Use --overwrite to proceed."
                )

        files = _collect_image_files(in_dir, pattern)
        recorder.inputs["files_found"] = len(files)

        if not files:
            recorder.log(f"No files matched {pattern} in {in_dir}")
            summary["status"] = "no-matches"
            summary["files_found"] = 0
            return

        recorder.log(f"Rotating {len(files)} image(s) by {degrees} degrees.")

        if not dry_run:
            ensure_dir(out_dir, dry_run=False)

        for position, in_path in enumerate(files, start=1):
            out_path = out_dir / in_path.name

            if out_path.exists() and not overwrite:
                recorder.log(f"Skipping existing file: {out_path}")
                recorder.add_action(
                    action="rotate_image",
                    status="skipped",
                    input=str(in_path),
                    output=str(out_path),
                )
                continue

            if dry_run:
                recorder.log(
                    f"[dry-run] Would rotate {in_path.name} "
                    f"({position}/{len(files)}) -> {out_path}"
                )
                recorder.add_action(
                    action="rotate_image",
                    status="dry-run",
                    input=str(in_path),
                    output=str(out_path),
                )
                continue

            try:
                with Image.open(in_path) as image:
                    # Pillow rotates counter-clockwise; use negative for clockwise.
                    rotated = image.rotate(-degrees, expand=True)
                    # Load pixel data now so the input file can close cleanly.
                    rotated.load()
                rotated.save(out_path)
            except Exception as exc:  # pragma: no cover - rare file/codec errors
                raise UserError(f"Failed to rotate image {in_path}: {exc}") from exc

            recorder.log(
                f"Rotated {in_path.name} ({position}/{len(files)}) -> {out_path}"
            )
            recorder.add_action(
                action="rotate_image",
                status="written",
                input=str(in_path),
                output=str(out_path),
            )
    except Exception as exc:  # pragma: no cover - includes validation and runtime errors
        if isinstance(exc, UserError):
            error_message = str(exc)
        else:
            error_message = f"Failed to rotate images in {in_dir}: {exc}"
        recorder.log(error_message, level="error")
        recorder.add_action(action="rotate_images", status="error", error=error_message)
        if isinstance(exc, UserError):
            raise
        raise UserError(error_message) from exc
    finally:
        summary["images_processed"] = len(files)
        summary["status"] = summary.get("status", "error" if error_message else "ok")
        if error_message is not None:
            summary["error"] = error_message
        recorder.write_manifest(manifest_path, summary)
